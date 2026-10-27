# ruff: noqa: E501
"""v0.1.76 (T14) → v0.1.77 (P4): cờ phiên bản `levels.REGULAR_PUSH_ENABLED` áp ở tầng `DeliveryState` (trước `levels.compute`) nên hàm thuần và bộ
vector không đổi. v0.1.76 khoá bốn cổng thường ở đầu bản tin; v0.1.77 BẬT cờ CÙNG ramp R4 (chạy thử 14 ngày từ `intel.ramp_started_at`, M1 gộp
n ≥ 10): cài mới / đang chạy thử ⇒ vẫn bóng; qua ramp + đạt M1 (hoặc Sếp chọn `ramp_gates=push`) ⇒ mới đẩy thật. Lừa đảo/an toàn luôn đẩy.
Test ramp đầy đủ ở `test_intel_p4_v0177.py`."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels, signals
from tests.conftest import Api, verify_pin
from tests.mem_helpers import mk_org
from tests.test_intel_core_signals_v0176 import NOW, _install, _li, _raw, _up


def test_regular_push_enabled_with_ramp_in_v0177() -> None:
    assert levels.REGULAR_PUSH_ENABLED is True


async def test_regular_gate_red_is_shadow_when_install_is_new_but_pushed_after_ramp_and_m1(db: AsyncSession) -> None:
    from datetime import timedelta

    new = await mk_org(db)
    await _install(db, new, installed_at=(NOW - timedelta(days=3)).isoformat())     # cài mới ⇒ chạy thử dù `m1_passed_gates` đã có
    st = await signals.delivery_state(db, new, NOW)
    assert st.ramp_early_enable is False and st.m1_passed_gates == list(levels.REGULAR_GATES) and st.days_since_install < 14
    raw = await _raw(db, new)
    s = await signals.upsert(db, new, _up("xung_dot_khieu_nai", _li("xung_dot_khieu_nai", customer_of_owner=True, flags={"public": True}), raw,
                                          levels.case_key("xung_dot_khieu_nai", at=NOW, customer_entity_id="c1")), NOW)
    assert (s.level, s.gate, s.delivery) == ("red", levels.G3, "digest_top")
    assert s.pushed_at is None and s.would_push_at == NOW and "shadow" in s.reasons
    old = await mk_org(db)
    await _install(db, old)                                                          # cài từ 01/09 (> 14 ngày) + mọi cổng thường đạt M1
    raw2 = await _raw(db, old)
    s2 = await signals.upsert(db, old, _up("xung_dot_khieu_nai", _li("xung_dot_khieu_nai", customer_of_owner=True, flags={"public": True}), raw2,
                                           levels.case_key("xung_dot_khieu_nai", at=NOW, customer_entity_id="c1")), NOW)
    assert (s2.level, s2.gate, s2.delivery) == ("red", levels.G3, "push_now") and s2.pushed_at == NOW


async def test_fraud_and_safety_still_push_now(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org, ramp_gates="push")
    raw = await _raw(db, org, "lỡ nhập OTP")
    fr = await signals.upsert(db, org, _up(levels.FR, _li(levels.FR, hard_rule_hit=["credentials_entered"], ago_h=0.1), raw,
                                           levels.case_key(levels.FR, at=NOW, raw_event_id=str(raw))), NOW)
    assert (fr.level, fr.gate, fr.delivery) == ("red", levels.G1, "push_now")
    raw2 = await _raw(db, org, "cháy bếp")
    sk = await signals.upsert(db, org, _up(levels.SK, _li(levels.SK, flags={"emergency": True}, ago_h=0.1), raw2,
                                           levels.case_key(levels.SK, at=NOW, raw_event_id=str(raw2))), NOW)
    assert (sk.level, sk.gate, sk.delivery) == ("red", levels.G2, "push_now")


async def test_put_settings_ramp_push_is_accepted_in_v0177(owner_api: Api) -> None:
    await verify_pin(owner_api)
    r = await owner_api.send("PUT", "/intel/settings", {"ramp_gates": "push"})
    assert r.status_code == 200 and r.json()["ramp_gates"] == "push"
    assert (await owner_api.send("PUT", "/intel/settings", {"ramp_gates": "digest_top"})).status_code == 200
    st = (await owner_api.get("/intel/console")).json()
    assert st["ramp_gates"] == "digest_top" and st["regular_push_enabled"] is True
