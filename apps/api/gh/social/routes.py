"""/social — Tài khoản mạng xã hội (v0.1.29, docs/design/gen-browser-agent.md §5.1). CHỈ Owner, mọi route.

- `GET /social/status` · `GET /social/platforms` · `GET /social/accounts` · `GET /social/accounts/{id}`
- `POST /social/accounts` (PIN `social.manage`) — thêm tài khoản; bắt buộc tích chấp nhận rủi ro + tài khoản thật.
- `PATCH /social/accounts/{id}` — tên, lịch đọc (tắt mặc định), trần lượt đọc/ngày (chỉ chỉnh xuống).
- `POST /social/accounts/{id}/login` (PIN) → vé WS `WS /social/login/{ticket}` (cửa sổ trình duyệt từ xa).
- `POST /social/accounts/{id}/check|read|pause|resume` · `DELETE /social/accounts/{id}` (PIN — gỡ, xoá phiên).
- `GET /social/accounts/{id}/jobs|latest` · `GET /social/jobs/{id}`
- `POST /social/halt` (Dừng tất cả — KHÔNG cần PIN để dừng được ngay) · `DELETE /social/halt` (Bật lại — PIN).
- Gửi (trả lời bình luận / nhắn tin; KHÔNG đăng bài): `POST /social/accounts/{id}/write` (PIN `social.write`, thường qua
  đề xuất của Gen) · `GET /social/writes` · `GET /social/jobs/{id}/proof` (ảnh chụp bằng chứng, mã hoá khi lưu).
- Cổng F-85: `GET /social/write-gate` · `POST /social/write-consent` (PIN `social.manage`) ·
  `DELETE /social/write-consent` (rút lại, không cần PIN).
"""

import asyncio
import contextlib
import logging
import uuid
from typing import Any, Literal

import orjson
from fastapi import APIRouter, Depends, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import crypto
from gh.auth import rbac, service
from gh.auth.deps import require, require_owner, require_pin
from gh.boss_checks import service as boss_checks
from gh.chassis import actionlog
from gh.db import DB, sessionmaker
from gh.gen.coach import cache as coach_cache
from gh.social import platforms, protocol
from gh.social import service as social

log = logging.getLogger("gh.social.routes")
router = APIRouter(prefix="/social", tags=["social"])
MANAGE = require("system.manage", rbac.ALL)


def _redis(request: Request) -> Redis:
    return request.app.state.redis  # type: ignore[no-any-return]


@router.get("/status")
async def get_status(request: Request, _m: service.CurrentUser = Depends(MANAGE),
                     _o: service.CurrentUser = Depends(require_owner)) -> dict[str, Any]:
    return await social.status(_redis(request))


@router.get("/platforms")
async def get_platforms(_m: service.CurrentUser = Depends(MANAGE),
                        _o: service.CurrentUser = Depends(require_owner)) -> dict[str, Any]:
    return {"items": [platforms.public(p) for p in platforms.PLATFORMS.values() if p.enabled],
            "hard_rules": list(platforms.HARD_RULES), "risk_version": platforms.RISK_VERSION}


@router.get("/accounts")
async def get_accounts(_m: service.CurrentUser = Depends(MANAGE), user: service.CurrentUser = Depends(require_owner),
                       db: AsyncSession = DB) -> dict[str, Any]:
    return {"items": await social.list_accounts(db, user.org_id)}


class AccountIn(BaseModel):
    platform: str = Field(max_length=40)
    label: str = Field(max_length=80)
    risk_version: str = Field(max_length=20)
    accept_risk: bool = False
    accept_rules: bool = False


@router.post("/accounts", status_code=201)
async def add_account(body: AccountIn, request: Request, _m: service.CurrentUser = Depends(MANAGE),
                      _o: service.CurrentUser = Depends(require_owner),
                      user: service.CurrentUser = Depends(require_pin("social.manage")),
                      db: AsyncSession = DB) -> dict[str, Any]:
    return await social.create_account(db, _redis(request), user, platform=body.platform, label=body.label,
                                       risk_version=body.risk_version, accept_risk=body.accept_risk,
                                       accept_rules=body.accept_rules)


