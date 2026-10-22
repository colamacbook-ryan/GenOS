"""Bộ kích hoạt sàng lọc trong worker: chu kỳ HOẶC ngưỡng (cái nào tới trước), đường nhanh, chạy ngay.

- `LISTEN raw_ingested` đánh thức vòng lặp ngay khi có tin (đường nhanh ≤ vài giây).
- Mỗi nhịp: phục hồi tin kẹt `processing` của lượt đã chết → lượt thủ công đang chờ → đường nhanh → ngưỡng → chu kỳ.
- Khoá Redis theo tổ chức cho quyết định chu kỳ/ngưỡng (tránh hai worker cùng mở lượt rỗng); việc nhận tin vẫn
  an toàn nhờ `SKIP LOCKED` dù có nhiều lượt song song.
"""

import asyncio
import contextlib
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg  # type: ignore[import-untyped]
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gh.config import get_settings
from gh.refinery.runner import CRASH_SET, MAX_CRASHES, Refinery, load_schedule

log = logging.getLogger("gh.refinery.scheduler")

TICK_S = 5.0
STUCK_AFTER = timedelta(minutes=15)
FAST_LIMIT = 20
#: F055: tin `error` (model trả sai định dạng đủ MAX_ATTEMPTS lần) tự được nhận lại sau chừng này, tối đa chừng này lần;
#: hết lượt vẫn nằm ở `error` và hiện số ở dải pipeline — không thử vô hạn, cũng không biến mất âm thầm.
ERROR_RETRY_AFTER = timedelta(minutes=30)
MAX_ERROR_RETRIES = 3


async def requeue_errors(db: AsyncSession, org_id: uuid.UUID) -> int:
    """Đưa tin `error` đã nằm đủ lâu về `pending` (làm lại từ đầu số lần thử), có giới hạn số lần thử lại."""
    rows = (await db.execute(text("""
        UPDATE refinery.event_state SET state = 'pending', attempts = 0, updated_at = now(),
               detail = detail || jsonb_build_object('error_retries',
                                                     COALESCE((detail ->> 'error_retries')::int, 0) + 1)
        WHERE org_id = :o AND state = 'error' AND updated_at < :cut
          AND COALESCE((detail ->> 'error_retries')::int, 0) < :max
        RETURNING event_id"""), {"o": org_id, "cut": datetime.now(UTC) - ERROR_RETRY_AFTER,
                                 "max": MAX_ERROR_RETRIES})).all()
    return len(rows)


def tick_key(org_id: Any) -> str:
    return f"gh:refinery:tick:{org_id}"


async def pending_count(db: AsyncSession, org_id: uuid.UUID) -> int:
    return int((await db.execute(text("""SELECT count(*) FROM refinery.event_state
                                         WHERE org_id = :o AND state = 'pending'"""), {"o": org_id})).scalar_one())


async def next_run(db: AsyncSession, redis: Redis, org_id: uuid.UUID) -> dict[str, Any]:
    sched = await load_schedule(db, org_id)
    pending = await pending_count(db, org_id)
    last = await redis.get(tick_key(org_id))
    last_ts = float(last) if last else time.time()
    at = datetime.fromtimestamp(last_ts, UTC) + timedelta(seconds=sched.interval_seconds)
    return {"interval_seconds": sched.interval_seconds, "count_threshold": sched.count_threshold,
            "batch_size": sched.batch_size, "min_confidence": sched.min_confidence, "pending": pending,
            "next_run_at": at.isoformat().replace("+00:00", "Z"),
            "next_trigger": "count" if pending >= sched.count_threshold else "interval"}


