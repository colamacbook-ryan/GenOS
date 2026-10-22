# 07 · Nghiệm thu

## "Xong" cho mỗi màn hình Console

1. Đặt cạnh `design/Gen-Harness Console.dc.html` ở **1440px** và **1280px**: bố cục, màu, chữ, khoảng cách, icon, văn bản khớp. Chênh lệch ≤ 2px.
2. Dữ liệu đọc/ghi qua API thật; với seed mẫu, màn hình hiển thị đúng dữ liệu như thiết kế.
3. Có trạng thái **trống**, **đang tải** (skeleton cùng khung), **lỗi** (thông báo + thử lại).
4. Quyền kiểm ở backend; đăng nhập bằng từng vai trò trong ma trận và xác nhận màn/tác vụ bị ẩn **và** API trả 403.
5. Bộ lọc, tab, mục mở rộng phản ánh vào URL; tải lại giữ nguyên trạng thái.
6. Không chữ nào dưới tỉ lệ tương phản 4.5:1 (3:1 cho chữ ≥ 24px). Không dùng `--color-neutral-700` làm màu chữ.
7. Bàn phím đi hết các điều khiển; focus ring accent.
8. Test: unit cho logic nghiệp vụ, API test cho endpoint, Playwright cho luồng chính của màn.

## Danh sách màn hình

Từ `design/screens.json` — khoá route, miền, màn cha:

| Khoá | Màn | Miền | Cha |
|---|---|---|---|
| `overview` | Tổng quan điều hành | Kinh doanh | — |
| `inbox` | Hộp thư ý nghĩa | Kinh doanh | Hàng đợi & Hành động |
| `workbench` | Bàn làm việc | Kinh doanh | Hàng đợi & Hành động |
| `directory` | Nhóm & Con người | Kinh doanh | — |
| `graph` | Bản đồ quan hệ | Kinh doanh | — |
| `profile` | Hồ sơ sống | Kinh doanh | Bản đồ quan hệ |
| `notebook` | Sổ tay nhận thức | Kinh doanh | Bản đồ quan hệ |
| `opportunity` | Bảng cơ hội | Kinh doanh | Cơ hội & Thị trường |
| `supply` | Cung ↔ Cầu | Kinh doanh | Cơ hội & Thị trường |
| `search` | Kho hội thoại | Kinh doanh | Cơ hội & Thị trường |
| `people` | Đánh giá con người | Kinh doanh | Con người & Chất lượng |
| `care` | Chất lượng chăm sóc | Kinh doanh | Con người & Chất lượng |
| `raw` | Kho dữ liệu thô | Kỹ thuật | Tầng dữ liệu |
| `rules` | Quy tắc sàng lọc | Kỹ thuật | Tầng dữ liệu |
| `clean` | Kho sạch SSOT | Kỹ thuật | Tầng dữ liệu |
| `identity` | Hợp nhất danh tính | Kỹ thuật | Tầng dữ liệu |
| `agents` | Danh tính Agent | Kỹ thuật | Agent & Model |
| `api` | API & Model | Kỹ thuật | Agent & Model |
| `mcp` | MCP Hub | Kỹ thuật | Agent & Model |
| `plugins` | Plugin & Tiện ích | Kỹ thuật | — |
| `system` | Điều khiển hệ thống | Kỹ thuật | — |

## Luồng đầu-cuối phải chạy được

1. **Cài sạch** trên máy không có Docker → TUI tới 100% → trình duyệt mở `/setup`.
2. **Thiết lập** 12 bước → kết nối Zalo bằng QR → bật 1 nhóm → agent đầu tiên.
3. **Tin nhắn thật** trong nhóm → xuất hiện ở Kho dữ liệu thô trong ≤ 2 giây → sau chu kỳ/ngưỡng xuất hiện ở Kho sạch với chứng cứ trỏ về bản ghi thô.
4. Tin nhắn có ý định hỏi giá → tạo **cơ hội** + **tín hiệu cầu** → hiện ở Hàng đợi, Bảng cơ hội, Cung ↔ Cầu.
5. Agent được tag → đọc sổ tay + dữ liệu sạch → soạn trả lời → nếu vượt mức tự trị thì vào **Bàn làm việc** → Owner duyệt → gửi thật → ghi action log.
6. **Hợp nhất danh tính** hai tài khoản → hồ sơ sống gộp lịch sử, sổ tay gộp, action log ghi `identity.merged`.
7. **Plugin**: cài một plugin từ tệp → yêu cầu PIN → hiện quyền → chạy trong sandbox → cố tình làm lỗi → breaker mở, hệ thống chính vẫn chạy.
8. **MCP**: gọi tool đọc → OK; gọi tool ghi → tạo bản nháp chờ duyệt; gọi tool chưa mở → bị chặn, ghi log.
9. `genh backup` → `genh uninstall` (v0.1.40: mặc định giữ dữ liệu; `--keep-data` vẫn nhận) → cài lại → `genh restore` → dữ liệu nguyên vẹn.
9b. **Bản sao ngoài máy (v0.1.40, F-12):** `genh offsite set <thư mục trên USB/NAS>` → lịch Chủ nhật ~05:30 xuất gói `.ghbundle` đã tự kiểm đọc lại được → `genh uninstall --delete-data` (gõ `XOÁ DỮ LIỆU`) → cài sạch máy mới → `GH_BUNDLE_PASSWORD=<Khoá khôi phục> genh import --yes <gói>` → số dòng trùng khớp (E2E job `e2e-offsite`). Rút USB ra rồi chạy lịch ⇒ "Chưa thấy ổ USB/NAS" (GH-EB01), không ghi gì vào ổ chính.
10. `genh update` sang bản mới có migration → dữ liệu còn, rollback được khi migration lỗi.

## Tiêu chí phi chức năng

- Tổng quan tải < 1,5 s (dữ liệu mẫu, máy 4 CPU / 8 GB).
- Truy vấn Tổng quan < 150 ms p95 với 10 triệu `raw.events`.
- Refinery xử lý ≥ 500 bản ghi/phút với một worker (chưa tính độ trễ LLM).
- Toàn bộ hệ thống ở trạng thái rảnh dùng < 2 GB RAM.
- Không dịch vụ nào ngoài `proxy` mở cổng ra host.
- Không bí mật nào trong image, repo hay log.
