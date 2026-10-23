# ruff: noqa: E501
"""Tải file đa phương tiện qua bridge — an toàn tải file (spec §8.1, §4E).

Quy tắc cứng:
- KHÔNG tải theo URL: lõi chỉ gửi `media.fetch{media_ref}`; `media_ref` có khuôn hẹp (không thể là URL/đường dẫn). Bridge là bên
  duy nhất chạm nền tảng, bằng phiên đang có.
- Giới hạn kích thước (voice ≤ 5 MB hoặc ≤ 10 phút, ảnh ≤ 8 MB) và MIME allowlist, kiểm cả khai báo lẫn byte thật (magic
  bytes): bridge nói gì cũng KHÔNG tin — kích thước, sha256 và loại được kiểm lại ở đây.
- Không ghi `media_ref`, token hay URL vào log; lỗi chỉ mang MÃ + câu thân thiện.
- Tên tệp lưu kho luôn do hệ thống đặt (không lấy tên do người gửi), `safe_filename` chống `../`, ký tự điều khiển.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any

import orjson

from gh.chassis.bus import BRIDGE_CONTROL, EventBus

MAX_VOICE_BYTES = 5 * 1024 * 1024
MAX_VOICE_SECONDS = 10 * 60
MAX_IMAGE_BYTES = 8 * 1024 * 1024
FETCH_TIMEOUT_S = 30.0

KINDS = ("voice", "image")

ALLOWED_MIME: dict[str, frozenset[str]] = {
    "voice": frozenset({"audio/ogg", "audio/opus", "audio/mpeg", "audio/mp3", "audio/mp4", "audio/aac", "audio/x-m4a",
                        "audio/m4a", "audio/wav", "audio/x-wav", "audio/amr", "audio/webm"}),
    "image": frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"}),
}

#: Phần mở rộng tên tệp theo MIME ĐÃ KIỂM (không theo tên người gửi).
EXT = {"audio/ogg": "ogg", "audio/opus": "opus", "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/mp4": "m4a",
       "audio/aac": "aac", "audio/x-m4a": "m4a", "audio/m4a": "m4a", "audio/wav": "wav", "audio/x-wav": "wav",
       "audio/amr": "amr", "audio/webm": "webm", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
       "image/gif": "gif"}

#: Cùng khuôn với bridge (`apps/bridge/src/media.js`): <kênh>:<phiên>:<khoá tin>, không có `/ ? % #` ⇒ không phải URL.
REF_RE = re.compile(r"^(whatsapp|zalo):[A-Za-z0-9_-]{1,64}:[A-Za-z0-9_.@=-]{1,120}$")

#: Mã lỗi → câu thân thiện (tiếng Việt có dấu). Mã là khoá ổn định (ghi `clean.media_texts.error_code`).
FRIENDLY: dict[str, str] = {
    "too_large": "File quá lớn nên Gen không đọc (voice tối đa 5 MB hoặc 10 phút, ảnh tối đa 8 MB).",
    "mime_not_allowed": "Loại file này Gen không đọc được (chỉ voice và ảnh thông dụng).",
    "blocked_host": "Gen từ chối tải file này vì địa chỉ tải không an toàn.",
    "not_found": "Gen không còn lấy được file này (bridge đã khởi động lại hoặc tin quá cũ).",
    "session_inactive": "Tài khoản kênh đang ngắt kết nối nên chưa tải được file.",
    "timeout": "Tải file quá lâu nên tạm dừng.",
    "download_failed": "Nền tảng không cho tải file này.",
    "bad_request": "Tin này không có file hợp lệ để đọc.",
    "engine_unavailable": "Chưa bật công cụ đọc voice/ảnh.",
    "engine_error": "Công cụ đọc voice/ảnh báo lỗi với file này.",
    "engine_timeout": "Công cụ đọc voice/ảnh trả lời quá lâu.",
    "gone": "Gen không còn lấy được file này (bridge đã khởi động lại hoặc tin quá cũ).",
    "gave_up": "Gen đã thử nhiều lần vẫn không đọc được file này.",
    "skipped_budget": "Tạm chưa đọc để giữ trần chi phí AI; Gen sẽ đọc khi còn ngân sách.",
}
#: Lỗi KHÔNG đáng thử lại (kết quả không đổi theo thời gian).
#: `not_found` KHÔNG cố định ngay (bridge vừa khởi động lại / tin chưa kịp vào sổ có thể hết lỗi): thử thêm một lần, lần
#: thứ hai chốt thành `gone`. `engine_unavailable` cũng không cố định và không tính lượt thử (xem `jobs`).
FINAL_ERRORS = frozenset({"too_large", "mime_not_allowed", "blocked_host", "bad_request", "gone", "gave_up"})
#: Cửa sổ quét/thử lại của việc nền (giờ) — quá cửa sổ thì tin không còn được nhặt nữa (`will_retry` = False).
RETRY_WINDOW_H = 72
NOT_FOUND_ATTEMPTS = 2


class MediaFetchError(Exception):
    def __init__(self, code: str):
        super().__init__(code)   # thông điệp = mã (không bao giờ kèm URL/khoá)
        self.code = code


def friendly(code: str | None) -> str:
    return FRIENDLY.get(code or "", "Gen chưa đọc được file này.")


def cap_bytes(kind: str) -> int:
    return MAX_VOICE_BYTES if kind == "voice" else MAX_IMAGE_BYTES


def valid_ref(ref: Any) -> bool:
    return isinstance(ref, str) and REF_RE.fullmatch(ref) is not None


def base_mime(mime: Any) -> str:
    return str(mime).split(";")[0].strip().lower() if isinstance(mime, str) else ""


def declared_problem(kind: str, media: Any) -> str | None:
    """Kiểm mô tả KHAI BÁO (trước khi tải): trả mã lỗi hoặc None. `media` = `payload.media` do bridge gắn."""
    if kind not in KINDS:
        return "bad_request"
    m = media if isinstance(media, dict) else {}
    mime = base_mime(m.get("mime"))
    if mime and mime not in ALLOWED_MIME[kind]:
        return "mime_not_allowed"
    try:
        size = int(m.get("size") or 0)
        seconds = int(m.get("seconds") or 0)
    except (TypeError, ValueError):
        return "bad_request"
    if size > cap_bytes(kind):
        return "too_large"
    if kind == "voice" and seconds > MAX_VOICE_SECONDS:
        return "too_large"
    return None


def sniff_mime(data: bytes) -> str | None:
    """Loại thật theo byte đầu (không tin tên/mime do người gửi khai)."""
    h = data[:16]
    if h[:4] == b"OggS":
        return "audio/ogg"
    if h[:3] == b"ID3":
        return "audio/mpeg"
    if len(h) >= 2 and h[0] == 0xFF and (h[1] & 0xE0) == 0xE0:
        # khung đồng bộ MPEG: layer = 00 ⇒ ADTS AAC (0xFFF1/0xFFF9), còn lại ⇒ mp3
        return "audio/aac" if (h[1] & 0x06) == 0 else "audio/mpeg"
    if h[4:8] == b"ftyp":
        return "audio/mp4"
    if h[:4] == b"RIFF" and h[8:12] == b"WAVE":
        return "audio/wav"
    if h[:5] == b"#!AMR":
        return "audio/amr"
    if h[:4] == b"\x1a\x45\xdf\xa3":
        return "audio/webm"
    if h[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if h[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "image/webp"
    if h[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def verified_mime(kind: str, data: bytes, claimed: str = "") -> str:
    """MIME cuối cùng = loại thật theo byte; phải thuộc allowlist của `kind`. Ném `mime_not_allowed`."""
    real = sniff_mime(data)
    if real is None or real not in ALLOWED_MIME[kind]:
        raise MediaFetchError("mime_not_allowed")
    return real


_NAME_BAD = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: Any = None, mime: str = "") -> str:
    """Tên tệp an toàn: chỉ phần cuối (bỏ mọi thư mục `/`, `\\`, `..`), ký tự an toàn, ≤ 80 ký tự; đuôi theo MIME ĐÃ KIỂM."""
    raw = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem = _NAME_BAD.sub("_", raw.rsplit(".", 1)[0] if "." in raw else raw).strip("._-")[:60] or "media"
    return f"{stem}.{EXT.get(mime, 'bin')}"


@dataclass
class Fetched:
    data: bytes
    mime: str
    sha256: str
    size: int


def result_key(request_id: str) -> str:
    return f"gh:media:res:{request_id}"


async def fetch(bus: EventBus, org_id: uuid.UUID, *, kind: str, media_ref: str, chat: str, sender: str, timeout: float = FETCH_TIMEOUT_S,  # noqa: ASYNC109 — hạn BLPOP phía Redis
                request_id: str | None = None) -> Fetched:
    """`media.fetch{media_ref} -> bytes` qua bus. Ném `MediaFetchError(mã)`; mọi kiểm tra chạy LẠI ở phía lõi."""
    # `chat` ('' = tin 1-1) + `sender` = ràng buộc ghi lúc nhận tin; bridge đối chiếu với mục sổ của ref (chống tráo media).
    if kind not in KINDS or not valid_ref(media_ref) or not isinstance(chat, str) or not isinstance(sender, str) or not sender:
        raise MediaFetchError("bad_request")
    rid = request_id or str(uuid.uuid4())
    channel, session_id, _ = media_ref.split(":", 2)
    await bus.publish(BRIDGE_CONTROL, "media.fetch",
                      {"channel": channel, "session_id": session_id, "request_id": rid, "media_ref": media_ref,
                       "external_group_id": chat, "sender_external_id": sender},
                      actor="media", org_id=org_id, correlation_id=rid)
    key = result_key(rid)
    try:
        item = await bus.redis.blpop([key], timeout=max(1, int(timeout)))  # type: ignore[misc]
    except (TimeoutError, asyncio.TimeoutError):  # noqa: UP041
        item = None
    if item is None:
        raise MediaFetchError("timeout")
    try:
        await bus.redis.delete(key)
    except Exception:  # noqa: BLE001 — khoá tự hết hạn sau 120 giây
        pass
    try:
        res = orjson.loads(item[1])
    except orjson.JSONDecodeError:
        raise MediaFetchError("download_failed") from None
    if not isinstance(res, dict) or res.get("request_id") != rid:
        raise MediaFetchError("download_failed")
    if not res.get("ok"):
        code = str(res.get("error") or "download_failed")
        raise MediaFetchError(code if code in FRIENDLY else "download_failed")
    try:
        data = base64.b64decode(str(res.get("data_b64") or ""), validate=True)
    except (binascii.Error, ValueError):
        raise MediaFetchError("download_failed") from None
    if len(data) > cap_bytes(kind):
        raise MediaFetchError("too_large")        # bridge nói dối về kích thước
    sha = hashlib.sha256(data).hexdigest()
    if res.get("sha256") != sha or int(res.get("size") or -1) != len(data):
        raise MediaFetchError("download_failed")
    mime = verified_mime(kind, data, base_mime(res.get("mime")))
    return Fetched(data=data, mime=mime, sha256=sha, size=len(data))
