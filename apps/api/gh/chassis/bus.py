"""Event bus trên Redis Streams (ARCHITECTURE §6.5).

- publish: XADD kèm envelope chuẩn (event_id UUIDv7, org_id, correlation_id, actor, occurred_at, schema_version).
- consume: consumer group, ack sau khi handler thành công; lỗi → để pending, được nhận lại
  (XAUTOCLAIM) cho tới `max_deliveries`, quá ngưỡng → chuyển sang `<stream>.dlq` và ack.
Consumer chết giữa chừng không làm mất sự kiện: tin chưa ack nằm trong PEL và được consumer khác nhận lại.
"""

import asyncio
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import orjson
from redis.asyncio import Redis
from redis.exceptions import ResponseError

log = logging.getLogger("gh.bus")

SCHEMA_VERSION = 1
#: v0.1.36: tập tên các stream `<stream>.dlq` đã từng nhận tin — GET /system/health đọc tập này thay vì SCAN cả
#: keyspace mỗi lần (gh/health.py).
DLQ_STREAMS_KEY = "gh:dlq:streams"

# Stream chuẩn của hệ thống.
BRIDGE_INBOUND = "gh.bridge.inbound"
BRIDGE_OUTBOUND = "gh.bridge.outbound"
BRIDGE_CONTROL = "gh.bridge.control"
BRIDGE_STATUS = "gh.bridge.status"
BRIDGE_DIRECTORY = "gh.bridge.directory"
#: v0.1.72: lõi → consumer của kênh quản trị (dm | pair | call | support_ask); mang chữ của Sếp/mã ghép nên có
# khoá nội dung.
OWNER_INBOUND = "gh.owner.inbound"
CLEAN_READY = "gh.clean.ready"
ACTION_REQUESTED = "gh.action.requested"
ACTION_DECIDED = "gh.action.decided"
PLUGIN_CONTROL = "gh.plugin.control"
PLUGIN_HEALTH = "gh.plugin.health"


#: Khoá payload mang NỘI DUNG tin theo stream: bản ghi gốc không được nằm lại sau khi đã xử lý (consumer tự XDEL khi
#: xong) và bản sao vào `.dlq` không được mang theo (review v0.1.65 P2-1: `directory` ←
#: `group.discovered.first_message`).
CONTENT_PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    BRIDGE_DIRECTORY: ("first_message",),
    OWNER_INBOUND: ("text", "code"),
}


def scrub_content(stream: str, fields: dict[Any, Any]) -> tuple[dict[Any, Any], bool]:
    """Bản sao `fields` đã bỏ các khoá nội dung của `stream` khỏi payload JSON; trả (fields mới, có bỏ gì không)."""
    keys = CONTENT_PAYLOAD_KEYS.get(stream)
    if not keys:
        return fields, False
    out = dict(fields)
    for k, v in fields.items():
        if (k.decode() if isinstance(k, bytes) else k) != "payload":
            continue
        try:
            payload = orjson.loads(v)
        except orjson.JSONDecodeError:
            return fields, False
        if not isinstance(payload, dict) or not any(x in payload for x in keys):
            return fields, False
        for x in keys:
            payload.pop(x, None)
        out[k] = orjson.dumps(payload)
        return out, True
    return fields, False


def uuid7() -> uuid.UUID:
    ms = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms & ((1 << 48) - 1)) << 80 | (0x7 << 76) | ((rand >> 64) & 0x0FFF) << 64 | (0b10 << 62) | (
        rand & ((1 << 62) - 1))
    return uuid.UUID(int=value)


@dataclass
class Event:
    stream: str
    message_id: str
    event_id: str
    type: str
    org_id: str | None
    correlation_id: str
    actor: str
    occurred_at: str
    schema_version: int
    payload: dict[str, Any]
    deliveries: int = 1


Handler = Callable[[Event], Awaitable[None]]


class Deferred(Exception):  # noqa: N818
    """Handler ném ra khi tạm thời chưa nhận tin: tin nằm lại, không tính là lỗi, không vào DLQ."""


def _sid(message_id: Any) -> str:
    return message_id.decode() if isinstance(message_id, bytes) else message_id


def _decode(stream: str, message_id: str, fields: dict[Any, Any], deliveries: int = 1) -> Event:
    f = {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
         for k, v in fields.items()}
    return Event(stream=stream, message_id=message_id, event_id=f["event_id"], type=f["type"],
                 org_id=f.get("org_id") or None, correlation_id=f["correlation_id"], actor=f["actor"],
                 occurred_at=f["occurred_at"], schema_version=int(f.get("schema_version", 1)),
                 payload=orjson.loads(f["payload"]), deliveries=deliveries)


