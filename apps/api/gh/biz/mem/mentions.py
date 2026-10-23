"""Nhắc tới (L2): một cách gọi trong tin ("chị Tư Đồng Nai") nối với thực thể — hoặc để ngỏ (candidate / unresolved)."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.mem import aliases, entities

METHODS = ("sender_id", "alias_exact", "alias_fuzzy", "llm", "owner")


@dataclass(frozen=True)
class Mention:
    id: uuid.UUID
    entity_id: uuid.UUID | None
    method: str
    confidence: float
    status: str                 # linked | candidate | unresolved


async def _insert(db: AsyncSession, org_id: uuid.UUID, unit_id: uuid.UUID, observed_at: datetime,
                  raw_event_id: uuid.UUID | None, surface: str, entity_id: uuid.UUID | None, method: str,
                  confidence: float, status: str) -> Mention:
    if method not in METHODS:
        raise ValueError(f"method không hợp lệ: {method}")
    mid = (await db.execute(text("""
        INSERT INTO mem.mentions (org_id, meaning_unit_id, meaning_observed_at, raw_event_id, surface, entity_id,
                                  method, confidence, status)
        VALUES (:o, :u, :t, :r, :s, :e, :m, :c, :st) RETURNING id"""),
        {"o": org_id, "u": unit_id, "t": observed_at, "r": raw_event_id, "s": surface, "e": entity_id, "m": method,
         "c": confidence, "st": status})).scalar_one()
    return Mention(mid, entity_id, method, confidence, status)


async def link(db: AsyncSession, org_id: uuid.UUID, *, meaning_unit_id: uuid.UUID, meaning_observed_at: datetime,
               surface: str, raw_event_id: uuid.UUID | None = None, group_id: uuid.UUID | None = None,
               sender_entity_id: uuid.UUID | None = None, entity_id: uuid.UUID | None = None,
               method: str | None = None, confidence: float | None = None) -> Mention:
    """Ghi một mention. Thứ tự: (1) `sender_entity_id` (ID nền tảng) ⇒ linked/sender_id 1.0; (2) `entity_id` do bên gọi
    đã quyết (llm/owner) ⇒ linked; (3) tra bí danh: một ứng viên chính xác duy nhất ⇒ linked/alias_exact; gần đúng hoặc
    nhiều ứng viên chính xác ⇒ candidate (trỏ ứng viên tốt nhất); không có ⇒ unresolved."""
    surface = " ".join((surface or "").split())
    for ref in (sender_entity_id, entity_id):                       # cùng tổ chức, chưa bị quên
        if ref is not None:
            await entities.require(db, org_id, await entities.canonical(db, ref), lock="share", allow_forgotten=False)
    if sender_entity_id is not None:
        return await _insert(db, org_id, meaning_unit_id, meaning_observed_at, raw_event_id, surface,
                             await entities.canonical(db, sender_entity_id), "sender_id", 1.0, "linked")
    if entity_id is not None:
        m = method or "llm"
        return await _insert(db, org_id, meaning_unit_id, meaning_observed_at, raw_event_id, surface,
                             await entities.canonical(db, entity_id), m, confidence if confidence is not None else 0.7,
                             "linked")
    matches = await aliases.resolve(db, org_id, surface, group_id)
    if not matches:
        return await _insert(db, org_id, meaning_unit_id, meaning_observed_at, raw_event_id, surface, None,
                             "alias_exact", 0.0, "unresolved")
    top = matches[0]
    tied = [m for m in matches if m.method == top.method and m.scoped == top.scoped]
    if top.method == "alias_exact" and len(tied) == 1:
        return await _insert(db, org_id, meaning_unit_id, meaning_observed_at, raw_event_id, surface, top.entity_id,
                             "alias_exact", top.score, "linked")
    return await _insert(db, org_id, meaning_unit_id, meaning_observed_at, raw_event_id, surface, top.entity_id,
                         top.method, top.score, "candidate")


async def open_mentions(db: AsyncSession, org_id: uuid.UUID, limit: int = 100) -> list[Mention]:
    rows = (await db.execute(text("""
        SELECT id, entity_id, method, confidence, status FROM mem.mentions
        WHERE org_id = :o AND status <> 'linked' ORDER BY id LIMIT :n"""), {"o": org_id, "n": limit})).all()
    return [Mention(r.id, r.entity_id, r.method, float(r.confidence), r.status) for r in rows]
