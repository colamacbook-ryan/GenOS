# ruff: noqa: E501
"""Trần chi phí tình báo (spec Bước 1 r6.6.1 §4D.5, CNS-074/099/100): ba bậc trần, giãn nhịp ở 80%, chỉ-luật ở 100%, lừa đảo/an toàn
không bao giờ bị cắt (vượt trần ⇒ báo Ryan), chế độ miễn phí (Q14), đếm từ MỘT sổ `agent.model_calls` (giờ VN), chi phí theo luồng."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from gh import ai_cost
from gh.db import sessionmaker
from gh.refinery import budget, clock
from gh.refinery.budget import KIND_GATE, KIND_NORMAL, KIND_SAFETY, Caps, Usage, decide
from gh.refinery.runner import Refinery
from tests.phase2 import FakeRouter, listen, msg, org_id, put, states
from tests.refinery_group_helpers import (
    Analyzer,
    LoggingRouter,
    add_call,
    enable_groups,
    mk_model,
    pending_n,
    set_ai_cost,
)

CAPS = Caps.from_settings(ai_cost.INTEL_DEFAULTS)               # 400 / 60 / 40, VND = 0 (gói thuê bao)
TK = "g:t1"


def usage(normal: int = 0, gate: int = 0, safety: int = 0, thread: int = 0, vnd: float = 0.0) -> Usage:
    u = Usage(calls={KIND_NORMAL: normal, KIND_GATE: gate, KIND_SAFETY: safety}, vnd={KIND_NORMAL: vnd})
    u.thread_calls[TK] = thread
    return u


# ─── hàm thuần: mặc định, bậc trần ──────────────────────────────────────────────────────────────────────────────────

def test_defaults_follow_spec_and_thread_cap_is_ten_percent_of_call_cap() -> None:
    assert (CAPS.call_cap, CAPS.safety_call_cap, CAPS.gate_call_cap, CAPS.budget_vnd) == (400, 60, 40, 0)
    assert CAPS.thread_call_cap == 40 and CAPS.thread_vnd_cap is None
    assert Caps(400, 60, 40, 1_000_000, 0, 0).thread_vnd_cap == 100_000                      # 10% trần tổ chức
    assert Caps(400, 60, 40, 50_000, 0, 0).thread_vnd_cap == 15_000                          # sàn 15.000 ₫
    assert Caps(0, 60, 40, 0, 0, 0).thread_call_cap == 0


def test_normal_below_80_percent_runs_with_normal_cadence() -> None:
    d = decide(KIND_NORMAL, TK, CAPS, usage(normal=100, thread=10))
    assert d.allow and d.mode == budget.MODE_NORMAL and not d.stretched and not d.capped


def test_thread_at_80_percent_stretches_only_that_thread() -> None:
    d = decide(KIND_NORMAL, TK, CAPS, usage(normal=100, thread=32))
    assert d.allow and d.stretch_thread and not d.stretch_org and d.mode == budget.MODE_STRETCHED
    other = decide(KIND_NORMAL, "g:other", CAPS, usage(normal=100, thread=32))
    assert not other.stretched


def test_org_at_80_percent_stretches_every_thread() -> None:
    d = decide(KIND_NORMAL, "g:any", CAPS, usage(normal=320))
    assert d.allow and d.stretch_org and d.stretched


def test_org_at_100_percent_is_rules_only_for_normal_turns() -> None:
    d = decide(KIND_NORMAL, TK, CAPS, usage(normal=400))
    assert not d.allow and d.mode == budget.MODE_RULES_ONLY and d.capped and d.reasons == ("org_cap",)
    t = decide(KIND_NORMAL, TK, CAPS, usage(normal=10, thread=40))
    assert not t.allow and t.reasons == ("thread_cap",)


def test_gate_runs_now_until_gate_cap_even_when_org_is_capped() -> None:
    d = decide(KIND_GATE, TK, CAPS, usage(normal=361, gate=39))
    assert d.allow and d.kind == KIND_GATE and d.capped                                # tổ chức đã 100% mà cổng vẫn chạy
    full = decide(KIND_GATE, TK, CAPS, usage(normal=360, gate=40))
    assert not full.allow and full.kind == KIND_NORMAL and full.reasons[0] == "gate_cap_reached"        # hết trần cổng + tổ chức hết ⇒ chỉ luật
    room = decide(KIND_GATE, TK, CAPS, usage(normal=10, gate=40))
    assert room.allow and room.kind == KIND_NORMAL and room.reasons == ("gate_cap_reached",)          # còn lượt chung ⇒ xếp như lượt thường


def test_safety_over_soft_cap_still_runs_and_only_flags_the_alert() -> None:
    d = decide(KIND_SAFETY, TK, CAPS, usage(normal=400, safety=59))
    assert d.allow and not d.over_safety_cap
    over = decide(KIND_SAFETY, TK, CAPS, usage(normal=400, safety=100, thread=5))     # 100 > 60 nhưng < 3 × 60: fail-high, vẫn chạy
    assert over.allow and over.over_safety_cap and over.reasons == ("safety_cap_exceeded",)


def test_safety_abuse_caps_fall_back_to_hard_rules_only() -> None:
    u = usage(safety=10, thread=5)
    u.thread_safety[TK] = budget.SAFETY_PER_THREAD_DAY
    d = decide(KIND_SAFETY, TK, CAPS, u)
    assert not d.allow and d.mode == budget.MODE_HARD_ONLY and d.reasons == ("thread_safety_cap",)
    assert not decide(KIND_SAFETY, TK, CAPS, usage(safety=10), sender_over=True).allow                  # ≤ 6 lượt/giờ/người gửi
    assert decide(KIND_SAFETY, TK, CAPS, usage(safety=179)).allow
    hard = decide(KIND_SAFETY, TK, CAPS, usage(safety=180))                                             # trần cứng 3 × 60
    assert not hard.allow and hard.reasons == ("safety_hard_cap",) and hard.over_safety_cap
    assert not decide(KIND_SAFETY, TK, CAPS, usage(thread=40)).allow                                    # không bỏ qua thread_ratio
    assert not decide(KIND_SAFETY, TK, Caps(0, 60, 40, 0, 0, 0), usage()).allow                         # trần luồng = 0 ⇒ chỉ luật


def test_free_mode_blocks_every_class_when_paid_calls_appear_with_zero_vnd_budget() -> None:
    u = usage(normal=3, vnd=12.5)
    assert budget.free_mode(CAPS, u)
    for kind in (KIND_NORMAL, KIND_GATE, KIND_SAFETY):
        d = decide(kind, TK, CAPS, u)
        assert not d.allow and d.mode == budget.MODE_FREE and d.reasons == ("free_mode",)
    assert not budget.free_mode(Caps(400, 60, 40, 1_000_000, 0, 0), u)                       # có trần VND > 0 ⇒ không phải chế độ miễn phí


def test_vnd_budget_mode_uses_the_larger_ratio() -> None:
    caps = Caps(400, 60, 40, 1_000_000, 0, 0)
    assert decide(KIND_NORMAL, TK, caps, usage(normal=10, vnd=800_000)).stretch_org
    assert decide(KIND_NORMAL, TK, caps, usage(normal=10, vnd=1_000_000)).capped
    u = usage(normal=1)
    u.thread_vnd[TK] = 90_000                                                                # 90% của trần luồng 100.000
    assert decide(KIND_NORMAL, TK, caps, u).stretch_thread


def test_zero_call_cap_means_rules_only_and_cadence_stretch_value() -> None:
    assert not decide(KIND_NORMAL, TK, Caps(0, 60, 40, 0, 0, 0), usage()).allow
    assert budget.cadence_minutes(20, False) == 20 and budget.cadence_minutes(20, True) == 60 and budget.cadence_minutes(90, True) == 90


def test_decide_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        decide("bogus", TK, CAPS, usage())


# ─── đọc sổ ─────────────────────────────────────────────────────────────────────────────────────────────────────────

NOW = datetime(2026, 10, 21, 5, 0, tzinfo=UTC)                  # 12:00 giờ VN ngày 21/10


async def test_usage_counts_vn_day_by_purpose_status_and_thread(app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    m = await mk_model(db, org)
    await add_call(db, org, m, "refinery.group", at=NOW, thread_key="g:a")
    await add_call(db, org, m, "refinery.group", at=NOW + timedelta(minutes=1), thread_key="g:a")
    await add_call(db, org, m, "refinery.group", at=NOW, thread_key="g:b")
    await add_call(db, org, m, "refinery.gate", at=NOW, thread_key="g:b")
    await add_call(db, org, m, "refinery.safety", at=NOW, thread_key="g:b")
    await add_call(db, org, m, "refinery.group", at=NOW, thread_key="g:a", status="error")        # lỗi: không tính
    await add_call(db, org, m, "refinery", at=NOW)                                                # đường cũ: không phải lượt theo luồng
    await add_call(db, org, m, "refinery.group", at=NOW - timedelta(days=1), thread_key="g:a")    # hôm qua
    await add_call(db, org, m, "refinery.group", at=datetime(2026, 10, 20, 17, 30, tzinfo=UTC), thread_key="g:a")   # 00:30 VN ngày 21 ⇒ hôm nay
    u = await budget.load_usage(db, org, NOW)
    assert u.calls == {KIND_NORMAL: 4, KIND_GATE: 1, KIND_SAFETY: 1}
    assert u.org_calls == 5 and u.thread_calls == {"g:a": 3, "g:b": 3}
    assert u.vnd.get(KIND_NORMAL) == 0.0                                                          # CLI thuê bao = 0 ₫


async def test_usage_cost_comes_from_ai_cost_prices_single_source(app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    paid = await mk_model(db, org, kind="openrouter", price=(10_000.0, 40_000.0))                  # 10.000 vào / 40.000 ra mỗi triệu token
    await add_call(db, org, paid, "refinery.group", at=NOW, thread_key="g:a", tin=100_000, tout=50_000)       # 1.000 + 2.000 = 3.000 ₫
    u = await budget.load_usage(db, org, NOW)
    assert u.vnd[KIND_NORMAL] == 3000.0 and u.thread_vnd["g:a"] == 3000.0
    assert ai_cost.vnd((await db.execute(text("SELECT cost_vnd FROM refinery.group_budget"))).scalar_one()) == 3000      # cùng số với view L1
    assert budget.free_mode(await budget.load_caps(db, org), u)                                   # trần VND mặc định 0 ⇒ chế độ miễn phí


async def test_load_caps_reads_intel_keys_with_defaults(app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    assert await budget.load_caps(db, org) == CAPS
    await set_ai_cost(db, org, intel_daily_call_cap=10, intel_gate_call_cap=2, intel_daily_budget_vnd=500_000, junk="x")
    c = await budget.load_caps(db, org)
    assert (c.call_cap, c.gate_call_cap, c.budget_vnd, c.safety_call_cap) == (10, 2, 500_000, 60)


async def test_sync_thread_state_tracks_stretch_and_cap_minutes(app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    t0 = datetime(2026, 10, 21, 7, 10, tzinfo=UTC)                 # 14:10 giờ VN
    stretched = decide(KIND_NORMAL, TK, CAPS, usage(thread=33))
    await budget.sync_thread_state(db, org, TK, stretched, now=t0)
    await budget.sync_thread_state(db, org, TK, stretched, now=t0 + timedelta(minutes=5))             # gọi lại: không đổi mốc
    w = (await db.execute(text("SELECT priority, stretched_at, stretched_minutes, capped_at FROM refinery.group_watermarks"))).one()
    assert w.priority == "stretched" and w.stretched_at == t0 and w.stretched_minutes == 0 and w.capped_at is None
    capped = decide(KIND_NORMAL, TK, CAPS, usage(normal=400, thread=33))
    await budget.sync_thread_state(db, org, TK, capped, now=t0 + timedelta(minutes=30))
    assert (await db.execute(text("SELECT capped_at FROM refinery.group_watermarks"))).scalar_one() == t0 + timedelta(minutes=30)
    calm = decide(KIND_NORMAL, TK, CAPS, usage(thread=1))
    await budget.sync_thread_state(db, org, TK, calm, now=t0 + timedelta(minutes=90))
    w = (await db.execute(text("SELECT priority, stretched_at, stretched_minutes, capped_at, capped_minutes FROM refinery.group_watermarks"))).one()
    assert (w.priority, w.stretched_at, w.stretched_minutes, w.capped_at, w.capped_minutes) == ("normal", None, 90, None, 60)


async def test_coverage_notes_name_the_stretched_group_and_free_mode(app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    gid = await listen(db, org, "g1")
    await db.execute(text("UPDATE core.groups SET name = 'Kho Bình Dương' WHERE id = :g"), {"g": gid})
    await db.commit()
    t0 = datetime(2026, 10, 21, 7, 10, tzinfo=UTC)
    await budget.sync_thread_state(db, org, f"g:{gid}", decide(KIND_NORMAL, f"g:{gid}", CAPS, Usage(thread_calls={f"g:{gid}": 33})),
                                   group_id=gid, now=t0)
    notes = await budget.coverage_notes(db, org, now=t0 + timedelta(hours=1))
    assert notes == ["nhóm Kho Bình Dương giãn nhịp từ 14:10"]
    m = await mk_model(db, org, kind="openrouter", price=(1.0, 1.0))
    await add_call(db, org, m, "refinery.group", at=t0 + timedelta(minutes=30), tin=2_000_000, tout=0)
    notes = await budget.coverage_notes(db, org, now=t0 + timedelta(hours=1))
    assert notes[0] == "đang chạy chế độ miễn phí: chỉ quét luật"


# ─── trong lượt chạy thật ───────────────────────────────────────────────────────────────────────────────────────────

def refinery(sm, redis, an: Analyzer, router=None) -> Refinery:  # type: ignore[no-untyped-def]
    return Refinery(sm, redis, router or FakeRouter(), analyzer=an)


async def test_capped_org_keeps_events_pending_rules_only_until_next_vn_day(app, db, redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GH_ENV", "eval")
    sm = sessionmaker()
    org = await org_id(db)
    await listen(db, org, "g1")
    await enable_groups(db, org)
    await set_ai_cost(db, org, intel_daily_call_cap=5)
    m = await mk_model(db, org)
    now = datetime.now(UTC)
    for _ in range(5):
        await add_call(db, org, m, "refinery.group", at=now)
    await put(sm, org, msg("Cần báo giá thép cuộn nhé anh", sender="u1"))
    an = Analyzer()
    r = refinery(sm, redis, an)
    await r.run(org, "manual")
    assert an.windows == [] and await pending_n(db, org) == 1                  # hết lượt: chỉ luật (a), tin nằm chờ
    cap = (await db.execute(text("SELECT capped_at, priority FROM refinery.group_watermarks"))).one()
    assert cap.capped_at is not None
    assert any("hết lượt" in n or "đã hết lượt" in n for n in await budget.coverage_notes(db, org))
    clock.freeze(now + timedelta(hours=26))                                       # sang ngày VN kế tiếp: trần đặt lại
    try:
        await r.run(org, "manual")
    finally:
        clock.reset()
    assert len(an.windows) == 1 and await pending_n(db, org) == 0


async def test_safety_candidate_runs_over_every_cap_and_alerts_ryan_once(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org = await org_id(db)
    await listen(db, org, "g1")
    await enable_groups(db, org)
    await set_ai_cost(db, org, intel_daily_call_cap=30, intel_safety_call_cap=1)
    m = await mk_model(db, org)
    for _ in range(30):
        await add_call(db, org, m, "refinery.group")                           # tổ chức 100%
    await add_call(db, org, m, "refinery.safety")                              # trần an toàn 100%
    await put(sm, org, msg("Em lỡ nhập mã OTP vào link đó rồi chị ơi", sender="u1"))
    an = Analyzer()
    router = LoggingRouter(sm, m)
    r = refinery(sm, redis, an, router)
    await r.run(org, "schedule")
    assert len(an.windows) == 1 and router.purposes == ["refinery.safety"]      # KHÔNG cắt lừa đảo/an toàn
    alerts = (await db.execute(text("SELECT alert_type, priority FROM biz.alerts"))).all()
    assert [(a.alert_type, a.priority) for a in alerts] == [("intel_safety_cap", "P1")]
    await put(sm, org, msg("Em lại lỡ nhập OTP vào link khác rồi", sender="u2", name="Anh Tư"))
    await r.run(org, "schedule")
    assert len(an.windows) == 2
    assert (await db.execute(text("SELECT count(*) FROM biz.alerts"))).scalar_one() == 1       # một lần trong ngày


async def test_gate_candidate_uses_gate_cap_then_falls_back_to_normal_queue(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org = await org_id(db)
    await listen(db, org, "g1")
    await enable_groups(db, org)
    await set_ai_cost(db, org, intel_daily_call_cap=1, intel_gate_call_cap=1)
    m = await mk_model(db, org)
    await add_call(db, org, m, "refinery.group")                               # tổ chức 100%
    await put(sm, org, msg("Em sẽ huỷ hợp đồng luôn nếu hôm nay không xử lý", sender="u1"))
    an = Analyzer()
    router = LoggingRouter(sm, m)
    r = refinery(sm, redis, an, router)
    await r.run(org, "schedule")
    assert router.purposes == ["refinery.gate"] and len(an.windows) == 1       # cổng chạy ngay dù tổ chức hết lượt chung
    await put(sm, org, msg("Em sẽ ngừng hợp tác nếu còn trễ nữa", sender="u2", name="Anh Tư"))
    await r.run(org, "schedule")
    assert len(an.windows) == 1 and await pending_n(db, org) == 1               # hết trần cổng + tổ chức hết ⇒ chỉ luật, tin chờ


async def test_free_mode_blocks_all_model_calls_and_alerts(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org = await org_id(db)
    await listen(db, org, "g1")
    await enable_groups(db, org)
    paid = await mk_model(db, org, kind="openrouter", price=(10_000.0, 40_000.0))
    await add_call(db, org, paid, "refinery.group", tin=1_000_000, tout=0)      # 10.000 ₫ lọt vào khi trần VND = 0
    await put(sm, org, msg("Em lỡ nhập mã OTP vào link đó rồi", sender="u1"), msg("Cần báo giá thép nhé anh", sender="u2", name="Anh Tư"))
    an = Analyzer()
    await refinery(sm, redis, an).run(org, "schedule")
    assert an.windows == [] and await pending_n(db, org) == 2                   # Q14: không chi tiền theo token cho BẤT KỲ lớp nào
    assert [a.alert_type for a in (await db.execute(text("SELECT alert_type FROM biz.alerts"))).all()] == ["intel_free_mode"]
    # Sếp đặt trần VND > 0 ⇒ thoát chế độ miễn phí
    await set_ai_cost(db, org, intel_daily_budget_vnd=1_000_000)
    await refinery(sm, redis, an).run(org, "manual")
    assert len(an.windows) >= 1 and await states(db, org) != {"pending": 2}


async def test_spam_of_safety_keywords_burns_at_most_six_model_calls_per_sender_hour(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org = await org_id(db)
    await listen(db, org, "g1")
    await enable_groups(db, org)
    m = await mk_model(db, org)
    an = Analyzer()
    router = LoggingRouter(sm, m)
    r = refinery(sm, redis, an, router)
    for i in range(30):                                                       # cùng một kẻ gửi, mỗi tin một lượt chạy ngay
        await put(sm, org, msg(f"Em lỡ nhập mã OTP vào link đó rồi {i}", sender="spam", name="Kẻ spam"))
        await r.run(org, "schedule")
    assert router.purposes.count("refinery.safety") == budget.SAFETY_PER_SENDER_HOUR
    cands = (await db.execute(text("""SELECT count(*) FROM refinery.event_state
                                      WHERE detail -> 'cands' @> '[{"flag": "credentials_entered"}]'"""))).scalar_one()
    assert cands == 30                                                        # luật cứng vẫn bắt đủ 30/30
    assert [a.alert_type for a in (await db.execute(text("SELECT alert_type FROM biz.alerts"))).all()].count("intel_safety_abuse") == 1
    # nạn nhân thật (người gửi khác) cùng luồng vẫn được chạy
    await put(sm, org, msg("Em cũng lỡ nhập OTP vào link đó rồi", sender="real", name="Nạn nhân"))
    await r.run(org, "schedule")
    assert router.purposes.count("refinery.safety") == budget.SAFETY_PER_SENDER_HOUR + 1
