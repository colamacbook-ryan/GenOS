# Báo cáo giai đoạn 5 — Hoàn thiện

Nhánh `claude/zen-lovelace-ph1qa2` · PR #3 · 25/09/2026

Đây là báo cáo tổng hợp; chi tiết từng mục nằm ở các báo cáo con: `phase-5-resilience.md`, `phase-5-performance.md`,
`phase-5-visual.md`, `phase-5-e2e-live.md`, `phase-5-backup.md`.

## Đã xong

| PLAN | Mục | Kết quả | Chi tiết |
|---|---|---|---|
| 5.1 | Seed dữ liệu mẫu đi qua đúng luồng | `gh/seed_demo.py`: 12 người, 7 nhóm, 22 tin thô → 15 đơn vị sạch, 3 cơ hội, 7 tín hiệu ghép, 1 cảnh báo P1, 2 đánh giá con người, 4 agent — mọi chứng cứ lần ngược đúng về `raw.events` thật. `make seed-demo` / `make seed-demo-clean`, idempotent. | — |
| 5.2 | So ảnh pixel 21 màn ở 1440/1280 | Đủ 21/21 màn có mockup, tỉ lệ lệch đều dưới ngưỡng (chi tiết bảng đo trong báo cáo con). | `phase-5-visual.md` |
| 5.3 | Luồng đầu-cuối 4-8 trên hệ thống thật | `apps/web/e2e-live/live-phase3.spec.ts`: cơ hội+tín hiệu cầu thật, agent soạn→Bàn làm việc→duyệt→gửi thật, hợp nhất danh tính, plugin lỗi liên tục→breaker mở, MCP (đọc/ghi/chặn) — chạy qua `bash e2e-live/run.sh`, **2/2 xanh**, xác minh độc lập ổn định qua nhiều lượt. | `phase-5-e2e-live.md` |
| 5.4 | Chịu lỗi (spec M7) | Dừng/khởi động lại Postgres, Redis **thật** giữa chừng: không sập, tự phục hồi. Phát hiện và vá lỗi thật: bản nháp kẹt "approved" vĩnh viễn khi bridge rớt giữa chừng (`expire_stale_permits()` + quét nền 15s). | `phase-5-resilience.md` |
| 5.5 | Row-Level Security + hiệu năng | RLS bật trên các bảng nghiệp vụ chính (lớp phòng thủ thứ hai — hệ thống là "một bản cài = một tổ chức", không phải cách ly SaaS nhiều khách thuê). Benchmark 10 triệu `raw.events` thật: **p95 = 26.6ms** (mục tiêu <150ms), refinery **2.471 bản ghi/phút** (mục tiêu ≥500), RAM rảnh ~14GB lúc tải đỉnh. | `phase-5-performance.md` |
| 5.6 | Backup/restore | `pg_dump`/`pg_restore` thật, mã hoá AES-256-GCM (`gh.crypto`, cùng cơ chế bí mật giai đoạn 1), lưu qua `ObjectStore` (đĩa cục bộ, điểm nối sẵn cho MinIO thật). Vòng đời GFS 7 ngày/4 tuần/12 tháng. Test round-trip thật: seed → backup → xoá → restore → dữ liệu khớp tuyệt đối. `make backup` / `make backup-list` / `make restore`. | `phase-5-backup.md` |
| 5.7 | README | Viết lại từ chỉ có tiêu đề: cài `docker compose up`, tóm tắt 12 bước thiết lập, cảnh báo rủi ro Zalo/WhatsApp cá nhân, sao lưu/khôi phục, phát triển, liên kết ARCHITECTURE/PLAN/báo cáo. | `/README.md` |

