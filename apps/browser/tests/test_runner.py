"""Runner + adapter Facebook trên TRANG MẪU với Chromium thật (không có Facebook thật)."""

import asyncio
import time
from typing import Any

import orjson
from redis.asyncio import Redis

from ghb import protocol
from ghb.runner import Runner
from tests.conftest import ACC, KEY, LOGGED_IN, ORG, FakeSite, cfg, job, results, sealed


def runner(redis: Redis, chromium: Any, site: FakeSite, **kw: Any) -> Runner:
    async def provide() -> Any:
        return chromium

    return Runner(cfg(**kw), redis, provide, context_hook=site.install)


async def test_read_extracts_structured_items_and_blocks_foreign_hosts(redis: Redis, chromium: Any,
                                                                      site: FakeSite) -> None:
    j = job("read", {"state": sealed(LOGGED_IN), "what": ["notifications", "inbox"],
                     "limits": {"max_pages": 40, "max_items": 30}})
    await runner(redis, chromium, site).run(j, asyncio.Event())
    res = await results(redis)
    assert [r["type"] for r in res] == ["done"], res
    data = res[0]["data"]
    notes = [i for i in data["items"] if i["kind"] == "notification"]
    inbox = [i for i in data["items"] if i["kind"] == "inbox"]
    assert [n["time"] for n in notes] == ["5 phút", "2 giờ", "Hôm qua"]
    assert notes[0]["unread"] is True and notes[1]["unread"] is False
    assert notes[0]["text"].startswith("Chị Lan đã bình luận")
    assert "notif_id=111" in notes[0]["link"]
    assert [(c["who"], c["text"], c["unread"]) for c in inbox] == [
        ("Shop Mai", "Mai em giao hàng nhé anh", True), ("Anh Tuấn", "Bạn: Ok anh, em gửi báo giá", False)]
    assert data["pages"] == 3 and data["page_state"] == "ok"
    assert data["cost"]["blocked"] >= 2                     # tracker.example.net + evil.example.org bị chặn
    assert "/checkpoint/" not in site.seen
    # Phiên trả về mã hoá bằng khoá truyền, giải được đúng AAD tài khoản.
    state = orjson.loads(protocol.unseal(KEY, res[0]["state"], protocol.account_aad(ORG, ACC)))
    assert any(c["name"] == "c_user" for c in state["cookies"])


async def test_checkpoint_captcha_and_logged_out_stop_the_job(redis: Redis, chromium: Any, site: FakeSite) -> None:
    site.pages["/"] = "fb_checkpoint.html"            # Facebook chuyển sang trang xác minh
    await runner(redis, chromium, site).run(job("health", {"state": sealed(LOGGED_IN)}), asyncio.Event())
    site.pages["/"] = "fb_home.html"
    site.pages["/notifications"] = "fb_captcha.html"
    await runner(redis, chromium, site).run(job("read", {"state": sealed(LOGGED_IN), "what": ["notifications"]}),
                                            asyncio.Event())
    await runner(redis, chromium, site).run(job("read", {"state": sealed({"cookies": [], "origins": []})}),
                                            asyncio.Event())
    codes = [(r["type"], r["data"].get("code")) for r in await results(redis)]
    assert codes == [("failed", "CHECKPOINT"), ("failed", "CAPTCHA"), ("failed", "LOGGED_OUT")]
    assert all("state" not in r for r in await results(redis))   # không trả phiên khi lỗi


async def test_halt_before_next_step(redis: Redis, chromium: Any, site: FakeSite) -> None:
    async def halt() -> None:
        await redis.set(protocol.HALT_KEY, b"{}")

    site.on_path["/"] = halt
    await runner(redis, chromium, site).run(job("read", {"state": sealed(LOGGED_IN)}), asyncio.Event())
    assert [r["type"] for r in await results(redis)] == ["halted"]
    assert "/notifications" not in site.seen


