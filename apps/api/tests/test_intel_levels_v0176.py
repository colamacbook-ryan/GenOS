# ruff: noqa: E501
"""v0.1.76 (gói T1) — `levels.compute` theo spec r6.6.1 §4.2 + §4.2.1 (6 cổng INT-19, sàn lừa đảo/an toàn, 7a′, leo thang, trần,
`bypass_all`) + INTENT Q13–Q16. Hàm thuần: không DB. Ví dụ lấy từ chính spec (S22, ca bắt buộc §4D.4, biên 23h/24,0h/25h của Q15,
"còn ≤ 4 giờ" của cổng 4 …), KHÔNG chép bộ vector Đợt 0 (bộ đó chạy ngoài repo)."""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta

import pytest

from gh.biz.intel import levels as L
from tests.intel_helpers import NOW, compute, person, policy, profile, sig, state

G1, G2, G3, G4, G5, G6 = L.GATES


# ─── cổng 1 lừa đảo ───────────────────────────────────────────────────────────

def test_levels_gate1_credentials_entered_red_push_now() -> None:
    lv = compute(sig("rui_ro_lua_dao", flags={"credentials_entered": True}))
    assert (lv.level, lv.gate, lv.delivery) == ("red", G1, "push_now")


def test_levels_gate1_link_not_entered_is_amber_not_red() -> None:
    """Link/phishing chưa ai nhập, chưa giao dịch ⇒ tối đa 🟠 (§4.2 cổng 1)."""
    lv = compute(sig("rui_ro_lua_dao"))
    assert lv.level == "amber" and lv.gate is None


def test_levels_gate1_otp_window_7_days_then_window_expired() -> None:
    assert compute(sig("rui_ro_lua_dao", ago_h=6 * 24, flags={"credentials_entered": True})).level == "red"
    lv = compute(sig("rui_ro_lua_dao", ago_h=8 * 24, flags={"credentials_entered": True}))
    assert (lv.level, lv.candidate, lv.delivery) == ("amber", G1, "digest_top") and "window_expired" in lv.reasons


def test_levels_gate1_money_moved_over_24h_done_cannot_stop() -> None:
    lv = compute(sig("rui_ro_lua_dao", ago_h=30, txn_state="just_done", flags={"money_request": True}))
    assert lv.level == "amber" and lv.delivery == "digest_top" and {"window_expired", "done_cannot_stop"} <= set(lv.reasons)


def test_levels_gate1_bypass_all_ignores_cap_quiet_shadow_precision() -> None:
    s = sig("rui_ro_lua_dao", flags={"credentials_entered": True})
    st = state(intel_mode="shadow", reds_pushed_today=9, low_precision_gates=[G1], days_since_install=0)
    night = datetime(2026, 10, 23, 23, 30, tzinfo=L.VN)
    s_n = sig("rui_ro_lua_dao", flags={"credentials_entered": True}, now=night)
    assert compute(s, st=st).delivery == "push_now"
    assert compute(s_n, st=st, now=night).delivery == "push_now"


def test_levels_gate1_hard_rule_beats_d2_disagree() -> None:
    s = sig("rui_ro_lua_dao", hard_rule_hit=["credentials_entered"], confidence_exist=0.5, d2={"verdict": "disagree"})
    lv = compute(s)
    assert lv.level == "red" and "d2_disagree" in lv.reasons


def test_levels_gate1_d2_disagree_without_hard_rule_downgrades_to_floor() -> None:
    s = sig("rui_ro_lua_dao", flags={"credentials_entered": True}, confidence_exist=0.8, d2={"verdict": "disagree"})
    lv = compute(s)
    assert (lv.level, lv.candidate, lv.delivery) == ("amber", G1, "digest_top")


def test_levels_gate1_big_group_member_is_wide_circle_amber() -> None:
    s = sig("rui_ro_lua_dao", flags={"credentials_entered": True}, thread={"type": "group", "size": 150, "owner_in_thread": True},
            victims=[person("m1", thread_member=True)])
    lv = compute(s, ctx="giao_thuong")
    assert (lv.level, lv.candidate, lv.delivery) == ("amber", G1, "digest_top") and "fraud_wide_circle" in lv.reasons
    s500 = s.model_copy(update={"thread": L.Thread(type="group", size=500, owner_in_thread=True)})
    assert compute(s500, ctx="giao_thuong").candidate is None


