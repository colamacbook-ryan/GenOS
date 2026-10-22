"""Việc nền của cụm agent trực kênh: hook sau sàng lọc và việc định kỳ cho worker.

- `HOOKS`: `duty` — mỗi lô `gh.clean.ready`: cặp (agent đang bật, đơn vị trong phạm vi) chưa quyết định → model →
  policy → ghi vết (`gh.biz.duty.engine`). Còn phần chưa xong (model chết, vượt tần suất, hết giờ) → `Deferred`: sự
  kiện nằm lại trong consumer group `hook:duty` và được nhận lại, không tính là lỗi, không vào DLQ.
- `JOBS`: `duty_sweep` mỗi 5 phút vớt đơn vị 24 giờ qua còn thiếu quyết định (vd. sự kiện đã vào DLQ, worker chết).
"""

import time
from typing import Any

from sqlalchemy import text

from gh.biz.duty import engine
from gh.biz.duty.context import candidates
from gh.biz.hooks import CronJob, Hook, HookCtx
from gh.chassis.bus import Deferred

HOOK_TIMEOUT_S = 300.0
BUDGET_S = HOOK_TIMEOUT_S * 0.8
SWEEP_HOURS = 24
SWEEP_MIN_AGE_S = 120
SWEEP_LIMIT = 50


async def duty_hook(ctx: HookCtx) -> None:
    async with ctx.sm() as db:
        cands = await candidates(db, ctx.org_id, unit_ids=ctx.unit_ids)
    if not cands:
        return
    b = await engine.process(ctx.sm, ctx.redis, ctx.router, ctx.org_id, cands,
                             deadline=time.monotonic() + BUDGET_S)
    if b.failed:
        # Lỗi thật (không phải model chết): để bus tính lần thử / DLQ; engine tự chốt `silent` sau MAX_FAILS lần.
        raise RuntimeError(f"agent trực kênh lỗi {b.failed} cặp")
    if b.pending:
        why = " (model chưa chạy được)" if b.model_down else ""
        raise Deferred(f"agent trực kênh còn {b.deferred} cặp chờ{why}")


async def duty_sweep(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.db import sessionmaker

    sm = sessionmaker()
    redis, router = ctx["redis_bus"], ctx["model_router"]
    out: dict[str, int] = {}
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        async with sm() as db:
            cands = await candidates(db, org, since_hours=SWEEP_HOURS, min_age_s=SWEEP_MIN_AGE_S, limit=SWEEP_LIMIT)
        if cands:
            b = await engine.process(sm, redis, router, org, cands, deadline=time.monotonic() + BUDGET_S)
            out[str(org)] = b.done
    return out


HOOKS: list[Hook] = [Hook("duty", duty_hook, timeout_s=HOOK_TIMEOUT_S)]
JOBS: list[CronJob] = [(duty_sweep, {"minute": set(range(2, 60, 5))})]
