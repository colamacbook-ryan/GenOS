# ruff: noqa: E501
"""v0.1.76 (gói T3) — `POST /intel/missed` (M11): chỉ Owner; IDOR theo tổ chức (tin tổ chức khác ⇒ 404, không ghi gì); chữ Sếp dán được che
trước khi lưu; sót lừa đảo/an toàn mang cổng để M11 đếm; không hiện ở danh sách tín hiệu / bản tin."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import Api
from tests.test_rbac_api import login_as


async def _raw(db: AsyncSession, org: uuid.UUID, body: str) -> uuid.UUID:
    ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', :n) RETURNING id"), {"o": org, "n": uuid.uuid4().hex[:8]})).scalar_one()
    r = (await db.execute(text("""INSERT INTO raw.events (org_id, occurred_at, channel_id, external_msg_id, kind, body_text, payload, content_hash)
                                  VALUES (:o, now(), :c, :x, 'text', :b, '{}', '\\x01') RETURNING id"""), {"o": org, "c": ch, "x": uuid.uuid4().hex, "b": body})).scalar_one()
    await db.commit()
    return r  # type: ignore[no-any-return]


async def _org(db: AsyncSession) -> uuid.UUID:
    return (await db.execute(text("SELECT id FROM core.organizations ORDER BY created_at LIMIT 1"))).scalar_one()  # type: ignore[no-any-return]


async def _n_feedback(db: AsyncSession, verdict: str = "missed") -> int:
    return int((await db.execute(text("SELECT count(*) FROM biz.signal_feedback WHERE verdict = :v"), {"v": verdict})).scalar_one())


async def test_missed_from_text_records_masked_feedback_with_gate(owner_api: Api, db: AsyncSession) -> None:
    r = await owner_api.send("POST", "/intel/missed", {"text": "Có người xin chuyển tiền vào 0123456789012", "kind": "rui_ro_lua_dao", "note": "đáng lẽ báo ngay"})
    assert r.status_code == 200, r.text
    assert r.json()["gate"] == "lua_dao_dang_dien_ra"
    row = (await db.execute(text("SELECT a.summary, a.status, a.kind, f.verdict, f.gate, f.note FROM biz.signal_feedback f JOIN biz.alerts a ON a.id = f.signal_id"))).one()
    assert "0123456789012" not in row.summary and row.status == "dismissed" and row.kind == "legacy"
    assert (row.verdict, row.gate) == ("missed", "lua_dao_dang_dien_ra") and "rui_ro_lua_dao" in row.note
    listed = (await owner_api.get("/signals")).json()["items"]
    assert listed == []                                                      # không hiện thành tín hiệu


async def test_missed_from_raw_event_of_own_org(owner_api: Api, db: AsyncSession) -> None:
    raw = await _raw(db, await _org(db), "khách doạ bỏ đi")
    r = await owner_api.send("POST", "/intel/missed", {"raw_event_id": str(raw), "kind": "xung_dot_khieu_nai"})
    assert r.status_code == 200 and r.json()["gate"] is None
    assert await _n_feedback(db) == 1


async def test_missed_idor_other_org_raw_event_is_404_and_writes_nothing(owner_api: Api, db: AsyncSession) -> None:
    other = (await db.execute(text("INSERT INTO core.organizations (name) VALUES ('Khác') RETURNING id"))).scalar_one()
    await db.commit()
    foreign = await _raw(db, other, "tin của tổ chức khác")
    r = await owner_api.send("POST", "/intel/missed", {"raw_event_id": str(foreign)})
    assert r.status_code == 404
    assert await _n_feedback(db) == 0
    assert (await db.execute(text("SELECT count(*) FROM biz.alerts"))).scalar_one() == 0


async def test_missed_only_owner_and_authenticated(owner_api: Api, client, db: AsyncSession) -> None:  # type: ignore[no-untyped-def]
    mgr = await login_as(client, db, "manager")
    assert (await mgr.send("POST", "/intel/missed", {"text": "sót"})).status_code == 403
    anon = Api(client)
    anon.c.cookies.clear()
    assert (await anon.send("POST", "/intel/missed", {"text": "sót"})).status_code in (401, 403)
    assert await _n_feedback(db) == 0


async def test_missed_validation(owner_api: Api, db: AsyncSession) -> None:
    assert (await owner_api.send("POST", "/intel/missed", {})).status_code == 422
    assert (await owner_api.send("POST", "/intel/missed", {"text": "a", "raw_event_id": str(uuid.uuid4())})).status_code == 422
    assert (await owner_api.send("POST", "/intel/missed", {"text": "a", "kind": "khong_co"})).status_code == 422
    assert await _n_feedback(db) == 0
