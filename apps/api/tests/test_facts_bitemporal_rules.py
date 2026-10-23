# ruff: noqa: E501
"""Luật ghi sự thật ngoài 10 ca S6: bằng chứng hoãn, bất biến, nguồn ngoài, Sếp xác nhận/sửa, hai thời gian, khoá so sánh."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from gh.biz.mem import disputes, entities, facts
from gh.biz.mem.facts import EvidenceIn, FactIn, FactWriteError
from tests.mem_helpers import T, assert_no_overlap, mk_entity, mk_org, mkfact, n_disputes, rows


async def _owner_user(db, org):  # type: ignore[no-untyped-def]
    return (await db.execute(text("""INSERT INTO core.users (org_id, email, display_name, password_hash)
                                     VALUES (:o, 'sep@example.vn', 'Sếp', 'x') RETURNING id"""), {"o": org})).scalar_one()


async def test_rules_value_key_folds_case_accents_spaces_and_json() -> None:
    k = facts.value_key
    assert k("Hà  Nội") == k("ha noi") == k(" HÀ NỘI ")
    assert k({"b": "Đồng Nai", "a": 1}) == k({"a": 1, "b": "dong nai"})
    assert k("0901") != k("0902") and k({"a": 1}) != k({"a": 2})
    assert facts.entity_key(uuid.UUID(int=5)) == "e:" + str(uuid.UUID(int=5))


async def test_rules_insert_without_verified_evidence_raises_and_rolls_back(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan = await mk_entity(db, org)
    with pytest.raises(FactWriteError) as ei:
        await facts.assert_fact(db, mkfact(org, lan, "0901", verified=False))
    assert ei.value.code == "evidence_missing"
    assert (await db.execute(text("SELECT count(*) FROM mem.facts"))).scalar_one() == 0
    # không bằng chứng nào
    f = mkfact(org, lan, "0901")
    f.evidence = []
    with pytest.raises(FactWriteError) as ei2:
        await facts.assert_fact(db, f)
    assert ei2.value.code == "evidence_missing"
    # savepoint đã cuộn lại ⇒ phiên còn dùng được
    ok = await facts.assert_fact(db, mkfact(org, lan, "0901"))
    assert ok.evidence_n == 1


async def test_rules_proposed_needs_any_evidence_not_verified(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    owner = await mk_entity(db, org, "Sếp")
    f = await facts.assert_fact(db, mkfact(org, owner, "mở quán thứ 2", predicate="owner_goal", verified=False, proposed=True))
    assert f.status == "proposed" and f.evidence_n == 0       # chưa kiểm ⇒ không đếm vào evidence_n
    # proposed không vào Hồ sơ chấm mức
    from gh.biz.mem import profile_read

    await db.execute(text("UPDATE mem.entities SET is_owner = true WHERE id = :i"), {"i": owner})
    snap = await profile_read.snapshot(db, org)
    assert snap.items == () and snap.version == 0


async def test_rules_facts_guard_blocks_content_edit_and_delete(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan = await mk_entity(db, org)
    f = await facts.assert_fact(db, mkfact(org, lan, "0901"))
    for sql in ("UPDATE mem.facts SET object_value = '\"x\"' WHERE id = :i", "UPDATE mem.facts SET valid_from = now() WHERE id = :i",
                "UPDATE mem.facts SET predicate = 'lives_in' WHERE id = :i", "UPDATE mem.facts SET evidence_n = 9 WHERE id = :i",
                "DELETE FROM mem.facts WHERE id = :i", "UPDATE mem.fact_status_events SET reason = 'x'",
                "DELETE FROM mem.fact_status_events"):
        with pytest.raises(DBAPIError):
            async with db.begin_nested():
                await db.execute(text(sql), {"i": f.id})
    with pytest.raises(DBAPIError):
        async with db.begin_nested():
            await db.execute(text("TRUNCATE mem.facts CASCADE"))
    # các cột được phép đổi thì đổi được
    await db.execute(text("UPDATE mem.facts SET valid_to = :t WHERE id = :i"), {"t": T(9), "i": f.id})
    # xoá chỉ khi cờ quên người bật
    await db.execute(text("SELECT set_config('gh.mem_erase', 'on', true)"))
    await db.execute(text("DELETE FROM mem.facts WHERE id = :i"), {"i": f.id})
    assert (await db.execute(text("SELECT count(*) FROM mem.fact_status_events"))).scalar_one() == 0   # cascade cũng qua được


async def test_rules_outsider_never_owner_confirmed_and_confidence_capped(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan, stranger = await mk_entity(db, org), await mk_entity(db, org, "Người lạ")
    user = await _owner_user(db, org)
    bad = mkfact(org, lan, "x", cls="outsider", speaker=stranger, confirmed_by=user)
    with pytest.raises(FactWriteError) as ei:
        await facts.assert_fact(db, bad)
    assert ei.value.code == "invalid_fact"
    with pytest.raises(DBAPIError):                                # và CHECK ở DB
        async with db.begin_nested():
            await db.execute(text("""INSERT INTO mem.facts (org_id, subject_id, predicate, object_value, object_key, valid_from,
                status, is_single, source_class, source_reliability, confidence_exist, confidence_content, created_by, confirmed_by)
                VALUES (:o, :s, 'contact_phone', '"1"', 'k', now(), 'owner_confirmed', true, 'outsider', 0.3, 0.5, 0.5, 'extractor', :u)"""),
                             {"o": org, "s": lan, "u": user})
    f = await facts.assert_fact(db, mkfact(org, lan, "0909", cls="outsider", speaker=stranger, confidence_content=0.99))
    assert f.status == "asserted" and f.confidence_content == 0.5 and f.attributed_to == stranger   # theo lời X


async def test_rules_outsider_conflict_only_makes_insider_fact_disputed(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan, stranger = await mk_entity(db, org), await mk_entity(db, org, "Người lạ")
    base = await facts.assert_fact(db, mkfact(org, lan, "0901", T(1), speaker=lan))
    out = await facts.assert_fact(db, mkfact(org, lan, "0999", T(3), cls="outsider", speaker=stranger))
    assert out.status == "disputed" and out.outcome == "disputed"
    assert (await facts.get(db, org, base.id)).status == "disputed"  # type: ignore[union-attr]   # không bị đóng (valid_to giữ nguyên)
    assert (await facts.get(db, org, base.id)).valid_to is None  # type: ignore[union-attr]
    # không có sự thật tranh chấp nào thành "hiện hành"
    assert await facts.at(db, org, lan, "contact_phone", T(10), include_disputed=False) == []
    assert (await disputes.list(db, org))[0].dispute_signal_kind is None   # contact_phone không sinh tín hiệu


async def test_rules_member_cannot_replace_insider_value_about_someone_else_goes_dispute(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan, other = await mk_entity(db, org), await mk_entity(db, org, "Bình")
    await facts.assert_fact(db, mkfact(org, lan, "HN", T(1), predicate="lives_in", speaker=lan))
    x = await facts.assert_fact(db, mkfact(org, lan, "HCM", T(3), predicate="lives_in", speaker=other, cls="member"))
    assert x.status == "disputed" and await n_disputes(db, lan) == 1


async def test_rules_owner_confirmed_only_closed_by_owner(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan = await mk_entity(db, org)
    user = await _owner_user(db, org)
    ow = await facts.assert_fact(db, mkfact(org, lan, "0901", T(1), cls="owner", confirmed_by=user))
    assert ow.status == "owner_confirmed" and ow.confirmed_by == user
    # chính chủ nói đổi, nhưng sự thật owner_confirmed chỉ Sếp đóng được ⇒ tranh chấp
    me = await facts.assert_fact(db, mkfact(org, lan, "0902", T(4), speaker=lan))
    assert me.status == "disputed" and (await facts.get(db, org, ow.id)).status == "disputed"  # type: ignore[union-attr]
    # Sếp nói lại ⇒ đóng tranh chấp với bản của Sếp thắng
    win = await facts.assert_fact(db, mkfact(org, lan, "0903", T(6), cls="owner", confirmed_by=user))
    assert win.status == "owner_confirmed"
    d = (await disputes.list(db, org, open_only=False))[0]
    assert d.resolved_by == "owner" and d.winner_fact_id == win.id
    assert (await facts.get(db, org, me.id)).status == "superseded"  # type: ignore[union-attr]
    await assert_no_overlap(db, lan)


async def test_rules_owner_correct_supersedes_and_retract_keeps_row(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan = await mk_entity(db, org)
    user = await _owner_user(db, org)
    old = await facts.assert_fact(db, mkfact(org, lan, "khách", T(1), predicate="prefers"))
    new = await facts.correct(db, org, old.id, actor_user_id=user, new_value="em họ")
    assert new.status == "owner_confirmed" and new.supersedes == old.id and new.valid_from == old.valid_from
    o = await facts.get(db, org, old.id)
    assert o is not None and o.status == "superseded" and o.superseded_by == new.id
    ev = (await db.execute(text("SELECT kind, quote_status FROM mem.fact_evidence WHERE fact_id = :f"), {"f": new.id})).one()
    assert (ev.kind, ev.quote_status) == ("owner_edit", "verified")
    gone = await facts.correct(db, org, new.id, actor_user_id=user, retract=True)
    assert gone.status == "retracted" and gone.valid_to is not None
    n = (await db.execute(text("SELECT count(*) FROM mem.facts WHERE subject_id = :s"), {"s": lan})).scalar_one()
    assert n == 2                                                    # không xoá dòng
    with pytest.raises(FactWriteError):
        await facts.correct(db, org, new.id, actor_user_id=user, new_value="x")        # đã retracted
    with pytest.raises(FactWriteError):
        await facts.correct(db, org, old.id, actor_user_id=user)                        # thiếu cả value lẫn retract


async def test_rules_multi_valued_same_value_adds_evidence_other_values_coexist(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan = await mk_entity(db, org)
    a = await facts.assert_fact(db, mkfact(org, lan, "giao 200 bao", T(1), predicate="promised_to"))
    b = await facts.assert_fact(db, mkfact(org, lan, "Giao 200 BAO", T(2), predicate="promised_to"))
    assert b.id == a.id and b.outcome == "evidence_added" and b.evidence_n == 2
    c = await facts.assert_fact(db, mkfact(org, lan, "chuyển 30%", T(3), predicate="promised_to"))
    assert c.id != a.id and len(await rows(db, lan, "promised_to")) == 2


async def test_rules_validation_unknown_predicate_object_kind_subject_type(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan = await mk_entity(db, org)
    grp = await mk_entity(db, org, "Nhóm", "group")
    with pytest.raises(FactWriteError) as e1:
        await facts.assert_fact(db, mkfact(org, lan, "x", predicate="khong_co_vi_tu"))
    assert e1.value.code == "unknown_predicate"
    with pytest.raises(FactWriteError) as e2:
        await facts.assert_fact(db, mkfact(org, lan, "x", predicate="works_at"))          # cần object_entity_id
    assert e2.value.code == "invalid_fact"
    with pytest.raises(FactWriteError) as e3:
        await facts.assert_fact(db, mkfact(org, grp, "0901"))                             # contact_phone: chủ thể không phải nhóm
    assert e3.value.code == "invalid_fact"
    corp = await mk_entity(db, org, "Công ty A", "org")
    ok = await facts.assert_fact(db, mkfact(org, lan, None, T(1), predicate="works_at", object_entity=corp, speaker=lan))
    assert ok.object_entity_id == corp and ok.object_key == facts.entity_key(corp)
    other_org = await mk_org(db, "Tổ chức khác")
    with pytest.raises(FactWriteError) as e4:
        await facts.assert_fact(db, mkfact(other_org, lan, "x"))
    assert e4.value.code == "invalid_fact"


async def test_rules_same_value_as_prev_adds_evidence_only(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan = await mk_entity(db, org)
    a = await facts.assert_fact(db, mkfact(org, lan, "0901", T(1), speaker=lan))
    b = await facts.assert_fact(db, mkfact(org, lan, "0901", T(5), speaker=lan))
    assert b.id == a.id and b.outcome == "evidence_added" and b.evidence_n == 2
    assert await rows(db, lan) == [(1, None, "asserted", "0901")]


async def test_rules_tie_on_valid_from_older_receipt_goes_before(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan = await mk_entity(db, org)
    newer = await facts.assert_fact(db, mkfact(org, lan, "B", T(5), speaker=lan, recv=T(6)))
    older = await facts.assert_fact(db, mkfact(org, lan, "A", T(5), speaker=lan, recv=T(5)))   # tin nhận TRƯỚC, cùng thời điểm
    assert older.outcome == "inserted_between"
    cur = await facts.at(db, org, lan, "contact_phone", T(20))
    assert [f.id for f in cur] == [newer.id]                         # sự thật hiện hành vẫn là tin nhận sau
    await assert_no_overlap(db, lan)


async def test_rules_known_at_reconstructs_what_system_believed(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    org = await mk_org(db)
    lan = await mk_entity(db, org)
    before = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
    await asyncio.sleep(0.05)
    a = await facts.assert_fact(db, mkfact(org, lan, "A", T(1), speaker=lan))
    await asyncio.sleep(0.05)
    mid = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
    await asyncio.sleep(0.05)
    await facts.assert_fact(db, mkfact(org, lan, "B", T(5), speaker=lan))
    # lúc `mid` hệ thống tin A đang hiệu lực vô thời hạn; nay A đã đóng ở ngày 5
    then = await facts.at(db, org, lan, "contact_phone", T(8), known_at=mid)
    assert [f.id for f in then] == [a.id] and then[0].valid_to is None
    now = await facts.at(db, org, lan, "contact_phone", T(8))
    assert [f.object_value for f in now] == ["B"]
    assert [f.object_value for f in await facts.at(db, org, lan, "contact_phone", T(2))] == ["A"]
    assert await facts.at(db, org, lan, "contact_phone", T(2), known_at=before) == []     # trước khi hệ thống biết gì


async def test_rules_idempotent_search_includes_merged_entities(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    a, b = await mk_entity(db, org, "A"), await mk_entity(db, org, "B")
    raw = uuid.uuid4()
    f1 = await facts.assert_fact(db, mkfact(org, a, "HN", T(1), predicate="lives_in", raw=raw, speaker=a))
    await entities.merge(db, org, a, b)
    f2 = await facts.assert_fact(db, mkfact(org, b, "HN", T(1), predicate="lives_in", raw=raw, speaker=b))
    assert f2.id == f1.id and f2.outcome == "duplicate"


async def test_rules_retry_failed_reprocesses_then_gives_up_after_three(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan = await mk_entity(db, org)
    res = await facts.assert_facts(db, [mkfact(org, lan, "0901", verified=False)])
    assert res.errors[0]["code"] == "evidence_missing"
    await db.execute(text("UPDATE refinery.fact_retry SET next_at = now() - interval '1 minute'"))
    out = await facts.retry_failed(db)
    assert out == {"ok": 0, "failed": 1}
    for _ in range(3):
        await db.execute(text("UPDATE refinery.fact_retry SET next_at = now() - interval '1 minute'"))
        await facts.retry_failed(db)
    attempts = (await db.execute(text("SELECT attempts FROM refinery.fact_retry"))).scalar_one()
    assert attempts == 3                                              # dừng ở 3, không thử nữa
    assert await facts.retry_failed(db) == {"ok": 0, "failed": 0}
    # serialize/deserialize FactIn khớp
    f = mkfact(org, lan, {"digest": "x"}, T(3), predicate="payment_account")
    g = FactIn.from_json(f.to_json())
    assert g.valid_from == f.valid_from and g.object_value == f.object_value and g.evidence[0].raw_event_id == f.evidence[0].raw_event_id
    assert isinstance(g.evidence[0], EvidenceIn)


async def test_rules_dispute_expiry_retracts_both_except_payment_account(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan, other = await mk_entity(db, org), await mk_entity(db, org, "Bình")
    await facts.assert_fact(db, mkfact(org, lan, "HN", T(1), predicate="lives_in", speaker=lan))
    await facts.assert_fact(db, mkfact(org, lan, "HCM", T(3), predicate="lives_in", speaker=other, cls="member"))
    await facts.assert_fact(db, mkfact(org, lan, {"digest": "a"}, T(1), predicate="payment_account", speaker=lan))
    await facts.assert_fact(db, mkfact(org, lan, {"digest": "b"}, T(3), predicate="payment_account", speaker=lan))
    await db.execute(text("UPDATE mem.disputes SET opened_at = now() - interval '31 days'"))
    assert await disputes.expire_stale(db, org) == 1                  # payment_account không hết hạn
    st = (await db.execute(text("SELECT predicate, status FROM mem.facts ORDER BY predicate, status"))).all()
    assert [(r.predicate, r.status) for r in st] == [("lives_in", "retracted"), ("lives_in", "retracted"),
                                                     ("payment_account", "disputed"), ("payment_account", "disputed")]
    left = [d for d in await disputes.list(db, org)]
    assert [d.predicate for d in left] == ["payment_account"]


async def test_rules_payment_account_stores_only_digest_and_last3(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan = await mk_entity(db, org)
    for bad in ("0123456789012", {"digest": "x", "so_tai_khoan": "1"}, {"last3": "123"}, {"digest": "0123456789012"},
                {"digest": "abc", "last3": "1234"}, {"digest": "abc", "bank": "VCB 0123456789"}):
        with pytest.raises(FactWriteError) as ei:
            await facts.assert_fact(db, mkfact(org, lan, bad, predicate="payment_account"))
        assert ei.value.code == "invalid_fact", bad
    ok = await facts.assert_fact(db, mkfact(org, lan, {"digest": "9f86d0", "last3": "789", "bank": "VCB"}, predicate="payment_account"))
    assert ok.sensitivity == "sensitive" and ok.status == "asserted"
