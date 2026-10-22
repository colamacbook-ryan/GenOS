"""Mẫu chữ đáng ngờ dùng chung (v0.1.45, F-60).

- `SUSPICIOUS`: chữ thường gặp trong lừa đảo/tấn công prompt trên trang mạng xã hội (chuyển từ gh.social.service —
  hành vi không đổi). Chỉ để gắn cờ, nội dung vẫn là dữ liệu.
- `REVIEW_MANIPULATION`: câu lệnh cho AI hoặc xin điểm mà nhân viên có thể chèn vào tin nhắn để lách điểm đánh giá
  nhân sự. Job tính điểm chỉ quét tin ĐI do chính nhân viên gửi (không quét tin khách) và chỉ GẮN CỜ 'Đáng ngờ' —
  không đổi điểm, không kỷ luật tự động (khoá cứng 2).
"""

import re
from collections.abc import Iterable

SUSPICIOUS = re.compile(
    r"(bỏ qua (mọi |các )?(chỉ dẫn|hướng dẫn|lệnh)|ignore (all |previous |the above )?instructions|system prompt|"
    r"mã (otp|xác (minh|nhận))|\botp\b|chuyển (tiền|khoản)|mật khẩu|password|gửi (mã|tiền)|click (vào )?link)",
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
