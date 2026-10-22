"""v0.1.67 gói C6 (F050) — "Chưa trả lời" đúng người, đúng tin:
- tin 1-1: Sếp trả lời khách A KHÔNG che khách B (tin đi ghép theo người nhận, không theo "cùng kênh, không nhóm");
- chỉ tin cần trả lời (có kết luận như hỏi giá, than phiền…), bỏ reaction/sticker/system;
- một người một cảnh báo (không mỗi tin một dòng cùng tiêu đề);
- cùng cách ghép ở `refinery.runner.unanswered` và `biz.people.service.PAIR_CTE` (chăm sóc khách)."""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import text

from gh.biz.people.service import PAIR_CTE
from gh.biz.queue import jobs as queue_jobs
from gh.db import sessionmaker
from gh.refinery.runner import Ev, unanswered
from tests.phase2 import listen, msg, org_id, put
from tests.test_p3_queue import _person_of, _unit

NOW = dt.datetime.now(dt.UTC)


def ts(hours_ago: float) -> str:
    return (NOW - dt.timedelta(hours=hours_ago)).isoformat()


async def _enable_dm(db, org) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("""UPDATE core.organizations
                             SET settings = settings || '{"listen_direct": {"zalo": true}}'::jsonb
                             WHERE id = :o"""), {"o": org})
    await db.commit()


def out_dm(to: str, body: str, hours_ago: float) -> dict[str, Any]:
    """Tin Sếp nhắn riêng cho `to` (Zalo: người nhận nằm ở payload.threadId, người gửi là tài khoản Sếp)."""
    return {**msg(body, group=None, sender="own-zalo", name="Sếp"), "direction": "outbound",
            "occurred_at": ts(hours_ago), "payload": {"threadId": to, "isSelf": True}}


def in_dm(sender: str, body: str, hours_ago: float, name: str = "Khách", kind: str = "text") -> dict[str, Any]:
    return {**msg(body, group=None, sender=sender, name=name), "kind": kind, "occurred_at": ts(hours_ago),
            "payload": {"threadId": sender}}


async def _alerts(db) -> list[Any]:  # type: ignore[no-untyped-def]
    return list((await db.execute(text("""SELECT title, subject_id, evidence FROM biz.alerts
                                          WHERE alert_type = 'slow_response' ORDER BY created_at"""))).all())