def test_levels_confidence_required_cases() -> None:
    """§4D.4 ca bắt buộc: người lạ vào nhóm 3 ngày xin 15 triệu (qua d2) ⇒ 🔴; tin chuyển tiếp cảnh báo không nhắm người của Sếp ⇒ ⚪."""
    stranger = sig("rui_ro_lua_dao", source_class="outsider", confidence_exist=0.8, d2={"verdict": "agree"},
                   txn_state="pending", flags={"money_request": True}, victims=[person("ba", owner_key_relation=True)])
    assert compute(stranger).level == "red"
    fwd = sig("rui_ro_lua_dao", source_class="forwarded", delivery_form="forwarded", victims=[person("x")],
              confidence_exist=0.4, thread={"type": "group", "size": 300, "owner_in_thread": True})
    assert compute(fwd, ctx="giao_thuong").level == "grey"


# ─── cổng 2 an toàn ───────────────────────────────────────────────────────────

def test_levels_gate2_23h_red_25h_window_expired() -> None:
    assert compute(sig("suc_khoe_an_toan", ago_h=23, flags={"emergency": True})).gate == G2
    lv = compute(sig("suc_khoe_an_toan", ago_h=25, flags={"emergency": True}))
    assert (lv.level, lv.candidate, lv.delivery) == ("amber", G2, "digest_top")


def test_levels_gate2_ongoing_has_no_hour_limit() -> None:
    assert compute(sig("suc_khoe_an_toan", ago_h=30, ongoing=True, flags={"emergency": True})).level == "red"


def test_levels_gate2_only_forwarded_sources_no_gate() -> None:
    lv = compute(sig("suc_khoe_an_toan", non_forwarded_sources_n=0, flags={"emergency": True}))
    assert lv.gate is None


def test_levels_gate2_outsider_kin_claim_no_red() -> None:
    """L-87: người lạ mới vào nhóm "mẹ em cấp cứu, ai cho mượn tiền" — vai thân tộc không áp cho outsider."""
    s = sig("suc_khoe_an_toan", source_class="outsider", victims=[person("me", kin_of_member=True)], flags={"emergency": True},
            thread={"type": "group", "size": 120, "owner_in_thread": True})
    assert compute(s).level != "red"


# ─── cổng 3 khách chê công khai / doạ bỏ (Q15) ────────────────────────────────

def _g3(ago: float, **kw: object) -> L.SignalIn:
    kw.setdefault("impact_base", 2)                            # detector: khách của Sếp ⇒ cơ sở impact 2 (§4.2.1 (f)); đường bảng r7 đọc nó
    return sig("xung_dot_khieu_nai", ago_h=ago, customer_of_owner=True, flags={"public": True}, **kw)  # type: ignore[arg-type]


@pytest.mark.parametrize(("ago", "expect"), [(23, ("red", G3, None)), (24.0, ("amber", None, G3)), (25, ("amber", None, G3))])
def test_levels_gate3_q15_boundary(ago: float, expect: tuple[str, str | None, str | None]) -> None:
    lv = compute(_g3(ago))
    assert (lv.level, lv.gate, lv.candidate) == expect
    if lv.level == "amber":
        assert lv.delivery == "digest_top" and "window_expired" in lv.reasons     # Q15: 🟠 ĐỨNG ĐẦU bản tin


def test_levels_gate3_staff_closure_digest_low_owner_closure_none() -> None:
    assert compute(_g3(2, closure="staff")).delivery == "digest_low"
    lv = compute(_g3(2, closure="customer"))
    assert (lv.level, lv.delivery) == ("grey", "none")


def test_levels_gate3_supplier_not_customer_no_gate() -> None:
    lv = compute(sig("xung_dot_khieu_nai", customer_of_owner=False, flags={"public": True}))
    assert lv.gate is None and lv.level == "amber"


# ─── cổng 4 lời hứa với khách sắp vỡ (Q16) ────────────────────────────────────

def _g4(due: float, **kw: object) -> L.SignalIn:
    kw.setdefault("promise", {"to_customer": True})
    kw.setdefault("impact_base", 2)
    return sig("nghia_vu_loi_hua", due_in_h=due, **kw)  # type: ignore[arg-type]


