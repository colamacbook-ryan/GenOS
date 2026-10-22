"""v0.1.35 (F-43): lỗi không rò chi tiết kỹ thuật — SQL/tham số/tên ràng buộc, địa chỉ IP nội bộ chỉ nằm trong log
server; người dùng nhận 500 INTERNAL kèm mã lỗi. /docs tắt ở production. Action Log đăng nhập sai che email."""

import errno
import logging
import re
import socket
from typing import Any

import httpx
import orjson
import pytest
from redis import exceptions as redis_exc
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from starlette.requests import Request

from gh.errors import db_error_handler, infra_error_handler, os_error_handler
from tests.conftest import Api

IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _req(path: str = "/api/v1/x", method: str = "POST") -> Request:
    return Request({"type": "http", "method": method, "path": path, "headers": [], "query_string": b"",
                    "server": ("test", 80), "scheme": "http", "root_path": ""})


def _json(resp: Any) -> dict[str, Any]:
    assert resp.media_type == "application/problem+json"
    out: dict[str, Any] = orjson.loads(resp.body)
    return out


def _integrity() -> IntegrityError:
    return IntegrityError("INSERT INTO core.secret_x (a) VALUES ($1)", {"a": "bi-mat"},
                          Exception('duplicate key value violates unique constraint "uq_x"'))


LEAKS = ("INSERT", "bi-mat", "uq_x", "constraint", "secret_x")


# ── (a)–(d): gọi trực tiếp handler ─────────────────────────────────────────────────────────────────────────────

async def test_integrity_error_is_500_internal_without_sql(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR, logger="gh.errors")
    resp = await db_error_handler(_req("/api/v1/users"), _integrity())
    assert resp.status_code == 500
    body = _json(resp)
    assert body["code"] == "INTERNAL" and body["status"] == 500
    eid = body["error_id"]
    assert re.fullmatch(r"[0-9a-f]{8}", eid)
    assert body["detail"] == f"Mã lỗi {eid} — gửi mã này cho người hỗ trợ"
    assert body["title"] == "Hệ thống gặp lỗi khi xử lý yêu cầu — đã ghi nhật ký"
    raw = bytes(resp.body).decode()
    for leak in LEAKS:
        assert leak not in raw, leak
    recs = [r for r in caplog.records if eid in r.getMessage()]
    assert recs and recs[0].exc_info is not None
    assert "Lỗi CSDL không mong đợi" in recs[0].getMessage() and "/api/v1/users" in recs[0].getMessage()


async def test_operational_error_is_503_db_unavailable_without_detail() -> None:
    exc = OperationalError("SELECT 1", None, Exception("could not connect to server at 10.0.0.5:5432"))
    resp = await db_error_handler(_req(), exc)
    body = _json(resp)
    assert resp.status_code == 503 and body["code"] == "DB_UNAVAILABLE" and body["detail"] is None
    assert "10.0.0.5" not in bytes(resp.body).decode()


async def test_dbapi_error_connection_invalidated_is_503() -> None:
    exc = DBAPIError("SELECT 1", None, Exception("terminating connection due to administrator command"),
                     connection_invalidated=True)
    resp = await db_error_handler(_req(), exc)
    body = _json(resp)
    assert resp.status_code == 503 and body["code"] == "DB_UNAVAILABLE" and body["detail"] is None
    # DBAPIError gốc KHÔNG đánh dấu kết nối hỏng → lỗi lập trình, không phải mất kết nối.
    plain = DBAPIError("SELECT 1", None, Exception("x"))
    assert (await db_error_handler(_req(), plain)).status_code == 500

    # asyncpg CannotConnectNowError/AdminShutdownError (SQLSTATE 57P0x, lớp 08) bọc thành DBAPIError gốc → 503;
    # SQLSTATE khác (23505 trùng khoá) → 500.
    class Orig(Exception):
        def __init__(self, sqlstate: str):
            super().__init__("the database system is shutting down")
            self.sqlstate = sqlstate

    # Quá tải tạm thời (lớp 53: too_many_connections/disk_full/out_of_memory; 57014 statement_timeout) → 503
    # để client thử lại; lớp 42 (lỗi cú pháp/thiếu bảng) và 22 (dữ liệu) vẫn là lỗi mã → 500.
    for state, status in (("57P03", 503), ("57P01", 503), ("08006", 503), ("53300", 503), ("53100", 503),
                          ("53200", 503), ("57014", 503), ("23505", 500), ("42P01", 500), ("22P02", 500)):
        resp = await db_error_handler(_req(), DBAPIError("SELECT 1", None, Orig(state)))
        assert resp.status_code == status, state
        if status == 503:
            assert _json(resp)["code"] == "DB_UNAVAILABLE", state


