"""v0.1.45 (F-55): WebSocket kiểm Origin (chống CSWSH), chặn phiên phải đổi mật khẩu, nạp lại phiên định kỳ — phiên
bị thu hồi / người dùng bị khoá thì socket đóng 4401 trong ≤ SESSION_RECHECK_SECONDS; đổi vai trò có hiệu lực ngay
lượt nạp sau. F-58 đồng bộ ở WS: sự kiện system.manage chỉ tới phạm vi ALL."""

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import orjson
import pytest
from fastapi import WebSocketDisconnect
from sqlalchemy import text

from gh import realtime
from gh.auth import rbac, service
from gh.db import admin_sessionmaker, sessionmaker
from tests.conftest import Api

PUBLIC = "https://localhost:8443"


# ─── (1) origin_allowed: hàm thuần ─────────────────────────────────────────────

def test_origin_allowed_unit() -> None:
    assert realtime.origin_allowed("https://localhost:8443", "whatever:1", PUBLIC)
    assert realtime.origin_allowed("HTTPS://LocalHost:8443", None, PUBLIC)
    assert realtime.origin_allowed("https://gh.example.vn", None, "https://gh.example.vn:443/")
    # Console mở bằng IP LAN: Origin trùng Host của chính yêu cầu WS.
    assert realtime.origin_allowed("https://192.168.1.10:8443", "192.168.1.10:8443", PUBLIC)
    assert realtime.origin_allowed("http://192.168.1.10", "192.168.1.10", PUBLIC)
    assert not realtime.origin_allowed("https://evil.example", "192.168.1.10:8443", PUBLIC)
    assert not realtime.origin_allowed("https://evil.example", "evil.example:8443", PUBLIC)
    assert not realtime.origin_allowed(None, "192.168.1.10:8443", PUBLIC)
    assert not realtime.origin_allowed("", "localhost:8443", PUBLIC)
    assert not realtime.origin_allowed("null", "localhost:8443", PUBLIC)
    # Khác cổng → khác gốc.
    assert not realtime.origin_allowed("https://localhost:9443", "localhost:8443", PUBLIC)
    assert not realtime.origin_allowed("https://192.168.1.10:9443", "192.168.1.10:8443", PUBLIC)


def test_recheck_default_is_at_most_60s() -> None:
    assert realtime.SESSION_RECHECK_SECONDS <= 60


def test_system_manage_event_needs_all_scope() -> None:
    assert realtime.allowed({"system.manage": rbac.ALL}, "cli.login")
    assert not realtime.allowed({"system.manage": rbac.TEAM}, "cli.login")
    assert not realtime.allowed({}, "cli.login")
    assert realtime.allowed({"data.read": rbac.TEAM}, "raw.new")  # quyền khác giữ như cũ


# ─── WebSocket giả: đủ bề mặt mà ws_endpoint dùng ──────────────────────────────

class _State:
    def __init__(self, hub: realtime.Hub) -> None:
        self.ws_hub = hub


class _App:
    def __init__(self, hub: realtime.Hub) -> None:
        self.state = _State(hub)


class FakeWs:
    def __init__(self, hub: realtime.Hub, *, token: str | None, origin: str | None,
                 host: str = "localhost:8443") -> None:
        self.app = _App(hub)
        self.cookies = {service.SESSION_COOKIE: token} if token else {}
        self.headers = {k: v for k, v in (("origin", origin), ("host", host)) if v is not None}
        self.accepted = False
        self.closed: tuple[int, str | None] | None = None
        self.sent: list[dict[str, Any]] = []
        self._q: asyncio.Queue[str | None] = asyncio.Queue()

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        if self.closed is None:
            self.closed = (code, reason)
        self._q.put_nowait(None)

    async def receive_text(self) -> str:
        item = await self._q.get()
        if item is None:
            raise WebSocketDisconnect(self.closed[0] if self.closed else 1000)
        return item

    async def send_text(self, t: str) -> None:
        if self.closed is not None:
            raise RuntimeError("closed")
        self.sent.append(orjson.loads(t))


