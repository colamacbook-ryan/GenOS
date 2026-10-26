# ruff: noqa: E501
"""v0.1.77 (gói P2) — `person_role` MỘT nơi dựng vai người (spec r7.2 §4.2.1 (c′), bản 3 §5 dòng P2, L-96): so khớp `fraud.Who.rel()` (không chạm
fraud.py), `generic.who_rel` giữ hành vi, `audience_n`, `is_customer` một nguồn, `staff_owner_ref` (`staff_owned`)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels, person_role
from gh.biz.intel.detectors import fraud
from gh.biz.intel.detectors import generic as g
from gh.biz.mem import entities, facts
from tests.mem_helpers import mk_org, mkfact
from tests.test_owner_profile_support import mk_person, mk_raw, mk_world

NOW = datetime(2026, 10, 23, 14, 0, tzinfo=levels.VN)


async def _mk(db: AsyncSession, w: Any, name: str, *, ptype: str | None = None, staff_confirmed: bool = False, member: bool = False) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """(person, identity, entity)."""
    pid, iid = await mk_person(db, w.org, w.channel, name)
    if ptype:
        await db.execute(text("UPDATE core.persons SET person_type = :t WHERE id = :p"), {"t": ptype, "p": pid})
    if staff_confirmed:
        await db.execute(text("""UPDATE core.persons SET person_type = 'staff', attrs = attrs || jsonb_build_object('person_type_locked', true, 'staff_marked_at', CAST(:t AS text))
                                 WHERE id = :p"""), {"t": (NOW - timedelta(days=1)).isoformat(), "p": pid})
    if member:
        await db.execute(text("INSERT INTO core.group_members (group_id, person_id) VALUES (:g, :p)"), {"g": w.group, "p": pid})
    ent = (await entities.ensure_person(db, w.org, pid)).id
    await db.commit()
    return pid, iid, ent


async def _fact(db: AsyncSession, w: Any, ent: uuid.UUID, predicate: str) -> None:
    await facts.assert_fact(db, mkfact(w.org, ent, predicate=predicate, object_entity=w.owner, at=NOW - timedelta(days=9)))
    await db.commit()


# ─── generic.who_rel giữ hành vi (thân dời sang person_role) ─────────────────

def test_who_rel_is_person_role_rel_from_who_unchanged() -> None:
    ent = uuid.uuid4()
    sets = g.OrgSets(owner=None, staff=frozenset({ent}), customers=frozenset(), key_relations=frozenset({ent}))
    w = g.Who(ent, uuid.uuid4(), "customer")
    rel = g.who_rel(w, sets, thread_member=True)
    assert rel == person_role.rel_from_who(w, sets, thread_member=True)
    assert rel.team and rel.customer and rel.owner_key_relation and rel.thread_member and rel.present_in_thread and not rel.is_owner
    plain = g.who_rel(g.Who(uuid.uuid4(), None, None), sets)
    assert plain == levels.PersonRel(ref=plain.ref) and plain.ref.startswith("e_")        # không tra cứu ⇒ không vai nào
    assert g.who_rel(w, sets) == g.person_rel(ent, sets, ref=w.ref).model_copy(update={"customer": True, "team": True})   # đúng thân cũ


# ─── L-96: fraud.Who.rel() == person_role cho cùng người ─────────────────────

async def test_person_role_matches_fraud_who_rel_for_same_person(db: AsyncSession) -> None:
    w = await mk_world(db, await mk_org(db))
    cust_type, _, e1 = await _mk(db, w, "Khách loại", ptype="customer", member=True)
    cust_fact, _, e2 = await _mk(db, w, "Khách fact")
    await _fact(db, w, e2, "customer_of")
    staff, _, e3 = await _mk(db, w, "NV", staff_confirmed=True, member=True)
    key, _, e4 = await _mk(db, w, "Người quan trọng", member=True)
    plain, _, _e5 = await _mk(db, w, "Người thường")
    await db.execute(text("""UPDATE core.persons SET attrs = attrs || '{"is_owner": true}'::jsonb WHERE id = :p"""), {"p": w.owner_person})
    await db.commit()
    sets = await g.org_sets(db, w.org)
    sets = g.OrgSets(sets.owner, sets.staff, sets.customers, frozenset({e4}), sets.owners)
    owners = set(await g.paired_owner_person_ids(db, w.org))
    assert w.owner_person in owners
    for pid in (cust_type, cust_fact, staff, key, plain, w.owner_person):
        for gid in (w.group, None):
            fw = await fraud._who(db, w.org, pid, key_rel=sets.key_relations, owner_people=owners, group_id=gid)
            for tm in (True, False):
                assert await person_role.person_role(db, w.org, person_id=pid, group_id=gid, thread_member=tm, sets=sets) == fw.rel(thread_member=tm), (pid, gid, tm)
    # theo thực thể: cùng kết quả như theo người
    assert await person_role.person_role(db, w.org, entity_id=e1, group_id=w.group, sets=sets) == await person_role.person_role(db, w.org, person_id=cust_type, group_id=w.group, sets=sets)
    r = await person_role.person_role(db, w.org, person_id=staff, group_id=w.group, sets=sets)
    assert r.team and not r.customer and r.present_in_thread
    r = await person_role.person_role(db, w.org, person_id=key, group_id=w.group, sets=sets)
    assert r.owner_key_relation
    assert (await person_role.person_role(db, w.org, person_id=w.owner_person, group_id=None, sets=sets)).is_owner


async def test_person_role_unknown_entity_has_no_role(db: AsyncSession) -> None:
    w = await mk_world(db, await mk_org(db))
    ghost = uuid.uuid4()
    r = await person_role.person_role(db, w.org, entity_id=ghost)
    assert r.ref == f"e_{ghost}" and not (r.customer or r.team or r.is_owner or r.owner_key_relation)


# ─── is_customer: một nguồn customer_of_owner ────────────────────────────────

async def test_is_customer_person_type_or_fact_single_source(db: AsyncSession) -> None:
    w = await mk_world(db, await mk_org(db))
    a, _, ea = await _mk(db, w, "A", ptype="customer")
    b, _, eb = await _mk(db, w, "B")
    await _fact(db, w, eb, "customer_of")
    c, _, ec = await _mk(db, w, "C")
    assert await person_role.is_customer(db, w.org, a, ea) and await person_role.is_customer(db, w.org, b, eb)
    assert not await person_role.is_customer(db, w.org, c, ec) and not await person_role.is_customer(db, w.org, None, None)
    from gh.biz.intel.detectors import conflicts
    assert await conflicts.is_customer(db, w.org, a, ea) and not await conflicts.is_customer(db, w.org, c, ec)   # conflicts dùng lại hàm này


# ─── audience_n ──────────────────────────────────────────────────────────────

async def test_audience_n_counts_other_customers_only(db: AsyncSession) -> None:
    w = await mk_world(db, await mk_org(db))
    sp, _, esp = await _mk(db, w, "Người nói", ptype="customer", member=True)
    await _mk(db, w, "Khách 1", ptype="customer", member=True)
    _, _, e3 = await _mk(db, w, "Khách fact", member=True)
    await _fact(db, w, e3, "customer_of")
    await _mk(db, w, "NV", staff_confirmed=True, member=True)
    await _mk(db, w, "Người thường", member=True)
    await db.execute(text("INSERT INTO core.group_members (group_id, person_id) VALUES (:g, :p)"), {"g": w.group, "p": w.owner_person})
    await db.commit()
    assert await person_role.audience_n(db, w.org, w.group, speaker_person_id=sp) == 2            # trừ người nói, NV, Sếp, người thường
    assert await person_role.audience_n(db, w.org, w.group, speaker_entity_id=esp) == 2
    assert await person_role.audience_n(db, w.org, w.group) == 3                                  # không biết ai nói ⇒ người nói tính như khách khác
    await db.execute(text("UPDATE core.group_members SET left_at = now() WHERE group_id = :g AND person_id = :p"), {"g": w.group, "p": sp})
    await db.commit()
    assert await person_role.audience_n(db, w.org, w.group, speaker_person_id=sp) == 2            # thành viên đã rời không đếm


async def test_audience_n_none_when_not_countable(db: AsyncSession) -> None:
    w = await mk_world(db, await mk_org(db))
    assert await person_role.audience_n(db, w.org, None) is None                                  # tin riêng
    assert await person_role.audience_n(db, w.org, w.group) is None                               # nhóm chưa có danh sách thành viên: KHÔNG suy (0 sai nghĩa)
    sp, _, _ = await _mk(db, w, "Chỉ mình nói", ptype="customer", member=True)
    assert await person_role.audience_n(db, w.org, w.group, speaker_person_id=sp) == 0            # có danh sách, không ai khác ⇒ 0 đã đếm


def test_thread_of_carries_audience_and_default_none() -> None:
    assert g.thread_of(uuid.uuid4(), 10).customer_audience_n is None
    assert g.thread_of(uuid.uuid4(), 10, 4).customer_audience_n == 4


# ─── staff_owned / staff_ref ─────────────────────────────────────────────────

async def _scene(db: AsyncSession, *, staff_confirmed: bool = True, staff_after: bool = True, customer_after: bool = False) -> tuple[Any, uuid.UUID, uuid.UUID, list[uuid.UUID], datetime]:
    w = await mk_world(db, await mk_org(db))
    cust, ci, _ = await _mk(db, w, "Khách", ptype="customer", member=True)
    staff, si, _ = await _mk(db, w, "NV", staff_confirmed=staff_confirmed, member=True)
    if not staff_confirmed:
        await db.execute(text("UPDATE core.persons SET person_type = 'staff' WHERE id = :p"), {"p": staff})   # đặt bằng đường cũ: chưa xác nhận
    trigger = NOW - timedelta(minutes=30)
    r1 = await mk_raw(db, w.org, w.channel, identity=ci, body="sao giao trễ vậy", at=trigger, group=w.group)
    r2 = await mk_raw(db, w.org, w.channel, identity=si, body="em đang xử lý, 15h giao", at=NOW - timedelta(minutes=(10 if staff_after else 40)), group=w.group)
    raws = [r1, r2]
    if customer_after:
        raws.append(await mk_raw(db, w.org, w.channel, identity=ci, body="nhanh lên", at=NOW - timedelta(minutes=5), group=w.group))
    return w, cust, staff, raws, trigger


async def _ref(db: AsyncSession, w: Any, cust: uuid.UUID, staff: uuid.UUID, raws: list[uuid.UUID], trigger: datetime, **kw: Any) -> str | None:
    args: dict[str, Any] = {"status_hint": "dang_xu_ly", "status_by_person_id": staff, "raw_event_ids": raws, "customer_person_id": cust, "group_id": w.group, "trigger_at": trigger}
    args.update(kw)
    return await person_role.staff_owner_ref(db, w.org, **args)


async def test_staff_owner_ref_confirmed_staff_after_trigger_owns(db: AsyncSession) -> None:
    w, cust, staff, raws, trig = await _scene(db)
    assert await _ref(db, w, cust, staff, raws, trig) == f"u_{staff}"


async def test_staff_owner_ref_refusals(db: AsyncSession) -> None:
    w, cust, staff, raws, trig = await _scene(db)
    assert await _ref(db, w, cust, staff, raws, trig, status_hint="da_xu_ly") is None             # da_xu_ly là `closure = staff`, không phải staff_owned
    assert await _ref(db, w, cust, staff, raws, trig, status_hint=None) is None
    assert await _ref(db, w, cust, staff, raws, trig, status_by_person_id=None) is None
    assert await _ref(db, w, cust, staff, raws, trig, asks_for_owner=True) is None                # asks_for_owner thắng
    assert await _ref(db, w, cust, cust, raws, trig) is None                                       # người trả lời là khách, không phải NV
    assert await _ref(db, w, cust, staff, raws, trig + timedelta(hours=1)) is None                # tin NV TRƯỚC tin kích hoạt


async def test_staff_owner_ref_unconfirmed_staff_or_customer_after_staff_is_not_owned(db: AsyncSession) -> None:
    w, cust, staff, raws, trig = await _scene(db, staff_confirmed=False)
    assert await _ref(db, w, cust, staff, raws, trig) is None                                      # staff chưa có khoá + staff_marked_at (D3)
    w, cust, staff, raws, trig = await _scene(db, customer_after=True)
    assert await _ref(db, w, cust, staff, raws, trig) is None                                      # khách nhắn SAU tin NV ⇒ false
    w, cust, staff, raws, trig = await _scene(db, staff_after=False)
    assert await _ref(db, w, cust, staff, raws, trig) is None                                      # NV trả lời trước khi khách chê (không phải đáp lại)
