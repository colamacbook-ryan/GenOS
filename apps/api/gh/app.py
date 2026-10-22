"""Nhà máy ứng dụng FastAPI."""

import asyncio
import contextlib
import json
import logging
import re
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

from gh import __version__, biz, health, realtime
from gh.agents_api.routes import router as agents_router
from gh.audit.routes import router as audit_router
from gh.auth import deps
from gh.auth.account import router as account_router
from gh.auth.routes import router as auth_router
from gh.auth.users import router as users_router
from gh.bootstrap import bootstrap
from gh.boss_checks.routes import router as boss_checks_router
from gh.chassis.bus import BRIDGE_DIRECTORY, BRIDGE_INBOUND, BRIDGE_STATUS, EventBus
from gh.chassis.plugins import Manifest, PluginManager
from gh.config import get_settings
from gh.data.ingest import Ingest
from gh.data_api.routes import router as data_router
from gh.db import dispose_engine, sessionmaker
from gh.defaults.routes import router as defaults_router
from gh.errors import (
    INFRA_ERRORS,
    ApiError,
    JsonResponse,
    api_error_handler,
    db_error_handler,
    http_error_handler,
    infra_error_handler,
    os_error_handler,
    request_id_var,
    unhandled_error_handler,
    validation_error_handler,
)
from gh.gen.coach.routes import router as coach_router
from gh.gen.routes import router as gen_router
from gh.hub_link.routes import router as hub_router
from gh.mcp_api.routes import router as mcp_router
from gh.middleware import (
    ActionLogGuard,
    RequestIdMiddleware,
    SameOriginFrame,
    SessionCookieRenewal,
    SetupGate,
    TrustedForwardedProto,
)
from gh.notifications import router as notifications_router
from gh.owner.routes import router as owner_router
from gh.owner_channel import jobs as owner_jobs
from gh.owner_channel.routes import router as owner_channel_router
from gh.plugins_api.routes import router as plugins_router
from gh.providers import cli as climod
from gh.providers import clients
from gh.providers.router import ModelRouter
from gh.refinery.jev_routes import router as jev_router
from gh.refinery.triage_routes import router as triage_router
from gh.setup.routes import router as setup_router
from gh.shell.routes import router as shell_router
from gh.social import service as social_service
from gh.social.routes import router as social_router
from gh.system_api.access import router as access_router
from gh.system_api.backups import router as backups_router
from gh.system_api.client_errors import router as client_errors_router
from gh.system_api.diagnostics import router as diagnostics_router
from gh.system_api.health import router as health_router
from gh.system_api.offsite import router as offsite_router
from gh.system_api.org import router as org_router
from gh.system_api.routes import router as system_router
from gh.system_api.update import router as update_router
from gh.telegram import service as telegram_service
from gh.telegram.routes import router as telegram_router

log = logging.getLogger("gh.app")


async def _persist_plugin(package: str, change: dict[str, Any]) -> None:
    async with sessionmaker()() as db:
        if change.get("_deleted"):
            await db.execute(text("DELETE FROM ops.plugins WHERE package = :p AND origin <> 'core'"), {"p": package})
        elif "is_enabled" in change:
            await db.execute(text("UPDATE ops.plugins SET is_enabled = :e WHERE package = :p"),
                             {"e": change["is_enabled"], "p": package})
        await db.commit()


async def _breaker_sink(package: str, state: str, reason: str | None) -> None:
    async with sessionmaker()() as db:
        await db.execute(text("""INSERT INTO ops.breaker_events (plugin_id, state, reason)
                                 SELECT id, :s, :r FROM ops.plugins WHERE package = :p"""),
                         {"s": state, "r": reason, "p": package})
        await db.commit()


def _plugin_log_sink(redis: Redis) -> Any:
    """Bền vững hoá dòng log plugin (`ops.plugin_logs`, PLAN 4.4) + đẩy LIVE qua WebSocket (`gh.realtime`)."""

    async def sink(package: str, level: str, message: str) -> None:
        msg = message[:2000]
        async with sessionmaker()() as db:
            pid = (await db.execute(text("SELECT id FROM ops.plugins WHERE package = :p"), {"p": package})
                  ).scalar_one_or_none()
            if pid is None:
                return
            await db.execute(text("INSERT INTO ops.plugin_logs (plugin_id, level, message) VALUES (:i, :l, :m)"),
                             {"i": pid, "l": level, "m": msg})
            await db.commit()
        with contextlib.suppress(Exception):
            await realtime.publish(redis, "plugin.log", {"package": package, "level": level, "message": msg})

    return sink