def test_levels_gate4_four_hours_inclusive_and_overdue_today() -> None:
    # r7 (R1, Ryan chốt): bỏ vế "còn ≤ 4 giờ tự thành 🔴". Đã trễ trong hôm nay mà khách CHƯA nhắc ⇒ 🟠 đầu bản tin (P2), khách đã nhắc ⇒ 🔴 (Q16).
    assert compute(_g4(4.0)).level == "grey" and "milestone_running" in compute(_g4(4.0)).reasons
    lv = compute(_g4(-1.0))
    assert (lv.level, lv.delivery, lv.candidate, lv.gate) == ("amber", "digest_top", G4, None)
    assert compute(_g4(-1.0, promise={"to_customer": True, "nudged_by_customer": True})).gate == G4     # A17 + Q16
    assert compute(_g4(4.5)).gate is None


def test_levels_gate4_obstacle_reported_today_red() -> None:
    lv = compute(_g4(7, promise={"to_customer": True, "obstacle_reported": True}))
    assert (lv.level, lv.candidate) == ("amber", G4)           # r7: có trở ngại mà khách chưa nhắc ⇒ cửa đạt, P2 🟠 đầu bản tin
    assert compute(_g4(7, promise={"to_customer": True, "obstacle_reported": True, "nudged_by_customer": True})).gate == G4     # Q16


def test_levels_gate4_completed_closes_a16() -> None:
    lv = compute(_g4(2, status="da_xu_ly"))
    assert (lv.level, lv.delivery) == ("grey", "none") and "closed" in lv.reasons


def test_levels_gate4_unmet_condition_never_gate() -> None:
    assert compute(_g4(2, flags={"conditional": True}, promise={"to_customer": True, "condition_met": "no"})).gate is None


# ─── cổng 5 cơ hội lớn sắp trôi (S22) ─────────────────────────────────────────

def test_levels_gate5_s22_regression_red() -> None:
    """S22: "cần gấp 50 tấn thép … tuần sau", khớp catalog, 730 triệu ước tính ⇒ 🔴 qua co_hoi_lon_sap_troi."""
    s = sig("co_hoi_ket_noi", due_in_h=24 * 6, money_vnd=730_000_000, money_basis="estimated", impact_base=2,
            opportunity={"catalog_score": 88}, flags={"explicit_urgent": True})
    assert compute(s).gate == G5


def test_levels_gate5_off_in_family_context() -> None:
    s = sig("co_hoi_ket_noi", due_in_h=10, opportunity={"catalog_score": 90})
    lv = compute(s, ctx="gia_dinh")
    assert lv.level == "off" and lv.delivery == "none"


def test_levels_gate5_48h_boundary() -> None:
    base = {"opportunity": {"catalog_score": 90}}
    # r7: cơ hội 24–72 giờ ⇒ I3 × U2 = P2 🟠 đầu bản tin (không 🔴); < 24 giờ ⇒ U3 ⇒ 🔴 (G5, `need_fields_n` vắng ⇒ không trần).
    lv = compute(sig("co_hoi_ket_noi", due_in_h=47.9, **base))  # type: ignore[arg-type]
    assert (lv.level, lv.gate, lv.candidate, lv.delivery) == ("amber", None, G5, "digest_top")
    assert compute(sig("co_hoi_ket_noi", due_in_h=23.9, **base)).gate == G5  # type: ignore[arg-type]
    assert compute(sig("co_hoi_ket_noi", due_in_h=-1, **base)).gate is None  # type: ignore[arg-type]    # hạn đã qua ⇒ đã trôi


# ─── cổng 6 sự cố công việc gấp ───────────────────────────────────────────────

def test_levels_gate6_deadline_passed_today_still_counts() -> None:
    s = sig("viec_han", due_in_h=-1, work={"type": "submission_today", "owner_work": True})
    assert compute(s).gate == G6
    s2 = sig("viec_han", due_in_h=38, work={"type": "submission_today", "owner_work": True})
    assert compute(s2).gate is None


# ─── phân phối (e) ────────────────────────────────────────────────────────────

def test_levels_delivery_ramp_14_days_shadow_would_push() -> None:
    lv = compute(_g3(2), st=state(days_since_install=13.9))
    assert (lv.level, lv.gate, lv.delivery, lv.would_push) == ("red", G3, "digest_top", True)
    assert compute(_g3(2), st=state(days_since_install=14.0)).delivery == "push_now"


