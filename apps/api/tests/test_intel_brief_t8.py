# ruff: noqa: E501
"""T8 — `/profile/{id}/brief` (W6) và `/graph/person/{id}`: tóm tắt đọc `mem.facts` + `entity_summaries`, không điểm đánh giá ở summary,
`full` cần Owner + PIN và không có đánh giá ở gia đình/bạn bè, lọc §4.5 (hr / lừa đảo / nhạy cảm)."""

import uuid
from typing import Any

from sqlalchemy import text

from gh.biz.mem import consolidate, entities, facts
from tests.conftest import Api, verify_pin
from tests.mem_helpers import T, mkfact
from tests.phase2 import org_id
from tests.test_owner_profile_support import link_owner_user, mk_person
from tests.test_rbac_api import login_as


async def _setup(db: Any) -> tuple[Any, uuid.UUID, uuid.UUID]:
    w = await link_owner_user(db, await org_id(db))
    pid, _ = await mk_person(db, w.org, w.channel, "Chị Lan", "lan1")
    ent = (await entities.ensure_person(db, w.org, pid)).id
    await facts.assert_fact(db, mkfact(w.org, ent, {"text": "gửi mẫu xi măng"}, T(2), predicate="promised_to"))
    await facts.assert_fact(db, mkfact(w.org, ent, {"text": "mổ ruột thừa"}, T(3), predicate="health_event"))
    await facts.assert_fact(db, mkfact(w.org, ent, "sẽ nghỉ việc", T(4), predicate="said_will_leave"))
    await db.execute(text("""INSERT INTO clean.current_scores (subject_type, subject_id, dimension, value, trend, snapshot_id, updated_at)
                             VALUES ('person', :p, 'heat', 77, 'up', :s, now())"""), {"p": pid, "s": uuid.uuid4()})
    await consolidate.rebuild_summaries(db, w.org)
    await db.commit()
    return w, pid, ent


async def test_brief_summary_has_no_scores_and_filters_private_facts(owner_api: Api, db: Any) -> None:
    w, pid, _ = await _setup(db)
    r = await owner_api.get(f"/profile/{pid}/brief")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["tier"] == "summary" and "scores" not in b
    assert any("xi măng" in f["text"] for f in b["facts"]) and all(f["fact_ids"] and f["evidence"] for f in b["facts"])
    for s in ("mổ ruột thừa", "nghỉ việc"):
        assert s not in r.text, s
    await verify_pin(owner_api)
    r = await owner_api.get(f"/profile/{pid}/brief")
    assert "mổ ruột thừa" in r.text and "nghỉ việc" not in r.text            # hr không bao giờ, ngay cả có PIN
    _ = w


async def test_brief_full_requires_owner_pin_and_never_scores_family(owner_api: Api, db: Any, client: Any) -> None:
    w, pid, ent = await _setup(db)
    assert (await owner_api.get(f"/profile/{pid}/brief?tier=full")).status_code == 423
    op = await login_as(client, db, "operator")
    assert (await op.get(f"/profile/{pid}/brief?tier=full")).status_code in (403, 404)
    await verify_pin(owner_api)
    b = (await owner_api.get(f"/profile/{pid}/brief?tier=full")).json()
    assert b["tier"] == "full" and [s["dimension"] for s in b["scores"]] == ["heat"]
    mai = (await entities.ensure_person(db, w.org, (await mk_person(db, w.org, w.channel, "Mẹ", "me1"))[0])).id
    await facts.assert_fact(db, mkfact(w.org, ent, None, T(3), predicate="family_of", object_entity=mai))
    await db.commit()
    b = (await owner_api.get(f"/profile/{pid}/brief?tier=full")).json()
    assert b["scores"] == [] and b["scores_withheld"] == "gia_dinh_ban_be"


async def test_brief_non_owner_gets_no_memory_facts(owner_api: Api, db: Any, client: Any) -> None:
    _, pid, _ = await _setup(db)
    op = await login_as(client, db, "operator")
    r = await op.get(f"/profile/{pid}/brief")
    assert r.status_code == 200, r.text
    assert r.json()["facts"] == [] and r.json()["summary"] is None and "xi măng" not in r.text


async def test_graph_person_endpoint_scoped(owner_api: Api, db: Any) -> None:
    _, pid, _ = await _setup(db)
    r = await owner_api.get(f"/graph/person/{pid}")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["person_id"] == str(pid) and b["top_people"] == [] and b["total_people"] == 0
    assert (await owner_api.get(f"/graph/person/{uuid.uuid4()}")).status_code == 404
