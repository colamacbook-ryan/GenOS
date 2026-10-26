# ruff: noqa: E501
"""Tầng (c) — CODE kiểm đầu ra bộ não (spec §4D.2 (c), §4F "Luật kiểm", §4F.1 #1–#12). Không gọi model, không I/O.

`check(out, events, media_texts, *, now, tz, ...) -> Verified`:
- mỗi `quotes[].text` phải là chuỗi con của ĐÚNG tin `message_id`, đúng trường (`body`/`caption` ⇒ thân tin; `transcript`/`ocr` ⇒
  `clean.media_texts` loại tương ứng) sau chuẩn hoá HẸP (NFC, gộp khoảng trắng, bỏ khoảng trắng hai đầu; phân biệt hoa/thường và dấu).
  Ghi `quote_status` (`verified`|`absent`) + span (tính trên chuỗi đã chuẩn hoá). Đơn vị có ≥ 1 trích dẫn `absent` ⇒ đơn vị `absent`:
  bị loại khỏi tín hiệu và sự thật (QUYẾT ĐỊNH L3: nghiêm — một trích dẫn bịa đủ để loại cả đơn vị; G-KB đếm `absent` để chỉnh).
- tính MỐC bằng code: `msg_at`, `event_at` (`vi_time.parse_past(...).latest`), `deadline_at` (`vi_time.parse`), `ongoing`, `money_vnd`,
  `status`/`txn` (người gửi + giờ gửi tin chứng minh), `delivery_form`, `explicit_urgent` (kiểm bằng `URGENT_LEXICON`).
- lọc: `owner_relevance.fact_id` ngoài khối Hồ sơ đã gửi ⇒ bỏ mục (giữ đơn vị); `person_ref` lạ ⇒ chỉ dùng `surface`;
  `predicate` lạ ⇒ bỏ sự thật, đếm `unknown_predicate`; sự thật của đơn vị `absent` ⇒ bỏ.
Đầu vào "events" là đối tượng/dict có: `message_id`, `sent_at`, `body` (hoặc `text`), `sender_person_id`, `forwarded`, `raw_event_id`
(xem `extract.GroupEvent`); `media_texts` là {message_id: [MediaText]} hoặc danh sách phẳng có `message_id`, `kind`, `text`.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from gh.biz.mem import vi_time
from gh.refinery.brain_contract import NEED_CELLS, BrainOut, FactProposed, MemoryUpdate, Need, Unit
from gh.textnorm import strip_accents

DEFAULT_TZ = vi_time.DEFAULT_TZ
ONGOING_WINDOW = timedelta(hours=48)
_LABEL_RE = re.compile(r"\[(?:đã che|OTP \d+ số|mật khẩu|token|CVV|thẻ …\d{4})\]")
TXN_JUST_DONE = timedelta(hours=24)
#: `quotes[].field` → loại `clean.media_texts` (body/caption nằm ở thân tin).
FIELD_TO_MEDIA_KIND = {"transcript": "transcript", "ocr": "ocr"}
#: Vai người gửi tin (đọc từ thuộc tính/khoá `sender_role` của tin; P2 điền theo `person_type`/`person_role`): "customer" | "staff" | "owner" | …;
#: thiếu ⇒ không kiểm được vai (giữ trường, ghi `*_role_unchecked`). Trường của KHÁCH (bậc khiếu nại, đòi Sếp, nhắc) không nhận tin của NV/Sếp.
NON_CUSTOMER_ROLES = frozenset({"staff", "owner"})
#: Từ khoá dịp cố định (§4F.2 `fixed_occasion`): phải có trong tin `occasion_message_id` (so trên chữ thường, giữ dấu).
OCCASION_RE = re.compile(r"khai trương|cưới|giỗ|hội chợ|tân gia|tất niên|sinh nhật|lễ kỷ niệm|triển lãm")
#: Từ cấm của `owner_action` (Q18, spec §4.2 "Việc Sếp cần làm"): so trên chữ bỏ dấu.
SELLING_RE = re.compile(r"chao hang|moi mua|upsell|ban them|goi y mua|khuyen mai de chot|chot don|chot sale")
ASKED_PRICE_RE = re.compile(r"\bgia\b|bao nhieu|bao gia")
MIN_QUOTE_COPY_CHARS = 20      # `owner_action` chứa một đoạn liền ≥ ngần này ký tự của trích dẫn ⇒ coi là chép lời khách


# ─── đầu vào ───────────────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MediaText:
    message_id: str
    kind: str                    # transcript | ocr | description
    text: str
    status: str = "ok"


def _g(o: Any, name: str, default: Any = None) -> Any:
    if isinstance(o, Mapping):
        return o.get(name, default)
    return getattr(o, name, default)


def norm(s: str | None) -> str:
    """Chuẩn hoá HẸP để so trích dẫn: NFC, gộp mọi khoảng trắng thành một dấu cách, bỏ khoảng trắng hai đầu."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s or "")).strip()


def _event_body(ev: Any) -> str:
    return str(_g(ev, "body") if _g(ev, "body") is not None else (_g(ev, "text") or ""))


def _index_media(media_texts: Mapping[str, Sequence[Any]] | Iterable[Any] | None) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    if media_texts is None:
        return out
    if isinstance(media_texts, Mapping):
        for mid, items in media_texts.items():
            out.setdefault(str(mid), []).extend(items)
        return out
    for it in media_texts:
        out.setdefault(str(_g(it, "message_id")), []).append(it)
    return out


