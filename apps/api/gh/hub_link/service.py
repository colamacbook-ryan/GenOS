"""Liên kết Gen-hub — Gen ĐỌC Kho dữ liệu (v0.1.26), lịch/mail/việc/Drive Google (v0.1.49, QD-16)
(docs/design/gen-hub-link.md §3, §6).

- Liên kết (`agent.hub_links`, một dòng / tổ chức) trỏ tới một máy chủ trong MCP Hub sẵn có (`agent.mcp_servers`);
  token agent của Gen-hub mã hoá phong bì trong `mcp_servers.auth_enc` (gh.crypto) — không lưu rõ, không log, không
  trả qua API. Mọi lời gọi ra ngoài đi qua `gh.mcp_api.invoke.invoke_tool` (không có đường gọi MCP thứ hai).
- Chỉ-đọc, 2 lớp: Gen-hub chỉ grant tool đọc; ở đây chỉ gọi tool có hậu tố thuộc `READ_SUFFIXES` = Kho + Google (so
  hậu tố vì Gen-hub đặt tiền tố theo connector, vd `mcp-58450__kho_tom_tat`) — tool lạ bị bỏ qua dù Owner lỡ mở.
  Quy tắc THẬT là danh sách cho phép; `WRITE_SUFFIXES_DENY` chỉ để ghi lý do/kiểm thử. Tool ghi (gửi mail, tạo lịch,
  ghi Drive…) bị từ chối cả ở route MCP chung, kể cả Owner (`generic_call`).
- Nội dung Kho/lịch/mail/việc/Drive sang model đám mây (quyết định Boss #2, QD-16) → luôn qua `mask_for_model` (gen-v1
  §9.2: cùng lớp che `mask_text` như vai trò dưới Owner + che email, khoá/token; id kỹ thuật giữ nguyên qua
  `HUB_KEEP_KEYS`) TRƯỚC khi trả về, lưu đệm hay ghi `mcp_calls` (chỉ siêu dữ liệu).
- Đệm Redis 5 phút theo org + tool + tham số (chỉ bản đã che); đổi địa chỉ/token → xoá đệm.
- v0.1.49 (F-83): ngắt mạch riêng của Gen-hub (3 lỗi mạng/timeout/5xx/429 liên tiếp → mở 60 giây, nửa mở lỗi 1 lần →
  mở lại; 401/403 không tính), khoá Redis `gh:hub:brk:*:{org}`, đồng hồ tiêm được `_clock`; mở quá 15 phút ⇒ sự cố
  `hub.breaker` + chuông Owner (`breaker_watch`, worker mỗi 5 phút). `briefing_read` là hàm đọc cho Bản tin (không ném).
- v0.1.50 (F-81, QD-18): MỘT đường GHI duy nhất — `write_kho` (kho_create / kho_update, chỉ bảng Phiên và Việc): Owner +
  PIN `hub.write` + permit ký 5 phút gắn đúng đề xuất + tham số (gh.hub_link.permit) + kiểm lại tham số (kho_write) →
  `invoke_tool(approved_write=True)`. `suffix_of` / `call_hub` / `generic_call` GIỮ chỉ-đọc: route MCP chung gọi
  kho_create vẫn 403 `HUB_TOOL_NOT_ALLOWED`. Kiểm tra (`test_link`) mở + cấp `core.gen` cho kho_create/kho_update nhưng
  KHÔNG BAO GIỜ gọi chúng; `write_scopes` cho thẻ Gen-hub và Gen biết đã có quyền ghi chưa.
"""

import asyncio
import hashlib
import ipaddress
import logging
import math
import re
import socket
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import health, notifications
from gh.auth import rbac, service
from gh.boss_checks import service as boss_checks
from gh.chassis import actionlog
from gh.chassis.masking import _RE_SECRET, MASK, mask_for_model
from gh.chassis.mcp_client import McpClient, always_forbidden, forbidden_host
from gh.data.common import iso
from gh.errors import ApiError, conflict
from gh.hub_link import KHO_LABEL, KHO_LABEL_MAX, kho_write, load_kho_label, relabel
from gh.hub_link import permit as hub_permit
from gh.mcp_api import invoke

log = logging.getLogger("gh.hub_link")

AGENT_KEY = "core.gen"
SERVER_NAME = "Gen-hub"
# v0.1.56: ghi chú máy chủ lưu vào DB của Owner — tên Kho chung; ghi chú cũ (tên riêng) do migration 0035 cập nhật.
# v0.1.69 (F124): Gen đọc tự do nhưng GHI Kho (Phiên, Việc) được — chỉ khi Sếp Xác nhận + mã PIN (QD-18); migration 0041
# cập nhật ghi chú cũ "(chỉ đọc)" đã lưu trong DB.
SERVER_NOTE = (f"Liên kết Gen-hub — Gen đọc {KHO_LABEL}, lịch, mail, việc, Drive "
               "(đọc; ghi Kho chỉ khi Sếp Xác nhận + mã PIN). Quản lý ở thẻ Gen-hub.")


def server_note(label: str = KHO_LABEL) -> str:
    """Ghi chú máy chủ theo tên Kho hiệu lực của tổ chức (v0.1.57); mặc định = `SERVER_NOTE`."""
    return relabel(SERVER_NOTE, label)
# Danh sách cho phép cố định trong code (không cấu hình được): chỉ tool ĐỌC — Kho + Google (QD-16, v0.1.49).
KHO_READ_SUFFIXES = ("kho_tom_tat", "kho_search", "kho_get", "kho_find_by_id", "kho_list")
GOOGLE_READ_SUFFIXES = ("calendar_list_events", "tasks_list", "gmail_search", "gmail_read_message", "drive_search")
READ_SUFFIXES = KHO_READ_SUFFIXES + GOOGLE_READ_SUFFIXES
# Chỉ để ghi lý do + kiểm thử: các tool GHI không bao giờ đi qua đường ĐỌC / route MCP chung (call_hub, generic_call).
# Quy tắc thật vẫn là danh sách cho phép ở trên. Riêng kho_create/kho_update có đúng MỘT đường ghi: `write_kho`.
WRITE_SUFFIXES_DENY = ("gmail_send", "gmail_create_draft", "calendar_create_event", "drive_create_file",
                       "drive_share_file", "tasks_create", "docs_edit", "sheets_write", "slides_add_slide",
                       "kho_create", "kho_update")
# v0.1.50 (F-81): hai tool ghi Kho duy nhất được gọi — chỉ qua `write_kho` (Xác nhận + PIN + permit).
KHO_WRITE_SUFFIXES = ("kho_create", "kho_update")
REQUIRED_SUFFIXES = ("kho_tom_tat", "kho_search", "kho_find_by_id")
# Quyền đọc thêm (không bắt buộc): mỗi quyền = mọi hậu tố của nó đều có tool đọc, đã mở, đã cấp cho `core.gen`.
READ_SCOPES: dict[str, tuple[str, ...]] = {
    "calendar": ("calendar_list_events",),
    "mail": ("gmail_search", "gmail_read_message"),
    "tasks": ("tasks_list",),
    "drive": ("drive_search",),
}
SCOPE_LABELS = {"calendar": "đọc lịch", "mail": "đọc mail", "tasks": "đọc việc (Google Tasks)",
                "drive": "tìm tệp Drive"}
# Khoá mà giá trị chuỗi là id kỹ thuật (id Gmail/sự kiện có thể chứa ≥ 8 chữ số liền — regex số dài sẽ phá id).
# KHÔNG có khoá chung chung như `code`: mã đặt chỗ / OTP / SĐT viết liền dưới `code` phải bị che như thường.
HUB_KEEP_KEYS = frozenset({"id", "messageId", "threadId", "eventId", "taskId", "fileId", "tasklistId"})
# Nguồn hiển thị + danh từ trong câu lỗi, theo tiền tố hậu tố tool.
_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("kho_", f"{KHO_LABEL} qua Gen-hub", "Kho"),
    ("calendar_", "Lịch Google qua Gen-hub", "lịch"),
    ("gmail_", "Gmail qua Gen-hub", "mail"),
    ("tasks_", "Google Tasks qua Gen-hub", "việc"),
    ("drive_", "Google Drive qua Gen-hub", "Drive"),
)
RECORD_RE = re.compile(r"^[A-Z]{2,6}-\d{1,6}$")
GMAIL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
MAIL_TEXT_MAX = 4000
CACHE_TTL_S = 300
EXPIRY_WARN_DAYS = 14
CALL_TIMEOUT_S = 10.0
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


# ─── che dữ liệu trước khi sang model (gen-v1 §9.2) ───────────────────────────
# Lớp che chuyển sang `gh.chassis.masking` (v0.1.45, F-57); `mask_for_model`/`MASK` vẫn re-export ở đây.


def endpoint_forbidden(endpoint: str) -> bool:
    """Chống SSRF tới dịch vụ siêu dữ liệu đám mây / địa chỉ đặc biệt / dịch vụ nội bộ của Gen-Harness: tên dịch vụ
    compose (`db`, `redis`, `gen-harness-api-1`…) hay host phân giải ra link-local (169.254.x, fe80::), unspecified
    (0.0.0.0) hoặc multicast → cấm, bất kể công tắc mạng công cộng. Dùng chung `forbidden_host` + `always_forbidden`
    với MCP Hub (v0.1.45). LAN/loopback vẫn theo guard MCP Hub sẵn có (Gen-hub có thể chạy cùng mạng nội bộ)."""
    host = urlparse(endpoint).hostname
    if not host:
        return True
    if forbidden_host(host):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False  # không phân giải được → guard mạng MCP Hub xử lý (coi là công cộng → chặn khi công tắc tắt)
    for info in infos:
        try:
            ip = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        except ValueError:
            continue
        if always_forbidden(ip):
            return True
    return False


ENDPOINT_FORBIDDEN_MSG = "Địa chỉ Gen-hub trỏ tới vùng mạng bị cấm (link-local/siêu dữ liệu đám mây)"
ENDPOINT_INVALID_MSG = "Địa chỉ Gen-hub không hợp lệ — kiểm tra lại (dạng https://<máy-chủ>/mcp)"


def _summary(suffix: str, result: Any) -> str:
    """`mcp_calls.result_summary` + sự kiện WS `mcp.call` đi tới cả vai trò `system.read` — Kho/lịch/mail/việc/Drive
    chỉ cho Owner (quyết định Boss #1, QD-16) nên KHÔNG lưu nội dung, chỉ siêu dữ liệu."""
    return f"Đọc Gen-hub ({suffix}) — {len(orjson.dumps(result))} byte, nội dung không lưu"


def source_of(suffix: str, kho: str = KHO_LABEL) -> str:
    """Nhãn nguồn theo hậu tố: kho_* "Kho dữ liệu qua Gen-hub" (`kho` = tên Kho hiệu lực của tổ chức, v0.1.57),
    calendar_* "Lịch Google qua Gen-hub", gmail_* …"""
    for prefix, label, _ in _SOURCES:
        if suffix.startswith(prefix):
            return relabel(label, kho)
    return "Gen-hub"


def _noun_of(suffix: str) -> str:
    for prefix, _, noun in _SOURCES:
        if suffix.startswith(prefix):
            return noun
    return "Gen-hub"


