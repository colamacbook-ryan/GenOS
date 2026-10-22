# Kiểm toán 1 — Kiến trúc & sức khoẻ mã (Gen-Harness v0.1.31, origin/main `eb5a71b`)

Phạm vi: toàn dự án, chỉ đọc. Bỏ qua vùng chọn model/effort của CLI (providers/cli.py, catalog.py, Step4Brain/CliCard)
vì đang được làm lại ở v0.1.32. Mọi trích dẫn `file:dòng` tính từ gốc repo. Phần "đã chạy thử" ghi rõ ở chỗ có chạy.

---

## (a) Bản đồ hệ thống (ngắn)

```
Trình duyệt ──8443──► Caddy (proxy) ──► web (nginx, React/Vite SPA)
                                   └──► api (FastAPI, uvicorn)  ◄──Redis chính──►  worker (arq)
                                          │  ├ consumer: bridge.inbound/status/directory (ingest)       │ ├ 17 cron
                                          │  ├ vòng quét permit hết hạn                                  │ ├ 3 hook (duty, triage, market)
                                          │  ├ consumer kết quả browser (gh:browser:results)             │ └ Scheduler sàng lọc (LISTEN raw_ingested + nhịp 5s)
                                          │  └ phiên đăng nhập CLI (pty), WS hub
                                          ▼
                                   Postgres 16 (+pg_partman, pgvector) — role gh_app (RLS) / superuser (migrate, backup)
bridge (Node: Baileys/zca-js — WhatsApp/Zalo) ──Redis chính (streams)
browser (Playwright, mạng nội bộ) ── browser-redis riêng ── api/worker;  browser-egress (chỉ tên miền Facebook)
genh (Go, trên máy chủ): cài / update (backup→pull→migrate→rollback) / backup / export-import / auto-update, hộp thư run/
```

**Quy mô mã** (dòng, không tính test):

| Khối | Dòng | Ghi chú |
|---|---|---|
| Backend `apps/api/gh` | 26.4k / 127 tệp | `biz/` 7.1k (7 cụm), `gen/` 2.1k, `refinery/` 1.9k, `providers/` 1.8k, `chassis/` 1.5k, `system_api/` 1.5k, `social/` 1.3k, `auth/` 1.1k, tệp gốc 3.1k (backup, bundle, crypto, bootstrap, seed_demo…) |
| Web `apps/web/src` | 25.0k / 170 tệp | `screens/` 16.3k (13 cụm, ~25 màn), `setup/` 2.7k; + `packages/contracts` 4.6k (viết tay), `packages/ui`, CSS 2.4k |
| Trình cài `apps/genh` (Go) | 12.2k (+7.7k test) | `cmd/genh/main.go` 965 dòng là bộ điều phối lệnh |
| bridge (Node) / browser (Python) | 1.6k / 1.0k | |
| CSDL `db/sql` | 22 migration (0001–0022), ~82 bảng, 9 schema | Alembic chỉ là vỏ gọi SQL thuần (`apps/api/migrations/sqlfile.py`) |

**Việc nền** (`apps/api/gh/worker.py:187-202` + `biz/*/jobs.py` + `backup.py:443`): 17 cron — 2 job mỗi phút
(`social_schedule`, `task_reminder_scan`), `detect_identities` 10 phút, `duty_sweep`/`triage_sweep` 5 phút,
`graph_recompute`/`matches_recompute`/`early_warning_scan`/`scheduled_backup_scan` 15 phút, `partition_maintenance`/
`expire_sessions` mỗi giờ, 6 job hằng ngày (verify chuỗi Action Log, people review, nén sổ tay, purge Gen, purge chuông,
nhắc token Gen-hub); 1 job theo yêu cầu (`backup_now`, timeout 3600 s). Hook chạy trên Redis Streams, mỗi hook một
consumer group, thử lại 5 lần rồi vào `<stream>.dlq` (`chassis/bus.py:111-132`).

**Test** (đếm hàm/khối test): pytest 469 (55 tệp) · vitest 275 (38 tệp) · Playwright mock 120 (10 spec) · live e2e
4 spec · browser 14 · bridge 44 · Go 285 (45 tệp). Tổng ≈ 1.200. **Đã chạy thử `go test ./...` trên bản snapshot: xanh,
~5 giây.**

---

## (b) Điểm mạnh

1. **Kỷ luật vận hành/bảo mật tốt cho dự án một người**: tách khoá (master/bridge/browser/backup), container trình duyệt
   không có đường tới Postgres/Redis chính (`deploy/compose.yaml:141-216`), binary bên thứ ba ghim SHA-256
   (`deploy/images/api.Dockerfile:7-34`), arq dùng JSON thay pickle (`gh/worker.py:182-184`).
