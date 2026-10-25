# ruff: noqa: E501
"""v0.1.76 (gói T1) — sửa theo review: khoá trần lấy TRƯỚC khoá dòng (không deadlock, F1), job đồng hồ bền (F5), một vụ một 🔴 khi gộp
đường nhanh → đường model (F2) và giữa hai loại chung khoá cổng 3 (F6), "Đã xử lý" trên tín hiệu bị mute (F3), mục leo lên nhóm đầu bản
tin sau khi đã lên bản tin (F4), trần 0 (M6)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import digest, levels, mutes, signals
from tests.mem_helpers import mk_org
from tests.test_intel_core_signals_v0176 import NOW, _install, _li, _raw, _up


async def _rows(db: AsyncSession, org: uuid.UUID) -> list[Any]:
    return list((await db.execute(text("""SELECT id, kind, level, delivery, pushed_at, case_key, status FROM biz.alerts
                                          WHERE org_id = :o ORDER BY created_at"""), {"o": org})).all())


# ─── F1: khoá trần trên dòng ĐÃ CÓ (gộp, không qua next_code) ────────────────

async def test_intel_cap_reservation_atomic_on_existing_rows_merge(db: AsyncSession) -> None:
    """Đã 2/3 🔴 hôm nay; hai vụ ĐANG MỞ (chưa 🔴) cùng được gộp tin mới SONG SONG làm cả hai thành 🔴 ⇒ đúng MỘT push_now. Gộp không gọi
    `core.next_code`, nên chỉ khoá tư vấn trần xếp hàng hai transaction."""
    from gh.db import sessionmaker

    org = await mk_org(db)
    await _install(db, org)
    for i in range(2):
        li = _li("xung_dot_khieu_nai", customer_of_owner=True, flags={"public": True})
        await signals.upsert(db, org, _up("xung_dot_khieu_nai", li, await _raw(db, org), f"case3:pre{i}|x"), NOW)
    base = []
    for i in range(2):
        li = _li("nghia_vu_loi_hua", due_in_h=30, promise={"to_customer": True})
        s = await signals.upsert(db, org, _up("nghia_vu_loi_hua", li, await _raw(db, org), f"nghia_vu_loi_hua:promise|E{i}"), NOW)
        assert s.level != "red"
        base.append(s.id)
    raws = [await _raw(db, org) for _ in range(2)]
    await db.commit()

    async def one(i: int) -> signals.Signal:
        async with sessionmaker()() as s:
            li = _li("nghia_vu_loi_hua", due_in_h=3, promise={"to_customer": True})
            out = await signals.upsert(s, org, _up("nghia_vu_loi_hua", li, raws[i], f"nghia_vu_loi_hua:promise|E{i}"), NOW)
            await asyncio.sleep(0.3)                       # giữ transaction một lúc
            await s.commit()
            return out

    a, b = await asyncio.gather(one(0), one(1))
    assert {a.id, b.id} == set(base)                       # đúng là gộp, không tạo dòng mới
    assert sorted([a.delivery, b.delivery]) == ["digest_top", "push_now"]


async def test_intel_upsert_merge_and_reeval_in_parallel_do_not_deadlock(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Một lượt gộp vào vụ S2 đang chạy (đã khoá dòng S2) trong khi job đồng hồ tính lại S1 rồi S2 ⇒ cả hai xong, không DeadlockDetected."""
    from gh.db import sessionmaker

    org = await mk_org(db)
    await _install(db, org)
    s1 = await signals.upsert(db, org, _up("viec_han", _li("viec_han", due_in_h=30), await _raw(db, org), "viec_han:x"), NOW)
    s2 = await signals.upsert(db, org, _up("viec_han", _li("viec_han", due_in_h=30), await _raw(db, org), "viec_han:y"), NOW)
    await db.execute(text("UPDATE biz.alerts SET next_eval_at = :t WHERE id = :i"), {"t": NOW - timedelta(minutes=2), "i": s1.id})
    await db.execute(text("UPDATE biz.alerts SET next_eval_at = :t WHERE id = :i"), {"t": NOW - timedelta(minutes=1), "i": s2.id})
    raw = await _raw(db, org)
    await db.commit()

    locked = asyncio.Event()
    orig = signals.relane
    fired: list[bool] = []

    async def slow_relane(d: AsyncSession, o: uuid.UUID, sid: uuid.UUID, now: Any) -> signals.Signal:
        if sid == s2.id and not fired:                     # lượt gộp: đã khoá dòng S2, dừng một lúc trước relane
            fired.append(True)
            locked.set()
            await asyncio.sleep(0.5)
        return await orig(d, o, sid, now)

    monkeypatch.setattr(signals, "relane", slow_relane)

    async def merge() -> str:
        async with sessionmaker()() as d:
            await signals.upsert(d, org, _up("viec_han", _li("viec_han", due_in_h=20), raw, "viec_han:y"), NOW)
            await d.commit()
            return "ok"

    async def reeval() -> str:
        await locked.wait()
        async with sessionmaker()() as d:
            await signals.reevaluate_due(d, NOW)
            await d.commit()
            return "ok"

    res = await asyncio.wait_for(asyncio.gather(merge(), reeval(), return_exceptions=True), 30)
    assert res == ["ok", "ok"], res


