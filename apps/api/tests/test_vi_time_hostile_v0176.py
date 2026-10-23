# ruff: noqa: E501
"""Cụm thời gian do NGƯỜI NGOÀI gõ không được làm hỏng lượt phân tích: số khổng lồ, năm lạ, ngày không có, tháng nhuận sai."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from gh.biz.mem import vi_time as vt

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
A = datetime(2026, 10, 23, 10, 30, tzinfo=TZ)

HOSTILE = [
    "sau 999999999 năm", "999999999999999999999 ngày nữa", "trong 99999999 tuần", "sau 1000000 tháng", "9" * 400 + " ngày nữa",
    "99999999999 tiếng nữa", "99999999999 phút nữa", "999999999 ngày trước", "999999999 năm trước", "1e999 ngày nữa",
    "ngày 0", "ngày 32", "ngày 99999", "tháng 0", "tháng 13", "tháng 99", "0/0", "31/2", "30/2/2027", "29/2/2027", "31/4", "0/10/2026",
    "ngày 1 tháng 1 năm 0", "1/1/0000", "1/1/10000", "1/1/9999", "15/6/1", "ngày 5 tháng 11 năm 99999", "tháng 5 năm 10000",
    "25h", "99h99", "24:00", "30 tháng 2 âm", "15/13 âm", "31/12 âm", "mùng 99 tháng 2 âm", "1/2/0001 âm", "15 tháng 7 nhuận âm",
    "30 tháng 12 nhuận âm lịch", "15/1/10000 âm", "mỗi 99999999 tuần", "mỗi 999999999 năm", "hàng tháng ngày 31", "ngày 31 hàng tháng",
    "mỗi 0 ngày", "thứ 9", "hôm kia kia kia", "ngày kia " * 50,
]


@pytest.mark.parametrize("phrase", HOSTILE, ids=[p[:30] for p in HOSTILE])
def test_vi_time_hostile_never_raises(phrase: str) -> None:
    d = vt.parse(phrase, A)
    assert isinstance(d, vt.Due)
    r = vt.parse_past(phrase, A)
    assert r is None or r.earliest <= r.latest <= A


@pytest.mark.parametrize("phrase", ["sau 999999999 năm", "999999999999999999999 ngày nữa", "9" * 400 + " ngày nữa", "999999999 năm trước",
                                    "1/1/0000", "1/1/10000", "ngày 1 tháng 1 năm 0", "15/1/10000 âm", "1e999 ngày nữa"])
def test_vi_time_hostile_unreadable_is_none(phrase: str) -> None:
    assert vt.parse(phrase, A).at is None
    assert vt.parse_past(phrase, A) is None            # đúng nhánh `event_time_unparsed`


def test_vi_time_hostile_impossible_dates_unreadable() -> None:
    for p in ("31/2", "30/2/2027", "29/2/2027", "31/4"):
        d = vt.parse(p, A)
        assert d.at is None or d.at.month != 2 or d.at.day <= 29, p
    assert vt.parse("30/2/2027", A).at is None


def test_vi_time_hostile_lunar_leap_wrong() -> None:
    assert vt.lunar_to_solar(15, 7, 2026, leap=True) is None            # 2026 không nhuận tháng 7
    assert vt.lunar_to_solar(30, 2, 2026) in (None,) or vt.solar_to_lunar(vt.lunar_to_solar(30, 2, 2026))[:2] == (30, 2)
    assert vt.lunar_to_solar(0, 1, 2026) is None and vt.lunar_to_solar(31, 1, 2026) is None and vt.lunar_to_solar(1, 13, 2026) is None
    assert vt.lunar_to_solar(1, 1, 99999) is None or True               # không ném (ngoài vùng tin cậy)
    assert vt.parse("15 tháng 7 nhuận âm", A).at is None or vt.parse("15 tháng 7 nhuận âm", A).lunar


def test_vi_time_cap_boundary_still_reads_reasonable_offsets() -> None:
    assert vt.parse("5 năm nữa", A).at is not None
    assert vt.parse("sau 99 năm", A).at is not None
    assert vt.parse("sau 101 năm", A).at is None
