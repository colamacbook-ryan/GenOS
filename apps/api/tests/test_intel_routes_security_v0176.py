# ruff: noqa: E501
"""v0.1.76 (gói T1) — bảo mật route Tình báo: `privacy='console_pin'` chỉ Owner có PIN (423 khi thiếu PIN, vai khác không thấy), org_id/IDOR
(không đọc/ghi tín hiệu tổ chức khác), mute lừa đảo/an toàn cần PIN, ghi chỉ Owner, không trả chữ tin thô."""

from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels, signals
from gh.refinery import clock
from tests.conftest import Api, verify_pin
from tests.test_rbac_api import login_as


async def _org(db: AsyncSession) -> uuid.UUID:
    return (await db.execute(text("SELECT id FROM core.organizations ORDER BY created_at LIMIT 1"))).scalar_one()  # type: ignore[no-any-return]


async def _signal(db: AsyncSession, org: uuid.UUID, *, privacy: str = "chat_ok", kind: str = "xung_dot_khieu_nai",
                  quote: str = "khách chê STK 0123456789012") -> uuid.UUID:
    ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', :n) RETURNING id"),
                           {"o": org, "n": uuid.uuid4().hex[:8]})).scalar_one()
    raw = (await db.execute(text("""INSERT INTO raw.events (org_id, occurred_at, channel_id, external_msg_id, kind, body_text, payload, content_hash)
                                    VALUES (:o, now(), :c, :x, 'text', :b, '{}', '\\x01') RETURNING id"""),
                            {"o": org, "c": ch, "x": uuid.uuid4().hex, "b": quote})).scalar_one()
    now = clock.now()
    li = levels.SignalIn(kind=kind, privacy=privacy, msg_at=now - timedelta(hours=1), customer_of_owner=True, harm_what="mất khách",  # type: ignore[arg-type]
                         flags=levels.Flags(public=True), confidence_content=0.8, source_class="insider")
    sig = await signals.upsert(db, org, signals.UpsertIn(
        kind=kind, title="Tín hiệu", detector="test", level_input=li, case_key=f"k:{uuid.uuid4()}",
        evidence=[signals.EvidenceIn(type="raw_event", id=raw, source_field="body", span_start=0, span_end=8, quote=quote)]), now)
    await db.commit()
    return sig.id


async def test_intel_routes_console_pin_signal_needs_owner_pin(owner_api: Api, db: AsyncSession) -> None:
    org = await _org(db)
    pub = await _signal(db, org)
    priv = await _signal(db, org, privacy="console_pin")
    r = await owner_api.get("/signals")
    assert r.status_code == 200
    ids = {i["id"] for i in r.json()["items"]}
    assert str(pub) in ids and str(priv) not in ids and r.json()["hidden_pin_n"] == 1
    assert (await owner_api.get(f"/signals/{priv}")).status_code == 423
    await verify_pin(owner_api)
    r = await owner_api.get(f"/signals/{priv}")
    assert r.status_code == 200 and r.json()["privacy"] == "console_pin"
    assert str(priv) in {i["id"] for i in (await owner_api.get("/signals")).json()["items"]}


async def test_intel_routes_detail_has_no_raw_text(owner_api: Api, db: AsyncSession) -> None:
    org = await _org(db)
    sid = await _signal(db, org)
    body = (await owner_api.get(f"/signals/{sid}")).text
    assert "0123456789012" not in body
    assert "quote_display" in body


async def test_intel_routes_other_role_never_sees_private_and_cannot_write(owner_api: Api, client: httpx.AsyncClient,
                                                                           db: AsyncSession) -> None:
    org = await _org(db)
    pub = await _signal(db, org)
    priv = await _signal(db, org, privacy="console_pin")
    mgr = await login_as(client, db, "manager")
    await verify_pin(mgr, "112233")
    r = await mgr.get("/signals")
    assert r.status_code == 200 and str(priv) not in {i["id"] for i in r.json()["items"]} and r.json()["hidden_pin_n"] == 0
    assert (await mgr.get(f"/signals/{priv}")).status_code == 404
    assert (await mgr.send("POST", f"/signals/{pub}/feedback", {"verdict": "wrong"})).status_code == 403
    assert (await mgr.send("POST", f"/signals/{pub}/transition", {"to": "resolved"})).status_code == 403
    assert (await mgr.send("POST", "/intel/mutes", {"scope_type": "kind", "scope_value": "viec_han"})).status_code == 403
    assert (await mgr.get("/catalog")).status_code == 403


