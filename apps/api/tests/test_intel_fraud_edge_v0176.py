# ruff: noqa: E501
"""v0.1.76 (gói T7) — BỘ CA BIÊN LỪA ĐẢO (thuần, không DB): chữ tin ⇒ `fraud.scan` ⇒ `fraud.decide` (ngữ cảnh người dựng tay) ⇒
`levels.compute`. Mỗi ca ghi mức mong đợi; có cặp đúng/suýt và ca đã/chưa nhập OTP. Thêm bộ regex dương/âm kiểu S3 (STK không khớp SĐT /
mã đơn; mồi phishing không có cờ tiền). Chạy với khoá master tạm (băm có khoá)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from gh.biz.intel import levels as L
from gh.biz.intel.detectors import fraud as F
from gh.biz.mem import facts
from tests import intel_helpers as H

OWNER = F.Who(ref="u_owner", person_id=uuid.uuid4(), entity_id=uuid.uuid4(), is_owner=True, business_contact=True, present_in_thread=True)
STRANGER = F.Who(ref="u_new", person_id=uuid.uuid4(), entity_id=uuid.uuid4(), present_in_thread=True)
STAFF = F.Who(ref="u_staff", person_id=uuid.uuid4(), entity_id=uuid.uuid4(), team=True, business_contact=True, present_in_thread=True)
CUSTOMER = F.Who(ref="u_kh", person_id=uuid.uuid4(), entity_id=uuid.uuid4(), customer=True, business_contact=True, present_in_thread=True)
MEMBER = F.Who(ref="u_mem", person_id=uuid.uuid4(), entity_id=uuid.uuid4(), present_in_thread=True)
NCC = F.Who(ref="u_ncc", person_id=uuid.uuid4(), entity_id=uuid.uuid4(), name="Hùng Thép", business_contact=True, present_in_thread=True)
OLD = facts.payment_digest("0987654321")      # khoá master dev ngẫu nhiên theo tiến trình — đủ cho băm có khoá


def _thread(size: int, kind: str = "group") -> L.Thread:
    return L.Thread(type=kind, size=size, owner_in_thread=kind == "group", agent_in_thread=True)  # type: ignore[arg-type]


CTX = {
    "stranger": F.Ctx(sender=STRANGER, stranger=True, owner=OWNER),
    "stranger_addr_owner": F.Ctx(sender=STRANGER, stranger=True, owner=OWNER, addressed=(OWNER,)),
    "staff": F.Ctx(sender=STAFF, owner=OWNER),
    "customer": F.Ctx(sender=CUSTOMER, owner=OWNER),
    "member": F.Ctx(sender=MEMBER, owner=OWNER),
    "imp_ncc": F.Ctx(sender=STRANGER, stranger=True, impersonated=NCC, owner=OWNER, known_digests=frozenset({OLD})),
    "ncc_hist": F.Ctx(sender=NCC, owner=OWNER, known_digests=frozenset({OLD})),
    "ncc_verified_new": F.Ctx(sender=NCC, owner=OWNER, known_digests=frozenset({OLD, facts.payment_digest("0123456789")}),
                              verified_digests=frozenset({facts.payment_digest("0123456789")}), any_verified=True),
    "ncc_nohist": F.Ctx(sender=NCC, owner=OWNER),
}

# (mã ca, chữ, ngữ cảnh, cỡ nhóm | 'dm', mức mong đợi; None = không có tín hiệu)
CASES: list[tuple[str, str, str, int | str, str | None]] = [
    # — OTP / thẻ / mật khẩu ĐÃ nhập (đang diễn ra) ⇒ 🔴 —
    ("otp1", "Em lỡ nhập OTP rồi anh ơi", "staff", 40, "red"),
    ("otp2", "Mình điền rồi nè, nó hỏi thêm số thẻ với mã OTP để xác minh, hơi lạ ta", "customer", 120, "red"),
    ("otp3", "Chị đã nhập mật khẩu vào link đó rồi, giờ sao em", "customer", 80, "red"),
    ("otp4", "Em đăng nhập vào link anh gửi rồi, nó báo trừ 10 triệu", "staff", 300, "red"),
    ("otp5", "Tài khoản em bị trừ 15.000.000đ sau khi bấm link", "staff", 15, "red"),
    ("otp6", "Đã đọc mã OTP cho bên ngân hàng gọi tới rồi", "staff", 10, "red"),
    ("otp7", "Mẹ em đã nhập OTP vào trang giả rồi", "staff", 10, "red"),
    ("otp8", "Em cung cấp mật khẩu với mã OTP cho nhân viên ngân hàng rồi chị ơi", "customer", 40, "red"),
    ("otp_dm", "anh ơi em lỡ gửi mã OTP cho người ta rồi", "staff", "dm", "red"),
    # T12 (G-KB, câu thật của Ngọc): > 3 từ giữa "đăng nhập" và "rồi", rồi "gửi mã OTP … bảo nhập" ⇒ đang bị dụ nhập OTP ⇒ 🔴
    ("otp_ngoc", "Chị ơi em đăng nhập bằng tài khoản portal đại lý của mình rồi, giờ nó gửi mã OTP về máy em bảo nhập để nhận quà, em nhập luôn nhé?",
     "staff", 40, "red"),
    ("otp_long_gap", "Em đã điền thông tin tài khoản đại lý vào trang đó rồi, nó hỏi thêm mã OTP", "staff", 40, "red"),
    # — suýt: CHƯA nhập / cảnh báo ⇒ không 🔴 —
    ("otp_no1", "Đừng ai nhập OTP vào link lạ nha mọi người", "staff", 40, None),
    ("otp_no2", "Em chưa nhập mã OTP, giờ làm sao chặn ạ", "staff", 40, None),
    ("otp_no3", "Ngân hàng không bao giờ hỏi mật khẩu đâu", "member", 40, None),
    ("otp_no4", "Ngân hàng gửi mã OTP về máy, bảo không được nhập vào link lạ nhé mọi người", "staff", 40, None),
    ("otp_no5", "Em chưa đăng nhập bằng tài khoản portal đại lý của mình đâu, nó gửi mã OTP về máy bảo nhập", "staff", 40, None),
    ("otp_no6", "Hôm qua em đăng nhập app ngân hàng rồi, chuyển khoản tiền hàng cho NCC xong xuôi cả", "staff", 40, None),
    # — mạo danh NCC đòi đổi STK ⇒ 🔴 —
    ("imp1", "Anh ơi bên em đổi số tài khoản nhận tiền sang 0123456789 MB nhé, anh chuyển vào số này giúp em", "imp_ncc", 120, "red"),
    ("imp2", "Từ nay chuyển vào tài khoản mới 0123456789 Techcombank giúp em nha, số cũ bị khoá", "imp_ncc", 500, "red"),
    ("imp3", "Anh chuyển tiền hàng vào STK 0123456789 ACB giúp em", "imp_ncc", 60, "red"),
    ("imp_nomoney", "Chào anh, em Hùng đây", "imp_ncc", 60, None),
    # — NCC thật đổi STK khác lịch sử (luật cứng) / đã xác minh (an toàn) / chưa có lịch sử —
    ("stk1", "Bên em đổi STK sang 0123456789 MB, anh chuyển vào số này", "ncc_hist", 120, "red"),
    ("stk_verified", "Anh chuyển vào số 0123456789 MB giúp em nha", "ncc_verified_new", 120, None),
    ("stk_first", "STK công ty: 0123456789 Vietcombank", "ncc_nohist", 120, None),
    ("stk_same", "Anh chuyển vào STK 0987654321 VCB như cũ nhé", "ncc_hist", 120, None),
    ("stk_nohist_change", "Bên em đổi STK sang 0123456789 MB, anh chuyển vào số này", "ncc_nohist", 120, "red"),
    # — người lạ nhờ ứng tiền ⇒ 🔴 (nhắm Sếp / nhóm gần) —
    ("ung1", "Anh Minh ơi ứng giúp em 5 triệu, chiều em trả", "stranger_addr_owner", 150, "red"),
    ("ung2", "Mọi người ơi cho em mượn 2 triệu, chuyển giúp em gấp với", "stranger", 25, "red"),
    ("ung3", "Cho em vay 3tr, mai em gửi lại", "stranger", 10, "red"),
    ("ung_wide", "Mọi người ơi cho em mượn 2 triệu, chuyển giúp em gấp với", "stranger", 150, "amber"),
    ("ung_huge", "Mọi người ơi cho em mượn 2 triệu, chuyển giúp em gấp với", "stranger", 800, "grey"),
    ("ung_known", "Cho em mượn 2 triệu nha chị, cuối tuần em trả", "member", 25, None),
    # — đơn lớn không cọc, giục gửi ngay ⇒ 🔴 —
    ("goods1", "Em lấy 200 thùng nước suối, không cọc nha, gửi ngay trong hôm nay giúp em", "stranger", "dm", "red"),
    ("goods2", "Chốt đơn 50 bao xi măng, không cọc, giao gấp giúp anh", "stranger", 40, "red"),
    ("goods3", "Lấy nguyên lô, cọc sau, gửi luôn chiều nay nhé", "stranger", 300, "red"),
    # — báo nhầm không chịu được (review M4): COD / "nhận hàng rồi thanh toán" là cách mua thường; "giúp em" không kèm chuyển/ứng/mượn —
    ("fp_cod", "Em lấy 20 thùng, ship cod, giao ngay trong hôm nay nhé", "stranger", "dm", None),
    ("fp_cod2", "Lấy nguyên lô, ship COD, gửi luôn chiều nay nhé", "stranger", 300, None),
    ("fp_nhan_hang", "Chốt đơn 50 bao xi măng, nhận hàng rồi mới thanh toán, giao gấp giúp anh", "stranger", 40, None),
    ("fp_giup1", "Anh Minh ơi giúp em kiểm tra xem đã nhận tiền chưa ạ", "stranger_addr_owner", 150, None),
    ("fp_giup2", "Giúp em xem lại tiền ship đơn hôm qua nha", "stranger", 25, None),
    ("fp_cuu", "Cứu em với, tiền điện tháng này cao quá", "stranger", 25, None),
    # — né bằng ký tự lạ (review M1) —
    ("zw1", "Anh ơi bên em đổi số tài khoản sang 0123\u200b456789 MB nhé, anh chuyển vào số này giúp em", "ncc_hist", 120, "red"),
    ("zw2", "Bên em đổi STK sang 0123_456_789 MB, anh chuyển vào số này", "ncc_hist", 120, "red"),
    ("zw3", "Bên em đổi\n\n\nSTK sang 0123/456/789 MB,\n anh chuyển vào số này", "ncc_hist", 120, "red"),
    ("claim1", "Bên em đổi tài khoản nhận tiền rồi, anh chuyển khoản tiền hàng giúp em nhé", "ncc_nohist", 120, "red"),
    # — người lạ gửi STK + đòi thanh toán (review M3) —
    ("nl1", "Anh thanh toán đơn hôm qua 48.500.000đ vào STK 0123456789 MB giúp em", "stranger", 120, "red"),
    ("goods_known", "Chị lấy 200 thùng, cọc sau nha em, gửi ngay trong hôm nay", "customer", 40, None),
    ("goods_small", "Em lấy 2 cái áo, ship COD gửi ngay nha", "stranger", "dm", None),
    ("goods_deposit", "Em lấy 200 thùng, em cọc 30% rồi, gửi ngay giúp em", "stranger", "dm", None),
    # — mồi phishing chưa ai nhập ⇒ tối đa 🟠 —
    ("ph1", "MỪNG 20/10 TẶNG 500.000đ! Nhận quà tại nutrimilk-qua2010.com", "member", 6, "amber"),
    ("ph2", "Cập nhật thông tin tài khoản tại bit.ly/vcb-update để tránh bị khoá", "member", 20, "amber"),
    ("ph3", "Tài liệu họp ở drive.google.com/abc nhé", "staff", 20, None),
    # — không phải lừa đảo —
    ("neg1", "Mã đơn 123456789012 giao chiều nay nhé", "customer", 20, None),
    ("neg2", "Gọi em số 0912345678 nhé", "customer", 20, None),
    ("neg3", "Đơn này anh chuyển khoản trước giúp em nha", "customer", 20, None),
    ("neg4", "Tháng này doanh thu 500 triệu", "staff", 20, None),
]


def _run(text_: str, ctx_name: str, size: int | str) -> L.Level | None:
    sc = F.scan(text_)
    d = F.decide(sc, CTX[ctx_name])
    if d is None or d.record_only:
        return None
    thread = L.Thread(type="dm_agent", size=2, agent_in_thread=True) if size == "dm" else _thread(int(size))
    li = F.signal_input(d, msg_at=H.NOW - timedelta(minutes=3), thread=thread)
    _title, harm, _ = F.wording(d, "nhóm", None)
    li = li.model_copy(update={"harm_what": harm})
    return L.compute(li, H.profile(), H.policy("kinh_doanh"), H.state(), H.NOW)


@pytest.mark.parametrize(("code", "body", "ctx", "size", "want"), CASES, ids=[c[0] for c in CASES])
def test_fraud_edge_case(code: str, body: str, ctx: str, size: int | str, want: str | None) -> None:
    lv = _run(body, ctx, size)
    got = None if lv is None else lv.level
    assert got == want, f"{code}: {got} ({lv.reasons if lv else ''}) ≠ {want}"
    if want == "red":
        assert lv is not None and lv.gate == L.G1 and lv.delivery == "push_now"


def test_fraud_edge_suite_size_and_otp_coverage() -> None:
    assert len(CASES) >= 50
    assert sum(1 for c in CASES if c[0].startswith("otp") and c[4] == "red") >= 8
    assert sum(1 for c in CASES if c[0].startswith("otp_no")) >= 3


@pytest.mark.parametrize("code", [c[0] for c in CASES if c[4] == "red"])
def test_fraud_edge_red_never_demoted_by_d2(code: str) -> None:
    """Mọi ca 🔴 đều có luật cứng ⇒ (d2) không đồng ý / lỗi không hạ (nguyên lý fail-high §4.2.1)."""
    body, ctx, size = next((c[1], c[2], c[3]) for c in CASES if c[0] == code)
    d = F.decide(F.scan(body), CTX[ctx])
    assert d is not None and d.hard, f"{code}: 🔴 phải có luật cứng"
    thread = L.Thread(type="dm_agent", size=2, agent_in_thread=True) if size == "dm" else _thread(int(size))
    li = F.signal_input(d, msg_at=H.NOW - timedelta(minutes=3), thread=thread).model_copy(update={"harm_what": "mất tiền"})
    for d2 in (L.D2(verdict="disagree"), L.D2(error=True)):
        lv = L.compute(li.model_copy(update={"d2": d2}), H.profile(), H.policy("kinh_doanh"), H.state(), H.NOW)
        assert lv.level == "red", f"{code}: (d2) hạ 🔴"


# — bộ regex kiểu S3: STK dương/âm —
STK_POS = ["STK 0123456789 MB", "số tài khoản: 0123 4567 89 Vietcombank", "chuyển khoản vào 19036789012345 Techcombank",
           "tk 1234567890 ACB", "ck vào số 0987654321 (MB)", "Ngân hàng BIDV 31410001234567", "Momo 0912345678 tk em"]
STK_NEG = ["mã đơn 123456789012", "SĐT 0912345678", "hotline 19001234567", "ngày 20251023", "CCCD 079123456789", "giá 12345678 đồng",
           "mã vận đơn 8812345678901", "gọi 0909 123 456"]


@pytest.mark.parametrize("s", STK_POS)
def test_fraud_stk_regex_positive(s: str) -> None:
    assert F.scan(s).accounts, s


@pytest.mark.parametrize("s", STK_NEG)
def test_fraud_stk_regex_negative(s: str) -> None:
    assert not F.scan(s).accounts, s


@pytest.mark.parametrize(("body", "want"), [
    ("Phí ship 50k thôi, còn lại chuyển 50 triệu vào tài khoản giúp em", 50_000_000),
    ("Em lấy hàng, phí ship 50k, đơn 50 triệu, không cọc, gửi ngay giúp em", 50_000_000),
    ("Cọc 200 nghìn, tổng 1,5 tỷ", 1_500_000_000),
    ("tổng 12.500.000đ, ship 30k", 12_500_000),
])
def test_fraud_amount_takes_max_not_first(body: str, want: int) -> None:
    """Tấn công: số nhỏ đứng TRƯỚC số lớn không kéo được số tiền (và mức) xuống."""
    assert F.scan(body).amount_vnd == want


def test_fraud_amount_small_first_does_not_lower_level() -> None:
    small_first = "Em đặt hàng, phí ship 50k, đơn 50 triệu, không cọc, gửi ngay giúp em"
    big_only = "Em đặt hàng, đơn 50 triệu, không cọc, gửi ngay giúp em"
    a, b = _run(small_first, "stranger", "dm"), _run(big_only, "stranger", "dm")
    assert a is not None and b is not None and a.level == b.level == "red"
    da = F.decide(F.scan(small_first), CTX["stranger"])
    assert da is not None and da.amount_vnd == 50_000_000 and "goods_no_deposit_rush" in da.rules
    li = F.signal_input(da, msg_at=H.NOW, thread=L.Thread(type="dm_agent", size=2, agent_in_thread=True))
    assert L.axes(li, H.profile(), H.state(), H.NOW)[0] == 3                    # impact theo số lớn (≥ 50 triệu)


def test_fraud_zero_width_digits_parse_to_same_account() -> None:
    a = F.scan("STK 0123\u200b456789 MB").accounts
    b = F.scan("STK 0123456789 MB").accounts
    assert a and b and a[0].digest == b[0].digest
    body = "đổi STK sang 0123\u200b456\u200b789 MB"
    sp = F.scan(body).accounts[0].span
    assert body[sp[0]:sp[1]].replace("\u200b", "") == "0123456789"         # span về đúng chữ gốc


def test_fraud_no_master_key_uses_claim_rule_and_warns_once(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """m5: thiếu khoá master ⇒ không im lặng bỏ qua: luật cứng `doi_stk_xin_tien`, cảnh báo MỘT lần."""
    import logging

    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(F, "_digest", lambda _d: None)
    monkeypatch.setattr(F, "_NO_KEY_WARNED", False)
    for _ in range(2):
        d = F.decide(F.scan("Anh chuyển tiền hàng vào STK 0123456789 MB giúp em"), CTX["ncc_hist"])
        assert d is not None and F.H_STK_CLAIM in d.hard and "stk_unhashed" in d.rules
    assert sum("thiếu khoá master" in r.getMessage() for r in caplog.records) == 1
