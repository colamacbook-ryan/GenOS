"""Gói G (P3, review 16/10/2026) — timeout agy cho việc nền < JOB_TIMEOUT của worker; khoá (flock) quanh làm mới/ghi
tệp token OAuth agy dùng chung giữa api ↔ worker ↔ các lượt agy song song."""

import asyncio
import base64
import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gh import worker
from gh.config import get_settings
from gh.db import sessionmaker
from gh.providers import cli as climod
from gh.providers import clients
from gh.providers.clients import (
    TOKEN_FILE,
    TOKEN_LOCK_FILE,
    AgyClient,
    Message,
    token_expiry,
    token_flock,
    token_needs_refresh,
)
from gh.providers.router import AGY_BACKGROUND_TIMEOUT_S, ModelRouter
from tests.phase2 import org_id
from tests.test_background_cli_v0141 import FakeCli, cli_provider


def _token(expiry: float, **extra: Any) -> bytes:
    return json.dumps({"access_token": "tok", "refresh_token": "ref", "expiry": expiry, **extra}).encode()


def _home(tmp: Path, expiry: float) -> Path:
    home = tmp / "agy" / ".gemini" / "antigravity-cli"
    home.mkdir(parents=True)
    (home / TOKEN_FILE).write_bytes(_token(expiry))
    return home


# ─── (a) timeout việc nền ─────────────────────────────────────────────────

def test_background_timeout_is_below_the_worker_job_timeout() -> None:
    """Một lượt agy nền phải kết thúc (kể cả lượt gọi lại khi JSON sai) trước khi arq giết job."""
    assert AGY_BACKGROUND_TIMEOUT_S < worker.JOB_TIMEOUT
    assert 2 * AGY_BACKGROUND_TIMEOUT_S < worker.JOB_TIMEOUT          # lượt đầu + lượt gọi lại còn dư thời gian dọn
    assert AgyClient("agy", "~/x").timeout == 300.0                     # lượt Gen của Sếp (api) giữ nguyên 300s


class _Timed(FakeCli):
    """CLI giả ghi lại timeout lúc được gọi."""

    def __init__(self, timeout: float = 300.0) -> None:
        super().__init__("agy")
        self.timeout = timeout
        self.seen: list[float] = []

    async def generate(self, model: str, messages: list[Message], **kw: Any) -> Any:
        self.seen.append(self.timeout)
        return await super().generate(model, messages, **kw)


def test_router_client_caps_timeout_only_for_background() -> None:
    cli = _Timed(300.0)
    router = ModelRouter(None, None, cli_factory=cli)       # type: ignore[arg-type]
    p = SimpleNamespace(kind="antigravity_cli")
    assert router._client(p, None, where="inproc") is cli and cli.timeout == 300.0
    router._client(p, None, where="inproc", background=True)
    assert cli.timeout == AGY_BACKGROUND_TIMEOUT_S
    short = _Timed(30.0)                                    # timeout đã ngắn hơn trần thì giữ nguyên (không nới ra)
    ModelRouter(None, None, cli_factory=short)._client(p, None, where="inproc", background=True)    # type: ignore[arg-type]
    assert short.timeout == 30.0


async def test_generate_applies_the_cap_to_background_purposes_only(  # type: ignore[no-untyped-def]
        app, db, redis, boxctl) -> None:
    org = await org_id(db)
    await cli_provider(db, org, "antigravity_cli", 1)
    for purpose, expect in (("refinery.extract", AGY_BACKGROUND_TIMEOUT_S), ("duty_decide", AGY_BACKGROUND_TIMEOUT_S),
                            ("gen.briefing", AGY_BACKGROUND_TIMEOUT_S), ("gen.turn", 300.0)):
        cli = _Timed(300.0)
        # core.refinery chạy qua hộp riêng (H2), core.gen trong tiến trình: trần thời gian áp cho cả hai client.
        router = ModelRouter(sessionmaker(), redis, cli_factory=cli, box_factory=cli)
        await router.generate(org, agent_key="core.gen" if purpose == "gen.turn" else "core.refinery",
                              purpose=purpose, messages=[Message("user", "x")], json_mode=False)
        assert cli.seen == [expect], (purpose, cli.seen)


