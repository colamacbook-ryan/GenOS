# Báo cáo giai đoạn 1 — Nền tảng

Nhánh `claude/project-thread-bnesk5` · 23/09/2026

## Đã xong

| PLAN | Nội dung | Kiểm chứng |
|---|---|---|
| 1.1 | Monorepo (apps/api, apps/web, apps/bridge, packages/*, plugins, db, deploy), `docker compose` 9 dịch vụ, chỉ cổng 8443 mở ra ngoài (Caddy, HTTPS nội bộ), khoá master là Docker secret | `docker compose config` hợp lệ |
| 1.2 | Schema gốc + bổ sung giai đoạn 1 qua Alembic; kho thô và Action Log chặn UPDATE/DELETE/TRUNCATE | migrate lên → xuống → lên sạch; test chặn sửa |
| 1.3 | Đăng nhập (argon2id, cookie HttpOnly, CSRF), 5 vai trò theo ma trận thiết kế, kiểm quyền ở backend | test ma trận vai trò × endpoint, danh mục lọc theo vai trò |
| 1.4 | PIN 6 số: phiên 30 phút trượt, sai 5 lần khoá 15 phút, mọi lần nhập vào nhật ký; thao tác nhạy cảm trả 423 | test luồng PIN và khoá |
| 1.5 | Action Log chuỗi băm, kiểm chuỗi hằng đêm, đứt chuỗi → cảnh báo P1 cho Owner; mọi request ghi đều có dòng nhật ký | test sửa ngầm bị phát hiện đúng dòng, 20 lượt ghi đồng thời vẫn một chuỗi |
| 1.6 | Event bus Redis Streams: consumer chết không mất tin, tin lỗi 5 lần vào DLQ | test |
| 1.7 | Plugin manager: thứ tự nạp theo phụ thuộc, bật/tắt nóng, 5 plugin nền không gỡ/không tắt được (409) | test + API |
| 1.8 | Circuit breaker + sandbox tiến trình con (giới hạn RAM, timeout, chỉ publish stream đã khai báo); plugin bị ngắt mạch tạm dừng đọc, hồi phục thì xử lý tiếp tin còn treo | test |
| 1.9 | Policy 0–6 với luật cứng: gửi ra ngoài, liên quan nhân sự, vượt ngưỡng tiền luôn chờ duyệt ở mọi mức; permit dùng một lần gắn nội dung + đích | bảng chân lý 588 ca |
| 1.10 | Khung Console: sidebar 2 miền, thu gọn/mở rộng, header trạng thái thật (kênh, nhóm, mức tự trị), đăng nhập, hộp PIN | so pixel với thiết kế ở 1440 và 1280: lệch ≤1% (icon font so với SVG), kích thước phần tử khớp |
| 1.11 | Trình thiết lập 12 bước, làm thật bước 1–3; tải lại giữa chừng vẫn đúng bước; mã thiết lập hết hiệu lực khi có Owner | e2e với API thật và với mock |

Số test: API 654 (Postgres 16 + Redis thật), web 56 unit + 8 e2e (mock) + 1 e2e với API thật, bridge 2.

## Còn lại / chưa làm được trong giai đoạn này

- Khoá PIN chưa báo Owner qua Zalo: cần kênh Zalo (giai đoạn 2). Hiện ghi vào nhật ký.
- Chưa build được image Docker trong môi trường làm việc (Docker Hub chặn); CI build cả 4 image khi push.
- TOTP ở bước 2 chưa có (tuỳ chọn, chưa có API).
- Nút "Góc nhìn đã lưu" và "Tìm theo ý định" trên header có hiển thị nhưng chưa có chức năng (màn tương ứng ở giai đoạn sau).
- Các màn nghiệp vụ hiện khung trống "Màn hình này được dựng ở giai đoạn sau", không dùng dữ liệu mẫu.

## Lệch so với spec / thiết kế và lý do

1. **D1 (đã duyệt trong PLAN):** bridge đẩy tin vào event bus, worker ghi kho thô.
2. **Sửa schema gốc:** hàm tạo ID `core.uuid_v7()` trong schema bàn giao bị tràn số (không chạy được); đã sửa, thêm 2 chỉnh cho pg_partman 5.
3. **Console mở sau bước 1–3** (có Owner, tổ chức, xưng hô); bước 4–12 làm tiếp trong trình thiết lập. Handoff 06 không nói phải chặn Console tới bước 12.
4. **Thêm 2 khoá cứng không có công tắc trên màn hình:** "chờ duyệt khi gửi ra ngoài / vượt ngưỡng / nhân sự" và "tool MCP ghi phải duyệt", đúng 8 khoá cứng đã thống nhất.
5. **Đếm "11 màn / 9 màn"** theo đúng cách đếm của thiết kế.

## Cần anh quyết

- **Icon logo:** thiết kế dùng `ph-radar`, icon này không có trong bộ Phosphor nên ô logo trong thiết kế đang trống. Web đang giữ trống cho giống hệt. Anh muốn giữ trống hay chọn một icon?

## Tự kiểm tra

```
make secrets && make up        # rồi mở https://localhost:8443/setup, mã thiết lập: make logs-token
make api-test                  # cần Postgres 16 (pgvector, pg_partman) + Redis ở máy
npm test && npm run -w apps/web test:e2e
```
