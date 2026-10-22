"""Ghi (trả lời bình luận / nhắn tin) trên TRANG MẪU với Chromium thật: permit, dừng, huỷ, ảnh chụp, nhật ký bước.
Không gọi facebook.com."""

import asyncio
import hashlib
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from redis.asyncio import Redis

from ghb import protocol
from ghb.adapters.facebook import FacebookAdapter
from ghb.config import load, parse_delay
from ghb.runner import Runner
from tests.conftest import ACC, KEY, LOGGED_IN, ORG, FakeSite, cfg, job, results, sealed

COMMENT_URL = "https://www.facebook.com/permalink.php?story_fbid=1&comment_id=2"
THREAD_URL = "https://www.facebook.com/messages/t/1001/"
TEXT = "Cảm ơn bạn, shop còn hàng ạ!"


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def runner(redis: Redis, chromium: Any, site: FakeSite, **kw: Any) -> Runner:
    async def provide() -> Any:
        return chromium

    return Runner(cfg(**kw), redis, provide, context_hook=site.install)


def write_job(action: str = "reply_comment", target: str = COMMENT_URL, text: str = TEXT, *,
              permit_over: dict[str, Any] | None = None, key: bytes = KEY, **kw: Any) -> dict[str, Any]:
    j = job("write", {}, **kw)
    now = int(time.time())
    claims: dict[str, Any] = {"v": 1, "nonce": uuid.uuid4().hex, "job_id": j["id"], "org_id": ORG, "account_id": ACC,
                              "action": action, "target_url_sha256": sha(target), "body_sha256": sha(text),
                              "iat": now, "exp": now + 300, "confirmed_by": "0190a000-0000-7000-8000-0000000000u1",
                              **(permit_over or {})}
    j["payload"] = {"state": sealed(LOGGED_IN), "action": action, "target_url": target, "text": text,
                    "permit": protocol.sign(key, protocol.P_PERMIT, claims), "timeout_s": 180}
    return j


