# ruff: noqa: E501
"""T8 (r4 P7) — độ tin cậy lời hứa mọi loại người. Job có cận (org, cửa sổ thời gian, số người); R5: ≥ 3 bằng chứng ở ≥ 2 ngày và
≥ 2 tin gốc không phải forwarded; CHỈ chấm khi người có ≥ 1 nhóm Sếp đã xác nhận bối cảnh cho phép chấm, không có nhóm gia đình/bạn bè,
không là `owner_key_relation`, không có `family_of` (hai chiều); xoá điểm của người không còn đủ điều kiện; chỉ hiện ở `tier=full` (Owner + PIN)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from gh.biz.mem import entities, facts, profile
from gh.biz.people import jobs
from tests.conftest import Api, verify_pin
from tests.mem_helpers import T, mk_org, mkfact
from tests.phase2 import org_id
from tests.test_owner_profile_support import link_owner_user, mk_group, mk_person, mk_raw

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


async def ctx(db: Any, org: uuid.UUID, group: uuid.UUID, code: str) -> None:
    await db.execute(text("""INSERT INTO mem.group_contexts (org_id, group_id, context, is_primary, source, confidence)
                             VALUES (:o, :g, :c, NOT EXISTS (SELECT 1 FROM mem.group_contexts WHERE group_id = :g AND is_primary), 'owner', 1)"""),
                     {"o": org, "g": group, "c": code})
    await db.commit()


async def promise(db: Any, org: uuid.UUID, pid: uuid.UUID, ident: uuid.UUID | None, group: uuid.UUID | None, days_ago: int, *, kept: bool,
                  evidence: bool = True, forwarded: bool = False) -> None:
    """Một lời hứa đã chốt, có đơn vị ý nghĩa + tin gốc thật (nguồn đếm theo tin gốc không phải forwarded)."""
    due = NOW - timedelta(days=days_ago)
    mu = uuid.uuid4()
    if evidence and ident is not None:
        raw = await mk_raw(db, org, (await db.execute(text("SELECT channel_id FROM core.person_identities WHERE id = :i"), {"i": ident})).scalar_one(),
                           identity=ident, body="em hứa", group=group, payload={"forwarded": True} if forwarded else {})
        await db.execute(text("""INSERT INTO clean.meaning_units (id, org_id, observed_at, group_id, person_id, event_type, conclusion, confidence, run_id)
                                 VALUES (:m, :o, now(), :g, :p, 'Promised', 'hứa', 0.9, core.uuid_v7())"""),
                         {"m": mu, "o": org, "g": group, "p": pid})
        await db.execute(text("""INSERT INTO clean.evidence (meaning_unit_id, meaning_observed_at, raw_event_id, raw_received_at)
                                 SELECT :m, now(), :r, occurred_at FROM raw.events WHERE id = :r"""), {"m": mu, "r": raw})
    await db.execute(text("""INSERT INTO biz.promises (org_id, promiser_person_id, text, due_at, kept_at, broken, meaning_unit_id)
                             VALUES (:o, :p, 'hứa', :d, :k, :b, :m)"""),
                     {"o": org, "p": pid, "d": due, "k": due if kept else None, "b": None if kept else True, "m": mu})
    await db.commit()


async def score(db: Any, pid: uuid.UUID) -> float | None:
    v = (await db.execute(text("SELECT value FROM clean.current_scores WHERE subject_type = 'person' AND subject_id = :p AND dimension = 'reliability'"),
                          {"p": pid})).scalar_one_or_none()
    return None if v is None else float(v)


async def person(db: Any, w: Any, name: str, *, group: Any = ..., days: tuple[int, ...] = (1, 3, 5),
                 kept: tuple[bool, ...] | None = None, fwd_n: int = 0, **kw: Any) -> uuid.UUID:
    pid, ident = await mk_person(db, w.org, w.channel, name, name)
    g = w.group if group is ... else group
    for i, d in enumerate(days):
        await promise(db, w.org, pid, ident, g, d, kept=(kept[i] if kept else True), forwarded=i < fwd_n, **kw)
    return pid


async def world(db: Any) -> Any:
    w = await link_owner_user(db, await org_id(db))
    await ctx(db, w.org, w.group, "kinh_doanh")
    return w


async def test_reliability_scores_eligible_person_with_evidence(owner_api: Api, db: Any) -> None:
    w = await world(db)
    cust = await person(db, w, "kha", days=(1, 3, 5, 8), kept=(True, True, True, False))
    assert await jobs.recompute_reliability_org(db, w.org, now=NOW) == 1
    await db.commit()
    assert await score(db, cust) == 75.0
    snap = (await db.execute(text("SELECT explanation FROM clean.score_snapshots WHERE subject_id = :p AND dimension = 'reliability'"), {"p": cust})).scalar_one()
    assert len(snap["factors"][0]["evidence"]) >= 3


async def test_reliability_writes_snapshot_only_when_value_changes(owner_api: Api, db: Any) -> None:
    w = await world(db)
    p = await person(db, w, "doi")
    q = "SELECT count(*) FROM clean.score_snapshots WHERE subject_id = :p AND dimension = 'reliability'"
    await jobs.recompute_reliability_org(db, w.org, now=NOW)
    await jobs.recompute_reliability_org(db, w.org, now=NOW)
    await db.commit()
    assert (await db.execute(text(q), {"p": p})).scalar_one() == 1
    _, ident = (None, (await db.execute(text("SELECT id FROM core.person_identities WHERE person_id = :p"), {"p": p})).scalar_one())
    await promise(db, w.org, p, ident, w.group, 2, kept=False)
    await jobs.recompute_reliability_org(db, w.org, now=NOW)
    await db.commit()
    assert (await db.execute(text(q), {"p": p})).scalar_one() == 2 and await score(db, p) == 75.0


async def test_reliability_needs_three_evidence_two_days_two_unforwarded_sources(owner_api: Api, db: Any) -> None:
    w = await world(db)
    few = await person(db, w, "few", days=(1, 2))
    same_day = await person(db, w, "sameday", days=(2, 2, 2))
    no_ev = await person(db, w, "noev", evidence=False)
    old = await person(db, w, "old", days=(100, 120, 140))
    fwd = await person(db, w, "fwd", fwd_n=3)                             # 3 lời hứa nhưng mọi tin gốc đều là forwarded
    one_src = await person(db, w, "one", days=(1, 3, 5), fwd_n=2)         # chỉ 1 tin gốc không phải forwarded
    assert await jobs.recompute_reliability_org(db, w.org, now=NOW) == 0
    for p in (few, same_day, no_ev, old, fwd, one_src):
        assert await score(db, p) is None


async def test_reliability_only_scores_people_in_a_confirmed_scoring_group(owner_api: Api, db: Any) -> None:
    w = await world(db)
    dm_only = await person(db, w, "dm", group=None)                          # chỉ nhắn 1-1
    unconf = await person(db, w, "unc", group=await mk_group(db, w.org, w.channel, "chưa gán"))
    non_scoring = await mk_group(db, w.org, w.channel, "không chấm")
    await db.execute(text("""UPDATE mem.context_types SET policy = jsonb_set(policy, '{score_people}', 'false') WHERE code = 'khac'"""))
    await ctx(db, w.org, non_scoring, "khac")
    off = await person(db, w, "off", group=non_scoring)
    assert await jobs.recompute_reliability_org(db, w.org, now=NOW) == 0
    for p in (dm_only, unconf, off):
        assert await score(db, p) is None


async def test_reliability_skips_family_friend_key_relation_even_with_business_group(owner_api: Api, db: Any) -> None:
    w = await world(db)
    fam_g = await mk_group(db, w.org, w.channel, "Gia đình")
    await ctx(db, w.org, fam_g, "gia_dinh")
    mom, mom_i = await mk_person(db, w.org, w.channel, "mom", "mom")
    await mk_raw(db, w.org, w.channel, identity=mom_i, body="con ăn cơm chưa", group=fam_g)
    for d in (1, 3, 5):
        await promise(db, w.org, mom, mom_i, w.group, d, kept=False)            # có cả nhóm kinh doanh, nhưng cũng nhóm gia đình
    key = await person(db, w, "key")
    keye = (await entities.ensure_person(db, w.org, key)).id
    await profile.add_item(db, w.org, user_id=w.user, section="key_relations", object_entity_id=keye, action_log_id=uuid.uuid4())
    fam_subject = await person(db, w, "fs")                                     # family_of: người này là chủ thể
    fam_object = await person(db, w, "fo")                                      # family_of: người này là đối tượng (chiều ngược)
    es, eo = (await entities.ensure_person(db, w.org, fam_subject)).id, (await entities.ensure_person(db, w.org, fam_object)).id
    await facts.assert_fact(db, mkfact(w.org, es, None, T(3), predicate="family_of", object_entity=eo))
    await db.commit()
    assert await jobs.recompute_reliability_org(db, w.org, now=NOW) == 0
    for p in (mom, key, fam_subject, fam_object):
        assert await score(db, p) is None, p


async def test_reliability_removes_score_of_person_no_longer_eligible(owner_api: Api, db: Any) -> None:
    w = await world(db)
    p = await person(db, w, "stale")
    other = await person(db, w, "stays")
    await jobs.recompute_reliability_org(db, w.org, now=NOW)
    await db.commit()
    assert await score(db, p) == 100.0
    e, o = (await entities.ensure_person(db, w.org, p)).id, (await entities.ensure_person(db, w.org, other)).id
    await facts.assert_fact(db, mkfact(w.org, o, None, T(3), predicate="family_of", object_entity=e))   # p thành người thân
    await db.commit()
    await jobs.recompute_reliability_org(db, w.org, now=NOW)
    await db.commit()
    assert await score(db, p) is None and await score(db, other) is None
    # dưới ngưỡng bằng chứng (cửa sổ trượt) cũng bị xoá
    q = await person(db, w, "fade", days=(80, 85, 89))
    await jobs.recompute_reliability_org(db, w.org, now=NOW)
    await db.commit()
    assert await score(db, q) == 100.0
    await jobs.recompute_reliability_org(db, w.org, now=NOW + timedelta(days=30))
    await db.commit()
    assert await score(db, q) is None


async def test_reliability_is_bounded_by_org_and_max_people(owner_api: Api, db: Any) -> None:
    w = await world(db)
    other = await mk_org(db, "Org khác")
    ch2 = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', 'z') RETURNING id"), {"o": other})).scalar_one()
    mine = [await person(db, w, f"m{i}") for i in range(3)]
    pid, ident = await mk_person(db, other, ch2, "ngoai", "x")
    g2 = await mk_group(db, other, ch2, "g2")
    await ctx(db, other, g2, "kinh_doanh")
    for d in (1, 3, 5):
        await promise(db, other, pid, ident, g2, d, kept=True)
    assert await jobs.recompute_reliability_org(db, w.org, now=NOW, max_people=2) == 2
    await db.commit()
    assert await score(db, pid) is None
    assert len([m for m in mine if await score(db, m) is not None]) == 2


async def test_reliability_only_in_full_tier_with_pin(owner_api: Api, db: Any) -> None:
    w = await world(db)
    p = await person(db, w, "tier")
    await jobs.recompute_reliability_org(db, w.org, now=NOW)
    await db.commit()
    assert "reliability" not in (await owner_api.get(f"/profile/{p}/brief")).text
    assert (await owner_api.get(f"/profile/{p}/brief?tier=full")).status_code == 423
    assert "reliability" not in (await owner_api.get(f"/profile/{p}")).text
    await verify_pin(owner_api)
    full = (await owner_api.get(f"/profile/{p}/brief?tier=full")).json()
    assert {s["dimension"]: s["value"] for s in full["scores"]}["reliability"] == 100.0
