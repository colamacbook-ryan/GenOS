# ruff: noqa: E501
"""Tiện ích test lõi trí nhớ (v0.1.76, gói L1): tổ chức/thực thể giả, dựng `FactIn`, đọc dòng sự thật gọn."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.mem import entities
from gh.biz.mem.facts import EvidenceIn, FactIn


def T(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def day_of(t: datetime | None) -> int | None:
    return None if t is None else t.astimezone(UTC).day


async def mk_org(db: AsyncSession, name: str = "Tổ chức thử") -> uuid.UUID:
    oid = (await db.execute(text("INSERT INTO core.organizations (name) VALUES (:n) RETURNING id"), {"n": name})).scalar_one()
    await db.commit()
    return oid  # type: ignore[no-any-return]


async def mk_entity(db: AsyncSession, org: uuid.UUID, name: str = "Lan", etype: str = "person") -> uuid.UUID:
    e = await entities.create(db, org, etype, name)
    await db.commit()
    return e.id


async def mk_person_row(db: AsyncSession, org: uuid.UUID, name: str) -> uuid.UUID:
    pid = (await db.execute(text("""INSERT INTO core.persons (org_id, code, display_name)
                                    VALUES (:o, core.next_code('PER'), :n) RETURNING id"""), {"o": org, "n": name})).scalar_one()
    await db.commit()
    return pid  # type: ignore[no-any-return]


def evid(raw: uuid.UUID | None = None, *, verified: bool = True) -> EvidenceIn:
    raw = raw or uuid.uuid4()
    span = {"source_field": "body", "span_start": 0, "span_end": 12} if verified else None
    return EvidenceIn("raw_event", raw_event_id=raw, span=span)


def mkfact(org: uuid.UUID, subject: uuid.UUID, value: Any = "0901", at: datetime | None = None, *,
           predicate: str = "contact_phone", cls: str = "insider", speaker: uuid.UUID | None = None,
           raw: uuid.UUID | None = None, ver: str | None = None, recv: datetime | None = None,
           verified: bool = True, object_entity: uuid.UUID | None = None, **kw: Any) -> FactIn:
    raw = raw or uuid.uuid4()
    return FactIn(org_id=org, subject_id=subject, predicate=predicate, valid_from=at or T(1),
                  object_value=None if object_entity is not None else value, object_entity_id=object_entity,
                  source_class=cls, speaker_entity_id=speaker, source_raw_event_id=raw, source_received_at=recv,
                  extractor_version=ver, evidence=[evid(raw, verified=verified)], **kw)


async def rows(db: AsyncSession, subject: uuid.UUID, predicate: str = "contact_phone",
               statuses: tuple[str, ...] | None = None) -> list[tuple[int | None, int | None, str, Any]]:
    """[(ngày bắt đầu, ngày kết thúc|None, status, giá trị)] theo thời gian sự việc."""
    r = (await db.execute(text("""SELECT valid_from, valid_to, status, object_value FROM mem.facts
                                  WHERE subject_id = :s AND predicate = :p ORDER BY valid_from, recorded_at, id"""),
                          {"s": subject, "p": predicate})).all()
    return [(day_of(x.valid_from), day_of(x.valid_to), x.status, x.object_value) for x in r
            if statuses is None or x.status in statuses]


async def assert_no_overlap(db: AsyncSession, subject: uuid.UUID, predicate: str = "contact_phone") -> None:
    """Sau mỗi ca: không có hai dòng asserted/owner_confirmed chồng khoảng cùng (chủ thể, vị từ) đơn trị."""
    n = (await db.execute(text("""
        SELECT count(*) FROM mem.facts a JOIN mem.facts b ON a.id < b.id AND a.subject_id = b.subject_id
         AND a.predicate = b.predicate AND a.status IN ('asserted', 'owner_confirmed') AND b.status IN ('asserted', 'owner_confirmed')
         AND tstzrange(a.valid_from, coalesce(a.valid_to, 'infinity')) && tstzrange(b.valid_from, coalesce(b.valid_to, 'infinity'))
        WHERE a.subject_id = :s AND a.predicate = :p"""), {"s": subject, "p": predicate})).scalar_one()
    assert n == 0, f"{n} cặp dòng đơn trị chồng khoảng"


async def n_disputes(db: AsyncSession, subject: uuid.UUID | None = None) -> int:
    q = "SELECT count(*) FROM mem.disputes" + (" WHERE subject_id = :s" if subject else "")
    return int((await db.execute(text(q), {"s": subject})).scalar_one())


def pay(tag: str = "a", last3: str = "123", bank: str | None = None) -> dict[str, str]:
    """Giá trị `payment_account` hợp lệ: digest 64 hex (băm khoá của số tài khoản — ở đây giả bằng sha256 của `tag`)."""
    import hashlib

    v = {"digest": hashlib.sha256(tag.encode()).hexdigest(), "last3": last3}
    if bank:
        v["bank"] = bank
    return v
