# ruff: noqa: E501
"""v0.1.77 gói P5 (G-KB P-T4) — Lịch sử truy hồi giữ lời hứa/việc có hạn CÒN MỞ của luồng.

Lỗi: kênh người của cửa sổ (trọng số 1,5) chiếm hết 10 chỗ Lịch sử; lời hứa mở cũ hơn rơi ra ngoài ⇒ lượt sau khách báo "xong rồi" model
không thấy lời hứa (và mã tin bằng chứng) nên không đóng được. Sửa: kênh riêng, trọng số 2, ≤ 3 chỗ, cùng luồng (không rò nhóm khác).
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from gh.biz.mem import retrieve
from tests.mem_helpers import mk_org, mk_person_row
from tests.test_retrieve_scope_fixgkb3 import _group, _user

NOW = datetime.now(UTC)


async def _raw(db, org, group):  # type: ignore[no-untyped-def]
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o LIMIT 1"), {"o": org})).scalar_one()
    m = f"m-{uuid.uuid4().hex}"
    rid = (await db.execute(text("""
        INSERT INTO raw.events (org_id, occurred_at, channel_id, group_id, external_msg_id, kind, body_text, payload, content_hash)
        VALUES (:o, now() - interval '5 day', :c, :g, :m, 'text', 'tin', '{}'::jsonb, decode(md5(:m), 'hex')) RETURNING id"""),
                             {"o": org, "c": ch, "g": group, "m": m})).scalar_one()
    return rid


async def _unit(db, org, group, person, concl, days, kind=None, promise_state=None):  # type: ignore[no-untyped-def]
    at = NOW - timedelta(days=days)
    uid = (await db.execute(text("""INSERT INTO clean.meaning_units (org_id, observed_at, group_id, person_id, event_type, conclusion,
                                       confidence, run_id, kind_hint) VALUES (:o, :t, :g, :p, 'AskedPrice', :c, 0.9, core.uuid_v7(), :k)
                                    RETURNING id"""), {"o": org, "t": at, "g": group, "p": person, "c": concl, "k": kind})).scalar_one()
    raw = await _raw(db, org, group)
    await db.execute(text("""INSERT INTO clean.evidence (meaning_unit_id, meaning_observed_at, raw_event_id, raw_received_at, quote, quote_status)
                             SELECT :u, :t, e.id, e.received_at, :q, 'verified' FROM raw.events e WHERE e.id = :r"""),
                     {"u": uid, "t": at, "r": raw, "q": concl})
    if promise_state is not None:
        await db.execute(text("""INSERT INTO biz.promises (org_id, promiser_person_id, text, due_at, meaning_unit_id, state)
                                 VALUES (:o, :p, :c, :d, :u, :s)"""), {"o": org, "p": person, "c": concl, "d": at, "u": uid, "s": promise_state})
    await db.commit()
    return uid, raw


async def _world(db):  # type: ignore[no-untyped-def]
    org = await mk_org(db)
    user = await _user(db, org)
    g = await _group(db, org, "Khách sỉ", "kinh_doanh", user, members=10)
    g2 = await _group(db, org, "Khách VIP", "kinh_doanh", user, members=10)
    x = await mk_person_row(db, org, "X")
    y = await mk_person_row(db, org, "Y")
    return org, g, g2, x, y


async def _history(db, org, g, x):  # type: ignore[no-untyped-def]
    ev = [{"raw_event_id": uuid.uuid4(), "sender_person_id": x, "text": "ok rồi, sạch đẹp"}]
    return (await retrieve.pack(db, org, f"g:{g}", ev, now=NOW)).history


async def test_pack_history_keeps_open_promise(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org, g, _g2, x, y = await _world(db)
    promise, raw = await _unit(db, org, g, y, "Y hứa dọn sạch kho cho X trước thứ Sáu", 20, kind="nghia_vu_loi_hua")
    for i in range(12):
        await _unit(db, org, g, x, f"X nhắn việc số {i}", 1 + i * 0.1)
    hist = await _history(db, org, g, x)
    assert len(hist) <= retrieve.HISTORY_TOP
    got = {i.meaning_unit_id: i for i in hist}
    assert promise in got
    assert raw in got[promise].evidence_raw_ids and got[promise].meta["evidence"][0][0] == raw      # mã tin để model trích
    # tối đa PROMISE_SLOTS lời hứa mở chiếm chỗ riêng
    for k in range(5):
        await _unit(db, org, g, y, f"Y hứa việc phụ {k}", 10 + k, kind="viec_han")
    hist = await _history(db, org, g, x)
    assert sum(1 for i in hist if i.meta["kind_hint"] in retrieve.PROMISE_KINDS) <= retrieve.PROMISE_SLOTS
    assert len(hist) == retrieve.HISTORY_TOP


async def test_open_promise_not_leaked_across_groups(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org, g, g2, x, y = await _world(db)
    other, _ = await _unit(db, org, g2, y, "Y hứa giữ giá riêng cho khách VIP", 3, kind="nghia_vu_loi_hua")
    private, _ = await _unit(db, org, None, y, "Y hứa 1-1 với X", 3, kind="nghia_vu_loi_hua")
    for i in range(3):
        await _unit(db, org, g, x, f"X nhắn {i}", 1)
    ids = {i.meaning_unit_id for i in await _history(db, org, g, x)}
    assert other not in ids and private not in ids
    # 1-1: không thấy lời hứa của nhóm
    grp, _ = await _unit(db, org, g, x, "X hứa trong nhóm", 2, kind="nghia_vu_loi_hua")
    dm = await retrieve.pack(db, org, f"dm:{y}", [{"raw_event_id": uuid.uuid4(), "sender_person_id": y, "text": "xong rồi"}], now=NOW)
    dm_ids = {i.meaning_unit_id for i in dm.history}
    assert grp not in dm_ids and other not in dm_ids and private in dm_ids


async def test_closed_or_stale_promise_not_prioritised(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    org, g, _g2, x, y = await _world(db)
    closed, _ = await _unit(db, org, g, y, "Y hứa đã giao xong", 20, kind="nghia_vu_loi_hua", promise_state="kept")
    stale, _ = await _unit(db, org, g, y, "Y hứa quá cũ", 60, kind="nghia_vu_loi_hua")
    for i in range(12):
        await _unit(db, org, g, x, f"X nhắn việc số {i}", 1 + i * 0.1)
    ids = {i.meaning_unit_id for i in await _history(db, org, g, x)}
    assert closed not in ids and stale not in ids
