# ruff: noqa: E501
"""Bí danh (L2): "anh Bảy", "Bảy Kho" → thực thể. So khớp trên `alias_folded` = `mem.fold(alias)` (bỏ dấu, chữ thường).

`scope_group_id`: bí danh chỉ đúng trong một nhóm (vd "anh Bảy" ở nhóm kho). Kết quả luôn chuẩn hoá về thực thể đích
(`coalesce(merged_into, id)`).
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: Từ xưng hô đầu câu — bỏ đi để thử khớp lần hai ("anh Bảy" → "bảy"). Đã bỏ dấu, chữ thường.
HONORIFICS = frozenset({"anh", "chi", "em", "co", "chu", "bac", "ong", "ba", "thay", "mr", "ms", "mrs", "a", "c", "bo", "me"})
FUZZY_THRESHOLD = 0.55
ALIAS_SOURCES = ("display_name", "mention", "owner", "merge", "import")


@dataclass(frozen=True)
class AliasMatch:
    entity_id: uuid.UUID       # đã chuẩn hoá về thực thể đích
    alias: str
    method: str                # alias_exact | alias_fuzzy
    score: float
    scoped: bool               # khớp bí danh riêng của nhóm


async def fold(db: AsyncSession, s: str) -> str:
    return str((await db.execute(text("SELECT mem.fold(:s)"), {"s": s})).scalar_one())


async def add(db: AsyncSession, org_id: uuid.UUID, entity_id: uuid.UUID, alias: str, *,
              scope_group_id: uuid.UUID | None = None, source: str = "mention", confidence: float = 0.5,
              evidence_raw_id: uuid.UUID | None = None) -> uuid.UUID | None:
    """Thêm (hoặc làm mới `last_seen_at`) một bí danh. Chữ rỗng ⇒ None. Gọi lại an toàn."""
    alias = " ".join((alias or "").split())
    if not alias:
        return None
    if source not in ALIAS_SOURCES:
        raise ValueError(f"nguồn bí danh không hợp lệ: {source}")
    from gh.biz.mem import entities

    target = await entities.canonical(db, entity_id)
    return (await db.execute(text("""
        INSERT INTO mem.aliases (org_id, entity_id, alias, scope_group_id, source, confidence, evidence_raw_id)
        VALUES (:o, :e, :a, :g, :s, :c, :r)
        ON CONFLICT (entity_id, alias_folded, coalesce(scope_group_id, '00000000-0000-0000-0000-000000000000'::uuid))
        DO UPDATE SET last_seen_at = now(), confidence = greatest(mem.aliases.confidence, EXCLUDED.confidence)
        RETURNING id"""), {"o": org_id, "e": target, "a": alias, "g": scope_group_id, "s": source,
                           "c": confidence, "r": evidence_raw_id})).scalar_one()  # type: ignore[no-any-return]


def _candidates(folded: str) -> list[str]:
    out = [folded]
    parts = folded.split()
    while len(parts) > 1 and parts[0] in HONORIFICS:
        parts = parts[1:]
        out.append(" ".join(parts))
    return out


async def resolve(db: AsyncSession, org_id: uuid.UUID, surface: str, group_id: uuid.UUID | None = None,
                  *, limit: int = 5) -> list[AliasMatch]:
    """Tìm thực thể theo cách gọi. Khớp CHÍNH XÁC (cả khi bỏ từ xưng hô) trước, gần đúng (trigram) sau; bí danh riêng
    của nhóm xếp trước bí danh chung. Mỗi thực thể đích xuất hiện một lần (kết quả tốt nhất)."""
    folded = await fold(db, " ".join((surface or "").split()))
    if not folded:
        return []
    best: dict[uuid.UUID, AliasMatch] = {}

    def keep(m: AliasMatch) -> None:
        cur = best.get(m.entity_id)
        if cur is None or (m.method == "alias_exact", m.scoped, m.score) > (cur.method == "alias_exact", cur.scoped, cur.score):
            best[m.entity_id] = m

    for i, cand in enumerate(_candidates(folded)):
        rows = (await db.execute(text("""
            SELECT coalesce(e.merged_into, e.id) AS eid, a.alias, a.confidence, (a.scope_group_id IS NOT NULL) AS scoped
            FROM mem.aliases a JOIN mem.entities e ON e.id = a.entity_id
            WHERE a.org_id = :o AND a.alias_folded = :f AND e.forgotten_at IS NULL
              AND (a.scope_group_id IS NULL OR a.scope_group_id = CAST(:g AS uuid))"""),
            {"o": org_id, "f": cand, "g": group_id})).all()
        for r in rows:
            keep(AliasMatch(r.eid, r.alias, "alias_exact", float(r.confidence) * (1.0 if i == 0 else 0.9), bool(r.scoped)))
        if best:
            break
    if not best:
        rows = (await db.execute(text("""
            SELECT coalesce(e.merged_into, e.id) AS eid, a.alias, similarity(a.alias_folded, :f) AS sim,
                   (a.scope_group_id IS NOT NULL) AS scoped
            FROM mem.aliases a JOIN mem.entities e ON e.id = a.entity_id
            WHERE a.org_id = :o AND a.alias_folded % :f AND e.forgotten_at IS NULL
              AND (a.scope_group_id IS NULL OR a.scope_group_id = CAST(:g AS uuid))
              AND similarity(a.alias_folded, :f) >= :t
            ORDER BY sim DESC LIMIT :n"""),
            {"o": org_id, "f": folded, "g": group_id, "t": FUZZY_THRESHOLD, "n": limit * 3})).all()
        for r in rows:
            keep(AliasMatch(r.eid, r.alias, "alias_fuzzy", float(r.sim), bool(r.scoped)))
    out = sorted(best.values(), key=lambda m: (m.method == "alias_exact", m.scoped, m.score), reverse=True)
    return out[:limit]
