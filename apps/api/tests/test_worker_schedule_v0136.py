"""v0.1.36 (F-45, F-6): lịch Bộ xử lý nền theo GIỜ VN + dấu cron cho GET /system/health.

- Cron của arq hiểu theo `WorkerSettings.timezone` = Asia/Ho_Chi_Minh (không phụ thuộc múi giờ máy/ảnh).
- Job theo ngày (nặng) không rơi vào giờ làm việc 08:00–18:00 và cửa sổ cập nhật genh 02:30–03:30.
- Mọi cron được bọc `_tracked`: ghi `gh:cron:last:<tên hàm>` + `gh:worker:heartbeat` (hợp đồng Redis với API), ném
  lại nguyên ngoại lệ của job; tên CronJob và `timeout` không đổi.
- v0.1.40 (F-2): `retention_sweep` 05:00 thay purge_gen_conversations (05:00) + purge_notifications (05:10); hai
  hàm cũ vẫn ở `functions`. Payload `_tracked` thêm "timeout": bool (F-16).
"""

import asyncio
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import orjson
import pytest
from arq.cron import CronJob
from redis.asyncio import Redis

from gh import worker
from gh.backup import JOBS as BACKUP_JOBS
from gh.backup import scheduled_backup_scan
from gh.worker import WorkerSettings

# nhẹ, chỉ nhắc — được chạy trong giờ làm việc. v0.1.41 (F-8b): Bản tin Gen 07:30/17:30 (+ lượt bù) chủ ý trong giờ.
# v0.1.47 (F-83): kiểm phiên mạng xã hội 09:10 — chỉ xếp việc `health` cho trình duyệt nền (không nặng cho api/CSDL),
# chủ ý trong giờ để chuông "phiên hết hạn" tới lúc Sếp đang dùng máy (ngoài giờ yên lặng của mạng xã hội).
# v0.1.54 (g1-api): chuông Gen hướng dẫn 09:05/11:05/14:05 — job nhẹ (chỉ đọc tín hiệu + ghi chuông), chủ ý trong giờ
# làm việc để Sếp thấy chuông lúc đang dùng máy (ngoài giờ yên lặng của từng Owner).
LIGHT_ALLOWLIST = {"hub_token_expiry_scan", "gen_briefing", "social_session_check", "gen_coach"}
DAILY = {"partition_maintenance", "verify_action_log", "compact_notebooks", "retention_sweep",
         "people_review_recompute"}


def _jobs() -> dict[str, CronJob]:
    return {cj.name.removeprefix("cron:"): cj for cj in WorkerSettings.cron_jobs}


def _as_set(v: Any, full: range) -> set[int]:
    if v is None:
        return set(full)
    if isinstance(v, int):
        return {v}
    return set(v)


def test_timezone_is_vietnam() -> None:
    assert ZoneInfo("Asia/Ho_Chi_Minh").utcoffset(datetime(2026, 1, 1)) is not None
    tz = WorkerSettings.timezone
    assert str(tz) == "Asia/Ho_Chi_Minh"
    assert tz.key == "Asia/Ho_Chi_Minh"
    assert worker.WORKER_TZ is tz


def test_fixed_hour_jobs_avoid_work_hours_and_update_window() -> None:
    checked = 0
    for name, cj in _jobs().items():
        if cj.hour is None or name in LIGHT_ALLOWLIST:
            continue
        for h in _as_set(cj.hour, range(24)):
            for m in _as_set(cj.minute, range(60)):
                t = h * 60 + m
                assert not (2 * 60 + 30 <= t <= 3 * 60 + 30), f"{name} chạy {h:02d}:{m:02d} — trong cửa sổ cập nhật"
                assert not (8 * 60 <= t < 18 * 60), f"{name} chạy {h:02d}:{m:02d} — trong giờ làm việc VN"
                checked += 1
    assert checked >= len(DAILY)


def test_daily_jobs_have_fixed_hour() -> None:
    jobs = _jobs()
    for name in DAILY:
        assert name in jobs, name
        assert jobs[name].hour is not None, f"{name} phải là job theo ngày (hour cố định)"
    assert _as_set(jobs["partition_maintenance"].hour, range(24)) == {4, 23}
    assert (jobs["people_review_recompute"].hour, jobs["people_review_recompute"].minute) == ({4}, {40})
    assert (jobs["retention_sweep"].hour, jobs["retention_sweep"].minute) == ({5}, {0})
    assert jobs["retention_sweep"].timeout_s == 1800
    # nhẹ: giữ đúng 08:50 giờ VN như trước
    assert (jobs["hub_token_expiry_scan"].hour, jobs["hub_token_expiry_scan"].minute) == ({8}, {50})


