"""v0.1.45 — đọc/ghi AN TOÀN hộp thư run/ dùng chung với genh (gh.hostlink_io).

Ghi: mkstemp (O_EXCL) → fchmod 0644 → fsync → os.replace — không đi theo symlink cài sẵn ở tên đích/tên tạm, không
rò `*.tmp`. Đọc: không theo symlink, không treo ở FIFO, 1 liên kết, ≤ 64 KiB, đúng chủ — sai ⇒ None ("không rõ",
không 500).
"""

import json
import os
import stat
from pathlib import Path

import pytest

from gh import hostlink_io
from gh.hostlink_io import read_state, write_request
from tests.conftest import Api
from tests.test_offsite_v0140 import link as offsite_link  # noqa: F401 — fixture dùng lại
from tests.test_system_backups import env as backups_env  # noqa: F401 — fixture dùng lại
from tests.test_system_update import link  # noqa: F401 — fixture dùng lại

SECRET = '{"POSTGRES_PASSWORD": "khong-duoc-doi"}'


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "run"
    (d / "request").mkdir(parents=True)
    return d


@pytest.fixture
def secret_file(tmp_path: Path) -> Path:
    p = tmp_path / "secrets.json"
    p.write_text(SECRET)
    return p


# ─── write_request ──────────────────────────────────────────────────────────────────────────────────────────

def test_write_request_replaces_planted_symlink(run_dir: Path, secret_file: Path) -> None:
    target = run_dir / "request" / "update.json"
    target.symlink_to(secret_file)
    write_request(run_dir / "request", "update.json", {"id": "x"})
    assert not target.is_symlink() and target.is_file()
    assert json.loads(target.read_text()) == {"id": "x"}
    assert secret_file.read_text() == SECRET  # tệp symlink trỏ tới không đổi
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert not list((run_dir / "request").glob("*.tmp"))  # không rò tệp tạm


def test_write_request_ignores_planted_tmp_names(run_dir: Path, secret_file: Path) -> None:
    d = run_dir / "request"
    planted = {"restore.tmp", "restore.json.tmp", ".restore.json.tmp", ".restore.json.0.tmp"}
    for name in planted:
        (d / name).symlink_to(secret_file)
    write_request(d, "restore.json", {"key": "k"})
    assert secret_file.read_text() == SECRET
    assert json.loads((d / "restore.json").read_text()) == {"key": "k"}
    assert {p.name for p in d.iterdir() if p.name.endswith(".tmp")} == planted  # chỉ còn bẫy cũ, không rò tệp tạm
    assert all((d / n).is_symlink() for n in planted)