@router.get("/accounts/{account_id}")
async def get_account(account_id: uuid.UUID, _m: service.CurrentUser = Depends(MANAGE),
                      user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return await social.get_account(db, user.org_id, account_id)


class ScheduleIn(BaseModel):
    enabled: bool = False
    times: list[str] = Field(default_factory=list, max_length=4)


class AccountPatch(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    schedule: ScheduleIn | None = None
    daily_read_limit: int | None = None
    daily_write_limit: int | None = None


@router.patch("/accounts/{account_id}")
async def patch_account(account_id: uuid.UUID, body: AccountPatch, request: Request,
                        _m: service.CurrentUser = Depends(MANAGE), user: service.CurrentUser = Depends(require_owner),
                        db: AsyncSession = DB) -> dict[str, Any]:
    return await social.update_account(db, _redis(request), user, account_id, label=body.label,
                                       schedule=body.schedule.model_dump() if body.schedule else None,
                                       daily_read_limit=body.daily_read_limit,
                                       daily_write_limit=body.daily_write_limit)


@router.post("/accounts/{account_id}/login")
async def login(account_id: uuid.UUID, request: Request, _m: service.CurrentUser = Depends(MANAGE),
                _o: service.CurrentUser = Depends(require_owner),
                user: service.CurrentUser = Depends(require_pin("social.manage")),
                db: AsyncSession = DB) -> dict[str, Any]:
    return await social.request_login(db, _redis(request), user, account_id)


@router.post("/accounts/{account_id}/check")
async def check(account_id: uuid.UUID, request: Request, _m: service.CurrentUser = Depends(MANAGE),
                user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return await social.request_check(db, _redis(request), user, account_id)


class ReadIn(BaseModel):
    what: list[Literal["notifications", "inbox"]] | None = Field(default=None, max_length=2)


@router.post("/accounts/{account_id}/read")
async def read(account_id: uuid.UUID, request: Request, body: ReadIn | None = None,
               _m: service.CurrentUser = Depends(MANAGE), user: service.CurrentUser = Depends(require_owner),
               db: AsyncSession = DB) -> dict[str, Any]:
    job = await social.request_read(db, _redis(request), org_id=user.org_id, account_id=account_id, via="user",
                                    user=user, what=list(body.what) if body and body.what else None)
    # v0.1.68 (F028): "Đọc ngay" ở đây cũng là một lần kiểm Facebook của "Việc Sếp cần làm" (cùng bản ghi 'pending'
    # như nút Kiểm tra ở đó; `boss_checks.resolve_pending` chốt Đạt/Lỗi theo lượt đọc). Chỉ lượt do Sếp bấm, chỉ
    # tài khoản Facebook.
    platform = (await db.execute(text("SELECT platform FROM core.social_accounts WHERE id = :a AND org_id = :o"),
                                 {"a": account_id, "o": user.org_id})).scalar_one_or_none()
    if platform is not None and str(platform).startswith("facebook"):
        await boss_checks.record(db, user.org_id, "facebook", "pending", detail={"job_status": job["status"]},
                                 ref_id=uuid.UUID(job["id"]), user_id=user.id)
        await coach_cache.commit_and_invalidate(db, _redis(request), user.org_id)
    return job


@router.post("/accounts/{account_id}/pause")
async def pause(account_id: uuid.UUID, request: Request, _m: service.CurrentUser = Depends(MANAGE),
                user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return await social.set_paused(db, _redis(request), user, account_id, True)


@router.post("/accounts/{account_id}/resume")
async def resume(account_id: uuid.UUID, request: Request, _m: service.CurrentUser = Depends(MANAGE),
                 user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return await social.set_paused(db, _redis(request), user, account_id, False)


@router.delete("/accounts/{account_id}", status_code=204)
async def remove(account_id: uuid.UUID, request: Request, _m: service.CurrentUser = Depends(MANAGE),
                 _o: service.CurrentUser = Depends(require_owner),
                 user: service.CurrentUser = Depends(require_pin("social.manage")), db: AsyncSession = DB) -> None:
    await social.revoke(db, _redis(request), user, account_id)


@router.get("/accounts/{account_id}/jobs")
async def jobs(account_id: uuid.UUID, _m: service.CurrentUser = Depends(MANAGE),
               user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return {"items": await social.list_jobs(db, user.org_id, account_id)}


@router.get("/accounts/{account_id}/latest")
async def latest(account_id: uuid.UUID, _m: service.CurrentUser = Depends(MANAGE),
                 user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return {"job": await social.latest_read(db, user.org_id, account_id)}


@router.get("/jobs/{job_id}")
async def job(job_id: uuid.UUID, _m: service.CurrentUser = Depends(MANAGE),
              user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return await social.get_job(db, user.org_id, job_id)


class WriteIn(BaseModel):
    action: Literal["reply_comment", "send_message"]
    target_url: str = Field(max_length=300)
    text: str = Field(max_length=2000)
    proposal_id: uuid.UUID | None = None


@router.post("/accounts/{account_id}/write", status_code=201)
async def write(account_id: uuid.UUID, body: WriteIn, request: Request, _m: service.CurrentUser = Depends(MANAGE),
                _o: service.CurrentUser = Depends(require_owner),
                user: service.CurrentUser = Depends(require_pin("social.write")),
                db: AsyncSession = DB) -> dict[str, Any]:
    """Gửi trả lời / tin nhắn — Gen chỉ đề xuất; việc này chỉ chạy khi chính Owner xác nhận và nhập PIN."""
    return await social.request_write(db, _redis(request), user, account_id=account_id, action=body.action,
                                      target_url=body.target_url, text=body.text, proposal_id=body.proposal_id,
                                      via="gen" if body.proposal_id else "user")


@router.get("/writes")
async def writes(account_id: uuid.UUID | None = None, limit: int = 20, _m: service.CurrentUser = Depends(MANAGE),
                 user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return {"items": await social.list_writes(db, user.org_id, account_id, limit)}


@router.get("/jobs/{job_id}/proof")
async def proof(job_id: uuid.UUID, _m: service.CurrentUser = Depends(MANAGE),
                user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> Response:
    data = await social.proof_image(db, user.org_id, job_id)
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/write-gate")
async def get_write_gate(request: Request, _m: service.CurrentUser = Depends(MANAGE),
                         user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return await social.write_gate(db, _redis(request), user.org_id)


class ConsentIn(BaseModel):
    version: str = Field(max_length=20)


@router.post("/write-consent")
async def accept_write_consent(body: ConsentIn, request: Request, _m: service.CurrentUser = Depends(MANAGE),
                               _o: service.CurrentUser = Depends(require_owner),
                               user: service.CurrentUser = Depends(require_pin("social.manage")),
                               db: AsyncSession = DB) -> dict[str, Any]:
    return await social.accept_write_risk(db, _redis(request), user, body.version)


@router.delete("/write-consent")
async def revoke_write_consent(request: Request, _m: service.CurrentUser = Depends(MANAGE),
                               user: service.CurrentUser = Depends(require_owner),
                               db: AsyncSession = DB) -> dict[str, Any]:
    """Rút lại đồng ý — KHÔNG cần PIN (rút lại phải dễ)."""
    return await social.revoke_write_risk(db, _redis(request), user)


@router.post("/halt")
async def halt(request: Request, _m: service.CurrentUser = Depends(MANAGE),
               user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return await social.set_halt(db, _redis(request), user, True)


@router.delete("/halt")
async def release(request: Request, _m: service.CurrentUser = Depends(MANAGE),
                  _o: service.CurrentUser = Depends(require_owner),
                  user: service.CurrentUser = Depends(require_pin("social.manage")),
                  db: AsyncSession = DB) -> dict[str, Any]:
    return await social.set_halt(db, _redis(request), user, False)


# ─── cửa sổ trình duyệt từ xa (chỉ lúc đăng nhập) ────────────────────────────────

MOUSE_ACTIONS = {"move", "down", "up", "click"}
KEY_ACTIONS = {"down", "up", "press"}
# Phím đặc biệt cho phép (tên theo Playwright keyboard); ký tự thường đi qua "text".
KEYS = {"Enter", "Tab", "Backspace", "Delete", "Escape", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home",
        "End", "PageUp", "PageDown", "Shift", "Control", "Alt", "Meta", "Space"}
VIEW_W, VIEW_H = 1280, 800


def clean_input(msg: Any) -> dict[str, Any] | None:
    """Chỉ chuyển tiếp sự kiện chuột/phím hợp lệ, có giới hạn — không bao giờ ghi log nội dung phím."""
    if not isinstance(msg, dict):
        return None
    t = msg.get("type")

    def coord(v: Any, hi: int) -> int:
        return max(0, min(hi, int(v))) if isinstance(v, int | float) else 0

    if t == "mouse" and msg.get("action") in MOUSE_ACTIONS:
        button = msg.get("button") if msg.get("button") in ("left", "right", "middle") else "left"
        return {"type": "mouse", "action": msg["action"], "x": coord(msg.get("x"), VIEW_W),
                "y": coord(msg.get("y"), VIEW_H), "button": button}
    if t == "wheel":
        return {"type": "wheel", "dx": max(-2000, min(2000, int(msg.get("dx") or 0))),
                "dy": max(-2000, min(2000, int(msg.get("dy") or 0))),
                "x": coord(msg.get("x"), VIEW_W), "y": coord(msg.get("y"), VIEW_H)}
    if t == "key" and msg.get("action") in KEY_ACTIONS and msg.get("key") in KEYS:
        return {"type": "key", "action": msg["action"], "key": msg["key"]}
    if t == "text" and isinstance(msg.get("text"), str) and 0 < len(msg["text"]) <= 256:
        return {"type": "text", "text": msg["text"]}
    if t == "nav" and msg.get("action") in ("back", "reload"):
        return {"type": "nav", "action": msg["action"]}
    if t in ("done", "cancel"):
        return {"type": t}
    return None


@router.websocket("/login/{ticket}")
async def login_view(ws: WebSocket, ticket: str) -> None:
    """Khung hình (CDP screencast) từ worker → Owner; chuột/phím của Owner → worker. Không lưu, không ghi log khung
    hình hay phím gõ. Chỉ đúng Owner đã mở vé (ticket 32 byte ngẫu nhiên, hết hạn 12 phút) mới vào được."""
    redis: Redis = ws.app.state.redis
    token = ws.cookies.get(service.SESSION_COOKIE)
    info = await social.ticket_info(redis, ticket) if len(ticket) <= 64 else None
    async with sessionmaker()() as db:
        user = await service.load_session(db, token) if token else None
    await ws.accept()
    if user is None or info is None or user.role_code != rbac.OWNER or str(user.id) != info.get("user_id") \
            or str(user.org_id) != info.get("org_id"):
        await ws.close(code=4403)
        return
    key = crypto.browser_key()
    chan = social.bus(redis)          # kênh riêng với browser-worker — không phải Redis chính
    pubsub = chan.pubsub()
    await pubsub.subscribe(protocol.FRAMES_PREFIX + ticket)

    async def pump_frames() -> None:
        async for item in pubsub.listen():
            if item.get("type") != "message":
                continue
            try:
                msg = protocol.verify(key, protocol.P_FRAME, orjson.loads(item["data"]))
            except orjson.JSONDecodeError:
                continue
            if msg is None:
                continue
            if msg.get("t") == "frame" and isinstance(msg.get("data"), str):
                await ws.send_text(orjson.dumps({"type": "frame", "data": msg["data"], "w": msg.get("w"),
                                                 "h": msg.get("h")}).decode())
            elif msg.get("t") == "status":
                await ws.send_text(orjson.dumps({"type": "status", "state": str(msg.get("state"))[:30],
                                                 "message": str(msg.get("message") or "")[:200]}).decode())

    pump = asyncio.create_task(pump_frames())
    try:
        await ws.send_text(orjson.dumps({"type": "status", "state": "connecting",
                                         "message": "Đang mở trình duyệt…"}).decode())
        while True:
            raw = await ws.receive_text()
            if len(raw) > 2000:
                continue
            try:
                ev = clean_input(orjson.loads(raw))
            except (orjson.JSONDecodeError, ValueError, TypeError):
                continue
            if ev is None:
                continue
            ev |= {"ticket": ticket, "ts": int(social._now().timestamp())}
            await chan.publish(protocol.INPUT_PREFIX + ticket, orjson.dumps(protocol.sign(key, protocol.P_INPUT, ev)))
            if ev["type"] == "cancel":
                async with sessionmaker()() as db:
                    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                                           action="social.login_cancelled", target_type="social_account",
                                           target_id=info.get("account_id"), detail={"job_id": info.get("job_id")})
                    await db.commit()
    except WebSocketDisconnect:
        pass
    finally:
        pump.cancel()
        with contextlib.suppress(BaseException):
            await pump
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe()
            await pubsub.aclose()


__all__ = ["router"]
