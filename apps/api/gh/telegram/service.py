"""Kênh "Báo động & bản tin" qua Telegram (v0.1.44 — F-8c, F-6b): cấu hình mã hoá, hộp thư đi, tệp cho trực canh.

Gen gửi bản tin 07:30/17:30 và nhắc việc cho Sếp; Telegram không có nút duyệt/hành động — mọi thao tác Sếp xác nhận
trong Console. v0.1.64: Sếp có thể nhắn Gen qua bot (chỉ tài khoản đã ghép, chỉ đọc/đề xuất) — xem
`gh.telegram.inbound`, `gh.telegram.commands`. KHÔNG đi qua bridge/Zalo ở bất kỳ đường nào.

- `ops.notify_channels` (migration 0029): một cấu hình mỗi tổ chức. Token bot CHỈ ở `token_enc`
  (`crypto.encrypt(token, TOKEN_AAD)`, có trong gh/bundle.py::REENCRYPT_TARGETS). Không bao giờ vào log, phản hồi,
  Action Log, ops.boss_checks hay hộp thư đi.
- `ops.telegram_outbox`: `enqueue` ghi trong CÙNG transaction với việc gây ra (bản tin, nhắc việc) — khử trùng lặp
  theo (org_id, dedupe_key); worker `telegram_flush` gọi `flush_outbox` mỗi phút.
- `run/telegram.json` (hợp đồng với genh — Trực canh máy chủ gửi cảnh báo sự cố khi api đã chết):
  {"schema":1,"enabled":true,"enc":"<base64 GH1>","briefing","reminders","updated_at"} | {"schema":1,"enabled":false,
  "updated_at"}. `enc` = crypto.encrypt(JSON gọn {"token","chat_id"}, NOTIFY_AAD) bằng khoá master. Ghi nguyên tử
  (tmp + rename, 0644) sau lưu/xoá và lúc api khởi động (sau nhập gói/đổi khoá vẫn khớp). Không có thư mục ⇒ bỏ qua.
- Đường cảnh báo SỰ CỐ duy nhất là genh watchdog — api không gửi Telegram cho sự cố (tránh gửi đôi). Gửi hộp thư đi
  hỏng vì cấu hình (token bị từ chối, chat sai, bot bị chặn) ⇒ sự cố `telegram.failed` trong api (chuông + dải
  "Cần Sếp xử lý"); genh đọc khoá đó qua run/api-health.json.
"""

import asyncio
import base64
import json
import logging
import os
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import crypto
from gh.errors import ApiError, field_errors
from gh.hostlink_io import write_request
from gh.owner_cmd.text import HUB_DOWN, NOTHING, UNREAD, defang, public_url  # noqa: F401 — import lại tên cũ (v0.1.71)
from gh.owner_cmd.text import briefing_items as _briefing_items
from gh.owner_cmd.text import line as _line
from gh.telegram import client as tg

log = logging.getLogger("gh.telegram")

TOKEN_AAD = b"telegram_token"
NOTIFY_AAD = b"telegram_notify"
TOKEN_RE = re.compile(r"^\d{5,12}:[A-Za-z0-9_-]{30,64}$")
CHAT_RE = re.compile(r"^-?[0-9]{1,20}$")
HOST_FILE = "telegram.json"
CHECK_KEY = "telegram"
KINDS = ("briefing", "reminder")
ALERT_KEY = "telegram.failed"
ALERT_LINK = "/connections#telegram"
ALERT_TITLE = "Gen chưa gửi được tin Telegram cho Sếp"
#: Lỗi cấu hình — tin hỏng hẳn (không thử lại) + sự cố telegram.failed.
FATAL_CODES = frozenset({tg.TOKEN_REJECTED, tg.CHAT_NOT_FOUND, tg.BOT_BLOCKED})
NOT_CONFIGURED = "TELEGRAM_NOT_CONFIGURED"

