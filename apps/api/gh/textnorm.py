"""Chuẩn hoá chữ tiếng Việt dùng chung (F-38) — nơi DUY NHẤT bỏ dấu tiếng Việt trong api.

Trước v0.1.43 `gh.refinery.rules` (NFD, bỏ ký tự loại Mn) và `gh.refinery.triage` (NFKD, bỏ ký tự kết hợp) mỗi
nơi có một bản `strip_accents` riêng, lệch nhau ở chữ toàn chiều rộng, 'm²', '…'. Nay cả hai import từ đây.

Giữ NFKD (đúng bản của triage): `refinery.item_marks.text_hash` đã lưu băm trên chuỗi NFKD — đổi sang NFD sẽ làm lọc
trùng 'exact' lệch với các dấu đã lưu. Ngoại lệ có chủ ý: thêm 'Ð' (U+00D0) → 'D' (bản cũ của triage không đổi) nên
`text_hash` chỉ khác với dấu đã lưu ở tin có chứa 'Ð' — lọc trùng 'exact' bỏ sót đúng một lần với các tin đó. Hàm thuần,
không I/O.
"""

import re
import unicodedata

# 'Ð' (U+00D0, chữ Eth của Iceland) hay bị gõ nhầm thay 'Đ' (U+0110); NFKD không tách được cả hai nên thay tay.
_D_MAP = str.maketrans({"đ": "d", "Đ": "D", "Ð": "D"})
_SPACES = re.compile(r"\s+")


def strip_accents(s: str) -> str:
    """Bỏ dấu: đ/Đ/Ð → d/D, NFKD rồi bỏ mọi ký tự kết hợp (dấu thanh, dấu mũ…). Dạng tương thích cũng quy về dạng
    thường (chữ toàn chiều rộng 'ｇ' → 'g', 'm²' → 'm2', '…' → '...')."""
    s = unicodedata.normalize("NFKD", s.translate(_D_MAP))
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def collapse_lower(s: str) -> str:
    """NFC, gộp mọi khoảng trắng thành một dấu cách, bỏ khoảng trắng hai đầu, chữ thường (giữ dấu)."""
    return _SPACES.sub(" ", unicodedata.normalize("NFC", s or "")).strip().lower()


__all__ = ["strip_accents", "collapse_lower"]
