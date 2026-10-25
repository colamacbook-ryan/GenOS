# ruff: noqa
"""T6 review vòng 2: thẻ gộp đã bỏ qua không mở lại; catalog > 500 hàng active."""
from sqlalchemy import text
from gh.biz.intel import signals
from gh.biz.intel.detectors import catalog_match
from gh.refinery import clock
from tests.test_intel_catalog_v0176 import _setup, _demand, _alerts


async def test_rv2_merged_demand_resurrects_after_dismiss(db):
    w = await _setup(db)
    _, person = await _demand(db, w)
    await _demand(db, w, person=person)            # cung khach, cung hang, trong 7 ngay => gop vao 1 the
    rows = await _alerts(db, w.org)
    print("BEFORE", len(rows))
    assert len(rows) == 1
    await signals.transition(db, w.org, rows[0].id, "dismissed", now=clock.now())
    await db.commit()
    await catalog_match.recompute_org(db, w.org, clock.now()); await db.commit()
    rows2 = (await db.execute(text("SELECT status, case_key FROM biz.alerts WHERE org_id=:o AND kind='co_hoi_ket_noi' ORDER BY created_at"), {"o": w.org})).all()
    print("AFTER", rows2)
    assert all(r.status not in ("open", "acknowledged") for r in rows2), rows2


async def test_rv2_active_item_beyond_500_is_still_matched(db):
    """Hàng active thứ 501+ (cũ nhất) vẫn được khớp — không cắt âm thầm."""
    w = await _setup(db)                       # "thép phi 16" tạo đầu tiên ⇒ cũ nhất
    await db.execute(text("""INSERT INTO biz.catalog_items (org_id, name, state, source, created_at)
                             SELECT :o, 'hàng phụ ' || g, 'active', 'owner', now() + make_interval(secs => g) FROM generate_series(1, 510) g"""),
                     {"o": w.org})
    await db.commit()
    await _demand(db, w)
    assert len(await _alerts(db, w.org)) == 1
