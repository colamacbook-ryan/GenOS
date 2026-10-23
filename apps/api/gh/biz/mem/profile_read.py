# ruff: noqa: E501
"""Đọc Hồ sơ Sếp (owner model, §4C): `snapshot(org)` là NGUỒN DUY NHẤT cho prompt (L3) và cho `levels.compute` (T1). CHỈ gồm
`owner_confirmed` đang hiệu lực — `proposed` ("em đoán") chỉ hiển thị ở mặt "Tôi", không bao giờ vào đây (R7)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SECTIONS = ("goals", "resources", "key_relations", "values_limits", "rhythm", "report_prefs")
SECTION_LABEL_VI = {"goals": "Mục tiêu & ưu tiên", "resources": "Nguồn lực", "key_relations": "Vai trò & quan hệ quan trọng",
                    "values_limits": "Giá trị & giới hạn", "rhythm": "Lịch & nhịp sống", "report_prefs": "Cách báo"}


def value_text(value: Any) -> str:
    """Chữ hiển thị của một giá trị sự thật: chuỗi ⇒ chính nó; {"text": …} ⇒ text; còn lại ⇒ JSON gọn."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return str(value["text"])
    return orjson.dumps(value).decode()


@dataclass(frozen=True)
class ProfileItem:
    fact_id: uuid.UUID
    section: str
    predicate: str
    text: str
    object_entity_id: uuid.UUID | None
    valid_from: datetime
    evidence_n: int


@dataclass(frozen=True)
class ProfileSnapshot:
    org_id: uuid.UUID
    version: int
    owner_entity_id: uuid.UUID | None
    items: tuple[ProfileItem, ...] = field(default_factory=tuple)

    def by_section(self) -> dict[str, list[ProfileItem]]:
        out: dict[str, list[ProfileItem]] = {s: [] for s in SECTIONS}
        for it in self.items:
            out.setdefault(it.section, []).append(it)
        return out

    def fact_ids(self) -> frozenset[uuid.UUID]:
        return frozenset(i.fact_id for i in self.items)

    def has(self, fact_id: uuid.UUID) -> bool:
        return fact_id in self.fact_ids()

    def key_relation_entity_ids(self) -> frozenset[uuid.UUID]:
        return frozenset(i.object_entity_id for i in self.items
                         if i.predicate == "owner_key_relation" and i.object_entity_id is not None)

    def render_for_prompt(self) -> str:
        """Khối Hồ sơ trong prompt: mỗi dòng mang `f_<fact_id>` để `owner_relevance.fact_id` của model kiểm được bằng code."""
        if not self.items:
            return "Hồ sơ Sếp: (chưa có mục nào được Sếp xác nhận)"
        lines = [f"Hồ sơ Sếp (phiên bản {self.version}, chỉ mục Sếp đã xác nhận):"]
        for s, items in self.by_section().items():
            if items:
                lines.append(f"{SECTION_LABEL_VI.get(s, s)}:")
                lines.extend(f"- [f_{i.fact_id}] {i.text}" for i in items)
        return "\n".join(lines)


async def snapshot(db: AsyncSession, org_id: uuid.UUID) -> ProfileSnapshot:
    """Hồ sơ Sếp đang hiệu lực, chỉ `owner_confirmed` (kể cả sự thật gắn với thực thể đã gộp vào thực thể Sếp)."""
    owner = (await db.execute(text("SELECT id FROM mem.entities WHERE org_id = :o AND is_owner"), {"o": org_id})).scalar_one_or_none()
    ver = (await db.execute(text("SELECT version FROM mem.owner_profile WHERE org_id = :o"), {"o": org_id})).scalar_one_or_none()
    if owner is None:
        return ProfileSnapshot(org_id, int(ver or 0), None, ())
    rows = (await db.execute(text("""
        SELECT v.fact_id, v.owner_section, v.predicate, v.object_value, v.object_entity_id, v.valid_from, v.evidence_n,
               e.display_name AS obj_name
        FROM mem.v_owner_profile v LEFT JOIN mem.entities e ON e.id = v.object_entity_id
        WHERE v.org_id = :o AND v.status = 'owner_confirmed'
        ORDER BY v.owner_section, v.valid_from, v.fact_id"""), {"o": org_id})).all()
    items = tuple(ProfileItem(r.fact_id, r.owner_section, r.predicate,
                              r.obj_name if r.object_entity_id is not None else value_text(r.object_value),
                              r.object_entity_id, r.valid_from, r.evidence_n) for r in rows)
    return ProfileSnapshot(org_id, int(ver or 0), owner, items)
