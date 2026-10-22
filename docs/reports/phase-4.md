# Báo cáo giai đoạn 4 — Kỹ thuật

Nhánh `claude/zen-lovelace-ph1qa2` · PR #3 · 25/09/2026

## Đã xong

| PLAN | Màn/Mục | Nội dung | Kiểm chứng |
|---|---|---|---|
| 4.1 | Danh tính Agent (`agents`) | Tạo/sửa/nhân bản (tắt sẵn)/tắt agent, PIN cho mọi thao tác nhạy cảm, 6 mẫu có sẵn bật mặc định + 1 tắt mặc định (không mặc định bắt buộc), "đã nói gì, nhân danh gì" tái dùng `GET /agents/decisions` (nền chung giai đoạn 3) | test API + e2e + pixel-diff |
| 4.2 | API & Model (`api`) | Phần lớn đã có từ giai đoạn 1/2 (`/providers`, khoá chỉ lộ `last4`, kiểm kết nối, hồ sơ CLI) — bổ sung `agent.bindings` (gán model theo agent/mục đích + tham số core), chuỗi ưu tiên kéo-thả (+ thay thế bàn phím), hạn mức/giới hạn tốc độ | test API + e2e + pixel-diff |
| 4.3 | MCP Hub (`mcp`) | `agent.mcp_servers/mcp_tools/mcp_grants/mcp_calls` (đã có từ baseline, chỉ thêm chỉ mục); tool mới luôn đóng (`is_exposed=false`); **khoá cứng #4**: máy chủ bật → tool mở → agent có grant, thiếu một → chặn + ghi nhật ký, không cài đặt nào tắt được; tool ghi luôn qua bản nháp chờ duyệt; mạng công cộng mặc định chặn; nhật ký gọi LIVE | test API (đủ khoá cứng) + e2e + pixel-diff |
| 4.4 | Plugin & Tiện ích (`plugins`) | Mở rộng cụm có sẵn từ giai đoạn 1: nạp từ tệp (chữ ký ed25519 + hiện quyền xin + PIN, không chạy mã tải lên, `permissions_status='pending'` cho tới khi duyệt — và vá luôn một lỗ hổng phát hiện được: `PluginManager.load_all()` từng có thể nạp cả plugin chưa duyệt), plugin nền hiện rõ "không gỡ được", reset breaker, nhật ký LIVE bền vững | test API + e2e + pixel-diff |
| 4.5 | Điều khiển hệ thống — Bộ não AI, Quyền hạn, Nhật ký, Dữ liệu & lưu trữ (`system`) | Tab "Bộ não AI" chỉ đọc lại dữ liệu của màn `api` (không trùng logic); ma trận quyền 7×5 sửa được qua API (PIN + log) nhưng **8 khoá cứng ARCHITECTURE §7.4 không lộ ra như ô sửa được** (kiểm bằng test + ẩn hẳn trên UI, không phải bấm rồi báo lỗi); Nhật ký tìm kiếm + xuất CSV (PIN); tab bổ sung Dữ liệu & lưu trữ (spec I: chính sách lưu trữ, xuất/xoá dữ liệu một người, `erase` giữ nguyên chứng cứ thô theo khoá cứng #5) | test API (khẳng định khoá cứng bị từ chối) + e2e + pixel-diff |
| 4.6 | Trình thiết lập bước 10–11 | Bước 10 mời đội ngũ (tạo tài khoản + mật khẩu tạm, chưa có SMTP thật), bước 11 chỉ cấu hình lịch/đích sao lưu (backup thật chạy ở giai đoạn 5) | test API + e2e |

**Tự kiểm tra toàn bộ, xác minh độc lập sau mỗi cụm** (Postgres 16 + pgvector + pg_partman, Redis, Chromium — không cần Docker):
```
cd apps/api && .venv/bin/ruff check gh tests && .venv/bin/mypy gh && .venv/bin/pytest -q     # 851 passed
npm run -w apps/web lint && npm run -w apps/web typecheck && npm run -w apps/web test        # 150 passed
PW_CHROMIUM=<chromium thật> npm run -w apps/web test:e2e                                     # 88 passed (kể cả pixel-diff)
```
Tổng: **851 test API + 150 unit web + 88 e2e** (851 = 791 cuối giai đoạn 3 + 60 mới; thêm 6 màn/tab mới có pixel-diff sidebar/header ở 1440/1280).

## Cách dựng

Mỗi mục lớn (4.1+4.2, 4.3+4.4, 4.5+4.6) được một phiên làm việc riêng dựng backend trước rồi frontend, theo đúng mẫu đã thiết lập ở giai đoạn 3 — nhưng khác giai đoạn 3, **không có khung sẵn** cho các module này (`gh/agents_api/`, `gh/mcp_api/`, `packages/contracts/src/p4-*.ts`, `apps/web/src/screens/{agents,api,mcp,plugins}/` đều dựng mới hoàn toàn từ đầu, không phải điền vào stub có sẵn). Trước khi viết code mới, mỗi phiên đọc kỹ những gì đã có từ giai đoạn 1–3 (`system_api`, `plugins_api`, `providers`) để tránh làm lại — phần lớn 4.2 hoá ra đã có sẵn, chỉ cần bổ sung phần thiếu.

Sau mỗi cụm, kết quả được xác minh lại độc lập (chạy lại lint/typecheck/test/e2e từ đầu, không chỉ tin báo cáo của phiên làm việc) trước khi giao cụm tiếp theo.

## Lỗi/rủi ro phát hiện và xử lý trong lúc làm

1. **Lỗ hổng nạp plugin chưa duyệt**: `PluginManager.load_all()` trước đây sẽ instantiate + gọi `on_load()` mọi manifest trong `ops.plugins` ở lần khởi động kế tiếp, kể cả plugin vừa nạp từ tệp chưa được Owner duyệt quyền. Đã sửa `build_plugin_manager()` (áp cho cả `api` và `worker`) chỉ nạp `permissions_status='approved'`.
2. **Route `/providers/chain` bị `/providers/{pid}` nuốt mất** nếu đăng ký sai thứ tự — đăng ký route tĩnh trước route có tham số (mẫu đã áp dụng nhất quán cho các route mới khác).
3. **Nhiễu do chạy song song nhiều lượt pytest/e2e cùng lúc trên một DB/cổng dùng chung** trong môi trường làm việc (không phải CI) — gây vài lần báo lỗi giả khi tôi tự xác minh song song với phiên đang tự kiểm tra; xác nhận lại bằng cách chạy một lượt sạch, độc lập mỗi lần. Bài học: không chạy `pytest`/`playwright test` song song trên cùng máy trong môi trường này.

## Còn lại / chưa kiểm được trong giai đoạn này

- **Nạp plugin từ tệp**: chỉ kiểm chữ ký + lưu trạng thái chờ duyệt, KHÔNG chạy mã plugin thật (không có sandbox tiến trình con cho gói người dùng tải lên trong phạm vi giai đoạn này) — cố ý, tránh chạy mã không kiểm soát trong môi trường làm việc.
- **MCP**: transport `stdio` chưa gọi được thật (chỉ `http+sse`/`streamable_http`); breaker máy chủ MCP rút gọn thành cờ sức khoẻ, không có state machine mở/nửa-mở đầy đủ như plugin; sau khi duyệt bản nháp `mcp_write` ở Bàn làm việc, việc gọi tool thật ra ngoài CHƯA nối dây executor (chưa có phần tương đương "bridge" cho MCP).
- **Antigravity CLI, QR Zalo/WhatsApp thật**: vẫn chưa kiểm được trên máy thật (đã ghi từ giai đoạn 2, không đổi).
- **Bước 10 (mời đội ngũ)**: chưa có SMTP thật, chỉ tạo tài khoản + mật khẩu tạm.
- **Backup thật** (`pg_dump`/MinIO) chưa chạy — bước 11 chỉ cấu hình, việc backup/restore thật là mục 5.6 giai đoạn 5.
- **1 flake e2e môi trường** tiếp diễn từ giai đoạn 3 (một test ngẫu nhiên trượt khi chạy tuần tự toàn bộ 88 bài trong sandbox, luôn xanh khi chạy lại/chạy riêng) — không phải regression, đáng theo dõi khi có CI thật.

## Lệch so với spec/thiết kế và lý do

1. **Tab "Bộ não AI" của `system` không tự chứa logic sửa** — chỉ đọc lại `useProviders`/`useCliProfiles`/`useFailoverRules` đã có ở màn `api` riêng (đầy đủ hơn), có nút dẫn sang màn `api` để cấu hình. Tránh hai nơi cùng sửa một dữ liệu bằng hai luồng khác nhau.
2. **Tab thứ 5 "Dữ liệu & lưu trữ"** không có trong 4 `sysTabs` gốc của thiết kế — PLAN 4.5 yêu cầu rõ, dựng thêm theo cùng ngôn ngữ thiết kế (Panel/table), không có pixel-diff riêng cho nội dung tab (chỉ sidebar/header của màn `system` nói chung).
3. **Chuỗi ưu tiên provider + hồ sơ CLI** đặt ở màn `api` (theo đúng phạm vi giao việc) dù mockup gốc vẽ ở tab "Bộ não AI" của `system` — không ảnh hưởng pixel-diff (chỉ so sidebar/header).
4. **Ma trận quyền**: một cột thiết kế có thể gộp nhiều quyền con (vd "Đánh giá nhân sự" gồm 3 quyền) — API sửa từng quyền con, UI nhóm lại theo đúng cột thiết kế.

## Tự kiểm tra

```
pg_ctlcluster 16 main start && redis-server --daemonize yes    # nếu chưa chạy

cd apps/api
export GH_TEST_PG="postgresql://postgres:postgres@localhost:5432" GH_TEST_REDIS="redis://localhost:6379/15"
.venv/bin/ruff check gh tests && .venv/bin/mypy gh && .venv/bin/pytest -q

cd ../..
npm run -w apps/web lint && npm run -w apps/web typecheck && npm run -w apps/web test
PW_CHROMIUM=/opt/pw-browsers/chromium-1194/chrome-linux/chrome npm run -w apps/web test:e2e
```
