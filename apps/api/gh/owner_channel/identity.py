"""Nhận diện Owner CHỈ theo ID tài khoản nền tảng đã ghép (Zalo `uid`; WhatsApp JID số điện thoại và LID) — không bao
giờ theo tên hiển thị. Cũng đồng bộ các khoá Redis mà bridge đọc (thiết kế §3.1)."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.data.common import LISTENING_MODES

CHANNELS = ("zalo", "whatsapp")


def bare(external_id: str | None) -> str:
    """Dạng chuẩn hoá để so khớp ID: bỏ khoảng trắng, bỏ hậu tố thiết bị của WhatsApp (`user:12@server` →
    `user@server`),
    giữ nguyên phần máy chủ (số điện thoại và LID là hai không gian ID khác nhau). Zalo `uid` giữ nguyên."""
    x = (external_id or "").strip()
    if not x:
        return ""
    user, sep, server = x.partition("@")
    user = user.split(":")[0]
    return f"{user}@{server.lower()}" if sep else user


@dataclass(frozen=True)
class OwnerIds:
    """Kết quả `owner_ids`. `enabled` chỉ đúng khi ĐÃ ghép + bật + phiên là tài khoản bot đang chạy."""

    channel: str
    session_id: uuid.UUID | None = None
    ids: frozenset[str] = field(default_factory=frozenset)
    thread_id: str | None = None
    enabled: bool = False
    owner_user_id: uuid.UUID | None = None
    paired: bool = False


async def owner_ids(db: AsyncSession, org_id: uuid.UUID, channel: str) -> OwnerIds:
    row = (await db.execute(text("""
        SELECT oc.session_id, oc.owner_user_id, oc.owner_external_id, oc.owner_external_alt, oc.owner_thread_id,
               oc.enabled, s.account_kind, s.state, s.ended_at
        FROM ops.owner_channels oc LEFT JOIN core.channel_sessions s ON s.id = oc.session_id
        WHERE oc.org_id = :o AND oc.channel_type = :c"""), {"o": org_id, "c": channel})).one_or_none()
    if row is None or not row.owner_external_id:
        return OwnerIds(channel=channel)
    ids = frozenset(x for x in (bare(row.owner_external_id), bare(row.owner_external_alt)) if x)
    live = row.account_kind == "bot" and row.state == "active" and row.ended_at is None
    return OwnerIds(channel=channel, session_id=row.session_id, ids=ids, thread_id=row.owner_thread_id,
                    enabled=bool(row.enabled and live and row.session_id is not None),
                    owner_user_id=row.owner_user_id, paired=True)


def is_owner(owner: OwnerIds, sender_external_id: str | None, sender_alt_id: str | None = None) -> bool:
    """ID người gửi (hoặc ID phụ) khớp ID Owner đã ghép. KHÔNG nhận tên: chữ trùng tên Owner không là Owner."""
    if not owner.paired or not owner.ids:
        return False
    return any(x and x in owner.ids for x in (bare(sender_external_id), bare(sender_alt_id)))


async def active_session(db: AsyncSession, org_id: uuid.UUID, channel: str) -> Any:
    """Phiên đang chạy của kênh: (id, account_kind, external_account) hoặc None."""
    return (await db.execute(text("""
        SELECT s.id, s.account_kind, s.external_account FROM core.channel_sessions s
        JOIN core.channels c ON c.id = s.channel_id
        WHERE c.org_id = :o AND c.type = :t AND s.state = 'active' AND s.ended_at IS NULL
        ORDER BY s.started_at DESC NULLS LAST LIMIT 1"""), {"o": org_id, "t": channel})).one_or_none()


async def is_bot_account(db: AsyncSession, org_id: uuid.UUID, channel: str) -> bool:
    s = await active_session(db, org_id, channel)
    return s is not None and s.account_kind == "bot"


async def support_active_groups(db: AsyncSession, org_id: uuid.UUID, channel: str) -> list[str]:
    """external_id của nhóm đang bật chế độ hỗ trợ (chưa tắt, chưa hết hạn, đang ở chế độ nghe, chưa rời)."""
    rows = (await db.execute(text("""
        SELECT g.external_id FROM ops.group_support gs
        JOIN core.groups g ON g.id = gs.group_id JOIN core.channels c ON c.id = g.channel_id
        WHERE gs.org_id = :o AND c.type = :t AND gs.disabled_at IS NULL
          AND (gs.until_at IS NULL OR gs.until_at > now()) AND g.left_at IS NULL
          AND g.listen_mode = ANY(:modes)"""), {"o": org_id, "t": channel, "modes": list(LISTENING_MODES)})).scalars()
    return list(rows)


async def sync_owner_sets(db: AsyncSession, redis: Redis, org_id: uuid.UUID) -> None:
    """Ghi các khoá Redis cho bridge (lớp kiểm thứ hai): tập ID Owner, thread Owner, khoá chờ ghép (TTL = hạn
    mã), tập nhóm
    hỗ trợ, loại tài khoản từng phiên. Tài khoản không phải `bot` ⇒ mọi tập rỗng (bridge không phát `owner.*`)."""
    pipe = redis.pipeline(transaction=True)
    for ch in CHANNELS:
        sess = (await db.execute(text("""
            SELECT s.id, s.account_kind FROM core.channel_sessions s JOIN core.channels c ON c.id = s.channel_id
            WHERE c.org_id = :o AND c.type = :t AND s.state IN ('active', 'error') AND s.ended_at IS NULL"""),
                                 {"o": org_id, "t": ch})).all()
        for s in sess:
            pipe.set(f"gh:bridge:account_kind:{ch}:{s.id}", "bot" if s.account_kind == "bot" else "owner_self")
        oi = await owner_ids(db, org_id, ch)
        owner_key, thread_key, support_key = (f"gh:bridge:owner:{ch}", f"gh:bridge:owner_thread:{ch}",
                                              f"gh:bridge:support:{ch}")
        for k in (owner_key, thread_key, support_key):
            pipe.delete(k)
        if oi.enabled:
            pipe.sadd(owner_key, *sorted(oi.ids))
            if oi.thread_id:
                pipe.sadd(thread_key, bare(oi.thread_id), oi.thread_id)
            groups = await support_active_groups(db, org_id, ch)
            if groups:
                pipe.sadd(support_key, *groups)
        pairing_key = f"gh:bridge:pairing:{ch}"
        exp = (await db.execute(text("""
            SELECT pair_expires_at FROM ops.owner_channels
            WHERE org_id = :o AND channel_type = :c AND pair_code_hash IS NOT NULL"""),
                                {"o": org_id, "c": ch})).scalar_one_or_none()
        left = int((exp - datetime.now(UTC)).total_seconds()) if exp is not None else 0
        if left > 0 and await is_bot_account(db, org_id, ch):
            pipe.set(pairing_key, "1", ex=left)
        else:
            pipe.delete(pairing_key)
    await pipe.execute()
