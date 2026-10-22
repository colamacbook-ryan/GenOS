"""Test chạy trên PostgreSQL 16 + Redis thật (không mock DB).

Biến môi trường: GH_TEST_PG (mặc định postgresql://postgres:postgres@localhost:5432), GH_TEST_REDIS
(mặc định redis://localhost:6379/15), GH_TEST_TEMPLATE (tên CSDL mẫu, mặc định gh_test_template),
GH_TEST_BROWSER_REDIS (DB Redis thứ hai cho test kênh browser-worker, mặc định cùng máy chủ với GH_TEST_REDIS, DB 13).
Chạy song song nhiều bộ test trên cùng máy (v0.1.57: CI chạy lượt superuser và lượt gh_app SONG SONG trong job `api`):
mỗi bộ đặt GH_TEST_TEMPLATE, số db Redis (GH_TEST_REDIS) và GH_TEST_BROWSER_REDIS riêng — ba tài nguyên này là toàn bộ
trạng thái dùng chung của bộ test (tên DB mỗi test đã có hậu tố ngẫu nhiên; thư mục tạm theo pid). Một CSDL mẫu được
migrate một lần; mỗi test cần DB sạch nhận một bản sao.

`GH_TEST_APP_ROLE=1` (mục v0.1.1/1a): chạy TOÀN BỘ bộ test dưới role ứng dụng `gh_app` (không superuser,
không BYPASSRLS — migration 0014) thay vì `postgres`, để bắt sớm mọi GRANT còn thiếu. Migrate vẫn luôn chạy
bằng superuser (`GH_ADMIN_DATABASE_URL` trỏ CSDL đang test) — đúng hợp đồng chung (docs/reports/HANDOFF-v0.1.1.md):
GH_DATABASE_URL → gh_app, GH_ADMIN_DATABASE_URL → superuser, rỗng ⇒ dùng GH_DATABASE_URL. Xem Makefile mục
`api-test-app-role`. `GH_APP_DB_PASSWORD` (mặc định cố định chỉ dùng cho test) luôn được đặt để migration 0014
tạo role `gh_app` LOGIN được ngay cả khi không bật GH_TEST_APP_ROLE — vô hại, chỉ ảnh hưởng vai trò test.
"""

import asyncio
import os
import socket
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from redis.asyncio import Redis

from tests.box_fakes import box, boxctl  # noqa: F401 — hộp riêng Antigravity giả (v0.1.63 H2), dùng chung nhiều bộ test

API_DIR = Path(__file__).resolve().parents[1]
PG = os.environ.get("GH_TEST_PG", "postgresql://postgres:postgres@localhost:5432")
REDIS_URL = os.environ.get("GH_TEST_REDIS", "redis://localhost:6379/15")
TEMPLATE = os.environ.get("GH_TEST_TEMPLATE", "gh_test_template")
# v0.1.57: DB Redis riêng của test kênh browser-worker (tests/test_social.py) — hai lượt song song phải khác nhau.
BROWSER_REDIS_URL = os.environ.get("GH_TEST_BROWSER_REDIS") or REDIS_URL.rsplit("/", 1)[0] + "/13"
# Khoá tư vấn cấp CỤM (kết nối tới DB `postgres`) tuần tự hoá bước migrate CSDL mẫu — xem `template_db`.
TEMPLATE_LOCK_KEY = 0x6768_5445  # "ghTE"

os.environ.setdefault("GH_ENV", "test")
os.environ["GH_COOKIE_SECURE"] = "false"
os.environ["GH_REDIS_URL"] = REDIS_URL
# v0.1.31: thư mục phiên Claude Code CLI của test không bao giờ là ~/.claude thật của máy chạy test.
os.environ.setdefault("GH_CLAUDE_HOME", f"/tmp/gh-test-claude-{os.getpid()}/.claude")
os.environ.setdefault("GH_CLAUDE_BINARY", "gh-test-no-claude")
# F-22: HOME của agy trong test không bao giờ là HOME thật (mặc định ~/.gemini/antigravity-cli ⇒ HOME = ~).
os.environ.setdefault("GH_CLI_HOME", f"/tmp/gh-test-agy-{os.getpid()}/.gemini/antigravity-cli")
os.environ.setdefault("GH_MASTER_KEY", "")
# v0.1.57: kho đối tượng đĩa mặc định (/tmp/gh-objects) là chỗ dùng chung của hai lượt pytest song song ⇒ theo pid.
os.environ.setdefault("GH_OBJECTS_DIR", f"/tmp/gh-test-objects-{os.getpid()}")

