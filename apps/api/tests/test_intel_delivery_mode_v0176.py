# ruff: noqa: E501
"""v0.1.76 (gói T3) — `intel.mode` (live | shadow) chỉ đổi được trên Console + PIN, chỉ Owner; live → shadow là hạ phòng thủ nên ghi Nhật ký và báo kênh phụ."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import Api, verify_pin
from tests.test_rbac_api import login_as
from tests.test_watchdog_backup_v0176 import _telegram


async def _mode(db: AsyncSession) -> str | None:
    return (await db.execute(text("SELECT settings->'intel'->>'mode' FROM core.organizations ORDER BY created_at LIMIT 1"))).scalar_one()  # type: ignore[no-any-return]


async def test_mode_needs_pin_and_owner(owner_api: Api, client, db: AsyncSession) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.send("PUT", "/owner-channel/zalo", {"intel_mode": "shadow"})
    assert r.status_code in (401, 403, 423) and await _mode(db) is None            # chưa PIN
    mgr = await login_as(client, db, "manager")
    await verify_pin(mgr, "112233")
    assert (await mgr.send("PUT", "/owner-channel/zalo", {"intel_mode": "shadow"})).status_code == 403
    assert await _mode(db) is None
    await verify_pin(owner_api)
    assert (await owner_api.send("PUT", "/owner-channel/zalo", {"intel_mode": "bong"})).status_code == 422


async def test_live_to_shadow_with_pin_logs_and_notifies_backup(owner_api: Api, db: AsyncSession) -> None:
    org = (await db.execute(text("SELECT id FROM core.organizations ORDER BY created_at LIMIT 1"))).scalar_one()
    await _telegram(db, org)
    await verify_pin(owner_api)
    r = await owner_api.send("PUT", "/owner-channel/zalo", {"intel_mode": "shadow"})
    assert r.status_code == 200, r.text
    assert r.json()["intel_mode"] == "shadow" and await _mode(db) == "shadow"
    assert (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'strong_confirm.console'"))).scalar_one() == 1
    assert "chạy bóng" in (await db.execute(text("SELECT text FROM ops.backup_outbox WHERE kind = 'security'"))).scalar_one()
    r = await owner_api.send("PUT", "/owner-channel/zalo", {"intel_mode": "live"})
    assert r.status_code == 200 and r.json()["intel_mode"] == "live" and await _mode(db) == "live"
