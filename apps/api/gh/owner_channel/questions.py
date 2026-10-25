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
            await _ask(db, org_id, row, kind)
            return row
    raise RuntimeError("không cấp được mã câu hỏi")


#: Tóm tắt ask do HỆ THỐNG viết theo loại câu hỏi (v0.1.76, một hàng Cần quyết).
ASK_SUMMARY = {"listen_confirm": "Bật nghe một nhóm?", "group_added": "Nhóm mới thêm em vào — nghe hay bỏ?",
               "support_confirm": "Xác nhận bật chế độ hỗ trợ nhóm"}


async def _ask(db: AsyncSession, org_id: uuid.UUID, row: Any, kind: str) -> None:
    """v0.1.76 (T2, spec r4 §4.6): mỗi câu hỏi `owner_questions` ghi thêm MỘT ask cùng transaction
    (nguồn `question`, hạn = hạn câu hỏi).
    Trả lời vẫn bằng lệnh cũ (/nghe, /bo, /xac_nhan với mã 4 ký tự) — ask đóng theo khi câu hỏi được trả lời."""
    from gh.biz.intel import asks

    await asks.create(db, org_id, source="question", ref_id=row.id, decision_kind="self",
                      summary=ASK_SUMMARY.get(kind, "Câu hỏi chờ Sếp trả lời"), expires_at=row.expires_at)


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
                                   WHERE id = :i AND answered_at IS NULL RETURNING id, org_id"""),
                           {"a": answer_[:40], "i": question_id})
    row = res.first()
    if row is None:
        return False
    from gh.biz.intel import asks

    await asks.close_for(db, row.org_id, "question", row.id, state="executed")   # v0.1.76: cùng transaction
    return True
