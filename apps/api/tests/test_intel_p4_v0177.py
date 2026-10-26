# ruff: noqa: E501
"""v0.1.77 (gói P4) — phân phối, bản tin, Console của Tình báo:
(1) `delivery_state`: `work_hours`, `days_since_install` = min(cài, ramp R4), bật `REGULAR_PUSH_ENABLED` CHỈ cùng ramp (cài mới/đang chạy thử
    ⇒ loại thường là bóng; qua ramp + M1 ⇒ đẩy thật; lừa đảo/an toàn luôn đẩy);
(2) `signals`: `merge_level_input` (asks OR, giữ trường r7), `stage_eff` lưu ngoài `SignalIn`, `same_customer_day_red`, ngăn `handling`,
    `held_for_digest`, `signal_feedback.reason/rule_version`;
(3) `digest.build`: thứ tự nhóm, trần 5 dòng P2, `milestone_due_soon`, "NV X đang lo", "Việc Sếp cần làm";
(4) `pipes/metrics`: M1 gộp n ≥ 10, đếm Sai theo lý do, `rule_version`, job đêm;
(5) Console: bước 3 "Khách chủ chốt", banner chạy thử, dòng bóng + nút Sai 3 lý do, đúng quyền chủ/PIN, không rò org khác."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import httpx
import orjson
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import digest, levels, signals
from gh.biz.intel.pipes import metrics
from gh.biz.mem import entities, facts, profile_read
from tests.conftest import Api, verify_pin
from tests.mem_helpers import mk_org, mkfact
from tests.test_intel_core_signals_v0176 import NOW, _li, _raw, _up
from tests.test_intel_routes_security_v0176 import _org, _signal
from tests.test_rbac_api import login_as

VN = levels.VN


async def _settings(db: AsyncSession, org: uuid.UUID, **intel: Any) -> None:
    await db.execute(text("""UPDATE core.organizations SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb), '{intel}',
                             COALESCE(settings -> 'intel', '{}'::jsonb) || CAST(:s AS jsonb)) WHERE id = :o"""),
                     {"s": orjson.dumps(intel).decode(), "o": org})


def _g3(case: str = "c1", **kw: Any) -> dict[str, Any]:
    return {"customer_of_owner": True, "flags": {"public": True}, **kw}


async def _g3_signal(db: AsyncSession, org: uuid.UUID, case: str, **kw: Any) -> signals.Signal:
    raw = await _raw(db, org)
    li = _li("xung_dot_khieu_nai", **_g3(**kw))
    return await signals.upsert(db, org, _up("xung_dot_khieu_nai", li, raw, levels.case_key("xung_dot_khieu_nai", at=NOW, customer_entity_id=case)), NOW)


# ─── (1) delivery_state + ramp R4 ─────────────────────────────────────────────

def test_regular_push_flag_is_on_with_ramp() -> None:
    assert levels.REGULAR_PUSH_ENABLED is True


async def test_delivery_state_work_hours_default_settings_and_bad_value(db: AsyncSession) -> None:
    org = await mk_org(db)
    assert (await signals.delivery_state(db, org, NOW)).work_hours == levels.WorkHours(start="08:00", end="17:30")
    await _settings(db, org, work_start="07:30", work_end="18:00")
    assert (await signals.delivery_state(db, org, NOW)).work_hours == levels.WorkHours(start="07:30", end="18:00")
    await _settings(db, org, work_start="25:99", work_end="chiều")                 # sai định dạng ⇒ mặc định, không làm hỏng job đồng hồ
    assert (await signals.delivery_state(db, org, NOW)).work_hours == levels.WorkHours(start="08:00", end="17:30")


async def test_days_since_install_is_min_of_install_and_ramp(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _settings(db, org, installed_at=(NOW - timedelta(days=40)).isoformat())
    assert round((await signals.delivery_state(db, org, NOW)).days_since_install) == 40                  # chưa có ramp ⇒ ngày từ cài
    await _settings(db, org, ramp_started_at=(NOW - timedelta(days=3)).isoformat())
    assert round((await signals.delivery_state(db, org, NOW)).days_since_install) == 3                   # ramp mới hơn ⇒ ramp
    await _settings(db, org, ramp_started_at=(NOW - timedelta(days=90)).isoformat())
    assert round((await signals.delivery_state(db, org, NOW)).days_since_install) == 40                  # ramp cũ hơn cài ⇒ ngày từ cài
    await _settings(db, org, ramp_started_at="rác")
    assert (await signals.delivery_state(db, org, NOW)).days_since_install == 0.0                        # sai định dạng ⇒ 0 (không tự đẩy thật) + log


async def test_new_install_and_ramp_trial_regular_red_is_shadow_not_pushed(db: AsyncSession) -> None:
    """Cài mới (chưa 14 ngày) HOẶC đang chạy thử R4 (ramp < 14 ngày) ⇒ loại thường là bóng, kể cả khi `m1_passed_gates` đã có."""
    new = await mk_org(db, "mới")
    await _settings(db, new, installed_at=(NOW - timedelta(days=2)).isoformat(), m1_passed_gates=list(levels.REGULAR_GATES))
    s = await _g3_signal(db, new, "a")
    assert (s.level, s.gate, s.delivery) == ("red", levels.G3, "digest_top") and s.pushed_at is None and s.would_push_at == NOW and "shadow" in s.reasons
    old = await mk_org(db, "cũ cài lâu nhưng ramp mới")
    await _settings(db, old, installed_at=(NOW - timedelta(days=60)).isoformat(), ramp_started_at=(NOW - timedelta(days=5)).isoformat(),
                    m1_passed_gates=list(levels.REGULAR_GATES))
    s = await _g3_signal(db, old, "b")
    assert (s.delivery, s.pushed_at) == ("digest_top", None) and s.would_push_at == NOW


async def test_after_ramp_and_m1_regular_red_is_pushed_but_not_when_gate_missing(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _settings(db, org, installed_at=(NOW - timedelta(days=60)).isoformat(), ramp_started_at=(NOW - timedelta(days=15)).isoformat(),
                    m1_passed_gates=list(levels.REGULAR_GATES))
    s = await _g3_signal(db, org, "a")
    assert (s.level, s.gate, s.delivery) == ("red", levels.G3, "push_now") and s.pushed_at == NOW
    other = await mk_org(db, "chưa đạt M1")
    await _settings(db, other, installed_at=(NOW - timedelta(days=60)).isoformat(), ramp_started_at=(NOW - timedelta(days=15)).isoformat(),
                    m1_passed_gates=[])
    s = await _g3_signal(db, other, "b")
    assert (s.delivery, s.pushed_at) == ("digest_top", None)


async def test_fraud_and_safety_always_push_even_in_trial(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _settings(db, org, installed_at=(NOW - timedelta(days=1)).isoformat(), ramp_started_at=(NOW - timedelta(days=1)).isoformat())
    raw = await _raw(db, org, "lỡ nhập OTP")
    fr = await signals.upsert(db, org, _up(levels.FR, _li(levels.FR, hard_rule_hit=["credentials_entered"], ago_h=0.1), raw,
                                           levels.case_key(levels.FR, at=NOW, raw_event_id=str(raw))), NOW)
    assert (fr.gate, fr.delivery) == (levels.G1, "push_now")
    raw2 = await _raw(db, org, "cháy bếp")
    sk = await signals.upsert(db, org, _up(levels.SK, _li(levels.SK, flags={"emergency": True}, ago_h=0.1), raw2,
                                           levels.case_key(levels.SK, at=NOW, raw_event_id=str(raw2))), NOW)
    assert (sk.gate, sk.delivery) == (levels.G2, "push_now")


async def test_ramp_gates_push_choice_still_takes_effect_after_reset(db: AsyncSession) -> None:
    """Sếp chọn `push` SAU khi migration đặt lại `digest_top` ⇒ có hiệu lực (CNS-181)."""
    org = await mk_org(db)
    await _settings(db, org, installed_at=(NOW - timedelta(days=60)).isoformat(), ramp_started_at=(NOW - timedelta(days=2)).isoformat(), ramp_gates="push")
    assert (await signals.delivery_state(db, org, NOW)).ramp_early_enable is True
    assert (await _g3_signal(db, org, "a")).delivery == "push_now"


# ─── (2) merge_level_input ────────────────────────────────────────────────────

def _si(**kw: Any) -> levels.SignalIn:
    return _li("xung_dot_khieu_nai", **kw)


def test_merge_asks_for_owner_is_or_and_clears_staff_owned() -> None:
    old = _si(staff_owned=True, staff_ref="u_nv1")
    new = _si(asks_for_owner=True)
    m = signals.merge_level_input(old, new)
    assert m.asks_for_owner is True and m.staff_owned is False and m.staff_ref is None
    assert signals.merge_level_input(new, old).asks_for_owner is True            # thứ tự không đổi kết quả
    only_new_staff = signals.merge_level_input(_si(asks_for_owner=True), _si(staff_owned=True, staff_ref="u_nv1"))
    assert only_new_staff.asks_for_owner and not only_new_staff.staff_owned and only_new_staff.staff_ref is None


def test_merge_keeps_r7_fields_of_old_when_new_is_silent() -> None:
    old = _si(staff_owned=True, staff_ref="u_nv1", prior_threat_no_exit_n=2, complaint_stage="ultimatum", public_venue="group",
              deadline_granularity="day", promise={"nudged_by_customer": True, "fixed_occasion": True},
              opportunity={"need_fields_n": 3}, thread={"type": "group", "size": 20, "customer_audience_n": 5})
    m = signals.merge_level_input(old, _si())                                        # tin mới vắng mọi trường r7
    assert (m.staff_owned, m.staff_ref, m.prior_threat_no_exit_n) == (False, None, 2)           # staff_owned theo bản MỚI (CNS-179)
    assert (m.complaint_stage, m.public_venue, m.deadline_granularity) == ("ultimatum", "group", "day")
    assert m.promise.nudged_by_customer and m.promise.fixed_occasion and m.opportunity.need_fields_n == 3 and m.thread.customer_audience_n == 5
    m2 = signals.merge_level_input(old, _si(prior_threat_no_exit_n=1, complaint_stage="exit_pending", opportunity={"need_fields_n": 1}))
    assert m2.prior_threat_no_exit_n == 2 and m2.complaint_stage == "exit_pending" and m2.opportunity.need_fields_n == 1     # có nguồn mới ⇒ theo mới
    assert signals.merge_level_input(old, _si(staff_owned=True, staff_ref="u_nv2")).staff_ref == "u_nv2"


def test_merge_staff_owned_old_true_new_false_stays_false_and_stage_is_highest() -> None:
    m = signals.merge_level_input(_si(staff_owned=True, staff_ref="u_nv1", complaint_stage="voice"), _si(complaint_stage="exit_pending"))
    assert m.staff_owned is False and m.staff_ref is None and m.complaint_stage == "exit_pending"
    assert signals.merge_level_input(_si(complaint_stage="exit_pending"), _si(complaint_stage="voice")).complaint_stage == "exit_pending"
    assert signals.merge_level_input(_si(complaint_stage="exit_done"), _si(complaint_stage="third_party")).complaint_stage == "exit_done"
    assert signals.merge_level_input(_si(complaint_stage="voice"), _si(complaint_stage="ultimatum")).complaint_stage == "ultimatum"
    assert signals.merge_level_input(_si(complaint_stage="ultimatum"), _si(complaint_stage="vent")).complaint_stage == "ultimatum"
    assert signals.merge_level_input(_si(complaint_stage="ultimatum"), _si()).complaint_stage == "ultimatum"


# ─── stage_eff lưu ngoài SignalIn ─────────────────────────────────────────────

async def test_stage_eff_is_stored_before_theta1_and_relane_survives_it(db: AsyncSession) -> None:
    org = await mk_org(db)
    raw = await _raw(db, org)
    ck = levels.case_key("xung_dot_khieu_nai", at=NOW, customer_entity_id="s1")
    li = _si(customer_of_owner=True, complaint_stage="ultimatum", prior_threat_no_exit_n=3)      # θ1 sẽ hạ ultimatum ⇒ vent khi tính mức
    s = await signals.upsert(db, org, _up("xung_dot_khieu_nai", li, raw, ck), NOW)
    stored = (await db.execute(text("SELECT level_input FROM biz.alerts WHERE id = :i"), {"i": s.id})).scalar_one()
    assert stored["stage_eff"] == "ultimatum" and stored["complaint_stage"] == "ultimatum"       # TRƯỚC θ1
    with pytest.raises(ValueError):
        levels.SignalIn.model_validate(stored)                                                   # SignalIn extra=forbid ⇒ phải tách khoá phụ
    assert signals.load_level_input(stored).complaint_stage == "ultimatum"
    again = await signals.relane(db, org, s.id, NOW + timedelta(minutes=5))                      # đọc lại + tính lại không vỡ
    assert again.id == s.id
    s2 = await signals.upsert(db, org, _up("xung_dot_khieu_nai", _si(customer_of_owner=True, flags={"said_will_leave": True}), raw, ck), NOW)
    assert s2.id == s.id
    assert (await db.execute(text("SELECT level_input ->> 'stage_eff' FROM biz.alerts WHERE id = :i"), {"i": s.id})).scalar_one() == "ultimatum"


async def test_stage_eff_compat_path_and_not_for_other_kinds(db: AsyncSession) -> None:
    org = await mk_org(db)
    raw = await _raw(db, org)
    a = await signals.upsert(db, org, _up("xung_dot_khieu_nai", _si(flags={"said_will_leave": True}), raw, "xung_dot_khieu_nai:a"), NOW)
    raw2 = await _raw(db, org)
    b = await signals.upsert(db, org, _up("xung_dot_khieu_nai", _si(), raw2, "xung_dot_khieu_nai:b"), NOW)
    raw3 = await _raw(db, org)
    c = await signals.upsert(db, org, _up("cau_hoi_cho_xu_ly", _li("cau_hoi_cho_xu_ly"), raw3, "cau_hoi_cho_xu_ly:c"), NOW)
    got = {r.id: r.level_input for r in (await db.execute(text("SELECT id, level_input FROM biz.alerts WHERE org_id = :o"), {"o": org})).all()}
    assert got[a.id]["stage_eff"] == "ultimatum" and got[b.id]["stage_eff"] == "voice" and "stage_eff" not in got[c.id]


# ─── same_customer_day_red ────────────────────────────────────────────────────

async def _mk_person(db: AsyncSession, org: uuid.UUID) -> uuid.UUID:
    return (await db.execute(text("INSERT INTO core.persons (org_id, code, display_name, person_type) VALUES (:o, core.next_code('PER'), 'Khách A', 'customer') RETURNING id"),
                             {"o": org})).scalar_one()  # type: ignore[no-any-return]


async def _mark(db: AsyncSession, sid: uuid.UUID, person: uuid.UUID, *, gate: str = levels.G4, delivery: str = "push_now", level: str = "red",
                at: datetime = NOW) -> None:
    await db.execute(text("""UPDATE biz.alerts SET gate = :g, level = :l, delivery = :d, subject_type = 'person', subject_id = :p,
                             pushed_at = :at, level_at = :at WHERE id = :i"""), {"g": gate, "l": level, "d": delivery, "p": person, "at": at, "i": sid})


async def test_same_customer_day_red_counts_pushed_morning_merge_not_shadow_or_capped(db: AsyncSession) -> None:
    org = await mk_org(db)
    cust = await _mk_person(db, org)
    me = await _g3_signal(db, org, "me")
    other = await _g3_signal(db, org, "other")
    for delivery, level, expect in (("push_now", "red", True), ("digest_morning", "red", True), ("merge", "red", True),
                                    ("digest_top", "red", False),                 # thẻ bóng (would_push) — không tính, giữ mẫu M1
                                    ("digest_top", "amber", False)):              # thẻ bị trần (🟠 digest_top)
        await _mark(db, other.id, cust, delivery=delivery, level=level)
        assert await signals.same_customer_day_red(db, org, me.id, cust, None, NOW) is expect, (delivery, level)
    await _mark(db, other.id, cust)                                                # khác ngày VN ⇒ không tính
    assert await signals.same_customer_day_red(db, org, me.id, cust, None, NOW + timedelta(days=1)) is False
    assert await signals.same_customer_day_red(db, org, me.id, await _mk_person(db, org), None, NOW) is False       # khách khác
    assert await signals.same_customer_day_red(db, org, other.id, cust, None, NOW) is False                          # không tự đếm chính mình
    assert await signals.same_customer_day_red(db, org, me.id, None, None, NOW) is False                             # không biết khách ⇒ không suy
    await _mark(db, other.id, cust, gate=levels.G3)                                # chỉ thẻ G4
    assert await signals.same_customer_day_red(db, org, me.id, cust, None, NOW) is False
    foreign = await mk_org(db, "org khác")
    await _mark(db, other.id, cust)
    assert await signals.same_customer_day_red(db, foreign, me.id, cust, None, NOW) is False                        # lọc org_id


# ─── ngăn handling khi staff_owned ────────────────────────────────────────────

def test_lane_staff_owned_goes_to_handling_not_delegated() -> None:
    assert signals._lane("open", "grey", False, False, "fyi", staff_owned=True) == "handling"
    assert signals._lane("open", "grey", True, False, "fyi", staff_owned=True) == "delegated"      # Sếp giao việc vẫn là delegated
    assert signals._lane("open", "grey", False, False, "fyi") == "fyi"
    assert signals._lane("resolved", "grey", False, False, "handling", staff_owned=True) == "handling"   # kết thúc ⇒ giữ ngăn cuối


async def test_relane_puts_staff_owned_signal_into_handling(db: AsyncSession) -> None:
    org = await mk_org(db)
    raw = await _raw(db, org)
    li = _si(customer_of_owner=True, complaint_stage="voice", staff_owned=True, staff_ref="u_nv1", impact_base=2)
    s = await signals.upsert(db, org, _up("xung_dot_khieu_nai", li, raw, "xung_dot_khieu_nai:nv"), NOW)
    assert "staff_owned" in s.reasons and s.lane == "handling"
    raw2 = await _raw(db, org)
    asks = _si(customer_of_owner=True, complaint_stage="voice", asks_for_owner=True, impact_base=2)
    s2 = await signals.upsert(db, org, _up("xung_dot_khieu_nai", asks, raw2, "xung_dot_khieu_nai:nv"), NOW)       # gộp: khách đòi gặp chủ ⇒ hết "NV lo"
    assert s2.id == s.id and "staff_owned" not in s2.reasons and s2.lane != "handling"


# ─── held_for_digest ──────────────────────────────────────────────────────────

async def test_held_for_digest_keeps_unseen_top_item_when_clock_downgrades(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _settings(db, org, installed_at=(NOW - timedelta(days=60)).isoformat(), m1_passed_gates=list(levels.REGULAR_GATES))
    s = await _g3_signal(db, org, "held", ago_h=30)                               # quá 24 giờ chưa xử lý ⇒ đứng đầu bản tin (Q15)
    assert s.delivery == "digest_top" and "window_expired" in s.reasons
    later = NOW + timedelta(days=16)                                              # quá 14 ngày ⇒ đồng hồ hạ về bản tin thường
    plain = await signals.relane(db, org, s.id, later)                            # có tin mới/không theo đồng hồ ⇒ hạ thật
    assert plain.delivery in ("digest", "digest_low")
    await db.execute(text("UPDATE biz.alerts SET delivery = 'digest_top', level_at = :n, digest_at = NULL WHERE id = :i"), {"n": NOW, "i": s.id})
    held = await signals._relane(db, org, s.id, later, skip_locked=False, by_clock=True)
    assert held is not None and held.delivery == "digest_top" and "held_for_digest" in held.reasons
    await db.execute(text("UPDATE biz.alerts SET digest_at = :n WHERE id = :i"), {"n": later, "i": s.id})        # đã lên bản tin ⇒ không giữ nữa
    freed = await signals._relane(db, org, s.id, later, skip_locked=False, by_clock=True)
    assert freed is not None and freed.delivery in ("digest", "digest_low") and "held_for_digest" not in freed.reasons


# ─── signal_feedback.reason / rule_version ────────────────────────────────────

async def _fb_rows(db: AsyncSession, sid: uuid.UUID) -> list[Any]:
    return list((await db.execute(text("SELECT verdict, reason, rule_version FROM biz.signal_feedback WHERE signal_id = :i ORDER BY at, id"), {"i": sid})).all())


async def test_feedback_reason_and_explicit_rule_version_by_card(db: AsyncSession) -> None:
    org = await mk_org(db)
    new = await _g3_signal(db, org, "new")
    old = await _g3_signal(db, org, "old")
    await db.execute(text("UPDATE biz.alerts SET projection_version = 1 WHERE id = :i"), {"i": old.id})      # thẻ tạo trước r7
    await signals.feedback(db, org, new.id, verdict="wrong", verdict_by="owner", now=NOW, reason="still_buying")
    await signals.feedback(db, org, old.id, verdict="wrong", verdict_by="owner", now=NOW, reason="staff_handled")
    [a] = await _fb_rows(db, new.id)
    [b] = await _fb_rows(db, old.id)
    assert (a.reason, a.rule_version) == ("still_buying", "r7") and (b.reason, b.rule_version) == ("staff_handled", "r6")   # theo thẻ, không theo DEFAULT


async def test_feedback_reason_only_with_wrong_and_one_of_three(db: AsyncSession) -> None:
    org = await mk_org(db)
    s = await _g3_signal(db, org, "x")
    for verdict, reason in (("correct", "not_urgent"), ("wrong", "tuy_thich"), ("missed", "still_buying")):
        with pytest.raises(signals.SignalError):
            await signals.feedback(db, org, s.id, verdict=verdict, verdict_by="owner", now=NOW, reason=reason)
    assert await _fb_rows(db, s.id) == []
    await signals.feedback(db, org, s.id, verdict="correct", verdict_by="owner", now=NOW)
    assert [(r.verdict, r.reason, r.rule_version) for r in await _fb_rows(db, s.id)] == [("correct", None, "r7")]


async def test_feedback_route_reason_validation_and_org_isolation(owner_api: Api, client: httpx.AsyncClient, db: AsyncSession) -> None:
    org = await _org(db)
    sid = await _signal(db, org)
    r = await owner_api.send("POST", f"/signals/{sid}/feedback", {"verdict": "correct", "reason": "not_urgent"})
    assert r.status_code == 422, r.text
    r = await owner_api.send("POST", f"/signals/{sid}/feedback", {"verdict": "wrong", "reason": "bậy"})
    assert r.status_code == 422
    r = await owner_api.send("POST", f"/signals/{sid}/feedback", {"verdict": "wrong", "reason": "not_urgent"})
    assert r.status_code == 200, r.text
    row = (await db.execute(text("SELECT reason, rule_version FROM biz.signal_feedback WHERE signal_id = :i"), {"i": sid})).one()
    assert (row.reason, row.rule_version) == ("not_urgent", "r7")
    assert (await owner_api.send("POST", f"/signals/{uuid.uuid4()}/feedback", {"verdict": "wrong", "reason": "not_urgent"})).status_code == 404
    mgr = await login_as(client, db, "manager")
    await verify_pin(mgr, "112233")
    assert (await mgr.send("POST", f"/signals/{sid}/feedback", {"verdict": "wrong"})).status_code == 403          # chỉ Owner chấm


async def test_feedback_on_other_org_signal_is_404(owner_api: Api, db: AsyncSession) -> None:
    other = await mk_org(db, "org khác")
    s = await _g3_signal(db, other, "z")
    await db.commit()
    assert (await owner_api.send("POST", f"/signals/{s.id}/feedback", {"verdict": "wrong", "reason": "not_urgent"})).status_code == 404
    assert await _fb_rows(db, s.id) == []


# ─── (3) digest ───────────────────────────────────────────────────────────────

async def _row(db: AsyncSession, org: uuid.UUID, tag: str, *, kind: str = "cau_hoi_cho_xu_ly", delivery: str = "digest_top", level: str = "amber",
               gate: str | None = None, reasons: list[str] | None = None, impact: int = 2, urgency: int = 2, would_push: bool = False,
               action: str | None = None, staff_ref: str | None = None, title: str | None = None) -> uuid.UUID:
    """Một dòng `biz.alerts` hợp lệ rồi ép các cột ĐÃ LƯU (digest chỉ thực thi trường đã lưu, không tính lại mức)."""
    raw = await _raw(db, org)
    s = await signals.upsert(db, org, _up(kind, _li(kind), raw, f"{kind}:{tag}"), NOW)
    await db.execute(text("""UPDATE biz.alerts SET delivery = :d, level = :l, gate = :g, reasons = CAST(:r AS text[]), impact = :im, urgency = :ur,
                             would_push_at = :wp, suggested_action = :sa, title = :t, digest_at = NULL,
                             level_input = level_input || CAST(:li AS jsonb) WHERE id = :i"""),
                     {"d": delivery, "l": level, "g": gate, "r": reasons or [], "im": impact, "ur": urgency, "wp": NOW if would_push else None, "sa": action,
                      "t": title or f"Dòng {tag}", "li": orjson.dumps({"staff_ref": staff_ref} if staff_ref else {}).decode(), "i": s.id})
    return s.id


async def _order(db: AsyncSession, org: uuid.UUID) -> digest.Digest:
    return await digest.build(db, org, NOW, audience="owner", only_new=False)


async def test_digest_groups_order_cap_five_p2_and_q15_head(db: AsyncSession) -> None:
    org = await mk_org(db)
    amber = await _row(db, org, "amber", delivery="digest")
    low = [await _row(db, org, f"low{i}", delivery="digest_low", level="grey", reasons=["prio_p4"]) for i in range(2)]
    p2 = [await _row(db, org, f"p2-{i}", reasons=["prio_p2"], impact=3, urgency=3 - (i % 2)) for i in range(6)]
    milestone = await _row(db, org, "ms", reasons=["prio_p2", "milestone_due_soon"], impact=1, urgency=1)
    q15 = await _row(db, org, "q15", kind="xung_dot_khieu_nai", level="red", gate=levels.G3, reasons=["prio_p2", "window_expired"], impact=1, urgency=1)
    shadow = await _row(db, org, "shadow", level="red", gate=levels.G4, reasons=["shadow"], would_push=True, impact=1)
    fraud = await _row(db, org, "fraud", kind=levels.FR, level="red", gate=levels.G1, reasons=["window_expired"], impact=1)
    d = await _order(db, org)
    ids = [it.signal_id for it in d.items]
    assert ids[:4] == [str(fraud), str(shadow), str(q15), str(milestone)]                  # 1 lừa đảo · 2 bóng · 3 P2: Q15 rồi Q-A đứng đầu
    shown_p2 = ids[4:9]
    assert set(shown_p2) <= {str(x) for x in p2} and len(set(shown_p2)) == 5               # đúng 5 dòng P2 thường
    overflow = [x for x in p2 if str(x) not in shown_p2]
    assert len(overflow) == 1 and ids[9] == str(overflow[0]) and ids[10] == str(amber)    # dòng dư xuống nhóm 🟠 thường, trước 🟠 `digest`
    assert d.p2_more == 1 and "(+1 mục cần xem)" in d.items[9].line_facts
    assert all("(+" not in " ".join(it.line_facts) for i, it in enumerate(d.items) if i != 9)
    assert d.items[-1].cluster_ids and set(d.items[-1].cluster_ids) == {str(x) for x in low}
    assert [it.n for it in d.items] == list(range(1, len(d.items) + 1))


async def test_digest_group1_never_pushed_below_plus_k(db: AsyncSession) -> None:
    """Lừa đảo/an toàn (nhóm 1) và 🔴 bóng không chịu trần P2, kể cả khi có hàng chục dòng."""
    org = await mk_org(db)
    fr = [await _row(db, org, f"fr{i}", kind=levels.FR, level="red", gate=levels.G1, reasons=["window_expired"]) for i in range(7)]
    sh = [await _row(db, org, f"sh{i}", level="red", gate=levels.G3, reasons=["shadow", "prio_p2"], would_push=True) for i in range(7)]
    p2 = [await _row(db, org, f"p{i}", reasons=["prio_p2"]) for i in range(7)]
    d = await _order(db, org)
    ids = [it.signal_id for it in d.items]
    assert set(ids[:7]) == {str(x) for x in fr} and set(ids[7:14]) == {str(x) for x in sh}
    assert d.p2_more == 2 and set(ids[14:19]) <= {str(x) for x in p2}


async def test_digest_milestone_due_soon_line_has_text_and_action(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _row(db, org, "ms", kind="nghia_vu_loi_hua", reasons=["prio_p2", "milestone_due_soon"], gate=None)
    [it] = (await _order(db, org)).items
    assert "lời hứa với khách sắp tới hạn" in it.line_facts and it.action


async def test_digest_staff_owned_line_names_staff_and_says_nothing_to_do(db: AsyncSession) -> None:
    org = await mk_org(db)
    pid = (await db.execute(text("INSERT INTO core.persons (org_id, code, display_name, person_type) VALUES (:o, core.next_code('PER'), 'Hùng', 'staff') RETURNING id"),
                            {"o": org})).scalar_one()
    sid = await _row(db, org, "nv", kind="xung_dot_khieu_nai", delivery="digest_low", level="grey", reasons=["prio_p4", "staff_owned"],
                     staff_ref=f"u_{pid}", title="Khách chê giao trễ")
    other = await _row(db, org, "other", kind="xung_dot_khieu_nai", delivery="digest_low", level="grey", reasons=["prio_p4"])
    d = await _order(db, org)
    staff_line = d.items[0]
    assert staff_line.signal_id == str(sid) and staff_line.title == "NV Hùng đang lo: Khách chê giao trễ"
    assert staff_line.action == "Không cần làm gì, NV Hùng đang lo"
    assert d.items[1].cluster_ids == [str(other)]                       # dòng cụm ⚪ còn lại, không action
    # staff_ref lạ / người org khác ⇒ chỉ "NV", không lộ tên
    foreign = await mk_org(db, "org khác")
    fp = (await db.execute(text("INSERT INTO core.persons (org_id, code, display_name, person_type) VALUES (:o, core.next_code('PER'), 'Người Ngoài', 'staff') RETURNING id"),
                           {"o": foreign})).scalar_one()
    await db.execute(text("UPDATE biz.alerts SET level_input = level_input || CAST(:li AS jsonb) WHERE id = :i"),
                     {"li": orjson.dumps({"staff_ref": f"u_{fp}"}).decode(), "i": sid})
    assert (await _order(db, org)).items[0].title == "NV đang lo: Khách chê giao trễ"


def test_checked_action_rules() -> None:
    ok = digest.checked_action("Gọi khách xác nhận giờ giao", None)
    assert ok == "Gọi khách xác nhận giờ giao"
    for bad in ("Chào hàng thêm combo", "Mời mua gói mới", "Upsell cho khách", "Chốt đơn ngay", "Bán thêm phụ kiện", "gợi ý mua thêm", "", None, "x" * 200):
        assert digest.checked_action(bad, "khách hỏi") is None, bad
    assert digest.checked_action("Báo giá cho khách", "anh chê trễ") is None            # "báo giá" chỉ khi khách đã hỏi giá
    assert digest.checked_action("Báo giá cho khách", "cho em hỏi giá bao nhiêu") == "Báo giá cho khách"


def test_template_actions_cover_ten_cases_without_selling() -> None:
    t = digest.template_action
    assert t(levels.FR, [], None, None, None) == "Gọi xác minh trực tiếp, chưa chuyển tiền/giao hàng"
    assert t(levels.SK, [], None, None, None) == "Gọi hỏi tình hình, cần thì gọi 115"
    assert t("xung_dot_khieu_nai", [], levels.G3, "exit_pending", None) == "Gọi khách trước khi khách huỷ hẳn"
    assert t("xung_dot_khieu_nai", [], levels.G3, "voice", None) == "Đọc lời khách, quyết cách xử lý"
    assert t("nghia_vu_loi_hua", [], levels.G4, None, None) == "Xác nhận giờ giao với khách hoặc giao NV báo lại"
    assert t("xung_dot_khieu_nai", ["window_expired"], levels.G3, "voice", None) == "Hỏi NV vụ này đã xử lý chưa"
    assert t("co_hoi_ket_noi", [], levels.G5, None, None) == "Xem nhu cầu, trả lời khách nếu muốn nhận"
    assert t("viec_han", [], levels.G6, None, None) == "Quyết: làm ngay, dời hạn hay giao người"
    assert t("xung_dot_khieu_nai", ["staff_owned"], None, None, "NV Hùng") == "Không cần làm gì, NV Hùng đang lo"
    assert t("su_kien_dip", [], None, None, None) == "Xem tin gốc khi rảnh"
    every = [t(k, r, g, s, None) for k in ("rui_ro_lua_dao", "su_kien_dip", "viec_han") for r in ([], ["window_expired"]) for g in (None, *levels.GATES) for s in (None, "exit_pending")]
    assert all(digest.checked_action(x, "giá") == x for x in every)                 # mẫu không chạm từ cấm


async def test_digest_every_line_ge_amber_has_owner_action(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _row(db, org, "a", action="Gọi khách xác nhận giờ giao", delivery="digest")
    await _row(db, org, "b", action="Chốt đơn ngay đi", delivery="digest")
    await _row(db, org, "c", level="red", gate=levels.G3, kind="xung_dot_khieu_nai", reasons=["shadow"], would_push=True)
    d = await _order(db, org)
    acts = {it.title: it.action for it in d.items}
    assert acts["Dòng a"] == "Gọi khách xác nhận giờ giao"
    assert acts["Dòng b"] == "Xem tin gốc khi rảnh"                                 # từ cấm ⇒ mẫu, không bao giờ chào bán
    assert acts["Dòng c"] == "Đọc lời khách, quyết cách xử lý" and all(it.action for it in d.items)
    assert all("chốt đơn" not in (it.action or "").casefold() for it in d.items)


# ─── (4) metrics: M1 gộp n ≥ 10, Sai theo lý do, rule_version, job đêm ────────

async def _fb(db: AsyncSession, org: uuid.UUID, sid: uuid.UUID, verdict: str, *, gate: str = levels.G3, reason: str | None = None, rule: str = "r7",
              shadow: bool = True, at: datetime | None = None, by: str = "owner") -> None:
    await db.execute(text("""INSERT INTO biz.signal_feedback (org_id, signal_id, verdict, verdict_by, gate, shadow, at, reason, rule_version)
                             VALUES (:o, :s, :v, :b, :g, :sh, :at, :r, :rv)"""),
                     {"o": org, "s": sid, "v": verdict, "b": by, "g": gate, "sh": shadow, "at": at or NOW, "r": reason, "rv": rule})


async def _shadow_ids(db: AsyncSession, org: uuid.UUID, n: int) -> list[uuid.UUID]:
    return [(await _g3_signal(db, org, f"m{i}")).id for i in range(n)]


async def _ramp_org(db: AsyncSession, started: datetime) -> uuid.UUID:
    org = await mk_org(db)
    await _settings(db, org, ramp_started_at=started.isoformat())
    return org


async def test_m1_threshold_is_ten_pooled_and_needs_ten_of_ten(db: AsyncSession) -> None:
    org = await _ramp_org(db, NOW - timedelta(days=5))
    ids = await _shadow_ids(db, org, 12)
    gates = [levels.G3, levels.G4, levels.G5, levels.G6]
    for i, sid in enumerate(ids[:9]):
        await _fb(db, org, sid, "correct", gate=gates[i % 4])
    m = await metrics.m1_ramp(db, org)
    assert m["pooled"]["n"] == 9 and m["pooled"]["m1_ok"] is False and m["passed_gates"] == []           # n < 10 ⇒ chưa đủ
    await _fb(db, org, ids[9], "wrong", reason="not_urgent")
    m = await metrics.m1_ramp(db, org)
    assert (m["pooled"]["k"], m["pooled"]["n"]) == (9, 10) and m["pooled"]["m1_ok"] is False              # 9/10: cận dưới Wilson < 60% ⇒ cần 10/10
    await _fb(db, org, ids[9], "correct", at=NOW + timedelta(minutes=1))                                    # bản mới nhất mỗi tín hiệu thắng
    m = await metrics.m1_ramp(db, org)
    assert (m["pooled"]["k"], m["pooled"]["n"], m["pooled"]["m1_ok"]) == (10, 10, True)
    assert set(m["passed_gates"]) == set(levels.REGULAR_GATES) and m["low_precision_gates"] == []
    assert {g["gate"]: g["n"] for g in m["by_gate"]} == {levels.G3: 4, levels.G4: 2, levels.G5: 2, levels.G6: 2}


async def test_m1_counts_only_r7_shadow_owner_rows_since_ramp_start(db: AsyncSession) -> None:
    org = await _ramp_org(db, NOW - timedelta(days=5))
    ids = await _shadow_ids(db, org, 6)
    await _fb(db, org, ids[0], "correct")                                                                  # tính
    await _fb(db, org, ids[1], "correct", rule="r6")                                                       # thẻ r6 ⇒ không tính
    await _fb(db, org, ids[2], "correct", at=NOW - timedelta(days=9))                                      # trước ramp ⇒ không tính
    await _fb(db, org, ids[3], "correct", shadow=False)                                                    # không phải dòng bóng ⇒ không tính
    await _fb(db, org, ids[4], "correct", by="system")                                                     # máy bác ⇒ không tính
    await _fb(db, org, ids[5], "correct", gate=levels.G1)                                                  # lừa đảo tính riêng ⇒ không vào M1 gộp
    m = await metrics.m1_ramp(db, org)
    assert m["pooled"]["n"] == 1 and m["rule_version"] == "r7" and m["ramp_started_at"]


async def test_m1_wrong_by_reason_and_low_precision_gate(db: AsyncSession) -> None:
    org = await _ramp_org(db, NOW - timedelta(days=5))
    ids = await _shadow_ids(db, org, 14)
    for sid in ids[:8]:
        await _fb(db, org, sid, "correct", gate=levels.G4)
    await _fb(db, org, ids[8], "correct", gate=levels.G3)
    for sid, reason in ((ids[9], "still_buying"), (ids[10], "still_buying"), (ids[11], "staff_handled"), (ids[12], None)):
        await _fb(db, org, sid, "wrong", gate=levels.G3, reason=reason)
    m = await metrics.m1_ramp(db, org)
    assert m["wrong_by_reason"] == {"still_buying": 2, "not_urgent": 0, "staff_handled": 1, "none": 1}
    assert m["low_precision_gates"] == [levels.G3]                                                         # G3: 1/5 đúng, ≥ 3 lượt ⇒ "Sai nhiều"
    assert levels.G3 not in m["passed_gates"]                                                              # n = 13 nhưng 9/13 < 80% ⇒ chưa đạt M1


async def test_m1_pass_excludes_only_the_low_precision_gate_and_nightly_writes_settings(db: AsyncSession) -> None:
    org = await _ramp_org(db, NOW - timedelta(days=5))
    ids = await _shadow_ids(db, org, 26)
    for sid in ids[:22]:
        await _fb(db, org, sid, "correct", gate=levels.G4)
    for sid in ids[22:25]:
        await _fb(db, org, sid, "correct", gate=levels.G5)
    await _fb(db, org, ids[25], "wrong", gate=levels.G6, reason="not_urgent")                              # G6: 0/1 (< 3 lượt) ⇒ chưa phải "Sai nhiều"
    m = await metrics.run_m1_nightly(db, org, NOW)
    assert m["pooled"]["m1_ok"] is True and m["low_precision_gates"] == []
    st = (await db.execute(text("SELECT settings -> 'intel' FROM core.organizations WHERE id = :o"), {"o": org})).scalar_one()
    assert set(st["m1_passed_gates"]) == set(levels.REGULAR_GATES) and st["low_precision_gates"] == [] and st["m1_checked_at"]
    assert not {levels.G1, levels.G2} & set(st["m1_passed_gates"])                                         # cổng 1–2 không bao giờ vào danh sách
    for sid in ids[:3]:                                                                                    # G4 tụt: lật 3 lượt thành Sai ⇒ G4 "Sai nhiều" và ra khỏi danh sách
        await _fb(db, org, sid, "wrong", gate=levels.G4, reason="still_buying", at=NOW + timedelta(hours=1))
    for sid in ids[3:25]:
        await _fb(db, org, sid, "wrong", gate=levels.G5, reason="staff_handled", at=NOW + timedelta(hours=1))
    m2 = await metrics.run_m1_nightly(db, org, NOW + timedelta(days=1))
    st2 = (await db.execute(text("SELECT settings -> 'intel' FROM core.organizations WHERE id = :o"), {"o": org})).scalar_one()
    assert m2["pooled"]["m1_ok"] is False and st2["m1_passed_gates"] == [] and levels.G4 in st2["low_precision_gates"]
    log = (await db.execute(text("SELECT detail FROM ops.action_log WHERE org_id = :o AND action = 'intel.m1.nightly' ORDER BY at"), {"o": org})).scalars().all()
    assert len(log) == 2 and log[0]["m1_ok"] is True and log[1]["m1_ok"] is False


async def test_precision_ci_m1_ok_uses_ten_not_fifteen(db: AsyncSession) -> None:
    org = await mk_org(db)
    ids = await _shadow_ids(db, org, 10)
    for sid in ids:
        await _fb(db, org, sid, "correct")
        await db.execute(text("UPDATE biz.alerts SET would_push_at = :n WHERE id = :i"), {"n": NOW, "i": sid})
    m = await metrics.compute(db, org, days=14, now=NOW + timedelta(hours=1))
    g3 = next(g for g in m["precision_ci"] if g["gate"] == levels.G3)
    assert g3["n"] == 10 and g3["m1_ok"] is True and m["m1"]["pooled"]["n"] == 10


def test_nightly_job_is_registered() -> None:
    from gh import biz

    assert "intel_m1_nightly" in {fn.__name__ for fn, _ in biz.jobs()}


async def test_metrics_routes_owner_only_and_include_m1(owner_api: Api, client: httpx.AsyncClient, db: AsyncSession) -> None:
    r = await owner_api.get("/intel/metrics")
    assert r.status_code == 200 and r.json()["m1"]["pooled"]["n"] == 0 and r.json()["m1"]["rule_version"] == "r7"
    full = await owner_api.get("/intel/metrics/full")
    assert full.status_code == 200 and "m1" in full.json()
    mgr = await login_as(client, db, "manager")
    await verify_pin(mgr, "112233")
    assert (await mgr.get("/intel/metrics/full")).status_code == 403


# ─── (5) Console ──────────────────────────────────────────────────────────────

async def _mk_entity(db: AsyncSession, org: uuid.UUID, person: uuid.UUID, name: str) -> uuid.UUID:
    return (await db.execute(text("INSERT INTO mem.entities (org_id, entity_type, person_id, display_name) VALUES (:o, 'person', :p, :n) RETURNING id"),
                             {"o": org, "p": person, "n": name})).scalar_one()  # type: ignore[no-any-return]


async def _customer(db: AsyncSession, org: uuid.UUID, name: str, ptype: str = "customer") -> tuple[uuid.UUID, uuid.UUID]:
    pid = (await db.execute(text("INSERT INTO core.persons (org_id, code, display_name, person_type) VALUES (:o, core.next_code('PER'), :n, :t) RETURNING id"),
                            {"o": org, "n": name, "t": ptype})).scalar_one()
    return pid, await _mk_entity(db, org, pid, name)


async def test_setup_key_customers_step_lists_customers_only_and_hides_other_orgs(owner_api: Api, db: AsyncSession) -> None:
    org = await _org(db)
    await db.execute(text("UPDATE core.organizations SET settings = settings || '{\"intel\": {}}'::jsonb WHERE id = :o"), {"o": org})
    await _customer(db, org, "Chị Mai")
    await _customer(db, org, "Nhân viên Hùng", ptype="staff")
    other = await mk_org(db, "org khác")
    await _customer(db, other, "Khách org khác")
    await db.commit()
    s = (await owner_api.get("/intel/setup")).json()
    k = s["key_customers"]
    assert [c["name"] for c in k["customers"]] == ["Chị Mai"] and k["done"] is False and k["key_count"] == 0
    assert k["customers"][0]["key_fact_id"] is None and k["customers"][0]["suggested"] is False and k["customers"][0]["entity_id"]
    assert "Khách org khác" not in str(s) and s["ready"] is False


async def test_key_customer_flow_goes_through_profile_api_with_pin_then_ready(owner_api: Api, db: AsyncSession) -> None:
    org = await _org(db)
    await _customer(db, org, "Chị Mai")
    await db.execute(text("DELETE FROM mem.group_contexts WHERE org_id = :o"), {"o": org})
    await db.commit()
    k = (await owner_api.get("/intel/setup")).json()["key_customers"]
    ent = k["customers"][0]["entity_id"]
    r = await owner_api.send("POST", "/me/profile/items", {"section": "key_relations", "object_entity_id": ent})
    assert r.status_code == 423, r.text                                       # chưa mở PIN ⇒ KHÔNG ghi (cùng luật các thao tác Hồ sơ)
    assert (await owner_api.get("/intel/setup")).json()["key_customers"]["key_count"] == 0
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/me/profile/items", {"section": "key_relations", "object_entity_id": ent})
    assert r.status_code == 201, r.text
    k = (await owner_api.get("/intel/setup")).json()["key_customers"]
    c = k["customers"][0]
    assert k["key_count"] == 1 and c["key_fact_id"] and c["suggested"] is False
    assert uuid.UUID(ent) in (await profile_read.snapshot(db, org)).key_relation_entity_ids()          # owner_confirmed ⇒ levels thấy "khách chủ chốt"
    assert (await owner_api.send("POST", "/intel/setup/key-customers-confirmed", {})).json()["key_count"] == 1
    assert (await owner_api.get("/intel/setup")).json()["key_customers"]["done"] is True
    log = (await db.execute(text("SELECT detail, target_label FROM ops.action_log WHERE org_id = :o AND action = 'intel.setup.key_customers_confirmed'"), {"o": org})).one()
    assert log.detail == {"key_count": 1} and "Mai" not in str(log.detail) and log.target_label is None
    r = await owner_api.send("DELETE", f"/me/profile/items/{c['key_fact_id']}")
    assert r.status_code == 200 and (await owner_api.get("/intel/setup")).json()["key_customers"]["key_count"] == 0


async def test_key_customer_write_rejects_other_org_entity_and_non_owner(owner_api: Api, client: httpx.AsyncClient, db: AsyncSession) -> None:
    org = await _org(db)
    mgr = await login_as(client, db, "manager")                               # trước khi có tổ chức thứ hai (add_user chọn tổ chức duy nhất)
    await verify_pin(mgr, "112233")
    other = await mk_org(db, "org khác")
    _, foreign_ent = await _customer(db, other, "Khách org khác")
    _, mine = await _customer(db, org, "Chị Mai")
    await db.commit()
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/me/profile/items", {"section": "key_relations", "object_entity_id": str(foreign_ent)})
    assert r.status_code == 404, r.text                                       # thực thể tổ chức khác ⇒ không tồn tại
    assert (await mgr.send("POST", "/me/profile/items", {"section": "key_relations", "object_entity_id": str(mine)})).status_code == 403
    assert (await mgr.send("POST", "/intel/setup/key-customers-confirmed", {})).status_code == 403
    assert (await mgr.get("/intel/setup")).status_code == 403


async def test_setup_key_customers_shows_assistant_suggestion_as_proposed_only(owner_api: Api, db: AsyncSession) -> None:
    """Trợ lý chỉ GỢI Ý tên (`proposed`): hiện cờ `suggested`, KHÔNG phải khách chủ chốt, không vào Hồ sơ đã xác nhận."""
    org = await _org(db)
    _, ent = await _customer(db, org, "Chú Bảy")
    owner = (await entities.owner(db, org)).id
    await facts.assert_fact(db, mkfact(org, owner, predicate="owner_key_relation", cls="owner", proposed=True, object_entity=ent, speaker=owner))
    await db.commit()
    c = next(x for x in (await owner_api.get("/intel/setup")).json()["key_customers"]["customers"] if x["name"] == "Chú Bảy")
    assert c["suggested"] is True and c["key_fact_id"] is None
    assert ent not in (await profile_read.snapshot(db, org)).key_relation_entity_ids()


async def test_console_state_has_ramp_banner_data_and_shadow_items_with_pin_privacy(owner_api: Api, db: AsyncSession) -> None:
    org = await _org(db)
    now = datetime.now(VN)
    await db.execute(text("""UPDATE core.organizations SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb), '{intel}',
                             COALESCE(settings -> 'intel', '{}'::jsonb) || CAST(:s AS jsonb)) WHERE id = :o"""),
                     {"s": orjson.dumps({"installed_at": (now - timedelta(days=60)).isoformat(), "ramp_started_at": (now - timedelta(days=2)).isoformat()}).decode(), "o": org})
    pub = (await _g3_signal(db, org, "pub")).id                               # 🔴 G3 đang chạy thử (cài mới) ⇒ bóng
    priv = (await _g3_signal(db, org, "priv", privacy="console_pin")).id
    await db.commit()
    c = (await owner_api.get("/intel/console")).json()
    assert c["regular_push_enabled"] is True and c["ramp"]["in_trial"] is True and c["ramp"]["days"] == 14 and c["ramp"]["days_left"] in (12, 13)
    assert c["ramp"]["m1"]["min_n"] == 10 and c["ramp"]["m1"]["n"] == 0 and c["ramp"]["started_at"] and c["ramp"]["ends_at"]
    ids = {i["id"] for i in c["shadow_items"]}
    assert str(pub) in ids and str(priv) not in ids                           # console_pin chưa mở PIN ⇒ không lộ
    await verify_pin(owner_api)
    ids = {i["id"] for i in (await owner_api.get("/intel/console")).json()["shadow_items"]}
    assert {str(pub), str(priv)} <= ids


async def test_console_shadow_items_exclude_other_org_and_judged_flow_updates_m1(owner_api: Api, db: AsyncSession) -> None:
    org = await _org(db)
    mine = (await _g3_signal(db, org, "mine")).id
    other = await mk_org(db, "org khác")
    foreign = await _g3_signal(db, other, "f")
    await db.commit()
    c = (await owner_api.get("/intel/console")).json()
    assert {i["id"] for i in c["shadow_items"]} == {str(mine)} and str(foreign.id) not in str(c)
    r = await owner_api.send("POST", f"/signals/{mine}/feedback", {"verdict": "wrong", "reason": "staff_handled"})
    assert r.status_code == 200
    c = (await owner_api.get("/intel/console")).json()
    assert c["ramp"]["m1"]["n"] == 1 and c["ramp"]["m1"]["wrong_by_reason"]["staff_handled"] == 1
    assert mine not in {uuid.UUID(i["id"]) for i in c["shadow_items"]}        # đã chấm Sai ⇒ vụ đóng, rời danh sách chờ


async def test_console_ramp_ends_when_window_over_and_gates_passed(owner_api: Api, db: AsyncSession) -> None:
    org = await _org(db)
    now = datetime.now(VN)
    await db.execute(text("""UPDATE core.organizations SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb), '{intel}',
                             COALESCE(settings -> 'intel', '{}'::jsonb) || CAST(:s AS jsonb)) WHERE id = :o"""),
                     {"s": orjson.dumps({"installed_at": (now - timedelta(days=60)).isoformat(), "ramp_started_at": (now - timedelta(days=20)).isoformat(),
                                         "m1_passed_gates": list(levels.REGULAR_GATES)}).decode(), "o": org})
    await db.commit()
    r = (await owner_api.get("/intel/console")).json()["ramp"]
    assert r["in_trial"] is False and r["days_left"] == 0 and set(r["passed_gates"]) == set(levels.REGULAR_GATES)


async def test_put_settings_ramp_push_now_allowed_for_owner_with_pin(owner_api: Api) -> None:
    await verify_pin(owner_api)
    r = await owner_api.send("PUT", "/intel/settings", {"ramp_gates": "push"})
    assert r.status_code == 200 and r.json()["ramp_gates"] == "push"
    assert (await owner_api.get("/intel/console")).json()["ramp_gates"] == "push"
    assert (await owner_api.send("PUT", "/intel/settings", {"ramp_gates": "digest_top"})).status_code == 200


async def test_console_py_message_has_no_version_number() -> None:
    import inspect

    from gh.biz.intel import console

    src = inspect.getsource(console)
    assert "v0.1.76" not in src.split("class SettingsIn")[1].split("# ─── xác minh STK")[0]


def test_merge_new_value_wins_nudged_by_customer_true_in_new_is_kept() -> None:
    """P3b `refresh_staff_commitments` dựng lại thẻ qua upsert: giá trị có ở bản MỚI vẫn thắng (không bị bản cũ trống ghi đè)."""
    old = _li("nghia_vu_loi_hua")
    new = _li("nghia_vu_loi_hua", promise={"to_customer": True, "nudged_by_customer": True})
    m = signals.merge_level_input(old, new)
    assert m.promise.nudged_by_customer is True and m.promise.to_customer is True
    assert signals.merge_level_input(new, old).promise.nudged_by_customer is True        # đã nhắc thì giữ (OR), không bị bản sau vắng làm rơi
    m2 = signals.merge_level_input(_li("xung_dot_khieu_nai", complaint_stage="voice"), _li("xung_dot_khieu_nai", complaint_stage="exit_pending", staff_ref="u_n"))
    assert m2.complaint_stage == "exit_pending"                                           # bản mới có giá trị ⇒ theo bản mới


# ─── review P4: banner, action ở kênh chính, checked_action, nightly 30 ngày ──

async def test_ramp_banner_new_install_is_trial_not_renewed_and_early_push_turns_it_off(owner_api: Api, db: AsyncSession) -> None:
    org = await _org(db)
    now = datetime.now(VN)

    async def ramp(**intel: Any) -> dict[str, Any]:
        await db.execute(text("UPDATE core.organizations SET settings = settings - 'intel' WHERE id = :o"), {"o": org})
        await _settings(db, org, **intel)
        await db.commit()
        return (await owner_api.get("/intel/console")).json()["ramp"]  # type: ignore[no-any-return]

    r = await ramp(installed_at=(now - timedelta(days=3)).isoformat())                  # cài mới, chưa có ramp ⇒ fallback ngày cài, "chạy thử" (không "lại")
    assert r["in_trial"] is True and r["renewed"] is False and r["days_left"] in (11, 12) and r["started_at"]
    r = await ramp(installed_at=(now - timedelta(days=60)).isoformat(), ramp_started_at=(now - timedelta(days=3)).isoformat())
    assert r["in_trial"] is True and r["renewed"] is True                               # cài lâu, ramp mới ⇒ chạy thử LẠI
    r = await ramp(installed_at=(now - timedelta(days=60)).isoformat(), ramp_started_at=(now - timedelta(days=3)).isoformat(), ramp_gates="push")
    assert r["in_trial"] is False and r["early_push"] is True                           # Sếp chọn nhắn ngay sớm ⇒ banner tắt
    r = await ramp(installed_at=(now - timedelta(days=60)).isoformat(), ramp_started_at=(now - timedelta(days=20)).isoformat(),
                   m1_passed_gates=list(levels.REGULAR_GATES), mode="shadow")
    assert r["in_trial"] is True                                                         # chế độ chạy thử toàn bộ
    r = await ramp(installed_at=(now - timedelta(days=60)).isoformat(), ramp_started_at=(now - timedelta(days=20)).isoformat(), m1_passed_gates=[])
    assert r["in_trial"] is True                                                         # qua 14 ngày nhưng chưa cổng nào đạt M1
    r = await ramp(installed_at=(now - timedelta(days=60)).isoformat(), ramp_started_at=(now - timedelta(days=20)).isoformat(),
                   m1_passed_gates=list(levels.REGULAR_GATES))
    assert r["in_trial"] is False and r["red_cap_per_day"] == 3


def test_checked_action_review_rules_copy_accents_and_price_pattern() -> None:
    q = ["Khách nói giao hàng trễ quá em không chịu được nữa đâu"]
    assert digest.checked_action("Khách nói giao hàng trễ quá em không chịu", q) is None          # ≥ 6 từ liền trùng lời khách
    assert digest.checked_action("Gọi khách xin lỗi vụ trễ", q) == "Gọi khách xin lỗi vụ trễ"
    for bad in ("CHOT DON luon", "Moi khach mua them", "chao ban combo", "Chốt Đơn ngay", "BÁN THÊM phụ kiện"):
        assert digest.checked_action(bad, None) is None, bad
    for ok in ("giá bao nhiêu vậy shop", "cho em xin báo giá", "bao nhiêu tiền một bao", "giá sao anh", "hỏi giá giúp em"):
        assert digest.checked_action("Báo giá cho khách", ok) == "Báo giá cho khách", ok
    assert digest.checked_action("Báo giá cho khách", "khách chê trễ") is None
    assert digest.checked_action("Báo giá cho khách", ["giá của anh cao quá"]) is None           # "giá" trơn không phải câu hỏi giá


async def test_digest_fraud_and_safety_always_use_fixed_template_even_with_model_action(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _row(db, org, "fr", kind=levels.FR, level="red", gate=levels.G1, reasons=["window_expired"], action="Gọi khách xác nhận giờ giao")
    await _row(db, org, "sk", kind=levels.SK, level="red", gate=levels.G2, action="Nhắn tin hỏi thăm")
    acts = {it.kind: it.action for it in (await _order(db, org)).items}
    assert acts[levels.FR] == "Gọi xác minh trực tiếp, chưa chuyển tiền/giao hàng" and acts[levels.SK] == "Gọi hỏi tình hình, cần thì gọi 115"


async def test_digest_staff_owned_line_in_digest_group_also_names_staff(db: AsyncSession) -> None:
    org = await mk_org(db)
    pid = (await db.execute(text("INSERT INTO core.persons (org_id, code, display_name, person_type) VALUES (:o, core.next_code('PER'), 'Lan', 'staff') RETURNING id"),
                            {"o": org})).scalar_one()
    await _row(db, org, "n", kind="xung_dot_khieu_nai", delivery="digest", reasons=["staff_owned"], staff_ref=f"u_{pid}", title="Khách chê")
    [it] = (await _order(db, org)).items
    assert it.title == "NV Lan đang lo: Khách chê" and it.action == "Không cần làm gì, NV Lan đang lo"


async def test_zalo_digest_text_prints_owner_action_line_masked_and_within_ten_lines(db: AsyncSession) -> None:
    from gh.biz.intel.pipes import delivery

    org = await mk_org(db)
    for i in range(4):
        await _row(db, org, f"a{i}", delivery="digest", action="Gọi khách theo số 0912345678 xác nhận giờ giao" if i == 0 else None)
    d = await _order(db, org)
    text_, shown = delivery.render(d, label="07:30", shadow=False, hidden_console=0, coverage_line="Độ phủ: ok", recovered=[])
    assert "Việc Sếp cần làm:" in text_ and "0912345678" not in text_              # che số như quote
    assert len(text_.splitlines()) <= 10 and len(text_) <= 1500 and shown


async def test_owner_chat_hom_nay_prints_owner_action_line(db: AsyncSession) -> None:
    from types import SimpleNamespace

    from gh.db import sessionmaker
    from gh.owner_cmd import core

    org = await mk_org(db)
    await _row(db, org, "a", delivery="digest", action="Gọi khách xác nhận giờ giao")
    await db.commit()
    lines = await core.digest_lines(SimpleNamespace(sm=sessionmaker(), org_id=org), NOW)  # type: ignore[arg-type]
    assert any(ln.strip().startswith("Việc Sếp cần làm: Gọi khách xác nhận giờ giao") for ln in lines)


async def test_m1_latest_owner_verdict_decides_even_if_not_correct_wrong(db: AsyncSession) -> None:
    org = await _ramp_org(db, NOW - timedelta(days=5))
    [a, b] = await _shadow_ids(db, org, 2)
    await _fb(db, org, a, "correct")
    await _fb(db, org, a, "unimportant", at=NOW + timedelta(minutes=1))                # bản mới nhất là "không quan trọng" ⇒ không còn tính Đúng
    await _fb(db, org, b, "wrong", reason="not_urgent")
    await _fb(db, org, b, "correct", at=NOW + timedelta(minutes=1))
    m = await metrics.m1_ramp(db, org)
    assert (m["pooled"]["k"], m["pooled"]["n"]) == (1, 1)


async def test_console_shadow_items_exclude_really_pushed(owner_api: Api, db: AsyncSession) -> None:
    org = await _org(db)
    sid = (await _g3_signal(db, org, "p")).id
    await db.commit()
    assert {i["id"] for i in (await owner_api.get("/intel/console")).json()["shadow_items"]} == {str(sid)}
    await db.execute(text("UPDATE biz.alerts SET pushed_at = now() WHERE id = :i"), {"i": sid})
    await db.commit()
    assert (await owner_api.get("/intel/console")).json()["shadow_items"] == []


async def test_nightly_stalled_alert_once_per_gate_after_30_days_via_owner_channel(db: AsyncSession) -> None:
    from tests.owner_helpers import bot_world

    org = await _ramp_org(db, NOW - timedelta(days=40))
    await _raw(db, org)                                                                     # tạo kênh trước khi dựng bot
    await bot_world(db, org)
    ids = await _shadow_ids(db, org, 3)
    for sid in ids:
        await _fb(db, org, sid, "correct", gate=levels.G3)
    m = await metrics.run_m1_nightly(db, org, NOW)
    assert set(m["stalled_notified"]) == set(levels.REGULAR_GATES)                      # chưa đạt M1 sau > 30 ngày ⇒ báo từng loại
    rows = (await db.execute(text("SELECT text, dedupe_key, kind FROM ops.owner_outbox WHERE org_id = :o AND dedupe_key LIKE 'intel-m1-stalled:%'"), {"o": org})).all()
    assert len(rows) == 4 and all(r.kind == "alert" and "30 ngày" in r.text and "3/10" in r.text for r in rows)
    assert "khách chê" in " ".join(r.text for r in rows)
    m2 = await metrics.run_m1_nightly(db, org, NOW + timedelta(days=1))                  # đêm sau: không lặp
    assert m2["stalled_notified"] == []
    assert (await db.execute(text("SELECT count(*) FROM ops.owner_outbox WHERE org_id = :o AND dedupe_key LIKE 'intel-m1-stalled:%'"), {"o": org})).scalar_one() == 4
    assert (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE org_id = :o AND action = 'intel.m1.stalled'"), {"o": org})).scalar_one() == 4
    # nhật ký đêm chỉ khi danh sách đổi: hai đêm giống nhau ⇒ một dòng
    assert (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE org_id = :o AND action = 'intel.m1.nightly'"), {"o": org})).scalar_one() == 1


async def test_nightly_no_stalled_alert_before_30_days_or_without_channel_or_when_passed(db: AsyncSession) -> None:
    from tests.owner_helpers import bot_world

    young = await _ramp_org(db, NOW - timedelta(days=20))
    await _raw(db, young)                                                                     # tạo kênh trước khi dựng bot
    await bot_world(db, young)
    assert (await metrics.run_m1_nightly(db, young, NOW))["stalled_notified"] == []
    nochan = await _ramp_org(db, NOW - timedelta(days=40))                               # chưa bật kênh quản trị ⇒ không xếp
    assert (await metrics.run_m1_nightly(db, nochan, NOW))["stalled_notified"] == []
    done = await _ramp_org(db, NOW - timedelta(days=40))
    await _raw(db, done)                                                                     # tạo kênh trước khi dựng bot
    await bot_world(db, done)
    for sid in await _shadow_ids(db, done, 10):
        await _fb(db, done, sid, "correct", gate=levels.G3)
    m = await metrics.run_m1_nightly(db, done, NOW)
    assert m["pooled"]["m1_ok"] and m["stalled_notified"] == []