async def build_plugin_manager(bus: EventBus | None, redis: Redis) -> PluginManager:
    pm = PluginManager(bus, persist=_persist_plugin, breaker_sink=_breaker_sink,
                       log_sink=_plugin_log_sink(redis))
    async with sessionmaker()() as db:
        # permissions_status = 'pending' (nạp từ tệp chưa duyệt quyền, PLAN 4.4) KHÔNG được nạp ở đây — tránh
        # instantiate + on_load() một manifest do người dùng tải lên (mã lạ không kiểm soát) chỉ vì nó nằm
        # trong CSDL; xem docstring `gh.plugins_api.routes.install_local`.
        rows = (await db.execute(text(
            "SELECT origin, is_enabled, manifest, settings FROM ops.plugins WHERE permissions_status = 'approved'"))
               ).all()
    for r in rows:
        try:
            pm.register(Manifest.model_validate(r.manifest), origin=r.origin, enabled=r.is_enabled,
                        settings=r.settings or {})
        except Exception as exc:  # noqa: BLE001 — manifest hỏng không làm sập API
            log.error("Bỏ qua plugin có manifest lỗi: %s", exc)
    await pm.load_all()
    return pm


async def _permit_sweep_loop(sm: Any, redis: Redis, stop: asyncio.Event, interval_s: float = 15.0) -> None:
    """Giai đoạn 5.4: quét định kỳ permit gửi tin đã hết hạn mà bridge chưa báo kết quả (rớt giữa chừng) —
    xem `gh.biz.core.drafts.expire_stale_permits`. Vòng lặp không được chết vì một lượt lỗi (DB/Redis tạm mất
    kết nối): log rồi thử lại sau `interval_s`, giống các consumer khác (`EventBus.run`)."""
    from gh.biz.core import drafts as core_drafts

    while not stop.is_set():
        try:
            async with sm() as db:
                await core_drafts.expire_stale_permits(db, redis)
                await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — vòng quét không được chết vì một lượt lỗi
            log.error("quét permit hết hạn lỗi: %s", exc)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_s)


def start_ingest(app: FastAPI, stop: asyncio.Event) -> list[asyncio.Task[None]]:
    """Consumer luồng từ bridge: tin nhắn vào Kho thô, trạng thái phiên kênh, danh bạ nhóm."""
    ingest = Ingest(app.state.bus, app.state.redis, app.state.org_id, sessionmaker())
    consumer = f"api-{socket.gethostname()}"
    return [asyncio.create_task(app.state.bus.run(stream, Ingest.GROUP, consumer, handler, stop),
                                name=f"ingest:{stream}")
            for stream, handler in ((BRIDGE_INBOUND, ingest.on_inbound), (BRIDGE_STATUS, ingest.on_status),
                                    (BRIDGE_DIRECTORY, ingest.on_directory))]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    async with sessionmaker()() as db:
        result = await bootstrap(db)
        await db.commit()
    app.state.org_id = result.org_id
    # Review lần 2 v0.1.63 (P3-5): HOME tạm agy (có token) còn sót khi tiến trình trước bị SIGKILL.
    with contextlib.suppress(Exception):
        await asyncio.to_thread(clients.sweep_stale_turn_dirs)
    # Review lần 2 v0.1.63 (P3-2): phân giải tên proxy tin cậy (Caddy) trước yêu cầu đầu — sau đó làm mới ở luồng nền.
    with contextlib.suppress(Exception):
        await asyncio.to_thread(deps.refresh_trusted_proxies)
    # v0.1.44 (F-8c): đồng bộ run/telegram.json cho Trực canh máy chủ (sau nhập gói/đổi khoá vẫn khớp) — best-effort.
    await telegram_service.sync_host_file(sessionmaker())
    app.state.console_ready = None
    app.state.redis = Redis.from_url(s.redis_url, decode_responses=False)
    app.state.bus = EventBus(app.state.redis, s.stream_maxlen)
    # Transport HTTP tiêm được cho McpClient (gh.chassis.mcp_client) — None = httpx thật; test thay bằng
    # httpx.MockTransport trên chính app.state sau khi app dựng xong (cùng cách gh.providers.router làm).
    app.state.mcp_transport = None
    # v0.1.44 (F-8c): transport HTTP tiêm được cho Telegram Bot API (gh.telegram.client) — cùng cách mcp_transport.
    app.state.telegram_transport = None
    app.state.plugins = await build_plugin_manager(app.state.bus, app.state.redis)
    app.state.plugins.start_control_listener()
    sm = sessionmaker()
    app.state.ws_hub = realtime.Hub(app.state.redis)
    app.state.ws_hub.start()
    app.state.model_router = ModelRouter(sm, app.state.redis)
    app.state.cli_logins = climod.CliLogins(sm, app.state.redis)
    with contextlib.suppress(Exception):
        await climod.restore_active(sm, redis=app.state.redis)
    stop = asyncio.Event()
    consumers = start_ingest(app, stop)
    consumers.append(asyncio.create_task(_permit_sweep_loop(sm, app.state.redis, stop), name="permit-sweep"))
    # v0.1.72: kênh quản trị Zalo/WhatsApp — tin 1-1 của Sếp, mã ghép (gh.owner.inbound).
    consumers.extend(owner_jobs.start(app, stop, sm))
    # v0.1.29: kết quả đã ký từ browser-worker (gh:browser:results) → DB, phiên mã hoá, chuông Owner.
    consumers.append(asyncio.create_task(
        social_service.consume_results(sm, app.state.redis, stop, f"api-{socket.gethostname()}"),
        name="social-results"))
    # v0.1.36 (F-6): vòng theo dõi sức khoẻ — mở/đóng sự cố (cập nhật lỗi, quá 36 giờ chưa sao lưu, bộ xử lý nền im,
    # ổ đĩa sắp đầy) và gửi chuông MỘT lần mỗi sự cố. `health_started_at` cho biết api đã chạy bao lâu (worker chưa
    # từng ghi nhịp mà api mới khởi động ⇒ 'unknown', chưa báo im).
    app.state.health_started_at = datetime.now(UTC)
    if s.health_watch_seconds > 0:
        consumers.append(asyncio.create_task(
            health.watch_loop(sm, app.state.redis, stop, interval=s.health_watch_seconds,
                              started_at=app.state.health_started_at), name="health-watch"))
    log.info("Gen-Harness API %s sẵn sàng", __version__)
    try:
        yield
    finally:
        stop.set()
        background = [*consumers, *getattr(app.state, "gen_tasks", ())]
        for t in background:
            t.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        await app.state.cli_logins.shutdown()
        await app.state.ws_hub.stop()
        await app.state.plugins.shutdown()
        await social_service.close_bus(app.state.redis)
        await app.state.redis.aclose()
        await dispose_engine()


