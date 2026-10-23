# ruff: noqa: E501
"""`vi_time.parse_past` (§4F.1 #7): khoảng [earliest, latest] + độ mịn; không bao giờ sau `anchor`; "9h" đọc lúc 8:50 ⇒ 9:00 hôm trước."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from gh.biz.mem import vi_time as vt

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
A = datetime(2026, 10, 23, 10, 30, tzinfo=TZ)          # thứ sáu
A_EARLY = datetime(2026, 10, 23, 8, 50, tzinfo=TZ)


def t(d: int, h: int = 0, m: int = 0, s: int = 0, mo: int = 10) -> datetime:
    return datetime(2026, mo, d, h, m, s, tzinfo=TZ)


PAST: list[tuple[str, datetime, datetime, datetime, str]] = [
    ("hôm qua", A, t(22), t(22, 23, 59, 59), "day"),
    ("chiều hôm kia", A, t(21, 13), t(21, 18), "part_of_day"),
    ("sáng hôm qua", A, t(22, 6), t(22, 12), "part_of_day"),
    ("tối qua", A, t(22, 18), t(22, 22), "part_of_day"),
    ("hôm kia", A, t(21), t(21, 23, 59, 59), "day"),
    ("tuần trước", A, t(12), t(18, 23, 59, 59), "week"),
    ("tuần rồi", A, t(12), t(18, 23, 59, 59), "week"),
    ("tháng trước", A, t(1, mo=9), t(30, 23, 59, 59, mo=9), "week"),
    ("9h", A_EARLY, t(22, 9), t(22, 9), "minute"),            # §4F.1 #7: đọc lúc 8:50 ⇒ 9:00 hôm trước
    ("9h", A, t(23, 9), t(23, 9), "minute"),
    ("9h30 sáng nay", A, t(23, 9, 30), t(23, 9, 30), "minute"),
    ("hôm nay", A, t(23), A, "day"),                          # kẹp về anchor
    ("sáng nay", A, t(23, 6), t(23, 10, 30), "part_of_day"),  # latest kẹp về anchor
    ("hôm qua lúc 3h chiều", A, t(22, 15), t(22, 15), "minute"),
    ("3 ngày trước", A, t(20), t(20, 23, 59, 59), "day"),
    ("2 tuần trước", A, t(9), t(9, 23, 59, 59), "day"),
    ("thứ 2", A, t(19), t(19, 23, 59, 59), "day"),
    ("thứ 4 tuần trước", A, t(14), t(14, 23, 59, 59), "day"),
    ("thứ 6 tuần trước", A, t(16), t(16, 23, 59, 59), "day"),
    ("chủ nhật vừa rồi", A, t(18), t(18, 23, 59, 59), "day"),
    ("ngày 20", A, t(20), t(20, 23, 59, 59), "day"),
    ("ngày 25", A, t(25, mo=9), t(25, 23, 59, 59, mo=9), "day"),
    ("15/10", A, t(15), t(15, 23, 59, 59), "day"),
    ("20/12", A, datetime(2025, 12, 20, tzinfo=TZ), datetime(2025, 12, 20, 23, 59, 59, tzinfo=TZ), "day"),
]


@pytest.mark.parametrize(("phrase", "anchor", "earliest", "latest", "gran"), PAST, ids=[f"{p[0]}@{p[1]:%H:%M}" for p in PAST])
def test_vi_time_past_cases(phrase: str, anchor: datetime, earliest: datetime, latest: datetime, gran: str) -> None:
    r = vt.parse_past(phrase, anchor, "Asia/Ho_Chi_Minh")
    assert r is not None, phrase
    assert (r.earliest, r.latest, r.granularity) == (earliest, latest, gran), (phrase, r)


def test_vi_time_past_never_after_anchor_and_ordered() -> None:
    for phrase in ["hôm nay", "sáng nay", "chiều nay", "tối nay", "9h", "11h", "chiều", "2 tiếng trước", "vừa xong", "ngày 23",
                   "thứ 6", "thứ 7", "cuối tuần", "tuần này", "tháng này"]:
        for anchor in (A, A_EARLY):
            r = vt.parse_past(phrase, anchor)
            if r is None:
                continue
            assert r.earliest <= r.latest <= anchor, (phrase, anchor, r)
            assert r.granularity in vt.GRANULARITIES


def test_vi_time_past_hours_ago_and_unreadable() -> None:
    r = vt.parse_past("2 tiếng trước", A)
    assert r is not None and r.granularity == "hour" and r.earliest < t(23, 8, 30) < r.latest + (A - A) + (t(23, 12) - t(23, 8, 30))
    assert t(23, 8, 20) <= r.latest <= t(23, 8, 50)
    assert vt.parse_past("không rõ lúc nào", A) is None
    assert vt.parse_past("", A) is None
    assert vt.parse_past("mỗi sáng 8h", A) is None             # lịch lặp không phải một thời điểm đã xảy ra


def test_vi_time_past_latest_is_freshest_for_fail_high() -> None:
    """Cụm chỉ có ngày ⇒ khoảng cả ngày; cổng dùng `latest` (tuổi nhỏ nhất)."""
    r = vt.parse_past("hôm qua", A)
    assert r is not None and (A - r.latest).total_seconds() < (A - r.earliest).total_seconds()
