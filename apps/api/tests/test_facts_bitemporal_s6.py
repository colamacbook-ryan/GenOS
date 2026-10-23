# ruff: noqa: E501
"""S6 (spec §4A.1, §10): `facts.assert_fact` kiểm TRẠNG THÁI SAU GHI chứ không chỉ "không lỗi" — 10 ca + bất biến chung.

Sau mỗi ca: không có hai dòng asserted/owner_confirmed chồng khoảng cùng (chủ thể, vị từ) đơn trị; `fact_write_conflict` = 0.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text

from gh.biz.mem import disputes, entities, facts
from gh.biz.mem.facts import FactWriteError
from gh.db import sessionmaker
from tests.mem_helpers import T, assert_no_overlap, mk_entity, mk_org, mkfact, n_disputes, pay, rows


async def _setup(db):  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    return org, await mk_entity(db, org, "Lan")


async def test_s6_case1_forward_change_closes_x_keeps_asserted_y_open_no_dispute(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org, lan = await _setup(db)
    a = await facts.assert_fact(db, mkfact(org, lan, "0901", T(1), speaker=lan))
    assert a.outcome == "inserted" and a.status == "asserted" and a.evidence_n == 1
    b = await facts.assert_fact(db, mkfact(org, lan, "0902", T(5), speaker=lan))
    assert b.outcome == "superseded_prev"
    # X đóng theo thời gian sự việc nhưng VẪN asserted (CNS-095); Y mở; không tranh chấp
    assert await rows(db, lan) == [(1, 5, "asserted", "0901"), (5, None, "asserted", "0902")]
    assert await n_disputes(db) == 0
    await assert_no_overlap(db, lan)
    ev = (await db.execute(text("SELECT from_status, to_status FROM mem.fact_status_events WHERE fact_id = :f"),
                           {"f": a.id})).all()
    assert [(e.from_status, e.to_status) for e in ev] == [(None, "asserted"), ("asserted", "asserted")]   # tạo + đóng valid_to
    closed = (await db.execute(text("SELECT closed_recorded_at FROM mem.facts WHERE id = :f"), {"f": a.id})).scalar_one()
    assert closed is not None


async def test_s6_case2_backfill_insert_in_the_middle(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org, lan = await _setup(db)
    await facts.assert_fact(db, mkfact(org, lan, "A", T(1), speaker=lan))
    await facts.assert_fact(db, mkfact(org, lan, "C", T(10), speaker=lan))
    mid = await facts.assert_fact(db, mkfact(org, lan, "B", T(5), speaker=lan))
    assert mid.outcome == "superseded_prev"
    # tin cũ chèn vào giữa, KHÔNG đè sự thật mới hơn
    assert await rows(db, lan) == [(1, 5, "asserted", "A"), (5, 10, "asserted", "B"), (10, None, "asserted", "C")]
    await assert_no_overlap(db, lan)


async def test_s6_case3_forward_then_backward(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org, lan = await _setup(db)
    await facts.assert_fact(db, mkfact(org, lan, "A", T(1), speaker=lan))
    await facts.assert_fact(db, mkfact(org, lan, "B", T(5), speaker=lan))
    await facts.assert_fact(db, mkfact(org, lan, "C", T(3), speaker=lan))
    assert await rows(db, lan) == [(1, 3, "asserted", "A"), (3, 5, "asserted", "C"), (5, None, "asserted", "B")]
    await assert_no_overlap(db, lan)
    now = await facts.at(db, org, lan, "contact_phone", T(20))
    assert [f.object_value for f in now] == ["B"]
    assert [f.object_value for f in await facts.at(db, org, lan, "contact_phone", T(4))] == ["C"]


async def test_s6_case4_before_the_first_version(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org, lan = await _setup(db)
    await facts.assert_fact(db, mkfact(org, lan, "A", T(5), speaker=lan))
    z = await facts.assert_fact(db, mkfact(org, lan, "Z", T(1), speaker=lan))
    assert z.outcome == "inserted_between"
    assert await rows(db, lan) == [(1, 5, "asserted", "Z"), (5, None, "asserted", "A")]   # bản đầu giữ nguyên, mở
    await assert_no_overlap(db, lan)


async def test_s6_case5_replay_twice_no_duplicate_no_evidence_change(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org, lan = await _setup(db)
    raw = uuid.uuid4()
    f1 = await facts.assert_fact(db, mkfact(org, lan, "A", T(1), speaker=lan, raw=raw, ver="v1"))
    f2 = await facts.assert_fact(db, mkfact(org, lan, "A", T(1), speaker=lan, raw=raw, ver="v1"))
    assert f2.id == f1.id and f2.outcome == "duplicate" and f2.evidence_n == 1
    n = (await db.execute(text("SELECT count(*) FROM mem.facts WHERE subject_id = :s"), {"s": lan})).scalar_one()
    assert n == 1
    # replay với extractor_version mới sinh lại đúng nó ⇒ vẫn một dòng
    f3 = await facts.assert_fact(db, mkfact(org, lan, "A", T(1), speaker=lan, raw=raw, ver="v2"))
    assert f3.id == f1.id
    # replay v2 sinh nội dung KHÁC cho cùng tin ⇒ bản cũ superseded, superseded_by = bản mới
    f4 = await facts.assert_fact(db, mkfact(org, lan, "A2", T(1), speaker=lan, raw=raw, ver="v2"))
    old = await facts.get(db, org, f1.id)
    assert old is not None and old.status == "superseded" and old.superseded_by == f4.id
    assert f4.status == "asserted"
    await assert_no_overlap(db, lan)
    # bản mới không sinh lại sự thật nào của bản cũ ⇒ retracted
    other = uuid.uuid4()
    g1 = await facts.assert_fact(db, mkfact(org, lan, "Z", T(30), predicate="lives_in", raw=other, ver="v1", speaker=lan))
    assert await facts.retire_stale_replay(db, org, [other], extractor_version="v2", keep_fact_ids=[]) == 1
    assert (await facts.get(db, org, g1.id)).status == "retracted"  # type: ignore[union-attr]


async def test_s6_case6_concurrent_passes_reverse_order_no_deadlock_same_as_sequential(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    e1, e2 = await mk_entity(db, org, "An"), await mk_entity(db, org, "Binh")
    sm = sessionmaker()

    async def one_pass(order_flip: bool, i: int) -> facts.BatchResult:
        batch = [mkfact(org, e1, f"p{i}", T(1 + i % 20), speaker=e1), mkfact(org, e2, f"q{i}", T(1 + i % 20), speaker=e2)]
        if order_flip:
            batch.reverse()
        async with sm() as s:
            res = await facts.assert_facts(s, batch)
            await s.commit()
        return res

    results = await asyncio.gather(*[one_pass(i % 2 == 1, i) for i in range(16)])
    assert all(not r.errors and r.fact_write_conflict == 0 for r in results), [r.errors for r in results]
    for e in (e1, e2):
        await assert_no_overlap(db, e)
        n = (await db.execute(text("SELECT count(*) FROM mem.facts WHERE subject_id = :s"), {"s": e})).scalar_one()
        assert n == 16
    assert await n_disputes(db) == 0


async def test_s6_case6b_assert_facts_sorts_by_subject_then_predicate(fresh_db, db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    e1, e2 = await mk_entity(db, org, "An"), await mk_entity(db, org, "Binh")
    seen: list[tuple[str, str]] = []

    async def fake(_db, fin):  # type: ignore[no-untyped-def]
        seen.append((str(fin.subject_id), fin.predicate))
        raise FactWriteError("unknown_predicate", "x")

    monkeypatch.setattr(facts, "assert_fact", fake)
    batch = [mkfact(org, e2, "x", predicate="lives_in"), mkfact(org, e1, "x", predicate="works_at"),
             mkfact(org, e2, "x", predicate="contact_phone"), mkfact(org, e1, "x", predicate="lives_in")]
    await facts.assert_facts(db, batch)
    assert seen == sorted(seen)


async def test_s6_case7_three_facts_middle_missing_evidence_others_written(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org, lan = await _setup(db)
    analysis = (await db.execute(text("""INSERT INTO refinery.analyses (org_id, thread_key, stage, status)
                                         VALUES (:o, 'g:x', 'extract', 'ok') RETURNING id"""), {"o": org})).scalar_one()
    batch = [mkfact(org, lan, "HN", T(1), predicate="lives_in"),
             mkfact(org, lan, "0901", T(1), verified=False),          # bằng chứng chưa kiểm ⇒ thiếu
             mkfact(org, lan, "Kho A", T(1), predicate="prefers")]
    res = await facts.assert_facts(db, batch, analysis_id=analysis)
    assert len(res.written) == 2 and [e["code"] for e in res.errors] == ["evidence_missing"]
    await db.commit()
    kept = (await db.execute(text("SELECT predicate FROM mem.facts ORDER BY predicate"))).scalars().all()
    assert kept == ["lives_in", "prefers"]
    errs = (await db.execute(text("SELECT errors FROM refinery.analyses WHERE id = :i"), {"i": analysis})).scalar_one()
    assert errs == [{"code": "evidence_missing", "predicate": "contact_phone", "subject_id": str(lan)}]   # mã + id, không nội dung
    retry = (await db.execute(text("SELECT error_code, attempts FROM refinery.fact_retry"))).one()
    assert (retry.error_code, retry.attempts) == ("evidence_missing", 0)
    # dòng lỗi không để lại mảnh nào (savepoint đã cuộn lại)
    assert (await db.execute(text("SELECT count(*) FROM mem.facts WHERE predicate = 'contact_phone'"))).scalar_one() == 0


async def test_s6_case8_owner_of_account_changes_stk_becomes_disputed(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org, lan = await _setup(db)
    v1 = pay("aaa", "123")
    v2 = pay("bbb", "456")
    a = await facts.assert_fact(db, mkfact(org, lan, v1, T(1), predicate="payment_account", speaker=lan))
    assert a.status == "asserted"
    # chính chủ tài khoản đổi STK ⇒ vẫn disputed (CNS-096), không tự thay thế
    b = await facts.assert_fact(db, mkfact(org, lan, v2, T(5), predicate="payment_account", speaker=lan))
    assert b.status == "disputed" and b.outcome == "disputed"
    prev = await facts.get(db, org, a.id)
    assert prev is not None and prev.status == "disputed" and prev.dispute_id == b.dispute_id
    d = (await disputes.list(db, org))[0]
    assert d.dispute_signal_kind == "rui_ro_lua_dao" and d.predicate == "payment_account" and d.note == "stk_moi"
    assert set(d.fact_ids) == {a.id, b.id} and d.resolved_at is None
    assert await facts.at(db, org, lan, "payment_account", T(20), include_disputed=False) == []
    # giá trị TỪNG thấy quay lại (từ nguồn khác) ⇒ cùng giá trị với một bên đang tranh chấp ⇒ chỉ thêm bằng chứng
    c = await facts.assert_fact(db, mkfact(org, lan, v1, T(7), predicate="payment_account", cls="member"))
    assert c.id == a.id and c.outcome == "evidence_added" and c.evidence_n == 2
    # chỉ Sếp đóng được
    with pytest.raises(disputes.DisputeError):
        await disputes.resolve(db, org, d.id, a.id, by="consolidator")
    got = await disputes.resolve(db, org, d.id, a.id, by="owner")
    assert got.resolved_by == "owner" and got.winner_fact_id == a.id
    assert (await facts.get(db, org, a.id)).status == "asserted"  # type: ignore[union-attr]
    assert (await facts.get(db, org, b.id)).status == "superseded"  # type: ignore[union-attr]
    await assert_no_overlap(db, lan, "payment_account")


async def test_s6_case8b_returning_to_previously_seen_stk_noted(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org, lan = await _setup(db)
    v1, v2 = pay("aaa", "123"), pay("bbb", "456")
    a = await facts.assert_fact(db, mkfact(org, lan, v1, T(1), predicate="payment_account", speaker=lan))
    b = await facts.assert_fact(db, mkfact(org, lan, v2, T(3), predicate="payment_account", speaker=lan))
    d0 = (await disputes.list(db, org))[0]
    await disputes.resolve(db, org, d0.id, b.id, by="owner")          # Sếp xác minh STK mới
    c = await facts.assert_fact(db, mkfact(org, lan, v1, T(9), predicate="payment_account", speaker=lan))
    assert c.status == "disputed"
    d1 = [d for d in await disputes.list(db, org) if d.resolved_at is None][0]
    assert d1.note == "stk_quay_lai_cu" and a.id != c.id


async def test_s6_case9_merge_two_people_with_conflicting_single_facts(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    a, b = await mk_entity(db, org, "Bảy Kho"), await mk_entity(db, org, "Anh Bảy")
    fa = await facts.assert_fact(db, mkfact(org, a, "0901", T(1), speaker=a))
    fb = await facts.assert_fact(db, mkfact(org, b, "0902", T(2), speaker=b))
    same_a = await facts.assert_fact(db, mkfact(org, a, "HN", T(1), predicate="lives_in", speaker=a))
    same_b = await facts.assert_fact(db, mkfact(org, b, "hn", T(3), predicate="lives_in", speaker=b))    # cùng giá trị
    res = await entities.merge(db, org, a, b)
    assert res.disputes == 1 and res.repointed == []
    for f in (fa, fb):
        assert (await facts.get(db, org, f.id)).status == "disputed"  # type: ignore[union-attr]
    for f in (same_a, same_b):                                          # giá trị giống nhau ⇒ không tranh chấp
        assert (await facts.get(db, org, f.id)).status == "asserted"  # type: ignore[union-attr]
    d = (await disputes.list(db, org))[0]
    assert d.subject_id == b and d.predicate == "contact_phone" and set(d.fact_ids) == {fa.id, fb.id}
    # chạy lại đối soát: không mở thêm tranh chấp
    assert await facts.reconcile_entity(db, org, b) == 0
    assert await n_disputes(db) == 1
    # sự thật mới về entity đã gộp ghi vào chủ thể đích
    n = await facts.assert_fact(db, mkfact(org, a, "x", T(9), predicate="prefers"))
    assert n.subject_id == b


async def test_s6_case10_two_level_merge_compresses_path(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    a, b, c = [await mk_entity(db, org, n) for n in ("A", "B", "C")]
    await entities.merge(db, org, a, b)
    r = await entities.merge(db, org, b, c)
    assert r.repointed == [a]
    assert (await entities.get(db, a)).merged_into == c  # type: ignore[union-attr]
    assert (await entities.get(db, b)).merged_into == c  # type: ignore[union-attr]
    assert await entities.canonical(db, a) == c and await entities.canonical(db, b) == c
    assert set(await entities.members(db, a)) == {a, b, c}
    # đích gộp không được có merged_into
    d = await mk_entity(db, org, "D")
    with pytest.raises(entities.EntityError):
        await entities.merge(db, org, d, b)
    # tách B khỏi C: trả A về B
    await entities.unmerge(db, org, b, r.repointed)
    assert (await entities.get(db, b)).merged_into is None  # type: ignore[union-attr]
    assert (await entities.get(db, a)).merged_into == b  # type: ignore[union-attr]
    assert await entities.canonical(db, a) == b


async def test_s6_db_trigger_blocks_one_step_violation(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    a, b, c = [await mk_entity(db, org, n) for n in ("A", "B", "C")]
    await db.execute(text("UPDATE mem.entities SET merged_into = :b WHERE id = :a"), {"a": a, "b": b})
    with pytest.raises(Exception, match="đích gộp|đang là đích"):
        async with db.begin_nested():
            await db.execute(text("UPDATE mem.entities SET merged_into = :c WHERE id = :b"), {"b": b, "c": c})   # b đang là đích của a


async def test_s6_many_facts_concurrently_no_violation_no_write_conflict(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    """Bản rút gọn của "10k facts ghi đồng thời không vi phạm" (bản 10k: test_s6_10k_facts_slow, đánh dấu slow)."""
    org = await mk_org(db)
    subs = [await mk_entity(db, org, f"N{i}") for i in range(6)]
    sm = sessionmaker()

    async def worker(w: int) -> int:
        conflicts = 0
        async with sm() as s:
            for i in range(40):
                sub = subs[(w + i) % len(subs)]
                res = await facts.assert_facts(s, [mkfact(org, sub, f"v{w}-{i}", T(1 + (w * 7 + i) % 28), speaker=sub)])
                conflicts += res.fact_write_conflict + len(res.errors)
                await s.commit()
        return conflicts

    assert sum(await asyncio.gather(*[worker(w) for w in range(8)])) == 0
    for s_ in subs:
        await assert_no_overlap(db, s_)


@pytest.mark.slow
async def test_s6_10k_facts_slow(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    subs = [await mk_entity(db, org, f"N{i}") for i in range(20)]
    sm = sessionmaker()

    async def worker(w: int) -> int:
        bad = 0
        async with sm() as s:
            for i in range(1250):
                sub = subs[(w * 3 + i) % len(subs)]
                res = await facts.assert_facts(s, [mkfact(org, sub, f"v{w}-{i}", T(1 + (w * 11 + i) % 28), speaker=sub)])
                bad += res.fact_write_conflict + len(res.errors)
                await s.commit()          # mỗi lượt một giao dịch ngắn (khoá tư vấn nhả lúc commit), như L2
        return bad

    assert sum(await asyncio.gather(*[worker(w) for w in range(8)])) == 0
    for s_ in subs:
        await assert_no_overlap(db, s_)
