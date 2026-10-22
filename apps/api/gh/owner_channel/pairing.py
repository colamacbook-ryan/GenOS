"""Ghép kênh quản trị Zalo/WhatsApp bằng mã 6 số (thiết kế §3.3), cùng khuôn ghép Telegram.

1. Console (PIN) `start_pairing`: cần tài khoản `bot` đang chạy; sinh mã 6 số, chỉ lưu BĂM có khoá, sống 10 phút.
2. Sếp nhắn mã cho bot từ tài khoản của mình ⇒ bridge phát `owner.pair` ⇒ `try_pair` kiểm lại ở lõi (tin 1-1, ≤
5 phút, không
   chuyển tiếp, đúng phiên bot, mã còn hạn, so sánh hằng thời gian, mỗi người gửi sai 5 lần ⇒ khoá người đó;
   tổng sai 20 lần ⇒ huỷ mã). Sai mã: KHÔNG trả lời.
3. Đúng: ghi ID Owner (Zalo `uid`; WhatsApp JID và LID), bật kênh, Nhật ký + chuông + Nhật ký lắng nghe; lời
đáp đi qua cửa
   `send_auto(owner_dm)`.
"""

import hmac
import logging
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gh import crypto, notifications
from gh.chassis import actionlog
from gh.chassis.bus import EventBus
from gh.data import listen_events as lev
from gh.errors import ApiError
from gh.owner_channel import inbound as oin
from gh.owner_channel.autosend import send_auto
from gh.owner_channel.basis import Basis, Trigger
from gh.owner_channel.identity import CHANNELS, active_session, bare, sync_owner_sets

log = logging.getLogger("gh.owner_channel.pairing")

PAIR_TTL = timedelta(minutes=10)
PAIR_MAX_ATTEMPTS = 5          # mỗi người gửi: sai 5 lần ⇒ khoá RIÊNG người đó (không huỷ mã của Sếp)
PAIR_MAX_TOTAL = 20            # trần tổng hợp mọi người gửi: sai tới đây mới huỷ mã
MAX_AGE_S = 300
ACTION_PAIR_START = "owner_channel.pair_start"
ACTION_PAIRED = "owner_channel.paired"
ACTION_UNPAIRED = "owner_channel.unpaired"
PAIRED_TEXT = ("Đã ghép kênh quản trị. Từ giờ Sếp nhắn em ở đây: /tro_giup xem các lệnh em hiểu. "
               "Em chỉ nhắn Sếp khi có việc đặc biệt, không báo định kỳ.")


def _hash(org_id: uuid.UUID, channel: str, code: str) -> bytes:
    """HMAC khoá master (mã chỉ có 10^6 khả năng — băm trơn thì lộ DB là dò hết)."""
    return crypto.keyed_digest(b"owner-pair", f"{org_id}:{channel}:{code}".encode())


async def start_pairing(db: AsyncSession, redis: Redis, org_id: uuid.UUID, channel: str,
                        user_id: uuid.UUID) -> tuple[str, datetime]:
    """Sinh mã ghép (trả MỘT lần). Đòi tài khoản `bot` đang chạy (409 NOT_BOT_ACCOUNT / SESSION_NOT_ACTIVE).
    Không commit."""
    if channel not in CHANNELS:
        raise ApiError(404, "CHANNEL_NOT_FOUND", "Kênh này chưa hỗ trợ kênh quản trị")
    sess = await active_session(db, org_id, channel)
    if sess is None:
        raise ApiError(409, "SESSION_NOT_ACTIVE", "Kênh chưa đăng nhập — nối tài khoản của bot trước rồi mới ghép")
    if sess.account_kind != "bot":
        raise ApiError(409, "NOT_BOT_ACCOUNT",
                       "Kênh đang chạy bằng tài khoản cá nhân của Sếp. Đánh dấu «tài khoản riêng của bot» trước")
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = datetime.now(UTC) + PAIR_TTL
    await db.execute(text("""
        INSERT INTO ops.owner_channels (org_id, channel_type, session_id, pair_code_hash, pair_expires_at,
        pair_attempts,
                                        pair_user_id, updated_at, updated_by)
        VALUES (:o, :c, :s, :h, :e, 0, :u, now(), :u)
        ON CONFLICT (org_id, channel_type) DO UPDATE SET pair_code_hash = :h, pair_expires_at = :e, pair_attempts = 0,
          pair_user_id = :u, updated_at = now(), updated_by = :u, session_id = :s"""),
        {"o": org_id, "c": channel, "s": sess.id, "h": _hash(org_id, channel, code), "e": expires, "u": user_id})
    await sync_owner_sets(db, redis, org_id)
    return code, expires