FLUSH_BATCH = 20
FLUSH_MAX_ATTEMPTS = 5
FLUSH_WINDOW = timedelta(hours=24)
FLUSH_BUDGET_S = 60.0
KEEP_SENT = timedelta(days=7)
ONE_WAY = "(Tin một chiều — mọi thao tác Sếp xác nhận trong Console.)"
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

MESSAGES: dict[str, str] = {
    NOT_CONFIGURED: "Chưa nối Telegram — làm theo hướng dẫn ở Kết nối › Telegram",
    tg.TOKEN_REJECTED: "Telegram từ chối token bot — chép lại token từ BotFather rồi lưu lại",
    tg.CHAT_NOT_FOUND: "Không tìm thấy chat_id này — Sếp mở bot trên Telegram, bấm Bắt đầu (Start) rồi bấm Tìm chat_id",
    tg.BOT_BLOCKED: "Bot chưa được phép nhắn Sếp (bị chặn hoặc chưa bấm Bắt đầu) — mở bot trên Telegram và bấm Bắt đầu "
                    "(Start)",
    tg.RATE_LIMITED: "Telegram đang giới hạn tốc độ gửi — thử lại sau ít phút",
    tg.UNREACHABLE: "Không kết nối được tới Telegram — kiểm tra mạng của máy chủ rồi thử lại",
    tg.POLL_CONFLICT: "Bot đang bị nơi khác giữ kênh nhận tin (webhook hoặc máy khác) — Gen chưa nhận được tin của Sếp",
    tg.BAD_REQUEST: "Telegram không nhận tin này — thử lại; nếu vẫn lỗi, gửi Gói chẩn đoán cho người hỗ trợ",
}
ALERT_BODIES: dict[str, str] = {
    tg.TOKEN_REJECTED: "Telegram từ chối token bot (có thể bot đã bị xoá hoặc token bị đổi). Mở Kết nối › Telegram, "
                       "bấm Đổi token/chat_id, dán token mới, bấm Lưu rồi bấm Gửi thử.",
    tg.CHAT_NOT_FOUND: "Không tìm thấy chat_id đã lưu. Mở Kết nối › Telegram, bấm Đổi token/chat_id, bấm Tìm chat_id, "
                       "chọn chat, bấm Lưu rồi bấm Gửi thử.",
    tg.BOT_BLOCKED: "Bot đang bị chặn hoặc chưa được bấm Bắt đầu (Start). Mở bot trên Telegram, bấm Bắt đầu rồi bấm "
                    "Gửi thử ở Kết nối › Telegram.",
}


def message_for(code: str | None) -> str | None:
    return MESSAGES.get(code or "", MESSAGES[tg.UNREACHABLE]) if code else None


def mask_chat(chat_id: str | None) -> str | None:
    """'987654321' → '•••4321'. Không bao giờ trả chuỗi gốc."""
    if not chat_id:
        return None
    digits = chat_id.lstrip("-")
    return "•••" + digits[-4:]


def _iso(v: datetime | None) -> str | None:
    return v.astimezone(UTC).isoformat().replace("+00:00", "Z") if v else None


#: Chưa bật Truy cập từ xa (public_url còn là localhost) thì link trong tin trên điện thoại không mở được (F092).
LOCAL_CONSOLE_LINE = "Mở Console từ máy chủ Gen-Harness (chưa bật Truy cập từ xa nên điện thoại chưa mở được link)."


def console_line(path: str) -> str:
    """Dòng "Mở Console" cuối tin: chỉ chèn link khi `public_url` đã là địa chỉ mở được từ ngoài máy chủ."""
    from gh.system_api.access import is_local_url  # nhập muộn: chỉ cần khi dựng tin

    url = public_url()
    return LOCAL_CONSOLE_LINE if is_local_url(url) else f"Mở Console: {url}{path}"


# ─── cấu hình ───────────────────────────────────────────────────────────────

async def get_config(db: AsyncSession, org_id: uuid.UUID) -> Any:
    return (await db.execute(text("""SELECT org_id, token_enc, chat_id, bot_username, enabled, briefing, reminders,
                                            updated_at FROM ops.notify_channels WHERE org_id = :o"""),
                             {"o": org_id})).one_or_none()