def scope_of(suffix: str) -> str | None:
    """Quyền đọc thêm (calendar/mail/tasks/drive) chứa hậu tố này; None với Kho và hậu tố lạ."""
    for scope, suffixes in READ_SCOPES.items():
        if suffix in suffixes:
            return scope
    return None


def mask_hub(result: Any, *, secrets: tuple[str, ...] = (), suffix: str | None = None) -> Any:
    """Che kết quả tool Gen-hub trước khi trả/đệm. Tool Google: id kỹ thuật dưới `HUB_KEEP_KEYS` giữ nguyên và
    `content[].text` là JSON được che theo cấu trúc rồi gói lại thành chuỗi (nếu che cả chuỗi, regex số dài sẽ phá id
    Gmail như `18c2f41234567890`). Kho giữ nguyên hành vi cũ."""
    if suffix is None or scope_of(suffix) is None:
        return mask_for_model(result, secrets=secrets)
    pre = result
    parsed_at: list[int] = []
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        content = list(result["content"])
        for i, c in enumerate(content):
            if isinstance(c, dict) and isinstance(c.get("text"), str):
                obj = _json_or_none(c["text"])
                if obj is not None:
                    content[i] = {**c, "text": obj}
                    parsed_at.append(i)
        pre = {**result, "content": content}
    masked = mask_for_model(pre, secrets=secrets, keep_keys=HUB_KEEP_KEYS)
    for i in parsed_at:
        masked["content"][i]["text"] = orjson.dumps(masked["content"][i]["text"]).decode()
    return masked


def _json_or_none(raw: str) -> Any:
    t = raw.strip()
    if not t or t[0] not in "[{":
        return None
    try:
        return orjson.loads(t)
    except orjson.JSONDecodeError:
        return None


def truncate_text(data: Any, limit: int = 4000) -> Any:
    """Cắt mọi chuỗi dài hơn `limit` (gọi SAU khi che) — thân mail dài không tràn ngữ cảnh model."""
    if isinstance(data, str):
        return data if len(data) <= limit else data[: limit - 1] + "…"
    if isinstance(data, list):
        return [truncate_text(v, limit) for v in data]
    if isinstance(data, dict):
        return {k: truncate_text(v, limit) for k, v in data.items()}
    return data


def vn_day_bounds(day: date) -> dict[str, str]:
    """Đầu/cuối ngày giờ VN (ISO, +07:00) cho `calendar_list_events`."""
    return {"timeMin": datetime.combine(day, dtime(0, 0, 0), tzinfo=VN_TZ).isoformat(),
            "timeMax": datetime.combine(day, dtime(23, 59, 59), tzinfo=VN_TZ).isoformat()}


def vn_today(now: datetime | None = None) -> date:
    """Ngày hiện tại theo giờ VN; `now` naive coi là giờ VN."""
    now = now or datetime.now(UTC)
    return (now.astimezone(VN_TZ) if now.tzinfo else now.replace(tzinfo=VN_TZ)).date()


def scrub(message: str, token: str | None) -> str:
    """Lỗi từ máy chủ ngoài có thể lặp lại header — không bao giờ để token lọt vào last_error / phản hồi / log."""
    msg = message.replace(token, MASK) if token else message
    return _RE_SECRET.sub(MASK, msg)[:300]


# ─── liên kết ─────────────────────────────────────────────────────────────────

LINK_SELECT = """
SELECT l.org_id, l.server_id, l.enabled, l.token_expires_at, l.expiry_notified_at, l.last_ok_at, l.last_error,
       l.updated_at, s.endpoint, s.allow_public_network, s.is_enabled AS server_enabled, s.health,
       (s.auth_enc IS NOT NULL) AS has_token
FROM agent.hub_links l LEFT JOIN agent.mcp_servers s ON s.id = l.server_id
WHERE l.org_id = :o
"""


async def load(db: AsyncSession, org_id: uuid.UUID) -> Any:
    return (await db.execute(text(LINK_SELECT), {"o": org_id})).one_or_none()


def status_of(r: Any, now: datetime | None = None) -> str:
    """off (chưa cấu hình / chưa kiểm tra / đã tắt) · ok · expiring (≤ 14 ngày) · expired · error."""
    now = now or datetime.now(UTC)
    if r is None or r.server_id is None:
        return "off"
    if r.last_error and r.last_error.startswith(("401", "403")):
        return "expired"
    if r.token_expires_at is not None and r.token_expires_at <= now:
        return "expired"
    if not r.enabled:
        return "off"
    if r.last_error:
        return "error"
    if r.token_expires_at is not None and r.token_expires_at <= now + timedelta(days=EXPIRY_WARN_DAYS):
        return "expiring"
    return "ok"


LAST_ERROR_HIDDEN = "Gen-hub đang lỗi — Owner xem chi tiết ở thẻ Gen-hub"


def link_out(r: Any, *, owner: bool = True) -> dict[str, Any]:
    """Không bao giờ có token — chỉ `has_token`. `last_error` (lỗi thô từ Gen-hub, đã lọc token) chỉ Owner thấy;
    vai trò `system.read` khác (Kiểm toán) nhận một câu chung (v0.1.27)."""
    if r is None:
        return {"configured": False, "enabled": False, "status": "off", "server_id": None, "endpoint": None,
                "has_token": False, "allow_public_network": False, "token_expires_at": None, "days_left": None,
                "last_ok_at": None, "last_error": None, "health": None}
    days_left = None
    if r.token_expires_at is not None:
        days_left = max(0, (r.token_expires_at - datetime.now(UTC)).days)
    return {"configured": r.server_id is not None, "enabled": bool(r.enabled and r.server_id is not None),
            "status": status_of(r), "server_id": str(r.server_id) if r.server_id else None, "endpoint": r.endpoint,
            "has_token": bool(r.has_token), "allow_public_network": bool(r.allow_public_network),
            "token_expires_at": iso(r.token_expires_at), "days_left": days_left, "last_ok_at": iso(r.last_ok_at),
            "last_error": r.last_error if owner else (LAST_ERROR_HIDDEN if r.last_error else None),
            "health": r.health}


def cache_prefix(org_id: uuid.UUID) -> str:
    return f"gh:hub:kho:{org_id}:"


def cache_key(org_id: uuid.UUID, suffix: str, args: dict[str, Any]) -> str:
    digest = hashlib.sha256(orjson.dumps({"t": suffix, "a": args}, option=orjson.OPT_SORT_KEYS)).hexdigest()[:24]
    return cache_prefix(org_id) + digest


async def clear_cache(redis: Any, org_id: uuid.UUID) -> None:
    if redis is None:
        return
    keys = [k async for k in redis.scan_iter(match=cache_prefix(org_id) + "*", count=200)]
    if keys:
        await redis.delete(*keys)


def suffix_of(tool_name: str) -> str | None:
    """Hậu tố cho phép của một tool Gen-hub (`kho_tom_tat` hoặc `<connector>__kho_tom_tat`, `mcp-46634__gmail_search`),
    None nếu tool lạ — gồm mọi tool GHI (gmail_send, calendar_create_event…): không bao giờ gọi."""
    base = tool_name.rsplit("__", 1)[-1]
    return base if base in READ_SUFFIXES else None


def write_suffix_of(tool_name: str) -> str | None:
    """Hậu tố của tool GHI Kho cho phép (`kho_create` hoặc `<connector>__kho_update`), None nếu không phải. KHÔNG dùng
    để quyết định gọi tool — chỉ `write_kho` (sau Xác nhận + PIN + permit) được gọi các tool này."""
    base = tool_name.rsplit("__", 1)[-1]
    return base if base in KHO_WRITE_SUFFIXES else None


async def read_scopes(db: AsyncSession, org_id: uuid.UUID) -> dict[str, bool]:
    """Quyền đọc thêm đang có: True khi MỌI hậu tố của quyền có tool trên máy chủ liên kết, `access='read'`, đã mở
    (`is_exposed`) và đã cấp cho `core.gen`. Chưa nối ⇒ tất cả False."""
    names = (await db.execute(text("""
        SELECT t.name FROM agent.hub_links l
        JOIN agent.mcp_servers s ON s.id = l.server_id AND s.org_id = l.org_id
        JOIN agent.mcp_tools t ON t.server_id = s.id
        JOIN agent.mcp_grants g ON g.tool_id = t.id AND g.agent_key = :a
        WHERE l.org_id = :o AND t.access = 'read' AND t.is_exposed"""), {"o": org_id, "a": AGENT_KEY})).scalars().all()
    have = {suffix_of(n) for n in names}
    return {scope: all(suf in have for suf in sufs) for scope, sufs in READ_SCOPES.items()}


def write_scope_map(have: set[str | None]) -> dict[str, bool]:
    """`{kho_create, kho_update}` → có từng tool không, và `kho` = có CẢ HAI (giữ cho bên đọc cũ: job F-87, web cũ)."""
    each = {suf: suf in have for suf in KHO_WRITE_SUFFIXES}
    return {"kho": all(each.values()), **each}


async def write_scopes(db: AsyncSession, org_id: uuid.UUID) -> dict[str, bool]:
    """Quyền GHI Kho theo TỪNG tool (`kho_create`, `kho_update`) + `kho` (= có cả hai): tool có trên máy chủ liên kết,
    đã mở (`is_exposed`) và đã cấp cho `core.gen` (mọi `access` — Gen-hub đánh dấu chúng là tool ghi). Chưa nối ⇒
    False."""
    names = (await db.execute(text("""
        SELECT t.name FROM agent.hub_links l
        JOIN agent.mcp_servers s ON s.id = l.server_id AND s.org_id = l.org_id
        JOIN agent.mcp_tools t ON t.server_id = s.id
        JOIN agent.mcp_grants g ON g.tool_id = t.id AND g.agent_key = :a
        WHERE l.org_id = :o AND t.is_exposed"""), {"o": org_id, "a": AGENT_KEY})).scalars().all()
    return write_scope_map({write_suffix_of(n) for n in names})


async def write_hidden(db: AsyncSession, org_id: uuid.UUID) -> list[str]:
    """Hậu tố tool ghi Kho ('kho_create' / 'kho_update') của máy chủ liên kết mà Owner đã TỰ đóng (hoặc gỡ cấp
    `core.gen`) ở MCP Hub — cùng quy tắc `test_link` (Kiểm tra không mở lại). Cho `GET /hub/link`: tải lại trang vẫn
    biết tool đóng là do Sếp, không giục tick ở Gen-hub. Chưa nối ⇒ []."""
    rows = (await db.execute(text("""
        SELECT t.id, t.name FROM agent.hub_links l
        JOIN agent.mcp_servers s ON s.id = l.server_id AND s.org_id = l.org_id
        JOIN agent.mcp_tools t ON t.server_id = s.id
        WHERE l.org_id = :o ORDER BY t.name"""), {"o": org_id})).all()
    out: list[str] = []
    for r in rows:
        suf = write_suffix_of(r.name)
        if suf is not None and suf not in out and await _hidden_by_owner(db, org_id, r.id):
            out.append(suf)
    return [suf for suf in KHO_WRITE_SUFFIXES if suf in out]