async def _clear_code(db: AsyncSession, org_id: uuid.UUID, channel: str) -> None:
    await db.execute(text("""UPDATE ops.owner_channels SET pair_code_hash = NULL, pair_expires_at = NULL,
                             pair_attempts = 0 WHERE org_id = :o AND channel_type = :c"""), {"o": org_id, "c": channel})


def _thread_for(channel: str, ext: str, alt: str | None) -> str:
    """Thread 1-1 để gửi cho Owner: Zalo = uid; WhatsApp = JID số điện thoại nếu biết (ưu tiên), không thì LID."""
    if channel == "whatsapp":
        for x in (ext, alt):
            if x and x.endswith("@s.whatsapp.net"):
                return bare(x)
        return bare(ext)
    return bare(ext)


async def try_pair(db: AsyncSession, redis: Redis, org_id: uuid.UUID, p: dict[str, Any],
                   now: datetime | None = None) -> tuple[bool, str | None]:
    """Xử lý một `owner.pair`. Trả (đã ghép, lý do bỏ). Commit khi ghép được / khi đếm lần sai. Lý do chỉ để
    Nhật ký/test —
    KHÔNG bao giờ trả lời người nhắn khi sai."""
    now = now or datetime.now(UTC)
    ch = str(p.get("channel") or "")
    code = str(p.get("code") or "")
    ext = bare(str(p.get("sender_external_id") or ""))
    row = (await db.execute(text("""
        SELECT oc.session_id, oc.pair_code_hash, oc.pair_expires_at, oc.pair_attempts, oc.pair_user_id, s.account_kind,
               s.state FROM ops.owner_channels oc LEFT JOIN core.channel_sessions s ON s.id = oc.session_id
        WHERE oc.org_id = :o AND oc.channel_type = :c"""), {"o": org_id, "c": ch})).one_or_none()
    if row is None or row.pair_code_hash is None:
        return False, "no_code"
    if not ext or len(code) != 6 or not code.isdigit():
        return False, "bad_input"
    if row.pair_expires_at is None or row.pair_expires_at <= now:
        await _clear_code(db, org_id, ch)
        await db.commit()
        return False, "expired"
    if str(row.session_id) != str(p.get("session_id")) or row.account_kind != "bot" or row.state != "active":
        return False, "wrong_session"
    age = (now - _parse(p.get("occurred_at"), now)).total_seconds()
    if age > MAX_AGE_S or age < -60:
        return False, "stale"
    fail_key = f"gh:owner:pairfail:{org_id}:{ch}:{int(row.pair_expires_at.timestamp())}:{ext}"
    if int(await redis.get(fail_key) or 0) >= PAIR_MAX_ATTEMPTS:
        return False, "sender_locked"
    if not hmac.compare_digest(bytes(row.pair_code_hash), _hash(org_id, ch, code)):
        mine = int(await redis.incr(fail_key))
        await redis.expire(fail_key, int(PAIR_TTL.total_seconds()) + 60)
        attempts = int(row.pair_attempts or 0) + 1
        if attempts >= PAIR_MAX_TOTAL:
            await _clear_code(db, org_id, ch)
            await sync_owner_sets(db, redis, org_id)
        else:
            await db.execute(text("""UPDATE ops.owner_channels SET pair_attempts = :a
                                     WHERE org_id = :o AND channel_type = :c"""), {"a": attempts, "o": org_id, "c": ch})
        await db.commit()
        if attempts >= PAIR_MAX_TOTAL:
            return False, "locked"
        return False, "sender_locked" if mine >= PAIR_MAX_ATTEMPTS else "wrong_code"
    alt = bare(str(p.get("sender_alt_id") or "")) or None
    await db.execute(text("""
        UPDATE ops.owner_channels SET owner_external_id = :x, owner_external_alt = :a, owner_thread_id = :t,
               owner_user_id = pair_user_id, owner_display = :d, paired_at = now(), enabled = true,
               pair_code_hash = NULL, pair_expires_at = NULL, pair_attempts = 0, updated_at = now()
        WHERE org_id = :o AND channel_type = :c"""),
        {"x": ext, "a": alt, "t": _thread_for(ch, ext, alt), "d": str(p.get("sender_name") or "")[:120] or None,
         "o": org_id, "c": ch})
    await actionlog.record(db, org_id=org_id, actor_type="system", actor_id="system:owner_channel",
                           action=ACTION_PAIRED, target_type="owner_channel", target_id=ch,
                           detail={"channel": ch, "owner_masked": "•••" + ext[-4:]})
    chan = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = :t"),
                             {"o": org_id, "t": ch})).scalar_one_or_none()
    await lev.record(db, org_id, "owner_paired", channel_id=chan, channel_type=ch)
    await notifications.notify(db, org_id, await notifications.owner_ids(db, org_id), kind="owner_channel.paired",
                               title="Đã ghép kênh quản trị", redis=redis,
                               body=f"Kênh quản trị {ch.capitalize()} đã ghép với tài khoản •••{ext[-4:]}.",
                               link="/connections")
    msg_id = str(p.get("external_msg_id") or "")
    if msg_id:
        await redis.set(f"gh:owner:dm:{ch}:{msg_id}", str(int(time.time())), ex=86400)
    await sync_owner_sets(db, redis, org_id)
    await db.commit()
    return True, None