# ─── kết quả ────────────────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class VerifiedQuote:
    message_id: str
    field: str
    text: str
    status: str                              # verified | absent
    span_start: int | None = None
    span_end: int | None = None
    sent_at: datetime | None = None
    raw_event_id: uuid.UUID | None = None
    sender_person_id: uuid.UUID | None = None
    reason: str | None = None                # message_unknown | field_missing | not_substring
    via_scrub: bool = False                  # khớp qua bản đã che thông tin đăng nhập (trích dẫn chứa nhãn)

    @property
    def span(self) -> dict[str, Any] | None:
        if self.status != "verified" or self.span_start is None or self.span_end is None:
            return None
        return {"source_field": self.field, "span_start": self.span_start, "span_end": self.span_end}


@dataclass
class VerifiedUnit:
    unit: Unit                               # đã lọc (person_ref lạ, owner_relevance ngoài Hồ sơ, cờ ongoing/urgent đã kiểm)
    quotes: list[VerifiedQuote]
    status: str                              # ok | absent
    msg_at: datetime | None = None           # sent_at MỚI NHẤT trong trích dẫn verified (CNS-137)
    event_at: datetime | None = None
    event_time_unparsed: bool = False
    event_granularity: str | None = None
    deadline_at: datetime | None = None
    deadline_lunar: bool = False
    deadline_ambiguous: bool = False
    deadline_unparsed: bool = False
    ongoing: bool = False
    explicit_urgent: bool = False
    money_vnd: int | None = None
    money_basis: str | None = None           # amount_text | harm.amount_text
    status_by_person_id: uuid.UUID | None = None
    status_at: datetime | None = None
    status_by_role: str | None = None        # vai người gửi tin `status_message_id` (nếu tin có `sender_role`); P2 kiểm NV
    need_fields_n: int | None = None         # r7: số ô `need` có trích dẫn verified (0–4); None = đơn vị không khai `need`
    txn_state: str | None = None             # pending | just_done | done_cannot_stop
    txn_at: datetime | None = None
    delivery_form: str = "direct"            # direct | warning_story | forwarded
    owner_relevance_kept: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    projects_signal: bool = True             # kind != null ⇒ chiếu tín hiệu; null ⇒ chỉ ghi sự thật/ghi nhớ

    @property
    def verified_quotes(self) -> list[VerifiedQuote]:
        return [q for q in self.quotes if q.status == "verified"]

    @property
    def absent(self) -> bool:
        return self.status == "absent"


@dataclass
class VerifiedFact:
    fact: FactProposed
    unit_ref: str
    evidence: list[VerifiedQuote]            # mỗi quote_ref còn hợp lệ ↔ trích dẫn verified của đơn vị
    speaker_person_id: uuid.UUID | None = None


@dataclass
class Verified:
    out: BrainOut
    units: list[VerifiedUnit] = field(default_factory=list)      # theo thứ tự đầu ra; gồm cả đơn vị absent (status="absent")
    facts: list[VerifiedFact] = field(default_factory=list)
    memory_updates: list[MemoryUpdate] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)              # mã lỗi, KHÔNG nội dung

    @property
    def usable_units(self) -> list[VerifiedUnit]:
        return [u for u in self.units if u.status == "ok"]

    @property
    def signal_units(self) -> list[VerifiedUnit]:
        return [u for u in self.usable_units if u.projects_signal]

    def bump(self, key: str, n: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + n


# ─── số tiền (code parse, §4D.2 (c)) ──────────────────────────────────────────────────────────────────────────────────────
_UNITS_VND = {"ty": 1_000_000_000, "ti": 1_000_000_000, "trieu": 1_000_000, "tr": 1_000_000, "cu": 1_000_000, "nghin": 1_000,
              "ngan": 1_000, "k": 1_000}
_MONEY_RE = re.compile(
    r"(?<![\w.,])(?P<num>\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s*(?P<unit>ty|ti|trieu|tr|cu|nghin|ngan|k)(?![a-z])(?:\s*(?P<half>ruoi)|(?P<tail0>\d{1,3})|\s+(?P<tail1>\d{3}))?(?!\d)")
_PLAIN_RE = re.compile(r"(?<![\w.,])(?P<num>\d{1,3}(?:[.,]\d{3})+|\d{4,})\s*(?:dong|vnd|vnđ|d|đ)\b")


def _fold(s: str) -> str:
    from gh.textnorm import strip_accents
    return strip_accents(norm(s).lower())


def _to_float(raw: str) -> float:
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw):
        return float(re.sub(r"[.,]", "", raw))
    return float(raw.replace(",", "."))


def parse_money_vnd(text: str | None) -> int | None:
    """"50 triệu" ⇒ 50_000_000; "1,2 tỷ" ⇒ 1_200_000_000; "3tr5" ⇒ 3_500_000; "800k" ⇒ 800_000; "25.000.000đ" ⇒ 25_000_000.
    Không có số tiền đọc được ⇒ None (không đoán)."""
    if not text:
        return None
    f = _fold(text)
    m = _MONEY_RE.search(f)
    if m:
        unit = _UNITS_VND[m.group("unit")]
        val = _to_float(m.group("num")) * unit
        if m.group("half"):
            val += unit / 2
        elif m.group("tail0") or m.group("tail1"):
            tail = m.group("tail0") or m.group("tail1")
            val += int(tail) / (10 ** len(tail)) * unit
        return int(round(val))
    m = _PLAIN_RE.search(f)
    if m:
        return int(_to_float(m.group("num")))
    return None


