"""Sổ tay nhận thức theo ID người / nhóm (ARCHITECTURE §5.1).

- Lũy tiến: refinery và agent thêm mục sau mỗi lượt.
- Nén khi token_used ≥ 90% ngân sách hoặc mỗi 24 giờ: mục không ghim (trừ "Giới hạn cho agent") cũ nhất được
  lưu trữ (archived_at, vẫn truy được), thay bằng một mục tóm tắt; ghi memory.compactions.
- Sửa mục = tạo bản mới, bản cũ lưu trữ và trỏ replaced_by. Xoá = lưu trữ. Không xoá cứng.
"""

import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SECTIONS = {
    "attention_now": "Điều cần chú ý ngay",
    "rolling_context": "Ngữ cảnh ngắn lũy tiến",
    "guardrails": "Giới hạn cho agent",
    "preferences": "Sở thích",
    "open_threads": "Việc dở",
}
NEVER_COMPACT = ("guardrails",)
COMPACT_AT = 0.90
KEEP_AFTER = 0.40         # sau khi nén, phần giữ lại chiếm tối đa 40% ngân sách
COMPACT_EVERY_S = 24 * 3600


def estimate_tokens(body: str) -> int:
    return max(1, len(body) // 3)


async def ensure(db: AsyncSession, org_id: uuid.UUID, subject_type: str, subject_id: uuid.UUID) -> Any:
    await db.execute(text("""INSERT INTO memory.notebooks (org_id, subject_type, subject_id) VALUES (:o, :t, :s)
                             ON CONFLICT (org_id, subject_type, subject_id) DO NOTHING"""),
                     {"o": org_id, "t": subject_type, "s": subject_id})
    return (await db.execute(text("""
        SELECT id, token_budget, token_used, compaction_no, last_compacted_at, created_at
        FROM memory.notebooks WHERE org_id = :o AND subject_type = :t AND subject_id = :s"""),
                             {"o": org_id, "t": subject_type, "s": subject_id})).one()


async def recount(db: AsyncSession, notebook_id: uuid.UUID) -> int:
    used = (await db.execute(text("""SELECT COALESCE(sum(tokens), 0) FROM memory.entries
                                     WHERE notebook_id = :n AND archived_at IS NULL"""),
                             {"n": notebook_id})).scalar_one()
    await db.execute(text("UPDATE memory.notebooks SET token_used = :u WHERE id = :n"), {"u": used, "n": notebook_id})
    return int(used)


async def append(db: AsyncSession, org_id: uuid.UUID, subject_type: str, subject_id: uuid.UUID, section: str,
                 body: str, refs: list[dict[str, Any]], author: str, pinned: bool = False) -> uuid.UUID:
    if section not in SECTIONS:
        raise ValueError(f"Mục sổ tay không hợp lệ: {section}")
    nb = await ensure(db, org_id, subject_type, subject_id)
    entry_id = (await db.execute(text("""
        INSERT INTO memory.entries (notebook_id, section, body, refs, author, is_pinned, tokens)
        VALUES (:n, :s, :b, CAST(:r AS jsonb), :a, :p, :t) RETURNING id"""),
        {"n": nb.id, "s": section, "b": body, "r": orjson.dumps(refs, default=str).decode(), "a": author,
         "p": pinned, "t": estimate_tokens(body)})).scalar_one()
    used = await recount(db, nb.id)
    if used >= COMPACT_AT * nb.token_budget:
        await compact(db, nb.id, reason="budget")
    return entry_id  # type: ignore[no-any-return]


async def compact(db: AsyncSession, notebook_id: uuid.UUID, *, reason: str = "manual",
                  summary_text: str | None = None, model: str | None = None) -> dict[str, Any] | None:
    """Nén một sổ tay. Trả thông tin lần nén, hoặc None nếu không có gì để nén."""
    nb = (await db.execute(text("""SELECT id, token_budget, token_used, compaction_no FROM memory.notebooks
                                   WHERE id = :n FOR UPDATE"""), {"n": notebook_id})).one()
    rows = (await db.execute(text("""
        SELECT id, section, body, refs, tokens, created_at FROM memory.entries
        WHERE notebook_id = :n AND archived_at IS NULL AND NOT is_pinned AND section <> ALL(:never)
        ORDER BY created_at DESC"""), {"n": notebook_id, "never": list(NEVER_COMPACT)})).all()
    pinned_tokens = nb.token_used - sum(r.tokens for r in rows)
    keep_budget = max(0, int(KEEP_AFTER * nb.token_budget) - pinned_tokens)
    kept: list[Any] = []
    archived: list[Any] = []
    acc = 0
    for r in rows:
        if acc + r.tokens <= keep_budget and len(kept) < 30:
            kept.append(r)
            acc += r.tokens
        else:
            archived.append(r)
    if not archived:
        await db.execute(text("UPDATE memory.notebooks SET last_compacted_at = now() WHERE id = :n"), {"n": nb.id})
        return None
    now = datetime.now(UTC)
    ids = [r.id for r in archived]
    await db.execute(text("UPDATE memory.entries SET archived_at = :t WHERE id = ANY(:ids)"), {"t": now, "ids": ids})
    no = nb.compaction_no + 1
    summary = summary_text or _summary(archived)
    refs: list[dict[str, Any]] = []
    for r in archived:
        refs.extend(r.refs or [])
    await db.execute(text("""
        INSERT INTO memory.entries (notebook_id, section, body, refs, author, tokens)
        VALUES (:n, 'rolling_context', :b, CAST(:r AS jsonb), 'agent:core.compaction', :t)"""),
        {"n": nb.id, "b": f"Tóm tắt nén lần {no}: {summary}", "r": orjson.dumps(refs[:20], default=str).decode(),
         "t": estimate_tokens(summary) + 5})
    after = await recount(db, nb.id)
    await db.execute(text("""UPDATE memory.notebooks SET compaction_no = :c, last_compacted_at = :t WHERE id = :n"""),
                     {"c": no, "t": now, "n": nb.id})
    await db.execute(text("""
        INSERT INTO memory.compactions (notebook_id, compaction_no, tokens_before, tokens_after, archived_entries,
                                        summary, model)
        VALUES (:n, :c, :b, :a, :ids, :s, :m)"""),
        {"n": nb.id, "c": no, "b": nb.token_used, "a": after, "ids": ids, "s": summary,
         "m": model or f"rules:{reason}"})
    return {"compaction_no": no, "tokens_before": nb.token_used, "tokens_after": after, "archived": len(ids),
            "summary": summary}


def _summary(archived: list[Any]) -> str:
    """Tóm tắt tất định khi không gọi model: số mục theo mục, khoảng thời gian, các ý chính gần nhất."""
    by_section = Counter(SECTIONS.get(r.section, r.section) for r in archived)
    first = min(r.created_at for r in archived)
    last = max(r.created_at for r in archived)
    parts = ", ".join(f"{n} mục {name.lower()}" for name, n in by_section.most_common())
    heads = "; ".join(r.body[:80] for r in archived[:3])
    return f"{parts} từ {first:%d/%m} đến {last:%d/%m}. Gần nhất: {heads}"


async def due_for_daily(db: AsyncSession, org_id: uuid.UUID) -> list[uuid.UUID]:
    rows = (await db.execute(text("""
        SELECT n.id FROM memory.notebooks n
        WHERE n.org_id = :o AND COALESCE(n.last_compacted_at, n.created_at) < now() - interval '24 hours'
          AND EXISTS (SELECT 1 FROM memory.entries e WHERE e.notebook_id = n.id AND e.archived_at IS NULL
                      AND NOT e.is_pinned AND e.section <> ALL(:never)
                      AND e.created_at > COALESCE(n.last_compacted_at, n.created_at))"""),
        {"o": org_id, "never": list(NEVER_COMPACT)})).scalars().all()
    return list(rows)
