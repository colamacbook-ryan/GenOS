# ruff: noqa: E501
"""Độ nóng quan hệ theo tuần (L4): tính từ metadata, chạy lại an toàn, chuỗi tuần bù 0, không lộ chéo tổ chức, quên người xoá sạch."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from gh.biz.mem import entities, forget, heat
from tests.mem_helpers import mk_org
from tests.test_owner_profile_support import mk_person, mk_raw, mk_world


def _anchor(weeks_back: int = 1) -> datetime:
    """Thứ Tư 12:00 giờ VN của `weeks_back` tuần trước — cố định trong một tuần, không vắt qua nửa đêm."""
    now = datetime.now(UTC)
    mon = datetime.combine(heat.week_start(now) - timedelta(weeks=weeks_back), datetime.min.time(), tzinfo=heat.VN)
    return (mon + timedelta(days=2, hours=12)).astimezone(UTC)


def test_heat_score_properties() -> None:
    assert heat.score(0, 0, 0, 0, None) == 0.0
    one_sided = heat.score(10, 0, 1, 0, None)
    both = heat.score(5, 5, 1, 1, 600)
    assert both > one_sided > 0
    assert heat.score(5, 5, 1, 1, 60) > heat.score(5, 5, 1, 1, 86400)             # trả lời nhanh nóng hơn
    assert heat.score(500, 500, 9, 9, 30) <= 100
    assert [heat.state_of(x) for x in (0, 8, 30, 60, 99)] == ["cold", "cool", "warm", "hot", "hot"]
    assert heat.week_start(datetime(2026, 10, 25, 20, 0, tzinfo=UTC)).isoformat() == "2026-10-26"   # CN 20:00 UTC = T2 03:00 VN


async def test_heat_rebuild_dm_group_counts_initiative_reply_and_idempotent(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    lan_p, lan_i = await mk_person(db, w.org, w.channel, "Chị Lan", "lan-zx")
    t0 = _anchor()
    m = lambda mins: t0 + timedelta(minutes=mins)  # noqa: E731
    # DM: Lan mở đầu (chủ động b), Sếp trả lời sau 10 phút; rồi Sếp nhắn tiếp; Lan trả lời sau 20 phút
    await mk_raw(db, w.org, w.channel, identity=lan_i, body="Anh ơi", at=m(0))
    await mk_raw(db, w.org, w.channel, identity=None, body="Dạ em đây", at=m(10), direction="outbound", payload={"threadId": "lan-zx"})
    await mk_raw(db, w.org, w.channel, identity=w.owner_identity, body="Gửi báo giá nhé", at=m(15), direction="outbound", payload={"threadId": "lan-zx"})
    await mk_raw(db, w.org, w.channel, identity=lan_i, body="Ok anh", at=m(35))
    # tin vô nghĩa không tính
    await mk_raw(db, w.org, w.channel, identity=lan_i, body=None, at=m(36), kind="reaction")
    # nhóm: Sếp mở (chủ động a), người khác trả lời sau 5 phút
    await mk_raw(db, w.org, w.channel, identity=w.owner_identity, body="Mọi người chú ý", at=m(60), group=w.group)
    await mk_raw(db, w.org, w.channel, identity=lan_i, body="Dạ", at=m(65), group=w.group)
    st = await heat.rebuild(db, w.org, weeks=4)
    await db.commit()
    assert st["pairs"] == 2 and st["rows"] == 2
    lan_e = (await entities.ensure_person(db, w.org, lan_p)).id
    grp_e = (await entities.ensure_group(db, w.org, w.group)).id
    wk = heat.week_start(t0)
    rows = {r.entity_b: r for r in (await db.execute(text("SELECT * FROM mem.relation_heat_weekly WHERE org_id = :o"), {"o": w.org})).all()}
    lan = rows[lan_e]
    assert (lan.entity_a, lan.week, lan.msgs_ab, lan.msgs_ba, lan.initiated_a, lan.initiated_b) == (w.owner, wk, 2, 2, 0, 1)
    assert lan.reply_median_s == 20 * 60 and lan.state in ("hot", "warm", "cool") and float(lan.heat) > 0
    g = rows[grp_e]
    assert (g.msgs_ab, g.msgs_ba, g.initiated_a, g.initiated_b, g.reply_median_s) == (1, 1, 1, 0, 300)
    before = [(r.entity_b, r.week, float(r.heat)) for r in rows.values()]
    again = await heat.rebuild(db, w.org, weeks=4)
    await db.commit()
    assert again == {"pairs": 2, "rows": 2, "deleted": 0}
    after = [(r.entity_b, r.week, float(r.heat)) for r in (await db.execute(text("SELECT * FROM mem.relation_heat_weekly WHERE org_id = :o"), {"o": w.org})).all()]
    assert sorted(before, key=str) == sorted(after, key=str)


async def test_heat_series_zero_filled_ordered_and_org_isolated(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    w2 = await mk_world(db, await mk_org(db, "Org 2"))
    _, lan_i = await mk_person(db, w.org, w.channel, "Lan", "lan1")
    t = _anchor(2)
    for i in range(6):
        await mk_raw(db, w.org, w.channel, identity=lan_i, body="hi", at=t + timedelta(minutes=i * 7))
        await mk_raw(db, w.org, w.channel, identity=w.owner_identity, body="yo", at=t + timedelta(minutes=i * 7 + 2), direction="outbound", payload={"threadId": "lan1"})
    await heat.rebuild(db, w.org, weeks=12)
    await db.commit()
    lan_e = (await db.execute(text("SELECT entity_b FROM mem.relation_heat_weekly WHERE org_id = :o"), {"o": w.org})).scalar_one()
    line = await heat.series(db, w.org, w.owner, lan_e, 12)
    assert len(line) == 12 and [x["week"] for x in line] == sorted(x["week"] for x in line)
    nz = [x for x in line if x["msgs_ab"]]
    assert len(nz) == 1 and nz[0]["week"] == heat.week_start(t) and nz[0]["msgs_ab"] == 6 and nz[0]["msgs_ba"] == 6
    assert all(x["state"] == "cold" and x["heat"] == 0.0 for x in line if not x["msgs_ab"])
    assert len(await heat.series(db, w.org, w.owner, lan_e, 3)) == 3
    # tổ chức khác hỏi cùng id ⇒ chỉ thấy số 0 (hàng bị lọc theo org_id)
    other = await heat.series(db, w2.org, w2.owner, lan_e, 12)
    assert all(x["msgs_ab"] == 0 and x["heat"] == 0.0 for x in other)


def test_heat_reads_metadata_only_never_message_body() -> None:
    from pathlib import Path

    assert "body_text" not in Path(heat.__file__).read_text(encoding="utf-8").split('"""', 2)[2]


