# ruff: noqa: E501
"""Trạng thái xử lý media đọc được: đang chờ / đã đọc / lỗi kèm lý do thân thiện — suy ra từ `raw.events` +
`clean.media_texts` (không thêm bảng, không thêm cột). Tin media CHƯA có hàng `media_texts` nào = đang chờ."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.media import fetch as mfetch
from gh.biz.media.fetch import FINAL_ERRORS, friendly

PENDING, OK, FAILED, TOO_LARGE, SKIPPED_BUDGET = "pending", "ok", "failed", "too_large", "skipped_budget"
LABELS = {PENDING: "Đang chờ đọc", OK: "Đã đọc", FAILED: "Lỗi khi đọc", TOO_LARGE: "Quá lớn, chưa đọc",
          SKIPPED_BUDGET: "Chờ ngân sách AI"}
#: Chữ chính của từng loại tin: có hàng `ok` của loại này ⇒ tin đã đọc xong (mô tả ảnh là phần phụ).
PRIMARY = {"voice": "transcript", "image": "ocr"}
KIND_NAME = {"voice": "voice", "image": "ảnh"}


@dataclass
class MediaStatus:
    raw_event_id: uuid.UUID
    kind: str
    state: str
    label: str
    reason: str | None = None
    error_code: str | None = None
    will_retry: bool = False
    texts: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"raw_event_id": str(self.raw_event_id), "kind": self.kind, "state": self.state, "label": self.label,
                "reason": self.reason, "error_code": self.error_code, "will_retry": self.will_retry, "texts": self.texts}


def decide(kind: str, rows: list[dict[str, Any]]) -> tuple[str, str | None, bool]:
    """(trạng thái, mã lỗi, còn thử lại) từ các hàng `media_texts` của một tin. Có chữ chính `ok` ⇒ ok; không thì lấy hàng mới nhất."""
    if not rows:
        return PENDING, None, True
    if any(r["kind"] == PRIMARY.get(kind) and r["status"] == OK for r in rows):
        return OK, None, False
    last = max(rows, key=lambda r: r["created_at"])
    code = last.get("error_code")
    if last["status"] == TOO_LARGE:
        return TOO_LARGE, code or TOO_LARGE, False
    if last["status"] == SKIPPED_BUDGET:
        return SKIPPED_BUDGET, code or SKIPPED_BUDGET, True
    if last["status"] == OK:      # chỉ có mô tả ảnh, OCR chưa/ không đọc được
        return FAILED, code, True
    return FAILED, code, code not in FINAL_ERRORS


async def event_status(db: AsyncSession, org_id: uuid.UUID, raw_event_ids: list[uuid.UUID]) -> dict[uuid.UUID, MediaStatus]:
    """Trạng thái từng tin media trong `raw_event_ids` (tin không phải media bị bỏ khỏi kết quả)."""
    if not raw_event_ids:
        return {}
    evs = (await db.execute(text("""
        SELECT id, kind, received_at > now() - make_interval(hours => :w) AS in_window FROM raw.events
         WHERE org_id = :o AND id = ANY(:ids) AND kind IN ('voice', 'image') AND payload->>'media_ref' IS NOT NULL"""),
        {"o": org_id, "ids": raw_event_ids, "w": mfetch.RETRY_WINDOW_H})).all()
    rows = (await db.execute(text("""
        SELECT raw_event_id, kind, status, error_code, created_at, lang, engine, length(coalesce(text, '')) AS chars
          FROM clean.media_texts WHERE org_id = :o AND raw_event_id = ANY(:ids)"""),
        {"o": org_id, "ids": [e.id for e in evs]})).mappings().all()
    by: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[r["raw_event_id"]].append(dict(r))
    out: dict[uuid.UUID, MediaStatus] = {}
    for e in evs:
        state, code, retry = decide(e.kind, by.get(e.id, []))
        reason = None
        if state == PENDING:
            reason = f"Gen đang xếp hàng để đọc {KIND_NAME[e.kind]} này."
        elif state != OK:
            reason = friendly(code or state)
        out[e.id] = MediaStatus(
            raw_event_id=e.id, kind=e.kind, state=state, label=LABELS[state], reason=reason, error_code=code,
            will_retry=retry and state != OK and bool(e.in_window),   # quá cửa sổ quét ⇒ không còn được thử lại
            texts=[{"kind": r["kind"], "status": r["status"], "chars": r["chars"], "lang": r["lang"], "engine": r["engine"]}
                   for r in by.get(e.id, [])])
    return out


async def coverage(db: AsyncSession, org_id: uuid.UUID, *, hours: int = 24, group_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Độ phủ cho dòng "12 voice đã chép · 3 ảnh chưa đọc ở nhóm X" của bản tin (§4E): đếm tin media theo nhóm, `read` =
    có chữ chính `ok`, `unread` = còn lại (chờ / lỗi / quá lớn / chờ ngân sách)."""
    rows = (await db.execute(text("""
        SELECT e.group_id, coalesce(g.name, '(tin riêng)') AS group_name, e.kind,
               count(*) AS total,
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM clean.media_texts m
                     WHERE m.raw_event_id = e.id AND m.status = 'ok' AND m.kind IN ('transcript', 'ocr'))) AS read
          FROM raw.events e LEFT JOIN core.groups g ON g.id = e.group_id
         WHERE e.org_id = :o AND e.kind IN ('voice', 'image') AND e.payload->>'media_ref' IS NOT NULL
           AND e.received_at > now() - make_interval(hours => :h)
           AND (CAST(:g AS uuid) IS NULL OR e.group_id = CAST(:g AS uuid))
         GROUP BY e.group_id, g.name, e.kind ORDER BY group_name, e.kind"""),
        {"o": org_id, "h": hours, "g": group_id})).mappings().all()
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        g = groups.setdefault(str(r["group_id"]), {"group_id": str(r["group_id"]) if r["group_id"] else None,
                                                    "group_name": r["group_name"], "voice_read": 0, "voice_unread": 0,
                                                    "image_read": 0, "image_unread": 0})
        g[f"{r['kind']}_read"] = r["read"]
        g[f"{r['kind']}_unread"] = r["total"] - r["read"]
    lines = []
    for g in groups.values():
        parts = []
        if g["voice_read"]:
            parts.append(f"{g['voice_read']} voice đã chép")
        if g["voice_unread"]:
            parts.append(f"{g['voice_unread']} voice chưa chép")
        if g["image_unread"]:
            parts.append(f"{g['image_unread']} ảnh chưa đọc")
        if parts:
            lines.append(f"{' · '.join(parts)} ở {g['group_name']}")
    return {"hours": hours, "groups": list(groups.values()), "lines": lines}
