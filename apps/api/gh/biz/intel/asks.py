# ruff: noqa: E501
"""Một hàng Cần quyết `ops.asks` (spec r4 §4.4 + r6 §4.4). Gói khác import MODULE (`from gh.biz.intel import asks`), không import tên —
rào AST cấm tên `decide` trong `owner_channel/*`, `owner_cmd/*` (CNS-044).

- Mã `code`: 5 ký tự NGẪU NHIÊN từ bảng `A-HJ-NP-Z2-9` (không 0/O/1/I), `UNIQUE (org_id, code) WHERE state='open'`, trùng thì sinh lại
  (tối đa 5 lần). `id` không bao giờ dùng để sinh mã.
- `source`: proposal · draft · question · signal · profile · identity; `UNIQUE(org_id, source, ref_id)` — tạo lại cùng nguồn ⇒ trả ask cũ.
- `summary` do HỆ THỐNG viết (bên gọi truyền chuỗi đã soạn, không chép chữ model / chữ tin thô).
- Hạn theo nguồn: proposal 24 giờ; signal = hạn của tín hiệu; draft không hết hạn; question = hạn câu hỏi (bên gọi truyền).
- Trần chung 1 câu hỏi hệ thống/bản tin cho `profile`, `identity`, `question` (bản tin áp — `digest.build`).
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LEN = 5
SOURCES = ("proposal", "draft", "question", "signal", "profile", "identity")
DECISION_KINDS = ("self", "internal", "outbound", "console")
STATES = ("open", "confirmed", "executed", "failed", "cancelled", "expired")
VIA = ("console", "zalo", "whatsapp", "telegram", "auto_self")
PROPOSAL_TTL = timedelta(hours=24)
SYSTEM_QUESTION_SOURCES = ("profile", "identity", "question")


class AskError(ValueError):
    pass


@dataclass(frozen=True)
class Ask:
    id: uuid.UUID
    org_id: uuid.UUID
    code: str
    source: str
    ref_id: uuid.UUID
    signal_id: uuid.UUID | None
    decision_kind: str
    summary: str
    state: str
    expires_at: datetime | None
    created_at: datetime
    decided_via: str | None = None
    result: dict[str, Any] | None = None


_COLS = "id, org_id, code, source, ref_id, signal_id, decision_kind, summary, state, expires_at, created_at, decided_via, result"


def _row(r: Any) -> Ask:
    return Ask(r.id, r.org_id, r.code, r.source, r.ref_id, r.signal_id, r.decision_kind, r.summary, r.state, r.expires_at,
               r.created_at, r.decided_via, r.result)


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


async def create(db: AsyncSession, org_id: uuid.UUID, *, source: str, ref_id: uuid.UUID, decision_kind: str, summary: str,
                 signal_id: uuid.UUID | None = None, payload: dict[str, Any] | None = None, expires_at: datetime | None = None,
                 created_via: str = "job") -> Ask:
    if source not in SOURCES:
        raise AskError(f"nguồn không hợp lệ: {source}")
    if decision_kind not in DECISION_KINDS:
        raise AskError(f"loại quyết định không hợp lệ: {decision_kind}")
    if not summary.strip():
        raise AskError("thiếu tóm tắt")
    existing = await get_for(db, org_id, source, ref_id)
    if existing is not None:
        return existing
    if source == "proposal" and expires_at is None:
        expires_at = (await db.execute(text("SELECT now()"))).scalar_one() + PROPOSAL_TTL
    last: Exception | None = None
    for _ in range(5):
        try:
            async with db.begin_nested():
                r = (await db.execute(text(f"""
                    INSERT INTO ops.asks (org_id, code, source, ref_id, signal_id, decision_kind, summary, payload, created_via, expires_at)
                    VALUES (:o, :c, :s, :r, :sig, :dk, :su, CAST(:p AS jsonb), :cv, :ex) RETURNING {_COLS}"""),  # noqa: S608
                    {"o": org_id, "c": new_code(), "s": source, "r": ref_id, "sig": signal_id, "dk": decision_kind,
                     "su": summary[:500], "p": orjson.dumps(payload or {}).decode(), "cv": created_via, "ex": expires_at})).one()
            return _row(r)
        except IntegrityError as e:                            # mã trùng một ask mở khác (hoặc đua tạo cùng nguồn) ⇒ thử lại
            last = e
            existing = await get_for(db, org_id, source, ref_id)
            if existing is not None:
                return existing
    raise AskError("không sinh được mã câu hỏi không trùng") from last


async def get(db: AsyncSession, org_id: uuid.UUID, ask_id: uuid.UUID) -> Ask | None:
    r = (await db.execute(text(f"SELECT {_COLS} FROM ops.asks WHERE id = :i AND org_id = :o"), {"i": ask_id, "o": org_id})).one_or_none()  # noqa: S608
    return _row(r) if r else None


async def get_for(db: AsyncSession, org_id: uuid.UUID, source: str, ref_id: uuid.UUID) -> Ask | None:
    r = (await db.execute(text(f"SELECT {_COLS} FROM ops.asks WHERE org_id = :o AND source = :s AND ref_id = :r"),  # noqa: S608
                          {"o": org_id, "s": source, "r": ref_id})).one_or_none()
    return _row(r) if r else None


async def by_code(db: AsyncSession, org_id: uuid.UUID, code: str) -> Ask | None:
    """«OK #MÃ» phải khớp ĐÚNG MỘT ask đang mở; không khớp ⇒ None."""
    code = code.strip().lstrip("#").upper()
    rows = (await db.execute(text(f"SELECT {_COLS} FROM ops.asks WHERE org_id = :o AND code = :c AND state = 'open'"),  # noqa: S608
                             {"o": org_id, "c": code})).all()
    return _row(rows[0]) if len(rows) == 1 else None


