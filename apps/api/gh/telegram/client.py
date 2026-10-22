"""HTTP tới Telegram Bot API chính thức (v0.1.44, F-8c) — getMe, sendMessage, getUpdates.

v0.1.64 (T1/T2, Telegram 2 chiều): thêm `poll_updates` (getUpdates có offset + long-poll, trả update thô để
`gh.telegram.inbound` xác thực) và nút LINK cho `send_message` (`buttons` — chỉ nút mở link, KHÔNG BAO GIỜ nút gọi
lại (callback): trên Telegram không có nút bấm làm gì ngoài mở Console).

- Transport tiêm được (`app.state.telegram_transport` hoặc tham số) như `mcp_transport`; None = httpx thật.
- URL chứa token bot (`/bot<token>/…`) ⇒ KHÔNG BAO GIỜ log/ném chuỗi có URL: mọi ngoại lệ httpx bị bắt và đổi thành
  `TelegramError(code)` (chỉ giữ mã, `from None` để không mang ngoại lệ gốc theo traceback). Logger `httpx`/`httpcore`
  bị hạ xuống WARNING (dòng INFO "HTTP Request: POST https://api.telegram.org/bot<token>/…" của httpx).
- Văn bản thường: KHÔNG `parse_mode` (không Markdown/HTML — chữ của khách/Kho không thành định dạng), tắt xem trước
  link, cắt ≤ `TEXT_MAX` ký tự. Telegram VẪN tự dò URL/tên miền/@username trong văn bản thường ⇒ phần chữ không tin
  cậy phải qua `gh.telegram.service.defang` trước khi xếp vào hộp thư đi.
"""

import logging
from typing import Any

import httpx

API_BASE = "https://api.telegram.org"
TIMEOUT_S = 10.0
TEXT_MAX = 3900

TOKEN_REJECTED = "TELEGRAM_TOKEN_REJECTED"
CHAT_NOT_FOUND = "TELEGRAM_CHAT_NOT_FOUND"
BOT_BLOCKED = "TELEGRAM_BOT_BLOCKED"
RATE_LIMITED = "TELEGRAM_RATE_LIMITED"
UNREACHABLE = "TELEGRAM_UNREACHABLE"
#: 400 không phải lỗi chat (vd văn bản rỗng) — chỉ hỏng đúng tin đó, không phải cấu hình.
BAD_REQUEST = "TELEGRAM_BAD_REQUEST"
#: 409 từ getUpdates — bot đang có webhook hoặc nơi khác đang nhận tin của cùng bot (v0.1.64).
POLL_CONFLICT = "TELEGRAM_POLL_CONFLICT"
#: Thời gian chờ HTTP của getUpdates = `timeout` long-poll + khoảng này.
POLL_SLACK_S = 10.0


def quiet_http_logs() -> None:
    """httpx ghi URL đầy đủ (có token) ở mức INFO — hạ `httpx`/`httpcore` xuống WARNING."""
    for name in ("httpx", "httpcore"):
        lg = logging.getLogger(name)
        # Đặt mức RIÊNG (không dựa vào root): root hạ xuống DEBUG/INFO sau này cũng không làm lộ URL.
        if lg.level == logging.NOTSET or lg.level < logging.WARNING:
            lg.setLevel(logging.WARNING)


# Hạ ngay khi nạp module — kể cả tiến trình chưa gọi gh.app.configure_logging (test, CLI).
quiet_http_logs()