def test_levels_delivery_cap_precision_quiet() -> None:
    lv = compute(_g3(2), st=state(reds_pushed_today=3))
    assert (lv.level, lv.gate, lv.capped, lv.demoted, lv.delivery) == ("amber", G3, True, "cap", "digest_top")
    lv = compute(_g3(2), st=state(low_precision_gates=[G3]))
    assert (lv.level, lv.demoted) == ("amber", "precision")
    night = datetime(2026, 10, 23, 22, 30, tzinfo=L.VN)
    assert compute(_g3(2, now=night), now=night).delivery == "digest_morning"


def test_levels_one_case_one_red_merge_and_escalation() -> None:
    prior = {"red_pushed": True, "gate": G3, "said_will_leave": False}
    lv = compute(_g3(2, case_prior=prior))
    assert (lv.level, lv.delivery) == ("red", "merge") and "merged" in lv.reasons
    esc = sig("xung_dot_khieu_nai", ago_h=1, customer_of_owner=True, impact_base=2, flags={"public": True, "said_will_leave": True},
              case_prior=prior)
    lv = compute(esc)
    assert lv.delivery == "push_now" and "escalation" in lv.reasons


def test_levels_fraud_new_payee_alone_does_not_repush() -> None:
    prior = {"red_pushed": True, "gate": G1, "branch_rank": 3, "victim_refs": ["v1"], "payee_refs": ["stkA"]}
    s = sig("rui_ro_lua_dao", flags={"credentials_entered": True}, payee_refs=["stkB"], case_prior=prior)
    assert compute(s).delivery == "merge"
    s2 = sig("rui_ro_lua_dao", flags={"credentials_entered": True, "account_takeover": True, "money_request": True},
             case_prior=prior)
    assert compute(s2).delivery == "push_now"                  # nhánh tăng 3 → 4 ⇒ đẩy lại


# ─── mute / bối cảnh ──────────────────────────────────────────────────────────

def test_levels_mute_chat_ignored_for_fail_high_pin_respected() -> None:
    s = sig("rui_ro_lua_dao", flags={"credentials_entered": True}, mute="chat")
    lv = compute(s)
    assert lv.level == "red" and "mute_chat_ignored" in lv.reasons
    assert compute(s.model_copy(update={"mute": "pin"})).delivery == "digest_low"
    assert compute(_g3(2, mute="chat")).delivery == "none"


def test_levels_context_cap_only_lowers() -> None:
    s = sig("thi_truong_thong_tin", impact_base=2, due_in_h=10, confidence_content=0.9)
    assert compute(s, ctx="kinh_doanh").level == "amber"
    assert compute(s, ctx="hoc_tap").level == "grey"
    assert compute(s, ctx=None).level == "off"


# ─── sàn lừa đảo/an toàn — bất biến (nguyên lý fail-high mục 1–2) ─────────────

def test_levels_floor_invariant_fuzz() -> None:
    """Vụ thoả cổng 1/2 (hoặc mọi điều kiện trừ cửa sổ, trong 14 ngày) không bao giờ ra ⚪/off — trừ "Đã xử lý" của Sếp và mute PIN."""
    rnd = random.Random(20261025)
    checked = 0
    for _ in range(3000):
        kind = rnd.choice(["rui_ro_lua_dao", "suc_khoe_an_toan", "viec_han", None])
        flags = {k: rnd.random() < 0.35 for k in ("credentials_entered", "account_takeover", "money_request", "emergency",
                                                   "said_will_leave", "public")}
        s = sig(kind, ago_h=rnd.choice([0.2, 5, 23, 30, 47, 60, 24 * 6, 24 * 10, 24 * 15]), flags=flags,
                txn_state=rnd.choice(["none", "pending", "just_done"]), ongoing=rnd.random() < 0.2,
                status=rnd.choice(["dang_mo", "da_xu_ly"]), remediation=rnd.choice(["none", "victim_or_owner_side", "other"]),
                mute=rnd.choice([None, "chat"]), confidence_exist=rnd.choice([0.4, 0.8, 1.0]),
                d2={"verdict": rnd.choice([None, "agree", "disagree"]), "error": rnd.random() < 0.2},
                hard_rule_hit=rnd.choice([[], ["credentials_entered"], ["emergency_115_chay_cap_cuu"]]),
                harm_what=rnd.choice(["", "mất tiền"]),
                victims=[person("v", **{rnd.choice(["team", "owner_key_relation", "customer"]): True})])
        lv = compute(s, ctx=rnd.choice(["kinh_doanh", "gia_dinh", None]))
        if lv.gate in L.BYPASS_GATES or lv.candidate in L.BYPASS_GATES:
            checked += 1
            assert lv.level in ("red", "amber"), (s, lv)
    assert checked > 300


