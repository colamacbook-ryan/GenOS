# ruff: noqa: E501
"""T8 (r4 P7) — độ tin cậy lời hứa cho MỌI loại người: job có cận (org, cửa sổ thời gian, số người), cần ≥ 3 bằng chứng ở ≥ 2 ngày
(R5), KHÔNG chấm ở bối cảnh gia đình/bạn bè (INT-16), và chỉ hiện ở `tier=full` (Owner + PIN)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from gh.biz.mem import entities, facts
from gh.biz.people import jobs
from tests.conftest import Api, verify_pin
from tests.mem_helpers import T, mk_org, mkfact
from tests.phase2 import org_id
from tests.test_owner_profile_support import link_owner_user, mk_person

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


async def _promise(db: Any, org: uuid.UUID, pid: uuid.UUID, days_ago: int, *, kept: bool, evidence: bool = True) -> None:
    due = NOW - timedelta(days=days_ago)
    await db.execute(text("""INSERT INTO biz.promises (org_id, promiser_person_id, text, due_at, kept_at, broken, meaning_unit_id)
                             VALUES (:o, :p, 'hứa', :d, :k, :b, :m)"""),
                     {"o": org, "p": pid, "d": due, "k": due if kept else None, "b": None if kept else True,
                      "m": uuid.uuid4() if evidence else None})


async def _score(db: Any, pid: uuid.UUID) -> float | None:
    v = (await db.execute(text("SELECT value FROM clean.current_scores WHERE subject_type = 'person' AND subject_id = :p AND dimension = 'reliability'"),
                          {"p": pid})).scalar_one_or_none()
    return None if v is None else float(v)


async def _mk(db: Any, org: uuid.UUID, channel: uuid.UUID, name: str, ext: str) -> uuid.UUID:
    return (await mk_person(db, org, channel, name, ext))[0]


async def test_reliability_scores_any_person_type_with_enough_evidence(owner_api: Api, db: Any) -> None:
    w = await link_owner_user(db, await org_id(db))
    cust = await _mk(db, w.org, w.channel, "Khách A", "a1")
    await db.execute(text("UPDATE core.persons SET person_type = 'customer' WHERE id = :p"), {"p": cust})
    for d, k in ((1, True), (3, True), (5, True), (8, False)):
        await _promise(db, w.org, cust, d, kept=k)
    await db.commit()
    assert await jobs.recompute_reliability_org(db, w.org, now=NOW) == 1
    await db.commit()
    assert await _score(db, cust) == 75.0
    snap = (await db.execute(text("SELECT explanation FROM clean.score_snapshots WHERE subject_id = :p AND dimension = 'reliability'"), {"p": cust})).scalar_one()
    assert len(snap["factors"][0]["evidence"]) >= 3                      # có chứng cứ truy được
    assert await jobs.recompute_reliability_org(db, w.org, now=NOW) == 1  # chạy lại an toàn: không nhân đôi dòng hiện tại
    await db.commit()
    assert (await db.execute(text("SELECT count(*) FROM clean.current_scores WHERE subject_id = :p AND dimension = 'reliability'"), {"p": cust})).scalar_one() == 1


async def test_reliability_needs_three_evidence_on_two_days_inside_window(owner_api: Api, db: Any) -> None:
    w = await link_owner_user(db, await org_id(db))
    few, same_day, no_ev, old = [await _mk(db, w.org, w.channel, n, n) for n in ("few", "sameday", "noev", "old")]
    for d in (1, 2):
        await _promise(db, w.org, few, d, kept=True)                      # chỉ 2 bằng chứng
    for _ in range(3):
        await _promise(db, w.org, same_day, 2, kept=True)                 # 3 bằng chứng nhưng cùng một ngày
    for d in (1, 2, 3):
        await _promise(db, w.org, no_ev, d, kept=True, evidence=False)    # không có tin gốc
    for d in (100, 120, 140):
        await _promise(db, w.org, old, d, kept=True)                      # ngoài cửa sổ 90 ngày
    await db.commit()
    assert await jobs.recompute_reliability_org(db, w.org, now=NOW) == 0
    for p in (few, same_day, no_ev, old):
        assert await _score(db, p) is None


async def test_reliability_skips_family_and_friend_context(owner_api: Api, db: Any) -> None:
    w = await link_owner_user(db, await org_id(db))
    fam = await _mk(db, w.org, w.channel, "Mẹ", "me")
    ent = (await entities.ensure_person(db, w.org, fam)).id
    mai = (await entities.ensure_person(db, w.org, await _mk(db, w.org, w.channel, "Bà", "ba"))).id
    await facts.assert_fact(db, mkfact(w.org, ent, None, T(3), predicate="family_of", object_entity=mai))
    friend = await _mk(db, w.org, w.channel, "Bạn", "ban")
    await db.execute(text("""INSERT INTO mem.group_contexts (org_id, group_id, context, is_primary, source, confidence)
                             VALUES (:o, :g, 'ban_be', true, 'owner', 1)"""), {"o": w.org, "g": w.group})
    ident = (await db.execute(text("SELECT id FROM core.person_identities WHERE person_id = :p"), {"p": friend})).scalar_one()
    from tests.test_owner_profile_support import mk_raw
    await mk_raw(db, w.org, w.channel, identity=ident, body="chào", group=w.group)
    for p in (fam, friend):
        for d in (1, 3, 5):
            await _promise(db, w.org, p, d, kept=False)
    await db.commit()
    assert await jobs.recompute_reliability_org(db, w.org, now=NOW) == 0
    assert await _score(db, fam) is None and await _score(db, friend) is None


async def test_reliability_is_bounded_by_org_and_max_people(owner_api: Api, db: Any) -> None:
    w = await link_owner_user(db, await org_id(db))
    other = await mk_org(db, "Org khác")
    ch2 = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', 'z') RETURNING id"), {"o": other})).scalar_one()
    mine = [await _mk(db, w.org, w.channel, f"m{i}", f"m{i}") for i in range(3)]
    theirs = await _mk(db, other, ch2, "ngoài", "x")
    for p in (*mine, theirs):
        for d in (1, 3, 5):
            await _promise(db, w.org if p != theirs else other, p, d, kept=True)
    await db.commit()
    assert await jobs.recompute_reliability_org(db, w.org, now=NOW, max_people=2) == 2     # cận số người
    assert await _score(db, theirs) is None                                                 # không chạm org khác


async def test_reliability_only_in_full_tier_with_pin(owner_api: Api, db: Any) -> None:
    w = await link_owner_user(db, await org_id(db))
    p = await _mk(db, w.org, w.channel, "Khách B", "b1")
    for d in (1, 3, 5):
        await _promise(db, w.org, p, d, kept=True)
    await db.commit()
    await jobs.recompute_reliability_org(db, w.org, now=NOW)
    await db.commit()
    assert "reliability" not in (await owner_api.get(f"/profile/{p}/brief")).text            # summary: không
    assert (await owner_api.get(f"/profile/{p}/brief?tier=full")).status_code == 423         # full cần PIN
    assert "reliability" not in (await owner_api.get(f"/profile/{p}")).text                  # hồ sơ cũ cũng không hiện
    await verify_pin(owner_api)
    full = (await owner_api.get(f"/profile/{p}/brief?tier=full")).json()
    assert {s["dimension"]: s["value"] for s in full["scores"]}["reliability"] == 100.0
