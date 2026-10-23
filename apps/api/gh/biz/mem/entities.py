# ruff: noqa: E501
"""Thực thể (L2): một nơi đặt `subject_id` cho mọi sự thật.

Dòng cho person/group tạo LƯỜI qua `ensure_person` / `ensure_group` (không sửa đường ingest). Gộp = đặt `merged_into`
(chuyển hướng, KHÔNG dời dữ liệu; trigger bất biến của `mem.facts` và EXCLUDE không bị đụng). Nén đường đi (CNS-103):
gộp B → C thì mọi entity đang trỏ B đổi sang trỏ C trong cùng giao dịch ⇒ `coalesce(merged_into, id)` một bậc luôn đủ.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import orjson
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from gh.errors import ApiError

T = TypeVar("T")

ENTITY_TYPES = ("person", "group", "org", "place", "item")


def _sqlstate(e: BaseException) -> str | None:
    orig = getattr(e, "orig", None)
    return getattr(orig, "sqlstate", None) or getattr(getattr(orig, "__cause__", None), "sqlstate", None)


async def retry_deadlock(db: AsyncSession, op: Callable[[], Awaitable[T]]) -> T:
    """Chạy `op` trong SAVEPOINT; deadlock (40P01) ⇒ thử lại MỘT lần; vẫn lỗi ⇒ 409 MEM_BUSY thân thiện + "Chi tiết kỹ thuật"
    (không để 500). Dùng cho gộp/quên người từ Console, nơi tranh khoá với worker ghi sự thật."""
    last: DBAPIError | None = None
    for _ in range(2):
        try:
            async with db.begin_nested():
                return await op()
        except DBAPIError as e:
            if _sqlstate(e) != "40P01":
                raise
            last = e
    raise ApiError(409, "MEM_BUSY", "Trí nhớ đang bận ghi dữ liệu khác — thử lại sau ít giây",
                   detail="deadlock_detected (40P01) sau 2 lần thử") from last


class EntityError(ValueError):
    """Thao tác thực thể không hợp lệ (gộp sai chiều, khác tổ chức…)."""


@dataclass(frozen=True)
class Entity:
    id: uuid.UUID
    org_id: uuid.UUID
    entity_type: str
    person_id: uuid.UUID | None
    group_id: uuid.UUID | None
    display_name: str
    is_owner: bool
    merged_into: uuid.UUID | None
    attrs: dict[str, Any]
    forgotten: bool


_COLS = "id, org_id, entity_type, person_id, group_id, display_name, is_owner, merged_into, attrs, forgotten_at"


def _entity(r: Any) -> Entity:
    return Entity(r.id, r.org_id, r.entity_type, r.person_id, r.group_id, r.display_name, r.is_owner, r.merged_into,
                  dict(r.attrs or {}), r.forgotten_at is not None)


async def get(db: AsyncSession, entity_id: uuid.UUID, *, lock: str | None = None) -> Entity | None:
    """`lock='share'` (ghi sự thật) / `'update'` (quên người, gộp): khoá dòng thực thể để quên người và worker ghi không đua nhau."""
    suffix = {"share": " FOR SHARE", "update": " FOR UPDATE", None: ""}[lock]
    r = (await db.execute(text(f"SELECT {_COLS} FROM mem.entities WHERE id = :i{suffix}"), {"i": entity_id})).one_or_none()  # noqa: S608
    return None if r is None else _entity(r)


async def lock_rows(db: AsyncSession, ids: list[uuid.UUID], mode: str) -> None:
    """Khoá nhiều dòng thực thể theo thứ tự id tăng dần (`share` = ghi sự thật; `update` = quên người/gộp) — mọi bên cùng thứ
    tự nên không deadlock; quên người chờ các lượt ghi đang chạy, lượt ghi sau đó thấy `forgotten_at` và bị từ chối."""
    uniq = sorted(set(ids))
    if uniq:
        suffix = {"share": "FOR SHARE", "update": "FOR UPDATE"}[mode]
        await db.execute(text(f"SELECT id FROM mem.entities WHERE id = ANY(:i) ORDER BY id {suffix}"), {"i": uniq})  # noqa: S608


async def require(db: AsyncSession, org_id: uuid.UUID, entity_id: uuid.UUID, *, lock: str | None = None,
                  allow_forgotten: bool = True) -> Entity:
    """Thực thể PHẢI tồn tại, thuộc đúng tổ chức (và, nếu `allow_forgotten=False`, chưa bị quên) — mọi chỗ nhận id thực thể
    từ bên ngoài đều qua đây (chống tham chiếu chéo tổ chức, chống ghi vào người đã quên)."""
    e = await get(db, entity_id, lock=lock)
    if e is None or e.org_id != org_id:
        raise EntityError(f"thực thể {entity_id} không thuộc tổ chức")
    if e.forgotten and not allow_forgotten:
        raise EntityError(f"thực thể {entity_id} đã bị quên")
    return e


async def canonical(db: AsyncSession, entity_id: uuid.UUID) -> uuid.UUID:
    """`coalesce(merged_into, id)` — một bậc là đủ (nén đường đi). Không có thực thể ⇒ EntityError."""
    r = (await db.execute(text("SELECT coalesce(merged_into, id) FROM mem.entities WHERE id = :i"),
                          {"i": entity_id})).scalar_one_or_none()
    if r is None:
        raise EntityError(f"không có thực thể {entity_id}")
    return r  # type: ignore[no-any-return]


async def members(db: AsyncSession, entity_id: uuid.UUID) -> list[uuid.UUID]:
    """Thực thể đích + mọi thực thể đã gộp vào nó (nơi các sự thật cũ vẫn mang `subject_id` gốc)."""
    c = await canonical(db, entity_id)
    rows = (await db.execute(text("SELECT id FROM mem.entities WHERE id = :c OR merged_into = :c"), {"c": c})).scalars().all()
    return list(rows)


async def create(db: AsyncSession, org_id: uuid.UUID, entity_type: str, display_name: str,
                 *, attrs: dict[str, Any] | None = None) -> Entity:
    """Thực thể không gắn hồ sơ có sẵn (tổ chức, địa điểm, món đồ, người mới nghe tên)."""
    if entity_type not in ENTITY_TYPES:
        raise EntityError(f"entity_type không hợp lệ: {entity_type}")
    r = (await db.execute(text(f"""INSERT INTO mem.entities (org_id, entity_type, display_name, attrs)
                                   VALUES (:o, :t, :n, CAST(:a AS jsonb)) RETURNING {_COLS}"""),  # noqa: S608
                          {"o": org_id, "t": entity_type, "n": display_name.strip() or "(không tên)",
                           "a": orjson.dumps(attrs or {}).decode()})).one()
    from gh.biz.mem import aliases

    await aliases.add(db, org_id, r.id, display_name, source="display_name", confidence=1.0)
    return _entity(r)


async def _kept_person(db: AsyncSession, person_id: uuid.UUID) -> uuid.UUID:
    """Theo `core.persons.merged_into_id` tới hồ sơ giữ lại (chống vòng)."""
    seen: set[uuid.UUID] = set()
    cur = person_id
    while cur not in seen:
        seen.add(cur)
        nxt = (await db.execute(text("SELECT merged_into_id FROM core.persons WHERE id = :p"), {"p": cur})).scalar_one_or_none()
        if nxt is None:
            return cur
        cur = nxt
    return cur


async def ensure_person(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID) -> Entity:
    """Thực thể (đích) của một hồ sơ người; tạo nếu chưa có. Hồ sơ đã bị gộp ⇒ thực thể của hồ sơ giữ lại."""
    pid = await _kept_person(db, person_id)
    r = (await db.execute(text(f"SELECT {_COLS} FROM mem.entities WHERE org_id = :o AND person_id = :p"),  # noqa: S608
                          {"o": org_id, "p": pid})).one_or_none()
    if r is None:
        name = (await db.execute(text("SELECT display_name FROM core.persons WHERE id = :p AND org_id = :o"),
                                 {"p": pid, "o": org_id})).scalar_one_or_none()
        if name is None:
            raise EntityError(f"không có hồ sơ người {person_id} trong tổ chức")
        await db.execute(text("""INSERT INTO mem.entities (org_id, entity_type, person_id, display_name)
                                 VALUES (:o, 'person', :p, :n)
                                 ON CONFLICT (org_id, person_id) WHERE person_id IS NOT NULL DO NOTHING"""),
                         {"o": org_id, "p": pid, "n": name})
        r = (await db.execute(text(f"SELECT {_COLS} FROM mem.entities WHERE org_id = :o AND person_id = :p"),  # noqa: S608
                              {"o": org_id, "p": pid})).one()
        from gh.biz.mem import aliases

        await aliases.add(db, org_id, r.id, name, source="display_name", confidence=1.0)
    ent = _entity(r)
    if ent.merged_into is not None:
        got = await get(db, ent.merged_into)
        assert got is not None
        return got
    return ent


async def ensure_group(db: AsyncSession, org_id: uuid.UUID, group_id: uuid.UUID) -> Entity:
    r = (await db.execute(text(f"SELECT {_COLS} FROM mem.entities WHERE org_id = :o AND group_id = :g"),  # noqa: S608
                          {"o": org_id, "g": group_id})).one_or_none()
    if r is None:
        name = (await db.execute(text("SELECT name FROM core.groups WHERE id = :g AND org_id = :o"),
                                 {"g": group_id, "o": org_id})).scalar_one_or_none()
        if name is None:
            raise EntityError(f"không có nhóm {group_id} trong tổ chức")
        await db.execute(text("""INSERT INTO mem.entities (org_id, entity_type, group_id, display_name)
                                 VALUES (:o, 'group', :g, :n)
                                 ON CONFLICT (org_id, group_id) WHERE group_id IS NOT NULL DO NOTHING"""),
                         {"o": org_id, "g": group_id, "n": name})
        r = (await db.execute(text(f"SELECT {_COLS} FROM mem.entities WHERE org_id = :o AND group_id = :g"),  # noqa: S608
                              {"o": org_id, "g": group_id})).one()
        from gh.biz.mem import aliases

        await aliases.add(db, org_id, r.id, name, source="display_name", confidence=1.0)
    return _entity(r)


async def owner(db: AsyncSession, org_id: uuid.UUID) -> Entity:
    """Thực thể của Sếp (`is_owner`; mỗi tổ chức một). Chưa có ⇒ lấy hồ sơ người của tài khoản Owner (nếu nối) hoặc
    tạo thực thể người mới mang tên Owner."""
    r = (await db.execute(text(f"SELECT {_COLS} FROM mem.entities WHERE org_id = :o AND is_owner"), {"o": org_id})).one_or_none()  # noqa: S608
    if r is not None:
        return _entity(r)
    u = (await db.execute(text("""
        SELECT u.display_name, u.person_id FROM core.users u
        JOIN core.user_roles ur ON ur.user_id = u.id JOIN core.roles ro ON ro.id = ur.role_id
        WHERE u.org_id = :o AND ro.code = 'owner' AND u.deleted_at IS NULL ORDER BY u.created_at LIMIT 1"""),
                          {"o": org_id})).one_or_none()
    ent: Entity | None = None
    if u is not None and u.person_id is not None:
        ent = await ensure_person(db, org_id, u.person_id)
    if ent is None:
        ent = await create(db, org_id, "person", u.display_name if u is not None else "Sếp")
    await db.execute(text("UPDATE mem.entities SET is_owner = true WHERE id = :i"), {"i": ent.id})
    got = await get(db, ent.id)
    assert got is not None
    return got


@dataclass(frozen=True)
class MergeResult:
    source: uuid.UUID
    target: uuid.UUID
    repointed: list[uuid.UUID]          # các entity từng trỏ `source`, nay trỏ `target` (để tách lại chính xác)
    disputes: int = 0                   # số tranh chấp mới do đối soát sau gộp (CNS-065)
    reconciled: dict[str, Any] | None = None   # {"disputes": [...], "facts": [[id, trạng thái cũ], ...]} để tách lại hoàn tác

    def snapshot(self) -> dict[str, Any]:
        return {"source": str(self.source), "target": str(self.target), "repointed": [str(i) for i in self.repointed],
                "reconciled": self.reconciled}


async def merge(db: AsyncSession, org_id: uuid.UUID, source_id: uuid.UUID, target_id: uuid.UUID,
                *, reconcile: bool = True) -> MergeResult:
    """Gộp `source` vào `target` (cả hai phải là thực thể đích — chưa bị gộp). Chuyển hướng, không dời sự thật; nén
    đường đi; rồi đối soát sự thật đơn trị trái nhau giữa hai bên ⇒ `disputed` (CNS-065)."""
    if source_id == target_id:
        raise EntityError("không gộp một thực thể vào chính nó")
    rows = {r.id: r for r in (await db.execute(text(
        f"SELECT {_COLS} FROM mem.entities WHERE id IN (:a, :b) ORDER BY id FOR UPDATE"),  # noqa: S608
        {"a": source_id, "b": target_id})).all()}
    if source_id not in rows or target_id not in rows:
        raise EntityError("thực thể không tồn tại")
    src, tgt = rows[source_id], rows[target_id]
    if src.org_id != org_id or tgt.org_id != org_id:
        raise EntityError("thực thể không thuộc tổ chức này")
    if src.merged_into is not None:
        raise EntityError(f"nguồn {source_id} đã bị gộp — dùng thực thể đích của nó")
    if tgt.merged_into is not None:
        raise EntityError(f"đích {target_id} đã bị gộp (CNS-103): đích gộp không được có merged_into")
    if src.is_owner and not tgt.is_owner:
        raise EntityError("không gộp thực thể của Sếp vào thực thể khác")
    repointed = list((await db.execute(text("SELECT id FROM mem.entities WHERE merged_into = :s"), {"s": source_id})).scalars().all())
    # Nén đường đi: các entity đang trỏ nguồn đổi sang trỏ đích TRƯỚC khi đặt nguồn.merged_into (trigger entities_guard).
    await db.execute(text("UPDATE mem.entities SET merged_into = :t WHERE merged_into = :s"), {"s": source_id, "t": target_id})
    await db.execute(text("UPDATE mem.entities SET merged_into = :t WHERE id = :s"), {"s": source_id, "t": target_id})
    n_disputes = 0
    recon: dict[str, Any] | None = None
    if reconcile:
        from gh.biz.mem import facts

        rec = await facts.reconcile(db, org_id, target_id)
        n_disputes = len(rec.created)
        recon = rec.to_json()
    return MergeResult(source_id, target_id, repointed, n_disputes, recon)


async def unmerge(db: AsyncSession, org_id: uuid.UUID, source_id: uuid.UUID, repointed: list[uuid.UUID],
                  reconciled: dict[str, Any] | None = None) -> None:
    """Tách `source` khỏi đích: bỏ `merged_into`, trả các entity từng trỏ nó (theo bản ghi lúc gộp) về nó. Sự thật không
    bị dời nên không cần dời lại. Các tranh chấp do đối soát lúc gộp (`reconciled`) được hoàn tác: sự thật còn `disputed` trả
    về trạng thái cũ, tranh chấp đóng với lý do `unmerge` — không để hết hạn 30 ngày làm mất sự thật của cả hai người."""
    r = (await db.execute(text("SELECT org_id, merged_into FROM mem.entities WHERE id = :s FOR UPDATE"), {"s": source_id})).one_or_none()
    if r is None or r.org_id != org_id:
        raise EntityError("thực thể không tồn tại")
    if r.merged_into is None:
        return
    target = r.merged_into
    await db.execute(text("UPDATE mem.entities SET merged_into = NULL WHERE id = :s"), {"s": source_id})
    if repointed:
        await db.execute(text("UPDATE mem.entities SET merged_into = :s WHERE id = ANY(:ids) AND merged_into = :t"),
                         {"s": source_id, "ids": repointed, "t": target})
    if reconciled:
        from gh.biz.mem import facts

        await facts.reverse_reconcile(db, org_id, reconciled)


async def merge_persons(db: AsyncSession, org_id: uuid.UUID, drop_person: uuid.UUID,
                        keep_person: uuid.UUID) -> dict[str, Any] | None:
    """Móc cho `gh.identity.service`: hai hồ sơ người vừa gộp ⇒ gộp hai thực thể tương ứng (nếu hồ sơ bị gộp có thực
    thể). Trả bản ghi để đảo ngược (`unmerge_persons`) hoặc None khi không có gì để gộp."""
    drop_row = (await db.execute(text("SELECT id FROM mem.entities WHERE org_id = :o AND person_id = :p"),
                                 {"o": org_id, "p": drop_person})).scalar_one_or_none()
    if drop_row is None:
        return None
    keep_ent = await ensure_person(db, org_id, keep_person)
    src_id, tgt_id = await canonical(db, drop_row), keep_ent.id
    if src_id == tgt_id:
        return None
    src, tgt = await get(db, src_id), await get(db, tgt_id)
    assert src is not None and tgt is not None
    if src.is_owner and not tgt.is_owner:           # thực thể của Sếp luôn là bên được giữ
        src_id, tgt_id = tgt_id, src_id
    res = await retry_deadlock(db, lambda: merge(db, org_id, src_id, tgt_id))
    return {**res.snapshot(), "disputes": res.disputes}


async def unmerge_persons(db: AsyncSession, org_id: uuid.UUID, snap: dict[str, Any] | None) -> None:
    """Đảo ngược `merge_persons` theo bản ghi (khoá lạ/thiếu ⇒ bỏ qua)."""
    if not snap or "source" not in snap:
        return
    await unmerge(db, org_id, uuid.UUID(snap["source"]), [uuid.UUID(i) for i in snap.get("repointed", [])],
                  snap.get("reconciled"))
