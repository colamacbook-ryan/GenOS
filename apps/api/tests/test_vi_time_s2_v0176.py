# ruff: noqa: E501
"""S2 `vi_time` (spec §10): ≥ 95% đúng ngày trên ≥ 120 cụm tiếng Việt tự soạn (gồm âm lịch cơ bản và lịch lặp). Mỗi cụm có đáp án
tính tay trên mốc cố định: thứ sáu 23/10/2026 10:30 giờ VN (tuần bắt đầu thứ hai; 31/10 là thứ bảy; 1/11 là chủ nhật)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from gh.biz.mem import vi_time as vt

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
ANCHOR = datetime(2026, 10, 23, 10, 30, tzinfo=TZ)


def E(m: int, d: int, y: int = 2026) -> datetime:       # hạn chỉ có ngày ⇒ cuối ngày
    return datetime(y, m, d, 23, 59, 59, tzinfo=TZ)


def T(m: int, d: int, h: int, mi: int = 0, y: int = 2026) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=TZ)


# (cụm, `at` mong đợi). `("am", (ngày, tháng))` = kiểm bằng chuyển ngược dương → âm (nguồn độc lập với bộ đọc).
CASES: list[tuple[str, object]] = [
    ("hôm nay", E(10, 23)), ("ngày mai", E(10, 24)), ("mai", E(10, 24)), ("ngày mốt", E(10, 25)), ("ngày kia", E(10, 25)),
    ("ngày kìa", E(10, 26)), ("sáng mai", T(10, 24, 12)), ("chiều mai", T(10, 24, 18)), ("tối mai", T(10, 24, 22)),
    ("trưa mai", T(10, 24, 13)), ("chiều nay", T(10, 23, 18)), ("tối nay", T(10, 23, 22)), ("sáng mai 9h", T(10, 24, 9)),
    ("9h sáng mai", T(10, 24, 9)), ("3h chiều", T(10, 23, 15)), ("trước 5h chiều", T(10, 23, 17)), ("9h", T(10, 24, 9)),
    ("11h", T(10, 23, 11)), ("9h30", T(10, 24, 9, 30)), ("9 giờ 30", T(10, 24, 9, 30)), ("9 giờ rưỡi", T(10, 24, 9, 30)),
    ("15:45", T(10, 23, 15, 45)), ("21h", T(10, 23, 21)), ("8 giờ tối", T(10, 23, 20)), ("12h trưa", T(10, 23, 12)),
    ("12h đêm", T(10, 24, 0)), ("11h đêm nay", T(10, 23, 23)),
    ("thứ 6", E(10, 23)), ("thứ 2", E(10, 26)), ("thứ hai", E(10, 26)), ("thứ 7", E(10, 24)), ("chủ nhật", E(10, 25)),
    ("CN", E(10, 25)), ("T4", E(10, 28)), ("thứ 3 tuần sau", E(10, 27)), ("thứ 6 tuần sau", E(10, 30)),
    ("chủ nhật tuần này", E(10, 25)), ("thứ 6 tới", E(10, 30)), ("thứ 4 tới", E(10, 28)), ("thứ 2 sáng", T(10, 26, 12)),
    ("chiều thứ 6", T(10, 23, 18)),
    ("tuần này", E(10, 25)), ("tuần sau", E(11, 1)), ("tuần tới", E(11, 1)), ("cuối tuần", E(10, 25)), ("cuối tuần sau", E(11, 1)),
    ("đầu tuần sau", E(10, 27)), ("tháng này", E(10, 31)), ("tháng sau", E(11, 30)), ("tháng 12", E(12, 31)), ("tháng 11", E(11, 30)),
    ("cuối tháng", E(10, 31)), ("cuối tháng sau", E(11, 30)), ("đầu tháng sau", E(11, 7)), ("giữa tháng sau", E(11, 20)),
    ("ngày 25", E(10, 25)), ("ngày 5", E(11, 5)), ("ngày 23", E(10, 23)), ("ngày 31", E(10, 31)), ("ngày 5 tháng sau", E(11, 5)),
    ("ngày 15 tháng 11", E(11, 15)), ("15/11", E(11, 15)), ("15/11/2026", E(11, 15)), ("5-12", E(12, 5)), ("1/1", E(1, 1, 2027)),
    ("ngày 20/10", E(10, 20, 2027)), ("2/9/2027", E(9, 2, 2027)), ("mùng 5 tháng 11", E(11, 5)), ("ngày 3 tháng 12 năm 2026", E(12, 3)),
    ("3 ngày nữa", E(10, 26)), ("1 tuần nữa", E(10, 30)), ("2 tuần nữa", E(11, 6)), ("một tuần nữa", E(10, 30)), ("hai ngày nữa", E(10, 25)),
    ("sau 3 ngày", E(10, 26)), ("trong 3 ngày", E(10, 26)), ("trong vòng 5 ngày", E(10, 28)), ("1 tháng nữa", E(11, 23)),
    ("2 tiếng nữa", T(10, 23, 12, 30)), ("30 phút nữa", T(10, 23, 11)), ("45 phút nữa", T(10, 23, 11, 15)),
    ("hai tiếng rưỡi nữa", T(10, 23, 13)), ("ba mươi ngày nữa", E(11, 22)), ("hai mươi ngày nữa", E(11, 12)),
    ("mười lăm ngày nữa", E(11, 7)), ("ngay bây giờ", ANCHOR), ("ngay lập tức", ANCHOR),
    ("hết hôm nay", E(10, 23)), ("cuối ngày", E(10, 23)), ("EOD", E(10, 23)),
    ("trước thứ 6", E(10, 23)), ("trước thứ 2 tuần sau", E(10, 26)), ("trước ngày 30/10", E(10, 30)), ("hạn chót 15/11", E(11, 15)),
    ("deadline thứ 4", E(10, 28)), ("trước 12h trưa mai", T(10, 24, 12)), ("chiều mai lúc 2h", T(10, 24, 14)),
    ("tầm 3h chiều mai", T(10, 24, 15, 30)), ("khoảng 9h sáng thứ 2", T(10, 26, 9, 30)),
    # âm lịch cơ bản (Tết 2027 = 6/2/2027; rằm tháng giêng = 20/2/2027; 30 Tết = 5/2/2027)
    ("Tết", E(2, 6, 2027)), ("mùng 1 Tết", E(2, 6, 2027)), ("mùng 2 Tết", E(2, 7, 2027)), ("mùng 3 Tết", E(2, 8, 2027)),
    ("30 Tết", E(2, 5, 2027)), ("giao thừa", E(2, 5, 2027)), ("rằm tháng giêng", E(2, 20, 2027)), ("tết dương lịch", E(1, 1, 2027)),
    ("rằm", E(10, 24)), ("rằm tháng 8", ("am", (15, 8))), ("mùng 3 tháng 2 âm", ("am", (3, 2))), ("15/7 âm", ("am", (15, 7))),
    ("ngày 10 tháng 3 âm lịch", ("am", (10, 3))), ("3/2 âm", ("am", (3, 2))),
    # lịch lặp: (cụm, hạn kế tiếp)
    ("mỗi sáng 8h", T(10, 24, 8)), ("hàng ngày 9h", T(10, 24, 9)), ("mỗi thứ 2", E(10, 26)), ("mỗi thứ 6", E(10, 23)),
    ("hàng tuần thứ 4 lúc 9h", T(10, 28, 9)), ("ngày 5 hàng tháng", E(11, 5)), ("mỗi tháng ngày 25", E(10, 25)),
    ("hàng tháng ngày 1", E(11, 1)), ("mỗi 2 tuần", E(11, 6)), ("mỗi chiều 5h", T(10, 23, 17)), ("mùng 1 hàng tháng", E(11, 1)),
    ("15/3 hằng năm", E(3, 15, 2027)),
    # không đọc được ⇒ at None (cũng tính là đúng: không bịa ngày)
    ("mai mốt", None), ("khi nào rảnh", None), ("", None), ("sớm nhất có thể", None),
]


def _ok(phrase: str, want: object) -> bool:
    d = vt.parse(phrase, ANCHOR, "Asia/Ho_Chi_Minh")
    if want is None:
        return d.at is None
    if d.at is None:
        return False
    if isinstance(want, tuple) and want[0] == "am":
        ld, lm, _ly, leap = vt.solar_to_lunar(d.at.date())
        return (ld, lm) == want[1] and not leap and d.at >= ANCHOR and d.lunar
    return d.at == want


def test_vi_time_s2_accuracy_at_least_95_percent() -> None:
    assert len(CASES) >= 120
    bad = [(p, w) for p, w in CASES if not _ok(p, w)]
    rate = 1 - len(bad) / len(CASES)
    assert rate >= 0.95, f"S2 chỉ đúng {rate:.1%}; sai: {bad}"


@pytest.mark.parametrize(("phrase", "want"), CASES, ids=[c[0] or "(rỗng)" for c in CASES])
def test_vi_time_s2_each(phrase: str, want: object) -> None:
    """Từng cụm một (mục tiêu nội bộ 100%): hỏng cụm nào lộ ngay cụm đó."""
    assert _ok(phrase, want), (phrase, vt.parse(phrase, ANCHOR))


def test_vi_time_lunar_known_dates() -> None:
    """Tết Giáp Thìn → Bính Ngọ… và Trung thu: ngày dương đã biết (nguồn ngoài)."""
    assert vt.lunar_to_solar(1, 1, 2024) == date(2024, 2, 10)
    assert vt.lunar_to_solar(1, 1, 2025) == date(2025, 1, 29)
    assert vt.lunar_to_solar(1, 1, 2026) == date(2026, 2, 17)
    assert vt.lunar_to_solar(1, 1, 2027) == date(2027, 2, 6)
    assert vt.lunar_to_solar(15, 8, 2025) == date(2025, 10, 6)
    assert vt.lunar_to_solar(15, 8, 2026) == date(2026, 9, 25)
    assert vt.solar_to_lunar(date(2026, 9, 25))[:3] == (15, 8, 2026)
    assert vt.lunar_to_solar(30, 2, 2026) in (None, date(2026, 4, 17))      # ngày 30 chỉ có ở tháng đủ


def test_vi_time_recurrence_fields() -> None:
    d = vt.parse("hàng tuần thứ 4 lúc 9h", ANCHOR)
    assert d.recurrence is not None and (d.recurrence.freq, d.recurrence.weekday, d.recurrence.hour) == ("week", 2, 9)
    d = vt.parse("ngày 5 hàng tháng", ANCHOR)
    assert d.recurrence is not None and (d.recurrence.freq, d.recurrence.monthday) == ("month", 5)
    d = vt.parse("mỗi 2 tuần", ANCHOR)
    assert d.recurrence is not None and (d.recurrence.freq, d.recurrence.interval) == ("week", 2)
    assert vt.parse("thứ 6", ANCHOR).recurrence is None


def test_vi_time_naive_anchor_and_tz_name() -> None:
    naive = datetime(2026, 10, 23, 10, 30)
    assert vt.parse("ngày mai", naive, "Asia/Ho_Chi_Minh").at == E(10, 24)
    utc = datetime(2026, 10, 23, 20, 0, tzinfo=ZoneInfo("UTC"))       # 03:00 24/10 giờ VN ⇒ "hôm nay" là 24/10
    assert vt.parse("hôm nay", utc, "Asia/Ho_Chi_Minh").at == E(10, 24)


def test_vi_time_urgent_lexicon_single_source() -> None:
    assert isinstance(vt.URGENT_LEXICON, frozenset) and "gấp" in vt.URGENT_LEXICON
    assert vt.has_urgent("Chị ơi GẤP lắm, giao trong hôm nay nhé") and vt.has_urgent("lam ngay lap tuc")
    assert not vt.has_urgent("em giao thứ 6 nhé")
    assert "gấp" in vt.urgent_hits("rất gấp")
