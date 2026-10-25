# ruff: noqa: E501
"""Mẫu chữ đáng ngờ dùng chung (v0.1.45, F-60).

- `SUSPICIOUS`: chữ thường gặp trong lừa đảo/tấn công prompt trên trang mạng xã hội (chuyển từ gh.social.service —
  hành vi không đổi). Chỉ để gắn cờ, nội dung vẫn là dữ liệu.
- `REVIEW_MANIPULATION`: câu lệnh cho AI hoặc xin điểm mà nhân viên có thể chèn vào tin nhắn để lách điểm đánh giá
  nhân sự. Job tính điểm chỉ quét tin ĐI do chính nhân viên gửi (không quét tin khách) và chỉ GẮN CỜ 'Đáng ngờ' —
  không đổi điểm, không kỷ luật tự động (khoá cứng 2).

v0.1.76 (T7, spec Bước 1 r6.6.1 W5 = r4 P6): `SUSPICIOUS` mở rộng theo r4 W5 mục 3 ("chuyển gấp", "cọc ngay", link rút gọn,
"nhận quà", "cập nhật thông tin"); `PHISHING` (mồi phishing chưa ai nhập — tối đa 🟠, `levels` quyết), `short_link_domain`/`domain_of`
(khoá vụ link theo domain), `mask_secrets` (che khi HIỂN THỊ/ghi log: OTP, mật khẩu, số thẻ, CVV, token — Q9: chữ gửi model KHÔNG che;
STK chỉ giữ 3 số cuối như r4 "0123…789" để phòng tài khoản Zalo của Sếp bị chiếm).
"""

import re
from collections.abc import Iterable

from gh.textnorm import strip_accents

SUSPICIOUS = re.compile(
    r"(bỏ qua (mọi |các )?(chỉ dẫn|hướng dẫn|lệnh)|ignore (all |previous |the above )?instructions|system prompt|"
    r"mã (otp|xác (minh|nhận))|\botp\b|chuyển (tiền|khoản)|mật khẩu|password|gửi (mã|tiền)|click (vào )?link|"
    r"chuyển gấp|cọc ngay|nhận quà|cập nhật (thông tin|tài khoản)|\b(bit\.ly|tinyurl\.com|cutt\.ly|t\.ly|rb\.gy|is\.gd|shorturl\.at)/)",
    re.IGNORECASE)

# Hẹp có chủ ý (sửa review v0.1.45): chỉ bắt câu NHẮM VÀO bộ chấm điểm — lệnh cho AI, hoặc xin điểm có chữ "điểm".
# KHÔNG bắt câu bán hàng thường ngày: "cho em 10 cái áo", "chị cho em tốt nhé", "em đánh giá cao sản phẩm",
# "bạn là ai vậy?", "chị đánh giá shop 5 sao giúp em" (test_people_suspicious_v0145 giữ danh sách câu không được khớp).
REVIEW_MANIPULATION = re.compile(
    r"(bỏ qua (mọi |các |tất cả )?(chỉ dẫn|hướng dẫn|lệnh)"
    r"|ignore (all |previous |the above |all previous )?(instructions|prompts?)"
    r"|system prompt|prompt hệ thống"
    r"|(chấm|cho|đánh giá) (tôi |em |mình |anh |chị )?điểm (tôi |em |mình )?(thật )?(cao|tốt|tối đa|10|100)\b"
    r"|(chấm|cho) (tôi|em|mình) (10|100) điểm"
    r"|rate me\b|give (me )?(a )?(high|perfect|full|10|100) (score|rating)"
    r"|you are (an? )?(ai|language model)\b)",
    re.IGNORECASE)

SNIPPET_MAX = 60


def scan_texts(texts: Iterable[str | None], pattern: re.Pattern[str] = REVIEW_MANIPULATION) -> tuple[int, str | None]:
    """Đếm số tin khớp mẫu + đoạn khớp ĐẦU TIÊN (cắt ≤ 60 ký tự). Không bao giờ trả cả tin nhắn."""
    hits, first = 0, None
    for t in texts:
        if not t:
            continue
        m = pattern.search(t)
        if m is None:
            continue
        hits += 1
        if first is None:
            first = " ".join(m.group(0).split())[:SNIPPET_MAX]
    return hits, first


# ─── v0.1.76 (T7): mồi phishing, domain, che khi hiển thị ─────────────────────────────────────────────────────────────────

#: Dịch vụ rút gọn link thường dùng để giấu trang giả.
SHORT_LINK_DOMAINS = frozenset({"bit.ly", "tinyurl.com", "cutt.ly", "t.ly", "rb.gy", "is.gd", "shorturl.at", "s.id", "goo.gl",
                                "ow.ly", "tiny.cc", "bitly.com", "shorte.st", "v.gd", "lnkd.in", "t.co"})