def test_levels_floor_two_owner_exits() -> None:
    s = sig("rui_ro_lua_dao", flags={"credentials_entered": True})
    assert compute(s.model_copy(update={"owner_resolved": True})).delivery == "none"
    assert compute(s.model_copy(update={"mute": "pin"})).level == "grey"
    assert compute(s.model_copy(update={"status": "da_xu_ly"})).level == "red"            # status_hint của model bị bỏ qua
    assert compute(s.model_copy(update={"remediation": "other"})).level == "red"          # người lạ báo "đã xử lý"


def test_levels_remediation_by_victim_floor_amber() -> None:
    lv = compute(sig("rui_ro_lua_dao", flags={"credentials_entered": True}, remediation="victim_or_owner_side"))
    assert (lv.level, lv.delivery) == ("amber", "digest_top") and "remediated" in lv.reasons
    lv = compute(sig("rui_ro_lua_dao", hard_rule_hit=["credentials_entered"], remediation="victim_or_owner_side"))
    assert lv.level == "red" and "remediation_ignored_hard_rule" in lv.reasons


# ─── impact_base / trục ───────────────────────────────────────────────────────

def test_levels_impact_base_table() -> None:
    assert L.impact_base("rui_ro_lua_dao", owner_side_targeted=True, money_or_credentials=True) == 3
    assert L.impact_base("rui_ro_lua_dao", owner_side_targeted=True) == 2
    assert L.impact_base("rui_ro_lua_dao") == 1
    assert L.impact_base("suc_khoe_an_toan", emergency=True) == 3
    assert L.impact_base("suc_khoe_an_toan", subject_owner_side=True) == 2
    assert L.impact_base("nghia_vu_loi_hua", important=True) == 2 and L.impact_base("co_hoi_ket_noi") == 1


def test_levels_impact_base_axes_money_and_flags() -> None:
    st = state()
    s = sig("co_hoi_ket_noi", money_vnd=6_000_000, money_basis="quoted")
    assert L.axes(s, profile(), st, NOW)[0] == 2
    s = sig("co_hoi_ket_noi", money_vnd=900_000_000, money_basis="estimated")
    assert L.axes(s, profile(), st, NOW)[0] == 2                # tiền ước tính tối đa 2
    e = uuid.uuid4()
    s = sig("quan_he_nong_nguoi", subject_entity_id=e, flags={"public": True})
    assert L.axes(s, profile((e,)), st, NOW)[0] == 3            # public +1, owner_key_relation +1
    s = sig("co_hoi_ket_noi", irreversible=True)
    assert L.axes(s, profile(), st, NOW)[0] == 1                # irreversible không bao giờ cho co_hoi_ket_noi (CNS-143)
    assert L.axes(sig("viec_han", due_in_h=-3), profile(), st, NOW)[1] == 3       # đã qua mà chưa xong ⇒ 3
    assert L.axes(sig("viec_han", due_in_h=72), profile(), st, NOW)[1] == 2       # 24–72 đóng hai đầu


# ─── mốc vụ cổng 3 (relane đưa case_history) ──────────────────────────────────

def test_relane_case_anchor_complaint_again_does_not_refresh() -> None:
    t0 = NOW - timedelta(hours=30)
    h = [L.CaseEvent(at=t0, evt="complaint", public=True), L.CaseEvent(at=NOW - timedelta(hours=1), evt="complaint", public=True)]
    assert L.case_anchor(h, NOW) == t0
    lv = compute(sig("xung_dot_khieu_nai", ago_h=1, customer_of_owner=True, flags={"public": True}, case_history=h))
    assert lv.level == "amber" and lv.candidate == G3          # chê lại không làm mới mốc ⇒ quá 24 giờ ⇒ 7a′


