"""Bản đồ quan hệ (`graph`, PLAN §3.5): danh sách + bộ lọc mạnh, Người↔Người, Nhóm↔Nhóm, Luồng chủ đề, vị trí
node đã lưu. Test theo đúng mục PLAN liệt kê: trọng số cạnh, cầu nối, tải quan hệ (giới hạn ≤ 200 node), lạnh
> 30 ngày."""

import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from gh.biz.graph import jobs as graph_jobs
from gh.db import sessionmaker
from tests.conftest import Api
from tests.phase2 import listen, msg, org_id, put
from tests.test_rbac_api import login_as


async def _person_of(db, raw_id) -> uuid.UUID:  # type: ignore[no-untyped-def]
    return (await db.execute(text("""SELECT pi.person_id FROM raw.events e
                                     JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                     WHERE e.id = :r"""), {"r": raw_id})).scalar_one()


async def _send(sm, org, group_ext: str, sender: str, name: str, body: str, *,  # type: ignore[no-untyped-def]
                occurred_at: datetime | None = None):
    p = msg(body, group=group_ext, sender=sender, name=name)
    if occurred_at is not None:
        p["occurred_at"] = occurred_at.isoformat()
    [raw_id] = await put(sm, org, p)
    return raw_id


async def assign_scope(db, email: str, subject_type: str, subject_id) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("""INSERT INTO core.assignments (org_id, user_id, subject_type, subject_id)
                             SELECT org_id, id, :t, :s FROM core.users WHERE email = :e"""),
                     {"t": subject_type, "s": subject_id, "e": email})
    await db.commit()


@pytest.fixture
async def world(app, db, redis, owner_api):  # type: ignore[no-untyped-def]
    """Tổ chức đã khởi tạo, 5 nhóm:
    - g1 (A ×2, B ×1 cùng ngày) → cạnh `interacts` A↔B, trọng số = min(2,1) = 1.
    - g2 (A, C cùng ngày) → g1/g2 chung thành viên A → cạnh `shares_members` (trọng số 1/2 = số chung / nhỏ
      hơn giữa hai tổng: g1 có {A,B}=2, g2 có {A,C}=2).
    - gcold (E, F cùng ngày, cách đây 40 ngày, không gì gần đây) → cạnh `interacts` lạnh (> 30 ngày).
    - gb1 (P, G), gb2 (P, H) — P là thành viên duy nhất chung của gb1/gb2 → P là **cầu nối**.
    A còn được gán `admin` ở g1 → cạnh `owns` A→g1.
    Một đơn vị ý nghĩa ở g1 gắn `entities.product` → `topic` của cạnh A↔B."""
    org = await org_id(db)
    sm = sessionmaker()

    g1 = await listen(db, org, "g1")
    g2 = await listen(db, org, "g2")
    gcold = await listen(db, org, "gcold")
    gb1 = await listen(db, org, "gb1")
    gb2 = await listen(db, org, "gb2")

    raw_a1 = await _send(sm, org, "g1", "a", "Chị A", "Cần báo giá thép")
    raw_a2 = await _send(sm, org, "g1", "a", "Chị A", "3 container nhé")
    raw_b1 = await _send(sm, org, "g1", "b", "Anh B", "Cho hỏi giá với")
    raw_c1 = await _send(sm, org, "g2", "c", "Chị C", "Chào shop")
    raw_a3 = await _send(sm, org, "g2", "a", "Chị A", "Ghé nhóm 2 luôn")

    old = datetime.now(UTC) - timedelta(days=40)
    raw_e1 = await _send(sm, org, "gcold", "e", "Anh E", "Hỏi giá cũ", occurred_at=old)
    raw_f1 = await _send(sm, org, "gcold", "f", "Chị F", "Đồng ý", occurred_at=old)

    raw_p1 = await _send(sm, org, "gb1", "p", "Anh P", "Xin chào nhóm 1")
    raw_g1 = await _send(sm, org, "gb1", "gg", "Chị G", "Chào")
    raw_p2 = await _send(sm, org, "gb2", "p", "Anh P", "Xin chào nhóm 2")
    raw_h1 = await _send(sm, org, "gb2", "hh", "Anh H", "Chào")

    await db.commit()
    pa = await _person_of(db, raw_a1)
    pb = await _person_of(db, raw_b1)
    pc = await _person_of(db, raw_c1)
    pe = await _person_of(db, raw_e1)
    pf = await _person_of(db, raw_f1)
    pp = await _person_of(db, raw_p1)
    pg_ = await _person_of(db, raw_g1)
    ph = await _person_of(db, raw_h1)
    assert await _person_of(db, raw_a2) == pa
    assert await _person_of(db, raw_a3) == pa
    assert await _person_of(db, raw_p2) == pp

    await db.execute(text("UPDATE core.group_members SET role = 'admin' WHERE group_id = :g AND person_id = :p"),
                     {"g": g1, "p": pa})
    await db.execute(text("""INSERT INTO clean.meaning_units (org_id, observed_at, group_id, person_id, event_type,
                                                               conclusion, entities, confidence, run_id)
        VALUES (:o, now(), :g, :p, 'AskedPrice', 'Hỏi giá thép', CAST(:e AS jsonb), 0.9, core.uuid_v7())"""),
        {"o": org, "g": g1, "p": pa, "e": '{"product": "Thép cuộn"}'})
    await db.commit()

    counts = await graph_jobs.recompute_org(db, org)
    await db.commit()

    return {"org": org, "g1": g1, "g2": g2, "gcold": gcold, "gb1": gb1, "gb2": gb2,
            "pa": pa, "pb": pb, "pc": pc, "pe": pe, "pf": pf, "pp": pp, "pg": pg_, "ph": ph, "counts": counts}


