"""Sao lưu & khôi phục trên giao diện (v0.1.20): danh sách, "Sao lưu ngay" qua worker, tải về (Owner + PIN), yêu
cầu khôi phục qua hộp thư genh (Owner + PIN + gõ "KHÔI PHỤC"), lịch sao lưu; và chặn API khi phải đổi mật khẩu."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import orjson
import pytest
from sqlalchemy import text

from gh import backup
from gh.chassis import objects as objects_mod
from gh.config import get_settings
from gh.db import admin_sessionmaker
from tests.conftest import OWNER, Api
from tests.test_rbac_api import login_as

KEY_OLD = "backups/20260927T020000Z-0000aaaa.pgcustom.enc"
KEY_NEW = "backups/20260928T020000Z-1111bbbb.pgcustom.enc"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, redis):  # type: ignore[no-untyped-def]
    """ObjectStore tạm có sẵn 2 bản + hộp thư genh có watcher nhận cả update lẫn restore."""
    objects = tmp_path / "objects"
    (objects / "backups").mkdir(parents=True)
    entries = [
        backup.BackupEntry(key=KEY_OLD, taken_at=datetime(2026, 9, 27, 2, tzinfo=UTC), database="gh", size_bytes=11,
                           sha256="x", key_id="master"),  # bản cũ, chưa có trigger
        backup.BackupEntry(key=KEY_NEW, taken_at=datetime(2026, 9, 28, 2, tzinfo=UTC), database="gh", size_bytes=22,
                           sha256="y", key_id="backup", trigger="pre-update"),
    ]
    (objects / backup.MANIFEST_KEY).write_bytes(orjson.dumps([e.to_json() for e in entries]))
    (objects / KEY_NEW).write_bytes(b"ENCRYPTED-BYTES")
    (objects / KEY_OLD).write_bytes(b"OLD")
    link = tmp_path / "run"
    (link / "request").mkdir(parents=True)
    (link / "genh.json").write_text(json.dumps({"version": "v0.1.20", "updater": "systemd",
                                                "requests": ["update", "restore"]}))
    monkeypatch.setenv("GH_OBJECTS_DIR", str(objects))
    monkeypatch.setenv("GH_HOST_LINK_DIR", str(link))
    get_settings.cache_clear()
    objects_mod.reset_object_store()
    yield {"objects": objects, "link": link}
    get_settings.cache_clear()
    objects_mod.reset_object_store()


async def _pin(api: Api) -> None:
    assert (await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})).status_code == 200


async def _actions(action: str) -> list[str]:
    async with admin_sessionmaker()() as db:
        return list((await db.execute(text("SELECT target_id FROM ops.action_log WHERE action = :a ORDER BY at"),
                                      {"a": action})).scalars().all())


async def test_list_backups_newest_first_with_trigger(owner_api: Api, env) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.get("/system/backups")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [i["key"] for i in body["items"]] == [KEY_NEW, KEY_OLD]
    assert body["items"][0]["trigger"] == "pre-update" and body["items"][1]["trigger"] is None
    assert body["items"][0]["encrypted"] is True and body["items"][0]["size_bytes"] == 22
    assert body["schedule"] is None and body["timezone"] == "Asia/Ho_Chi_Minh"
    assert body["retention"] == {"daily": 7, "weekly": 4, "monthly": 12, "recent_hours": 24}
    assert body["job"] is None
    assert body["restore"]["can_request"] is True and body["restore"]["state"] == "idle"


async def test_members_without_system_manage_are_refused(client, db, owner_api: Api, env) -> None:  # type: ignore[no-untyped-def]
    op = await login_as(client, db, "operator")
    assert (await op.get("/system/backups")).status_code == 403
    assert (await op.send("POST", "/system/backups")).status_code == 403
    assert (await op.send("PUT", "/system/backups/schedule", {"frequency": "daily", "time_of_day": "01:00"})
            ).status_code == 403


async def test_download_and_restore_are_owner_only(client, db, owner_api: Api, env) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("""INSERT INTO core.role_permissions (role_id, permission_code, scope)
                             SELECT id, 'system.manage', 'all' FROM core.roles WHERE code = 'manager'
                             ON CONFLICT (role_id, permission_code) DO UPDATE SET scope = 'all'"""))
    await db.commit()
    mgr = await login_as(client, db, "manager")
    assert (await mgr.get("/system/backups")).status_code == 200
    r = await mgr.get("/system/backups/download", params={"key": KEY_NEW})
    assert r.status_code == 403
    r = await mgr.send("POST", "/system/backups/restore", {"key": KEY_NEW, "confirm": "KHÔI PHỤC"})
    assert r.status_code == 403
    assert not (env["link"] / "request" / "restore.json").exists()


async def test_download_needs_pin_and_returns_encrypted_bytes(owner_api: Api, env) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.get("/system/backups/download", params={"key": KEY_NEW})
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED"
    await _pin(owner_api)
    r = await owner_api.get("/system/backups/download", params={"key": KEY_NEW})
    assert r.status_code == 200 and r.content == b"ENCRYPTED-BYTES"
    assert r.headers["content-type"] == "application/octet-stream"
    assert "gen-harness-20260928T020000Z-1111bbbb.pgcustom.enc" in r.headers["content-disposition"]
    # Khoá lạ/không có trong danh mục → 404, không đọc tệp tuỳ ý.
    for key in ("backups/../secrets.json", "backups/20260101T000000Z-deadbeef.pgcustom.enc"):
        assert (await owner_api.get("/system/backups/download", params={"key": key})).status_code == 404
    assert await _actions("backup.downloaded") == [KEY_NEW]


async def test_backup_now_queues_worker_job_then_reports_done(owner_api: Api, env, redis,  # type: ignore[no-untyped-def]
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    r = await owner_api.send("POST", "/system/backups")
    assert r.status_code == 202, r.text
    assert r.json()["job"]["state"] == "queued"
    assert await redis.zcard("arq:queue") == 1
    r = await owner_api.send("POST", "/system/backups")
    assert r.status_code == 409 and r.json()["code"] == "BACKUP_IN_PROGRESS"
    assert await _actions("backup.requested") == ["backup"]

    seen: list[str] = []

    async def fake_run_backup(*, trigger: str = "manual", **_: object) -> backup.BackupEntry:
        seen.append(trigger)
        return backup.BackupEntry(key=KEY_NEW, taken_at=datetime.now(UTC), database="gh", size_bytes=1, sha256="z")

    monkeypatch.setattr(backup, "run_backup", fake_run_backup)
    assert await backup.backup_now({"redis": redis}) == {"ran": KEY_NEW}
    assert seen == ["manual"] and not await redis.exists(backup.LOCK_KEY)
    job = (await owner_api.get("/system/backups")).json()["job"]
    assert job["state"] == "done" and job["key"] == KEY_NEW

    # Lỗi pg_dump → failed kèm thông báo, khoá được nhả.
    async def boom(**_: object) -> backup.BackupEntry:
        raise RuntimeError("pg_dump thất bại (mã 1)")

    monkeypatch.setattr(backup, "run_backup", boom)
    await backup.backup_now({"redis": redis})
    job = (await owner_api.get("/system/backups")).json()["job"]
    # F106: câu thân thiện cố định cho Sếp; lỗi gốc nằm riêng ở `technical`.
    assert job["state"] == "failed" and job["message"] == backup.BACKUP_FAILED_MESSAGE
    assert "pg_dump" in job["technical"]
    assert not await redis.exists(backup.LOCK_KEY)


async def test_restore_request_needs_pin_confirmation_and_watcher(owner_api: Api, env) -> None:  # type: ignore[no-untyped-def]
    link: Path = env["link"]
    body = {"key": KEY_NEW, "confirm": "KHÔI PHỤC"}
    r = await owner_api.send("POST", "/system/backups/restore", body)
    assert r.status_code == 423
    await _pin(owner_api)
    r = await owner_api.send("POST", "/system/backups/restore", {**body, "confirm": "khoi phuc"})
    assert r.status_code == 422 and "confirm" in r.json()["errors"]
    r = await owner_api.send("POST", "/system/backups/restore", {**body, "key": "backups/khong-co.pgcustom.enc"})
    assert r.status_code == 404
    assert not (link / "request" / "restore.json").exists()

    # Watcher cũ (v0.1.19) chỉ nhận update → chưa khôi phục bằng nút được.
    (link / "genh.json").write_text(json.dumps({"version": "v0.1.19", "updater": "systemd"}))
    r = await owner_api.send("POST", "/system/backups/restore", body)
    assert r.status_code == 409 and r.json()["code"] == "RESTORE_UNAVAILABLE"
    (link / "genh.json").write_text(json.dumps({"version": "v0.1.20", "updater": "cron",
                                                "requests": ["update", "restore"]}))

    r = await owner_api.send("POST", "/system/backups/restore", body)
    assert r.status_code == 202, r.text
    assert r.json()["restore"]["state"] == "requested" and r.json()["restore"]["key"] == KEY_NEW
    req = json.loads((link / "request" / "restore.json").read_text())
    assert req["key"] == KEY_NEW and req["id"] and req["requested_at"]
    r = await owner_api.send("POST", "/system/backups/restore", body)
    assert r.status_code == 409 and r.json()["code"] == "RESTORE_IN_PROGRESS"
    # Đang khôi phục thì không cho bấm cập nhật phiên bản.
    r = await owner_api.send("POST", "/system/update")
    assert r.status_code == 409 and r.json()["code"] == "RESTORE_IN_PROGRESS"
    assert await _actions("backup.restore_requested") == [KEY_NEW]

    # genh nhận yêu cầu (xoá tệp) rồi báo tiến trình.
    (link / "request" / "restore.json").unlink()
    (link / "restore-status.json").write_text(json.dumps({"state": "running", "key": KEY_NEW,
                                                          "started_at": "2026-09-29T01:00:00Z"}))
    assert (await owner_api.get("/system/backups")).json()["restore"]["state"] == "running"
    (link / "restore-status.json").write_text(json.dumps({"state": "done", "key": KEY_NEW, "safety_key": KEY_OLD,
                                                          "finished_at": "2026-09-29T01:03:00Z"}))
    st = (await owner_api.get("/system/backups")).json()["restore"]
    assert st["state"] == "done" and st["safety_key"] == KEY_OLD


async def test_stale_restore_request_can_be_retried(owner_api: Api, env) -> None:  # type: ignore[no-untyped-def]
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    (env["link"] / "request" / "restore.json").write_text(json.dumps({"key": KEY_OLD, "requested_at": old}))
    assert (await owner_api.get("/system/backups")).json()["restore"]["state"] == "stalled"
    await _pin(owner_api)
    r = await owner_api.send("POST", "/system/backups/restore", {"key": KEY_NEW, "confirm": " KHÔI PHỤC "})
    assert r.status_code == 202, r.text


async def test_schedule_edit_keeps_setup_fields(owner_api: Api, env, db) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.send("PUT", "/setup/steps/11", {"frequency": "daily", "time_of_day": "02:00",
                                                        "retention_count": 7, "destination": "local"})
    assert r.status_code == 200, r.text
    assert (await owner_api.get("/system/backups")).json()["schedule"] == {"frequency": "daily",
                                                                           "time_of_day": "02:00"}
    r = await owner_api.send("PUT", "/system/backups/schedule", {"frequency": "weekly", "time_of_day": "25:00"})
    assert r.status_code == 422 and "time_of_day" in r.json()["errors"]
    r = await owner_api.send("PUT", "/system/backups/schedule", {"frequency": "weekly", "time_of_day": "03:30"})
    assert r.status_code == 200, r.text
    assert r.json()["schedule"] == {"frequency": "weekly", "time_of_day": "03:30"}
    cfg = (await db.execute(text("SELECT settings -> 'backup' FROM core.organizations"))).scalar_one()
    assert cfg == {"frequency": "weekly", "time_of_day": "03:30", "retention_count": 7, "destination": "local"}
    assert await _actions("backup.schedule_changed") == ["backup"]


class _MemStore:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> None:
        self.data[key] = data

    async def get(self, key: str) -> bytes:
        return self.data[key]

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


async def test_prune_keeps_every_backup_from_last_24_hours() -> None:
    """GFS chỉ giữ bản mới nhất mỗi ngày — nhưng bản trong 24 giờ qua luôn giữ, để "Sao lưu ngay"/bản an toàn trước
    khi khôi phục không xoá bản cùng ngày Owner vừa chọn."""
    now = datetime(2026, 9, 29, 12, tzinfo=UTC)
    store = _MemStore()
    entries = [backup.BackupEntry(key=f"backups/{i}", taken_at=now - timedelta(hours=h), database="gh", size_bytes=1,
                                  sha256="s") for i, h in enumerate((1, 3, 30, 31))]
    for e in entries:
        store.data[e.key] = b"x"
    removed = await backup.prune(store=store, entries=entries, now=now)  # type: ignore[arg-type]
    assert removed == ["backups/3"]  # hôm qua có 2 bản, cũ hơn 24 giờ → chỉ giữ bản mới nhất của ngày đó


def test_entry_trigger_roundtrip() -> None:
    e = backup.BackupEntry(key="k", taken_at=datetime(2026, 9, 29, tzinfo=UTC), database="d", size_bytes=1,
                           sha256="s", trigger="scheduled")
    assert backup.BackupEntry.from_json(e.to_json()).trigger == "scheduled"


async def test_must_change_password_blocks_everything_but_auth_and_account(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("UPDATE core.users SET must_change_password = true"))
    await db.commit()
    for path in ("/navigation", "/system/backups", "/channels"):
        r = await owner_api.get(path)
        assert r.status_code == 403 and r.json()["code"] == "PASSWORD_CHANGE_REQUIRED", path
    r = await owner_api.send("PATCH", "/account", {"display_name": "Đổi tên"})
    assert r.status_code == 403 and r.json()["code"] == "PASSWORD_CHANGE_REQUIRED"
    assert (await owner_api.get("/auth/me")).status_code == 200
    assert (await owner_api.get("/account")).status_code == 200
    r = await owner_api.send("POST", "/account/password", {"current_password": OWNER["password"],
                                                           "new_password": "mat-khau-moi-rat-dai-456"})
    assert r.status_code == 200, r.text
    assert (await owner_api.get("/navigation")).status_code == 200
