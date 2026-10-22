"""Middleware ASGI: Mã yêu cầu (X-Request-ID), chặn Console khi chưa thiết lập (428), bảo đảm mọi request ghi có dòng
Action Log."""

import logging
import re
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from gh.chassis import actionlog
from gh.db import sessionmaker
from gh.errors import ApiError, _body, request_id_var

log = logging.getLogger("gh.http")

API_PREFIX = "/api/v1"
# /auth/me KHÔNG được miễn: trước khi thiết lập xong, câu trả lời đúng là 428 (đi tới /setup), không phải 401.
# v0.1.44 (F-4b): /client-errors nhận báo lỗi giao diện cả khi chưa thiết lập xong (màn thiết lập cũng có thể lỗi).
SETUP_EXEMPT = ("/setup", "/auth/login", "/auth/logout", "/auth/pin", "/health", "/ready", "/docs", "/openapi.json",
                "/client-errors")
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


#: v0.1.40 (F-12): "Tải gói mang đi" tải bằng một khung ẩn CÙNG GỐC (gói rất lớn — không fetch→blob). Proxy Caddy đặt
#: `X-Frame-Options DENY` + CSP `frame-ancestors 'none'` cho mọi phản hồi CHƯA có ⇒ trang lỗi JSON (409/423/500…) bị
#: chặn trong khung, web không đọc được mã lỗi và Owner chờ 35 phút không có gì. Riêng đường này api tự đặt hai header
#: cho phép khung cùng gốc (Caddy dùng `?` nên giữ nguyên) — vẫn chặn mọi trang khác nhúng.
FRAMABLE_PATHS = frozenset({API_PREFIX + "/system/offsite/portable"})
FRAMABLE_HEADERS = [(b"x-frame-options", b"SAMEORIGIN"),
                    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'self'")]


#: Mã yêu cầu client gửi vào chỉ được nhận khi đúng dạng — còn lại sinh mới (không để chuỗi lạ chui vào log/header).
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
REQUEST_ID_HEADER = b"x-request-id"


class TrustedForwardedProto:
    """Review lần 2 v0.1.63 (P3-4): uvicorn chạy `proxy_headers=False` (không tin header chuyển tiếp của mọi kết nối)
    nên mất X-Forwarded-Proto ⇒ URL tuyệt đối api tự dựng (vd chuyển hướng thêm "/" của FastAPI) mang `http://` dù
    người dùng vào qua HTTPS. Middleware này đặt `scope["scheme"]` theo X-Forwarded-Proto (`http`/`https`; websocket
    ⇒ `ws`/`wss`) CHỈ khi kết nối đến từ proxy tin cậy hoặc loopback (`gh.auth.deps._from_trusted_proxy` — cùng luật
    với `client_ip`). Không đổi `scope["client"]` (IP kết nối thật)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            proto = next((v for k, v in scope.get("headers") or () if k == b"x-forwarded-proto"), b"")
            proto = proto.split(b",")[0].strip().lower()
            if proto in (b"http", b"https"):
                from gh.auth.deps import _from_trusted_proxy

                client = scope.get("client")
                if _from_trusted_proxy(client[0] if client else None):
                    https = proto == b"https"
                    scheme = ("wss" if https else "ws") if scope["type"] == "websocket" else proto.decode()
                    scope = {**scope, "scheme": scheme}
        await self.app(scope, receive, send)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


class RequestIdMiddleware:
    """v0.1.44 (F-4b) — NGOÀI CÙNG: gán Mã yêu cầu cho mọi request http.

    - Nhận `X-Request-ID` vào nếu khớp `REQUEST_ID_RE`, ngược lại sinh 16 hex; đặt `request_id_var` (problem+json, log)
      và `scope['state']['request_id']`.
    - Thêm header `x-request-id` vào MỌI phản hồi (kể cả 428 của SetupGate, 423, 500).
    - Ngoại lệ chưa xử lý tới đây TRƯỚC ServerErrorMiddleware của Starlette: chưa gửi phản hồi ⇒ trả 500 INTERNAL
      problem+json (error_id + request_id + header), ghi log khi Mã yêu cầu còn hiệu lực — không để Starlette trả
      text/plain không mã."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        incoming = next((v for k, v in scope.get("headers", []) if k.lower() == REQUEST_ID_HEADER), b"")
        raw = incoming.decode("latin-1")
        rid = raw if REQUEST_ID_RE.fullmatch(raw) else new_request_id()
        scope.setdefault("state", {})["request_id"] = rid
        token = request_id_var.set(rid)
        started = False

        async def send_with_id(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != REQUEST_ID_HEADER]
                message = {**message, "headers": [*headers, (REQUEST_ID_HEADER, rid.encode())]}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:
            if started:
                raise
            from starlette.requests import Request

            from gh.errors import _internal

            response = _internal(Request(scope), f"Lỗi không mong đợi ({type(exc).__name__})")
            await response(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)


class SameOriginFrame:
    """Đặt header cho phép khung cùng gốc trên `FRAMABLE_PATHS` — cả phản hồi lỗi của dependency (PIN/quyền) và 428."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] not in FRAMABLE_PATHS:
            return await self.app(scope, receive, send)

        async def send_framable(message: Message) -> None:
            if message["type"] == "http.response.start":
                names = {n for n, _ in FRAMABLE_HEADERS}
                headers = [(n, v) for n, v in message.get("headers", []) if n.lower() not in names]
                message = {**message, "headers": [*headers, *FRAMABLE_HEADERS]}
            await send(message)

        return await self.app(scope, receive, send_framable)


async def _problem(send: Send, err: ApiError) -> None:
    import orjson

    body = orjson.dumps(_body(err.status, err.code, err.title, err.detail, err.extra))
    await send({"type": "http.response.start", "status": err.status,
                "headers": [(b"content-type", b"application/problem+json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class SetupGate:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(API_PREFIX):
            return await self.app(scope, receive, send)
        sub = scope["path"][len(API_PREFIX):]
        if sub.startswith(SETUP_EXEMPT):
            return await self.app(scope, receive, send)
        app_state = scope["app"].state
        if not getattr(app_state, "console_ready", None):
            from gh.setup.routes import console_ready

            async with sessionmaker()() as db:
                app_state.console_ready = await console_ready(db) or None
        if not app_state.console_ready:
            return await _problem(send, ApiError(428, "SETUP_REQUIRED", "Hệ thống chưa thiết lập xong"))
        return await self.app(scope, receive, send)


class ActionLogGuard:
    """Mọi request ghi thành công mà route chưa ghi Action Log → ghi một dòng chung (không để lọt)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in WRITE_METHODS:
            return await self.app(scope, receive, send)
        marker: list[Any] = []
        token = actionlog.request_marker.set(marker)
        status = {"code": 500}

        async def _send(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                status["code"] = msg["status"]
            await send(msg)

        try:
            await self.app(scope, receive, _send)
        finally:
            actionlog.request_marker.reset(token)
        if 200 <= status["code"] < 300 and not marker:
            user = scope.get("state", {}).get("user")
            if user is None:
                return
            try:
                async with sessionmaker()() as db:
                    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                                           action=f"http.{scope['method'].lower()}", target_type="route",
                                           target_id=scope["path"],
                                           target_label=f"{scope['method']} {scope['path']}"[:200], ip=user.ip)
                    await db.commit()
            except Exception:  # noqa: BLE001
                log.exception("Không ghi được Action Log cho %s %s", scope["method"], scope["path"])


class SessionCookieRenewal:
    """Phiên vừa được gia hạn trượt (gh/auth/deps.py đặt `state.session_renewed`) → đặt lại cookie phiên + CSRF
    với hạn mới. Làm ở tầng ASGI để áp cho MỌI response, kể cả route trả Response trực tiếp."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def _send(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                expires = scope.get("state", {}).get("session_renewed")
                if expires is not None:
                    msg["headers"] = list(msg.get("headers", [])) + _renewed_cookie_headers(scope, expires)
            await send(msg)

        await self.app(scope, receive, _send)


def _renewed_cookie_headers(scope: Scope, expires: Any) -> list[tuple[bytes, bytes]]:
    from starlette.requests import Request
    from starlette.responses import Response

    from gh.auth import routes as auth_routes
    from gh.auth import service

    cookies = Request(scope).cookies
    token, csrf = cookies.get(service.SESSION_COOKIE), cookies.get(service.CSRF_COOKIE)
    if not token or not csrf:
        return []
    tmp = Response()
    auth_routes.set_session_cookies(tmp, service.NewSession(token=token, csrf=csrf, expires_at=expires))
    return [(k, v) for k, v in tmp.raw_headers if k == b"set-cookie"]