def test_backup_cron_keeps_kwargs_including_timeout() -> None:
    cj = next(c for c in WorkerSettings.cron_jobs if c.name == "cron:scheduled_backup_scan")
    kw = dict(BACKUP_JOBS)[scheduled_backup_scan]  # type: ignore[index]
    assert cj.minute == kw["minute"]
    assert cj.timeout_s == (float(kw["timeout"]) if "timeout" in kw else None)
    # _cron truyền NGUYÊN khoá kw — timeout 3600 (sao lưu) còn nguyên sau khi bọc.
    wrapped = worker._cron(scheduled_backup_scan, minute={0}, timeout=3600)
    assert wrapped.name == "cron:scheduled_backup_scan"
    assert wrapped.timeout_s == 3600


def test_cron_names_unchanged() -> None:
    names = {c.name for c in WorkerSettings.cron_jobs}
    for n in ("verify_action_log", "partition_maintenance", "detect_identities", "compact_notebooks",
              "expire_sessions", "retention_sweep", "hub_token_expiry_scan",
              "social_schedule", "people_review_recompute", "scheduled_backup_scan"):
        assert f"cron:{n}" in names, n
    # v0.1.40: hai purge cũ không còn cron riêng (gộp vào retention_sweep) nhưng vẫn nhận job enqueue cũ
    for n in ("purge_gen_conversations", "purge_notifications"):
        assert f"cron:{n}" not in names, n
        assert getattr(worker, n) in WorkerSettings.functions
    # danh sách functions (job enqueue) vẫn là hàm gốc, không phải bản bọc
    assert worker.verify_action_log in WorkerSettings.functions
    assert worker.retention_sweep in WorkerSettings.functions
    assert WorkerSettings.job_timeout == worker.JOB_TIMEOUT == 300


async def _job_ok(ctx: dict[str, Any]) -> int:
    return 7


async def _job_fail(ctx: dict[str, Any]) -> int:
    raise RuntimeError("hỏng thử")


async def _job_cancelled(ctx: dict[str, Any]) -> int:
    raise asyncio.CancelledError


async def test_tracked_writes_last_run_and_heartbeat(redis: Redis) -> None:
    wrapped = worker._tracked(_job_ok)
    assert wrapped.__name__ == "_job_ok"
    assert await wrapped({"redis": redis}) == 7
    raw = await redis.get("gh:cron:last:_job_ok")
    assert raw is not None
    data = orjson.loads(raw)
    assert data["ok"] is True and isinstance(data["ms"], int) and data["timeout"] is False
    assert data["at"].endswith("Z") and datetime.fromisoformat(data["at"].replace("Z", "+00:00"))
    assert 0 < await redis.ttl("gh:cron:last:_job_ok") <= 7 * 86400
    hb = await redis.get("gh:worker:heartbeat")
    assert hb is not None and hb.decode().endswith("Z")
    assert 0 < await redis.ttl("gh:worker:heartbeat") <= 86400


async def test_tracked_reraises_and_records_failure(redis: Redis) -> None:
    with pytest.raises(RuntimeError, match="hỏng thử"):
        await worker._tracked(_job_fail)({"redis": redis})
    assert orjson.loads(await redis.get("gh:cron:last:_job_fail"))["ok"] is False
    with pytest.raises(asyncio.CancelledError):
        await worker._tracked(_job_cancelled)({"redis": redis})
    assert orjson.loads(await redis.get("gh:cron:last:_job_cancelled"))["ok"] is False


class _BrokenRedis:
    async def set(self, *a: Any, **kw: Any) -> None:
        raise ConnectionError("redis rớt")


async def test_tracked_redis_error_only_warns(caplog: pytest.LogCaptureFixture) -> None:
    assert await worker._tracked(_job_ok)({"redis": _BrokenRedis()}) == 7
    assert any("Không ghi được dấu cron" in r.getMessage() for r in caplog.records)
    with pytest.raises(RuntimeError):
        await worker._tracked(_job_fail)({"redis": _BrokenRedis()})


async def test_wrapped_cron_coroutines_are_tracked(redis: Redis, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hàm cron thật trong WorkerSettings đã được bọc (ghi dấu khi chạy)."""

    async def fake_social(db: Any, r: Any) -> int:
        return 0

    monkeypatch.setattr(worker.social, "schedule_tick", fake_social)
    cj = next(c for c in WorkerSettings.cron_jobs if c.name == "cron:social_schedule")

    class _Db:
        async def __aenter__(self) -> "_Db":
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def commit(self) -> None:
            return None

    monkeypatch.setattr(worker, "sessionmaker", lambda: _Db)
    await cj.coroutine({"redis": redis, "redis_bus": redis})
    assert orjson.loads(await redis.get("gh:cron:last:social_schedule"))["ok"] is True
