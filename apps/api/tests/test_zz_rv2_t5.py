# ruff: noqa
from datetime import UTC, datetime, timedelta
from sqlalchemy import text
from gh.biz.intel.detectors import questions
from tests.test_owner_profile_support import mk_world, mk_person, mk_raw
from tests.mem_helpers import mk_org

async def test_rv2_legacy_first_then_scan_units_same_customer(db):
    from gh.biz.queue import jobs as queue_jobs
    from tests.test_p3_queue import _unit
    now = datetime.now(UTC)
    w = await mk_world(db, await mk_org(db))
    cust, ci = await mk_person(db, w.org, w.channel, "Khach A")
    await db.execute(text("UPDATE core.persons SET person_type = 'customer' WHERE id = :p"), {"p": cust})
    r1 = await mk_raw(db, w.org, w.channel, identity=ci, body="gia bao nhieu?", at=now - timedelta(hours=3))
    await _unit(db, w.org, person_id=cust, event_type="AskedPrice", conclusion="hoi", raw_id=r1)
    r2 = await mk_raw(db, w.org, w.channel, identity=ci, body="bao gio giao?", at=now - timedelta(hours=2))
    await _unit(db, w.org, person_id=cust, event_type="AskedStatus", conclusion="hoi", raw_id=r2)
    await db.commit()
    n = await queue_jobs._scan_slow_response(db, w.org); await db.commit()
    out = await questions.scan_units(db, w.org, now); await db.commit()
    rows = (await db.execute(text("SELECT alert_type, kind, detector, raw_event_ids FROM biz.alerts WHERE org_id = :o"), {"o": w.org})).all()
    print("DUP", n, len(out), rows)
    assert len(rows) == 1, rows