class Spy:
    """Ghi lại các lời gọi adapter + nội dung trang lúc xác nhận (trước khi trình duyệt đóng)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[str] = []
        self.body = ""
        self.hooks: dict[str, Any] = {}
        spy = self
        for name in ("open_target", "compose", "submit", "confirm_sent"):
            orig = getattr(FacebookAdapter, name)

            def make(name: str, orig: Any) -> Any:
                async def wrapped(self_: Any, page: Any, *a: Any, **k: Any) -> Any:
                    spy.calls.append(name)
                    out = await orig(self_, page, *a, **k)
                    if name == "confirm_sent":
                        spy.body = await page.inner_text("body")
                    if name in spy.hooks:
                        await spy.hooks[name]()
                    return out
                return wrapped

            monkeypatch.setattr(FacebookAdapter, name, make(name, orig))


async def one(redis: Redis) -> dict[str, Any]:
    res = await results(redis)
    assert len(res) == 1, res
    return res[0]


async def test_reply_comment_done_with_proof_and_trace(redis: Redis, chromium: Any, site: FakeSite,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch)
    j = write_job()
    await runner(redis, chromium, site).run(j, asyncio.Event())
    r = await one(redis)
    assert r["type"] == "done", r
    d = r["data"]
    assert d["action"] == "reply_comment" and d["sent"] is True and d["confirmed"] is True
    jpeg = protocol.unseal(KEY, d["proof"], f"{ORG}:{ACC}:proof:{j['id']}")
    assert jpeg[:3] == b"\xff\xd8\xff"
    assert hashlib.sha256(jpeg).hexdigest() == d["proof_sha256"]
    steps = [t["step"] for t in d["trace"]]
    core = [s for s in steps if s != "page_state"]
    assert core == ["open", "compose", "halt_check", "submit", "confirm", "screenshot"]
    assert all(t["ok"] is True and isinstance(t["ms"], int) for t in d["trace"]) and len(d["trace"]) <= 30
    assert all(set(t) == {"step", "ms", "ok"} for t in d["trace"])
    assert TEXT in spy.body                                  # bình luận trả lời mới có trong trang mẫu
    assert "state" in r
    assert TEXT not in str(d["trace"]) and "permalink" not in str(d["trace"])


async def test_send_message_done_with_screenshot(redis: Redis, chromium: Any, site: FakeSite,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch)
    j = write_job("send_message", THREAD_URL, "Em gửi báo giá ngay ạ")
    await runner(redis, chromium, site).run(j, asyncio.Event())
    r = await one(redis)
    assert r["type"] == "done", r
    assert r["data"]["sent"] is True and r["data"]["confirmed"] is True
    assert protocol.unseal(KEY, r["data"]["proof"], f"{ORG}:{ACC}:proof:{j['id']}")[:3] == b"\xff\xd8\xff"
    assert "Em gửi báo giá ngay ạ" in spy.body


async def test_expired_permit_opens_no_page(redis: Redis, chromium: Any, site: FakeSite) -> None:
    past = int(time.time()) - 1000
    j = write_job(permit_over={"iat": past - 300, "exp": past})
    await runner(redis, chromium, site).run(j, asyncio.Event())
    r = await one(redis)
    assert (r["type"], r["data"]["code"]) == ("failed", "PERMIT_INVALID")
    assert site.seen == []


async def test_bad_permits_are_rejected(redis: Redis, chromium: Any, site: FakeSite,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch)
    wrong_key = write_job(key=bytes(reversed(range(32))))
    changed_text = write_job()
    changed_text["payload"]["text"] = "Nội dung bị đổi sau khi ký"
    changed_url = write_job()
    changed_url["payload"]["target_url"] = "https://www.facebook.com/permalink.php?story_fbid=9&comment_id=9"
    other_job = write_job(permit_over={"job_id": "job-khac"})
    other_action = write_job(permit_over={"action": "send_message"})
    unknown_action = write_job("delete_post", permit_over={"action": "delete_post"})
    future = write_job(permit_over={"iat": int(time.time()) + 3600, "exp": int(time.time()) + 3900})
    no_permit = write_job()
    del no_permit["payload"]["permit"]
    for j in (wrong_key, changed_text, changed_url, other_job, other_action, unknown_action, future, no_permit):
        await runner(redis, chromium, site).run(j, asyncio.Event())
    # nonce dùng lại: cùng permit chạy hai lần
    j = write_job()
    await runner(redis, chromium, site).run(j, asyncio.Event())
    await runner(redis, chromium, site).run({**j, "id": j["id"]}, asyncio.Event())
    res = await results(redis)
    got = [(r["type"], r["data"].get("code")) for r in res]
    assert got[:8] == [("failed", "PERMIT_INVALID")] * 8, got
    assert got[8] == ("done", None) and got[9] == ("failed", "PERMIT_INVALID"), got
    assert spy.calls.count("submit") == 1                    # chỉ lần hợp lệ đầu tiên gửi


async def test_kill_switch_after_compose_sends_nothing(redis: Redis, chromium: Any, site: FakeSite,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch)

    async def halt() -> None:
        await redis.set(protocol.HALT_KEY, b"{}")

    spy.hooks["compose"] = halt
    await runner(redis, chromium, site).run(write_job(), asyncio.Event())
    r = await one(redis)
    assert r["type"] == "halted" and "proof" not in r["data"]
    assert "submit" not in spy.calls and "confirm_sent" not in spy.calls


async def test_api_cancel_marker_after_compose_sends_nothing(redis: Redis, chromium: Any, site: FakeSite,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner rút đồng ý / tạm dừng tài khoản khi việc đang soạn: api đặt khoá CANCELLED_PREFIX → kiểm cuối trước khi
    gửi phải thấy và dừng (không chỉ dựa vào pub/sub `cancel`)."""
    spy = Spy(monkeypatch)
    j = write_job()

    async def mark() -> None:
        await redis.set(protocol.CANCELLED_PREFIX + j["id"], "1", ex=60)

    spy.hooks["compose"] = mark
    await runner(redis, chromium, site).run(j, asyncio.Event())
    r = await one(redis)
    assert r["type"] == "halted" and "proof" not in r["data"]
    assert "submit" not in spy.calls and "confirm_sent" not in spy.calls


async def test_api_cancel_marker_before_start_opens_no_page(redis: Redis, chromium: Any, site: FakeSite) -> None:
    j = write_job()
    await redis.set(protocol.CANCELLED_PREFIX + j["id"], "1", ex=60)
    await runner(redis, chromium, site).run(j, asyncio.Event())
    r = await one(redis)
    assert r["type"] == "halted"
    assert site.seen == []


