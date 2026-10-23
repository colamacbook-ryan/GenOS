# ruff: noqa: E501
"""Đọc cụm thời gian tiếng Việt (spec §4D.2 (c), §4F.1 #7–#8, spike S2): CODE tính ngày, model không bao giờ ước.

- `parse(text, anchor, tz) -> Due` — thời hạn/thời điểm TƯƠNG LAI (hoặc đang nói tới): "thứ 6", "trước 5h chiều", "mùng 3 tháng 2 âm",
  "mỗi sáng 8h", "ngày 5 hàng tháng". Không đọc được ⇒ `Due.at is None`.
- `parse_past(text, anchor, tz) -> Instant | None` — thời điểm sự việc ĐÃ xảy ra: "hôm qua", "chiều hôm kia", "tuần trước", "9h"
  đọc lúc 8:50 ⇒ 9:00 hôm trước. Trả khoảng `[earliest, latest]` và `granularity ∈ minute | hour | part_of_day | day | week`
  (cụm dài hơn một tuần như "tháng trước" cũng ghi `week`). Chiều làm tròn: mọi cổng dùng `latest` (tuổi nhỏ nhất ⇒ 🔴 giữ lâu nhất,
  fail-high, §4.2.1 (a)); KHÔNG BAO GIỜ trả thời điểm sau `anchor`. Không đọc được ⇒ `None` (bên gọi đặt `event_at = msg_at`,
  `event_time_unparsed`).
- `URGENT_LEXICON` + `has_urgent(text)` — MỘT nguồn từ vựng "gấp" cho `verify.py` (kiểm `explicit_urgent`) và khối từ vựng của kỹ năng v2.1.

Quy ước mốc: tuần bắt đầu thứ hai. Cụm chỉ có ngày ⇒ hạn là 23:59:59 ngày đó; chỉ có buổi ⇒ hạn là cuối buổi (sáng 12:00, trưa 13:00,
chiều 18:00, tối 22:00, đêm 24:00); có giờ ⇒ đúng giờ đó. Cụm chỉ có giờ ("9h") ⇒ lần xuất hiện gần nhất theo chiều đọc
(tương lai: ≥ anchor; quá khứ: ≤ anchor). Âm lịch: thuật toán Hồ Ngọc Đức (múi giờ +7), tháng nhuận chỉ khi người viết nói "nhuận".
Hàm thuần, không I/O; không đổi `anchor`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, tzinfo
from functools import lru_cache
from math import pi, sin
from typing import Literal
from zoneinfo import ZoneInfo

from gh.textnorm import strip_accents

DEFAULT_TZ = "Asia/Ho_Chi_Minh"
Granularity = Literal["minute", "hour", "part_of_day", "day", "week"]
GRANULARITIES: tuple[str, ...] = ("minute", "hour", "part_of_day", "day", "week")

# ─── từ vựng "gấp" — một nguồn (§4F.1 #8) ───────────────────────────────────────────────────────────────────────────────
URGENT_LEXICON: frozenset[str] = frozenset({
    "gấp", "gấp lắm", "rất gấp", "khẩn", "khẩn cấp", "khẩn trương", "ngay lập tức", "ngay bây giờ", "ngay đi", "ngay và luôn",
    "càng sớm càng tốt", "csct", "asap", "urgent", "nhanh lên", "nhanh giúp", "lẹ lên", "hạn chót", "deadline", "hết hạn",
    "trong hôm nay", "hôm nay phải", "sập", "lỗi hệ thống", "cần ngay", "cần gấp", "chạy gấp",
})


def _urgent_patterns() -> list[re.Pattern[str]]:
    return [re.compile(rf"(?<![0-9a-z]){re.escape(strip_accents(t.lower()))}(?![0-9a-z])") for t in sorted(URGENT_LEXICON)]


_URGENT_RE = _urgent_patterns()


def has_urgent(text: str) -> bool:
    """Có từ trong `URGENT_LEXICON` (không phân biệt hoa/thường và dấu, theo ranh giới từ)?"""
    f = _fold(text)
    return any(p.search(f) for p in _URGENT_RE)


def urgent_hits(text: str) -> list[str]:
    f = _fold(text)
    return [t for t, p in zip(sorted(URGENT_LEXICON), _URGENT_RE, strict=True) if p.search(f)]


# ─── kiểu kết quả ────────────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Recurrence:
    freq: Literal["day", "week", "month", "year"]
    interval: int = 1
    weekday: int | None = None        # 0 = thứ hai … 6 = chủ nhật
    monthday: int | None = None
    hour: int | None = None
    minute: int | None = None


@dataclass(frozen=True)
class Due:
    """Kết quả `parse`. `at` = hạn (None ⇒ không đọc được); `earliest`/`latest` = khoảng cụm chỉ tới."""

    at: datetime | None
    earliest: datetime | None = None
    latest: datetime | None = None
    granularity: Granularity | None = None
    lunar: bool = False
    ambiguous: bool = False
    recurrence: Recurrence | None = None

    @property
    def parsed(self) -> bool:
        return self.at is not None


@dataclass(frozen=True)
class Instant:
    earliest: datetime
    latest: datetime
    granularity: Granularity


# ─── âm lịch (Hồ Ngọc Đức) ───────────────────────────────────────────────────────────────────────────────────────────────
def _jd_from_date(dd: int, mm: int, yy: int) -> int:
    a = (14 - mm) // 12
    y = yy + 4800 - a
    m = mm + 12 * a - 3
    jd = dd + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    if jd < 2299161:
        jd = dd + (153 * m + 2) // 5 + 365 * y + y // 4 - 32083
    return jd


def _jd_to_date(jd: int) -> tuple[int, int, int]:
    if jd > 2299160:
        a = jd + 32044
        b = (4 * a + 3) // 146097
        c = a - (b * 146097) // 4
    else:
        b = 0
        c = jd + 32082
    d = (4 * c + 3) // 1461
    e = c - (1461 * d) // 4
    m = (5 * e + 2) // 153
    return e - (153 * m + 2) // 5 + 1, m + 3 - 12 * (m // 10), b * 100 + d - 4800 + m // 10


def _new_moon(k: int) -> float:
    t = k / 1236.85
    t2, t3 = t * t, t * t * t
    dr = pi / 180
    jd1 = 2415020.75933 + 29.53058868 * k + 0.0001178 * t2 - 0.000000155 * t3
    jd1 += 0.00033 * sin((166.56 + 132.87 * t - 0.009173 * t2) * dr)
    m = 359.2242 + 29.10535608 * k - 0.0000333 * t2 - 0.00000347 * t3
    mpr = 306.0253 + 385.81691806 * k + 0.0107306 * t2 + 0.00001236 * t3
    f = 21.2964 + 390.67050646 * k - 0.0016528 * t2 - 0.00000239 * t3
    c1 = (0.1734 - 0.000393 * t) * sin(m * dr) + 0.0021 * sin(2 * dr * m)
    c1 -= 0.4068 * sin(mpr * dr) + (-0.0161) * sin(dr * 2 * mpr)
    c1 -= 0.0004 * sin(dr * 3 * mpr)
    c1 += 0.0104 * sin(dr * 2 * f) - 0.0051 * sin(dr * (m + mpr))
    c1 -= 0.0074 * sin(dr * (m - mpr)) - 0.0004 * sin(dr * (2 * f + m))
    c1 -= 0.0004 * sin(dr * (2 * f - m)) + 0.0006 * sin(dr * (2 * f + mpr))
    c1 += 0.0010 * sin(dr * (2 * f - mpr)) + 0.0005 * sin(dr * (2 * mpr + m))
    deltat = (0.001 + 0.000839 * t + 0.0002261 * t2 - 0.00000845 * t3 - 0.000000081 * t * t3) if t < -11 else \
        (-0.000278 + 0.000265 * t + 0.000262 * t2)
    return jd1 + c1 - deltat


def _sun_longitude(jdn: float) -> float:
    t = (jdn - 2451545.0) / 36525
    t2 = t * t
    dr = pi / 180
    m = 357.52910 + 35999.05030 * t - 0.0001559 * t2 - 0.00000048 * t * t2
    l0 = 280.46645 + 36000.76983 * t + 0.0003032 * t2
    dl = (1.914600 - 0.004817 * t - 0.000014 * t2) * sin(dr * m)
    dl += (0.019993 - 0.000101 * t) * sin(dr * 2 * m) + 0.000290 * sin(dr * 3 * m)
    lng = (l0 + dl) * dr
    return lng - pi * 2 * int(lng / (pi * 2))


@lru_cache(maxsize=4096)
def _sun_sector(day_number: int, tzh: int) -> int:
    return int(_sun_longitude(day_number - 0.5 - tzh / 24.0) / pi * 6)


@lru_cache(maxsize=4096)
def _new_moon_day(k: int, tzh: int) -> int:
    return int(_new_moon(k) + 0.5 + tzh / 24.0)


@lru_cache(maxsize=256)
def _lunar_month11(yy: int, tzh: int) -> int:
    off = _jd_from_date(31, 12, yy) - 2415021
    k = int(off / 29.530588853)
    nm = _new_moon_day(k, tzh)
    if _sun_sector(nm, tzh) >= 9:
        nm = _new_moon_day(k - 1, tzh)
    return nm


def _leap_month_offset(a11: int, tzh: int) -> int:
    k = int((a11 - 2415021.076998695) / 29.530588853 + 0.5)
    i = 1
    arc = _sun_sector(_new_moon_day(k + i, tzh), tzh)
    while True:
        last = arc
        i += 1
        arc = _sun_sector(_new_moon_day(k + i, tzh), tzh)
        if not (arc != last and i < 14):
            break
    return i - 1


def solar_to_lunar(d: date, tzh: int = 7) -> tuple[int, int, int, bool]:
    """(ngày âm, tháng âm, năm âm, nhuận?)."""
    dn = _jd_from_date(d.day, d.month, d.year)
    k = int((dn - 2415021.076998695) / 29.530588853)
    month_start = _new_moon_day(k + 1, tzh)
    if month_start > dn:
        month_start = _new_moon_day(k, tzh)
    a11 = _lunar_month11(d.year, tzh)
    b11 = a11
    if a11 >= month_start:
        lunar_year = d.year
        a11 = _lunar_month11(d.year - 1, tzh)
    else:
        lunar_year = d.year + 1
        b11 = _lunar_month11(d.year + 1, tzh)
    lunar_day = dn - month_start + 1
    diff = int((month_start - a11) / 29)
    leap = False
    lunar_month = diff + 11
    if b11 - a11 > 365:
        leap_diff = _leap_month_offset(a11, tzh)
        if diff >= leap_diff:
            lunar_month = diff + 10
            if diff == leap_diff:
                leap = True
    if lunar_month > 12:
        lunar_month -= 12
    if lunar_month >= 11 and diff < 4:
        lunar_year -= 1
    return lunar_day, lunar_month, lunar_year, leap


def lunar_to_solar(day: int, month: int, year: int, leap: bool = False, tzh: int = 7) -> date | None:
    """Ngày dương của (ngày, tháng, năm âm); None nếu không có (tháng nhuận không tồn tại, ngày 30 của tháng thiếu)."""
    if not (1 <= day <= 30 and 1 <= month <= 12):
        return None
    if month < 11:
        a11, b11 = _lunar_month11(year - 1, tzh), _lunar_month11(year, tzh)
    else:
        a11, b11 = _lunar_month11(year, tzh), _lunar_month11(year + 1, tzh)
    k = int(0.5 + (a11 - 2415021.076998695) / 29.530588853)
    off = month - 11
    if off < 0:
        off += 12
    if b11 - a11 > 365:
        leap_off = _leap_month_offset(a11, tzh)
        leap_month = leap_off - 2
        if leap_month < 0:
            leap_month += 12
        if leap and month != leap_month:
            return None
        if leap or off >= leap_off:
            off += 1
    elif leap:
        return None
    month_start = _new_moon_day(k + off, tzh)
    dd, mm, yy = _jd_to_date(month_start + day - 1)
    try:
        out = date(yy, mm, dd)
    except ValueError:
        return None
    return out if solar_to_lunar(out, tzh)[:2] == (day, month) else None


# ─── chuẩn hoá chữ ───────────────────────────────────────────────────────────────────────────────────────────────────────
_NUM_WORDS = {"một": 1, "hai": 2, "ba": 3, "bốn": 4, "tư": 4, "năm": 5, "sáu": 6, "bảy": 7, "bẩy": 7, "tám": 8, "chín": 9}
_NUM_WORDS_FOLDED = {strip_accents(k): v for k, v in _NUM_WORDS.items() if strip_accents(k) not in ("nam", "sau", "tu")}
_UNITS = r"(?:ngày|tuần|tháng|năm|tiếng|giờ|phút|h|g|p)"


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").lower()
    s = re.sub(r"\s+", " ", s).strip()
    return strip_accents(s)


def _word_to_int(tokens: list[str]) -> int | None:
    """"hai mươi lăm" ⇒ 25; "mười" ⇒ 10; "mười hai" ⇒ 12; "ba" ⇒ 3."""
    if not tokens:
        return None
    t = [x for x in tokens if x]
    if t[0] == "mười" or t[0] == "muoi":
        if len(t) == 1:
            return 10
        if len(t) == 2:
            u = _NUM_WORDS.get(t[1], 5 if t[1] in ("lăm", "lam") else _NUM_WORDS_FOLDED.get(t[1]))
            return 10 + u if u else None
        return None
    if len(t) == 1:
        return _NUM_WORDS.get(t[0], _NUM_WORDS_FOLDED.get(t[0]))
    if len(t) >= 2 and t[1] in ("mươi", "muoi"):
        tens = _NUM_WORDS.get(t[0], _NUM_WORDS_FOLDED.get(t[0]))
        if tens is None:
            return None
        if len(t) == 2:
            return tens * 10
        if len(t) == 3:
            u = 5 if t[2] in ("lăm", "lam") else (1 if t[2] in ("mốt", "mot") else _NUM_WORDS.get(t[2], _NUM_WORDS_FOLDED.get(t[2])))
            return tens * 10 + u if u else None
    return None


_WORDNUM_RE = re.compile(
    r"(?<![\wÀ-ỹ])((?:mười|muoi|một|mot|hai|ba|bốn|bon|năm|sáu|bảy|bẩy|bay|tám|tam|chín|chin|lăm|lam|mươi)"
    r"(?: (?:mười|muoi|một|mot|mốt|hai|ba|bốn|bon|năm|sáu|bảy|bẩy|bay|tám|tam|chín|chin|lăm|lam|mươi)){0,2})"
    r"(?= ?(?:ngày|ngay|tuần|tuan|tháng|thang|tiếng|tieng|giờ|gio|phút|phut|năm|nam|rưỡi|ruoi)(?![\wÀ-ỹ]))")


def _numberize(t: str) -> str:
    """Đổi cụm số bằng chữ đứng trước đơn vị thời gian thành chữ số ("hai mươi ngày nữa" ⇒ "20 ngày nữa")."""
    def sub(m: re.Match[str]) -> str:
        n = _word_to_int(m.group(1).split())
        return str(n) if n is not None else m.group(0)
    return _WORDNUM_RE.sub(sub, t)


# ─── cấu trúc trung gian ─────────────────────────────────────────────────────────────────────────────────────────────────
PART_WINDOWS: dict[str, tuple[time, time]] = {          # [bắt đầu, cuối buổi]; 24:00 ⇒ time.max ở bước dựng
    "rang_sang": (time(3), time(6)), "sang_som": (time(5), time(8)), "sang": (time(6), time(12)), "trua": (time(11), time(13)),
    "chieu": (time(13), time(18)), "chieu_toi": (time(17), time(20)), "toi": (time(18), time(22)), "dem": (time(21), time(23, 59, 59)),
    "khuya": (time(23), time(23, 59, 59)),
}
_PART_RE = [("rang_sang", r"rang sang"), ("sang_som", r"sang som"), ("chieu_toi", r"chieu toi"), ("khuya", r"khuya"),
            ("dem", r"dem"), ("trua", r"trua"), ("chieu", r"chieu"), ("toi", r"toi(?! ?[0-9]+ ?(?:ngay|tuan|thang))"),
            ("sang", r"sang")]
_WEEKDAY_NAMES = {"hai": 0, "ba": 1, "tu": 2, "nam": 3, "sau": 4, "bay": 5, "2": 0, "3": 1, "4": 2, "5": 3, "6": 4, "7": 5}


@dataclass
class _P:
    # ngày
    span: tuple[date, date] | None = None            # [bắt đầu, kết thúc] đã quy ra ngày
    span_gran: Granularity = "day"
    weekday: int | None = None                        # thứ trong tuần chưa neo
    weekday_mod: str = ""                             # "toi" (sau hôm nay) | "nay" | "tuansau" | "tuannay" | "tuantruoc" | "truoc"
    monthday: int | None = None                       # ngày trong tháng chưa neo tháng
    dm: tuple[int, int, int | None] | None = None     # (ngày, tháng, năm|None) dương
    lunar: tuple[int | None, int | None, int | None, bool] | None = None   # (ngày, tháng, năm, nhuận)
    lunar_tet: int | None = None                      # lệch ngày so với mùng 1 Tết (0 = mùng 1; -1 = 30/giao thừa)
    month_only: tuple[int, int | None] | None = None  # (tháng, năm|None)
    month_part: str = ""                              # dau | giua | cuoi
    week_part: str = ""
    # giờ
    hm: tuple[int, int] | None = None
    part: str | None = None
    approx: bool = False
    # lệch tương đối
    offset: tuple[int, str] | None = None             # (+n/-n, đơn vị: minute|hour|day|week|month|year)
    now: bool = False
    recurrence: Recurrence | None = None
    ambiguous: bool = False
    forward_only: bool = False


_CLOCK_RE = re.compile(
    r"(?<![0-9/.:-])(?P<h>[01]?\d|2[0-3])\s*(?:gio|h|g|:)\s*(?:(?P<m>[0-5]\d)(?!\d)|(?P<half>ruoi)|(?P<kem>kem\s*(?P<k>\d{1,2})))?(?![0-9])")
_CLOCK_WORD_RE = re.compile(r"(?<![0-9/])(?P<h>[01]?\d|2[0-3]) (?:gio) ?(?:(?P<m>[0-5]\d)(?!\d)|(?P<half>ruoi))?")


def _end_of_day(d: date, tz: tzinfo) -> datetime:
    return datetime.combine(d, time(23, 59, 59), tzinfo=tz)


def _start_of_day(d: date, tz: tzinfo) -> datetime:
    return datetime.combine(d, time(0, 0, 0), tzinfo=tz)


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _add_months(d: date, n: int) -> tuple[int, int]:
    idx = d.year * 12 + (d.month - 1) + n
    return idx // 12, idx % 12 + 1


def _month_span(y: int, m: int) -> tuple[date, date]:
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return date(y, m, 1), nxt - timedelta(days=1)


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _hour_with_part(h: int, part: str | None) -> int:
    if part is None:
        return h
    if part in ("chieu", "chieu_toi", "toi"):
        return h + 12 if 1 <= h < 12 and not (part == "toi" and h < 5 and False) else h
    if part == "trua":
        return h + 12 if h in (1, 2, 3) else h
    if part in ("dem", "khuya"):
        if h == 12:
            return 0
        return h + 12 if h >= 6 else h
    if part in ("sang", "sang_som", "rang_sang"):
        return 0 if h == 12 and part == "rang_sang" else h
    return h


# ─── bước đọc ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _scan(text: str) -> _P | None:
    """Đọc cụm thành `_P`; None nếu chẳng nhận ra thành phần thời gian nào."""
    t0 = unicodedata.normalize("NFC", text or "").lower()
    # từ cần dấu để phân biệt TRƯỚC khi bỏ dấu
    if re.search(r"mai mốt|mai mot", t0):
        return None
    t0 = re.sub(r"(?<![\wÀ-ỹ])(?:ngày )?mốt(?![\wÀ-ỹ])", " NGAYMOT2 ", t0)
    t0 = re.sub(r"(?<![\wÀ-ỹ])(?:ngày|hôm|bữa) kìa(?![\wÀ-ỹ])", lambda m: " NGAYKIA3 " if m.group(0).startswith("ngày") else " HOMKIA3 ", t0)
    t0 = _numberize(t0)
    f = _fold(t0).replace("ngaymot2", "NGAYMOT2").replace("ngaykia3", "NGAYKIA3").replace("homkia3", "HOMKIA3")
    f = re.sub(r"[,;()\"“”]", " ", f)
    f = re.sub(r"\s+", " ", f).strip()
    if not f:
        return None
    p = _P()
    hit = False

    def eat(pat: str | re.Pattern[str], flags: int = 0) -> re.Match[str] | None:
        nonlocal f
        m = re.search(pat, f, flags) if isinstance(pat, str) else pat.search(f)
        if m:
            f = (f[:m.start()] + " " + f[m.end():]).strip()
        return m

    # 0) tiền tố vô nghĩa với kết quả
    f = re.sub(r"(?<![a-z0-9])(?:truoc(?= ?(?:[0-9]|thu |chu nhat|cn\b|ngay |mai|hom |cuoi |het |tet|ram|mung|sang|chieu|toi|trua|tuan |t[2-7]\b))|khoang|tam)(?![a-z0-9])",
               lambda m: " APPROX " if m.group(0) in ("khoang", "tam") else " ", f)
    if "APPROX" in f:
        p.approx = True
        f = f.replace("APPROX", " ")
    f = re.sub(r"\s+", " ", f).strip()

    # 1) lịch lặp
    rec_freq: str | None = None
    rec_interval = 1
    m = eat(r"(?<![a-z0-9])(?:moi|hang|cu) (\d+) (ngay|tuan|thang|nam)(?![a-z0-9])")
    if m:
        rec_freq, rec_interval = {"ngay": "day", "tuan": "week", "thang": "month", "nam": "year"}[m.group(2)], int(m.group(1))
    else:
        m = eat(r"(?<![a-z0-9])(?:moi|hang|cu moi)(?: |$)(ngay|tuan|thang|nam|sang|chieu|toi|trua|dem|(?=thu )|(?=chu nhat)|(?=t[2-7]\b)|(?=cn\b))")
        if m:
            g = m.group(1)
            rec_freq = {"ngay": "day", "tuan": "week", "thang": "month", "nam": "year", "sang": "day", "chieu": "day", "toi": "day",
                        "trua": "day", "dem": "day", "": "week"}[g]
            if g in ("sang", "chieu", "toi", "trua", "dem"):
                f = (f + " " + g).strip()
        else:
            m = eat(r"(?<![a-z0-9])(?:hang|moi) ?(?:ngay|tuan|thang|nam)(?![a-z0-9])|(?<![a-z0-9])dinh ky(?![a-z0-9])")
            if m:
                rec_freq = {"ngay": "day", "tuan": "week", "thang": "month", "nam": "year"}.get(
                    next((u for u in ("ngay", "tuan", "thang", "nam") if u in m.group(0)), ""), "week")
            else:
                m = eat(r"(?<![a-z0-9])(?:hang|moi) (?:ngay|tuan|thang|nam)\b")
    if rec_freq is None and re.search(r"(?<![a-z0-9])(?:hang|moi)(?![a-z0-9])", f):
        f = re.sub(r"(?<![a-z0-9])(?:hang|moi)(?![a-z0-9])", " ", f)

    # 2) âm lịch / Tết
    lunar_flag = False
    m = eat(r"(?<![a-z0-9])(?:am lich|al|am)(?![a-z0-9])(?! ?[0-9]+ ?(?:ngay|tuan|thang))")
    if m:
        lunar_flag = True
    m = eat(r"(?<![a-z0-9])(?:giao thua)(?![a-z0-9])")
    if m:
        p.lunar_tet, hit = -1, True
    m = eat(r"(?<![a-z0-9])(?:mung|mong|ngay)? ?(\d{1,2}) tet(?![a-z0-9])(?! duong)")
    if m and p.lunar_tet is None:
        n = int(m.group(1))
        p.lunar_tet, hit = (-1 if n >= 29 else n - 1), True
    if p.lunar_tet is None and re.search(r"(?<![a-z0-9])tet duong(?: lich)?(?![a-z0-9])", f):
        eat(r"(?<![a-z0-9])tet duong(?: lich)?(?![a-z0-9])")
        p.dm, hit = (1, 1, None), True
    elif p.lunar_tet is None:
        m = eat(r"(?<![a-z0-9])tet(?: am lich| nguyen dan| co truyen)?(?![a-z0-9])")
        if m:
            p.lunar_tet, hit = 0, True
    m = eat(r"(?<![a-z0-9])ram(?: thang (\d{1,2}|gieng|chap|chap|hai))?(?![a-z0-9])")
    if m:
        lm = _lunar_month_word(m.group(1))
        p.lunar, hit = (15, lm, None, False), True
    if p.lunar is None and p.lunar_tet is None:
        m = eat(r"(?<![a-z0-9])(?:mung|mong|ngay)? ?(\d{1,2}) thang (gieng|chap|\d{1,2})(?: (nhuan))?(?: nam (\d{4}))?(?![a-z0-9])") if (
            lunar_flag or re.search(r"thang (?:gieng|chap)", f)) else None
        if m:
            lm = _lunar_month_word(m.group(2))
            p.lunar, hit = (int(m.group(1)), lm, int(m.group(4)) if m.group(4) else None, bool(m.group(3))), True
    if p.lunar is None and p.lunar_tet is None and lunar_flag:
        m = eat(r"(?<![a-z0-9])(?:mung|mong|ngay)? ?(\d{1,2})[/-](\d{1,2})(?:[/-](\d{4}))?(?![0-9])")
        if m:
            p.lunar, hit = (int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None, False), True
    if p.lunar is None and p.lunar_tet is None and lunar_flag:
        m = eat(r"(?<![a-z0-9])(?:mung|mong|ngay) ?(\d{1,2})(?![0-9])")
        if m:
            p.lunar, hit = (int(m.group(1)), None, None, False), True
    if p.lunar is None and p.lunar_tet is None:
        m = eat(r"(?<![a-z0-9])(?:mung|mong) ?(\d{1,2})(?![0-9])") if re.search(r"(?<![a-z0-9])(?:mung|mong)", f) and "thang" in f else None
        if m:   # "mùng 5 tháng 11" không âm: dương lịch
            mm = re.search(r"thang (\d{1,2})", f)
            if mm:
                eat(r"thang \d{1,2}")
                p.dm, hit = (int(m.group(1)), int(mm.group(1)), None), True
    if lunar_flag and p.lunar is None and p.lunar_tet is None:
        # "giỗ âm", "tháng 7 âm" không có ngày
        m = eat(r"thang (gieng|chap|\d{1,2})(?![0-9])")
        if m:
            p.lunar, hit = (None, _lunar_month_word(m.group(1)), None, False), True
    if p.lunar is None and p.lunar_tet is None and p.dm is None:
        m = eat(r"(?<![a-z0-9])(?:mung|mong) ?(\d{1,2})(?![0-9])")
        if m:                    # "mùng 1 hàng tháng": mùng không âm = ngày dương trong tháng
            p.monthday, hit = int(m.group(1)), True

    # 3) ngày dương số
    if p.lunar is None and p.lunar_tet is None and p.dm is None:
        m = eat(r"(?<![0-9/.-])(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})(?![0-9])")
        if m:
            y = int(m.group(3))
            p.dm, hit = (int(m.group(1)), int(m.group(2)), y + 2000 if y < 100 else y), True
        else:
            m = eat(r"(?<![0-9/.-])ngay (\d{1,2}) thang (\d{1,2})(?: nam (\d{4}))?(?![0-9])")
            if m:
                p.dm, hit = (int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None), True
            else:
                m = eat(r"(?<![0-9/.-])(\d{1,2})[/-](\d{1,2})(?![0-9/])(?! ?(?:h|g|gio|:|phut))")
                if m and 1 <= int(m.group(1)) <= 31 and 1 <= int(m.group(2)) <= 12:
                    p.dm, hit = (int(m.group(1)), int(m.group(2)), None), True
                elif m:
                    f = (f + " " + m.group(0)).strip()
    # 4) thuộc tính tháng ("ngày 5 tháng sau")
    mp = eat(r"(?<![a-z0-9])(dau|giua|cuoi)(?= thang(?![a-z0-9]))")
    if mp:
        p.month_part, hit = mp.group(1), True
    m_rel_month = None
    m = eat(r"(?<![a-z0-9])thang (sau|toi|nay|truoc|roi|vua roi|vua qua)(?![a-z0-9])")
    if m:
        m_rel_month = {"sau": 1, "toi": 1, "nay": 0, "truoc": -1, "roi": -1, "vua roi": -1, "vua qua": -1}[m.group(1)]
    # 5) mốc tương đối theo ngày
    m = eat(r"(?<![a-z0-9])(?:hom nay|ngay hom nay|trong hom nay|hnay|bua nay)(?![a-z0-9])")
    if m:
        p.span, p.span_gran, hit = (_SENT_TODAY, _SENT_TODAY), "day", True
    if p.span is None and re.search(r"(?<![a-z0-9])(?:het ngay|cuoi ngay|eod|het gio lam)(?![a-z0-9])", f):
        eat(r"(?<![a-z0-9])(?:het ngay|cuoi ngay|eod|het gio lam)(?![a-z0-9])")
        p.span, p.span_gran, hit = (_SENT_TODAY, _SENT_TODAY), "day", True
    for pat, delta in (
        (r"(?<![a-z0-9])NGAYKIA3(?![a-z0-9])", 3), (r"(?<![a-z0-9])NGAYMOT2(?![a-z0-9])", 2), (r"(?<![a-z0-9])HOMKIA3(?![a-z0-9])", -3),
        (r"(?<![a-z0-9])(?:hom|bua) kia(?![a-z0-9])", -2), (r"(?<![a-z0-9])ngay kia(?![a-z0-9])", 2),
        (r"(?<![a-z0-9])(?:hom qua|bua qua|ngay hom qua|toi qua|sang qua|chieu qua|trua qua|dem qua|dem hom qua)(?![a-z0-9])", -1),
        (r"(?<![a-z0-9])(?:ngay mai|hom sau|bua sau)(?![a-z0-9])", 1),
        (r"(?<![a-z0-9])(?:sang|chieu|trua|toi|dem) mai(?![a-z0-9])", 1), (r"(?<![a-z0-9])mai(?![a-z0-9])(?! mot)", 1),
        (r"(?<![a-z0-9])(?:sang|chieu|trua|toi|dem|khuya) nay(?![a-z0-9])", 0),
        (r"(?<![a-z0-9])qua(?![a-z0-9])", -1)):
        if p.span is not None:
            break
        mm = re.search(pat, f)
        if not mm:
            continue
        if pat.endswith("(?![a-z0-9])") and "qua" in pat and mm.group(0) == "qua" and not re.search(r"(?:sang|chieu|trua|toi|dem) qua", f):
            continue
        word = mm.group(0)
        # buổi đi kèm ("sáng mai", "tối qua") — giữ lại buổi, bỏ phần ngày
        pm = re.match(r"(sang|chieu|trua|toi|dem|khuya) (?:mai|nay|qua)", word)
        eat(re.escape(word) if not pm else re.escape(word))
        if pm:
            f = (f + " " + pm.group(1)).strip()
        elif re.fullmatch(r"(?:toi|sang|chieu|trua|dem) qua", word):
            f = (f + " " + word.split()[0]).strip()
        p.span, p.span_gran, hit = (_SENT_TODAY + timedelta(days=delta), _SENT_TODAY + timedelta(days=delta)), "day", True
        if word.endswith("nay") and pm is None:
            p.span = (_SENT_TODAY, _SENT_TODAY)
    if p.span is None and re.search(r"(?<![a-z0-9])(?:bay gio|ngay bay gio|ngay lap tuc|luc nay|vua xong|vua moi)(?![a-z0-9])", f):
        eat(r"(?<![a-z0-9])(?:ngay bay gio|ngay lap tuc|bay gio|luc nay|vua xong|vua moi)(?![a-z0-9])")
        p.now, hit = True, True
    # 6) độ lệch N đơn vị
    if p.span is None and not p.now:
        m = re.search(r"(?<!thu )(?<![a-z0-9])(?:trong vong|trong|sau|cach|con)? ?(?<!thu )(\d+(?:[.,]5)?) ?(ngay|tuan|thang|nam|tieng|gio|phut|h|p) ?(ruoi)? ?(nua|sau|toi|truoc|qua|ke tu bay gio|tu bay gio|vua roi|vua qua|truoc day)?(?![a-z0-9])", f)
        if m:
            unit_raw = m.group(2)
            unit = {"ngay": "day", "tuan": "week", "thang": "month", "nam": "year", "tieng": "hour", "gio": "hour", "h": "hour",
                    "phut": "minute", "p": "minute"}[unit_raw]
            qty = float(m.group(1).replace(",", "."))
            if m.group(3):
                qty += 0.5
            lead = re.match(r"(?:trong vong|trong|sau|cach|con)", m.group(0).strip())
            tail = m.group(4) or ""
            neg = tail in ("truoc", "qua", "vua roi", "vua qua", "truoc day")
            if (unit in ("hour", "minute") and not (m.group(4) or lead)) or (unit_raw in ("gio", "h", "p") and m.group(4) in (None, "toi", "sau")):
                m = None                                    # "9h", "9 giờ 30" là giờ trên đồng hồ, không phải 9 tiếng
            else:
                f = (f[:m.start()] + " " + f[m.end():]).strip()
                if unit in ("hour", "minute"):
                    mins = int(round(qty * (60 if unit == "hour" else 1)))
                    p.offset, hit = (-mins if neg else mins, "minute"), True
                else:
                    p.offset, hit = (-int(qty) if neg else int(qty), unit), True
                if lead and lead.group(0) in ("trong", "trong vong"):
                    p.forward_only = True
    # 7) tuần / tháng
    m = eat(r"(?<![a-z0-9])(dau|giua|cuoi) tuan(?: (nay|sau|toi|truoc|roi|vua roi|vua qua))?(?![a-z0-9])")
    if m and p.span is None and p.offset is None:
        k = {None: 0, "nay": 0, "sau": 1, "toi": 1, "truoc": -1, "roi": -1, "vua roi": -1, "vua qua": -1}[m.group(2)]
        p.week_part, hit = m.group(1), True
        ws = _SENT_TODAY - timedelta(days=_SENT_TODAY.weekday()) + timedelta(days=7 * k)
        p.span = {"dau": (ws, ws + timedelta(days=1)), "giua": (ws + timedelta(days=2), ws + timedelta(days=3)),
                  "cuoi": (ws + timedelta(days=5), ws + timedelta(days=6))}[m.group(1)]
        p.span_gran = "week"
    if p.span is None and p.offset is None:
        m = eat(r"(?<![a-z0-9])(?:thu (\d|hai|ba|tu|nam|sau|bay)|chu nhat|t([2-7])|cn)(?: (tuan nay|tuan sau|tuan toi|tuan truoc|tuan roi|tuan vua roi|nay|sau|toi|truoc|roi|vua roi|vua qua))?(?![a-z0-9])")
        if m:
            if m.group(0).startswith(("chu nhat", "cn")):
                wd = 6
            else:
                wd = _WEEKDAY_NAMES[m.group(1) or m.group(2)]
            p.weekday, p.weekday_mod, hit = wd, {"tuan sau": "tuansau", "tuan toi": "tuansau", "tuan nay": "tuannay", "tuan truoc": "tuantruoc",
                                                  "tuan roi": "tuantruoc", "tuan vua roi": "tuantruoc", "nay": "nay", "sau": "toi", "toi": "toi",
                                                  "truoc": "truoc", "roi": "truoc", "vua roi": "truoc", "vua qua": "truoc"}.get(m.group(3) or "", ""), True
    if p.span is None and p.offset is None and p.weekday is None:
        m = eat(r"(?<![a-z0-9])tuan (nay|sau|toi|truoc|roi|vua roi|vua qua|ke tiep)(?![a-z0-9])")
        if m:
            k = {"nay": 0, "sau": 1, "toi": 1, "ke tiep": 1, "truoc": -1, "roi": -1, "vua roi": -1, "vua qua": -1}[m.group(1)]
            ws = _SENT_TODAY - timedelta(days=_SENT_TODAY.weekday()) + timedelta(days=7 * k)
            p.span, p.span_gran, hit = (ws, ws + timedelta(days=6)), "week", True
    # 8) tháng số "tháng 11" / ngày trong tháng
    mm = None
    if p.dm is None and p.lunar is None and p.lunar_tet is None:
        mm = eat(r"(?<![0-9])thang (\d{1,2})(?: nam (\d{4}))?(?![0-9])")
        if mm:
            p.month_only, hit = (int(mm.group(1)), int(mm.group(2)) if mm.group(2) else None), True
    if p.dm is None and p.lunar is None and p.lunar_tet is None:
        md = eat(r"(?<![0-9/.:-])ngay (\d{1,2})(?![0-9/.:])(?! ?(?:thang|h|g|gio))")
        if md:
            p.monthday, hit = int(md.group(1)), True
    if m_rel_month is not None:
        hit = True
        y, mo = _add_months(_SENT_TODAY, m_rel_month)
        if p.monthday is not None and _safe_date(y, mo, p.monthday):
            p.span, p.span_gran = (date(y, mo, p.monthday),) * 2, "day"
            p.monthday = None
        else:
            ms, me = _month_span(y, mo)
            p.span, p.span_gran = (ms, me), "week"
        if p.month_part:
            ms, me = _month_span(y, mo)
            p.span = {"dau": (ms, ms + timedelta(days=6)), "giua": (ms + timedelta(days=9), ms + timedelta(days=19)),
                      "cuoi": (me - timedelta(days=6), me)}[p.month_part]
            p.span_gran = "week"
            p.month_part = ""
    elif p.month_part and p.span is None:
        p.month_part = p.month_part   # dùng tháng hiện tại ở bước giải
    # 9) giờ
    m = _CLOCK_RE.search(f)
    if m is None:
        m = _CLOCK_WORD_RE.search(f)
    if m and p.hm is None:
        h = int(m.group("h"))
        mi = int(m.group("m")) if m.group("m") else (30 if m.group("half") else 0)
        if "k" in m.groupdict() and m.groupdict().get("k"):
            h, mi = (h - 1) % 24, 60 - int(m.group("k"))
        f = (f[:m.start()] + " TIME " + f[m.end():]).strip()
        p.hm, hit = (h, mi), True
    # buổi trong ngày
    for key, pat in _PART_RE:
        mm2 = re.search(rf"(?<![a-z0-9]){pat}(?![a-z0-9])", f)
        if mm2:
            p.part, hit = key, True
            f = (f[:mm2.start()] + " " + f[mm2.end():]).strip()
            break
    # "tối" ngay sau giờ bị ăn thành offset — đã loại ở _PART_RE; "12h trưa"/"12h đêm" xử lý ở _hour_with_part
    if rec_freq is not None:
        hit = True
        p.recurrence = Recurrence(rec_freq, rec_interval)   # type: ignore[arg-type]
    if re.search(r"(?<![a-z0-9])(?:sang|chieu|toi|trua|dem)\b", f) and p.part is None:
        pass
    # chẳng nhận ra gì ⇒ không đọc
    leftover = re.sub(r"TIME|[\s,.]", "", f)
    if not hit:
        return None
    # còn dư chữ lạ mang nghĩa thời gian ("sang nam", "mai mot") ⇒ coi như mơ hồ chứ không đoán
    if re.search(r"(?<![a-z0-9])(?:mai mot|sang nam|nam sau|nam toi|nam ngoai|nam truoc)(?![a-z0-9])", f):
        return None
    _ = leftover
    return p


_SENT_TODAY: date = date(1970, 1, 1)         # đặt lại mỗi lần gọi (xem `_with_today`); chỉ là mốc đánh dấu trong `_scan`


def _lunar_month_word(w: str | None) -> int | None:
    if w is None:
        return None
    return {"gieng": 1, "chap": 12, "hai": 2}.get(w) or int(w)


# `_scan` cần biết "hôm nay" để quy tuần/tháng tương đối; dùng biến mô-đun đặt trong phạm vi một lần gọi (đơn luồng đủ: hàm thuần,
# nhưng KHÔNG an toàn nếu chạy xen kẽ giữa các luồng — bọc bằng khoá).
import threading  # noqa: E402

_LOCK = threading.Lock()


def _scan_for(text: str, today: date) -> _P | None:
    global _SENT_TODAY
    with _LOCK:
        _SENT_TODAY = today
        try:
            return _scan(text)
        finally:
            _SENT_TODAY = date(1970, 1, 1)


# ─── giải thành khoảng thời gian ─────────────────────────────────────────────────────────────────────────────────────────
Direction = Literal["future", "past"]


def _lunar_candidates(p: _P, today: date, direction: Direction) -> date | None:
    if p.lunar_tet is not None:
        tet = None
        ly = solar_to_lunar(today)[2]
        cands: list[date] = []
        for y in (ly - 1, ly, ly + 1, ly + 2):
            d = lunar_to_solar(1, 1, y)
            if d:
                cands.append(d + timedelta(days=p.lunar_tet))
        cands.sort()
        if direction == "future":
            tet = next((d for d in cands if d >= today), None)
        else:
            tet = next((d for d in reversed(cands) if d <= today), None)
        return tet
    assert p.lunar is not None
    ld, lm, lyr, leap = p.lunar
    if lyr is not None and lm is not None and ld is not None:
        return lunar_to_solar(ld, lm, lyr, leap)
    if lm is not None and ld is not None:
        ly = solar_to_lunar(today)[2]
        lcands = sorted(x for x in (lunar_to_solar(ld, lm, yy, leap) for yy in (ly - 1, ly, ly + 1, ly + 2)) if x)
        if direction == "future":
            return next((x for x in lcands if x >= today), None)
        return next((x for x in reversed(lcands) if x <= today), None)
    if ld is not None:       # ngày âm không nói tháng ("rằm", "mùng 1 âm"): quét ngày dương
        step = 1 if direction == "future" else -1
        cur = today
        for _ in range(70):
            if solar_to_lunar(cur)[0] == ld:
                return cur
            cur += timedelta(days=step)
        return None
    return None


def _resolve_span(p: _P, today: date, direction: Direction) -> tuple[date, date, Granularity, bool, bool] | None:
    """Trả (từ ngày, đến ngày, độ mịn, mơ hồ, âm lịch)."""
    amb = p.ambiguous
    if p.lunar is not None or p.lunar_tet is not None:
        d = _lunar_candidates(p, today, direction)
        if d is None:
            return None
        if p.lunar is not None and p.lunar[0] is None and p.lunar[1] is not None:   # chỉ tháng âm
            return d, d, "week", amb, True
        return d, d, "day", amb, True
    if p.span is not None:
        return p.span[0], p.span[1], p.span_gran, amb, False
    if p.dm is not None:
        dd, mo, y = p.dm
        if y is not None:
            s = _safe_date(y, mo, dd)
            return (s, s, "day", amb, False) if s else None
        cands = sorted(x for x in (_safe_date(yy, mo, dd) for yy in (today.year - 1, today.year, today.year + 1)) if x)
        s = next((x for x in cands if x >= today), None) if direction == "future" else next((x for x in reversed(cands) if x <= today), None)
        return (s, s, "day", amb, False) if s else None
    if p.weekday is not None:
        wd = p.weekday
        ws = today - timedelta(days=today.weekday())
        mod = p.weekday_mod
        if mod == "tuansau":
            s = ws + timedelta(days=7 + wd)
        elif mod == "tuannay":
            s = ws + timedelta(days=wd)
        elif mod == "tuantruoc":
            s = ws + timedelta(days=wd - 7)
        else:
            delta_f = (wd - today.weekday()) % 7
            if direction == "future" or mod in ("toi", "nay"):
                if mod == "toi" and delta_f == 0:
                    delta_f = 7
                if mod == "truoc":
                    s = today - timedelta(days=((today.weekday() - wd) % 7) or 7)
                else:
                    s = today + timedelta(days=delta_f)
                    if delta_f == 0:
                        amb = True
            else:
                back = (today.weekday() - wd) % 7
                if mod == "truoc":
                    back = back or 7
                elif back == 0:
                    amb = True
                s = today - timedelta(days=back)
        return s, s, "day", amb, False
    if p.month_only is not None:
        mo, y = p.month_only
        if not 1 <= mo <= 12:
            return None
        if y is None:
            cands2 = [(yy, mo) for yy in (today.year - 1, today.year, today.year + 1)]
            if direction == "future":
                yy = next((yy for yy, _ in cands2 if _month_span(yy, mo)[1] >= today), today.year + 1)
            else:
                yy = next((yy for yy, _ in reversed(cands2) if _month_span(yy, mo)[0] <= today), today.year - 1)
        else:
            yy = y
        ms, me = _month_span(yy, mo)
        return ms, me, "week", amb, False
    if p.monthday is not None:
        dd = p.monthday
        if not 1 <= dd <= 31:
            return None
        for k in range(0, 14):
            y, mo = _add_months(today, k if direction == "future" else -k)
            s = _safe_date(y, mo, dd)
            if s and ((direction == "future" and s >= today) or (direction == "past" and s <= today)):
                return s, s, "day", amb, False
        return None
    if p.month_part:
        ms, me = _month_span(today.year, today.month)
        span = {"dau": (ms, ms + timedelta(days=6)), "giua": (ms + timedelta(days=9), ms + timedelta(days=19)),
                "cuoi": (me - timedelta(days=6), me)}[p.month_part]
        return span[0], span[1], "week", amb, False
    return None


def _tzinfo(tz: str | tzinfo | None) -> tzinfo:
    if tz is None:
        return ZoneInfo(DEFAULT_TZ)
    return ZoneInfo(tz) if isinstance(tz, str) else tz


def _hm_to_hour(p: _P) -> tuple[int, int] | None:
    if p.hm is None:
        return None
    h, mi = p.hm
    return _hour_with_part(h, p.part), mi


def _build(p: _P, anchor: datetime, tz: tzinfo, direction: Direction) -> tuple[datetime, datetime, Granularity, bool, bool] | None:
    """(earliest, latest, granularity, ambiguous, lunar)."""
    a = anchor.astimezone(tz)
    today = a.date()
    # ── độ lệch tương đối
    if p.offset is not None:
        n, unit = p.offset
        if unit == "minute":
            center = a + timedelta(minutes=n)
            if direction == "future":
                return center, center, ("hour" if abs(n) >= 60 else "minute"), False, False
            half = timedelta(minutes=max(1, abs(n) // 6)) if abs(n) >= 60 else timedelta(seconds=30)
            gran: Granularity = "hour" if abs(n) >= 60 else "minute"
            return center - half, center + half, gran, False, False
        if unit in ("day", "week", "month", "year"):
            if unit == "day":
                d0 = today + timedelta(days=n)
                d1 = d0
                g: Granularity = "day"
            elif unit == "week":
                d0 = today + timedelta(weeks=n)
                d1 = d0
                g = "day"
            else:
                y, mo = _add_months(today, n * (12 if unit == "year" else 1))
                d0 = _safe_date(y, mo, min(today.day, _month_span(y, mo)[1].day)) or today
                d1 = d0
                g = "day"
            hm = _hm_to_hour(p)
            if hm is not None:
                t0 = datetime.combine(d0, time(*hm), tzinfo=tz)
                return t0, t0, "minute", False, False
            if p.part is not None:
                s, e = PART_WINDOWS[p.part]
                return datetime.combine(d0, s, tzinfo=tz), datetime.combine(d1, e, tzinfo=tz), "part_of_day", False, False
            return _start_of_day(d0, tz), _end_of_day(d1, tz), g, False, False
    if p.now and p.hm is None:
        return a, a, "minute", False, False
    sp = _resolve_span(p, today, direction)
    hm = _hm_to_hour(p)
    if sp is None:
        if hm is not None:             # chỉ có giờ ("9h"): lần xuất hiện gần nhất theo chiều đọc
            t_today = datetime.combine(today, time(*hm), tzinfo=tz)
            if direction == "future":
                cand = t_today if t_today >= a else t_today + timedelta(days=1)
                amb = cand != t_today
            else:
                cand = t_today if t_today <= a else t_today - timedelta(days=1)
                amb = False
            if p.approx:
                return cand - timedelta(minutes=30), cand + timedelta(minutes=30), "hour", amb, False
            return cand, cand, "minute", amb, False
        if p.part is not None:         # chỉ có buổi ("chiều")
            s, e = PART_WINDOWS[p.part]
            lo, hi = datetime.combine(today, s, tzinfo=tz), datetime.combine(today, e, tzinfo=tz)
            if direction == "future":
                if hi < a:
                    lo, hi = lo + timedelta(days=1), hi + timedelta(days=1)
            elif lo > a:
                lo, hi = lo - timedelta(days=1), hi - timedelta(days=1)
            return lo, hi, "part_of_day", False, False
        return None
    d0, d1, gran2, amb, lunar = sp
    if gran2 == "day" and (hm is not None or p.part is not None):
        if hm is not None:
            t0 = datetime.combine(d0, time(*hm), tzinfo=tz)
            if p.approx:
                return t0 - timedelta(minutes=30), t0 + timedelta(minutes=30), "hour", amb, lunar
            return t0, t0, "minute", amb, lunar
        assert p.part is not None
        s, e = PART_WINDOWS[p.part]
        return datetime.combine(d0, s, tzinfo=tz), datetime.combine(d0, e, tzinfo=tz), "part_of_day", amb, lunar
    return _start_of_day(d0, tz), _end_of_day(d1, tz), gran2, amb, lunar


# ─── lịch lặp ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _recurrence_due(p: _P, a: datetime, tz: tzinfo) -> Due | None:
    rec = p.recurrence
    assert rec is not None
    hm = _hm_to_hour(p)
    hour, minute = (hm if hm else (None, None))
    wd = p.weekday
    md = p.monthday if p.monthday is not None else (p.dm[0] if p.dm and p.dm[2] is None and False else None)
    if p.dm is not None and p.dm[2] is None and rec.freq == "year":
        md = None
    rec = replace(rec, weekday=wd, monthday=md, hour=hour, minute=minute)
    if rec.freq == "day" and wd is not None:
        rec = replace(rec, freq="week")
    if rec.freq == "day" and md is not None:
        rec = replace(rec, freq="month")
    if p.part is not None and hm is None:
        ps, _ = PART_WINDOWS[p.part]
        hour, minute = ps.hour, 0
        rec = replace(rec, hour=hour, minute=minute)
    today = a.date()
    t_of = time(hour or 0, minute or 0) if hour is not None else None

    def at_day(d: date) -> datetime:
        return datetime.combine(d, t_of, tzinfo=tz) if t_of is not None else _end_of_day(d, tz)

    cand: datetime
    if rec.freq == "day":
        cand = at_day(today)
        if cand < a:
            cand = at_day(today + timedelta(days=rec.interval))
    elif rec.freq == "week":
        if wd is None:
            cand = at_day(today + timedelta(weeks=rec.interval))
        else:
            cand = at_day(today + timedelta(days=(wd - today.weekday()) % 7))
            if cand < a:
                cand = at_day(today + timedelta(days=((wd - today.weekday()) % 7) + 7 * rec.interval))
    elif rec.freq == "month":
        if md is None:
            y, mo = _add_months(today, rec.interval)
            cand = at_day(_safe_date(y, mo, min(today.day, _month_span(y, mo)[1].day)) or today)
        else:
            cand = None  # type: ignore[assignment]
            for k in range(0, 14):
                y, mo = _add_months(today, k)
                sd = _safe_date(y, mo, md)
                if sd and at_day(sd) >= a:
                    cand = at_day(sd)
                    break
            if cand is None:
                return None
    else:  # year
        if p.dm is not None:
            dd, mo, _y = p.dm
            cand = None  # type: ignore[assignment]
            for yy in (today.year, today.year + 1, today.year + 2):
                sd = _safe_date(yy, mo, dd)
                if sd and at_day(sd) >= a:
                    cand = at_day(sd)
                    break
            if cand is None:
                return None
        else:
            cand = at_day(today + timedelta(days=365 * rec.interval))
    gran: Granularity = "minute" if t_of is not None else "day"
    return Due(at=cand, earliest=cand if t_of is not None else _start_of_day(cand.date(), tz), latest=cand, granularity=gran,
               lunar=False, ambiguous=False, recurrence=rec)


# ─── API công khai ───────────────────────────────────────────────────────────────────────────────────────────────────────
def parse(text: str, anchor: datetime, tz: str | tzinfo | None = DEFAULT_TZ) -> Due:
    """Cụm thời gian (hạn, hẹn) → `Due`. `anchor` = giờ gửi tin (có múi giờ; naive ⇒ coi là giờ `tz`)."""
    zone = _tzinfo(tz)
    a = anchor.replace(tzinfo=zone) if anchor.tzinfo is None else anchor.astimezone(zone)
    p = _scan_for(text, a.date())
    if p is None:
        return Due(at=None)
    if p.recurrence is not None:
        return _recurrence_due(p, a, zone) or Due(at=None)
    r = _build(p, a, zone, "future")
    if r is None:
        return Due(at=None)
    lo, hi, gran, amb, lunar = r
    return Due(at=hi, earliest=lo, latest=hi, granularity=gran, lunar=lunar, ambiguous=amb or p.approx and False)


def parse_past(text: str, anchor: datetime, tz: str | tzinfo | None = DEFAULT_TZ) -> Instant | None:
    """Cụm thời gian của sự việc ĐÃ xảy ra → `Instant{earliest, latest, granularity}`; không bao giờ sau `anchor`; None nếu không đọc được."""
    zone = _tzinfo(tz)
    a = anchor.replace(tzinfo=zone) if anchor.tzinfo is None else anchor.astimezone(zone)
    p = _scan_for(text, a.date())
    if p is None or p.recurrence is not None:
        return None
    r = _build(p, a, zone, "past")
    if r is None:
        return None
    lo, hi, gran, _amb, _lunar = r
    if lo > a:
        # cụm hướng về sau anchor ("chiều nay" đọc lúc 10h, "hôm nay" mà giờ chưa tới): kẹp về anchor, không bịa quá khứ
        if gran == "part_of_day" and p.span is None:      # buổi trần ("chiều"): lùi một ngày
            lo, hi = lo - timedelta(days=1), hi - timedelta(days=1)
        else:
            lo = a
    hi = min(hi, a)
    lo = min(lo, hi)
    return Instant(lo, hi, gran)


__all__ = ["Due", "Instant", "Recurrence", "URGENT_LEXICON", "GRANULARITIES", "has_urgent", "urgent_hits", "parse", "parse_past",
           "solar_to_lunar", "lunar_to_solar", "PART_WINDOWS"]
