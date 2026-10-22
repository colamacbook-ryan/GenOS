"""F123 — lớp che dữ liệu trước khi gửi model phải che mã OTP/mã xác minh, mật khẩu trong nội dung thư và
tham số token/code/key trong URL (QD-16: mọi nội dung qua `mask_for_model`). Kho mẫu thư vi/en."""

import pytest

from gh.chassis.masking import MASK, mask_error, mask_for_model

# (nội dung thư, chuỗi bí mật phải biến mất)
THU_CO_BI_MAT = [
    ("Mã xác minh của bạn là 482913", "482913"),
    ("Mã xác minh của bạn là: 482913. Không chia sẻ mã này.", "482913"),
    ("Mã OTP: 7391", "7391"),
    ("OTP 73915842 có hiệu lực 5 phút", "73915842"),
    ("Mã xác thực 123456 dùng một lần", "123456"),
    ("Mã bảo mật của anh/chị là 5530", "5530"),
    ("Ma xac minh cua ban la 482913", "482913"),
    ("Your verification code is 482913", "482913"),
    ("Your one-time code is: 482913", "482913"),
    ("Use code 482913 to sign in", "482913"),
    ("Security code: 98765", "98765"),
    ("Your OTP is 4821", "4821"),
    ("482913 là mã xác minh của bạn", "482913"),
    ("G-482913 is your Google verification code.", "482913"),
    ("482913 is your verification code", "482913"),
    ("Mã xác minh: 482 913", "482 913"),
    ("Mật khẩu tạm: Abc@12345xyz", "Abc@12345xyz"),
    ("Mật khẩu mới của bạn là Abc@12345xyz.", "Abc@12345xyz"),
    ("Mật khẩu: Zebra-Lamp-Cloud", "Zebra-Lamp-Cloud"),
    ("Tài khoản mới: sếp. mật khẩu: 12345678", "12345678"),
    ("password: hunter2hunter2", "hunter2hunter2"),
    ("Your temporary password is Xk9#mP2qLw", "Xk9#mP2qLw"),
    ("Password = correcthorse", "correcthorse"),
    ("Please reset: https://app.example.com/reset?token=Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MA&lang=vi",
     "Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MA"),
    ("Link: https://x.example.com/verify?email=a%40b.c&code=583920", "583920"),
    ("https://x.example.com/cb?access_token=abcDEF123456&state=1", "abcDEF123456"),
    ("https://x.example.com/u?API_KEY=k3y-v4lue-0001", "k3y-v4lue-0001"),
    ("Mở https://x.example.com/reset-password/Qm9zc1NlY3JldFRva2VuMTIz để đặt lại", "Qm9zc1NlY3JldFRva2VuMTIz"),
    # Gói R (rà soát v0.1.62): biến thể có từ đệm lạ, không dấu, từ khoá kích hoạt/truy cập, fragment URL
    ("Ma OTP giao dich cua Quy khach la 123456", "123456"),
    ("Mã xác thực (OTP) của bạn là 123456", "123456"),
    ("Mã OTP giao dịch của Quý khách là 654321", "654321"),
    ("Ma kich hoat cua ban la 123456", "123456"),
    ("Mã kích hoạt: 123456", "123456"),
    ("Mã truy cập: 123456", "123456"),
    ("Ma dang nhap 1234", "1234"),
    ("Mã đăng nhập 1234", "1234"),
    ("Your OTP for login is 482193", "482193"),
    ("mật khẩu wifi: abc12345", "abc12345"),
    ("Wifi password: abc12345", "abc12345"),
    ("https://x.example.com/cb#access_token=abcDEF123456&state=1", "abcDEF123456"),
    ("https://x.example.com/cb#state=1&id_token=eyJabc123def456", "eyJabc123def456"),
    # Review lần 2 v0.1.62: chữ toàn chiều (fullwidth) chuẩn hoá NFKC trước khi so; mã dính từ khoá; mã nằm dòng dưới
    ("otp１２３４５６", "１２３４５６"),
    ("OTP123456 dùng 5 phút", "123456"),
    ("Mã OTP: １２３４５６", "１２３４５６"),
    ("ＯＴＰ 123456", "123456"),
    ("Mã xác minh của bạn là ４８２９１３", "４８２９１３"),
    ("Mã OTP:\n482913", "482913"),
    ("123456 is your code", "123456"),
    ("4829 is your pin", "4829"),
]

# Thư thường: KHÔNG được bị che oan (nội dung còn đọc được để Gen tóm tắt/soạn trả lời)
THU_THUONG = [
    "Hẹn họp lúc 14:30 ngày 2026-10-20 về đơn hàng 1234.",
    "Please review the code in PR 4821 before Friday.",
    "Mật khẩu của bạn đã được đặt lại thành công.",
    "Your password is required to be at least 8 characters.",
    "Tổng cộng 4 mã giảm giá, trong đó 2 mã còn hạn.",
    "Mã đơn hàng ABC-1234 đã giao.",
    "Xem tại https://example.com/bao-cao?trang=2&lang=vi",
    "Code review hôm nay lúc 3 giờ chiều.",
    # Gói R: không che oan pin pin/điện thoại, năm, mã giảm giá ngắn
    "Điện thoại pin 10000mAh sạc nhanh.",
    "Pin: 5000 mAh, dùng được 2 ngày.",
    "Pin 4500mAh giá 3 triệu.",
    "Cập nhật code 2024 cho kịp tiến độ.",
    "Nhập code 1505 để được giảm 10%.",
    "Mật khẩu của bạn đã được đổi lúc 14:30.",
    "https://example.com/bai-viet#phan-2024",
    # Review lần 2 v0.1.62: "code"/"pin" đứng một mình đi SAU số ngắn không phải mã; từ khoá không "kéo" sang dòng khác
    "1505 là code giảm giá",
    "1505 is the code for discount",
    "2024 là code của dự án",
    "Mã xác thực\nSố đơn của bạn là 123456",
    "Mật khẩu\n\nCảm ơn bạn, đơn của anh là 654321",
]


@pytest.mark.parametrize(("mail", "secret"), THU_CO_BI_MAT)
def test_che_bi_mat_trong_thu(mail: str, secret: str) -> None:
    out = mask_for_model(mail)
    assert secret not in out, out
    assert MASK in out, out


@pytest.mark.parametrize("mail", THU_THUONG)
def test_thu_thuong_khong_bi_che_oan(mail: str) -> None:
    assert mask_for_model(mail) == mail


def test_giu_phan_con_lai_cua_thu() -> None:
    out = mask_for_model("Xin chào Sếp, mã xác minh của bạn là 482913. Cảm ơn!")
    assert out.startswith("Xin chào Sếp, mã xác minh của bạn là ")
    assert out.endswith(". Cảm ơn!")


def test_che_trong_cau_truc_long_nhau_va_loi() -> None:
    data = {"snippet": "Your verification code is 482913", "items": [{"body": "password: hunter2hunter2"}]}
    out = mask_for_model(data)
    assert "482913" not in str(out)
    assert "hunter2hunter2" not in str(out)
    assert "482913" not in mask_error("401: https://x.example.com/cb?code=482913")


def test_khong_pha_id_giu_theo_keep_keys() -> None:
    out = mask_for_model({"id": "18f3a9c2b7d4e601", "body": "OTP 4821"}, keep_keys=frozenset({"id"}))
    assert out["id"] == "18f3a9c2b7d4e601"
    assert "4821" not in out["body"]