# ─── NGOẠI LỆ Q9 duy nhất: thông tin ĐĂNG NHẬP không tới model (Gen duyệt, review bảo mật 23/10) ─────────────────────────────
# Q9 = gửi model chat ĐẦY ĐỦ (tên, SĐT, số tiền, STK nguyên văn vì code cần STK để so lịch sử). Riêng mã OTP, mật khẩu, CVV, số thẻ đầy đủ
# (13–19 số, qua Luhn) và API key/token bị thay bằng NHÃN giữ được nghĩa ("[OTP 6 số]", "[mật khẩu]", "[CVV]", "[thẻ …1234]", "[token]";
# bí mật hệ thống: "[đã che]") để model vẫn biết "đã gửi OTP" mà không thấy giá trị. THỨ TỰ BẮT BUỘC: luật (a) (`credentials_entered`,
# `hard_rule_hit`) chạy trên bản GỐC của tin TRƯỚC khi che; che chỉ xảy ra lúc dựng prompt (không sửa tin gốc).
# MỘT hàm che duy nhất, `mask_credentials(text, secrets)`, chạy trên bản đã CHUẨN HOÁ (`norm`: NFC + gộp khoảng trắng) — prompt (`extract`) và
# bước kiểm trích dẫn (`_quote_status`) gọi đúng hàm này nên model trích đúng chữ nó thấy thì verify luôn tìm lại được và ánh xạ span về chữ gốc.
_CARD_RE = re.compile(r"(?<![\d-])\d(?:[\s.\-]?\d){12,18}(?!\d)")
#: UUID (id đối tượng trong kết quả tool): đoạn giữa toàn chữ số của UUIDv7 (vd `01a17668-4746-7979-…`) trông như số thẻ qua
#: Luhn — không bao giờ che bên trong UUID, nếu không model mất id thật và đề xuất bị chặn «id không có trong kết quả tool».
_UUID_RE = re.compile(r"(?<![0-9A-Fa-f-])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}(?![0-9A-Fa-f-])")
_STK_KW_RE = re.compile(r"(?<![a-z0-9])(?:stk|so tk|tk|tai khoan|so tai khoan|chuyen khoan|ck|ngan hang|nhan tien|ben thu huong)(?![a-z0-9])")
_CARD_KW_RE = re.compile(r"(?<![a-z0-9])(?:the|card|visa|master|mastercard|jcb|atm|cvv|cvc|credit|debit)(?![a-z0-9])")
STK_WINDOW = 40          # số ký tự trước chuỗi số được tìm từ khoá STK/thẻ
ZW_RE = "[​-‏⁠-⁤­﻿᠎‪-‮⁦-⁩]"

# Các luật bổ sung chạy trên bản đã BỎ DẤU (ánh xạ chỉ số về chữ có dấu bằng `_fold_map`).
_OTP_KW = (r"(?:otp|ma xac (?:minh|thuc|nhan)|ma bao mat|ma dang nhap|ma kich hoat|ma truy cap|ma (?:dung )?mot lan|ma 2fa|2fa|"
           r"verification code|security code|auth(?:entication)? code|passcode|pin)")
_DIGITS = r"\d(?:[ \-]?\d){3,7}"
_OTP_NEAR_RE = re.compile(rf"(?<![a-z0-9-]){_OTP_KW}(?![a-z0-9])[^\d\n]{{0,40}}?(?P<c>{_DIGITS})(?!\d)")
_OTP_BARE_RE = re.compile(rf"(?<![a-z0-9])(?:ma(?: so)?|code)\s*[:=]?\s*(?:la\s+)?(?P<c>{_DIGITS})(?!\d)")
_OTP_CTX_RE = re.compile(r"(?<![a-z0-9])(?:otp|xac (?:minh|thuc|nhan)|dang nhap|verification|2fa|ma bao mat|ma xac)(?![a-z0-9])")
_CVV_RE = re.compile(r"(?<![a-z0-9])(?:cvv2?|cvc2?|ma bao mat the)(?![a-z0-9])\D{0,10}?(?P<c>\d{3,4})(?!\d)")
_PW_KW = r"(?:mat khau|mk|pass(?:word|wd|code)?|pw|pwd)"
_PW_SEP_RE = re.compile(rf"(?<![a-z0-9-]){_PW_KW}(?![a-z0-9])(?:\s+[a-z]{{1,12}}){{0,3}}?\s*(?P<sep>[:=]|\bla\b|\bis\b)\s*(?P<v>[^\s\"'<>]{{4,128}})")
_PW_BARE_RE = re.compile(rf"(?<![a-z0-9-]){_PW_KW}(?![a-z0-9])\s+(?P<v>(?=\S*\d)\S{{6,128}})")


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def _fold_map(t: str) -> tuple[str, list[int]]:
    """Chữ bỏ dấu + chữ thường, kèm chỉ số gốc của từng ký tự (độ dài có thể đổi vì NFKD)."""
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(t):
        f = strip_accents(ch).lower()
        out.append(f)
        idx.extend([i] * len(f))
    return "".join(out), idx


