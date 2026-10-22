"""Nhận sự kiện kênh quản trị từ bridge (ingest đã giải mã luồng): tin 1-1 của Owner, mã ghép, Sếp tag ở nhóm
không nghe,
đổi phiên tài khoản bot. Tin 1-1 của Owner KHÔNG vào `raw.events` (kênh lệnh của Sếp, không phải dữ liệu khách)
— chỉ được
chuyển sang luồng `gh.owner.inbound` cho consumer xử lý. Mọi kiểm tra Owner ở đây làm LẠI từ DB (không tin bridge).
"""

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.chassis.bus import OWNER_INBOUND, EventBus
from gh.data import listen_events as lev
from gh.owner_channel import reports
from gh.owner_channel.identity import CHANNELS, is_owner, owner_ids

log = logging.getLogger("gh.owner_channel.inbound")

DM_MAX_AGE = timedelta(minutes=10)
DM_TEXT_MAX = 4000


def stash(db: AsyncSession, type_: str, payload: dict[str, Any]) -> None:
    """Cất một sự kiện cho luồng `gh.owner.inbound`; phát SAU commit bằng `publish_pending`."""
    db.info.setdefault("owner_pub", []).append((type_, payload))


def take_pending(db: AsyncSession) -> list[tuple[str, dict[str, Any]]]:
    return list(db.info.pop("owner_pub", []) or [])


async def publish_pending(bus: EventBus, org_id: uuid.UUID, pending: list[tuple[str, dict[str, Any]]]) -> None:
    for type_, payload in pending:
        await bus.publish(OWNER_INBOUND, type_, payload, actor="system:ingest", org_id=org_id)


def _when(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, UTC)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


async def _channel(db: AsyncSession, org_id: uuid.UUID, ch: str) -> Any:
    if ch not in CHANNELS:
        return None
    return (await db.execute(text("SELECT id, type FROM core.channels WHERE org_id = :o AND type = :t"),
                             {"o": org_id, "t": ch})).one_or_none()


async def handle_owner_dm(db: AsyncSession, redis: Redis, org_id: uuid.UUID, p: dict[str, Any],
                          now: datetime | None = None) -> bool:
    """`owner.dm`: chỉ nhận khi kênh quản trị đang bật, phiên đúng, ID người gửi KHỚP Owner (kiểm lại ở lõi), không phải
    tin chuyển tiếp, là chữ, còn trong 10 phút, chưa xử lý. Trả True nếu đã chuyển cho consumer."""
    ch = str(p.get("channel") or "")
    if await _channel(db, org_id, ch) is None:
        return False
    oi = await owner_ids(db, org_id, ch)
    msg_id = str(p.get("external_msg_id") or "")
    body = p.get("body_text")
    if p.get("external_group_id") or p.get("group_id") or p.get("thread_type") == "group":
        return False   # v0.1.73: tin trong NHÓM không bao giờ là lệnh / xác nhận của Sếp (bridge cũng không phát)
    if (not oi.enabled or not msg_id or p.get("forwarded") or str(p.get("session_id") or "") != str(oi.session_id)
            or not is_owner(oi, str(p.get("sender_external_id") or ""), p.get("sender_alt_id"))
            or p.get("kind", "text") != "text" or not isinstance(body, str) or not body.strip()):
        return False
    if (now or datetime.now(UTC)) - _when(p.get("occurred_at")) > DM_MAX_AGE:
        return False
    if not await redis.set(f"gh:owner:dm:{ch}:{msg_id}", str(int(time.time())), nx=True, ex=86400):
        return False
    stash(db, "dm", {"channel": ch, "session_id": str(oi.session_id), "external_msg_id": msg_id,
                     "text": body.strip()[:DM_TEXT_MAX], "occurred_at": _when(p.get("occurred_at")).isoformat()})
    return True


async def handle_owner_pair(db: AsyncSession, org_id: uuid.UUID, p: dict[str, Any]) -> bool:
    """`owner.pair`: chuyển mã 6 số cho consumer CHỈ khi đang có mã ghép còn hạn (bridge cũng đã kiểm khoá chờ ghép)."""
    ch = str(p.get("channel") or "")
    code = str(p.get("code") or "")
    if await _channel(db, org_id, ch) is None or len(code) != 6 or not code.isdigit() or p.get("forwarded"):
        return False
    live = (await db.execute(text("""
        SELECT 1 FROM ops.owner_channels WHERE org_id = :o AND channel_type = :c AND pair_code_hash IS NOT NULL
          AND pair_expires_at > now()"""), {"o": org_id, "c": ch})).first()
    if live is None:
        return False
    stash(db, "pair", {"channel": ch, "session_id": str(p.get("session_id") or ""), "code": code,
                       "sender_external_id": str(p.get("sender_external_id") or ""),
                       "sender_alt_id": p.get("sender_alt_id"), "sender_name": str(p.get("sender_name") or "")[:120],
                       "occurred_at": _when(p.get("occurred_at")).isoformat(),
                       "external_msg_id": p.get("external_msg_id")})
    return True


