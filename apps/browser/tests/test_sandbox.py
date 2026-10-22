"""Sandbox Chromium (F-85): cờ khởi chạy, lùi êm khi máy chủ không cho, dò THẬT qua /proc, nhịp tim có trường sandbox.

Hai nhánh của phép dò thật đều được khẳng định (không skip): CI đặt GH_BROWSER_SANDBOX_REQUIRED=1 (đã mở user namespace)
thì BẮT BUỘC bật được; máy dev chặn userns hoặc chạy bằng root thì probe phải báo enabled=False + lý do và khớp cờ thật.
"""

import asyncio
import os
from typing import Any

import orjson
import pytest
from redis.asyncio import Redis

from ghb import protocol, sandbox
from ghb.worker import BrowserHolder, Worker
from tests.conftest import cfg

REQUIRED = os.environ.get("GH_BROWSER_SANDBOX_REQUIRED") == "1"


class FakeBrowser:
    def is_connected(self) -> bool:
        return True

    async def new_context(self, **_: Any) -> Any:
        raise RuntimeError("không có trình duyệt thật")

    async def close(self) -> None:
        return None


class FakeLauncher:
    """Thay `chromium.launch`: ghi lại chromium_sandbox mỗi lần gọi; `fail_with_sandbox` → lần có sandbox ném lỗi."""

    def __init__(self, fail_with_sandbox: bool = False, fail_all: bool = False):
        self.calls: list[bool] = []
        self.fail_with_sandbox, self.fail_all = fail_with_sandbox, fail_all

    async def __call__(self, **kw: Any) -> Any:
        self.calls.append(kw["chromium_sandbox"])
        if self.fail_all or (self.fail_with_sandbox and kw["chromium_sandbox"]):
            raise RuntimeError("Failed to launch: No usable sandbox! Update your kernel or see chromium docs")
        return FakeBrowser()


# ─── 1. cờ khởi chạy ──────────────────────────────────────────────────────────

def test_launch_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    assert sandbox.launch_kwargs(True) == {"chromium_sandbox": True}
    assert sandbox.launch_kwargs(False) == {"chromium_sandbox": False}
    for val, want in (("", "auto"), ("auto", "auto"), ("on", "on"), ("OFF", "off"), ("lung tung", "auto")):
        monkeypatch.setenv(sandbox.ENV, val)
        assert sandbox.mode() == want
    monkeypatch.delenv(sandbox.ENV)
    assert sandbox.mode() == "auto"


# ─── 2 + 3. dò thật, nhất quán với cờ thật ────────────────────────────────────

async def test_auto_probe_consistent_with_real_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sandbox.ENV, "auto")
    holder = BrowserHolder(cfg(headless=True))
    try:
        browser = await holder.get()
    except Exception as e:  # noqa: BLE001
        if os.environ.get("GH_BROWSER_TESTS_REQUIRED") == "1":
            raise
        pytest.skip(f"không mở được Chromium: {e}")
    try:
        res = holder.sandbox
        assert res is not None and res["mode"] == "auto" and res["checked_at"]
        pids = await sandbox._browser_pids(browser)
        assert pids, "phải tìm được cây tiến trình của đúng trình duyệt vừa mở"
        main = [sandbox._cmdline(p) or [] for p in pids]
        main = [c for c in main if c and not any(a.startswith("--type=") for a in c)]
        assert main, "phải thấy tiến trình trình duyệt chính"
        no_sandbox = any("--no-sandbox" in c for c in main)
        page_ctx = await browser.new_context()
        page = await page_ctx.new_page()
        await page.goto("about:blank")
        isolated = False
        for _ in range(15):   # tiến trình render sinh chậm sau điều hướng
            info = sandbox.inspect(await sandbox._browser_pids(browser))
            isolated = any(r["seccomp"] == "2" and r["userns_differs"] for r in info["renderers"])
            if isolated:
                break
            await asyncio.sleep(0.2)
        await page_ctx.close()
        if REQUIRED:
            assert res["enabled"] is True, f"CI phải bật được sandbox: {res}"
        if res["enabled"]:
            assert res["reason"] is None
            assert not no_sandbox, "enabled=True mà cmdline vẫn có --no-sandbox"
            assert isolated, "enabled=True mà không có renderer Seccomp:2 trong user namespace khác"
        else:
            assert res["reason"], "enabled=False thì phải có lý do"
            assert no_sandbox, "enabled=False (đã lùi) thì Chromium phải chạy với --no-sandbox"
            assert not isolated
    finally:
        await holder.close()