async def test_cancel_after_submit_still_reports_done(redis: Redis, chromium: Any, site: FakeSite,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch)
    cancel = asyncio.Event()

    async def do_cancel() -> None:
        cancel.set()
        await asyncio.sleep(0.2)              # cho watcher cơ hội đóng trình duyệt (không được phép)

    spy.hooks["submit"] = do_cancel
    j = write_job()
    await runner(redis, chromium, site).run(j, cancel)
    r = await one(redis)
    assert r["type"] == "done", r
    assert r["data"]["sent"] is True and r["data"]["confirmed"] is True and r["data"]["proof"]
    assert TEXT in spy.body


@pytest.mark.parametrize(("page_file", "code"), [("fb_checkpoint.html", "CHECKPOINT"), ("fb_captcha.html", "CAPTCHA")])
async def test_checkpoint_or_captcha_stops_before_typing(redis: Redis, chromium: Any, site: FakeSite,
                                                         monkeypatch: pytest.MonkeyPatch, page_file: str,
                                                         code: str) -> None:
    spy = Spy(monkeypatch)
    site.pages["/permalink.php"] = page_file
    await runner(redis, chromium, site).run(write_job(), asyncio.Event())
    r = await one(redis)
    assert (r["type"], r["data"]["code"]) == ("failed", code)
    assert "compose" not in spy.calls and "submit" not in spy.calls


