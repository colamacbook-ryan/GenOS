"""v0.1.40 (F-16): Bản đồ quan hệ — mọi truy vấn raw.events nằm trong cửa sổ `window_days`, mỗi kind ghi bằng MỘT câu
upsert (INSERT … SELECT … ON CONFLICT), `state` tính trong SQL khớp `edge_state()` ở biên 30/31 ngày,
cạnh rác bị dọn."""

import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, text

from gh.biz.graph import jobs as graph_jobs
from gh.db import get_engine, sessionmaker
from tests.phase2 import listen, org_id
from tests.test_p3_graph import _person_of, _send


async def _edge(db: Any, org: uuid.UUID, kind: str, a: uuid.UUID, b: uuid.UUID) -> Any:
    return (await db.execute(text("""
        SELECT weight, interactions, last_at, state FROM clean.relationships
        WHERE org_id = :o AND kind = :k AND ((from_id = :a AND to_id = :b) OR (from_id = :b AND to_id = :a))"""),
        {"o": org, "k": kind, "a": a, "b": b})).one_or_none()


@pytest.fixture
async def gworld(app, db, redis, owner_api):  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sm = sessionmaker()
    now = datetime.now(UTC)
    old = now - timedelta(days=120)
    groups = {g: await listen(db, org, g) for g in ("gold", "gnew", "gs1", "gs2", "gs3", "gs4", "gi30", "gi31")}
    # owns: A admin của gold (chỉ có tin 120 ngày trước) và gnew (2 tin trong cửa sổ)
    ra = await _send(sm, org, "gold", "a", "Chị A", "Tin cũ", occurred_at=old)
    await _send(sm, org, "gold", "a", "Chị A", "Tin cũ 2", occurred_at=old)
    await _send(sm, org, "gnew", "a", "Chị A", "Tin mới")
    await _send(sm, org, "gnew", "a", "Chị A", "Tin mới 2")
    # shares_members: S chung gs1/gs2, chỉ nhắn 120 ngày trước; T chung gs3/gs4, nhắn hôm nay
    rs = await _send(sm, org, "gs1", "s", "Anh S", "Cũ", occurred_at=old)
    await _send(sm, org, "gs2", "s", "Anh S", "Cũ", occurred_at=old)
    rt = await _send(sm, org, "gs3", "t", "Anh T", "Mới")
    await _send(sm, org, "gs4", "t", "Anh T", "Mới")
    # interacts ở biên lạnh: 30 ngày + 1 giờ (còn active) và 31 ngày + 1 giờ (cold)
    t30 = now - timedelta(days=30, hours=1)
    t31 = now - timedelta(days=31, hours=1)
    ru = await _send(sm, org, "gi30", "u", "Anh U", "x", occurred_at=t30)
    rv = await _send(sm, org, "gi30", "v", "Chị V", "y", occurred_at=t30)
    rw = await _send(sm, org, "gi31", "w", "Anh W", "x", occurred_at=t31)
    rz = await _send(sm, org, "gi31", "z", "Chị Z", "y", occurred_at=t31)
    await db.commit()
    pa = await _person_of(db, ra)
    await db.execute(text("UPDATE core.group_members SET role = 'admin' WHERE person_id = :p"), {"p": pa})
    await db.commit()
    return {"org": org, **groups, "pa": pa, "ps": await _person_of(db, rs), "pt": await _person_of(db, rt),
            "pu": await _person_of(db, ru), "pv": await _person_of(db, rv), "pw": await _person_of(db, rw),
            "pz": await _person_of(db, rz)}


async def test_owns_and_shares_use_window(gworld, db) -> None:  # type: ignore[no-untyped-def]
    w = gworld
    await graph_jobs.recompute_org(db, w["org"])
    await db.commit()
    owns_old = await _edge(db, w["org"], "owns", w["pa"], w["gold"])
    assert owns_old is not None
    assert owns_old.interactions == 0 and owns_old.last_at is None and owns_old.state == "cold"
    assert float(owns_old.weight) == 1.0
    owns_new = await _edge(db, w["org"], "owns", w["pa"], w["gnew"])
    assert owns_new.interactions == 2 and owns_new.last_at is not None and owns_new.state == "active"
    assert float(owns_new.weight) == 2.0
    sh_old = await _edge(db, w["org"], "shares_members", w["gs1"], w["gs2"])
    assert sh_old is not None and sh_old.last_at is None and sh_old.state == "cold"
    sh_new = await _edge(db, w["org"], "shares_members", w["gs3"], w["gs4"])
    assert sh_new is not None and sh_new.last_at is not None and sh_new.state == "active"


async def test_state_matches_edge_state_at_boundary(gworld, db) -> None:  # type: ignore[no-untyped-def]
    w = gworld
    await graph_jobs.recompute_org(db, w["org"])
    await db.commit()
    e30 = await _edge(db, w["org"], "interacts", w["pu"], w["pv"])
    e31 = await _edge(db, w["org"], "interacts", w["pw"], w["pz"])
    assert e30.state == "active" == graph_jobs.edge_state(e30.last_at)
    assert e31.state == "cold" == graph_jobs.edge_state(e31.last_at)
    rows = (await db.execute(text("SELECT last_at, state FROM clean.relationships WHERE org_id = :o"),
                             {"o": w["org"]})).all()
    assert rows and all(r.state == graph_jobs.edge_state(r.last_at) for r in rows)


async def test_one_insert_per_kind_and_stale_edges_removed(gworld, db) -> None:  # type: ignore[no-untyped-def]
    w = gworld
    stale = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO clean.relationships (org_id, from_type, from_id, to_type, to_id, kind, window_days, weight,
                                         computed_at)
        VALUES (:o, 'person', :a, 'person', :b, 'interacts', 90, 1, now() - interval '1 day')"""),
        {"o": w["org"], "a": stale, "b": uuid.uuid4()})
    await db.commit()

    seen: Counter[str] = Counter()

    def on_exec(conn: Any, cursor: Any, statement: str, *a: Any) -> None:
        if "INSERT INTO clean.relationships" in statement:
            for k in graph_jobs.MANAGED_KINDS:
                if f"'{k}'" in statement:
                    seen[k] += 1

    sync_engine = get_engine().sync_engine
    event.listen(sync_engine, "before_cursor_execute", on_exec)
    try:
        counts = await graph_jobs.recompute_org(db, w["org"])
        await db.commit()
    finally:
        event.remove(sync_engine, "before_cursor_execute", on_exec)
    assert seen["interacts"] == 1 and seen["shares_members"] == 1 and seen["owns"] == 1
    assert seen["bridges"] == 1
    assert counts["interacts"] >= 2 and counts["owns"] == 2 and counts["shares_members"] >= 2
    left = (await db.execute(text("SELECT count(*) FROM clean.relationships WHERE from_id = :a"),
                             {"a": stale})).scalar_one()
    assert left == 0
    total = (await db.execute(text("SELECT count(*) FROM clean.relationships WHERE org_id = :o"),
                              {"o": w["org"]})).scalar_one()
    assert total == sum(counts.values())