2. **`genh update` an toàn**: backup → đồng bộ compose → pull → migrate → tự rollback nếu bước sau lỗi
   (`apps/genh/internal/ops/update.go:56-177`). Đây là lý do chấp nhận được việc 20/22 migration có `downgrade = pass`.
3. **Mẫu nhất quán ở biên**: một kiểu lỗi duy nhất `ApiError` → problem+json, không chỗ nào dùng `HTTPException`
   (`gh/errors.py`); web gọi API qua đúng một client (0 lời gọi `fetch` trực tiếp trong `apps/web/src`).
4. **Thiết kế Gen gọn và an toàn**: công cụ của Gen gọi endpoint nội bộ qua ASGI bằng chính cookie người hỏi → tái dùng
   RBAC/phạm vi, không có SQL riêng (`gh/gen/tools.py:4-7`); xác nhận đề xuất có khoá claim Redis chống bấm đôi
   (`gh/gen/routes.py:278-279`).
5. **Bus sự kiện có cách ly lỗi**: consumer group riêng mỗi hook, đếm lần lỗi, DLQ, `Deferred` (`chassis/bus.py`); hook
   ghi dữ liệu idempotent (`ON CONFLICT (meaning_unit_id)` ở `biz/market/jobs.py:46`); bootstrap có advisory lock nên
   api và worker khởi động cùng lúc không đua (`gh/bootstrap.py:65`).
6. **Action Log chuỗi băm + trigger chỉ-INSERT** (`db/sql/0001_baseline.sql:878`, `gh/chassis/actionlog.py`).
7. **Rất ít nợ đánh dấu**: chỉ 1 TODO thật (`gh/gen/jev.py:11` — schema Jev giả định). Quyết định thiết kế được ghi
   ngay cạnh mã (docstring dày, dù đôi khi quá dài).

---

## (c) Phát hiện

### 🔴 Nghiêm trọng

**R1. Ba thao tác trên web gửi ID giả cứng → luôn lỗi 422 ở bản thật; test không bắt được vì mock dùng cùng ID giả.**
- Bằng chứng: `apps/web/src/screens/queue/InboxScreen.tsx:25-30` (`TEAMMATES = [{id:'u-lan'},{id:'u-minh'},{id:'u-me'}]`,
  dùng ở :234 "Giao cho người khác"); `apps/web/src/screens/market/DealsScreen.tsx:21-26`, dùng ở :239-240 (gán người
  xử lý vụ việc); `apps/web/src/screens/relations/DirectoryScreen.tsx:28-34` (`AGENTS = 'agent-tls'…`, dùng ở :183,
  :403, :462 — gán BOT cho nhóm/người). Backend đòi UUID: `gh/biz/queue/routes.py:226-227` (`user_id: uuid.UUID`),
  `gh/biz/market/routes.py:878-880` (`assignee_user_id: uuid.UUID`), `gh/biz/relations/routes.py:101-103, 199-201`
  (`agent_id: uuid.UUID`). Mock web dùng đúng các ID giả này: `apps/web/test/mock-p3-relations.ts:70-74`.
  Chú thích trong mã còn ghi "chưa có màn Danh mục người dùng (GĐ 4)" — nhưng màn Người dùng (v0.1.22) và Danh tính
  Agent đã có từ lâu.
- Tác động: Giao việc ở Hộp thư, gán người cho Vụ việc, gán BOT ở Nhóm & Con người **không bao giờ chạy** với người dùng
  thật; Boss chỉ thấy "Dữ liệu chưa hợp lệ". Đây cũng là bằng chứng rõ nhất của lỗ hổng phương pháp ở O2.
- Sửa: thay danh sách cứng bằng truy vấn `/users` (đã có, Gen dùng) và `/agents`; thêm 1 test e2e-live cho mỗi thao tác.
- Công sức: **S**.

