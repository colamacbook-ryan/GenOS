"""Telegram 2 chiều — NHẬN và XÁC THỰC tin của Sếp (v0.1.64, T1; thiết kế: ke-hoach mục 3.2).

Chỉ một người nhắn được Gen: tài khoản Telegram Sếp đã GHÉP bằng mã 6 số (sống 10 phút) từ Console. Mỗi tin chỉ thành
`TelegramCommand` khi đủ MỌI điều kiện (xem `evaluate`):
  update là `message` thật (không sửa tin, không bài kênh) · chat riêng (`private`) · người gửi không phải bot ·
  không phải tin chuyển tiếp · đã ghép · `chat.id` == chat đã ghép · `from.id` == tài khoản đã ghép ·
  `date` trong vòng 5 phút · `update_id` chưa xử lý (> offset đã lưu) · có chữ.
Sai điều kiện nào ⇒ BỎ IM LẶNG + Action Log `telegram.rejected` (chỉ LÝ DO, không nội dung, không tên); 5 lần/giờ ⇒ một
chuông cho Owner. Mỗi lệnh hợp lệ còn bị giới hạn 20/phút.

Module này (cùng `commands`, `poller`) KHÔNG nhận mã PIN, KHÔNG có nút duyệt và KHÔNG gọi đường ghi nào — test
quét import khoá điều đó (tests/test_telegram_commands_v0164.py). Ghi ra ngoài chỉ qua Console: Xác nhận + mã PIN.

Ghép: Console `POST /notify/telegram/pair/start` (PIN) ⇒ mã 6 số (chỉ lưu BĂM) ⇒ Sếp gửi `/start <mã>` cho bot ⇒
`try_pair` lưu `owner_chat_id` + `owner_tg_user_id` (Telegram `from.id`) + Owner Console đã bấm ghép.
Sai mã 5 lần ⇒ huỷ mã.
"""

import hmac
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeGuard

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import crypto, notifications
from gh.chassis import actionlog
from gh.errors import ApiError
from gh.owner_cmd import core as owner_core
from gh.owner_cmd import parse as owner_parse
from gh.owner_cmd.core import rate_ok as owner_rate_ok

log = logging.getLogger("gh.telegram.inbound")

MAX_AGE_S = 300
PAIR_TTL = timedelta(minutes=10)
PAIR_MAX_ATTEMPTS = 5
REJECT_BELL_AFTER = 5
REJECT_WINDOW_S = 3600
REJECT_LOG_PER_REASON = 20  # tối đa dòng Action Log `telegram.rejected` mỗi lý do mỗi giờ (chống phình nhật ký)
RATE_PER_MIN = owner_core.RATE_PER_MIN
TEXT_MAX = 4000
ACTION_REJECTED = "telegram.rejected"
ACTION_PAIRED = "telegram.paired"
ACTION_UNPAIRED = "telegram.unpaired"
ACTION_INBOUND = "telegram.inbound_toggle"
ACTION_PAIR_START = "telegram.pair_start"
ACTION_COMMAND = "telegram.command"
BELL_KIND = "telegram.rejected"
NOT_PAIRED = "TELEGRAM_NOT_PAIRED"

# Lý do bỏ tin (chỉ những chuỗi này vào Action Log).
R_DUPLICATE = "duplicate"  # update_id đã xử lý
R_NOT_MESSAGE = "not_message"  # sửa tin, callback, loại update khác
R_CHANNEL = "channel"  # bài đăng kênh / chat kênh
R_GROUP = "group"  # nhóm / siêu nhóm
R_BOT = "bot"  # người gửi là bot (hoặc tin qua bot khác)
R_FORWARDED = "forwarded"  # tin chuyển tiếp
R_UNPAIRED = "unpaired"  # chưa ghép ai
R_STRANGER_CHAT = "stranger_chat"  # chat_id lạ
R_STRANGER_USER = "stranger_user"  # from.id lạ
R_STALE = "stale"  # date cũ (hoặc lệch) hơn 5 phút
R_NO_TEXT = "no_text"  # không có chữ (ảnh, sticker…)
R_PAIR_WRONG = "pair_code_wrong"
R_PAIR_LOCKED = "pair_locked"
R_PAIR_EXPIRED = "pair_expired"
R_RATE = "rate_limited"
R_DISABLED = "disabled"  # đã ghép nhưng công tắc "Nhận tin từ Sếp" đang tắt
#: Không tính vào bộ đếm "5 lần/giờ ⇒ chuông": trùng update_id là bình thường, vượt tần suất là chính Sếp; tin cũ
#: (`stale` — tin tồn của Sếp khi vừa ghép/bật) và tin khi chưa ghép (`unpaired` — chưa biết ai là chủ) không chứng tỏ
#: có người lạ. Tin do CHÍNH tài khoản đã ghép gửi cũng không tính, bất kể lý do (xem `record_reject`).
NO_BELL = frozenset({R_DUPLICATE, R_RATE, R_DISABLED, R_STALE, R_UNPAIRED})