def _is_stk_context(text_: str, start: int) -> bool:
    """Số tài khoản ngân hàng (không phải thẻ) nếu trong ~40 ký tự trước số có từ khoá STK và từ khoá THẺ không đứng sau nó."""
    win = strip_accents(text_[max(0, start - STK_WINDOW):start]).lower()
    stk = [m.start() for m in _STK_KW_RE.finditer(win)]
    if not stk:
        return False
    card = [m.start() for m in _CARD_KW_RE.finditer(win)]
    return not card or max(stk) > max(card)


def _credential_spans(text_: str, secrets: Sequence[str] = ()) -> list[tuple[int, int, str]]:
    from gh.chassis import masking as m_
    spans: list[tuple[int, int, str]] = []

    def add(m: re.Match[str], grp: int, label: str) -> None:
        if m.start(grp) >= 0 and m.end(grp) > m.start(grp):
            spans.append((m.start(grp), m.end(grp), label))

    def otp_label(code: str) -> str:
        n = len(re.sub(r"\D", "", code))
        return f"[OTP {n} số]"

    secret_spans: list[tuple[int, int, str]] = []
    for sec in secrets:                    # bí mật HỆ THỐNG: mọi chỗ xuất hiện, cả dạng thô lẫn dạng đã `norm()`
        for variant in {sec, norm(sec)}:
            if variant:
                for m in re.finditer(re.escape(variant), text_):
                    secret_spans.append((m.start(), m.end(), "[đã che]"))
    for rx in (m_._RE_OTP_AFTER, m_._RE_OTP_GAP, m_._RE_OTP_BEFORE, m_._RE_OTP_BEFORE_CODE):
        for m in rx.finditer(text_):
            add(m, 2, otp_label(m.group(2)))
    for m in m_._RE_OTP_BARE.finditer(text_):
        tail = re.search(r"\d{3}[ -]\d{3}$|\d+$", m.group(0))
        if tail:
            spans.append((m.start() + tail.start(), m.start() + tail.end(), otp_label(m.group(0)[tail.start():tail.end()])))
    for rx, need_mark in ((m_._RE_PW_EXPLICIT, False), (m_._RE_PW_IS, True)):
        for m in rx.finditer(text_):
            val = m.group(2)
            core = val.rstrip(".,;!?)")
            if len(core) >= 4 and not (need_mark and all(c.isalpha() for c in core)):
                spans.append((m.start(2), m.start(2) + len(core), "[mật khẩu]"))
    for rx in (m_._RE_URL_PARAM, m_._RE_URL_PATH):
        for m in rx.finditer(text_):
            add(m, 2, "[token]")
    for m in m_._RE_SECRET.finditer(text_):
        spans.append((m.start(), m.end(), "[token]"))
    # luật bổ sung trên bản bỏ dấu: pass|pw|mk, OTP có dấu cách/gạch, "mã: 6 số" khi có ngữ cảnh OTP, CVV
    folded, idx = _fold_map(text_)

    def fspan(m: re.Match[str], grp: str, label: str, trim: str = "") -> None:
        a, b = m.start(grp), m.end(grp)
        if trim:
            while b > a and folded[b - 1] in trim:
                b -= 1
        if b > a:
            spans.append((idx[a], idx[b - 1] + 1, label))

    for m in _OTP_NEAR_RE.finditer(folded):
        fspan(m, "c", otp_label(m.group("c")))
    if _OTP_CTX_RE.search(folded):
        for m in _OTP_BARE_RE.finditer(folded):
            fspan(m, "c", otp_label(m.group("c")))
    for m in _CVV_RE.finditer(folded):
        fspan(m, "c", "[CVV]")
    for m in _PW_SEP_RE.finditer(folded):
        v = m.group("v").rstrip(".,;!?)")
        if len(v) >= 4 and (m.group("sep") in (":", "=") or any(not c.isalpha() for c in v)):
            fspan(m, "v", "[mật khẩu]", trim=".,;!?)")
    for m in _PW_BARE_RE.finditer(folded):
        fspan(m, "v", "[mật khẩu]", trim=".,;!?)")
    # UUID thay bằng ký tự không phải số (cùng độ dài, giữ offset) trước khi quét thẻ: không che nhầm UUID mà thẻ thật đứng sát vẫn bị che
    card_scan = _UUID_RE.sub(lambda u: "x" * len(u.group(0)), text_)
    for m in _CARD_RE.finditer(card_scan):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits) and not _is_stk_context(text_, m.start()):
            spans.append((m.start(), m.end(), f"[thẻ …{digits[-4:]}]"))
    # Giải chồng lấn: bí mật HỆ THỐNG có ưu tiên TUYỆT ĐỐI — một cụm span chồng nhau mà có span bí mật thì che trọn cả cụm bằng `[đã che]`
    # (không bao giờ bỏ span bí mật để giữ span thông tin đăng nhập). Cụm không có bí mật: span dài nhất, đứng đầu thắng.
    tagged = [(a, b, lab, False) for a, b, lab in spans] + [(a, b, lab, True) for a, b, lab in secret_spans]
    tagged.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    out: list[tuple[int, int, str]] = []
    i = 0
    while i < len(tagged):
        j, end = i, tagged[i][1]
        while j + 1 < len(tagged) and tagged[j + 1][0] < end:
            j += 1
            end = max(end, tagged[j][1])
        cluster = tagged[i:j + 1]
        if any(t[3] for t in cluster):
            out.append((cluster[0][0], end, "[đã che]"))
        else:
            last_end = -1
            for a, b, lab, _ in cluster:
                if a >= last_end:
                    out.append((a, b, lab))
                    last_end = b
        i = j + 1
    return out


