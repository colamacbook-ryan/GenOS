# ruff: noqa: E501
"""v0.1.77 (P4, lỗi chạy thật, có từ v0.1.76): tin 🔴 đã quyết đẩy (`pushed_at`) mất hẳn khi lượt đánh giá sau trên CHÍNH dòng đó
(intel_fraud + intel_reeval cùng phút, hay tin mới của vụ) ghi `delivery='merge'` trước khi `intel_delivery` kịp xếp — vì `push_alerts`
lọc theo `delivery='push_now'`. `merge` trên đầu vụ đã đẩy là ĐÚNG spec (e) "một vụ một 🔴" (test core_signals/t14 D1 khoá); ý định đẩy phải
đọc từ `pushed_at` + khoá dedupe, không từ `delivery` đang bị ghi đè."""

from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels, raw_tail, signals
from gh.biz.intel.pipes import delivery
from gh.db import sessionmaker as SM
from tests.mem_helpers import mk_org
from tests.owner_helpers import bot_world
from tests.test_intel_digest_rawtail_v0176 import NOW, _install, _raw, _sig, _state


def _fraud(db: AsyncSession, org: uuid.UUID, ck: str = "rui_ro_lua_dao:stk|111", **kw: Any) -> Any:
    return _sig(db, org, "rui_ro_lua_dao", ck, flags={"credentials_entered": True}, **kw)


async def _row(db: AsyncSession, sid: uuid.UUID) -> Any:
    return (await db.execute(text("SELECT delivery, reasons, pushed_at, level, gate FROM biz.alerts WHERE id = :i"), {"i": sid})).one()


async def _alerts(db: AsyncSession) -> list[Any]:
    return list((await db.execute(text("SELECT refs, dedupe_key FROM ops.owner_outbox WHERE kind = 'alert' ORDER BY created_at"))).all())


async def _reeval(db: AsyncSession, org: uuid.UUID, sid: uuid.UUID, now: datetime, by_clock: bool) -> None:
    """Lượt đánh giá lại; `by_clock` (job intel_reeval) chỉ có từ v0.1.77 — giữ test chạy được trên tag v0.1.76 để đối chiếu."""
    kw = {"by_clock": by_clock} if "by_clock" in inspect.signature(signals._relane).parameters else {}
    await signals._relane(db, org, sid, now, skip_locked=False, **kw)


async def _push(org: uuid.UUID, now: datetime = NOW) -> int:
    async with SM()() as s:
        n = await delivery.push_alerts(s, org, now)
        await s.commit()
    return n


async def test_case_head_reeval_before_delivery_still_sends_its_red_once(db: AsyncSession) -> None:
    """(i) A: đầu vụ 🔴 duy nhất bị đánh giá lại (thường + theo đồng hồ) TRƯỚC lượt intel_delivery ⇒ `merge` theo spec, pushed_at giữ nguyên,
    nhưng tin 🔴 của chính lần đẩy đó vẫn được xếp đúng 1 lần."""
    org = await mk_org(db)
    await _install(db, org)
    s = await _fraud(db, org)
    assert (s.level, s.delivery) == ("red", "push_now")
    first = await _row(db, s.id)
    for later, by_clock in ((NOW + timedelta(seconds=20), False), (NOW + timedelta(minutes=1), True), (NOW + timedelta(minutes=5), False)):
        await _reeval(db, org, s.id, later, by_clock)
        r = await _row(db, s.id)
        assert (r.level, r.delivery) == ("red", "merge") and r.pushed_at == first.pushed_at, (later, r)   # không đẩy lại (không leo thang)
    await db.commit()
    await bot_world(db, org)
    assert await _push(org, NOW + timedelta(minutes=6)) == 1
    assert await _push(org, NOW + timedelta(minutes=7)) == 0                 # khoá dedupe theo pushed_at
    await _reeval(db, org, s.id, NOW + timedelta(minutes=8), True)
    await db.commit()
    assert await _push(org, NOW + timedelta(minutes=9)) == 0                 # đã gửi rồi thì đánh giá lại không gửi thêm
    assert [a.refs[0]["signal_id"] for a in await _alerts(db)] == [str(s.id)]


async def test_fraud_and_reeval_same_minute_send_exactly_one_red(db: AsyncSession) -> None:
    """(ii) B: raw_tail (intel_reeval) + intel_fraud đánh giá CÙNG tin gốc trong cùng phút ⇒ outbox đúng 1 tin 🔴 dù delivery cuối là merge."""
    org = await mk_org(db)
    await _install(db, org)
    g = uuid.uuid4()
    now = datetime.now(levels.VN)
    raw, recv = await _raw(db, org, "em lỡ nhập OTP rồi", group=g, at=now - timedelta(minutes=2))
    cand = {"flag": "credentials_entered", "field": "body", "span": [3, 17], "kind": "rui_ro_lua_dao", "gate": "lua_dao_dang_dien_ra",
            "hard": "credentials_entered", "first_person": True}
    await _state(db, org, raw, recv, [cand], f"g:{g}")
    [t] = await raw_tail.tail(db, org, now)
    li = levels.SignalIn(kind="rui_ro_lua_dao", msg_at=now - timedelta(minutes=2), flags=levels.Flags(credentials_entered=True),
                         victims=[levels.PersonRel(ref="u_x", team=True)], harm_what="mất tiền", confidence_exist=1.0,
                         thread=levels.Thread(size=10, owner_in_thread=True))
    s2 = await signals.upsert(db, org, signals.UpsertIn(
        kind="rui_ro_lua_dao", title="t", detector="fraud", level_input=li, case_key="rui_ro_lua_dao:stk|zzz",
        evidence=[signals.EvidenceIn(type="raw_event", id=raw, source_field="body", span_start=3, span_end=17)]), now + timedelta(seconds=5))
    assert s2.id == t.id
    await _reeval(db, org, t.id, now + timedelta(seconds=30), True)   # intel_reeval cùng phút
    r = await _row(db, t.id)
    assert (r.level, r.gate) == ("red", levels.G1) and r.pushed_at is not None, r
    await db.commit()
    await bot_world(db, org)
    assert await _push(org, now + timedelta(minutes=1)) == 1
    assert await _push(org, now + timedelta(minutes=2)) == 0                 # không trùng
    assert [a.refs[0]["signal_id"] for a in await _alerts(db)] == [str(t.id)]


