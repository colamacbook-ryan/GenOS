"""/notify/telegram — kênh "Báo động & bản tin" (v0.1.44, F-8c). CHỈ Owner (+ `system.manage`).

- `GET` — cấu hình (KHÔNG BAO GIỜ có token; chat_id dạng che), lần Gửi thử gần nhất, trạng thái Trực canh máy chủ
  (run/watchdog-status.json do genh ghi).
- `PUT` (PIN `notify.change`) — lưu token + chat_id (cả hai tuỳ chọn khi đã có — trống ⇒ giữ) + cờ; token mới được
  kiểm bằng getMe. `{}` = "Lưu lại" (ghi lại run/telegram.json bằng khoá master hiện tại).
- `DELETE` (PIN) — gỡ cấu hình; run/telegram.json chuyển sang enabled=false.
- `POST /find-chat` — đọc getUpdates (token trong thân hoặc token đã lưu; token trong thân KHÔNG được lưu).
- `POST /test` — Gửi thử bằng cấu hình đã lưu, ghi boss_checks 'telegram', và để lại run/request/watchdog.json cho
  genh gửi tin thử thứ hai từ máy chủ (`host_requested`).
- v0.1.64 (Telegram 2 chiều, T3): `POST /pair/start` (PIN) cấp mã ghép 6 số sống 10 phút (trả MỘT lần) ·
  `PUT /inbound` (PIN) bật/tắt "Nhận tin từ Sếp" (bật cần đã ghép) · `DELETE /pair` (PIN) gỡ ghép. GET có khối `inbound`
  (không mã, không id Telegram đầy đủ). Khi đang nhận tin (hoặc có mã ghép sống), `find-chat` không gọi getUpdates riêng
  (tránh tranh offset với vòng nhận tin của worker).
Action Log chỉ có chat_id đã che + bot_username.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.auth.deps import require, require_owner, require_pin
from gh.chassis import actionlog
from gh.db import DB, sessionmaker
from gh.telegram import client as tg
from gh.telegram import inbound, poller
from gh.telegram import service as tsvc

router = APIRouter(prefix="/notify/telegram", tags=["notify"])
MANAGE = require("system.manage", rbac.ALL)


class TelegramIn(BaseModel):
    token: str | None = Field(None, max_length=128)
    #: Bỏ trống khi đã cấu hình ⇒ giữ chat_id cũ (như token).
    chat_id: str | None = Field(None, max_length=32)
    enabled: bool | None = None
    briefing: bool | None = None
    reminders: bool | None = None


class InboundIn(BaseModel):
    enabled: bool


class FindChatIn(BaseModel):
    token: str | None = Field(None, max_length=128)


def client(request: Request) -> tg.TelegramClient:
    return tg.client_for(getattr(request.app.state, "telegram_transport", None))


async def _payload(db: AsyncSession, org_id: Any, redis: Any = None) -> dict[str, Any]:
    row = await tsvc.get_config(db, org_id)
    ib = inbound.view(await inbound.load(db, org_id))
    status = await poller.read_status(redis, org_id) if redis is not None else None
    code = status.get("error_code") if status else None
    ib["error_code"] = code if isinstance(code, str) else None
    ib["message"] = tsvc.message_for(ib["error_code"])
    return {**tsvc.view(row), "last_test": await tsvc.last_test(db, org_id), "host": tsvc.host_status(),
            "inbound": ib}


@router.get("")
async def get_telegram(request: Request, _m: service.CurrentUser = Depends(MANAGE),
                       user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return await _payload(db, user.org_id, request.app.state.redis)


@router.put("")
async def put_telegram(body: TelegramIn, request: Request, _m: service.CurrentUser = Depends(MANAGE),
                       user: service.CurrentUser = Depends(require_owner),
                       _p: service.CurrentUser = Depends(require_pin("notify.change")),
                       db: AsyncSession = DB) -> dict[str, Any]:
    row = await tsvc.save_config(db, user.org_id, body.token, body.chat_id, enabled=body.enabled,
                                 briefing=body.briefing, reminders=body.reminders, user_id=user.id,
                                 client=client(request))
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="notify.telegram.save", target_type="notify_channel", target_id="telegram",
                           target_label=row.bot_username,
                           detail={"chat_masked": tsvc.mask_chat(row.chat_id), "bot_username": row.bot_username,
                                   "token_changed": bool(body.token and body.token.strip()),
                                   "enabled": bool(row.enabled), "briefing": bool(row.briefing),
                                   "reminders": bool(row.reminders)}, ip=user.ip)
    await db.commit()
    await tsvc.sync_host_file(sessionmaker())
    return await _payload(db, user.org_id, request.app.state.redis)


@router.delete("")
async def delete_telegram(request: Request, _m: service.CurrentUser = Depends(MANAGE),
                          user: service.CurrentUser = Depends(require_owner),
                          _p: service.CurrentUser = Depends(require_pin("notify.change")),
                          db: AsyncSession = DB) -> dict[str, Any]:
    row = await tsvc.get_config(db, user.org_id)
    await tsvc.delete_config(db, user.org_id, redis=request.app.state.redis)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="notify.telegram.delete", target_type="notify_channel", target_id="telegram",
                           target_label=row.bot_username if row else None,
                           detail={"chat_masked": tsvc.mask_chat(row.chat_id) if row else None,
                                   "bot_username": row.bot_username if row else None}, ip=user.ip)
    await db.commit()
    await tsvc.sync_host_file(sessionmaker())
    return await _payload(db, user.org_id, request.app.state.redis)


@router.post("/pair/start")
async def pair_start(request: Request, _m: service.CurrentUser = Depends(MANAGE),
                     user: service.CurrentUser = Depends(require_owner),
                     _p: service.CurrentUser = Depends(require_pin("notify.change")),
                     db: AsyncSession = DB) -> dict[str, Any]:
    """Cấp mã ghép 6 số (sống 10 phút). Mã chỉ có trong phản hồi này — DB chỉ lưu băm, Action Log không có mã."""
    code, expires = await inbound.start_pairing(db, user.org_id, user.id)
    row = await tsvc.get_config(db, user.org_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action=inbound.ACTION_PAIR_START, target_type="notify_channel", target_id="telegram",
                           target_label=row.bot_username if row else None,
                           detail={"expires_at": tsvc._iso(expires)}, ip=user.ip)
    await db.commit()
    return {**await _payload(db, user.org_id, request.app.state.redis),
            "pair": {"code": code, "expires_at": tsvc._iso(expires),
                     "bot_username": row.bot_username if row else None}}


@router.put("/inbound")
async def put_inbound(body: InboundIn, request: Request, _m: service.CurrentUser = Depends(MANAGE),
                      user: service.CurrentUser = Depends(require_owner),
                      _p: service.CurrentUser = Depends(require_pin("notify.change")),
                      db: AsyncSession = DB) -> dict[str, Any]:
    """Bật/tắt "Nhận tin từ Sếp". Bật cần đã ghép (409 TELEGRAM_NOT_PAIRED)."""
    await inbound.set_inbound(db, user.org_id, body.enabled)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action=inbound.ACTION_INBOUND, target_type="notify_channel", target_id="telegram",
                           detail={"enabled": body.enabled}, ip=user.ip)
    await db.commit()
    return await _payload(db, user.org_id, request.app.state.redis)


@router.delete("/pair")
async def unpair(request: Request, _m: service.CurrentUser = Depends(MANAGE),
                 user: service.CurrentUser = Depends(require_owner),
                 _p: service.CurrentUser = Depends(require_pin("notify.change")),
                 db: AsyncSession = DB) -> dict[str, Any]:
    """Gỡ ghép + tắt nhận tin + huỷ mã đang chờ."""
    await inbound.unpair(db, user.org_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action=inbound.ACTION_UNPAIRED, target_type="notify_channel", target_id="telegram",
                           detail={}, ip=user.ip)
    await db.commit()
    return await _payload(db, user.org_id, request.app.state.redis)


@router.post("/find-chat")
async def find_chat(request: Request, body: FindChatIn | None = None, _m: service.CurrentUser = Depends(MANAGE),
                    user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    """Chỉ đọc — không lưu token, không ghi Action Log (thao tác tra cứu)."""
    actionlog.exempt()
    # Vòng nhận tin của worker đang giữ offset của bot — gọi getUpdates riêng (kể cả token dán trong thân) sẽ tranh/làm
    # mất tin và Telegram trả 409. Kiểm theo TỔ CHỨC cho cả hai nhánh.
    ib = await inbound.load(db, user.org_id)
    if ib is not None and (ib.inbound_enabled or inbound.pair_active(ib)):
        return {"chats": [], "error_code": "TELEGRAM_INBOUND_ACTIVE",
                "message": "Đang bật nhận tin từ Sếp nên không tìm chat_id kiểu cũ — dùng nút Ghép Telegram "
                           "(mã 6 số) hoặc tắt \"Nhận tin từ Sếp\" rồi tìm lại"}
    token = (body.token or "").strip() if body else ""
    if token:
        token = tsvc.validate_token(token)
    else:
        row = await tsvc.get_config(db, user.org_id)
        if row is None:
            return {"chats": [], "error_code": tsvc.NOT_CONFIGURED,
                    "message": "Dán token bot trước rồi bấm Tìm chat_id"}
        token = tsvc.decrypt_token(row)
    try:
        chats = await client(request).get_updates(token)
    except tg.TelegramError as e:
        return {"chats": [], "error_code": e.code, "message": tsvc.message_for(e.code)}
    if not chats:
        return {"chats": [], "error_code": None,
                "message": "Chưa thấy tin nào — Sếp mở bot trên Telegram, bấm Bắt đầu (Start) hoặc nhắn "
                           "một chữ bất kỳ, rồi bấm Tìm chat_id lần nữa"}
    return {"chats": chats, "error_code": None, "message": None}


async def run_test(request: Request, db: AsyncSession, user: service.CurrentUser) -> dict[str, Any]:
    """Dùng chung cho POST /notify/telegram/test và POST /boss-checks/telegram/run."""
    rec, detail = await tsvc.run_test(db, user.org_id, user.id, client=client(request),
                                      redis=request.app.state.redis)
    host_requested = tsvc.request_host_test() if rec.get("error_code") != tsvc.NOT_CONFIGURED else False
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="notify.telegram.test", target_type="notify_channel", target_id="telegram",
                           result="ok" if rec["status"] == "pass" else "failed",
                           detail={**detail, "host_requested": host_requested}, ip=user.ip)
    await db.commit()
    return {**rec, "host_requested": host_requested}


@router.post("/test")
async def test_telegram(request: Request, _m: service.CurrentUser = Depends(MANAGE),
                        user: service.CurrentUser = Depends(require_owner),
                        db: AsyncSession = DB) -> dict[str, Any]:
    return await run_test(request, db, user)


__all__ = ["router", "run_test"]
