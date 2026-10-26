# ruff: noqa: E501
"""v0.1.76 (T14) khoá bốn cổng thường ở đầu bản tin; v0.1.77 (spec r7.2) gỡ khoá: `levels.REGULAR_PUSH_ENABLED = True`. Cờ phiên bản áp ở
tầng `DeliveryState` (trước `levels.compute`). Mặc định bốn cổng thường chạy bóng 14 ngày (🔴 lên đầu bản tin kèm "đáng lẽ báo ngay …");
Sếp chọn `ramp_gates=push` ⇒ đẩy thật. Lừa đảo và an toàn luôn đẩy ngay."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels, signals
from tests.conftest import Api, verify_pin
from tests.mem_helpers import mk_org
from tests.test_intel_core_signals_v0176 import NOW, _install, _li, _raw, _up


def test_regular_push_enabled_in_v0177() -> None:
    """v0.1.77 (spec r7.2 §4.2.1 (e)): `levels.REGULAR_PUSH_ENABLED = True` — khoá của v0.1.76 được gỡ."""
    assert levels.REGULAR_PUSH_ENABLED is True


async def _regular_red(db: AsyncSession, org: uuid.UUID) -> signals.Signal:
    raw = await _raw(db, org)
    return await signals.upsert(db, org, _up("xung_dot_khieu_nai", _li("xung_dot_khieu_nai", customer_of_owner=True, impact_base=2, flags={"public": True}),
                                             raw, levels.case_key("xung_dot_khieu_nai", at=NOW, customer_entity_id="c1")), NOW)


async def test_regular_gate_red_pushes_when_sep_chose_push_after_ramp(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org, ramp_gates="push")            # đã cài từ 01/09 (> 14 ngày), Sếp chọn "Báo ngay"
    st = await signals.delivery_state(db, org, NOW)
    assert st.ramp_early_enable is True
    s = await _regular_red(db, org)
    assert (s.level, s.gate, s.delivery) == ("red", levels.G3, "push_now")
    assert s.would_push_at is None and "shadow" not in s.reasons


async def test_regular_gate_red_stays_digest_top_before_push_setting(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org, ramp_gates="digest_top", installed_at="2026-10-20T00:00:00+07:00", m1_passed_gates=[])   # mới cài 3 ngày, chưa M1 ⇒ bóng
    st = await signals.delivery_state(db, org, NOW)
    assert st.ramp_early_enable is False and st.m1_passed_gates == []
    s = await _regular_red(db, org)
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


async def test_put_settings_ramp_push_is_accepted_in_v0177(owner_api: Api) -> None:
    await verify_pin(owner_api)
    r = await owner_api.send("PUT", "/intel/settings", {"ramp_gates": "push"})
    assert r.status_code == 200
    st = (await owner_api.get("/intel/console")).json()
    assert st["ramp_gates"] == "push" and st["regular_push_enabled"] is True
    assert (await owner_api.send("PUT", "/intel/settings", {"ramp_gates": "digest_top"})).status_code == 200