def test_relane_case_anchor_reset_after_closure_and_escalation() -> None:
    t0, t1, t2 = NOW - timedelta(hours=50), NOW - timedelta(hours=40), NOW - timedelta(hours=2)
    closed = [L.CaseEvent(at=t0, evt="complaint", public=True), L.CaseEvent(at=t1, evt="closure"),
              L.CaseEvent(at=t2, evt="complaint", public=True)]
    assert L.case_anchor(closed, NOW) == t2
    esc = [L.CaseEvent(at=t0, evt="complaint", public=True), L.CaseEvent(at=t2, evt="complaint", said_will_leave=True)]
    assert L.case_anchor(esc, NOW) == t2


# ─── case_key 10 loại ─────────────────────────────────────────────────────────

def test_case_key_ten_kinds_stable_within_window() -> None:
    a, b = NOW, NOW + timedelta(hours=2)
    assert L.case_key("nghia_vu_loi_hua", at=a, promise_id="P1") == L.case_key("nghia_vu_loi_hua", at=b, promise_id="P1")
    assert L.case_key("viec_han", at=a, thread_key="g:1", task_title_folded="bao cao", deadline_at=NOW).startswith("viec_han:")
    assert L.case_key("cau_hoi_cho_xu_ly", at=a, raw_event_id="r1") == "cau_hoi_cho_xu_ly:raw|r1"
    assert L.case_key("su_kien_dip", at=a, entity_id="e", event_date="2026-11-02") == "su_kien_dip:e|2026-11-02"
    assert L.case_key("suc_khoe_an_toan", at=a, entity_id="me") == L.case_key("suc_khoe_an_toan", at=a + timedelta(minutes=5), entity_id="me")
    k3 = L.case_key("xung_dot_khieu_nai", at=a, customer_entity_id="c1", reply_chain_id="chain9")
    assert k3 == L.case_key("quan_he_nong_nguoi", at=b, customer_entity_id="c1", reply_chain_id="chain9")   # dùng chung khoá vụ
    assert L.case_key("quan_he_nong_nguoi", at=a, entity_id="p") == "quan_he_nong_nguoi:p"      # T12: không bucket, cửa sổ trượt
    assert L.case_key("co_hoi_ket_noi", at=a, demand_id="d1") == "co_hoi_ket_noi:demand|d1"
    assert L.case_key("rui_ro_lua_dao", at=a, payee_digest="h", group_id="g") == L.case_key("rui_ro_lua_dao", at=b, payee_digest="h", group_id="g")
    assert L.case_key("rui_ro_lua_dao", at=a, domain="EVIL.top") == L.case_key("rui_ro_lua_dao", at=a, domain="evil.top")
    assert L.case_key("thi_truong_thong_tin", at=a, simhash="ab").startswith("thi_truong_thong_tin:simhash|ab")


# ─── đồng hồ: mốc tính lại ────────────────────────────────────────────────────

def test_levels_next_change_at_promise_due_soon_mark() -> None:
    s = _g4(6)
    nxt = L.next_change_at(s, state(), NOW)
    assert nxt == NOW + timedelta(hours=4)                     # r7: mốc cảnh báo `deadline_eff − 2 giờ` (Q-A); bỏ mốc "≤ 4 giờ"
    assert compute(s, now=NOW).gate is None
    lv = compute(s, now=nxt)
    assert (lv.level, lv.delivery, lv.gate) == ("amber", "digest_top", None) and "milestone_due_soon" in lv.reasons


def test_levels_next_change_at_q15_24h() -> None:
    s = _g3(20)
    nxt = L.next_change_at(s, state(), NOW)
    assert nxt == NOW + timedelta(hours=4)
    assert compute(s, now=nxt).level == "amber"                # quá 24 giờ ⇒ 🟠 đứng đầu bản tin (Q15)


def test_levels_policy_unconfirmed_profile_match_opportunity_not_off() -> None:
    s = sig("co_hoi_ket_noi", opportunity={"resource_owner_confirmed": True}, impact_base=2, confidence_content=0.8)
    assert compute(s, ctx=None).level != "off"
    s2 = sig("co_hoi_ket_noi", impact_base=2, confidence_content=0.8)
    assert compute(s2, ctx=None).level == "off"
    assert policy(None).confirmed is False
