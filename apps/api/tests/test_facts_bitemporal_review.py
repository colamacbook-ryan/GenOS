# ruff: noqa: E501
"""Sửa theo review opus của L1 (2 🔴, 4 🟠, các 🟡 rẻ): mỗi mục có test tái hiện — đỏ trước khi sửa, xanh sau khi sửa."""

import asyncio
import hashlib
import json
import uuid

import pytest
from sqlalchemy import text

from gh.biz.mem import aliases, disputes, entities, facts, forget, mentions
from gh.biz.mem.facts import FactWriteError
from gh.db import sessionmaker
from tests.conftest import OWNER, Api
from tests.mem_helpers import T, assert_no_overlap, mk_entity, mk_org, mk_person_row, mkfact, n_disputes, pay, rows
from tests.phase2 import org_id


async def _user(db, org):  # type: ignore[no-untyped-def]
    uid = (await db.execute(text("""INSERT INTO core.users (org_id, email, display_name, password_hash)
                                    VALUES (:o, :e, 'Sếp', 'x') RETURNING id"""), {"o": org, "e": f"{uuid.uuid4().hex[:6]}@x.vn"})).scalar_one()
    await db.commit()
    return uid


# ─── 🔴1 chèn lùi thời gian payment_account ────────────────────────────────

async def test_review_r1_payment_account_backward_insert_is_disputed(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan = await mk_entity(db, org)
    b = await facts.assert_fact(db, mkfact(org, lan, pay("b"), T(10), predicate="payment_account", speaker=lan))
    a = await facts.assert_fact(db, mkfact(org, lan, pay("a"), T(1), predicate="payment_account", speaker=lan))   # G1 trễ, lùi thời gian
    assert a.status == "disputed" and a.outcome == "disputed"
    assert (await facts.get(db, org, b.id)).status == "disputed"  # type: ignore[union-attr]
    assert await n_disputes(db, lan) == 1
    d = (await disputes.list(db, org))[0]
    assert d.dispute_signal_kind == "rui_ro_lua_dao" and set(d.fact_ids) == {a.id, b.id}
    assert await facts.at(db, org, lan, "payment_account", T(20), include_disputed=False) == []


async def test_review_r1_payment_account_closed_prev_with_other_value_is_disputed(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan = await mk_entity(db, org)
    c = await facts.assert_fact(db, mkfact(org, lan, pay("c"), T(1), predicate="payment_account", speaker=lan))
    await db.execute(text("UPDATE mem.facts SET valid_to = :t WHERE id = :i"), {"t": T(3), "i": c.id})      # đã đóng
    d = await facts.assert_fact(db, mkfact(org, lan, pay("d"), T(5), predicate="payment_account", speaker=lan))
    assert d.status == "disputed"
    assert (await facts.get(db, org, c.id)).status == "disputed"  # type: ignore[union-attr]
    # cả prev lẫn next khác giá trị: chèn giữa ⇒ vào cùng một tranh chấp
    e = await facts.assert_fact(db, mkfact(org, lan, pay("e"), T(4), predicate="payment_account", speaker=lan))
    assert e.status == "disputed" and await n_disputes(db, lan) == 1


async def test_review_r1_payment_account_same_value_backward_is_not_a_dispute(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan = await mk_entity(db, org)
    b = await facts.assert_fact(db, mkfact(org, lan, pay("b"), T(10), predicate="payment_account", speaker=lan))
    same = await facts.assert_fact(db, mkfact(org, lan, pay("b"), T(1), predicate="payment_account", speaker=lan))
    assert same.status == "asserted" and await n_disputes(db, lan) == 0 and b.id is not None
    await assert_no_overlap(db, lan, "payment_account")


# ─── 🔴2 / 🟠3 STK nguyên văn không lọt vào sổ ─────────────────────────────

async def test_review_r2_correct_validates_payment_value(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan = await mk_entity(db, org)
    user = await _user(db, org)
    f = await facts.assert_fact(db, mkfact(org, lan, pay("a"), T(1), predicate="payment_account"))
    for bad in ({"stk": "0123456789012"}, {"digest": "0123456789012"}, {**pay("x"), "bank": "VCB 0123456789"}, "0123456789012"):
        with pytest.raises(FactWriteError) as ei:
            await facts.correct(db, org, f.id, actor_user_id=user, new_value=bad)
        assert ei.value.code == "invalid_fact", bad
    assert (await facts.get(db, org, f.id)).status == "asserted"  # type: ignore[union-attr]      # không đụng gì
    ok = await facts.correct(db, org, f.id, actor_user_id=user, new_value=pay("b", "999"))
    assert ok.status == "owner_confirmed" and ok.object_value["last3"] == "999"


async def test_review_r2_correct_route_returns_422_for_raw_account_number(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    lan = await mk_entity(db, org)
    f = await facts.assert_fact(db, mkfact(org, lan, pay("a"), T(1), predicate="payment_account"))
    await db.commit()
    r = await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    assert r.status_code == 200
    r = await api.send("POST", f"/facts/{f.id}/correct", {"new_value": {"stk": "0123456789012"}})
    assert r.status_code == 422, r.text
    assert (await db.execute(text("SELECT count(*) FROM mem.facts WHERE object_value::text LIKE '%0123456789012%'"))).scalar_one() == 0


async def test_review_r3_real_digests_are_accepted_every_time_and_format_is_strict(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    # ~60% sha256 hex có chuỗi ≥ 7 chữ số liền: trước đây bị bỏ im lặng. 200 digest thật phải đều qua.
    for i in range(200):
        lan = await mk_entity(db, org, f"N{i}")
        d = {"digest": hashlib.sha256(str(i).encode()).hexdigest(), "last3": f"{i % 1000:03d}"}
        assert (await facts.assert_fact(db, mkfact(org, lan, d, T(1), predicate="payment_account"))).status == "asserted"
    lan = await mk_entity(db, org, "X")
    for bad in ({"digest": "abc"}, {"digest": "G" * 64}, {"digest": "a" * 63}, {"digest": "A" * 64}):
        with pytest.raises(FactWriteError) as ei:
            await facts.assert_fact(db, mkfact(org, lan, bad, predicate="payment_account"))
        assert ei.value.code == "invalid_fact", bad


async def test_review_r3_payment_digest_is_keyed_and_normalized() -> None:
    a, b = facts.payment_digest("0123 456-789"), facts.payment_digest("0123456789")
    assert a == b and len(a) == 64 and int(a, 16) >= 0
    assert a != hashlib.sha256(b"0123456789").hexdigest()                 # khoá app, không phải sha256 trần (không dò ngược được)
    assert facts.payment_digest("0123456788") != a
    with pytest.raises(ValueError):
        facts.payment_digest("abc")


# ─── 🟠4 unmerge hoàn tác tranh chấp do reconcile ──────────────────────────

async def test_review_r4_unmerge_restores_facts_and_closes_reconcile_disputes(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    a, b = await mk_entity(db, org, "A"), await mk_entity(db, org, "B")
    fa = await facts.assert_fact(db, mkfact(org, a, "0901", T(1), speaker=a))
    fb = await facts.assert_fact(db, mkfact(org, b, "0902", T(2), speaker=b))
    res = await entities.merge(db, org, a, b)
    assert res.disputes == 1 and (await facts.get(db, org, fa.id)).status == "disputed"  # type: ignore[union-attr]
    await entities.unmerge(db, org, a, res.repointed, res.reconciled)
    for f in (fa, fb):
        got = await facts.get(db, org, f.id)
        assert got is not None and got.status == "asserted" and got.dispute_id is None
    d = (await disputes.list(db, org, open_only=False))[0]
    assert d.resolved_by == "unmerge" and d.resolved_at is not None
    assert await disputes.expire_stale(db, org, days=0) == 0                # hết hạn không còn đụng tới
    assert len(await facts.at(db, org, a, "contact_phone", T(20))) == 1 and len(await facts.at(db, org, b, "contact_phone", T(20))) == 1


async def test_review_r4_identity_snapshot_roundtrip_via_merge_persons(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    pa, pb = await mk_person_row(db, org, "An"), await mk_person_row(db, org, "Bình")
    ea, eb = await entities.ensure_person(db, org, pa), await entities.ensure_person(db, org, pb)
    fa = await facts.assert_fact(db, mkfact(org, ea.id, "0901", T(1), speaker=ea.id))
    fb = await facts.assert_fact(db, mkfact(org, eb.id, "0902", T(2), speaker=eb.id))
    await db.execute(text("UPDATE core.persons SET merged_into_id = :k WHERE id = :d"), {"k": pa, "d": pb})
    snap = await entities.merge_persons(db, org, pb, pa)
    assert snap is not None and snap["reconciled"]["facts"]
    await entities.unmerge_persons(db, org, snap)
    assert {(await facts.get(db, org, f.id)).status for f in (fa, fb)} == {"asserted"}  # type: ignore[union-attr]


async def test_review_r4_unmerge_keeps_changes_made_after_merge(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    user = await _user(db, org)
    a, b = await mk_entity(db, org, "A"), await mk_entity(db, org, "B")
    fa = await facts.assert_fact(db, mkfact(org, a, "0901", T(1), speaker=a))
    fb = await facts.assert_fact(db, mkfact(org, b, "0902", T(2), speaker=b))
    res = await entities.merge(db, org, a, b)
    d = (await disputes.list(db, org))[0]
    await disputes.resolve(db, org, d.id, fb.id, by="owner", user_id=user)    # Sếp đã chọn bên thắng: không được lật lại
    await entities.unmerge(db, org, a, res.repointed, res.reconciled)
    assert (await facts.get(db, org, fa.id)).status == "superseded"  # type: ignore[union-attr]
    assert (await facts.get(db, org, fb.id)).status == "owner_confirmed"  # type: ignore[union-attr]


# ─── 🟠5 replay không đảo ngược bản Sếp đã sửa ─────────────────────────────

async def test_review_r5_replay_does_not_undo_owner_correction(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    lan = await mk_entity(db, org)
    user = await _user(db, org)
    raw = uuid.uuid4()
    orig = await facts.assert_fact(db, mkfact(org, lan, "khách", T(1), predicate="prefers", raw=raw, ver="v1"))
    fixed = await facts.correct(db, org, orig.id, actor_user_id=user, new_value="em họ")
    again = await facts.assert_fact(db, mkfact(org, lan, "khách", T(1), predicate="prefers", raw=raw, ver="v2"))       # replay sinh lại bản cũ
    assert again.id == fixed.id and again.outcome == "duplicate"
    assert (await facts.get(db, org, orig.id)).status == "superseded"  # type: ignore[union-attr]
    assert (await facts.get(db, org, fixed.id)).status == "owner_confirmed"  # type: ignore[union-attr]
    assert (await db.execute(text("SELECT count(*) FROM mem.facts WHERE status = 'asserted'"))).scalar_one() == 0
    assert await rows(db, lan, "prefers", ("owner_confirmed",)) == [(1, None, "owner_confirmed", "em họ")]


# ─── 🟡 erase, quên người, org, khoá ───────────────────────────────────────

async def test_review_erase_removes_attributed_facts_candidate_mentions_and_retry(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    pid = await mk_person_row(db, org, "Hùng")
    hung = (await entities.ensure_person(db, org, pid)).id
    lan = await mk_entity(db, org, "Lan")
    f = await facts.assert_fact(db, mkfact(org, lan, "nợ 5 triệu", T(1), predicate="owes", cls="outsider", speaker=hung))
    keep = await facts.assert_fact(db, mkfact(org, lan, "HN", T(1), predicate="lives_in", speaker=lan))
    await aliases.add(db, org, hung, "anh Hùng Kho", source="mention")
    unit = uuid.uuid4()
    await mentions.link(db, org, meaning_unit_id=unit, meaning_observed_at=T(2), surface="Hùng Kho")          # candidate (gần đúng)
    await db.execute(text("INSERT INTO mem.mentions (org_id, meaning_unit_id, meaning_observed_at, surface, method, status) "
                          "VALUES (:o, :u, now(), 'anh hùng kho', 'alias_exact', 'unresolved')"), {"o": org, "u": unit})
    await db.execute(text("INSERT INTO mem.mentions (org_id, meaning_unit_id, meaning_observed_at, surface, method, status) "
                          "VALUES (:o, :u, now(), 'ông Chín', 'alias_exact', 'unresolved')"), {"o": org, "u": unit})
    for k in ("speaker_entity_id", "attributed_to"):
        await db.execute(text("INSERT INTO refinery.fact_retry (org_id, fact_in, error_code) VALUES (:o, CAST(:j AS jsonb), 'deadlock')"),
                         {"o": org, "j": json.dumps({k: str(hung), "subject_id": str(lan)})})
    await db.commit()
    c = await forget.erase_entity(db, org, pid)
    await db.commit()
    assert (await db.execute(text("SELECT id FROM mem.facts WHERE org_id = :o"), {"o": org})).scalars().all() == [keep.id]
    assert c["facts"] == 1 and f.id is not None
    surfaces = (await db.execute(text("SELECT surface FROM mem.mentions"))).scalars().all()
    assert surfaces == ["ông Chín"]                                                                          # chỉ mention liên quan Hùng bị xoá
    assert (await db.execute(text("SELECT count(*) FROM refinery.fact_retry"))).scalar_one() == 0


async def test_review_forgotten_entity_rejects_alias_and_mention_writes(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    pid = await mk_person_row(db, org, "Hùng")
    hung = (await entities.ensure_person(db, org, pid)).id
    await forget.erase_entity(db, org, pid)
    with pytest.raises(entities.EntityError):
        await aliases.add(db, org, hung, "anh Hùng")
    for kw in ({"sender_entity_id": hung}, {"entity_id": hung}):
        with pytest.raises(entities.EntityError):
            await mentions.link(db, org, meaning_unit_id=uuid.uuid4(), meaning_observed_at=T(1), surface="Hùng", **kw)
    assert (await db.execute(text("SELECT count(*) FROM mem.aliases WHERE entity_id = :e"), {"e": hung})).scalar_one() == 0


async def test_review_org_checks_on_every_entity_reference(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org, other = await mk_org(db), await mk_org(db, "Tổ chức khác")
    user = await _user(db, org)
    lan, foreign = await mk_entity(db, org), await mk_entity(db, other, "Người nơi khác")
    corp = await mk_entity(db, other, "Công ty khác", "org")
    for kw in ({"speaker": foreign}, {"attributed_to": foreign}):
        with pytest.raises(FactWriteError) as ei:
            await facts.assert_fact(db, mkfact(org, lan, "x", predicate="prefers", cls="outsider", **kw))
        assert ei.value.code == "invalid_fact", kw
    with pytest.raises(FactWriteError) as e2:
        await facts.assert_fact(db, mkfact(org, lan, None, predicate="works_at", object_entity=corp))
    assert e2.value.code == "invalid_fact"
    with pytest.raises(entities.EntityError):
        await aliases.add(db, other if False else org, foreign, "x")
    with pytest.raises(entities.EntityError):
        await mentions.link(db, org, meaning_unit_id=uuid.uuid4(), meaning_observed_at=T(1), surface="x", entity_id=foreign)
    ok = await facts.assert_fact(db, mkfact(org, lan, "HN", T(1), predicate="lives_in", speaker=lan))
    own_corp = await mk_entity(db, org, "Công ty mình", "org")
    f = await facts.assert_fact(db, mkfact(org, lan, None, T(1), predicate="works_at", object_entity=own_corp, speaker=lan))
    with pytest.raises(FactWriteError) as e3:
        await facts.correct(db, org, f.id, actor_user_id=user, new_object_id=corp)
    assert e3.value.code == "invalid_fact" and ok.id is not None
    assert (await facts.get(db, org, f.id)).status == "asserted"  # type: ignore[union-attr]


async def test_review_correct_and_assert_take_locks_in_same_order_no_deadlock(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    user = await _user(db, org)
    lan = await mk_entity(db, org)
    sm = sessionmaker()
    base = await facts.assert_fact(db, mkfact(org, lan, "v0", T(1), speaker=lan))
    await db.commit()

    async def corrector(fid: uuid.UUID) -> str:
        async with sm() as s:
            try:
                await facts.correct(s, org, fid, actor_user_id=user, new_value=f"c{uuid.uuid4().hex[:4]}")
                await s.commit()
                return "ok"
            except FactWriteError as e:
                await s.rollback()
                return e.code

    async def writer(i: int) -> str:
        async with sm() as s:
            res = await facts.assert_facts(s, [mkfact(org, lan, f"w{i}", T(2 + i % 20), speaker=lan)])
            await s.commit()
            return "ok" if not res.errors else res.errors[0]["code"]

    out = await asyncio.gather(corrector(base.id), *[writer(i) for i in range(10)], corrector(base.id))
    assert "deadlock" not in out, out
    await assert_no_overlap(db, lan)


async def test_review_erase_races_with_concurrent_writer_never_leaves_facts(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    pid = await mk_person_row(db, org, "Hùng")
    hung = (await entities.ensure_person(db, org, pid)).id
    await db.commit()
    sm = sessionmaker()

    async def writer() -> None:
        async with sm() as s:
            for i in range(15):
                try:
                    await facts.assert_facts(s, [mkfact(org, hung, f"v{i}", T(1 + i), predicate="prefers", speaker=hung)])
                    await s.commit()
                except Exception:
                    await s.rollback()

    async def eraser() -> None:
        await asyncio.sleep(0.05)
        async with sm() as s:
            await forget.erase_entity(s, org, pid)
            await s.commit()

    await asyncio.gather(writer(), eraser())
    left = (await db.execute(text("SELECT count(*) FROM mem.facts WHERE subject_id = :s"), {"s": hung})).scalar_one()
    assert left == 0                                                       # ghi sau khi quên bị từ chối; ghi trước đã bị xoá


# ─── vòng review 2 ─────────────────────────────────────────────────────────

async def test_review2_b_replay_keeps_sibling_multi_facts_of_same_message(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    """Vị từ đa trị: Sếp sửa 'gạo' ⇒ replay sinh 'muối' phải trả CHÍNH 'muối', không bị 'đường' của Sếp thế chỗ rồi bị rút mất."""
    org = await mk_org(db)
    lan = await mk_entity(db, org)
    user = await _user(db, org)
    raw = uuid.uuid4()
    g = await facts.assert_fact(db, mkfact(org, lan, "gạo", T(1), raw=raw, ver="v1", predicate="needs"))
    m = await facts.assert_fact(db, mkfact(org, lan, "muối", T(1), raw=raw, ver="v1", predicate="needs"))
    d = await facts.correct(db, org, g.id, actor_user_id=user, new_value="đường")
    r1 = await facts.assert_fact(db, mkfact(org, lan, "gạo", T(1), raw=raw, ver="v2", predicate="needs"))
    r2 = await facts.assert_fact(db, mkfact(org, lan, "muối", T(1), raw=raw, ver="v2", predicate="needs"))
    assert r1.id == d.id and r1.object_value == "đường"                       # bản Sếp sửa được giữ
    assert r2.id == m.id and r2.object_value == "muối" and r2.outcome == "duplicate"
    retired = await facts.retire_stale_replay(db, org, [raw], extractor_version="v2", keep_fact_ids=[r1.id, r2.id])
    assert retired == 0
    assert await rows(db, lan, "needs", ("asserted", "owner_confirmed")) == [(1, None, "asserted", "muối"), (1, None, "owner_confirmed", "đường")]


async def test_review2_a_unmerge_rehomes_fact_that_joined_dispute_after_merge(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    a, b = await mk_entity(db, org, "An"), await mk_entity(db, org, "Bình")
    f1 = await facts.assert_fact(db, mkfact(org, a, "0901", T(1)))
    f2 = await facts.assert_fact(db, mkfact(org, b, "0902", T(2)))
    res = await entities.merge(db, org, b, a)
    f3 = await facts.assert_fact(db, mkfact(org, a, "0903", T(5)))                 # gia nhập tranh chấp SAU khi gộp
    assert f3.status == "disputed"
    await entities.unmerge(db, org, b, res.repointed, res.reconciled)
    assert (await facts.get(db, org, f2.id)).status == "asserted"  # type: ignore[union-attr]       # người b về lại bình thường
    # 0901 và 0903 đều là của An và chồng khoảng ⇒ tranh chấp ĐÚNG chủ thể An, không kẹt ở tranh chấp cũ
    assert (await facts.get(db, org, f1.id)).status == "disputed" and (await facts.get(db, org, f3.id)).status == "disputed"  # type: ignore[union-attr]
    open_d = [d for d in await disputes.list(db, org) if d.resolved_at is None]
    assert len(open_d) == 1 and open_d[0].subject_id == a and set(open_d[0].fact_ids) == {f1.id, f3.id}
    assert b not in {open_d[0].subject_id}
    stuck = (await db.execute(text("""SELECT count(*) FROM mem.facts f JOIN mem.disputes d ON d.id = f.dispute_id
                                      WHERE f.status = 'disputed' AND d.resolved_at IS NOT NULL"""))).scalar_one()
    assert stuck == 0


async def test_review2_a_unmerge_rehomes_joined_fact_into_plain_asserted_when_no_overlap(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    a, b = await mk_entity(db, org, "An"), await mk_entity(db, org, "Bình")
    await facts.assert_fact(db, mkfact(org, a, "0901", T(1), speaker=a))
    await facts.assert_fact(db, mkfact(org, b, "0902", T(2), speaker=b))
    res = await entities.merge(db, org, b, a)
    other = await facts.assert_fact(db, mkfact(org, a, "Hà Nội", T(3), predicate="lives_in", speaker=a))   # khác vị từ: không dính tranh chấp
    assert other.status == "asserted"
    await entities.unmerge(db, org, b, res.repointed, res.reconciled)
    assert {r[2] for r in await rows(db, a)} == {"asserted"} and {r[2] for r in await rows(db, b)} == {"asserted"}
    assert [d for d in await disputes.list(db, org) if d.resolved_at is None] == []


class _FakeOrig(Exception):
    sqlstate = "40P01"


def _deadlock() -> Exception:
    from sqlalchemy.exc import DBAPIError

    return DBAPIError("stmt", {}, _FakeOrig("deadlock detected"))


async def test_review2_deadlock_retried_once_then_409_for_erase_and_merge(fresh_db, db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from gh.errors import ApiError

    org = await mk_org(db)
    pid = await mk_person_row(db, org, "Hùng")
    await entities.ensure_person(db, org, pid)
    calls = {"n": 0}
    real = forget._erase

    async def flaky(*a, **kw):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise _deadlock()
        return await real(*a, **kw)

    monkeypatch.setattr(forget, "_erase", flaky)
    out = await forget.erase_entity(db, org, pid)                      # lần 1 deadlock, lần 2 qua
    assert calls["n"] == 2 and out["entities"] == 1

    async def always(*a, **kw):  # type: ignore[no-untyped-def]
        raise _deadlock()

    monkeypatch.setattr(forget, "_erase", always)
    with pytest.raises(ApiError) as ei:
        await forget.erase_entity(db, org, pid)
    assert ei.value.status == 409 and ei.value.code == "MEM_BUSY" and "40P01" in str(ei.value.detail)
    # gộp người: cùng cơ chế
    pa, pb = await mk_person_row(db, org, "A1"), await mk_person_row(db, org, "B1")
    await entities.ensure_person(db, org, pa)
    await entities.ensure_person(db, org, pb)
    monkeypatch.setattr(entities, "merge", always)
    with pytest.raises(ApiError) as e2:
        await entities.merge_persons(db, org, pb, pa)
    assert e2.value.status == 409
    other = await entities.retry_deadlock(db, lambda: asyncio.sleep(0, "ok"))
    assert other == "ok"
    with pytest.raises(ValueError):                                    # lỗi khác không bị nuốt
        async def boom() -> None:
            raise ValueError("x")

        await entities.retry_deadlock(db, boom)


def test_review2_normalization_notes_are_code_only_and_capped() -> None:
    from gh.refinery import brain_contract as bc
    from tests.test_brain_contract_v0176 import FULL

    d = json.loads(json.dumps(FULL))
    d["units"][0]["normalization_notes"] = ["model tự khai"]
    d["units"][0]["status_message_id"] = "m_" + "x" * 120                       # id dài hợp lệ (≤ 128) nhưng ngoài quotes
    out = bc.BrainOut.model_validate(d).units[0]
    assert out.normalization_notes == [("status_message_id_dropped:m_" + "x" * 120)[:200]]   # lời model bỏ; note cắt ≤ 200
    assert all(len(n) <= 200 for n in out.normalization_notes)
    d["units"][0]["status_message_id"] = "m_" + "x" * 200
    with pytest.raises(Exception, match="at most 128|string_too_long"):
        bc.BrainOut.model_validate(d)
    d["units"][0]["status_message_id"] = None
    assert bc.BrainOut.model_validate(d).units[0].normalization_notes == []     # sạch ⇒ không còn lời model


async def test_review2_mentions_link_takes_share_lock_on_entity(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    import psycopg

    from tests.conftest import PG

    org = await mk_org(db)
    e = await mk_entity(db, org, "Chị Tư")
    await mentions.link(db, org, meaning_unit_id=uuid.uuid4(), meaning_observed_at=T(1), surface="x", sender_entity_id=e)
    with psycopg.connect(f"{PG}/{fresh_db}", autocommit=True) as c:             # giao dịch của db còn mở ⇒ FOR UPDATE phải bị chặn
        with pytest.raises(psycopg.errors.LockNotAvailable):
            c.execute("SELECT id FROM mem.entities WHERE id = %s FOR NO KEY UPDATE NOWAIT", (e,))   # FK chỉ giữ KEY SHARE; FOR SHARE mới chặn được
        c.execute("SELECT id FROM mem.entities WHERE id = %s FOR SHARE NOWAIT", (e,))   # share với share thì được
    await db.rollback()