async def test_fraud_upserted_twice_same_second_sends_once(db: AsyncSession) -> None:
    """(ii′) hai lần upsert cùng khoá vụ trong cùng giây (hai job) ⇒ một dòng, một tin 🔴."""
    org = await mk_org(db)
    await _install(db, org)
    a = await _fraud(db, org)
    b = await _fraud(db, org)
    assert a.id == b.id and b.pushed_at is not None
    await db.commit()
    await bot_world(db, org)
    assert await _push(org) == 1 and len(await _alerts(db)) == 1


@pytest.mark.usefixtures("regular_push_on")
async def test_second_signal_same_case_still_merges_and_escalation_still_repushes(db: AsyncSession) -> None:
    """(iii) Thiết kế giữ nguyên: thẻ KHÁC cùng case_key gộp vào vụ đã đẩy (`merge`, không pushed_at ⇒ KHÔNG có tin); leo thang (nạn nhân
    mới) trên chính đầu vụ vẫn đẩy thêm đúng một tin."""
    org = await mk_org(db)
    await _install(db, org)
    head = await _sig(db, org, "xung_dot_khieu_nai", "vu-1", customer_of_owner=True, impact_base=2, flags={"public": True})
    assert head.delivery == "push_now"
    other = await _sig(db, org, "quan_he_nong_nguoi", "vu-1", customer_of_owner=True, impact_base=2, flags={"public": True})
    assert other.id != head.id
    ro = await _row(db, other.id)
    assert ro.delivery == "merge" and "merged" in ro.reasons, ro
    assert ro.pushed_at is None
    # leo thang G1 trên chính đầu vụ: nạn nhân mới ⇒ pushed_at mới ⇒ tin 🔴 thứ hai
    f = await _fraud(db, org, ck="rui_ro_lua_dao:stk|esc")
    p0 = (await _row(db, f.id)).pushed_at
    await db.commit()
    await bot_world(db, org)
    assert await _push(org, NOW + timedelta(minutes=2)) == 2                 # đầu vụ khiếu nại + lừa đảo; thẻ merge không đẩy
    raw, _ = await _raw(db, org)
    li = levels.SignalIn.model_validate({"kind": "rui_ro_lua_dao", "privacy": "chat_ok", "msg_at": NOW - timedelta(hours=1), "harm_what": "thiệt hại",
                                         "thread": {"type": "group", "size": 20, "owner_in_thread": True}, "source_class": "insider",
                                         "victims": [{"ref": "v", "team": True}, {"ref": "v2", "team": True}], "confidence_exist": 1.0,
                                         "confidence_content": 0.8, "flags": {"credentials_entered": True}})
    f2 = await signals.upsert(db, org, signals.UpsertIn(kind="rui_ro_lua_dao", title="esc", detector="t", level_input=li, case_key="rui_ro_lua_dao:stk|esc",
                                                        evidence=[signals.EvidenceIn(type="raw_event", id=raw, source_field="body", span_start=0, span_end=3,
                                                                                     quote="abc")]), NOW + timedelta(minutes=10))
    rf = await _row(db, f2.id)
    assert f2.id == f.id and rf.delivery == "push_now" and "escalation" in rf.reasons and rf.pushed_at > p0, rf
    await db.commit()
    assert await _push(org, NOW + timedelta(minutes=11)) == 1                 # leo thang = một tin 🔴 mới (khoá theo pushed_at mới)
    assert str(other.id) not in str([a.refs for a in await _alerts(db)])


async def test_old_merge_row_owing_a_red_is_not_sent_late_after_upgrade(db: AsyncSession) -> None:
    """Dòng `merge` + `pushed_at` cũ hơn MERGE_CATCHUP_H (bản cũ đã làm mất tin) không gửi muộn ồ ạt; trong trần thì vẫn gửi bù."""
    org = await mk_org(db)
    await _install(db, org)
    old = await _fraud(db, org, ck="rui_ro_lua_dao:stk|old")
    fresh = await _fraud(db, org, ck="rui_ro_lua_dao:stk|fresh")
    for sid, h in ((old.id, delivery.MERGE_CATCHUP_H + 1), (fresh.id, delivery.MERGE_CATCHUP_H - 1)):
        await db.execute(text("UPDATE biz.alerts SET delivery = 'merge', pushed_at = CAST(:t AS timestamptz) WHERE id = :i"),
                         {"t": NOW - timedelta(hours=h), "i": sid})
    await db.commit()
    await bot_world(db, org)
    assert await _push(org) == 1
    assert [a.refs[0]["signal_id"] for a in await _alerts(db)] == [str(fresh.id)]
