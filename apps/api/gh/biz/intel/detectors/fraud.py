# ruff: noqa: E501
"""W5 Rủi ro & lừa đảo (gói T7; spec Bước 1 r6.6.1 §6.2 W5 = r4 P6 + r6: chạy trên OCR, mạo danh qua `mem.aliases`, mâu thuẫn
`payment_account` qua `mem.disputes`, cổng `lua_dao_dang_dien_ra` 3 nhánh, (d2) fail-high, `payment_identifiers.verified_*`).

Detector KHÔNG gán mức: chỉ dựng `levels.SignalIn` (cờ + luật cứng + người) rồi gọi `signals.upsert`; `levels.compute` quyết 🔴/🟠/⚪.

Luật tất định (0 token) trên chữ tin (`body`) **và** chữ OCR/transcript (`clean.media_texts`), cộng luật (a) của L2 (`prefilter.scan_text`):
- `stk_change` — STK (8–19 số gần nhãn STK/ngân hàng/chuyển khoản) + yêu cầu trả tiền vào số đó. So với LỊCH SỬ của chủ STK (băm có khoá
  `facts.payment_digest` = `crypto.keyed_digest`; nguồn: `mem.facts payment_account` còn hiệu lực + `biz.payment_identifiers`): khác lịch sử
  ⇒ luật cứng `stk_khac_lich_su+money_request`; trùng STK Sếp đã XÁC MINH (`verified_at`) ⇒ an toàn, không báo; STK lần đầu, không chữ "đổi"
  ⇒ chỉ ghi lịch sử (⚪, không tín hiệu). Chống đầu độc lịch sử: STK chỉ "đã biết" khi Sếp xác minh / `owner_confirmed`, hoặc dùng ≥ 7 ngày
  và chưa dính tín hiệu W5; `payment_account` chỉ ghi cho NGƯỜI GỬI đã biết (không người lạ, không mạo danh — STK kẻ mạo danh không bao giờ vào
  sự thật của người bị mạo danh), tin đã kích luật chỉ ghi khi ra `disputed`. L1 mở `mem.disputes` khi khác giá trị; detector nhận tranh chấp
  đó (gắn `projected_signal_id` = tín hiệu của mình, để T1 `project_disputes` không chiếu lại thành tín hiệu thứ hai yếu hơn).
- `mao_danh_xin_tien` — tên hiển thị người gửi khớp CHÍNH XÁC bí danh (`mem.aliases`) / tên thành viên của một người KHÁC đã biết, ID gửi mới
  (< 7 ngày trong nhóm, hoặc chưa có lịch sử tin ≥ 7 ngày) ⇒ `impersonated`; kèm xin tiền/đổi STK ⇒ `account_takeover` + luật cứng; không kèm ⇒ 🟠.
- `nguoi_la_xin_tien` — người lạ (vào nhóm < 7 ngày / chưa có lịch sử) nhờ ứng / mượn / chuyển giúp tiền ⇒ `money_request` + luật cứng.
- `don_lon_khong_coc` — người lạ đặt đơn lớn, không cọc, giục gửi ngay ⇒ Sếp (bên bán) là nạn nhân, `money_request` (giao hàng = giao giá trị
  chưa nhận tiền) + luật cứng.
- `credentials_entered` — luật (a) L2 + mẫu "đã điền form rồi, nó hỏi số thẻ/OTP" (kb1 S28) ⇒ luật cứng.
- `unc` — ảnh/tin "chuyển khoản thành công" ⇒ `txn_state=just_done` (nhánh "vừa chuyển").
- `phishing` — mồi link (`suspicious.phishing_bait`) chưa ai nhập ⇒ không cờ tiền/OTP ⇒ tối đa 🟠 (`levels`).

Mọi luật cứng ⇒ `confidence_exist = 1,00` bất kể (d2) (§4.2.1 (b)0): 🔴 lừa đảo không bao giờ bị hạ khi chưa có xác nhận an toàn của Sếp
("Đã xử lý", mute PIN, hoặc STK đã xác minh — `verify_identifier`). Ứng viên KHÔNG có luật cứng chạy (d2) TRƯỚC khi ghi (không có 🔴 nào
bị hạ sau khi đã đẩy); có luật cứng thì ghi ngay rồi mới (d2) (đẩy nhanh; (d2) chỉ thêm `d2_disagree`/cờ). (d2) lỗi ⇒ giữ nguyên (fail-high).
"Một vụ một 🔴": `levels.case_key` (STK / người bị mạo danh / domain / người gửi theo ngày); vụ đã có tín hiệu của đường nhanh `raw_tail`
thì dùng lại khoá của tín hiệu đó (`facts.fraud_case`).

Bảo mật: số STK/OTP/thẻ không bao giờ vào DB (`biz.alerts`, `ops.asks`, `mem.facts`, `biz.payment_identifiers` chỉ có băm + 3 số cuối),
log, Nhật ký hay thân lỗi. Log chỉ có mã tin + mã luật + tên lỗi. Mọi truy vấn lọc theo `org_id`.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import suspicious
from gh.biz.hooks import CronJob, Hook
from gh.biz.intel import levels, signals
from gh.textnorm import strip_accents

log = logging.getLogger("gh.intel.fraud")

DETECTOR = "fraud"
VERSION = "1"
FR = levels.FR
WINDOW = timedelta(minutes=30)            # tin mới (thân tin)
MEDIA_WINDOW = timedelta(hours=24)        # chữ OCR/transcript tới trễ cho tin ≤ 24 giờ
SCAN_LIMIT = 400
MAX_D2_PER_RUN = 5
FETCH_FACTOR = 5                          # lấy tối đa SCAN_LIMIT × 5 ứng viên (mới nhất trước) rồi lọc theo watermark
RUN_BUDGET_S = 30.0                       # trần thời gian MỘT lượt quét một tổ chức; còn lại để lượt sau (tin mới vẫn được ưu tiên)
GROUP_WINDOW = timedelta(minutes=10)      # gom tin chia nhỏ: ≤ 3 tin liên tiếp của CÙNG người trong CÙNG luồng trong 10 phút
GROUP_MAX = 3
SEEN_TTL_S = 2 * 86400
STRANGER_DAYS = 7.0
BIG_ORDER_VND = 5_000_000
BIG_ORDER_QTY = 10
SOURCE_FIELDS = ("body", "ocr", "transcript")
WINDOW_CHARS = 4000                       # cỡ MỘT cửa sổ quét (regex chạy trên cửa sổ, không trên cả tin dài)
WINDOW_OVERLAP = 400                      # chồng lấn giữa hai cửa sổ: mẫu nằm vắt qua ranh giới vẫn trọn trong một cửa sổ
MAX_SCAN_CHARS = 20_000                   # trần TỔNG = ngưỡng của prefilter (L2); đệm phía trước không đẩy được nội dung ra khỏi vùng quét

# Mã luật cứng (đi vào `SignalIn.hard_rule_hit`; `levels` coi mọi mã là luật cứng: exist 1,00, (d2) không hạ).
H_CRED = "credentials_entered"
H_STK = "stk_khac_lich_su+money_request"
H_IMP = "mao_danh_xin_tien"
H_STRANGER = "nguoi_la_xin_tien"
H_GOODS = "don_lon_khong_coc"
H_STK_CLAIM = "doi_stk_xin_tien"


class FraudError(ValueError):
    """Lỗi nghiệp vụ của W5 (thân lỗi không bao giờ chứa STK/OTP)."""


# ─── chuẩn hoá: bản bỏ dấu + ánh xạ vị trí về chữ gốc ───────────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=4096)
def _fold_char(ch: str) -> str:
    return strip_accents(ch).lower()


def _fold_map(s: str) -> tuple[str, list[int]]:
    table = {ord(ch): _fold_char(ch) for ch in set(s)}
    if all(len(v) == 1 for v in table.values()):        # trường hợp thường (tiếng Việt): bỏ dấu giữ nguyên độ dài ⇒ một lần translate
        return s.translate(table), list(range(len(s)))
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(s):
        f = _fold_char(ch)
        if len(f) == 1:
            out.append(f)
            idx.append(i)
        else:
            for c in f:
                out.append(c)
                idx.append(i)
    return "".join(out), idx


def _orig_span(idx: list[int], n: int, a: int, b: int) -> tuple[int, int]:
    if not idx or a >= len(idx):
        return 0, 0
    return idx[a], (idx[b - 1] + 1) if 0 < b <= len(idx) else n


#: Một từ đệm. KHÔNG mơ hồ (chống ReDoS): phần phân cách và phần chữ là hai lớp ký tự rời nhau, nên mỗi vị trí chỉ có một cách khớp.
_SEPS = r"\s,.:;!?-"
_W = rf"(?:[{_SEPS}]+[^{_SEPS}]+)"


def _g(lo: int, hi: int) -> str:
    return rf"{_W}{{{lo},{hi}}}?"


_BANKS = (r"(?:vcb|vietcombank|techcombank|tcb|mb|mbbank|mb bank|acb|bidv|agribank|agr|vietinbank|vtb|vpbank|tpbank|sacombank|stb|scb|shb|"
          r"hdbank|ocb|msb|vib|seabank|eximbank|lpbank|lienvietpostbank|namabank|abbank|baovietbank|pvcombank|kienlongbank|momo|zalopay|"
          r"viettelpay|vnpay|shinhan|uob|hsbc|cake|timo|tnex)")
_ACCT_LABEL = re.compile(rf"\b(?:stk|so tai khoan|so tk|tai khoan|tk|chuyen khoan|chuyen tien|ck|ngan hang|nh|{_BANKS})\b")
_DIGITS = re.compile(r"(?<![\d.,])(\d(?:[\s._/-]{0,2}\d){7,18})(?![\d])")   # nhóm số ngăn bằng ≤ 2 ký tự " ._/-" (không mơ hồ)
_ORDER_CODE_BEFORE = re.compile(r"(?:ma don|don hang|so don|ma van don|van don|mvd|ma hd|hoa don|ma gd|ma giao dich|ngay|sdt|so dt|dien thoai|"
                                r"hotline|zalo|goi|lh|lien he|cccd|cmnd|mst|ma so thue)\s*(?:hang|so)?\s*[:#.-]?\s*$")
_PHONE = re.compile(r"^(?:84|0)(?:3|5|7|8|9)\d{8}$")
_CHANGE = re.compile(rf"\b(?:doi|thay doi|doi sang|chuyen sang|cap nhat)\b{_g(0, 4)}[\s,]+(?:stk|so tai khoan|tai khoan|so tk|tk|so)\b"
                     rf"|\b(?:stk|so tai khoan|tai khoan|so tk|tk)\b{_g(0, 3)}[\s,]+(?:moi|khac)\b"
                     r"|\b(?:so moi|tk moi|stk moi)\b|\bchuyen vao so nay\b|\bchuyen qua so nay\b|\bsang so nay\b"
                     r"|\b(?:khong dung|bo|ngung dung|dung chuyen vao|thoi dung)\s+(?:so|stk|tai khoan|tk)\s+cu\b"
                     r"|\b(?:so|stk|tai khoan|tk)\s+cu\s+(?:bi khoa|khong dung|khong nhan|loi|dang loi|bi loi)\b")
_PAY_VERB = re.compile(r"\b(?:chuyen khoan|chuyen tien|chuyen|ck|thanh toan|tra tien|tra|gui tien|dong tien|nap|nop|banking|bank)\b")
_ASK_MONEY = re.compile(
    rf"\b(?:ung|ung truoc|tam ung|muon|vay|cho muon|cho vay|chuyen giup|chuyen ho|nho chuyen|gui giup|nap giup|chuyen gap|chuyen truoc|"
    rf"ck giup|ck ho|ck gap|bank giup|(?:giup em|giup minh|giup chau|cuu em|cuu minh)\s+(?:chuyen|ung|muon|vay|ck|gui|nap))\b{_g(0, 5)}[\s,]+(?:tien\b|\d[\d.,]*\s*(?:k|tr|trieu|nghin|ngan|cu|xi|lit|d|dong|vnd|ty)\b)"
    rf"|\b(?:ung|muon|vay)\s+(?:giup\s+|ho\s+|tam\s+|cho\s+)?(?:em|minh|toi|chau|con|anh|chi)?\s*\d")
_ORDER = re.compile(r"\b(?:dat|lay|chot|order|mua|dat hang|chot don|len don|nhap|can mua|lay luon|mua luon)\b")
#: "Không cọc" tường minh. KHÔNG gồm "ship COD" / "nhận hàng rồi thanh toán" — cách mua hằng ngày, báo nhầm không chịu được (review T7 M4).
_NO_DEPOSIT = re.compile(r"\b(?:khong coc|chua coc|khoi coc|coc sau|khong dat coc|ko coc|k coc|khong can coc|chua chuyen coc|"
                         r"gui hang truoc|giao truoc tra sau|cong no|ghi no|thieu tra sau)\b")
_RUSH = re.compile(r"\b(?:gui ngay|giao ngay|ship ngay|gui gap|giao gap|ship gap|xuat hang ngay|xuat ngay|gui lien|giao lien|ship lien|"
                   r"gui luon|giao luon|ship luon|can gap|gap lam|gap gap|ngay trong hom nay|trong hom nay|ngay hom nay|ngay bay gio|"
                   r"len xe ngay|chay ngay|gui di ngay|lam ngay)\b")
_BIG_WORDS = re.compile(r"\b(?:don lon|so luong lon|lay si|hang si|ca lo|nguyen lo|nguyen xe|ca container|full container)\b")
_QTY = re.compile(r"\b(\d{2,6})\s*(?:thung|bao|tan|cai|chiec|may|kg|container|cont|lo|hop|cuon|tam|bo|kien|can|chai|goi|pallet|xe|bich|thanh|"
                  r"cay|met|m2|m3|tui|phan|suat|ly|coc)\b")
_UNC = re.compile(r"\b(?:giao dich thanh cong|chuyen khoan thanh cong|chuyen tien thanh cong|ck thanh cong|chuyen thanh cong|"
                  r"giao dich hoan tat|da chuyen khoan|da ck|da chuyen tien|transfer successful|successful transaction)\b")
#: "đã điền/đăng nhập … rồi" (≤ 10 từ chen giữa — câu thật "đăng nhập bằng tài khoản portal đại lý của mình rồi", T12) rồi bị (a) hỏi/đòi
#: OTP/thẻ/mật khẩu, hoặc (b) "gửi mã OTP … bảo nhập" (bị dụ nhập tiếp mã vừa về máy).
_CRED_SECRET = r"(?:so the|ma the|ma otp|otp|mat khau|cvv|cvc|ma pin|pin|ma xac (?:thuc|minh|nhan)|thong tin the)\b"
_CRED_FORM = re.compile(
    rf"\b(?:dien|nhap|dang nhap|login|dien form|dien thong tin|khai)\b{_g(0, 10)}[\s,]+(?:roi|xong)\b.{{0,80}}?"
    rf"(?:\b(?:hoi|doi|yeu cau|bat|xin|can|bao nhap|bao dien|nhap tiep|dien tiep|hoi them|doi them)\b{_g(0, 4)}[\s,]+{_CRED_SECRET}"
    rf"|\b(?:gui|nhan|ve|bao ve)\b{_g(0, 2)}[\s,]+(?:ma otp|otp|ma xac (?:thuc|minh|nhan)|ma pin)\b.{{0,60}}?"
    r"\b(?:bao nhap|bat nhap|yeu cau nhap|doi nhap|bao dien|bat dien)\b)")
_NEG = re.compile(r"\b(?:chua|khong|ko|dung|dung co|chang)\s+(?:dien|nhap|dang nhap|login|khai)\b")
_ADDRESS = re.compile(r"(?i:(?<![\w])(anh|chị|chi|em|cô|co|chú|chu|bác|bac|ông|ong|bà|ba|sếp|sep|boss|a|c))\s+(\w+(?:\s+\w+)?)")
#: Chủ ngữ ngôi thứ ba ("mẹ em đã nhập") — đồng bộ ý với `prefilter._THIRD` (L2).
_THIRD = re.compile(r"\b(?:me|ba|bo|cha|ong|vo|chong|con trai|con gai|anh trai|chi gai|em trai|em gai|nguoi than|ban|khach|"
                    r"nhan vien|hang xom|co ay|chu ay|anh ay|chi ay|ho)\s+(?:cua\s+)?(?:em|toi|minh|anh|chi|nha|tao|tui)?\s*"
                    r"(?:da|vua|lo|trot|bi|moi|dang|nhap|dien|doc|dua|cung cap|dang nhap|login)\b")


# ─── tầng thuần: quét MỘT đoạn chữ ───────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Account:
    digest: str | None            # băm có khoá (64 hex); None khi thiếu khoá master
    last3: str
    bank: str | None
    span: tuple[int, int]


@dataclass(frozen=True)
class Scan:
    """Kết quả quét MỘT trường chữ. KHÔNG giữ chữ của tin (chỉ span + băm + 3 số cuối)."""
    source_field: str
    accounts: tuple[Account, ...] = ()
    change: tuple[int, int] | None = None
    pay_ask: tuple[int, int] | None = None
    ask_money: tuple[int, int] | None = None
    goods: tuple[int, int] | None = None
    unc: tuple[int, int] | None = None
    cred: tuple[int, int] | None = None
    cred_first_person: bool = True
    takeover: tuple[int, int] | None = None
    phishing: tuple[int, int] | None = None
    domain: str | None = None
    amount_vnd: int | None = None
    addressed: tuple[str, ...] = ()
    pay_verb: tuple[int, int] | None = None       # có động từ trả/chuyển tiền (kể cả khi không đọc được STK)

    @property
    def money_ask(self) -> bool:
        return bool(self.pay_ask or self.ask_money or (self.accounts and self.change))

    @property
    def any(self) -> bool:
        return bool(self.accounts or self.change or self.ask_money or self.goods or self.cred or self.takeover or self.phishing or self.unc)


def _digest(digits: str) -> str | None:
    try:
        from gh.biz.mem import facts

        return facts.payment_digest(digits)
    except Exception:  # noqa: BLE001 — thiếu khoá master không được làm hỏng luật rẻ (không ghi số vào log)
        return None


def _accounts(fold: str, idx: list[int], n: int) -> list[Account]:
    out: list[Account] = []
    for m in _DIGITS.finditer(fold):
        digits = re.sub(r"\D", "", m.group(1))
        if not 8 <= len(digits) <= 19:
            continue
        before = fold[max(0, m.start(1) - 40):m.start(1)]
        after = fold[m.end(1):m.end(1) + 30]
        if _ORDER_CODE_BEFORE.search(before):
            continue                                   # mã đơn / ngày / SĐT có nhãn
        labelled = _ACCT_LABEL.search(before) is not None or re.match(rf"\s*[-(]?\s*{_BANKS}\b", after) is not None
        if not labelled:
            continue
        bank_near = re.search(rf"\b{_BANKS}\b", before[-20:]) is not None or re.match(rf"\s*[-(]?\s*{_BANKS}\b", after) is not None
        if _PHONE.match(digits) and not (bank_near or re.search(r"\b(?:stk|so tai khoan|so tk|tai khoan|tk)\b", before[-20:])):
            continue                                   # trông như SĐT mà không có nhãn STK / ngân hàng / ví sát bên ⇒ SĐT
        bank = re.search(_BANKS, before[-40:] + " " + after)
        out.append(Account(_digest(digits), digits[-3:], bank.group(0).upper()[:16] if bank else None, _orig_span(idx, n, m.start(1), m.end(1))))
    seen: set[str] = set()
    uniq: list[Account] = []
    for a in out:
        k = a.digest or a.last3 + str(a.span)
        if k not in seen:
            seen.add(k)
            uniq.append(a)
    return uniq


#: Số tiền: phần số GIỚI HẠN độ dài (≤ 15 chữ số, hoặc nhóm nghìn ≤ 6 nhóm) — chuỗi số cực dài không bao giờ khớp.
_AMOUNT = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3}){1,5}|\d{1,15}(?:[.,]\d{1,3})?)(?![\d])\s*(ty|ti|tr|trieu|cu|nghin|ngan|k|d|dong|vnd)\b")
_UNIT = {"ty": 1_000_000_000, "ti": 1_000_000_000, "tr": 1_000_000, "trieu": 1_000_000, "cu": 1_000_000, "nghin": 1_000, "ngan": 1_000, "k": 1_000}
AMOUNT_CAP = 10**15
_LONG_RUN = re.compile(r"\d{20,}")
_GROUPED = re.compile(r"\d{1,3}(?:[.,]\d{3})+")


def _vnd(num: str, unit: str) -> int | None:
    """Đổi sang VND bằng `Decimal` (không float ⇒ không inf/OverflowError); trần `AMOUNT_CAP`. Lỗi ⇒ None (bên gọi vẫn đánh giá tin)."""
    if not re.fullmatch(r"\d[\d.,]{0,22}", num or ""):
        return None                                        # chỉ nhận chữ số + dấu nhóm (không "1e999", không chuỗi số cực dài)
    try:
        base = Decimal(re.sub(r"[.,]", "", num)) if _GROUPED.fullmatch(num) else Decimal(num.replace(",", "."))
        v = base * _UNIT.get(unit, 1)
        if not v.is_finite() or v < 0:
            return None
        return int(v) if v <= AMOUNT_CAP else AMOUNT_CAP
    except (InvalidOperation, ValueError, OverflowError):
        return None


def max_amount(fold: str) -> int | None:
    """Số tiền (VND) LỚN NHẤT trong chữ đã bỏ dấu — "50k phí ship … chuyển 50 triệu" ⇒ 50 triệu (chống kéo mức xuống bằng số nhỏ đứng trước)."""
    vals = [v for m in _AMOUNT.finditer(fold) if (v := _vnd(m.group(1), m.group(2)))]
    return max(vals) if vals else None


def _span(m: re.Match[str] | None, idx: list[int], n: int) -> tuple[int, int] | None:
    return _orig_span(idx, n, m.start(), m.end()) if m else None


def _addressed_surfaces(t: str) -> list[str]:
    """Cách gọi "chị Lan", "anh Minh Tuấn" (tên viết hoa sau từ xưng hô) — chỉ để tra `mem.aliases` chính xác."""
    out: list[str] = []
    for m in _ADDRESS.finditer(t):
        words = m.group(2).split()
        names = []
        for w in words:
            if not w[:1].isupper():
                break
            names.append(w)
        if names:
            out.append(f"{m.group(1)} {' '.join(names)}")
    return out


def _windows(n: int) -> list[tuple[int, int]]:
    """Cửa sổ `WINDOW_CHARS` chồng lấn `WINDOW_OVERLAP`, phủ KÍN [0, min(n, MAX_SCAN_CHARS)) — đầu, giữa, cuối đều được quét."""
    end = min(n, MAX_SCAN_CHARS)
    if end <= WINDOW_CHARS:
        return [(0, end)]
    step = WINDOW_CHARS - WINDOW_OVERLAP
    out = [(a, min(a + WINDOW_CHARS, end)) for a in range(0, end - WINDOW_OVERLAP, step)]
    if out[-1][1] < end:
        out.append((max(0, end - WINDOW_CHARS), end))
    return out


def _shift(sp: tuple[int, int] | None, off: int) -> tuple[int, int] | None:
    return None if sp is None else (sp[0] + off, sp[1] + off)


def scan(body: str | None, source_field: str = "body") -> Scan:
    """Luật W5 trên MỘT đoạn chữ (tin / OCR / transcript), quét theo cửa sổ chồng lấn rồi gộp (span theo vị trí trong chữ gốc). Hàm thuần."""
    if source_field not in SOURCE_FIELDS:
        raise FraudError("trường nguồn không hợp lệ")
    t = body or ""
    if not t.strip():
        return Scan(source_field)
    parts = [(a, _scan_one(t[a:b], source_field)) for a, b in _windows(len(t))]
    if len(parts) == 1:
        return parts[0][1]

    def first(name: str) -> tuple[int, int] | None:
        return next((_shift(getattr(sc, name), a) for a, sc in parts if getattr(sc, name)), None)

    accts: dict[str, Account] = {}
    for a, sc in parts:
        for ac in sc.accounts:
            k = ac.digest or ac.last3
            accts.setdefault(k, replace(ac, span=(ac.span[0] + a, ac.span[1] + a)))
    cred_part = next(((a, sc) for a, sc in parts if sc.cred), None)
    amounts = [sc.amount_vnd for _a, sc in parts if sc.amount_vnd]
    return Scan(source_field, tuple(accts.values()), first("change"), first("pay_ask"), first("ask_money"), first("goods"), first("unc"),
                first("cred"), cred_part[1].cred_first_person if cred_part else True, first("takeover"), first("phishing"),
                next((sc.domain for _a, sc in parts if sc.domain), None), max(amounts) if amounts else None,
                tuple(dict.fromkeys(x for _a, sc in parts for x in sc.addressed))[:3], pay_verb=first("pay_verb"))


def _clean(t: str) -> tuple[str, list[int]]:
    """Bỏ ký tự định dạng vô hình (Unicode Cf: zero-width, đảo chiều…) và gộp mọi khoảng trắng/xuống dòng liên tiếp thành MỘT dấu cách
    — chống né bằng ký tự lạ ("0123<ZWSP>456789", "đổi\n\nSTK"). Trả (chữ sạch, ánh xạ vị trí sạch → vị trí gốc)."""
    out: list[str] = []
    cmap: list[int] = []
    prev_space = False
    for i, ch in enumerate(t):
        if unicodedata.category(ch) == "Cf":
            continue
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            cmap.append(i)
            prev_space = True
            continue
        out.append(ch)
        cmap.append(i)
        prev_space = False
    return "".join(out), cmap


def _remap(sc: Scan, cmap: list[int], n: int) -> Scan:
    def m(sp: tuple[int, int] | None) -> tuple[int, int] | None:
        if sp is None or not cmap:
            return sp
        a = min(max(sp[0], 0), len(cmap) - 1)
        b = min(max(sp[1], 1), len(cmap))
        return (cmap[a], min(n, cmap[b - 1] + 1))

    return replace(sc, accounts=tuple(replace(x, span=m(x.span) or x.span) for x in sc.accounts), change=m(sc.change), pay_ask=m(sc.pay_ask),
                   pay_verb=m(sc.pay_verb), ask_money=m(sc.ask_money), goods=m(sc.goods), unc=m(sc.unc), cred=m(sc.cred),
                   takeover=m(sc.takeover), phishing=m(sc.phishing))


def _scan_one(t: str, source_field: str) -> Scan:
    if not t.strip():
        return Scan(source_field)
    clean, cmap = _clean(t)
    if len(clean) == len(t):
        return _scan_core(t, source_field)
    return _remap(_scan_core(clean, source_field), cmap, len(t))


def _scan_core(t: str, source_field: str) -> Scan:
    if not t.strip():
        return Scan(source_field)
    from gh.refinery import prefilter

    fold, idx = _fold_map(t)
    n = len(t)
    # Luật (a) của L2: dãy số > 19 chữ số được thay bằng '#' CÙNG ĐỘ DÀI (giữ span) — `prefilter._vnd` đổi số qua float nên chuỗi số cực
    # dài làm nó ném OverflowError (lỗi của L2, đã báo). Vẫn lỗi ⇒ bỏ ứng viên L2, luật W5 của file này vẫn chạy (fail-high).
    try:
        cands = prefilter.scan_text(_LONG_RUN.sub(lambda m: "#" * len(m.group(0)), t), source_field)
    except Exception as e:  # noqa: BLE001
        log.warning("fraud: luật (a) L2 lỗi trên một đoạn chữ: %s", type(e).__name__)
        cands = []
    cred_c = next((c for c in cands if c.flag == "credentials_entered"), None)
    take_c = next((c for c in cands if c.flag == "account_takeover"), None)
    cred: tuple[int, int] | None = cred_c.span if cred_c else None
    first_person = cred_c.first_person if cred_c else True
    if cred is None:
        m = _CRED_FORM.search(fold)
        if m and not _NEG.search(fold[max(0, m.start() - 12):m.start() + 20]):
            cred = _orig_span(idx, n, m.start(), m.end())
            lo = max((x.end() for x in re.finditer(r"[.!?\n;]", fold[:m.start()])), default=0)
            first_person = _THIRD.search(fold[lo:m.end()]) is None
    accts = _accounts(fold, idx, n)
    change = _CHANGE.search(fold)
    pay_any = _PAY_VERB.search(fold)
    pay = pay_any if accts else None
    ask = _ASK_MONEY.search(fold)
    order, nodep, rush = _ORDER.search(fold), _NO_DEPOSIT.search(fold), _RUSH.search(fold)
    try:
        amount = max_amount(fold)
    except Exception:  # noqa: BLE001 — fail-high: số tiền hỏng không chặn đánh giá tin (amount = None)
        amount = None
    qty = max((int(q.group(1)) for q in _QTY.finditer(fold)), default=0)
    big = (amount or 0) >= BIG_ORDER_VND or qty >= BIG_ORDER_QTY or _BIG_WORDS.search(fold) is not None
    goods = _span(order, idx, n) if (order and nodep and rush and big) else None
    unc = _UNC.search(fold)
    phish = suspicious.phishing_bait(t)
    pm = suspicious.PHISHING.search(fold)
    addressed = tuple(dict.fromkeys(_addressed_surfaces(t)))[:3]
    return Scan(source_field, tuple(accts), _span(change, idx, n), _span(pay, idx, n), _span(ask, idx, n), goods, _span(unc, idx, n), cred,
                first_person, take_c.span if take_c else None,
                (_span(pm, idx, n) or (0, min(n, 1))) if phish else None, suspicious.domain_of(t), amount, addressed,
                pay_verb=_span(pay_any, idx, n))


# ─── tầng thuần: quyết định từ quét + ngữ cảnh người ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Who:
    """Dữ kiện tra cứu về MỘT người (không phán đoán)."""
    ref: str
    person_id: uuid.UUID | None = None
    entity_id: uuid.UUID | None = None
    name: str | None = None
    is_owner: bool = False
    team: bool = False
    customer: bool = False
    key_relation: bool = False
    business_contact: bool = False      # NCC / đối tác / khách / nhân viên (bên có giao dịch tiền với Sếp)
    present_in_thread: bool = False

    def rel(self, *, thread_member: bool = True) -> levels.PersonRel:
        return levels.PersonRel(ref=self.ref, is_owner=self.is_owner, owner_key_relation=self.key_relation, customer=self.customer,
                                team=self.team, thread_member=thread_member, present_in_thread=self.present_in_thread)


@dataclass(frozen=True)
class Ctx:
    """Ngữ cảnh code tra cho MỘT tin (bên gọi đọc DB). `known_digests`/`verified_digests` là của CHỦ STK (người gửi, hoặc người bị mạo danh)."""
    sender: Who
    stranger: bool = False                       # vào nhóm < 7 ngày / chưa có lịch sử tin ≥ 7 ngày
    impersonated: Who | None = None
    owner: Who | None = None
    addressed: tuple[Who, ...] = ()
    known_digests: frozenset[str] = frozenset()
    verified_digests: frozenset[str] = frozenset()
    any_verified: bool = False                   # chủ STK có STK đã xác minh
    disputed: bool = False                       # ghi `payment_account` vừa mở `mem.disputes`


@dataclass(frozen=True)
class Decision:
    rules: tuple[str, ...]
    hard: tuple[str, ...]
    flags: dict[str, bool]
    victims: tuple[levels.PersonRel, ...]
    impersonated: tuple[levels.PersonRel, ...]
    txn_state: str
    payee: Account | None
    domain: str | None
    span: tuple[int, int]
    source_field: str
    record_only: bool = False                    # chỉ ghi lịch sử STK (lần đầu thấy) — không tín hiệu
    safe_verified: bool = False                  # STK trùng STK Sếp đã xác minh ⇒ không báo
    amount_vnd: int | None = None                # số tiền LỚN NHẤT trong chữ (trục impact)
    extra: dict[str, Any] = field(default_factory=dict)


_NO_KEY_WARNED = False


def _warn_no_key() -> None:
    global _NO_KEY_WARNED
    if not _NO_KEY_WARNED:
        _NO_KEY_WARNED = True
        log.warning("fraud: thiếu khoá master — không băm được STK để so lịch sử; dùng luật 'đổi STK + đòi chuyển tiền' (fail-high)")


def decide(sc: Scan, ctx: Ctx) -> Decision | None:
    """Hàm thuần: quét + người ⇒ cờ/luật cứng/nạn nhân cho `levels.SignalIn`. None ⇒ không có gì về lừa đảo."""
    if not sc.any:
        return None
    rules: list[str] = []
    hard: list[str] = []
    flags: dict[str, bool] = {}
    victims: list[levels.PersonRel] = []
    spans: list[tuple[int, int]] = []
    payee = sc.accounts[0] if sc.accounts else None
    new_accts = [a for a in sc.accounts if a.digest and a.digest not in ctx.known_digests]
    verified_hit = any(a.digest in ctx.verified_digests for a in sc.accounts if a.digest)
    business = ctx.sender.business_contact or (ctx.impersonated is not None and ctx.impersonated.business_contact)
    owner_rel = [ctx.owner.rel(thread_member=ctx.owner.present_in_thread)] if ctx.owner else []
    addressed = [w.rel(thread_member=w.present_in_thread) for w in ctx.addressed]
    txn = "none"

    # 1. thông tin đăng nhập / thẻ đã lộ (luật cứng; người gửi là nạn nhân — ngôi thứ ba thì vẫn là phía người gửi)
    if sc.cred:
        rules.append("credentials_entered")
        hard.append(H_CRED)
        flags["credentials_entered"] = True
        victims.append(ctx.sender.rel())
        spans.append(sc.cred)
    if sc.takeover:
        rules.append("account_takeover")
        flags["account_takeover"] = True
        victims.append(ctx.sender.rel())
        spans.append(sc.takeover)

    # 2. STK + yêu cầu trả tiền vào số đó
    stk_ask = bool(sc.accounts and (sc.change or sc.pay_ask))
    if sc.accounts:
        if verified_hit and not new_accts:
            pass                                                    # STK Sếp đã xác minh: an toàn
        elif stk_ask and new_accts and (ctx.known_digests or ctx.any_verified or ctx.disputed):
            rules.append("stk_change")
            hard.append(H_STK)
            flags["money_request"] = True
            txn = "pending"
            spans.append(payee.span if payee else (0, 0))
            payee = new_accts[0]
        elif stk_ask and sc.change:
            # "đổi STK" + yêu cầu chuyển vào số mới mà chưa có lịch sử để so: chính lời "đổi" khẳng định số cũ khác ⇒ luật cứng (fail-high)
            rules.append("stk_change_unverified")
            hard.append(H_STK_CLAIM)
            flags["money_request"] = True
            txn = "pending"
            spans.append(sc.change)
        elif ctx.disputed:
            rules.append("payment_account_dispute")                 # STK khác lịch sử, không kèm yêu cầu tiền ⇒ 🟠 (levels)
            spans.append(payee.span if payee else (0, 0))
        if stk_ask and not set(rules) & {"stk_change", "stk_change_unverified"} and any(a.digest is None for a in sc.accounts):
            # Thiếu khoá master ⇒ không băm được để so lịch sử: KHÔNG im lặng bỏ qua — coi như lời "đổi/chuyển vào số này" (luật cứng)
            _warn_no_key()
            rules.append("stk_unhashed")
            hard.append(H_STK_CLAIM)
            flags["money_request"] = True
            txn = "pending"
            spans.append(sc.accounts[0].span)
    elif sc.change and sc.pay_verb:
        # "đổi STK" + yêu cầu trả tiền mà không đọc được số (số bị chèn ký tự, gửi ảnh, gửi tin sau…) ⇒ vẫn là luật cứng (review M1)
        rules.append("stk_change_claim")
        hard.append(H_STK_CLAIM)
        flags["money_request"] = True
        txn = "pending"
        spans.append(sc.change)
    stk_rules = set(rules) & {"stk_change", "stk_change_unverified", "stk_unhashed", "stk_change_claim"}
    if (sc.accounts and stk_ask) or stk_rules:
        victims.extend(addressed)
        if business:
            victims.extend(owner_rel)                               # bên trả tiền là Sếp (NCC/đối tác/khách đòi chuyển vào số mới)

    # 2b. người lạ (< 7 ngày / chưa có lịch sử) gửi STK + yêu cầu thanh toán vào số đó ⇒ luật cứng (spec: money_request + người lạ)
    if ctx.stranger and stk_ask and not stk_rules and ctx.impersonated is None and not (verified_hit and not new_accts):
        rules.append("stranger_stk_request")
        hard.append(H_STRANGER)
        flags["money_request"] = True
        txn = "pending"
        spans.append(sc.pay_ask or sc.change or sc.accounts[0].span)
        victims.extend(addressed)
        if ctx.owner is not None and ctx.owner.present_in_thread:
            victims.extend(owner_rel)                               # đòi "thanh toán" trong luồng có Sếp ⇒ bên trả là Sếp

    # 3. mạo danh (mem.aliases / trùng tên, ID mới)
    imp: list[levels.PersonRel] = []
    if ctx.impersonated is not None:
        imp.append(ctx.impersonated.rel(thread_member=ctx.impersonated.present_in_thread))
        rules.append("impersonation")
        if sc.money_ask or sc.ask_money or stk_ask:
            hard.append(H_IMP)
            flags["account_takeover"] = True
            flags["money_request"] = True
            txn = "pending"
            victims.extend(addressed)
            if ctx.impersonated.business_contact or ctx.impersonated.is_owner or ctx.impersonated.team:
                victims.extend(owner_rel)
        spans.append(sc.ask_money or sc.pay_ask or sc.change or (payee.span if payee else (0, 1)))

    # 4. người lạ nhờ ứng / mượn / chuyển giúp tiền
    if sc.ask_money and ctx.stranger:
        rules.append("stranger_money_request")
        hard.append(H_STRANGER)
        flags["money_request"] = True
        txn = "pending"
        victims.extend(addressed)
        spans.append(sc.ask_money)

    # 5. đặt đơn lớn không cọc, giục gửi ngay (người lạ / mạo danh) ⇒ Sếp (bên bán) là nạn nhân
    if sc.goods and (ctx.stranger or ctx.impersonated is not None):
        rules.append("goods_no_deposit_rush")
        hard.append(H_GOODS)
        flags["money_request"] = True
        txn = "pending"
        victims.extend(owner_rel)
        spans.append(sc.goods)

    # 6. ảnh/tin chuyển khoản thành công — chỉ có nghĩa cùng một dấu hiệu lừa đảo
    if sc.unc and rules:
        txn = "just_done"
        rules.append("unc")
        flags["money_request"] = True

    # 7. mồi phishing (chưa ai nhập) — không cờ tiền/OTP ⇒ tối đa 🟠
    if sc.phishing and not rules:
        rules.append("phishing")
        spans.append(sc.phishing)

    if not rules:
        if sc.accounts:
            return Decision((), (), {}, (), (), "none", payee, None, sc.accounts[0].span, sc.source_field, record_only=True,
                            safe_verified=verified_hit)
        return None
    seen: set[str] = set()
    vic = tuple(v for v in victims if not (v.ref in seen or seen.add(v.ref)))     # type: ignore[func-returns-value]
    span = next((s for s in spans if s[1] > s[0]), (0, 0))
    return Decision(tuple(dict.fromkeys(rules)), tuple(dict.fromkeys(hard)), flags, vic, tuple(imp), txn, payee,
                    sc.domain if "phishing" in rules else None, span, sc.source_field, safe_verified=verified_hit, amount_vnd=sc.amount_vnd)


# ─── chữ hiển thị (không số, không phán xét người) ───────────────────────────────────────────────────────────────────────

ACTION_PAY = "KHÔNG chuyển; gọi số cũ để xác minh."
ACTION_GOODS = "KHÔNG gửi hàng khi chưa nhận cọc; gọi lại số cũ/kênh quen để xác minh người đặt."
ACTION_CRED = "Khoá thẻ / đổi mật khẩu ngay; gọi tổng đài ngân hàng."
ACTION_LINK = "Không bấm link, không nhập thông tin."


def wording(d: Decision, place: str, name: str | None) -> tuple[str, str, str]:
    """(tiêu đề, harm_what, câu hành động) — mô tả SỰ VIỆC, không dán nhãn "lừa đảo"/"nghi" lên người (CNS-052, CNS-061)."""
    tail = f" (STK …{d.payee.last3})" if d.payee and d.payee.last3 else ""
    r = d.rules
    if "credentials_entered" in r:
        return (f"Có người báo đã nhập OTP / mật khẩu / số thẻ trong {place}", "có thể bị rút tiền trong tài khoản/thẻ ngay", ACTION_CRED)
    if "goods_no_deposit_rush" in r:
        return (f"Có đơn hàng lớn không cọc, giục gửi ngay trong {place}", "có thể mất hàng không thu được tiền", ACTION_GOODS)
    if "stk_change" in r or "stk_change_unverified" in r or "stk_unhashed" in r or "stk_change_claim" in r:
        diff = ", khác STK đã dùng" if "stk_change" in r else ""
        return (f"Có yêu cầu đổi số tài khoản nhận tiền trong {place}{tail[:-1] + diff + ')' if tail else ''}",
                "có thể chuyển tiền vào tài khoản lạ, không đòi lại được", ACTION_PAY)
    if "impersonation" in r and d.hard:
        who = f" “{name}”" if name else ""
        return (f"Có tài khoản khác ID trùng tên{who} yêu cầu chuyển tiền trong {place}", "có thể chuyển tiền cho người mạo danh", ACTION_PAY)
    if "stranger_stk_request" in r:
        return (f"Người mới vào {place} gửi số tài khoản và yêu cầu thanh toán{tail}", "có thể chuyển tiền vào tài khoản lạ", ACTION_PAY)
    if "stranger_money_request" in r:
        return (f"Người mới vào {place} nhờ ứng / chuyển tiền", "có thể mất tiền cho người lạ", ACTION_PAY)
    if "impersonation" in r:
        who = f" “{name}”" if name else ""
        return (f"Có tài khoản khác ID trùng tên{who} trong {place}", "", "Kiểm tra lại đúng người trước khi làm theo.")
    if "payment_account_dispute" in r:
        return (f"Số tài khoản nhận tiền khác số đã dùng trong {place}{tail}", "", ACTION_PAY)
    return (f"Có link nhận quà / cập nhật thông tin lạ trong {place}", "", ACTION_LINK)


def signal_input(d: Decision, *, msg_at: datetime, thread: levels.Thread, delivery_form: str = "direct") -> levels.SignalIn:
    """`Decision` ⇒ `levels.SignalIn` (luật: nguồn `rule`, exist 1,00)."""
    targeted = any(p.is_owner or p.owner_key_relation or p.customer or p.team for p in d.victims)
    imp = levels.impact_base(FR, owner_side_targeted=targeted, money_or_credentials=bool(d.flags.get("money_request") or d.flags.get("credentials_entered")))
    return levels.SignalIn(kind=FR, msg_at=msg_at, event_at=msg_at, flags=levels.Flags(**d.flags), hard_rule_hit=list(d.hard),
                           victims=list(d.victims), impersonated=list(d.impersonated), thread=thread, txn_state=d.txn_state,  # type: ignore[arg-type]
                           txn_at=msg_at if d.txn_state == "just_done" else None, source_class="rule", confidence_exist=1.0,
                           confidence_content=1.0, impact_base=imp, delivery_form=delivery_form,  # type: ignore[arg-type]
                           payee_refs=[f"stk:{d.payee.digest[:16]}"] if d.payee and d.payee.digest else [],
                           money_vnd=d.amount_vnd, money_basis="quoted" if d.amount_vnd else None)


# ─── tầng DB ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class _Row:
    event_id: uuid.UUID
    received_at: datetime
    occurred_at: datetime
    group_id: uuid.UUID | None
    person_id: uuid.UUID | None
    source_field: str
    body: str


async def _candidates(db: AsyncSession, org_id: uuid.UUID, now: datetime, limit: int) -> list[_Row]:
    """Tin vào gần đây (thân tin) + chữ OCR/transcript mới (tin ≤ 24 giờ) chưa có tín hiệu W5 cho đúng (tin, trường) — MỚI NHẤT TRƯỚC
    (xả tin thường không đẩy được tin lừa đảo mới ra khỏi lượt; bên gọi lọc tiếp theo watermark)."""
    rows = (await db.execute(text("""
        WITH src AS (
          SELECT e.id, e.received_at, e.occurred_at, e.group_id, e.sender_identity_id, 'body' AS field, e.body_text AS body
          FROM raw.events e
          WHERE e.org_id = :o AND e.received_at >= :since AND e.direction = 'inbound' AND e.body_text IS NOT NULL AND e.body_text <> ''
          UNION ALL
          SELECT e.id, e.received_at, e.occurred_at, e.group_id, e.sender_identity_id, mt.kind AS field, mt.text AS body
          FROM clean.media_texts mt JOIN raw.events e ON e.id = mt.raw_event_id AND e.received_at = mt.raw_received_at
          WHERE mt.org_id = :o AND e.org_id = :o AND mt.created_at >= :since AND e.received_at >= :msince AND mt.status = 'ok'
            AND mt.kind IN ('ocr', 'transcript') AND mt.text IS NOT NULL AND e.direction = 'inbound')
        SELECT s.*, pi.person_id FROM src s
        LEFT JOIN core.person_identities pi ON pi.id = s.sender_identity_id
        WHERE NOT EXISTS (SELECT 1 FROM biz.alerts a WHERE a.org_id = :o AND a.kind = 'rui_ro_lua_dao' AND a.raw_event_ids @> ARRAY[s.id]
                            AND a.evidence @> jsonb_build_array(jsonb_build_object('raw_event_id', s.id::text, 'source_field', s.field)))
        ORDER BY s.received_at DESC, s.occurred_at DESC, s.id DESC LIMIT :n"""), {"o": org_id, "since": now - WINDOW, "msince": now - MEDIA_WINDOW, "n": limit})).all()
    return [_Row(r.id, r.received_at, r.occurred_at, r.group_id, r.person_id, r.field, r.body) for r in rows]


class SeenStore:
    """Watermark "đã quét" theo (tin, trường). Bộ nhớ trong tiến trình (mặc định / test); `RedisSeen` cho worker (giữ qua khởi động lại)."""

    def __init__(self) -> None:
        self._d: dict[str, float] = {}

    async def unseen(self, org_id: uuid.UUID, keys: list[str]) -> set[str]:
        t = time.time()
        return {k for k in keys if self._d.get(f"{org_id}:{k}", 0.0) < t}

    async def mark(self, org_id: uuid.UUID, keys: list[str]) -> None:
        t = time.time()
        if len(self._d) > 200_000:
            self._d = {k: v for k, v in self._d.items() if v >= t}
        for k in keys:
            self._d[f"{org_id}:{k}"] = t + SEEN_TTL_S

    async def fail(self, org_id: uuid.UUID, key: str) -> int:
        """Đếm số lần MỘT tin làm lượt quét lỗi (để tin hỏng không bị quét lại vô hạn). Trả số lần sau khi cộng."""
        k = f"fail:{org_id}:{key}"
        self._f = getattr(self, "_f", {})
        self._f[k] = self._f.get(k, 0) + 1
        return self._f[k]


class RedisSeen(SeenStore):
    def __init__(self, redis: Any) -> None:
        super().__init__()
        self.r = redis

    async def unseen(self, org_id: uuid.UUID, keys: list[str]) -> set[str]:
        if not keys:
            return set()
        vals = await self.r.mget([f"intel:fraud:seen:{org_id}:{k}" for k in keys])
        return {k for k, v in zip(keys, vals, strict=True) if v is None}

    async def mark(self, org_id: uuid.UUID, keys: list[str]) -> None:
        if keys:
            pipe = self.r.pipeline()
            for k in keys:
                pipe.set(f"intel:fraud:seen:{org_id}:{k}", b"1", ex=SEEN_TTL_S)
            await pipe.execute()

    async def fail(self, org_id: uuid.UUID, key: str) -> int:
        k = f"intel:fraud:fail:{org_id}:{key}"
        n = int(await self.r.incr(k))
        await self.r.expire(k, SEEN_TTL_S)
        return n


MEMORY_SEEN = SeenStore()
MAX_FAILS = 3                             # tin làm lỗi ≥ 3 lượt ⇒ đánh dấu "đã quét" (bỏ), không quét lại vô hạn


def _seen_key(row: _Row) -> str:
    return f"{row.event_id}:{row.source_field}"


async def _priors(db: AsyncSession, org_id: uuid.UUID, row: _Row) -> list[_Row]:
    """≤ 2 tin THÂN TIN ngay trước tin này của CÙNG người trong CÙNG luồng, trong `GROUP_WINDOW` (gom tin chia nhỏ — review M2)."""
    if row.person_id is None or row.source_field != "body":
        return []
    rows = (await db.execute(text("""
        SELECT e.id, e.received_at, e.occurred_at, e.group_id, e.body_text FROM raw.events e
        JOIN core.person_identities pi ON pi.id = e.sender_identity_id
        WHERE e.org_id = :o AND pi.person_id = :p AND e.direction = 'inbound' AND e.id <> :i AND e.body_text IS NOT NULL AND e.body_text <> ''
          AND e.occurred_at <= :at AND e.occurred_at >= :lo AND e.received_at >= :rlo
          AND ((CAST(:g AS uuid) IS NULL AND e.group_id IS NULL) OR e.group_id = :g)
        ORDER BY e.occurred_at DESC LIMIT :n"""), {"o": org_id, "p": row.person_id, "i": row.event_id, "at": row.occurred_at,
                                                    "lo": row.occurred_at - GROUP_WINDOW, "rlo": row.received_at - GROUP_WINDOW - timedelta(hours=1),
                                                    "g": row.group_id, "n": GROUP_MAX - 1})).all()
    return [_Row(r.id, r.received_at, r.occurred_at, r.group_id, row.person_id, "body", r.body_text) for r in reversed(rows)]


async def _sender_flagged(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID | None, at: datetime) -> bool:
    """Người gửi đang dính tín hiệu W5 mở (≤ 7 ngày) — tài khoản có thể đã bị chiếm: STK của các tin SAU cũng không thành lịch sử (m2)."""
    if person_id is None:
        return False
    return (await db.execute(text("""SELECT 1 FROM biz.alerts WHERE org_id = :o AND kind = 'rui_ro_lua_dao' AND status IN ('open', 'acknowledged')
                                     AND created_at >= :lo AND coalesce(facts -> 'person_ids', '[]'::jsonb) ? :p LIMIT 1"""),
                            {"o": org_id, "lo": at - timedelta(days=STRANGER_DAYS), "p": str(person_id)})).first() is not None


async def _who(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID | None, *, key_rel: frozenset[uuid.UUID], owner_people: set[uuid.UUID],
               group_id: uuid.UUID | None, entity_id: uuid.UUID | None = None) -> Who:
    """Quan hệ của một người với Sếp (tra `mem`/`core.persons`, không phán đoán)."""
    if person_id is None and entity_id is not None:
        person_id = (await db.execute(text("SELECT person_id FROM mem.entities WHERE id = :e AND org_id = :o"),
                                      {"e": entity_id, "o": org_id})).scalar_one_or_none()
    if person_id is None:
        return Who(ref=f"e_{entity_id}" if entity_id else "u_unknown", entity_id=entity_id)
    p = (await db.execute(text("SELECT display_name, person_type FROM core.persons WHERE id = :p AND org_id = :o"),
                          {"p": person_id, "o": org_id})).one_or_none()
    if p is None:
        return Who(ref=f"u_{person_id}")
    ent = entity_id or (await db.execute(text("""SELECT coalesce(merged_into, id) FROM mem.entities WHERE org_id = :o AND person_id = :p"""),
                                         {"o": org_id, "p": person_id})).scalar_one_or_none()
    customer = p.person_type == "customer"
    if ent is not None and not customer:
        customer = (await db.execute(text("""SELECT 1 FROM mem.facts WHERE org_id = :o AND subject_id = :e AND predicate = 'customer_of'
                                             AND valid_to IS NULL AND status IN ('asserted', 'owner_confirmed') LIMIT 1"""),
                                     {"o": org_id, "e": ent})).first() is not None
    present = group_id is not None and (await db.execute(text("""SELECT 1 FROM core.group_members WHERE group_id = :g AND person_id = :p
                                                                 AND left_at IS NULL LIMIT 1"""), {"g": group_id, "p": person_id})).first() is not None
    is_owner = person_id in owner_people
    return Who(ref=f"u_{person_id}", person_id=person_id, entity_id=ent, name=p.display_name, is_owner=is_owner,
               team=p.person_type == "staff", customer=customer, key_relation=ent in key_rel if ent else False,
               business_contact=is_owner or p.person_type in ("supplier", "partner", "customer", "staff") or customer,
               present_in_thread=present or is_owner)


async def _stranger(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID | None, group_id: uuid.UUID | None, at: datetime) -> tuple[bool, bool]:
    """(người lạ, đã biết lâu). Người lạ = vào nhóm < 7 ngày (khi `joined_at` đáng tin: vào SAU lúc hệ thống thấy nhóm + 1 ngày, như L2) hoặc
    chưa có tin nào ≥ 7 ngày trước tin này."""
    if person_id is None:
        return True, False
    first = (await db.execute(text("""SELECT min(e.occurred_at) FROM raw.events e JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                      WHERE e.org_id = :o AND pi.person_id = :p"""), {"o": org_id, "p": person_id})).scalar_one_or_none()
    known = first is not None and first <= at - timedelta(days=STRANGER_DAYS)
    joined_recent = False
    if group_id is not None:
        jr = (await db.execute(text("""SELECT gm.joined_at, g.created_at FROM core.group_members gm JOIN core.groups g ON g.id = gm.group_id
                                       WHERE gm.group_id = :g AND gm.person_id = :p"""), {"g": group_id, "p": person_id})).one_or_none()
        if jr is not None and jr.joined_at is not None and jr.created_at is not None and jr.joined_at > jr.created_at + timedelta(days=1):
            joined_recent = (at - jr.joined_at).total_seconds() < STRANGER_DAYS * 86400
    return (joined_recent or not known), known and not joined_recent


async def _impersonated(db: AsyncSession, org_id: uuid.UUID, sender: Who, group_id: uuid.UUID | None, *, key_rel: frozenset[uuid.UUID],
                        owner_people: set[uuid.UUID]) -> Who | None:
    """Tên hiển thị người gửi khớp CHÍNH XÁC bí danh (`mem.aliases`, bỏ qua thực thể của chính người gửi) hoặc tên thành viên của MỘT người
    khác ⇒ người bị trùng tên. Nhiều người khớp ⇒ không đoán (None)."""
    if not sender.name or sender.person_id is None:
        return None
    from gh.biz.mem import aliases

    own = {sender.entity_id} if sender.entity_id else set()
    if sender.entity_id is not None:
        own.update((await db.execute(text("SELECT id FROM mem.entities WHERE org_id = :o AND (id = :e OR merged_into = :e)"),
                                     {"o": org_id, "e": sender.entity_id})).scalars().all())
    hits = [m for m in await aliases.resolve(db, org_id, sender.name, group_id) if m.method == "alias_exact" and m.entity_id not in own]
    cands: dict[uuid.UUID, uuid.UUID | None] = {}
    for m in hits:
        pid = (await db.execute(text("SELECT person_id FROM mem.entities WHERE id = :e AND org_id = :o"), {"e": m.entity_id, "o": org_id})).scalar_one_or_none()
        if pid != sender.person_id:
            cands[m.entity_id] = pid
    if not cands and group_id is not None:
        rows = (await db.execute(text("""
            SELECT p2.id FROM core.group_members gm JOIN core.persons p2 ON p2.id = gm.person_id
            WHERE gm.group_id = :g AND p2.org_id = :o AND p2.id <> :p AND p2.deleted_at IS NULL AND p2.merged_into_id IS NULL
              AND mem.fold(p2.display_name) = mem.fold(:n) LIMIT 2"""), {"g": group_id, "o": org_id, "p": sender.person_id, "n": sender.name})).scalars().all()
        if len(rows) == 1:
            return await _who(db, org_id, rows[0], key_rel=key_rel, owner_people=owner_people, group_id=group_id)
    if len(cands) != 1:
        return None
    (eid, pid), = cands.items()
    w = await _who(db, org_id, pid, key_rel=key_rel, owner_people=owner_people, group_id=group_id, entity_id=eid)
    if group_id is not None and not w.present_in_thread:
        scoped = (await db.execute(text("""SELECT 1 FROM mem.aliases WHERE org_id = :o AND entity_id = :e AND scope_group_id = :g LIMIT 1"""),
                                   {"o": org_id, "e": eid, "g": group_id})).first() is not None
        w = replace(w, present_in_thread=scoped)
    return w


async def _addressed(db: AsyncSession, org_id: uuid.UUID, surfaces: tuple[str, ...], group_id: uuid.UUID | None, sender: Who, *,
                     key_rel: frozenset[uuid.UUID], owner_people: set[uuid.UUID]) -> tuple[Who, ...]:
    """Người được gọi tên trong tin ("chị Lan ơi…") khớp MỘT thực thể chính xác (không đoán)."""
    from gh.biz.mem import aliases

    out: list[Who] = []
    for s in surfaces:
        ms = [m for m in await aliases.resolve(db, org_id, s, group_id) if m.method == "alias_exact"]
        if len(ms) != 1 or ms[0].entity_id == sender.entity_id:
            continue
        w = await _who(db, org_id, None, key_rel=key_rel, owner_people=owner_people, group_id=group_id, entity_id=ms[0].entity_id)
        if w.person_id != sender.person_id:
            out.append(w)
    return tuple(out)


async def _history(db: AsyncSession, org_id: uuid.UUID, owner_of: Who, at: datetime) -> tuple[frozenset[str], frozenset[str], bool]:
    """(STK "đã biết", STK đã xác minh, có STK xác minh) của chủ STK — chỉ băm.

    Chống "đầu độc lịch sử" (kẻ gian gửi STK của nó một lần rồi đổi): một STK chỉ là "đã biết" khi có dấu hiệu tin cậy —
    Sếp xác minh (`payment_identifiers.verified_at`, fact `owner_confirmed`), hoặc đã dùng lâu hơn cửa sổ người lạ (thấy lần đầu
    ≥ 7 ngày trước tin này) VÀ chưa từng nằm trong một tín hiệu W5 (tin kích luật lừa đảo không bao giờ thành lịch sử)."""
    known: set[str] = set()
    verified: set[str] = set()
    old = at - timedelta(days=STRANGER_DAYS)
    if owner_of.entity_id is not None:
        rows = (await db.execute(text("""
            SELECT f.object_value ->> 'digest' FROM mem.facts f JOIN mem.entities e ON e.id = f.subject_id
            WHERE f.org_id = :o AND f.predicate = 'payment_account' AND (e.id = :e OR e.merged_into = :e)
              AND (f.status = 'owner_confirmed'
                   OR (f.status = 'asserted' AND f.valid_from <= :old
                       AND NOT EXISTS (SELECT 1 FROM biz.alerts a WHERE a.org_id = :o AND a.kind = 'rui_ro_lua_dao'
                                         AND (a.facts ->> 'payee_digest' = f.object_value ->> 'digest'
                                              OR coalesce(a.facts -> 'fraud_digests', '[]'::jsonb) ? (f.object_value ->> 'digest')
                                              OR (f.source_raw_event_id IS NOT NULL AND a.raw_event_ids @> ARRAY[f.source_raw_event_id])))))"""),
                                 {"o": org_id, "e": owner_of.entity_id, "old": old})).scalars().all()
        known.update(x for x in rows if x)
    pi = (await db.execute(text("""
        SELECT p.digest, p.verified_at IS NOT NULL AS v,
               (p.created_at <= :old AND NOT EXISTS (SELECT 1 FROM biz.alerts a WHERE a.org_id = :o AND a.kind = 'rui_ro_lua_dao'
                                                     AND (a.facts ->> 'payee_digest' = p.digest
                                                          OR coalesce(a.facts -> 'fraud_digests', '[]'::jsonb) ? p.digest))) AS aged
        FROM biz.payment_identifiers p
        WHERE p.org_id = :o AND ((CAST(:p AS uuid) IS NOT NULL AND p.person_id = :p) OR (CAST(:e AS uuid) IS NOT NULL AND p.entity_id = :e))"""),
                          {"o": org_id, "p": owner_of.person_id, "e": owner_of.entity_id, "old": old})).all()
    for r in pi:
        if r.v:
            verified.add(r.digest)
            known.add(r.digest)
        elif r.aged:
            known.add(r.digest)
    return frozenset(known), frozenset(verified), bool(verified)


async def _record_identifier(db: AsyncSession, org_id: uuid.UUID, acct: Account, *, sender: Who) -> None:
    """`biz.payment_identifiers` (băm + 3 số cuối) gắn với người GỬI THẬT (không bao giờ gắn với người bị mạo danh). Một dòng chưa xác minh,
    mới thấy, chỉ là "đã biết" sau cửa sổ người lạ và khi chưa dính tín hiệu W5 (`_history`)."""
    if acct.digest:
        await db.execute(text("""INSERT INTO biz.payment_identifiers (org_id, person_id, entity_id, digest, last3, bank, source)
                                 VALUES (:o, :p, :e, :d, :l, :b, 'chat') ON CONFLICT (org_id, digest) DO NOTHING"""),
                         {"o": org_id, "p": sender.person_id, "e": sender.entity_id, "d": acct.digest, "l": acct.last3, "b": acct.bank})


async def _live_payment_keys(db: AsyncSession, org_id: uuid.UUID, entity_id: uuid.UUID) -> set[str]:
    rows = (await db.execute(text("""SELECT f.object_value ->> 'digest' FROM mem.facts f JOIN mem.entities e ON e.id = f.subject_id
                                     WHERE f.org_id = :o AND f.predicate = 'payment_account' AND f.status IN ('asserted', 'owner_confirmed')
                                       AND f.valid_to IS NULL AND (e.id = :e OR e.merged_into = :e)"""), {"o": org_id, "e": entity_id})).scalars().all()
    return {x for x in rows if x}


async def _record_fact(db: AsyncSession, org_id: uuid.UUID, acct: Account, *, subject: Who, row: _Row, suspicious_msg: bool) -> uuid.UUID | None:
    """`mem.facts payment_account` của NGƯỜI GỬI (L1 mở `mem.disputes` khi khác giá trị). Tin đã kích luật lừa đảo chỉ được ghi khi chắc
    chắn ra `disputed` (chủ thể đã có STK khác còn hiệu lực) — không bao giờ để STK đáng ngờ thành `asserted`. Trả `dispute_id` nếu có."""
    if not acct.digest or subject.entity_id is None:
        return None
    if suspicious_msg:
        live = await _live_payment_keys(db, org_id, subject.entity_id)
        if not live or acct.digest in live:
            return None
    from gh.biz.mem import facts

    value: dict[str, Any] = {"digest": acct.digest, "last3": acct.last3}
    if acct.bank:
        value["bank"] = acct.bank
    fin = facts.FactIn(org_id=org_id, subject_id=subject.entity_id, predicate="payment_account", valid_from=row.occurred_at, object_value=value,
                       source_class="rule", created_by="rule", speaker_entity_id=subject.entity_id, source_raw_event_id=row.event_id,
                       source_received_at=row.received_at, confidence_exist=1.0, confidence_content=0.9, extractor_version=f"{DETECTOR}/{VERSION}",
                       evidence=[facts.EvidenceIn("raw_event", row.event_id, row.received_at,
                                                  span={"source_field": row.source_field, "span_start": acct.span[0], "span_end": acct.span[1]})])
    try:
        f = await facts.assert_fact(db, fin)
    except facts.FactWriteError as e:
        log.warning("fraud: không ghi được payment_account cho tin %s: %s", row.event_id, e.code)
        return None
    return f.dispute_id if f.status == "disputed" else None


async def _thread(db: AsyncSession, org_id: uuid.UUID, group_id: uuid.UUID | None, owner_people: set[uuid.UUID]) -> tuple[levels.Thread, str]:
    if group_id is None:
        return levels.Thread(type="dm_agent", size=2, owner_in_thread=False, agent_in_thread=True), "tin nhắn riêng"
    g = (await db.execute(text("SELECT name, member_count FROM core.groups WHERE id = :g AND org_id = :o"), {"g": group_id, "o": org_id})).one_or_none()
    owner_in = bool(owner_people) and (await db.execute(text("""SELECT 1 FROM core.group_members WHERE group_id = :g AND person_id = ANY(:p)
                                                                AND left_at IS NULL LIMIT 1"""), {"g": group_id, "p": list(owner_people)})).first() is not None
    name = (g.name if g else None) or "nhóm"
    return levels.Thread(type="group", size=int(g.member_count or 0) if g else 0, owner_in_thread=owner_in, agent_in_thread=True), f"nhóm “{name[:60]}”"


async def _effective_case(db: AsyncSession, org_id: uuid.UUID, ck: str) -> str:
    """Vụ đã có tín hiệu mở (kể cả do đường nhanh `raw_tail` tạo với khoá tin gốc) ⇒ dùng lại khoá đó (một vụ một 🔴)."""
    got = (await db.execute(text("""SELECT case_key FROM biz.alerts WHERE org_id = :o AND kind = 'rui_ro_lua_dao' AND status IN ('open', 'acknowledged')
                                    AND (case_key = :ck OR facts ->> 'fraud_case' = :ck) ORDER BY (case_key = :ck) DESC, created_at LIMIT 1"""),
                            {"o": org_id, "ck": ck})).scalar_one_or_none()
    return got or ck


def _case_key(d: Decision, row: _Row, ctx: Ctx) -> str:
    vn_day = row.occurred_at.astimezone(levels.VN).date().isoformat()
    if d.payee and d.payee.digest and set(d.rules) & {"stk_change", "stk_change_unverified", "stk_unhashed", "payment_account_dispute", "impersonation", "unc"}:
        return levels.case_key(FR, at=row.occurred_at, payee_digest=d.payee.digest[:32], group_id=str(row.group_id) if row.group_id else None)
    if ctx.impersonated is not None and ctx.impersonated.entity_id is not None:
        return levels.case_key(FR, at=row.occurred_at, impersonated_entity_id=str(ctx.impersonated.entity_id))
    if d.domain:
        return levels.case_key(FR, at=row.occurred_at, domain=d.domain)
    if ctx.sender.person_id is not None and set(d.rules) & {"stranger_money_request", "stranger_stk_request", "goods_no_deposit_rush", "stk_change_claim"}:
        return levels.case_key(FR, at=row.occurred_at, raw_event_id=f"actor:{ctx.sender.person_id}:{vn_day}")
    return levels.case_key(FR, at=row.occurred_at, raw_event_id=str(row.event_id))


@dataclass
class Prepared:
    """Một tin đã quyết, sẵn sàng ghi tín hiệu (d2 có thể chạy trước/sau)."""
    row: _Row
    decision: Decision
    ctx: Ctx
    upsert: signals.UpsertIn
    dispute_id: uuid.UUID | None = None


async def prepare(db: AsyncSession, org_id: uuid.UUID, row: _Row, *, key_rel: frozenset[uuid.UUID], owner_people: set[uuid.UUID],
                  owner_entity: uuid.UUID | None) -> Prepared | None:
    sc = await asyncio.to_thread(scan, row.body, row.source_field)          # regex chạy ngoài vòng sự kiện
    priors: list[_Row] = []
    offset = 0
    if sc.any or sc.pay_verb:
        priors = await _priors(db, org_id, row)
        if priors:
            prefix = "\n".join(p.body for p in priors) + "\n"
            comb = await asyncio.to_thread(scan, prefix + row.body, row.source_field)
            if comb.any:
                sc, offset = comb, len(prefix)
            else:
                priors = []
    if not sc.any:
        return None
    from gh.biz.mem import entities

    if row.person_id is not None:
        try:
            await entities.ensure_person(db, org_id, row.person_id)
        except entities.EntityError:
            pass
    sender = await _who(db, org_id, row.person_id, key_rel=key_rel, owner_people=owner_people, group_id=row.group_id)
    stranger, _known = await _stranger(db, org_id, row.person_id, row.group_id, row.occurred_at)
    if sender.is_owner or sender.team:
        stranger = False
    imp = None
    if not sender.is_owner and (stranger or not _known):
        imp = await _impersonated(db, org_id, sender, row.group_id, key_rel=key_rel, owner_people=owner_people)
    owner = None
    if owner_entity is not None and owner_people:
        owner = Who(ref=f"u_{sorted(owner_people)[0]}", person_id=sorted(owner_people)[0], entity_id=owner_entity, is_owner=True,
                    business_contact=True, present_in_thread=True)
    addressed = await _addressed(db, org_id, sc.addressed, row.group_id, sender, key_rel=key_rel, owner_people=owner_people)
    stk_owner = imp if imp is not None else sender
    known, verified, any_v = await _history(db, org_id, stk_owner, row.occurred_at)
    ctx = Ctx(sender=sender, stranger=stranger, impersonated=imp, owner=owner, addressed=addressed, known_digests=known,
              verified_digests=verified, any_verified=any_v)
    d = decide(sc, ctx)
    # Ghi lịch sử SAU khi quyết (chống đầu độc): STK luôn vào `payment_identifiers` gắn người gửi thật; `payment_account` chỉ ghi cho
    # người gửi đã biết, không phải người lạ, không mạo danh — tin đã kích luật thì chỉ khi ra `disputed`.
    dispute = None
    flagged = bool(d and d.rules) or await _sender_flagged(db, org_id, row.person_id, row.occurred_at)
    for a in sc.accounts:
        if a.digest in verified or a.span[0] < offset:
            continue                                                # STK của tin trước (đã gom) được ghi khi quét chính tin đó
        own = replace(a, span=(a.span[0] - offset, a.span[1] - offset))
        if not flagged:
            await _record_identifier(db, org_id, own, sender=sender)    # tin đã kích luật / người đang dính W5: KHÔNG STK nào vào lịch sử
        if imp is None and not stranger:
            dispute = await _record_fact(db, org_id, own, subject=sender, row=row, suspicious_msg=flagged) or dispute
    if dispute is not None and (d is None or d.record_only or "stk_change" not in d.rules):
        ctx = replace(ctx, disputed=True)
        d = decide(sc, ctx)
    if d is None or d.record_only:
        return None
    thread, place = await _thread(db, org_id, row.group_id, owner_people)
    title, harm, action = wording(d, place, imp.name if imp else None)
    li = signal_input(d, msg_at=row.occurred_at, thread=thread).model_copy(update={"harm_what": harm, "subject_entity_id": stk_owner.entity_id})
    ck = await _effective_case(db, org_id, _case_key(d, row, ctx))
    fx: dict[str, Any] = {"fraud_rules": list(d.rules), "fraud_case": _case_key(d, row, ctx), "fraud_v": VERSION,
                          "fraud_digests": sorted({a.digest for a in sc.accounts if a.digest})}   # MỌI STK của tin (băm) ⇒ không bao giờ thành lịch sử
    prev = (await db.execute(text("""SELECT facts -> 'fraud_digests' FROM biz.alerts WHERE org_id = :o AND kind = 'rui_ro_lua_dao'
                                     AND case_key = :ck AND status IN ('open', 'acknowledged') LIMIT 1"""), {"o": org_id, "ck": ck})).scalar_one_or_none()
    if isinstance(prev, list):                     # `upsert` ghi đè khoá `facts` ⇒ cộng dồn STK của mọi tin trong vụ
        fx["fraud_digests"] = sorted(set(fx["fraud_digests"]) | {x for x in prev if isinstance(x, str)})
    if d.payee and d.payee.digest:
        fx |= {"payee_digest": d.payee.digest, "payee_last3": d.payee.last3, "payee_bank": d.payee.bank,
               "payee_person_id": str(sender.person_id) if sender.person_id else None}
    if d.domain:
        fx["domain"] = d.domain
    if dispute:
        fx["dispute_id"] = str(dispute)
    a0, b0 = d.span[0] - offset, d.span[1] - offset
    if b0 <= 0 or a0 >= len(row.body):
        a0, b0 = 0, len(row.body)                                   # luật trúng trên tin trước (đã gom): bằng chứng = cả tin này
    a0, b0 = max(a0, 0), min(max(b0, a0 + 1), len(row.body))
    quote = row.body[a0:b0] if b0 > a0 else None
    ev = [signals.EvidenceIn(type="raw_event", id=row.event_id, received_at=row.received_at, source_field=row.source_field,  # type: ignore[arg-type]
                             span_start=a0, span_end=max(b0, a0 + 1), quote=suspicious.mask_secrets(quote) if quote else None)]
    ev += [signals.EvidenceIn(type="raw_event", id=p.event_id, received_at=p.received_at, source_field="body", span_start=0,
                              span_end=max(1, len(p.body))) for p in priors]
    tk = f"g:{row.group_id}" if row.group_id else (f"dm:{row.person_id}" if row.person_id else None)
    persons = [p for p in (row.person_id, imp.person_id if imp else None) if p]
    up = signals.UpsertIn(kind=FR, title=suspicious.mask_secrets(title)[:300], suggested_action=action, detector=DETECTOR, detector_version=VERSION,
                          level_input=li, evidence=ev, case_key=ck, thread_key=tk, group_id=row.group_id,
                          subject_type="group" if row.group_id else ("person" if row.person_id else None),
                          subject_id=row.group_id or row.person_id, person_ids=persons, facts=fx)
    return Prepared(row, d, ctx, up, dispute)


async def _write(db: AsyncSession, org_id: uuid.UUID, p: Prepared, now: datetime) -> signals.Signal:
    async with db.begin_nested():
        sig = await signals.upsert(db, org_id, p.upsert, now)
        if p.dispute_id is not None:
            # Tranh chấp `payment_account` này ĐÃ có tín hiệu W5 (đủ cờ/luật cứng) ⇒ gắn để T1 `project_disputes` không chiếu lại thành
            # tín hiệu "cần xác nhận" yếu hơn (sẽ ghi đè đầu vào mức của cùng vụ).
            await db.execute(text("""UPDATE mem.disputes SET projected_signal_id = :s WHERE id = :d AND org_id = :o AND projected_signal_id IS NULL"""),
                             {"s": sig.id, "d": p.dispute_id, "o": org_id})
            await db.execute(text("""INSERT INTO biz.signal_links (org_id, signal_id, ref_type, ref_id) VALUES (:o, :s, 'dispute', :d)
                                     ON CONFLICT DO NOTHING"""), {"o": org_id, "s": sig.id, "d": p.dispute_id})
    return sig


# ─── (d2) xác minh mạnh — fail-high ───────────────────────────────────────────────────────────────────────────────────────

async def _raw_ctx(db: AsyncSession, org_id: uuid.UUID, row: _Row) -> tuple[Any, Any]:
    """Đơn vị + tin gốc cho `crosscheck.verify_strong`: tin này + ≤ 12 tin trước cùng luồng (chữ NGUYÊN VĂN — Q9)."""
    from gh.refinery.crosscheck import RawContext
    from gh.refinery.extract import GroupEvent

    prev = (await db.execute(text("""
        SELECT e.id, e.occurred_at, e.body_text, p.display_name, pi.person_id FROM raw.events e
        LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id LEFT JOIN core.persons p ON p.id = pi.person_id
        WHERE e.org_id = :o AND e.occurred_at <= :at AND e.occurred_at >= :lo AND e.id <> :i
          AND ((CAST(:g AS uuid) IS NOT NULL AND e.group_id = :g) OR (CAST(:g AS uuid) IS NULL AND e.group_id IS NULL AND e.sender_identity_id IN
               (SELECT id FROM core.person_identities WHERE person_id = :p)))
        ORDER BY e.occurred_at DESC LIMIT 12"""), {"o": org_id, "at": row.occurred_at, "lo": row.occurred_at - timedelta(days=2), "i": row.event_id,
                                                  "g": row.group_id, "p": row.person_id})).all()
    name = (await db.execute(text("SELECT display_name FROM core.persons WHERE id = :p AND org_id = :o"), {"p": row.person_id, "o": org_id})).scalar_one_or_none() if row.person_id else None
    evs = [GroupEvent(message_id=f"m_{i + 2}", sent_at=r.occurred_at, body=r.body_text, sender_person_id=r.person_id, sender_name=r.display_name,
                      raw_event_id=r.id, context=True) for i, r in enumerate(reversed(prev))]
    body = row.body if row.source_field == "body" else None
    evs.append(GroupEvent(message_id="m_1", sent_at=row.occurred_at, body=body, sender_person_id=row.person_id, sender_name=name, raw_event_id=row.event_id))
    media = {"m_1": [SimpleNamespace(status="ok", text=row.body, kind=row.source_field)]} if row.source_field != "body" else {}
    q = SimpleNamespace(message_id="m_1", field=row.source_field, text=row.body[:2000])
    return SimpleNamespace(quotes=[q]), RawContext(events=evs, media_texts=media)


async def run_d2(db: AsyncSession, org_id: uuid.UUID, p: Prepared, call: Any) -> Prepared:
    """(d2) cho MỘT ứng viên; lỗi ⇒ giữ nguyên (fail-high). Luật cứng ⇒ (d2) chỉ thêm cờ/`d2_disagree`, không hạ (levels)."""
    from gh.refinery import crosscheck

    unit, raw_ctx = await _raw_ctx(db, org_id, p.row)
    try:
        res = await crosscheck.verify_strong(unit, FR, raw_ctx, call=call)
    except Exception as e:  # noqa: BLE001 — verify_strong không ném; phòng xa: fail-high
        log.warning("fraud: (d2) lỗi ở tin %s: %s", p.row.event_id, type(e).__name__)
        return p
    li, _dismissed = signals.apply_crosscheck(p.upsert.level_input, d2=dict(res))
    fx = {**p.upsert.facts, "d2": {"verdict": res.get("verdict"), "model": (res.get("model") or "")[:64], "error": res.get("error")}}
    return replace(p, upsert=p.upsert.model_copy(update={"level_input": li, "facts": fx}))


async def _d2_allowed(db: AsyncSession, org_id: uuid.UUID, thread_key: str, now: datetime) -> bool:
    from gh.refinery import budget

    try:
        caps = await budget.load_caps(db, org_id)
        usage = await budget.load_usage(db, org_id, now)
        paid = await budget.paid_route_possible(db, org_id)
        return budget.decide(budget.KIND_SAFETY, thread_key, caps, usage, paid_route=paid).allow
    except Exception as e:  # noqa: BLE001 — không đọc được trần ⇒ không gọi model (luật cứng vẫn chạy)
        log.warning("fraud: không đọc được trần lượt an toàn: %s", type(e).__name__)
        return False


# ─── chạy ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────

async def run(db: AsyncSession, org_id: uuid.UUID, now: datetime, *, call: Any = None, limit: int = SCAN_LIMIT,
              max_d2: int = MAX_D2_PER_RUN, seen: SeenStore | None = None, budget_s: float = RUN_BUDGET_S,
              processed: list[str] | None = None) -> list[signals.Signal]:
    """Quét một tổ chức. Luật cứng ⇒ ghi NGAY rồi (d2); không luật cứng ⇒ (d2) TRƯỚC rồi ghi (không 🔴 nào bị hạ sau khi đẩy).
    Bên gọi commit. KHÔNG tự đánh dấu "đã quét" (dấu chỉ có SAU commit: `run_and_commit`; `processed` nhận các khoá đã xử lý xong).
    Một tin lỗi không dừng tin khác (lỗi chỉ ghi mã tin + tên lỗi); tin lỗi ≥ `MAX_FAILS` lượt mới bị bỏ."""
    from gh.biz.mem import entities, profile, profile_read

    seen = seen or MEMORY_SEEN
    fetched = await _candidates(db, org_id, now, limit * FETCH_FACTOR)
    fresh = await seen.unseen(org_id, [_seen_key(r) for r in fetched])
    rows = [r for r in fetched if _seen_key(r) in fresh][:limit]
    if not rows:
        return []
    started = time.monotonic()
    prof = await profile_read.snapshot(db, org_id)
    key_rel = prof.key_relation_entity_ids()
    owner_people = set(await profile.owner_person_ids(db, org_id))
    owner_ent = (await entities.owner(db, org_id)).id if owner_people else None
    out: list[signals.Signal] = []
    d2_left = max_d2 if call is not None else 0
    for row in rows:
        if time.monotonic() - started > budget_s:
            log.warning("fraud: hết trần thời gian lượt quét, còn %d tin để lượt sau", len(rows) - rows.index(row))
            break
        try:
            async with db.begin_nested():
                p = await prepare(db, org_id, row, key_rel=key_rel, owner_people=owner_people, owner_entity=owner_ent)
            if p is None:
                if processed is not None:
                    processed.append(_seen_key(row))
                continue
            want_d2 = d2_left > 0 and await _d2_allowed(db, org_id, p.upsert.thread_key or "", now)
            if p.decision.hard:
                sig = await _write(db, org_id, p, now)
                if want_d2:
                    d2_left -= 1
                    p = await run_d2(db, org_id, p, call)
                    sig = await _write(db, org_id, p, now)
            else:
                if want_d2:
                    d2_left -= 1
                    p = await run_d2(db, org_id, p, call)
                sig = await _write(db, org_id, p, now)
            out.append(sig)
            if processed is not None:
                processed.append(_seen_key(row))
        except Exception as e:  # noqa: BLE001 — một tin hỏng chỉ bỏ qua tin đó (mã tin + tên lỗi, không nội dung), lô vẫn chạy tiếp
            n_fail = await seen.fail(org_id, _seen_key(row))
            log.warning("fraud: bỏ qua tin %s (lần lỗi %d): %s", row.event_id, n_fail, type(e).__name__)
            if n_fail >= MAX_FAILS:
                await seen.mark(org_id, [_seen_key(row)])          # tin hỏng: thôi quét lại (không phụ thuộc commit)
    return out


async def run_and_commit(db: AsyncSession, org_id: uuid.UUID, now: datetime, *, seen: SeenStore | None = None, **kw: Any) -> list[signals.Signal]:
    """`run` → COMMIT → mới đánh dấu "đã quét". Commit lỗi / bị huỷ / worker chết giữa chừng ⇒ không có dấu nào ⇒ lượt sau quét lại
    (không bao giờ có tin lừa đảo bị đánh dấu "đã quét" trong khi tín hiệu của nó đã rollback)."""
    seen = seen or MEMORY_SEEN
    keys: list[str] = []
    out = await run(db, org_id, now, seen=seen, processed=keys, **kw)
    await db.commit()
    await seen.mark(org_id, keys)
    return out


async def intel_fraud(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.db import sessionmaker
    from gh.refinery import budget, clock
    from gh.refinery.extract import router_caller

    now = clock.now()
    sm = sessionmaker()
    router = ctx.get("model_router")
    rds = ctx.get("redis_bus") or ctx.get("redis")
    seen: SeenStore = RedisSeen(rds) if rds is not None else MEMORY_SEEN
    res = {"signals": 0, "errors": 0}
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        call = router_caller(router, org, purpose=budget.PURPOSE[budget.KIND_SAFETY], tier="strong") if router is not None else None
        try:
            async with sm() as db:
                res["signals"] += len(await asyncio.wait_for(run_and_commit(db, org, now, call=call, seen=seen), timeout=240))
        except Exception as e:  # noqa: BLE001 — một tổ chức lỗi không dừng tổ chức khác; không ghi nội dung tin
            log.error("intel_fraud lỗi ở một tổ chức: %s", type(e).__name__)
            res["errors"] += 1
    return res


# ─── xác minh STK (`payment_identifiers.verified_*`) — "xác nhận an toàn" của Sếp ────────────────────────────────────────

async def verify_identifier(db: AsyncSession, org_id: uuid.UUID, identifier_id: uuid.UUID, *, user_id: uuid.UUID, pin_verified: bool,
                            now: datetime) -> list[signals.Signal]:
    """Sếp xác minh một STK (§8.1: "thêm/xác minh STK trong payment_identifiers" là việc hạ phòng thủ ⇒ chỉ Console + PIN, hoặc
    `strong_confirm` cho qua). Ghi `verified_by/at`, Nhật ký (không số), và ĐÓNG các tín hiệu W5 đang mở về đúng STK đó bằng đường
    "Sếp đã xử lý" (một trong hai đường xuống ⚪ của nguyên lý fail-high). Sai tổ chức ⇒ `FraudError` (không lộ có/không)."""
    await _require_strong(db, org_id, pin_verified)
    r = (await db.execute(text("""SELECT id, digest FROM biz.payment_identifiers WHERE id = :i AND org_id = :o FOR UPDATE"""),
                          {"i": identifier_id, "o": org_id})).one_or_none()
    if r is None:
        raise FraudError("không tìm thấy số tài khoản")
    return await _verify_row(db, org_id, r.id, r.digest, user_id=user_id, now=now)


async def verify_signal_payee(db: AsyncSession, org_id: uuid.UUID, signal_id: uuid.UUID, *, user_id: uuid.UUID, pin_verified: bool,
                              now: datetime) -> list[signals.Signal]:
    """Sếp xác minh STK của MỘT tín hiệu W5 (STK trong tin đã kích luật KHÔNG được ghi vào lịch sử khi quét, nên Console xác minh theo
    tín hiệu): tạo dòng `payment_identifiers` đã xác minh từ băm + 3 số cuối lưu ở tín hiệu rồi đóng các tín hiệu cùng STK."""
    await _require_strong(db, org_id, pin_verified)
    a = (await db.execute(text("""SELECT facts FROM biz.alerts WHERE id = :i AND org_id = :o AND kind = 'rui_ro_lua_dao'"""),
                          {"i": signal_id, "o": org_id})).scalar_one_or_none()
    fx = a if isinstance(a, dict) else {}
    digest = fx.get("payee_digest")
    if not isinstance(digest, str) or len(digest) != 64:
        raise FraudError("không tìm thấy số tài khoản")
    pp = fx.get("payee_person_id")
    rid = (await db.execute(text("""
        INSERT INTO biz.payment_identifiers (org_id, person_id, digest, last3, bank, source)
        VALUES (:o, CAST(:p AS uuid), :d, :l, :b, 'owner')
        ON CONFLICT (org_id, digest) DO UPDATE SET digest = EXCLUDED.digest RETURNING id"""),
        {"o": org_id, "p": pp if isinstance(pp, str) else None, "d": digest, "l": fx.get("payee_last3"), "b": fx.get("payee_bank")})).scalar_one()
    return await _verify_row(db, org_id, rid, digest, user_id=user_id, now=now)


async def _require_strong(db: AsyncSession, org_id: uuid.UUID, pin_verified: bool) -> None:
    from gh.biz.intel import strong_confirm

    if not pin_verified:
        ok = await strong_confirm.require(db, org_id, "payment_identifier_verify")
        if not isinstance(ok, strong_confirm.Allowed):
            raise FraudError("Việc này cần mở Console (mã PIN)")


async def _verify_row(db: AsyncSession, org_id: uuid.UUID, rid: uuid.UUID, digest: str, *, user_id: uuid.UUID, now: datetime) -> list[signals.Signal]:
    from gh.chassis import actionlog

    r = SimpleNamespace(id=rid, digest=digest)
    await db.execute(text("""UPDATE biz.payment_identifiers SET verified_by = :u, verified_at = :t, source = CASE WHEN source = 'chat' THEN 'owner' ELSE source END
                             WHERE id = :i AND org_id = :o"""), {"u": user_id, "t": now, "i": r.id, "o": org_id})
    await actionlog.record(db, org_id=org_id, actor_type="user", actor_id=str(user_id), action="intel.payment_identifier.verify",
                           target_type="payment_identifier", target_id=str(r.id), result="ok")
    ids = (await db.execute(text("""SELECT id FROM biz.alerts WHERE org_id = :o AND kind = 'rui_ro_lua_dao' AND status IN ('open', 'acknowledged')
                                    AND facts ->> 'payee_digest' = :d
                                    AND coalesce(facts -> 'fraud_rules', '[]'::jsonb) ?| ARRAY['stk_change', 'stk_change_unverified', 'stk_unhashed',
                                                                                             'stk_change_claim', 'payment_account_dispute']
                                    AND NOT coalesce(facts -> 'fraud_rules', '[]'::jsonb) ? 'credentials_entered'"""),
                     {"o": org_id, "d": r.digest})).scalars().all()
    return [await signals.resolve_by_owner(db, org_id, i, now=now, actor_id=str(user_id)) for i in ids]


HOOKS: list[Hook] = []
JOBS: list[CronJob] = [(intel_fraud, {"minute": set(range(60))})]