def _token(api: Api) -> str:
    tok = api.c.cookies.get(service.SESSION_COOKIE)
    assert tok
    return tok


async def _connect(app: Any, token: str | None, origin: str | None = PUBLIC,
                   host: str = "localhost:8443") -> tuple[FakeWs, asyncio.Task[None]]:
    hub: realtime.Hub = app.state.ws_hub
    ws = FakeWs(hub, token=token, origin=origin, host=host)
    task = asyncio.create_task(realtime.ws_endpoint(ws))  # type: ignore[arg-type]
    for _ in range(200):
        if ws in hub.clients or task.done():
            break
        await asyncio.sleep(0.01)
    return ws, task


async def _wait_closed(ws: FakeWs, task: asyncio.Task[None], within: float = 1.0) -> None:
    await asyncio.wait_for(task, within)
    assert ws.closed is not None


async def _admin_sql(sql: str, params: dict[str, Any]) -> None:
    async with admin_sessionmaker()() as db:
        await db.execute(text(sql), params)
        await db.commit()


async def _owner_id() -> uuid.UUID:
    async with admin_sessionmaker()() as db:
        return (await db.execute(text("""SELECT u.id FROM core.users u JOIN core.user_roles ur ON ur.user_id = u.id
                                          JOIN core.roles r ON r.id = ur.role_id WHERE r.code = 'owner'
                                          LIMIT 1"""))).scalar_one()  # type: ignore[no-any-return]


# ─── (2) Tích hợp: Origin + nhận sự kiện ───────────────────────────────────────

async def test_bad_origin_closes_4403_before_hub(app: Any, owner_api: Api) -> None:
    hub: realtime.Hub = app.state.ws_hub
    for origin in ("https://evil.example", None):
        ws, task = await _connect(app, _token(owner_api), origin=origin)
        await _wait_closed(ws, task)
        assert ws.closed == (4403, "origin")
        assert ws not in hub.clients


async def test_good_origin_receives_header_event(app: Any, owner_api: Api) -> None:
    hub: realtime.Hub = app.state.ws_hub
    # Cùng Host (mở Console bằng IP LAN) cũng được.
    for origin, host in ((PUBLIC, "localhost:8443"), ("https://192.168.1.10:8443", "192.168.1.10:8443")):
        ws, task = await _connect(app, _token(owner_api), origin=origin, host=host)
        assert ws in hub.clients and ws.closed is None
        await hub.dispatch({"type": "header", "data": {"x": 1}, "org_id": str(hub.clients[ws].org_id)})
        assert [m["type"] for m in ws.sent] == ["header"]
        await ws.close(1000)
        await _wait_closed(ws, task)
        assert ws not in hub.clients


async def test_must_change_password_closes_4403(app: Any, owner_api: Api) -> None:
    await _admin_sql("UPDATE core.users SET must_change_password = true WHERE id = :u", {"u": await _owner_id()})
    ws, task = await _connect(app, _token(owner_api))
    await _wait_closed(ws, task)
    assert ws.closed == (4403, "password_change_required")
    assert ws not in app.state.ws_hub.clients


# ─── (3) Thu hồi / khoá / đổi vai trò khi đang kết nối ─────────────────────────

