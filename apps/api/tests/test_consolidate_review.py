# ruff: noqa: E501
"""Review opus L4 (mục 2, 9): tự đóng tranh chấp chỉ tính bằng chứng do CHÍNH chủ thể/Sếp nói, theo giờ tin; vị từ nhạy bị loại; due_at parse an toàn."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from gh.biz.mem import consolidate, disputes, entities, facts
from tests.mem_helpers import mk_org
from tests.test_owner_profile_support import mk_person, mk_raw, mk_world


async def _say(db, w, ident, speaker, cls, pred, val, at):  # type: ignore[no-untyped-def]
    rid = await mk_raw(db, w.org, w.channel, identity=ident, body=f"noi: {val}", group=w.group, at=at)
    subject = w.subject
    fin = facts.FactIn(org_id=w.org, subject_id=subject, predicate=pred, valid_from=at, object_value=val, source_class=cls, speaker_entity_id=speaker,
                       source_raw_event_id=rid, confidence_exist=0.5, confidence_content=0.5, extractor_version="t/1", created_by="extractor",
                       evidence=[facts.EvidenceIn("raw_event", raw_event_id=rid, span={"source_field": "body", "span_start": 0, "span_end": 3})])
    f = await facts.assert_fact(db, fin)
    await db.commit()
    return f


async def _evidence_from(db, w, fact_id, ident, at):  # type: ignore[no-untyped-def]
    rid = await mk_raw(db, w.org, w.channel, identity=ident, body="lai noi", group=w.group, at=at)
    rcv = (await db.execute(text("SELECT received_at FROM raw.events WHERE id = :i"), {"i": rid})).scalar_one()
    await db.execute(text("""INSERT INTO mem.fact_evidence (org_id, fact_id, kind, raw_event_id, raw_received_at, quote_status, span)
                             VALUES (:o, :f, 'raw_event', :r, :rc, 'verified', CAST('{"source_field": "body", "span_start": 0, "span_end": 3}' AS jsonb))"""),
                     {"o": w.org, "f": fact_id, "r": rid, "rc": rcv})
    await db.commit()


async def _scene(db, pred="lives_in", a_val="HN", b_val="HCM"):  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    x_pid, x_i = await mk_person(db, w.org, w.channel, "X")
    m_pid, m_i = await mk_person(db, w.org, w.channel, "M")
    o_pid, o_i = await mk_person(db, w.org, w.channel, "O")
    w.subject = (await entities.ensure_person(db, w.org, x_pid)).id
    M = (await entities.ensure_person(db, w.org, m_pid)).id
    await db.commit()
    t0 = datetime.now(UTC) - timedelta(days=5)
    a = await _say(db, w, x_i, w.subject, "insider", pred, a_val, t0)
    b = await _say(db, w, m_i, M, "member", pred, b_val, t0 + timedelta(hours=1))
    d = (await disputes.list(db, w.org, open_only=True))[0]
    w.a_id, w.b_id = a.id, b.id
    w.x_i, w.m_i, w.o_i = x_i, m_i, o_i
    return w, a, b, d


async def test_review2_outsider_and_member_evidence_never_closes_a_dispute(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w, a, b, d = await _scene(db)
    assert {x.status for x in (await db.execute(text("SELECT status FROM mem.facts"))).all()} == {"disputed"}
    for k in range(3):                                         # người ngoài lặp lại đúng giá trị của insider vào 3 ngày khác nhau
        await _evidence_from(db, w, a.id, w.o_i, d.opened_at + timedelta(days=k, hours=1))
    for k in range(3):                                         # người khác trong nhóm cũng không phải chủ thể
        await _evidence_from(db, w, a.id, w.m_i, d.opened_at + timedelta(days=k, hours=2))
    assert (await consolidate.resolve_by_evidence(db, w.org))["disputes_resolved"] == 0
    assert (await db.execute(text("SELECT resolved_at FROM mem.disputes"))).scalar_one() is None


async def test_review2_subject_or_owner_evidence_by_message_time_closes(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w, a, b, d = await _scene(db)
    await _evidence_from(db, w, a.id, w.x_i, d.opened_at + timedelta(hours=1))                # chính chủ thể
    await _evidence_from(db, w, a.id, w.owner_identity, d.opened_at + timedelta(days=1, hours=1))   # Sếp xác nhận
    assert (await consolidate.resolve_by_evidence(db, w.org))["disputes_resolved"] == 1
    sts = {x.id: x.status for x in (await db.execute(text("SELECT id, status FROM mem.facts"))).all()}
    assert sts == {a.id: "asserted", b.id: "superseded"}


async def test_review2_old_messages_processed_late_are_not_new(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    """Tin nói TRƯỚC khi mở tranh chấp nhưng bằng chứng ghi sau (xử lý trễ) không phải bằng chứng mới."""
    w, a, b, d = await _scene(db)
    await _evidence_from(db, w, a.id, w.x_i, d.opened_at - timedelta(days=2))
    await _evidence_from(db, w, a.id, w.x_i, d.opened_at - timedelta(days=1))
    assert (await consolidate.resolve_by_evidence(db, w.org))["disputes_resolved"] == 0


async def test_review2_same_raw_event_counts_once(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    from zoneinfo import ZoneInfo

    w, a, b, d = await _scene(db)
    # hai tin CÙNG một ngày giờ VN (bộ đếm ngày theo Asia/Ho_Chi_Minh). Trước đây +1h/+2h từ giờ mở tranh chấp (= giờ máy) vắt qua nửa đêm
    # VN khi test chạy lúc 22:00–23:00 VN ⇒ thành hai ngày ⇒ đóng tranh chấp ⇒ test đỏ theo giờ chạy.
    vn = ZoneInfo("Asia/Ho_Chi_Minh")
    t1 = d.opened_at + timedelta(minutes=1)
    if (t1 + timedelta(minutes=1)).astimezone(vn).date() != t1.astimezone(vn).date():
        t1 += timedelta(minutes=3)
    await _evidence_from(db, w, a.id, w.x_i, t1)
    await _evidence_from(db, w, a.id, w.x_i, t1 + timedelta(minutes=1))
    assert (await consolidate.resolve_by_evidence(db, w.org))["disputes_resolved"] == 0       # cùng một ngày dù đúng người


async def test_review2_sensitive_predicates_are_left_to_the_owner(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    for pred, a_val, b_val in (("role_in_group", "ke toan", "khach"), ("works_at", None, None), ("contact_phone", "0901", "0902")):
        if pred == "works_at":
            continue                                              # works_at là vị từ đối tượng — cùng nhóm loại trừ, kiểm bằng hằng
        w, a, b, d = await _scene(db, pred, a_val, b_val)
        await _evidence_from(db, w, a.id, w.x_i, d.opened_at + timedelta(hours=1))
        await _evidence_from(db, w, a.id, w.x_i, d.opened_at + timedelta(days=1, hours=1))
        assert (await consolidate.resolve_by_evidence(db, w.org))["disputes_resolved"] == 0, pred
    assert consolidate.AUTO_CLOSE_EXCLUDED == frozenset({"contact_phone", "role_in_group", "works_at"})


async def test_review9_due_soon_parses_safely_in_python(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    soon = (datetime.now(UTC) + timedelta(hours=5)).isoformat()
    for i, v in enumerate((soon, "2026-13-45T99:99:99", "2026-12-25", "mai nhé", None)):
        val = {"text": f"hứa {i}"} | ({} if v is None else {"due_at": v})
        rid = await mk_raw(db, w.org, w.channel, identity=w.owner_identity, body=f"hua {i}", group=w.group)
        await facts.assert_fact(db, facts.FactIn(org_id=w.org, subject_id=w.owner, predicate="promised_to", valid_from=datetime.now(UTC) - timedelta(days=1),
                                                  object_value=val, source_class="insider", source_raw_event_id=rid,
                                                  evidence=[facts.EvidenceIn("raw_event", raw_event_id=rid, span={"source_field": "body", "span_start": 0, "span_end": 3})]))
    await db.commit()
    r = await consolidate.count_promises_due_soon(db, w.org)                 # trước đây: cast ngày không hợp lệ làm hỏng cả đêm
    assert r == {"promises_due_soon": 1}
    assert uuid.UUID(int=0)