async def test_cancel_closes_browser_immediately(redis: Redis, chromium: Any, site: FakeSite) -> None:
    site.hang.add("/notifications")
    cancel = asyncio.Event()
    t0 = time.monotonic()
    task = asyncio.create_task(runner(redis, chromium, site).run(
        job("read", {"state": sealed(LOGGED_IN), "what": ["notifications"]}), cancel))
    await asyncio.sleep(1.0)
    cancel.set()
    await asyncio.wait_for(task, timeout=10)
    assert time.monotonic() - t0 < 10
    assert [r["type"] for r in await results(redis)] == ["halted"]


async def test_login_streams_frames_and_takes_owner_input(redis: Redis, chromium: Any, site: FakeSite) -> None:
    ticket = "ve-thu-" + "x" * 20
    j = job("login", {"ticket": ticket, "login_url": "https://www.facebook.com/login/", "timeout_s": 60})
    pubsub = redis.pubsub()
    await pubsub.subscribe(protocol.FRAMES_PREFIX + ticket)
    task = asyncio.create_task(runner(redis, chromium, site).run(j, asyncio.Event()))
    frames = 0
    deadline = time.monotonic() + 20
    while frames < 1 and time.monotonic() < deadline:
        m = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
        if m:
            msg = protocol.verify(KEY, protocol.P_FRAME, orjson.loads(m["data"]))
            assert msg is not None
            frames += msg.get("t") == "frame"
    assert frames >= 1, "phải thấy ít nhất một khung hình screencast"

    async def send(ev: dict[str, Any]) -> None:
        ev = {**ev, "ticket": ticket, "ts": int(time.time())}
        await redis.publish(protocol.INPUT_PREFIX + ticket, orjson.dumps(protocol.sign(KEY, protocol.P_INPUT, ev)))
        await asyncio.sleep(0.3)

    # Sự kiện giả mạo (sai chữ ký) bị bỏ.
    await redis.publish(protocol.INPUT_PREFIX + ticket, orjson.dumps({"type": "text", "text": "x", "sig": "sai"}))
    await send({"type": "mouse", "action": "click", "x": 250, "y": 120, "button": "left"})
    await send({"type": "text", "text": "owner@example.vn"})
    await send({"type": "mouse", "action": "click", "x": 250, "y": 180, "button": "left"})
    await send({"type": "text", "text": "mat-khau-cua-sep"})
    await send({"type": "key", "action": "press", "key": "Enter"})
    await asyncio.wait_for(task, timeout=20)
    await pubsub.aclose()
    res = await results(redis)
    assert [r["type"] for r in res] == ["login.done"], res
    state = orjson.loads(protocol.unseal(KEY, res[0]["state"], protocol.account_aad(ORG, ACC)))
    assert any(c["name"] == "c_user" for c in state["cookies"])
    assert "mat-khau-cua-sep" not in orjson.dumps(res).decode()   # mật khẩu không bao giờ nằm trong kết quả


async def test_login_cancel_and_blocked_login_url(redis: Redis, chromium: Any, site: FakeSite) -> None:
    ticket = "ve-huy-" + "y" * 20
    j = job("login", {"ticket": ticket, "login_url": "https://www.facebook.com/login/", "timeout_s": 60})
    task = asyncio.create_task(runner(redis, chromium, site).run(j, asyncio.Event()))
    await asyncio.sleep(2.0)
    ev = {"type": "cancel", "ticket": ticket, "ts": int(time.time())}
    await redis.publish(protocol.INPUT_PREFIX + ticket, orjson.dumps(protocol.sign(KEY, protocol.P_INPUT, ev)))
    await asyncio.wait_for(task, timeout=15)
    bad = job("login", {"ticket": "t" * 30, "login_url": "https://evil.example.com/login", "timeout_s": 60})
    await runner(redis, chromium, site).run(bad, asyncio.Event())
    codes = [(r["type"], r["data"].get("code")) for r in await results(redis)]
    assert codes == [("login.failed", "CANCELLED"), ("login.failed", "BLOCKED_URL")]
