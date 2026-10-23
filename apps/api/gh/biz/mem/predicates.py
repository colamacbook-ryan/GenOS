"""Danh mục vị từ (`mem.predicates`, seed 29 ở migration 0043). Thêm vị từ = INSERT qua migration có review (R6)."""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class UnknownPredicate(ValueError):
    """Vị từ lạ — bên gọi bỏ sự thật đó và đếm `unknown_predicate` (§4F)."""


@dataclass(frozen=True)
class Predicate:
    code: str
    cardinality: str            # single | multi
    subject_types: tuple[str, ...]
    object_kind: str            # entity | value
    sensitivity: str            # normal | sensitive | hr
    dispute_signal_kind: str | None
    owner_section: str | None
    auto_supersede: bool
    description_vi: str

    @property
    def is_single(self) -> bool:
        return self.cardinality == "single"


_COLS = """code, cardinality, subject_types, object_kind, sensitivity, dispute_signal_kind, owner_section,
           auto_supersede, description_vi"""


def _row(r: object) -> Predicate:
    return Predicate(r.code, r.cardinality, tuple(r.subject_types), r.object_kind, r.sensitivity,  # type: ignore[attr-defined]
                     r.dispute_signal_kind, r.owner_section, r.auto_supersede, r.description_vi)  # type: ignore[attr-defined]


async def get(db: AsyncSession, code: str) -> Predicate:
    r = (await db.execute(text(f"SELECT {_COLS} FROM mem.predicates WHERE code = :c"), {"c": code})).one_or_none()  # noqa: S608
    if r is None:
        raise UnknownPredicate(code)
    return _row(r)


async def all_predicates(db: AsyncSession) -> list[Predicate]:
    rows = (await db.execute(text(f"SELECT {_COLS} FROM mem.predicates ORDER BY code"))).all()  # noqa: S608
    return [_row(r) for r in rows]


async def codes(db: AsyncSession) -> set[str]:
    return set((await db.execute(text("SELECT code FROM mem.predicates"))).scalars().all())


