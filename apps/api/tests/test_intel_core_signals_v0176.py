# ruff: noqa: E501
"""v0.1.76 (gói T1) — `signals.upsert`/`relane`/`reevaluate_due` trên PostgreSQL thật. Chứng minh ba điểm yếu G-KB:
(a) một sự việc = MỘT tín hiệu (gộp theo `case_key`, gộp đường nhanh với đường model theo tin gốc), không lặp 🔴 cùng vụ;
(b) có xác nhận đã xong ⇒ đóng/hạ (`da_xu_ly` ⇒ ⚪ none, A16; "Đã xử lý" của Sếp; cổng thôi thoả ⇒ hạ);
(c) mức tính lại THEO ĐỒNG HỒ: hạn tới gần ⇒ leo cổng 4 (Q16); quá 24 giờ ⇒ 🟠 đứng đầu bản tin (Q15)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import asks, levels, mutes, signals
from tests.mem_helpers import mk_org

NOW = datetime(2026, 10, 23, 14, 0, tzinfo=levels.VN)


async def _install(db: AsyncSession, org: uuid.UUID, **intel: Any) -> None:
    import orjson
    s = {"installed_at": "2026-09-01T00:00:00+07:00", "m1_passed_gates": list(levels.REGULAR_GATES), **intel}
    await db.execute(text("UPDATE core.organizations SET settings = settings || jsonb_build_object('intel', CAST(:s AS jsonb)) WHERE id = :o"),
                     {"s": orjson.dumps(s).decode(), "o": org})


async def _raw(db: AsyncSession, org: uuid.UUID, body: str = "khách chê") -> uuid.UUID:
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o LIMIT 1"), {"o": org})).scalar_one_or_none()
    if ch is None:
        ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', 'z') RETURNING id"), {"o": org})).scalar_one()
    return (await db.execute(text("""INSERT INTO raw.events (org_id, occurred_at, channel_id, external_msg_id, kind, body_text, payload, content_hash)
                                     VALUES (:o, now(), :c, :x, 'text', :b, '{}', '\\x01') RETURNING id"""),
                             {"o": org, "c": ch, "x": uuid.uuid4().hex, "b": body})).scalar_one()  # type: ignore[no-any-return]


def _ev(raw: uuid.UUID, quote: str = "lần sau lấy chỗ khác") -> signals.EvidenceIn:
    return signals.EvidenceIn(type="raw_event", id=raw, source_field="body", span_start=0, span_end=len(quote), quote=quote)


def _li(kind: str, *, ago_h: float = 1.0, due_in_h: float | None = None, now: datetime = NOW, **kw: Any) -> levels.SignalIn:
    data: dict[str, Any] = {"kind": kind, "msg_at": now - timedelta(hours=ago_h), "event_at": now - timedelta(hours=ago_h),
                            "deadline_at": None if due_in_h is None else now + timedelta(hours=due_in_h),
                            "harm_what": "mất khách hôm nay", "thread": {"type": "group", "size": 20, "owner_in_thread": True},
                            "victims": [{"ref": "v1", "team": True}], "source_class": "insider", "confidence_exist": 1.0,
                            "confidence_content": 0.765}
    data.update(kw)
    return levels.SignalIn.model_validate(data)


def _up(kind: str, li: levels.SignalIn, raw: uuid.UUID, case_key: str, **kw: Any) -> signals.UpsertIn:
    return signals.UpsertIn(kind=kind, title="Tín hiệu thử", detector="test", level_input=li, evidence=[_ev(raw)], case_key=case_key,
                            thread_key=f"g:{uuid.uuid4()}", **kw)


async def _count(db: AsyncSession, org: uuid.UUID) -> int:
    return int((await db.execute(text("SELECT count(*) FROM biz.alerts WHERE org_id = :o"), {"o": org})).scalar_one())


# ─── trích dẫn chưa kiểm ──────────────────────────────────────────────────────

async def test_intel_core_upsert_rejects_unverified_quotes(db: AsyncSession) -> None:
    org = await mk_org(db)
    raw = await _raw(db, org)
    li = _li("xung_dot_khieu_nai", customer_of_owner=True, flags={"public": True})
    bad = [signals.EvidenceIn(type="raw_event", id=raw),                                            # thiếu span do code cắt
           signals.EvidenceIn(type="raw_event", id=raw, source_field="body", span_start=0, span_end=5, quote_status="absent"),
           signals.EvidenceIn(type="meaning_unit", id=uuid.uuid4())]                                # không có clean.evidence verified
    for ev in bad:
        with pytest.raises(signals.UnverifiedQuote):
            await signals.upsert(db, org, signals.UpsertIn(kind="xung_dot_khieu_nai", title="t", detector="t", level_input=li,
                                                           evidence=[ev], case_key="k"), NOW)
    with pytest.raises(signals.UnverifiedQuote):
        await signals.upsert(db, org, signals.UpsertIn(kind="xung_dot_khieu_nai", title="t", detector="t", level_input=li,
                                                       evidence=[], case_key="k"), NOW)
    assert await _count(db, org) == 0


async def test_intel_core_upsert_raw_event_of_other_org_is_rejected(db: AsyncSession) -> None:
    a, b = await mk_org(db, "A"), await mk_org(db, "B")
    raw_b = await _raw(db, b)
    with pytest.raises(signals.UnverifiedQuote):
        await signals.upsert(db, a, _up("xung_dot_khieu_nai", _li("xung_dot_khieu_nai"), raw_b, "k"), NOW)


async def test_intel_core_quote_display_is_masked_and_short(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    raw = await _raw(db, org)
    q = "chuyển vào STK 0123456789012 giúp em nha, gấp lắm, " + "x" * 100
    up = signals.UpsertIn(kind="rui_ro_lua_dao", title="t", detector="t", case_key="fr:1",
                          level_input=_li("rui_ro_lua_dao"), evidence=[signals.EvidenceIn(type="raw_event", id=raw, source_field="body",
                                                                                           span_start=0, span_end=20, quote=q)])
    sig = await signals.upsert(db, org, up, NOW)
    ev = (await db.execute(text("SELECT evidence FROM biz.alerts WHERE id = :i"), {"i": sig.id})).scalar_one()
    disp = ev[0]["quote_display"]
    assert "0123456789012" not in disp and len(disp) <= 80 and "quote" not in ev[0]


# ─── (a) một sự việc = một tín hiệu, không lặp 🔴 ─────────────────────────────

async def test_intel_case_key_same_case_merges_one_signal_no_repeat_red(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    li = _li("xung_dot_khieu_nai", customer_of_owner=True, flags={"public": True})
    s1 = await signals.upsert(db, org, _up("xung_dot_khieu_nai", li, await _raw(db, org), "case3:c1|chain"), NOW)
    assert (s1.level, s1.gate, s1.delivery, s1.created) == ("red", levels.G3, "push_now", True)
    pushed = s1.pushed_at
    later = NOW + timedelta(minutes=20)
    li2 = _li("xung_dot_khieu_nai", ago_h=0.1, now=later, customer_of_owner=True, flags={"public": True})
    s2 = await signals.upsert(db, org, _up("xung_dot_khieu_nai", li2, await _raw(db, org, "chê lần nữa"), "case3:c1|chain"), later)
    assert s2.id == s1.id and not s2.created
    assert (s2.level, s2.delivery) == ("red", "merge") and "merged" in s2.reasons      # không đẩy 🔴 lần hai
    assert s2.pushed_at == pushed
    assert await _count(db, org) == 1
    n_ev = (await db.execute(text("SELECT evidence_n FROM biz.alerts WHERE id = :i"), {"i": s1.id})).scalar_one()
    assert n_ev == 2                                                                   # bằng chứng được gắn thêm
    assert (await db.execute(text("SELECT count(*) FROM biz.alerts WHERE org_id = :o AND cap_day IS NOT NULL"), {"o": org})).scalar_one() == 1


async def test_intel_case_key_escalation_repushes_once(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    li = _li("xung_dot_khieu_nai", customer_of_owner=True, flags={"public": True})
    s1 = await signals.upsert(db, org, _up("xung_dot_khieu_nai", li, await _raw(db, org), "case3:c9|x"), NOW)
    later = NOW + timedelta(hours=1)
    li2 = _li("xung_dot_khieu_nai", ago_h=0.1, now=later, customer_of_owner=True, flags={"public": True, "said_will_leave": True})
    s2 = await signals.upsert(db, org, _up("xung_dot_khieu_nai", li2, await _raw(db, org, "thôi huỷ đơn"), "case3:c9|x"), later)
    assert s2.id == s1.id and s2.delivery == "push_now" and "escalation" in s2.reasons and s2.pushed_at == later
    s3 = await signals.upsert(db, org, _up("xung_dot_khieu_nai", li2, await _raw(db, org, "huỷ thật"), "case3:c9|x"), later + timedelta(minutes=5))
    assert s3.delivery == "merge"                                                      # leo thang chỉ đẩy lại MỘT lần


async def test_intel_case_key_fast_path_and_model_path_share_raw_event(db: AsyncSession) -> None:
    """Đường nhanh (luật, khoá theo tin gốc) và đường model (khoá theo STK) cùng một tin ⇒ MỘT tín hiệu, không băm hai mục."""
    org = await mk_org(db)
    await _install(db, org)
    raw = await _raw(db, org, "em lỡ nhập OTP rồi")
    rule = _li("rui_ro_lua_dao", hard_rule_hit=["credentials_entered"], source_class="rule", harm_what="")
    s1 = await signals.upsert(db, org, _up("rui_ro_lua_dao", rule, raw, f"rui_ro_lua_dao:raw|{raw}"), NOW)
    model = _li("rui_ro_lua_dao", flags={"credentials_entered": True})
    up = signals.UpsertIn(kind="rui_ro_lua_dao", title="t", detector="fraud", level_input=model, case_key="rui_ro_lua_dao:stk|abc|g|x",
                          evidence=[_ev(raw), signals.EvidenceIn(type="raw_event", id=await _raw(db, org, "link lạ"), source_field="body",
                                                                 span_start=0, span_end=4)])
    s2 = await signals.upsert(db, org, up, NOW + timedelta(minutes=3))
    assert s2.id == s1.id and await _count(db, org) == 1
    assert s1.delivery == "push_now" and s2.delivery == "merge"


async def test_intel_case_key_two_kinds_same_event_are_two_signals(db: AsyncSession) -> None:
    """§4B.1: khiếu nại kèm "lần sau lấy chỗ khác" sinh xung_dot_khieu_nai VÀ quan_he_nong_nguoi (khoá (case_key, kind))."""
    org = await mk_org(db)
    await _install(db, org)
    raw = await _raw(db, org)
    ck = levels.case_key("xung_dot_khieu_nai", at=NOW, customer_entity_id="c1", reply_chain_id="r")
    assert ck == levels.case_key("quan_he_nong_nguoi", at=NOW, customer_entity_id="c1", reply_chain_id="r")
    a = await signals.upsert(db, org, _up("xung_dot_khieu_nai", _li("xung_dot_khieu_nai", customer_of_owner=True, flags={"public": True}), raw, ck), NOW)
    b = await signals.upsert(db, org, _up("quan_he_nong_nguoi", _li("quan_he_nong_nguoi", customer_of_owner=True,
                                                                       flags={"said_will_leave": True}), raw, ck), NOW)
    assert a.id != b.id and await _count(db, org) == 2


# ─── (b) đóng / hạ khi có xác nhận ────────────────────────────────────────────

async def test_intel_close_da_xu_ly_goes_grey_none_and_closes_ask(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    li = _li("nghia_vu_loi_hua", due_in_h=3, promise={"to_customer": True})
    up = _up("nghia_vu_loi_hua", li, await _raw(db, org, "chiều em giao"), "nghia_vu_loi_hua:promise|P1",
             suggested_action="Nhắc NV giao hàng", action_executable=True)
    s1 = await signals.upsert(db, org, up, NOW)
    assert (s1.level, s1.gate, s1.lane) == ("red", levels.G4, "decide") and s1.ask_id is not None
    done = li.model_copy(update={"status": "da_xu_ly", "promise": levels.Promise(to_customer=True, completed=True)})
    up2 = _up("nghia_vu_loi_hua", done, await _raw(db, org, "em giao xong rồi"), "nghia_vu_loi_hua:promise|P1")
    s2 = await signals.upsert(db, org, up2, NOW + timedelta(minutes=30))
    assert s2.id == s1.id and (s2.level, s2.delivery, s2.status) == ("grey", "none", "resolved") and "closed" in s2.reasons
    a = await asks.get(db, org, s1.ask_id)
    assert a is not None and a.state == "expired"
    assert s2.next_eval_at is None


async def test_intel_close_owner_resolved_fraud_is_only_exit(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    li = _li("rui_ro_lua_dao", flags={"credentials_entered": True}, status="da_xu_ly")      # status_hint model ⇒ bị bỏ qua
    s1 = await signals.upsert(db, org, _up("rui_ro_lua_dao", li, await _raw(db, org), "fr:k"), NOW)
    assert s1.level == "red"
    s2 = await signals.resolve_by_owner(db, org, s1.id, now=NOW + timedelta(minutes=5), actor_id="u:owner")
    assert (s2.level, s2.delivery, s2.status) == ("grey", "none", "resolved")


async def test_intel_demote_when_gate_no_longer_satisfied(db: AsyncSession) -> None:
    """Cổng thôi thoả (khách hoãn hạn sang tuần sau) ⇒ tín hiệu bị hạ khỏi 🔴."""
    org = await mk_org(db)
    await _install(db, org)
    li = _li("nghia_vu_loi_hua", due_in_h=2, promise={"to_customer": True})
    s1 = await signals.upsert(db, org, _up("nghia_vu_loi_hua", li, await _raw(db, org), "nghia_vu_loi_hua:promise|P2"), NOW)
    assert s1.level == "red"
    moved = li.model_copy(update={"deadline_at": NOW + timedelta(days=6)})
    s2 = await signals.upsert(db, org, _up("nghia_vu_loi_hua", moved, await _raw(db, org, "để tuần sau"), "nghia_vu_loi_hua:promise|P2"),
                              NOW + timedelta(minutes=10))
    assert s2.id == s1.id and s2.level != "red" and s2.gate is None


async def test_intel_feedback_owner_correct_beats_system_reject(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    s1 = await signals.upsert(db, org, _up("xung_dot_khieu_nai", _li("xung_dot_khieu_nai", customer_of_owner=True, flags={"public": True}),
                                           await _raw(db, org), "k-fb"), NOW)
    await signals.feedback(db, org, s1.id, verdict="correct", verdict_by="owner", now=NOW)
    s2 = await signals.feedback(db, org, s1.id, verdict="wrong", verdict_by="system", now=NOW)
    assert s2.status == "open"
    s3 = await signals.feedback(db, org, s1.id, verdict="wrong", verdict_by="owner", now=NOW)
    assert s3.status == "dismissed"
    n = (await db.execute(text("SELECT count(*) FROM biz.signal_feedback WHERE signal_id = :i"), {"i": s1.id})).scalar_one()
    assert n == 3


# ─── (c) tính lại theo đồng hồ ────────────────────────────────────────────────

async def test_intel_reeval_clock_promise_escalates_to_red_without_new_message(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    li = _li("nghia_vu_loi_hua", due_in_h=6, promise={"to_customer": True})
    s1 = await signals.upsert(db, org, _up("nghia_vu_loi_hua", li, await _raw(db, org), "nghia_vu_loi_hua:promise|P3"), NOW)
    assert s1.level != "red" and s1.next_eval_at is not None and s1.next_eval_at <= NOW + timedelta(hours=2, seconds=1)
    assert await signals.reevaluate_due(db, NOW + timedelta(hours=1)) == []                # chưa tới mốc
    changed = await signals.reevaluate_due(db, s1.next_eval_at)
    assert [c.id for c in changed] == [s1.id]
    assert (changed[0].level, changed[0].gate, changed[0].delivery) == ("red", levels.G4, "push_now")   # Q16: còn ≤ 4 giờ ⇒ 🔴


async def test_intel_reeval_clock_q15_complaint_over_24h_goes_digest_top(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    li = _li("xung_dot_khieu_nai", ago_h=20, customer_of_owner=True, flags={"public": True})
    s1 = await signals.upsert(db, org, _up("xung_dot_khieu_nai", li, await _raw(db, org), "case3:q15|x"), NOW)
    assert s1.level == "red" and s1.next_eval_at == NOW + timedelta(hours=4)
    changed = await signals.reevaluate_due(db, NOW + timedelta(hours=4))
    assert len(changed) == 1
    c = changed[0]
    assert (c.level, c.candidate, c.delivery) == ("amber", levels.G3, "digest_top") and "window_expired" in c.reasons


async def test_intel_reeval_ignores_closed_and_other_org(db: AsyncSession) -> None:
    a, b = await mk_org(db, "A"), await mk_org(db, "B")
    await _install(db, a)
    await _install(db, b)
    li = _li("nghia_vu_loi_hua", due_in_h=6, promise={"to_customer": True})
    sa = await signals.upsert(db, a, _up("nghia_vu_loi_hua", li, await _raw(db, a), "P"), NOW)
    sb = await signals.upsert(db, b, _up("nghia_vu_loi_hua", li, await _raw(db, b), "P"), NOW)
    await signals.transition(db, b, sb.id, "dismissed", now=NOW)
    changed = await signals.reevaluate_due(db, NOW + timedelta(hours=3), org_id=a)
    assert [c.id for c in changed] == [sa.id]
    assert (await signals.get(db, b, sb.id)).status == "dismissed"  # type: ignore[union-attr]
    assert await signals.get(db, a, sb.id) is None                  # IDOR: không đọc chéo tổ chức


# ─── trần nguyên tử, bóng, mute ───────────────────────────────────────────────

async def test_intel_cap_three_reds_per_day_then_capped_fraud_uncapped(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    out = []
    for i in range(4):
        li = _li("xung_dot_khieu_nai", customer_of_owner=True, flags={"public": True})
        out.append(await signals.upsert(db, org, _up("xung_dot_khieu_nai", li, await _raw(db, org), f"case3:c{i}|x"), NOW))
    assert [s.delivery for s in out] == ["push_now"] * 3 + ["digest_top"]
    assert out[3].level == "amber" and out[3].gate == levels.G3 and out[3].capped_at is not None
    fr = await signals.upsert(db, org, _up("rui_ro_lua_dao", _li("rui_ro_lua_dao", flags={"credentials_entered": True}),
                                           await _raw(db, org), "fr:z"), NOW)
    assert fr.delivery == "push_now"                                                   # bypass_all


async def test_intel_ramp_shadow_records_would_push_at(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org, installed_at=(NOW - timedelta(days=3)).isoformat())
    li = _li("xung_dot_khieu_nai", customer_of_owner=True, flags={"public": True})
    s = await signals.upsert(db, org, _up("xung_dot_khieu_nai", li, await _raw(db, org), "case3:r|x"), NOW)
    assert (s.level, s.delivery, s.would_push_at, s.pushed_at) == ("red", "digest_top", NOW, None)


async def test_intel_mutes_chat_cannot_silence_fraud_pin_can(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    with pytest.raises(mutes.MuteError):
        await mutes.create(db, org, scope_type="kind", scope_value="rui_ro_lua_dao", source="chat", user_id=None)
    g = uuid.uuid4()
    await mutes.create(db, org, scope_type="group", scope_value=str(g), source="chat", user_id=None)
    up = _up("rui_ro_lua_dao", _li("rui_ro_lua_dao", flags={"credentials_entered": True}), await _raw(db, org), "fr:m", group_id=g)
    s = await signals.upsert(db, org, up, NOW)
    assert s.level == "red" and "mute_chat_ignored" in s.reasons
    await mutes.create(db, org, scope_type="group", scope_value=str(g), source="pin", user_id=None)
    s2 = await signals.relane(db, org, s.id, NOW + timedelta(minutes=1))
    assert (s2.level, s2.delivery) == ("grey", "digest_low")
    up3 = _up("xung_dot_khieu_nai", _li("xung_dot_khieu_nai", customer_of_owner=True, flags={"public": True}), await _raw(db, org),
              "c3m", group_id=g)
    assert (await signals.upsert(db, org, up3, NOW)).delivery == "none"


# ─── chiếu mem.disputes, owner_fact_ids ───────────────────────────────────────

async def test_intel_project_disputes_once(db: AsyncSession) -> None:
    from tests.mem_helpers import mk_entity

    org = await mk_org(db)
    await _install(db, org)
    e = await mk_entity(db, org, "NCC Hạnh")
    raw = await _raw(db, org)
    fid = (await db.execute(text("""INSERT INTO mem.facts (org_id, subject_id, predicate, object_value, object_key, valid_from, status,
                                     is_single, source_class, source_reliability, confidence_exist, confidence_content, created_by)
                                    VALUES (:o, :s, 'payment_account', '"x"', 'dg1', now(), 'disputed', true, 'insider', 0.9, 0.9, 0.8,
                                            'extractor') RETURNING id"""), {"o": org, "s": e})).scalar_one()
    await db.execute(text("""INSERT INTO mem.fact_evidence (org_id, fact_id, kind, raw_event_id, quote_status)
                             VALUES (:o, :f, 'raw_event', :r, 'verified')"""), {"o": org, "f": fid, "r": raw})
    await db.execute(text("""INSERT INTO mem.disputes (org_id, subject_id, predicate, fact_ids, dispute_signal_kind)
                             VALUES (:o, :s, 'payment_account', ARRAY[CAST(:f AS uuid)], 'rui_ro_lua_dao')"""), {"o": org, "s": e, "f": fid})
    out = await signals.project_disputes(db, org, NOW)
    assert len(out) == 1 and out[0].kind == "rui_ro_lua_dao" and out[0].level == "amber"
    assert await signals.project_disputes(db, org, NOW) == []
    pid = (await db.execute(text("SELECT projected_signal_id FROM mem.disputes WHERE org_id = :o"), {"o": org})).scalar_one()
    assert pid == out[0].id


async def test_intel_owner_fact_ids_filtered_to_confirmed_profile(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    bogus = uuid.uuid4()
    up = _up("co_hoi_ket_noi", _li("co_hoi_ket_noi"), await _raw(db, org), "op:1", owner_fact_ids=[bogus])
    s = await signals.upsert(db, org, up, NOW)
    got = (await db.execute(text("SELECT owner_fact_ids FROM biz.alerts WHERE id = :i"), {"i": s.id})).scalar_one()
    assert got == []


# ─── (d1)/(d2) ────────────────────────────────────────────────────────────────

def test_intel_crosscheck_apply_d2_asymmetric_and_d1_dismiss() -> None:
    li = _li("rui_ro_lua_dao", hard_rule_hit=["credentials_entered"], confidence_exist=0.8)
    s, dis = signals.apply_crosscheck(li, d2={"verdict": "disagree", "flags_confirmed": [], "error": None})
    assert s.d2.verdict == "disagree" and not dis
    s, _ = signals.apply_crosscheck(li, d2={"verdict": "error", "error": "timeout"})
    assert s.d2.error is True and s.d2.flags_confirmed == []
    li2 = _li("xung_dot_khieu_nai", source_class="member")
    s, dis = signals.apply_crosscheck(li2, d1={"agreement": 0.0})
    assert dis and s.confidence_content == 0.0
    s, dis = signals.apply_crosscheck(li2, d1={"agreement": None})
    assert not dis and s.confidence_content == li2.confidence_content


def test_intel_cap_day_evening_quiet_counts_next_day() -> None:
    q = levels.QuietHours(start="22:00", end="07:00")
    assert signals.cap_day(datetime(2026, 10, 23, 22, 30, tzinfo=levels.VN), q).day == 24
    assert signals.cap_day(datetime(2026, 10, 23, 21, 59, tzinfo=levels.VN), q).day == 23
    assert signals.wilson_low(9, 10) < 0.6 <= signals.wilson_low(10, 10)


async def test_intel_cap_reservation_is_atomic_across_parallel_sessions(db: AsyncSession) -> None:
    """Giữ chỗ trần nguyên tử (CNS-126): đã 2/3 🔴 hôm nay, hai lượt SONG SONG cùng ra 🔴 ⇒ đúng MỘT được push_now, lượt kia bị trần."""
    import asyncio

    from gh.db import sessionmaker

    org = await mk_org(db)
    await _install(db, org)
    for i in range(2):
        li = _li("xung_dot_khieu_nai", customer_of_owner=True, flags={"public": True})
        await signals.upsert(db, org, _up("xung_dot_khieu_nai", li, await _raw(db, org), f"case3:pre{i}|x"), NOW)
    raws = [await _raw(db, org) for _ in range(2)]
    await db.commit()

    async def one(i: int) -> signals.Signal:
        async with sessionmaker()() as s:
            li = _li("xung_dot_khieu_nai", customer_of_owner=True, flags={"public": True})
            out = await signals.upsert(s, org, _up("xung_dot_khieu_nai", li, raws[i], f"case3:par{i}|x"), NOW)
            await asyncio.sleep(0.2)                       # giữ transaction (và khoá trần) một lúc
            await s.commit()
            return out

    a, b = await asyncio.gather(one(0), one(1))
    assert sorted([a.delivery, b.delivery]) == ["digest_top", "push_now"]


async def test_attack_privacy_is_sticky_strictest_wins_on_merge_relane_reeval(db: AsyncSession) -> None:
    """Gộp tín hiệu console_pin với tin chat_ok (cùng vụ) KHÔNG được hạ thành chat_ok — cả cột lẫn level_input, sau relane và reevaluate_due."""
    org = await mk_org(db)
    await _install(db, org)
    li = _li("nghia_vu_loi_hua", due_in_h=6, promise={"to_customer": True}, privacy="console_pin")
    s1 = await signals.upsert(db, org, _up("nghia_vu_loi_hua", li, await _raw(db, org), "nghia_vu_loi_hua:promise|PRIV"), NOW)
    pub = li.model_copy(update={"privacy": "chat_ok"})
    s2 = await signals.upsert(db, org, _up("nghia_vu_loi_hua", pub, await _raw(db, org, "tin công khai"), "nghia_vu_loi_hua:promise|PRIV"),
                              NOW + timedelta(minutes=5))
    assert s2.id == s1.id

    async def priv() -> tuple[str, str]:
        r = (await db.execute(text("SELECT privacy, level_input ->> 'privacy' AS lp FROM biz.alerts WHERE id = :i"), {"i": s1.id})).one()
        return r.privacy, r.lp

    assert await priv() == ("console_pin", "console_pin")
    await signals.relane(db, org, s1.id, NOW + timedelta(minutes=10))
    assert await priv() == ("console_pin", "console_pin")
    await signals.reevaluate_due(db, NOW + timedelta(hours=3))
    assert await priv() == ("console_pin", "console_pin")


async def test_attack_merge_weaker_followup_never_demotes_fraud_red(db: AsyncSession) -> None:
    """Cùng vụ (cùng khoá): tin đỏ (luật cứng + xin tiền) rồi tin YẾU hơn ("em đổi số điện thoại rồi") ⇒ vẫn 🔴, hard_rule_hit giữ đủ cả hai."""
    org = await mk_org(db)
    await _install(db, org)
    red = _li("rui_ro_lua_dao", hard_rule_hit=["stk_changed_money_request"], txn_state="pending", flags={"money_request": True},
              victims=[{"ref": "kh1", "customer": True}], payee_refs=["stkA"])
    s1 = await signals.upsert(db, org, _up("rui_ro_lua_dao", red, await _raw(db, org, "cho em mượn 5 triệu"), "fr:imp|x"), NOW)
    assert (s1.level, s1.gate) == ("red", levels.G1)
    weak = _li("rui_ro_lua_dao", ago_h=0.1, now=NOW + timedelta(minutes=3), hard_rule_hit=["emergency"], harm_what="",
               victims=[], confidence_exist=0.5, d2={"verdict": "disagree"})
    s2 = await signals.upsert(db, org, _up("rui_ro_lua_dao", weak, await _raw(db, org, "em đổi số điện thoại rồi"), "fr:imp|x"),
                              NOW + timedelta(minutes=3))
    assert s2.id == s1.id and s2.level == "red" and s2.gate == levels.G1
    li = (await db.execute(text("SELECT level_input FROM biz.alerts WHERE id = :i"), {"i": s1.id})).scalar_one()
    assert set(li["hard_rule_hit"]) == {"stk_changed_money_request", "emergency"}
    assert li["txn_state"] == "pending" and li["flags"]["money_request"] is True and [v["ref"] for v in li["victims"]] == ["kh1"]


async def test_attack_merge_late_ocr_same_raw_event_keeps_red(db: AsyncSession) -> None:
    """OCR của CÙNG ảnh tới sau (khớp theo raw_event_ids, khoá khác) không hạ 🔴 đã có."""
    org = await mk_org(db)
    await _install(db, org)
    raw = await _raw(db, org, "[ảnh]")
    red = _li("rui_ro_lua_dao", flags={"credentials_entered": True}, victims=[{"ref": "nv", "team": True}])
    s1 = await signals.upsert(db, org, _up("rui_ro_lua_dao", red, raw, "fr:a"), NOW)
    assert s1.level == "red"
    ocr = _li("rui_ro_lua_dao", ago_h=0.05, victims=[], harm_what="")
    up = signals.UpsertIn(kind="rui_ro_lua_dao", title="t", detector="ocr", level_input=ocr, case_key="rui_ro_lua_dao:domain|qua-tang.xyz|x",
                          evidence=[signals.EvidenceIn(type="raw_event", id=raw, source_field="ocr", span_start=0, span_end=10)])
    s2 = await signals.upsert(db, org, up, NOW + timedelta(minutes=2))
    assert s2.id == s1.id and s2.level == "red" and s2.gate == levels.G1


def test_merge_level_input_closure_still_applies() -> None:
    """Hợp không chặn đường đóng hợp lệ: tin hoàn thành (status da_xu_ly, A16) và hạn dời vẫn theo tin mới."""
    old = _li("nghia_vu_loi_hua", due_in_h=2, promise={"to_customer": True})
    new = _li("nghia_vu_loi_hua", due_in_h=24 * 6, status="da_xu_ly", promise={"to_customer": True, "completed": True})
    m = signals.merge_level_input(old, new)
    assert m.status == "da_xu_ly" and m.promise.completed and m.deadline_at == new.deadline_at
