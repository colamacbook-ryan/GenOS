"""v0.1.44 (F-4b) — "Gói chẩn đoán" từ Console: yêu cầu qua run/request/doctor.json, genh chạy `genh doctor`, Console
tải zip trong run/diagnostics/ (chỉ tên hợp lệ, không theo liên kết mềm, PIN + chỉ Owner)."""

import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from gh.config import get_settings
from gh.db import admin_sessionmaker
from gh.system_api import diagnostics
from tests.conftest import Api, verify_pin
from tests.test_rbac_api import login_as

ZIP = "genh-doctor-20261003T010203Z.zip"
CONTENT = b"PK\x03\x04 gia-zip-chan-doan"


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "run"
    (d / "request").mkdir(parents=True)
    (d / "diagnostics").mkdir()
    monkeypatch.setattr(get_settings(), "host_link_dir", str(d))
    return d


def _supported(host: Path) -> None:
    (host / "genh.json").write_text(json.dumps({"version": "v0.1.44", "updater": "systemd",
                                                "requests": ["update", "restore", "offsite", "doctor", "watchdog"]}))


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _done(host: Path, name: str = ZIP, **kw: Any) -> None:
    (host / "doctor-status.json").write_text(json.dumps({
        "schema": 1, "request_id": "0123456789abcdef", "state": "done", "started_at": "2026-10-03T01:02:00Z",
        "finished_at": "2026-10-03T01:02:03Z", "file": name, "size_bytes": len(CONTENT), "sha256": "a" * 64,
        "error_code": "", "message": "", **kw}))


async def test_unsupported_409(owner_api: Api, host: Path) -> None:
    g = (await owner_api.get("/system/diagnostics")).json()
    assert g["supported"] is False and g["state"] == "idle" and g["command"] == "genh doctor"
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/system/diagnostics")
    assert r.status_code == 409 and r.json()["code"] == "DIAG_UNSUPPORTED"
    assert r.json()["detail"] == "Bản genh trên máy chủ chưa hỗ trợ — chạy genh doctor trên máy chủ"
    assert not (host / "request" / "doctor.json").exists()


async def test_request_needs_pin_writes_file_then_busy(owner_api: Api, host: Path) -> None:
    _supported(host)
    r = await owner_api.send("POST", "/system/diagnostics")
    assert r.status_code == 423
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/system/diagnostics")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["state"] == "pending" and body["supported"] is True and len(body["request_id"]) == 16
    f = host / "request" / "doctor.json"
    req = json.loads(f.read_text())
    assert set(req) == {"schema", "request_id", "requested_at"} and req["schema"] == 1
    assert req["request_id"] == body["request_id"] and req["requested_at"].endswith("Z")
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o644
    r = await owner_api.send("POST", "/system/diagnostics")
    assert r.status_code == 409 and r.json()["code"] == "DIAG_BUSY"
    # genh nhận và đang chạy (< 15 phút) ⇒ vẫn bận; quá 15 phút ⇒ cho yêu cầu lại.
    f.unlink()
    (host / "doctor-status.json").write_text(json.dumps({"schema": 1, "request_id": req["request_id"],
                                                         "state": "running", "started_at": _iso(datetime.now(UTC))}))
    assert (await owner_api.get("/system/diagnostics")).json()["state"] == "running"
    assert (await owner_api.send("POST", "/system/diagnostics")).json()["code"] == "DIAG_BUSY"
    old = _iso(datetime.now(UTC) - timedelta(minutes=20))
    (host / "doctor-status.json").write_text(json.dumps({"schema": 1, "request_id": req["request_id"],
                                                         "state": "running", "started_at": old}))
    assert (await owner_api.send("POST", "/system/diagnostics")).status_code == 202
    async with admin_sessionmaker()() as s:
        n = (await s.execute(text("""SELECT count(*) FROM ops.action_log
                                     WHERE action = 'system.diagnostics.request'"""))).scalar_one()
    assert n == 2


async def test_download_valid_zip(owner_api: Api, host: Path) -> None:
    _supported(host)
    (host / "diagnostics" / ZIP).write_bytes(CONTENT)
    _done(host)
    g = (await owner_api.get("/system/diagnostics")).json()
    assert g["state"] == "done" and g["file_name"] == ZIP and g["size_bytes"] == len(CONTENT)
    assert g["sha256"] == "a" * 64 and g["request_id"] == "0123456789abcdef"
    r = await owner_api.get("/system/diagnostics/download")
    assert r.status_code == 423
    await verify_pin(owner_api)
    r = await owner_api.get("/system/diagnostics/download")
    assert r.status_code == 200 and r.content == CONTENT
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"] == f'attachment; filename="{ZIP}"'
    async with admin_sessionmaker()() as s:
        n = (await s.execute(text("""SELECT count(*) FROM ops.action_log
                                     WHERE action = 'system.diagnostics.download'"""))).scalar_one()
    assert n == 1


