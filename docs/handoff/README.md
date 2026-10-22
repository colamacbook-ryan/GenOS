# Handoff: Gen-Harness (Genesis Harness OS) — webapp hoàn chỉnh

Gói này đủ để một kỹ sư (hoặc Claude Code) chưa từng tham gia cuộc trao đổi thiết kế có thể dựng Gen-Harness thành webapp chạy thật, cài bằng **một lệnh duy nhất** trên mọi hệ điều hành.

## Overview

Gen-Harness là hệ điều hành quản trị dựa trên hội thoại. Các bridge lắng nghe Zalo, WhatsApp… gom mọi tin nhắn về **kho dữ liệu thô**; **core agent** sàng lọc theo quy tắc do Owner định nghĩa, chạy theo chu kỳ thời gian hoặc ngưỡng số lượng, và ghi ra **kho sạch SSOT** gắn ID nhóm + ID người + thời gian. Các **agent trực kênh** trả lời dựa trên dữ liệu sạch và **sổ tay nhận thức** (trí nhớ tạm lũy tiến theo từng ID). Console cho Owner nhìn, hiểu, quản và duyệt mọi thứ; hệ thống không tự ra quyết định nhân sự và không tự thực thi hành động rủi ro cao.

## Thứ tự đọc

| # | Tệp | Vai trò |
|---|---|---|
| 1 | `CLAUDE_CODE_PROMPT.md` | Prompt dán thẳng vào Claude Code |
| 2 | `spec/Gen-Harness-Product-Spec-LOCKED.md` | **SSOT mục đích & nghiệp vụ.** Thắng mọi tài liệu khác khi mâu thuẫn |
| 3 | `design/Gen-Harness Console.dc.html` | **SSOT giao diện.** Mở trực tiếp bằng trình duyệt |
| 4 | `docs/01-ui-screens.md` | 21 màn hình: mục đích, bố cục, thành phần, tương tác |
| 5 | `docs/02-design-tokens.md` + `design/tokens.json` | Màu, chữ, khoảng cách, bo góc, bóng, trạng thái |
| 6 | `docs/03-database.md` (`db/schema.sql` gốc đã bỏ ở v0.1.48; lược đồ hiện hành: `db/sql/0001_baseline.sql` + `apps/api/migrations`) | Mô hình dữ liệu, quy ước mở rộng, lớp thống kê |
| 7 | `docs/04-architecture.md` | Dịch vụ, luồng dữ liệu, plugin, API, bảo mật |
| 8 | `docs/05-installer.md` | Cài một lệnh, TUI có % tiến độ, tự cung cấp mọi công cụ |
| 9 | `docs/06-owner-onboarding.md` | Trình thiết lập từng bước cho Owner mới trên Web UI |
| 10 | `docs/07-acceptance.md` | Định nghĩa "xong" và kế hoạch kiểm thử |
| — | `design/screens.json` | Danh mục 21 màn hình máy đọc được (khoá, miền, cha) |
| — | `design/seed-data.json` | 115 tập dữ liệu mẫu trích nguyên từ thiết kế — dùng để seed |
| — | `spec/github.md` | Ánh xạ màn hình ↔ mục spec |

## About the Design Files

Các tệp trong `design/` là **bản tham chiếu thiết kế dựng bằng HTML** — nguyên mẫu thể hiện đúng hình thức và hành vi mong muốn, **không phải mã sản phẩm để sao chép**. Nhiệm vụ là **dựng lại** các màn hình này trong stack mục tiêu (React + TypeScript, xem `docs/04-architecture.md`) với component, routing, state và API thật.

- Mở `design/Gen-Harness Console.dc.html` trực tiếp trong trình duyệt (cần `support.js` và thư mục `_ds/` nằm cạnh). Bấm danh mục bên trái để chuyển giữa 21 màn hình; các tab, bộ lọc và mục mở rộng đều bấm được.
- Toàn bộ logic dựng dữ liệu nằm trong khối `<script data-dc-script>` cuối tệp (`class Component … renderVals()`). Hằng `NAV` là cây danh mục; `TITLES` là tiêu đề + phụ đề song ngữ của từng màn.
- `design/seed-data.json` là dữ liệu hiển thị đã bóc khỏi phần trình bày. Seed database từ đây để màn hình chạy thật trông giống hệt thiết kế.

## Fidelity

**High-fidelity.** Màu, chữ, khoảng cách, bo góc, trạng thái hover/focus, văn bản và dữ liệu mẫu đều là bản cuối. Dựng lại pixel-perfect ở 1440px và 1280px. Không "cải tiến" giao diện; mọi thay đổi phải hỏi trước.

## Screens

21 màn hình, hai miền. Chi tiết từng màn ở `docs/01-ui-screens.md`.

**Kinh doanh** — Tổng quan điều hành · Hàng đợi & Hành động (Hộp thư ý nghĩa, Bàn làm việc) · Nhóm & Con người · Bản đồ quan hệ (Hồ sơ sống, Sổ tay nhận thức) · Cơ hội & Thị trường (Bảng cơ hội, Cung ↔ Cầu, Kho hội thoại) · Con người & Chất lượng (Đánh giá con người, Chất lượng chăm sóc)

**Kỹ thuật · Backend** — Tầng dữ liệu (Kho dữ liệu thô, Quy tắc sàng lọc, Kho sạch SSOT, Hợp nhất danh tính) · Agent & Model (Danh tính Agent, API & Model, MCP Hub) · Plugin & Tiện ích · Điều khiển hệ thống

Cộng thêm hai bề mặt không có trong Console nhưng bắt buộc phải có: **trình cài đặt TUI** (`docs/05`) và **trình thiết lập Owner** (`docs/06`).

## Yêu cầu không thương lượng

1. **Nền móng dữ liệu vững:** PostgreSQL 16 là SSOT duy nhất; khoá UUIDv7; mọi bảng có `org_id`; bảng sự kiện lớn phân vùng theo tháng; kho thô bất biến; mọi kết luận truy được về bản ghi thô gốc; lớp `analytics` tách riêng cho thống kê. Xem `docs/03-database.md`.
2. **Cài một lệnh, không ỷ lại môi trường:** trình cài tự mang theo hoặc tự cài mọi thứ nó cần, kể cả container runtime. Máy người dùng chỉ cần có shell mặc định của hệ điều hành. Xem `docs/05-installer.md`.
3. **TUI chuyên nghiệp:** một màn hình gọn, thanh tiến độ tổng có %, từng bước có trạng thái, log chi tiết giấu sau phím tắt, lỗi có hướng khắc phục.
4. **Thiết lập Owner từng bước** tự mở ngay khi Web UI lên. Xem `docs/06-owner-onboarding.md`.
5. **Các nguyên tắc có trách nhiệm trong spec** (thang tự trị 0–6, duyệt trước khi ghi ra ngoài, chứng cứ cho mọi điểm số, không tự quyết nhân sự, nhật ký hành động bất biến).

## Assets

- Icon: **Phosphor Icons** 2.1.1 (regular + fill) — `@phosphor-icons/react` ở bản sản phẩm.
- Font: **Inter** 400/500/600/700 — tự host trong image frontend, không gọi Google Fonts lúc chạy (webapp phải chạy offline trong mạng nội bộ).
- Hệ thiết kế: **Nocturne** — `design/_ds/…/styles.css` và `readme.md`.
- Không có ảnh chụp hay minh hoạ. Mã QR, avatar chữ cái và đồ thị đều sinh lúc chạy.
