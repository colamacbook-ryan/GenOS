# ruff: noqa: E501
"""Kênh phụ (v0.1.76, gói T10 — spec §8.1): đường báo SỨC KHOẺ và MÃ KÉP độc lập với tài khoản agent Zalo/WhatsApp.

Hợp đồng cho gói khác (T2 `strong_confirm`, T3, T11):
- `await notify(db, org_id, text, *, kind="incident", dedupe_key=None) -> bool` — xếp tin vào `ops.backup_outbox` trong transaction của
  bên gọi (không commit). Có Telegram đã nối ⇒ kênh `telegram`; chưa có ⇒ kênh `console` (ghi nhận để Console hiện, không gửi đi đâu).
- `await send_code(db, org_id) -> code_hash` — sinh mã 4 số, xếp tin chứa mã vào hàng đợi Telegram và trả BĂM (HMAC khoá master, gắn tổ chức).
  Mã rõ CHỈ nằm trong hàng đợi tới lúc gửi xong / hỏng / hết hạn rồi bị xoá khỏi `text`; không bao giờ vào log/Action Log. Bên gọi tự lưu băm
  và so bằng `verify_code`. Không có Telegram ⇒ `BackupUnavailable` (không có đường nào an toàn để gửi mã).
- `await flush(sm, *, transport=None) -> {sent, retry, failed}` — worker `backup_flush` (đăng ký trong `intel/pipes/watchdog.py`) gọi mỗi phút.

"Khoá trợ lý" (`lock_assistant`): lệnh khẩn, CHỈ Sếp. Nhận người ra lệnh theo ID, không theo tên: Owner Console (`user_id` thuộc vai Owner),
tài khoản Telegram đã ghép (`from.id` + `chat.id` khớp `ops.notify_channels`), hoặc ID Owner đã ghép ở kênh quản trị (`ops.owner_channels`).
Hiệu lực dùng đúng các công tắc mà `send_auto` đã kiểm: tắt kênh quản trị, tắt mọi lượt hỗ trợ nhóm, huỷ báo cáo đang chờ gửi, đặt cờ
`settings.intel.assistant_locked_at`. Mở khoá: chỉ Owner trên Console (route bắt PIN) và KHÔNG tự bật lại các công tắc — Sếp bật lại có chủ ý."""

from __future__ import annotations

import hmac
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import crypto, notifications
from gh.chassis import actionlog
from gh.telegram import client as tg
from gh.telegram import service as tsvc

log = logging.getLogger("gh.telegram.backup")

KINDS = ("incident", "security", "digest_fallback")        # `code` chỉ đi qua send_code
CODE_PURPOSE = b"backup-code"
CODE_TTL = timedelta(minutes=10)
CODE_SCRUBBED = "[mã đã gửi — không lưu lại]"
TEXT_MAX = 3900
FLUSH_BATCH = 20
FLUSH_MAX_ATTEMPTS = 5
FLUSH_BUDGET_S = 50.0
KEEP = timedelta(days=7)
ACTOR = "system:backup"
LOCK_ACTIONS = ("assistant.lock", "assistant.lock_denied", "assistant.unlock")


class BackupUnavailable(Exception):
    """Chưa có kênh phụ gửi được mã (chưa nối Telegram)."""


@dataclass(frozen=True)
class LockResult:
    ok: bool
    code: str | None = None
    already: bool = False


# ─── gửi ─────────────────────────────────────────────────────────────────────

async def _telegram_enabled(db: AsyncSession, org_id: uuid.UUID) -> bool:
    return bool((await db.execute(text("SELECT enabled FROM ops.notify_channels WHERE org_id = :o"), {"o": org_id})).scalar_one_or_none())


async def notify(db: AsyncSession, org_id: uuid.UUID, text_: str, *, kind: str = "incident", dedupe_key: str | None = None) -> bool:
    """Xếp một tin báo vào kênh phụ (không commit). Trả True nếu thêm dòng mới (trùng `dedupe_key` ⇒ False)."""
    if kind not in KINDS:
        raise ValueError(f"kind lạ: {kind}")
    channel = "telegram" if await _telegram_enabled(db, org_id) else "console"
    row = (await db.execute(text("""
        INSERT INTO ops.backup_outbox (org_id, channel, kind, dedupe_key, text, sent_at)
        VALUES (:o, :c, :k, :d, :t, CASE WHEN :c = 'console' THEN now() END)
        ON CONFLICT (org_id, channel, dedupe_key) DO NOTHING RETURNING id"""),
        {"o": org_id, "c": channel, "k": kind, "d": dedupe_key, "t": text_[:TEXT_MAX]})).scalar_one_or_none()
    return row is not None


