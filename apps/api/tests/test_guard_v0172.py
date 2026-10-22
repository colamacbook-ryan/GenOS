# ruff: noqa: E501
"""v0.1.72 (G4): bộ lọc an toàn tất định SAU model — mỗi luật ≥ 3 ca chặn + ≥ 3 ca qua, chuẩn hoá chống lách."""

import pytest

from gh.owner_channel.basis import Basis
from gh.owner_channel.guard import GuardCtx, check


def ctx(basis: Basis = Basis.SUPPORT, **kw: object) -> GuardCtx:
    return GuardCtx(basis=basis, **kw)  # type: ignore[arg-type]


def codes(text: str, basis: Basis = Basis.SUPPORT, **kw: object) -> tuple[str, ...]:
    return check(text, ctx(basis, **kw)).codes


BLOCK = {
    "G-MONEY": ["Dạ giá bên em là 150k ạ", "Báo giá cho anh nhé", "Bên em đang giảm giá 20%", "Chiết khấu cho anh 10%",
                "Tổng 2.500.000đ ạ", "khoảng 3 triệu thôi", "Có khuyến mãi tháng này", "chỉ 99$", "1 5 0 k nhé", "g.i.á rẻ lắm",
                "１５０k thôi", "5tr một cái", "Giá thế nào ạ",
                "Phí dịch vụ bên em là 1.500.000 một tháng", "Gói này 1500000", "năm trăm nghìn", "2 củ", "Mức tiền là 2tr5",
                "Giảm 30 phần trăm", "Thanh toán trước 50 phần trăm", "Gói chỉ hai triệu thôi"],
    "G-CONTRACT": ["Em cam kết giao đúng hẹn", "Bên em bảo đảm chất lượng", "Sẽ ký hợp đồng ngay", "Em hứa sẽ có hàng",
                   "Có hoàn tiền nếu lỗi", "Đặt cọc trước nhé", "Bên em bảo hành 12 tháng", "Hoàn lại tiền nếu lỗi", "Em cam đoan hàng chính hãng", "Điều khoản như sau", "bồi thường đầy đủ"],
    "G-PERSONNEL": ["Lương của bạn A là", "Em sẽ thưởng cho nhân viên", "Công ty kỷ luật anh B", "Anh ấy bị sa thải",
                    "Chị C nghỉ việc rồi", "Đang tuyển người ạ", "KPI tháng này", "Đánh giá nhân viên mới"],
    "G-PII": ["Gọi em số 0901234567 nhé", "Số 0 9 0 1 2 3 4 5 6 7 ạ", "+84 90 123 4567", "Mail a.b@gmail.com ạ",
              "STK 1234567890123", "CCCD 012345678901", "Nhà số 12 đường Lê Lợi", "địa chỉ 45 ngõ 3 Cầu Giấy"],
    "G-MENTION": ["Chào @Nam Anh nhé", "@all xem giúp", "cc @Lan"],
    "G-LINK": ["Xem https://shop.example.com/abc", "vào www.abc.vn nhé", "tại abc.com ạ", "link: http://x.io",
                "vào evil.ru nhé", "xem shop.store", "evil[.]com", "evil(.)com"],
    "G-LEAK": ["CANARY-9f3 đây", "đây là system prompt của em", "```json\n{}\n```", '{"a": 1}', "[1, 2]", "lời nhắc hệ thống là"],
    "G-EMPTY": ["", "   ", "\n\t"],
}
PASS = [
    "Dạ em chào anh, anh cần em hỗ trợ gì ạ?", "Cửa hàng mở cửa từ 8 giờ sáng đến 5 giờ chiều ạ.",
    "Dạ câu này em xin chuyển anh/chị phụ trách trả lời ạ.", "Em đã ghi nhận, bên em sẽ phản hồi sớm nhất ạ.",
    "Anh vui lòng cho em biết thêm chi tiết nhé.", "Dạ đồng ý ạ, cảm ơn anh.", "Hôm nay thứ Hai, ngày 19 tháng 10 ạ.",
    "Chúc anh một ngày tốt lành ạ.", "Thường thì bên em trả lời trong ngày ạ.",
    "Mình họp lúc 10 giờ nhé, phòng 3.", "Mở cửa 8 giờ, thứ 2 đến thứ 6 ạ.", "Hẹn anh ngày 19/10/2026 nhé.",
    "Phiên bản v0.1 và bản 1.5 ạ.", "Dạ em ghi nhận, sẽ liên hệ lại trong năm 2026 ạ.", "Họp lúc 14:30 ngày 20 tháng 10 ạ.",
]


