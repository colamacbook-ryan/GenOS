# ruff: noqa: E501
"""Nhịp theo luồng (spec §4D.1, §4D.5): luồng có tin mới VÀ (tin chờ cũ nhất ≥ 20 phút HOẶC ≥ 40 tin mới); ứng viên 6 cổng INT-19 chạy NGAY;
giãn lên 60 phút khi chạm 80% trần; round-robin theo luồng; đồng hồ giả chỉ bật khi GH_ENV=eval."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from gh import ai_cost
from gh.db import sessionmaker
from gh.refinery import budget, clock
from gh.refinery.budget import ThreadState, due_reason, order_threads
from gh.refinery.runner import Refinery
from gh.refinery.scheduler import Scheduler
from tests.phase2 import FakeRouter, listen, msg, org_id, put
from tests.refinery_group_helpers import Analyzer, LoggingRouter, add_call, enable_groups, mk_model, pending_n

NOW = datetime(2026, 10, 21, 5, 0, tzinfo=UTC)


def ts(n: int = 3, age_min: float = 0, **kw) -> ThreadState:  # type: ignore[no-untyped-def]
    return ThreadState(kw.pop("thread_key", "g:x"), n, NOW - timedelta(minutes=age_min), **kw)


# ─── hàm thuần ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_due_reason_cadence_20_minutes_or_40_messages() -> None:
    assert due_reason(ts(3, 19.9), NOW) is None
    assert due_reason(ts(3, 20), NOW) == "cadence"
    assert due_reason(ts(39, 1), NOW) is None
    assert due_reason(ts(40, 0), NOW) == "count"
    assert due_reason(ts(0, 999), NOW) is None                                   # không có tin mới ⇒ không có lượt
    assert due_reason(ts(3, 5), NOW, cadence_min=5) == "cadence"                 # `intel.cadence_min` chỉnh được


def test_due_reason_stretched_cadence_is_60_minutes_but_gates_and_safety_still_run_now() -> None:
    assert due_reason(ts(3, 45), NOW, stretched=True) is None
    assert due_reason(ts(3, 60), NOW, stretched=True) == "cadence"
    assert due_reason(ts(1, 0, gate_class="safety"), NOW, stretched=True) == "safety"
    assert due_reason(ts(1, 0, gate_class="gate"), NOW, stretched=True) == "gate"
    assert due_reason(ts(1, 0, fast=True), NOW) == "fast"


def test_due_reason_respects_backoff_even_for_safety() -> None:
    later = NOW + timedelta(minutes=5)
    assert due_reason(ts(1, 0, gate_class="safety", next_try_at=later), NOW) is None
    assert due_reason(ts(1, 0, gate_class="safety", next_try_at=later), later + timedelta(seconds=1)) == "safety"


def test_order_threads_is_round_robin_safety_first_then_least_recently_analysed() -> None:
    a = ts(5, 30, thread_key="a", last_analysis_at=NOW - timedelta(minutes=10))
    b = ts(5, 30, thread_key="b", last_analysis_at=NOW - timedelta(minutes=50))
    c = ts(5, 30, thread_key="c")                                                 # chưa bao giờ ⇒ trước hết trong hạng
    d = ts(1, 1, thread_key="d", gate_class="safety", last_analysis_at=NOW)
    e = ts(50, 5, thread_key="e")
    got = order_threads([(a, "cadence"), (b, "cadence"), (c, "cadence"), (d, "safety"), (e, "count")])
    assert [t.thread_key for t, _ in got] == ["d", "e", "c", "b", "a"]


def test_kind_for_reason() -> None:
    assert budget.kind_for("safety") == "safety" and budget.kind_for("gate") == "gate"
    assert all(budget.kind_for(r) == "normal" for r in ("fast", "count", "cadence", "manual"))


def test_fake_clock_only_when_gh_env_is_eval(monkeypatch: pytest.MonkeyPatch) -> None:
    clock.reset()
    monkeypatch.setenv("GH_ENV", "production")
    with pytest.raises(RuntimeError):
        clock.freeze(NOW)
    with pytest.raises(RuntimeError):
        clock.advance(timedelta(hours=1))
    assert not clock.is_fake() and abs((clock.now() - datetime.now(UTC)).total_seconds()) < 5
    monkeypatch.setenv("GH_ENV", "eval")
    assert clock.freeze(NOW) == NOW and clock.now() == NOW and clock.is_fake()
    assert clock.advance(timedelta(minutes=21)) == NOW + timedelta(minutes=21)
    monkeypatch.setenv("GH_ENV", "production")                                      # đổi môi trường ⇒ đồng hồ giả KHÔNG còn hiệu lực
    assert not clock.is_fake() and abs((clock.now() - datetime.now(UTC)).total_seconds()) < 5
    monkeypatch.setenv("GH_ENV", "eval")
    clock.reset()
    assert not clock.is_fake()
    with pytest.raises(ValueError):
        clock.freeze(datetime(2026, 1, 1))                                          # thiếu múi giờ


# ─── bộ kích hoạt thật (Scheduler.tick) trên đồng hồ giả ───────────────────────────────────────────────────────────

@pytest.fixture
def eval_clock(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GH_ENV", "eval")
    clock.reset()
    yield
    clock.reset()


async def boot(db, redis, *groups: str, **intel):  # type: ignore[no-untyped-def]
    org = await org_id(db)
    gids = [await listen(db, org, g) for g in groups]
    await enable_groups(db, org, **intel)
    return org, gids


def sched(sm, redis, an: Analyzer, router=None) -> Scheduler:  # type: ignore[no-untyped-def]
    return Scheduler(sm, redis, Refinery(sm, redis, router or FakeRouter(), analyzer=an))


async def test_cadence_20_minutes_through_the_scheduler(app, db, redis, eval_clock) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, (g1,) = await boot(db, redis, "g1")
    await put(sm, org, msg("Cần báo giá thép cuộn nhé anh", sender="u1"), msg("Còn hàng không em", sender="u2", name="Anh Tư"))
    an = Analyzer()
    s = sched(sm, redis, an)
    base = datetime.now(UTC)
    clock.freeze(base + timedelta(minutes=19))
    assert await s.tick(wait=True) == [] and an.windows == []                       # 19 phút: chưa tới nhịp
    clock.freeze(base + timedelta(minutes=21))
    runs = await s.tick(wait=True)
    assert len(runs) == 1 and runs[0].trigger == "schedule" and an.threads == [f"g:{g1}"]
    assert len(an.windows[0].new_events) == 2 and await pending_n(db, org) == 0
    assert await s.tick(wait=True) == []                                            # hết tin chờ: không mở lượt rỗng


async def test_forty_new_messages_trigger_immediately(app, db, redis, eval_clock) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await boot(db, redis, "g1")
    await put(sm, org, *[msg(f"Tin số {i} hỏi hàng nhé", sender=f"s{i}", name=f"Người {i}") for i in range(39)])
    an = Analyzer()
    s = sched(sm, redis, an)
    assert await s.tick(wait=True) == []                                            # 39 tin, mới gửi ⇒ chưa
    await put(sm, org, msg("Tin thứ bốn mươi", sender="s40", name="Người 40"))
    runs = await s.tick(wait=True)
    assert len(runs) == 1 and runs[0].trigger == "threshold" and len(an.windows[0].new_events) == 40


async def test_flooding_sender_counts_as_one_block_for_the_threshold(app, db, redis, eval_clock) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await boot(db, redis, "g1")
    await put(sm, org, *[msg(f"spam dòng {i} abc", sender="flood", name="Người xả tin") for i in range(45)])      # 45 tin trong vài giây
    an = Analyzer()
    s = sched(sm, redis, an)
    assert await s.tick(wait=True) == []                                            # 45 tin của MỘT người = một khối, không phải ≥ 40 tin
    clock.freeze(datetime.now(UTC) + timedelta(minutes=21))
    runs = await s.tick(wait=True)
    assert len(runs) == 1 and len(an.windows) == 1
    assert len(an.windows[0].new_events) == 1 and an.windows[0].new_events[0].body.count("\n") == 44      # một khối gộp, không tăng số lượt


async def test_safety_candidate_runs_immediately_without_waiting_for_cadence(app, db, redis, eval_clock) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, (g1, g2) = await boot(db, redis, "g1", "g2")
    await put(sm, org, msg("Báo giá thép tuần sau nhé anh", group="g1", sender="u1"),
              msg("Em lỡ nhập mã OTP vào link đó rồi chị ơi", group="g2", sender="u2", name="Chị Năm"))
    m = await mk_model(db, org)
    an = Analyzer()
    router = LoggingRouter(sm, m)
    s = sched(sm, redis, an, router)
    runs = await s.tick(wait=True)
    assert len(runs) == 1 and runs[0].trigger == "fast"
    assert an.threads == [f"g:{g2}"] and router.purposes == ["refinery.safety"]      # chỉ luồng có ứng viên; luồng thường vẫn đợi nhịp
    assert await pending_n(db, org) == 1
    cands = (await db.execute(text("SELECT detail -> 'cands' AS c, detail ->> 'gate_class' AS gc FROM refinery.event_state WHERE detail ->> 'gate_class' IS NOT NULL"))).one()
    assert cands.gc == "safety" and cands.c[0]["flag"] == "credentials_entered" and cands.c[0]["hard"] == "credentials_entered"
    assert "OTP" not in str(cands.c)                                                 # ứng viên không mang chữ của tin


async def test_stretched_thread_waits_60_minutes(app, db, redis, eval_clock) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, (g1,) = await boot(db, redis, "g1")
    m = await mk_model(db, org)
    await put(sm, org, msg("Cần báo giá thép nhé anh", sender="u1"))
    an = Analyzer()
    s = sched(sm, redis, an)
    base = datetime.now(UTC)

    async def spend_80_percent(at: datetime) -> None:                               # 32/40 = 80% trần luồng, tính theo NGÀY VN của đồng hồ giả
        for _ in range(32):
            await add_call(db, org, m, "refinery.group", at=at, thread_key=f"g:{g1}")

    await spend_80_percent(base + timedelta(minutes=45))
    clock.freeze(base + timedelta(minutes=45))
    assert await s.tick(wait=True) == []
    w = (await db.execute(text("SELECT priority, stretched_at FROM refinery.group_watermarks"))).one()
    assert w.priority == "stretched" and w.stretched_at is not None                   # ghi mốc giãn nhịp cho dòng độ phủ
    if ai_cost.today_vn(base + timedelta(minutes=61)) != ai_cost.today_vn(base + timedelta(minutes=45)):
        await spend_80_percent(base + timedelta(minutes=61))                         # qua nửa đêm VN giữa hai nhịp: trần ngày mới, chi lại 80%
    clock.freeze(base + timedelta(minutes=61))
    assert len(await s.tick(wait=True)) == 1 and len(an.windows) == 1


async def test_noisy_thread_does_not_delay_other_threads(app, db, redis, eval_clock) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, (g1, g2) = await boot(db, redis, "g1", "g2")
    base = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)
    flood = []
    for i in range(130):
        p = msg(f"Tin ồn số {i} về hàng", group="g1", sender=f"n{i}", name=f"Người {i}")
        p["occurred_at"] = (base + timedelta(minutes=5 * i)).isoformat()
        flood.append(p)
    await put(sm, org, *flood, msg("Em lỡ nhập mã OTP vào link lạ rồi", group="g2", sender="v1", name="Nạn nhân"))
    an = Analyzer()
    s = sched(sm, redis, an)
    runs = await s.tick(wait=True)
    assert len(runs) == 1
    assert an.threads[0] == f"g:{g2}"                                                # lừa đảo/an toàn trước, không chờ luồng ồn
    assert an.threads.count(f"g:{g1}") == 1 and len(an.windows[1].new_events) == 60   # luồng ồn chỉ một lượt mỗi vòng
    assert await pending_n(db, org) == 70


async def test_manual_run_ignores_cadence_but_scheduler_leaves_legacy_orgs_alone(app, db, redis, eval_clock) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org = await org_id(db)
    await listen(db, org, "g1")                                                       # KHÔNG bật đường theo luồng
    await put(sm, org, msg("Cần báo giá thép nhé anh", sender="u1"))
    an = Analyzer()
    s = sched(sm, redis, an)
    await s.tick(wait=True)
    assert an.windows == []                                                           # đường cũ: không đụng đường theo luồng
    assert (await db.execute(text("SELECT count(*) FROM refinery.group_watermarks"))).scalar_one() == 0
    assert uuid.UUID(str(org))