def hash_code(org_id: uuid.UUID, code: str) -> str:
    return crypto.keyed_digest(CODE_PURPOSE, f"{org_id}:{code}".encode()).hex()


def verify_code(org_id: uuid.UUID, code: str, code_hash: str) -> bool:
    return hmac.compare_digest(hash_code(org_id, code), code_hash)


CODE_SUPERSEDED = "SUPERSEDED"
PREVIEW_MAX = 60


def _clip(s: str | None, n: int) -> str:
    t = " ".join(str(s or "").split())
    return t if len(t) <= n else t[:n] + "…"


async def send_code(db: AsyncSession, org_id: uuid.UUID, *, ask_code: str | None = None, recipient: str | None = None,
                    preview: str | None = None) -> str:
    """Sinh mã 4 số, xếp tin gửi qua Telegram, trả BĂM. Không commit.
    v0.1.76 (T2, review bảo mật): tin nêu RÕ mã dùng cho đề xuất nào (`#MÃ`, người nhận, ~60 ký tự đầu nội dung) + cách báo động;
    mỗi tổ chức chỉ MỘT mã còn hiệu lực — tin mã cũ chưa gửi bị huỷ + xoá chữ (`SUPERSEDED`)."""
    row = (await db.execute(text("""SELECT owner_chat_id, owner_tg_user_id FROM ops.notify_channels WHERE org_id = :o AND enabled"""),
                            {"o": org_id})).one_or_none()
    if row is None or not row.owner_chat_id or not row.owner_tg_user_id:
        raise BackupUnavailable("Chưa ghép Telegram của Sếp (mã chỉ gửi riêng cho Sếp, không gửi vào chat chung)")
    code = f"{secrets.randbelow(10000):04d}"
    await db.execute(text("""UPDATE ops.backup_outbox SET failed_code = :f, text = :s
                             WHERE org_id = :o AND kind = 'code' AND sent_at IS NULL AND failed_code IS NULL"""),
                     {"o": org_id, "f": CODE_SUPERSEDED, "s": CODE_SCRUBBED})
    what = []
    if ask_code:
        what.append(f"đề xuất {ask_code}")
    if recipient:
        what.append(f"gửi cho «{_clip(recipient, 60)}»")
    if preview:
        what.append(f"nội dung «{_clip(preview, PREVIEW_MAX)}»")
    about = (" — dùng cho " + ", ".join(what)) if what else ""
    await db.execute(text("""
        INSERT INTO ops.backup_outbox (org_id, channel, kind, dedupe_key, text)
        VALUES (:o, 'telegram', 'code', :d, :t)"""),
        {"o": org_id, "d": f"code:{row.owner_chat_id}:{uuid.uuid4().hex}",   # chat đích (không phải bí mật) ghi theo hàng: bảng chưa có cột riêng
         "t": f"Mã xác nhận của Sếp: {code}{about} (dùng trong 10 phút; không đưa mã này cho ai, kể cả người xưng là Gen). "
              "Nếu không phải Sếp vừa yêu cầu: nhắn «khoá trợ lý»."})
    return hash_code(org_id, code)