def decrypt_token(row: Any) -> str:
    return crypto.decrypt(bytes(row.token_enc), TOKEN_AAD).decode()


def view(row: Any) -> dict[str, Any]:
    """Phần cấu hình của GET /notify/telegram — KHÔNG BAO GIỜ có token (chat_id cũng chỉ ở dạng che)."""
    if row is None:
        return {"configured": False, "enabled": False, "bot_username": None, "chat_id_masked": None,
                "briefing": True, "reminders": True, "updated_at": None}
    return {"configured": True, "enabled": bool(row.enabled), "bot_username": row.bot_username,
            "chat_id_masked": mask_chat(row.chat_id), "briefing": bool(row.briefing),
            "reminders": bool(row.reminders), "updated_at": _iso(row.updated_at)}


def _same_token(row: Any, token: str) -> bool:
    try:
        return decrypt_token(row) == token
    except Exception:  # noqa: BLE001 — token cũ không giải được (đổi khoá) ⇒ coi như khác
        return False


def validate_token(token: str) -> str:
    token = token.strip()
    if not TOKEN_RE.fullmatch(token):
        raise field_errors({"token": "Token bot không đúng dạng — chép nguyên dòng BotFather gửi (dạng 123456789:AA…)"})
    return token


async def save_config(db: AsyncSession, org_id: uuid.UUID, token: str | None, chat_id: str | None, *,
                      enabled: bool | None, briefing: bool | None, reminders: bool | None, user_id: uuid.UUID | None,
                      client: tg.TelegramClient) -> Any:
    """Kiểm dạng; token mới ⇒ hỏi getMe (lưu bot_username), mã hoá `TOKEN_AAD`. Token/chat_id trống khi đã có cấu
    hình ⇒ giữ giá trị cũ (Sếp chỉ thấy chat_id dạng che, không phải tìm lại chỉ để bật/tắt bản tin hay "Lưu lại").
    Không commit."""
    errors: dict[str, str] = {}
    existing = await get_config(db, org_id)
    chat_id = (chat_id or "").strip()
    if not chat_id and existing is not None:
        chat_id = existing.chat_id
    if not CHAT_RE.fullmatch(chat_id or ""):
        errors["chat_id"] = "chat_id là một dãy số — bấm Tìm chat_id sau khi Sếp đã nhắn bot"
    clean_token: str | None = None
    if token is not None and token.strip():
        if not TOKEN_RE.fullmatch(token.strip()):
            errors["token"] = "Token bot không đúng dạng — chép nguyên dòng BotFather gửi (dạng 123456789:AA…)"
        else:
            clean_token = token.strip()
    if existing is None and clean_token is None and "token" not in errors:
        errors["token"] = "Dán token bot lấy từ BotFather"
    if errors:
        raise field_errors(errors)
    bot_username = existing.bot_username if existing is not None else None
    token_enc = bytes(existing.token_enc) if existing is not None else b""
    if clean_token is not None:
        try:
            me = await client.get_me(clean_token)
        except tg.TelegramError as e:
            code = e.code if e.code in (tg.TOKEN_REJECTED, tg.RATE_LIMITED) else tg.UNREACHABLE
            raise ApiError(409, code, MESSAGES[code]) from None
        uname = me.get("username")
        bot_username = uname[:64] if isinstance(uname, str) and uname else None
        token_enc = crypto.encrypt(clean_token.encode(), TOKEN_AAD)

    # v0.1.64: đổi sang BOT khác ⇒ ghép cũ không còn nghĩa (bot mới là ranh giới tin cậy mới, dãy update_id khác):
    # gỡ ghép, tắt "Nhận tin từ Sếp", về offset 0 — Sếp ghép lại bằng mã 6 số.
    token_replaced = existing is not None and clean_token is not None and not _same_token(existing, clean_token)

    # Token hoặc chat_id thật sự đổi (hoặc nối lần đầu) ⇒ kết quả Gửi thử cũ không còn nói gì về cấu hình mới:
    # xoá để thẻ Telegram và dòng 6 "Việc Sếp cần làm" cùng về "Chưa kiểm" (Sếp phải Gửi thử lại).
    if existing is None or chat_id != existing.chat_id or (clean_token is not None
                                                            and not _same_token(existing, clean_token)):
        await forget_tests(db, org_id)

    def pick(v: bool | None, old: str) -> bool:
        return bool(v) if v is not None else (bool(getattr(existing, old)) if existing is not None else True)

    await db.execute(text("""
        INSERT INTO ops.notify_channels (org_id, kind, token_enc, chat_id, bot_username, enabled, briefing, reminders,
                                         updated_at, updated_by)
        VALUES (:o, 'telegram', :t, :c, :b, :e, :br, :r, now(), :u)
        ON CONFLICT (org_id) DO UPDATE SET token_enc = EXCLUDED.token_enc, chat_id = EXCLUDED.chat_id,
               bot_username = EXCLUDED.bot_username, enabled = EXCLUDED.enabled, briefing = EXCLUDED.briefing,
               reminders = EXCLUDED.reminders, updated_at = now(), updated_by = EXCLUDED.updated_by"""),
        {"o": org_id, "t": token_enc, "c": chat_id, "b": bot_username, "e": pick(enabled, "enabled"),
         "br": pick(briefing, "briefing"), "r": pick(reminders, "reminders"), "u": user_id})
    if token_replaced:
        from gh.telegram import inbound

        await inbound.unpair(db, org_id)
        await db.execute(text("UPDATE ops.notify_channels SET inbound_offset = 0 WHERE org_id = :o"), {"o": org_id})
    return await get_config(db, org_id)