@pytest.fixture
def fast_recheck(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(realtime, "SESSION_RECHECK_SECONDS", 0.05)


async def test_revoked_session_closes_4401(app: Any, owner_api: Api, fast_recheck: None) -> None:
    hub: realtime.Hub = app.state.ws_hub
    ws, task = await _connect(app, _token(owner_api))
    assert ws in hub.clients
    await _admin_sql("UPDATE core.sessions SET revoked_at = now() WHERE token_hash = :h",
                     {"h": service.token_digest(_token(owner_api))})
    await _wait_closed(ws, task, 1.0)
    assert ws.closed == (4401, "session_revoked")
    assert ws not in hub.clients


async def test_locked_user_closes_4401(app: Any, owner_api: Api, fast_recheck: None) -> None:
    hub: realtime.Hub = app.state.ws_hub
    ws, task = await _connect(app, _token(owner_api))
    assert ws in hub.clients
    await _admin_sql("UPDATE core.users SET is_active = false WHERE id = :u", {"u": await _owner_id()})
    await _wait_closed(ws, task, 1.0)
    assert ws.closed == (4401, "session_revoked")
    assert ws not in hub.clients


async def test_role_change_applies_next_recheck(app: Any, owner_api: Api, fast_recheck: None) -> None:
    hub: realtime.Hub = app.state.ws_hub
    ws, task = await _connect(app, _token(owner_api))
    user = hub.clients[ws]
    assert user.role_code == rbac.OWNER
    await hub.dispatch({"type": "cli.login", "data": {}, "org_id": str(user.org_id)})
    assert [m["type"] for m in ws.sent] == ["cli.login"]

    await _admin_sql("""UPDATE core.user_roles SET role_id = (SELECT id FROM core.roles
                                                              WHERE org_id = :o AND code = 'auditor')
                        WHERE user_id = :u""", {"o": user.org_id, "u": user.id})
    for _ in range(100):
        if hub.clients.get(ws) is not None and hub.clients[ws].role_code == rbac.AUDITOR:
            break
        await asyncio.sleep(0.01)
    assert hub.clients[ws].role_code == rbac.AUDITOR
    await hub.dispatch({"type": "cli.login", "data": {}, "org_id": str(user.org_id)})
    assert [m["type"] for m in ws.sent] == ["cli.login"]  # Auditor không còn nhận sự kiện system.manage
    assert ws.closed is None
    await ws.close(1000)
    await _wait_closed(ws, task)


async def test_transient_db_errors_keep_then_close_1011(app: Any, owner_api: Api, fast_recheck: None,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    hub: realtime.Hub = app.state.ws_hub
    ws, task = await _connect(app, _token(owner_api))
    calls = {"n": 0}

    async def boom(*a: Any, **kw: Any) -> None:
        calls["n"] += 1
        raise ConnectionError("db down")

    monkeypatch.setattr(service, "load_session", boom)
    await _wait_closed(ws, task, 2.0)
    assert calls["n"] == realtime.SESSION_RECHECK_MAX_ERRORS
    assert ws.closed is not None and ws.closed[0] == 1011
    assert ws not in hub.clients


# ─── (4) load_session(renew=False) chỉ đọc ─────────────────────────────────────

async def test_load_session_without_renew_is_read_only(owner_api: Api) -> None:
    token = _token(owner_api)
    h = service.token_digest(token)
    await _admin_sql("""UPDATE core.sessions SET expires_at = now() + interval '1 hour',
                               pin_verified_until = now() + interval '1 minute',
                               last_seen_at = now() - interval '1 day' WHERE token_hash = :h""", {"h": h})

    async def snapshot() -> Any:
        async with admin_sessionmaker()() as db:
            return (await db.execute(text("""SELECT expires_at, pin_verified_until, last_seen_at FROM core.sessions
                                              WHERE token_hash = :h"""), {"h": h})).one()

    before = await snapshot()
    async with sessionmaker()() as db:
        user = await service.load_session(db, token, renew=False)
        await db.commit()
    assert user is not None and user.session_renewed is False
    assert tuple(await snapshot()) == tuple(before)

    # Mặc định (HTTP) vẫn gia hạn phiên đăng nhập trượt; phiên PIN (v0.1.61, F115) chỉ gia hạn khi nơi gọi bật
    # `renew_pin` (lời gọi ghi do người bấm) — GET tự làm mới không giữ PIN sống mãi.
    async with sessionmaker()() as db:
        user = await service.load_session(db, token)
        await db.commit()
    after = await snapshot()
    assert user is not None and user.session_renewed is True
    assert after.expires_at - before.expires_at > timedelta(hours=1)
    assert after.pin_verified_until == before.pin_verified_until

    async with sessionmaker()() as db:
        user = await service.load_session(db, token, renew_pin=True)
        await db.commit()
    after = await snapshot()
    assert user is not None
    assert after.pin_verified_until > before.pin_verified_until