# ─── F5: job đồng hồ bền ─────────────────────────────────────────────────────

async def test_intel_reeval_one_bad_row_does_not_block_other_org(db: AsyncSession) -> None:
    a, b = await mk_org(db, "A"), await mk_org(db, "B")
    await _install(db, a)
    await _install(db, b)
    li = _li("nghia_vu_loi_hua", due_in_h=6, promise={"to_customer": True})
    sa = await signals.upsert(db, a, _up("nghia_vu_loi_hua", li, await _raw(db, a), "nghia_vu_loi_hua:promise|A"), NOW)
    sb = await signals.upsert(db, b, _up("nghia_vu_loi_hua", li, await _raw(db, b), "nghia_vu_loi_hua:promise|B"), NOW)
    # dòng của A hỏng (đầu vào mức không hợp lệ) và tới mốc TRƯỚC dòng của B
    await db.execute(text("""UPDATE biz.alerts SET level_input = level_input || '{"truong_la": 1}'::jsonb, next_eval_at = :t
                             WHERE id = :i"""), {"t": NOW, "i": sa.id})
    await db.commit()
    assert sb.next_eval_at is not None
    changed = await signals.reevaluate_due(db, sb.next_eval_at)
    assert [c.id for c in changed] == [sb.id]
    assert (changed[0].level, changed[0].delivery) == ("red", "push_now")


async def test_intel_settings_bad_quiet_hours_fall_back_to_default(db: AsyncSession, caplog: pytest.LogCaptureFixture) -> None:
    org = await mk_org(db)
    await _install(db, org, quiet_start="7:00", quiet_end="25:99")
    st = await signals.delivery_state(db, org, NOW)
    assert (st.quiet_hours.start, st.quiet_hours.end) == ("22:00", "07:00")
    assert "7:00" not in caplog.text and "25:99" not in caplog.text
    li = _li("xung_dot_khieu_nai", customer_of_owner=True, flags={"public": True})
    s = await signals.upsert(db, org, _up("xung_dot_khieu_nai", li, await _raw(db, org), "case3:q|x"), NOW)
    assert s.level == "red"


