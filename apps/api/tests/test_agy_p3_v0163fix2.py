"""Review lần 2 gói sửa v0.1.63 — các mục P3 phía agy trong tiến trình (`gh.providers.clients`).

- P3-1: chép ngược token làm mới (`sync_token_back`) không lấy được khoá ⇒ BỎ ghi (trước: đi tiếp ghi không khoá); lượt
  agy chờ khoá đó trong luồng phụ (trước: `time.sleep` của `_try_flock` chặn vòng lặp sự kiện tới 2 giây).
- P3-5: `_workdir()` rò khi `make_turn_home` ném lỗi; HOME tạm `gh-agy-home-*` (chứa token) / `gh-agy-*` còn lại sau khi
  tiến trình bị SIGKILL (api/worker không có tmpfs /tmp) được dọn khi khởi động."""

import asyncio
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from gh.providers import clients
from gh.providers.clients import TOKEN_FILE
from tests.test_agy_home_tam_v0163fix import TOKEN, _agy, _say, vol  # noqa: F401 — fixture dùng chung

NEW = json.dumps({**json.loads(TOKEN), "access_token": "tok-moi"}).encode()


# ─── P3-1 ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_sync_back_without_the_lock_skips_the_write(vol: Path) -> None:  # noqa: F811
    with clients.token_flock(vol, exclusive=True, wait=1) as held:   # người ghi khác đang giữ khoá
        assert held
        assert clients.sync_token_back(str(vol), TOKEN, NEW, wait=0.2) is False
        assert (vol / TOKEN_FILE).read_bytes() == TOKEN
    assert clients.sync_token_back(str(vol), TOKEN, NEW, wait=0.2) is True   # có khoá ⇒ ghi
    assert (vol / TOKEN_FILE).read_bytes() == NEW


async def test_turn_waits_for_the_sync_lock_off_the_event_loop(tmp_path: Path, vol: Path) -> None:  # noqa: F811
    """Một người ghi khác giữ khoá 3 giây: lượt REFRESH chờ khoá (2 giây) KHÔNG chặn vòng lặp, rồi bỏ ghi."""
    release = threading.Event()
    holding = threading.Event()

    def hold() -> None:
        with clients.token_flock(vol, exclusive=True, wait=1):
            holding.set()
            release.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    assert holding.wait(5)
    worst = 0.0
    stop = False

    async def tick() -> None:
        nonlocal worst
        while not stop:
            t0 = time.monotonic()
            await asyncio.sleep(0.05)
            worst = max(worst, time.monotonic() - t0)

    ticker = asyncio.create_task(tick())
    try:
        assert await _say(_agy(tmp_path, vol), "REFRESH") == "echo:REFRESH"
    finally:
        stop = True
        await ticker
        release.set()
        t.join(10)
    assert worst < 1.0, f"vòng lặp sự kiện bị chặn {worst:.2f}s"
    assert (vol / TOKEN_FILE).read_bytes() == TOKEN                  # không có khoá ⇒ không ghi đè


# ─── P3-5 ──────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_workdir_is_removed_when_turn_home_fails(tmp_path: Path, vol: Path,  # noqa: F811
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "t").mkdir()
    monkeypatch.setattr(clients.tempfile, "tempdir", str(tmp_path / "t"))

    def boom(token: bytes | None) -> Path:
        raise OSError("đầy đĩa")

    monkeypatch.setattr(clients, "make_turn_home", boom)
    with pytest.raises(OSError):
        await _say(_agy(tmp_path, vol), "LIST")
    assert list((tmp_path / "t").iterdir()) == []


def _dead_pid() -> int:
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


def test_turn_dirs_carry_the_owner_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clients.tempfile, "tempdir", str(tmp_path))
    home = clients.make_turn_home(None)
    work = clients.AgyClient._workdir()  # noqa: SLF001
    assert home.name.startswith(f"gh-agy-home-{os.getpid()}-")
    assert Path(work).name.startswith(f"gh-agy-{os.getpid()}-")


def test_sweep_stale_turn_dirs(tmp_path: Path) -> None:
    root = tmp_path / "tmp"
    root.mkdir()
    dead, me, alive = _dead_pid(), os.getpid(), os.getppid()
    stale_home = root / f"gh-agy-home-{dead}-abc"                     # tiến trình chủ đã chết (SIGKILL)
    (stale_home / ".gemini" / "antigravity-cli").mkdir(parents=True)
    (stale_home / ".gemini" / "antigravity-cli" / TOKEN_FILE).write_bytes(TOKEN)
    (root / f"gh-agy-{dead}-xyz").mkdir()
    (root / f"gh-agy-home-{me}-old").mkdir()                          # chính pid này lúc khởi động (container khởi
    #                                                                  động lại cấp lại số pid cũ) ⇒ cũng là đồ cũ
    (root / f"gh-agy-{alive}-dang-chay").mkdir()                      # tiến trình khác còn sống: giữ
    outside = tmp_path / "ngoai"
    outside.mkdir()
    (outside / "giu").write_text("x")
    (root / f"gh-agy-home-{dead}-lienket").symlink_to(outside, target_is_directory=True)   # xoá liên kết, không theo
    (root / f"gh-agy-{dead}-tep").write_text("tệp thường: không phải thư mục lượt — giữ")
    (root / "gh-agy-home-abcdefgh").mkdir()                           # dạng cũ không có pid: không đoán, giữ
    (root / "khac").mkdir()
    assert clients.sweep_stale_turn_dirs(root) == 4
    assert sorted(p.name for p in root.iterdir()) == sorted(
        [f"gh-agy-{alive}-dang-chay", f"gh-agy-{dead}-tep", "gh-agy-home-abcdefgh", "khac"])
    assert (outside / "giu").exists()
    assert clients.sweep_stale_turn_dirs(tmp_path / "khong-co") == 0


def test_sweep_runs_at_api_and_worker_startup() -> None:
    """Gọi lúc khởi động api (lifespan) và worker (on_startup) — quét mã để không bị gỡ nhầm."""
    root = Path(clients.__file__).resolve().parents[1]
    assert "clients.sweep_stale_turn_dirs" in (root / "app.py").read_text(encoding="utf-8")
    assert "clients.sweep_stale_turn_dirs" in (root / "worker.py").read_text(encoding="utf-8")