async def forget_tests(db: AsyncSession, org_id: uuid.UUID) -> None:
    """Xoá các bản kiểm 'telegram' (Gửi thử) của tổ chức — chúng thuộc cấu hình cũ. Không commit."""
    await db.execute(text("DELETE FROM ops.boss_checks WHERE org_id = :o AND check_key = :k"),
                     {"o": org_id, "k": CHECK_KEY})


async def delete_config(db: AsyncSession, org_id: uuid.UUID, *, redis: Any = None) -> bool:
    """Xoá cấu hình + tin đang chờ gửi + kết quả Gửi thử cũ của tổ chức, đóng sự cố telegram.failed (Sếp tắt Telegram
    là đã xử lý xong cảnh báo — không còn đường nào gửi được để tự đóng). Có `redis` thì chuông đang mở đổi ngay. Không
    commit."""
    from gh import health

    res = await db.execute(text("DELETE FROM ops.notify_channels WHERE org_id = :o"), {"o": org_id})
    await db.execute(text("""DELETE FROM ops.telegram_outbox WHERE org_id = :o AND sent_at IS NULL"""), {"o": org_id})
    await forget_tests(db, org_id)
    await health.clear(db, org_id, ALERT_KEY, redis=redis)
    return bool(getattr(res, "rowcount", 0))


# ─── tệp run/telegram.json cho Trực canh máy chủ ─────────────────────────────

def host_dir() -> Path:
    from gh.system_api import update

    return update._dir()


def write_json_atomic(path: Path, data: dict[str, Any], mode: int = 0o644) -> None:
    """Ghi nguyên tử vào hộp thư (gh.hostlink_io.write_request: mkstemp O_EXCL → fchmod → fsync → os.replace) —
    genh không bao giờ đọc tệp viết dở, không ai cài sẵn symlink ở tên tạm được."""
    write_request(path.parent, path.name, data, mode=mode)


def host_payload(row: Any) -> dict[str, Any]:
    now = _iso(datetime.now(UTC))
    if row is None or not row.enabled:
        return {"schema": 1, "enabled": False, "updated_at": now}
    plain = json.dumps({"token": decrypt_token(row), "chat_id": row.chat_id}, separators=(",", ":"))
    enc = base64.b64encode(crypto.encrypt(plain.encode(), NOTIFY_AAD)).decode()
    return {"schema": 1, "enabled": True, "enc": enc, "briefing": bool(row.briefing),
            "reminders": bool(row.reminders), "updated_at": _iso(row.updated_at) or now}


