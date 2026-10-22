"""v0.1.44 (F-4b) — Mã yêu cầu (X-Request-ID): mọi phản hồi có header, mọi problem+json có `request_id` khớp header,
dòng log của lỗi 500 mang đúng mã đó (cả khoá `request_id` trong log JSON)."""

import json
import logging
import re
from typing import Any

import httpx
import pytest

from gh.app import JsonFormatter, RequestIdFilter
from gh.errors import request_id_var
from tests.conftest import Api

HEX16 = re.compile(r"^[0-9a-f]{16}$")


def _check_problem(r: httpx.Response, status: int) -> str:
    assert r.status_code == status, r.text
    rid = r.headers["x-request-id"]
    assert r.headers["content-type"].startswith("application/problem+json"), r.text
    assert r.json()["request_id"] == rid
    return rid


async def test_header_on_success_and_generated_format(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/health")
    assert r.status_code == 200 and HEX16.fullmatch(r.headers["x-request-id"])
    r2 = await client.get("/api/v1/health")
    assert r2.headers["x-request-id"] != r.headers["x-request-id"]


async def test_428_before_setup(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/auth/me")
    assert _check_problem(r, 428) and r.json()["code"] == "SETUP_REQUIRED"


async def test_404_422_423(owner_api: Api) -> None:
    r = await owner_api.get("/khong-ton-tai")
    _check_problem(r, 404)
    assert r.json()["code"] == "NOT_FOUND"
    r = await owner_api.send("POST", "/auth/login", {"email": 1})
    _check_problem(r, 422)
    r = await owner_api.send("PUT", "/notify/telegram", {"chat_id": "1"})
    _check_problem(r, 423)
    assert r.json()["code"] == "PIN_REQUIRED"


async def test_incoming_id_kept_or_replaced(owner_api: Api) -> None:
    good = "web-Req_12345678"
    r = await owner_api.get("/khong-ton-tai", headers={"X-Request-ID": good})
    assert r.headers["x-request-id"] == good and r.json()["request_id"] == good
    for bad in ("ngan", "co dau!12345678", "x" * 65, "a;b=c Set-Cookie"):
        r = await owner_api.get("/health", headers={"X-Request-ID": bad})
        assert HEX16.fullmatch(r.headers["x-request-id"]), bad


async def test_500_problem_json_and_log_carry_request_id(owner_api: Api, app: Any,
                                                         caplog: pytest.LogCaptureFixture) -> None:
    async def boom() -> None:
        raise KeyError("khoa_bi_mat")

    app.add_api_route("/api/v1/__t/rid500", boom, methods=["GET"])
    caplog.set_level(logging.ERROR, logger="gh.errors")
    caplog.handler.addFilter(RequestIdFilter())
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=owner_api.c.cookies) as c:
        r = await c.get("/api/v1/__t/rid500")
    rid = _check_problem(r, 500)
    body = r.json()
    assert body["code"] == "INTERNAL" and body["error_id"] and "khoa_bi_mat" not in r.text
    recs = [rec for rec in caplog.records if body["error_id"] in rec.getMessage()]
    assert recs and recs[0].exc_info and getattr(recs[0], "request_id", None) == rid
    line = json.loads(JsonFormatter().format(recs[0]))
    assert line["request_id"] == rid and line["error_id"] == body["error_id"]


def test_json_formatter_has_request_id_key() -> None:
    rec = logging.LogRecord("gh.test", logging.INFO, __file__, 1, "ngoài request", (), None)
    assert json.loads(JsonFormatter().format(rec))["request_id"] is None
    token = request_id_var.set("abcdef0123456789")
    try:
        rec2 = logging.LogRecord("gh.test", logging.INFO, __file__, 1, "trong request", (), None)
        assert RequestIdFilter().filter(rec2) is True
        assert rec2.request_id == "abcdef0123456789"  # type: ignore[attr-defined]
        assert json.loads(JsonFormatter().format(rec2))["request_id"] == "abcdef0123456789"
        rec3 = logging.LogRecord("gh.test", logging.INFO, __file__, 1, "không qua filter", (), None)
        assert json.loads(JsonFormatter().format(rec3))["request_id"] == "abcdef0123456789"
    finally:
        request_id_var.reset(token)
    rec4 = logging.LogRecord("gh.test", logging.INFO, __file__, 1, "dev", (), None)
    RequestIdFilter().filter(rec4)
    assert logging.Formatter("[%(request_id)s] %(message)s").format(rec4) == "[-] dev"
