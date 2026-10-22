"""Bơm N sự kiện vào stream riêng `e2e.plugin.explode` (giai đoạn 5.3 luồng 7) mà plugin `@e2e/exploder`
(seed bởi `run.sh` — xem `INSERT INTO ops.plugins`, entry `tests.plugin_fixtures:Exploder`) đang lắng nghe thật
qua `PluginManager`. `Exploder.handle` luôn `raise` → mỗi sự kiện là một lỗi thật đi qua circuit breaker thật
(`gh.chassis.breaker`), không đụng tới stream nào của luồng nghiệp vụ chính — cô lập hoàn toàn, đúng tinh thần
"lỗi một plugin không lan sang plugin khác"."""
import asyncio
import os
import sys

from redis.asyncio import Redis

sys.path.insert(0, os.environ.get("GH_API_DIR", os.path.join(os.path.dirname(__file__), "../../api")))
from gh.chassis.bus import EventBus

STREAM = "e2e.plugin.explode"


async def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    r = Redis.from_url(os.environ["GH_REDIS_URL"])
    bus = EventBus(r, 10000)
    for i in range(n):
        await bus.publish(STREAM, "boom", {"i": i}, actor="e2e:explode", org_id=os.environ["ORG"])
    await r.aclose()


asyncio.run(main())
