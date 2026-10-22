"""Câu hỏi chờ Sếp trả lời qua kênh quản trị (`ops.owner_questions`): mã ngắn 4 ký tự dễ gõ tay, hết hạn, trả
lời một lần."""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: Bỏ I, O, 0, 1 cho khỏi nhầm khi gõ tay (khớp CHECK short_code của migration 0042).
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
DEFAULT_TTL = timedelta(days=7)


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(4))


async def create(db: AsyncSession, org_id: uuid.UUID, *, kind: str, subject_id: uuid.UUID | None,
                 payload: dict[str, Any] | None = None, ttl: timedelta = DEFAULT_TTL,
                 now: datetime | None = None) -> Any:
    """Tạo câu hỏi mới; trả dòng (id, short_code, expires_at). Thử lại nếu trùng mã đang mở."""
    now = now or datetime.now(UTC)
    for _ in range(20):
        code = new_code()
        async with db.begin_nested():
            row = (await db.execute(text("""
                INSERT INTO ops.owner_questions (org_id, short_code, kind, subject_id, payload, created_at, expires_at)
                VALUES (:o, :c, :k, :s, CAST(:p AS jsonb), :now, :exp)
                ON CONFLICT DO NOTHING RETURNING id, short_code, expires_at"""),
                {"o": org_id, "c": code, "k": kind, "s": subject_id, "p": orjson.dumps(payload or {}).decode(),
                 "now": now, "exp": now + ttl})).one_or_none()
        if row is not None:
            return row
    raise RuntimeError("không cấp được mã câu hỏi")


async def open_by_code(db: AsyncSession, org_id: uuid.UUID, code: str, *, kind: str | None = None,
                       now: datetime | None = None) -> Any:
    """Câu hỏi CHƯA trả lời và CHƯA hết hạn theo mã (không phân biệt hoa thường)."""
    return (await db.execute(text("""
        SELECT id, short_code, kind, subject_id, payload, expires_at FROM ops.owner_questions
        WHERE org_id = :o AND short_code = :c AND answered_at IS NULL AND expires_at > :now
          AND (CAST(:k AS text) IS NULL OR kind = :k)"""),
        {"o": org_id, "c": (code or "").strip().upper(), "k": kind, "now": now or datetime.now(UTC)})).one_or_none()


async def answer(db: AsyncSession, question_id: uuid.UUID, answer_: str) -> bool:
    res = await db.execute(text("""UPDATE ops.owner_questions SET answered_at = now(), answer = :a
                                   WHERE id = :i AND answered_at IS NULL RETURNING id"""),
                           {"a": answer_[:40], "i": question_id})
    return res.first() is not None
