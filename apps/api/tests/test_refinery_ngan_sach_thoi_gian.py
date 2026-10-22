"""Review lần 2 v0.1.62 (P3-4) — job sàng lọc lặp nhiều phần lô không được vượt `JOB_TIMEOUT` của worker.

Mỗi phần lô gọi model tối đa hai lượt (lượt gọi lại khi JSON sai), mỗi lượt agy nền tới `AGY_BACKGROUND_TIMEOUT_S`;
nhiều phần lô nối tiếp có thể vượt 300 giây và arq giết job giữa chừng (bản ghi kẹt "đang xử lý"). Nay có NGÂN SÁCH
thời gian theo lượt: phần đầu luôn chạy; trước mỗi phần kế tiếp mà ngân sách còn lại không đủ cho trường hợp xấu
nhất của một phần thì dừng sớm, trả các bản ghi chưa xử lý về `pending` (không tính một lần thử) cho lượt sau.
"""

import asyncio
from typing import Any

import pytest
from sqlalchemy import text

from gh import worker
from gh.db import sessionmaker
from gh.providers import router as router_mod
from gh.refinery import extract, runner
from gh.refinery.runner import Refinery
from tests.phase2 import FakeRouter, msg, put, states, texts_in
from tests.test_refinery import setup_org, unit


class SlowRouter(FakeRouter):
    def __init__(self, delay: float) -> None:
        def reply(messages):  # type: ignore[no-untyped-def]
            return {"units": [unit(ref) for ref in texts_in(messages)], "noise": []}
        super().__init__(reply)
        self.delay = delay

    async def generate(self, *a: Any, **kw: Any) -> Any:
        await asyncio.sleep(self.delay)
        return await super().generate(*a, **kw)


BODIES = [f"Cần {n} container thép cuộn, giá bao nhiêu vậy em?" for n in (3, 4, 5)]


def test_default_budget_leaves_room_under_the_worker_job_timeout() -> None:
    assert runner.RUN_TIME_BUDGET_S < worker.JOB_TIMEOUT
    # Phần cuối được phép bắt đầu khi còn đủ cho lượt đầu + lượt gọi lại ⇒ kết thúc trước hạn ngân sách.
    assert runner.PART_WORST_S == 2 * router_mod.AGY_BACKGROUND_TIMEOUT_S
    assert runner.RUN_TIME_BUDGET_S >= runner.PART_WORST_S


async def test_budget_exhausted_defers_the_remaining_parts_and_a_later_run_finishes_them(  # type: ignore[no-untyped-def]
        app, db, redis, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extract, "CHUNK_EVENTS", 1)                         # 3 tin ⇒ 3 phần lô
    org = await setup_org(db)
    sm = sessionmaker()
    await put(sm, org, *[msg(b) for b in BODIES])
    router = SlowRouter(0.3)
    st = await Refinery(sm, redis, router, time_budget_s=0.5, part_worst_s=0.4).run(org, "manual")  # type: ignore[arg-type]
    assert len(router.calls) == 1                                           # phần đầu luôn chạy, hai phần sau để lại
    assert (st.status, st.clean, st.deferred) == ("done", 1, 2)
    assert await states(db, org) == {"clean": 1, "pending": 2}
    run = (await db.execute(text("SELECT status, error FROM refinery.runs WHERE id = :i"), {"i": st.run_id})).one()
    assert run.status == "done" and run.error is None                       # dừng sớm không phải lỗi
    pending = text("SELECT attempts FROM refinery.event_state WHERE state = 'pending'")
    attempts = (await db.execute(pending)).scalars().all()
    assert attempts == [0, 0]                                               # hoãn không tính một lần thử

    st2 = await Refinery(sm, redis, SlowRouter(0.0), time_budget_s=60, part_worst_s=1).run(org, "manual")  # type: ignore[arg-type]
    assert (st2.clean, st2.deferred) == (2, 0)
    assert await states(db, org) == {"clean": 3}


async def test_enough_budget_runs_every_part(app, db, redis, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(extract, "CHUNK_EVENTS", 1)
    org = await setup_org(db)
    sm = sessionmaker()
    await put(sm, org, *[msg(b) for b in BODIES])
    router = SlowRouter(0.05)
    st = await Refinery(sm, redis, router, time_budget_s=60, part_worst_s=1).run(org, "manual")  # type: ignore[arg-type]
    assert len(router.calls) == 3 and (st.clean, st.deferred) == (3, 0)
