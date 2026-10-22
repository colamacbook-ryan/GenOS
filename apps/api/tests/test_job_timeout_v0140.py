"""v0.1.40 (F-16): việc nền quá giờ 2 lần liền ⇒ sự cố `job.timeout:<tên>` (ops.health_alerts) + MỘT chuông cho Owner;
lần chạy thành công sau đó đóng sự cố và xoá bộ đếm. Bị huỷ sớm (tắt worker) không tính là quá giờ; Redis/DB hỏng
không đổi ngoại lệ gốc của job."""

import asyncio
import logging
from typing import Any

import orjson
import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from gh import health, worker


async def slow_job(ctx: dict[str, Any]) -> str:
    await asyncio.sleep(ctx.get("sleep", 1.0))
    return "xong"


async def _alerts(db: Any) -> list[Any]:
    await db.commit()
    return list((await db.execute(text("""SELECT key, kind, severity, title, link, cleared_at FROM ops.health_alerts
                                          WHERE kind = 'job.timeout'"""))).all())


async def _bells(db: Any) -> list[Any]:
    await db.commit()
    return list((await db.execute(text("""SELECT n.title, n.user_id FROM core.notifications n
                                          WHERE n.kind = 'job.timeout'"""))).all())


async def _time_out(wrapped: Any, ctx: dict[str, Any]) -> None:
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(wrapped(ctx), 0.05)


async def test_two_timeouts_raise_one_bell_then_success_clears(owner_api, db, redis: Redis) -> None:  # type: ignore[no-untyped-def]
    wrapped = worker._tracked(slow_job, timeout_s=0.05)
    ctx = {"redis": redis}

    await _time_out(wrapped, ctx)
    last = orjson.loads(await redis.get("gh:cron:last:slow_job"))
    assert last["timeout"] is True and last["ok"] is False and isinstance(last["ms"], int) and last["at"]
    assert int(await redis.get("gh:cron:timeouts:slow_job")) == 1
    assert 0 < await redis.ttl("gh:cron:timeouts:slow_job") <= 7 * 86400
    assert await _alerts(db) == [] and await _bells(db) == []         # lần 1: chưa chuông

    await _time_out(wrapped, ctx)
    alerts = await _alerts(db)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.key == "job.timeout:slow_job" and a.severity == "warn" and a.cleared_at is None
    assert a.link == health.HEALTH_LINK == "/system?tab=storage&focus=health" and "quá giờ 2 lần liền" in a.title
    bells = await _bells(db)
    owners = (await db.execute(text("""SELECT count(*) FROM core.users u JOIN core.user_roles ur ON ur.user_id = u.id
                                        JOIN core.roles r ON r.id = ur.role_id AND r.code = 'owner'"""))).scalar_one()
    assert owners == 1 and len(bells) == 1

    await _time_out(wrapped, ctx)                                       # lần 3: vẫn một chuông (khử trùng lặp)
    assert len(await _bells(db)) == 1

    assert await wrapped({"redis": redis, "sleep": 0}) == "xong"        # chạy OK ⇒ đóng sự cố, xoá bộ đếm
    alerts = await _alerts(db)
    assert len(alerts) == 1 and alerts[0].cleared_at is not None
    assert await redis.get("gh:cron:timeouts:slow_job") is None
    assert orjson.loads(await redis.get("gh:cron:last:slow_job"))["timeout"] is False


async def test_job_labels_used_in_title(owner_api, db, redis: Redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setitem(worker.JOB_LABELS, "slow_job", "Việc thử chậm")
    wrapped = worker._tracked(slow_job, timeout_s=0.05)
    for _ in range(2):
        await _time_out(wrapped, {"redis": redis})
    [a] = await _alerts(db)
    assert a.title == "Việc nền 'Việc thử chậm' chạy quá giờ 2 lần liền"


async def test_shutdown_cancel_is_not_a_timeout(owner_api, db, redis: Redis) -> None:  # type: ignore[no-untyped-def]
    wrapped = worker._tracked(slow_job, timeout_s=10)
    for _ in range(3):
        task = asyncio.create_task(wrapped({"redis": redis}))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert orjson.loads(await redis.get("gh:cron:last:slow_job"))["timeout"] is False
    assert await redis.get("gh:cron:timeouts:slow_job") is None
    assert await _alerts(db) == []


async def test_failed_run_keeps_open_alert_until_success(owner_api, db, redis: Redis) -> None:  # type: ignore[no-untyped-def]
    async def flaky(ctx: dict[str, Any]) -> str:
        if ctx.get("fail"):
            raise RuntimeError("hỏng thử")
        await asyncio.sleep(ctx.get("sleep", 1.0))
        return "xong"

    wrapped = worker._tracked(flaky, timeout_s=0.05)
    for _ in range(2):
        await _time_out(wrapped, {"redis": redis})
    with pytest.raises(RuntimeError):
        await wrapped({"redis": redis, "fail": True})
    assert [a.cleared_at for a in await _alerts(db)] == [None]         # lỗi thường không đóng sự cố quá giờ
    assert await wrapped({"redis": redis, "sleep": 0}) == "xong"
    assert all(a.cleared_at is not None for a in await _alerts(db))


class _BrokenRedis:
    async def set(self, *a: Any, **kw: Any) -> None:
        raise ConnectionError("redis rớt")

    async def incr(self, *a: Any, **kw: Any) -> int:
        raise ConnectionError("redis rớt")

    async def get(self, *a: Any, **kw: Any) -> None:
        raise ConnectionError("redis rớt")


async def test_broken_redis_or_db_keeps_original_exception(owner_api, db, redis: Redis, monkeypatch,  # type: ignore[no-untyped-def]
                                                          caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="gh.worker")
    wrapped = worker._tracked(slow_job, timeout_s=0.05)
    await _time_out(wrapped, {"redis": _BrokenRedis()})
    assert any("Không ghi được bộ đếm quá giờ" in r.getMessage() for r in caplog.records)

    def broken_sm() -> Any:
        raise OSError("CSDL rớt")

    monkeypatch.setattr(worker, "sessionmaker", broken_sm)
    caplog.clear()
    for _ in range(2):
        await _time_out(wrapped, {"redis": redis})                      # vẫn TimeoutError, không phải OSError
    assert any("Không ghi được bộ đếm quá giờ" in r.getMessage() for r in caplog.records)
    # bị huỷ (CancelledError) vẫn ném lại nguyên vẹn dù DB hỏng
    task = asyncio.create_task(worker._tracked(slow_job, timeout_s=0.01)({"redis": redis}))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_cron_passes_effective_timeout() -> None:
    assert worker.WorkerSettings.job_timeout == worker.JOB_TIMEOUT == 300
    cj = next(c for c in worker.WorkerSettings.cron_jobs if c.name == "cron:retention_sweep")
    assert cj.timeout_s == 1800
    assert worker.JOB_LABELS["detect_identities"] == "Dò trùng danh tính"
    assert worker.JOB_LABELS["graph_recompute"] == "Dựng bản đồ quan hệ"
    assert worker.JOB_LABELS["retention_sweep"] == "Dọn dữ liệu quá hạn"
    assert worker.JOB_LABELS["partition_maintenance"] == "Bảo trì phân vùng"