def _parse(value: Any, default: datetime) -> datetime:
    return oin._when(value) if value else default


async def reply_paired(sm: async_sessionmaker[AsyncSession], redis: Redis, bus: EventBus, org_id: uuid.UUID,
                       p: dict[str, Any]) -> bool:
    """Lời đáp sau khi ghép: đi cửa `send_auto(owner_dm)` (Sếp vừa nhắn mã ⇒ có tin kích hoạt)."""
    ch, msg_id = str(p.get("channel")), str(p.get("external_msg_id") or "")
    if not msg_id:
        return False
    async with sm() as db:
        r = await send_auto(db, bus, redis, org_id=org_id, channel=ch, basis=Basis.OWNER_DM,
                            trigger=Trigger("owner_message", f"{ch}:{msg_id}"), text=PAIRED_TEXT)
    return r.ok


async def unpair(db: AsyncSession, redis: Redis, org_id: uuid.UUID, channel: str) -> None:
    """Huỷ ghép từ Console (PIN): xoá ID Owner, tắt kênh quản trị và mọi chế độ hỗ trợ của kênh. Không commit."""
    await oin.cancel_pairing(db, org_id, channel, reason="owner")
    await sync_owner_sets(db, redis, org_id)


async def handle_pair_event(sm: async_sessionmaker[AsyncSession], redis: Redis, bus: EventBus, org_id: uuid.UUID,
                            p: dict[str, Any]) -> str:
    """Consumer `pair`: ghép rồi (nếu đúng mã) trả lời Sếp. Sai mã ⇒ im lặng."""
    async with sm() as db:
        ok, why = await try_pair(db, redis, org_id, p)
    if ok:
        await reply_paired(sm, redis, bus, org_id, p)
        return "paired"
    return f"rejected:{why}"
