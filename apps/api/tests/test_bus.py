"""Event bus trên Redis Streams: không mất sự kiện khi consumer chết, DLQ, breaker tạm dừng tiêu thụ."""

import asyncio
from pathlib import Path
from typing import Any

from gh.chassis.bus import Event, EventBus
from gh.chassis.plugins import Manifest, PluginManager
from tests import plugin_fixtures as fx

STREAM = "gh.test.stream"


async def test_publish_and_consume(redis: Any) -> None:
    bus = EventBus(redis)
    await bus.publish(STREAM, "t.one", {"n": 1}, actor="test", org_id="org-1")
    got: list[Event] = []

    async def handler(e: Event) -> None:
        got.append(e)

    assert await bus.process_once(STREAM, "g", "c1", handler, block_ms=100) == 1
    assert got[0].payload == {"n": 1} and got[0].org_id == "org-1" and got[0].type == "t.one"
    assert got[0].correlation_id == got[0].event_id
    assert await bus.process_once(STREAM, "g", "c1", handler, block_ms=100) == 0


async def test_crashed_consumer_message_is_reclaimed_not_lost(redis: Any) -> None:
    bus = EventBus(redis)
    await bus.publish(STREAM, "t", {"n": 1}, actor="test")
    await bus.ensure_group(STREAM, "g")
    # consumer A đọc rồi "chết" trước khi ack
    await redis.xreadgroup("g", "A", {STREAM: ">"}, count=10)
    assert (await redis.xpending(STREAM, "g"))["pending"] == 1
    got: list[int] = []

    async def handler(e: Event) -> None:
        got.append(e.payload["n"])

    await asyncio.sleep(0.05)
    await bus.process_once(STREAM, "g", "B", handler, block_ms=50, reclaim_idle_ms=10)
    assert got == [1]
    assert (await redis.xpending(STREAM, "g"))["pending"] == 0


async def test_poison_message_goes_to_dlq_after_max_deliveries(redis: Any) -> None:
    bus = EventBus(redis)
    await bus.publish(STREAM, "t", {"n": 1}, actor="test")

    async def boom(e: Event) -> None:
        raise ValueError("hỏng")

    for _ in range(3):
        await bus.process_once(STREAM, "g", "c", boom, block_ms=50, reclaim_idle_ms=0, max_deliveries=3)
        await asyncio.sleep(0.01)
    assert (await redis.xpending(STREAM, "g"))["pending"] == 0
    dlq = await redis.xrange(f"{STREAM}.dlq")
    assert len(dlq) == 1
    assert dlq[0][1][b"error"] == "hỏng".encode()
    assert dlq[0][1][b"group"] == b"g"
    # GET /system/health đọc tập này thay vì SCAN cả keyspace.
    from gh.chassis.bus import DLQ_STREAMS_KEY

    assert await redis.smembers(DLQ_STREAMS_KEY) == {f"{STREAM}.dlq".encode()}


async def test_deferred_is_not_counted_as_failure(redis: Any) -> None:
    from gh.chassis.bus import Deferred

    bus = EventBus(redis)
    await bus.publish(STREAM, "t", {"n": 1}, actor="test")

    async def later(e: Event) -> None:
        raise Deferred()

    for _ in range(10):
        await bus.process_once(STREAM, "g", "c", later, block_ms=10, own_pending=True, max_deliveries=2)
    assert await redis.exists(f"{STREAM}.dlq") == 0
    got: list[int] = []

    async def ok(e: Event) -> None:
        got.append(e.payload["n"])

    await bus.process_once(STREAM, "g", "c", ok, block_ms=10, own_pending=True)
    assert got == [1]


async def test_open_breaker_pauses_consumption_and_resumes_without_loss(redis: Any, tmp_path: Path) -> None:
    fx.SEEN.clear()
    heal = tmp_path / "healed"
    bus = EventBus(redis)
    pm = PluginManager(bus, instance_id="t1")
    m = Manifest.model_validate({
        "package": "@x/flaky", "name": "flaky", "version": "1.0.0", "layer": "extension",
        "entry": "tests.plugin_fixtures:FlakyUntilTold", "events": {"subscribe": [STREAM]},
        "breaker": {"failure_threshold": 2, "window_s": 60, "cooldown_s": 0.5}})
    pm.register(m, origin="marketplace", settings={"heal_file": str(heal)})
    for i in range(5):
        await bus.publish(STREAM, "t", {"n": i}, actor="test")
    await pm.load_all()
    try:
        for _ in range(50):
            if pm.get("@x/flaky").breaker.state == "open":
                break
            await asyncio.sleep(0.05)
        assert pm.get("@x/flaky").health == "isolated"
        heal.touch()
        # Sau thời gian nghỉ: lượt thử nửa mở thành công → mạch đóng → tin còn treo được xử lý, không mất tin nào.
        for _ in range(100):
            if sorted(set(fx.SEEN)) == [0, 1, 2, 3, 4]:
                break
            await asyncio.sleep(0.05)
        assert sorted(set(fx.SEEN)) == [0, 1, 2, 3, 4]
        assert await redis.exists(f"{STREAM}.dlq") == 0
        assert (await redis.xpending(STREAM, "@x/flaky"))["pending"] == 0
        assert pm.get("@x/flaky").health == "healthy"
    finally:
        await pm.shutdown()
