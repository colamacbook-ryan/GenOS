"""F106: lỗi sao lưu nói tiếng người — câu thân thiện cố định cho Sếp (job + chuông), stderr của pg_dump tách riêng
sang `technical` (hiện trong "Chi tiết kỹ thuật", đã che mật khẩu), không bao giờ vào chuông."""

from typing import Any

import orjson
import pytest
from sqlalchemy import text

from gh import backup
from tests.conftest import Api
from tests.phase2 import org_id

STDERR = ("pg_dump thất bại (mã 1): pg_dump: error: connection to server at \"127.0.0.1\", port 5432 failed: "
          "FATAL:  password authentication failed for user \"gh\" postgresql://gh:Mat-Khau-Bi-Mat@db/gh")


async def _boom(**_: Any) -> Any:
    raise RuntimeError(STDERR)


async def test_backup_now_failure_job_is_friendly_and_technical_is_separate(
        owner_api: Api, db, redis, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(backup, "run_backup", _boom)
    out = await backup.backup_now({"redis": redis})
    job = orjson.loads(await redis.get(backup.JOB_KEY))
    assert job["state"] == "failed"
    assert job["message"] == backup.BACKUP_FAILED_MESSAGE
    for word in ("pg_dump", "FATAL", "mã 1", "worker"):
        assert word not in job["message"]
    # stderr nằm riêng ở `technical`, đã che mật khẩu trong URL.
    assert "password authentication failed" in job["technical"]
    assert "Mat-Khau-Bi-Mat" not in job["technical"]
    assert "Mat-Khau-Bi-Mat" not in str(out)
    assert "pg_dump" not in str(out)
    # Chuông chỉ mang câu thân thiện.
    await db.commit()
    [row] = (await db.execute(text("SELECT body FROM core.notifications WHERE kind = 'backup.failed'"))).all()
    assert row.body == backup.BACKUP_FAILED_MESSAGE
    # API trả đủ cho web: message thân thiện + technical.
    page = (await owner_api.get("/system/backups")).json()["job"]
    assert page["message"] == backup.BACKUP_FAILED_MESSAGE and "password authentication" in page["technical"]
    assert not await redis.exists(backup.LOCK_KEY)


async def test_next_run_clears_old_technical(owner_api: Api, redis,  # type: ignore[no-untyped-def]
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backup, "run_backup", _boom)
    await backup.backup_now({"redis": redis})
    assert orjson.loads(await redis.get(backup.JOB_KEY)).get("technical")

    async def locked(_: Any) -> bool:
        return False

    monkeypatch.setattr(backup, "_acquire_lock", locked)
    await backup.backup_now({"redis": redis})  # nhánh "đang có bản khác chạy" — không được giữ stderr cũ
    job = orjson.loads(await redis.get(backup.JOB_KEY))
    assert job["state"] == "failed" and not job.get("technical")


async def test_scheduled_failure_rings_friendly_message(app, db, redis,  # type: ignore[no-untyped-def]
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    org = await org_id(db)
    await db.execute(text("""UPDATE core.organizations SET settings = settings ||
                             '{"backup": {"frequency": "daily", "time_of_day": "02:00"}}'::jsonb WHERE id = :o"""),
                     {"o": org})
    await db.commit()
    calls: list[dict[str, Any]] = []

    async def no_backups(**_: Any) -> list[Any]:
        return []

    async def record(redis_: Any, *, ok: bool, message: str) -> None:
        calls.append({"ok": ok, "message": message})

    monkeypatch.setattr(backup, "list_backups", no_backups)
    monkeypatch.setattr(backup, "is_due", lambda *a, **k: True)
    monkeypatch.setattr(backup, "run_backup", _boom)
    monkeypatch.setattr(backup, "_notify_owners", record)
    with pytest.raises(RuntimeError):
        await backup.scheduled_backup_scan({"redis": redis})
    assert calls == [{"ok": False, "message": backup.SCHEDULED_FAILED_MESSAGE}]
    assert "pg_dump" not in calls[0]["message"] and "Sao lưu ngay" in calls[0]["message"]
    assert not await redis.exists(backup.LOCK_KEY)
