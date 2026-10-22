"""v0.1.36 — kiểm tích hợp hai gói: worker ghi dấu cron/nhịp tim (gh/worker.py `_tracked`, gói lich-phien-ban-log) và
API đọc lại đúng hợp đồng Redis đó cho `GET /system/health` (gh/health.py, gói api-tu-bao-sao-luu)."""

from typing import Any

import pytest

from gh import health, worker
from tests.conftest import Api


def test_redis_contract_keys_match() -> None:
    assert worker.HEARTBEAT_KEY == health.HEARTBEAT_KEY
    assert worker.CRON_LAST_KEY.format("x") == f"{health.CRON_LAST_PREFIX}x"
    assert worker.CRON_NAMES_KEY == health.CRON_NAMES_KEY


async def test_system_health_does_not_scan_keyspace_each_call(owner_api: Api, redis: Any,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Dấu/hàng lỗi có từ trước (chưa đăng ký tập) được quét bù MỘT lần; các lượt sau chỉ đọc tập — không SCAN."""
    from gh.chassis.bus import DLQ_STREAMS_KEY

    await redis.set(f"{health.CRON_LAST_PREFIX}old_job", b'{"at": "2026-10-02T01:00:00Z", "ok": true}')
    await redis.xadd("gh.old.dlq", {"error": "x"})
    body = (await owner_api.get("/system/health")).json()
    assert [c["name"] for c in body["crons"]] == ["old_job"]
    assert {"stream": "gh.old", "dlq": 1} in body["queues"]

    scans: list[str] = []
    real_scan = health._discover

    async def spy(r: Any) -> None:
        before = await r.exists(health.DISCOVERED_KEY)
        await real_scan(r)
        if not before:
            scans.append("scan")

    monkeypatch.setattr(health, "_discover", spy)

    async def tracked_job(ctx: dict[str, Any]) -> None:
        return None

    await worker._tracked(tracked_job)({"redis": redis})
    await redis.xadd("gh.new.dlq", {"error": "y"})
    await redis.sadd(DLQ_STREAMS_KEY, "gh.new.dlq")  # như EventBus khi chuyển tin vào DLQ
    await redis.delete("gh.old.dlq")
    for _ in range(3):
        body = (await owner_api.get("/system/health")).json()
    assert scans == []
    assert [c["name"] for c in body["crons"]] == ["old_job", "tracked_job"]
    assert body["queues"] == [{"stream": "gh.new", "dlq": 1}]


async def test_tracked_cron_shows_up_in_system_health(owner_api: Api, redis: Any) -> None:
    async def scheduled_backup_scan(ctx: dict[str, Any]) -> None:
        return None

    async def broken_job(ctx: dict[str, Any]) -> None:
        raise RuntimeError("hỏng cố ý")

    await worker._tracked(scheduled_backup_scan)({"redis": redis})
    with pytest.raises(RuntimeError):
        await worker._tracked(broken_job)({"redis": redis})

    r = await owner_api.get("/system/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["worker"]["state"] == "ok" and isinstance(body["worker"]["last_seen_at"], str)
    crons = {c["name"]: c for c in body["crons"]}
    assert crons["scheduled_backup_scan"]["ok"] is True and isinstance(crons["scheduled_backup_scan"]["last_at"], str)
    assert crons["broken_job"]["ok"] is False
    assert body["overall"] in ("warn", "bad")  # cron lỗi ⇒ ít nhất 'warn'
