# ruff: noqa: E501
"""Việc nền `evidence_reverify` (L3, spec §11 "job kiểm lại trích dẫn cũ"): kiểm lại `clean.evidence` chưa `verified`.

Chứng cứ sinh từ đường lô cũ (trước 0043) mang `quote_status = 'unverifiable'` và chưa có span; chứng cứ mới có thể `absent` vì chữ từ
voice/ảnh (`clean.media_texts`) chưa sẵn lúc kiểm. Job đọc LẠI tin thô (`raw.events`, chỉ-thêm nên không đổi) và chữ từ phương tiện, rồi áp
ĐÚNG luật của `verify.check`: `quote` là chuỗi con của đúng trường sau chuẩn hoá hẹp (NFC + gộp khoảng trắng) ⇒ `verified` kèm
`source_field`, `span_start/end`, `verified_at`; tin có mặt mà không khớp ⇒ `absent`; không tìm thấy tin/trường (chữ chưa sẵn) ⇒ vẫn `unverifiable`.
Idempotent: chỉ đụng dòng chưa `verified`; dòng đã kiểm trong 24 giờ không kiểm lại. KHÔNG sửa `mem.facts` (bất biến) — sự thật đã ghi chỉ trỏ
tới bằng chứng của chính nó.

Quét mỗi ngày 05:20 giờ VN (ngoài cửa sổ job đêm hợp nhất 02:00–05:00 và cửa sổ cập nhật genh 02:30–03:30).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.hooks import CronJob, Hook
from gh.refinery import verify

HOOKS: list[Hook] = []
BATCH = 500
MAX_ROUNDS = 20
RECHECK_AFTER_HOURS = 24
FIELD_ORDER = ("body", "transcript", "ocr", "caption")        # dòng cũ chưa có source_field: thử lần lượt


async def reverify_org(db: AsyncSession, org_id: uuid.UUID, *, limit: int = BATCH) -> dict[str, int]:
    """Kiểm lại tối đa `limit` dòng `clean.evidence` của tổ chức. Trả {checked, verified, absent, unverifiable}. Bên gọi commit."""
    rows = (await db.execute(text("""
        SELECT x.meaning_unit_id, x.meaning_observed_at, x.raw_event_id, x.raw_received_at, x.quote, x.source_field
        FROM clean.evidence x
        JOIN clean.meaning_units m ON m.id = x.meaning_unit_id AND m.observed_at = x.meaning_observed_at
        WHERE m.org_id = :o AND x.quote_status <> 'verified' AND x.quote IS NOT NULL AND x.quote <> ''
          AND (x.verified_at IS NULL OR x.verified_at < now() - make_interval(hours => :h))
        ORDER BY x.verified_at NULLS FIRST, x.meaning_observed_at DESC
        LIMIT :n"""), {"o": org_id, "h": RECHECK_AFTER_HOURS, "n": limit})).all()
    stats = {"checked": 0, "verified": 0, "absent": 0, "unverifiable": 0}
    if not rows:
        return stats
    raw_ids = list({r.raw_event_id for r in rows})
    bodies = {r.id: r.body_text for r in (await db.execute(
        text("SELECT id, body_text FROM raw.events WHERE org_id = :o AND id = ANY(:ids)"), {"o": org_id, "ids": raw_ids})).all()}
    media: dict[str, list[Any]] = {}
    for m in (await db.execute(text("""SELECT raw_event_id, kind, text, status FROM clean.media_texts
                                       WHERE org_id = :o AND raw_event_id = ANY(:ids)"""), {"o": org_id, "ids": raw_ids})).all():
        media.setdefault(str(m.raw_event_id), []).append(SimpleNamespace(message_id=str(m.raw_event_id), kind=m.kind, text=m.text, status=m.status))
    for r in rows:
        mid = str(r.raw_event_id)
        ev = {mid: {"message_id": mid, "body": bodies.get(r.raw_event_id), "sent_at": None}} if r.raw_event_id in bodies else {}
        fields = (r.source_field,) if r.source_field else FIELD_ORDER
        best: verify.VerifiedQuote | None = None
        for f in fields:
            q = verify._quote_status(SimpleNamespace(message_id=mid, field=f, text=r.quote), ev, media)
            if q.status == "verified":
                best = q
                break
            if best is None or (best.reason == "field_missing" and q.reason == "not_substring"):
                best = q                                     # có tin + có chữ mà không khớp mạnh hơn "chưa có chữ"
        assert best is not None
        stats["checked"] += 1
        if best.status == "verified":
            new, span_f = "verified", best.field
        elif best.reason == "not_substring":
            new, span_f = "absent", r.source_field
        else:
            new, span_f = "unverifiable", r.source_field
        stats[new] += 1
        await db.execute(text("""
            UPDATE clean.evidence SET quote_status = :s, source_field = :f, span_start = :a, span_end = :b, verified_at = now()
            WHERE meaning_unit_id = :u AND meaning_observed_at = :uo AND raw_event_id = :e"""),
                         {"s": new, "f": span_f, "a": best.span_start if new == "verified" else None,
                          "b": best.span_end if new == "verified" else None, "u": r.meaning_unit_id, "uo": r.meaning_observed_at,
                          "e": r.raw_event_id})
    return stats


async def evidence_reverify(ctx: dict[str, Any]) -> dict[str, dict[str, int]]:
    from gh.db import sessionmaker

    sm = sessionmaker()
    out: dict[str, dict[str, int]] = {}
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        total = {"checked": 0, "verified": 0, "absent": 0, "unverifiable": 0}
        for _ in range(MAX_ROUNDS):
            async with sm() as db:
                s = await reverify_org(db, org)
                await db.commit()
            for k, v in s.items():
                total[k] += v
            if s["checked"] < BATCH:
                break
        out[str(org)] = total
    return out


JOBS: list[CronJob] = [(evidence_reverify, {"hour": {5}, "minute": {20}})]
