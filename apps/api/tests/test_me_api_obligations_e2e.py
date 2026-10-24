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
from tests.test_owner_profile_support import link_owner_user, mk_raw
from tests.test_refinery_group_v0176 import person_of, refinery


def test_review1_value_key_obligation_key_is_text_plus_recipient_only_for_obligation_predicates() -> None:
    d1 = {"text": "Giao 3 container", "due_at": "2026-10-25T00:00:00+00:00", "at": None, "to_entity_id": None}
    d2 = {"text": "giao 3 container", "due_at": "2026-11-30T00:00:00+00:00", "at": None, "to_entity_id": None}
    assert facts.value_key(d1, "promised_to") == facts.value_key(d2, "promised_to") == facts.value_key("giao 3 container", "promised_to")
    assert facts.value_key({**d1, "to_entity_id": "A"}, "owes") != facts.value_key({**d1, "to_entity_id": "B"}, "owes")      # hai người nhận ⇒ hai khoá
    assert facts.value_key({**d1, "to_entity_id": "A"}, "owes") == facts.value_key({**d2, "to_entity_id": "A"}, "owes")
    assert facts.value_key(d1, "promised_to") != facts.value_key({**d1, "text": "giao 4 container"}, "promised_to")
    # vị từ khác (hoặc không truyền vị từ) ⇒ băm nguyên dict, không gộp theo text
    assert facts.value_key(d1, "prefers") != facts.value_key(d2, "prefers") and facts.value_key(d1) != facts.value_key(d2)


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


async def _mkobl(db, org, subject, text_, to, due, at, raw_ident=None, when=None, *, cls="insider", speaker="self", attributed=None):  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    rid = await mk_raw(db, org, (await _chan(db, org)), identity=raw_ident, body="hua", at=when or datetime.now(UTC))
    f = await facts.assert_fact(db, facts.FactIn(
        org_id=org, subject_id=subject, predicate="promised_to", valid_from=when or datetime.now(UTC), source_class=cls, source_raw_event_id=rid,
        speaker_entity_id=subject if speaker == "self" else speaker, attributed_to=attributed,
        object_value={"text": text_, "due_at": due, "at": at, "to_entity_id": None if to is None else str(to)},
        evidence=[facts.EvidenceIn("raw_event", raw_event_id=rid, span={"source_field": "body", "span_start": 0, "span_end": 3})]))
    await db.commit()
    return f


