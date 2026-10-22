# ruff: noqa: F811 — fixture `w` nhập từ test_p3_duty (tham số test trùng tên là cách pytest dùng fixture)
"""v0.1.65 (B8, F080) — "Gán BOT" cho MỘT người có tác dụng thật với tin nhắn 1-1: agent trực được gán là ứng viên
cho tin riêng của người đó (kể cả khi kênh để tin 1-1 ở chế độ im lặng và agent không có phạm vi cả kênh); người
khác, agent tắt, tin nhóm không bị ảnh hưởng. Gỡ gán thì thôi."""

import uuid
from typing import Any

from sqlalchemy import text

from gh.biz.duty.context import candidates, reach
from gh.biz.relations import service as rsvc
from gh.db import sessionmaker
from tests.conftest import Api, verify_pin
from tests.phase2 import msg, put
from tests.test_p3_duty import BUY, agent_drafts, decide, decisions, hook, make_agent, refine, w  # noqa: F401


async def person_of(db: Any, external_id: str) -> uuid.UUID:
    return (await db.execute(text("""SELECT person_id FROM core.person_identities WHERE external_id = :x"""),
                             {"x": external_id})).scalar_one()


async def assign(db: Any, person: uuid.UUID, agent: uuid.UUID | None) -> None:
    await rsvc.set_person_bot(db, person, agent_id=agent, set_agent=True, autonomy_level=None, set_autonomy=False)
    await db.commit()


async def cands(db: Any, w: Any, units: list[uuid.UUID]) -> set[tuple[uuid.UUID, uuid.UUID]]:
    return {(c.agent_id, c.unit_id) for c in await candidates(db, w["org"], unit_ids=units)}