# gh_app (migration 0014) — mật khẩu test cố định, KHÔNG dùng ngoài môi trường test. Luôn đặt (kể cả khi
# GH_TEST_APP_ROLE tắt) để role gh_app có LOGIN sẵn nếu một test nào đó cần SET ROLE gh_app thủ công.
GH_APP_DB_PASSWORD = os.environ.setdefault("GH_APP_DB_PASSWORD", "gh-app-test-only-pw-1")
APP_ROLE = os.environ.get("GH_TEST_APP_ROLE") == "1"
APP_PG = os.environ.get("GH_TEST_APP_PG", f"postgresql://gh_app:{GH_APP_DB_PASSWORD}@localhost:5432")


# v0.1.45 (F-49): mọi lời gọi MCP / nhà cung cấp AI ghim DNS qua `gh.chassis.mcp_client._getaddrinfo`. Test không
# phụ thuộc DNS thật: tên máy (không phải IP literal) phân giải ra một IP công cộng giả cố định; `localhost` →
# 127.0.0.1. Tên dịch vụ compose (`db`, `redis`…) vẫn bị `forbidden_host` cấm TRƯỚC khi phân giải. Test cần hành vi
# thật (không phân giải được, DNS rebinding) tự `monkeypatch.setattr(mcp_client, "_getaddrinfo", …)` đè lên.
FAKE_PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    from gh.chassis import mcp_client

    async def fake(host: str, port: int) -> list[Any]:
        ip = "127.0.0.1" if host.lower().rstrip(".") == "localhost" else FAKE_PUBLIC_IP
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(mcp_client, "_getaddrinfo", fake)


def _admin(sql: str) -> None:
    with psycopg.connect(f"{PG}/postgres", autocommit=True) as c:
        c.execute(sql)


def _async_url(db: str, base: str = PG) -> str:
    return f"{base.replace('postgresql://', 'postgresql+asyncpg://')}/{db}"


@pytest.fixture(scope="session")
def template_db() -> str:
    # v0.1.57: hai lượt pytest song song cùng migrate CSDL mẫu khác tên của MỘT cụm Postgres; migration 0014 chạy
    # `ALTER ROLE gh_app` (vai trò dùng chung cả cụm) nên hai lượt đồng thời có thể đụng "tuple concurrently updated".
    # Khoá tư vấn cấp cụm (giữ tới khi đóng kết nối) xếp hàng phần tạo + migrate; test sau đó chạy song song thoải mái.
    with psycopg.connect(f"{PG}/postgres", autocommit=True) as lock:
        lock.execute("SELECT pg_advisory_lock(%s)", (TEMPLATE_LOCK_KEY,))
        _admin(f"DROP DATABASE IF EXISTS {TEMPLATE} WITH (FORCE)")
        _admin(f"CREATE DATABASE {TEMPLATE}")
        # Migrate = DDL → luôn superuser, dù GH_TEST_APP_ROLE=1 (gh_app chỉ có quyền DML từ migration 0014).
        # GH_ADMIN_DATABASE_URL ép rỗng để không kế thừa giá trị của test trước đó (env tiến trình con là bản sao).
        env = {**os.environ, "GH_DATABASE_URL": _async_url(TEMPLATE), "GH_ADMIN_DATABASE_URL": ""}
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "heads"], cwd=API_DIR, env=env, check=True)
    return TEMPLATE


