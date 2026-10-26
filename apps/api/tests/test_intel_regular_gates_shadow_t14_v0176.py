# ruff: noqa: E501
"""v0.1.76 (T14, Ryan quyết 2026-10-26 "phát hành phần chắc"): trong bản này CHỈ cổng 1 (lừa đảo đang diễn ra) và cổng 2 (an toàn) đẩy 🔴 ngay;
bốn cổng thường (`levels.REGULAR_GATES`) luôn chỉ lên đầu bản tin ("đáng lẽ báo ngay …") — kể cả đã qua 14 ngày, đã đạt M1, hay Sếp chọn
`ramp_gates=push`. Cờ phiên bản `levels.REGULAR_PUSH_ENABLED` áp ở tầng `DeliveryState` (trước `levels.compute`) nên hàm thuần và bộ vector
không đổi. `PUT /intel/settings` với `ramp_gates=push` ⇒ 422 thân thiện."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels, signals
from tests.conftest import Api, verify_pin
from tests.mem_helpers import mk_org
from tests.test_intel_core_signals_v0176 import NOW, _install, _li, _raw, _up


def test_regular_push_disabled_in_this_release() -> None:
    assert levels.REGULAR_PUSH_ENABLED is False


async def test_regular_gate_red_stays_digest_top_even_after_ramp_and_push_setting(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org, ramp_gates="push")            # đã cài từ 01/09 (> 14 ngày), mọi cổng thường đạt M1, Sếp chọn "Báo ngay"
    st = await signals.delivery_state(db, org, NOW)
    assert st.ramp_early_enable is False and st.m1_passed_gates == []
    raw = await _raw(db, org)
    s = await signals.upsert(db, org, _up("xung_dot_khieu_nai", _li("xung_dot_khieu_nai", customer_of_owner=True, flags={"public": True}), raw,
                                          levels.case_key("xung_dot_khieu_nai", at=NOW, customer_entity_id="c1")), NOW)
    assert (s.level, s.gate, s.delivery) == ("red", levels.G3, "digest_top")
    assert s.pushed_at is None and s.would_push_at == NOW and "shadow" in s.reasons


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


async def test_put_settings_ramp_push_is_rejected_friendly(owner_api: Api) -> None:
    await verify_pin(owner_api)
    r = await owner_api.send("PUT", "/intel/settings", {"ramp_gates": "push"})
    assert r.status_code == 422 and "đầu bản tin" in r.json()["title"]
    assert (await owner_api.send("PUT", "/intel/settings", {"ramp_gates": "digest_top"})).status_code == 200
    st = (await owner_api.get("/intel/console")).json()
    assert st["ramp_gates"] == "digest_top" and st["regular_push_enabled"] is False