async def test_intel_job_reeval_error_does_not_block_raw_tail(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    from gh.biz.intel import jobs, raw_tail

    org = await mk_org(db)
    await db.commit()
    seen: list[uuid.UUID] = []

    async def boom(*a: Any, **k: Any) -> list[signals.Signal]:
        raise RuntimeError("hỏng")

    async def fake_tail(d: AsyncSession, o: uuid.UUID, now: Any, **k: Any) -> list[signals.Signal]:
        seen.append(o)
        return []

    monkeypatch.setattr(signals, "reevaluate_due", boom)
    monkeypatch.setattr(raw_tail, "tail", fake_tail)
    out = await jobs.intel_reeval({})
    assert org in seen
    assert out["errors"] >= 1


# ─── F2: đường nhanh → đường model → tin sau cùng STK ─────────────────────────

async def test_intel_fast_path_then_model_upgrades_weak_key_one_red(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    a, b = await _raw(db, org, "a"), await _raw(db, org, "b")
    li = _li("rui_ro_lua_dao", hard_rule_hit=["credentials_entered"], ago_h=0.1)
    s1 = await signals.upsert(db, org, _up("rui_ro_lua_dao", li, a, levels.case_key("rui_ro_lua_dao", at=NOW, raw_event_id=str(a))), NOW)
    assert (s1.level, s1.delivery) == ("red", "push_now")
    stk = levels.case_key("rui_ro_lua_dao", at=NOW, payee_digest="D1", group_id="G1")
    t2 = NOW + timedelta(minutes=3)
    s2 = await signals.upsert(db, org, _up("rui_ro_lua_dao", _li("rui_ro_lua_dao", ago_h=0.1, now=t2, flags={"credentials_entered": True}), a, stk), t2)
    assert s2.id == s1.id and s2.case_key == stk           # khoá dự phòng raw| được nâng lên khoá mạnh
    t3 = NOW + timedelta(minutes=10)
    s3 = await signals.upsert(db, org, _up("rui_ro_lua_dao", _li("rui_ro_lua_dao", ago_h=0.05, now=t3, flags={"credentials_entered": True}), b, stk), t3)
    assert s3.id == s1.id
    rows = await _rows(db, org)
    assert len(rows) == 1 and sum(1 for r in rows if r.delivery == "push_now") <= 1
    assert s3.delivery == "merge"


# ─── F6: hai loại chung khoá cổng 3 ───────────────────────────────────────────

async def test_intel_case_prior_shared_across_kinds_same_case_key(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    ck = levels.case_key("xung_dot_khieu_nai", at=NOW, customer_entity_id="c1", reply_chain_id="ch")
    raw = await _raw(db, org)
    a = await signals.upsert(db, org, _up("xung_dot_khieu_nai", _li("xung_dot_khieu_nai", customer_of_owner=True, flags={"said_will_leave": True}), raw, ck), NOW)
    b = await signals.upsert(db, org, _up("quan_he_nong_nguoi", _li("quan_he_nong_nguoi", customer_of_owner=True, flags={"said_will_leave": True}), raw, ck), NOW)
    assert a.id != b.id
    assert (a.level, a.delivery) == ("red", "push_now")
    assert b.delivery == "merge"
    cap = (await db.execute(text("SELECT count(*) FROM biz.alerts WHERE org_id = :o AND cap_day IS NOT NULL"), {"o": org})).scalar_one()
    assert cap == 1                                        # một chỗ trần cho một vụ


# ─── F3: "Đã xử lý" trên tín hiệu bị mute ─────────────────────────────────────

async def test_intel_owner_resolve_on_muted_signal_closes_it(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    s = await signals.upsert(db, org, _up("viec_han", _li("viec_han", due_in_h=30), await _raw(db, org), "viec_han:m"), NOW)
    await mutes.create(db, org, scope_type="kind", scope_value="viec_han", source="chat", user_id=None)
    out = await signals.resolve_by_owner(db, org, s.id, now=NOW, actor_id="u")
    assert out.status == "resolved"


async def test_intel_muted_signal_closes_on_verified_done_message(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    s = await signals.upsert(db, org, _up("viec_han", _li("viec_han", due_in_h=30), await _raw(db, org), "viec_han:m2"), NOW)
    await mutes.create(db, org, scope_type="kind", scope_value="viec_han", source="chat", user_id=None)
    done = _li("viec_han", due_in_h=30, status="da_xu_ly")
    out = await signals.upsert(db, org, _up("viec_han", done, await _raw(db, org, "xong rồi"), "viec_han:m2"), NOW + timedelta(minutes=5))
    assert out.id == s.id and out.status == "resolved"


# ─── F4: digest_at không giấu mục leo lên nhóm đầu bản tin ────────────────────

async def test_intel_digest_item_escalated_after_sent_shows_again(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    li = _li("nghia_vu_loi_hua", due_in_h=11, impact_base=2, promise={"to_customer": True}, flags={"nudged": True})
    s = await signals.upsert(db, org, _up("nghia_vu_loi_hua", li, await _raw(db, org), "nghia_vu_loi_hua:promise|D4"), NOW)
    d1 = await digest.build(db, org, NOW + timedelta(hours=1))
    assert str(s.id) in [it.signal_id for it in d1.items]
    await digest.mark_sent(db, org, d1, NOW + timedelta(hours=1))
    t = NOW + timedelta(hours=8, minutes=30)              # 22:30 VN — giờ yên lặng, còn 2,5 giờ tới hạn
    await signals.reevaluate_due(db, t)
    r = (await db.execute(text("SELECT level, delivery FROM biz.alerts WHERE id = :i"), {"i": s.id})).one()
    assert (r.level, r.delivery) == ("red", "digest_morning")
    d2 = await digest.build(db, org, t + timedelta(hours=9))
    assert str(s.id) in [it.signal_id for it in d2.items]


# ─── M6: trần 0 ───────────────────────────────────────────────────────────────

async def test_intel_red_cap_zero_is_respected(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org, red_cap_per_day=0)
    li = _li("xung_dot_khieu_nai", customer_of_owner=True, flags={"public": True})
    s = await signals.upsert(db, org, _up("xung_dot_khieu_nai", li, await _raw(db, org), "case3:cap0|x"), NOW)
    assert (s.level, s.delivery) == ("amber", "digest_top") and "capped" in s.reasons
