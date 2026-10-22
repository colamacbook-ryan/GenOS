"""Nối kênh quản trị vào tiến trình: consumer luồng `gh.owner.inbound` (chạy trong api) và cron gửi hàng đợi báo cáo."""

import asyncio
import logging
import socket
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from redis.asyncio import Redis

from gh.chassis.bus import OWNER_INBOUND, EventBus
from gh.owner_channel import dm, group_reply, pairing

log = logging.getLogger("gh.owner_channel.jobs")

GROUP = "owner"

Handler = Callable[..., Awaitable[str]]     # (sm, redis, bus, router, org_id, payload[, app])


async def _dm(sm: Any, redis: Redis, bus: EventBus, router: Any, org_id: uuid.UUID, p: dict[str, Any],
              app: Any = None) -> str:
    return await dm.handle_dm(sm, redis, bus, router, org_id, p, app)


async def _pair(sm: Any, redis: Redis, bus: EventBus, router: Any, org_id: uuid.UUID, p: dict[str, Any]) -> str:
    return await pairing.handle_pair_event(sm, redis, bus, org_id, p)


#: loại sự kiện → hàm xử lý.
HANDLERS: dict[str, Handler] = {"dm": _dm, "pair": _pair}
group_reply.register(HANDLERS)   # + call, support_ask


async def dispatch(sm: Any, redis: Redis, bus: EventBus, router: Any, org_id: uuid.UUID, event: Any,
                   app: Any = None) -> str:
    """Xử lý một sự kiện `gh.owner.inbound`, rồi XOÁ bản ghi gốc (mang chữ của Sếp / mã ghép). Không ném."""
    fn = HANDLERS.get(event.type)
    try:
        if fn is None:
            out = "ignored:unknown_type"
        elif event.type == "dm":   # v0.1.73: lệnh «OK» cần `app` (gọi nội bộ đúng endpoint Console)
            out = await fn(sm, redis, bus, router, org_id, event.payload, app)
        else:
            out = await fn(sm, redis, bus, router, org_id, event.payload)
    except Exception:  # noqa: BLE001
        log.exception("gh.owner.inbound: xử lý %s lỗi", event.type)
        out = "error"
    if getattr(event, "message_id", None):
        await redis.xdel(event.stream, event.message_id)
    return out


def start(app: Any, stop: asyncio.Event, sm: Any) -> list[asyncio.Task[None]]:
    """Một consumer cho `gh.owner.inbound` trong api. Router model lấy lúc chạy từ `app.state.model_router`."""
    async def handler(event: Any) -> None:
        await dispatch(sm, app.state.redis, app.state.bus, getattr(app.state, "model_router", None),
                       app.state.org_id, event, app)

    consumer = f"api-{socket.gethostname()}"
    return [asyncio.create_task(app.state.bus.run(OWNER_INBOUND, GROUP, consumer, handler, stop),
                                name="owner-inbound")]
