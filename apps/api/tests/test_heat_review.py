# ruff: noqa: E501
"""Review opus L4 (mục 8): dựng độ nóng theo lô (không nạp cả cửa sổ), job từng tổ chức độc lập."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from gh.biz.mem import heat
from gh.biz.mem import job_heat as jh
from tests.mem_helpers import mk_org
from tests.test_heat_weekly import _anchor
from tests.test_owner_profile_support import mk_person, mk_raw, mk_world


async def _seed(db, w):  # type: ignore[no-untyped-def]
    _, lan_i = await mk_person(db, w.org, w.channel, "Lan", "lan-b")
    t = _anchor()
    for i in range(7):
        await mk_raw(db, w.org, w.channel, identity=lan_i, body="hi", at=t + timedelta(minutes=i * 9))
        await mk_raw(db, w.org, w.channel, identity=None, body="yo", at=t + timedelta(minutes=i * 9 + 3), direction="outbound", payload={"threadId": "lan-b"})
    await mk_raw(db, w.org, w.channel, identity=w.owner_identity, body="nhóm", at=t + timedelta(hours=2), group=w.group)


async def _dump(db, org):  # type: ignore[no-untyped-def]
    rows = (await db.execute(text("""SELECT entity_b, week, msgs_ab, msgs_ba, initiated_a, initiated_b, reply_median_s, heat FROM mem.relation_heat_weekly
                                     WHERE org_id = :o ORDER BY entity_b, week"""), {"o": org})).all()
    return [tuple(r) for r in rows]


async def test_review8_rebuild_in_small_batches_gives_same_result_and_bounded_fetch(fresh_db, db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    await _seed(db, w)
    await heat.rebuild(db, w.org, weeks=4)
    await db.commit()
    full = await _dump(db, w.org)
    assert full
    monkeypatch.setattr(heat, "BATCH", 3)
    fetched: list[int] = []
    orig = db.execute

    async def spy(stmt, params=None, *a, **k):  # type: ignore[no-untyped-def]
        res = await orig(stmt, params, *a, **k)
        if "FROM raw.events e" in str(stmt):
            fetched.append(len(res.all()) if False else 0)
        return res

    monkeypatch.setattr(db, "execute", spy)
    await heat.rebuild(db, w.org, weeks=4)
    await db.commit()
    monkeypatch.undo()
    assert len(fetched) >= 5                                                 # 15 tin / lô 3 ⇒ nhiều lượt đọc, không phải một `.all()` cả cửa sổ
    assert await _dump(db, w.org) == full                                   # kết quả y hệt dù cắt lô (trạng thái phiên/trả lời xuyên lô)


async def test_review8_job_isolates_org_failures(fresh_db, db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    w1, w2 = await mk_world(db, await mk_org(db)), await mk_world(db, await mk_org(db, "Org 2"))
    await _seed(db, w2)
    real = heat.rebuild

    async def flaky(session, org, **kw):  # type: ignore[no-untyped-def]
        if org == w1.org:
            raise RuntimeError("hỏng một tổ chức")
        return await real(session, org, **kw)

    monkeypatch.setattr(heat, "rebuild", flaky)
    out = await jh.mem_heat_weekly({})
    assert out[str(w1.org)] == {"error": 1} and out[str(w2.org)]["rows"] >= 1
    assert datetime.now(UTC)