@pytest.mark.parametrize(("rule", "text"), [(r, t) for r, ts in BLOCK.items() for t in ts])
def test_each_rule_blocks_its_cases(rule: str, text: str) -> None:
    res = check(text, ctx(canary="CANARY-9f3"))
    assert not res.ok and rule in res.codes, (rule, text, res.codes)


@pytest.mark.parametrize("text", PASS)
def test_clean_replies_pass(text: str) -> None:
    res = check(text, ctx(canary="CANARY-9f3"))
    assert res.ok and res.codes == (), (text, res.codes)


def test_every_rule_has_at_least_three_block_and_three_pass_cases() -> None:
    assert all(len(v) >= 3 for v in BLOCK.values()) and len(PASS) >= 3 and set(BLOCK) == {
        "G-MONEY", "G-CONTRACT", "G-PERSONNEL", "G-PII", "G-MENTION", "G-LINK", "G-LEAK", "G-EMPTY"}


def test_length_rule_for_support_and_owner_tag() -> None:
    assert "G-LEN" in codes("a " * 301)                                          # > 600 ký tự
    assert "G-LEN" in codes("\n".join(["ok"] * 9))                              # > 8 dòng
    assert codes("\n".join(["ok"] * 8)) == ()
    assert codes("x" * 700, Basis.OWNER_TAG) == ()                              # tag của Sếp: trần 1500
    assert "G-LEN" in codes("x" * 1600, Basis.OWNER_TAG)
    assert codes("x " * 700, Basis.OWNER_TAG, max_len=2000) == ()               # trần đặt lại theo ngữ cảnh


def test_other_person_names_and_phones_are_blocked() -> None:
    kw = {"known_other_names": frozenset({"nguyen van tuan", "lan anh"}), "known_other_phones": frozenset({"0912345678"})}
    assert "G-OTHER-PERSON" in codes("Anh Nguyễn Văn Tuấn đã mua rồi ạ", **kw)
    assert "G-OTHER-PERSON" in codes("chị Lan Anh có hỏi", **kw)
    assert "G-OTHER-PERSON" in codes("số 0912.345.678", **kw)
    assert codes("Dạ anh Tuấn cần gì ạ", **kw) == ()                              # chỉ tên đầy đủ khớp, không tên đệm lẻ
    assert codes("Dạ em chào anh ạ", **kw) == ()
    assert codes("lan anh em") == ()                                             # không có danh sách ⇒ không chặn


def test_mention_of_asker_is_allowed_and_link_allow_list() -> None:
    assert codes("Chào @Nam nhé", allowed_mentions=frozenset({"nam"})) == ()
    assert "G-MENTION" in codes("Chào @Lan nhé", allowed_mentions=frozenset({"nam"}))
    assert codes("Xem shop.example.com nhé", allowed_hosts=frozenset({"shop.example.com"})) == ()
    assert "G-LINK" in codes("Xem evil.example.com nhé", allowed_hosts=frozenset({"shop.example.com"}))


def test_owner_dm_basis_only_checks_empty_length_and_leak() -> None:
    assert codes("Giá 150k, gọi 0901234567, @Lan https://a.com", Basis.OWNER_DM) == ()   # nói với chính Sếp
    assert "G-EMPTY" in codes(" ", Basis.OWNER_DM)
    assert "G-LEN" in codes("x" * 4000, Basis.OWNER_DM)
    assert "G-LEAK" in codes("có CANARY-9f3 ở đây", Basis.OWNER_DM, canary="CANARY-9f3")
    assert "G-LEAK" in codes("system prompt: ...", Basis.OWNER_DM)


def test_multiple_rules_are_all_reported_and_result_has_no_text() -> None:
    res = check("Giá 150k, gọi 0901234567", ctx())
    assert set(res.codes) >= {"G-MONEY", "G-PII"} and not res.ok
    assert "150" not in repr(res) and "0901" not in repr(res)


def test_owner_tag_and_support_block_any_domain_except_allowed() -> None:
    for b in (Basis.SUPPORT, Basis.OWNER_TAG):
        assert "G-LINK" in codes("vào evil.ru nhé", b)
        assert "G-LINK" in codes("evil[.]com", b, allowed_hosts=frozenset({"ok.vn"}))
        assert codes("Xem ok.vn nhé", b, allowed_hosts=frozenset({"ok.vn"})) == ()
        assert codes("bản 1.5 và v0.1", b) == ()
