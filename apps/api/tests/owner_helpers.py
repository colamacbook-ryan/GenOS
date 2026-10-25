# ruff: noqa: E501
"""Tiện ích test bot v0.1.72: tài khoản bot đã ghép Owner, bus giả đếm tin gửi, nhóm hỗ trợ."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

OWNER_ZALO = "100200300"      # uid Zalo của Sếp
OWNER_WA = "84901234567@s.whatsapp.net"
OWNER_WA_LID = "99887766@lid"


class FakeBus:
    """Thay EventBus: ghi lại mọi `publish` (stream, type, payload) — dùng đếm tin ra bridge."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    async def publish(self, stream: str, type: str, payload: dict[str, Any], **kw: Any) -> str:
        self.events.append((stream, type, payload))
        return "0-0"

    def on(self, stream: str) -> list[tuple[str, dict[str, Any]]]:
        return [(t, p) for s, t, p in self.events if s == stream]


async def make_session(db: AsyncSession, org: uuid.UUID, channel: str = "zalo", *, kind: str | None = "bot",
                       account: str = "bot-acc-1", state: str = "active") -> uuid.UUID:
    sid = (await db.execute(text("""
        INSERT INTO core.channel_sessions (channel_id, org_id, account_label, state, started_at, external_account,
                                           account_kind)
        SELECT id, org_id, 'Bot', :st, now(), :acc, :k FROM core.channels WHERE org_id = :o AND type = :t
        RETURNING id"""), {"o": org, "t": channel, "st": state, "acc": account, "k": kind})).scalar_one()
    await db.commit()
    return sid  # type: ignore[no-any-return]


async def owner_user_id(db: AsyncSession, org: uuid.UUID) -> uuid.UUID:
    """Owner Console THẬT của tổ chức (đang hoạt động, vai `owner`); chưa có thì tạo — kênh quản trị chỉ ghép với Owner hợp lệ
    (v0.1.76: mọi lệnh nhóm qua cổng `owner_cmd.core.owner_user`)."""
    uid = (await db.execute(text("""SELECT u.id FROM core.users u JOIN core.user_roles ur ON ur.user_id = u.id
                                    JOIN core.roles r ON r.id = ur.role_id
                                    WHERE u.org_id = :o AND r.code = 'owner' AND u.is_active AND u.deleted_at IS NULL
                                    ORDER BY u.created_at LIMIT 1"""), {"o": org})).scalar_one_or_none()
    if uid is not None:
        return uid  # type: ignore[no-any-return]
    uid = (await db.execute(text("""INSERT INTO core.users (org_id, email, display_name, password_hash)
                                    VALUES (:o, :e, 'Sếp thử', 'x') RETURNING id"""),
                            {"o": org, "e": f"owner-{uuid.uuid4().hex[:8]}@example.vn"})).scalar_one()
    rid = (await db.execute(text("SELECT id FROM core.roles WHERE org_id = :o AND code = 'owner'"), {"o": org})).scalar_one_or_none()
    if rid is None:
        rid = (await db.execute(text("""INSERT INTO core.roles (org_id, code, name, is_system) VALUES (:o, 'owner', 'Chủ', true)
                                        RETURNING id"""), {"o": org})).scalar_one()
    await db.execute(text("INSERT INTO core.user_roles (user_id, role_id) VALUES (:u, :r)"), {"u": uid, "r": rid})
    return uid  # type: ignore[no-any-return]


async def pair_owner(db: AsyncSession, org: uuid.UUID, sid: uuid.UUID, channel: str = "zalo", *,
                     ext: str | None = None, alt: str | None = None, enabled: bool = True) -> None:
    ext = ext or (OWNER_ZALO if channel == "zalo" else OWNER_WA)
    uid = await owner_user_id(db, org)
    await db.execute(text("""
        INSERT INTO ops.owner_channels (org_id, channel_type, session_id, owner_user_id, owner_external_id,
                                        owner_external_alt, owner_thread_id, paired_at, enabled)
        VALUES (:o, :c, :s, :u, :x, :a, :x, now(), :e)
        ON CONFLICT (org_id, channel_type) DO UPDATE SET session_id = :s, owner_external_id = :x,
          owner_external_alt = :a, owner_thread_id = :x, paired_at = now(), enabled = :e"""),
        {"o": org, "c": channel, "s": sid, "u": uid, "x": ext, "a": alt, "e": enabled})
    await db.commit()


async def bot_world(db: AsyncSession, org: uuid.UUID, channel: str = "zalo", *, alt: str | None = None) -> uuid.UUID:
    """Tài khoản bot đang chạy + Owner đã ghép + bật kênh quản trị. Trả id phiên."""
    sid = await make_session(db, org, channel)
    await pair_owner(db, org, sid, channel, alt=alt)
    return sid


async def enable_support(db: AsyncSession, group_id: uuid.UUID, *, until: datetime | None = None,
                         via: str = "console") -> uuid.UUID:
    org = (await db.execute(text("SELECT org_id FROM core.groups WHERE id = :g"), {"g": group_id})).scalar_one()
    sid = (await db.execute(text("""
        INSERT INTO ops.group_support (org_id, group_id, enabled_via, until_at) VALUES (:o, :g, :v, :u) RETURNING id"""),
        {"o": org, "g": group_id, "v": via, "u": until})).scalar_one()
    await db.commit()
    return sid  # type: ignore[no-any-return]


def ago(**kw: float) -> str:
    return (datetime.now(UTC) - timedelta(**kw)).isoformat()