class Scheduler:
    def __init__(self, sm: async_sessionmaker[AsyncSession], redis: Redis, refinery: Refinery):
        self.sm, self.redis, self.refinery = sm, redis, refinery
        self.wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._listener: asyncpg.Connection | None = None
        self._running: set[asyncio.Task[Any]] = set()

    async def start(self) -> None:
        with contextlib.suppress(Exception):
            dsn = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
            self._listener = await asyncpg.connect(dsn)
            await self._listener.add_listener("raw_ingested", lambda *_: self.wake.set())
        self._task = asyncio.create_task(self._loop(), name="refinery-scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        for t in list(self._running):
            t.cancel()
        if self._listener is not None:
            with contextlib.suppress(Exception):
                await self._listener.close()

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("nhịp sàng lọc lỗi")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), TICK_S)
            self.wake.clear()

    def _spawn(self, coro: Any) -> None:
        t = asyncio.create_task(coro)
        self._running.add(t)
        t.add_done_callback(self._running.discard)

    async def tick(self, *, wait: bool = False) -> list[Any]:
        """Một nhịp cho mọi tổ chức. `wait=True` (test) chạy các lượt tuần tự và trả kết quả."""
        results: list[Any] = []
        async with self.sm() as db:
            orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
        for org in orgs:
            results.extend(await self._org_tick(org, wait))
        return results

    async def _go(self, coro: Any, wait: bool, results: list[Any]) -> None:
        if wait:
            results.append(await coro)
        else:
            self._spawn(coro)

    async def _org_tick(self, org_id: uuid.UUID, wait: bool) -> list[Any]:
        results: list[Any] = []
        async with self.sm() as db:
            # Tin kẹt: lượt chết giữa chừng (worker bị giết) → trả về pending.
            await db.execute(text(f"""
                UPDATE refinery.event_state s SET {CRASH_SET}
                FROM refinery.runs r WHERE s.run_id = r.id AND s.state = 'processing' AND s.org_id = :o
                  AND (r.status <> 'running' OR r.started_at < :cut)"""),  # noqa: S608 — CRASH_SET là hằng
                {"o": org_id, "cut": datetime.now(UTC) - STUCK_AFTER, "maxc": MAX_CRASHES,
                 "why": "Lượt xử lý bị ngắt giữa chừng"})
            await requeue_errors(db, org_id)
            await db.execute(text("""UPDATE refinery.runs SET status = 'failed', finished_at = now(),
                                     error = 'Lượt bị ngắt giữa chừng' WHERE org_id = :o AND status = 'running'
                                     AND started_at < :cut"""),
                             {"o": org_id, "cut": datetime.now(UTC) - STUCK_AFTER})
            queued = (await db.execute(text("""SELECT id FROM refinery.runs WHERE org_id = :o AND status = 'queued'
                                               ORDER BY started_at"""), {"o": org_id})).scalars().all()
            fast = (await db.execute(text("""SELECT event_id FROM refinery.event_state
                                             WHERE org_id = :o AND state = 'pending' AND fast
                                             ORDER BY event_received_at LIMIT :n"""),
                                     {"o": org_id, "n": FAST_LIMIT})).scalars().all()
            sched = await load_schedule(db, org_id)
            pending = await pending_count(db, org_id)
            busy = bool((await db.execute(text("""SELECT 1 FROM refinery.runs WHERE org_id = :o AND status = 'running'
                                                  AND trigger IN ('schedule', 'threshold') LIMIT 1"""),
                                          {"o": org_id})).scalar())
            await db.commit()
        for run_id in queued:
            # Đánh dấu ngay để nhịp sau không chạy lại.
            async with self.sm() as db:
                n = (await db.execute(text("""UPDATE refinery.runs SET status = 'running', started_at = now()
                                              WHERE id = :i AND status = 'queued' RETURNING id"""),
                                      {"i": run_id})).scalar()
                await db.commit()
            if n:
                await self._go(self.refinery.run(org_id, "manual", run_id=run_id), wait, results)
        if fast:
            await self._go(self.refinery.run(org_id, "fast", event_ids=list(fast), limit=FAST_LIMIT), wait, results)
            pending -= len(fast)
        now = time.time()
        if await self.redis.get(tick_key(org_id)) is None:
            await self.redis.set(tick_key(org_id), str(now))
        last = float(await self.redis.get(tick_key(org_id)) or now)
        due_interval = now - last >= sched.interval_seconds
        due_count = pending >= sched.count_threshold
        if not (due_interval or due_count) or busy:
            return results
        if not await self.redis.set(f"gh:refinery:lock:{org_id}", "1", nx=True, ex=30):
            return results
        try:
            if due_interval:
                await self.redis.set(tick_key(org_id), str(now))
            if pending > 0:
                trigger = "threshold" if due_count else "schedule"
                await self._go(self.refinery.run(org_id, trigger), wait, results)
        finally:
            await self.redis.delete(f"gh:refinery:lock:{org_id}")
        return results