def scrub_credentials_map(text_: str, secrets: Sequence[str] = ()) -> tuple[str, list[tuple[int, int, int, int]]]:
    """(chữ đã che, [(đầu_mới, cuối_mới, đầu_cũ, cuối_cũ)]) trên CHÍNH chuỗi truyền vào (đã chuẩn hoá); để ánh xạ span về chữ gốc."""
    pieces: list[str] = []
    segs: list[tuple[int, int, int, int]] = []
    pos_old, new_len = 0, 0
    for a, b, lab in _credential_spans(text_, secrets):
        pieces.append(text_[pos_old:a])
        new_len += a - pos_old
        segs.append((new_len, new_len + len(lab), a, b))
        pieces.append(lab)
        new_len += len(lab)
        pos_old = b
    pieces.append(text_[pos_old:])
    return "".join(pieces), segs


def mask_credentials(text_: str | None, secrets: Sequence[str] = ()) -> str | None:
    """HÀM CHE DUY NHẤT (prompt và verify dùng chung): chuẩn hoá `norm` trước, rồi thay OTP / mật khẩu / CVV / số thẻ / token bằng nhãn giữ
    nghĩa; `secrets` (bí mật hệ thống) ⇒ `[đã che]`. Mọi thứ khác (tên, SĐT, tiền, STK) giữ NGUYÊN (Q9)."""
    return None if text_ is None else scrub_credentials_map(norm(text_), secrets)[0]


scrub_credentials = mask_credentials


def _map_back(segs: list[tuple[int, int, int, int]], new_pos: int, *, end: bool) -> int:
    delta = 0
    for ns, ne, os_, oe in segs:
        if end:
            if ns < new_pos <= ne:
                return oe
            if new_pos <= ns:
                break
        else:
            if ns <= new_pos < ne:
                return os_
            if new_pos < ns:
                break
        delta = oe - ne
    return new_pos + delta


# ─── kiểm trích dẫn ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def _quote_status(q: Any, ev_by_id: Mapping[str, Any], media: Mapping[str, list[Any]], secrets: Sequence[str] = ()) -> VerifiedQuote:
    mid, fld, text = q.message_id, q.field, norm(q.text)
    ev = ev_by_id.get(mid)
    base = VerifiedQuote(mid, fld, q.text, "absent")
    if ev is None:
        base.reason = "message_unknown"
        return base
    base.sent_at = _g(ev, "sent_at")
    base.raw_event_id = _g(ev, "raw_event_id")
    base.sender_person_id = _g(ev, "sender_person_id")
    descs = [str(_g(m, "text") or "") for m in media.get(mid, []) if _g(m, "kind") == "description" and _g(m, "status", "ok") == "ok"]
    if fld == "body":
        haystacks = [_event_body(ev)]
    elif fld == "caption":      # `caption` = chữ chú thích người gửi (thân tin) HOẶC mô tả ảnh do máy tạo (`clean.media_texts` kind description)
        haystacks = [_event_body(ev), *descs]
    else:
        kind = FIELD_TO_MEDIA_KIND.get(fld, fld)
        haystacks = [str(_g(m, "text") or "") for m in media.get(mid, []) if _g(m, "kind") == kind and _g(m, "status", "ok") == "ok"]
    if not haystacks or not any(h.strip() for h in haystacks):
        base.reason = "field_missing"
        return base
    if not text:
        base.reason = "not_substring"
        return base
    for h in haystacks:
        hn = norm(h)
        i = hn.find(text)
        if i >= 0:
            base.status, base.span_start, base.span_end = "verified", i, i + len(text)
            return base
    # Trích dẫn có thể chứa nhãn che ("[OTP 6 số]", "[đã che]"): so với bản đã che bằng ĐÚNG hàm của prompt (cùng `secrets`), khớp CHÍNH XÁC qua
    # bản đồ che, span ánh xạ về chữ gốc. Không có đường khớp lỏng; trích dẫn chỉ toàn nhãn (không có chữ thật) không đủ làm bằng chứng.
    if re.search(r"[^\W_]", _LABEL_RE.sub("", text)):
        for h in haystacks:
            hn = norm(h)
            sn, segs = scrub_credentials_map(hn, secrets)
            if segs and (i := sn.find(text)) >= 0:
                base.status, base.span_start, base.span_end = "verified", _map_back(segs, i, end=False), _map_back(segs, i + len(text), end=True)
                base.via_scrub = True
                return base
    base.reason = "not_substring"
    return base


def _message_text(ev: Any, media: Mapping[str, list[Any]]) -> str:
    parts = [_event_body(ev)] + [str(_g(m, "text") or "") for m in media.get(str(_g(ev, "message_id")), [])]
    return "\n".join(p for p in parts if p)


def _tzinfo(tz: str | Any) -> Any:
    from zoneinfo import ZoneInfo
    return ZoneInfo(tz) if isinstance(tz, str) else tz


def _as_ref(x: str) -> str:
    return x if x.startswith("f_") else f"f_{x}"


