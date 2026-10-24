# ruff: noqa: E501
"""Bộ đọc file xuất lịch sử chat: WhatsApp (Android + iOS) và Zalo PC (spec §6 A7, §4 "Khởi động lạnh"). HÀM THUẦN — không mạng, không DB.

Mỗi bộ đọc trả `ParseResult`: danh sách `Line` (ngày giờ địa phương CHƯA gắn múi giờ, người gửi theo TÊN, nội dung, loại) + thống kê bỏ qua.
Tin nhiều dòng được nối vào tin trước. Dòng hệ thống (không có người gửi: mã hoá đầu cuối, thêm/xoá thành viên, đổi tên nhóm…) bị bỏ và đếm.

WhatsApp — hai biến thể (cả giờ 24h và 12h AM/PM):
    Android: `12/10/2026, 14:05 - Tên: nội dung`      iOS: `[12/10/2026, 14:05:33] Tên: nội dung`
  Thứ tự ngày/tháng: tự đoán theo toàn tệp (có số > 12 ở vế đầu ⇒ ngày/tháng; ở vế sau ⇒ tháng/ngày); mơ hồ hoàn toàn ⇒ ngày/tháng (Việt Nam) và
  cờ `date_order_ambiguous` (ép bằng `date_order`). Đính kèm: `<Media omitted>`, `IMG-….jpg (file attached)`, `<attached: …>`, `image omitted`,
  `audio omitted`, `PTT-….opus (file attached)`, `sticker omitted` và bản tiếng Việt tương đương.

Zalo PC — [CHƯA VERIFY với tệp xuất thật của Zalo PC; không có tệp mẫu trong gói L6]. Hỗ trợ các khuôn thường gặp và cách thoát hiểm:
    `[HH:MM(:SS) DD/MM/YYYY] Tên: nội dung`  ·  `DD/MM/YYYY HH:MM(:SS) Tên: nội dung`  ·  `DD/MM/YYYY, HH:MM(:SS) - Tên: nội dung`
    và khuôn "tiêu đề ngày": dòng `Thứ …, DD/MM/YYYY` (hoặc `---- DD/MM/YYYY ----`) rồi các dòng `HH:MM(:SS) Tên: nội dung`.
  Khuôn khác ⇒ `custom_pattern` (regex có nhóm đặt tên `date`, `time`, `sender`, `text`; tuỳ chọn `date_order`). Không nhận ra dòng nào ⇒ ParseError (không đoán bừa).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

INVISIBLE = dict.fromkeys(map(ord, "‎‏‪‫‬‭‮﻿"), None)
SPACES = str.maketrans({" ": " ", " ": " ", " ": " "})


class ParseError(ValueError):
    pass


#: Trần chống tệp/dòng khổng lồ làm treo hoặc ăn hết RAM máy Sếp.
MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_LINE_CHARS = 20_000


@dataclass
class Line:
    ts: datetime                # giờ địa phương, naive
    sender: str
    text: str
    kind: str = "text"          # text | image | voice | file | sticker
    line_no: int = 0


@dataclass
class ParseResult:
    format: str
    messages: list[Line] = field(default_factory=list)
    system_skipped: int = 0
    deleted_skipped: int = 0
    unparsed_lines: int = 0
    truncated_lines: int = 0
    date_order: str = "dmy"
    date_order_ambiguous: bool = False
    notes: list[str] = field(default_factory=list)


def _clean(line: str) -> str:
    return line.translate(INVISIBLE).translate(SPACES).rstrip("\r\n")


def _lines(text: str, res_truncated: list[int]) -> list[str]:
    """Tách dòng + chuẩn hoá; dòng dài quá `MAX_LINE_CHARS` bị cắt (đếm vào `truncated_lines`) thay vì ăn bộ nhớ/regex."""
    out = []
    for x in text.split("\n"):
        c = _clean(x)
        if len(c) > MAX_LINE_CHARS:
            c = c[:MAX_LINE_CHARS]
            res_truncated[0] += 1
        out.append(c)
    return out


# ─── Ngày giờ ────────────────────────────────────────────────────────────────────────────────────────────────────────────

def parse_time(t: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp]\.?[Mm]\.?)?\s*", t)
    if not m:
        raise ValueError(f"giờ lạ: {t!r}")
    h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    ap = m.group(4)
    if ap:
        pm = ap[0].lower() == "p"
        h = (h % 12) + (12 if pm else 0)
    if not (0 <= h < 24 and 0 <= mi < 60 and 0 <= s < 60):
        raise ValueError(f"giờ ngoài khoảng: {t!r}")
    return h, mi, s


def detect_date_order(date_strs: list[str]) -> tuple[str, bool]:
    """('dmy'|'mdy', mơ hồ?) — duyệt MỌI ngày trong tệp: vế đầu > 12 ⇒ dmy; vế sau > 12 ⇒ mdy; không có ⇒ dmy + mơ hồ."""
    first_big = second_big = False
    for d in date_strs:
        p = re.split(r"[/.\-]", d.strip())
        if len(p) < 3:
            continue
        a, b = int(p[0]), int(p[1])
        first_big |= a > 12
        second_big |= b > 12
    if first_big and not second_big:
        return "dmy", False
    if second_big and not first_big:
        return "mdy", False
    if first_big and second_big:
        return "dmy", True        # mâu thuẫn: tệp trộn khuôn — giữ dmy, báo mơ hồ
    return "dmy", True


def make_dt(date: str, time: str, order: str) -> datetime:
    p = re.split(r"[/.\-]", date.strip())
    a, b, y = int(p[0]), int(p[1]), int(p[2])
    if y < 100:
        y += 2000
    d, m = (a, b) if order == "dmy" else (b, a)
    h, mi, s = parse_time(time)
    return datetime(y, m, d, h, mi, s)


# ─── Phân loại nội dung ─────────────────────────────────────────────────────────────────────────────────────────────────

MEDIA_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("voice", re.compile(r"^(?:PTT-[\w\-]+\.\w+\s*\((?:file attached|đã đính kèm tệp|tệp đính kèm)\)|<attached: [\w\-]*(?:PTT|AUDIO)[\w\-\.]*>|(?:audio|voice(?: message)?|âm thanh|tin nhắn thoại|thoại)\s*(?:omitted|đã bị bỏ qua|bị bỏ qua|đã bỏ qua)|\[?(?:Voice|Thoại|Tin nhắn thoại)\]?\s*(?:\d+:\d+|\(\d+s\))?)$", re.I)),
    ("image", re.compile(r"^(?:(?:IMG|PHOTO|VID)-[\w\-]+\.\w+\s*\((?:file attached|đã đính kèm tệp|tệp đính kèm)\)|<attached: [\w\-\.]*(?:PHOTO|IMG|VID)[\w\-\.]*>|(?:image|photo|video|ảnh|hình ảnh|video)\s*(?:omitted|đã bị bỏ qua|bị bỏ qua|đã bỏ qua)|\[?(?:Hình ảnh|Ảnh|Photo)\]?)$", re.I)),
    ("sticker", re.compile(r"^(?:sticker\s*(?:omitted|đã bị bỏ qua|bị bỏ qua)|\[?Sticker\]?)$", re.I)),
    ("file", re.compile(r"^(?:<Media omitted>|<Phương tiện (?:đã )?(?:bị )?bỏ qua>|<Đã bỏ qua phương tiện>|<attached: [^>]+>|[\w\-\. ]+\.\w{2,5}\s*\((?:file attached|đã đính kèm tệp|tệp đính kèm)\)|\[?(?:Tệp|File)\]?\s*[\w\-\. ]*)$", re.I)),
)
DELETED_RE = re.compile(r"^(?:This message was deleted|You deleted this message|Tin nhắn này đã bị xóa|Tin nhắn này đã bị xoá|Bạn đã xóa tin nhắn này|Bạn đã xoá tin nhắn này|Tin nhắn đã được thu hồi|Bạn đã thu hồi một tin nhắn|.* đã thu hồi (?:một )?tin nhắn)\.?$", re.I)


def classify(body: str) -> tuple[str, str]:
    """(kind, text) — media xuất ra KHÔNG có nội dung: kind theo loại, text rỗng. `deleted` báo bằng kind 'deleted'."""
    b = body.strip()
    if DELETED_RE.match(b):
        return "deleted", ""
    for kind, rx in MEDIA_RULES:
        if rx.match(b):
            return kind, ""
    return "text", body


# ─── WhatsApp ───────────────────────────────────────────────────────────────────────────────────────────────────────────

WA_ANDROID = re.compile(r"^(?P<date>\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}),?\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AaPp]\.?[Mm]\.?)?)\s+-\s+(?P<rest>.*)$")
WA_IOS = re.compile(r"^\[(?P<date>\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}),?\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AaPp]\.?[Mm]\.?)?)\]\s+(?P<rest>.*)$")


def _split_sender(rest: str) -> tuple[str, str] | None:
    """`Tên: nội dung` — tên không chứa ': '; dòng hệ thống không có người gửi thì None."""
    i = rest.find(": ")
    if i <= 0 or i > 80:
        return None
    return rest[:i].strip(), rest[i + 2:]


def parse_whatsapp(text: str, date_order: str | None = None) -> ParseResult:
    trunc = [0]
    lines = _lines(text, trunc)
    heads: list[tuple[int, re.Match[str]]] = []
    for i, ln in enumerate(lines):
        m = WA_IOS.match(ln) or WA_ANDROID.match(ln)
        if m:
            heads.append((i, m))
    if not heads:
        raise ParseError("whatsapp: không nhận ra dòng nào theo khuôn Android/iOS")
    order, amb = (date_order, False) if date_order else detect_date_order([m.group("date") for _, m in heads])
    res = ParseResult(format="whatsapp", date_order=order, date_order_ambiguous=amb, truncated_lines=trunc[0])
    if amb:
        res.notes.append("thứ tự ngày/tháng mơ hồ (mọi ngày đều ≤ 12): đã giả định ngày/tháng — ép bằng --date-order mdy nếu sai")
    cur: Line | None = None
    head_by_line = dict(heads)        # O(n): trước đây tìm tuyến tính cho mỗi dòng ⇒ O(n²) trên tệp lớn
    for i, ln in enumerate(lines):
        if i in head_by_line:
            m = head_by_line[i]
            if cur is not None:
                _finish(res, cur)
                cur = None
            parts = _split_sender(m.group("rest"))
            if parts is None:
                res.system_skipped += 1
                continue
            try:
                ts = make_dt(m.group("date"), m.group("time"), order)
            except ValueError:
                res.unparsed_lines += 1
                continue
            cur = Line(ts=ts, sender=parts[0], text=parts[1], line_no=i + 1)
        elif cur is not None:
            cur.text += "\n" + ln
        elif ln.strip():
            res.unparsed_lines += 1
    if cur is not None:
        _finish(res, cur)
    return res


def _finish(res: ParseResult, ln: Line) -> None:
    ln.text = ln.text.rstrip("\n")
    kind, body = classify(ln.text)
    if kind == "deleted":
        res.deleted_skipped += 1
        return
    ln.kind, ln.text = kind, body
    res.messages.append(ln)


# ─── Zalo PC ────────────────────────────────────────────────────────────────────────────────────────────────────────────

D = r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}"
T = r"\d{1,2}:\d{2}(?::\d{2})?"
ZALO_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"^\[(?P<time>{T})\s+(?P<date>{D})\]\s*(?P<sender>[^:]{{1,80}}?):\s?(?P<text>.*)$"),
    re.compile(rf"^\[(?P<date>{D}),?\s+(?P<time>{T})\]\s*(?P<sender>[^:]{{1,80}}?):\s?(?P<text>.*)$"),
    re.compile(rf"^(?P<date>{D}),?\s+(?P<time>{T})\s+-\s+(?P<sender>[^:]{{1,80}}?):\s?(?P<text>.*)$"),
    re.compile(rf"^(?P<date>{D}),?\s+(?P<time>{T})\s+(?P<sender>[^:]{{1,80}}?):\s?(?P<text>.*)$"),
)
ZALO_DAY_HEADER = re.compile(rf"^\W*(?:(?:Thứ \w+|Chủ nhật|Hôm nay|Hôm qua)[,\s]*)?(?P<date>{D})\W*$", re.I)
ZALO_TIME_LINE = re.compile(rf"^(?P<time>{T})\s+(?P<sender>[^:]{{1,80}}?):\s?(?P<text>.*)$")


def parse_zalo(text: str, date_order: str | None = None, custom_pattern: str | None = None) -> ParseResult:
    trunc = [0]
    lines = _lines(text, trunc)
    patterns: tuple[re.Pattern[str], ...] = (re.compile(custom_pattern),) if custom_pattern else ZALO_PATTERNS
    if custom_pattern:
        missing = {"date", "time", "sender", "text"} - set(patterns[0].groupindex)
        if missing:
            raise ParseError(f"custom_pattern thiếu nhóm đặt tên: {sorted(missing)}")
    # 1) khuôn dòng đủ: chọn khuôn khớp nhiều dòng nhất
    best, best_n = None, 0
    for p in patterns:
        n = sum(1 for ln in lines if p.match(ln))
        if n > best_n:
            best, best_n = p, n
    entries: list[tuple[int, str, str, str, str]] = []        # (dòng, date, time, sender, text)
    if best is not None:
        for i, ln in enumerate(lines):
            m = best.match(ln)
            if m:
                entries.append((i, m.group("date"), m.group("time"), m.group("sender").strip(), m.group("text")))
    else:
        # 2) khuôn tiêu đề ngày
        cur_date: str | None = None
        for i, ln in enumerate(lines):
            h = ZALO_DAY_HEADER.match(ln)
            if h:
                cur_date = h.group("date")
                continue
            m = ZALO_TIME_LINE.match(ln)
            if m and cur_date:
                entries.append((i, cur_date, m.group("time"), m.group("sender").strip(), m.group("text")))
    if not entries:
        raise ParseError("zalo: không nhận ra dòng nào (khuôn xuất chưa được hỗ trợ — dùng custom_pattern)")
    order, amb = (date_order, False) if date_order else detect_date_order([e[1] for e in entries])
    res = ParseResult(format="zalo", date_order=order, date_order_ambiguous=amb, truncated_lines=trunc[0])
    if amb:
        res.notes.append("thứ tự ngày/tháng mơ hồ: đã giả định ngày/tháng")
    res.notes.append("khuôn Zalo PC CHƯA verify với tệp xuất thật")
    starts = {e[0] for e in entries}
    cur: Line | None = None
    byline = {e[0]: e for e in entries}
    for i, ln in enumerate(lines):
        if i in starts:
            if cur is not None:
                _finish(res, cur)
                cur = None
            _, d, t, s, body = byline[i]
            try:
                ts = make_dt(d, t, order)
            except ValueError:
                res.unparsed_lines += 1
                continue
            cur = Line(ts=ts, sender=s, text=body, line_no=i + 1)
        elif cur is not None and not ZALO_DAY_HEADER.match(ln):
            cur.text += "\n" + ln
        elif ln.strip() and not ZALO_DAY_HEADER.match(ln):
            res.unparsed_lines += 1
    if cur is not None:
        _finish(res, cur)
    return res


def sniff_format(text: str) -> str:
    """Đoán 'whatsapp' | 'zalo' theo vài chục dòng đầu: WA có ` - ` hoặc `[ngày, giờ]`; Zalo là các khuôn còn lại."""
    head = [_clean(x) for x in text.split("\n")[:200]]
    wa = sum(1 for ln in head if WA_IOS.match(ln) or WA_ANDROID.match(ln))
    za = max(max(sum(1 for ln in head if p.match(ln)) for p in ZALO_PATTERNS), sum(1 for ln in head if ZALO_TIME_LINE.match(ln)))
    if wa == 0 and za == 0:
        raise ParseError("không nhận ra khuôn xuất (không phải WhatsApp, cũng không khớp khuôn Zalo đã biết)")
    # Android WA và khuôn Zalo số 3 giống nhau; WA ưu tiên khi có "- " + ", " sau ngày hoặc `[...]`
    return "whatsapp" if wa >= za else "zalo"


def fold_name(s: str) -> str:
    return re.sub(r"\s+", " ", "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn").replace("đ", "d")).strip()