async def test_download_rejects_bad_names_and_symlinks(owner_api: Api, host: Path, tmp_path: Path) -> None:
    await verify_pin(owner_api)
    assert (await owner_api.get("/system/diagnostics/download")).json()["code"] == "DIAG_NOT_READY"
    secret = tmp_path / "secrets.json"
    secret.write_text('{"master_key": "khong-duoc-lo"}')
    for name in ("../secrets.json", "secrets.json", "genh-doctor-1.zip", "/etc/passwd"):
        _done(host, name=name)
        r = await owner_api.get("/system/diagnostics/download")
        assert r.status_code == 409 and r.json()["code"] == "DIAG_FILE_UNSAFE", name
        assert "khong-duoc-lo" not in r.text
    # Tên hợp lệ nhưng là liên kết mềm ⇒ từ chối.
    (host / "diagnostics" / ZIP).symlink_to(secret)
    _done(host)
    r = await owner_api.get("/system/diagnostics/download")
    assert r.status_code == 409 and r.json()["code"] == "DIAG_FILE_UNSAFE" and "khong-duoc-lo" not in r.text
    # Tên hợp lệ nhưng chưa có tệp ⇒ chưa sẵn sàng; là thư mục ⇒ không an toàn.
    (host / "diagnostics" / ZIP).unlink()
    assert (await owner_api.get("/system/diagnostics/download")).json()["code"] == "DIAG_NOT_READY"
    (host / "diagnostics" / ZIP).mkdir()
    assert (await owner_api.get("/system/diagnostics/download")).json()["code"] == "DIAG_FILE_UNSAFE"
    # Chưa xong ⇒ chưa sẵn sàng.
    _done(host, state="running")
    assert (await owner_api.get("/system/diagnostics/download")).json()["code"] == "DIAG_NOT_READY"


async def test_download_rejects_symlinked_dir_and_too_big(owner_api: Api, host: Path, tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    await verify_pin(owner_api)
    (host / "diagnostics" / ZIP).write_bytes(CONTENT)
    _done(host)
    monkeypatch.setattr(diagnostics, "MAX_BYTES", 5)
    assert (await owner_api.get("/system/diagnostics/download")).json()["code"] == "DIAG_FILE_UNSAFE"
    monkeypatch.setattr(diagnostics, "MAX_BYTES", 200 * 1024 * 1024)
    other = tmp_path / "khac"
    other.mkdir()
    (other / ZIP).write_bytes(CONTENT)
    (host / "diagnostics" / ZIP).unlink()
    (host / "diagnostics").rmdir()
    (host / "diagnostics").symlink_to(other)
    assert (await owner_api.get("/system/diagnostics/download")).json()["code"] == "DIAG_FILE_UNSAFE"
    # Tráo thư mục SAU lần kiểm is_symlink (đua TOCTOU — run/ là 0777): mở thư mục bằng O_NOFOLLOW vẫn từ chối.
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    r = await owner_api.get("/system/diagnostics/download")
    assert r.json()["code"] == "DIAG_FILE_UNSAFE" and CONTENT not in r.content


async def test_failed_state_and_staff_forbidden(owner_api: Api, host: Path, client: httpx.AsyncClient,
                                                db: Any) -> None:
    _supported(host)
    (host / "doctor-status.json").write_text(json.dumps({
        "schema": 1, "request_id": "0123456789abcdef", "state": "failed", "error_code": "GH-E9A1",
        "message": "Không chạy được docker", "file": ZIP}))
    g = (await owner_api.get("/system/diagnostics")).json()
    assert g["state"] == "failed" and g["error_code"] == "GH-E9A1" and g["file_name"] is None
    staff = await login_as(client, db, "manager")
    assert (await staff.get("/system/diagnostics")).status_code == 403
    assert (await staff.send("POST", "/system/diagnostics")).status_code == 403
    assert (await staff.get("/system/diagnostics/download")).status_code == 403


async def test_stale_when_genh_never_picks_up(owner_api: Api, host: Path) -> None:
    """Yêu cầu nằm > 15 phút (watcher không chạy) ⇒ stale=True để Console thôi chờ, cho tạo lại + hiện lệnh tay."""
    _supported(host)
    f = host / "request" / "doctor.json"
    f.write_text(json.dumps({"schema": 1, "request_id": "0123456789abcdef", "requested_at": _iso(datetime.now(UTC))}))
    g = (await owner_api.get("/system/diagnostics")).json()
    assert g["state"] == "pending" and g["stale"] is False
    f.write_text(json.dumps({"schema": 1, "request_id": "0123456789abcdef",
                             "requested_at": _iso(datetime.now(UTC) - timedelta(minutes=16))}))
    g = (await owner_api.get("/system/diagnostics")).json()
    assert g["state"] == "pending" and g["stale"] is True
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/system/diagnostics")
    assert r.status_code == 202 and r.json()["stale"] is False
    _done(host)
    f.unlink()
    assert (await owner_api.get("/system/diagnostics")).json()["stale"] is False
