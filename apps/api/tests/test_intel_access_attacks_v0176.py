# ruff: noqa: E501
"""v0.1.76 (gói T1) — test TẤN CÔNG cho 3 lỗi kiểm bảo mật báo trên T1 (đường anh em phải cùng cổng — L-90):
1. hàng chung `/inbox` (đọc + `/act`) không được lộ / acknowledge tín hiệu `privacy='console_pin'` cho vai khác hoặc Owner chưa mở PIN;
2. tín hiệu có bằng chứng `raw_event` (đường nhanh) / `group_id` thuộc nhóm «Chỉ Sếp» phải ẩn với vai khác ở MỌI đường đọc
   (`/inbox`, `/signals`, `/explain`);
3. `GET /asks` không trả `summary` của ask gắn tín hiệu `console_pin` (và ask Hồ sơ/danh tính nói về người) khi chưa mở PIN."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import asks, levels, signals
from gh.refinery import clock
from tests.conftest import Api, verify_pin
from tests.test_rbac_api import login_as


async def _org(db: AsyncSession) -> uuid.UUID:
    return (await db.execute(text("SELECT id FROM core.organizations ORDER BY created_at LIMIT 1"))).scalar_one()  # type: ignore[no-any-return]


async def _group(db: AsyncSession, org: uuid.UUID, view_scope: str) -> tuple[uuid.UUID, uuid.UUID]:
    ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', :n) RETURNING id"),
                           {"o": org, "n": uuid.uuid4().hex[:8]})).scalar_one()
    g = (await db.execute(text("""INSERT INTO core.groups (org_id, code, channel_id, external_id, name, view_scope)
                                  VALUES (:o, :c, :ch, :x, 'Nhóm', :v) RETURNING id"""),
                          {"o": org, "c": "GRP-" + uuid.uuid4().hex[:6], "ch": ch, "x": uuid.uuid4().hex, "v": view_scope})).scalar_one()
    return ch, g


async def _signal(db: AsyncSession, org: uuid.UUID, *, privacy: str = "chat_ok", view_scope: str = "all_members",
                  suggested: str | None = None) -> uuid.UUID:
    ch, g = await _group(db, org, view_scope)
    raw = (await db.execute(text("""INSERT INTO raw.events (org_id, occurred_at, channel_id, external_msg_id, kind, body_text, payload,
                                                            content_hash, group_id)
                                    VALUES (:o, now(), :c, :x, 'text', 'nội dung nhóm kín', '{}', '\\x01', :g) RETURNING id"""),
                            {"o": org, "c": ch, "x": uuid.uuid4().hex, "g": g})).scalar_one()
    now = clock.now()
    li = levels.SignalIn(kind="xung_dot_khieu_nai", privacy=privacy, msg_at=now - timedelta(hours=1), customer_of_owner=True,  # type: ignore[arg-type]
                         harm_what="mất khách", flags=levels.Flags(public=True), confidence_content=0.8, source_class="insider")
    sig = await signals.upsert(db, org, signals.UpsertIn(
        kind="xung_dot_khieu_nai", title="Bí mật nhân sự X" if privacy == "console_pin" else "Khiếu nại công khai", detector="raw_tail", level_input=li, case_key=f"k:{uuid.uuid4()}",
        group_id=g, suggested_action=suggested, action_executable=suggested is not None,
        evidence=[signals.EvidenceIn(type="raw_event", id=raw, source_field="body", span_start=0, span_end=7)]), now)
    await db.commit()
    return sig.id


async def _status(db: AsyncSession, sid: uuid.UUID) -> str:
    await db.rollback()
    return (await db.execute(text("SELECT status FROM biz.alerts WHERE id = :i"), {"i": sid})).scalar_one()  # type: ignore[no-any-return]


def _ids(r: httpx.Response) -> set[str]:
    body: Any = r.json()
    return {str(i.get("item_id") or i.get("id")) for i in body.get("items", [])}


# ─── 1. hàng chung /inbox ─────────────────────────────────────────────────────

async def test_attack_inbox_other_role_cannot_see_or_ack_console_pin_signal(owner_api: Api, client: httpx.AsyncClient,
                                                                            db: AsyncSession) -> None:
    org = await _org(db)
    priv = await _signal(db, org, privacy="console_pin")
    pub = await _signal(db, org)
    op = await login_as(client, db, "operator")
    await verify_pin(op, "112233")
    lst = await op.get("/inbox?tab=alert")
    assert lst.status_code == 200 and str(priv) not in _ids(lst) and str(pub) in _ids(lst)
    assert (await op.get(f"/inbox/{priv}")).status_code == 404
    assert (await op.send("POST", f"/inbox/{priv}/act", {})).status_code == 404
    assert await _status(db, priv) == "open"


async def test_attack_inbox_owner_without_pin_cannot_ack_console_pin_signal(owner_api: Api, db: AsyncSession) -> None:
    org = await _org(db)
    priv = await _signal(db, org, privacy="console_pin")
    assert str(priv) not in _ids(await owner_api.get("/inbox?tab=alert"))
    assert (await owner_api.get(f"/inbox/{priv}")).status_code == 423
    assert (await owner_api.send("POST", f"/inbox/{priv}/act", {})).status_code == 423
    assert await _status(db, priv) == "open"
    await verify_pin(owner_api)
    assert str(priv) in _ids(await owner_api.get("/inbox?tab=alert"))
    assert (await owner_api.send("POST", f"/inbox/{priv}/act", {})).status_code == 200


# ─── 2. nhóm «Chỉ Sếp» + bằng chứng raw_event ─────────────────────────────────

async def test_attack_hidden_group_raw_event_signal_hidden_everywhere(owner_api: Api, client: httpx.AsyncClient,
                                                                      db: AsyncSession) -> None:
    org = await _org(db)
    hidden = await _signal(db, org, view_scope="owner")
    op = await login_as(client, db, "operator")
    assert str(hidden) not in _ids(await op.get("/inbox?tab=alert"))
    assert (await op.get(f"/inbox/{hidden}")).status_code == 404
    assert (await op.send("POST", f"/inbox/{hidden}/act", {})).status_code == 404
    assert str(hidden) not in _ids(await op.get("/signals"))
    assert (await op.get(f"/signals/{hidden}")).status_code == 404
    assert (await op.get(f"/explain/alert/{hidden}")).status_code == 404
    assert await _status(db, hidden) == "open"
    assert str(hidden) in _ids(await owner_api.get("/signals"))           # Owner vẫn thấy


# ─── 3. GET /asks ─────────────────────────────────────────────────────────────

async def test_attack_asks_hide_console_pin_and_people_asks_without_pin(owner_api: Api, db: AsyncSession) -> None:
    org = await _org(db)
    priv = await _signal(db, org, privacy="console_pin", suggested="Gặp riêng nhân viên")
    pub = await _signal(db, org, suggested="Nhắn khách")
    a_prof = await asks.create(db, org, source="profile", ref_id=uuid.uuid4(), decision_kind="self", summary="Em đoán anh ưu tiên X")
    await db.commit()
    r = (await owner_api.get("/asks")).json()
    shown = {i["signal_id"] for i in r["items"]}
    assert str(priv) not in shown and str(pub) in shown
    assert str(a_prof.id) not in {i["id"] for i in r["items"]}
    assert r["hidden_pin_n"] == 2
    assert "Bí mật" not in str(r)
    await verify_pin(owner_api)
    r2 = (await owner_api.get("/asks")).json()
    assert str(priv) in {i["signal_id"] for i in r2["items"]} and r2["hidden_pin_n"] == 0


async def test_attack_owner_surfaces_do_not_leak_console_pin_titles(owner_api: Api, db: AsyncSession) -> None:
    """Mặt tiền Owner (dòng tin, màn Việc) không mang tiêu đề tín hiệu console_pin — chỉ ở Tình báo khi đã mở PIN."""
    from gh.owner import feed
    from gh.owner import service as osvc

    org = await _org(db)
    priv = await _signal(db, org, privacy="console_pin")
    pub = await _signal(db, org)
    f = await feed.feed(db, org, "all", 0, 50)
    ids = {str(i.get("id") or i.get("item_id")) for i in f["items"]}
    assert str(priv) not in ids and str(pub) in ids
    t = await osvc.tasks(db, org)
    assert "Bí mật nhân sự X" not in str(t) and "Khiếu nại công khai" in str(t)


async def test_attack_directory_priority_does_not_reveal_private_signal(owner_api: Api, db: AsyncSession) -> None:
    """Danh bạ người: mức ưu tiên tính từ hàng chung không được lộ "người này có tín hiệu console_pin P1"."""
    org = await _org(db)
    pid = (await db.execute(text("INSERT INTO core.persons (org_id, code, display_name) VALUES (:o, core.next_code('PER'), 'Chị Tư') RETURNING id"),
                            {"o": org})).scalar_one()
    sid = await _signal(db, org, privacy="console_pin")
    await db.execute(text("UPDATE biz.alerts SET subject_type = 'person', subject_id = :p WHERE id = :i"), {"p": pid, "i": sid})
    await db.commit()
    assert (await db.execute(text("SELECT priority FROM biz.alerts WHERE id = :i"), {"i": sid})).scalar_one() == "P1"
    rows = (await owner_api.get("/directory/people")).json()["items"]
    me = next(r for r in rows if r["id"] == str(pid))
    assert me["priority"] == "P3"