def create_app(*, with_lifespan: bool = True, expose_docs: bool | None = None) -> FastAPI:
    # v0.1.35 (F-43): production KHÔNG mở /docs, /openapi.json (lộ toàn bộ bề mặt API); redoc tắt hẳn.
    expose = (not get_settings().is_production) if expose_docs is None else expose_docs
    app = FastAPI(title="Gen-Harness API", version=__version__, default_response_class=JsonResponse,
                  lifespan=lifespan if with_lifespan else None,
                  docs_url="/api/v1/docs" if expose else None,
                  openapi_url="/api/v1/openapi.json" if expose else None, redoc_url=None)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    # v0.1.44 (F-4b): 404/405 của router cũng là problem+json (có request_id), không {"detail": "Not Found"}.
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(DBAPIError, db_error_handler)
    # Starlette chọn handler theo MRO: lớp con mất kết nối (ConnectionError/TimeoutError/redis) thắng OSError chung.
    for exc_cls in INFRA_ERRORS:
        app.add_exception_handler(exc_cls, infra_error_handler)
    app.add_exception_handler(OSError, os_error_handler)
    # Lưới cuối: ngoại lệ lạ (KeyError, ValueError…) vẫn là problem+json 500 INTERNAL kèm error_id, không text/plain.
    app.add_exception_handler(Exception, unhandled_error_handler)
    for r in (auth_router, account_router, users_router, setup_router, shell_router, audit_router, plugins_router,
             mcp_router, data_router, system_router, update_router, backups_router, offsite_router, org_router,
             gen_router, coach_router, notifications_router, triage_router, hub_router, social_router, health_router,
             boss_checks_router, telegram_router, diagnostics_router, client_errors_router, access_router,
             defaults_router, jev_router, owner_router, owner_channel_router):
        app.include_router(r, prefix="/api/v1")
    for r in biz.routers():
        app.include_router(r, prefix="/api/v1")
    # agents_router SAU biz.routers(): "/agents/{agent_id}" (một đoạn biến) không được đứng trước
    # "/agents/decisions" (literal, gh.biz.core.routes) — Starlette so khớp theo thứ tự đăng ký.
    app.include_router(agents_router, prefix="/api/v1")
    app.include_router(realtime.router, prefix="/api/v1")
    # Thứ tự: middleware thêm sau bọc ngoài cùng.
    app.add_middleware(SessionCookieRenewal)
    app.add_middleware(ActionLogGuard)
    app.add_middleware(SetupGate)
    app.add_middleware(SameOriginFrame)  # cả 428 của SetupGate cũng mang header khung cùng gốc
    # v0.1.44 (F-4b) NGOÀI CÙNG: Mã yêu cầu cho mọi phản hồi (kể cả 428/423/500) + lưới 500 problem+json.
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(TrustedForwardedProto)  # ngoài cùng: mọi lớp trong thấy đúng scheme (P3-4, review lần 2)
    return app


