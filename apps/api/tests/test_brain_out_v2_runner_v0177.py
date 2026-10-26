# ruff: noqa: E501
"""v0.1.77 (P3a) — runner nối `brain-out/2`: vai người gửi vào cửa sổ, `verified_fields` mang trường mới, `contract_version` ghi DB."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from gh.biz.mem import entities
from gh.db import sessionmaker
from gh.refinery import brain_contract as bc
from gh.refinery import runner, verify
from gh.refinery.runner import Refinery
from tests.mem_helpers import mk_person_row
from tests.phase2 import FakeRouter, listen, msg, org_id, put
from tests.refinery_group_helpers import Analyzer, enable_groups, out_with, unit_for


async def test_sender_roles_only_from_existing_data(app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    owner, staff_ok, staff_unmarked, cust, nobody = [await mk_person_row(db, org, n) for n in ("Sếp", "NV xác nhận", "NV chưa xác nhận", "Khách", "Người lạ")]
    await db.execute(text("UPDATE core.persons SET attrs = attrs || jsonb_build_object('is_owner', true) WHERE id = :p"), {"p": owner})
    await db.execute(text("""UPDATE core.persons SET person_type = 'staff', attrs = attrs || jsonb_build_object('person_type_locked', true,
                             'staff_marked_at', CAST(:t AS text)) WHERE id = :p"""), {"p": staff_ok, "t": datetime.now(UTC).isoformat()})
    await db.execute(text("UPDATE core.persons SET person_type = 'staff', attrs = attrs || jsonb_build_object('person_type_locked', true) WHERE id = :p"),
                     {"p": staff_unmarked})                                        # khoá nhưng chưa có staff_marked_at ⇒ không tin
    await db.execute(text("UPDATE core.persons SET person_type = 'customer' WHERE id = :p"), {"p": cust})
    await db.commit()
    got = await runner.sender_roles(db, org, [owner, staff_ok, staff_unmarked, cust, nobody, None])
    assert got == {owner: "owner", staff_ok: "staff", staff_unmarked: "staff_side", cust: "customer", nobody: None}      # NV chưa xác nhận ⇒ phía Sếp, không phải None
    assert await runner.sender_roles(db, org, []) == {} and entities is not None


async def test_customer_of_fact_gives_customer_role(app, db) -> None:  # type: ignore[no-untyped-def]
    from gh.biz.mem import facts
    from tests.mem_helpers import mkfact

    org = await org_id(db)
    pid = await mk_person_row(db, org, "Khách quen")
    ent = (await entities.ensure_person(db, org, pid)).id
    owner = (await entities.owner(db, org)).id
    assert (await runner.sender_roles(db, org, [pid]))[pid] is None            # chưa có dữ liệu ⇒ không suy đoán
    await facts.assert_fact(db, mkfact(org, ent, value=None, predicate="customer_of", object_entity=owner, cls="insider"))
    await db.commit()
    assert (await runner.sender_roles(db, org, [pid]))[pid] == "customer"


async def test_window_events_carry_sender_role_and_analysis_records_contract_v2(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org = await org_id(db)
    await listen(db, org, "g1")
    await enable_groups(db, org)
    await put(sm, org, msg("Em hứa giao 50 hộp thứ 7 nhé chị", sender="u1", name="Anh Khách"))
    await db.execute(text("UPDATE core.persons SET person_type = 'customer' WHERE display_name = 'Anh Khách'"))
    await db.commit()
    an = Analyzer(lambda w: out_with(w, unit_for(w, "giao 50 hộp", kind="nghia_vu_loi_hua", unit_ref="u1")))
    r = Refinery(sm, redis, FakeRouter(), None, analyzer=an)
    await r.run(org, "manual")
    assert [e.sender_role for e in an.windows[-1].new_events] == ["customer"]
    cv = (await db.execute(text("SELECT contract_version FROM refinery.analyses"))).scalars().all()
    assert cv == [bc.CONTRACT_VERSION] == ["brain-out/2"]


def test_verified_fields_carry_v2_fields_after_verify() -> None:
    now = datetime(2026, 10, 26, 10, 30, tzinfo=UTC)
    cus, staff = uuid.uuid4(), uuid.uuid4()
    events = [{"message_id": "m_1", "sent_at": now - timedelta(minutes=9), "body": "Cần gấp 50 tấn thép phi 16 tuần sau, khai trương thứ 7", "sender_person_id": cus,
               "sender_role": "customer", "raw_event_id": uuid.uuid4()},
              {"message_id": "m_2", "sent_at": now - timedelta(minutes=5), "body": "Em đang lo ạ", "sender_person_id": staff, "sender_role": "staff",
               "raw_event_id": uuid.uuid4()}]
    o = bc.BrainOut.model_validate({"thread_ref": "g:1", "units": [{
        "unit_ref": "u1", "event_ref": "e1", "kind": "co_hoi_ket_noi", "summary": "s",
        "quotes": [{"message_id": "m_1", "field": "body", "text": "Cần gấp 50 tấn thép phi 16"}, {"message_id": "m_2", "field": "body", "text": "Em đang lo ạ"}],
        "need": {"what": {"text": "thép phi 16", "message_id": "m_1"}, "qty": {"text": "50 tấn", "message_id": "m_1"}},
        "fixed_occasion": True, "occasion_message_id": "m_1", "complaint_stage": "voice", "stage_message_id": "m_1", "asks_for_owner": True, "asks_message_id": "m_1",
        "nudge_message_id": "m_1", "public_venue": "group", "flags": {"public": True},
        "status_hint": "dang_xu_ly", "status_message_id": "m_2", "owner_action": "Xem nhu cầu, trả lời khách nếu muốn nhận"}]})
    vu = verify.check(o, events, None, now=now).units[0]
    f = runner.verified_fields(vu, vu.unit, [])
    assert f["need_fields_n"] == 2 and f["need"]["what"]["text"] == "thép phi 16" and f["need"]["when"] is None
    assert (f["complaint_stage"], f["stage_message_id"], f["asks_for_owner"], f["public_venue"], f["public"], f["fixed_occasion"], f["nudge_message_id"],
            f["owner_action"], f["status_by_role"], f["status_hint"]) == (
        "voice", "m_1", True, "group", True, True, "m_1", "Xem nhu cầu, trả lời khách nếu muốn nhận", "staff", "dang_xu_ly")
    # trường bị verify loại thì không vào entities.v
    events[0]["sender_role"] = "staff"
    vu2 = verify.check(o, events, None, now=now).units[0]
    f2 = runner.verified_fields(vu2, vu2.unit, [])
    assert f2["complaint_stage"] is None and f2["asks_for_owner"] is False and f2["nudge_message_id"] is None
    assert runner.verified_fields(None, None, []) == {"people": [], "status_hint": None, "condition_met": None}


async def test_sender_roles_cold_customer_only_in_confirmed_biz_group_or_dm(app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    gid = await listen(db, org, "g1")
    stranger, staff_side = await mk_person_row(db, org, "Người lạ"), await mk_person_row(db, org, "NV")
    await db.execute(text("UPDATE core.persons SET person_type = 'staff' WHERE id = :p"), {"p": staff_side})
    await db.commit()
    tk = f"g:{gid}"
    assert (await runner.sender_roles(db, org, [stranger], thread_key=tk))[stranger] is None                # nhóm chưa xác nhận ⇒ không suy
    await db.execute(text("INSERT INTO mem.group_contexts (org_id, group_id, context, source, is_primary) VALUES (:o, :g, 'kinh_doanh', 'owner', true)"),
                     {"o": org, "g": gid})
    await db.commit()
    got = await runner.sender_roles(db, org, [stranger, staff_side], thread_key=tk)
    assert got == {stranger: "cold_customer", staff_side: "staff_side"}                                     # NV (kể cả chưa tin) không thành khách
    assert (await runner.sender_roles(db, org, [stranger], thread_key=f"dm:{stranger}:{uuid.uuid4()}"))[stranger] == "cold_customer"
    assert (await runner.sender_roles(db, org, [stranger]))[stranger] is None                               # không có luồng


async def test_sender_roles_never_looks_at_another_orgs_person(app, db) -> None:  # type: ignore[no-untyped-def]
    from tests.mem_helpers import mk_org

    org = await org_id(db)
    other_org = await mk_org(db, "Tổ chức khác")
    foreign = await mk_person_row(db, other_org, "Người org khác")
    await db.execute(text("UPDATE core.persons SET person_type = 'customer' WHERE id = :p"), {"p": foreign})
    await db.commit()
    before = (await db.execute(text("SELECT count(*) FROM mem.entities WHERE person_id = :p"), {"p": foreign})).scalar_one()
    assert await runner.sender_roles(db, org, [foreign]) == {foreign: None}
    assert (await db.execute(text("SELECT count(*) FROM mem.entities WHERE person_id = :p"), {"p": foreign})).scalar_one() == before     # không tạo thực thể chéo org


async def test_analysis_records_contract_version_of_the_model_output(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org = await org_id(db)
    await listen(db, org, "g1")
    await enable_groups(db, org)
    await put(sm, org, msg("Em hứa giao 50 hộp thứ 7 nhé chị", sender="u1"))

    def build(w):  # type: ignore[no-untyped-def]
        o = out_with(w, unit_for(w, "giao 50 hộp", kind="nghia_vu_loi_hua", unit_ref="u1"))
        o.contract_version = "brain-out/1"
        return o

    await Refinery(sm, redis, FakeRouter(), None, analyzer=Analyzer(build)).run(org, "manual")
    assert (await db.execute(text("SELECT contract_version FROM refinery.analyses"))).scalars().all() == ["brain-out/1"]
    v = (await db.execute(text("SELECT entities -> 'v' FROM clean.meaning_units"))).scalar_one()
    assert v["contract_version"] == "brain-out/1" and v["public_eff"] is False and v["nudge_rejected"] is False


def test_verified_fields_public_eff_keeps_raw_flag_and_records_nudge_rejected() -> None:
    now = datetime(2026, 10, 26, 10, 30, tzinfo=UTC)
    cus, staff = uuid.uuid4(), uuid.uuid4()
    events = [{"message_id": "m_1", "sent_at": now, "body": "Chê quán dở quá", "sender_person_id": cus, "sender_role": "customer", "raw_event_id": uuid.uuid4()},
              {"message_id": "m_2", "sent_at": now, "body": "Dạ em xin lỗi", "sender_person_id": staff, "sender_role": "staff_side", "raw_event_id": uuid.uuid4()}]
    base = {"unit_ref": "u1", "event_ref": "e1", "kind": "xung_dot_khieu_nai", "summary": "s", "flags": {"public": True},
            "quotes": [{"message_id": "m_1", "field": "body", "text": "Chê quán dở quá"}, {"message_id": "m_2", "field": "body", "text": "Dạ em xin lỗi"}],
            "nudge_message_id": "m_2"}
    for version, eff in (("brain-out/2", False), ("brain-out/1", True)):
        o = bc.BrainOut.model_validate({"contract_version": version, "thread_ref": "g:1", "units": [base]})
        vu = verify.check(o, events, None, now=now).units[0]
        f = runner.verified_fields(vu, vu.unit, [])
        assert f["public_eff"] is eff and vu.unit.flags.public is True and f["nudge_rejected"] is True and f["nudge_by_role"] is None
        assert f["contract_version"] == version


async def test_history_evidence_char_cap_follows_priority_not_time(app, db, redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    sm = sessionmaker()
    org = await org_id(db)
    gid = await listen(db, org, "g1")
    await enable_groups(db, org)
    promise = msg("Em giữ giá 320k/thùng cho chị tới hết tháng nhé", sender="u1")           # 47 ký tự, cũ nhất
    other = msg("Chị ơi hôm qua quán hơi ồn nha em ơi nhé nhé", sender="u1")                  # mới hơn, cùng độ dài
    promise["occurred_at"] = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    other["occurred_at"] = (datetime.now(UTC) - timedelta(days=4)).isoformat()
    [pid, oid] = await put(sm, org, promise, other)
    rcv = {r.id: r.received_at for r in (await db.execute(text("SELECT id, received_at FROM raw.events WHERE id = ANY(:i)"), {"i": [pid, oid]})).all()}
    monkeypatch.setattr(runner, "HISTORY_EVIDENCE_CHARS", 60)                                 # chỉ đủ MỘT tin
    pack = SimpleNamespace(history=[SimpleNamespace(meta={"kind_hint": "nghia_vu_loi_hua", "evidence": [(pid, rcv[pid])]}),
                                    SimpleNamespace(meta={"kind_hint": "xung_dot_khieu_nai", "evidence": [(oid, rcv[oid])]})])
    turns = Refinery(sm, redis, FakeRouter(), None, analyzer=Analyzer()).groups
    got = await turns._history_evidence(db, org, f"g:{gid}", [], [], pack)
    assert [c.raw_event_id for c in got] == [pid]                                             # tin lời hứa cũ thắng tin mới hơn khi hết trần ký tự
    monkeypatch.setattr(runner, "HISTORY_EVIDENCE_CHARS", 3000)
    both = await turns._history_evidence(db, org, f"g:{gid}", [], [], pack)
    assert [c.raw_event_id for c in both] == [pid, oid]                                       # đủ trần ⇒ trả theo giờ gửi