def test_write_request_cleans_tmp_on_error(run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(src: str, dst: object) -> None:
        raise OSError("đĩa đầy")

    monkeypatch.setattr(hostlink_io.os, "replace", boom)
    with pytest.raises(OSError):
        write_request(run_dir / "request", "offsite.json", {"action": "run"})
    assert list((run_dir / "request").iterdir()) == []


# ─── read_state ─────────────────────────────────────────────────────────────────────────────────────────────

def _ok(run_dir: Path, name: str = "update-status.json", body: object | None = None) -> Path:
    p = run_dir / name
    p.write_text(json.dumps(body if body is not None else {"state": "done"}))
    return p


def test_read_state_valid(run_dir: Path) -> None:
    assert read_state(_ok(run_dir), root=run_dir) == {"state": "done"}
    assert read_state(run_dir / "missing.json", root=run_dir) is None


def test_read_state_rejects_symlink(run_dir: Path, tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text(json.dumps({"state": "running"}))
    (run_dir / "update-status.json").symlink_to(real)
    assert read_state(run_dir / "update-status.json", root=run_dir) is None


def test_read_state_rejects_fifo_without_hanging(run_dir: Path) -> None:
    os.mkfifo(run_dir / "update-status.json")
    assert read_state(run_dir / "update-status.json", root=run_dir) is None


def test_read_state_rejects_hardlink(run_dir: Path) -> None:
    p = _ok(run_dir)
    os.link(p, run_dir / "other.json")
    assert read_state(p, root=run_dir) is None


def test_read_state_rejects_large_and_bad_json(run_dir: Path) -> None:
    big = run_dir / "big.json"
    big.write_text(json.dumps({"x": "a" * (64 * 1024)}))
    assert read_state(big, root=run_dir) is None
    bad = run_dir / "bad.json"
    bad.write_text("{không phải json")
    assert read_state(bad, root=run_dir) is None
    arr = run_dir / "arr.json"
    arr.write_text("[1, 2]")
    assert read_state(arr, root=run_dir) is None


def test_read_state_rejects_foreign_owner(run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _ok(run_dir)
    real_fstat = os.fstat

    def fstat_other_uid(fd: int) -> os.stat_result:
        st = list(real_fstat(fd))
        st[stat.ST_UID] = st[stat.ST_UID] + 1
        return os.stat_result(st)

    monkeypatch.setattr(os, "fstat", fstat_other_uid)
    assert read_state(p, root=run_dir) is None
    assert read_state(p, root=run_dir, allow_self=True) is None  # không phải chủ run/, không phải api


def test_read_state_owner_is_run_root_or_self(run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # uid giả (≠ 0) để test đúng cả khi CI chạy bằng root: tệp do api (5001) ghi, run/ thuộc genh (5002).
    p = _ok(run_dir / "request", "update.json", {"id": "x"})
    real_stat, real_fstat = os.stat, os.fstat

    def with_uid(st: os.stat_result, uid: int) -> os.stat_result:
        lst = list(st)
        lst[stat.ST_UID] = uid
        return os.stat_result(lst)

    def fake_stat(path: object, *a: object, **kw: object) -> os.stat_result:
        st = real_stat(path, *a, **kw)  # type: ignore[arg-type]
        return with_uid(st, 5002) if Path(str(path)) == run_dir else st

    monkeypatch.setattr(os, "stat", fake_stat)
    monkeypatch.setattr(os, "fstat", lambda fd: with_uid(real_fstat(fd), 5001))
    monkeypatch.setattr(os, "geteuid", lambda: 5001)
    assert read_state(p, root=run_dir) is None  # chủ run/ (genh) khác ⇒ tệp lạ
    assert read_state(p, root=run_dir, allow_self=True) == {"id": "x"}  # tệp yêu cầu do chính api ghi


def test_read_state_accepts_root_owned(run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`sudo genh doctor/start/update` ghi lại genh.json, update-status.json… với chủ uid 0 — vẫn phải đọc được."""
    p = _ok(run_dir)
    real_fstat = os.fstat

    def fstat_root_owned(fd: int) -> os.stat_result:
        st = list(real_fstat(fd))
        st[stat.ST_UID] = 0
        return os.stat_result(st)

    monkeypatch.setattr(os, "fstat", fstat_root_owned)
    assert read_state(p, root=run_dir) is not None


# ─── API: tệp hợp lệ ⇒ bình thường; tệp bẫy ⇒ "không rõ", không 500 ─────────────────────────────────────────

async def test_update_status_trap_reads_unknown(owner_api: Api, link: Path, tmp_path: Path) -> None:  # noqa: F811
    (link / "update-status.json").write_text(json.dumps({"state": "done", "from": "v0.1.16", "to": "v0.1.17"}))
    r = await owner_api.get("/system/update")
    assert r.status_code == 200 and r.json()["state"] == "done"

    (link / "update-status.json").unlink()
    real = tmp_path / "planted.json"
    real.write_text(json.dumps({"state": "failed", "message": "bị cài"}))
    (link / "update-status.json").symlink_to(real)
    r = await owner_api.get("/system/update")
    assert r.status_code == 200 and r.json()["state"] == "idle"

    (link / "update-status.json").unlink()
    os.mkfifo(link / "update-status.json")
    r = await owner_api.get("/system/update")
    assert r.status_code == 200 and r.json()["state"] == "idle"

    # Yêu cầu do api ghi (tệp thường) vẫn được thấy; symlink ở tên đích bị thay khi bấm.
    (link / "update-status.json").unlink()
    (link / "request" / "update.json").symlink_to(real)
    r = await owner_api.get("/system/update")
    assert r.status_code == 200 and r.json()["state"] == "idle"
    r = await owner_api.send("POST", "/system/update")
    assert r.status_code == 202, r.text
    assert r.json()["state"] == "requested"
    assert not (link / "request" / "update.json").is_symlink()
    assert json.loads(real.read_text())["message"] == "bị cài"


async def test_restore_status_trap_reads_unknown(owner_api: Api, backups_env, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    d: Path = backups_env["link"]
    (d / "restore-status.json").write_text(json.dumps({"state": "done", "key": "k"}))
    r = await owner_api.get("/system/backups")
    assert r.status_code == 200 and r.json()["restore"]["state"] == "done"
    (d / "restore-status.json").unlink()
    os.mkfifo(d / "restore-status.json")
    r = await owner_api.get("/system/backups")
    assert r.status_code == 200 and r.json()["restore"]["state"] == "idle"


async def test_offsite_status_trap_reads_unknown(owner_api: Api, offsite_link: Path, tmp_path: Path) -> None:  # noqa: F811
    status = {"schema": 1, "configured": True, "dest": "/mnt/usb", "state": "ok", "error_code": "",
              "last_attempt_at": "", "last_success_at": "", "last_file": "", "last_size_bytes": 0,
              "verified": True, "kept": 1, "schedule": "systemd", "key_id": ""}
    (offsite_link / "offsite-status.json").write_text(json.dumps(status))
    r = await owner_api.get("/system/offsite")
    assert r.status_code == 200 and r.json()["configured"] is True
    (offsite_link / "offsite-status.json").unlink()
    real = tmp_path / "planted.json"
    real.write_text(json.dumps(status))
    (offsite_link / "offsite-status.json").symlink_to(real)
    r = await owner_api.get("/system/offsite")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False and body["state"] == "not_configured"