# ─── hàm chính ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def check(out: BrainOut, events: Sequence[Any], media_texts: Mapping[str, Sequence[Any]] | Iterable[Any] | None = None, *,
          now: datetime | None = None, tz: str = DEFAULT_TZ, thread_ref: str | None = None,
          profile_fact_ids: Collection[Any] | None = None, known_person_refs: Collection[str] | None = None,
          known_predicates: Collection[str] | None = None, secrets: Sequence[str] = ()) -> Verified:
    """Kiểm một `BrainOut` với tin của lượt (gồm cả cửa sổ gần). `profile_fact_ids` = id sự thật có trong khối Hồ sơ đã gửi cho model
    (`ProfileSnapshot.fact_ids()` hoặc chuỗi `f_…`); `None` ⇒ không có Hồ sơ nào được gửi ⇒ mọi `owner_relevance` bị bỏ. `known_person_refs`
    / `known_predicates` `None` ⇒ không lọc. `secrets` = bí mật hệ thống đã thay `[đã che]` trong prompt. `now` mặc định giờ máy (L2 truyền `clock.now()` để đồng hồ giả chạy được)."""
    now = (now or datetime.now(UTC))
    zone = _tzinfo(tz)
    res = Verified(out=out)
    if thread_ref is not None and out.thread_ref != thread_ref:
        res.errors.append("thread_ref_mismatch")
        res.bump("thread_ref_mismatch")
        return res
    ev_by_id = {str(_g(e, "message_id")): e for e in events}
    media = _index_media(media_texts)
    prof_ids = {_as_ref(str(i)) for i in (profile_fact_ids or [])}
    seen_refs: set[str] = set()
    unit_by_ref: dict[str, VerifiedUnit] = {}
    kind_by_event: set[tuple[str, str]] = set()
    for u in out.units:
        notes: list[str] = list(u.normalization_notes)
        if u.unit_ref in seen_refs:
            res.errors.append("duplicate_unit_ref")
            res.bump("duplicate_unit_ref")
            continue
        seen_refs.add(u.unit_ref)
        vq = [_quote_status(q, ev_by_id, media, secrets) for q in u.quotes]
        vu = VerifiedUnit(unit=u, quotes=vq, status="ok", notes=notes, projects_signal=u.kind is not None)
        if any(q.status == "absent" for q in vq):
            vu.status = "absent"
            res.bump("absent_units")
            res.bump("absent_quotes", sum(1 for q in vq if q.status == "absent"))
            for q in vq:
                if q.status == "absent" and q.reason:
                    res.bump(f"quote_{q.reason}")
            res.units.append(vu)
            continue
        if u.kind is not None:
            key = (u.event_ref, u.kind)
            if key in kind_by_event:
                notes.append("duplicate_kind_for_event")
                res.bump("duplicate_kind_for_event")
            kind_by_event.add(key)
        _verify_unit(res, vu, ev_by_id, media, now, zone, tz, prof_ids, known_person_refs)
        unit_by_ref[u.unit_ref] = vu
        res.units.append(vu)
    _verify_facts(res, out.facts_proposed, unit_by_ref, ev_by_id, known_predicates)
    for mu in out.memory_updates:
        ids = [i for i in mu.message_ids if i in ev_by_id]
        if len(ids) != len(mu.message_ids):
            res.bump("memory_update_message_dropped", len(mu.message_ids) - len(ids))
        res.memory_updates.append(mu.model_copy(update={"message_ids": ids}))
    return res


