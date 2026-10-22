"""v0.1.36 (F-4): log JSON tra được — ts/level/logger/msg/exc/stack + trường riêng (error_id, method, path)."""

import io
import json
import logging
import sys

import httpx

from gh.app import JsonFormatter
from tests.conftest import Api


def _record(**kw) -> logging.LogRecord:  # type: ignore[no-untyped-def]
    return logging.LogRecord("gh.test", logging.ERROR, __file__, 10, "lỗi %s", ("thử",), **kw)


def test_json_formatter_exception_stack_and_extra() -> None:
    try:
        raise ValueError("hỏng rồi")
    except ValueError:
        exc_info = sys.exc_info()
    rec = _record(exc_info=exc_info)
    rec.error_id = "abcd1234"
    out = json.loads(JsonFormatter().format(rec))
    assert out["msg"] == "lỗi thử" and out["level"] == "ERROR" and out["logger"] == "gh.test"
    assert out["ts"].endswith("Z") and "T" in out["ts"]
    assert "Traceback" in out["exc"] and "ValueError" in out["exc"]
    assert out["error_id"] == "abcd1234"
    assert "args" not in out and "exc_info" not in out  # thuộc tính chuẩn của LogRecord không bị lặp lại

    rec2 = _record(exc_info=None, sinfo="Stack (most recent call last):\n  File x")
    out2 = json.loads(JsonFormatter().format(rec2))
    assert "stack" in out2 and "exc" not in out2


def test_json_formatter_stack_info_true() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    lg = logging.getLogger("gh.test.stack")
    lg.addHandler(handler)
    lg.propagate = False
    try:
        lg.warning("kiểm stack", stack_info=True)
    finally:
        lg.removeHandler(handler)
        lg.propagate = True
    out = json.loads(stream.getvalue().strip())
    assert "stack" in out and "test_json_formatter_stack_info_true" in out["stack"]


async def test_500_logs_json_with_error_id(owner_api: Api, app) -> None:  # type: ignore[no-untyped-def]
    async def boom() -> None:
        raise RuntimeError("bi-mat-noi-bo")

    app.add_api_route("/api/v1/__t/log500", boom, methods=["GET"])
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    lg = logging.getLogger("gh.errors")
    lg.addHandler(handler)
    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                     cookies=owner_api.c.cookies) as c:
            r = await c.get("/api/v1/__t/log500")
    finally:
        lg.removeHandler(handler)
    assert r.status_code == 500 and r.json()["code"] == "INTERNAL"
    assert "bi-mat-noi-bo" not in r.text
    error_id = r.json()["error_id"]
    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    [entry] = [x for x in lines if x.get("error_id") == error_id]
    assert entry["level"] == "ERROR" and entry["logger"] == "gh.errors"
    assert entry["ts"].endswith("Z")
    assert "RuntimeError" in entry["exc"]
    assert entry["method"] == "GET" and entry["path"] == "/api/v1/__t/log500"


def test_json_formatter_redacts_secrets_in_msg_and_traceback() -> None:
    """`genh doctor` gói log vào tệp gửi hỗ trợ ⇒ mật khẩu trong URL, Bearer, token=… không lọt nguyên văn."""
    try:
        raise RuntimeError("connect failed: postgresql://gh:S3cretPw@db:5432/gh?sslmode=disable")
    except RuntimeError:
        exc_info = sys.exc_info()
    rec = logging.LogRecord("gh.test", logging.ERROR, __file__, 1,
                            "gọi https://api.test/v1?access_token=abc123XYZ&x=1 với Authorization: Bearer sk-live-999",
                            (), exc_info)
    rec.detail = "password=hunter2; api_key: KEY-42; status code: 500; https://cb.test/?code=OAUTH9&state=1"  # type: ignore[attr-defined]
    rec.error_id = "err-1"  # type: ignore[attr-defined]
    line = JsonFormatter().format(rec)
    for secret in ("S3cretPw", "abc123XYZ", "sk-live-999", "hunter2", "KEY-42", "OAUTH9"):
        assert secret not in line, secret
    out = json.loads(line)
    assert "postgresql://***:***@db:5432/gh" in out["exc"]
    assert "access_token=***&x=1" in out["msg"] and "Bearer ***" in out["msg"]
    assert out["detail"] == "password=***; api_key: ***; status code: 500; https://cb.test/?code=***&state=1"
    assert out["error_id"] == "err-1" and out["level"] == "ERROR"