def _edge(body: dict, a: uuid.UUID, b: uuid.UUID) -> dict | None:  # type: ignore[no-untyped-def]
    want = {str(a), str(b)}
    for e in body["edges"]:
        if {e["from"], e["to"]} == want:
            return e
    return None


# ═══ trọng số cạnh (Người↔Người) ═════════════════════════════════════════════

async def test_interacts_edge_weight_and_topic(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    body = (await owner_api.get("/graph/people")).json()
    e = _edge(body, world["pa"], world["pb"])
    assert e is not None, body
    assert e["weight"] == 1.0                          # min(2 tin của A, 1 tin của B) trong ngày chung
    assert e["interactions"] == 1                       # 1 ngày cùng hoạt động
    assert e["state"] == "active"
    assert e["topic"] == "Thép cuộn"
    ids = {n["id"] for n in body["nodes"]}
    assert {str(world["pa"]), str(world["pb"])} <= ids


async def test_recompute_counts_and_idempotent(world, db) -> None:  # type: ignore[no-untyped-def]
    assert world["counts"]["interacts"] > 0
    assert world["counts"]["shares_members"] > 0
    assert world["counts"]["bridges"] > 0
    again = await graph_jobs.recompute_org(db, world["org"])
    await db.commit()
    assert again == world["counts"]                     # chạy lại cho cùng dữ liệu → cùng kết quả (idempotent)
    n = (await db.execute(text("SELECT count(*) FROM clean.relationships WHERE org_id = :o"),
                          {"o": world["org"]})).scalar_one()
    assert n == sum(world["counts"].values())            # không để lại cạnh rác từ lượt trước


# ═══ Nhóm↔Nhóm: thành viên chung ═════════════════════════════════════════════

async def test_shares_members_edge(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    body = (await owner_api.get("/graph/groups")).json()
    e = _edge(body, world["g1"], world["g2"])
    assert e is not None, body
    assert e["interactions"] == 1                       # A là thành viên chung duy nhất
    assert e["weight"] == 0.5                            # 1 / nhỏ hơn giữa (|g1|=2, |g2|=2)


# ═══ cầu nối ══════════════════════════════════════════════════════════════════

async def test_bridge_detection(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    rows = (await db.execute(text("""SELECT to_id, weight FROM clean.relationships
                                     WHERE org_id = :o AND kind = 'bridges' AND from_id = :p"""),
                             {"o": world["org"], "p": world["pp"]})).all()
    assert {str(r.to_id) for r in rows} == {str(world["gb1"]), str(world["gb2"])}
    assert all(r.weight == 1.0 for r in rows)             # đúng 1 cặp nhóm bị bắc cầu

    # người không bắc cầu (chỉ ở một nhóm) không có cạnh bridges
    none_rows = (await db.execute(text("""SELECT 1 FROM clean.relationships
                                          WHERE org_id = :o AND kind = 'bridges' AND from_id = :p"""),
                                  {"o": world["org"], "p": world["pg"]})).all()
    assert none_rows == []

    listing = (await owner_api.get("/graph/list")).json()
    by_id = {i["id"]: i for i in listing["items"]}
    assert by_id[str(world["pp"])]["bridge_score"] == 1
    assert by_id[str(world["pg"])]["bridge_score"] == 0


# ═══ lạnh > 30 ngày ═══════════════════════════════════════════════════════════

async def test_cold_after_30_days(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    body = (await owner_api.get("/graph/people")).json()
    e = _edge(body, world["pe"], world["pf"])
    assert e is not None, body
    assert e["state"] == "cold"

    listing = (await owner_api.get("/graph/list?state=cold")).json()
    ids = {i["id"] for i in listing["items"]}
    assert {str(world["pe"]), str(world["pf"])} <= ids
    active = (await owner_api.get("/graph/list?state=active")).json()
    assert str(world["pe"]) not in {i["id"] for i in active["items"]}
    assert str(world["pa"]) in {i["id"] for i in active["items"]}


# ═══ ai đang nắm (owns) ═══════════════════════════════════════════════════════

async def test_owns_edge_for_group_admin(world, db) -> None:  # type: ignore[no-untyped-def]
    row = (await db.execute(text("""SELECT weight, interactions FROM clean.relationships
                                    WHERE org_id = :o AND kind = 'owns' AND from_id = :p AND to_id = :g"""),
                            {"o": world["org"], "p": world["pa"], "g": world["g1"]})).one()
    assert row.interactions == 2                          # A gửi 2 tin ở g1
    assert row.weight == 2.0


# ═══ danh sách + bộ lọc ═══════════════════════════════════════════════════════

async def test_list_filters(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    everyone = (await owner_api.get("/graph/list")).json()
    assert everyone["total"] >= 8

    by_type = (await owner_api.get("/graph/list?type=unknown")).json()
    assert by_type["total"] >= 1

    high_degree = {i["id"]: i for i in everyone["items"]}[str(world["pa"])]
    assert high_degree["degree"] > 0 and high_degree["total_weight"] > 0     # "tải quan hệ" của A > 0


async def test_list_scope_by_role(world, owner_api: Api, client, db) -> None:  # type: ignore[no-untyped-def]
    staff = await login_as(client, db, "agent_staff")
    empty = (await staff.get("/graph/list")).json()
    assert empty["total"] == 0
    await assign_scope(db, "agent_staff@example.vn", "person", world["pa"])
    scoped = (await staff.get("/graph/list")).json()
    assert {i["id"] for i in scoped["items"]} == {str(world["pa"])}

    # đồ thị Người↔Người ở tầng cạnh: chỉ trả cạnh có CẢ HAI đầu trong phạm vi — A trong phạm vi nhưng B thì
    # không, nên cạnh A↔B không hiện dù A hiện được.
    graph = (await staff.get("/graph/people")).json()
    assert _edge(graph, world["pa"], world["pb"]) is None


# ═══ Luồng chủ đề ═════════════════════════════════════════════════════════════

async def test_topic_threads(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    topics = (await owner_api.get("/graph/topics")).json()
    [t] = [x for x in topics["items"] if x["topic"] == "Thép cuộn"]
    assert t["edges"] >= 1 and t["people"] == 2 and t["state"] == "active"

    thread = (await owner_api.get(f"/graph/topics/{urllib.parse.quote('Thép cuộn')}")).json()
    assert _edge(thread, world["pa"], world["pb"]) is not None


# ═══ tải quan hệ: giới hạn ≤ 200 node ═════════════════════════════════════════

async def test_node_cap_returns_top_weight_subset(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    """202 người, 101 cạnh rời nhau (mỗi cạnh 2 người riêng, trọng số giảm dần) → top 100 cạnh nặng nhất vừa
    khít 200 node, cạnh nhẹ nhất (thứ 101) bị bỏ và báo `truncated`."""
    org = world["org"]
    ids = []
    for i in range(202):
        pid = (await db.execute(text("""INSERT INTO core.persons (org_id, code, display_name)
                                        VALUES (:o, :c, :n) RETURNING id"""),
                                {"o": org, "c": f"STRESS-{i:04d}", "n": f"Người {i}"})).scalar_one()
        ids.append(pid)
    for i in range(101):
        a, b = ids[2 * i], ids[2 * i + 1]
        await db.execute(text("""INSERT INTO clean.relationships (org_id, from_type, from_id, to_type, to_id,
                                                                   kind, window_days, weight, interactions,
                                                                   last_at, state)
            VALUES (:o, 'person', :a, 'person', :b, 'interacts', 90, :w, 1, now(), 'active')"""),
                         {"o": org, "a": a, "b": b, "w": 101 - i})
    await db.commit()

    body = (await owner_api.get("/graph/people")).json()
    assert body["truncated"] is True
    assert body["total_edges"] >= 101
    assert len(body["nodes"]) == 200
    assert len(body["edges"]) == 100
    assert "hint" in body
    weights = [e["weight"] for e in body["edges"]]
    assert min(weights) >= 2                              # cạnh nhẹ nhất (trọng số 1) bị loại, không cắt ngẫu nhiên


# ═══ vị trí node đã lưu ═══════════════════════════════════════════════════════

async def test_layout_save_and_load(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    empty = (await owner_api.get("/graph/layout/people")).json()
    assert empty["positions"] == {}

    pid = str(world["pa"])
    r = await owner_api.send("PUT", "/graph/layout/people",
                             {"positions": {pid: {"x": 12.5, "y": -4.0}}})
    assert r.status_code == 200, r.text

    got = (await owner_api.get("/graph/layout/people")).json()
    assert got["positions"][pid] == {"x": 12.5, "y": -4.0}

    # ghi đè (autosave) không lỗi 409 như /views cùng tên
    r2 = await owner_api.send("PUT", "/graph/layout/people",
                              {"positions": {pid: {"x": 1.0, "y": 2.0}}})
    assert r2.status_code == 200
    got2 = (await owner_api.get("/graph/layout/people")).json()
    assert got2["positions"][pid] == {"x": 1.0, "y": 2.0}

    # vị trí của chế độ khác tách biệt
    other = (await owner_api.get("/graph/layout/groups")).json()
    assert other["positions"] == {}


# ═══ dựng lại đồ thị (tay) ════════════════════════════════════════════════════

async def test_manual_recompute_endpoint(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("DELETE FROM clean.relationships WHERE org_id = :o"), {"o": world["org"]})
    await db.commit()
    empty = (await owner_api.get("/graph/people")).json()
    assert empty["edges"] == []

    r = await owner_api.send("POST", "/graph/recompute", {})
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["interacts"] > 0

    rebuilt = (await owner_api.get("/graph/people")).json()
    assert _edge(rebuilt, world["pa"], world["pb"]) is not None

    log = (await db.execute(text("""SELECT action FROM ops.action_log
                                    WHERE action LIKE 'graph.%' ORDER BY at"""))).scalars().all()
    assert "graph.recomputed" in log