async def test_assigned_person_makes_agent_a_candidate_for_their_direct_messages(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    # kênh để mặc định im lặng; agent "Lan" KHÔNG có phạm vi kênh nào — chỉ được gán cho khách A
    lan = await make_agent(db, w, [], name="Trợ lý Lan")
    wide = await make_agent(db, w, [None], name="Cả kênh")
    ua, ub = await refine(w, redis, msg(BUY, group=None, sender="khachA", name="Khách A"),
                          msg(BUY, group=None, sender="khachB", name="Khách B"))
    pa = await person_of(db, "khachA")
    assert await cands(db, w, [ua, ub]) == set()                       # chưa gán + kênh im lặng ⇒ không ai trả lời
    await assign(db, pa, lan)
    assert await cands(db, w, [ua, ub]) == {(lan, ua)}                  # chỉ Lan, chỉ tin của khách A
    assert wide not in {a for a, _ in await cands(db, w, [ua, ub])}     # agent cả kênh vẫn bị im lặng chặn
    await hook(w, redis, decide("draft"), [ua, ub])
    assert [(d.agent_id, d.trigger_unit_id) for d in await decisions(db)] == [(lan, ua)]
    [dr] = await agent_drafts(db)
    assert dr.agent_id == lan and dr.subject_id == pa
    # gỡ gán ⇒ thôi (đơn vị mới không còn ứng viên)
    await assign(db, pa, None)
    [ua2] = await refine(w, redis, msg(BUY, group=None, sender="khachA", name="Khách A"))
    assert await cands(db, w, [ua2]) == set()


async def test_assignment_does_not_leak_to_groups_disabled_agents_or_other_orgs(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    lan = await make_agent(db, w, [], name="Trợ lý Lan")
    off = await make_agent(db, w, [], enabled=False, name="Agent tắt")
    ug, ud = await refine(w, redis, msg(BUY, group="g1", sender="khachA", name="Khách A"),
                          msg(BUY, group=None, sender="khachA", name="Khách A"))
    pa = await person_of(db, "khachA")
    await assign(db, pa, lan)
    got = await cands(db, w, [ug, ud])
    assert got == {(lan, ud)}                    # tin NHÓM của khách A không thuộc Lan (Lan không có phạm vi nhóm nào)
    await assign(db, pa, off)
    assert await cands(db, w, [ug, ud]) == set() # agent đã tắt không trả lời dù được gán


async def test_assignment_wins_over_silent_but_off_still_stores_nothing(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    lan = await make_agent(db, w, [], name="Trợ lý Lan")
    [u1] = await refine(w, redis, msg(BUY, group=None, sender="khachA", name="Khách A"))
    await assign(db, await person_of(db, "khachA"), lan)
    await db.execute(text("""UPDATE core.organizations SET settings = settings || '{"direct_mode": {"zalo": "off"}}'
                             WHERE id = :o"""), {"o": w["org"]})
    await db.commit()
    # "Không lưu" là tuyệt đối: tin mới không vào Kho, nên không có đơn vị mới nào để Lan trả lời
    [dropped] = await put(sessionmaker(), w["org"], msg(BUY, group=None, sender="khachA", name="Khách A"))
    assert dropped is None
    assert await cands(db, w, [u1]) == {(lan, u1)}                     # tin đã lưu từ trước vẫn thuộc Lan


async def test_assigned_person_dm_takes_fast_path_unassigned_silent_does_not(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    lan = await make_agent(db, w, [], name="Trợ lý Lan")
    await put(sessionmaker(), w["org"], msg("chào", group=None, sender="khachA", name="Khách A"))
    pa = await person_of(db, "khachA")
    await assign(db, pa, lan)
    a2, b1 = await put(sessionmaker(), w["org"], msg("hỏi giá", group=None, sender="khachA", name="Khách A"),
                       msg("hỏi giá", group=None, sender="khachB", name="Khách B"))
    rows = (await db.execute(text("SELECT event_id, fast FROM refinery.event_state"))).all()
    fast = {r.event_id: r.fast for r in rows}
    assert fast[a2] is True and fast[b1] is False


async def test_reach_includes_assigned_persons_direct_units(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    lan = await make_agent(db, w, [], name="Trợ lý Lan")
    ua, ub = await refine(w, redis, msg(BUY, group=None, sender="khachA", name="Khách A"),
                          msg(BUY, group=None, sender="khachB", name="Khách B"))
    await assign(db, await person_of(db, "khachA"), lan)
    r = await reach(db, w["org"], lan)
    assert r.persons == [await person_of(db, "khachA")]
    ids = (await db.execute(text(f"SELECT mu.id FROM clean.meaning_units mu WHERE {r.unit_sql()}"),  # noqa: S608
                            r.params())).scalars().all()
    assert set(ids) == {ua}                      # đọc được tin của khách A, không đọc tin của khách B


async def test_person_bot_api_assignment_is_what_the_duty_agent_reads(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    api: Api = w["api"]
    await verify_pin(api)   # review v0.1.65 P2-3: gán BOT cho người cần Owner + PIN policy.change
    lan = await make_agent(db, w, [], name="Trợ lý Lan")
    [ua] = await refine(w, redis, msg(BUY, group=None, sender="khachA", name="Khách A"))
    pa = await person_of(db, "khachA")
    r = await api.send("POST", f"/directory/people/{pa}/bot", {"agent_id": str(lan)})
    assert r.status_code == 200, r.text
    assert await cands(db, w, [ua]) == {(lan, ua)}
    r = await api.send("POST", f"/directory/people/{pa}/bot", {"agent_id": None})
    assert r.status_code == 200, r.text
    assert await cands(db, w, [ua]) == set()


async def test_org_isolation_agent_from_another_org_never_matches(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    lan = await make_agent(db, w, [], name="Trợ lý Lan")
    [ua] = await refine(w, redis, msg(BUY, group=None, sender="khachA", name="Khách A"))
    await assign(db, await person_of(db, "khachA"), lan)
    other = uuid.uuid4()
    assert await cands(db, {**w, "org": other}, [ua]) == set()
