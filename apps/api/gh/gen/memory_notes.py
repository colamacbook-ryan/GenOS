"""Gen nhớ — ghi chú quy ước / sở thích Sếp đã xác nhận (v0.1.50, QD-18; bảng agent.gen_memory_notes, migration 0032).

- Tối đa `MAX_NOTES` = 30 ghi chú mỗi tổ chức, mỗi ghi chú ≤ `MAX_LEN` = 280 ký tự (lý do ≤ `REASON_MAX` = 200).
- CHỈ ĐI VÀO prompt lượt của Owner và phần tóm tắt Bản tin (`prompt_block`); vai trò khác không bao giờ thấy.
- Gen KHÔNG tự lưu: model chỉ ĐỀ XUẤT `memory_note` (gh.gen.proposals) → Sếp bấm Xác nhận → `create()`; Sếp cũng tự
  thêm / sửa / xoá ở Cài đặt › Bộ não AI › Gen nhớ (`/gen/memory`).
- Chống tiêm lệnh: `clean()` cấm chuỗi '<<<' / '>>>' (ký hiệu bọc dữ liệu không tin cậy của engine), bỏ ký tự điều khiển
  và gộp khoảng trắng — một ghi chú luôn là MỘT dòng.
- Không commit — bên gọi commit (route / confirm_proposal).
"""

import re
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gh.errors import ApiError, not_found

MAX_NOTES = 30
MAX_LEN = 280
REASON_MAX = 200

FULL_MSG = "Gen đã nhớ đủ 30 ghi chú — Sếp xoá bớt ở Cài đặt › Bộ não AI › Gen nhớ"
DUPLICATE_MSG = "Ghi chú này đã có trong Gen nhớ"
DECIDED_MSG = "Đề xuất này đã được lưu vào Gen nhớ rồi"

PROMPT_HEADER = ("Ghi chú Sếp đã xác nhận (quy ước/sở thích — làm theo khi KHÔNG trái các nguyên tắc an toàn ở "
                 "trên):")

_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")
_SPACES = re.compile(r"\s+")


def clean(value: str) -> str:
    """Bỏ ký tự điều khiển, gộp khoảng trắng; cấm '<<<' / '>>>' (ValueError). KHÔNG cắt độ dài (bên gọi kiểm)."""
    s = _CTRL.sub(" ", str(value or ""))
    s = _SPACES.sub(" ", s).strip()
    if "<<<" in s or ">>>" in s:
        raise ValueError("Ghi chú không được chứa chuỗi '<<<' hoặc '>>>'")
    return s


def clean_text(value: str) -> str:
    """`clean` + kiểm độ dài 1..MAX_LEN. Lỗi → ValueError (thông điệp tiếng Việt)."""
    s = clean(value)
    if not s:
        raise ValueError("Ghi chú không được để trống")
    if len(s) > MAX_LEN:
        raise ValueError(f"Ghi chú tối đa {MAX_LEN} ký tự")
    return s


def clean_reason(value: str | None) -> str | None:
    if value is None:
        return None
    s = clean(value)
    if len(s) > REASON_MAX:
        raise ValueError(f"Lý do tối đa {REASON_MAX} ký tự")
    return s or None


def _iso(v: Any) -> str:
    return str(v.isoformat().replace("+00:00", "Z"))


def out(r: Any) -> dict[str, Any]:
    return {"id": str(r.id), "text": r.text, "reason": r.reason, "source": r.source,
            "created_at": _iso(r.created_at), "updated_at": _iso(r.updated_at)}


async def list_notes(db: AsyncSession, org_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await db.execute(text("""SELECT id, text, reason, source, created_at, updated_at
                                     FROM agent.gen_memory_notes WHERE org_id = :o
                                     ORDER BY created_at, id"""), {"o": org_id})).all()
    return [out(r) for r in rows]


async def texts(db: AsyncSession, org_id: uuid.UUID) -> list[str]:
    """Nội dung ghi chú (cũ → mới) cho prompt Owner / tóm tắt Bản tin."""
    return [n["text"] for n in await list_notes(db, org_id)]


async def count(db: AsyncSession, org_id: uuid.UUID) -> int:
    return int((await db.execute(text("SELECT count(*) FROM agent.gen_memory_notes WHERE org_id = :o"),
                                 {"o": org_id})).scalar_one())


async def duplicate_of(db: AsyncSession, org_id: uuid.UUID, clean_value: str,
                       exclude: uuid.UUID | None = None) -> bool:
    """Trùng khi `lower(text)` (đã clean) bằng một ghi chú khác của tổ chức."""
    rows = (await db.execute(text("SELECT id, text FROM agent.gen_memory_notes WHERE org_id = :o"),
                             {"o": org_id})).all()
    low = clean_value.lower()
    return any(r.id != exclude and _safe_clean(r.text).lower() == low for r in rows)