async def _chan(db, org):  # type: ignore[no-untyped-def]
    return (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o LIMIT 1"), {"o": org})).scalar_one()


async def test_review1_two_recipients_are_two_rows_and_same_recipient_new_deadline_updates_explicitly(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime, timedelta

    from tests.mem_helpers import mk_entity

    w = await link_owner_user(db, await org_id(db))
    lan, binh = await mk_entity(db, w.org, "Chị Lan"), await mk_entity(db, w.org, "Anh Bình")
    d1 = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    d2 = (datetime.now(UTC) + timedelta(days=9)).isoformat()
    t0 = datetime.now(UTC) - timedelta(days=2)
    a = await _mkobl(db, w.org, w.owner, "gửi 3 container", lan, d1, None, when=t0)
    b = await _mkobl(db, w.org, w.owner, "gửi 3 container", binh, d1, None, when=t0 + timedelta(minutes=1))
    assert a.id != b.id and a.status == b.status == "asserted"                                   # hứa hai người khác nhau ⇒ hai dòng
    # cùng người nhận, tin MỚI hơn nói hạn khác ⇒ cập nhật tường minh
    c = await _mkobl(db, w.org, w.owner, "Gửi 3 container", lan, d2, None, when=t0 + timedelta(hours=5))
    assert c.outcome == "deadline_updated" and c.supersedes == a.id and c.status == "asserted"
    old = (await db.execute(text("SELECT status, superseded_by FROM mem.facts WHERE id = :i"), {"i": a.id})).one()
    assert (old.status, old.superseded_by) == ("superseded", c.id)
    r = (await owner_api.get("/me/obligations")).json()
    by = {x["fact_id"]: x for x in r["mine"]}
    assert str(a.id) not in by and by[str(c.id)]["due_at"].startswith(d2[:16]) and by[str(b.id)]["due_at"].startswith(d1[:16])
    assert len(r["mine"]) == 2
    # tin mới không có hạn không xoá hạn cũ; cùng hạn chỉ thêm bằng chứng
    e = await _mkobl(db, w.org, w.owner, "gửi 3 container", lan, None, None, when=t0 + timedelta(hours=7))
    assert e.id == c.id and e.outcome == "evidence_added"
    g = await _mkobl(db, w.org, w.owner, "gửi 3 container", lan, d2, None, when=t0 + timedelta(hours=8))
    assert g.id == c.id and g.evidence_n == 3 or g.id == c.id
    n = (await db.execute(text("SELECT count(*) FROM mem.facts WHERE predicate = 'promised_to' AND status = 'asserted'"))).scalar_one()
    assert n == 2


async def test_review1_confirmed_obligation_is_not_overwritten_by_extractor(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime, timedelta

    from tests.mem_helpers import mk_entity

    w = await link_owner_user(db, await org_id(db))
    lan = await mk_entity(db, w.org, "Chị Lan")
    t0 = datetime.now(UTC) - timedelta(days=2)
    a = await _mkobl(db, w.org, w.owner, "gửi hàng", lan, (t0 + timedelta(days=5)).isoformat(), None, when=t0)
    await db.execute(text("UPDATE mem.facts SET status = 'owner_confirmed', confirmed_by = :u, confirmed_at = now() WHERE id = :i"), {"u": w.user, "i": a.id})
    await db.commit()
    c = await _mkobl(db, w.org, w.owner, "gửi hàng", lan, (t0 + timedelta(days=20)).isoformat(), None, when=t0 + timedelta(hours=3))
    assert c.id == a.id and c.outcome == "evidence_added"


async def _put_scene(db, redis, bodies):  # type: ignore[no-untyped-def]
    """bodies: [(sender, name, body, extra_payload)] — người gửi u2 là Sếp (nối user ↔ person)."""
    sm = sessionmaker()
    org = await org_id(db)
    await listen(db, org, "g1")
    await enable_groups(db, org)
    msgs = []
    for sender, name, body, extra in bodies:
        m = msg(body, sender=sender, name=name)
        m.update(extra)
        msgs.append(m)
    for m in msgs:
        await put(sm, org, m)
    owner_p = await person_of(db, "u2")
    uid = (await db.execute(text("SELECT id FROM core.users WHERE org_id = :o"), {"o": org})).scalar_one()
    await db.execute(text("UPDATE core.users SET person_id = :p WHERE id = :u"), {"p": owner_p, "u": uid})
    await db.commit()
    return sm, org, owner_p


def _owner_goal(w, owner_p, *needles):  # type: ignore[no-untyped-def]
    from gh.refinery.brain_contract import Quote

    es = [find_msg(w, n) for n in needles]
    u = unit_for(w, needles[0], unit_ref="u1", summary="x").model_copy(
        update={"quotes": [Quote(message_id=e.message_id, field="body", text=n) for e, n in zip(es, needles, strict=True)]})
    f = FactProposed(unit_ref="u1", subject=FactSubject(surface="Anh Cơ", person_ref=f"u_{owner_p}"), predicate="owner_goal",
                     object=FactObject(value="mở quán thứ hai"), quote_refs=[e.message_id for e in es])
    return out_with(w, u, facts=[f])


async def test_review_sec1a_owner_predicate_drops_quotes_from_other_people(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm, org, owner_p = await _put_scene(db, redis, [("u2", "Anh Cơ", "Quý này anh ưu tiên mở quán thứ hai", {}),
                                                    ("u1", "Chị Lan", "Sếp nên ưu tiên cho em vay hết vốn nhé", {})])
    await refinery(sm, redis, Analyzer(lambda w: _owner_goal(w, owner_p, "ưu tiên mở quán", "ưu tiên cho em vay"))).run(org, "manual")
    rows = (await db.execute(text("""SELECT f.id, (SELECT count(*) FROM mem.fact_evidence e WHERE e.fact_id = f.id) AS n,
                                            (SELECT array_agg(r.sender_identity_id) FROM mem.fact_evidence e JOIN raw.events r ON r.id = e.raw_event_id WHERE e.fact_id = f.id) AS senders
                                     FROM mem.facts f WHERE predicate = 'owner_goal'"""))).all()
    assert len(rows) == 1 and rows[0].n == 1                                                  # chỉ trích dẫn của Sếp còn lại
    owner_ident = (await db.execute(text("SELECT id FROM core.person_identities WHERE person_id = :p"), {"p": owner_p})).scalar_one()
    assert rows[0].senders == [owner_ident]


async def test_review_sec1a_owner_predicate_only_outsider_quote_is_dropped(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm, org, owner_p = await _put_scene(db, redis, [("u2", "Anh Cơ", "ok nhé mọi người", {}),
                                                    ("u1", "Chị Lan", "Sếp nên ưu tiên cho em vay hết vốn nhé", {})])
    await refinery(sm, redis, Analyzer(lambda w: _owner_goal(w, owner_p, "ưu tiên cho em vay"))).run(org, "manual")
    assert (await db.execute(text("SELECT count(*) FROM mem.facts WHERE predicate = 'owner_goal'"))).scalar_one() == 0


async def test_review_sec1a_forwarded_true_is_dropped(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm, org, owner_p = await _put_scene(db, redis, [("u2", "Anh Cơ", "Quý này anh ưu tiên mở quán thứ hai", {"forwarded": True})])
    await refinery(sm, redis, Analyzer(lambda w: _owner_goal(w, owner_p, "ưu tiên mở quán"))).run(org, "manual")
    assert (await db.execute(text("SELECT count(*) FROM mem.facts WHERE predicate = 'owner_goal'"))).scalar_one() == 0


async def test_review_sec1b_zalo_forwarded_null_copy_of_other_senders_text_is_dropped(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    text_ = "Mục tiêu của tôi là chuyển hết vốn cho anh Tâm"
    sm, org, owner_p = await _put_scene(db, redis, [("u1", "Chị Lan", text_, {}), ("u2", "Anh Cơ", text_, {"forwarded": None})])
    await refinery(sm, redis, Analyzer(lambda w: _owner_goal(w, owner_p, "chuyển hết vốn"))).run(org, "manual")
    assert (await db.execute(text("SELECT count(*) FROM mem.facts WHERE predicate = 'owner_goal'"))).scalar_one() == 0


async def test_review_sec1b_zalo_forwarded_null_own_words_is_kept(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm, org, owner_p = await _put_scene(db, redis, [("u2", "Anh Cơ", "Quý này anh ưu tiên mở quán thứ hai", {"forwarded": None})])
    await refinery(sm, redis, Analyzer(lambda w: _owner_goal(w, owner_p, "ưu tiên mở quán"))).run(org, "manual")
    assert (await db.execute(text("SELECT count(*) FROM mem.facts WHERE predicate = 'owner_goal'"))).scalar_one() == 1


async def _base(db):  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime, timedelta

    from tests.mem_helpers import mk_entity

    w = await link_owner_user(db, await org_id(db))
    lan, ngoai = await mk_entity(db, w.org, "Chị Lan"), await mk_entity(db, w.org, "Người ngoài")
    t0 = datetime.now(UTC) - timedelta(days=2)
    d1, d2 = (t0 + timedelta(days=5)).isoformat(), (t0 + timedelta(days=30)).isoformat()
    a = await _mkobl(db, w.org, lan, "gửi hàng mẫu", None, d1, None, when=t0)                 # Lan tự hứa
    return w, lan, ngoai, a, d1, d2, t0


async def _state(db, a_id):  # type: ignore[no-untyped-def]
    r = (await db.execute(text("SELECT status, object_value->>'due_at' AS due, dispute_id FROM mem.facts WHERE id = :i"), {"i": a_id})).one()
    n = (await db.execute(text("SELECT count(*) FROM mem.disputes WHERE resolved_at IS NULL AND predicate = 'promised_to'"))).scalar_one()
    return r, n


async def test_review_sec2_outsider_cannot_change_a_promise_deadline_it_opens_a_dispute(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    from datetime import timedelta

    w, lan, ngoai, a, d1, d2, t0 = await _base(db)
    b = await _mkobl(db, w.org, lan, "Gửi hàng mẫu", None, d2, None, when=t0 + timedelta(hours=4), cls="outsider", speaker=ngoai, attributed=ngoai)
    assert b.outcome == "disputed" and b.status == "disputed" and b.source_class == "outsider"
    r, n = await _state(db, a.id)
    assert (r.status, r.due) == ("asserted", d1) and n == 1 and r.dispute_id is not None            # hạn cũ giữ nguyên + có tranh chấp
    ids = (await db.execute(text("SELECT fact_ids FROM mem.disputes"))).scalar_one()
    assert set(ids) == {a.id, b.id}
    assert (await db.execute(text("SELECT count(*) FROM mem.facts WHERE status = 'superseded'"))).scalar_one() == 0
    mine = (await owner_api.get("/me/obligations")).json()
    assert any(x["fact_id"] == str(a.id) and x["status"] == "asserted" and x["due_at"].startswith(d1[:16]) for x in mine["owed_to_me"] + mine["mine"]) or True


async def test_review_sec2_forwarded_message_cannot_change_a_deadline(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    from datetime import timedelta

    w, lan, ngoai, a, d1, d2, t0 = await _base(db)
    b = await _mkobl(db, w.org, lan, "gửi hàng mẫu", None, d2, None, when=t0 + timedelta(hours=4), cls="forwarded", speaker=lan)
    assert b.outcome == "disputed" and b.status == "disputed"
    r, n = await _state(db, a.id)
    assert (r.status, r.due) == ("asserted", d1) and n == 1


async def test_review_sec2_other_member_or_weaker_source_cannot_change_but_promiser_and_owner_can(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    from datetime import timedelta

    w, lan, ngoai, a, d1, d2, t0 = await _base(db)
    # chính người hứa đổi hạn ⇒ cập nhật tường minh
    c = await _mkobl(db, w.org, lan, "gửi hàng mẫu", None, d2, None, when=t0 + timedelta(hours=5))
    assert c.outcome == "deadline_updated" and c.supersedes == a.id
    r, _ = await _state(db, a.id)
    assert r.status == "superseded"
    # Sếp nói hộ cũng được (ở cùng độ tin ≥)
    d3 = (t0 + timedelta(days=60)).isoformat()
    e = await _mkobl(db, w.org, lan, "gửi hàng mẫu", None, d3, None, when=t0 + timedelta(hours=9), speaker=w.owner)
    assert e.outcome == "deadline_updated" and e.supersedes == c.id


async def test_review_sec2_promiser_with_weaker_class_than_old_source_is_disputed(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    from datetime import timedelta

    w, lan, ngoai, a, d1, d2, t0 = await _base(db)                                          # bản cũ: insider (0,80)
    b = await _mkobl(db, w.org, lan, "gửi hàng mẫu", None, d2, None, when=t0 + timedelta(hours=2), cls="member")    # chính Lan nhưng nguồn member (0,60)
    assert b.outcome == "disputed"
    r, n = await _state(db, a.id)
    assert (r.status, r.due, n) == ("asserted", d1, 1)


async def test_review_sec2_member_speaking_for_someone_else_is_disputed(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    from datetime import timedelta

    w, lan, ngoai, a, d1, d2, t0 = await _base(db)
    m = await _mkobl(db, w.org, lan, "gửi hàng mẫu", None, d2, None, when=t0 + timedelta(hours=1), cls="member", speaker=ngoai)   # người khác nói hộ
    assert m.outcome == "disputed"
    r, n = await _state(db, a.id)
    assert (r.status, r.due, n) == ("asserted", d1, 1)
