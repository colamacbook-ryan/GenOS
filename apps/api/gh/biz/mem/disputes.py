# ruff: noqa: E501
"""Tranh chấp sự thật (hàng chờ): Đợt 1 chỉ GHI; T1 (Đợt 2) chiếu sang tín hiệu (CNS-076, `projected_signal_id`).

Ba đường đóng (CNS-107): (i) Sếp chọn bên thắng (`resolve(..., by='owner')`); (ii) job đêm theo bằng chứng mới (L4 gọi
`resolve(..., by='consolidator')`, KHÔNG áp cho `payment_account`); (iii) hết hạn 30 ngày ⇒ cả hai `retracted`
(`expire_stale`, cũng không áp cho `payment_account`: chỉ Sếp đóng).

Hợp đồng L1: `disputes.open(...)`, `disputes.list(...)`. (Tên `list` che hàm dựng sẵn trong module này nên mã dùng
`_list`.)
"""

from __future__ import annotations

import builtins
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_list = builtins.list
ONLY_OWNER_PREDICATES = frozenset({"payment_account"})
EXPIRY_DAYS = 30


class DisputeError(ValueError):
    """Đóng tranh chấp không hợp lệ (sai người đóng, bên thắng không hợp lệ, chồng khoảng…)."""


@dataclass(frozen=True)
class Dispute:
    id: uuid.UUID
    org_id: uuid.UUID
    subject_id: uuid.UUID
    predicate: str
    fact_ids: tuple[uuid.UUID, ...]
    dispute_signal_kind: str | None
    opened_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None
    winner_fact_id: uuid.UUID | None
    note: str | None


_COLS = ("id, org_id, subject_id, predicate, fact_ids, dispute_signal_kind, opened_at, resolved_at, resolved_by, "
         "winner_fact_id, note")


def _d(r: Any) -> Dispute:
    return Dispute(r.id, r.org_id, r.subject_id, r.predicate, tuple(r.fact_ids or ()), r.dispute_signal_kind,
                   r.opened_at, r.resolved_at, r.resolved_by, r.winner_fact_id, r.note)


async def set_actor(db: AsyncSession, by: str, reason: str | None = None) -> None:
    """Đặt biến phiên (SET LOCAL) cho trigger `mem.facts_guard` ghi `mem.fact_status_events.by/reason`."""
    await db.execute(text("SELECT set_config('gh.mem_by', :b, true), set_config('gh.mem_reason', :r, true)"),
                     {"b": by, "r": reason or ""})


async def open(db: AsyncSession, org_id: uuid.UUID, subject_id: uuid.UUID, predicate: str,  # noqa: A001
               fact_ids: Any, *, signal_kind: str | None = None, note: str | None = None
               ) -> tuple[uuid.UUID, bool]:
    """Mở tranh chấp mới cho (chủ thể, vị từ) hoặc GỘP vào tranh chấp đang mở của cặp đó (một tranh chấp mở / cặp).
    Trả (dispute_id, mới_tạo?). `subject_id` phải là thực thể đích. Mọi `fact_ids` được gắn `dispute_id`."""
    ids = _list(dict.fromkeys(fact_ids))
    row = (await db.execute(text("""
        SELECT id FROM mem.disputes WHERE org_id = :o AND subject_id = :s AND predicate = :p AND resolved_at IS NULL
        ORDER BY opened_at LIMIT 1 FOR UPDATE"""), {"o": org_id, "s": subject_id, "p": predicate})).scalar_one_or_none()
    created = row is None
    if row is None:
        row = (await db.execute(text("""
            INSERT INTO mem.disputes (org_id, subject_id, predicate, fact_ids, dispute_signal_kind, note)
            VALUES (:o, :s, :p, :f, :k, :n) RETURNING id"""),
            {"o": org_id, "s": subject_id, "p": predicate, "f": ids, "k": signal_kind, "n": note})).scalar_one()
    else:
        await db.execute(text("""
            UPDATE mem.disputes SET fact_ids = (SELECT array_agg(DISTINCT x) FROM unnest(fact_ids || CAST(:f AS uuid[])) x),
                                    note = coalesce(CAST(:n AS text), note)
            WHERE id = :i"""), {"f": ids, "i": row, "n": note})
    if ids:
        await db.execute(text("UPDATE mem.facts SET dispute_id = :d WHERE id = ANY(:f) AND dispute_id IS DISTINCT FROM :d"),
                         {"d": row, "f": ids})
    return row, created  # type: ignore[return-value]


async def list(db: AsyncSession, org_id: uuid.UUID, *, open_only: bool = True,  # noqa: A001
               subject_id: uuid.UUID | None = None, limit: int = 100) -> Any:
    rows = (await db.execute(text(f"""
        SELECT {_COLS} FROM mem.disputes
        WHERE org_id = :o AND (NOT :oo OR resolved_at IS NULL) AND (CAST(:s AS uuid) IS NULL OR subject_id = CAST(:s AS uuid))
        ORDER BY opened_at DESC LIMIT :n"""),  # noqa: S608
        {"o": org_id, "oo": open_only, "s": subject_id, "n": limit})).all()
    return [_d(r) for r in rows]