@pytest.mark.parametrize("exc", [ConnectionRefusedError(111, "Connect call failed ('172.18.0.3', 5432)"),
                                 TimeoutError("timed out connecting to 10.1.2.3:6379"),
                                 ConnectionResetError("reset by 192.168.1.9"),
                                 redis_exc.ConnectionError("Error 111 connecting to 10.9.8.7:6379"),
                                 redis_exc.TimeoutError("Timeout reading from 10.9.8.7:6379"),
                                 socket.gaierror(-2, "Name or service not known")])
async def test_infra_errors_are_503_without_ip(exc: Exception) -> None:
    resp = await infra_error_handler(_req(), exc)
    body = _json(resp)
    assert resp.status_code == 503 and body["code"] == "SERVICE_UNAVAILABLE" and body["detail"] is None
    assert not IP_RE.search(bytes(resp.body).decode())


@pytest.mark.parametrize("exc", [PermissionError(errno.EACCES, "Permission denied", "/var/lib/gh/secret.key"),
                                 OSError(errno.ENOSPC, "No space left on device", "/data/x")])
async def test_other_os_errors_are_500_internal(exc: OSError) -> None:
    resp = await os_error_handler(_req(), exc)
    body = _json(resp)
    assert resp.status_code == 500 and body["code"] == "INTERNAL" and body["error_id"]
    raw = bytes(resp.body).decode()
    assert "/var/lib" not in raw and "/data/x" not in raw and "space" not in raw


@pytest.mark.parametrize("exc", [socket.gaierror(-2, "Name or service not known"),
                                 OSError(errno.EHOSTUNREACH, "No route to host"),
                                 OSError(errno.ENETUNREACH, "Network is unreachable"),
                                 OSError(errno.ECONNREFUSED, "Connect call failed ('10.0.0.5', 5432)"),
                                 OSError("Multiple exceptions: [Errno 111] Connect call failed ('10.0.0.5', 5432), "
                                         "[Errno 99] Cannot assign requested address")])
async def test_os_errors_meaning_unreachable_are_503(exc: OSError) -> None:
    """Container `db` dừng → Docker DNS không phân giải `db` → asyncpg ném socket.gaierror thô; asyncio ném
    OSError('Multiple exceptions…') khi mọi địa chỉ đều hỏng; EHOSTUNREACH/ENETUNREACH → vẫn là mất kết nối (503)."""
    resp = await os_error_handler(_req(), exc)
    body = _json(resp)
    assert resp.status_code == 503 and body["code"] == "SERVICE_UNAVAILABLE" and body["detail"] is None
    assert not IP_RE.search(bytes(resp.body).decode())


async def test_app_routes_exceptions_by_mro(owner_api: Api, app) -> None:  # type: ignore[no-untyped-def]
    """Đăng ký thật trong create_app: lớp con mất kết nối → 503; OSError còn lại → 500; redis → 503."""
    cases: list[tuple[str, BaseException, int, str]] = [
        ("refused", ConnectionRefusedError(111, "Connect call failed ('172.18.0.3', 5432)"), 503,
         "SERVICE_UNAVAILABLE"),
        ("timeout", TimeoutError(), 503, "SERVICE_UNAVAILABLE"),
        ("broken", BrokenPipeError(), 503, "SERVICE_UNAVAILABLE"),
        ("redis", redis_exc.ConnectionError("Error 111 connecting to 10.9.8.7:6379"), 503, "SERVICE_UNAVAILABLE"),
        ("rtimeout", redis_exc.TimeoutError("Timeout reading from 10.9.8.7:6379"), 503, "SERVICE_UNAVAILABLE"),
        ("perm", PermissionError(errno.EACCES, "Permission denied", "/etc/gh"), 500, "INTERNAL"),
        ("nospc", OSError(errno.ENOSPC, "No space left on device"), 500, "INTERNAL"),
        ("dns", socket.gaierror(-2, "Name or service not known"), 503, "SERVICE_UNAVAILABLE"),
        ("unreach", OSError(errno.EHOSTUNREACH, "No route to host"), 503, "SERVICE_UNAVAILABLE"),
        ("multi", OSError("Multiple exceptions: [Errno 111] Connect call failed ('10.0.0.5', 5432)"), 503,
         "SERVICE_UNAVAILABLE"),
        ("operational", OperationalError("SELECT 1", None, Exception("10.0.0.5")), 503, "DB_UNAVAILABLE"),
        ("integrity", _integrity(), 500, "INTERNAL"),
    ]
    def make(exc: BaseException) -> Any:
        async def raiser() -> None:
            raise exc

        return raiser

    for name, exc, status, code in cases:
        app.add_api_route(f"/api/v1/__t/{name}", make(exc), methods=["GET"])
        r = await owner_api.get(f"/__t/{name}")
        assert r.status_code == status, (name, r.text)
        assert r.headers["content-type"].startswith("application/problem+json")
        assert r.json()["code"] == code, (name, r.text)
        assert not IP_RE.search(r.text), (name, r.text)
        for leak in (*LEAKS, "/etc/gh"):
            assert leak not in r.text, (name, leak)