def _use_db(name: str) -> None:
    from gh.config import get_settings

    if APP_ROLE:
        # Runtime (fixture app/client, và mọi test dùng session `db` thẳng) kết nối bằng gh_app — không
        # superuser, không BYPASSRLS. DDL/backup/bảo trì phân vùng (gh/db.py::admin_sessionmaker) vẫn qua
        # superuser thật của CSDL này để lộ sớm bất kỳ chỗ nào lỡ cần vượt quyền GRANT của migration 0014.
        os.environ["GH_ADMIN_DATABASE_URL"] = _async_url(name)
        os.environ["GH_DATABASE_URL"] = _async_url(name, base=APP_PG)
    else:
        os.environ["GH_DATABASE_URL"] = _async_url(name)
        os.environ.pop("GH_ADMIN_DATABASE_URL", None)
    get_settings.cache_clear()


@pytest.fixture
async def fresh_db(template_db: str) -> AsyncIterator[str]:
    from gh import db as dbmod

    name = f"{TEMPLATE}_{uuid.uuid4().hex[:10]}"
    await asyncio.to_thread(_admin, f"CREATE DATABASE {name} TEMPLATE {template_db}")
    _use_db(name)
    await dbmod.dispose_engine()
    try:
        yield name
    finally:
        await dbmod.dispose_engine()
        await asyncio.to_thread(_admin, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.fixture
async def db(fresh_db: str) -> AsyncIterator[object]:
    from gh.db import sessionmaker

    async with sessionmaker()() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    r = Redis.from_url(REDIS_URL)
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


@pytest.fixture
async def app(fresh_db: str, redis: Redis) -> AsyncIterator[object]:
    from gh.app import create_app

    os.environ["GH_SETUP_TOKEN"] = "test-setup-token"
    from gh.config import get_settings

    get_settings.cache_clear()
    application = create_app()
    async with application.router.lifespan_context(application):
        yield application
    os.environ.pop("GH_SETUP_TOKEN", None)
    get_settings.cache_clear()


@pytest.fixture
async def client(app: object) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class Api:
    """Client có CSRF tự động."""

    def __init__(self, c: httpx.AsyncClient):
        self.c = c

    def _headers(self) -> dict[str, str]:
        csrf = self.c.cookies.get("gh_csrf")
        return {"X-CSRF-Token": csrf} if csrf else {}

    async def get(self, url: str, **kw: object) -> httpx.Response:
        return await self.c.get(f"/api/v1{url}", **kw)  # type: ignore[arg-type]

    async def send(self, method: str, url: str, json: object = None) -> httpx.Response:
        return await self.c.request(method, f"/api/v1{url}", json=json, headers=self._headers())


OWNER = {"display_name": "Anh Cơ", "email": "owner@example.vn", "password": "mat-khau-rat-dai-123",
         "pin": "246810", "pin_confirm": "246810"}


async def do_setup(api: Api) -> None:
    r = await api.send("PUT", "/setup/steps/1", {"token": "test-setup-token", "language": "vi", "mode": "empty"})
    assert r.status_code == 200, r.text
    r = await api.send("PUT", "/setup/steps/2", {"token": "test-setup-token", **OWNER})
    assert r.status_code == 200, r.text
    r = await api.send("PUT", "/setup/steps/3", {"org_name": "Genesis Việt", "timezone": "Asia/Ho_Chi_Minh",
                                                  "currency": "VND", "self_name": "Anh",
                                                  "bot_calls_me": "Sếp"})
    assert r.status_code == 200, r.text


@pytest.fixture
async def owner_api(client: httpx.AsyncClient) -> Api:
    api = Api(client)
    await do_setup(api)
    return api


async def verify_pin(api: Api, pin: str = OWNER["pin"]) -> None:
    """v0.1.35 (F-20): mở phiên PIN trước thao tác cần PIN (vd `ai.route_change` khi tạo/sửa nhà cung cấp AI)."""
    r = await api.send("POST", "/auth/pin/verify", {"pin": pin})
    assert r.status_code == 200, r.text
