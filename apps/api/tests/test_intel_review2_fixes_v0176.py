# ruff: noqa: E501
"""v0.1.76 (gói T1b) — vá nốt các điểm nhỏ của review T1 lần 2: thứ tự khoá cap → next_code (kể cả `record_legacy`), nâng khoá raw|/anon|
qua ranh giới ngày-trần, dòng lỗi của job đồng hồ không chiếm đầu hàng, `mark_sent` chỉ đánh dấu mục còn nguyên như lúc dựng, log job không
rò thông điệp lỗi, một vụ một 🔴 khi dòng raw| mở còn sót."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import digest, levels, signals
from tests.mem_helpers import mk_org
from tests.test_intel_core_signals_v0176 import NOW, _install, _li, _raw, _up


async def _rows(db: AsyncSession, org: uuid.UUID) -> list[Any]:
    return list((await db.execute(text("""SELECT id, kind, level, delivery, pushed_at, case_key, status FROM biz.alerts
                                          WHERE org_id = :o ORDER BY created_at"""), {"o": org})).all())


# ─── m1: record_legacy lấy khoá trần trước next_code ─────────────────────────

async def test_record_legacy_then_upsert_same_tx_no_deadlock(db: AsyncSession) -> None:
    from gh.db import sessionmaker

    org = await mk_org(db)
    await _install(db, org)
    r1, r2 = await _raw(db, org), await _raw(db, org)
    await db.commit()
    started = asyncio.Event()

    async def t1() -> str:
        async with sessionmaker()() as d:
            await signals.record_legacy(d, org, alert_type="slow_response", priority="P2", title="t", summary=None, now=NOW)
            started.set()
            await asyncio.sleep(0.5)
            await signals.upsert(d, org, _up("viec_han", _li("viec_han", due_in_h=30), r1, "viec_han:L1"), NOW)
            await d.commit()
            return "ok"

    async def t2() -> str:
        await started.wait()
        async with sessionmaker()() as d:
            await signals.upsert(d, org, _up("viec_han", _li("viec_han", due_in_h=30), r2, "viec_han:L2"), NOW)
            await d.commit()
            return "ok"

    assert await asyncio.wait_for(asyncio.gather(t1(), t2()), 30) == ["ok", "ok"]


# ─── m4: giữ thứ tự cap → next_code (mutation: bỏ cap_lock đầu upsert ⇒ test này đỏ) ──

async def test_cap_lock_before_next_code_merge_then_insert_vs_insert(db: AsyncSession) -> None:
    from gh.db import sessionmaker

    org = await mk_org(db)
    await _install(db, org)
    base_raw, r1, r2 = await _raw(db, org), await _raw(db, org), await _raw(db, org)
    await signals.upsert(db, org, _up("viec_han", _li("viec_han", due_in_h=30), base_raw, "viec_han:base"), NOW)
    await db.commit()

    async def t1() -> str:
        async with sessionmaker()() as d:
            await signals.upsert(d, org, _up("viec_han", _li("viec_han", due_in_h=29), await _raw(d, org), "viec_han:base"), NOW)  # gộp
            await asyncio.sleep(0.6)
            await signals.upsert(d, org, _up("viec_han", _li("viec_han", due_in_h=30), r1, "viec_han:new1"), NOW)                 # INSERT
            await d.commit()
            return "ok"

    async def t2() -> str:
        await asyncio.sleep(0.2)
        async with sessionmaker()() as d:
            await signals.upsert(d, org, _up("viec_han", _li("viec_han", due_in_h=30), r2, "viec_han:new2"), NOW)
            await d.commit()
            return "ok"

    assert await asyncio.wait_for(asyncio.gather(t1(), t2()), 30) == ["ok", "ok"]
    assert len(await _rows(db, org)) == 3


# ─── m2: nâng khoá raw|/anon| qua ranh giới ngày-trần ────────────────────────

async def test_rekey_race_across_cap_day_merges_into_strong_row(db: AsyncSession) -> None:
    from gh.db import sessionmaker

    org = await mk_org(db)
    await _install(db, org)
    a, b = await _raw(db, org, "a"), await _raw(db, org, "b")
    for x in (a, b):
        li = _li("rui_ro_lua_dao", hard_rule_hit=["credentials_entered"], ago_h=0.1)
        await signals.upsert(db, org, _up("rui_ro_lua_dao", li, x, levels.case_key("rui_ro_lua_dao", at=NOW, raw_event_id=str(x))), NOW)
    await db.commit()
    stk = levels.case_key("rui_ro_lua_dao", at=NOW, payee_digest="D1", group_id="G1")
    t_a, t_b = NOW.replace(hour=21, minute=59, second=59), NOW.replace(hour=22, minute=0, second=1)

    async def go(x: uuid.UUID, t: Any, delay: float) -> str:
        await asyncio.sleep(delay)
        async with sessionmaker()() as d:
            li = _li("rui_ro_lua_dao", ago_h=0.1, now=t, flags={"credentials_entered": True})
            await signals.upsert(d, org, _up("rui_ro_lua_dao", li, x, stk), t)
            await asyncio.sleep(0.5)
            await d.commit()
            return "ok"

    assert await asyncio.wait_for(asyncio.gather(go(a, t_a, 0), go(b, t_b, 0.1)), 30) == ["ok", "ok"]
    rows = await _rows(db, org)
    opened = [r for r in rows if r.status in ("open", "acknowledged")]
    assert len(opened) == 1 and opened[0].case_key == stk
    assert sum(1 for r in rows if r.pushed_at is not None and r.status in ("open", "acknowledged")) <= 1


# ─── ghi chú raw|: một vụ một 🔴 khi model khớp vụ mạnh còn dòng raw| mở ──────

async def test_model_path_absorbs_open_weak_row_with_same_raw(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    a, b = await _raw(db, org, "a"), await _raw(db, org, "b")
    stk = levels.case_key("rui_ro_lua_dao", at=NOW, payee_digest="D1", group_id="G1")
    li = _li("rui_ro_lua_dao", ago_h=0.1, flags={"credentials_entered": True})
    sb = await signals.upsert(db, org, _up("rui_ro_lua_dao", li, b, stk), NOW)                      # B mạnh trước
    sa = await signals.upsert(db, org, _up("rui_ro_lua_dao", _li("rui_ro_lua_dao", hard_rule_hit=["credentials_entered"], ago_h=0.1), a,
                                           levels.case_key("rui_ro_lua_dao", at=NOW, raw_event_id=str(a))), NOW)  # A đường nhanh
    assert sa.id != sb.id
    sm = await signals.upsert(db, org, _up("rui_ro_lua_dao", li, a, stk), NOW + timedelta(minutes=2))  # model A
    assert sm.id == sb.id
    rows = await _rows(db, org)
    opened = [r for r in rows if r.status in ("open", "acknowledged")]
    assert [r.id for r in opened] == [sb.id]
    assert sum(1 for r in opened if r.pushed_at is not None) <= 1
    weak = next(r for r in rows if r.id == sa.id)
    assert weak.status == "dismissed"


# ─── m3: dòng lỗi dời mốc; skip_locked không chờ khoá trần ───────────────────

async def test_reevaluate_due_poison_row_backs_off_and_does_not_starve(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    li = _li("nghia_vu_loi_hua", due_in_h=6, promise={"to_customer": True})
    bad = await signals.upsert(db, org, _up("nghia_vu_loi_hua", li, await _raw(db, org), "nghia_vu_loi_hua:promise|BAD"), NOW)
    good = await signals.upsert(db, org, _up("nghia_vu_loi_hua", li, await _raw(db, org), "nghia_vu_loi_hua:promise|GOOD"), NOW)
    await db.execute(text("""UPDATE biz.alerts SET level_input = level_input || '{"truong_la": 1}'::jsonb, next_eval_at = :t WHERE id = :i"""),
                     {"t": NOW, "i": bad.id})
    await db.commit()
    assert good.next_eval_at is not None
    seen: list[list[Any]] = []
    for k in range(3):
        seen.append([c.id for c in await signals.reevaluate_due(db, good.next_eval_at + timedelta(minutes=k), limit=1)])
    assert [good.id] in seen                               # dòng tốt cuối cùng cũng được tính lại
    ne = (await db.execute(text("SELECT next_eval_at FROM biz.alerts WHERE id = :i"), {"i": bad.id})).scalar_one()
    assert ne > good.next_eval_at + timedelta(minutes=2)


async def test_reevaluate_due_skips_row_when_cap_lock_busy(db: AsyncSession) -> None:
    from gh.db import sessionmaker

    org = await mk_org(db)
    await _install(db, org)
    li = _li("nghia_vu_loi_hua", due_in_h=6, promise={"to_customer": True})
    s = await signals.upsert(db, org, _up("nghia_vu_loi_hua", li, await _raw(db, org), "nghia_vu_loi_hua:promise|X"), NOW)
    await db.commit()
    assert s.next_eval_at is not None
    async with sessionmaker()() as holder:
        await signals.cap_lock(holder, org, s.next_eval_at)   # giữ khoá trần tới khi đóng
        out = await asyncio.wait_for(signals.reevaluate_due(db, s.next_eval_at), 5)
        assert out == []
    again = await signals.reevaluate_due(db, s.next_eval_at)  # nhả khoá ⇒ lượt sau tính được
    assert [c.id for c in again] == [s.id]


# ─── m5: mark_sent chỉ đánh dấu mục còn nguyên như lúc dựng ──────────────────

async def test_mark_sent_skips_item_whose_delivery_changed_after_build(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    li = _li("nghia_vu_loi_hua", due_in_h=11, impact_base=2, promise={"to_customer": True}, flags={"nudged": True})
    s = await signals.upsert(db, org, _up("nghia_vu_loi_hua", li, await _raw(db, org), "nghia_vu_loi_hua:promise|p1"), NOW)
    d1 = await digest.build(db, org, NOW + timedelta(hours=1))
    assert str(s.id) in [it.signal_id for it in d1.items]
    t = NOW + timedelta(hours=8, minutes=30)
    await signals.reevaluate_due(db, t)                    # leo lên nhóm đầu bản tin trước khi gửi xong
    cur = (await db.execute(text("SELECT delivery FROM biz.alerts WHERE id = :i"), {"i": s.id})).scalar_one()
    assert cur != s.delivery
    await digest.mark_sent(db, org, d1, t)
    assert (await db.execute(text("SELECT digest_at FROM biz.alerts WHERE id = :i"), {"i": s.id})).scalar_one() is None
    d2 = await digest.build(db, org, t + timedelta(hours=9))
    assert str(s.id) in [it.signal_id for it in d2.items]


async def test_mark_sent_still_marks_unchanged_items(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    li = _li("viec_han", due_in_h=30)
    s = await signals.upsert(db, org, _up("viec_han", li, await _raw(db, org), "viec_han:ok"), NOW)
    d = await digest.build(db, org, NOW)
    if not d.items:
        pytest.skip("không có mục bản tin cho tình huống này")
    assert await digest.mark_sent(db, org, d, NOW) >= 1
    assert (await db.execute(text("SELECT digest_at FROM biz.alerts WHERE id = :i"), {"i": s.id})).scalar_one() is not None


# ─── jobs: log raw_tail chỉ ghi tên loại lỗi ─────────────────────────────────

async def test_intel_reeval_raw_tail_log_has_no_message_or_traceback(db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
                                                                     caplog: pytest.LogCaptureFixture) -> None:
    from gh.biz.intel import jobs, raw_tail

    await mk_org(db)
    await db.commit()

    async def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("postgresql://user:SECRETPW@host/db detail")

    monkeypatch.setattr(raw_tail, "tail", boom)
    with caplog.at_level(logging.DEBUG):
        out = await jobs.intel_reeval({})
    assert out["errors"] >= 1
    recs = [r for r in caplog.records if r.name == "gh.intel.jobs"]
    assert recs and all(r.exc_info is None for r in recs)
    assert "SECRETPW" not in caplog.text and "Traceback" not in caplog.text
    assert "RuntimeError" in caplog.text