async def get(db: AsyncSession, org_id: uuid.UUID, dispute_id: uuid.UUID) -> Dispute | None:
    r = (await db.execute(text(f"SELECT {_COLS} FROM mem.disputes WHERE id = :i AND org_id = :o"),  # noqa: S608
                          {"i": dispute_id, "o": org_id})).one_or_none()
    return None if r is None else _d(r)


async def resolve(db: AsyncSession, org_id: uuid.UUID, dispute_id: uuid.UUID, winner_fact_id: uuid.UUID, *,
                  by: str, user_id: uuid.UUID | None = None) -> Dispute:
    """Đóng tranh chấp: bên thắng trở lại `asserted` (hoặc `owner_confirmed` khi Sếp chọn và có `user_id`), bên thua
    `superseded` (`superseded_by` = bên thắng). Bên thắng có thể là một sự thật MỚI nằm ngoài danh sách (Sếp nói lại)."""
    if by not in ("owner", "consolidator"):
        raise DisputeError("by phải là owner hoặc consolidator")
    d = (await db.execute(text(f"SELECT {_COLS} FROM mem.disputes WHERE id = :i AND org_id = :o FOR UPDATE"),  # noqa: S608
                          {"i": dispute_id, "o": org_id})).one_or_none()
    if d is None:
        raise DisputeError("không có tranh chấp này")
    if d.resolved_at is not None:
        raise DisputeError("tranh chấp đã đóng")
    if d.predicate in ONLY_OWNER_PREDICATES and by != "owner":
        raise DisputeError(f"{d.predicate}: chỉ Sếp đóng được tranh chấp này (Console + PIN)")
    win = (await db.execute(text("SELECT id, status, org_id FROM mem.facts WHERE id = :i FOR UPDATE"),
                            {"i": winner_fact_id})).one_or_none()
    if win is None or win.org_id != org_id:
        raise DisputeError("bên thắng không tồn tại")
    await set_actor(db, by, "dispute_resolved")
    losers = [f for f in (d.fact_ids or []) if f != winner_fact_id]
    if losers:
        await db.execute(text("""
            UPDATE mem.facts SET status = 'superseded', superseded_by = :w, closed_recorded_at = now()
            WHERE id = ANY(:l) AND status IN ('disputed', 'asserted', 'owner_confirmed', 'proposed')"""),
            {"w": winner_fact_id, "l": losers})
    if win.status in ("disputed", "proposed"):
        if by == "owner" and user_id is not None:
            await db.execute(text("""UPDATE mem.facts SET status = 'owner_confirmed', confirmed_by = :u, confirmed_at = now(),
                                     closed_recorded_at = now() WHERE id = :w AND source_class NOT IN ('outsider', 'forwarded')"""),
                             {"u": user_id, "w": winner_fact_id})
            await db.execute(text("""UPDATE mem.facts SET status = 'asserted', closed_recorded_at = now()
                                     WHERE id = :w AND status = 'disputed'"""), {"w": winner_fact_id})
        else:
            await db.execute(text("UPDATE mem.facts SET status = 'asserted', closed_recorded_at = now() WHERE id = :w"),
                             {"w": winner_fact_id})
    await db.execute(text("""UPDATE mem.disputes SET resolved_at = now(), resolved_by = :b, winner_fact_id = :w
                             WHERE id = :i"""), {"b": by, "w": winner_fact_id, "i": dispute_id})
    got = await get(db, org_id, dispute_id)
    assert got is not None
    return got


async def expire_stale(db: AsyncSession, org_id: uuid.UUID, *, days: int = EXPIRY_DAYS) -> int:
    """Hết hạn `days` ngày không ai đóng ⇒ mọi bên `retracted`, `resolved_by='expiry'`. Không áp cho `payment_account`."""
    rows = (await db.execute(text("""
        SELECT id, fact_ids FROM mem.disputes
        WHERE org_id = :o AND resolved_at IS NULL AND predicate <> ALL(:op)
          AND opened_at < now() - make_interval(days => :d) FOR UPDATE"""),
        {"o": org_id, "op": _list(ONLY_OWNER_PREDICATES), "d": days})).all()
    for r in rows:
        await set_actor(db, "consolidator", "dispute_expired")
        await db.execute(text("""UPDATE mem.facts SET status = 'retracted', closed_recorded_at = now(),
                                   valid_to = coalesce(valid_to, greatest(now(), valid_from))
                                 WHERE id = ANY(:f) AND status = 'disputed'"""), {"f": _list(r.fact_ids or [])})
        await db.execute(text("UPDATE mem.disputes SET resolved_at = now(), resolved_by = 'expiry' WHERE id = :i"),
                         {"i": r.id})
    return len(rows)
