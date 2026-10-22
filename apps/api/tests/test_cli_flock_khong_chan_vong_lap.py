"""Review lần 2 v0.1.62 (P3-3) — flock quanh tệp token OAuth của agy KHÔNG được chặn vòng lặp sự kiện.

`token_flock` đồng bộ thăm dò bằng `time.sleep(0.05)` tới 10 giây; `park_token` / `unpark_token` / `write_session`
dùng nó nên gọi thẳng từ coroutine (LoginManager._run, activate, restore_active) sẽ làm đứng cả api khi một lượt agy
đang giữ khoá làm mới token. Các coroutine phải gọi qua bản `*_async` (chạy trong luồng phụ).

Token agy có refresh token mà KHÔNG đọc được hạn ⇒ coi là có thể làm mới (khoá độc quyền) thay vì mặc định "không khoá".
"""

import ast
import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from gh.config import get_settings
from gh.providers import cli as climod
from gh.providers.clients import TOKEN_FILE, token_flock, token_needs_refresh

CLI_SRC = Path(climod.__file__)
SYNC_LOCKING = {"park_token", "unpark_token", "write_session", "write_token_file"}


def test_no_coroutine_calls_the_blocking_token_helpers_directly() -> None:
    tree = ast.parse(CLI_SRC.read_text(encoding="utf-8"))
    bad: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in SYNC_LOCKING:
                bad.append(f"{fn.name}:{node.lineno} gọi {node.func.id}()")
    assert not bad, bad


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    h = tmp_path / "agy" / ".gemini" / "antigravity-cli"
    h.mkdir(parents=True)
    (h / TOKEN_FILE).write_bytes(json.dumps({"access_token": "a", "refresh_token": "r", "expiry": 4102444800}).encode())
    monkeypatch.setenv("GH_CLI_HOME", str(h))
    get_settings.cache_clear()
    yield h
    get_settings.cache_clear()


async def _max_loop_lag(during: "asyncio.Future[object]") -> float:
    """Độ trễ lớn nhất của vòng lặp (giây) trong lúc `during` chạy: một tác vụ ngủ 10 ms rồi đo khoảng thực tế."""
    worst = 0.0

    async def ticker() -> None:
        nonlocal worst
        while True:
            t = time.monotonic()
            await asyncio.sleep(0.01)
            worst = max(worst, time.monotonic() - t - 0.01)

    task = asyncio.create_task(ticker())
    try:
        await during
    finally:
        task.cancel()
    return worst


async def test_async_helpers_do_not_block_the_event_loop_while_the_lock_is_held(home: Path) -> None:
    new = json.dumps({"access_token": "b", "refresh_token": "r2", "expiry": 4102444800}).encode()
    loop = asyncio.get_running_loop()
    holder_has_lock = asyncio.Event()
    release_flag = threading.Event()

    def hold() -> None:
        with token_flock(home, exclusive=True, wait=1):
            loop.call_soon_threadsafe(holder_has_lock.set)
            release_flag.wait(10)

    h = asyncio.create_task(asyncio.to_thread(hold))
    try:
        await holder_has_lock.wait()

        async def writers() -> None:
            # Mỗi lệnh phải chờ khoá (bị chặn) mà KHÔNG làm đứng vòng lặp.
            w = asyncio.gather(climod.park_token_async(), climod.write_session_async("antigravity_cli", new))
            await asyncio.sleep(0.5)
            release_flag.set()
            await w

        lag = await _max_loop_lag(asyncio.ensure_future(writers()))
    finally:
        release_flag.set()
        await h
    assert lag < 0.25, f"vòng lặp bị chặn {lag:.2f}s"
    assert (home / TOKEN_FILE).exists() or (home / (TOKEN_FILE + climod.PARK_SUFFIX)).exists()
    await climod.unpark_token_async()                                      # không ném lỗi


def test_token_without_a_readable_expiry_but_with_a_refresh_token_may_refresh() -> None:
    # không đọc được hạn ⇒ coi như có thể làm mới
    assert token_needs_refresh(b'{"access_token": "a", "refresh_token": "r"}')
    assert token_needs_refresh(b'{"access_token": "a", "refreshToken": "r"}')
    assert token_needs_refresh(b'{"access_token": "a", "refresh_token": "r", "expiry": "khong-phai-ngay"}')
    assert not token_needs_refresh(b'{"access_token": "a", "refresh_token": "r", "expiry": 4102444800}', now=1.0)
    assert not token_needs_refresh(b'{"access_token": "a"}')                  # không refresh token ⇒ không làm mới
    assert not token_needs_refresh(b"{}") and not token_needs_refresh(b"khong-ro")
