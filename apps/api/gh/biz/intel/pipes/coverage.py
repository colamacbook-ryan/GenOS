# ruff: noqa: E501
"""Độ phủ + "em sót" (v0.1.76, gói T3 — spec r6 W7, M11, M12).

- `line(db, org, now)`: dòng cuối bản tin — "Độ phủ 24 giờ: đọc 2.140 tin / 47 nhóm · 12 voice đã chép, 3 ảnh chưa đọc · 1 nhóm mất kết nối".
  Tin không đọc được luôn được đếm (không im lặng về vùng mù).
- `record_missed(...)`: Sếp báo chuyện bị sót ("em sót: …" trên Zalo, `POST /intel/missed` trên Console) ⇒ MỘT dòng `biz.alerts`
  (`legacy`, đã đóng, không hiện ở đâu) + `biz.signal_feedback(verdict='missed')`. Chữ Sếp dán được CHE trước khi lưu. Sót 🔴 lừa đảo / an
  toàn mang `gate` của cổng đó để M11 đếm ("0 lần sót").
- Route `POST /intel/missed` (Owner; chỉ tin của tổ chức mình, tin nhóm Sếp không được xem ⇒ 404) — gắn vào router `intel` qua `_include_subrouters`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import service
from gh.auth.deps import require_owner
from gh.biz.intel import levels, signals
from gh.chassis import actionlog
from gh.data.common import mask_text
from gh.db import DB
from gh.errors import ApiError, not_found
from gh.refinery import clock

router = APIRouter(tags=["intel"])

WINDOW = timedelta(hours=24)
#: Loại sót ⇒ cổng (M11 đếm sót 🔴 lừa đảo/an toàn riêng).
GATE_OF_KIND = {"rui_ro_lua_dao": levels.G1, "suc_khoe_an_toan": levels.G2}
TEXT_MAX = 1000


def _n(x: int) -> str:
    return f"{x:,}".replace(",", ".")


async def line(db: AsyncSession, org_id: uuid.UUID, now: datetime) -> str:
    since = now - WINDOW
    r = (await db.execute(text("""
        SELECT count(*) FILTER (WHERE direction = 'inbound') AS msgs,
               count(DISTINCT group_id) FILTER (WHERE direction = 'inbound') AS groups,
               count(*) FILTER (WHERE kind = 'voice') AS voices,
               count(*) FILTER (WHERE kind = 'voice' AND EXISTS (SELECT 1 FROM clean.media_texts m WHERE m.raw_event_id = e.id
                                 AND m.kind = 'transcript' AND m.status = 'ok')) AS voices_ok,
               count(*) FILTER (WHERE kind = 'image') AS images,
               count(*) FILTER (WHERE kind = 'image' AND EXISTS (SELECT 1 FROM clean.media_texts m WHERE m.raw_event_id = e.id
                                 AND m.kind IN ('ocr', 'description') AND m.status = 'ok')) AS images_ok
        FROM raw.events e WHERE e.org_id = :o AND e.occurred_at > :s AND e.occurred_at <= :n"""),
        {"o": org_id, "s": since, "n": now})).one()
    lost = int((await db.execute(text("""SELECT count(*) FROM ops.health_alerts WHERE org_id = :o AND key LIKE 'agent.group_removed:%'
                                         AND cleared_at IS NULL"""), {"o": org_id})).scalar_one())
    parts = [f"đọc {_n(int(r.msgs))} tin / {_n(int(r.groups))} nhóm"]
    media = []
    if r.voices:
        media.append(f"{r.voices_ok} voice đã chép" + (f", {r.voices - r.voices_ok} voice chưa nghe" if r.voices > r.voices_ok else ""))
    if r.images:
        media.append(f"{r.images - r.images_ok} ảnh chưa đọc" if r.images > r.images_ok else f"{r.images_ok} ảnh đã đọc")
    if media:
        parts.append(", ".join(media))
    if lost:
        parts.append(f"{lost} nhóm mất kết nối")
    return "Độ phủ 24 giờ: " + " · ".join(parts)


async def record_missed(db: AsyncSession, org_id: uuid.UUID, *, user_id: uuid.UUID | None, text_: str, raw_event_id: uuid.UUID | None = None,
                        kind: str | None = None, note: str | None = None) -> tuple[uuid.UUID, str | None]:
    """Ghi một lần sót (bên gọi commit + kiểm quyền trước). Trả (id dòng, cổng nếu sót lừa đảo/an toàn)."""
    if kind is not None and kind not in levels.KINDS:
        raise ValueError("loại không hợp lệ")
    gate = GATE_OF_KIND.get(kind or "")
    masked = (mask_text(text_.strip(), False) or "")[:TEXT_MAX]
    evidence = [{"type": "raw_event", "id": str(raw_event_id)}] if raw_event_id else []
    aid = await signals.record_legacy(db, org_id, alert_type="owner_missed", priority="P3", title="Sếp báo sót",
                                      summary=masked or None, evidence=evidence)
    await signals.transition(db, org_id, aid, "dismissed", now=clock.now(), actor_id=str(user_id) if user_id else "system:intel")
    tag = f"[{kind}] " if kind else ""
    await db.execute(text("""INSERT INTO biz.signal_feedback (org_id, signal_id, verdict, verdict_by, user_id, gate, shadow, note)
                             VALUES (:o, :s, 'missed', 'owner', :u, :g, false, :n)"""),
                     {"o": org_id, "s": aid, "u": user_id, "g": gate, "n": ((tag + (note or "")).strip() or None) and (tag + (note or "")).strip()[:500]})
    return aid, gate


class MissedIn(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=TEXT_MAX * 2)
    raw_event_id: uuid.UUID | None = None
    kind: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _one(self) -> MissedIn:
        if (self.text is None) == (self.raw_event_id is None):
            raise ValueError("cần đúng một trong: text, raw_event_id")
        return self


@router.post("/intel/missed")
async def post_missed(body: MissedIn, user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    """Sếp báo chuyện bị sót (M11). Chỉ Owner; `raw_event_id` phải là tin CỦA TỔ CHỨC NÀY (khác ⇒ 404, không lộ tồn tại)."""
    if body.kind is not None and body.kind not in levels.KINDS:
        raise ApiError(422, "VALIDATION", "Loại tín hiệu không hợp lệ", errors={"kind": "Không có trong 10 loại"})
    content = body.text or ""
    if body.raw_event_id is not None:
        row = (await db.execute(text("SELECT body_text FROM raw.events WHERE id = :i AND org_id = :o LIMIT 1"),
                                {"i": body.raw_event_id, "o": user.org_id})).one_or_none()
        if row is None:
            raise not_found("Tin nhắn")
        content = row.body_text or ""
    aid, gate = await record_missed(db, user.org_id, user_id=user.id, text_=content, raw_event_id=body.raw_event_id, kind=body.kind,
                                    note=body.note)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="intel.missed",
                           target_type="signal", target_id=str(aid), result="ok", detail={"kind": body.kind, "gate": gate})
    return {"ok": True, "id": str(aid), "gate": gate}