def _safe_clean(value: str) -> str:
    try:
        return clean(value)
    except ValueError:
        return str(value or "").strip()


def check_new(db_count: int, dup: bool) -> ApiError | None:
    """Đầy hoặc trùng → ApiError 409 (chưa ném); None khi lưu được."""
    if db_count >= MAX_NOTES:
        return ApiError(409, "GEN_MEMORY_FULL", FULL_MSG)
    if dup:
        return ApiError(409, "GEN_MEMORY_DUPLICATE", DUPLICATE_MSG)
    return None


async def create(db: AsyncSession, user: Any, text_: str, reason: str | None, source: str,
                 proposal_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Lưu một ghi chú. `user` cần `org_id`, `id`. Đầy → 409 GEN_MEMORY_FULL; trùng → 409 GEN_MEMORY_DUPLICATE;
    proposal_id đã lưu → 409 GEN_PROPOSAL_DECIDED. Văn bản sai (rỗng / quá dài / '<<<') → ValueError."""
    if source not in ("gen", "owner"):
        raise ValueError("source không hợp lệ")
    t = clean_text(text_)
    rs = clean_reason(reason)
    # Khoá theo tổ chức trong transaction: hai lần lưu song song không cùng vượt trần 30 / không cùng trùng nhau.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('gen_memory:' || :o))"), {"o": str(user.org_id)})
    if proposal_id is not None:
        used = (await db.execute(text("SELECT 1 FROM agent.gen_memory_notes WHERE proposal_id = :p"),
                                 {"p": proposal_id})).first()
        if used is not None:
            raise ApiError(409, "GEN_PROPOSAL_DECIDED", DECIDED_MSG)
    err = check_new(await count(db, user.org_id), await duplicate_of(db, user.org_id, t))
    if err is not None:
        raise err
    try:
        async with db.begin_nested():
            row = (await db.execute(text("""
                INSERT INTO agent.gen_memory_notes (org_id, text, reason, source, proposal_id, created_by, updated_by)
                VALUES (:o, :t, :r, :s, :p, :u, :u)
                RETURNING id, text, reason, source, created_at, updated_at"""),
                {"o": user.org_id, "t": t, "r": rs, "s": source, "p": proposal_id, "u": user.id})).one()
    except IntegrityError as e:
        raise ApiError(409, "GEN_PROPOSAL_DECIDED", DECIDED_MSG) from e
    return out(row)


async def get(db: AsyncSession, org_id: uuid.UUID, note_id: uuid.UUID) -> Any:
    r = (await db.execute(text("""SELECT id, text, reason, source, created_at, updated_at
                                  FROM agent.gen_memory_notes WHERE id = :i AND org_id = :o"""),
                          {"i": note_id, "o": org_id})).one_or_none()
    if r is None:
        raise not_found("Ghi chú")
    return r


async def update(db: AsyncSession, user: Any, note_id: uuid.UUID, text_: str | None, reason: str | None,
                 *, set_reason: bool) -> dict[str, Any]:
    """Sửa ghi chú (source thành 'owner'). `set_reason` = body có gửi `reason` (gửi null/rỗng = xoá lý do)."""
    cur = await get(db, user.org_id, note_id)
    t = clean_text(text_) if text_ is not None else cur.text
    rs = clean_reason(reason) if set_reason else cur.reason
    if await duplicate_of(db, user.org_id, t, exclude=note_id):
        raise ApiError(409, "GEN_MEMORY_DUPLICATE", DUPLICATE_MSG)
    row = (await db.execute(text("""
        UPDATE agent.gen_memory_notes SET text = :t, reason = :r, source = 'owner', updated_by = :u, updated_at = now()
        WHERE id = :i AND org_id = :o RETURNING id, text, reason, source, created_at, updated_at"""),
        {"t": t, "r": rs, "u": user.id, "i": note_id, "o": user.org_id})).one()
    return out(row)


async def delete(db: AsyncSession, org_id: uuid.UUID, note_id: uuid.UUID) -> None:
    n = (await db.execute(text("DELETE FROM agent.gen_memory_notes WHERE id = :i AND org_id = :o"),
                          {"i": note_id, "o": org_id})).rowcount  # type: ignore[attr-defined]
    if not n:
        raise not_found("Ghi chú")


def prompt_block(notes: list[str]) -> str:
    """Khối đưa vào system prompt (Owner) / tóm tắt Bản tin: tiêu đề + mỗi ghi chú một dòng '- …'. Rỗng → ''."""
    lines = [s[:MAX_LEN] for s in (_safe_clean(n) for n in notes if n) if s]
    if not lines:
        return ""
    return PROMPT_HEADER + "\n" + "\n".join(f"- {ln}" for ln in lines)