#: Mồi phishing trên chữ ĐÃ BỎ DẤU, chữ thường (r4 W5 mục 3): chỉ là mồi — chưa ai nhập, chưa giao dịch ⇒ tối đa 🟠.
PHISHING = re.compile(
    r"\b(?:nhan qua|qua tang|tang \d[\d.,]*\s*(?:k|d|dong|vnd|trieu|tr)\b|trung thuong|trung giai|tri an|nhan thuong|"
    r"cap nhat (?:thong tin|tai khoan)|xac minh tai khoan|xac thuc tai khoan|tai khoan (?:cua ban |se )?bi (?:khoa|tam khoa|dong bang)|"
    r"dang nhap (?:de|ngay|tai)|bam vao link|click vao link|vao link|truy cap link|nhan ngay|mien phi|"
    r"chuyen gap|coc ngay|khoa the|the bi khoa|hoan tien|nang han muc|vay nhanh|giai ngan)\b")
_URL = re.compile(r"(?:https?://)?(?:www\.)?((?:[a-z0-9-]+\.)+[a-z]{2,})(?:/[^\s]*)?", re.IGNORECASE)
_URL_STRICT = re.compile(r"(?:https?://\S+|www\.\S+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|vn|net|org|xyz|top|click|info|me|io|cc|link|"
                         r"site|online|shop|store|app|ly|gd|at|co|id|in)(?:/\S*)?)", re.IGNORECASE)


def fold(text: str | None) -> str:
    """Bỏ dấu + chữ thường (so khớp mẫu tiếng Việt gõ có/không dấu)."""
    return strip_accents((text or "").lower())


def domain_of(text: str | None) -> str | None:
    """Domain ĐẦU TIÊN trong chữ (chữ thường, bỏ `www.`) — khoá vụ link `(domain, 24 giờ)`. Không có link ⇒ None."""
    m = _URL_STRICT.search(text or "")
    if m is None:
        return None
    d = _URL.match(m.group(0))
    return d.group(1).lower() if d else None


def short_link_domain(text: str | None) -> bool:
    d = domain_of(text)
    return d is not None and d in SHORT_LINK_DOMAINS


def has_link(text: str | None) -> bool:
    return _URL_STRICT.search(text or "") is not None


def phishing_bait(text: str | None) -> bool:
    """Có mồi phishing (chữ mồi kèm link, hoặc link rút gọn)? Chưa ai nhập ⇒ chỉ là mồi (tối đa 🟠)."""
    t = text or ""
    return has_link(t) and (PHISHING.search(fold(t)) is not None or short_link_domain(t))


# Che khi HIỂN THỊ / ghi log (Q9: chỉ che bí mật — OTP, mật khẩu, số thẻ, CVV, token; STK giữ 3 số cuối).
_CVV = re.compile(r"(?i)\b(cvv2?|cvc2?|csc|m[ãa] (?:b[ảa]o m[ậa]t|cvv))\b(\s*(?:l[àa]|:|=)?\s*)(\d{3,4})\b")
_TOKEN = re.compile(r"(?i)\b(token|api[ _-]?key|secret|access[ _-]?key|bearer)\b(\s*(?:l[àa]|:|=)?\s*)([A-Za-z0-9._~+/=-]{6,})")
_LONG_DIGITS = re.compile(r"(?<!\d)(\d(?:[ ._/-]{0,2}\d){5,24})(?!\d)")      # số ngăn bằng " . _ / -" (không mơ hồ)
_PASSWORD = re.compile(r"(?i)(?<![\w])(mk|m[ậa]t kh[ẩa]u|pass(?:word|wd)?|pw|pwd)\b(\s*(?:l[àa]|:|=)?\s*)(?!\[đã che\]|l[àa]\b|\s)(\S+)")
MASK = "[đã che]"


def mask_secrets(text: str | None) -> str:
    """Che OTP / mật khẩu (theo ngữ cảnh từ khoá), CVV, token; mọi dãy ≥ 6 chữ số chỉ giữ 3 số cuối (STK, số thẻ). Dùng cho chữ
    HIỂN THỊ (trích dẫn, tiêu đề) và log — KHÔNG dùng cho chữ gửi model (Q9)."""
    import unicodedata

    from gh.data.common import mask_text

    clean = "".join(ch for ch in (text or "") if unicodedata.category(ch) != "Cf")       # bỏ zero-width trước (chống né che số thẻ)
    out = mask_text(clean, False) or ""
    out = _PASSWORD.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", out)
    out = _CVV.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", out)
    out = _TOKEN.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", out)

    def tail3(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(1))
        return m.group(1) if len(digits) < 6 else "…" + digits[-3:]

    return _LONG_DIGITS.sub(tail3, out)
