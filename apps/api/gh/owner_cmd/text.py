"""Chữ dùng chung của lõi lệnh quản trị (v0.1.71, G5): chống link/@nhắc bấm được, rút gọn dòng, dòng bản tin, địa chỉ
Console. Dời NGUYÊN VĂN từ `gh.telegram.service` (Telegram import lại dưới tên cũ) để kênh quản trị Zalo/WhatsApp dùng
lại — hàm thuần, không I/O, không import đường ghi nào.
"""

import re
from typing import Any

from gh.config import get_settings


def public_url() -> str:
    return get_settings().public_url.rstrip("/")


def line(s: str, limit: int = 160) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= limit else s[:limit - 1] + "…"


_SCHEME_RE = re.compile(r":/{2}")
_HOST_DOT_RE = re.compile(r"(?<=\w)\.(?=[^\W\d_])")
_MENTION_RE = re.compile(r"@(?=\w)")


def defang(s: str) -> str:
    """Chữ KHÔNG tin cậy (tóm tắt AI từ nội dung khách/Kho, dòng đầu của mục, tên việc) ⇒ không để Telegram tự nhận
    thành link/@nhắc bấm được trên điện thoại Sếp: 'https://x.vn' → 'https[:]//x[.]vn', '@ten' → '[@]ten'.
    `parse_mode` trống chưa đủ — Telegram vẫn tự dò URL, tên miền, @username trong văn bản thường."""
    s = _SCHEME_RE.sub("[:]//", s)
    s = _HOST_DOT_RE.sub("[.]", s)
    return _MENTION_RE.sub("[@]", s)


HUB_DOWN = "Gen-hub tạm không trả lời"
#: Mục lỗi lần này (`state` = 'error' — mục nội bộ hoặc Gen-hub): chưa biết có việc hay không, không phải "không có".
UNREAD = "chưa đọc được lần này"
NOTHING = "Không có việc gì cần Sếp xử lý lúc này."
#: Tên ngắn của mục Gen-hub khi gộp một dòng "• Lịch / mail / việc Google: Gen-hub tạm không trả lời".
_HUB_SHORT = {"calendar_today": "lịch", "mail_reply": "mail", "gtasks_open": "việc Google"}


def briefing_item(s: dict[str, Any]) -> str | None:
    """Một dòng của mục bản tin. v0.1.49 (QD-16): mục Gen-hub (`external`) chỉ có SỐ ĐẾM — tiêu đề mail/lịch/việc
    không ra Telegram; ngắt mạch mở ⇒ "Gen-hub tạm không trả lời"; chạm trần (`more`) ⇒ "10+". Mục lỗi (`state` =
    'error', nội bộ hay Gen-hub) ⇒ "chưa đọc được lần này" — luôn có dòng, nên không bao giờ kèm "Không có việc gì…"."""
    count = int(s.get("count") or 0)
    if s.get("state") == "error" and count <= 0:
        return f"• {s['title']}: {UNREAD}"
    if s.get("external"):
        if s.get("state") == "breaker":
            return f"• {s['title']}: {HUB_DOWN}"
        return f"• {s['title']} ({count}{'+' if s.get('more') else ''})" if count > 0 else None
    if count <= 0:
        return None
    return f"• {s['title']} ({count})" + (f": {defang(line(s['lines'][0]))}" if s.get("lines") else "")


def briefing_items(sections: list[dict[str, Any]]) -> list[str]:
    """Các dòng mục; khi MỌI mục Gen-hub (≥ 2) cùng "tạm không trả lời" thì gộp thành MỘT dòng ở chỗ mục đầu tiên."""
    ext = [s for s in sections if s.get("external")]
    merge = len(ext) > 1 and all(s.get("state") == "breaker" for s in ext)
    out: list[str] = []
    merged = False
    for s in sections:
        if merge and s.get("external"):
            if not merged:
                names = " / ".join(_HUB_SHORT.get(str(x.get("key")), str(x.get("title") or "")) for x in ext)
                out.append(f"• {names[:1].upper() + names[1:]}: {HUB_DOWN}")
                merged = True
            continue
        item = briefing_item(s)
        if item is not None:
            out.append(item)
    return out


__all__ = ["HUB_DOWN", "NOTHING", "UNREAD", "briefing_item", "briefing_items", "defang", "line", "public_url"]
