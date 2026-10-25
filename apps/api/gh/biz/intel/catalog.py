# ruff: noqa: E501
"""Hàng/dịch vụ của Sếp (`biz.catalog_items`) — phần "hàng" của mục Nguồn lực trong Hồ sơ Sếp (§4C). CRUD cho Console (Owner) và cho
việc riêng "thêm hàng vào catalog" (W8, T2). Khớp cung↔cầu đi qua `score_match` ở T6 — module này không chấm điểm."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

STATES = ("active", "suggested", "archived")


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class Item:
    id: uuid.UUID
    name: str
    description: str | None
    unit: str | None
    price_vnd: int | None
    tags: list[str]
    state: str
    source: str
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["id"] = str(self.id)
        d["created_at"] = self.created_at.isoformat()
        return d


_COLS = "id, name, description, unit, price_vnd, tags, state, source, created_at"


def _row(r: Any) -> Item:
    return Item(r.id, r.name, r.description, r.unit, r.price_vnd, list(r.tags or []), r.state, r.source, r.created_at)


async def create(db: AsyncSession, org_id: uuid.UUID, *, name: str, user_id: uuid.UUID | None, description: str | None = None,
                 unit: str | None = None, price_vnd: int | None = None, tags: list[str] | None = None, source: str = "owner") -> Item:
    if not name.strip():
        raise CatalogError("thiếu tên hàng")
    if source not in ("owner", "suggested"):
        raise CatalogError("nguồn không hợp lệ")
    r = (await db.execute(text(f"""INSERT INTO biz.catalog_items (org_id, name, description, unit, price_vnd, tags, state, source, created_by)
                                   VALUES (:o, :n, :d, :u, :p, :t, :st, :src, :by) RETURNING {_COLS}"""),  # noqa: S608
                          {"o": org_id, "n": name.strip()[:200], "d": description, "u": unit, "p": price_vnd, "t": tags or [],
                           "st": "active" if source == "owner" else "suggested", "src": source, "by": user_id})).one()
    return _row(r)


async def list_items(db: AsyncSession, org_id: uuid.UUID, *, state: str | None = "active") -> list[Item]:
    rows = (await db.execute(text(f"""SELECT {_COLS} FROM biz.catalog_items WHERE org_id = :o AND (CAST(:s AS text) IS NULL OR state = :s)
                                      ORDER BY created_at DESC LIMIT 500"""), {"o": org_id, "s": state})).all()  # noqa: S608
    return [_row(r) for r in rows]


async def update(db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID, **fields: Any) -> Item | None:
    allowed = {k: v for k, v in fields.items() if k in ("name", "description", "unit", "price_vnd", "tags", "state") and v is not None}
    if "state" in allowed and allowed["state"] not in STATES:
        raise CatalogError("trạng thái không hợp lệ")
    if not allowed:
        r = (await db.execute(text(f"SELECT {_COLS} FROM biz.catalog_items WHERE id = :i AND org_id = :o"), {"i": item_id, "o": org_id})).one_or_none()  # noqa: S608
        return _row(r) if r else None
    sets = ", ".join(f"{k} = :{k}" for k in allowed)
    r = (await db.execute(text(f"UPDATE biz.catalog_items SET {sets}, updated_at = now() WHERE id = :i AND org_id = :o RETURNING {_COLS}"),  # noqa: S608
                          {**allowed, "i": item_id, "o": org_id})).one_or_none()
    return _row(r) if r else None


async def archive(db: AsyncSession, org_id: uuid.UUID, item_id: uuid.UUID) -> bool:
    """Xoá = lưu trữ (không xoá dòng)."""
    r = await db.execute(text("UPDATE biz.catalog_items SET state = 'archived', updated_at = now() WHERE id = :i AND org_id = :o"),
                         {"i": item_id, "o": org_id})
    return bool(r.rowcount)  # type: ignore[attr-defined]
