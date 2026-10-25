# ruff: noqa: E501
"""v0.1.76 (gói T9) — API Toàn cảnh / Bối cảnh / tín hiệu của một mục Hồ sơ (`gh/biz/mem/overview.py`, route trong `view_routes.py`).
Riêng tư: `console_pin` chỉ Owner có PIN; vai khác chỉ thấy nhóm theo `view_scope`; mọi truy vấn lọc org (IDOR); vòng người chỉ Owner."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.mem import entities, facts
from tests.conftest import Api, verify_pin
from tests.mem_helpers import T, mk_org, mkfact
from tests.phase2 import org_id
from tests.test_intel_digest_rawtail_v0176 import _sig
from tests.test_owner_profile_support import link_owner_user, mk_group, mk_person
from tests.test_rbac_api import login_as


async def _ctx(db: AsyncSession, org: uuid.UUID, group: uuid.UUID, code: str) -> None:
    await db.execute(text("""INSERT INTO mem.group_contexts (org_id, group_id, context, is_primary, source, confidence)
                             VALUES (:o, :g, :c, true, 'owner', 1.0)"""), {"o": org, "g": group, "c": code})
    await db.commit()


async def _scope(db: AsyncSession, group: uuid.UUID, scope: str) -> None:
    await db.execute(text("UPDATE core.groups SET view_scope = :s WHERE id = :g"), {"s": scope, "g": group})
    await db.commit()


async def _put(db: AsyncSession, org: uuid.UUID, group: uuid.UUID | None, kind: str, ck: str, *, level: str = "amber",
               privacy: str = "chat_ok", status: str = "open", title: str | None = None, age_days: float = 0.0, **kw: Any) -> uuid.UUID:
    s = await _sig(db, org, kind, ck, privacy=privacy, **kw)
    await db.execute(text("""UPDATE biz.alerts SET group_id = :g, level = :l, status = :st, created_at = :t,
                             title = COALESCE(:ti, title) WHERE id = :i"""),
                     {"g": group, "l": level, "st": status, "t": datetime.now(UTC) - timedelta(days=age_days), "ti": title, "i": s.id})
    await db.commit()
    return s.id


async def _world(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """org của owner_api; nhóm kinh_doanh (owner-only), nhóm cong_viec (manager), nhóm khac (all_members)."""
    org = await org_id(db)
    w = await link_owner_user(db, org)
    g_owner = await mk_group(db, org, w.channel, "Nhóm chỉ Sếp")
    g_mgr = await mk_group(db, org, w.channel, "Nhóm quản lý")
    g_all = await mk_group(db, org, w.channel, "Nhóm mọi người")
    await _scope(db, g_owner, "owner")
    await _scope(db, g_mgr, "manager")
    await _scope(db, g_all, "all_members")
    await _ctx(db, org, g_owner, "kinh_doanh")
    await _ctx(db, org, g_mgr, "cong_viec")
    await _ctx(db, org, g_all, "khac")
    return org, g_owner, g_mgr, g_all


def _cell(body: dict[str, Any], code: str) -> dict[str, Any]:
    return next(c for c in body["contexts"] if c["code"] == code)


async def test_overview_grid_shape_counts_by_level_and_week(owner_api: Api, db: AsyncSession) -> None:
    org, g_owner, _, _ = await _world(db)
    await _put(db, org, g_owner, "viec_han", "a1", level="red")
    await _put(db, org, g_owner, "viec_han", "a2", level="amber")
    await _put(db, org, g_owner, "viec_han", "a3", level="grey", status="resolved")
    await _put(db, org, g_owner, "viec_han", "a4", level="amber", age_days=15)           # tuần cũ hơn
    r = await owner_api.get("/overview/map?weeks=4")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["breadcrumb"][0]["tier"] == "overview" and "asks_open_n" in b
    kd = _cell(b, "kinh_doanh")
    assert len(kd["weeks"]) == 4 and all({"week", "red", "amber", "grey", "off", "open_items", "heat_avg"} <= set(w) for w in kd["weeks"])
    last = kd["weeks"][-1]
    assert (last["red"], last["amber"], last["grey"]) == (1, 1, 1) and last["open_items"] == 2
    assert sum(w["amber"] for w in kd["weeks"]) == 2                                    # tuần cũ vẫn nằm trong 4 tuần (15 ngày)
    assert kd["hotspots"] and kd["hotspots"][0]["group_id"] == str(g_owner)
    assert len(_cell(b, "cong_viec")["weeks"]) == 4


async def test_overview_weeks_param_bounded(owner_api: Api, db: AsyncSession) -> None:
    await _world(db)
    assert (await owner_api.get("/overview/map?weeks=0")).status_code == 422
    assert (await owner_api.get("/overview/map?weeks=999")).status_code == 422


async def test_overview_console_pin_hidden_without_pin_shown_with_pin(owner_api: Api, db: AsyncSession) -> None:
    org, g_owner, _, _ = await _world(db)
    await _put(db, org, g_owner, "viec_han", "pub", level="amber")
    await _put(db, org, g_owner, "quan_he_nong_nguoi", "priv", level="red", privacy="console_pin", title="BÍ-MẬT nhân sự Hùng")
    r = await owner_api.get("/overview/map")
    b = r.json()
    last = _cell(b, "kinh_doanh")["weeks"][-1]
    assert (last["red"], last["amber"]) == (0, 1) and b["hidden_pin_n"] == 1
    assert "BÍ-MẬT" not in r.text
    await verify_pin(owner_api)
    b = (await owner_api.get("/overview/map")).json()
    last = _cell(b, "kinh_doanh")["weeks"][-1]
    assert (last["red"], last["amber"]) == (1, 1) and b["hidden_pin_n"] == 0


async def test_overview_other_roles_see_only_allowed_view_scope(owner_api: Api, client: Any, db: AsyncSession) -> None:
    org, g_owner, g_mgr, g_all = await _world(db)
    await _put(db, org, g_owner, "viec_han", "o1", level="red", title="BÍ-MẬT nhóm chỉ Sếp")
    await _put(db, org, g_mgr, "viec_han", "m1", level="amber", title="việc nhóm quản lý")
    await _put(db, org, g_all, "viec_han", "a1", level="amber", title="việc nhóm mọi người")
    await _put(db, org, g_all, "quan_he_nong_nguoi", "p1", level="red", privacy="console_pin", title="BÍ-MẬT nhân sự")
    mgr = await login_as(client, db, "manager")
    await verify_pin(mgr, "112233")
    b = (r := await mgr.get("/overview/map")).json()
    assert r.status_code == 200
    assert "BÍ-MẬT" not in r.text and "Nhóm chỉ Sếp" not in r.text
    assert _cell(b, "kinh_doanh")["weeks"][-1]["red"] == 0 and _cell(b, "kinh_doanh")["hotspots"] == []
    assert _cell(b, "cong_viec")["weeks"][-1]["amber"] == 1
    assert _cell(b, "khac")["weeks"][-1]["amber"] == 1 and _cell(b, "khac")["weeks"][-1]["red"] == 0     # console_pin: manager có PIN vẫn không thấy
    assert b["hidden_pin_n"] == 0                                                                     # không lộ cả số lượng
    op = await login_as(client, db, "operator")
    b = (await op.get("/overview/map")).json()
    assert _cell(b, "cong_viec")["weeks"][-1]["amber"] == 0 and _cell(b, "khac")["weeks"][-1]["amber"] == 1
    assert "Nhóm quản lý" not in str(b) and "Nhóm chỉ Sếp" not in str(b)


async def test_overview_org_isolation(owner_api: Api, db: AsyncSession) -> None:
    org, g_owner, _, _ = await _world(db)
    other = await mk_org(db, "Org khác")
    ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', 'z2') RETURNING id"), {"o": other})).scalar_one()
    await db.commit()
    g2 = await mk_group(db, other, ch, "Nhóm org khác BÍ-MẬT")
    await _scope(db, g2, "all_members")
    await _ctx(db, other, g2, "kinh_doanh")
    await _put(db, other, g2, "viec_han", "x1", level="red", title="BÍ-MẬT org khác")
    await _put(db, org, g_owner, "viec_han", "mine", level="amber")
    r = await owner_api.get("/overview/map")
    assert "BÍ-MẬT" not in r.text and _cell(r.json(), "kinh_doanh")["weeks"][-1]["red"] == 0
    r = await owner_api.get("/contexts/kinh_doanh")
    assert "BÍ-MẬT" not in r.text and [g["id"] for g in r.json()["groups"]] == [str(g_owner)]


async def test_overview_asks_open_and_people_asks_hidden_without_pin(owner_api: Api, db: AsyncSession) -> None:
    org = await org_id(db)
    for src in ("question", "profile"):
        await db.execute(text("""INSERT INTO ops.asks (org_id, code, source, ref_id, decision_kind, summary)
                                 VALUES (:o, :c, :s, core.uuid_v7(), 'self', 'BÍ-MẬT hỏi về người')"""), {"o": org, "c": "AB3CD" if src == "question" else "XY4ZK", "s": src})
    await db.commit()
    b = (r := await owner_api.get("/overview/map")).json()
    assert b["asks_open_n"] == 1 and b["asks_hidden_pin_n"] == 1 and "BÍ-MẬT" not in r.text
    await verify_pin(owner_api)
    b = (await owner_api.get("/overview/map")).json()
    assert b["asks_open_n"] == 2 and b["asks_hidden_pin_n"] == 0


async def test_overview_unconfirmed_groups_bucket(owner_api: Api, db: AsyncSession) -> None:
    org = await org_id(db)
    w = await link_owner_user(db, org)
    g = await mk_group(db, org, w.channel, "Nhóm chưa gán")
    await _scope(db, g, "all_members")
    await _put(db, org, g, "viec_han", "u1", level="amber")
    b = (await owner_api.get("/overview/map")).json()
    assert _cell(b, "chua_xac_nhan")["weeks"][-1]["amber"] == 1


async def test_context_detail_groups_signals_ring_and_scope(owner_api: Api, client: Any, db: AsyncSession) -> None:
    org, g_owner, g_mgr, _ = await _world(db)
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = 'zalo'"), {"o": org})).scalar_one()
    mai_p, _ = await mk_person(db, org, ch, "Chị Mai", "mai-1")
    tu_p, _ = await mk_person(db, org, ch, "Anh Tuấn", "tu-1")
    for p in (mai_p, tu_p):
        await db.execute(text("INSERT INTO core.group_members (group_id, person_id) VALUES (:g, :p)"), {"g": g_mgr, "p": p})
    owner = await entities.owner(db, org)
    mai = (await entities.ensure_person(db, org, mai_p)).id
    from gh.biz.mem import heat
    wk = heat.week_start(datetime.now(UTC))
    await db.execute(text("""INSERT INTO mem.relation_heat_weekly (org_id, entity_a, entity_b, week, msgs_ab, msgs_ba, heat, state)
                             VALUES (:o, :a, :b, :w, 5, 5, 55, 'warm')"""), {"o": org, "a": owner.id, "b": mai, "w": wk})
    await db.commit()
    await _put(db, org, g_mgr, "co_hoi_ket_noi", "op1", level="amber")
    await _put(db, org, g_mgr, "xung_dot_khieu_nai", "rk1", level="red", customer_of_owner=True)
    r = await owner_api.get("/contexts/cong_viec")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["breadcrumb"][-1]["tier"] == "context" and b["code"] == "cong_viec"
    assert [g["id"] for g in b["groups"]] == [str(g_mgr)]
    assert {s["kind"] for s in b["signals"]} == {"co_hoi_ket_noi", "xung_dot_khieu_nai"}
    assert b["opportunities"]["n"] == 1 and b["risks"]["n"] == 1
    assert [x["label"] for x in b["ring"]["ring1"]] == ["Chị Mai"] and b["ring"]["ring1"][0]["heat"] == 55.0
    assert b["ring"]["ring2_n"] == 1 and "Anh Tuấn" not in r.text                         # vòng 2 chỉ đếm, không lộ tên
    assert (await owner_api.get("/contexts/khong_co")).status_code == 404
    mgr = await login_as(client, db, "manager")
    rm = await mgr.get("/contexts/cong_viec")
    assert rm.status_code == 200 and rm.json()["ring"]["visible"] is False and "Chị Mai" not in rm.text
    assert (await mgr.get("/contexts/kinh_doanh")).json()["groups"] == []                 # nhóm chỉ Sếp: không thấy


async def test_context_ring_hr_context_needs_pin(owner_api: Api, db: AsyncSession) -> None:
    org = await org_id(db)
    w = await link_owner_user(db, org)
    g = await mk_group(db, org, w.channel, "Nhóm nhân viên")
    await _ctx(db, org, g, "nhan_vien")
    p, _ = await mk_person(db, org, w.channel, "Bạn Hùng", "h-1")
    await db.execute(text("INSERT INTO core.group_members (group_id, person_id) VALUES (:g, :p)"), {"g": g, "p": p})
    ent = (await entities.ensure_person(db, org, p)).id
    from gh.biz.mem import heat
    await db.execute(text("""INSERT INTO mem.relation_heat_weekly (org_id, entity_a, entity_b, week, heat, state)
                             VALUES (:o, :a, :b, :w, 40, 'warm')"""), {"o": org, "a": w.owner, "b": ent, "w": heat.week_start(datetime.now(UTC))})
    await db.commit()
    r = await owner_api.get("/contexts/nhan_vien")
    assert r.json()["ring"]["visible"] is False and r.json()["ring"]["hidden_pin"] is True and "Bạn Hùng" not in r.text
    await verify_pin(owner_api)
    r = await owner_api.get("/contexts/nhan_vien")
    assert [x["label"] for x in r.json()["ring"]["ring1"]] == ["Bạn Hùng"]


async def test_context_detail_console_pin_signals_gated(owner_api: Api, db: AsyncSession) -> None:
    org, g_owner, _, _ = await _world(db)
    await _put(db, org, g_owner, "quan_he_nong_nguoi", "priv", level="red", privacy="console_pin", title="BÍ-MẬT nhân sự")
    r = await owner_api.get("/contexts/kinh_doanh")
    assert r.json()["signals"] == [] and r.json()["hidden_pin_n"] == 1 and "BÍ-MẬT" not in r.text
    await verify_pin(owner_api)
    r = await owner_api.get("/contexts/kinh_doanh")
    assert len(r.json()["signals"]) == 1 and r.json()["hidden_pin_n"] == 0


async def test_profile_item_signals_two_way_link_and_privacy(owner_api: Api, client: Any, db: AsyncSession) -> None:
    org = await org_id(db)
    w = await link_owner_user(db, org)
    f = await facts.assert_fact(db, mkfact(org, w.owner, "mục tiêu: mở chi nhánh", T(1), predicate="owner_goal", cls="owner"))
    other_f = await facts.assert_fact(db, mkfact(org, w.owner, "mục tiêu khác", T(1), predicate="owner_goal", cls="owner"))
    await db.commit()
    s_pub = await _put(db, org, None, "co_hoi_ket_noi", "l1", level="amber", title="khớp mục tiêu")
    s_priv = await _put(db, org, None, "quan_he_nong_nguoi", "l2", level="red", privacy="console_pin", title="BÍ-MẬT riêng")
    s_other = await _put(db, org, None, "viec_han", "l3", level="amber", title="không liên quan")
    for sid, fid in ((s_pub, f.id), (s_priv, f.id), (s_other, other_f.id)):
        await db.execute(text("UPDATE biz.alerts SET owner_fact_ids = ARRAY[CAST(:f AS uuid)] WHERE id = :i"), {"f": fid, "i": sid})
    await db.commit()
    r = await owner_api.get(f"/me/profile/items/{f.id}/signals")
    assert r.status_code == 200, r.text
    b = r.json()
    assert [s["id"] for s in b["items"]] == [str(s_pub)] and b["hidden_pin_n"] == 1 and "BÍ-MẬT" not in r.text
    assert b["breadcrumb"][0]["tier"] == "me"
    await verify_pin(owner_api)
    ids = {s["id"] for s in (await owner_api.get(f"/me/profile/items/{f.id}/signals")).json()["items"]}
    assert ids == {str(s_pub), str(s_priv)}
    mgr = await login_as(client, db, "manager")
    assert (await mgr.get(f"/me/profile/items/{f.id}/signals")).status_code == 403


async def test_profile_item_signals_idor_other_org_fact_404(owner_api: Api, db: AsyncSession) -> None:
    await link_owner_user(db, await org_id(db))
    other = await mk_org(db, "Org khác")
    ent = (await entities.create(db, other, "person", "Người org khác")).id
    f = await facts.assert_fact(db, mkfact(other, ent, "bí mật", T(1), predicate="owner_goal", cls="owner"))
    await db.commit()
    assert (await owner_api.get(f"/me/profile/items/{f.id}/signals")).status_code == 404
    assert (await owner_api.get(f"/me/profile/items/{uuid.uuid4()}/signals")).status_code == 404