def test_heat_cooling_detector() -> None:
    def mk(vals):  # type: ignore[no-untyped-def]
        return [{"heat": v} for v in vals]

    assert heat.cooling(mk([40, 42, 38, 41, 45, 40, 10, 8]))
    assert not heat.cooling(mk([40, 42, 38, 41, 45, 40, 35, 38]))
    assert not heat.cooling(mk([2, 3, 1, 2, 1, 2, 0, 0]))              # chưa từng nóng ⇒ không "nguội"
    assert not heat.cooling(mk([40, 3]))


async def test_heat_rebuild_removes_stale_rows_and_forget_erases(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    lan_p, lan_i = await mk_person(db, w.org, w.channel, "Lan", "lan2")
    await mk_raw(db, w.org, w.channel, identity=lan_i, body="hi", at=_anchor())
    await heat.rebuild(db, w.org, weeks=4)
    await db.commit()
    assert (await db.execute(text("SELECT count(*) FROM mem.relation_heat_weekly"))).scalar_one() == 1
    counts = await forget.erase_entity(db, w.org, lan_p)
    await db.commit()
    assert counts["heat"] == 1
    await heat.rebuild(db, w.org, weeks=4)                                  # người đã quên không sinh lại hàng
    await db.commit()
    assert (await db.execute(text("SELECT count(*) FROM mem.relation_heat_weekly"))).scalar_one() == 0
    # hàng cũ trong cửa sổ không còn tin nào ⇒ bị dọn
    await db.execute(text("INSERT INTO mem.relation_heat_weekly (org_id, entity_a, entity_b, week, msgs_ab) VALUES (:o, :a, :b, :w, 3)"),
                     {"o": w.org, "a": w.owner, "b": w.owner, "w": heat.week_start(datetime.now(UTC))})
    await db.commit()
    st = await heat.rebuild(db, w.org, weeks=4)
    assert st["deleted"] == 1
