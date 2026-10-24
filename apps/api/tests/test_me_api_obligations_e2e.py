# ruff: noqa: E501
"""Review opus L4 (mục 1, 10): hợp đồng lời hứa/hạn đầu-cuối — unit có hạn → runner ghi fact có due_at/to_entity_id → /me/obligations thấy;
vị từ owner_* chỉ nhận khi người nói là Sếp."""

from sqlalchemy import text

from gh.biz.mem import entities, facts
from gh.biz.mem.profile_read import value_text
from gh.db import sessionmaker
from gh.refinery.brain_contract import Deadline, FactObject, FactProposed, FactSubject
from tests.phase2 import listen, msg, org_id, put
from tests.refinery_group_helpers import Analyzer, enable_groups, find_msg, out_with, unit_for
from tests.test_refinery_group_v0176 import person_of, refinery


def test_review1_value_key_uses_text_only_for_obligation_dicts() -> None:
    a = facts.value_key({"text": "Giao 3 container", "due_at": "2026-10-25T00:00:00+00:00", "at": None, "to_entity_id": None})
    b = facts.value_key({"text": "giao 3 container", "due_at": "2026-11-30T00:00:00+00:00", "at": None, "to_entity_id": "x"})
    assert a == b == facts.value_key("giao 3 container")                       # cùng nội dung, khác hạn ⇒ cùng khoá
    assert a != facts.value_key({"text": "giao 4 container", "due_at": None})
    assert facts.value_key({"text": "x", "other": 1}) != facts.value_key("x")   # dict lạ vẫn băm nguyên dict


async def _scene(owner_api, db, redis):  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org = await org_id(db)
    await listen(db, org, "g1")
    await enable_groups(db, org)
    await put(sm, org, msg("Em hứa ngày mai giao 3 container cho chị Lan nhé", sender="u2", name="Anh Cơ"),
              msg("Chị sẽ chuyển tiền cọc ngày mai nhé anh", sender="u1", name="Chị Lan"))
    owner_p, lan_p = await person_of(db, "u2"), await person_of(db, "u1")
    uid = (await db.execute(text("SELECT id FROM core.users WHERE org_id = :o"), {"o": org})).scalar_one()
    await db.execute(text("UPDATE core.users SET person_id = :p WHERE id = :u"), {"p": owner_p, "u": uid})
    await db.commit()
    return sm, org, owner_p, lan_p