# ─── (b) hạn token ────────────────────────────────────────────────────────

def test_token_expiry_and_needs_refresh() -> None:
    now = 1_000_000.0
    assert token_expiry(_token(now + 5)) == now + 5
    assert token_expiry(b'{"expires_at": "2026-10-16T10:00:00Z"}') == pytest.approx(1_792_144_800.0)
    claims = base64.urlsafe_b64encode(json.dumps({"exp": 4102444800}).encode()).decode().rstrip("=")
    assert token_expiry(json.dumps({"id_token": f"h.{claims}.s"}).encode()) == 4102444800.0
    # định dạng thật của agy 1.2.9: expiry + refresh_token nằm trong "token" (không ở cấp đầu)
    nested = json.dumps({"token": {"access_token": "a", "refresh_token": "r", "expiry": "2026-10-16T10:00:00.5Z"},
                         "auth_method": "oauth"}).encode()
    assert token_expiry(nested) == pytest.approx(1_792_144_800.5)
    assert token_needs_refresh(nested, now=1_792_144_790.0) and not token_needs_refresh(nested, now=1_792_140_000.0)
    no_exp = json.dumps({"token": {"refresh_token": "r"}}).encode()
    assert token_expiry(no_exp) is None and token_needs_refresh(no_exp)  # có refresh, không rõ hạn ⇒ coi có thể làm mới
    assert not token_needs_refresh(json.dumps({"token": {"access_token": "a"}}).encode())
    assert token_expiry(b"khong-phai-json") is None and token_expiry(b"[]") is None and token_expiry(b"{}") is None
    assert token_needs_refresh(_token(now + 30), now=now)                  # sắp hết
    assert token_needs_refresh(_token(now - 5), now=now)                   # đã hết
    assert not token_needs_refresh(_token(now + 3600), now=now)            # còn lâu
    assert not token_needs_refresh(b"khong-ro", now=now)                   # không rõ hạn ⇒ không khoá


# ─── (c) khoá flock ───────────────────────────────────────────────────────

def test_flock_is_exclusive_between_holders_and_fail_open_on_timeout(tmp_path: Path, caplog) -> None:  # type: ignore[no-untyped-def]
    home = _home(tmp_path, time.time() + 3600)
    with token_flock(home, exclusive=True, wait=1) as got:
        assert got is True and (home / TOKEN_LOCK_FILE).exists()
        started = time.monotonic()
        with caplog.at_level(logging.WARNING), token_flock(home, exclusive=True, wait=0.3) as second:
            assert second is False                                         # hết hạn chờ ⇒ đi tiếp, không treo
        assert 0.25 <= time.monotonic() - started < 3
        assert "đi tiếp không khoá" in caplog.text
    with token_flock(home, exclusive=True, wait=0.3) as again:             # nhả rồi thì lấy lại được
        assert again is True


