"""Bộ lọc an toàn ĐẦU RA, tất định, chạy SAU model (model không tắt được). Hàm thuần — không I/O, không gọi model.

Mã luật (ghi `ops.auto_sends.guard`, KHÔNG ghi nội dung): G-MONEY, G-CONTRACT, G-PERSONNEL, G-PII, G-OTHER-PERSON,
G-MENTION, G-LINK, G-LEAK, G-LEN, G-EMPTY. Chuẩn hoá trước khi so (bỏ dấu tiếng Việt, NFKC, chữ số Unicode → ASCII, gộp
khoảng trắng, nối chuỗi ký tự rời «g.i.á», «1 5 0 k») để chống lách.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from gh.owner_channel.basis import Basis
from gh.textnorm import strip_accents

SUPPORT_MAX_LEN = 600
SUPPORT_MAX_LINES = 8
OWNER_TAG_MAX_LEN = 1500
OWNER_DM_MAX_LEN = 3900


@dataclass(frozen=True)
class GuardCtx:
    basis: Basis
    #: tên (đã bỏ dấu, chữ thường, ≥ 2 từ) và số điện thoại (chỉ chữ số) của người KHÔNG thuộc nhóm này
    known_other_names: frozenset[str] = field(default_factory=frozenset)
    known_other_phones: frozenset[str] = field(default_factory=frozenset)
    #: tên (bỏ dấu, chữ thường) được phép @ — thường chỉ là người hỏi
    allowed_mentions: frozenset[str] = field(default_factory=frozenset)
    allowed_hosts: frozenset[str] = field(default_factory=frozenset)
    canary: str = ""
    max_len: int | None = None


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    codes: tuple[str, ...] = ()


_SEP = r"[\s.\-_*/,·]"
#: Chuỗi ≥ 3 ký tự đơn lẻ (chữ/số) ngăn cách bởi dấu: «g . i . a», «1 5 0 k», «0 9 0 1…» ⇒ nối lại.
_RUN = re.compile(rf"(?<![0-9a-z])[0-9a-z](?:{_SEP}+[0-9a-z]){{2,}}(?![0-9a-z])")
_WS = re.compile(r"\s+")
_ASCII_DIGITS = {ord(c): str(i) for i in range(10) for c in [chr(0x0660 + i)]}   # chữ số Ả Rập-Ấn Độ


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).translate(_ASCII_DIGITS)
    out = []
    for ch in t:                                   # mọi chữ số Unicode (Nd) → ASCII
        out.append(str(unicodedata.digit(ch)) if unicodedata.category(ch) == "Nd" and ch.isdigit() else ch)
    return _WS.sub(" ", strip_accents("".join(out)).lower()).strip()


def _join_runs(t: str) -> str:
    return _RUN.sub(lambda m: re.sub(_SEP, "", m.group(0)), t)


def _forms(text: str) -> list[str]:
    plain = _norm(text)
    joined = _join_runs(plain)
    return [plain, joined] if joined != plain else [plain]


def _any(patterns: list[re.Pattern[str]], forms: list[str]) -> bool:
    return any(p.search(f) for p in patterns for f in forms)


def _c(*pats: str) -> list[re.Pattern[str]]:
    return [re.compile(p) for p in pats]


_MONEY = _c(
    r"\bgia\b", r"\bbao gia\b", r"\bchiet khau\b", r"\bgiam gia\b", r"\bkhuyen mai\b", r"\bvnd\b", r"\busd\b",
    r"\beur\b", r"[$€£]", r"₫", r"\d\s*%",
    r"\d[\d.,]*\s*(?:k|tr|trieu|nghin|ngan|ty|d|dong|vnd|usd)\b",
    # v0.1.72 (review bảo mật): từ khoá tiền, «2tr5», «2 củ», số lớn, số viết bằng chữ.
    r"\bphi\b", r"\btien\b", r"\bthanh toan\b", r"\bphan tram\b", r"\d\s*(?:cu|tr)\d*\b",
    r"\b(?:tram|nghin|ngan|trieu|ty)\b",
)
#: chỉ chạy trên bản đã bỏ ngày tháng / «năm 2026» (số 4 chữ số thường là năm trong FAQ).
_MONEY_NUM = _c(r"(?<![\d.,])\d{4,}(?![\d])", r"(?<![\d.,])\d{1,3}(?:[.,]\d{3})+(?![\d])")
_DATE = re.compile(r"\b\d{1,2}\s*[/.\-]\s*\d{1,2}\s*[/.\-]\s*\d{2,4}\b|\bnam\s+\d{4}\b|\b\d{1,2}\s*[/.\-]\s*\d{4}\b")
_CONTRACT = _c(r"\bhop dong\b", r"\bcam ket\b", r"\bbao dam\b", r"\bdam bao\b", r"\bboi thuong\b", r"\bhoan tien\b",
               r"\bbao hanh\b", r"\bhoan lai\b", r"\bcam doan\b",
               r"\bdat coc\b", r"\bdieu khoan\b", r"\bhua\b")
_PERSONNEL = _c(r"\bluong\b", r"\bky luat\b", r"\bsa thai\b", r"\bnghi viec\b", r"\bkpi\b", r"\bnhan su\b",
                r"\bung vien\b", r"\bdanh gia nhan vien\b")
#: thưởng / tuyển phải giữ dấu: bỏ dấu thì trùng «thường», «tuyên» (từ rất thường gặp).
_PERSONNEL_ACCENT = _c(r"thưởng", r"tuyển")
_PHONE = re.compile(r"(?<!\d)(?:\+?84|0)\d{8,10}(?!\d)")
_LONGNUM = re.compile(r"(?<!\d)\d{9,}(?!\d)")
_PII = _c(r"[\w.+-]+@[\w-]+\.[\w.-]+", r"\b(?:so|sn)\s*\d+[a-z]?\s*(?:duong|pho|ngo|ngach|hem|ph)\b",
          r"\b\d+[a-z]?\s*(?:ngo|ngach|hem|duong|pho)\b")
_MENTION = re.compile(r"@([^\s@]+)")
_URL = re.compile(r"(?:https?://|www\.)([^\s/]+)|\b((?:[a-z0-9-]+\.)+(?:com|vn|net|org|io|me|app|co|info|xyz|link|ly))\b")
#: tên miền bất kỳ (đuôi chữ ≥ 2 ký tự — «1.5», «v0.1» không khớp) sau khi gỡ che giấu «[.]», «(.)», «{.}».
_DOMAIN = re.compile(r"(?<![\w@.-])((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,})(?![\w-])")
_DEFANG = re.compile(r"\s*[\[(\{]\s*\.\s*[\])\}]\s*")
_LEAK = _c(r"system prompt", r"loi nhac he thong", r"```", r"^\s*[\[{]", r"\"\w+\"\s*:")


def _digits_only(t: str) -> str:
    return re.sub(r"[\s.\-_,()]", "", t)


def check(text: str, ctx: GuardCtx) -> GuardResult:
    """Kiểm một câu trả lời sắp gửi. `ok` chỉ khi KHÔNG luật nào bắt. Kết quả chỉ chứa mã luật."""
    raw = text or ""
    basis = ctx.basis
    hit: list[str] = []
    if not raw.strip():
        return GuardResult(False, ("G-EMPTY",))
    limit = ctx.max_len or {Basis.OWNER_DM: OWNER_DM_MAX_LEN, Basis.OWNER_TAG: OWNER_TAG_MAX_LEN}.get(
        basis, SUPPORT_MAX_LEN)
    if len(raw) > limit or (basis == Basis.SUPPORT and len(raw.strip().splitlines()) > SUPPORT_MAX_LINES):
        hit.append("G-LEN")
    forms = _forms(raw)
    low = unicodedata.normalize("NFC", raw).lower()
    canary_hit = bool(ctx.canary) and ctx.canary.lower() in low
    leak = canary_hit or _any(_c(r"system prompt", r"loi nhac he thong"), forms)
    if basis != Basis.OWNER_DM:
        leak = leak or _any(_LEAK, forms) or _any(_LEAK[:3], [low]) or bool(re.search(r"^\s*[\[{]", raw))
    if leak:
        hit.append("G-LEAK")
    if basis == Basis.OWNER_DM:                      # nói với chính Sếp: chỉ rỗng / dài / lộ lời nhắc
        return GuardResult(not hit, tuple(hit))
    nodate = [_DATE.sub(" ", f) for f in forms]
    if _any(_MONEY, forms + [raw.lower()]) or _any(_MONEY_NUM, nodate):
        hit.append("G-MONEY")
    if _any(_CONTRACT, forms):
        hit.append("G-CONTRACT")
    if _any(_PERSONNEL, forms) or any(p.search(low) for p in _PERSONNEL_ACCENT):
        hit.append("G-PERSONNEL")
    digits = [_digits_only(f) for f in forms]
    if (_any(_PII, forms) or any(_PHONE.search(d) or _LONGNUM.search(d) for d in digits)):
        hit.append("G-PII")
    plain = forms[0]
    if (any(re.search(rf"\b{re.escape(n)}\b", plain) for n in ctx.known_other_names if n)
            or any(p and p in d for p in ctx.known_other_phones for d in digits)):
        hit.append("G-OTHER-PERSON")
    if any(_norm(m.group(1)).strip(".,!?:;") not in ctx.allowed_mentions for m in _MENTION.finditer(raw)):
        hit.append("G-MENTION")
    hosts = {(m.group(1) or m.group(2) or "").lower() for m in _URL.finditer(raw)} - {""}
    for f in {_DEFANG.sub(".", forms[0]), _DEFANG.sub(".", raw.lower())}:
        hosts |= {m.group(1).removeprefix("www.") for m in _DOMAIN.finditer(f)}
    if any(h not in ctx.allowed_hosts for h in hosts):
        hit.append("G-LINK")
    return GuardResult(not hit, tuple(dict.fromkeys(hit)))
