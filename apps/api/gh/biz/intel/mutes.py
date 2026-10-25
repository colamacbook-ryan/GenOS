# ruff: noqa: E501
"""Mute tín hiệu ("loại này đừng báo", "nhóm này bỏ qua") — phạm vi kind | group | person | detector, nguồn `chat` | `pin` (CNS-123).

`active(db, org, kind=, group_id=, person_ids=, detector=)` gộp MỌI phạm vi cho từng tín hiệu và trả `None | "chat" | "pin"`
(PIN thắng chat: Sếp đã tự xác nhận trên Console). `levels.compute` quyết hệ quả: vụ lừa đảo/an toàn bỏ qua mute qua chat; chỉ mute
qua Console + PIN mới hạ được (⚪ dòng cụm). Tạo mute lừa đảo/an toàn qua chat bị từ chối ngay ở đây (§8.1 hạ phòng thủ).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SCOPES = ("kind", "group", "person", "detector")
DEFENSIVE_KINDS = ("rui_ro_lua_dao", "suc_khoe_an_toan")
Source = Literal["chat", "pin"]


class MuteError(ValueError):
    """Mute không hợp lệ (sai phạm vi, hoặc hạ phòng thủ qua chat)."""


@dataclass(frozen=True)
class Mute:
    id: uuid.UUID
    scope_type: str
    scope_value: str
    source: str
    until: datetime | None


async def create(db: AsyncSession, org_id: uuid.UUID, *, scope_type: str, scope_value: str, source: Source,
                 user_id: uuid.UUID | None, until: datetime | None = None) -> Mute:
    if scope_type not in SCOPES:
        raise MuteError(f"phạm vi mute không hợp lệ: {scope_type}")
    if source not in ("chat", "pin"):
        raise MuteError("nguồn mute không hợp lệ")
    if source == "chat" and scope_type == "kind" and scope_value in DEFENSIVE_KINDS:
        raise MuteError("Tắt cảnh báo lừa đảo/an toàn chỉ làm được trên Console có mã PIN")
    r = (await db.execute(text("""
        INSERT INTO biz.signal_mutes (org_id, scope_type, scope_value, source, created_by, until)
        VALUES (:o, :t, :v, :s, :u, :until) RETURNING id"""),
        {"o": org_id, "t": scope_type, "v": scope_value, "s": source, "u": user_id, "until": until})).scalar_one()
    return Mute(r, scope_type, scope_value, source, until)


async def revoke(db: AsyncSession, org_id: uuid.UUID, mute_id: uuid.UUID) -> bool:
    r = await db.execute(text("UPDATE biz.signal_mutes SET revoked_at = now() WHERE id = :i AND org_id = :o AND revoked_at IS NULL"),
                         {"i": mute_id, "o": org_id})
    return bool(r.rowcount)  # type: ignore[attr-defined]


async def active(db: AsyncSession, org_id: uuid.UUID, *, kind: str | None, group_id: uuid.UUID | None = None,
                 person_ids: Iterable[uuid.UUID | str] = (), detector: str | None = None,
                 now: datetime | None = None) -> Source | None:
    pairs: list[tuple[str, str]] = []
    if kind:
        pairs.append(("kind", kind))
    if group_id:
        pairs.append(("group", str(group_id)))
    pairs.extend(("person", str(p)) for p in person_ids)
    if detector:
        pairs.append(("detector", detector))
    if not pairs:
        return None
    rows = (await db.execute(text("""
        SELECT DISTINCT source FROM biz.signal_mutes
        WHERE org_id = :o AND revoked_at IS NULL AND (until IS NULL OR until > COALESCE(:now, now()))
          AND (scope_type || ':' || scope_value) = ANY(:pairs)"""),
        {"o": org_id, "now": now, "pairs": [f"{t}:{v}" for t, v in pairs]})).scalars().all()
    if "pin" in rows:
        return "pin"
    return "chat" if "chat" in rows else None