async def test_comment_without_reply_button_is_target_not_found(redis: Redis, chromium: Any, site: FakeSite,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy(monkeypatch)
    url = "https://www.facebook.com/permalink.php?story_fbid=1&comment_id=3"
    await runner(redis, chromium, site).run(write_job(target=url), asyncio.Event())
    r = await one(redis)
    assert (r["type"], r["data"]["code"]) == ("failed", "TARGET_NOT_FOUND")
    assert "submit" not in spy.calls


@pytest.mark.parametrize("url", [
    "https://www.facebook.com/permalink.php?story_fbid=1&comment_id=99",     # comment_id không có trên trang
    "https://www.facebook.com/permalink.php?story_fbid=1",                   # thông báo không có comment_id
    "https://www.facebook.com/permalink.php?story_fbid=1&comment_id=10",     # chỉ trùng tiền tố với comment_id=1
])
async def test_reply_never_falls_back_to_another_comment(redis: Redis, chromium: Any, site: FakeSite,
                                                         monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    """Không thấy ĐÚNG bình luận của target_url → TARGET_NOT_FOUND, không gõ, không gửi (trước đây rơi về bình luận
    đầu tiên = trả lời nhầm người dưới tên Owner)."""
    spy = Spy(monkeypatch)
    await runner(redis, chromium, site).run(write_job(target=url), asyncio.Event())
    r = await one(redis)
    assert (r["type"], r["data"]["code"]) == ("failed", "TARGET_NOT_FOUND"), r
    assert "submit" not in spy.calls and "confirm_sent" not in spy.calls


async def test_reply_uses_comment_id_of_target_not_page_url(redis: Redis, chromium: Any, site: FakeSite,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """Facebook chuyển hướng làm mất comment_id trên page.url: vẫn chọn đúng bình luận theo target_url đã ký."""
    spy = Spy(monkeypatch)
    orig_open = FacebookAdapter.open_target

    async def open_then_redirect(self_: Any, page: Any, action: str, target_url: str) -> None:
        await orig_open(self_, page, action, target_url)
        await page.evaluate("() => history.replaceState(null, '', '/permalink.php?story_fbid=1')")

    monkeypatch.setattr(FacebookAdapter, "open_target", open_then_redirect)
    await runner(redis, chromium, site).run(write_job(), asyncio.Event())
    r = await one(redis)
    assert r["type"] == "done" and r["data"]["confirmed"] is True, r
    assert "submit" in spy.calls
    # câu trả lời nằm ngay sau bình luận của Minh (comment_id=2), không phải của Lan
    order = spy.body.index("Còn hàng không ạ?") < spy.body.index(TEXT) < spy.body.index("Bình luận đã khoá")
    assert order


LONG_CID = "Y29tbWVudDoxMjM0NTY3ODkwMTIzNDU2Xzk4NzY1NDMyMTA5ODc2NTQz%3D%3D"


async def test_reply_matches_long_base64_comment_id(redis: Redis, chromium: Any, site: FakeSite,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """comment_id base64 của Facebook dài 50+ ký tự (trước đây cắt [:40] nên không bao giờ khớp → TARGET_NOT_FOUND).
    Vẫn phải chọn ĐÚNG bình luận của Thu, không nhầm sang Tú (id dài hơn, cùng tiền tố)."""
    from ghb.adapters.facebook import comment_id

    url = f"https://www.facebook.com/permalink.php?story_fbid=1&comment_id={LONG_CID}"
    assert len(comment_id(url)) > 40
    spy = Spy(monkeypatch)
    await runner(redis, chromium, site).run(write_job(target=url), asyncio.Event())
    r = await one(redis)
    assert r["type"] == "done" and r["data"]["confirmed"] is True, r
    assert spy.body.index("Ship về Đà Nẵng") < spy.body.index(TEXT) < spy.body.index("Có size XL")


async def test_error_after_submit_still_reports_done(redis: Redis, chromium: Any, site: FakeSite,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Lỗi SAU khi đã bấm gửi: vẫn báo 'done' (sent, chưa xác nhận, send_error) + ảnh chụp — KHÔNG báo 'failed' để
    Owner không bấm gửi lại thành hai lần."""
    async def boom(self_: Any, page: Any, action: str) -> None:
        await page.keyboard.press("Enter")
        raise RuntimeError("Target page, context or browser has been closed")

    monkeypatch.setattr(FacebookAdapter, "submit", boom)
    j = write_job()
    await runner(redis, chromium, site).run(j, asyncio.Event())
    r = await one(redis)
    assert r["type"] == "done", r
    d = r["data"]
    assert d["sent"] is True and d["confirmed"] is False and d["send_error"] is True
    assert protocol.unseal(KEY, d["proof"], f"{ORG}:{ACC}:proof:{j['id']}")[:3] == b"\xff\xd8\xff"
    assert ("submit", False) in [(t["step"], t["ok"]) for t in d["trace"]]


async def test_foreign_target_is_blocked(redis: Redis, chromium: Any, site: FakeSite) -> None:
    await runner(redis, chromium, site).run(write_job(target="https://evil.example.com/x?comment_id=2"),
                                            asyncio.Event())
    r = await one(redis)
    assert (r["type"], r["data"]["code"]) == ("failed", "BLOCKED_URL")
    assert site.seen == []


# ─── trễ cố định (F-59) ───────────────────────────────────────────────────────────────────────────────────────

def test_delay_config(monkeypatch: pytest.MonkeyPatch) -> None:
    assert parse_delay("3") == 3.0 and parse_delay("2,6") == 6.0 and parse_delay("0") == 1.0
    assert parse_delay("100") == 30.0 and parse_delay("rác") == 3.0
    monkeypatch.setenv("GH_BROWSER_KEY", KEY.hex())
    for raw, want in (("3", 3.0), ("2,6", 6.0), ("0", 1.0)):
        monkeypatch.setenv("GH_BROWSER_DELAY", raw)
        assert load().delay == want
    monkeypatch.delenv("GH_BROWSER_DELAY")
    assert load().delay == 3.0


async def test_pause_is_fixed_and_runner_has_no_random(redis: Redis, monkeypatch: pytest.MonkeyPatch) -> None:
    import ghb.runner as mod

    async def no_browser() -> Any:
        raise AssertionError

    seen: list[float] = []

    async def fake_wait_for(aw: Any, timeout: float) -> Any:  # noqa: ASYNC109
        seen.append(timeout)
        aw.close()
        raise TimeoutError

    monkeypatch.setattr(mod.asyncio, "wait_for", fake_wait_for)
    class StubRedis:
        async def exists(self, _key: str) -> int:
            return 0

    r = Runner(cfg(delay=3.0), StubRedis(), no_browser)  # type: ignore[arg-type]
    cancel = asyncio.Event()
    await r.pause(cancel)
    await r.pause(cancel)
    assert seen == [3.0, 3.0]


def test_runner_has_no_random() -> None:
    import ghb.runner as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "import random" not in src and "random." not in src