**Tự kiểm tra cuối cùng, xác minh độc lập** (Postgres 16 + pgvector + pg_partman + Redis local, Chromium):
```
cd apps/api && .venv/bin/ruff check gh tests && .venv/bin/mypy gh && .venv/bin/pytest -q     # 876 passed, 3 deselected
cd apps/web && PW_CHROMIUM=<chromium thật> bash e2e-live/run.sh                              # 2/2 passed
```
Tổng test API: **876** (860 giai đoạn 1-4 + 16 mới của 5.6: 6 vòng đời GFS thuần + 8 lịch chạy + 2 round-trip thật). 3 test đánh dấu `slow` (chịu lỗi thật, dừng/khởi động lại dịch vụ) loại khỏi lượt mặc định, chạy riêng khi cần.

## Cách dựng và bài học vận hành phiên làm việc

Mỗi mục được một phiên riêng dựng, xác minh lại độc lập trước khi giao mục tiếp theo — như giai đoạn 3-4. Giai đoạn 5 có thêm vài tình huống mới do bản chất "hoàn thiện, đo đạc, xác nhận" của nó, đáng ghi lại:

1. **Nhiều lượt `pytest`/`playwright test` chạy song song trên cùng máy gây nhiễu giả** (lỗi Redis stream "UNBLOCKED", CSDL template bị xoá giữa chừng) — không phải lỗi code. Rút kinh nghiệm: kiểm `ps aux | grep -E "pytest|playwright"` trước khi đụng Postgres/Redis dùng chung, và không chạy song song nhiều lượt tại một máy.
2. **Benchmark tự tạo dữ liệu phi thực tế** (10 triệu dòng rải hết vào cửa sổ 24 giờ, không tổ chức thật nào nhận khối lượng đó) từng khiến số đo đầu tiên sai lệch nặng (p95=3080ms) — phát hiện qua đọc kỹ truy vấn + cách sinh dữ liệu benchmark, không phải qua đoán; sửa cách rải mốc thời gian rồi đo lại ra số thật đạt mục tiêu. Bài học: một benchmark "không đạt" cần soi kỹ *cách tạo dữ liệu benchmark* trước khi kết luận lỗi ở code.
3. **Hai phiên vô tình cùng làm một việc** (agent cũ tự "resume" trùng lúc một agent mới được giao lại đúng việc đó) — xử lý bằng cách nhắn agent cũ dừng và bàn giao sạch (commit trước khi dừng) trước khi để agent mới tiếp tục.

## Còn lại / chưa kiểm được

- **MinIO/Docker thật**: sandbox này không có Docker daemon — `ObjectStore` (Tài liệu, backup) dùng đĩa cục bộ; điểm nối đã sẵn sàng để thay MinIO thật khi trình cài giai đoạn 6 dựng `docker compose`.
- **Cấu hình RLS cho vai trò kết nối phi-superuser**: policy đã đúng và có test xác nhận (`SET LOCAL ROLE`), nhưng vai trò `postgres` hiện dùng là superuser nên Postgres không áp RLS cho chính nó — trình cài giai đoạn 6 cần tạo role `gh_app` không phải superuser.
- **Antigravity CLI, QR Zalo/WhatsApp thật, SMTP mời đội ngũ**: vẫn chưa kiểm được trên dịch vụ ngoài thật (không đổi từ giai đoạn 2/4).
- **1 flake e2e môi trường** (tranh chấp tài nguyên khi chạy dồn dập nhiều test trong sandbox) tiếp diễn từ giai đoạn 3-4, không phải regression — đáng theo dõi khi có CI thật.

## Tự kiểm tra

```
pg_ctlcluster 16 main start && redis-server --daemonize yes    # nếu chưa chạy

cd apps/api
export GH_TEST_PG="postgresql://postgres:postgres@localhost:5432" GH_TEST_REDIS="redis://localhost:6379/15"
.venv/bin/ruff check gh tests && .venv/bin/mypy gh && .venv/bin/pytest -q

cd ../..
npm run -w apps/web lint && npm run -w apps/web typecheck && npm run -w apps/web test
PW_CHROMIUM=/opt/pw-browsers/chromium-1194/chrome-linux/chrome npm run -w apps/web test:e2e
cd apps/web && PW_CHROMIUM=/opt/pw-browsers/chromium-1194/chrome-linux/chrome bash e2e-live/run.sh
```