# ── (e): đầu-cuối qua owner_api ──────────────────────────────────────────────────────────────────────────────

async def test_integrity_error_end_to_end(owner_api: Api, app) -> None:  # type: ignore[no-untyped-def]
    async def boom() -> None:
        raise _integrity()

    app.add_api_route("/api/v1/__t/integrity", boom, methods=["GET"])
    r = await owner_api.get("/__t/integrity")
    assert r.status_code == 500, r.text
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "INTERNAL" and body["error_id"] and body["error_id"] in body["detail"]
    for leak in LEAKS:
        assert leak not in r.text


@pytest.mark.parametrize("exc", [KeyError("khoa_bi_mat"), ValueError("bi-mat"), AttributeError("x")])
async def test_unhandled_exception_is_problem_json_internal(  # type: ignore[no-untyped-def]
        owner_api: Api, app, exc: Exception, caplog: pytest.LogCaptureFixture) -> None:
    """Ngoại lệ lạ không có handler riêng vẫn là problem+json 500 INTERNAL kèm error_id (không text/plain)."""
    caplog.set_level(logging.ERROR, logger="gh.errors")
    name = type(exc).__name__.lower()

    async def boom() -> None:
        raise exc

    app.add_api_route(f"/api/v1/__t/unhandled-{name}", boom, methods=["GET"])
    # ServerErrorMiddleware gửi phản hồi rồi ném lại ngoại lệ cho server ghi log — client thật vẫn nhận 500 JSON.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                 cookies=owner_api.c.cookies) as c:
        r = await c.get(f"/api/v1/__t/unhandled-{name}")
    assert r.status_code == 500, r.text
    assert r.headers["content-type"].startswith("application/problem+json"), r.text
    body = r.json()
    assert body["code"] == "INTERNAL"
    assert re.fullmatch(r"[0-9a-f]{8}", body["error_id"])
    assert body["error_id"] in body["detail"]
    assert body["title"] == "Hệ thống gặp lỗi khi xử lý yêu cầu — đã ghi nhật ký"
    assert "bi-mat" not in r.text and "khoa_bi_mat" not in r.text
    assert any(body["error_id"] in rec.getMessage() and rec.exc_info for rec in caplog.records)


# ── (f): /docs, /openapi.json theo môi trường ────────────────────────────────────────────────────────────────

async def _docs_status(application: Any) -> tuple[int, int]:
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        return (await c.get("/api/v1/docs")).status_code, (await c.get("/api/v1/openapi.json")).status_code


async def test_docs_hidden_when_not_exposed(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from gh.app import create_app
    from gh.config import get_settings

    assert await _docs_status(create_app(with_lifespan=False, expose_docs=False)) == (404, 404)
    assert await _docs_status(create_app(with_lifespan=False, expose_docs=True)) == (200, 200)
    try:
        monkeypatch.setenv("GH_ENV", "production")
        get_settings.cache_clear()
        prod = create_app(with_lifespan=False)
        assert prod.redoc_url is None
        assert await _docs_status(prod) == (404, 404)
        monkeypatch.setenv("GH_ENV", "development")
        get_settings.cache_clear()
        assert await _docs_status(create_app(with_lifespan=False)) == (200, 200)
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


# ── (g): Action Log đăng nhập sai che email ──────────────────────────────────────────────────────────────────

def test_mask_email_unit() -> None:
    from gh.auth.routes import _mask_email

    assert _mask_email(" NguoiDung.Dai@CongTy.vn ") == "n***@congty.vn"
    assert _mask_email("o@example.vn") == "o***@example.vn"
    assert _mask_email("matkhau-go-nham-123") is None
    assert _mask_email("@congty.vn") is None and _mask_email("abc@") is None


async def test_login_failed_masks_email(owner_api: Api, db, caplog: pytest.LogCaptureFixture) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    for email in ("nguoidung.dai@congty.vn", "matkhau-go-nham-123"):
        r = await owner_api.send("POST", "/auth/login", {"email": email, "password": "sai-mat-khau-1"})
        assert r.status_code == 401
    rows = (await db.execute(text("SELECT detail FROM ops.action_log WHERE action = 'auth.login_failed' "
                                  "ORDER BY at"))).all()
    assert len(rows) == 2
    assert rows[0].detail == {"email_masked": "n***@congty.vn"}
    assert rows[1].detail == {"email_masked": None}
    dumped = orjson.dumps([r.detail for r in rows]).decode()
    assert "nguoidung.dai" not in dumped and "matkhau-go-nham-123" not in dumped
    assert "nguoidung.dai" not in caplog.text and "matkhau-go-nham-123" not in caplog.text