def _verify_unit(res: Verified, vu: VerifiedUnit, ev_by_id: Mapping[str, Any], media: Mapping[str, list[Any]], now: datetime,
                 zone: Any, tz: str, prof_ids: set[str], known_person_refs: Collection[str] | None) -> None:
    u = vu.unit
    vq = vu.quotes
    q_by_msg = {q.message_id: q for q in vq}
    sent = [q.sent_at for q in vq if q.sent_at is not None]
    vu.msg_at = max(sent) if sent else None
    notes = vu.notes

    # người: person_ref lạ ⇒ chỉ dùng surface
    if known_person_refs is not None:
        known = set(known_person_refs)
        people = []
        for p in u.people:
            if p.person_ref is not None and p.person_ref not in known:
                notes.append("person_ref_unknown_dropped")
                res.bump("person_ref_unknown")
                p = p.model_copy(update={"person_ref": None})
            people.append(p)
        u = u.model_copy(update={"people": people})

    # ongoing (#1–#2): ids ⊆ quotes (contract đã lọc), verified, sent_at ≥ now − 48 giờ
    kept_ongoing = []
    for mid in u.ongoing_message_ids:
        q = q_by_msg.get(mid)
        if q is None or q.status != "verified" or q.sent_at is None:
            notes.append(f"ongoing_id_dropped:{mid}")
        elif q.sent_at < now - ONGOING_WINDOW:
            notes.append(f"ongoing_id_expired:{mid}")
        else:
            kept_ongoing.append(mid)
    vu.ongoing = bool(u.flags.ongoing and kept_ongoing)
    if u.flags.ongoing and not vu.ongoing:
        notes.append("ongoing_forced_false")
        res.bump("ongoing_forced_false")
    u = u.model_copy(update={"ongoing_message_ids": kept_ongoing, "flags": u.flags.model_copy(update={"ongoing": vu.ongoing})})

    # explicit_urgent (#8): cờ chỉ giữ khi trong tin trích dẫn có từ vựng "gấp"
    vu.explicit_urgent = u.flags.explicit_urgent
    if u.flags.explicit_urgent:
        blob = "\n".join(_message_text(ev_by_id[q.message_id], media) for q in vq if q.message_id in ev_by_id)
        if not vi_time.has_urgent(blob):
            vu.explicit_urgent = False
            notes.append("explicit_urgent_unverified")
            res.bump("explicit_urgent_unverified")
            u = u.model_copy(update={"flags": u.flags.model_copy(update={"explicit_urgent": False})})

    # event_at (#3)
    vu.event_at = vu.msg_at
    et = u.event_time
    if et is not None and et.text:
        anchor_ev = ev_by_id.get(et.anchor_message_id or "")
        anchor_at = _g(anchor_ev, "sent_at") if anchor_ev is not None else None
        ok_text = anchor_ev is not None and norm(et.text) in norm(_message_text(anchor_ev, media))
        inst = vi_time.parse_past(et.text, anchor_at, tz) if (ok_text and anchor_at is not None) else None
        if inst is None:
            vu.event_time_unparsed = True
            notes.append("event_time_unparsed" if ok_text else "event_time_text_mismatch")
            res.bump("event_time_unparsed")
        else:
            vu.event_at = min(inst.latest, vu.msg_at) if vu.msg_at is not None else inst.latest
            vu.event_granularity = inst.granularity

    # deadline_at
    d = u.deadline
    if d.text:
        anchor_ev = ev_by_id.get(d.anchor_message_id or "")
        anchor_at = _g(anchor_ev, "sent_at") if anchor_ev is not None else None
        if anchor_at is None:
            anchor_at = vu.msg_at
            notes.append("deadline_anchor_fallback")
        due = vi_time.parse(d.text, anchor_at, tz) if anchor_at is not None else vi_time.Due(at=None)
        if due.at is None:
            vu.deadline_unparsed = True
            notes.append("deadline_unparsed")
            res.bump("deadline_unparsed")
        else:
            vu.deadline_at, vu.deadline_lunar, vu.deadline_ambiguous = due.at, due.lunar, due.ambiguous
            if due.recurrence is not None:
                notes.append("deadline_recurring")

    # tiền (code parse); số phải có trong tin được trích dẫn
    blob_fold = _fold("\n".join(_message_text(ev_by_id[q.message_id], media) for q in vq if q.message_id in ev_by_id))
    for basis, txt in (("amount_text", u.amount_text), ("harm.amount_text", u.harm.amount_text if u.harm else None)):
        if not txt:
            continue
        val = parse_money_vnd(txt)
        if val is None:
            continue
        if _fold(txt) not in blob_fold:
            notes.append("amount_text_not_in_messages")
            res.bump("amount_text_not_in_messages")
            continue
        vu.money_vnd, vu.money_basis = val, basis
        break

    # status (#4)
    if u.status_message_id:
        sq = q_by_msg.get(u.status_message_id)
        if sq is not None and sq.status == "verified":
            vu.status_by_person_id, vu.status_at = sq.sender_person_id, sq.sent_at

    u = _verify_v2(res, vu, u, q_by_msg, ev_by_id, media)

    # txn (#10)
    if u.txn is not None and u.txn.state_hint and u.txn.message_id:
        tq = q_by_msg.get(u.txn.message_id)
        if tq is not None and tq.status == "verified":
            vu.txn_at = tq.sent_at
            if u.txn.state_hint == "requested":
                vu.txn_state = "pending"
            else:
                ref = min([t for t in (vu.event_at, vu.txn_at) if t is not None], default=None)
                vu.txn_state = "just_done" if ref is None or max(timedelta(0), now - ref) < TXN_JUST_DONE else "done_cannot_stop"
        else:
            notes.append("txn_message_unverified")

    # delivery_form (#9): cờ chuyển tiếp của nền tảng thắng
    fwd = any(bool(_g(ev_by_id[q.message_id], "forwarded")) for q in vq if q.message_id in ev_by_id)
    vu.delivery_form = "forwarded" if fwd else u.delivery_form_hint

    # owner_relevance: chỉ id có trong khối Hồ sơ đã gửi
    rel = []
    for r in u.owner_relevance:
        if _as_ref(r.fact_id) in prof_ids:
            rel.append(r)
        else:
            res.bump("owner_relevance_dropped")
            notes.append("owner_relevance_dropped")
    vu.owner_relevance_kept = [r.fact_id for r in rel]
    u = u.model_copy(update={"owner_relevance": rel, "owner_basis": "co_ho_so" if rel else "thieu_ho_so"})
    vu.unit = u


def _verified_msg(q_by_msg: Mapping[str, VerifiedQuote], mid: str | None) -> VerifiedQuote | None:
    q = q_by_msg.get(mid or "")
    return q if q is not None and q.status == "verified" else None


