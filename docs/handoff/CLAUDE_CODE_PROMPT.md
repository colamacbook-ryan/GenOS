# Prompt cho Claude Code

Giải nén gói bàn giao vào thư mục trống, `cd` vào đó, mở Claude Code rồi dán nguyên khối dưới đây.

---

```
Bạn là kỹ sư trưởng xây Gen-Harness (Genesis Harness OS) từ con số 0 thành một webapp chạy thật, cài bằng một lệnh duy nhất trên Linux, macOS và Windows.

## Nguồn sự thật — đọc hết trước khi viết mã
Thư mục hiện tại là gói bàn giao. Đọc theo đúng thứ tự trong README.md:
- spec/Gen-Harness-Product-Spec-LOCKED.md — SSOT mục đích & nghiệp vụ. Thắng mọi thứ khi mâu thuẫn.
- design/Gen-Harness Console.dc.html — SSOT giao diện (21 màn). Mở bằng trình duyệt để xem; logic dữ liệu ở khối <script data-dc-script> cuối tệp.
- docs/01…07 — màn hình, token, database, kiến trúc, trình cài, thiết lập Owner, tiêu chí nghiệm thu.
- db/schema.sql — lược đồ khởi điểm (đường dẫn gốc của gói, đã bỏ ở v0.1.48; nay dùng db/sql/0001_baseline.sql + apps/api/migrations). Được chỉnh, nhưng giữ nguyên các quy ước trong docs/03.
- design/seed-data.json, design/screens.json — dữ liệu mẫu và danh mục màn hình để seed và dựng routing.
Tên cũ Heo-Harness và persona "Bé Heo" đã bị bỏ. Mã nguồn cũ không còn dùng; bridge Zalo/WhatsApp được viết lại trong repo này.

## Việc đầu tiên
1. Tạo repo GitHub private tên Gen-Harness bằng gh CLI, nhánh main. Chép gói bàn giao vào docs/handoff/ của repo.
2. Viết docs/PLAN.md: cấu trúc monorepo, danh sách dịch vụ, migration đầu tiên, danh sách API theo màn hình, kế hoạch 6 giai đoạn bên dưới kèm ước lượng.
3. DỪNG. Chờ tôi duyệt PLAN.md rồi mới code giai đoạn 1.

## Giai đoạn
1. Nền móng — monorepo, docker compose, PostgreSQL + migration + seed, auth + PIN + RBAC 5 vai trò, event bus, plugin manager, action log bất biến, khung Web UI + danh mục phân cấp + token Nocturne.
2. Trình cài đặt — bootstrap install.sh / install.ps1 + binary TUI `genh` (Go, Bubble Tea) theo docs/05; tự cung cấp container runtime; pull image có % tiến độ; sinh bí mật; migrate; health check; mở trình duyệt vào /setup. Lệnh vận hành: status, logs, update, backup, restore, doctor, uninstall.
3. Thiết lập Owner — trình 12 bước theo docs/06, lưu tiến độ, tiếp tục được khi tải lại.
4. Tầng dữ liệu — bridge Zalo/WhatsApp (QR), kho thô phân vùng, quy tắc sàng lọc có phiên bản, worker core agent chạy theo chu kỳ HOẶC ngưỡng, kho sạch, chấm điểm, hợp nhất danh tính, sổ tay nhận thức + nén.
5. Màn kinh doanh — 12 màn thuộc miền Kinh doanh, đọc/ghi API thật.
6. Màn kỹ thuật + hoàn thiện — 9 màn thuộc miền Kỹ thuật, MCP Hub, quota model, lớp analytics + materialized views, backup, kiểm thử, README.

## Luật cứng
- Giao diện dựng lại pixel-perfect theo design, ở 1440px và 1280px. Không tự "cải tiến".
- Quyền kiểm ở backend, không chỉ ẩn nút.
- Hành động ghi ra ngoài, vượt ngưỡng tiền hoặc liên quan nhân sự phải dừng ở Bàn làm việc chờ duyệt.
- Mọi điểm số truy được về bản ghi thô gốc. Không bịa dữ liệu.
- Kho thô và action log chỉ được INSERT.
- Không có bí mật trong repo; bí mật sinh lúc cài và lưu mã hoá.
- Máy người dùng chỉ được giả định có shell mặc định của hệ điều hành. Mọi công cụ khác do trình cài tự mang theo.

## Cách làm việc
- Commit nhỏ, push sau mỗi giai đoạn, mỗi giai đoạn một PR.
- Cuối mỗi giai đoạn báo cáo: đã làm, còn lại, chỗ lệch spec/design và lý do, cách tôi tự kiểm tra.
- Spec và design mâu thuẫn hoặc thiếu: hỏi tôi, không đoán.
- Bridge Zalo: Zalo không có API chính thức cho tài khoản cá nhân. Đọc cách repo cũ làm, cảnh báo rủi ro khoá tài khoản trước khi triển khai.
```