_FORWARD_KEYS = (
    "forward_origin",
    "forward_from",
    "forward_from_chat",
    "forward_date",
    "forward_sender_name",
    "forward_signature",
    "is_automatic_forward",
)
_PAIR_RE = re.compile(r"^/start(?:@[A-Za-z0-9_]{3,64})?\s+(\d{6})\s*$")


@dataclass(frozen=True)
class TelegramCommand:
    """Lệnh đã xác thực. `kind` = tên lệnh không có '/' ('hom_nay', 'viec', 'suc_khoe', 'nhap', 'start', …) hoặc 'hoi'
    cho câu hỏi tự do. `text` = phần sau tên lệnh (hoặc cả câu với 'hoi')."""

    kind: str
    text: str
    chat_id: str
    message_id: int
    received_at: datetime


@dataclass(frozen=True)
class InboundConfig:
    """Phần cấu hình `ops.notify_channels` mà việc xác thực cần."""

    org_id: uuid.UUID
    owner_chat_id: str | None
    owner_tg_user_id: str | None
    last_update_id: int = 0
    owner_user_id: uuid.UUID | None = None


def _is_int(v: Any) -> TypeGuard[int]:
    return isinstance(v, int) and not isinstance(v, bool)


def update_id_of(update: Any) -> int:
    """update_id nếu là số nguyên, ngược lại 0."""
    uid = update.get("update_id") if isinstance(update, dict) else None
    return uid if _is_int(uid) else 0


def parse_text(raw: str) -> tuple[str, str]:
    """'/nhap  gọi A' → ('nhap', 'gọi A'); chữ thường → ('hoi', cả câu). Dùng bộ tách chung `gh.owner_cmd.parse`."""
    return owner_parse.parse_text(raw, TEXT_MAX)


def evaluate(update: Any, cfg: InboundConfig, *, now: datetime | None = None) -> TelegramCommand | str:
    """Xác thực MỘT update. Trả `TelegramCommand` khi đạt, ngược lại trả CHUỖI LÝ DO (hằng `R_*`). Thuần, không I/O."""
    now = now or datetime.now(UTC)
    if not isinstance(update, dict):
        return R_NOT_MESSAGE
    uid = update.get("update_id")
    if _is_int(uid) and uid <= cfg.last_update_id:
        return R_DUPLICATE
    if not _is_int(uid):
        return R_NOT_MESSAGE
    if "channel_post" in update or "edited_channel_post" in update:
        return R_CHANNEL
    msg = update.get("message")
    if not isinstance(msg, dict):
        return R_NOT_MESSAGE
    chat = msg.get("chat")
    if not isinstance(chat, dict):
        return R_NOT_MESSAGE
    ctype = chat.get("type")
    if ctype == "channel":
        return R_CHANNEL
    if ctype != "private":
        return R_GROUP
    sender = msg.get("from")
    if not isinstance(sender, dict):
        return R_STRANGER_USER
    if sender.get("is_bot") is not False or msg.get("via_bot") is not None or "sender_chat" in msg:
        return R_BOT
    if cfg.owner_chat_id is None or cfg.owner_tg_user_id is None:
        return R_UNPAIRED
    if not _is_int(chat.get("id")) or str(chat["id"]) != cfg.owner_chat_id:
        return R_STRANGER_CHAT
    if not _is_int(sender.get("id")) or str(sender["id"]) != cfg.owner_tg_user_id:
        return R_STRANGER_USER
    if any(k in msg for k in _FORWARD_KEYS):
        return R_FORWARDED
    date = msg.get("date")
    if not _is_int(date) or abs(now.timestamp() - date) > MAX_AGE_S:
        return R_STALE
    body = msg.get("text")
    mid = msg.get("message_id")
    if not isinstance(body, str) or not body.strip() or not _is_int(mid):
        return R_NO_TEXT
    kind, rest = parse_text(body)
    return TelegramCommand(
        kind=kind, text=rest, chat_id=str(chat["id"]), message_id=mid, received_at=datetime.fromtimestamp(date, UTC)
    )