async def test_intel_routes_idor_other_org_signal_is_404(owner_api: Api, db: AsyncSession) -> None:
    other = (await db.execute(text("INSERT INTO core.organizations (name) VALUES ('Khác') RETURNING id"))).scalar_one()
    await db.commit()
    foreign = await _signal(db, other)
    assert (await owner_api.get(f"/signals/{foreign}")).status_code == 404
    assert (await owner_api.send("POST", f"/signals/{foreign}/feedback", {"verdict": "wrong"})).status_code == 404
    assert (await owner_api.send("POST", f"/signals/{foreign}/transition", {"to": "resolved"})).status_code == 404
    st = (await db.execute(text("SELECT status FROM biz.alerts WHERE id = :i"), {"i": foreign})).scalar_one()
    assert st == "open"
    assert str(foreign) not in {i["id"] for i in (await owner_api.get("/signals")).json()["items"]}


async def test_intel_routes_mute_defensive_kind_requires_pin(owner_api: Api) -> None:
    r = await owner_api.send("POST", "/intel/mutes", {"scope_type": "kind", "scope_value": "rui_ro_lua_dao"})
    assert r.status_code == 423
    r = await owner_api.send("POST", "/intel/mutes", {"scope_type": "kind", "scope_value": "viec_han"})
    assert r.status_code == 200 and r.json()["source"] == "chat"
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/intel/mutes", {"scope_type": "kind", "scope_value": "rui_ro_lua_dao"})
    assert r.status_code == 200 and r.json()["source"] == "pin"
    assert (await owner_api.send("DELETE", f"/intel/mutes/{r.json()['id']}")).status_code == 200


async def test_intel_routes_owner_resolve_and_feedback(owner_api: Api, db: AsyncSession) -> None:
    org = await _org(db)
    sid = await _signal(db, org)
    r = await owner_api.send("POST", f"/signals/{sid}/transition", {"to": "resolved"})
    assert r.status_code == 200 and (r.json()["level"], r.json()["delivery"], r.json()["status"]) == ("grey", "none", "resolved")
    assert (await owner_api.send("POST", f"/signals/{sid}/transition", {"to": "resolved"})).status_code == 409
    sid2 = await _signal(db, org)
    r = await owner_api.send("POST", f"/signals/{sid2}/feedback", {"verdict": "wrong"})
    assert r.status_code == 200 and r.json()["status"] == "dismissed"
    m = (await owner_api.get("/intel/metrics")).json()
    assert len(m["precision_ci"]) == 6 and "would_push_n" in m["shadow"]


async def test_intel_routes_catalog_crud_owner(owner_api: Api) -> None:
    r = await owner_api.send("POST", "/catalog", {"name": "Hạt cà phê rang mộc", "price_vnd": 250000, "unit": "kg"})
    assert r.status_code == 200
    iid = r.json()["id"]
    assert (await owner_api.send("PATCH", f"/catalog/{iid}", {"price_vnd": 260000})).json()["price_vnd"] == 260000
    assert (await owner_api.send("DELETE", f"/catalog/{iid}")).status_code == 200
    assert iid not in {i["id"] for i in (await owner_api.get("/catalog")).json()["items"]}
    assert (await owner_api.send("PATCH", f"/catalog/{uuid.uuid4()}", {"price_vnd": 1})).status_code == 404


async def test_intel_routes_validation(owner_api: Api) -> None:
    assert (await owner_api.get("/signals?kind=people")).status_code == 422
    assert (await owner_api.get("/signals?level=purple")).status_code == 422