class EventBus:
    def __init__(self, redis: Redis, maxlen: int = 100_000):
        self.redis = redis
        self.maxlen = maxlen

    async def publish(self, stream: str, type: str, payload: dict[str, Any], *, actor: str,
                      org_id: str | uuid.UUID | None = None, correlation_id: str | None = None) -> str:
        event_id = str(uuid7())
        fields: dict[Any, Any] = {
            "event_id": event_id, "type": type, "org_id": str(org_id) if org_id else "",
            "correlation_id": correlation_id or event_id, "actor": actor,
            "occurred_at": datetime.now(UTC).isoformat(), "schema_version": str(SCHEMA_VERSION),
            "payload": orjson.dumps(payload, default=str).decode(),
        }
        msg_id = await self.redis.xadd(stream, fields, maxlen=self.maxlen, approximate=True)
        return msg_id.decode() if isinstance(msg_id, bytes) else msg_id

    async def ensure_group(self, stream: str, group: str) -> None:
        try:
            await self.redis.xgroup_create(stream, group, id="0", mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def _deliveries(self, stream: str, group: str, message_id: str) -> int:
        info = await self.redis.xpending_range(stream, group, min=message_id, max=message_id, count=1)
        return int(info[0]["times_delivered"]) if info else 1

    async def _handle(self, stream: str, group: str, message_id: str, fields: dict[Any, Any],
                      handler: Handler, max_deliveries: int) -> bool:
        deliveries = await self._deliveries(stream, group, message_id)
        event = _decode(stream, message_id, fields, deliveries)
        fails_key = f"{stream}:{group}:fails"
        try:
            await handler(event)
        except Deferred:
            # Người nhận tạm không xử lý được (vd. plugin đang ngắt mạch): để nguyên trong PEL, không tính lỗi.
            return False
        except Exception as exc:  # noqa: BLE001 — cách ly lỗi handler
            fails = await self.redis.hincrby(fails_key, message_id, 1)  # type: ignore[misc]
            log.warning("handler lỗi stream=%s id=%s lần=%s: %s", stream, message_id, fails, exc)
            if fails >= max_deliveries:
                safe, scrubbed = scrub_content(stream, fields)
                await self.redis.xadd(f"{stream}.dlq", {**safe, "error": str(exc)[:500], "group": group},
                                      maxlen=self.maxlen, approximate=True)
                await self.redis.sadd(DLQ_STREAMS_KEY, f"{stream}.dlq")  # type: ignore[misc]
                await self.redis.xack(stream, group, message_id)
                if scrubbed:   # bản gốc còn nội dung tin ⇒ xoá; bản sao DLQ (đã bỏ nội dung) đủ để điều tra
                    await self.redis.xdel(stream, message_id)
                await self.redis.hdel(fails_key, message_id)  # type: ignore[misc]
            return False
        await self.redis.xack(stream, group, message_id)
        await self.redis.hdel(fails_key, message_id)  # type: ignore[misc]
        return True

    async def process_once(self, stream: str, group: str, consumer: str, handler: Handler, *, count: int = 10,
                           block_ms: int | None = 1000, max_deliveries: int = 5,
                           reclaim_idle_ms: int = 30_000, own_pending: bool = False) -> int:
        """Xử lý một vòng và trả về số tin thành công.

        Thứ tự: (1) tin còn treo của chính consumer này nếu `own_pending` (dùng khi khởi động / vừa hồi phục),
        (2) tin treo quá `reclaim_idle_ms` của consumer khác (đã chết), (3) tin mới.
        Tin lỗi được thử lại ở các vòng sau; lỗi thật lần thứ `max_deliveries` → `<stream>.dlq`.
        """
        await self.ensure_group(stream, group)
        done = 0
        if own_pending:
            resp = await self.redis.xreadgroup(group, consumer, {stream: "0"}, count=count)
            for _stream, messages in resp or []:
                for message_id, fields in messages:
                    if fields:
                        done += await self._handle(stream, group, _sid(message_id), fields, handler, max_deliveries)
        claimed = await self.redis.xautoclaim(stream, group, consumer, min_idle_time=reclaim_idle_ms,
                                              start_id="0-0", count=count)
        for message_id, fields in claimed[1]:
            if fields:
                done += await self._handle(stream, group, _sid(message_id), fields, handler, max_deliveries)
        resp = await self.redis.xreadgroup(group, consumer, {stream: ">"}, count=count, block=block_ms)
        for _stream, messages in resp or []:
            for message_id, fields in messages:
                done += await self._handle(stream, group, _sid(message_id), fields, handler, max_deliveries)
        return done

    async def run(self, stream: str, group: str, consumer: str, handler: Handler, stop: asyncio.Event,
                  **kwargs: Any) -> None:
        while not stop.is_set():
            try:
                await self.process_once(stream, group, consumer, handler, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — bus không được chết vì một vòng lỗi
                log.error("vòng consume lỗi stream=%s: %s", stream, exc)
                await asyncio.sleep(1)