def accept(update: Any, cfg: InboundConfig, *, now: datetime | None = None) -> TelegramCommand | None:
    """Hợp đồng công bố của T1: lệnh đã xác thực, hoặc None (bỏ im lặng). Ghi Action Log là việc của poller."""
    r = evaluate(update, cfg, now=now)
    return None if isinstance(r, str) else r


# ─── cấu hình + ghi nhận ─────────────────────────────────────────────────────


async def load(db: AsyncSession, org_id: uuid.UUID) -> Any:
    return (
        await db.execute(
            text("""
        SELECT org_id, token_enc, chat_id, bot_username, enabled, inbound_enabled, owner_chat_id, owner_tg_user_id,
               owner_user_id, paired_at, inbound_offset, pair_code_hash, pair_expires_at, pair_attempts, pair_user_id
        FROM ops.notify_channels WHERE org_id = :o"""),
            {"o": org_id},
        )
    ).one_or_none()


def config_of(row: Any) -> InboundConfig:
    return InboundConfig(
        org_id=row.org_id,
        owner_chat_id=row.owner_chat_id,
        owner_tg_user_id=row.owner_tg_user_id,
        last_update_id=int(row.inbound_offset or 0),
        owner_user_id=row.owner_user_id,
    )


def pair_active(row: Any, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return bool(
        row is not None
        and row.pair_code_hash is not None
        and row.pair_expires_at is not None
        and row.pair_expires_at > now
    )


async def advance_offset(db: AsyncSession, org_id: uuid.UUID, update_id: int) -> None:
    await db.execute(
        text("""UPDATE ops.notify_channels SET inbound_offset = GREATEST(inbound_offset, :u)
                             WHERE org_id = :o"""),
        {"u": update_id, "o": org_id},
    )


def _sent_by(update: Any, tg_user_id: str | None) -> bool:
    """Tin `message` do người (không phải bot) có `from.id` == `tg_user_id` gửi."""
    if tg_user_id is None or not isinstance(update, dict):
        return False
    m = update.get("message")
    sender = m.get("from") if isinstance(m, dict) else None
    return (
        isinstance(sender, dict)
        and sender.get("is_bot") is False
        and _is_int(sender.get("id"))
        and str(sender["id"]) == tg_user_id
    )


async def record_reject(
    db: AsyncSession,
    redis: Any,
    org_id: uuid.UUID,
    reason: str,
    update: Any,
    *,
    owner_tg_user_id: str | None = None,
) -> None:
    """Action Log `telegram.rejected` — CHỈ lý do + update_id + loại chat; KHÔNG nội dung, tên, id người gửi. 5 lần/giờ
    (không tính `NO_BELL`, không tính tin của chính `owner_tg_user_id`) ⇒ một chuông cho Owner. Không commit."""
    chat_type = None
    if isinstance(update, dict):
        for k in ("message", "channel_post", "edited_message"):
            m = update.get(k)
            if isinstance(m, dict) and isinstance(m.get("chat"), dict):
                t = m["chat"].get("type")
                chat_type = t if isinstance(t, str) and len(t) <= 20 else None
                break
    uid = update.get("update_id") if isinstance(update, dict) and _is_int(update.get("update_id")) else None
    detail: dict[str, Any] | None = {"reason": reason, "update_id": uid, "chat_type": chat_type}
    if redis is not None:
        # Giới hạn nhật ký: N dòng đầu mỗi giờ cho mỗi lý do; dòng thứ N+1 là MỘT dòng báo "đã giới hạn"; các tin sau
        # chỉ tăng bộ đếm Redis (`gh:tg:rejlog:<org>:<lý do>`). Redis lỗi ⇒ vẫn ghi (thà nhiều hơn mất dấu vết).
        try:
            k = f"gh:tg:rejlog:{org_id}:{reason}"
            c = int(await redis.incr(k))
            if c == 1:
                await redis.expire(k, REJECT_WINDOW_S)
            if c == REJECT_LOG_PER_REASON + 1:
                detail = {"reason": reason, "throttled": True, "per_hour": REJECT_LOG_PER_REASON}
            elif c > REJECT_LOG_PER_REASON + 1:
                detail = None
        except Exception:  # noqa: BLE001
            log.warning("Telegram: không đếm được giới hạn nhật ký bỏ tin", exc_info=True)
    if detail is not None:
        await actionlog.record(
            db,
            org_id=org_id,
            actor_type="system",
            actor_id="system:telegram",
            action=ACTION_REJECTED,
            target_type="telegram",
            target_id="inbound",
            result="blocked",
            detail=detail,
        )
    if reason in NO_BELL or redis is None or _sent_by(update, owner_tg_user_id):
        return
    key = f"gh:tg:rej:{org_id}"
    n = int(await redis.incr(key))
    if n == 1:
        await redis.expire(key, REJECT_WINDOW_S)
    if n == REJECT_BELL_AFTER:
        owners = await notifications.owner_ids(db, org_id)
        await notifications.notify(
            db,
            org_id,
            owners,
            kind=BELL_KIND,
            title="Có người lạ nhắn bot Telegram của Sếp",
            body=f"{REJECT_BELL_AFTER} tin bị bỏ trong 1 giờ qua (không phải tài khoản Sếp đã ghép). Gen không trả lời "
            "và không làm theo. Xem Nhật ký › telegram.rejected; nếu nghi ngờ, ghép lại hoặc đổi token bot.",
            link="/connections#telegram",
            redis=redis,
        )


async def rate_ok(redis: Any, org_id: uuid.UUID, now: datetime | None = None) -> bool:
    """Tối đa `RATE_PER_MIN` lệnh trong BẤT KỲ 60 giây liền kề/tổ chức (cửa sổ trượt, v0.1.68). Cài đặt ở bộ đếm chung
    `gh.owner_cmd.core.rate_ok` (zset + Lua, khoá Redis cũ `gh:tg:rate:<org>`) để kênh quản trị Zalo/WhatsApp
    dùng lại."""
    return await owner_rate_ok(redis, org_id, "telegram", now, RATE_PER_MIN)


# ─── ghép tài khoản ──────────────────────────────────────────────────────────


def _hash(org_id: uuid.UUID, code: str) -> bytes:
    """HMAC khoá master (không phải sha256 trơn: mã chỉ có 10^6 khả năng, lộ DB là dò hết). Mã đang chờ ghép lúc nâng
    cấp từ băm cũ sẽ không khớp nữa — chấp nhận được vì mã chỉ sống 10 phút, Sếp bấm Ghép lại."""
    return crypto.keyed_digest(b"telegram-pair", f"{org_id}:{code}".encode())


async def start_pairing(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> tuple[str, datetime]:
    """Sinh mã 6 số (trả MỘT LẦN, chỉ lưu băm), sống `PAIR_TTL`. Cần đã lưu token bot. Không commit."""
    row = await load(db, org_id)
    if row is None:
        raise ApiError(409, "TELEGRAM_NOT_CONFIGURED", "Chưa nối Telegram — lưu token bot trước rồi mới ghép")
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires = datetime.now(UTC) + PAIR_TTL
    await db.execute(
        text("""UPDATE ops.notify_channels SET pair_code_hash = :h, pair_expires_at = :e,
                             pair_attempts = 0, pair_user_id = :u WHERE org_id = :o"""),
        {"h": _hash(org_id, code), "e": expires, "u": user_id, "o": org_id},
    )
    return code, expires


def pair_candidate(update: Any) -> str | None:
    """Mã 6 số nếu update là tin `/start <6 số>` từ chat riêng của một người (chưa kiểm gì thêm); ngược lại None."""
    msg = update.get("message") if isinstance(update, dict) else None
    if not isinstance(msg, dict) or not isinstance(msg.get("text"), str):
        return None
    m = _PAIR_RE.match(msg["text"].strip())
    return m.group(1) if m else None


async def try_pair(
    db: AsyncSession, redis: Any, row: Any, update: dict[str, Any], code: str, *, now: datetime | None = None
) -> tuple[bool, str | None]:
    """Xử lý `/start <mã>` khi đang có mã ghép sống. Trả (đã ghép, lý do bỏ). Điều kiện như tin thường (chat riêng,
    không bot, không chuyển tiếp, ≤ 5 phút) trừ việc chưa cần là chủ. Không commit; bên gọi ghi offset + gửi lời đáp."""
    now = now or datetime.now(UTC)
    msg = update["message"]
    chat, sender = msg.get("chat"), msg.get("from")
    if not isinstance(chat, dict) or chat.get("type") != "private":
        return False, R_GROUP
    if not isinstance(sender, dict) or sender.get("is_bot") is not False or msg.get("via_bot") is not None:
        return False, R_BOT
    if any(k in msg for k in _FORWARD_KEYS):
        return False, R_FORWARDED
    date = msg.get("date")
    if not _is_int(date) or abs(now.timestamp() - date) > MAX_AGE_S:
        return False, R_STALE
    if not _is_int(chat.get("id")) or not _is_int(sender.get("id")) or sender["id"] <= 0:
        return False, R_STRANGER_USER
    org_id = row.org_id
    if row.pair_expires_at is None or row.pair_expires_at <= now:
        await _clear_code(db, org_id)
        return False, R_PAIR_EXPIRED
    if not hmac.compare_digest(bytes(row.pair_code_hash), _hash(org_id, code)):
        attempts = int(row.pair_attempts or 0) + 1
        if attempts >= PAIR_MAX_ATTEMPTS:
            await _clear_code(db, org_id)
            return False, R_PAIR_LOCKED
        await db.execute(
            text("UPDATE ops.notify_channels SET pair_attempts = :a WHERE org_id = :o"), {"a": attempts, "o": org_id}
        )
        return False, R_PAIR_WRONG
    await db.execute(
        text("""
        UPDATE ops.notify_channels SET owner_chat_id = :c, owner_tg_user_id = :t, owner_user_id = pair_user_id,
               paired_at = now(), inbound_enabled = true, pair_code_hash = NULL, pair_expires_at = NULL,
               pair_attempts = 0 WHERE org_id = :o"""),
        {"c": str(chat["id"]), "t": str(sender["id"]), "o": org_id},
    )
    await actionlog.record(
        db,
        org_id=org_id,
        actor_type="system",
        actor_id="system:telegram",
        action=ACTION_PAIRED,
        target_type="telegram",
        target_id="inbound",
        detail={"chat_masked": _mask(str(chat["id"]))},
    )
    return True, None


def _mask(chat_id: str) -> str:
    return "•••" + chat_id[-4:]


async def _clear_code(db: AsyncSession, org_id: uuid.UUID) -> None:
    await db.execute(
        text("""UPDATE ops.notify_channels SET pair_code_hash = NULL, pair_expires_at = NULL,
                             pair_attempts = 0 WHERE org_id = :o"""),
        {"o": org_id},
    )


async def unpair(db: AsyncSession, org_id: uuid.UUID) -> None:
    """Gỡ ghép + tắt nhận tin + huỷ mã đang chờ. Không commit."""
    await db.execute(
        text("""UPDATE ops.notify_channels SET owner_chat_id = NULL, owner_tg_user_id = NULL,
                             owner_user_id = NULL, paired_at = NULL, inbound_enabled = false, pair_code_hash = NULL,
                             pair_expires_at = NULL, pair_attempts = 0, pair_user_id = NULL WHERE org_id = :o"""),
        {"o": org_id},
    )


async def set_inbound(db: AsyncSession, org_id: uuid.UUID, enabled: bool) -> None:
    """Bật/tắt "Nhận tin từ Sếp". Bật cần đã ghép. Không commit."""
    row = await load(db, org_id)
    if row is None:
        raise ApiError(409, "TELEGRAM_NOT_CONFIGURED", "Chưa nối Telegram — lưu token bot trước")
    if enabled and not (row.owner_chat_id and row.owner_tg_user_id):
        raise ApiError(409, NOT_PAIRED, "Chưa ghép tài khoản Telegram của Sếp — bấm Ghép Telegram trước")
    await db.execute(
        text("UPDATE ops.notify_channels SET inbound_enabled = :e WHERE org_id = :o"), {"e": enabled, "o": org_id}
    )


def view(row: Any, now: datetime | None = None) -> dict[str, Any]:
    """Khối `inbound` của GET /notify/telegram — KHÔNG có mã ghép, KHÔNG có id Telegram đầy đủ."""
    if row is None:
        return {
            "enabled": False,
            "paired": False,
            "owner_masked": None,
            "paired_at": None,
            "pair_pending": False,
            "pair_expires_at": None,
        }
    active = pair_active(row, now)
    paired = bool(row.owner_chat_id and row.owner_tg_user_id)

    def iso(v: datetime | None) -> str | None:
        return v.astimezone(UTC).isoformat().replace("+00:00", "Z") if v else None

    return {
        "enabled": bool(row.inbound_enabled) and paired,
        "paired": paired,
        "owner_masked": _mask(row.owner_chat_id) if row.owner_chat_id else None,
        "paired_at": iso(row.paired_at),
        "pair_pending": active,
        "pair_expires_at": iso(row.pair_expires_at) if active else None,
    }


__all__ = ["TelegramCommand", "InboundConfig", "accept", "evaluate"]