async def sync_host_file(sm: Any) -> bool:
    """Ghi run/telegram.json theo cấu hình của tổ chức ĐẦU TIÊN có cấu hình (ORDER BY created_at). Thư mục hộp thư
    không có (bản phát triển) ⇒ bỏ qua êm. Không bao giờ ném — trả True khi đã ghi."""
    try:
        d = host_dir()
        if not d.is_dir():
            return False
        async with sm() as db:
            row = (await db.execute(text("""
                SELECT n.org_id, n.token_enc, n.chat_id, n.bot_username, n.enabled, n.briefing, n.reminders,
                       n.updated_at
                FROM ops.notify_channels n JOIN core.organizations o ON o.id = n.org_id
                ORDER BY o.created_at LIMIT 1"""))).one_or_none()
            await db.rollback()
        try:
            payload = host_payload(row)
        except Exception:  # noqa: BLE001 — token không giải được (khoá đổi ngoài luồng nhập gói) ⇒ tắt cho genh
            log.warning("Không giải mã được token Telegram để đồng bộ cho trực canh máy chủ")
            payload = host_payload(None)
        write_json_atomic(d / HOST_FILE, payload)
        return True
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — đồng bộ cho genh là phụ, không làm hỏng lưu/khởi động
        log.warning("Không ghi được run/%s: %s", HOST_FILE, type(e).__name__)
        return False


# ─── Gửi thử + yêu cầu genh gửi tin thử thứ hai ─────────────────────────────

def test_text() -> str:
    at = datetime.now(UTC).astimezone(VN_TZ).strftime("%H:%M %d/%m")
    return (f"Gen-Harness · Tin thử từ Console lúc {at}. Sếp nhận được tin này nghĩa là kênh Báo động & bản tin "
            f"đã thông.\n{console_line('/connections#telegram')}\n{ONE_WAY}")


def request_host_test() -> bool:
    """Ghi run/request/watchdog.json để genh (Trực canh máy chủ) gửi thêm một tin thử từ máy chủ. Chỉ khi genh trên
    máy chủ có nhận yêu cầu 'watchdog' (genh.json). Không ném."""
    from gh.system_api import update

    try:
        d = host_dir()
        info = update._read_json(d / "genh.json") or {}
        reqs = info.get("requests")
        if not (isinstance(reqs, list) and "watchdog" in reqs):
            return False
        req_dir = d / "request"
        if not req_dir.is_dir() or not os.access(req_dir, os.W_OK):
            return False
        write_json_atomic(req_dir / "watchdog.json",
                          {"schema": 1, "action": "test", "requested_at": _iso(datetime.now(UTC))})
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Không ghi được yêu cầu gửi thử cho trực canh máy chủ: %s", type(e).__name__)
        return False


def _transient(code: str, detail: dict[str, Any]) -> dict[str, Any]:
    return {"id": None, "key": CHECK_KEY, "status": "fail", "error_code": code, "message": MESSAGES[code],
            "detail": detail, "checked_at": _iso(datetime.now(UTC)), "runs": 0, "transient": True}