# ─── 4. on không lùi, auto lùi ────────────────────────────────────────────────

async def test_on_fails_without_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sandbox.ENV, "on")
    launcher = FakeLauncher(fail_with_sandbox=True)
    holder = BrowserHolder(cfg(), launcher=launcher)
    with pytest.raises(RuntimeError, match="No usable sandbox"):
        await holder.get()
    assert launcher.calls == [True], "chế độ on không được thử lại không sandbox"
    assert holder.sandbox is None


async def test_auto_falls_back_and_reports_honestly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sandbox.ENV, "auto")
    launcher = FakeLauncher(fail_with_sandbox=True)
    holder = BrowserHolder(cfg(), launcher=launcher)
    await holder.get()
    assert launcher.calls == [True, False]
    assert holder.sandbox is not None
    assert holder.sandbox["enabled"] is False and holder.sandbox["mode"] == "auto"
    assert holder.sandbox["reason"] == sandbox.REASON_BLOCKED
    # Đóng vì rảnh vẫn giữ kết quả dò.
    await holder.close_browser()
    assert holder.sandbox is not None and holder.sandbox["enabled"] is False


async def test_off_does_not_try_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sandbox.ENV, "off")
    launcher = FakeLauncher()
    holder = BrowserHolder(cfg(), launcher=launcher)
    await holder.get()
    assert launcher.calls == [False]
    assert holder.sandbox is not None
    assert holder.sandbox["enabled"] is False and holder.sandbox["mode"] == "off"
    assert holder.sandbox["reason"] == sandbox.REASON_OFF


async def test_warm_up_probes_then_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sandbox.ENV, "auto")
    holder = BrowserHolder(cfg(), launcher=FakeLauncher(fail_with_sandbox=True))
    await holder.warm_up()
    assert holder._browser is None, "khởi động xong phải đóng Chromium"
    assert holder.sandbox is not None and holder.sandbox["enabled"] is False
    # on + launch lỗi ở lúc khởi động: không ném ra ngoài, nhưng báo thật enabled=False.
    monkeypatch.setenv(sandbox.ENV, "on")
    holder2 = BrowserHolder(cfg(), launcher=FakeLauncher(fail_all=True))
    await holder2.warm_up()
    assert holder2.sandbox is not None and holder2.sandbox["enabled"] is False and holder2.sandbox["reason"]


# ─── 5. nhịp tim ──────────────────────────────────────────────────────────────

async def test_heartbeat_has_sandbox_field(redis: Redis, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sandbox.ENV, "auto")
    holder = BrowserHolder(cfg(), launcher=FakeLauncher(fail_with_sandbox=True))
    from ghb.runner import Runner

    w = Worker(cfg(), redis, Runner(cfg(), redis, holder.get), sandbox_info=holder.sandbox_info)
    await w.heartbeat_once()
    before = orjson.loads(await redis.get(protocol.HEARTBEAT_KEY))
    assert before["sandbox"] is None
    assert "version" in before and before["running"] == 0 and "at" in before

    await holder.warm_up()
    await w.heartbeat_once()
    after = orjson.loads(await redis.get(protocol.HEARTBEAT_KEY))
    assert set(after["sandbox"]) == {"enabled", "mode", "reason", "checked_at"}
    assert after["sandbox"]["enabled"] is False and after["sandbox"]["mode"] == "auto"
    assert after["sandbox"]["reason"] and after["sandbox"]["checked_at"]
    assert "version" in after and after["running"] == 0