def _verify_v2(res: Verified, vu: VerifiedUnit, u: Unit, q_by_msg: Mapping[str, VerifiedQuote], ev_by_id: Mapping[str, Any],
               media: Mapping[str, list[Any]]) -> Unit:
    """Kiểm trường `brain-out/2` (§4F.2, bản 3 §2.2 cột "Code kiểm"). Trường không kiểm được ⇒ về MẶC ĐỊNH + ghi chú (không loại đơn vị)."""
    notes = vu.notes
    upd: dict[str, Any] = {}

    def role_of(q: VerifiedQuote) -> str | None:
        ev = ev_by_id.get(q.message_id)
        r = _g(ev, "sender_role") if ev is not None else None
        return str(r) if r else None

    def customer_msg(field_name: str, mid: str | None) -> bool:
        """Tin `mid` phải verified và do KHÁCH gửi (không phải NV/Sếp); thiếu vai ⇒ chấp nhận nhưng ghi `*_role_unchecked`."""
        q = _verified_msg(q_by_msg, mid)
        if q is None:
            notes.append(f"{field_name}_unverified")
            res.bump(f"{field_name}_unverified")
            return False
        role = role_of(q)
        if role is None:
            notes.append(f"{field_name}_role_unchecked")
            res.bump(f"{field_name}_role_unchecked")
            return True
        if role in NON_CUSTOMER_ROLES:
            notes.append(f"{field_name}_sender_not_customer")
            res.bump(f"{field_name}_sender_not_customer")
            return False
        return True

    if u.complaint_stage is not None and not customer_msg("stage_message", u.stage_message_id):
        upd.update(complaint_stage=None, stage_message_id=None)
    if u.asks_for_owner and not customer_msg("asks_message", u.asks_message_id):
        upd.update(asks_for_owner=False, asks_message_id=None)
    if u.nudge_message_id is not None and not customer_msg("nudge_message", u.nudge_message_id):
        upd["nudge_message_id"] = None

    # status `dang_xu_ly`: cần tin chứng minh verified (vai NV do P2 kiểm theo `status_by_role`/`status_by_person_id`); không ⇒ `dang_mo`
    sq = _verified_msg(q_by_msg, u.status_message_id)
    if sq is not None:
        vu.status_by_role = role_of(sq)
    if u.status_hint == "dang_xu_ly" and sq is None:
        notes.append("dang_xu_ly_unverified")
        res.bump("dang_xu_ly_unverified")
        upd.update(status_hint="dang_mo", status_message_id=None)

    # need: chỉ ô có trích dẫn verified VÀ chữ của ô nằm nguyên văn trong tin đó mới được giữ; đếm ô giữ lại
    if u.need is not None:
        kept: dict[str, Any] = {}
        for name in NEED_CELLS:
            cell = getattr(u.need, name)
            nq = _verified_msg(q_by_msg, cell.message_id) if cell is not None else None
            if cell is not None and nq is not None and norm(cell.text) in norm(_message_text(ev_by_id[nq.message_id], media)):
                kept[name] = cell
            else:
                if cell is not None:
                    notes.append(f"need_{name}_unverified")
                    res.bump("need_cell_unverified")
                kept[name] = None
        vu.need_fields_n = sum(1 for c in kept.values() if c is not None)
        upd["need"] = Need(**kept)

    # fixed_occasion: từ khoá dịp phải có trong tin `occasion_message_id` đã kiểm
    if u.fixed_occasion:
        oq = _verified_msg(q_by_msg, u.occasion_message_id)
        if oq is None or not OCCASION_RE.search(norm(_message_text(ev_by_id[oq.message_id], media)).lower()):
            notes.append("fixed_occasion_unverified")
            res.bump("fixed_occasion_unverified")
            upd.update(fixed_occasion=False, occasion_message_id=None)

    # owner_action (Q18): không chép lời trong tin, không từ chào bán; "báo giá" chỉ khi khách đã hỏi giá
    if u.owner_action is not None:
        reason = _owner_action_problem(u.owner_action, [q for q in q_by_msg.values() if q.status == "verified"], ev_by_id, media)
        if reason is not None:
            notes.append(f"owner_action_dropped:{reason}")
            res.bump("owner_action_dropped")
            upd["owner_action"] = None
    return u.model_copy(update=upd) if upd else u


def _owner_action_problem(action: str, quotes: Sequence[VerifiedQuote], ev_by_id: Mapping[str, Any], media: Mapping[str, list[Any]]) -> str | None:
    a = norm(action)
    for q in quotes:
        qt = norm(q.text)
        if len(qt) >= MIN_QUOTE_COPY_CHARS or len(qt) >= len(a):
            m = SequenceMatcher(None, a.lower(), qt.lower(), autojunk=False).find_longest_match(0, len(a), 0, len(qt))
            if m.size >= min(MIN_QUOTE_COPY_CHARS, len(qt)):
                return "copies_quote"
    af = _fold(a)
    if SELLING_RE.search(af):
        return "selling_verb"
    if "bao gia" in af:
        blob = _fold("\n".join(_message_text(ev_by_id[q.message_id], media) for q in quotes if q.message_id in ev_by_id))
        if not ASKED_PRICE_RE.search(blob):
            return "quote_price_not_asked"
    return None


def _verify_facts(res: Verified, facts: Sequence[FactProposed], unit_by_ref: Mapping[str, VerifiedUnit], ev_by_id: Mapping[str, Any],
                  known_predicates: Collection[str] | None) -> None:
    known = set(known_predicates) if known_predicates is not None else None
    for f in facts:
        vu = unit_by_ref.get(f.unit_ref)
        if vu is None or vu.status != "ok":
            res.bump("fact_dropped_unit_absent")
            continue
        if known is not None and f.predicate not in known:
            res.bump("unknown_predicate")
            continue
        by_msg = {q.message_id: q for q in vu.verified_quotes}
        ev = [by_msg[m] for m in dict.fromkeys(f.quote_refs) if m in by_msg]
        if not ev:
            res.bump("fact_dropped_no_verified_quote")
            continue
        res.facts.append(VerifiedFact(f, f.unit_ref, ev, ev[0].sender_person_id))


__all__ = ["MediaText", "Verified", "VerifiedUnit", "VerifiedQuote", "VerifiedFact", "check", "norm", "parse_money_vnd",
           "ONGOING_WINDOW", "NON_CUSTOMER_ROLES"]
