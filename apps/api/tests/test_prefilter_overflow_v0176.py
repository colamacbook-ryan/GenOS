"""prefilter: chuỗi số cực dài không được làm hỏng quét luật (a) — kẻ gian gửi một tin là tắt được cả lượt."""
from gh.refinery import prefilter


def test_huge_number_does_not_crash_scan():
    body = "chuyển gấp " + "9" * 400 + " triệu vào stk 0123456789 nha"
    cands = prefilter.scan_text(body)                      # trước đây: OverflowError
    assert isinstance(cands, list)


def test_huge_number_amount_is_ignored_not_inf():
    assert prefilter._vnd("9" * 400, "trieu") is None
    assert prefilter._vnd("9" * 400, "k") is None
    assert prefilter._vnd("1" + "0" * 30, "") is None       # vượt trần 10^15


def test_normal_amounts_unchanged():
    assert prefilter._vnd("50", "trieu") == 50_000_000
    assert prefilter._vnd("1,5", "ty") == 1_500_000_000
    assert prefilter._vnd("200", "k") == 200_000
    assert prefilter._vnd("1.200.000", "d") == 1_200_000