async def run_test(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID | None, *,
                   client: tg.TelegramClient, redis: Any = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Gửi tin thử bằng cấu hình đã lưu; ghi boss_checks 'telegram' (pass/fail — 429 là lỗi TẠM, không ghi).
    Trả (kết quả khuôn BossCheck, chi tiết cho Action Log — không bí mật). Không commit."""
    from gh import health
    from gh.boss_checks import service as boss

    row = await get_config(db, org_id)
    if row is None:
        rec = await boss.record(db, org_id, CHECK_KEY, "fail", error_code=NOT_CONFIGURED,
                                message=MESSAGES[NOT_CONFIGURED], user_id=user_id)
        return rec, {"result": "fail", "error_code": NOT_CONFIGURED}
    detail = {"bot_username": row.bot_username, "chat_masked": mask_chat(row.chat_id)}
    code: str | None = None
    try:
        await client.send_message(decrypt_token(row), row.chat_id, test_text())
    except tg.TelegramError as e:
        code = e.code
    log_detail = {**detail, "result": "pass" if code is None else "fail", "error_code": code}
    from gh.boss_checks.routes import TRANSIENT_CODES  # lỗi TẠM (429) — không ghi thành bản kiểm

    if code in TRANSIENT_CODES:
        assert code is not None
        return _transient(code, detail), log_detail
    if code is None:
        await health.clear(db, org_id, ALERT_KEY, redis=redis)
        rec = await boss.record(db, org_id, CHECK_KEY, "pass", detail=detail, user_id=user_id)
    else:
        rec = await boss.record(db, org_id, CHECK_KEY, "fail", error_code=code, message=MESSAGES.get(code),
                                detail=detail, user_id=user_id)
    return rec, log_detail


# ─── trạng thái Trực canh máy chủ (run/watchdog-status.json — genh ghi, KHÔNG tin cậy) ────────────────────────

HOST_STATES = ("ok", "issues", "paused", "skipped_busy", "error")
HOST_TELEGRAM = ("ok", "not_configured", "disabled", "failed", "key_mismatch")
HOST_SCHEDULES = ("systemd", "cron", "launchd", "schtasks")
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_-]{0,47}$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,95}$")


def _s(v: Any, limit: int = 200) -> str | None:
    return v[:limit] if isinstance(v, str) and v else None


def _ts(v: Any) -> str | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _iso(t if t.tzinfo else t.replace(tzinfo=UTC))


def host_status() -> dict[str, Any]:
    """Khối `host` của GET /notify/telegram. Mọi giá trị từ run/ (0777) chỉ nhận đúng kiểu/tập cho phép."""
    from gh.system_api import update

    out: dict[str, Any] = {"supported": False, "schedule": None, "last_run_at": None, "state": None,
                           "telegram": None, "telegram_error_code": None, "incidents": [], "test": None}
    try:
        d = host_dir()
        if not d.is_dir():
            return out
        info = update._read_json(d / "genh.json") or {}
        reqs = info.get("requests")
        out["supported"] = isinstance(reqs, list) and "watchdog" in reqs
        st = update._read_json(d / "watchdog-status.json") or {}
    except Exception:  # noqa: BLE001
        return out
    out["schedule"] = st.get("schedule") if st.get("schedule") in HOST_SCHEDULES else None
    out["last_run_at"] = _ts(st.get("last_run_at"))
    out["state"] = st.get("state") if st.get("state") in HOST_STATES else None
    out["telegram"] = st.get("telegram") if st.get("telegram") in HOST_TELEGRAM else None
    code = st.get("telegram_error_code")
    out["telegram_error_code"] = code if isinstance(code, str) and _CODE_RE.fullmatch(code) else None
    incidents = st.get("incidents")
    for inc in (incidents if isinstance(incidents, list) else [])[:20]:
        if not isinstance(inc, dict):
            continue
        key = inc.get("key")
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            continue
        out["incidents"].append({"key": key, "severity": inc.get("severity") if inc.get("severity") in ("bad", "warn")
                                 else "warn", "title": _s(inc.get("title")) or key, "since": _ts(inc.get("since"))})
    test = st.get("test")
    if isinstance(test, dict) and isinstance(test.get("ok"), bool):
        tcode = test.get("error_code")
        out["test"] = {"at": _ts(test.get("at")), "ok": test["ok"],
                       "error_code": tcode if isinstance(tcode, str) and _CODE_RE.fullmatch(tcode) else None}
    return out


async def last_test(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any] | None:
    from gh.boss_checks import service as boss

    r = (await boss.latest(db, org_id)).get(CHECK_KEY)
    if r is None:
        return None
    return {"status": r["status"], "error_code": r["error_code"], "message": r["message"],
            "checked_at": r["checked_at"]}


# ─── hộp thư đi: bản tin + nhắc việc ────────────────────────────────────────

async def enqueue(db: AsyncSession, org_id: uuid.UUID, kind: str, body: str, dedupe_key: str) -> bool:
    """Xếp một tin vào hộp thư đi trong transaction CỦA BÊN GỌI (không commit). Chỉ khi tổ chức đã cấu hình, đang
    bật và bật đúng loại (briefing/reminders). Trùng `dedupe_key` ⇒ bỏ qua. Trả True khi đã thêm dòng."""
    if kind not in KINDS:
        raise ValueError(f"kind lạ: {kind}")
    flag = "briefing" if kind == "briefing" else "reminders"
    row = (await db.execute(text(f"""
        INSERT INTO ops.telegram_outbox (org_id, kind, dedupe_key, text)
        SELECT n.org_id, :k, :d, :t FROM ops.notify_channels n
        WHERE n.org_id = :o AND n.enabled AND n.{flag}
        ON CONFLICT (org_id, dedupe_key) DO NOTHING RETURNING id"""),  # noqa: S608 — flag từ tập cố định
        {"o": org_id, "k": kind, "d": dedupe_key, "t": body[:tg.TEXT_MAX]})).scalar_one_or_none()
    return row is not None


def briefing_text(slot_label: str, summary: str | None, sections: list[dict[str, Any]]) -> str:
    lines = [f"Bản tin Gen · {slot_label}"]
    if summary:
        lines += ["", defang(summary.strip())]
    # Mục chưa đọc được (lỗi / Gen-hub tạm không trả lời) luôn có dòng ⇒ không có dòng nào = mọi mục đã đọc và đều 0.
    items = _briefing_items(sections)
    lines += ["", *items] if items else ["", NOTHING]
    lines += ["", console_line("/overview"), ONE_WAY]
    return "\n".join(lines)


def reminder_text(title: str, code: str, priority: str, due: str | None) -> str:
    meta = f"{code} · {priority}" + (f" · hạn {due}" if due else "")
    return f"Nhắc việc: {defang(_line(title, 300))}\n{meta}\n{console_line('/tasks')}\n{ONE_WAY}"


async def _org_token(db: AsyncSession, org_id: uuid.UUID) -> tuple[str, str] | None:
    row = await get_config(db, org_id)
    if row is None or not row.enabled:
        return None
    return decrypt_token(row), row.chat_id


async def flush_outbox(sm: Any, *, transport: Any = None, redis: Any = None) -> dict[str, int]:
    """Gửi ≤ `FLUSH_BATCH` tin đang chờ (tạo trong 24 giờ, thử < 5 lần, tới giờ thử). Mỗi tin: khoá riêng dòng đó
    (FOR UPDATE SKIP LOCKED — hai worker không gửi trùng), gửi, ghi kết quả rồi COMMIT ngay — lỗi/huỷ giữa lượt không
    xoá `sent_at` của tin đã tới tay Sếp (không gửi trùng lượt sau). Thành công ⇒ `sent_at` (+ đóng sự cố
    telegram.failed); 429 ⇒ lùi `retry_after` giây; mạng ⇒ attempts+1, lùi 2^attempts phút; lỗi cấu hình ⇒
    `failed_code` + sự cố telegram.failed (chuông Owner một lần). Dọn tin quá 7 ngày. Không ném vì một tin lỗi."""
    from gh import health

    client = tg.client_for(transport)
    stats = {"sent": 0, "retry": 0, "failed": 0}
    started = time.monotonic()
    async with sm() as db:
        rows = (await db.execute(text("""
            SELECT id, org_id, text, attempts FROM ops.telegram_outbox
            WHERE sent_at IS NULL AND failed_code IS NULL AND created_at > now() - make_interval(secs => :w)
              AND attempts < :a AND next_attempt_at <= now()
            ORDER BY created_at LIMIT :n"""),
            {"w": FLUSH_WINDOW.total_seconds(), "a": FLUSH_MAX_ATTEMPTS, "n": FLUSH_BATCH})).all()
        await db.commit()
        creds: dict[uuid.UUID, tuple[str, str] | None] = {}
        blocked: dict[uuid.UUID, str] = {}   # tổ chức đã gặp lỗi cấu hình/429 trong lượt này ⇒ không gửi tiếp
        delivered: set[uuid.UUID] = set()

        async def side_effect(what: str, fn: Any) -> None:
            """Sự cố/chuông sau khi đã COMMIT kết quả gửi — lỗi ở đây không được làm mất `sent_at`."""
            try:
                await fn()
                await db.commit()
            except Exception:  # noqa: BLE001
                await db.rollback()
                log.warning("Telegram: %s thất bại", what, exc_info=True)

        for r in rows:
            if r.org_id not in creds:
                try:
                    creds[r.org_id] = await _org_token(db, r.org_id)
                except Exception:  # noqa: BLE001 — token không giải được
                    log.warning("Không giải mã được token Telegram của tổ chức %s", r.org_id)
                    creds[r.org_id] = None
            cred = creds[r.org_id]
            if cred is None:
                await db.execute(text("""UPDATE ops.telegram_outbox SET failed_code = :c
                                         WHERE id = :i AND sent_at IS NULL"""), {"c": NOT_CONFIGURED, "i": r.id})
                await db.commit()
                stats["failed"] += 1
                continue
            if r.org_id in blocked or time.monotonic() - started > FLUSH_BUDGET_S:
                continue  # để lượt sau
            mine = (await db.execute(text("""SELECT id FROM ops.telegram_outbox
                                             WHERE id = :i AND sent_at IS NULL AND failed_code IS NULL
                                             FOR UPDATE SKIP LOCKED"""), {"i": r.id})).first()
            if mine is None:  # worker khác đang gửi / đã gửi tin này
                await db.rollback()
                continue
            try:
                await client.send_message(cred[0], cred[1], r.text)
            except tg.TelegramError as e:
                fatal = False
                if e.code == tg.RATE_LIMITED:
                    await db.execute(text("""UPDATE ops.telegram_outbox
                                             SET next_attempt_at = now() + make_interval(secs => :s) WHERE id = :i"""),
                                     {"s": int(e.retry_after or 30), "i": r.id})
                    blocked[r.org_id] = e.code
                    stats["retry"] += 1
                elif e.code in FATAL_CODES or e.code == tg.BAD_REQUEST:
                    await db.execute(text("UPDATE ops.telegram_outbox SET failed_code = :c WHERE id = :i"),
                                     {"c": e.code, "i": r.id})
                    stats["failed"] += 1
                    if e.code in FATAL_CODES:
                        blocked[r.org_id] = e.code
                        fatal = True
                else:
                    n = int(r.attempts) + 1
                    await db.execute(text("""UPDATE ops.telegram_outbox SET attempts = :n,
                                                    next_attempt_at = now() + make_interval(mins => :m)
                                             WHERE id = :i"""), {"n": n, "m": 2 ** n, "i": r.id})
                    stats["retry"] += 1
                await db.commit()
                if fatal:
                    code, org = e.code, r.org_id
                    await side_effect("ghi sự cố telegram.failed", lambda code=code, org=org: health.raise_once(
                        db, org, key=ALERT_KEY, kind=ALERT_KEY, severity="warn", title=ALERT_TITLE,
                        body=ALERT_BODIES[code], link=ALERT_LINK, fingerprint=code, redis=redis))
                continue
            await db.execute(text("UPDATE ops.telegram_outbox SET sent_at = now() WHERE id = :i"), {"i": r.id})
            await db.commit()
            delivered.add(r.org_id)
            stats["sent"] += 1
        for org in delivered:
            await side_effect("đóng sự cố telegram.failed",
                              lambda org=org: health.clear(db, org, ALERT_KEY, redis=redis))
        await db.execute(text("""DELETE FROM ops.telegram_outbox
                                 WHERE (sent_at IS NOT NULL AND sent_at < now() - make_interval(secs => :k))
                                    OR created_at < now() - make_interval(secs => :k)"""),
                         {"k": KEEP_SENT.total_seconds()})
        await db.commit()
    return stats