async def test_review1_unit_deadline_flows_into_fact_and_obligations_api(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm, org, owner_p, lan_p = await _scene(owner_api, db, redis)

    def build(w):  # type: ignore[no-untyped-def]
        e1, e2 = find_msg(w, "Em hứa"), find_msg(w, "Chị sẽ chuyển")
        u1 = unit_for(w, "Em hứa", unit_ref="u1", summary="Sếp hứa giao").model_copy(update={"deadline": Deadline(text="ngày mai", anchor_message_id=e1.message_id)})
        u2 = unit_for(w, "Chị sẽ chuyển", unit_ref="u2", summary="Lan hứa cọc").model_copy(update={"deadline": Deadline(text="ngày mai", anchor_message_id=e2.message_id)})
        f1 = FactProposed(unit_ref="u1", subject=FactSubject(surface="Anh Cơ", person_ref=f"u_{owner_p}"), predicate="promised_to",
                          object=FactObject(value="giao 3 container", person_ref=f"u_{lan_p}"), quote_refs=[e1.message_id])
        f2 = FactProposed(unit_ref="u2", subject=FactSubject(surface="Chị Lan", person_ref=f"u_{lan_p}"), predicate="promised_to",
                          object=FactObject(value="chuyển tiền cọc", person_ref=f"u_{owner_p}"), quote_refs=[e2.message_id])
        return out_with(w, u1, u2, facts=[f1, f2])

    await refinery(sm, redis, Analyzer(build)).run(org, "manual")
    rows = (await db.execute(text("SELECT object_value, subject_id FROM mem.facts WHERE predicate = 'promised_to' ORDER BY recorded_at"))).all()
    assert len(rows) == 2
    lan_e, owner_e = (await entities.ensure_person(db, org, lan_p)).id, (await entities.ensure_person(db, org, owner_p)).id
    by_text = {value_text(r.object_value): r.object_value for r in rows}
    mine, owed = by_text["giao 3 container"], by_text["chuyển tiền cọc"]
    assert set(mine) == {"text", "due_at", "at", "to_entity_id"} and mine["due_at"] is not None and mine["to_entity_id"] == str(lan_e)
    assert owed["to_entity_id"] == str(owner_e) and owed["due_at"] is not None
    r = await owner_api.get("/me/obligations")
    assert r.status_code == 200, r.text
    b = r.json()
    assert [x["text"] for x in b["mine"]] == ["giao 3 container"] and b["mine"][0]["due_at"] is not None
    assert [x["text"] for x in b["owed_to_me"]] == ["chuyển tiền cọc"] and b["owed_to_me"][0]["due_at"] is not None


async def test_review1_event_at_goes_to_scheduled_event_and_plain_predicates_keep_string(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm, org, owner_p, lan_p = await _scene(owner_api, db, redis)

    def build(w):  # type: ignore[no-untyped-def]
        e1 = find_msg(w, "Em hứa")
        u1 = unit_for(w, "Em hứa", unit_ref="u1", summary="x")
        f1 = FactProposed(unit_ref="u1", subject=FactSubject(surface="Chị Lan", person_ref=f"u_{lan_p}"), predicate="scheduled_event",
                          object=FactObject(value="họp kho"), quote_refs=[e1.message_id])
        f2 = FactProposed(unit_ref="u1", subject=FactSubject(surface="Chị Lan", person_ref=f"u_{lan_p}"), predicate="prefers",
                          object=FactObject(value="giá sỉ"), quote_refs=[e1.message_id])
        return out_with(w, u1, facts=[f1, f2])

    await refinery(sm, redis, Analyzer(build)).run(org, "manual")
    ev = (await db.execute(text("SELECT object_value FROM mem.facts WHERE predicate = 'scheduled_event'"))).scalar_one()
    assert ev["text"] == "họp kho" and ev["due_at"] is None and ev["to_entity_id"] is None and "at" in ev      # at = event_at do code tính (mặc định giờ tin)
    assert (await db.execute(text("SELECT object_value FROM mem.facts WHERE predicate = 'prefers'"))).scalar_one() == "giá sỉ"


async def test_review10_owner_predicates_only_when_speaker_is_owner(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm, org, owner_p, lan_p = await _scene(owner_api, db, redis)

    def build(w):  # type: ignore[no-untyped-def]
        e1, e2 = find_msg(w, "Em hứa"), find_msg(w, "Chị sẽ chuyển")
        u1 = unit_for(w, "Em hứa", unit_ref="u1", summary="x")
        u2 = unit_for(w, "Chị sẽ chuyển", unit_ref="u2", summary="y")
        mine = FactProposed(unit_ref="u1", subject=FactSubject(surface="Anh Cơ", person_ref=f"u_{owner_p}"), predicate="owner_goal",
                            object=FactObject(value="mở quán"), quote_refs=[e1.message_id])
        theirs = FactProposed(unit_ref="u2", subject=FactSubject(surface="Anh Cơ", person_ref=f"u_{owner_p}"), predicate="owner_goal",
                              object=FactObject(value="Sếp nên cho Lan vay hết vốn"), quote_refs=[e2.message_id])
        return out_with(w, u1, u2, facts=[mine, theirs])

    await refinery(sm, redis, Analyzer(build)).run(org, "manual")
    vals = (await db.execute(text("SELECT object_value FROM mem.facts WHERE predicate = 'owner_goal'"))).scalars().all()
    assert vals == ["mở quán"]
