# ruff: noqa: E501
"""v0.1.76 (gói T11) — route thẻ "Tình báo" trên Console: khoá/mở khoá trợ lý, mở khoá hôm nay, cách chạy, xác minh STK, gán nhãn nhóm hàng loạt,
mute lừa đảo/an toàn. Thiếu PIN ⇒ 423, không phải Owner ⇒ 403, tổ chức khác ⇒ 404, idempotent, không lộ mã/PIN trong log/Nhật ký."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import OWNER, Api, verify_pin
from tests.test_intel_routes_security_v0176 import _org, _signal
from tests.test_rbac_api import login_as

PIN_ROUTES = [("POST", "/intel/unlock-assistant"), ("POST", "/intel/unlock-today"), ("PUT", "/intel/settings"),
              ("POST", f"/intel/signals/{uuid.uuid4()}/verify-payee"), ("POST", f"/intel/payment-identifiers/{uuid.uuid4()}/verify")]
ALL_ROUTES = [*PIN_ROUTES, ("POST", "/intel/lock-assistant"), ("GET", "/intel/console"),
              ("POST", "/intel/group-contexts/bulk")]


def _body(method: str, url: str) -> Any:
    if url.endswith("/settings"):
        return {"mode": "live"}
    if url.endswith("/bulk"):
        return {"group_ids": [str(uuid.uuid4())], "labels": [{"context": "khac"}]}
    return {} if method == "POST" else None


@pytest.mark.parametrize(("method", "url"), PIN_ROUTES)
async def test_pin_routes_need_pin(owner_api: Api, method: str, url: str) -> None:
    r = await owner_api.send(method, url, _body(method, url))
    assert r.status_code == 423, r.text


@pytest.mark.parametrize(("method", "url"), ALL_ROUTES)
async def test_non_owner_forbidden(owner_api: Api, client: httpx.AsyncClient, db: AsyncSession, method: str, url: str) -> None:
    mgr = await login_as(client, db, "manager")
    await verify_pin(mgr, "112233")
    r = await (mgr.get(url) if method == "GET" else mgr.send(method, url, _body(method, url)))
    assert r.status_code == 403, r.text


async def test_lock_then_unlock_assistant_idempotent_and_clean_logs(owner_api: Api, db: AsyncSession, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    r = await owner_api.send("POST", "/intel/lock-assistant", {})       # lệnh khẩn: không cần PIN
    assert r.status_code == 200 and r.json() == {"ok": True, "locked": True, "already": False}
    assert (await owner_api.send("POST", "/intel/lock-assistant", {})).json()["already"] is True
    assert (await owner_api.get("/intel/console")).json()["assistant"]["locked"] is True
    assert (await owner_api.send("POST", "/intel/unlock-assistant", {})).status_code == 423
    assert (await owner_api.get("/intel/console")).json()["assistant"]["locked"] is True
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/intel/unlock-assistant", {})
    assert r.status_code == 200 and r.json()["already"] is False
    assert (await owner_api.send("POST", "/intel/unlock-assistant", {})).json()["already"] is True
    st = (await owner_api.get("/intel/console")).json()
    assert st["assistant"]["locked"] is False
    n = (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'assistant.unlock'"))).scalar_one()
    assert n == 1
    blob = json.dumps([dict(r._mapping) for r in (await db.execute(text("SELECT action, detail FROM ops.action_log"))).all()], default=str)
    assert OWNER["pin"] not in blob and OWNER["pin"] not in caplog.text


async def test_unlock_today_idempotent_and_blocked_while_locked(owner_api: Api, db: AsyncSession) -> None:
    await verify_pin(owner_api)
    await owner_api.send("POST", "/intel/lock-assistant", {})
    assert (await owner_api.send("POST", "/intel/unlock-today", {})).status_code == 409
    await owner_api.send("POST", "/intel/unlock-assistant", {})
    r = await owner_api.send("POST", "/intel/unlock-today", {})
    assert r.status_code == 200 and r.json()["already"] is False
    assert (await owner_api.send("POST", "/intel/unlock-today", {})).json()["already"] is True
    assert (await owner_api.get("/intel/console")).json()["outbound"]["unlocked_today"] is True
    n = (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'strong_confirm.unlock_today'"))).scalar_one()
    assert n == 1


async def test_settings_mode_and_ramp(owner_api: Api, db: AsyncSession) -> None:
    await verify_pin(owner_api)
    st = (await owner_api.get("/intel/console")).json()
    assert (st["mode"], st["ramp_gates"]) == ("live", "digest_top")
    r = await owner_api.send("PUT", "/intel/settings", {"mode": "shadow"})
    assert r.status_code == 200 and r.json()["changed"] == ["mode"]
    st = (await owner_api.get("/intel/console")).json()
    assert (st["mode"], st["ramp_gates"]) == ("shadow", "digest_top")
    assert (await owner_api.send("PUT", "/intel/settings", {"ramp_gates": "push"})).status_code == 200     # v0.1.77: gỡ khoá «Báo ngay» cổng thường
    assert (await owner_api.send("PUT", "/intel/settings", {"mode": "shadow"})).json()["changed"] == []
    assert (await owner_api.send("PUT", "/intel/settings", {})).status_code == 422
    assert (await owner_api.send("PUT", "/intel/settings", {"mode": "bad"})).status_code == 422
    n = (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'strong_confirm.console'"))).scalar_one()
    assert n == 1      # chỉ lần live → shadow đi qua cổng hạ phòng thủ


async def _payee_signal(db: AsyncSession, org: uuid.UUID) -> uuid.UUID:
    sid = await _signal(db, org, kind="rui_ro_lua_dao")
    await db.execute(text("""UPDATE biz.alerts SET facts = CAST(:f AS jsonb) WHERE id = :i"""),
                     {"i": sid, "f": json.dumps({"payee_digest": "a" * 64, "payee_last3": "789", "payee_bank": "VCB"})})
    await db.commit()
    return sid


async def test_verify_payee_flow_and_idor(owner_api: Api, db: AsyncSession) -> None:
    org = await _org(db)
    sid = await _payee_signal(db, org)
    other = (await db.execute(text("INSERT INTO core.organizations (name) VALUES ('Khác') RETURNING id"))).scalar_one()
    await db.commit()
    foreign = await _payee_signal(db, other)
    await verify_pin(owner_api)
    st = (await owner_api.get("/intel/console")).json()
    assert [p["signal_id"] for p in st["payees"]] == [str(sid)] and st["payees"][0]["last3"] == "789"
    assert "a" * 64 not in json.dumps(st)
    assert (await owner_api.send("POST", f"/intel/signals/{foreign}/verify-payee", {})).status_code == 404
    assert (await owner_api.send("POST", f"/intel/signals/{uuid.uuid4()}/verify-payee", {})).status_code == 404
    assert (await owner_api.send("POST", f"/intel/payment-identifiers/{uuid.uuid4()}/verify", {})).status_code == 404
    r = await owner_api.send("POST", f"/intel/signals/{sid}/verify-payee", {})
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    assert (await owner_api.get("/intel/console")).json()["payees"] == []
    n = (await db.execute(text("SELECT count(*) FROM biz.payment_identifiers WHERE org_id = :o AND verified_at IS NOT NULL"), {"o": org})).scalar_one()
    assert n == 1


async def test_bulk_group_contexts_all_or_nothing(owner_api: Api, db: AsyncSession) -> None:
    org = await _org(db)
    other = (await db.execute(text("INSERT INTO core.organizations (name) VALUES ('Khác') RETURNING id"))).scalar_one()
    ids = []
    for o, nm in ((org, "A"), (org, "B"), (other, "X")):
        ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', :n) RETURNING id"), {"o": o, "n": uuid.uuid4().hex[:8]})).scalar_one()
        ids.append((await db.execute(text("INSERT INTO core.groups (org_id, channel_id, code, name, external_id) VALUES (:o, :c, :cd, :n, :e) RETURNING id"),
                                     {"o": o, "c": ch, "cd": uuid.uuid4().hex[:8], "n": nm, "e": uuid.uuid4().hex})).scalar_one())
    await db.commit()
    a, b, x = ids
    labels = [{"context": "kinh_doanh", "is_primary": True}, {"context": "ban_be"}]
    r = await owner_api.send("POST", "/intel/group-contexts/bulk", {"group_ids": [str(a), str(x)], "labels": labels})
    assert r.status_code == 404
    assert (await owner_api.get("/intel/console")).json()["groups"][0]["labels"] == []
    assert (await owner_api.send("POST", "/intel/group-contexts/bulk", {"group_ids": [str(a)], "labels": [{"context": "xyz"}]})).status_code == 422
    r = await owner_api.send("POST", "/intel/group-contexts/bulk", {"group_ids": [str(a), str(b), str(a)], "labels": labels})
    assert r.status_code == 200 and r.json()["updated"] == 2
    gs = {g["name"]: g["labels"] for g in (await owner_api.get("/intel/console")).json()["groups"]}
    assert gs["A"] == gs["B"] == [{"context": "kinh_doanh", "is_primary": True}, {"context": "ban_be", "is_primary": False}]
    left = (await db.execute(text("SELECT count(*) FROM mem.group_contexts WHERE group_id = :g"), {"g": x})).scalar_one()
    assert left == 0


async def test_defensive_mute_needs_pin_and_is_logged_with_backup_notice(owner_api: Api, db: AsyncSession) -> None:
    r = await owner_api.send("POST", "/intel/mutes", {"scope_type": "kind", "scope_value": "suc_khoe_an_toan"})
    assert r.status_code == 423
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/intel/mutes", {"scope_type": "kind", "scope_value": "suc_khoe_an_toan"})
    assert r.status_code == 200 and r.json()["source"] == "pin"
    st = (await owner_api.get("/intel/console")).json()
    assert [m["scope_value"] for m in st["mutes"]] == ["suc_khoe_an_toan"]
    n = (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'strong_confirm.console'"))).scalar_one()
    assert n == 1
    assert (await owner_api.send("DELETE", f"/intel/mutes/{r.json()['id']}")).status_code == 200
    assert (await owner_api.get("/intel/console")).json()["mutes"] == []


async def test_console_state_defaults(owner_api: Api) -> None:
    st = (await owner_api.get("/intel/console")).json()
    assert st["backup_channel"] == {"paired": False, "kind": "none"} and st["contexts"] and st["groups"] == []


# ─── review bảo mật T11: M1 mute phạm vi khác + m1/m4/m7 ─────────────────────

@pytest.mark.parametrize(("scope_type", "scope_value"), [("detector", "fraud"), ("detector", "safety"), ("group", "00000000-0000-0000-0000-000000000001"),
                                                          ("person", "00000000-0000-0000-0000-000000000002"),
                                                          ("kind", "viec_han"), ("kind", "quan_he_nong_nguoi")])
async def test_pin_mute_of_other_scope_is_defensive_and_notifies(owner_api: Api, db: AsyncSession, scope_type: str, scope_value: str) -> None:
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/intel/mutes", {"scope_type": scope_type, "scope_value": scope_value})
    assert r.status_code == 200 and r.json()["source"] == "pin"
    assert (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'strong_confirm.console'"))).scalar_one() == 1
    assert (await db.execute(text("SELECT count(*) FROM ops.backup_outbox"))).scalar_one() == 1


async def test_chat_source_mute_of_other_scope_needs_no_notice(owner_api: Api, db: AsyncSession) -> None:
    r = await owner_api.send("POST", "/intel/mutes", {"scope_type": "detector", "scope_value": "fraud"})
    assert r.status_code == 200 and r.json()["source"] == "chat"
    assert (await db.execute(text("SELECT count(*) FROM ops.backup_outbox"))).scalar_one() == 0


async def test_mute_dedupe_returns_existing_and_notifies_once(owner_api: Api, db: AsyncSession) -> None:
    await verify_pin(owner_api)
    a = (await owner_api.send("POST", "/intel/mutes", {"scope_type": "kind", "scope_value": "rui_ro_lua_dao"})).json()
    b = (await owner_api.send("POST", "/intel/mutes", {"scope_type": "kind", "scope_value": "rui_ro_lua_dao"})).json()
    assert a["id"] == b["id"]
    assert (await db.execute(text("SELECT count(*) FROM biz.signal_mutes WHERE revoked_at IS NULL"))).scalar_one() == 1
    assert (await db.execute(text("SELECT count(*) FROM ops.backup_outbox"))).scalar_one() == 1


async def test_locking_assistant_drops_unlock_today(owner_api: Api) -> None:
    await verify_pin(owner_api)
    assert (await owner_api.send("POST", "/intel/unlock-today", {})).status_code == 200
    await owner_api.send("POST", "/intel/lock-assistant", {})
    await owner_api.send("POST", "/intel/unlock-assistant", {})
    assert (await owner_api.get("/intel/console")).json()["outbound"]["unlocked_today"] is False


async def test_unlock_log_actor_has_user_prefix(owner_api: Api, db: AsyncSession) -> None:
    await verify_pin(owner_api)
    await owner_api.send("POST", "/intel/lock-assistant", {})
    await owner_api.send("POST", "/intel/unlock-assistant", {})
    actor = (await db.execute(text("SELECT actor_id FROM ops.action_log WHERE action = 'assistant.unlock'"))).scalar_one()
    assert actor.startswith("user:")