async def test_dm_reply_to_one_customer_does_not_hide_the_other(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _enable_dm(db, org)
    sm = sessionmaker()
    raw_a, raw_b, _out = await put(sm, org, in_dm("kh-A", "Anh báo giá thép giúp em", 3, "Chị A"),
                                   in_dm("kh-B", "Còn hàng không anh", 3, "Anh B"),
                                   out_dm("kh-A", "Dạ em gửi chị A báo giá ạ", 2))
    pa, pb = await _person_of(db, raw_a), await _person_of(db, raw_b)
    for raw, p in ((raw_a, pa), (raw_b, pb)):
        await _unit(db, org, person_id=p, event_type="AskedPrice", conclusion="Hỏi giá", raw_id=raw)
    await db.commit()
    await queue_jobs.early_warning_scan({"redis_bus": redis})
    rows = await _alerts(db)
    assert [r.subject_id for r in rows] == [pb], [r.title for r in rows]     # chỉ khách B bị bỏ quên
    assert "Anh B" in rows[0].title
    await queue_jobs.early_warning_scan({"redis_bus": redis})                 # quét lại không sinh trùng
    assert len(await _alerts(db)) == 1


async def test_only_messages_that_need_a_reply_and_one_alert_per_person(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    sm = sessionmaker()
    ask1, ask2, chat, thumbs, sticker = await put(
        sm, org,
        {**msg("Cần 3 container thép, giá bao nhiêu?", sender="u1", name="Chị Lan"), "occurred_at": ts(4)},
        {**msg("Chị hỏi lại giá nhé", sender="u1", name="Chị Lan"), "occurred_at": ts(3)},
        {**msg("ok cảm ơn", sender="u2", name="Anh Bình"), "occurred_at": ts(3)},
        {**msg("👍", sender="u2", name="Anh Bình"), "kind": "reaction", "occurred_at": ts(3)},
        {**msg("", sender="u3", name="Em Chi"), "kind": "sticker", "occurred_at": ts(3)})
    lan = await _person_of(db, ask1)
    await _unit(db, org, person_id=lan, event_type="AskedPrice", conclusion="Hỏi giá", raw_id=ask1)
    await _unit(db, org, person_id=lan, event_type="AskedPrice", conclusion="Hỏi lại giá", raw_id=ask2)
    # tin xã giao có kết luận không cần trả lời + tin reaction/sticker cũng lỡ có kết luận
    await _unit(db, org, person_id=await _person_of(db, chat), event_type="WentSilent", conclusion="Chào", raw_id=chat)
    await _unit(db, org, person_id=await _person_of(db, thumbs), event_type="AskedPrice", conclusion="👍",
                raw_id=thumbs)
    await _unit(db, org, person_id=await _person_of(db, sticker), event_type="AskedPrice", conclusion="…",
                raw_id=sticker)
    await db.commit()
    await queue_jobs.early_warning_scan({"redis_bus": redis})
    rows = await _alerts(db)
    assert [r.subject_id for r in rows] == [lan], [r.title for r in rows]     # một dòng cho Lan
    assert rows[0].evidence == [{"type": "raw", "id": str(ask1)}]             # bằng chứng = tin hỏi sớm nhất


async def test_reply_in_group_still_clears_and_staff_senders_are_ignored(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    sm = sessionmaker()
    ask, _reply = await put(sm, org, {**msg("Giá thép cuộn hôm nay?", sender="u1", name="Chị Lan"),
    "occurred_at": ts(3)},
                            {**msg("Dạ 14tr/tấn ạ", sender="own-zalo", name="Sếp"), "direction": "outbound",
                             "occurred_at": ts(2)})
    await _unit(db, org, person_id=await _person_of(db, ask), event_type="AskedPrice", conclusion="Hỏi giá", raw_id=ask)
    await db.commit()
    await queue_jobs.early_warning_scan({"redis_bus": redis})
    assert await _alerts(db) == []


async def test_unanswered_streak_counts_only_replies_to_that_customer(app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _enable_dm(db, org)
    sm = sessionmaker()
    b1, _to_a, b2 = await put(sm, org, in_dm("kh-B", "Anh ơi", 3, "Anh B"), out_dm("kh-A", "Dạ chị A", 2.5),
                              in_dm("kh-B", "Còn hàng không", 2, "Anh B"))
    async with sm() as s:
        row = (await s.execute(text("""SELECT id, received_at, seq, occurred_at, kind, coalesce(body_text,'') AS txt,
                                              direction, group_id, sender_identity_id FROM raw.events WHERE id = :i"""),
                               {"i": b2})).one()
        ev = Ev(row.id, row.received_at, row.seq, row.occurred_at, row.kind, row.txt, row.direction, None, None, None,
                None)
        assert await unanswered(s, ev, row.sender_identity_id) == 2        # trả lời riêng khách A không cắt chuỗi của B
        # Sếp trả lời đúng khách B thì chuỗi dừng
        await put(sm, org, out_dm("kh-B", "Dạ còn ạ", 1.5), in_dm("kh-B", "ok", 1, "Anh B"))
        row2 = (await s.execute(text("""SELECT id, received_at, seq, occurred_at, kind, coalesce(body_text,'') AS txt,
                                               direction, group_id, sender_identity_id FROM raw.events
                                        WHERE body_text = 'ok'"""))).one()
        ev2 = Ev(row2.id, row2.received_at, row2.seq, row2.occurred_at, row2.kind, row2.txt, row2.direction, None, None,
                 None, None)
        assert await unanswered(s, ev2, row2.sender_identity_id) == 1
    _ = b1


async def test_care_pairing_matches_reply_to_the_right_customer(app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _enable_dm(db, org)
    sm = sessionmaker()
    raw_a, raw_b, _out, _react = await put(sm, org, in_dm("kh-A", "Báo giá giúp em", 3, "Chị A"),
                                           in_dm("kh-B", "Còn hàng không", 3, "Anh B"),
                                           out_dm("kh-A", "Dạ em gửi ạ", 2),
                                           in_dm("kh-B", "👍", 2.5, "Anh B", kind="reaction"))
    df, dt_ = NOW - dt.timedelta(days=1), NOW + dt.timedelta(hours=1)
    rows = (await db.execute(text(PAIR_CTE + "SELECT id, replied_at FROM paired ORDER BY asked_at"),
                             {"o": org, "df": df, "dt": dt_})).all()
    by = {r.id: r.replied_at for r in rows}
    assert raw_a in by and by[raw_a] is not None                  # A đã được trả lời
    assert by[raw_b] is None                                      # B chưa — tin riêng cho A không che B
    assert len(rows) == 2                                         # reaction không tính là tin cần trả lời
    _ = uuid


async def test_unresolved_senders_in_dm_each_get_their_own_alert(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Review v0.1.67 P3: tin 1-1 chưa gắn được người gửi (sender_identity_id rỗng ⇒ person_id rỗng) không bị
    `DISTINCT ON` gom thành MỘT cảnh báo — hai cuộc 1-1 khác nhau bị bỏ quên phải thành hai cảnh báo."""
    org = await org_id(db)
    await _enable_dm(db, org)
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = 'zalo'"),
                           {"o": org})).scalar_one()
    raws = []
    for i, hours in enumerate((4, 3)):
        raws.append((await db.execute(text("""
            INSERT INTO raw.events (org_id, occurred_at, channel_id, group_id, sender_identity_id, external_msg_id,
                                    kind, body_text, payload, content_hash, direction)
            VALUES (:o, now() - make_interval(hours => :h), :c, NULL, NULL, :m, 'text', 'Anh báo giá giúp em',
                    CAST(:p AS jsonb), decode(md5(:m), 'hex'), 'inbound') RETURNING id"""),
                                    {"o": org, "h": hours, "c": ch, "m": f"anon-{i}-{uuid.uuid4().hex[:6]}",
                                     "p": f'{{"threadId": "lạ-{i}"}}'})).scalar_one())
    for raw in raws:
        await _unit(db, org, person_id=None, event_type="AskedPrice", conclusion="Hỏi giá", raw_id=raw)
    await db.commit()
    await queue_jobs.early_warning_scan({"redis_bus": redis})
    rows = await _alerts(db)
    assert sorted(str(r.evidence[0]["id"]) for r in rows) == sorted(str(r) for r in raws), [r.title for r in rows]
