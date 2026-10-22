"""WebSocket /api/v1/ws: đẩy sự kiện thời gian thực tới Console, lọc theo quyền người nhận.

Nguồn sự kiện: Redis pub/sub kênh `gh.ws` (api, worker, consumer đều publish được) → một bộ phát trong mỗi tiến trình
api → các kết nối WebSocket của tiến trình đó. Nhiều bản api chạy song song vẫn nhận đủ.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import orjson
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis

from gh.auth import rbac, service
from gh.config import get_settings
from gh.db import sessionmaker

log = logging.getLogger("gh.ws")
router = APIRouter()

CHANNEL = "gh.ws"

# v0.1.45 (F-55): kết nối WS nạp lại phiên định kỳ — phiên bị thu hồi/hết hạn, người dùng bị khoá/xoá hoặc phải đổi
# mật khẩu thì socket bị đóng trong ≤ SESSION_RECHECK_SECONDS; đổi vai trò/quyền có hiệu lực từ lượt nạp sau.
# Mã đóng: 4401 phiên hết/thu hồi · 4403 Origin sai hoặc phải đổi mật khẩu · 4428 chưa thiết lập · 1011 lỗi DB kéo dài.
SESSION_RECHECK_SECONDS: float = 60
SESSION_RECHECK_MAX_ERRORS = 3

# type → quyền cần có (None = mọi người đã đăng nhập).
EVENT_PERMISSION: dict[str, str | None] = {
    "raw.new": "data.read",
    "raw.state": "data.read",
    "refinery.progress": "data.read",
    "refinery.run": "data.read",
    "channel.qr": "system.read",
    "channel.status": "system.read",
    "group.discovered": "system.read",   # v0.1.65: nhóm mới phát hiện ⇒ thẻ kênh cập nhật (hỏi Sếp chỉ khi Hỏi Sếp)
    "listen.event": "data.read",         # v0.1.68: sự kiện kênh cho Nhật ký lắng nghe (chỉ tín hiệu, không câu chữ)
    "cli.login": "system.manage",
    "header": None,
}

# Sự kiện mang nội dung tin nhắn: vai trò dưới Owner nhận bản đã che số dài (khoá cứng 8).
MASKED_EVENTS = {"raw.new"}

# Sự kiện mang NGUYÊN VĂN một tin: tin 1-1 (`data.group` rỗng) chỉ gửi cho Owner — vai dưới (kể cả Kiểm soát có
# data.read) không nhận chuyện riêng của khách qua WebSocket (review v0.1.65 P2-2).
OWNER_ONLY_WHEN_DIRECT = {"raw.new"}

# Sự kiện tự khai báo `owner_only` trong gói tin (sự kiện 1-1 của Nhật ký lắng nghe — sự kiện kênh không thuộc nhóm
# nào cũng có `group` rỗng nên không dùng được quy tắc `OWNER_ONLY_WHEN_DIRECT`).
OWNER_ONLY_FLAGGED = {"listen.event"}

# Sự kiện mang nguyên văn tin của một NHÓM: theo «Quyền xem» của nhóm (v0.1.71) — nhóm «Chỉ Sếp» chỉ gửi cho Owner, nhóm
# «Sếp + quản lý» thêm vai Quản lý (cùng quy tắc `gh.biz.core.scope.view_scopes_for` của các API đọc).
GROUP_VIEW_SCOPED = {"raw.new", "draft.new", "draft.updated"}


async def _draft_group_id(draft_id: Any) -> str | None:
    """Nhóm mà một bản nháp gắn với (nhóm đích hoặc đối tượng là nhóm); None = không thuộc nhóm nào."""
    from sqlalchemy import text

    try:
        async with sessionmaker()() as db:
            r = (await db.execute(text("""SELECT COALESCE(group_id,
                                                          CASE WHEN subject_type = 'group' THEN subject_id END)
                                          FROM biz.action_drafts WHERE id = CAST(:d AS uuid)"""),
                                  {"d": str(draft_id)})).scalar_one_or_none()
            await db.rollback()
        return str(r) if r is not None else None
    except Exception as exc:  # noqa: BLE001 — lỗi tạm thời: giữ kín nhóm không rõ thay vì lộ
        log.warning("ws không đọc được nhóm của bản nháp: %s", type(exc).__name__)
        return "?"


async def _group_view_scope(group_id: Any) -> str | None:
    """`core.groups.view_scope` của nhóm; không đọc được / không có nhóm ⇒ None (coi như «Chỉ Sếp», an toàn)."""
    from sqlalchemy import text

    try:
        async with sessionmaker()() as db:
            r = (await db.execute(text("SELECT view_scope FROM core.groups WHERE id = CAST(:g AS uuid)"),
                                  {"g": str(group_id)})).scalar_one_or_none()
            await db.rollback()
        return str(r) if r is not None else None
    except Exception as exc:  # noqa: BLE001 — lỗi tạm thời: giữ kín thay vì lộ
        log.warning("ws không đọc được Quyền xem của nhóm: %s", type(exc).__name__)
        return None


def register_event(type: str, permission: str | None, *, masked: bool = False) -> None:
    """Các cụm màn giai đoạn 3 khai báo sự kiện WS của mình (gọi lúc import routes). Loại chưa khai báo bị bỏ."""
    if type in EVENT_PERMISSION and EVENT_PERMISSION[type] != permission:
        raise ValueError(f"Sự kiện {type} đã khai báo với quyền khác")
    EVENT_PERMISSION[type] = permission
    if masked:
        MASKED_EVENTS.add(type)


register_event("draft.new", "action.approve")
register_event("draft.updated", "action.approve")
# Gen v1: bước trả lời của khung chat — luôn kèm `to_user` (chỉ người hỏi nhận), xem gh.gen.engine.
# v0.1.23: thông báo chuông (`notification.new`, gh.notifications) cũng chỉ gửi đúng người nhận.
PRIVATE_PREFIXES = ("gen.", "notification.")
register_event("gen.step", None)
register_event("gen.done", None)


def mask_event(msg: dict[str, Any]) -> dict[str, Any]:
    from gh.data.common import mask_text

    data = dict(msg.get("data") or {})
    if isinstance(data.get("text"), str):
        data["text"] = mask_text(data["text"], False)
    data.pop("payload", None)
    return {**msg, "data": data}


async def publish(redis: Redis, type: str, data: dict[str, Any], *, org_id: Any = None,
                  to_user: Any = None) -> None:
    """`to_user` (v0.1.21, Gen): chỉ các kết nối của ĐÚNG người dùng đó nhận (khung chat Gen là riêng tư)."""
    msg = {"type": type, "data": data, "at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
           "org_id": str(org_id) if org_id else None, "to_user": str(to_user) if to_user else None}
    await redis.publish(CHANNEL, orjson.dumps(msg, default=str))


def allowed(permissions: dict[str, str], type: str) -> bool:
    if type not in EVENT_PERMISSION:
        return False
    need = EVENT_PERMISSION[type]
    if need is None:
        return True
    have = permissions.get(need, rbac.NONE)
    if need == "system.manage":
        # F-58 (đồng bộ với gh.auth.deps): quản trị hệ thống chỉ dành cho phạm vi ALL.
        return rbac.at_least(have, rbac.ALL)
    return have != rbac.NONE


_DEFAULT_PORTS = {"https": 443, "http": 80, "wss": 443, "ws": 80}


def _origin_key(url: str) -> tuple[str, str, int] | None:
    try:
        parts = urlsplit(url.strip())
        scheme, host, port = parts.scheme.lower(), (parts.hostname or "").lower(), parts.port
    except ValueError:
        return None
    if not scheme or not host:
        return None
    return scheme, host, port if port is not None else _DEFAULT_PORTS.get(scheme, 0)


def _netloc_key(netloc: str, default_port: int) -> tuple[str, int] | None:
    try:
        parts = urlsplit(f"//{netloc.strip()}")
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        return None
    if not host:
        return None
    return host, port if port is not None else default_port


def origin_allowed(origin: str | None, host: str | None, public_url: str) -> bool:
    """F-55: chặn trang lạ mở WS bằng cookie của Sếp (Cross-Site WebSocket Hijacking).

    Hợp lệ khi Origin trùng gốc của `public_url`, HOẶC host:port của Origin trùng header Host của chính yêu cầu WS
    (cùng gốc — Console mở bằng IP LAN vẫn chạy; trình duyệt không cho trang lạ giả Host). Thiếu Origin → từ chối.
    """
    if not origin or origin.strip().lower() == "null":
        return False
    o = _origin_key(origin)
    if o is None:
        return False
    if o == _origin_key(public_url):
        return True
    if host:
        h = _netloc_key(host, o[2])
        if h is not None and h == (o[1], o[2]):
            return True
    return False


class Hub:
    def __init__(self, redis: Redis):
        self.redis = redis
        self.clients: dict[WebSocket, service.CurrentUser] = {}
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._pump(), name="ws-hub")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        for ws in list(self.clients):
            with contextlib.suppress(Exception):
                await ws.close(code=1001)

    async def _pump(self) -> None:
        while True:
            try:
                pubsub = self.redis.pubsub()
                await pubsub.subscribe(CHANNEL)
                async for item in pubsub.listen():
                    if item.get("type") != "message":
                        continue
                    await self.dispatch(orjson.loads(item["data"]))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — mất Redis thì thử lại, không làm sập api
                log.warning("ws hub mất kết nối Redis: %s", exc)
                await asyncio.sleep(1)

    async def dispatch(self, msg: dict[str, Any]) -> None:
        org = msg.pop("org_id", None)
        to_user = msg.pop("to_user", None)
        if msg.get("type", "").startswith(PRIVATE_PREFIXES) and not to_user:
            return  # sự kiện riêng tư thiếu người nhận → bỏ, không phát cho cả tổ chức
        text = orjson.dumps(msg).decode()
        masked: str | None = None
        group_scope: str | None = None
        group_scope_read = False
        for ws, user in list(self.clients.items()):
            if org and str(user.org_id) != org:
                continue
            if to_user and str(user.id) != to_user:
                continue
            if not allowed(user.permissions, msg["type"]):
                continue
            if (msg["type"] in OWNER_ONLY_WHEN_DIRECT and user.role_code != rbac.OWNER
                    and (msg.get("data") or {}).get("group") is None):
                continue
            if (msg["type"] in OWNER_ONLY_FLAGGED and user.role_code != rbac.OWNER
                    and (msg.get("data") or {}).get("owner_only")):
                continue
            if msg["type"] in GROUP_VIEW_SCOPED and user.role_code != rbac.OWNER:
                if not group_scope_read:
                    data = msg.get("data") or {}
                    gid = (await _draft_group_id(data.get("id")) if msg["type"].startswith("draft.")
                           else (data.get("group") or {}).get("id"))
                    group_scope = (await _group_view_scope(gid) if gid else "all_members")
                    group_scope_read = True
                from gh.biz.core.scope import view_scopes_for

                if group_scope is None or group_scope not in (view_scopes_for(user.role_code) or ()):
                    continue
            out = text
            if msg["type"] in MASKED_EVENTS and user.role_code != rbac.OWNER:
                if masked is None:
                    masked = orjson.dumps(mask_event(msg)).decode()
                out = masked
            try:
                await ws.send_text(out)
            except Exception:  # noqa: BLE001 — client đã rời
                self.clients.pop(ws, None)


async def _close(ws: WebSocket, code: int, reason: str) -> None:
    with contextlib.suppress(Exception):
        await ws.close(code=code, reason=reason)


async def _watch_session(ws: WebSocket, token: str, hub: "Hub") -> None:
    """Nạp lại phiên mỗi SESSION_RECHECK_SECONDS (chỉ đọc — `renew=False` không gia hạn phiên/PIN).

    Mất phiên → rời hub + đóng 4401; còn hợp lệ → cập nhật người dùng (vai trò/quyền mới). Lỗi DB tạm thời giữ kết nối
    và thử lại lượt sau; SESSION_RECHECK_MAX_ERRORS lượt lỗi liên tiếp → đóng 1011.
    """
    errors = 0
    while True:
        await asyncio.sleep(SESSION_RECHECK_SECONDS)
        try:
            async with sessionmaker()() as db:
                user = await service.load_session(db, token, renew=False)
                await db.rollback()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — lỗi DB tạm thời: không đóng nhầm
            errors += 1
            log.warning("ws nạp lại phiên lỗi (%d/%d): %s", errors, SESSION_RECHECK_MAX_ERRORS,
                        type(exc).__name__)
            if errors >= SESSION_RECHECK_MAX_ERRORS:
                hub.clients.pop(ws, None)
                await _close(ws, 1011, "session_check_failed")
                return
            continue
        errors = 0
        if user is None or user.must_change_password:
            hub.clients.pop(ws, None)
            await _close(ws, 4401, "session_revoked")
            return
        if ws in hub.clients:
            hub.clients[ws] = user


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    from gh.setup.routes import console_ready

    origin = ws.headers.get("origin")
    if not origin_allowed(origin, ws.headers.get("host"), get_settings().public_url):
        log.warning("ws từ chối Origin không hợp lệ: %r", (origin or "")[:200])
        await ws.accept()
        await ws.close(code=4403, reason="origin")
        return
    token = ws.cookies.get(service.SESSION_COOKIE)
    async with sessionmaker()() as db:
        ready = await console_ready(db)
        user = await service.load_session(db, token) if token and ready else None
    await ws.accept()
    if not ready:
        await ws.close(code=4428)
        return
    if user is None or token is None:
        await ws.close(code=4401)
        return
    if user.must_change_password:
        await ws.close(code=4403, reason="password_change_required")
        return
    hub: Hub = ws.app.state.ws_hub
    hub.clients[ws] = user
    watcher = asyncio.create_task(_watch_session(ws, token, hub), name="ws-session-watch")
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = orjson.loads(raw)
            except orjson.JSONDecodeError:
                continue
            if isinstance(msg, dict) and msg.get("type") == "ping":
                await ws.send_text(orjson.dumps({"type": "pong", "data": {},
                                                 "at": datetime.now(UTC).isoformat()}).decode())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await watcher
        hub.clients.pop(ws, None)
