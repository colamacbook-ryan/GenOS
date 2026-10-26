# ruff: noqa: E501
"""v0.1.77 (gói P1) — `levels.compute` đường bảng r7.2 (§4.2.1 (a′), (b), (b′), (d′), (g)). Hàm thuần: không DB.

Mỗi bất biến B1–B18 của (g) có ≥ 1 test (tên `test_b<n>_*`); mỗi hàm thuần mới (`prio`, `red_type`, `act_by`, `public_ok`, `deadline_eff`,
`next_change_at`) có test riêng. Ví dụ dựng tay từ spec, KHÔNG chép đáp án kịch bản chấm ngoài repo."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta
from typing import Any

import pytest

from gh.biz.intel import levels as L
from tests.intel_helpers import NOW, compute, person, policy, profile, sig, state
from tests.test_levels_vectors import VECTORS
from tests.test_levels_vectors import _policy as _vec_policy
from tests.test_levels_vectors import _profile as _vec_profile
from tests.test_levels_vectors import _signal as _vec_signal
from tests.test_levels_vectors import _state as _vec_state

G1, G2, G3, G4, G5, G6 = L.GATES
H = timedelta(hours=1)
CUST = {"customer_of_owner": True, "impact_base": 2}


def cmp_(kind: str, **kw: Any) -> L.SignalIn:
    """Khiếu nại của khách của Sếp (đường bảng, loại G3). `flags`, `complaint_stage`… truyền qua kw."""
    return sig(kind, **{**CUST, **kw})


def promise(*, due_in_h: float | None = 3, ago_h: float = 5, flags: dict[str, Any] | None = None, **pm: Any) -> L.SignalIn:
    pm = {"to_customer": True, **pm}
    return sig("nghia_vu_loi_hua", ago_h=ago_h, due_in_h=due_in_h, impact_base=2, flags=flags or {}, promise=pm)


def opp(*, due_in_h: float | None = 20, **kw: Any) -> L.SignalIn:
    o = {"catalog_score": 85, **kw.pop("opportunity", {})}
    return sig("co_hoi_ket_noi", due_in_h=due_in_h, impact_base=2, opportunity=o, **kw)


def work(**kw: Any) -> L.SignalIn:
    w = {"type": "deadline_today", "owner_work": True, "late_signs": True, **kw.pop("work", {})}
    return sig("viec_han", due_in_h=kw.pop("due_in_h", 2), impact_base=2, work=w, **kw)


def key(**kw: Any) -> Any:
    import uuid
    return uuid.uuid5(uuid.NAMESPACE_URL, "khach-chu-chot")


def pr(s: L.SignalIn, **kw: Any) -> L.Level:
    return compute(s, **kw)


# ─── hàm thuần mới ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("i", "u", "p"), [(3, 3, 1), (3, 2, 2), (3, 1, 3), (2, 3, 2), (2, 2, 3), (2, 1, 4), (1, 3, 4), (1, 2, 4), (1, 1, 5)])
def test_levels_prio_table(i: int, u: int, p: int) -> None:
    assert L.prio(i, u) == p


def test_levels_prio_cell_i1_u3_is_p4_not_servicenow_p3() -> None:
    assert L.prio(1, 3) == 4


def test_levels_prio_clamps_out_of_range() -> None:
    assert L.prio(9, 9) == 1 and L.prio(0, 0) == 5


def test_levels_red_type_four_types() -> None:
    assert L.red_type(cmp_("xung_dot_khieu_nai"), None, NOW) == G3
    assert L.red_type(promise(), None, NOW) == G4
    assert L.red_type(opp(), None, NOW) == G5
    assert L.red_type(work(), None, NOW) == G6


def test_levels_red_type_null_outside_types() -> None:
    assert L.red_type(sig("su_kien_dip"), None, NOW) is None
    assert L.red_type(sig("xung_dot_khieu_nai"), None, NOW) is None          # không phải khách của Sếp
    assert L.red_type(promise(due_in_h=30), None, NOW) is None               # hạn không phải hôm nay
    assert L.red_type(opp(opportunity={"catalog_score": 40}), None, NOW) is None
    assert L.red_type(opp(due_in_h=-1), None, NOW) is None                   # hạn đã qua ⇒ đã trôi
    assert L.red_type(work(work={"owner_work": False}), None, NOW) is None


def test_levels_red_type_g4_past_day_is_null_broken_promise() -> None:
    assert L.red_type(promise(due_in_h=-20), None, NOW) is None              # hạn hôm trước ⇒ t = null (lời hứa đã vỡ)


def test_levels_red_type_respects_policy() -> None:
    s = opp(opportunity={"catalog_score": 85})
    assert L.red_type(s, policy("kinh_doanh"), NOW) == G5
    assert L.red_type(s, policy("gia_dinh"), NOW) is None                    # bối cảnh tắt G5
    assert L.red_type(s, policy(None), NOW) is None                          # nhóm chưa xác nhận, catalog một mình không đủ… nhưng cần Hồ sơ khớp
    assert L.red_type(opp(opportunity={"catalog_score": 85, "resource_owner_confirmed": True}), policy(None), NOW) == G5


def test_levels_red_type_day_only_deadline_uses_eff() -> None:
    """Hạn chỉ ngày hôm nay (đã qua 0:00) ⇒ 'hôm nay' trên `deadline_eff` = 17:30."""
    s = promise(due_in_h=-1).model_copy(update={"deadline_granularity": "day"})
    assert L.red_type(s, None, NOW) == G4
    assert L.red_type(promise(due_in_h=-1).model_copy(update={"deadline_at": NOW - 30 * H}), None, NOW) is None


def test_levels_deadline_eff_day_only_is_work_end() -> None:
    s = sig("viec_han", deadline_at=NOW.replace(hour=0, minute=0), deadline_granularity="day")
    assert L.deadline_eff(s, state()) == NOW.replace(hour=17, minute=30)
    st = state(work_hours={"start": "09:00", "end": "18:00"})
    assert L.deadline_eff(s, st) == NOW.replace(hour=18, minute=0)


def test_levels_deadline_eff_other_granularities_unchanged() -> None:
    for g in ("minute", "hour", "part_of_day", "week", None):
        s = sig("viec_han", deadline_at=NOW + H, deadline_granularity=g)
        assert L.deadline_eff(s, state()) == NOW + H
    assert L.deadline_eff(sig("viec_han"), state()) is None


def test_levels_act_by_g3_ongoing_case_now() -> None:
    s = cmp_("xung_dot_khieu_nai", complaint_stage="exit_pending", flags={"said_will_leave": True})
    assert L.act_by(s, state(), NOW, policy()) == NOW


def test_levels_act_by_g3_voice_asks_for_owner_now() -> None:
    assert L.act_by(cmp_("xung_dot_khieu_nai", complaint_stage="voice", asks_for_owner=True), state(), NOW, policy()) == NOW


def test_levels_act_by_g3_voice_without_asks_is_deadline_or_null() -> None:
    assert L.act_by(cmp_("xung_dot_khieu_nai", complaint_stage="voice"), state(), NOW, policy()) is None
    s = cmp_("xung_dot_khieu_nai", complaint_stage="voice", due_in_h=20)
    assert L.act_by(s, state(), NOW, policy()) == NOW + 20 * H


def test_levels_act_by_g3_public_not_vent_now_vent_not() -> None:
    s = cmp_("xung_dot_khieu_nai", complaint_stage="voice", flags={"public": True})
    assert L.act_by(s, state(), NOW, policy()) == NOW
    v = cmp_("xung_dot_khieu_nai", complaint_stage="vent", flags={"public": True})
    assert L.act_by(v, state(), NOW, policy()) is None


def test_levels_act_by_g3_older_than_24h_falls_to_deadline() -> None:
    s = cmp_("xung_dot_khieu_nai", ago_h=30, complaint_stage="exit_pending")
    assert L.act_by(s, state(), NOW, policy()) is None


def test_levels_act_by_g5_explicit_urgent_now_unless_past() -> None:
    assert L.act_by(opp(due_in_h=None, flags={"explicit_urgent": True}), state(), NOW, policy()) == NOW
    assert L.act_by(opp(due_in_h=20, flags={"explicit_urgent": True}), state(), NOW, policy()) == NOW
    assert L.act_by(opp(due_in_h=-2, flags={"explicit_urgent": True}), state(), NOW, policy()) == NOW - 2 * H


def test_levels_act_by_ongoing_now() -> None:
    assert L.act_by(sig("su_kien_dip", ongoing=True), state(), NOW) == NOW


def test_levels_act_by_deadline_and_none() -> None:
    assert L.act_by(work(due_in_h=5), state(), NOW, policy()) == NOW + 5 * H
    assert L.act_by(sig("viec_han"), state(), NOW) is None


def test_levels_act_by_day_only_uses_work_end() -> None:
    s = sig("viec_han", deadline_at=NOW.replace(hour=0, minute=0), deadline_granularity="day")
    assert L.act_by(s, state(), NOW) == NOW.replace(hour=17, minute=30)


def test_levels_public_ok_requires_flag() -> None:
    assert L.public_ok(sig("xung_dot_khieu_nai"), policy()) is False


def test_levels_public_ok_venue_null_is_r661_compat() -> None:
    assert L.public_ok(sig("xung_dot_khieu_nai", flags={"public": True}), policy()) is True


def test_levels_public_ok_social_authority_always() -> None:
    for v in ("social", "authority"):
        s = sig("xung_dot_khieu_nai", public_venue=v, flags={"public": True}, thread={"type": "dm_owner"})
        assert L.public_ok(s, policy()) is True


def test_levels_public_ok_group_needs_audience_and_group() -> None:
    f = {"public": True}
    assert L.public_ok(sig("xung_dot_khieu_nai", public_venue="group", flags=f), policy()) is True          # audience null = không chặn
    assert L.public_ok(sig("xung_dot_khieu_nai", public_venue="group", flags=f, thread={"type": "group", "customer_audience_n": 2}), policy()) is False
    assert L.public_ok(sig("xung_dot_khieu_nai", public_venue="group", flags=f, thread={"type": "group", "customer_audience_n": 3}), policy()) is True
    assert L.public_ok(sig("xung_dot_khieu_nai", public_venue="group", flags=f, thread={"type": "dm_owner"}), policy()) is False


def test_levels_public_ok_confirmed_nhan_vien_blocks_group_but_suggested_does_not() -> None:
    s = sig("xung_dot_khieu_nai", public_venue="group", flags={"public": True})
    assert L.public_ok(s, policy("nhan_vien")) is False
    assert L.public_ok(s, policy(None)) is True                                # nhãn gợi ý không vào Policy (AMB-17)
    assert L.public_ok(s, policy("kinh_doanh")) is True


# ─── B1/B2: Bước 0 giữ nguyên, không đọc trường r7 ─────────────────────────────

def test_b1_routing_step0_vs_table() -> None:
    assert L.is_step0(sig("rui_ro_lua_dao")) and L.is_step0(sig("suc_khoe_an_toan")) and L.is_step0(sig(None))
    assert L.is_step0(sig("nghia_vu_loi_hua", flags={"credentials_entered": True}))
    assert L.is_step0(sig("viec_han", flags={"account_takeover": True}))
    assert L.is_step0(sig("nghia_vu_loi_hua", flags={"emergency": True}))
    assert L.is_step0(sig("co_hoi_ket_noi", hard_rule_hit=["stk_khac_lich_su+money_request"]))
    assert L.is_step0(sig("co_hoi_ket_noi", hard_rule_hit=["emergency_115_chay_cap_cuu"]))     # luật cứng kéo cờ emergency
    assert not L.is_step0(sig("xung_dot_khieu_nai"))
    assert not L.is_step0(sig("viec_han", flags={"money_request": True}))                       # money_request một mình không mở cổng 1


def test_b1_step0_compute_is_exactly_step0_function() -> None:
    cases = [sig("rui_ro_lua_dao", flags={"credentials_entered": True}), sig("suc_khoe_an_toan", flags={"emergency": True}, impact_base=3),
             sig("nghia_vu_loi_hua", flags={"emergency": True}, promise={"to_customer": True}, due_in_h=3, impact_base=2), sig(None)]
    for s in cases:
        a = L.compute(s, profile(), policy(), state(), NOW)
        b = L._compute_step0(s, profile(), policy(), state(), NOW)
        assert a.model_dump() == b.model_dump() and a.branch_rank == b.branch_rank


def test_b1_locked_vectors_pass_via_compute() -> None:
    locked = [v for v in VECTORS if (v["gate_under_test"] in {G1, G2} and "locked_since" not in v) or "locked_since" in v]
    assert len(locked) == 150
    for v in locked:
        i = v["input"]
        p = i["profile"]
        out = L.compute(_vec_signal(i["signal"], p), _vec_profile(p), _vec_policy(i["ctx"]), _vec_state(i["state"], p), datetime.fromisoformat(i["now"]))
        assert (out.level, out.gate, out.candidate, out.delivery) == tuple(v["expected"][k] for k in ("level", "gate", "candidate", "delivery")), v["id"]


R7_PERTURB: dict[str, Any] = {
    "complaint_stage": "exit_done", "asks_for_owner": True, "public_venue": "authority", "prior_threat_no_exit_n": 7, "staff_owned": True,
    "staff_ref": "nv-x", "deadline_granularity": "day", "same_customer_day_red": True,
}


def test_b2_changing_every_r7_field_does_not_change_step0_results() -> None:
    n = 0
    off_step0: list[str] = []
    for v in VECTORS:
        if v["gate_under_test"] not in {G1, G2} and "locked_since" not in v:
            continue
        i = v["input"]
        p = i["profile"]
        base = _vec_signal(i["signal"], p)
        pert = copy.deepcopy(i["signal"])
        pert.update(R7_PERTURB)
        pert.setdefault("promise", {}).update(nudged_by_customer=True, fixed_occasion=True)
        pert.setdefault("opportunity", {})["need_fields_n"] = 0
        pert.setdefault("thread", {})["customer_audience_n"] = 0
        st = _vec_state(i["state"], p)
        st2 = st.model_copy(update={"work_hours": L.WorkHours(start="00:00", end="01:00")})
        now = datetime.fromisoformat(i["now"])
        a = L.compute(base, _vec_profile(p), _vec_policy(i["ctx"]), st, now)
        b = L.compute(_vec_signal(pert, p), _vec_profile(p), _vec_policy(i["ctx"]), st2, now)
        if L.is_step0(base):
            assert a.model_dump() == b.model_dump() and a.branch_rank == b.branch_rank, v["id"]
        else:                                   # LV-R64-09: `nghia_vu_loi_hua` chỉ có `money_request` ⇒ không mở cổng 1 ⇒ đường bảng (b); kết quả không đổi
            assert (a.level, a.gate, a.candidate, a.delivery) == (b.level, b.gate, b.candidate, b.delivery), v["id"]
            off_step0.append(v["id"])
        n += 1
    assert n == 150 and off_step0 == ["LV-R64-09"]


def test_b2_dang_xu_ly_staff_does_not_remediate_step0() -> None:
    """NV 'để em lo' (`staff_owned`) dưới 'em lỡ nhập OTP' ⇒ vẫn 🔴 G1, Bước 0 không đọc cửa Thẩm quyền."""
    s = sig("rui_ro_lua_dao", flags={"credentials_entered": True}, hard_rule_hit=["credentials_entered"], staff_owned=True, staff_ref="nv", impact_base=3)
    assert compute(s).gate == G1 and compute(s).level == "red"


# ─── B3: sàn lừa đảo/an toàn ───────────────────────────────────────────────────

def test_b3_floor_fraud_safety_never_grey_or_off_sweep() -> None:
    for kind, fl in (("rui_ro_lua_dao", {"credentials_entered": True}), ("suc_khoe_an_toan", {"emergency": True})):
        for ago in (1, 30, 100, 200, 300):
            for ctx in ("kinh_doanh", "gia_dinh", None):
                lv = compute(sig(kind, ago_h=ago, flags=fl, impact_base=3), ctx=ctx)
                assert lv.level in ("red", "amber"), (kind, ago, ctx, lv)
    # hai đường xuống ⚪ duy nhất là của Sếp
    s = sig("rui_ro_lua_dao", flags={"credentials_entered": True}, impact_base=3)
    assert compute(s.model_copy(update={"owner_resolved": True})).delivery == "none"
    assert compute(s.model_copy(update={"mute": "pin"})).delivery == "digest_low"
    assert compute(s.model_copy(update={"mute": "chat"})).level == "red"


# ─── B4: bóng 14 ngày ──────────────────────────────────────────────────────────

def _g5_p1(**st: Any) -> L.Level:
    return compute(opp(due_in_h=None, flags={"explicit_urgent": True}, opportunity={"catalog_score": 85}), st=state(**st))


def test_b4_shadow_until_ramp_14_days() -> None:
    lv = _g5_p1(days_since_install=3)
    assert (lv.level, lv.gate, lv.delivery, lv.would_push) == ("red", G5, "digest_top", True) and "shadow" in lv.reasons


def test_b4_live_after_14_days_and_m1() -> None:
    lv = _g5_p1(days_since_install=14, m1_passed_gates=[G5])
    assert (lv.delivery, lv.would_push) == ("push_now", False)
    assert _g5_p1(days_since_install=14, m1_passed_gates=[]).would_push is True         # chưa qua M1 ⇒ vẫn bóng
    assert _g5_p1(days_since_install=2, ramp_early_enable=True).delivery == "push_now"


def test_b4_shadow_mode_and_fraud_bypass() -> None:
    assert _g5_p1(intel_mode="shadow", days_since_install=40, m1_passed_gates=[G5]).would_push is True
    f = compute(sig("rui_ro_lua_dao", flags={"credentials_entered": True}, impact_base=3), st=state(intel_mode="shadow", days_since_install=0))
    assert f.delivery == "push_now" and not f.would_push


def test_b4_regular_push_flag_is_on_with_p4_ramp() -> None:
    assert L.REGULAR_PUSH_ENABLED is True                   # P4 đã bật cùng ramp R4


# ─── B5: Q15 ───────────────────────────────────────────────────────────────────

def test_b5_q15_g3_over_24h_window_expired_p2() -> None:
    lv = compute(cmp_("xung_dot_khieu_nai", ago_h=30, complaint_stage="exit_pending", flags={"said_will_leave": True}))
    assert (lv.level, lv.delivery, lv.candidate) == ("amber", "digest_top", G3) and {"window_expired", "prio_p2"} <= set(lv.reasons)


def test_b5_q15_boundary_23h_red_24h_amber() -> None:
    kw = {"complaint_stage": "exit_pending", "flags": {"said_will_leave": True}}
    assert compute(cmp_("xung_dot_khieu_nai", ago_h=23, **kw)).level == "red"
    assert compute(cmp_("xung_dot_khieu_nai", ago_h=24, **kw)).level == "amber"


def test_b5_q15_only_staff_gate_applies_after() -> None:
    lv = compute(cmp_("xung_dot_khieu_nai", ago_h=30, complaint_stage="exit_pending", staff_owned=True, flags={"said_will_leave": True}))
    assert lv.level == "grey" and "staff_owned" in lv.reasons and "window_expired" in lv.reasons


def test_b5_q15_beyond_h_san_goes_by_table() -> None:
    lv = compute(cmp_("xung_dot_khieu_nai", ago_h=340, complaint_stage="exit_pending", flags={"said_will_leave": True}))
    assert "window_expired" not in lv.reasons


# ─── B6: Q16 ───────────────────────────────────────────────────────────────────

def test_b6_q16_obstacle_nudged_today_red_regular_customer() -> None:
    lv = compute(promise(due_in_h=5, obstacle_reported=True, nudged_by_customer=True))
    assert (lv.level, lv.gate) == ("red", G4) and "prio_p1" in lv.reasons


def test_b6_q16_overdue_nudged_red() -> None:
    lv = compute(promise(due_in_h=-1, nudged_by_customer=True))
    assert (lv.level, lv.gate) == ("red", G4)


def test_b6_q16_overdue_not_nudged_is_amber_top_p2() -> None:
    lv = compute(promise(due_in_h=-1))
    assert (lv.level, lv.delivery) == ("amber", "digest_top") and "prio_p2" in lv.reasons


def test_b6_q16_no_obstacle_not_overdue_is_not_red() -> None:
    assert compute(promise(due_in_h=5, nudged_by_customer=True)).level != "red"


def test_b6_q16_only_for_g4_not_when_promise_to_non_customer() -> None:
    s = sig("nghia_vu_loi_hua", due_in_h=-1, impact_base=2, promise={"to_customer": False, "nudged_by_customer": True})
    assert compute(s).gate is None


# ─── B7/B8: D1, D2, D-T3, D5 ───────────────────────────────────────────────────

def test_b7_d1_hold_past_48h_until_end_of_due_day() -> None:
    s = sig("rui_ro_lua_dao", ago_h=60, deadline_at=NOW + 6 * H, txn_state="pending", flags={"money_request": True}, impact_base=3)
    s = s.model_copy(update={"thread": L.Thread(type="dm_owner")})
    lv = compute(s)
    assert (lv.level, lv.gate) == ("red", G1)


def test_b7_d2_impersonation_goods_order() -> None:
    s = sig("rui_ro_lua_dao", goods_risk=True, impersonated=[person("khach", customer=True)], impact_base=3,
            victims=[person("owner", is_owner=True)], harm_what="giao hàng ghi nợ cho kẻ mạo danh")
    lv = compute(s)
    assert (lv.level, lv.gate) == ("red", G1)


def test_b7_dt3_unknown_group_size_is_near_group() -> None:
    s = sig("suc_khoe_an_toan", flags={"emergency": True}, thread={"type": "group", "size": 0, "size_known": False, "owner_in_thread": True},
            victims=[person("v", customer=True)], impact_base=3)
    assert compute(s, ctx="kinh_doanh").gate == G2


def test_b8_d5_big_opportunity_unconfirmed_group_min_amber() -> None:
    s = sig("co_hoi_ket_noi", due_in_h=30, impact_base=1, money_vnd=80_000_000, money_basis="quoted", flags={"explicit_urgent": False})
    lv = compute(s, ctx=None)
    assert (lv.level, lv.candidate) == ("amber", G5) and "big_opportunity_no_catalog" in lv.reasons


def test_b8_d5_not_for_confirmed_context_or_estimated_money() -> None:
    s = sig("co_hoi_ket_noi", due_in_h=30, impact_base=1, money_vnd=80_000_000, money_basis="estimated")
    assert "big_opportunity_no_catalog" not in compute(s, ctx=None).reasons


def test_b8_d5_keeps_48h_on_deadline_at_not_eff() -> None:
    d = (NOW + timedelta(days=3)).replace(hour=0, minute=0)
    s = sig("co_hoi_ket_noi", deadline_at=d, deadline_granularity="day", impact_base=1, money_vnd=80_000_000, money_basis="quoted")
    assert "big_opportunity_no_catalog" not in compute(s, ctx=None).reasons


# ─── B9: trần 3 🔴/ngày ────────────────────────────────────────────────────────

@pytest.mark.parametrize("mk", [lambda: promise(due_in_h=-1, nudged_by_customer=True),
                                lambda: opp(due_in_h=None, flags={"explicit_urgent": True})])
def test_b9_cap_three_reds_demotes_to_amber_top(mk: Any) -> None:
    lv = compute(mk(), st=state(reds_pushed_today=3))
    assert (lv.level, lv.delivery, lv.capped, lv.demoted) == ("amber", "digest_top", True, "cap") and lv.gate is not None


def test_b9_cap_not_applied_to_fraud() -> None:
    lv = compute(sig("rui_ro_lua_dao", flags={"credentials_entered": True}, impact_base=3), st=state(reds_pushed_today=9))
    assert lv.delivery == "push_now" and not lv.capped


def test_b9_quiet_hours_digest_morning_and_precision() -> None:
    night = NOW.replace(hour=23)
    assert compute(opp(due_in_h=None, flags={"explicit_urgent": True}), now=night).delivery == "digest_morning"
    lv = compute(opp(due_in_h=None, flags={"explicit_urgent": True}), st=state(low_precision_gates=[G5]))
    assert (lv.delivery, lv.demoted) == ("digest_top", "precision")


# ─── B10: S22 ──────────────────────────────────────────────────────────────────

def test_b10_urgent_catalog_need_absent_or_enough_is_red() -> None:
    for nf in (None, 3, 4):
        lv = compute(opp(due_in_h=None, flags={"explicit_urgent": True}, opportunity={"catalog_score": 85, "need_fields_n": nf}))
        assert (lv.level, lv.gate) == ("red", G5), nf


def test_b10_need_unclear_caps_p3_when_known_and_low() -> None:
    lv = compute(opp(due_in_h=None, flags={"explicit_urgent": True}, opportunity={"catalog_score": 85, "need_fields_n": 2}))
    assert (lv.level, lv.delivery) == ("amber", "digest") and "need_unclear" in lv.reasons


# ─── B11: ngoài 6 mã không 🔴 ──────────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["su_kien_dip", "thi_truong_thong_tin", "viec_han", "cau_hoi_cho_xu_ly", "co_hoi_ket_noi", "xung_dot_khieu_nai",
                                  "nghia_vu_loi_hua", "quan_he_nong_nguoi"])
def test_b11_outside_red_types_never_red(kind: str) -> None:
    s = sig(kind, impact_base=3, ongoing=True, due_in_h=-1, flags={"public": True, "nudged": True, "explicit_urgent": True, "needs_owner": True,
                                                                      "repeat_n": 5, "said_will_leave": True}, money_vnd=900_000_000, money_basis="quoted")
    lv = compute(s)
    assert lv.level != "red"
    assert lv.level != "amber" or "outside_red_types" in lv.reasons or lv.candidate is not None or lv.gate is None


def test_b11_t_null_caps_p3_and_emits_outside_red_types() -> None:
    lv = compute(sig("su_kien_dip", impact_base=3, ongoing=True))
    assert lv.level == "amber" and lv.delivery == "digest" and {"outside_red_types", "prio_p3"} <= set(lv.reasons)


# ─── B12: T01 càu nhàu vẫn đặt ─────────────────────────────────────────────────

def test_b12_vent_goes_grey() -> None:
    lv = compute(cmp_("quan_he_nong_nguoi", complaint_stage="vent", flags={"said_will_leave": True}))
    assert (lv.level, lv.delivery) == ("grey", "digest_low") and "vent_only" in lv.reasons


def test_b12_ultimatum_regular_customer_at_most_amber_top() -> None:
    lv = compute(cmp_("xung_dot_khieu_nai", complaint_stage="ultimatum", flags={"said_will_leave": True}))
    assert (lv.level, lv.delivery) == ("amber", "digest_top")


def test_b12_prior_threat_closed_case_makes_ultimatum_vent() -> None:
    lv = compute(cmp_("xung_dot_khieu_nai", complaint_stage="ultimatum", prior_threat_no_exit_n=1, flags={"said_will_leave": True}))
    assert lv.level == "grey"


def test_b12_default_compat_stage_from_said_will_leave() -> None:
    s = cmp_("xung_dot_khieu_nai", flags={"said_will_leave": True})
    assert L.stage_eff(s, L._flags(s), False, False) == "ultimatum"
    assert L.stage_eff(cmp_("xung_dot_khieu_nai"), L.Flags(), False, False) == "voice"
    assert L.stage_eff(sig("viec_han"), L.Flags(), False, False) is None


# ─── B13: mỗi t có P1 và 'suýt P1' ─────────────────────────────────────────────

def test_b13_each_type_has_p1_and_near_p1() -> None:
    p1 = [cmp_("xung_dot_khieu_nai", complaint_stage="exit_pending"), promise(due_in_h=-1, nudged_by_customer=True),
          opp(due_in_h=None, flags={"explicit_urgent": True}), work(due_in_h=2)]
    near = [cmp_("xung_dot_khieu_nai", complaint_stage="exit_done"), promise(due_in_h=-1), opp(due_in_h=None, flags={"explicit_urgent": True}, opportunity={"need_fields_n": 1, "catalog_score": 85}),
            work(due_in_h=2, work={"late_signs": False, "type": "deadline_today"})]
    for s, n, g in zip(p1, near, (G3, G4, G5, G6), strict=True):
        assert (compute(s).level, compute(s).gate) == ("red", g), g
        assert compute(n).level == "amber" and compute(n).gate is None, g


# ─── B14/B15: θ1 theo vụ, same_customer_day_red ────────────────────────────────

def test_b14_second_threat_same_case_not_demoted_prior_counts_other_closed_cases_only() -> None:
    """Bên gọi chỉ đếm vụ đã đóng KHÁC `case_key` ⇒ doạ lần 2 cùng vụ có `prior_threat_no_exit_n = 0` ⇒ không bị hạ."""
    lv = compute(cmp_("xung_dot_khieu_nai", complaint_stage="ultimatum", prior_threat_no_exit_n=0, flags={"said_will_leave": True}))
    assert lv.level == "amber" and lv.delivery == "digest_top"


def test_b14_key_customer_with_precedent_still_p1() -> None:
    k = key()
    prof = profile((k,))
    s = cmp_("xung_dot_khieu_nai", complaint_stage="ultimatum", prior_threat_no_exit_n=3, flags={"said_will_leave": True}, subject_entity_id=k)
    lv = compute(s, prof=prof)
    assert (lv.level, lv.gate) == ("red", G3) and "key_customer" in lv.reasons


def test_b14_public_ok_exempts_theta1() -> None:
    s = cmp_("xung_dot_khieu_nai", complaint_stage="ultimatum", prior_threat_no_exit_n=3, public_venue="social", flags={"said_will_leave": True, "public": True})
    assert compute(s).level == "red"


def test_b15_same_customer_day_red_only_merges_g4() -> None:
    g4 = promise(due_in_h=-1, nudged_by_customer=True, ago_h=5).model_copy(update={"same_customer_day_red": True})
    lv = compute(g4)
    assert (lv.level, lv.delivery, lv.gate) == ("red", "merge", G4) and "merged" in lv.reasons
    for s in (cmp_("xung_dot_khieu_nai", complaint_stage="exit_pending"), opp(due_in_h=None, flags={"explicit_urgent": True}), work(due_in_h=2)):
        out = compute(s.model_copy(update={"same_customer_day_red": True}))
        assert out.level == "red" and out.delivery == "push_now"


def test_b15_case_prior_other_gate_never_merges() -> None:
    s = cmp_("xung_dot_khieu_nai", complaint_stage="exit_pending", case_prior={"red_pushed": True, "gate": G4})
    assert compute(s).delivery == "push_now"
    s2 = cmp_("xung_dot_khieu_nai", complaint_stage="exit_pending", case_prior={"red_pushed": True, "gate": G3, "said_will_leave": True})
    assert compute(s2).delivery == "merge"


def test_b15_g3_escalation_on_new_leave_threat() -> None:
    s = cmp_("xung_dot_khieu_nai", complaint_stage="exit_pending", case_prior={"red_pushed": True, "gate": G3, "said_will_leave": False})
    lv = compute(s)
    assert lv.delivery == "push_now" and "escalation" in lv.reasons


# ─── B16: trường vắng không tự trần; sàn không vượt mức 4 ───────────────────────

def test_b16_absent_fields_do_not_cap_red() -> None:
    s = cmp_("xung_dot_khieu_nai", complaint_stage="voice", flags={"public": True}, public_venue="group", thread={"type": "group", "size": 20, "customer_audience_n": None, "owner_in_thread": True, "agent_in_thread": True})
    assert compute(s).level == "red"          # audience null không chặn
    s2 = s.model_copy(update={"thread": s.thread.model_copy(update={"customer_audience_n": 2})})
    assert compute(s2).level != "red"


def test_b16_floor_does_not_exceed_cap_ev_4() -> None:
    lv = compute(promise(due_in_h=8, flags={"needs_owner": True, "conditional": True}))                # condition_unmet ⇒ trần P4
    assert lv.level == "grey" and "condition_unmet" in lv.reasons
    lv2 = compute(promise(due_in_h=8, flags={"needs_owner": True}))                                     # running ⇒ P4
    assert lv2.level == "grey" and "milestone_running" in lv2.reasons


def test_b16_floor_may_lower_not_owner_side_cap_to_p3() -> None:
    lv = compute(sig("viec_han", impact_base=1, flags={"needs_owner": True}))
    assert (lv.level, lv.delivery) == ("amber", "digest")


def test_b16_floor_does_not_exceed_staff_gate() -> None:
    lv = compute(sig("viec_han", impact_base=2, flags={"needs_owner": True}, staff_owned=True))
    assert lv.level == "grey" and "staff_owned" in lv.reasons


# ─── B17: Q-A, Q-B, Q-C ────────────────────────────────────────────────────────

def test_b17_qa_due_soon_p2_never_p1() -> None:
    lv = compute(promise(due_in_h=1.5))
    assert (lv.level, lv.delivery, lv.candidate) == ("amber", "digest_top", G4) and {"milestone_due_soon", "prio_p2"} <= set(lv.reasons)
    assert compute(promise(due_in_h=0.5, flags={"nudged": True, "explicit_urgent": True})).level == "amber"


def test_b17_qa_due_soon_boundary_2h_and_beyond() -> None:
    assert "milestone_due_soon" in compute(promise(due_in_h=2)).reasons
    assert "milestone_due_soon" not in compute(promise(due_in_h=2.01)).reasons


def test_b17_qa_day_only_deadline_due_soon_from_1530() -> None:
    d = NOW.replace(hour=0, minute=0)
    s = promise(due_in_h=None).model_copy(update={"deadline_at": d, "deadline_granularity": "day"})
    assert "milestone_due_soon" in compute(s, now=NOW.replace(hour=15, minute=30)).reasons
    assert "milestone_due_soon" not in compute(s, now=NOW.replace(hour=15, minute=29)).reasons


def test_b17_qa_due_soon_chịu_trần_not_owner_side_and_low_confidence() -> None:
    s = promise(due_in_h=1.5).model_copy(update={"impact_base": 1})
    lv = compute(s)
    assert lv.level == "grey" and {"milestone_due_soon", "not_owner_side"} <= set(lv.reasons)
    lv2 = compute(promise(due_in_h=1.5).model_copy(update={"confidence_content": 0.2}))
    assert lv2.level == "grey" and "low_confidence" in lv2.reasons


def test_b17_qb_threat_regular_customer_amber_top_key_customer_red() -> None:
    reg = compute(cmp_("xung_dot_khieu_nai", complaint_stage="third_party"))
    assert (reg.level, reg.delivery) == ("amber", "digest_top")
    k = key()
    vip = compute(cmp_("xung_dot_khieu_nai", complaint_stage="third_party", subject_entity_id=k), prof=profile((k,)))
    assert (vip.level, vip.gate) == ("red", G3)
    posted = compute(cmp_("xung_dot_khieu_nai", complaint_stage="voice", public_venue="social", flags={"public": True}))
    assert posted.level == "red"


def test_b17_qc_day_only_deadline_overdue_not_nudged_amber_nudged_red() -> None:
    d = NOW.replace(hour=0, minute=0)
    late = NOW.replace(hour=18, minute=0)
    plain = promise(due_in_h=None).model_copy(update={"deadline_at": d, "deadline_granularity": "day"})
    lv = compute(plain, now=late)
    assert (lv.level, lv.delivery) == ("amber", "digest_top") and "day_only_deadline" in lv.reasons
    nudged = plain.model_copy(update={"promise": plain.promise.model_copy(update={"nudged_by_customer": True})})
    assert compute(nudged, now=late).level == "red"


# ─── B18: r7.2 ─────────────────────────────────────────────────────────────────

def test_b18_g5_need_unclear_is_cap_not_off_in_nhan_vien() -> None:
    s = opp(due_in_h=20, opportunity={"catalog_score": 85, "need_fields_n": 1})
    lv = compute(s, ctx="nhan_vien")
    assert (lv.level, lv.delivery) == ("amber", "digest") and "need_unclear" in lv.reasons


def test_b18_context_off_g5_gia_dinh_stays_off() -> None:
    assert compute(opp(due_in_h=20, opportunity={"catalog_score": 85, "need_fields_n": 1}), ctx="gia_dinh").level == "off"


def test_b18_q15_candidate_kept_even_when_door_not_ok() -> None:
    lv = compute(cmp_("xung_dot_khieu_nai", complaint_stage="exit_done", gate_candidate=G3))
    assert (lv.level, lv.candidate) == ("amber", G3) and "exit_done" in lv.reasons
    lv2 = compute(opp(due_in_h=None, flags={"explicit_urgent": True}, opportunity={"catalog_score": 85, "need_fields_n": 1}, gate_candidate=G5))
    assert (lv2.level, lv2.candidate) == ("amber", G5)                                 # t = G5 nhưng không door_ok ⇒ candidate = gate_candidate


# ─── Thẩm quyền, N1, mã reasons ────────────────────────────────────────────────

def test_authority_staff_owned_caps_p4_and_asks_for_owner_overrides() -> None:
    s = cmp_("xung_dot_khieu_nai", complaint_stage="exit_pending", staff_owned=True, staff_ref="Hiếu")
    lv = compute(s)
    assert lv.level == "grey" and "staff_owned" in lv.reasons
    lv2 = compute(s.model_copy(update={"asks_for_owner": True}))
    assert lv2.level == "red" and "asks_for_owner" in lv2.reasons


def test_n1_closure_staff_digest_low_unless_asks_for_owner() -> None:
    s = cmp_("xung_dot_khieu_nai", complaint_stage="exit_pending", closure="staff")
    lv = compute(s)
    assert (lv.level, lv.delivery) == ("grey", "digest_low") and "closed_by_staff" in lv.reasons
    assert compute(s.model_copy(update={"asks_for_owner": True})).level == "red"
    assert compute(cmp_("xung_dot_khieu_nai", closure="owner")).delivery == "none"


def test_n1_mute_resolved_status_closed() -> None:
    base = cmp_("xung_dot_khieu_nai", complaint_stage="exit_pending")
    assert compute(base.model_copy(update={"mute": "chat"})).reasons[-1] == "muted"
    assert compute(base.model_copy(update={"owner_resolved": True})).delivery == "none"
    assert compute(base.model_copy(update={"status": "da_xu_ly"})).delivery == "none"


def test_n7_key_customer_reason_requires_customer_and_listing() -> None:
    k = key()
    lv = compute(cmp_("xung_dot_khieu_nai", subject_entity_id=k), prof=profile((k,)))
    assert "key_customer" in lv.reasons
    assert "key_customer" not in compute(sig("xung_dot_khieu_nai", impact_base=2, subject_entity_id=k), prof=profile((k,))).reasons


def test_irreversible_model_flag_is_not_read_in_table_path() -> None:
    a = compute(sig("su_kien_dip", irreversible=True))
    b = compute(sig("su_kien_dip", irreversible=False))
    assert a.model_dump() == b.model_dump()


def test_n9_low_confidence_p1_to_p2_for_g3_and_p3_otherwise() -> None:
    lv = compute(cmp_("xung_dot_khieu_nai", complaint_stage="exit_pending", harm_what=""))
    assert (lv.level, lv.delivery) == ("amber", "digest_top") and "no_harm" in lv.reasons
    lv2 = compute(opp(due_in_h=None, flags={"explicit_urgent": True}, harm_what="  "))
    assert (lv2.level, lv2.delivery) == ("amber", "digest") and "no_harm" in lv2.reasons


def test_n11_prio_reason_present_and_grey_low_priority() -> None:
    lv = compute(sig("su_kien_dip", impact_base=1))
    assert lv.level == "grey" and any(r.startswith("prio_p") for r in lv.reasons)


def test_urgency_plus_still_added_when_act_by_is_now() -> None:
    """AMB-16: `nudged`/`explicit_urgent` cộng khi `act_by = now` (trần 3)."""
    s = sig("su_kien_dip", impact_base=2, ongoing=True, flags={"nudged": True})
    assert L.act_by(s, state(), NOW) == NOW
    assert compute(s).level == "amber"


# ─── next_change_at ────────────────────────────────────────────────────────────

def test_levels_next_change_g5_deadline_has_second_after_deadline() -> None:
    s = opp(due_in_h=6)                                      # 14:00 + 6 = 20:00
    marks = _marks(s, NOW)
    assert NOW.replace(hour=20, minute=0, second=1) in marks


def test_levels_next_change_day_only_has_1530_and_173001() -> None:
    s = sig("viec_han", deadline_at=NOW.replace(hour=0, minute=0), deadline_granularity="day", impact_base=2)
    marks = _marks(s, NOW.replace(hour=8))
    assert NOW.replace(hour=15, minute=30) in marks and NOW.replace(hour=17, minute=30, second=1) in marks


def test_levels_next_change_g3_has_anchor_plus_24h() -> None:
    s = cmp_("xung_dot_khieu_nai", ago_h=2, complaint_stage="exit_pending")
    assert (NOW - 2 * H) + 24 * H in _marks(s, NOW)


def test_levels_next_change_table_drops_four_hour_mark_and_act_by() -> None:
    s = promise(due_in_h=10)                                 # r6.6.1 có mốc dl−4h; đường bảng thì không
    assert NOW + 6 * H not in _marks(s, NOW)
    g = cmp_("xung_dot_khieu_nai", ago_h=1, complaint_stage="exit_pending")        # act_by = now ⇒ không có mốc ≤ now
    assert all(m > NOW for m in _marks(g, NOW))


def test_levels_next_change_step0_keeps_v0176_marks() -> None:
    s = sig("rui_ro_lua_dao", ago_h=1, flags={"credentials_entered": True})
    nxt = L.next_change_at(s, state(), NOW)
    assert nxt == NOW - 1 * H + timedelta(hours=24)            # event_at + 24 giờ (cổng 2/3) — mốc v0.1.76 còn nguyên


def test_levels_next_change_d5_adds_minus_48h() -> None:
    s = sig("co_hoi_ket_noi", deadline_at=NOW + timedelta(days=3), impact_base=1)
    assert NOW + timedelta(days=1) in _marks(s, NOW)


def test_levels_next_change_none_when_nothing_ahead() -> None:
    s = sig("viec_han", ago_h=1000, impact_base=2)
    assert L.next_change_at(s, state(), NOW) is None


def _marks(s: L.SignalIn, now: datetime) -> set[datetime]:
    """Mọi mốc ứng viên (dò bằng cách lặp lấy mốc nhỏ nhất rồi đẩy `now` tới sau mốc đó)."""
    out: set[datetime] = set()
    cur = now
    for _ in range(40):
        nxt = L.next_change_at(s, state(), cur)
        if nxt is None:
            break
        out.add(nxt)
        cur = nxt
    return out


def test_vectors_fixture_is_valid_json_with_new_fields() -> None:
    ids = {v["id"] for v in VECTORS}
    assert {"LV-R7-01", "LV-R7-99", "LV-G1-01"} <= ids
    assert json.dumps(VECTORS[0])


def test_every_reason_in_vectors_is_a_documented_code() -> None:
    used = {r for v in VECTORS for r in v["expected"].get("reasons_include", [])}
    assert used <= set(L.REASONS), sorted(used - set(L.REASONS))


def test_every_emitted_reason_of_all_vectors_is_documented() -> None:
    for v in VECTORS:
        i = v["input"]
        p = i["profile"]
        out = L.compute(_vec_signal(i["signal"], p), _vec_profile(p), _vec_policy(i["ctx"]), _vec_state(i["state"], p), datetime.fromisoformat(i["now"]))
        assert set(out.reasons) <= set(L.REASONS), (v["id"], out.reasons)


def test_r73_gate_candidate_g1_g2_routes_to_step0() -> None:
    """Đề cử cổng 1/2 mà không cờ nào ⇒ Bước 0 (fail-high), không rơi xuống đường bảng bị trần Thẩm quyền / milestone / bối cảnh."""
    for g in (G1, G2):
        s = sig("nghia_vu_loi_hua", gate_candidate=g, staff_owned=True, impact_base=2)
        assert L.is_step0(s)
        lv = compute(s)
        assert (lv.level, lv.candidate, lv.delivery) == ("amber", g, "digest") and "gate_not_met" in lv.reasons
    assert not L.is_step0(sig("nghia_vu_loi_hua", gate_candidate=G4))


def _body_token_digest(fn_obj: object) -> str:
    """Băm chuỗi token của thân hàm (bỏ docstring, chú thích, khoảng trắng) — ổn định giữa các bản Python (ast.dump thì không)."""
    import ast
    import hashlib
    import inspect
    import io
    import textwrap
    import tokenize

    src = textwrap.dedent(inspect.getsource(fn_obj))  # type: ignore[arg-type]
    fn = ast.parse(src).body[0]
    assert isinstance(fn, ast.FunctionDef)
    first = fn.body[1] if isinstance(fn.body[0], ast.Expr) and isinstance(getattr(fn.body[0], "value", None), ast.Constant) else fn.body[0]
    lines = src.splitlines(keepends=True)[first.lineno - 1 : fn.end_lineno]
    skip = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER}
    toks = [t.string for t in tokenize.generate_tokens(io.StringIO(textwrap.dedent("".join(lines))).readline) if t.type not in skip]
    return hashlib.sha256("\x1f".join(toks).encode()).hexdigest()


def test_b1_step0_function_source_is_frozen() -> None:
    """Rào B1: băm token thân `_compute_step0` — sửa thân hàm Bước 0 ⇒ đỏ; chủ ý sửa thì cập nhật hằng sau khi so với v0.1.76."""
    digest = _body_token_digest(L._compute_step0)
    assert digest == STEP0_BODY_SHA256, digest


# Băm token thân hàm `compute` của v0.1.76 (commit 03866287, trước khi tách Bước 0) — thân `_compute_step0` phải trùng từng token.
STEP0_BODY_SHA256 = "140dc5d433f304bce9e07eba2ae6fe738bd29084b4a6505ebc8bab460b569c59"


def test_levels_next_change_d5_has_deadline_plus_1s() -> None:
    s = sig("co_hoi_ket_noi", deadline_at=NOW + 3 * H, impact_base=1)
    assert NOW + 3 * H + timedelta(seconds=1) in _marks(s, NOW)
