"""Lưu hội thoại Gen (agent.gen_conversations / agent.gen_messages — migration 0016) + cấu hình cờ `gen.enabled`.

Cấu hình nằm ở `core.organizations.settings->'gen'`: `{"enabled": bool, "roles": [...], "retention_days": int}`.
Mặc định (quyết định §9.1, §9.4): bật, chỉ vai trò Owner, giữ 90 ngày. Hội thoại chỉ chủ nhân đọc được (§9.3).
"""

import uuid
from datetime import datetime
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import service

DEFAULTS: dict[str, Any] = {"enabled": True, "roles": ["owner"], "retention_days": 90}
MIN_RETENTION, MAX_RETENTION = 7, 3650


async def get_settings(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    raw = (await db.execute(text("SELECT settings->'gen' FROM core.organizations WHERE id = :o"),
                            {"o": org_id})).scalar_one_or_none()
    cfg = dict(DEFAULTS)
    if isinstance(raw, dict):
        cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
    return cfg


async def save_settings(db: AsyncSession, org_id: uuid.UUID, cfg: dict[str, Any]) -> None:
    await db.execute(text("""UPDATE core.organizations
                             SET settings = jsonb_set(settings, '{gen}', CAST(:c AS jsonb), true) WHERE id = :o"""),
                     {"c": orjson.dumps(cfg).decode(), "o": org_id})


def available(cfg: dict[str, Any], user: service.CurrentUser) -> bool:
    return bool(cfg.get("enabled")) and user.role_code in (cfg.get("roles") or [])


def _conv(r: Any) -> dict[str, Any]:
    return {"id": str(r.id), "title": r.title, "created_at": r.created_at.isoformat().replace("+00:00", "Z"),
            "last_at": r.last_at.isoformat().replace("+00:00", "Z"),
            "kind": "briefing" if getattr(r, "is_briefing", False) else "chat"}


async def create_conversation(db: AsyncSession, user: service.CurrentUser, title: str) -> uuid.UUID:
    cid: uuid.UUID = (await db.execute(text("""INSERT INTO agent.gen_conversations (org_id, user_id, title)
                                               VALUES (:o, :u, :t) RETURNING id"""),
                                       {"o": user.org_id, "u": user.id, "t": title[:80]})).scalar_one()
    return cid


async def owned(db: AsyncSession, user: service.CurrentUser, cid: uuid.UUID) -> bool:
    """Chỉ CHỦ hội thoại — kể cả Owner cũng không đọc hội thoại của người khác."""
    return (await db.execute(text("""SELECT 1 FROM agent.gen_conversations
                                     WHERE id = :i AND org_id = :o AND user_id = :u"""),
                             {"i": cid, "o": user.org_id, "u": user.id})).first() is not None


async def list_conversations(db: AsyncSession, user: service.CurrentUser, limit: int = 30) -> list[dict[str, Any]]:
    """v0.1.41 (F-84): mỗi mục có "kind" = 'briefing' nếu hội thoại có tin Bản tin Gen (content kind='briefing'),
    ngược lại 'chat' — danh sách "Hội thoại cũ" gắn nhãn."""
    rows = (await db.execute(text("""
        SELECT c.id, c.title, c.created_at, c.last_at,
               EXISTS (SELECT 1 FROM agent.gen_messages gm
                       WHERE gm.conversation_id = c.id AND gm.content->>'kind' = 'briefing') AS is_briefing
        FROM agent.gen_conversations c
        WHERE c.org_id = :o AND c.user_id = :u ORDER BY c.last_at DESC LIMIT :l"""),
                             {"o": user.org_id, "u": user.id, "l": limit})).all()
    return [_conv(r) for r in rows]


async def add_message(db: AsyncSession, org_id: uuid.UUID, cid: uuid.UUID, role: str, content: dict[str, Any],
                      turn_id: uuid.UUID | None = None) -> None:
    await db.execute(text("""INSERT INTO agent.gen_messages (org_id, conversation_id, turn_id, role, content)
                             VALUES (:o, :c, :t, :r, CAST(:x AS jsonb))"""),
                     {"o": org_id, "c": cid, "t": turn_id, "r": role, "x": orjson.dumps(content).decode()})
    await db.execute(text("UPDATE agent.gen_conversations SET last_at = now() WHERE id = :c"), {"c": cid})


async def list_messages(db: AsyncSession, cid: uuid.UUID, limit: int = 200,
                        before: datetime | None = None) -> list[dict[str, Any]]:
    """`limit` tin MỚI NHẤT (cũ → mới). `before` = mốc created_at để lấy trang cũ hơn.

    v0.1.41 (F-84): mỗi tin có "feedback" = 'helpful' | 'not_helpful' | None (đánh giá của chủ hội thoại cho câu trả
    lời — chỉ chủ hội thoại chấm được nên mỗi lượt có tối đa một dòng)."""
    rows = (await db.execute(text("""
        SELECT m.id, m.turn_id, m.role, m.content, m.created_at, f.rating AS feedback
        FROM agent.gen_messages m
        LEFT JOIN agent.gen_feedback f ON f.conversation_id = m.conversation_id AND f.turn_id = m.turn_id
                                       AND m.role = 'assistant'
        WHERE m.conversation_id = :c AND (CAST(:b AS timestamptz) IS NULL OR m.created_at < :b)
        ORDER BY m.created_at DESC, m.id DESC LIMIT :l"""),
                             {"c": cid, "l": limit, "b": before})).all()
    return [{"id": str(r.id), "turn_id": str(r.turn_id) if r.turn_id else None, "role": r.role,
             "content": r.content, "created_at": r.created_at.isoformat().replace("+00:00", "Z"),
             "feedback": r.feedback}
            for r in reversed(rows)]


async def set_feedback(db: AsyncSession, user: service.CurrentUser, cid: uuid.UUID, turn_id: uuid.UUID,
                       rating: str) -> str | None:
    """v0.1.41 (F-84): lưu/đổi đánh giá "Hữu ích / Không hữu ích" cho câu trả lời `turn_id` của hội thoại `cid` (bên
    gọi đã kiểm chủ hội thoại). Không có tin trả lời của lượt đó ⇒ None.
    Trả kind: 'briefing' (Bản tin Gen) | 'reply'."""
    r = (await db.execute(text("""
        SELECT count(*) AS n, COALESCE(bool_or(content->>'kind' = 'briefing'), false) AS briefing
        FROM agent.gen_messages WHERE conversation_id = :c AND turn_id = :t AND role = 'assistant'"""),
                          {"c": cid, "t": turn_id})).one()
    if not r.n:
        return None
    kind = "briefing" if r.briefing else "reply"
    await db.execute(text("""
        INSERT INTO agent.gen_feedback (org_id, user_id, conversation_id, turn_id, kind, rating)
        VALUES (:o, :u, :c, :t, :k, :r)
        ON CONFLICT (user_id, turn_id) DO UPDATE SET rating = EXCLUDED.rating, kind = EXCLUDED.kind,
               conversation_id = EXCLUDED.conversation_id, updated_at = now()"""),
                     {"o": user.org_id, "u": user.id, "c": cid, "t": turn_id, "k": kind, "r": rating})
    return kind


async def delete_feedback(db: AsyncSession, user: service.CurrentUser, turn_id: uuid.UUID) -> None:
    """Bỏ chấm — chỉ xoá đánh giá của chính người dùng."""
    await db.execute(text("DELETE FROM agent.gen_feedback WHERE org_id = :o AND user_id = :u AND turn_id = :t"),
                     {"o": user.org_id, "u": user.id, "t": turn_id})


async def update_proposal_step(db: AsyncSession, cid: uuid.UUID, turn_id: uuid.UUID, pid: str,
                               patch: dict[str, Any]) -> None:
    """Gen v2: ghi trạng thái đề xuất (đã xác nhận / đã huỷ + kết quả) vào tin trả lời đã lưu — mở lại hội thoại
    thì thẻ hiện đúng trạng thái, không hiện lại nút Xác nhận."""
    rows = (await db.execute(text("""SELECT id, content FROM agent.gen_messages
                                     WHERE conversation_id = :c AND turn_id = :t AND role = 'assistant'"""),
                             {"c": cid, "t": turn_id})).all()
    for r in rows:
        content = dict(r.content or {})
        changed = False
        for st in content.get("steps") or []:
            p = st.get("proposal") if isinstance(st, dict) and st.get("kind") == "proposal" else None
            if isinstance(p, dict) and p.get("id") == pid:
                p.update(patch)
                changed = True
        if changed:
            await db.execute(text("UPDATE agent.gen_messages SET content = CAST(:x AS jsonb) WHERE id = :i"),
                             {"x": orjson.dumps(content).decode(), "i": r.id})


async def delete_conversation(db: AsyncSession, cid: uuid.UUID) -> None:
    await db.execute(text("DELETE FROM agent.gen_conversations WHERE id = :c"), {"c": cid})


async def purge_expired(db: AsyncSession) -> int:
    """Xoá hội thoại có tin cuối cũ hơn hạn lưu của tổ chức (mặc định 90 ngày) — tin nhắn và đánh giá Hữu ích
    (agent.gen_feedback, v0.1.41) xoá theo (CASCADE)."""
    total = 0
    for org_id in (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all():
        days = int((await get_settings(db, org_id))["retention_days"])
        n = (await db.execute(text("""DELETE FROM agent.gen_conversations
                                      WHERE org_id = :o AND last_at < now() - make_interval(days => :d)"""),
                              {"o": org_id, "d": days})).rowcount  # type: ignore[attr-defined]
        total += int(n or 0)
    return total
