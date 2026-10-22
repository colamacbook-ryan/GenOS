# 06 · Trình thiết lập Owner

> **Đã thay bởi v0.1.55 và v0.1.60 — đây là thiết kế gốc, không phải hiện trạng.** Hiện trạng: `/setup` là **một màn 3 bước** (Tài khoản → Nguồn AI → Xong, `?buoc=tai-khoan|nguon-ai|xong`),
> dữ liệu phía sau vẫn là 12 bước (`apps/api/gh/setup/routes.py` `STEPS`). **Chỉ bước 1, 2, 3 và 12 bắt buộc**; bước 4–11 "Để sau" được và làm lại ở màn tương ứng của Console
> (danh sách "Việc nên làm tiếp" ở Mặt tiền › Thêm). Cột "Bắt buộc" dưới đây đã sửa theo mã; bước 8 không còn khung "Thử trò chuyện" (v0.1.55). Đặc tả theo bản: [v0.1.55](../releases/v0.1.55.md), [v0.1.60](../releases/v0.1.60.md).

Route `/setup`. Trình cài mở trình duyệt vào đây kèm mã thiết lập. Chỉ truy cập được khi `ops.setup_state.finished_at IS NULL`; sau đó `/setup` chuyển hướng về Console.

## Khung giao diện

Dùng cùng token và thành phần của Console (`docs/02`) — không phải một giao diện khác.

- Trái (280px, nền `#131524`): logo GEN-HARNESS, danh sách 12 bước. Mỗi bước: số mono, tên, trạng thái (`·` chưa làm, chấm accent đang làm, ✓ OK xong, "bỏ qua" neutral-500). Dưới cùng: thanh tiến độ tổng + "Bước 4/12".
- Phải (fluid, max-width 760px, căn trái): tiêu đề 20px/500, mô tả 12.5px neutral-400, nội dung bước trong thẻ surface, thanh hành động dưới cùng: **Quay lại** (ghost) · **Bỏ qua** (secondary, chỉ bước cho phép) · **Tiếp tục** (primary).
- Tiến độ lưu vào `ops.setup_state` sau mỗi bước → tải lại trang hay đóng trình duyệt vẫn tiếp tục đúng chỗ.
- Mọi thao tác ghi `ops.action_log` với `actor_type=user`.

## 12 bước

