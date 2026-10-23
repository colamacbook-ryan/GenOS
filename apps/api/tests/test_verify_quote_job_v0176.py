# ruff: noqa: E501
"""Việc nền `evidence_reverify`: kiểm lại trích dẫn cũ trên tin thô + chữ phương tiện; idempotent; không đụng dòng đã verified."""

import uuid
from typing import Any

from sqlalchemy import text

from gh.biz.mem import job_evidence_verify as job
from gh.biz.mem import jobs as memjobs


async def _seed(db: Any, body: str, quote: str, *, source_field: str | None = None, media: tuple[str, str] | None = None):  # type: ignore[no-untyped-def]
    org = (await db.execute(text("INSERT INTO core.organizations (name) VALUES (:n) RETURNING id"), {"n": f"Org {uuid.uuid4().hex[:6]}"})).scalar_one()
    ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', 'z') RETURNING id"), {"o": org})).scalar_one()
    ev = (await db.execute(text("""INSERT INTO raw.events (org_id, occurred_at, channel_id, external_msg_id, kind, body_text, payload, content_hash)
                                   VALUES (:o, now(), :c, :m, 'text', :b, '{}', decode('01', 'hex')) RETURNING id, received_at"""),
                           {"o": org, "c": ch, "m": uuid.uuid4().hex, "b": body})).one()
    if media is not None:
        att = (await db.execute(text("""INSERT INTO raw.attachments (event_id, event_received_at, storage_key, mime, bytes, sha256)
                                        VALUES (:e, :r, 'k', 'audio/ogg', 3, decode('02', 'hex')) RETURNING id"""), {"e": ev.id, "r": ev.received_at})).scalar_one()
        await db.execute(text("""INSERT INTO clean.media_texts (org_id, raw_event_id, raw_received_at, attachment_id, kind, text, engine, engine_version, status)
                                 VALUES (:o, :e, :r, :a, :k, :t, 'fake', '1', 'ok')"""), {"o": org, "e": ev.id, "r": ev.received_at, "a": att, "k": media[0], "t": media[1]})
    mu = (await db.execute(text("""INSERT INTO clean.meaning_units (org_id, observed_at, event_type, conclusion, confidence, run_id)
                                   VALUES (:o, now(), 'AskedPrice', 'x', 0.9, core.uuid_v7()) RETURNING id, observed_at"""), {"o": org})).one()
    await db.execute(text("""INSERT INTO clean.evidence (meaning_unit_id, meaning_observed_at, raw_event_id, raw_received_at, quote, source_field)
                             VALUES (:u, :uo, :e, :r, :q, :f)"""), {"u": mu.id, "uo": mu.observed_at, "e": ev.id, "r": ev.received_at, "q": quote, "f": source_field})
    await db.commit()
    return org, ev


async def _row(db: Any, raw_id: uuid.UUID) -> Any:
    return (await db.execute(text("SELECT quote_status, source_field, span_start, span_end, verified_at FROM clean.evidence WHERE raw_event_id = :e"), {"e": raw_id})).one()


async def test_verify_quote_job_legacy_row_becomes_verified_with_span(app, db) -> None:  # type: ignore[no-untyped-def]
    body = "Chị ơi  thứ 6 em giao\n200 bao nhé"
    org, ev = await _seed(db, body, "thứ 6 em giao 200 bao")          # dòng cũ: không source_field, quote đã gộp khoảng trắng
    stats = await job.reverify_org(db, org)
    await db.commit()
    assert stats == {"checked": 1, "verified": 1, "absent": 0, "unverifiable": 0}
    r = await _row(db, ev.id)
    assert (r.quote_status, r.source_field) == ("verified", "body") and r.verified_at is not None
    assert "Chị ơi thứ 6 em giao 200 bao nhé"[r.span_start:r.span_end] == "thứ 6 em giao 200 bao"


async def test_verify_quote_job_absent_when_text_not_in_message(app, db) -> None:  # type: ignore[no-untyped-def]
    org, ev = await _seed(db, "em giao thứ 6", "em giao thứ 7")
    stats = await job.reverify_org(db, org)
    await db.commit()
    assert stats["absent"] == 1
    r = await _row(db, ev.id)
    assert r.quote_status == "absent" and r.span_start is None


async def test_verify_quote_job_media_text_and_waiting_for_media(app, db) -> None:  # type: ignore[no-untyped-def]
    org, ev = await _seed(db, "", "em đã chuyển rồi", source_field="transcript", media=("transcript", "alo anh em đã chuyển rồi nha"))
    assert (await job.reverify_org(db, org))["verified"] == 1
    await db.commit()
    assert (await _row(db, ev.id)).source_field == "transcript"
    # chữ phương tiện chưa sẵn ⇒ vẫn unverifiable (không kết luận absent), kiểm lại sau
    org2, ev2 = await _seed(db, "", "em đã chuyển", source_field="ocr")
    s = await job.reverify_org(db, org2)
    await db.commit()
    assert s["unverifiable"] == 1 and (await _row(db, ev2.id)).quote_status == "unverifiable"
    # đúng trường: chữ chỉ có ở transcript mà dòng khai ocr ⇒ không được tính (ocr có thể tới sau) ⇒ unverifiable, KHÔNG verified
    org3, ev3 = await _seed(db, "", "em đã chuyển", source_field="ocr", media=("transcript", "em đã chuyển"))
    assert (await job.reverify_org(db, org3))["unverifiable"] == 1
    # trường đã có chữ mà không khớp ⇒ absent
    org4, ev4 = await _seed(db, "", "em đã chuyển", source_field="ocr", media=("ocr", "STK 0123456789"))
    assert (await job.reverify_org(db, org4))["absent"] == 1


async def test_verify_quote_job_idempotent_recheck_window_and_org_scope(app, db) -> None:  # type: ignore[no-untyped-def]
    org, ev = await _seed(db, "em giao thứ 6", "thứ 6")
    other, ev_o = await _seed(db, "khác", "khác")
    assert (await job.reverify_org(db, org))["verified"] == 1
    await db.commit()
    assert (await job.reverify_org(db, org))["checked"] == 0                    # đã verified: không đụng
    assert (await _row(db, ev_o.id)).quote_status == "unverifiable"             # tổ chức khác không bị chạm
    org4, ev4 = await _seed(db, "em giao thứ 6", "thứ 7")
    await job.reverify_org(db, org4)
    await db.commit()
    assert (await job.reverify_org(db, org4))["checked"] == 0                   # absent vừa kiểm < 24 giờ: không kiểm lại ngay
    await db.execute(text("UPDATE clean.evidence SET verified_at = now() - interval '2 days' WHERE raw_event_id = :e"), {"e": ev4.id})
    await db.commit()
    assert (await job.reverify_org(db, org4))["checked"] == 1


def test_verify_quote_job_registered_in_mem_jobs() -> None:
    assert job.evidence_reverify in [fn for fn, _ in memjobs.JOBS]
    kw = dict(memjobs.JOBS)[job.evidence_reverify]
    assert kw == {"hour": {5}, "minute": {20}}
