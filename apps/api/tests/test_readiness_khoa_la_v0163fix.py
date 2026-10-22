"""Gói sửa review v0.1.63 — P3: khoá lạ (không có hồ sơ vai) — readiness coi là CẦN HỘP, khớp bộ định tuyến
(`placement.UNKNOWN_PLACEMENT` = box, đóng an toàn). Trước: readiness trả "không cần hộp" ⇒ báo dùng được agy trong khi
bộ định tuyến bỏ agy khi hộp tắt."""

from typing import Any

import pytest

from gh.providers import agy_policy, placement, readiness
from tests.phase2 import org_id
from tests.test_ai_readiness_hop_dong import AGY, _has_org, _src  # noqa: F401 — fixture tự dùng (dựng tổ chức)


def test_soft_gate_unknown_role_requires_box() -> None:
    gate = readiness.SoftBoxGate()
    assert gate.required(None, "core.khoa_la") is True
    assert gate.required(None, None) is True
    assert placement.box_required("core.khoa_la") is True                     # cùng luật với bộ định tuyến


async def test_unknown_key_readiness_agrees_with_router(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from gh.agybox import client as box_client

    org = await org_id(db)
    await _src(db, AGY, key=False, models=("gemini-3.8-flash",))

    async def down(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"state": "down"}

    monkeypatch.setattr(box_client, "box_status", down)
    r = await readiness.ai_ready(db, org, "core.khoa_la")
    verdict = agy_policy.decide("core.khoa_la", box="down")
    assert not verdict.allowed and (r["ok"], r["reason_code"]) == (False, "box_down")