| # | Bước | Bắt buộc | Nội dung | Hoàn thành khi |
|---|---|---|---|---|
| 1 | **Chào mừng** | ✓ | Nhập mã thiết lập từ TUI (tự điền nếu có trong URL). Chọn ngôn ngữ giao diện. Chọn **Bắt đầu trống** hoặc **Dùng dữ liệu mẫu** (seed `design/seed-data.json`, xoá được sau ở Điều khiển hệ thống) | Mã hợp lệ |
| 2 | **Tài khoản Owner** | ✓ | Tên hiển thị, email, mật khẩu (≥12 ký tự, đồng hồ độ mạnh), **mã PIN 6 số** nhập hai lần, tuỳ chọn TOTP (hiện QR). Giải thích PIN dùng khi nào (danh sách từ `pinRules`) | Tài khoản tạo xong, đăng nhập tự động |
| 3 | **Tổ chức & xưng hô** | ✓ | Tên tổ chức, múi giờ, tiền tệ. Xưng hô: "Sếp tự xưng là", "Agent gọi Sếp là" — xem trước một câu trả lời mẫu cập nhật trực tiếp | Lưu |
| 4 | **Bộ não AI** | — (Để sau được từ v0.1.29) | Chọn nguồn core agent: **Antigravity CLI** (bấm Đăng nhập → mở luồng OAuth của CLI trong container, hiện mã thiết bị + trạng thái chờ) và/hoặc **khoá API** (Gemini, DeepSeek, tương thích OpenAI). Mỗi khoá có nút **Kiểm tra** gọi thử 1 lượt, hiện độ trễ + model khả dụng. Sắp thứ tự chuỗi chuyển hướng | ≥1 provider kiểm tra OK, hoặc Để sau (Gen chưa trả lời được) |
| 5 | **Kết nối kênh** | — | Thẻ từng kênh. Zalo/WhatsApp: bấm **Tạo mã QR** → QR lớn 240px, đếm ngược 60s tự làm mới, trạng thái chờ quét → đã quét → đang đồng bộ danh sách nhóm (% theo số nhóm). Cảnh báo rủi ro tài khoản cá nhân trước khi hiện QR. Telegram/khác: cài plugin từ chợ | ≥1 kênh `active` |
| 6 | **Chọn nhóm lắng nghe** | — | Bảng nhóm vừa đồng bộ (tên, kênh, số thành viên, loại đoán được). Mỗi nhóm chọn chế độ: Không nghe · Chỉ khi được tag · Nghe im lặng · Chủ động bắt tín hiệu; và phạm vi xem. Mặc định **Không nghe** cho mọi nhóm — Owner phải bật từng nhóm (ranh giới `listen_authorized_only`) | Để sau được (mặc định mọi nhóm "Không nghe") |
| 7 | **Sàng lọc dữ liệu** | — | Chu kỳ thời gian (5/15/30/60 phút) + ngưỡng số lượng (100/250/500/1000) + ngưỡng tin cậy vào kho sạch. Chọn bộ quy tắc khởi đầu theo ngành (tick R-01…R-06, xem trước điều kiện/đầu ra). Bảng trọng số chấm điểm tổng 100% | Lưu |
| 8 | **Agent đầu tiên** | — | Chọn mẫu (Trợ lý thương mại, Key Account, Admin hậu cần, CSKH, Recruiter, Thư ký cá nhân) hoặc tạo trống. Sửa tên, xưng hô, giọng, được nói khi, cấm. Gán kênh/nhóm. ~~Khung **Thử trò chuyện** 3 lượt~~ — bỏ từ v0.1.55 (chọn mẫu là xong, không gọi model) | Agent lưu (gán kênh/nhóm làm sau ở màn Agent) |
| 9 | **Tự trị & ranh giới** | — | Thang 0–6 dạng segmented, mô tả từng mức; mặc định 4. Ngưỡng tiền phải duyệt (mặc định 50.000.000 ₫). Danh sách ranh giới có trách nhiệm (từ `boundaries`), mục khoá hiện công tắc mờ + giải thích vì sao không tắt được | Lưu |
| 10 | **Mời đội ngũ** | — | Thêm email + vai trò (Manager, Operator, Agent nhân viên, Auditor), xem trước ma trận quyền của vai trò đã chọn. Sinh liên kết mời | Bỏ qua được |
| 11 | **Sao lưu** | — | Lịch (hằng ngày 02:00 mặc định), nơi lưu trên máy chủ (bản sao ra ổ USB/NAS chọn ở Điều khiển hệ thống › Dữ liệu & lưu trữ), giữ bao lâu. Nút **Sao lưu thử ngay** với tiến độ % | Bỏ qua được (cảnh báo WARN) |
| 12 | **Hoàn tất** | ✓ | Tóm tắt những gì đã bật. Khung tiến độ **lần sàng lọc đầu tiên** realtime qua WebSocket: bản ghi thô đã gom · đang phân loại · đã vào kho sạch. Nút vào Console (từ v0.1.60: **Vào Mặt tiền**) | Bấm nút |

## Hành vi

- Kiểm tra trường ngay khi rời ô; lỗi hiện dưới ô, 11px, màu BAD, không dùng hộp thoại.
- Nút **Tiếp tục** chỉ bật khi điều kiện "Hoàn thành khi" thoả.
- Bước dài (đồng bộ nhóm, sao lưu thử, đăng nhập CLI) chạy nền; cho phép sang bước khác và quay lại xem.
- Sau khi hoàn tất, Tổng quan hiện dải **"Bắt đầu nhanh"** thu gọn được với các việc còn dở (bước đã bỏ qua, kênh thứ hai, mời đội ngũ) cho tới khi Owner đóng.
- Mọi bước chỉnh lại được sau ở màn tương ứng của Console; trình thiết lập chỉ là đường dẫn đầu tiên.

## Tiếp cận

- Điều hướng bàn phím đầy đủ, `Enter` = Tiếp tục, `Esc` đóng hộp thoại.
- Focus ring accent 2px.
- QR có văn bản thay thế + hướng dẫn bằng chữ.
- Mọi đếm ngược có `aria-live="polite"`.