def scopes_known(r: Any) -> bool:
    """`read_scopes` có nghĩa chưa: đã nối, đã có ít nhất một lần Kiểm tra xanh và liên kết đang bật (đổi địa chỉ/token
    ⇒ `upsert` tắt liên kết cho tới lần Kiểm tra xanh kế tiếp, nên quyền cũ không còn được coi là đã kiểm)."""
    return r is not None and r.server_id is not None and r.last_ok_at is not None and bool(r.enabled)


def read_missing(scopes: dict[str, bool]) -> list[str]:
    """Nhãn các quyền đọc thêm còn thiếu, theo thứ tự lịch, mail, việc, Drive."""
    return [SCOPE_LABELS[k] for k in READ_SCOPES if not scopes.get(k)]


async def find_tool(db: AsyncSession, org_id: uuid.UUID, server_id: uuid.UUID, suffix: str) -> Any:
    """Tool của máy chủ liên kết theo hậu tố — đọc (READ_SUFFIXES) hoặc ghi Kho (KHO_WRITE_SUFFIXES)."""
    assert suffix in READ_SUFFIXES or suffix in KHO_WRITE_SUFFIXES
    rows = (await db.execute(text(invoke.TOOL_SELECT + " WHERE t.server_id = :s AND s.org_id = :o ORDER BY t.name"),
                             {"s": server_id, "o": org_id})).all()
    for r in rows:
        if suffix_of(r.name) == suffix or write_suffix_of(r.name) == suffix:
            return r
    return None


async def _set_result(db: AsyncSession, org_id: uuid.UUID, *, ok: bool, error: str | None = None,
                      enable: bool = False) -> None:
    if ok:
        await db.execute(text(f"""UPDATE agent.hub_links SET last_ok_at = now(), last_error = NULL
                                  {', enabled = true' if enable else ''} WHERE org_id = :o"""),  # noqa: S608
                         {"o": org_id})
    else:
        await db.execute(text("UPDATE agent.hub_links SET last_error = :e WHERE org_id = :o"),
                         {"e": error, "o": org_id})


PUBLIC_NET_HINT = "Bật 'Cho phép Gen-hub ở mạng công cộng' ngay trong thẻ này."


def _classify(message: str, code: str | None = None) -> str:
    """Lỗi McpClient dạng "<mã HTTP>: …" / "mạng: …" → câu ngắn tiếng Việt, giữ mã đầu dòng cho `status_of`.

    v0.1.39 (F-31): guard mạng MCP Hub (`MCP_NETWORK_BLOCKED`) chặn vì địa chỉ ở mạng công cộng → chỉ đúng công tắc
    trong thẻ Gen-hub (server KHÔNG tự bật); chặn vì link-local/siêu dữ liệu → câu vùng mạng bị cấm; địa chỉ sai dạng
    (scheme lạ, cổng ngoài 0–65535) → câu "không hợp lệ" riêng (trước đây bị gán nhầm "vùng mạng bị cấm")."""
    if code == "MCP_NETWORK_BLOCKED":
        if "mạng công cộng" in message:
            return PUBLIC_NET_HINT
        if "vùng mạng bị cấm" in message:
            return ENDPOINT_FORBIDDEN_MSG
        return ENDPOINT_INVALID_MSG
    head = message.split(":", 1)[0].strip()
    if head in ("401", "403"):
        return f"{head}: Token Gen-hub hết hạn hoặc đã bị thu hồi — tạo token mới trong Gen-hub rồi dán lại"
    if head == "429":
        return "429: Gen-hub đang giới hạn lượt gọi — thử lại sau vài phút"
    if head == "mạng":
        return "Không kết nối được Gen-hub (mạng/timeout)"
    return message


def _error_code(msg: str, *, code: str | None = None, missing: bool = False) -> str:
    """Mã lỗi thống nhất cho trang "Việc Sếp cần làm" (v0.1.39) — đọc từ câu đã phân loại."""
    if msg == ENDPOINT_FORBIDDEN_MSG:
        return "HUB_ENDPOINT_FORBIDDEN"
    if msg == ENDPOINT_INVALID_MSG:
        return "HUB_ENDPOINT_INVALID"
    if msg == PUBLIC_NET_HINT or code == "MCP_NETWORK_BLOCKED":
        return "MCP_NETWORK_BLOCKED"
    if missing:
        return "HUB_TOOLS_MISSING"
    head = msg.split(":", 1)[0].strip()
    if head in ("401", "403"):
        return "HUB_TOKEN_REJECTED"
    if head == "429":
        return "HUB_RATE_LIMITED"
    if msg.startswith("Không kết nối được Gen-hub"):
        return "HUB_UNREACHABLE"
    return "HUB_ERROR"


def client_for(transport: Any) -> McpClient:
    """Mọi lời gọi Gen-hub ghim DNS (v0.1.27): phân giải một lần, kiểm IP, kết nối thẳng IP đã kiểm — đóng cửa sổ
    DNS rebinding giữa `endpoint_forbidden`/guard mạng và lúc kết nối thật."""
    return McpClient(transport=transport, timeout=CALL_TIMEOUT_S, pin_dns=True)


# ─── ngắt mạch riêng của Gen-hub (v0.1.49, F-83) ──────────────────────────────

BREAKER_FAILS = 3
BREAKER_OPEN_S = 60
BREAKER_ALERT_AFTER_S = 900
BREAKER_KEY = "hub.breaker"
BREAKER_KIND = "hub.unreachable"
_BREAKER_FAILS_TTL_S = 300
_BREAKER_OPEN_TTL_S = 3600
_BREAKER_DOWN_TTL_S = 86400
#: Đồng hồ tiêm được (epoch giây) — test monkeypatch `hub._clock`; luôn gọi qua tên module, không bind sớm.
_clock = time.time


def _bk(part: str, org_id: uuid.UUID) -> str:
    """gh:hub:brk:{fails|open_until|down_since|half}:{org}"""
    return f"gh:hub:brk:{part}:{org_id}"


