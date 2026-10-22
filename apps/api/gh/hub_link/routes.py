"""/hub — liên kết Gen-hub + đọc Kho dữ liệu (v0.1.26) + đọc lịch/mail/việc/Drive Google (v0.1.49, QD-16)
(docs/design/gen-hub-link.md §3.1, §6).

- `GET /hub/link` — trạng thái (không bao giờ có token) + `read_scopes` (quyền đọc thêm; null khi chưa có lần Kiểm tra
  xanh với địa chỉ/token hiện tại) + `breaker` (ngắt mạch F-83; chỉ Owner nhận đủ `retry_in_s`/`down_since`) ·
  `system.read`.
- `PATCH /hub/link` — địa chỉ, token (chỉ ghi), ngày hết hạn, mạng công cộng, tắt · Owner + PIN `hub.link`.
  v0.1.57 (Nợ #30): thêm `kho_label` (Tên Kho Owner tự đặt, ≤ 40 ký tự; rỗng ⇒ về mặc định "Kho dữ liệu"); `GET` và
  `PATCH` trả thêm `kho_label` (tên hiệu lực), `kho_label_custom`, `kho_label_default`, `kho_label_max`. Chỉ gửi
  `kho_label` thì không đụng liên kết (kể cả khi chưa nối Gen-hub).
- `POST /hub/link/test` — khám phá, mở + cấp `core.gen` đúng tool đọc (Kho + Google), gọi `kho_tom_tat`; trả thêm
  `read_scopes`, `read_missing`, `write_tools` · Owner + PIN.
- `GET /hub/kho/summary|search|records/{ma}` — đọc Kho (đã che, đệm 5 phút) · CHỈ Owner (quyết định Boss #1).
- `GET /hub/google/calendar?day=today|tomorrow`, `/hub/google/tasks`, `/hub/google/mail/search?q&limit`,
  `/hub/google/mail/message?id`, `/hub/google/drive/search?q` — đọc lịch/việc/mail/Drive qua Gen-hub (đã che, đệm
  5 phút, ngắt mạch) · CHỈ Owner (QD-16). Kết quả `{source, tool, cached, data}`. KHÔNG có đường ghi nào lên Google.
- v0.1.50 (F-81, QD-18): `GET /hub/link` thêm `write_scopes` ({kho, kho_create, kho_update: bool}) và `write_hidden`
  (tool ghi Kho Owner tự đóng ở MCP Hub) — cả hai null khi chưa có lần Kiểm tra xanh; `POST /hub/link/test` thêm
  `write_scopes` + `write_missing` + `write_hidden` (tool ghi Owner tự đóng ở MCP Hub — Kiểm tra không mở lại) +
  `exposed_write_tools`. `POST /hub/kho/write` — đường GHI Kho duy nhất
  (kho_create / kho_update, bảng Phiên và Việc): Owner + PIN `hub.write` + permit ký do `confirm_proposal` của Gen phát
  sau khi Sếp bấm Xác nhận (gh.hub_link.permit). Gọi thẳng không có permit hợp lệ → 403 `HUB_WRITE_PERMIT`.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.auth.deps import require, require_owner, require_pin
from gh.boss_checks import service as boss_checks
from gh.chassis.mcp_client import HTTPS_REQUIRED_MSG, McpBlockedNetwork, McpError, pin_endpoint
from gh.db import DB
from gh.errors import field_errors
from gh.gen.coach import cache as coach_cache
from gh.hub_link import KHO_LABEL_MAX, clean_kho_label
from gh.hub_link import service as hub

router = APIRouter(prefix="/hub", tags=["hub"])
READ = require("system.read", rbac.ALL)
MANAGE = require("system.manage", rbac.ALL)


def _client(request: Request) -> Any:
    return hub.client_for(getattr(request.app.state, "mcp_transport", None))


@router.get("/link")
async def get_link(request: Request, user: service.CurrentUser = Depends(READ),
                   db: AsyncSession = DB) -> dict[str, Any]:
    owner = user.role_code == rbac.OWNER
    link = await hub.load(db, user.org_id)
    out = hub.link_out(link, owner=owner)
    # Chưa nối / chưa từng Kiểm tra xanh / vừa đổi địa chỉ-token (liên kết tắt chờ kiểm lại) ⇒ null = "Chưa kiểm",
    # KHÔNG phải 4 quyền "Chưa" (thẻ sẽ giục tick quyền khi Sếp còn chưa nối).
    known = hub.scopes_known(link)
    out["read_scopes"] = await hub.read_scopes(db, user.org_id) if known else None
    out["write_scopes"] = await hub.write_scopes(db, user.org_id) if known else None  # v0.1.50 (F-81)
    # Tool ghi Kho Owner tự đóng ở MCP Hub — không lưu riêng, tính lại từ Action Log như lúc Kiểm tra (tải lại trang vẫn
    # nói đúng "Sếp đã tự đóng…", không giục tick ở Gen-hub).
    out["write_hidden"] = await hub.write_hidden(db, user.org_id) if known else None
    breaker = await hub.breaker_state(request.app.state.redis, user.org_id)
    out["breaker"] = breaker if owner else {"open": breaker["open"]}  # vai trò khác: chỉ biết đang mở hay không
    out.update(await hub.kho_label_out(db, user.org_id))                # v0.1.57: tên Kho hiệu lực (ai đọc cũng cần)
    return out


class LinkPatch(BaseModel):
    endpoint: str | None = Field(default=None, min_length=1, max_length=2000)
    token: str | None = Field(default=None, max_length=4000)
    token_expires_at: datetime | None = None
    allow_public_network: bool | None = None
    enabled: bool | None = None
    kho_label: str | None = Field(default=None, max_length=400)    # v0.1.57: ≤ 40 ký tự, kiểm ở route


@router.patch("/link")
async def patch_link(body: LinkPatch, request: Request, _m: service.CurrentUser = Depends(MANAGE),
                     _o: service.CurrentUser = Depends(require_owner),
                     user: service.CurrentUser = Depends(require_pin("hub.link")),
                     db: AsyncSession = DB) -> dict[str, Any]:
    errors: dict[str, str] = {}
    endpoint = body.endpoint.strip() if body.endpoint is not None else None
    if endpoint is not None:
        u = urlparse(endpoint)
        if u.scheme not in ("https", "http") or not u.hostname:
            errors["endpoint"] = "Địa chỉ phải dạng https://<máy chủ Gen-hub>/mcp"
        elif u.scheme == "http" and body.allow_public_network:
            errors["endpoint"] = "Gen-hub ở mạng công cộng phải dùng https://"
        elif hub.endpoint_forbidden(endpoint):
            errors["endpoint"] = hub.ENDPOINT_FORBIDDEN_MSG
    kho_label = clean_kho_label(body.kho_label) if body.kho_label is not None else None
    if kho_label is not None and len(kho_label) > KHO_LABEL_MAX:
        errors["kho_label"] = f"Tên Kho tối đa {KHO_LABEL_MAX} ký tự — Sếp rút gọn giúp em"
    token = body.token.strip() if body.token is not None else None
    if body.token is not None and (not token or len(token) < 8):
        errors["token"] = "Token quá ngắn — dán đúng token agent tạo trong Gen-hub"
    if body.enabled is True:
        errors["enabled"] = "Bấm \"Kiểm tra\" để bật liên kết"
    if body.token_expires_at is not None and body.token_expires_at.tzinfo is None:
        errors["token_expires_at"] = "Cần kèm múi giờ (ISO 8601)"
    if errors:
        raise field_errors(errors)
    # Sửa review v0.1.45: CÙNG quy tắc với lúc gọi (`pin_endpoint`) — Gen-hub luôn dùng token, nên http:// chỉ hợp lệ
    # khi máy Gen-hub ở cùng máy hoặc trong mạng nội bộ (10.x, 192.168.x, host.docker.internal…); http:// tới IP
    # công cộng ⇒ 422 ngay lúc lưu thay vì lưu được rồi "Kiểm tra" mới báo lỗi. Không phân giải được ⇒ cho qua.
    cur = await hub.load(db, user.org_id)
    eff_endpoint = endpoint or (cur.endpoint if cur is not None else None)
    if eff_endpoint and urlparse(eff_endpoint).scheme == "http" and (token or (cur is not None and cur.has_token)):
        try:
            await pin_endpoint(eff_endpoint, True, has_token=True)
        except McpBlockedNetwork as e:
            if str(e) == HTTPS_REQUIRED_MSG:
                raise field_errors({"endpoint": "Gen-hub ở mạng công cộng phải dùng https:// (http:// chỉ dùng được "
                                                "khi Gen-hub cùng máy hoặc trong mạng nội bộ)"}) from e
        except McpError:
            pass
    touches_link = (endpoint is not None or token is not None or body.allow_public_network is not None
                    or body.enabled is not None or "token_expires_at" in body.model_fields_set)
    if kho_label is not None:
        await hub.set_kho_label(db, user=user, label=kho_label)
    if touches_link or kho_label is None:      # chỉ đổi tên Kho thì KHÔNG đụng liên kết (kể cả khi chưa nối Gen-hub)
        row = await hub.upsert(db, request.app.state.redis, user=user, endpoint=endpoint, token=token or None,
                               token_expires_at=body.token_expires_at,
                               set_expiry="token_expires_at" in body.model_fields_set,
                               allow_public_network=body.allow_public_network, disable=body.enabled is False)
    else:
        row = await hub.load(db, user.org_id)
    out = hub.link_out(row)
    out.update(await hub.kho_label_out(db, user.org_id))
    return out


@router.post("/link/test")
async def test_link(request: Request, _m: service.CurrentUser = Depends(MANAGE),
                    _o: service.CurrentUser = Depends(require_owner),
                    user: service.CurrentUser = Depends(require_pin("hub.link")),
                    db: AsyncSession = DB) -> dict[str, Any]:
    out = await hub.test_link(db, request.app.state.redis, _client(request), user=user)
    # v0.1.68 (F028): ghi cả bản kiểm "Việc Sếp cần làm › Gen-hub" (cùng hàm với /boss-checks/hub/run) rồi làm mới
    # thẻ Gen.
    if await boss_checks.record_hub_result(db, user.org_id, out, user.id) is not None:
        await coach_cache.commit_and_invalidate(db, request.app.state.redis, user.org_id)
    return out


# ─── đọc Kho: chỉ Owner ────────────────────────────────────────────────────────

@router.get("/kho/summary")
async def kho_summary(request: Request, so_phien: int | None = Query(None, ge=1, le=5),
                      _r: service.CurrentUser = Depends(READ), user: service.CurrentUser = Depends(require_owner),
                      db: AsyncSession = DB) -> dict[str, Any]:
    args: dict[str, Any] = {"so_phien": so_phien} if so_phien else {}
    return await hub.call_hub(db, request.app.state.redis, _client(request), user=user, suffix="kho_tom_tat",
                              args=args)


@router.get("/kho/search")
async def kho_search(request: Request, q: str = Query(min_length=1, max_length=200),
                     bang: str | None = Query(None, max_length=40), _r: service.CurrentUser = Depends(READ),
                     user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    args: dict[str, Any] = {"text": q.strip()}
    if bang and bang.strip():
        args["bang"] = bang.strip()
    return await hub.call_hub(db, request.app.state.redis, _client(request), user=user, suffix="kho_search",
                              args=args)


@router.get("/kho/records/{ma}")
async def kho_record(ma: str, request: Request, _r: service.CurrentUser = Depends(READ),
                     user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    code = ma.strip().upper()
    if not hub.RECORD_RE.fullmatch(code):
        raise field_errors({"ma": "Mã bản ghi dạng VIEC-12, QD-3, PHIEN-1"})
    return await hub.call_hub(db, request.app.state.redis, _client(request), user=user, suffix="kho_find_by_id",
                              args={"id": code})


# ─── ghi Kho (v0.1.50, F-81): chỉ Owner + PIN + permit từ đề xuất đã Xác nhận ─────

class KhoWriteIn(BaseModel):
    proposal_id: uuid.UUID
    tool: str = Field(max_length=40)
    args: dict[str, Any]
    permit: dict[str, Any] | None = None  # thiếu → 403 HUB_WRITE_PERMIT (không phải 422)


@router.post("/kho/write")
async def kho_write(body: KhoWriteIn, request: Request, _o: service.CurrentUser = Depends(require_owner),
                    user: service.CurrentUser = Depends(require_pin("hub.write")),
                    db: AsyncSession = DB) -> dict[str, Any]:
    return await hub.write_kho(db, request.app.state.redis, _client(request), user=user,
                               proposal_id=body.proposal_id, tool=body.tool, args=body.args, permit=body.permit)


# ─── đọc Google qua Gen-hub (QD-16): chỉ Owner, chỉ đọc ────────────────────────

@router.get("/google/calendar")
async def google_calendar(request: Request, day: Literal["today", "tomorrow"] = Query("today"),
                          _r: service.CurrentUser = Depends(READ), user: service.CurrentUser = Depends(require_owner),
                          db: AsyncSession = DB) -> dict[str, Any]:
    target = hub.vn_today() + timedelta(days=1 if day == "tomorrow" else 0)
    args: dict[str, Any] = {**hub.vn_day_bounds(target), "maxResults": 20}
    return await hub.call_hub(db, request.app.state.redis, _client(request), user=user,
                              suffix="calendar_list_events", args=args)


@router.get("/google/tasks")
async def google_tasks(request: Request, _r: service.CurrentUser = Depends(READ),
                       user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return await hub.call_hub(db, request.app.state.redis, _client(request), user=user, suffix="tasks_list", args={})


def _query(q: str) -> str:
    text_ = q.strip()
    if not text_:
        raise field_errors({"q": "Nhập từ khoá cần tìm"})
    return text_


@router.get("/google/mail/search")
async def google_mail_search(request: Request, q: str = Query(min_length=1, max_length=200),
                             limit: int = Query(10, ge=1, le=10), _r: service.CurrentUser = Depends(READ),
                             user: service.CurrentUser = Depends(require_owner),
                             db: AsyncSession = DB) -> dict[str, Any]:
    return await hub.call_hub(db, request.app.state.redis, _client(request), user=user, suffix="gmail_search",
                              args={"query": _query(q), "maxResults": limit})


@router.get("/google/mail/message")
async def google_mail_message(request: Request, id: str = Query(max_length=200),  # noqa: A002 — tên tham số của API
                              _r: service.CurrentUser = Depends(READ),
                              user: service.CurrentUser = Depends(require_owner),
                              db: AsyncSession = DB) -> dict[str, Any]:
    message_id = id.strip()
    if not hub.GMAIL_ID_RE.fullmatch(message_id):
        raise field_errors({"id": "Mã thư dạng chữ-số 6–64 ký tự (lấy từ kết quả tìm thư)"})
    out = await hub.call_hub(db, request.app.state.redis, _client(request), user=user, suffix="gmail_read_message",
                             args={"messageId": message_id})
    out["data"] = hub.truncate_text(out["data"], hub.MAIL_TEXT_MAX)  # cắt SAU khi che (data đã che từ call_hub)
    return out


@router.get("/google/drive/search")
async def google_drive_search(request: Request, q: str = Query(min_length=1, max_length=200),
                              _r: service.CurrentUser = Depends(READ),
                              user: service.CurrentUser = Depends(require_owner),
                              db: AsyncSession = DB) -> dict[str, Any]:
    return await hub.call_hub(db, request.app.state.redis, _client(request), user=user, suffix="drive_search",
                              args={"query": _query(q), "maxResults": 10})


__all__ = ["router"]
