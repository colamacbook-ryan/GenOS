# ruff: noqa: E501
"""Xác nhận mạnh (spec r6 §8.1) — gói T2 thay thân stub của T1, GIỮ chữ ký `require(db, org_id, action) -> Allowed | NeedConsole`
(thêm `NeedCode` cho riêng `outbound`, tham số tuỳ chọn `via`, `pin_verified`, `now`).

Hai nhóm luật:
1. **Tin gửi khách** (`outbound`, «OK #MÃ» trong kênh quản trị): hợp lệ khi MỘT trong hai —
   (a) trong ngày VN đó Sếp đã "mở khoá hôm nay" bằng PIN trên Console (`unlock_today`), hoặc
   (b) **mã kép**: hệ thống gửi mã 4 số qua kênh phụ (`backup.send_code`, chỉ lưu BĂM ở Redis, gắn đúng một đề xuất), Sếp nhắn
       «OK #MÃ 4821». Sai 3 lần (đếm theo TỔ CHỨC trong Nhật ký, không theo phiên/kênh) ⇒ khoá «OK» `outbound` 1 giờ + báo kênh phụ.
   Mặc định: (b) nếu đã ghép kênh phụ, nếu không thì (a).
2. **Hạ phòng thủ** (`DEFENSIVE`): chỉ Console + PIN; từ chat luôn `NeedConsole` ("mở Console"). Mọi lần thực hiện báo kênh phụ.
   "Tắt bản tin" và mọi lệnh đổi giờ yên lặng (`NOTIFY_ONLY`) làm được từ chat nhưng LUÔN báo kênh phụ (kẻ chiếm tài khoản không
   tắt im lặng được).

Phát hiện bất thường kênh quản trị: lệnh nhạy cảm (mọi hành động ở đây) từ chat trong giờ yên lặng, hoặc ≥ 3 lệnh hạ phòng thủ /
đổi báo trong 10 phút ⇒ khoá «OK» `outbound` 1 giờ + báo kênh phụ. Trợ lý đang bị "khoá trợ lý" ⇒ mọi hành động từ chat bị chặn.

Không bao giờ ghi mã rõ vào log / Nhật ký / kết quả (chỉ có trong hàng đợi kênh phụ tới lúc gửi — xem `gh.telegram.backup`).
Không commit: bên gọi commit (kể cả khi bị từ chối, để dòng Nhật ký đếm lần sai được giữ).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import notifications
from gh.biz.intel import levels
from gh.chassis import actionlog
from gh.telegram import backup

#: Hành động cần xác nhận mạnh (§8.1): tin gửi khách + "hạ phòng thủ" + hai lệnh chỉ cần báo kênh phụ.
ACTIONS = ("outbound", "mute_defensive", "report_pref_defensive", "intel_mode_shadow", "rebind_account", "rebind_backup_channel",
           "payment_identifier_verify", "forget_person", "quiet_hours_change", "digest_off")
OUTBOUND = "outbound"
DEFENSIVE = ("mute_defensive", "report_pref_defensive", "intel_mode_shadow", "rebind_account", "rebind_backup_channel",
             "payment_identifier_verify", "forget_person")
NOTIFY_ONLY = ("quiet_hours_change", "digest_off")
LABEL = {"outbound": "gửi tin cho khách", "mute_defensive": "tắt/bớt báo lừa đảo hoặc an toàn",
         "report_pref_defensive": "đổi cách báo / giờ yên lặng chạm lừa đảo hoặc an toàn", "intel_mode_shadow": "chuyển tình báo sang chạy bóng",
         "rebind_account": "đổi tài khoản ghép", "rebind_backup_channel": "đổi / ghép lại kênh phụ",
         "payment_identifier_verify": "thêm / xác minh số tài khoản", "forget_person": "quên một người",
         "quiet_hours_change": "đổi giờ yên lặng", "digest_off": "tắt bản tin"}

LOCK_TTL = timedelta(hours=1)
MAX_CODE_FAILS = 3
CODE_TTL_S = int(backup.CODE_TTL.total_seconds())
DANGER_WINDOW = timedelta(minutes=10)
DANGER_MAX = 3
ACTOR = "system:strong_confirm"
_CODE_RE = re.compile(r"^[0-9]{4}$")
MAX_CODES_PER_WINDOW = 3
CODE_WINDOW = timedelta(minutes=10)

CONSOLE_REASON = "Việc này cần mở Console (mã PIN)"
UNLOCK_REASON = ("Tin gửi khách cần xác nhận mạnh: Sếp mở Console › Hệ thống › Tình báo › «Mở khoá hôm nay» (mã PIN), "
                 "hoặc ghép kênh phụ Telegram để nhận mã kép")
LOCKED_REASON = "«OK» gửi tin cho khách đang tạm khoá tới {until} (giờ VN) vì có dấu hiệu bất thường. Sếp mở Console (mã PIN) để gửi"
ASSISTANT_LOCKED_REASON = "Trợ lý đang bị khoá — Sếp mở khoá trên Console (mã PIN)"


@dataclass(frozen=True)
class Allowed:
    action: str
    via: str


@dataclass(frozen=True)
class NeedConsole:
    action: str
    reason: str = CONSOLE_REASON


@dataclass(frozen=True)
class NeedCode:
    """Chỉ cho `outbound`: cần mã kép qua kênh phụ (bên gọi gọi `issue_code` rồi hỏi Sếp «OK #MÃ <4 số>»)."""
    action: str


class CodeRateLimited(Exception):
    """Đã cấp quá `MAX_CODES_PER_WINDOW` mã trong `CODE_WINDOW` (chống dội mã / dò mã qua cấp lại liên tục)."""


@dataclass(frozen=True)
class WrongCode:
    action: str
    left: int


# ─── trạng thái (core.organizations.settings.intel) ─────────────────────────

def _now(now: datetime | None) -> datetime:
    t = now or datetime.now(UTC)
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def vn_day(now: datetime | None = None) -> str:
    return _now(now).astimezone(levels.VN).date().isoformat()


def _parse(v: Any) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


async def _intel(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    v = (await db.execute(text("SELECT settings -> 'intel' FROM core.organizations WHERE id = :o"), {"o": org_id})).scalar_one_or_none()
    return v if isinstance(v, dict) else {}


async def _set_intel(db: AsyncSession, org_id: uuid.UUID, patch: dict[str, Any]) -> None:
    await db.execute(text("""UPDATE core.organizations SET settings = settings || jsonb_build_object('intel',
                               COALESCE(settings->'intel', '{}'::jsonb) || CAST(:p AS jsonb)) WHERE id = :o"""),
                     {"o": org_id, "p": orjson.dumps(patch).decode()})


async def outbound_locked_until(db: AsyncSession, org_id: uuid.UUID, now: datetime | None = None) -> datetime | None:
    t = _parse((await _intel(db, org_id)).get("outbound_locked_until"))
    return t if t is not None and t > _now(now) else None


async def unlocked_today(db: AsyncSession, org_id: uuid.UUID, now: datetime | None = None) -> bool:
    return (await _intel(db, org_id)).get("outbound_unlocked_on") == vn_day(now)


async def _quiet(db: AsyncSession, org_id: uuid.UUID, now: datetime) -> bool:
    s = await _intel(db, org_id)
    q = levels.QuietHours(start=str(s.get("quiet_start") or "22:00"), end=str(s.get("quiet_end") or "07:00"))
    try:
        return levels.in_quiet(now, q)
    except (ValueError, TypeError):           # giờ cài sai định dạng ⇒ coi như mặc định 22:00–07:00 (nghiêng về an toàn)
        return levels.in_quiet(now, levels.QuietHours())


async def _serialize(db: AsyncSession, org_id: uuid.UUID) -> None:
    """Tuần tự hoá mọi đếm/khoá của một tổ chức trong transaction (hai lượt «OK … mã» song song không lách được trần 3 lần sai)."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('strong_confirm:' || :o))"), {"o": str(org_id)})


async def _count(db: AsyncSession, org_id: uuid.UUID, action: str, since: datetime) -> int:
    return int((await db.execute(text("""SELECT count(*) FROM ops.action_log WHERE org_id = :o AND action = :a AND at > :s"""),
                                 {"o": org_id, "a": action, "s": since})).scalar_one())


async def _last(db: AsyncSession, org_id: uuid.UUID, action: str) -> datetime | None:
    return (await db.execute(text("SELECT max(at) FROM ops.action_log WHERE org_id = :o AND action = :a"),  # type: ignore[no-any-return]
                             {"o": org_id, "a": action})).scalar_one_or_none()


async def _record(db: AsyncSession, org_id: uuid.UUID, action: str, *, result: str = "ok", **detail: Any) -> None:
    await actionlog.record(db, org_id=org_id, actor_type="system", actor_id=ACTOR, action=action, result=result, detail=detail)


async def lock_outbound(db: AsyncSession, org_id: uuid.UUID, *, reason: str, now: datetime | None = None) -> datetime:
    """Khoá «OK» `outbound` 1 giờ (kéo dài nếu đang khoá) + Nhật ký + báo kênh phụ."""
    until = _now(now) + LOCK_TTL
    await _set_intel(db, org_id, {"outbound_locked_until": until.astimezone(UTC).isoformat().replace("+00:00", "Z")})
    await _record(db, org_id, "strong_confirm.outbound_locked", result="blocked", reason=reason)
    hhmm = until.astimezone(levels.VN).strftime("%H:%M")
    why = {"code_fails": "nhập sai mã kép 3 lần", "quiet_hours": "có lệnh nhạy cảm trong giờ yên lặng",
           "danger_burst": "có nhiều lệnh hạ phòng thủ liên tiếp"}.get(reason, reason)
    await backup.notify(db, org_id, f"Cảnh báo an ninh: kênh quản trị {why}. Em đã tạm khoá «OK» gửi tin cho khách tới {hhmm}. "
                                    "Nếu không phải Sếp: dùng lệnh «khoá trợ lý» và đổi mật khẩu tài khoản ghép.",
                        kind="security", dedupe_key=f"sc.lock:{uuid.uuid4().hex}")
    return until


def _locked(action: str, until: datetime) -> NeedConsole:
    return NeedConsole(action, LOCKED_REASON.format(until=until.astimezone(levels.VN).strftime("%H:%M")))


# ─── luật ───────────────────────────────────────────────────────────────────

async def require(db: Any, org_id: uuid.UUID, action: str, *, via: str = "chat", pin_verified: bool = False,
                  now: datetime | None = None) -> Allowed | NeedConsole | NeedCode:
    """Quyết hành động nhạy cảm có làm được không. `via='chat'` (mặc định — kênh quản trị) hoặc `'console'` (+ `pin_verified`).
    Fail-closed: hành động lạ, không có DB, hay lỗi trạng thái ⇒ `NeedConsole`."""
    if db is None or action not in ACTIONS:
        return NeedConsole(action)
    t = _now(now)
    if via == "console":
        if not pin_verified:
            return NeedConsole(action)
        if action in DEFENSIVE or action in NOTIFY_ONLY or action == OUTBOUND:
            await _record(db, org_id, "strong_confirm.console", target=action)
            if action != OUTBOUND:
                await backup.notify(db, org_id, f"Sếp vừa {LABEL[action]} trên Console (đã nhập mã PIN). Nếu không phải Sếp: «khoá trợ lý» ngay.",
                                    kind="security", dedupe_key=f"sc.console:{uuid.uuid4().hex}")
        return Allowed(action, "console_pin")
    if via != "chat":
        return NeedConsole(action)
    if await backup.is_locked(db, org_id):
        return NeedConsole(action, ASSISTANT_LOCKED_REASON)
    await _serialize(db, org_id)
    quiet = await _quiet(db, org_id, t)
    if action != OUTBOUND:
        await _record(db, org_id, "strong_confirm.chat_command", result="blocked" if action in DEFENSIVE else "ok", target=action, quiet=quiet)
        burst = await _count(db, org_id, "strong_confirm.chat_command", t - DANGER_WINDOW) >= DANGER_MAX
        if (quiet or burst) and await outbound_locked_until(db, org_id, t) is None:
            await lock_outbound(db, org_id, reason="quiet_hours" if quiet else "danger_burst", now=t)
        if action in DEFENSIVE:
            return NeedConsole(action)
        await backup.notify(db, org_id, f"Kênh quản trị vừa {LABEL[action]}. Nếu không phải Sếp: «khoá trợ lý» ngay.",
                            kind="security", dedupe_key=f"sc.notify:{uuid.uuid4().hex}")
        return Allowed(action, "chat")
    blocked = await _outbound_gate(db, org_id, t, quiet=quiet, step="require")
    if blocked is not None:
        return blocked
    if await unlocked_today(db, org_id, t):
        return Allowed(action, "unlock_today")
    if await _backup_paired(db, org_id):
        return NeedCode(action)
    return NeedConsole(action, UNLOCK_REASON)


async def _outbound_gate(db: AsyncSession, org_id: uuid.UUID, t: datetime, *, quiet: bool, step: str) -> NeedConsole | None:
    """MỘT cổng chung cho MỌI đường mở `outbound` từ chat (`require` và `check_code` — bài học L-90: đường anh em phải cùng cổng).
    Bên gọi đã kiểm "khoá trợ lý" và đã `_serialize`. Đang khoá ⇒ chặn; trong giờ yên lặng ⇒ ghi lần thử, khoá 1 giờ, báo kênh phụ, chặn."""
    until = await outbound_locked_until(db, org_id, t)
    if until is not None:
        return _locked(OUTBOUND, until)
    if quiet:
        await _record(db, org_id, "strong_confirm.chat_command", result="blocked", target=OUTBOUND, quiet=True, step=step)
        return _locked(OUTBOUND, await lock_outbound(db, org_id, reason="quiet_hours", now=t))
    return None


async def _backup_paired(db: AsyncSession, org_id: uuid.UUID) -> bool:
    row = (await db.execute(text("SELECT owner_chat_id, owner_tg_user_id FROM ops.notify_channels WHERE org_id = :o AND enabled"),
                            {"o": org_id})).one_or_none()
    return bool(row is not None and row.owner_chat_id and row.owner_tg_user_id)


async def unlock_today(db: AsyncSession, org_id: uuid.UUID, *, user_id: uuid.UUID | None, pin_verified: bool,
                       now: datetime | None = None) -> bool:
    """"Mở khoá hôm nay" (Console + PIN, chỉ Owner): «OK #MÃ» `outbound` trong ngày VN này không cần mã kép. Không gỡ khoá bất thường
    (khoá 1 giờ vẫn giữ). Trả False nếu không phải Owner / chưa PIN (đã ghi Nhật ký từ chối)."""
    if not pin_verified or user_id is None or user_id not in await notifications.owner_ids(db, org_id):
        await _record(db, org_id, "strong_confirm.unlock_denied", result="blocked", pin=bool(pin_verified))
        return False
    day = vn_day(now)
    await _set_intel(db, org_id, {"outbound_unlocked_on": day})
    await actionlog.record(db, org_id=org_id, actor_type="user", actor_id=f"user:{user_id}", action="strong_confirm.unlock_today",
                           detail={"day": day})
    await backup.notify(db, org_id, f"Sếp vừa «mở khoá hôm nay» ({day}) trên Console: «OK #MÃ» gửi tin cho khách trong hôm nay không cần mã kép.",
                        kind="security", dedupe_key=f"sc.unlock:{day}:{uuid.uuid4().hex}")
    return True


# ─── mã kép ─────────────────────────────────────────────────────────────────

def code_key(org_id: uuid.UUID, ref: str | None = None) -> str:
    """MỘT khoá mã cho mỗi tổ chức (review bảo mật: một mã còn hiệu lực/tổ chức); giá trị = `<ref>|<băm>`. `ref` giữ để tương thích."""
    del ref
    return f"gh:intel:sc:code:{org_id}"


def pass_key(session_id: uuid.UUID | str) -> str:
    """Dấu "đã qua xác nhận mạnh" gắn MỘT phiên `chat_ok` — `POST /drafts/{id}/approve` bằng phiên `chat_ok` phải có dấu này."""
    return f"gh:intel:sc:pass:{session_id}"


async def issue_code(db: AsyncSession, redis: Any, org_id: uuid.UUID, ref: str, *, ask_code: str | None = None,
                     recipient: str | None = None, preview: str | None = None) -> None:
    """Gửi mã 4 số qua kênh phụ cho đề xuất `ref`; chỉ lưu BĂM (Redis, 10 phút). Mỗi tổ chức MỘT mã: cấp mã mới (đề xuất nào cũng
    vậy) ⇒ mã cũ mất hiệu lực. Tối đa `MAX_CODES_PER_WINDOW` lần/`CODE_WINDOW` ⇒ `CodeRateLimited`.
    Không có kênh phụ ⇒ `backup.BackupUnavailable`."""
    await _serialize(db, org_id)
    if await _count(db, org_id, "strong_confirm.code_sent", _now(None) - CODE_WINDOW) >= MAX_CODES_PER_WINDOW:
        await _record(db, org_id, "strong_confirm.code_rate_limited", result="blocked", ref=ref)
        raise CodeRateLimited(ref)
    h = await backup.send_code(db, org_id, ask_code=ask_code, recipient=recipient, preview=preview)
    await redis.set(code_key(org_id), f"{ref}|{h}", ex=CODE_TTL_S)
    await _record(db, org_id, "strong_confirm.code_sent", ref=ref)


async def check_code(db: AsyncSession, redis: Any, org_id: uuid.UUID, ref: str, code: str,
                     now: datetime | None = None) -> Allowed | NeedConsole | NeedCode | WrongCode:
    """So mã kép cho đề xuất `ref`. Đúng ⇒ `Allowed` (mã dùng một lần). Sai ⇒ `WrongCode(left)`; sai lần thứ 3 trong 1 giờ (theo tổ
    chức, mọi phiên/kênh) ⇒ khoá 1 giờ, `NeedConsole`. Chưa có mã còn hạn ⇒ `NeedCode` (bên gọi gửi mã mới)."""
    t = _now(now)
    if await backup.is_locked(db, org_id):
        return NeedConsole(OUTBOUND, ASSISTANT_LOCKED_REASON)
    await _serialize(db, org_id)
    blocked = await _outbound_gate(db, org_id, t, quiet=await _quiet(db, org_id, t), step="check_code")
    if blocked is not None:
        return blocked
    raw = await redis.get(code_key(org_id))
    stored = (raw.decode() if isinstance(raw, bytes) else str(raw)) if raw is not None else ""
    sref, _, h = stored.rpartition("|")
    if not h or sref != ref:              # chưa có mã, hết hạn, hoặc mã đang sống là của đề xuất KHÁC ⇒ không so (không đếm sai)
        return NeedCode(OUTBOUND)
    if _CODE_RE.match(code or "") and backup.verify_code(org_id, code, h):
        await redis.delete(code_key(org_id))
        await _record(db, org_id, "strong_confirm.code_ok", ref=ref)
        return Allowed(OUTBOUND, "double_code")
    await _record(db, org_id, "strong_confirm.code_failed", result="blocked", ref=ref)
    since = max(t - LOCK_TTL, (await _last(db, org_id, "strong_confirm.code_ok")) or (t - LOCK_TTL))
    fails = await _count(db, org_id, "strong_confirm.code_failed", since)
    if fails >= MAX_CODE_FAILS:
        await redis.delete(code_key(org_id))
        return _locked(OUTBOUND, await lock_outbound(db, org_id, reason="code_fails", now=t))
    return WrongCode(OUTBOUND, MAX_CODE_FAILS - fails)