async def handle_owner_ping(db: AsyncSession, org_id: uuid.UUID, p: dict[str, Any]) -> bool:
    """`group.owner_ping`: Sếp tag bot ở nhóm KHÔNG nghe (bridge đã bỏ nội dung). Báo Sếp qua kênh quản trị một
    lần mỗi giờ
    mỗi nhóm kèm cách bật nghe; ghi Nhật ký lắng nghe. Không lưu gì từ nhóm."""
    ch = str(p.get("channel") or "")
    chan = await _channel(db, org_id, ch)
    gid = str(p.get("group_id") or "")
    if chan is None or not gid:
        return False
    oi = await owner_ids(db, org_id, ch)
    if not oi.enabled or str(p.get("session_id") or "") != str(oi.session_id):
        return False
    g = (await db.execute(text("""SELECT id, name, listen_mode FROM core.groups
                                  WHERE channel_id = :c AND external_id = :x AND left_at IS NULL"""),
                          {"c": chan.id, "x": gid})).one_or_none()
    if g is None or g.listen_mode in ("tagged_only", "silent", "proactive"):
        return False
    from gh.owner_channel import questions

    q = await questions.create(db, org_id, kind="listen_confirm", subject_id=g.id,
                               payload={"reason": "owner_ping", "mode": g.listen_mode})
    hour = datetime.now(UTC).strftime("%Y%m%d%H")
    await lev.record(db, org_id, "owner_called", channel_id=chan.id, channel_type=ch, group_id=g.id,
                     detail={"not_listening": True})
    await reports.enqueue(
        db, org_id, ch, kind="ask", severity="important", dedupe_key=f"owner_ping:{g.id}:{hour}",
        text_=(f"Sếp gọi em ở nhóm «{str(g.name)[:80]}» nhưng nhóm đang không nghe nên em chưa đọc được. "
               f"Gõ /nghe {q.short_code} (bật Lắng nghe im lặng), /bo {q.short_code} để giữ không nghe."))
    return True


async def on_session_active(db: AsyncSession, org_id: uuid.UUID, channel: str, session_id: uuid.UUID,
                            account_id: str | None) -> str | None:
    """Phiên kênh mới vào `active`. Đã ghép kênh quản trị với phiên khác: CÙNG tài khoản bot (đăng nhập lại) ⇒
    chuyển ghép
    sang phiên mới và giữ loại tài khoản; KHÁC tài khoản ⇒ huỷ ghép và tắt mọi chế độ hỗ trợ (`session_changed`).
    Trả "carried" | "cancelled" | None (không có gì để làm)."""
    row = (await db.execute(text("""
        SELECT oc.session_id, s.external_account, s.account_kind FROM ops.owner_channels oc
        LEFT JOIN core.channel_sessions s ON s.id = oc.session_id
        WHERE oc.org_id = :o AND oc.channel_type = :c AND oc.owner_external_id IS NOT NULL"""),
                          {"o": org_id, "c": channel})).one_or_none()
    if row is None or row.session_id == session_id:
        return None
    if account_id and row.external_account and account_id == row.external_account:
        await db.execute(text("""UPDATE core.channel_sessions SET account_kind = COALESCE(account_kind, :k)
                                 WHERE id = :i"""), {"k": row.account_kind, "i": session_id})
        await db.execute(text("""UPDATE ops.owner_channels SET session_id = :i, updated_at = now()
                                 WHERE org_id = :o AND channel_type = :c"""),
                         {"i": session_id, "o": org_id, "c": channel})
        return "carried"
    await cancel_pairing(db, org_id, channel, reason="session_changed")
    return "cancelled"


async def cancel_pairing(db: AsyncSession, org_id: uuid.UUID, channel: str, *, reason: str) -> None:
    """Huỷ ghép kênh quản trị + tắt mọi lượt hỗ trợ đang bật của kênh (lý do `session_changed` |
    `account_kind_changed`)."""
    await db.execute(text("""
        UPDATE ops.owner_channels SET enabled = false, session_id = NULL, owner_user_id = NULL,
               owner_external_id = NULL, owner_external_alt = NULL, owner_thread_id = NULL, owner_display = NULL,
               paired_at = NULL, pair_code_hash = NULL, pair_expires_at = NULL, pair_attempts = 0,
               pair_user_id = NULL, updated_at = now()
        WHERE org_id = :o AND channel_type = :c"""), {"o": org_id, "c": channel})
    await disable_support(db, org_id, channel, reason=reason)
    chan = await _channel(db, org_id, channel)
    await lev.record(db, org_id, "owner_unpaired", channel_id=chan.id if chan else None, channel_type=channel,
                     detail={"reason": reason})


async def expire_stale_support(db: AsyncSession, group_id: uuid.UUID) -> int:
    """Đóng các lượt hỗ trợ ĐÃ HẾT HẠN nhưng còn `disabled_at` NULL (chỉ mục `group_support_one_active_uq` vẫn coi là
    đang bật ⇒ vướng lượt bật mới). Gọi trong cùng giao dịch, TRƯỚC khi chèn lượt mới."""
    res = await db.execute(text("""
        UPDATE ops.group_support SET disabled_at = now(), disabled_reason = 'expired'
        WHERE group_id = :g AND disabled_at IS NULL AND until_at IS NOT NULL AND until_at <= now()"""), {"g": group_id})
    return int(res.rowcount or 0)  # type: ignore[attr-defined]


async def disable_support(db: AsyncSession, org_id: uuid.UUID, channel: str, *, reason: str,
                          group_id: uuid.UUID | None = None) -> list[uuid.UUID]:
    """Tắt lượt hỗ trợ đang bật (của một nhóm hoặc cả kênh) và ghi Nhật ký lắng nghe `support_off`."""
    rows = (await db.execute(text("""
        UPDATE ops.group_support gs SET disabled_at = now(), disabled_reason = :r
        FROM core.groups g JOIN core.channels c ON c.id = g.channel_id
        WHERE gs.group_id = g.id AND gs.org_id = :o AND c.type = :t AND gs.disabled_at IS NULL
          AND (CAST(:g AS uuid) IS NULL OR gs.group_id = :g)
        RETURNING gs.group_id, c.id AS channel_id"""), {"r": reason, "o": org_id, "t": channel, "g": group_id})).all()
    for r in rows:
        await lev.record(db, org_id, "support_off", channel_id=r.channel_id, channel_type=channel, group_id=r.group_id,
                         detail={"reason": reason})
    return [r.group_id for r in rows]
