"""Hook sau sàng lọc và việc định kỳ của các cụm màn giai đoạn 3.

Mỗi hook tiêu thụ `gh.clean.ready` bằng **consumer group riêng** (`hook:<tên>`): một hook lỗi chỉ tự thử lại / vào
DLQ của chính nó, không chặn hook khác (cách ly lỗi như plugin — ARCHITECTURE §6.4).
"""

import asyncio
import logging
import socket
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gh.chassis.bus import CLEAN_READY, Event, EventBus

log = logging.getLogger("gh.biz.hooks")


@dataclass
class HookCtx:
    org_id: uuid.UUID
    unit_ids: list[uuid.UUID]
    run_id: uuid.UUID | None
    sm: async_sessionmaker[AsyncSession]
    redis: Redis
    bus: EventBus
    router: Any                     # gh.providers.router.ModelRouter (Any: tránh import vòng)


@dataclass(frozen=True)
class Hook:
    name: str                       # duy nhất toàn hệ thống, dùng làm tên consumer group
    fn: Callable[[HookCtx], Awaitable[None]]
    timeout_s: float = 120.0


CronJob = tuple[Callable[[dict[str, Any]], Awaitable[Any]], dict[str, Any]]


def context_from(event: Event, *, sm: async_sessionmaker[AsyncSession], redis: Redis, bus: EventBus,
                 router: Any) -> HookCtx:
    return HookCtx(org_id=uuid.UUID(event.org_id) if event.org_id else uuid.UUID(int=0),
                   unit_ids=[uuid.UUID(i) for i in event.payload.get("ids", [])],
                   run_id=uuid.UUID(event.payload["run_id"]) if event.payload.get("run_id") else None,
                   sm=sm, redis=redis, bus=bus, router=router)


def start_hooks(hooks: list[Hook], *, sm: async_sessionmaker[AsyncSession], redis: Redis, bus: EventBus,
                router: Any, stop: asyncio.Event) -> list[asyncio.Task[None]]:
    consumer = f"worker-{socket.gethostname()}"
    tasks = []
    for h in hooks:
        async def handler(event: Event, h: Hook = h) -> None:
            await asyncio.wait_for(h.fn(context_from(event, sm=sm, redis=redis, bus=bus, router=router)),
                                   timeout=h.timeout_s)
        tasks.append(asyncio.create_task(bus.run(CLEAN_READY, f"hook:{h.name}", consumer, handler, stop),
                                         name=f"hook:{h.name}"))
    return tasks