**R2. "Hạn lưu dữ liệu" trên giao diện là giả — không job nào thi hành; nhiều bảng phình vô hạn.**
- Bằng chứng: `ops.retention_policies` chỉ được đọc/ghi ở `gh/system_api/routes.py:867-901` (GET/PATCH, có PIN) và
  `apps/web/src/screens/system/StorageTab.tsx:54,101`; không nơi nào khác đọc `keep_days`/`anonymize_after_days`
  (grep toàn repo). pg_partman chỉ đặt `retention` cho `ops.plugin_logs` (`db/sql/0001_baseline.sql:855`). Các bảng
  không có purge: `raw.events`, `clean.meaning_units`, `clean.score_snapshots`, `agent.model_calls`, `agent.mcp_calls`,
  `ops.action_log`, `ops.breaker_events` (đều phân vùng tháng, không retention), `refinery.event_state` và
  `refinery.item_marks` (một dòng mỗi tin/đơn vị, `item_marks` giữ `norm_text` ≤600 ký tự —
  `db/sql/0019_v0125_triage.sql:5-30`), `agent.browser_jobs.result` (nội dung Facebook đã đọc; thiết kế hứa "giữ 14
  ngày (job dọn)" ở `docs/design/gen-browser-agent.md:105-107`, chưa làm). `raw.events` và `ops.action_log` có trigger
  cấm DELETE (`0001_baseline.sql:293, 878`) nên chỉ xoá được bằng drop phân vùng.
- Tác động: đĩa đầy dần (máy tự host, không ai trực); các job quét toàn bảng (R3, O4) chậm dần; Owner đặt "giữ 90 ngày"
  và tin là đã xoá — sai sự thật về dữ liệu cá nhân (tin nhắn Zalo/WhatsApp/Facebook).
- Sửa: một module `gh/retention.py` + một cron hằng ngày: đọc `ops.retention_policies` + mặc định an toàn; bảng phân
  vùng → ghi `partman.part_config.retention` (drop phân vùng cũ); bảng thường → DELETE theo lô; `browser_jobs.result`
  14 ngày; `ops.action_log` cần "điểm neo" (lưu hash cuối của phân vùng bị drop để `verify_chain` bắt đầu từ đó). Gộp
  luôn 3 kiểu purge đang rải rác (`gh/gen/store.py:111-119`, `gh/notifications.py:142-160`, `auth_service.purge_expired_sessions`).
  Trước khi làm xong: ẩn/ghi rõ "chưa áp dụng" trên StorageTab.
- Công sức: **M**.

**R3. Sao lưu theo lịch có thể hỏng âm thầm, không chuông, không bù.**
- Bằng chứng:
  - Cron `scheduled_backup_scan` dùng timeout mặc định của arq (300 s) vì `WorkerSettings` không đặt `job_timeout`
    (`gh/worker.py:180-202`) và cron không truyền `timeout` (`gh/backup.py:443`) — trong khi `backup_now` được 3600 s
    (`gh/backup.py:444`). CSDL lớn dần (R2) ⇒ pg_dump + mã hoá vượt 5 phút ⇒ job bị huỷ.
  - Khi bị huỷ, `CancelledError` không phải `Exception` nên nhánh báo lỗi `except Exception` (`gh/backup.py:380-381`)
    không chạy ⇒ **không có chuông "Sao lưu thất bại"**.
  - Toàn bộ dump đọc vào RAM rồi mã hoá đồng bộ trong event loop (`gh/backup.py:256, 260`) ⇒ tốn 2× dung lượng CSDL
    trong RAM và chặn mọi hook/consumer của worker trong lúc mã hoá.
  - Chỉ chạy khi giờ hiện tại nằm trong ±15 phút quanh giờ đặt (`gh/backup.py:333-334`); máy tắt/ worker chết đúng
    lúc đó ⇒ bỏ qua cả ngày, không bù, không cảnh báo "đã X giờ chưa có bản sao lưu".
  - `/ready` không kiểm worker (`gh/shell/routes.py:80-101`), nên worker chết thì mọi cron (kể cả backup) dừng mà
    Console vẫn "khoẻ".
  - Không test nào gọi `scheduled_backup_scan` (chỉ test `is_due` thuần, `apps/api/tests/test_backup.py:94-112`).
- Tác động: lưới an toàn dữ liệu của Boss có thể ngừng mà không ai biết. (Bản backup trước `genh update` chạy qua
  `docker compose` nên không dính timeout này.)
- Sửa: `cron(..., timeout=3600)`; dump theo luồng (pg_dump → mã hoá theo khối → ghi tệp, không `read_bytes`), chạy
  phần CPU trong `asyncio.to_thread`; bắt `BaseException` để chuông báo rồi raise lại; điều kiện "đến hạn" thành "đã qua
  giờ đặt và chưa có bản trong chu kỳ" (tự bù); job kiểm "bản mới nhất cũ hơn 36 giờ" → chuông P1; thêm nhịp tim worker.
- Công sức: **S–M**.

**R4. Log production nuốt traceback; không có mã lỗi nối được giữa web và server.**
- Bằng chứng: `JsonFormatter` chỉ ghi `level/logger/msg`, bỏ `exc_info` (`gh/app.py:218-221`); production bật JSON
  (`GH_ENV` mặc định `production`, `deploy/compose.yaml:12`); uvicorn chạy `log_config=None` (`gh/main.py:12-13`) nên
  log "Exception in ASGI application" của uvicorn đi qua formatter này ⇒ mọi lỗi 500 **mất stack trace**; 13 chỗ
  `log.exception(...)` (vd. `gh/refinery/scheduler.py:86`, `gh/middleware.py:84`) cũng mất. Không có handler
  `Exception` chung (`gh/app.py:196-199`) ⇒ lỗi lạ trả `text/plain "Internal Server Error"`, không mã. Mã lỗi giao
  diện `ERR-…` chỉ sinh ở trình duyệt và in ra console (`apps/web/src/shell/ErrorPage.tsx:90-99`), không gửi về server;
  không có request-id.
- Tác động: Boss báo "màn sập, mã ERR-XXXX" nhưng người sửa (Claude) không có gì để tra; ngược với mục tiêu "tự bảo trì".
- Sửa: formatter thêm `exc`/`stack` + `request_id`; middleware gán `X-Request-ID` (trả về header + đưa vào problem+json);
  handler `Exception` chung trả problem+json `INTERNAL` kèm mã; web gửi lỗi giao diện về `POST /client-errors` (ghi log).
- Công sức: **S**.

### 🟠 Nên sửa sớm

**O1. CI bỏ sót nhiều bộ test đã có.**
- Bằng chứng: không workflow nào chạy `go test` (grep `.github/workflows/*`: không có) dù genh có 285 test — tôi chạy thử
  thì xanh trong 5 giây; Playwright mock (`apps/web/e2e`, 120 test) và live e2e (`apps/web/e2e-live/run.sh`) không chạy
  ở CI (`.github/workflows/ci.yml:94-104` chỉ lint/typecheck/vitest/build); pytest ở CI chạy bằng superuser
  (`ci.yml:59-64`), còn production dùng role `gh_app` + RLS — chế độ `GH_TEST_APP_ROLE=1` (`apps/api/tests/conftest.py:45`,
  `Makefile:44-45`) chỉ chạy tay.
- Tác động: genh (thứ giữ dữ liệu của Boss khi update/khôi phục) có thể hỏng mà PR vẫn xanh; thiếu GRANT/RLS chỉ lộ sau
  khi đã phát hành (e2e-install chạy sau release).
- Sửa: thêm job `go vet ./... && go test ./...`; thêm `npx playwright test` (mock, đã có webServer trong
  `apps/web/playwright.config.ts`); đổi pytest CI sang `GH_TEST_APP_ROLE=1` (hoặc chạy cả hai).
- Công sức: **S**.

**O2. Hợp đồng API và mock viết tay, không đối chiếu với backend → lệch là chuyện thường.**
- Bằng chứng: `packages/contracts/src/schema.ts:1-9` ghi rõ "hand-written… có thể thay bằng openapi-typescript"; 4.6k
  dòng contracts + 7.8k dòng mock (`apps/web/test/mock-*.ts`) mô phỏng lại backend; không test nào so với
  `/api/v1/openapi.json`. Hệ quả đã xảy ra: R1; hotfix v0.1.30 "React error #31 {reasons}" do `detail` là đối tượng
  (`docs/reports/HANDOFF-v0.1.1.md:917-925`).
- Sửa: sinh types từ OpenAPI trong CI (`openapi-typescript`) và so khác biệt; chạy `e2e-live` rút gọn (1–2 luồng chính)
  trong CI với Postgres/Redis service đã có; dần cho mock lấy shape từ types sinh ra.
- Công sức: **M**.

**O3. Hai API sổ tay song song, phân quyền khác nhau → vượt phạm vi dữ liệu.**
- Bằng chứng: `GET/POST /notebooks/{type}/{sid}…` ở `gh/data_api/routes.py:713-725` chỉ kiểm quyền `profile.read/write`,
  không áp `scope_for` (cả `data_api/routes.py` 35 route, 0 lần dùng scope); `GET /notebook/{type}/{sid}` ở
  `gh/biz/relations/routes.py:434-439` có `scope_for`. Ma trận quyền cho Manager phạm vi TEAM và AgentNV phạm vi ASSIGNED
  với `profile.read/write` (`gh/auth/rbac.py:66-67`). Hai màn web dùng hai API khác nhau (`CleanScreen.tsx:208-386` vs
  `screens/relations/queries.ts:76-109`).
- Tác động: vai trò bị giới hạn phạm vi vẫn đọc/ghi được sổ tay của bất kỳ ai qua `/notebooks/*`; hai payload, hai UI cho
  cùng một dữ liệu.
- Sửa: giữ một API (bản relations có scope), cho CleanScreen dùng lại; hoặc tối thiểu thêm `scope_for` vào data_api.
  Rà các route khác của `data_api` cùng câu hỏi (raw/clean chỉ cho `data.read` = Owner/Auditor nên ít rủi ro hơn).
- Công sức: **S–M**.

**O4. Một số job định kỳ có chi phí tăng theo toàn bộ lịch sử, chạy mỗi 10–15 phút.**
- Bằng chứng:
  - `detect_identities` (mỗi 10 phút, `gh/worker.py:194`) tự nối toàn bộ danh tính với nhau và tính `similarity()`
    từng cặp (`gh/identity/service.py:29-41`, điều kiện `OR similarity(...) >= 0.55` không dùng được chỉ mục trigram):
    O(n²) — 5.000 danh tính ≈ 12,5 triệu phép so mỗi lượt; không chạy tăng dần.
  - `graph_recompute` (mỗi 15 phút) có 3/4 truy vấn quét **toàn bộ** `raw.events` không giới hạn thời gian dù có tham số
    cửa sổ (`gh/biz/graph/jobs.py:117-123, 152-157, 179-183`), rồi upsert từng cạnh một lượt CSDL
    (`:104, :139, :168, :216`).
  - `verify_action_log` quét lại toàn chuỗi mỗi đêm (`gh/chassis/actionlog.py:91-116`).
  - Tất cả chạy dưới timeout 300 s mặc định (R3); không có benchmark nào cho các job này (`apps/api/scripts/bench_phase5.py`,
    `docs/reports/phase-5-performance.md` không nhắc).
- Tác động: khi nối kênh Zalo/WhatsApp có nhóm đông, Postgres bận liên tục, job bị cắt giữa chừng, đồ thị/đề xuất gộp
  danh tính ngừng cập nhật — không ai được báo.
- Sửa: detect chỉ xét danh tính mới/đổi từ lần chạy trước (watermark) + giới hạn ứng viên qua chỉ mục trigram
  (`%` operator); graph áp cửa sổ thời gian cho mọi truy vấn và upsert hàng loạt (`INSERT … SELECT … ON CONFLICT`);
  verify chuỗi tăng dần (lưu điểm đã kiểm). Thêm benchmark với 20k danh tính / 1 triệu tin.
- Công sức: **M**.

**O5. Gói chuyển máy (`.ghbundle`) bỏ sót phiên mạng xã hội; danh sách bí mật viết tay không có gì bảo vệ.**
- Bằng chứng: `REENCRYPT_TARGETS` (`gh/bundle.py:100-109`) liệt kê 5 cột; `core.social_accounts.state_enc` (v0.1.29) mã
  hoá bằng khoá master với AAD theo từng dòng `social:{org}:{account}` (`gh/social/service.py:669`) — không có trong
  danh sách và cấu trúc tuple tĩnh không biểu diễn được AAD theo dòng. Sau `genh export → import` sang máy khác khoá,
  `_sealed_state` gọi `crypto.decrypt` (`gh/social/service.py:425`) ném `InvalidTag` — không có handler ⇒ 500 ở nút Kiểm
  tra/Đọc, và lịch đọc mỗi phút lỗi lặp lại.
- Sửa: cho phép AAD là hàm của dòng; thêm `social_accounts`; thêm test "mọi lời gọi `crypto.encrypt` đều có trong
  REENCRYPT_TARGETS" (quét AST) để bản sau không quên; `_sealed_state` bắt lỗi giải mã → đặt `needs_login`.
- Công sức: **S**.

**O6. Lỗi nền vô hình với Boss.**
- Bằng chứng: DLQ chỉ được ghi (`gh/chassis/bus.py:125`), không có chỗ nào đọc/hiển thị/phát lại (grep `dlq`); sự kiện
  `Deferred` được nhận lại mỗi 30 s mãi mãi, không bao giờ vào DLQ (`bus.py:118-120, 151-155`); consumer kết quả
  trình duyệt `xack` cả khi xử lý lỗi (`gh/social/service.py:836-842`); `/ready` chỉ có db/redis/bridge
  (`gh/shell/routes.py:80-101`), không có worker/browser dù `gh:browser:heartbeat` đã tồn tại (`gh/social/protocol.py:25`).
- Sửa: thẻ "Sức khoẻ hệ thống" ở Điều khiển hệ thống: nhịp tim worker (arq health-check key), lần chạy cuối của từng
  cron (ghi Redis khi job xong), số tin DLQ theo stream + nút "Chạy lại"; chuông P1 khi worker im > 10 phút.
- Công sức: **M**.

**O7. Logic nghiệp vụ nằm trong route; route import route; tệp quá lớn.**
- Bằng chứng: `gh/setup/routes.py:18-32` import handler từ 5 module route khác (`create_rule`, `save_schedule`,
  `update_group`, `try_chat`, `set_session_cookies`) và gọi thẳng; `gh/gen/proposals.py:196` import hàm *private*
  `_item_payload, _load_item` của `gh/biz/queue/routes.py`; worker kéo cả web app (`gh/worker.py:19` import `gh.app`;
  `gh/refinery/runner.py:144`, `gh/data/ingest.py:365` import `gh.shell.routes`). SQL thô nằm ngay trong handler
  (vd. `biz/market/routes.py` 63 lần `text(`, `system_api/routes.py` 60). Tệp > 600 dòng: `system_api/routes.py` 986
  (gộp 9 mảng: kênh, nhà cung cấp, CLI, failover, quyền, ranh giới, nhật ký, hạn lưu, yêu cầu dữ liệu —
  xem tiêu đề mục ở :45-865), `biz/market/routes.py` 913, `data_api/routes.py` 894, `biz/relations/routes.py` 858,
  `social/service.py` 849, `setup/routes.py` 830, `biz/queue/routes.py` 720, `biz/people/routes.py` 631; web
  `screens/api/ApiScreen.tsx` 610; Go `cmd/genh/main.go` 965.
- Tác động: sửa một màn dễ làm vỡ bước thiết lập/Gen; khó cho agent đọc trọn ngữ cảnh; kiểm thử phải đi qua HTTP.
- Sửa (dần, không đập đi): khi chạm vào route nào, rút phần logic sang `service.py` cùng cụm; tách `system_api/routes.py`
  theo 9 mảng sẵn có; `setup` và `gen` chỉ gọi service. Không cần làm một lần.
- Công sức: **L** (trải theo các bản).

**O8. Phụ thuộc Python không khoá phiên bản.**
- Bằng chứng: `apps/api/pyproject.toml:6-23` chỉ có cận dưới (`fastapi>=0.115`, `arq>=0.26`…), không có lockfile; ảnh
  build bằng `pip install .` (`deploy/images/api.Dockerfile:49`). Trong khi đó `gh/db.py:73` dùng
  `Depends(get_db, scope="function")` — tính năng chỉ có ở FastAPI bản mới (0.121+), nên cận dưới 0.115 đã sai. Tương
  phản với việc ghim SHA cho agy/claude.
- Tác động: mỗi lần phát hành có thể kéo bản thư viện khác bản CI đã test; một bản FastAPI/arq/redis-py lỗi là hỏng
  release mà không đổi dòng mã nào.
- Sửa: `uv lock` + `uv sync --frozen` trong Dockerfile và CI (cả `apps/browser`).
- Công sức: **S**.

### 🟡 Cải thiện

**Y1. Sao chép-dán giữa các cụm (hệ quả của làm song song theo cụm).** Backend: `person_ref` ×4
(`biz/relations/service.py:17`, `biz/queue/service.py:78`, `biz/people/service.py:24`, `biz/market/service.py:38`),
`group_ref` ×3, `user_ref` ×3, `uid_or_none` ×2, `USER_ROLE_JOIN` ×2 (`biz/people/service.py:45`,
`biz/market/service.py:62`), `strip_accents`/`normalize` ×2 **với thuật toán khác nhau** (`refinery/rules.py:41-53` NFKD,
`refinery/triage.py:87-93` NFD) — cùng câu tiếng Việt có thể chuẩn hoá khác nhau giữa lọc quy tắc và lọc trùng.
Web: `initialsOf` ×5, `fmtVnd` ×4, `CHANNEL_LABEL`/`channelIcon`/`channelTone` ×3, `heatTone` ×3, `EvidenceDialog` ×3
(`screens/graph/graphModel.ts`, `relations/relationsModel.ts`, `market/marketModel.ts`, `people/peopleModel.ts`,
`queue/queueModel.ts`, `data/dataModel.ts`) dù đã có `lib/format.ts`. Đọc/ghi `core.organizations.settings` theo 3 kiểu
(`jsonb_set` ở `gen/store.py:32`, `refinery/triage.py:270`; `settings || …` ở `setup/routes.py:320, 739`). — Gom vào
`gh/biz/refs.py`, `gh/textnorm.py`, `gh/orgsettings.py`, `apps/web/src/lib/format.ts`. **S**.

**Y2. RLS: tốn công bảo trì nhưng gần như không bảo vệ gì.** 24 bảng có RLS, ~29 bảng có `org_id` không có
(vd. `agent.cli_profiles`, `agent.mcp_servers`, `agent.providers`, `ops.action_log`, `refinery.*`, `memory.notebooks`,
`core.channels`, `biz.queue_silences`); chính sách cho qua khi `app.org_id` rỗng (`db/sql/0012_p5_rls.sql:47-54`) và chỉ
đặt biến ở đường request (`gh/auth/deps.py:33`); mỗi cài đặt chỉ có 1 tổ chức (`0012_p5_rls.sql:7-11`). Mỗi bảng mới
phải nhớ chép khối policy. — Quyết định một lần: hoặc ghi rõ "chỉ phòng thủ phụ, không mở rộng thêm" (đơn giản), hoặc
làm thật (worker đặt org, bỏ nhánh NULL). Khuyên phương án đơn giản. **S**.

**Y3. Khung đa-tổ chức không dùng.** 17 vòng `SELECT id FROM core.organizations` trong job/worker (vd. `worker.py:83,
118, 138`, `biz/*/jobs.py`, `refinery/scheduler.py:100`) trong khi bootstrap là "1 bản cài = 1 tổ chức". Không hại, chỉ
thêm nhiễu; khi viết job mới dùng `bootstrap.org_id()` một lần. **M**, ưu tiên thấp.

**Y4. Nền tảng plugin không có plugin thật.** Cả 9 manifest trong `plugins/*/manifest.json` đều `"entry": null`; hạ tầng
nạp/ký/sandbox/ngắt mạch + màn Plugin ≈ 1.900 dòng (`gh/chassis/plugins.py`, `sandbox.py`, `breaker.py`,
`gh/plugins_api/routes.py`, `apps/web/src/screens/plugins/*`) + test. Với một Owner không lập trình, nên ẩn màn "Plugin &
Tiện ích" và đóng băng (không phát triển thêm) cho tới khi có plugin thật. **S** (ẩn) / **M** (gỡ).

**Y5. Hợp đồng `Idempotency-Key` chỉ có một nửa.** Web gửi header này cho mọi request ghi và gửi lại sau PIN
(`packages/contracts/src/client.ts:116-133`, tài liệu `docs/api/phase-1.md:3`), backend không đọc ở đâu (grep
`idempotency` trong `apps/api/gh`: 0). Hoặc làm thật (bảng khoá 24 giờ ở middleware), hoặc xoá khỏi tài liệu. **S**.

**Y6. Phân loại lỗi hạ tầng quá rộng.** Mọi `OSError` → 503 "Mất kết nối CSDL/Redis… thử lại sau ít giây"
(`gh/errors.py:83-92`, đăng ký ở `gh/app.py:199`) — kể cả đĩa đầy (ENOSPC), thiếu quyền ghi, thiếu tệp chạy. Lỗi DB trả
`str(exc)[:200]` cho client (`gh/errors.py:79`) có thể lộ tên ràng buộc/giá trị. — Tách `ConnectionError`/`TimeoutError`
khỏi phần còn lại; ẩn chi tiết DB sau mã lỗi (R4). **S**.

**Y7. Vệ sinh migration/tài liệu lược đồ.** 20/22 `downgrade()` là `pass` nhưng 0002/0003 có downgrade thật (không nhất
quán, chấp nhận được nhờ backup của genh); `0012_p5_rls.sql:47` `CREATE POLICY` không `DROP … IF EXISTS` như các bản sau;
`0001_baseline.sql` dùng chỉ mục không tên (`CREATE INDEX ON …`) — khó tham chiếu khi sửa; `docs/handoff/schema.sql`
(986 dòng) là bản sao song song của baseline vẫn được chép tay thêm ghi chú (`diff` 40 dòng) — hai nguồn sự thật;
`apps/genh/internal/compose/embedded_compose.yaml` lệch `deploy/compose.yaml` (thiếu dịch vụ browser từ v0.1.29) trong khi
`embed.go:14-17` nói "bản sao y hệt" (release ghi đè nên chỉ ảnh hưởng bản build tay). — Bỏ `schema.sql` (hoặc sinh tự
động), thêm kiểm tra CI so `embedded_compose.yaml` với `deploy/compose.yaml`. **S**.

**Y8. Giờ cron theo UTC.** Job nặng hằng ngày rơi vào giờ làm việc ở VN: `verify_action_log` và
`people_review_recompute` cùng 02:30 UTC = 09:30 (`gh/worker.py:192`, `gh/biz/people/jobs.py:162`), nén sổ tay 10:15,
purge 10:40/10:45. — Đặt `timezone` cho WorkerSettings theo múi giờ tổ chức hoặc dời sang 17:00–20:00 UTC. **S**.

**Y9. Phiên bản hiển thị sai.** `gh/__init__.py:3` cứng `0.1.0` (cũng `pyproject.toml:3`, `apps/web/package.json:3`) —
log khởi động và OpenAPI luôn báo 0.1.0 dù `VERSION` là v0.1.31 (`gh/app.py:175, 193`). Đọc từ `VERSION`/hostlink. **S**.

**Y10. Mảnh rỗng/đặt nhầm chỗ.** `biz/core/jobs.py`, `biz/relations/jobs.py` (HOOKS/JOBS rỗng), `biz/duty/routes.py`
(router rỗng, chỉ đăng ký 1 sự kiện WS), `social/permit.py` (chỗ cắm tương lai); job lọc đầu của `refinery` lại đăng ký qua
`biz/queue/jobs.py:255-258`; hai endpoint nhật ký `/audit` và `/audit-log` cùng một hàm (`gh/audit/routes.py:125`,
`gh/system_api/routes.py:824`) với cơ chế phạm vi riêng (`audit/routes.py:28`) khác `scope_for`; tài liệu thay đổi dồn vào
`docs/reports/HANDOFF-v0.1.1.md` (1.053 dòng, chứa v0.1.2→v0.1.31). — Dọn khi tiện; đổi tên thành `CHANGELOG.md` và tách
"ghi chú bàn giao" theo bản. **S**.

**Y11. Web không chia gói theo màn.** `apps/web/src/screens/ScreenPage.tsx:1-40` import tĩnh mọi màn (kể cả d3-force) →
một bundle lớn. Dùng `React.lazy` theo cụm khi cần; ưu tiên thấp (LAN/máy tự host). **S**.

---

## Độ phức tạp so với mục tiêu "đơn giản, tự bảo trì"

- Hệ có 8 dịch vụ chạy thường trực, 17 cron, 3 hook, 4 vòng consumer trong api, ~25 màn (nhiều màn mang tính kỹ sư: Kho
  thô, Quy tắc sàng lọc, Kho sạch, Hợp nhất danh tính, Plugin, MCP Hub, API & Model). Với một Owner không lập trình, phần
  "tự bảo trì" hiện thiếu nhiều hơn phần "tính năng": hạn lưu thật (R2), backup chắc (R3), log tra được (R4), thấy được
  lỗi nền (O6).
- Có thể **bỏ/gộp** mà không mất giá trị: nền tảng plugin (Y4 — ẩn); RLS mở rộng (Y2 — dừng); vòng đa-tổ chức (Y3); API
  sổ tay trùng (O3); 3 kiểu purge → 1 job hạn lưu (R2); `/audit` + `/audit-log`.
- Có thể **ẩn sau "Chế độ kỹ thuật"**: Kho thô, Quy tắc, Kho sạch, Danh tính, Plugin, MCP — Boss vẫn có Gen làm lối vào.

---

## (d) Top 5 khuyến nghị (theo thứ tự làm)

1. **Sửa 3 thao tác dùng ID giả (R1) + chặn tái phát (O2)** — thay `TEAMMATES/AGENTS` bằng `/users`, `/agents`; đưa một
   luồng e2e-live ngắn vào CI (giao việc, gán người, gán BOT, xác nhận đề xuất Gen) chạy trên API thật. *S → M.*
2. **Bật đủ CI (O1, O8)** — `go test`, Playwright mock, pytest dưới `gh_app`, khoá phụ thuộc Python bằng `uv lock`.
   Rẻ, chặn được cả lớp lỗi "PR xanh, bản phát hành hỏng". *S.*
3. **Sao lưu chắc chắn + thấy được sức khoẻ nền (R3, O6)** — timeout 3600 s cho cron backup, dump theo luồng, tự bù khi
   lỡ giờ, chuông "quá 36 giờ chưa có bản sao lưu", nhịp tim worker và "lần chạy cuối" của từng job trên Điều khiển hệ
   thống. *S–M.*
4. **Một job hạn lưu dữ liệu thật (R2, Y1)** — thi hành `ops.retention_policies` bằng partman retention + DELETE theo lô,
   mặc định cho `browser_jobs.result` (14 ngày), `item_marks`, `event_state`; gộp 3 job purge hiện có; trong lúc chưa xong
   thì ghi rõ trên màn Lưu trữ là "chưa áp dụng". Kèm giảm tải job O4 (watermark, cửa sổ thời gian). *M.*
5. **Log tra được (R4)** — formatter giữ traceback, `X-Request-ID` xuyên suốt, handler lỗi chung trả mã, web gửi lỗi giao
   diện về server. Đây là điều kiện để Claude tự sửa lỗi Boss gặp mà không phải đoán. *S.*

(Ngay sau 5 việc trên: gộp API sổ tay để đóng lỗ phạm vi O3, và bổ sung `social_accounts` vào gói chuyển máy O5 — cả hai
đều nhỏ.)
