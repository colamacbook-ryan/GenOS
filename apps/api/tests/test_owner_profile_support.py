# ruff: noqa: E501
"""Tiện ích dùng chung cho test L4 (Hồ sơ Sếp, API "Tôi", độ nóng, job đêm). Không có test ở đây."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.mem import entities


async def mk_channel(db: AsyncSession, org: uuid.UUID, name: str = "Zalo thử") -> uuid.UUID:
    cid = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', :n) RETURNING id"),
                            {"o": org, "n": f"{name}-{uuid.uuid4().hex[:4]}"})).scalar_one()
    await db.commit()
    return cid  # type: ignore[no-any-return]


async def mk_group(db: AsyncSession, org: uuid.UUID, channel: uuid.UUID, name: str = "Nhóm kho") -> uuid.UUID:
    gid = (await db.execute(text("""INSERT INTO core.groups (org_id, code, channel_id, external_id, name)
                                    VALUES (:o, :c, :ch, :e, :n) RETURNING id"""),
                            {"o": org, "c": f"GRP-{uuid.uuid4().hex[:6]}", "ch": channel, "e": uuid.uuid4().hex[:8], "n": name})).scalar_one()
    await db.commit()
    return gid  # type: ignore[no-any-return]


async def mk_person(db: AsyncSession, org: uuid.UUID, channel: uuid.UUID, name: str, ext: str | None = None) -> tuple[uuid.UUID, uuid.UUID]:
    """(person_id, identity_id)."""
    pid = (await db.execute(text("INSERT INTO core.persons (org_id, code, display_name) VALUES (:o, core.next_code('PER'), :n) RETURNING id"),
                            {"o": org, "n": name})).scalar_one()
    iid = (await db.execute(text("INSERT INTO core.person_identities (person_id, channel_id, external_id) VALUES (:p, :c, :e) RETURNING id"),
                            {"p": pid, "c": channel, "e": ext or f"x{uuid.uuid4().hex[:8]}"})).scalar_one()
    await db.commit()
    return pid, iid


async def mk_user(db: AsyncSession, org: uuid.UUID, person: uuid.UUID | None = None) -> uuid.UUID:
    uid = (await db.execute(text("""INSERT INTO core.users (org_id, email, display_name, password_hash, person_id)
                                    VALUES (:o, :e, 'Sếp', 'x', :p) RETURNING id"""),
                            {"o": org, "e": f"{uuid.uuid4().hex[:6]}@x.vn", "p": person})).scalar_one()
    rid = (await db.execute(text("INSERT INTO core.roles (org_id, code, name, is_system) VALUES (:o, 'owner', 'Owner', true) "
                                 "ON CONFLICT (org_id, code) DO UPDATE SET name = 'Owner' RETURNING id"), {"o": org})).scalar_one()
    await db.execute(text("INSERT INTO core.user_roles (user_id, role_id) VALUES (:u, :r)"), {"u": uid, "r": rid})
    await db.commit()
    return uid  # type: ignore[no-any-return]


class World:
    """Một tổ chức có Sếp (người + định danh + user nối người), một kênh, một nhóm."""

    def __init__(self, org: uuid.UUID, channel: uuid.UUID, group: uuid.UUID, owner_person: uuid.UUID, owner_identity: uuid.UUID,
                 user: uuid.UUID, owner_entity: uuid.UUID) -> None:
        self.org, self.channel, self.group = org, channel, group
        self.owner_person, self.owner_identity, self.user, self.owner = owner_person, owner_identity, user, owner_entity


async def mk_world(db: AsyncSession, org: uuid.UUID) -> World:
    ch = await mk_channel(db, org)
    grp = await mk_group(db, org, ch)
    pid, iid = await mk_person(db, org, ch, "Anh Cơ", "owner-zx")
    uid = await mk_user(db, org, pid)
    ent = await entities.owner(db, org)
    await db.commit()
    return World(org, ch, grp, pid, iid, uid, ent.id)


async def link_owner_user(db: AsyncSession, org: uuid.UUID) -> World:
    """Dùng user Owner có sẵn của `owner_api`: nối người + định danh cho họ."""
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = 'zalo'"), {"o": org})).scalar_one()
    grp = await mk_group(db, org, ch)
    pid, iid = await mk_person(db, org, ch, "Anh Cơ", "owner-zx")
    uid = (await db.execute(text("SELECT id FROM core.users WHERE org_id = :o"), {"o": org})).scalar_one()
    await db.execute(text("UPDATE core.users SET person_id = :p WHERE id = :u"), {"p": pid, "u": uid})
    ent = await entities.owner(db, org)
    await db.commit()
    return World(org, ch, grp, pid, iid, uid, ent.id)


async def mk_raw(db: AsyncSession, org: uuid.UUID, channel: uuid.UUID, *, identity: uuid.UUID | None, body: str | None, at: datetime | None = None,
                 group: uuid.UUID | None = None, direction: str = "inbound", kind: str = "text", payload: dict[str, Any] | None = None) -> uuid.UUID:
    at = at or datetime.now(UTC) - timedelta(hours=1)
    m = f"m-{uuid.uuid4().hex}"
    rid = (await db.execute(text("""
        INSERT INTO raw.events (org_id, occurred_at, channel_id, group_id, sender_identity_id, external_msg_id, kind, body_text, payload,
                                content_hash, direction)
        VALUES (:o, :t, :c, :g, :i, :m, :k, :b, CAST(:p AS jsonb), decode(md5(:m), 'hex'), :d) RETURNING id"""),
                             {"o": org, "t": at, "c": channel, "g": group, "i": identity, "m": m, "k": kind, "b": body,
                              "p": orjson.dumps(payload or {}).decode(), "d": direction})).scalar_one()
    await db.commit()
    return rid  # type: ignore[no-any-return]