async def flush(sm: Any, *, transport: Any = None) -> dict[str, int]:
    """Gửi ≤ `FLUSH_BATCH` tin Telegram đang chờ. Mã (`kind='code'`) bị xoá chữ ngay khi gửi xong, hỏng hẳn hoặc quá `CODE_TTL`."""
    client = tg.client_for(transport)
    stats = {"sent": 0, "retry": 0, "failed": 0}
    started = time.monotonic()
    async with sm() as db:
        # Mã quá hạn: xoá chữ + đánh hỏng, KHÔNG gửi.
        expired = await db.execute(text("""
            UPDATE ops.backup_outbox SET failed_code = 'expired', text = :s
            WHERE kind = 'code' AND sent_at IS NULL AND failed_code IS NULL AND created_at < now() - make_interval(secs => :ttl)"""),
            {"s": CODE_SCRUBBED, "ttl": CODE_TTL.total_seconds()})
        stats["failed"] += int(expired.rowcount or 0)  # type: ignore[attr-defined]
        await db.execute(text("DELETE FROM ops.backup_outbox WHERE created_at < now() - make_interval(secs => :k)"), {"k": KEEP.total_seconds()})
        await db.commit()
        rows = (await db.execute(text("""
            SELECT id, org_id, kind, attempts, dedupe_key FROM ops.backup_outbox
            WHERE channel = 'telegram' AND sent_at IS NULL AND failed_code IS NULL AND attempts < :a AND next_attempt_at <= now()
            ORDER BY created_at LIMIT :n"""), {"a": FLUSH_MAX_ATTEMPTS, "n": FLUSH_BATCH})).all()
        await db.commit()
        creds: dict[uuid.UUID, tuple[str, str] | None] = {}
        blocked: set[uuid.UUID] = set()
        for r in rows:
            if r.org_id in blocked or time.monotonic() - started > FLUSH_BUDGET_S:
                continue
            if r.org_id not in creds:
                try:
                    creds[r.org_id] = await tsvc._org_token(db, r.org_id)
                except Exception:  # noqa: BLE001 — token không giải được
                    log.warning("kênh phụ: không giải mã được token của tổ chức %s", r.org_id)
                    creds[r.org_id] = None
            cred = creds[r.org_id]
            scrub = ", text = :s" if r.kind == "code" else ""
            params = {"i": r.id, "s": CODE_SCRUBBED}
            if cred is None:
                await db.execute(text(f"UPDATE ops.backup_outbox SET failed_code = :c{scrub} WHERE id = :i AND sent_at IS NULL"),  # noqa: S608
                                 {**params, "c": tsvc.NOT_CONFIGURED})
                await db.commit()
                stats["failed"] += 1
                continue
            mine = (await db.execute(text("""SELECT text FROM ops.backup_outbox WHERE id = :i AND sent_at IS NULL AND failed_code IS NULL
                                             FOR UPDATE SKIP LOCKED"""), {"i": r.id})).first()
            if mine is None:
                await db.rollback()
                continue
            chat = cred[1]
            if r.kind == "code":
                # Mã CHỈ tới chat riêng của Sếp đã ghép lúc tạo mã; không còn khớp (đổi/gỡ ghép) ⇒ huỷ, không bao giờ rơi về chat chung.
                target = str(r.dedupe_key or "").split(":")[1] if str(r.dedupe_key or "").count(":") >= 2 else ""
                cur = (await db.execute(text("SELECT owner_chat_id FROM ops.notify_channels WHERE org_id = :o"), {"o": r.org_id})).scalar_one_or_none()
                if not target or cur != target:
                    await db.execute(text("UPDATE ops.backup_outbox SET failed_code = 'OWNER_CHAT_CHANGED', text = :s WHERE id = :i"), params)
                    await db.commit()
                    stats["failed"] += 1
                    continue
                chat = target
            try:
                await client.send_message(cred[0], chat, mine.text)
            except tg.TelegramError as e:
                if e.code == tg.RATE_LIMITED:
                    await db.execute(text("UPDATE ops.backup_outbox SET next_attempt_at = now() + make_interval(secs => :s) WHERE id = :i"),
                                     {"s": int(e.retry_after or 30), "i": r.id})
                    blocked.add(r.org_id)
                    stats["retry"] += 1
                elif e.code in tsvc.FATAL_CODES or e.code == tg.BAD_REQUEST:
                    await db.execute(text(f"UPDATE ops.backup_outbox SET failed_code = :c{scrub} WHERE id = :i"),  # noqa: S608
                                     {**params, "c": e.code})
                    if e.code in tsvc.FATAL_CODES:
                        blocked.add(r.org_id)
                    stats["failed"] += 1
                else:
                    n = int(r.attempts) + 1
                    await db.execute(text("""UPDATE ops.backup_outbox SET attempts = :n, next_attempt_at = now() + make_interval(mins => :m)
                                             WHERE id = :i"""), {"n": n, "m": 2 ** n, "i": r.id})
                    stats["retry"] += 1
                await db.commit()
                continue
            await db.execute(text(f"UPDATE ops.backup_outbox SET sent_at = now(){scrub} WHERE id = :i"), params)  # noqa: S608
            await db.commit()
            stats["sent"] += 1
    return stats


# ─── khoá trợ lý ─────────────────────────────────────────────────────────────

async def _authorize(db: AsyncSession, org_id: uuid.UUID, via: str, *, user_id: uuid.UUID | None, tg_user_id: str | None,
                     chat_id: str | None, channel: str | None, sender_external_id: str | None,
                     sender_alt_id: str | None) -> str | None:
    """None = đúng Sếp; ngược lại mã lỗi. Chỉ so ID — không có tham số tên."""
    if via == "console":
        if user_id is None or user_id not in await notifications.owner_ids(db, org_id):
            return "NOT_OWNER"
        return None
    if via == "telegram":
        row = (await db.execute(text("""SELECT owner_chat_id, owner_tg_user_id FROM ops.notify_channels
                                        WHERE org_id = :o AND enabled"""), {"o": org_id})).one_or_none()
        if (row is None or not row.owner_chat_id or not row.owner_tg_user_id or not tg_user_id or not chat_id
                or not hmac.compare_digest(str(row.owner_tg_user_id), str(tg_user_id))
                or not hmac.compare_digest(str(row.owner_chat_id), str(chat_id))):
            return "NOT_OWNER"
        return None
    if via == "owner_channel":
        from gh.owner_channel.identity import is_owner, owner_ids

        if channel not in ("zalo", "whatsapp"):
            return "NOT_OWNER"
        if not is_owner(await owner_ids(db, org_id, channel), sender_external_id, sender_alt_id):
            return "NOT_OWNER"
        return None
    return "BAD_VIA"