def test_flock_excludes_another_process(tmp_path: Path) -> None:
    home = _home(tmp_path, time.time() + 3600)
    code = ("import sys, time; from gh.providers.clients import token_flock\n"
            "with token_flock(sys.argv[1], exclusive=True, wait=5) as ok:\n"
            "    print('held' if ok else 'no', flush=True); time.sleep(2.0)\n")
    child = subprocess.Popen([sys.executable, "-c", code, str(home)], cwd=Path(__file__).resolve().parents[1],
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "held"
        with token_flock(home, exclusive=True, wait=0.3) as ok:
            assert ok is False                                             # tiến trình khác đang giữ
    finally:
        child.wait(timeout=20)
    with token_flock(home, exclusive=True, wait=1) as ok:
        assert ok is True                                                  # nó thoát ⇒ khoá được nhả


def test_token_file_writers_wait_for_the_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ghi tệp phiên (`write_token_file`, park/unpark) giữ khoá độc quyền ⇒ chờ lượt agy đang làm mới token."""
    home = _home(tmp_path, time.time() + 60)
    monkeypatch.setenv("GH_CLI_HOME", str(home))
    get_settings.cache_clear()
    try:
        new = _token(time.time() + 7200)
        done = threading.Event()
        with token_flock(home, exclusive=True, wait=1):
            t = threading.Thread(target=lambda: (climod.write_token_file(new), done.set()))
            t.start()
            assert not done.wait(0.4)                                      # đang bị chặn bởi khoá
            assert token_expiry((home / TOKEN_FILE).read_bytes()) != token_expiry(new)
        t.join(timeout=10)
        assert done.is_set() and (home / TOKEN_FILE).read_bytes() == new   # nhả khoá ⇒ ghi xong, đủ nội dung
        with token_flock(home, exclusive=True, wait=1):
            parked = threading.Event()
            t2 = threading.Thread(target=lambda: (climod.park_token(), parked.set()))
            t2.start()
            assert not parked.wait(0.3)
        t2.join(timeout=10)
        assert parked.is_set() and not (home / TOKEN_FILE).exists() and climod.unpark_token()
        assert (home / TOKEN_FILE).read_bytes() == new
    finally:
        get_settings.cache_clear()


# ─── (d) các lượt agy song song ───────────────────────────────────────────

def _slow_agy(tmp: Path) -> tuple[str, Path]:
    """agy giả: ghi mốc bắt đầu/kết thúc (0,6s mỗi lượt) vào một tệp rồi trả JSON."""
    log = tmp / "spans.log"
    path = tmp / "bin" / "agy"
    path.parent.mkdir(exist_ok=True)
    path.write_text("#!/bin/sh\n"
                    'case "$1" in --version) echo 1.2.9; exit 0;; esac\n'
                    "cat > /dev/null\n"
                    f'echo "start $(date +%s.%N)" >> {log}\n'
                    "sleep 0.6\n"
                    f'echo "end $(date +%s.%N)" >> {log}\n'
                    'echo \'{"response": "ok"}\'\n')
    path.chmod(0o755)
    return str(path), log


async def _two_turns(binary: str, home: Path) -> None:
    c1, c2 = AgyClient(binary, str(home), timeout=30), AgyClient(binary, str(home), timeout=30)
    msgs = [Message("user", "hi")]
    outs = await asyncio.gather(c1.generate("gemini-3.1-pro", msgs, json_mode=False, temperature=0),
                                c2.generate("gemini-3.1-pro", msgs, json_mode=False, temperature=0))
    assert [o.text for o in outs] == ["ok", "ok"]


def _sequence(log: Path) -> list[str]:
    rows = sorted((float(x.split()[1]), x.split()[0]) for x in log.read_text().splitlines())
    return [k for _t, k in rows]


async def test_concurrent_turns_with_a_token_near_expiry_run_one_after_another(tmp_path: Path) -> None:
    binary, log = _slow_agy(tmp_path)
    await _two_turns(binary, _home(tmp_path, time.time() + 30))          # sắp hết hạn ⇒ có thể làm mới ⇒ tuần tự
    assert _sequence(log) == ["start", "end", "start", "end"]


async def test_concurrent_turns_with_a_fresh_token_still_run_in_parallel(tmp_path: Path) -> None:
    binary, log = _slow_agy(tmp_path)
    await _two_turns(binary, _home(tmp_path, time.time() + 7200))        # còn mới ⇒ không khoá ⇒ song song
    assert _sequence(log) == ["start", "start", "end", "end"]


async def test_a_refreshing_turn_waits_at_most_its_timeout_for_the_lock(tmp_path: Path) -> None:
    """Khoá bị giữ lâu (tiến trình khác treo) ⇒ lượt chờ tối đa min(timeout, 60s) rồi chạy tiếp, không treo mãi."""
    binary, _log = _slow_agy(tmp_path)
    home = _home(tmp_path, time.time() + 30)
    client = AgyClient(binary, str(home), timeout=1.5)
    msgs = [Message("user", "hi")]
    with token_flock(home, exclusive=True, wait=1):
        started = time.monotonic()
        out = await client.generate("gemini-3.1-pro", msgs, json_mode=False, temperature=0)
    assert out.text == "ok"
    assert 1.4 <= time.monotonic() - started < 6                          # chờ ~1,5s (= timeout) rồi mới chạy


def test_lock_api_is_exported() -> None:
    assert clients.TOKEN_LOCK_FILE == ".gh-token.lock" and clients.TOKEN_LOCK_WAIT_S > 0