#: Thuộc tính chuẩn của LogRecord — khoá khác trong `record.__dict__` là trường `extra=` (error_id, method, path…).
_LOG_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime",
                                                                                         "taskName"}


#: Che nhẹ bí mật trong log JSON (production) — `genh doctor` gói `docker compose logs` vào tệp Sếp gửi hỗ trợ, nên
#: thông điệp/traceback có URL kèm mật khẩu, token trong query hay header không được lọt nguyên văn.
_LOG_REDACT: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b([a-z][a-z0-9+.\-]*://)[^/\s:@'\"]+:[^/\s@'\"]*@", re.I), r"\1***:***@"),  # userinfo trong URL
    (re.compile(r"\bBearer\s+[^\s'\",]+", re.I), "Bearer ***"),
    (re.compile(r"((?:access_token|refresh_token|id_token|token|api[_-]?key|apikey|password|passwd|secret|"
                r"client_secret)\s*[=:]\s*['\"]?)[^\s&'\",;}]+", re.I), r"\1***"),
    (re.compile(r"([?&]code=)[^\s&'\"]+"), r"\1***"),                                            # mã OAuth
    # v0.1.44 (F-8c): token bot Telegram (`123456789:AA…`, cả dạng `/bot<token>/` trong URL Bot API).
    (re.compile(r"(?:bot)?\d{5,12}:[A-Za-z0-9_-]{30,}"), "***"),
)


def _redact_log(text: str) -> str:
    for pat, repl in _LOG_REDACT:
        text = pat.sub(repl, text)
    return text


def _record_request_id(record: logging.LogRecord) -> str | None:
    rid = getattr(record, "request_id", None)
    return rid if isinstance(rid, str) and rid not in ("", "-") else request_id_var.get()


class JsonFormatter(logging.Formatter):
    """v0.1.36 (F-4): một dòng JSON mỗi bản ghi log (production) — tra được theo thời điểm/mã lỗi.

    Khoá: `ts` (ISO UTC, mili giây, hậu tố 'Z'), `level`, `logger`, `msg`; `exc` (traceback) khi có ngoại lệ, `stack`
    khi `stack_info=True`; mọi trường `extra=` (vd. `error_id`, `method`, `path` từ gh.errors) ở cấp ngoài cùng.
    Chuỗi đi qua `_redact_log` (mật khẩu trong URL, Bearer, token=/password=…) trước khi ghi."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        out: dict[str, Any] = {"ts": ts, "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
                               # v0.1.44 (F-4b): mọi dòng có khoá request_id (null ngoài request).
                               "request_id": _record_request_id(record)}
        if record.exc_info and record.exc_info[0] is not None:
            out["exc"] = self.formatException(record.exc_info)
        elif record.exc_text:
            out["exc"] = record.exc_text
        if record.stack_info:
            out["stack"] = self.formatStack(record.stack_info)
        for k, v in record.__dict__.items():
            if k not in _LOG_RECORD_ATTRS and not k.startswith("_") and k not in out:
                out[k] = v
        for k, v in out.items():
            if isinstance(v, str) and k not in ("ts", "level", "logger"):
                out[k] = _redact_log(v)
        return json.dumps(out, ensure_ascii=False, default=str)


class RequestIdFilter(logging.Filter):
    """v0.1.44 (F-4b): gắn `record.request_id` (Mã yêu cầu của request đang xử lý, từ contextvar) — đặt trên MỌI
    handler; formatter dev in `[rid]`, JsonFormatter in khoá `request_id`."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.request_id = request_id_var.get() or "-"
        return True


class RedactFilter(logging.Filter):
    """v0.1.44 (F-8c): che bí mật (`_LOG_REDACT` — gồm token bot Telegram) trong thông điệp và traceback của MỌI bản
    ghi, kể cả formatter dev (dạng chữ) — JsonFormatter vẫn che thêm mọi trường chuỗi."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 — thông điệp hỏng: để formatter tự báo
            return True
        clean = _redact_log(msg)
        if clean != msg:
            record.msg, record.args = clean, ()
        if record.exc_info and record.exc_info[0] is not None and not record.exc_text:
            record.exc_text = _redact_log(logging.Formatter().formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = _redact_log(record.exc_text)
        return True


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if get_settings().is_production else logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s"))
    handler.addFilter(RequestIdFilter())
    handler.addFilter(RedactFilter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # httpx ghi URL đầy đủ ở mức INFO — URL Bot API Telegram chứa token (`/bot<token>/sendMessage`).
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