async def is_locked(db: AsyncSession, org_id: uuid.UUID) -> bool:
    return bool((await db.execute(text("SELECT settings->'intel'->>'assistant_locked_at' IS NOT NULL FROM core.organizations WHERE id = :o"),
                                  {"o": org_id})).scalar_one_or_none())


async def lock_assistant(db: AsyncSession, org_id: uuid.UUID, *, via: str, user_id: uuid.UUID | None = None, tg_user_id: str | None = None,
                         chat_id: str | None = None, channel: str | None = None, sender_external_id: str | None = None,
                         sender_alt_id: str | None = None) -> LockResult:
    """Khoá trợ lý ngay. Không commit (bên gọi commit — kể cả khi bị từ chối, để dòng `assistant.lock_denied` được giữ)."""
    why = await _authorize(db, org_id, via, user_id=user_id, tg_user_id=tg_user_id, chat_id=chat_id, channel=channel,
                           sender_external_id=sender_external_id, sender_alt_id=sender_alt_id)
    if why is not None:
        await actionlog.record(db, org_id=org_id, actor_type="system", actor_id=ACTOR, action="assistant.lock_denied", result="blocked",
                               detail={"via": via, "code": why})
        return LockResult(False, why)
    if await is_locked(db, org_id):
        return LockResult(True, already=True)
    from gh.owner_channel import inbound as owner_inbound

    await db.execute(text("""UPDATE core.organizations SET settings = settings || jsonb_build_object('intel',
                               COALESCE(settings->'intel', '{}'::jsonb) || jsonb_build_object('assistant_locked_at', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
                                                                                              'assistant_locked_via', CAST(:v AS text)))
                             WHERE id = :o"""), {"o": org_id, "v": via})
    await db.execute(text("UPDATE ops.owner_channels SET enabled = false WHERE org_id = :o"), {"o": org_id})
    for ch in ("zalo", "whatsapp"):
        await owner_inbound.disable_support(db, org_id, ch, reason="abuse_guard")
    await db.execute(text("""UPDATE ops.owner_outbox SET failed_code = 'ASSISTANT_LOCKED'
                             WHERE org_id = :o AND sent_at IS NULL AND failed_code IS NULL"""), {"o": org_id})
    await actionlog.record(db, org_id=org_id, actor_type="system", actor_id=ACTOR, action="assistant.lock", detail={"via": via})
    await notify(db, org_id, "Trợ lý đã bị KHOÁ: kênh quản trị và chế độ hỗ trợ nhóm đã tắt, tin đang chờ gửi đã huỷ. "
                             "Mở khoá và bật lại từng phần trên Console (cần mã PIN).", kind="security", dedupe_key=f"assistant.lock:{uuid.uuid4().hex}")
    return LockResult(True)


async def unlock_assistant(db: AsyncSession, org_id: uuid.UUID, *, user_id: uuid.UUID | None) -> LockResult:
    """Chỉ Owner Console. Xoá cờ khoá; KHÔNG bật lại kênh quản trị/hỗ trợ nhóm."""
    why = await _authorize(db, org_id, "console", user_id=user_id, tg_user_id=None, chat_id=None, channel=None,
                           sender_external_id=None, sender_alt_id=None)
    if why is not None:
        await actionlog.record(db, org_id=org_id, actor_type="system", actor_id=ACTOR, action="assistant.lock_denied", result="blocked",
                               detail={"via": "console", "code": why, "op": "unlock"})
        return LockResult(False, why)
    await db.execute(text("""UPDATE core.organizations SET settings = jsonb_set(settings, '{intel}',
                               (COALESCE(settings->'intel', '{}'::jsonb) - 'assistant_locked_at') - 'assistant_locked_via')
                             WHERE id = :o"""), {"o": org_id})
    await actionlog.record(db, org_id=org_id, actor_type="user", actor_id=str(user_id), action="assistant.unlock")
    return LockResult(True)
