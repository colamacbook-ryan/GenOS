"""Tách lệnh khỏi tin của Owner (v0.1.71, G5) — dùng chung Telegram, Zalo, WhatsApp. Hàm thuần.

`/nhap  gọi A` → ("nhap", "gọi A"); `/Hom_Nay@gen_bot` → ("hom_nay", ""); chữ thường (không bắt đầu bằng '/') →
("hoi", cả câu). Tên lệnh chỉ chữ/số/gạch dưới (≤ 32), hậu tố `@tên_bot` của Telegram được bỏ.
"""

import re

#: Độ dài tối đa của tin Owner được xét (Telegram `TEXT_MAX`); phần dư bị cắt trước khi tách.
TEXT_MAX = 4000
_CMD_RE = re.compile(r"^/([A-Za-z][A-Za-z0-9_]{0,31})(?:@[A-Za-z0-9_]{3,64})?(?:\s+(.*))?$", re.S)


def parse_text(raw: str, text_max: int = TEXT_MAX) -> tuple[str, str]:
    """Trả (kind, phần sau tên lệnh). Câu thường ⇒ ("hoi", cả câu)."""
    raw = raw.strip()[:text_max]
    m = _CMD_RE.match(raw)
    if m is None:
        return "hoi", raw
    return m.group(1).lower(), (m.group(2) or "").strip()


__all__ = ["TEXT_MAX", "parse_text"]
