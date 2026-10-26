"""Hợp đồng T1 r7 (c′): chỉ thêm trường, mọi trường có mặc định; dữ liệu r6.6.1 vẫn hợp lệ nguyên văn."""
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from gh.biz.intel import levels as L

T0 = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)


def test_r7_fields_default_to_r661_behaviour():
    s = L.SignalIn(msg_at=T0)
    assert s.complaint_stage is None and s.public_venue is None and s.deadline_granularity is None
    assert s.asks_for_owner is False and s.staff_owned is False and s.same_customer_day_red is False
    assert s.prior_threat_no_exit_n == 0 and s.staff_ref is None
    assert s.promise.nudged_by_customer is False and s.promise.fixed_occasion is False
    assert s.opportunity.need_fields_n is None  # None = không có nguồn ⇒ không trần
    assert s.thread.customer_audience_n is None
    st = L.DeliveryState()
    assert (st.work_hours.start, st.work_hours.end) == ("08:00", "17:30")


def test_r7_fields_validate_ranges_and_still_forbid_unknown():
    with pytest.raises(ValidationError):
        L.SignalIn(msg_at=T0, complaint_stage="angry")
    with pytest.raises(ValidationError):
        L.Opportunity(need_fields_n=5)
    with pytest.raises(ValidationError):
        L.SignalIn(msg_at=T0, prior_threat_no_exit_n=-1)
    with pytest.raises(ValidationError):
        L.SignalIn(msg_at=T0, not_a_field=True)


def test_r7_constants_and_pure_functions_exposed():
    assert (L.THETA_THREAT_NO_EXIT, L.THETA_NEED_FIELDS) == (1, 3)
    assert L.REGULAR_PUSH_ENABLED is True
    for name in ("prio", "red_type", "act_by", "public_ok", "deadline_eff", "next_change_at", "compute"):
        assert callable(getattr(L, name))
