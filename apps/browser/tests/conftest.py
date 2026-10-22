"""Test browser-worker: Redis thật (GH_TEST_REDIS, mặc định db 14) + Chromium thật trên TRANG MẪU (tests/fixtures).

Không bao giờ gọi facebook.com: mọi yêu cầu tới www.facebook.com được `FakeSite` trả trang mẫu ngay trong trình duyệt
(context.route), yêu cầu tới tên miền khác bị guard của worker chặn trước. Không có Chromium (máy dev thiếu trình duyệt)
→ các test cần trình duyệt bị bỏ qua, TRỪ khi GH_BROWSER_TESTS_REQUIRED=1 (CI) thì báo lỗi.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import orjson
import pytest
from redis.asyncio import Redis

from ghb import protocol
from ghb.config import Config

FIX = Path(__file__).parent / "fixtures"
REDIS_URL = os.environ.get("GH_TEST_REDIS", "redis://localhost:6379/14")
KEY = bytes(range(32))
ORG = "0190a000-0000-7000-8000-000000000001"
ACC = "0190a000-0000-7000-8000-0000000000aa"
FB_DOMAINS = ["facebook.com", "fbcdn.net", "facebook.net", "fbsbx.com", "messenger.com"]
LOGGED_IN = {"cookies": [
    {"name": "c_user", "value": "100012345", "domain": ".facebook.com", "path": "/", "expires": -1,
     "httpOnly": False, "secure": True, "sameSite": "None"},
    {"name": "xs", "value": "bi-mat-xs", "domain": ".facebook.com", "path": "/", "expires": -1,
     "httpOnly": True, "secure": True, "sameSite": "None"}], "origins": []}


def cfg(**kw: Any) -> Config:
    base: dict[str, Any] = {"redis_url": REDIS_URL, "key": KEY, "headless": True, "delay": 0.0,
                            "nav_timeout_ms": 8000}
    return Config(**{**base, **kw})


def job(kind: str, payload: dict[str, Any], **kw: Any) -> dict[str, Any]:
    import time
    import uuid

    return {"v": 1, "id": str(uuid.uuid4()), "kind": kind, "org_id": ORG, "account_id": ACC,
            "platform": "facebook_personal", "domains": FB_DOMAINS, "nonce": uuid.uuid4().hex,
            "exp": int(time.time()) + 600, "payload": payload, **kw}


def sealed(state: dict[str, Any]) -> str:
    return protocol.seal(KEY, orjson.dumps(state), protocol.account_aad(ORG, ACC))


async def results(redis: Redis) -> list[dict[str, Any]]:
    out = []
    for _id, fields in await redis.xrange(protocol.RESULTS_STREAM):
        msg = protocol.verify(KEY, protocol.P_RESULT, orjson.loads(fields[b"m"]))
        assert msg is not None, "kết quả phải được ký"
        out.append(msg)
    return out


class FakeSite:
    """www.facebook.com giả: đường dẫn → trang mẫu. `pages` đổi được theo từng kịch bản."""

    def __init__(self) -> None:
        self.pages: dict[str, str] = {"/": "fb_home.html", "/notifications": "fb_notifications.html",
                                      "/messages/t/": "fb_messages.html", "/login/": "fb_login.html",
                                      "/checkpoint/": "fb_checkpoint.html",
                                      "/permalink.php": "fb_post_comment.html",
                                      "/messages/t/1001": "fb_thread.html", "/messages/t/1001/": "fb_thread.html"}
        self.hang: set[str] = set()
        self.seen: list[str] = []
        self.on_path: dict[str, Any] = {}

    async def install(self, ctx: Any) -> None:
        async def handler(route: Any) -> None:
            from urllib.parse import urlsplit

            u = urlsplit(route.request.url)
            if u.hostname != "www.facebook.com":
                await route.fulfill(status=204, body="")
                return
            path = u.path
            self.seen.append(path)
            if path in self.on_path:
                await self.on_path[path]()
            if path in self.hang:
                await asyncio.sleep(30)
            name = self.pages.get(path)
            if name is None:
                await route.fulfill(status=404, body="not found")
                return
            await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                body=(FIX / name).read_text(encoding="utf-8"))

        await ctx.route("**/*", handler)


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    r = Redis.from_url(REDIS_URL)
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()




@pytest.fixture(scope="session")
async def chromium() -> AsyncIterator[Any]:
    try:
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        exe = os.environ.get("PW_CHROMIUM") or None
        browser = await pw.chromium.launch(headless=True, executable_path=exe)
    except Exception as e:  # noqa: BLE001
        if os.environ.get("GH_BROWSER_TESTS_REQUIRED") == "1":
            raise
        pytest.skip(f"không mở được Chromium: {e}")
    yield browser
    await browser.close()
    await pw.stop()


@pytest.fixture
def site() -> FakeSite:
    return FakeSite()