class TelegramError(Exception):
    """Lỗi gọi Telegram — chỉ mang MÃ (không URL, không thân phản hồi). `retry_after` (giây) khi bị giới hạn tốc độ."""

    def __init__(self, code: str, retry_after: int | None = None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after

    def __repr__(self) -> str:
        return f"TelegramError({self.code!r}, retry_after={self.retry_after!r})"


def _classify(status: int, body: Any) -> TelegramError:
    desc = str(body.get("description") or "").lower() if isinstance(body, dict) else ""
    if status in (401, 404):
        return TelegramError(TOKEN_REJECTED)
    if status == 403:
        return TelegramError(BOT_BLOCKED)
    if status == 429:
        params = body.get("parameters") if isinstance(body, dict) else None
        retry = params.get("retry_after") if isinstance(params, dict) else None
        ok = isinstance(retry, int) and not isinstance(retry, bool) and retry > 0
        return TelegramError(RATE_LIMITED, retry if ok else 30)
    if status == 409:
        return TelegramError(POLL_CONFLICT)
    if status == 400:
        return TelegramError(CHAT_NOT_FOUND if "chat not found" in desc else BAD_REQUEST)
    return TelegramError(UNREACHABLE)


class TelegramClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, base: str = API_BASE):
        self.transport = transport
        self.base = base.rstrip("/")

    async def _call(self, token: str, method: str, payload: dict[str, Any] | None = None, *,
                    http_timeout: float = TIMEOUT_S) -> Any:
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=http_timeout) as c:
                url = f"{self.base}/bot{token}/{method}"
                r = await (c.post(url, json=payload) if payload is not None else c.get(url))
                try:
                    body = r.json()
                except ValueError:
                    body = None
        except httpx.HTTPError:
            raise TelegramError(UNREACHABLE) from None
        if r.status_code == 200 and isinstance(body, dict) and body.get("ok") is True:
            return body.get("result")
        raise _classify(r.status_code, body) from None

    async def get_me(self, token: str) -> dict[str, Any]:
        result = await self._call(token, "getMe")
        return result if isinstance(result, dict) else {}

    async def send_message(self, token: str, chat_id: str, text: str,
                           buttons: list[tuple[str, str]] | None = None) -> None:
        """`buttons` = [(chữ nút, URL)] — mỗi nút một hàng, CHỈ nút link (mở Console), không có nút gọi lại."""
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:TEXT_MAX], "disable_web_page_preview": True}
        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": label[:64], "url": url}] for label, url in buttons]
            }
        await self._call(token, "sendMessage", payload)

    async def poll_updates(
        self, token: str, offset: int, *, timeout_s: int = 0, limit: int = 20
    ) -> list[dict[str, Any]]:
        """getUpdates có `offset` (xác nhận mọi update_id < offset) — update THÔ, chỉ loại `message`. `timeout_s` > 0 =
        long-poll (Telegram giữ kết nối tới khi có tin hoặc hết giờ). Việc xác thực là của `gh.telegram.inbound`."""
        result = await self._call(
            token, "getUpdates",
            {"offset": offset, "timeout": timeout_s, "limit": limit, "allowed_updates": ["message"]},
            http_timeout=timeout_s + POLL_SLACK_S)
        return [u for u in (result if isinstance(result, list) else []) if isinstance(u, dict)]

    async def get_updates(self, token: str) -> list[dict[str, Any]]:
        """Các chat RIÊNG (private) đã nhắn bot, mới nhất trước: [{chat_id, name, username}]. Không gửi `offset`
        (không xác nhận tin) — lần tìm sau vẫn thấy."""
        result = await self._call(token, "getUpdates")
        chats: list[dict[str, Any]] = []
        seen: set[str] = set()
        for upd in reversed(result if isinstance(result, list) else []):
            if not isinstance(upd, dict):
                continue
            msg = next((upd[k] for k in ("message", "edited_message", "my_chat_member")
                        if isinstance(upd.get(k), dict)), None)
            chat = msg.get("chat") if msg else None
            if not isinstance(chat, dict) or chat.get("type") != "private":
                continue
            cid = chat.get("id")
            if not isinstance(cid, int) or isinstance(cid, bool) or str(cid) in seen:
                continue
            seen.add(str(cid))
            name = " ".join(str(chat.get(k)) for k in ("first_name", "last_name") if chat.get(k)).strip()
            username = chat.get("username")
            chats.append({"chat_id": str(cid), "name": name[:100] or str(cid),
                          "username": username[:64] if isinstance(username, str) and username else None})
        return chats


def client_for(transport: Any = None) -> TelegramClient:
    return TelegramClient(transport=transport)