def _epoch(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _counts_for_breaker(cause: Exception) -> bool:
    """Chỉ lỗi KHÔNG tới được Gen-hub mới tính: mạng/timeout, 5xx, 429/408, phản hồi không phải JSON. 401/403 (token
    — đã có trạng thái 'expired') và lỗi cấu hình/ứng dụng (4xx khác, JSON-RPC báo lỗi) thì không."""
    head = str(cause).split(":", 1)[0].strip()
    if head == "mạng" or head.startswith("phản hồi không phải JSON"):
        return True
    return head.isdigit() and (int(head) in (408, 429) or int(head) >= 500)


async def _breaker_blocked(redis: Any, org_id: uuid.UUID) -> bool:
    if redis is None:
        return False
    until = _epoch(await redis.get(_bk("open_until", org_id)))
    return until is not None and until > _clock()


async def _breaker_fail(db: AsyncSession, redis: Any, org_id: uuid.UUID) -> None:
    """Một lỗi mạng/timeout/5xx/429: ≥ 3 lỗi liên tiếp, hoặc đang nửa mở (đã từng mở) mà lỗi ⇒ mở 60 giây. Cũng kiểm
    luôn "im hơn 15 phút" (không chờ cron). Bên gọi commit."""
    if redis is None:
        return
    now = _clock()
    fails_key = _bk("fails", org_id)
    down_key = _bk("down_since", org_id)
    fails = await redis.incr(fails_key)
    await redis.expire(fails_key, _BREAKER_FAILS_TTL_S)
    half = await redis.exists(_bk("half", org_id))
    if half:
        # Ngắt mạch đã từng mở mà chưa gọi được lại ⇒ vẫn là CÙNG đợt im (giữ mốc bắt đầu; đặt lại nếu lỡ mất).
        await redis.set(down_key, repr(now), nx=True, ex=_BREAKER_DOWN_TTL_S)
    elif fails == 1:
        # Lỗi đầu của một chuỗi mới: đợt im bắt đầu TỪ BÂY GIỜ — ghi đè mốc cũ (một lỗi lẻ lúc 08:00 không được làm
        # lần mở lúc 17:00 bị coi là "im hơn 15 phút"). Hết hạn cùng bộ đếm cho tới khi ngắt mạch mở.
        await redis.set(down_key, repr(now), ex=_BREAKER_FAILS_TTL_S)
    else:
        await redis.set(down_key, repr(now), nx=True, ex=_BREAKER_FAILS_TTL_S)
        await redis.expire(down_key, _BREAKER_FAILS_TTL_S)
    if fails >= BREAKER_FAILS or half:
        await redis.set(_bk("open_until", org_id), repr(now + BREAKER_OPEN_S), ex=_BREAKER_OPEN_TTL_S)
        await redis.set(_bk("half", org_id), "1", ex=_BREAKER_DOWN_TTL_S)
        await redis.expire(down_key, _BREAKER_DOWN_TTL_S)  # đã mở ⇒ giữ mốc tới khi gọi được lại (tối đa 24 giờ)
        await redis.delete(fails_key)
    await _watch_org(db, redis, org_id, now)


async def _breaker_ok(db: AsyncSession, redis: Any, org_id: uuid.UUID) -> None:
    """Gọi được Gen-hub: xoá mọi khoá ngắt mạch + đóng sự cố `hub.breaker` (nếu có). Bên gọi commit."""
    if redis is None:
        return
    await redis.delete(_bk("fails", org_id), _bk("open_until", org_id), _bk("down_since", org_id), _bk("half", org_id))
    await health.clear(db, org_id, BREAKER_KEY, redis=redis)  # no-op khi không có sự cố đang mở


async def breaker_state(redis: Any, org_id: uuid.UUID) -> dict[str, Any]:
    """{"open": bool, "retry_in_s": int|None, "down_since": ISO|None}. Redis None ⇒ ngắt mạch tắt (luôn đóng)."""
    if redis is None:
        return {"open": False, "retry_in_s": None, "down_since": None}
    until = _epoch(await redis.get(_bk("open_until", org_id)))
    down = _epoch(await redis.get(_bk("down_since", org_id)))
    now = _clock()
    is_open = until is not None and until > now
    return {"open": is_open, "retry_in_s": max(1, math.ceil(until - now)) if is_open and until is not None else None,
            "down_since": iso(datetime.fromtimestamp(down, UTC)) if down is not None else None}


async def _watch_org(db: AsyncSession, redis: Any, org_id: uuid.UUID, now: float) -> bool:
    """Gen-hub im ≥ 15 phút VÀ ngắt mạch đã từng mở ⇒ mở sự cố `hub.breaker` (chuông Owner một lần); không còn dấu
    "bắt đầu im" ⇒ đóng. Trả True khi vừa gửi chuông. Bên gọi commit."""
    down = _epoch(await redis.get(_bk("down_since", org_id)))
    if down is None:
        await health.clear(db, org_id, BREAKER_KEY, redis=redis)
        return False
    if now - down >= BREAKER_ALERT_AFTER_S and await redis.exists(_bk("half", org_id)):
        return await health.raise_once(
            db, org_id, key=BREAKER_KEY, kind=BREAKER_KIND, severity="warn", fingerprint="open",
            title="Gen-hub không trả lời hơn 15 phút",
            body="Gen tạm chưa đọc được Kho, lịch, mail và việc. Kiểm tra máy Gen-hub còn chạy không, rồi bấm Kiểm "
                 "tra ở Kết nối › Gen-hub.", link="/connections#genhub", redis=redis)
    return False


async def breaker_watch(sm: Any, redis: Any, now: datetime | float | None = None) -> int:
    """Cron `hub_breaker_watch` (5 phút/lần): duyệt mọi tổ chức có liên kết Gen-hub, mở/đóng sự cố `hub.breaker`.
    Chỉ đọc Redis + ghi `ops.health_alerts`. Trả số chuông MỚI đã gửi. Redis None ⇒ 0."""
    if redis is None:
        return 0
    at = _clock() if now is None else (now.timestamp() if isinstance(now, datetime) else float(now))
    sent = 0
    async with sm() as db:
        orgs = (await db.execute(text("SELECT org_id FROM agent.hub_links WHERE server_id IS NOT NULL"))
                ).scalars().all()
        for org in orgs:
            sent += int(await _watch_org(db, redis, org, at))
        await db.commit()
    return sent


# ─── đọc Gen-hub (Kho + Google) ────────────────────────────────────────────────

@dataclass(frozen=True)
class SystemActor:
    """Actor hệ thống của việc nền (Bản tin Gen đọc nhân danh tổ chức, chỉ để gửi Owner). Có cùng hình dạng tối
    thiểu như `CurrentUser` mà `call_hub`/`invoke_tool` cần; Action Log ghi `actor_type='system'`."""

    org_id: uuid.UUID
    actor_id: str = "system:gen.briefing"
    ip: str | None = None
    role_code: str = rbac.OWNER
    actor_type: str = "system"


def _missing_error(suffix: str) -> ApiError:
    scope = scope_of(suffix)
    if scope is None:
        return conflict("HUB_TOOL_MISSING", f"Gen-hub chưa cấp tool {suffix} — bấm Kiểm tra ở thẻ Gen-hub")
    return conflict("HUB_TOOL_MISSING", f"Gen-hub chưa cấp quyền {SCOPE_LABELS[scope]} — vào Gen-hub tick thêm quyền "
                    "cho token của Gen-Harness rồi bấm Kiểm tra ở Kết nối › Gen-hub", f"Thiếu tool {suffix}")


async def call_hub(db: AsyncSession, redis: Any, client: McpClient, *, user: service.CurrentUser | SystemActor,
                   suffix: str, args: dict[str, Any]) -> dict[str, Any]:
    """Một lần ĐỌC Gen-hub (Kho, lịch, mail, việc, Drive) cho Gen/Owner. Kết quả LUÔN đã che.

    Thứ tự: danh sách cho phép → liên kết bật → đệm 5 phút (vẫn trả khi ngắt mạch mở) → ngắt mạch → tool đã
    mở/cấp → guard MCP Hub (`invoke_tool`) → che → đệm. Lỗi mạng/5xx/429 tính vào ngắt mạch; thành công đóng nó."""
    if suffix not in READ_SUFFIXES:
        raise conflict("HUB_TOOL_NOT_ALLOWED", "Tool này không nằm trong danh sách đọc Gen-hub được phép")
    source = source_of(suffix, await load_kho_label(db, user.org_id))
    noun = _noun_of(suffix)
    link = await load(db, user.org_id)
    if link is None or link.server_id is None or not link.enabled:
        raise conflict("HUB_LINK_OFF", "Chưa nối Gen-hub — Sếp cấu hình ở Kết nối › thẻ Gen-hub rồi bấm Kiểm tra")
    key = cache_key(user.org_id, suffix, args)
    if redis is not None:
        raw = await redis.get(key)
        if raw:
            return {"source": source, "tool": suffix, "cached": True, "data": orjson.loads(raw)}
    if await _breaker_blocked(redis, user.org_id):
        raise conflict("HUB_BREAKER_OPEN", "Gen-hub tạm không trả lời — thử lại sau ít phút",
                       f"Ngắt mạch {BREAKER_OPEN_S} giây sau {BREAKER_FAILS} lỗi liên tiếp")
    tool = await find_tool(db, user.org_id, link.server_id, suffix)
    if tool is None:
        raise _missing_error(suffix)
    if scope_of(suffix) is not None and (not tool.is_exposed or AGENT_KEY not in await invoke.grants_of(db, tool.id)):
        raise _missing_error(suffix)  # Google: chưa mở/cấp = chưa có quyền đọc (cùng nghĩa với `read_scopes`)
    if endpoint_forbidden(link.endpoint or ""):
        raise conflict("HUB_BLOCKED", f"{noun.capitalize()} đang bị chặn bởi rào chắn MCP Hub", ENDPOINT_FORBIDDEN_MSG)
    token = await invoke.auth_token(db, link.server_id)
    secrets = (token,) if token else ()
    try:
        out = await invoke.invoke_tool(db, redis, client, org_id=user.org_id, tool=tool, agent_key=AGENT_KEY,
                                       args=args, actor=user, summarize=lambda r: _summary(suffix, r))
    except invoke.McpCallFailed as e:
        msg = scrub(_classify(str(e.cause)), token)
        if _counts_for_breaker(e.cause):
            await _breaker_fail(db, redis, user.org_id)
        await _set_result(db, user.org_id, ok=False, error=msg)
        await db.commit()
        raise conflict("HUB_UNAVAILABLE", f"Chưa đọc được {noun} lúc này", msg) from e
    except ApiError as e:
        # Bị chặn bởi guard MCP Hub (máy chủ tắt, tool đóng/chưa cấp, chặn mạng, mức tự trị) — log đã commit.
        msg = scrub(str(e.detail or e.title), token)
        await _set_result(db, user.org_id, ok=False, error=msg)
        await db.commit()
        raise conflict("HUB_BLOCKED", f"{noun.capitalize()} đang bị chặn bởi rào chắn MCP Hub", msg) from e
    if out["outcome"] != "ok":
        await db.commit()  # giữ bản nháp mcp_write + log trước khi báo lỗi (route ném → rollback)
        raise conflict("HUB_TOOL_HELD", "Tool đang ở loại ghi — đã tạo bản nháp chờ duyệt, không gọi ra ngoài")
    data = mask_hub(out["result"], secrets=secrets, suffix=suffix)
    if redis is not None:
        await redis.set(key, orjson.dumps(data), ex=CACHE_TTL_S)
    await _breaker_ok(db, redis, user.org_id)
    await _set_result(db, user.org_id, ok=True)
    return {"source": source, "tool": suffix, "cached": False, "data": data}


call_kho = call_hub  # tên cũ (v0.1.26) — mã/test cũ vẫn chạy


async def is_hub_server(db: AsyncSession, org_id: uuid.UUID, server_id: uuid.UUID) -> bool:
    link = await load(db, org_id)
    return link is not None and link.server_id is not None and link.server_id == server_id


async def guard_server_admin(db: AsyncSession, *, user: service.CurrentUser, server_id: uuid.UUID,
                             action: str) -> bool:
    """Route chung sửa/xoá/khám phá máy chủ MCP (`system.manage` — vai trò tuỳ biến có thể có) KHÔNG được đụng máy
    chủ của liên kết Gen-hub nếu không phải Owner: đổi `endpoint` rồi khám phá/gọi = gửi token Kho tới nơi khác.
    Trả True khi đó là máy chủ Gen-hub (bên gọi dùng client ghim DNS)."""
    if not await is_hub_server(db, user.org_id, server_id):
        return False
    if user.role_code != rbac.OWNER:
        msg = f"Bị chặn: máy chủ Gen-hub ({await load_kho_label(db, user.org_id)}) chỉ Owner được quản lý"
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="mcp.server_blocked", target_type="mcp_server", target_id=str(server_id),
                               target_label=SERVER_NAME, result="blocked",
                               detail={"code": "HUB_OWNER_ONLY", "reason": msg, "op": action}, ip=user.ip)
        await db.commit()
        raise ApiError(403, "HUB_OWNER_ONLY", "Bị chặn", msg)
    return True


async def _blocked_call(db: AsyncSession, redis: Any, *, user: service.CurrentUser, tool: Any, agent_key: str,
                        args: dict[str, Any], code: str, msg: str, detail: str) -> ApiError:
    """Ghi `mcp_calls` (blocked) + Action Log `mcp.call_blocked`, commit rồi trả lỗi 403 cho bên gọi ném (route ném
    → `DB` rollback cả phiên, nên log phải commit TRƯỚC)."""
    item = await invoke.log_call(db, redis, org_id=user.org_id, tool_id=tool.id, agent_key=agent_key, args=args,
                                 outcome="blocked", result_summary=msg, latency_ms=0)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="mcp.call_blocked", target_type="mcp_tool", target_id=str(tool.id),
                           target_label=f"{tool.server_name} · {tool.name}", result="blocked",
                           detail={"code": code, "reason": msg, "agent_key": agent_key}, ip=user.ip)
    await db.commit()
    return ApiError(403, code, "Bị chặn", detail, call=item)