async def list_open(db: AsyncSession, org_id: uuid.UUID, *, limit: int = 50) -> list[Ask]:
    rows = (await db.execute(text(f"""SELECT {_COLS} FROM ops.asks WHERE org_id = :o AND state = 'open'
                                     AND (expires_at IS NULL OR expires_at > now()) ORDER BY created_at DESC LIMIT :n"""),  # noqa: S608
                             {"o": org_id, "n": max(1, min(limit, 200))})).all()
    return [_row(r) for r in rows]


async def decide(db: AsyncSession, org_id: uuid.UUID, ask_id: uuid.UUID, *, decision: str, user_id: uuid.UUID | None,
                 via: str, result: dict[str, Any] | None = None) -> Ask:
    """Ghi quyết định (`confirmed` / `cancelled`). Thực thi luôn đi ĐÚNG đường cũ của nguồn (không thêm đường ghi/gửi nào ở đây)."""
    if decision not in ("confirmed", "cancelled", "executed", "failed"):
        raise AskError("quyết định không hợp lệ")
    if via not in VIA:
        raise AskError("kênh quyết định không hợp lệ")
    r = (await db.execute(text(f"""
        UPDATE ops.asks SET state = :st, decided_by = :u, decided_via = :v, decided_at = now(),
                            result = COALESCE(CAST(:res AS jsonb), result)
        WHERE id = :i AND org_id = :o AND (state = 'open' OR (state = 'confirmed' AND :st IN ('executed', 'failed')))
          AND (expires_at IS NULL OR expires_at > now())
        RETURNING {_COLS}"""),  # noqa: S608
        {"st": decision, "u": user_id, "v": via, "res": None if result is None else orjson.dumps(result).decode(), "i": ask_id,
         "o": org_id})).one_or_none()
    if r is None:
        raise AskError("Mục này đã đóng hoặc hết hạn")
    return _row(r)


async def close_for(db: AsyncSession, org_id: uuid.UUID, source: str, ref_id: uuid.UUID, *, state: str = "expired") -> int:
    """Đường cũ (Console) quyết nguồn ⇒ đóng ask CÙNG transaction. Tín hiệu đóng ⇒ ask `expired`."""
    if state not in STATES:
        raise AskError("trạng thái không hợp lệ")
    r = await db.execute(text("UPDATE ops.asks SET state = :st, decided_at = now() WHERE org_id = :o AND source = :s AND ref_id = :r "
                              "AND state IN ('open', 'confirmed')"), {"st": state, "o": org_id, "s": source, "r": ref_id})
    return int(r.rowcount)  # type: ignore[attr-defined]


async def expire(db: AsyncSession, org_id: uuid.UUID | None = None) -> int:
    r = await db.execute(text("UPDATE ops.asks SET state = 'expired' WHERE state = 'open' AND expires_at IS NOT NULL "
                              "AND expires_at <= now() AND (CAST(:o AS uuid) IS NULL OR org_id = :o)"), {"o": org_id})
    return int(r.rowcount)  # type: ignore[attr-defined]