async def generic_call(db: AsyncSession, redis: Any, transport: Any, *, user: service.CurrentUser, tool: Any,
                       agent_key: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Route chung `POST /mcp/tools/{id}/call` gọi vào máy chủ của liên kết Gen-hub (v0.1.27): Kho chỉ Owner
    (quyết định Boss #1) → vai trò khác bị chặn (403 `HUB_OWNER_ONLY`, có log); Owner thì đi đúng đường của liên
    kết — ghim DNS, `mcp_calls` chỉ siêu dữ liệu, kết quả đã che. Trả None khi tool không thuộc Gen-hub.

    v0.1.49 (QD-16): tool Gen-hub NGOÀI danh sách đọc (`suffix_of` None — gmail_send, calendar_create_event,
    drive_create_file…) bị từ chối 403 `HUB_TOOL_NOT_ALLOWED`, kể cả Owner, TRƯỚC `invoke_tool` (không tạo bản nháp
    `mcp_write`, không gọi ra ngoài): Gen-Harness chỉ đọc qua Gen-hub."""
    link = await load(db, user.org_id)
    if link is None or link.server_id is None or link.server_id != tool.server_id:
        return None
    if user.role_code != rbac.OWNER:
        msg = f"Bị chặn: máy chủ Gen-hub ({await load_kho_label(db, user.org_id)}) chỉ Owner được gọi"
        raise await _blocked_call(db, redis, user=user, tool=tool, agent_key=agent_key, args=args,
                                  code="HUB_OWNER_ONLY", msg=msg, detail=msg)
    suffix = suffix_of(tool.name)
    if suffix is None:
        detail = ("Gen-Harness chỉ đọc qua Gen-hub — tool ghi (gửi mail, tạo lịch, ghi Drive…) không được gọi")
        if write_suffix_of(tool.name) is not None:
            # v0.1.50: ghi Kho có đúng một đường — đề xuất của Gen, Sếp Xác nhận + nhập mã PIN (không gọi trực tiếp).
            detail = ("Chỉ đọc qua Gen-hub ở đường này — ghi vào Kho chỉ đi qua đề xuất của Gen: Sếp bấm Xác nhận và "
                      "nhập mã PIN, không gọi trực tiếp được")
        raise await _blocked_call(db, redis, user=user, tool=tool, agent_key=agent_key, args=args,
                                  code="HUB_TOOL_NOT_ALLOWED", msg=f"Bị chặn: {detail}", detail=detail)
    token = await invoke.auth_token(db, link.server_id)
    try:
        out = await invoke.invoke_tool(db, redis, client_for(transport), org_id=user.org_id, tool=tool,
                                       agent_key=agent_key, args=args, actor=user,
                                       summarize=lambda r: _summary(suffix, r))
    except invoke.McpCallFailed as e:
        raise invoke.McpCallFailed(e.cause, scrub(str(e.title), token)) from e
    if out["outcome"] == "ok":
        out["result"] = mask_hub(out["result"], secrets=(token,) if token else (), suffix=suffix)
    return out


# ─── ghi Kho (v0.1.50, F-81, QD-18): MỘT đường duy nhất, sau Xác nhận + PIN + permit ────────────────────────────────

WRITE_PERMIT_MSG = ("Giấy phép ghi Kho không hợp lệ hoặc đã quá 5 phút — chưa ghi gì vào Kho. Bấm Xác nhận lại "
                    "(nhập mã PIN) để ghi")
WRITE_MISSING_MSG = ("Gen-hub chưa cấp quyền ghi Kho — vào Kết nối › Gen-hub tick kho_create, kho_update cho token "
                     "rồi bấm Kiểm tra")
# Owner TỰ đóng tool ghi ở MCP Hub (hoặc gỡ cấp core.gen): tick ở Gen-hub + Kiểm tra KHÔNG mở lại ⇒ câu riêng,
# chỉ đúng chỗ cần mở.
WRITE_HIDDEN_MSG = ("Sếp đã tự đóng {tool} ở MCP Hub — chưa ghi gì vào Kho. Muốn Gen ghi thì mở lại tool đó "
                    "(và cấp cho Gen) ở MCP Hub; Kiểm tra ở Gen-hub không tự mở lại")
WRITE_UNCERTAIN_MSG = ("Chưa chắc đã ghi — Sếp mở Kho kiểm trước khi bấm lại: Kho đã có bản ghi thì bấm Huỷ; "
                       "chưa có thì bấm Xác nhận lại")
WRITE_REJECTED_MSG = "Kho từ chối lần ghi này"
WRITE_INVALID_MSG = "Dữ liệu ghi Kho chưa hợp lệ — chưa ghi gì vào Kho"
# 401/403 từ Gen-hub khi GHI: token hết hạn / bị thu hồi — không phải lỗi trường, Sếp phải đổi token chứ không Sửa thẻ.
WRITE_TOKEN_MSG = ("Token Gen-hub hết hạn hoặc đã bị thu hồi — chưa ghi gì vào Kho. Vào Kết nối › Gen-hub dán token "
                   "mới rồi bấm Kiểm tra")
WRITE_NOT_ALLOWED_MSG = "Chỉ ghi Kho bằng kho_create hoặc kho_update — tool khác không được gọi"
_MA_IN_TEXT = re.compile(r"\b(?:PHIEN|VIEC)-\d{1,6}\b")
_MA_KEYS = ("Mã ID", "ma_id", "ma")


def _write_summary(suffix: str, result: Any) -> str:
    """`mcp_calls.result_summary` của lần ghi — chỉ siêu dữ liệu, không nội dung."""
    return f"Ghi Gen-hub ({suffix}) — {len(orjson.dumps(result))} byte, nội dung không lưu"


def _ma_by_key(v: Any, bang: str, depth: int = 0) -> str | None:
    """Mã ở khoá 'Mã ID' / ma_id / ma (ưu tiên) — chỉ nhận mã đúng bảng vừa ghi."""
    if depth > 6:
        return None
    if isinstance(v, dict):
        for k in _MA_KEYS:
            x = v.get(k)
            if isinstance(x, str) and kho_write.bang_of_ma(x.strip().upper()) == bang:
                return x.strip().upper()
        for x in v.values():
            found = _ma_by_key(x, bang, depth + 1)
            if found:
                return found
    elif isinstance(v, list):
        for x in v:
            found = _ma_by_key(x, bang, depth + 1)
            if found:
                return found
    elif isinstance(v, str):
        parsed = _json_or_none(v)
        if parsed is not None:
            return _ma_by_key(parsed, bang, depth + 1)
    return None


def _codes_in_text(v: Any, depth: int = 0) -> list[str]:
    """Mọi mã PHIEN-n / VIEC-n trong chữ của `v` (theo thứ tự gặp)."""
    if depth > 6:
        return []
    if isinstance(v, str):
        return _MA_IN_TEXT.findall(v)
    items = list(v.values()) if isinstance(v, dict) else v if isinstance(v, list) else []
    return [c for x in items for c in _codes_in_text(x, depth + 1)]


def ma_from_result(result: Any, bang: str, sent: Iterable[Any] = ()) -> str | None:
    """Mã bản ghi VỪA TẠO ('PHIEN-12') trong kết quả tool ghi Kho (đã che), hoặc None khi không chắc.

    Chỉ nhận mã đúng bảng `bang`. Ưu tiên khoá 'Mã ID' / ma_id / ma; không có khoá thì lấy mã trong chữ nhưng BỎ mọi mã
    xuất hiện trong nội dung vừa gửi (`sent` — Kho hay lặp lại bản ghi, vd Việc 'Làm tiếp VIEC-12'): còn đúng một mã thì
    nhận, không còn hoặc còn nhiều mã khác nhau ⇒ None (không đoán — Sếp đọc Kho)."""
    by_key = _ma_by_key(result, bang)
    if by_key:
        return by_key
    echoed = {c for v in sent for c in _codes_in_text(v)}
    left = {c for c in _codes_in_text(result) if kho_write.bang_of_ma(c) == bang and c not in echoed}
    return left.pop() if len(left) == 1 else None


def _result_text(result: Any) -> str:
    """Chữ ngắn trong kết quả (cho lỗi nghiệp vụ của Kho) — đã che."""
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        parts = [str(c["text"]) for c in result["content"] if isinstance(c, dict) and isinstance(c.get("text"), str)]
        if parts:
            return " ".join(" ".join(parts).split())
    return " ".join(str(result).split())


def _digest16(value: Any) -> str:
    return hashlib.sha256(orjson.dumps(value, option=orjson.OPT_SORT_KEYS, default=str)).hexdigest()[:16]


async def _write_log(db: AsyncSession, user: service.CurrentUser, action: str, *, result: str, tool: str,
                     proposal_id: str, target_id: str | None = None, target_label: str | None = None,
                     **detail: Any) -> None:
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action=action,
                           target_type="kho_record", target_id=target_id, target_label=target_label, result=result,
                           detail={"tool": tool, "proposal_id": proposal_id, **detail}, ip=user.ip)


async def _write_blocked(db: AsyncSession, user: service.CurrentUser, status: int, code: str, title: str, *,
                         tool: str, proposal_id: str, reason: str | None = None) -> ApiError:
    """Ghi Action Log `hub.kho_write_blocked`, COMMIT (route ném lỗi sẽ rollback phiên) rồi trả lỗi cho bên gọi ném."""
    await _write_log(db, user, "hub.kho_write_blocked", result="blocked", tool=str(tool)[:40],
                     proposal_id=proposal_id, code=code, reason=reason or code)
    await db.commit()
    return ApiError(status, code, title, reason)


async def write_kho(db: AsyncSession, redis: Any, client: McpClient, *, user: service.CurrentUser,
                    proposal_id: uuid.UUID, tool: str, args: dict[str, Any], permit: Any) -> dict[str, Any]:
    """MỘT lần GHI Kho dữ liệu (kho_create / kho_update — bảng Phiên, Việc) sau khi Owner đã Xác nhận + nhập PIN.

    Thứ tự (lỗi nào cũng KHÔNG gọi ra ngoài trước khi qua hết): tool ∈ KHO_WRITE_SUFFIXES → Owner → permit ký (gắn
    đúng đề xuất + tool + sha256 tham số, 5 phút, dùng một lần) → kiểm lại tham số (kho_write) → liên kết bật →
    ngắt mạch → tool có + mở + cấp `core.gen` → guard địa chỉ → `invoke_tool(approved_write=True)`. Token Gen-hub
    không bao giờ vào log / Action Log / lỗi trả về. Lỗi mạng / timeout / 5xx SAU khi gửi ⇒ `HUB_WRITE_UNCERTAIN`."""
    pid = str(proposal_id)
    if tool not in KHO_WRITE_SUFFIXES:
        raise await _write_blocked(db, user, 403, "HUB_TOOL_NOT_ALLOWED", WRITE_NOT_ALLOWED_MSG, tool=tool,
                                   proposal_id=pid)
    if user.role_code != rbac.OWNER:
        label = await load_kho_label(db, user.org_id)
        raise await _write_blocked(db, user, 403, "HUB_OWNER_ONLY", f"Chỉ Owner được ghi vào {label}", tool=tool,
                                   proposal_id=pid)
    err = await hub_permit.verify(permit, org_id=user.org_id, user_id=user.id, proposal_id=proposal_id, tool=tool,
                                  args=args, redis=redis)
    if err is not None:
        raise await _write_blocked(db, user, 403, "HUB_WRITE_PERMIT", WRITE_PERMIT_MSG, tool=tool, proposal_id=pid,
                                   reason=err)
    try:
        bang, ma, record = kho_write.parse_args(tool, args)
    except ValueError as e:
        await _write_log(db, user, "hub.kho_write_blocked", result="blocked", tool=tool, proposal_id=pid,
                         code="HUB_WRITE_INVALID", reason=str(e)[:200])
        await db.commit()
        raise ApiError(422, "HUB_WRITE_INVALID", WRITE_INVALID_MSG, str(e), errors={"args": str(e)}) from e
    link = await load(db, user.org_id)
    if link is None or link.server_id is None or not link.enabled:
        raise conflict("HUB_LINK_OFF", "Chưa nối Gen-hub — Sếp cấu hình ở Kết nối › thẻ Gen-hub rồi bấm Kiểm tra")
    if await _breaker_blocked(redis, user.org_id):
        raise conflict("HUB_BREAKER_OPEN", "Gen-hub tạm không trả lời — thử lại sau ít phút",
                       f"Ngắt mạch {BREAKER_OPEN_S} giây sau {BREAKER_FAILS} lỗi liên tiếp")
    row = await find_tool(db, user.org_id, link.server_id, tool)
    if row is None or not row.is_exposed or AGENT_KEY not in await invoke.grants_of(db, row.id):
        if row is not None and await _hidden_by_owner(db, user.org_id, row.id):
            # Tick ở Gen-hub rồi Kiểm tra KHÔNG giúp gì (Kiểm tra tôn trọng lựa chọn đóng của Owner) ⇒ chỉ đúng MCP Hub.
            raise conflict("HUB_WRITE_HIDDEN", WRITE_HIDDEN_MSG.format(tool=tool),
                           f"Tool {tool} đã bị Owner đóng (hoặc gỡ cấp Gen) ở MCP Hub")
        raise conflict("HUB_WRITE_MISSING", WRITE_MISSING_MSG, f"Thiếu tool {tool}")
    if endpoint_forbidden(link.endpoint or ""):
        raise conflict("HUB_BLOCKED", "Kho đang bị chặn bởi rào chắn MCP Hub", ENDPOINT_FORBIDDEN_MSG)
    token = await invoke.auth_token(db, link.server_id)
    secrets = (token,) if token else ()
    label = f"{bang} · {record.get('Chủ đề') or record.get('Tiêu đề') or ma}"[:200]
    try:
        out = await invoke.invoke_tool(db, redis, client, org_id=user.org_id, tool=row, agent_key=AGENT_KEY,
                                       args=args, actor=user, summarize=lambda r: _write_summary(tool, r),
                                       approved_write=True)
    except invoke.McpCallFailed as e:
        msg = scrub(_classify(str(e.cause)), token)
        if _counts_for_breaker(e.cause):
            await _breaker_fail(db, redis, user.org_id)
            await _set_result(db, user.org_id, ok=False, error=msg)
            await _write_log(db, user, "hub.kho_write_failed", result="failed", tool=tool, proposal_id=pid,
                             target_id=ma or None, target_label=label, code="HUB_WRITE_UNCERTAIN", error=msg)
            await db.commit()
            raise ApiError(502, "HUB_WRITE_UNCERTAIN", WRITE_UNCERTAIN_MSG, msg) from e
        if str(e.cause).split(":", 1)[0].strip() in ("401", "403"):
            # Token bị từ chối: thẻ Gen-hub hiện "hết hạn"; mã riêng để thẻ đề xuất dẫn Sếp tới Kết nối › Gen-hub.
            await _set_result(db, user.org_id, ok=False, error=msg)
            await _write_log(db, user, "hub.kho_write_failed", result="failed", tool=tool, proposal_id=pid,
                             target_id=ma or None, target_label=label, code="HUB_TOKEN_REJECTED", error=msg)
            await db.commit()
            raise conflict("HUB_TOKEN_REJECTED", WRITE_TOKEN_MSG, msg) from e
        await _write_log(db, user, "hub.kho_write_failed", result="failed", tool=tool, proposal_id=pid,
                         target_id=ma or None, target_label=label, code="HUB_WRITE_REJECTED", error=msg)
        await db.commit()
        raise conflict("HUB_WRITE_REJECTED", f"{WRITE_REJECTED_MSG}: {msg}"[:300], msg) from e
    except ApiError as e:
        # Bị chặn bởi guard MCP Hub (máy chủ tắt, tool đóng/chưa cấp, chặn mạng, mức tự trị) — log đã commit.
        msg = scrub(str(e.detail or e.title), token)
        raise conflict("HUB_BLOCKED", "Ghi Kho đang bị chặn bởi rào chắn MCP Hub", msg) from e
    result = out["result"]
    masked = mask_hub(result, secrets=secrets, suffix=tool)
    await _breaker_ok(db, redis, user.org_id)
    if isinstance(result, dict) and result.get("isError") is True:
        # Kho trả lỗi nghiệp vụ (vd giá trị cột lựa chọn không có) — KHÔNG được coi là đã ghi.
        msg = scrub(_result_text(masked), token)[:200] or WRITE_REJECTED_MSG
        await _write_log(db, user, "hub.kho_write_failed", result="failed", tool=tool, proposal_id=pid,
                         target_id=ma or None, target_label=label, code="HUB_WRITE_REJECTED", error=msg)
        await db.commit()
        raise conflict("HUB_WRITE_REJECTED", f"{WRITE_REJECTED_MSG}: {msg}"[:300], msg)
    await clear_cache(redis, user.org_id)  # Kho vừa đổi: bỏ đệm đọc 5 phút để Gen không trả bản cũ
    await _set_result(db, user.org_id, ok=True)
    ma = ma or ma_from_result(masked, bang, record.values()) or ""
    await _write_log(db, user, "hub.kho_written", result="ok", tool=tool, proposal_id=pid, target_id=ma or None,
                     target_label=f"{bang} · {record.get('Chủ đề') or record.get('Tiêu đề') or ma}"[:200],
                     bang=bang, field_keys=sorted(record), fields_digest=_digest16(record))
    await db.commit()  # Kho ĐÃ ghi: giữ nhật ký (mcp.call_ok + hub.kho_written) dù bước phụ phía sau có lỗi
    if not await boss_checks.pass_count(db, user.org_id, "kho_write"):
        await boss_checks.record(db, user.org_id, "kho_write", "pass", user_id=user.id)
    return {"ok": True, "tool": tool, "bang": bang, "ma": ma or None}


# ─── cấu hình + kiểm tra ──────────────────────────────────────────────────────

async def upsert(db: AsyncSession, redis: Any, *, user: service.CurrentUser, endpoint: str | None,
                 token: str | None, token_expires_at: datetime | None, set_expiry: bool,
                 allow_public_network: bool | None, disable: bool) -> Any:
    cur = await load(db, user.org_id)
    changed: list[str] = []
    server_id = cur.server_id if cur is not None else None
    if server_id is None:
        if not endpoint or not token:
            raise ApiError(422, "VALIDATION", "Dữ liệu chưa hợp lệ",
                           errors={"endpoint": "Cần địa chỉ Gen-hub và token cho lần nối đầu"})
        server_id = (await db.execute(text("""
            INSERT INTO agent.mcp_servers (org_id, name, transport, endpoint, auth_enc, allow_public_network, note)
            VALUES (:o, :n, 'streamable_http', :e, :a, :pub, :note) RETURNING id"""),
            {"o": user.org_id, "n": SERVER_NAME, "e": endpoint, "a": invoke.encrypt_token(token),
             "pub": bool(allow_public_network),
             "note": server_note(await load_kho_label(db, user.org_id))})).scalar_one()
        changed += ["endpoint", "token"] + (["allow_public_network"] if allow_public_network else [])
    else:
        sets: list[str] = []
        params: dict[str, Any] = {"i": server_id}
        if endpoint is not None and endpoint != cur.endpoint:
            sets.append("endpoint = :e")
            params["e"] = endpoint
            changed.append("endpoint")
        if token:
            sets.append("auth_enc = :a")
            params["a"] = invoke.encrypt_token(token)
            changed.append("token")
        if allow_public_network is not None and allow_public_network != cur.allow_public_network:
            sets.append("allow_public_network = :pub")
            params["pub"] = allow_public_network
            changed.append("allow_public_network")
        if sets:
            await db.execute(text(f"UPDATE agent.mcp_servers SET {', '.join(sets)} WHERE id = :i"),  # noqa: S608
                             params)
    if set_expiry:
        changed.append("token_expires_at")
    if disable:
        changed.append("enabled")
    relink = any(k in changed for k in ("endpoint", "token", "allow_public_network"))
    await db.execute(text("""
        INSERT INTO agent.hub_links (org_id, server_id, enabled, token_expires_at, updated_by, updated_at)
        VALUES (:o, :s, false, :exp, :u, now())
        ON CONFLICT (org_id) DO UPDATE SET
          server_id = EXCLUDED.server_id,
          enabled = CASE WHEN :off THEN false ELSE agent.hub_links.enabled END,
          token_expires_at = CASE WHEN :setexp THEN EXCLUDED.token_expires_at
                                  ELSE agent.hub_links.token_expires_at END,
          expiry_notified_at = CASE WHEN :setexp OR :tok THEN NULL ELSE agent.hub_links.expiry_notified_at END,
          last_error = CASE WHEN :relink THEN NULL ELSE agent.hub_links.last_error END,
          updated_by = EXCLUDED.updated_by, updated_at = now()"""),
        {"o": user.org_id, "s": server_id, "exp": token_expires_at, "u": user.id,
         "off": disable or relink, "setexp": set_expiry, "tok": "token" in changed, "relink": relink})
    if relink or disable:
        await clear_cache(redis, user.org_id)
    if changed:
        detail: dict[str, Any] = {"changed": changed}
        if "token" in changed:
            detail["token"] = "(đã đổi)"
        if set_expiry:
            detail["token_expires_at"] = iso(token_expires_at)
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="hub.link_disabled" if changed == ["enabled"] else "hub.link_updated",
                               target_type="hub_link", target_id=str(server_id), target_label=SERVER_NAME,
                               detail=detail, ip=user.ip)
    return await load(db, user.org_id)


# ─── Tên Kho do Owner tự đặt (v0.1.57, Nợ #30) ───────────────────────────────

async def kho_label_out(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    """Phần `kho_label*` của `GET /hub/link`: tên hiệu lực + đã đổi chưa + tên mặc định (web hiện chip Đã đổi)."""
    label = await load_kho_label(db, org_id)
    return {"kho_label": label, "kho_label_custom": label != KHO_LABEL, "kho_label_default": KHO_LABEL,
            "kho_label_max": KHO_LABEL_MAX}


async def refresh_server_note(db: AsyncSession, org_id: uuid.UUID, old: str, new: str) -> None:
    """Đổi tên Kho ⇒ ghi chú máy chủ Gen-hub do hệ thống tự đặt (đúng chuỗi cũ) đổi theo; ghi chú Owner sửa tay giữ."""
    await db.execute(text("""UPDATE agent.mcp_servers SET note = :new
                             WHERE org_id = :o AND name = :n AND note = :old"""),
                     {"o": org_id, "n": SERVER_NAME, "old": server_note(old), "new": server_note(new)})


async def set_kho_label(db: AsyncSession, *, user: service.CurrentUser, label: str) -> bool:
    """Lưu tên Kho của tổ chức (`label` đã chuẩn hoá + kiểm độ dài); rỗng ⇒ xoá khoá, về mặc định. True khi có đổi."""
    old = await load_kho_label(db, user.org_id)
    new = label or KHO_LABEL
    if new == old:
        return False
    if label:
        await db.execute(text("""UPDATE core.organizations
                                 SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb), '{kho_label}',
                                                          to_jsonb(CAST(:l AS text)), true) WHERE id = :o"""),
                         {"o": user.org_id, "l": label})
    else:
        await db.execute(text("UPDATE core.organizations SET settings = settings - 'kho_label' WHERE id = :o"),
                         {"o": user.org_id})
    await refresh_server_note(db, user.org_id, old, new)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="hub.kho_label_changed", target_type="hub_link", target_label=SERVER_NAME,
                           detail={"from": old, "to": new}, ip=user.ip)
    return True


async def test_link(db: AsyncSession, redis: Any, client: McpClient, *, user: service.CurrentUser) -> dict[str, Any]:
    """Khám phá tool → mở + cấp `core.gen` ĐÚNG các tool đọc (hậu tố cho phép, loại `read`: Kho + Google) → gọi
    `kho_tom_tat`. Xanh → bật liên kết. Không ném lỗi khi Gen-hub lỗi: trả `{ok: false, error}` để thẻ hiện lý do.

    v0.1.49 (QD-16): thêm `read_scopes` (calendar/mail/tasks/drive → bool, tính từ lần khám phá này), `read_missing`
    (nhãn quyền đọc thêm còn thiếu) và `write_tools` (tool trong danh sách đọc nhưng Gen-hub đánh dấu GHI — không mở).
    Thiếu quyền đọc Google KHÔNG làm `ok=false`; không gọi tool Google khi kiểm. Nút Kiểm tra không bị ngắt mạch chặn;
    kiểm xanh thì đóng ngắt mạch."""
    link = await load(db, user.org_id)
    if link is None or link.server_id is None:
        raise conflict("HUB_LINK_NOT_CONFIGURED", "Chưa nhập địa chỉ và token Gen-hub")
    server = await invoke.get_server(db, user.org_id, link.server_id)
    token = await invoke.auth_token(db, link.server_id)
    started = time.monotonic()

    async def fail(msg: str, *, code: str | None = None, tools_missing: bool = False, **extra: Any) -> dict[str, Any]:
        error_code = _error_code(msg, code=code, missing=tools_missing)
        msg = scrub(msg, token)
        scopes = await read_scopes(db, user.org_id)
        wscopes = await write_scopes(db, user.org_id)
        await _set_result(db, user.org_id, ok=False, error=msg)
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="hub.link_tested", target_type="hub_link", target_id=str(link.server_id),
                               target_label=SERVER_NAME, result="failed", detail={"error": msg, **extra}, ip=user.ip)
        await db.commit()
        return {"ok": False, "error": msg, "error_code": error_code,
                "latency_ms": int((time.monotonic() - started) * 1000), "exposed_tools": [],
                "exposed_write_tools": [], "missing_tools": list(extra.get("missing_tools", [])), "read_scopes": scopes,
                "read_missing": read_missing(scopes), "write_tools": list(extra.get("write_tools", [])),
                "write_scopes": wscopes, "write_missing": write_missing(wscopes), "write_hidden": [],
                "link": link_out(await load(db, user.org_id))}

    if endpoint_forbidden(server.endpoint or ""):
        return await fail(ENDPOINT_FORBIDDEN_MSG)
    try:
        found = await invoke.discover(db, redis, client, org_id=user.org_id, server=server, actor=user)
    except ApiError as e:
        return await fail(_classify(str(e.title), code=e.code), code=e.code)
    exposed: list[str] = []
    exposed_write: list[str] = []
    write_hidden: list[str] = []
    write_tools: list[str] = []
    have: set[str] = set()
    have_write: set[str] = set()
    for t in found:
        suf = suffix_of(t["name"])
        wsuf = write_suffix_of(t["name"])
        if suf is None and wsuf is not None:
            # v0.1.50 (F-81): kho_create / kho_update — mở + cấp (mọi access, Gen-hub đánh dấu chúng là tool ghi) để
            # `write_kho` đi được; KHÔNG gọi khi kiểm, và route MCP chung vẫn chặn (generic_call). Owner đã TỰ đóng tool
            # (hoặc gỡ cấp core.gen) ở MCP Hub ⇒ Kiểm tra không mở lại — Owner mở lại ở MCP Hub khi muốn.
            if await _hidden_by_owner(db, user.org_id, uuid.UUID(t["id"])):
                write_hidden.append(wsuf)
                continue
            have_write.add(wsuf)
            await _open_and_grant(db, user, t, via="hub_link_write")
            exposed.append(t["name"])
            exposed_write.append(t["name"])
            continue
        if suf is None:
            continue  # tool lạ (Vault, gmail_send…): để nguyên, mặc định đóng — code cũng không bao giờ gọi
        if t["access"] != "read":
            write_tools.append(t["name"])
            continue
        have.add(suf)
        await _open_and_grant(db, user, t, via="hub_link")
        exposed.append(t["name"])
    if await _revoke_gone_google(db, user, link.server_id, {t["name"] for t in found}):
        # Quyền đọc vừa mất ⇒ bỏ đệm 5 phút NGAY (kể cả khi lượt kiểm này đỏ ở bước sau): không trả mail/lịch cũ nữa.
        await clear_cache(redis, user.org_id)
    scopes = {scope: all(suf in have for suf in sufs) for scope, sufs in READ_SCOPES.items()}
    wscopes = write_scope_map(set(have_write))
    missing = [s for s in REQUIRED_SUFFIXES if s not in have]
    if "kho_tom_tat" in missing:
        return await fail(f"Gen-hub chưa cấp tool đọc Kho: {', '.join(missing)}", missing_tools=missing,
                          write_tools=write_tools, tools_missing=True)
    tool = await find_tool(db, user.org_id, link.server_id, "kho_tom_tat")
    try:
        await invoke.invoke_tool(db, redis, client, org_id=user.org_id, tool=tool, agent_key=AGENT_KEY, args={},
                                 actor=user, summarize=lambda r: _summary("kho_tom_tat", r))
    except invoke.McpCallFailed as e:
        return await fail(_classify(str(e.cause)), missing_tools=missing, write_tools=write_tools)
    except ApiError as e:
        return await fail(_classify(str(e.detail or e.title), code=e.code), code=e.code, missing_tools=missing,
                          write_tools=write_tools)
    await _set_result(db, user.org_id, ok=True, enable=True)
    await _breaker_ok(db, redis, user.org_id)
    await clear_cache(redis, user.org_id)
    latency = int((time.monotonic() - started) * 1000)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="hub.link_tested", target_type="hub_link", target_id=str(link.server_id),
                           target_label=SERVER_NAME, detail={"exposed": exposed, "missing_tools": missing,
                                                             "write_tools": write_tools, "read_scopes": scopes,
                                                             "write_scopes": wscopes, "write_hidden": write_hidden,
                                                             "latency_ms": latency},
                           ip=user.ip)
    return {"ok": True, "error": None, "error_code": None, "latency_ms": latency, "exposed_tools": exposed,
            "exposed_write_tools": exposed_write, "missing_tools": missing, "read_scopes": scopes,
            "read_missing": read_missing(scopes),
            "write_tools": write_tools, "write_scopes": wscopes,
            "write_missing": write_missing(wscopes, hidden=write_hidden), "write_hidden": write_hidden,
            "link": link_out(await load(db, user.org_id))}


async def _hidden_by_owner(db: AsyncSession, org_id: uuid.UUID, tool_id: uuid.UUID) -> bool:
    """Thay đổi mở/cấp GẦN NHẤT của tool là Owner TỰ đóng (`mcp.tool_hidden` từ MCP Hub, PIN `mcp.expose`) hoặc tự gỡ
    cấp `core.gen` (`mcp.grant_removed`) — không phải liên kết Gen-hub tự thu hồi (`via` = hub_link)."""
    row = (await db.execute(text("""
        SELECT action, detail FROM ops.action_log
        WHERE org_id = :o AND target_type = 'mcp_tool' AND target_id = :t
          AND (action IN ('mcp.tool_exposed', 'mcp.tool_hidden')
               OR (action IN ('mcp.grant_added', 'mcp.grant_removed') AND detail->>'agent_key' = :a))
        ORDER BY at DESC, id DESC LIMIT 1"""), {"o": org_id, "t": str(tool_id), "a": AGENT_KEY})).first()
    if row is None or row.action not in ("mcp.tool_hidden", "mcp.grant_removed"):
        return False
    return not (row.detail or {}).get("via")


async def _open_and_grant(db: AsyncSession, user: service.CurrentUser, t: dict[str, Any], *, via: str) -> None:
    """Mở (`is_exposed`) + cấp `core.gen` cho một tool Gen-hub vừa khám phá; ghi Action Log mỗi thay đổi thật."""
    tid = uuid.UUID(t["id"])
    if not t["is_exposed"]:
        await db.execute(text("UPDATE agent.mcp_tools SET is_exposed = true WHERE id = :i"), {"i": tid})
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="mcp.tool_exposed", target_type="mcp_tool", target_id=str(tid),
                               target_label=f"{SERVER_NAME} · {t['name']}", detail={"via": via}, ip=user.ip)
    added = (await db.execute(text("""INSERT INTO agent.mcp_grants (tool_id, agent_key) VALUES (:t, :a)
                                      ON CONFLICT DO NOTHING RETURNING tool_id"""),
                              {"t": tid, "a": AGENT_KEY})).scalar_one_or_none()
    if added is not None:
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="mcp.grant_added", target_type="mcp_tool", target_id=str(tid),
                               target_label=f"{SERVER_NAME} · {t['name']}",
                               detail={"agent_key": AGENT_KEY, "via": via}, ip=user.ip)


def write_missing(wscopes: dict[str, bool], *, hidden: Iterable[str] = ()) -> list[str]:
    """Nhãn quyền ghi Kho Gen-hub còn THIẾU (rỗng khi đủ), nêu đúng tool: 'ghi Kho (kho_update)'. Tool Owner tự đóng
    ở MCP Hub (`hidden`) không tính là thiếu ở Gen-hub — thẻ nói riêng."""
    off = set(hidden)
    gone = [suf for suf in KHO_WRITE_SUFFIXES if not wscopes.get(suf, wscopes.get("kho")) and suf not in off]
    return [f"ghi Kho ({', '.join(gone)})"] if gone else []


async def _revoke_gone_google(db: AsyncSession, user: service.CurrentUser, server_id: uuid.UUID,
                              listed: set[str]) -> bool:
    """Gen-hub không còn liệt kê một tool Google hoặc kho_create/kho_update (Owner bỏ tick quyền) mà tool đó vẫn đang
    mở/cấp từ lần kiểm trước ⇒ đóng + thu hồi grant `core.gen`, để `read_scopes`/`write_scopes` và lời gọi không đứng
    trên quyền đã mất. Trả True khi đã thu hồi ít nhất một tool (bên gọi xoá đệm)."""
    revoked = False
    rows = (await db.execute(text("""
        SELECT t.id, t.name FROM agent.mcp_tools t
        WHERE t.server_id = :s AND (t.is_exposed OR EXISTS (SELECT 1 FROM agent.mcp_grants g
                                                            WHERE g.tool_id = t.id AND g.agent_key = :a))"""),
                            {"s": server_id, "a": AGENT_KEY})).all()
    for r in rows:
        suf = suffix_of(r.name)
        gone_google = suf is not None and scope_of(suf) is not None
        if r.name in listed or not (gone_google or write_suffix_of(r.name) is not None):
            continue  # còn trong Gen-hub, hoặc không phải tool Google / ghi Kho do liên kết quản lý
        await db.execute(text("UPDATE agent.mcp_tools SET is_exposed = false WHERE id = :i"), {"i": r.id})
        await db.execute(text("DELETE FROM agent.mcp_grants WHERE tool_id = :t AND agent_key = :a"),
                         {"t": r.id, "a": AGENT_KEY})
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="mcp.tool_hidden", target_type="mcp_tool", target_id=str(r.id),
                               target_label=f"{SERVER_NAME} · {r.name}",
                               detail={"via": "hub_link", "reason": "Gen-hub không còn cấp tool này"}, ip=user.ip)
        revoked = True
    return revoked


# ─── hàm đọc cho Bản tin Gen (v0.1.49, QD-16) ──────────────────────────────────

BRIEFING_MAX_ITEMS = 10
_ITEM_TEXT_MAX = 200
MAIL_REPLY_QUERY = "is:unread in:inbox newer_than:3d -category:promotions -category:social -category:updates"
#: kind → (hậu tố tool, quyền đọc thêm)
BRIEFING_KINDS: dict[str, tuple[str, str]] = {
    "calendar_today": ("calendar_list_events", "calendar"),
    "mail_reply": ("gmail_search", "mail"),
    "tasks_open": ("tasks_list", "tasks"),
}
_LIST_KEYS = ("items", "events", "messages", "tasks", "files", "threads", "results", "data")
_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")
_LINE_EVENT = re.compile(
    r"^(?P<start>\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?"
    r"|\d{1,2}:\d{2}(?:\s*[-–]\s*\d{1,2}:\d{2})?)\s*[-–:|]?\s*(?P<title>.+)$")


def _txt(value: Any, limit: int = _ITEM_TEXT_MAX) -> str:
    if value is None or isinstance(value, dict | list | bool):
        return ""
    return " ".join(str(value).split())[:limit]


def _first(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def _as_list(obj: Any, depth: int = 0) -> list[Any] | None:
    """Danh sách mục trong kết quả: chính nó nếu là list, hoặc dict có items/events/messages/tasks… (lồng tối đa 2
    cấp). Danh sách Google Tasks theo từng tasklist ({"tasklists": [{"tasks": [...]}]}) được trải phẳng."""
    if isinstance(obj, list):
        return obj
    if not isinstance(obj, dict) or depth > 2:
        return None
    for k in _LIST_KEYS:
        v = obj.get(k)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            inner = _as_list(v, depth + 1)
            if inner is not None:
                return inner
    for k in ("tasklists", "taskLists", "lists"):
        lists = obj.get(k)
        if isinstance(lists, list):
            flat: list[Any] = []
            for tl in lists:
                if isinstance(tl, dict) and isinstance(tl.get("tasks"), list):
                    flat.extend(tl["tasks"])
            return flat
    return None


def _raw_items(result: Any) -> list[Any]:
    """Mục thô từ kết quả MCP, chịu nhiều dạng: `structuredContent` (list hoặc dict có items/events/messages/tasks),
    `content[].text` là JSON, hoặc văn bản thường (mỗi dòng một mục)."""
    lines: list[str] = []
    if isinstance(result, dict):
        sc = result.get("structuredContent")
        if sc is not None:
            found = _as_list(sc)
            if found is not None:
                return found
        content = result.get("content")
        if isinstance(content, list):
            for c in content:
                text_ = c.get("text") if isinstance(c, dict) else c
                if not isinstance(text_, str):
                    continue
                parsed = _json_or_none(text_)
                if parsed is not None:
                    found = _as_list(parsed)
                    if found is not None:
                        return found
                    continue
                lines.extend(ln for ln in text_.splitlines() if ln.strip())
            return list(lines)
        return _as_list(result) or []
    if isinstance(result, list):
        return result
    if isinstance(result, str):
        parsed = _json_or_none(result)
        if parsed is not None:
            return _as_list(parsed) or []
        return [ln for ln in result.splitlines() if ln.strip()]
    return []


def _norm_event(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        line = raw.strip().lstrip("-*•").strip()
        m = _LINE_EVENT.match(line)
        start, title = (m.group("start"), m.group("title")) if m else ("", line)
        return {"start": _txt(start), "all_day": bool(_DATE_ONLY.fullmatch(start)),
                "title": _txt(title) or "(không có tiêu đề)"}
    if not isinstance(raw, dict):
        return None
    start = _first(raw, "start", "startTime", "start_time", "when")
    all_day = raw.get("allDay") is True or raw.get("all_day") is True
    if isinstance(start, dict):
        dt = _first(start, "dateTime", "datetime")
        if dt:
            start_s = _txt(dt)
        else:
            start_s = _txt(start.get("date"))
            all_day = all_day or bool(start_s)
    else:
        start_s = _txt(start)
        all_day = all_day or bool(_DATE_ONLY.fullmatch(start_s))
    return {"start": start_s, "all_day": all_day,
            "title": _txt(_first(raw, "summary", "title", "name", "subject")) or "(không có tiêu đề)"}


def _person(value: Any) -> str:
    if isinstance(value, dict):
        name, mail = _txt(_first(value, "name", "displayName")), _txt(_first(value, "email", "address"))
        return f"{name} <{mail}>" if name and mail else name or mail
    return _txt(value)


def _mail_date(value: Any) -> str:
    s_ = _txt(value)
    if s_.isdigit() and len(s_) >= 12:  # Gmail internalDate: epoch mili giây
        return datetime.fromtimestamp(int(s_) / 1000, UTC).isoformat().replace("+00:00", "Z")
    return s_


def _norm_mail(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        line = raw.strip().lstrip("-*•").strip()
        return {"id": "", "from": "", "subject": _txt(line), "date": ""} if line else None
    if not isinstance(raw, dict):
        return None
    headers: dict[str, Any] = {}
    payload = raw.get("payload")
    if isinstance(payload, dict) and isinstance(payload.get("headers"), list):
        headers = {str(h.get("name", "")).lower(): h.get("value") for h in payload["headers"] if isinstance(h, dict)}
    item = {"id": _txt(_first(raw, "id", "messageId", "message_id")),
            "from": _person(_first(raw, "from", "sender") or headers.get("from")),
            "subject": _txt(_first(raw, "subject", "title") or headers.get("subject")),
            "date": _mail_date(_first(raw, "date", "receivedAt", "internalDate") or headers.get("date"))}
    return item if item["id"] or item["subject"] or item["from"] else None  # KHÔNG lấy snippet/thân thư


def _norm_task(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        line = raw.strip().lstrip("-*•[] ").strip()
        return {"id": "", "title": _txt(line), "due": ""} if line else None
    if not isinstance(raw, dict):
        return None
    if str(raw.get("status", "")).lower() in ("completed", "done"):
        return None  # "Việc đang mở": bỏ việc đã xong
    title = _txt(_first(raw, "title", "name", "summary"))
    if not title:
        return None
    return {"id": _txt(_first(raw, "id", "taskId")), "title": title,
            "due": _txt(_first(raw, "due", "dueDate", "due_date"))}


_NORMALIZERS: dict[str, Callable[[Any], dict[str, Any] | None]] = {
    "calendar_today": _norm_event, "mail_reply": _norm_mail, "tasks_open": _norm_task}


def normalize_items(kind: str, result: Any) -> list[dict[str, Any]]:
    """Chuẩn hoá kết quả tool Google thành mục gọn cho Bản tin: calendar {start, all_day, title}, mail {id, from,
    subject, date}, tasks {id, title, due}. ≤ 10 mục, bỏ snippet/thân thư. Kết quả luôn qua `mask_for_model`
    (keep_keys=HUB_KEEP_KEYS) — an toàn kể cả khi `result` chưa che."""
    norm = _NORMALIZERS.get(kind)
    if norm is None:
        return []
    items: list[dict[str, Any]] = []
    for raw in _raw_items(result):
        item = norm(raw)
        if item is not None:
            items.append(item)
        if len(items) >= BRIEFING_MAX_ITEMS:
            break
    masked: list[dict[str, Any]] = mask_for_model(items, keep_keys=HUB_KEEP_KEYS)
    return masked


def _brief(scope: str, state: str, items: list[dict[str, Any]] | None = None, *, code: str | None = None,
           detail: str | None = None) -> dict[str, Any]:
    return {"state": state, "items": items or [], "error_code": code,
            "detail": scrub(detail, None)[:200] if detail else None, "scope": scope}


_BRIEF_STATES = {"HUB_LINK_OFF": "off", "HUB_TOOL_MISSING": "missing_scope", "HUB_BREAKER_OPEN": "breaker_open"}


async def briefing_read(sm: Any, redis: Any, *, org_id: uuid.UUID, kind: str, now: datetime,
                        transport: Any = None) -> dict[str, Any]:
    """Một lần đọc Google qua Gen-hub cho Bản tin (gói ban-tin gọi). KHÔNG BAO GIỜ ném (trừ CancelledError).

    Tự mở phiên riêng và commit riêng; đi đúng `call_hub` (đệm, ngắt mạch, ghim DNS, che). Actor là `SystemActor`
    (`actor_type='system'`) và chỉ dùng khi tổ chức có ≥ 1 Owner — Gen KHÔNG đọc thay nhân viên. Trả
    {"state": ok|off|missing_scope|breaker_open|error, "items", "error_code", "detail", "scope"}."""
    spec = BRIEFING_KINDS.get(kind)
    if spec is None:
        return _brief("", "error", code="HUB_BAD_KIND", detail="Loại mục Bản tin không hợp lệ")
    suffix, scope = spec
    if kind == "calendar_today":
        args: dict[str, Any] = {**vn_day_bounds(vn_today(now)), "maxResults": BRIEFING_MAX_ITEMS}
    elif kind == "mail_reply":
        args = {"query": MAIL_REPLY_QUERY, "maxResults": BRIEFING_MAX_ITEMS}
    else:
        args = {}
    try:
        async with sm() as db:
            await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_id)})
            if not await notifications.owner_ids(db, org_id):
                return _brief(scope, "off", code="HUB_NO_OWNER", detail="Tổ chức chưa có Owner để nhận Bản tin")
            res = await call_hub(db, redis, client_for(transport), user=SystemActor(org_id=org_id), suffix=suffix,
                                 args=args)
            await db.commit()
        return _brief(scope, "ok", normalize_items(kind, res["data"]))
    except asyncio.CancelledError:
        raise
    except ApiError as e:
        return _brief(scope, _BRIEF_STATES.get(e.code, "error"), code=e.code,
                      detail=str(e.detail or e.title) if e.code not in _BRIEF_STATES else str(e.title))
    except Exception as e:  # noqa: BLE001 — Bản tin không được hỏng vì Gen-hub/CSDL; chỉ tên lớp lỗi, không nội dung
        log.warning("Bản tin: đọc Gen-hub (%s) lỗi: %s", kind, type(e).__name__)
        return _brief(scope, "error", code="HUB_ERROR",
                      detail=f"Lỗi không mong đợi khi đọc Gen-hub ({type(e).__name__})")


# ─── nhắc token sắp hết hạn (worker, hằng ngày) ───────────────────────────────

async def expiry_scan(db: AsyncSession, redis: Any = None, now: datetime | None = None) -> int:
    """Token còn ≤ 14 ngày (hoặc đã hết) → chuông cho các Owner, MỘT lần cho mỗi token (đặt lại khi đổi token/hạn)."""
    now = now or datetime.now(UTC)
    rows = (await db.execute(text("""
        SELECT org_id, token_expires_at FROM agent.hub_links
        WHERE server_id IS NOT NULL AND token_expires_at IS NOT NULL AND expiry_notified_at IS NULL
          AND token_expires_at <= :lim"""), {"lim": now + timedelta(days=EXPIRY_WARN_DAYS)})).all()
    for r in rows:
        days = (r.token_expires_at - now).days
        expired = r.token_expires_at <= now
        title = "Token Gen-hub đã hết hạn" if expired else f"Token Gen-hub còn {max(days, 0)} ngày"
        body = ("Gen tạm không đọc được Kho. " if expired else "") + \
            "Tạo token mới trong Gen-hub (agent gen-harness-…) rồi dán vào Kết nối › thẻ Gen-hub, bấm Kiểm tra."
        await notifications.notify(db, r.org_id, await notifications.owner_ids(db, r.org_id),
                                   kind="hub.token_expiring", title=title, body=body, link="/connections#genhub",
                                   redis=redis)
        await db.execute(text("UPDATE agent.hub_links SET expiry_notified_at = :n WHERE org_id = :o"),
                         {"n": now, "o": r.org_id})
        await actionlog.record(db, org_id=r.org_id, actor_type="system", actor_id="system:worker",
                               action="hub.token_expiry_notified", target_type="hub_link", target_label=SERVER_NAME,
                               detail={"token_expires_at": iso(r.token_expires_at), "expired": expired})
    return len(rows)
