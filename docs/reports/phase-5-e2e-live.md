# Giai đoạn 5.3 — Luồng đầu-cuối 4–8 trên hệ thống thật

Nguồn: `apps/web/e2e-live/live-phase2.spec.ts` (thiết lập 1–7, tin thật, sàng lọc — đã có từ phiên trước) +
`apps/web/e2e-live/live-phase3.spec.ts` (luồng 4–8, viết mới ở giai đoạn này), chạy nối tiếp trong CÙNG một
phiên api + worker + Postgres/Redis thật (không mock, không Docker) bằng:

```
cd apps/web
PW_CHROMIUM=/opt/pw-browsers/chromium-1194/chrome-linux/chrome bash e2e-live/run.sh
```

**Kết quả lượt chạy cuối: `2 passed (54.4s)` — live-phase2 VÀ live-phase3 đều xanh, xác nhận ổn định qua 2 lượt
chạy liên tiếp (không phải "may một lần").** Ảnh chụp bằng chứng ở `apps/web/test-results/live-shots/12-…` tới
`24-…png` (luồng 4–8), `01-…` tới `11-…png` (luồng 1–3, live-phase2).

## 1. Luồng 4 — Cơ hội + tín hiệu cầu thật (`docs/handoff/07-acceptance.md` mục 4)

Tin "Cần 3 container thép cuộn" (gửi thật ở live-phase2) đi qua refinery + hook `market_signal_capture` thật →
hiện đúng ở Hộp thư (tab Cơ hội), Bảng cơ hội, và Cung ↔ Cầu sau khi gửi thêm một tín hiệu cung mới đủ
`min_confidence` (tín hiệu cung gốc của live-phase2 cố tình độ tin thấp để kiểm bộ lọc, không vào kho sạch —
đúng như thiết kế, không phải lỗi). Xác nhận qua ảnh `12-inbox-opportunity.png`, `13-opportunity-board.png`,
`14-supply-match.png`. **Đạt.**

## 2. Luồng 5 — Agent soạn, Sếp duyệt, gửi thật (mục 5)

Agent tạo qua Console thật (`/agents`, xem phát hiện #1 dưới đây), gán phạm vi kênh Zalo. Ghi một mục sổ tay
thật cho khách hàng, tag agent bằng tin thật → agent đọc sổ tay + dữ liệu sạch (qua `fake_llm.py`, chỉ trả
"draft" khi có tag, mô phỏng đúng điều kiện thật) → bản nháp vào Bàn làm việc với nguồn trích dẫn đúng (chip
"Sổ tay người" + "Hồ sơ …") → Sếp duyệt qua UI thật (PIN) → gửi thật qua `fake_bridge.py` → Action Log ghi
`draft.sent` đúng. Xác nhận qua `15-workbench-draft.png`, `16-workbench-sent.png`, `17-action-log-sent.png`.
**Đạt.**

## 3. Luồng 6 — Hợp nhất danh tính (mục 6)

Hai tài khoản Zalo cùng số điện thoại → `identity.detect()` thật (job worker, ép chạy ngay) → gợi ý hợp nhất
hiện đúng lý do "trùng số điện thoại" → Sếp gộp qua UI thật (PIN) → `GET /identity/history` xác nhận có bản ghi
`op: "merge"`, hộp thoại "Lịch sử gộp" hiện đúng tên → Action Log ghi `identity.merged`. Xác nhận qua
`18-identity-candidate.png`, `19-identity-merged-log.png`. **Đạt.**

## 4. Luồng 7 — Plugin lỗi liên tục, breaker mở (mục 7)

Kiểm bằng **hai đường thật khác nhau** của cùng một hệ thống (lý do ở phát hiện #2 dưới đây):

- **Cài từ tệp qua UI thật**: chữ ký ed25519 THẬT (ký ngoài trình duyệt bằng `sign_plugin.py`, khớp
  `GH_PLUGIN_TRUSTED_SIGNING_KEYS` mà `run.sh` cấu hình cho api — không phải chuỗi giả) + PIN + danh sách quyền
  xin hiện đúng trước khi nạp. Xác nhận qua `20-plugin-install-dialog.png`.
- **Breaker thật tự mở**: dùng plugin `@e2e/exploder` (seed sẵn, `origin=marketplace`, đã `approved`, entry
  `tests.plugin_fixtures:Exploder` luôn `raise`, cô lập trên stream riêng `e2e.plugin.explode`). Bơm lỗi thật
  qua `explode_plugin.py`, breaker mạch thật (`gh.chassis.breaker`) mở sau đủ ngưỡng lỗi/cửa sổ, hệ thống chính
  (`GET /health`, màn Tổng quan) vẫn chạy bình thường trong lúc breaker mở, reset breaker qua UI thành công.
  Xác nhận qua `21-plugin-breaker-open.png`.

**Đạt** (sau khi sửa lỗi phân tán breaker giữa 2 tiến trình — xem mục 6).

## 5. Luồng 8 — MCP thật (mục 8)

Thêm máy chủ MCP thật (`fake_mcp.py`, JSON-RPC `streamable_http`) → khám phá tool thật (`list_customer`,
`update_crm`) → gọi thử tool ghi CHƯA mở → bị chặn đúng khoá cứng #4, ghi vào nhật ký (`data-outcome="blocked"`)
→ mở + cấp `list_customer` (đọc) cho agent → gọi thử → gọi ra máy chủ MCP giả thật, trả kết quả thật
(`data-outcome="ok"`) → mở + cấp `update_crm` (ghi) cho agent → gọi thử → giữ lại chờ duyệt, tạo bản nháp ở Bàn
làm việc, KHÔNG gọi ra máy chủ MCP thật (đúng docstring `gh.mcp_api.routes.call_tool`,
`data-outcome="held_for_approval"`). Xác nhận qua `22-mcp-discovered.png`, `23-mcp-read-ok.png`,
`24-mcp-write-held.png`, và `.wb-list__row` "Gọi tool update_crm" xuất hiện ở Bàn làm việc. **Đạt.**

## 6. Phát hiện quan trọng (đã xác nhận thật, không phải đoán)

### 6.1. Bước 8–9 trình thiết lập vẫn là stub

`apps/web/src/setup/ComingSoonStep.tsx` — bước "Agent đầu tiên" (8) và "Tự trị & ranh giới" (9) của trình thiết
lập (`apps/web/src/setup/`) vẫn dùng `ComingSoonStep`: nút "Tiếp tục" chỉ điều hướng ở client, **không gọi**
`PUT /setup/steps/8` hay `/9` thật. Hệ quả: đi hết trình thiết lập tới "Hoàn tất" (như live-phase2 làm) không
tạo ra agent nào. Đây là khoảng trống **của bản thân trình thiết lập**, ngoài phạm vi 5.3 — không sửa ở đây.
`live-phase3` tránh phụ thuộc bước này bằng cách tạo agent qua đúng màn Console thật đã có
(`/agents` → "Dùng mẫu" → chọn mức tự trị + phạm vi kênh → PIN), đúng đường mà một Sếp thật cũng sẽ dùng nếu
muốn thêm agent sau khi hai bước 8–9 được xây xong.

### 6.2. Cài plugin từ tệp không chạy runtime thật (đúng phạm vi có chủ đích)

`gh.plugins_api.routes.install_local` nói rõ trong docstring: nạp từ tệp (`local_file`) chỉ kiểm chữ ký + PIN
rồi lưu `ops.plugins` ở `permissions_status='pending', is_enabled=false` — **không đăng ký vào `PluginManager`
đang chạy, không tự bật**, vì backend chưa có sandbox tiến trình con cho một gói bất kỳ do người dùng tải lên.
Đây là phạm vi tối thiểu có chủ đích từ giai đoạn 4, không phải lỗi phát sinh ở 5.3. Vì vậy luồng 7 phải kiểm
hai nửa qua hai đường khác nhau (mục 4 ở trên) thay vì một luồng liền mạch "cài từ tệp → tự lỗi → breaker mở".

## 7. Lỗi đã sửa thêm trong phiên này (chưa có ở 2 phiên trước)

1. **Breaker phân tán giữa 2 tiến trình (luồng 7)**: `@e2e/exploder` được `build_plugin_manager` nạp ở CẢ hai
   tiến trình api và worker, và cả hai cùng đọc MỘT nhóm tiêu thụ Redis (`PluginManager._consume`, group =
   package name) trên stream `e2e.plugin.explode` — 5 sự kiện lỗi bị **chia** ngẫu nhiên giữa hai tiến trình
   thay vì cả hai đều thấy đủ. Xác nhận thật qua log: worker nhận 4 lỗi (tự mở breaker của nó), api chỉ nhận 1
   (breaker riêng của api — chính là cái `GET /plugins` trả lời, vì route đọc
   `request.app.state.plugins` của tiến trình đang phục vụ HTTP — vẫn `closed`). Breaker là trạng thái
   **trong-tiến-trình**, không đồng bộ giữa api/worker. Sửa: bơm liên tục theo đợt nhỏ (mỗi đợt 5 sự kiện, poll
   8s) tới khi CHÍNH breaker của tiến trình api tự mở, trong cùng cửa sổ 120s của breaker, thay vì bơm một lần
   rồi đợi suông 30s.
2. **2 nút "Đóng" trùng tên trong hộp thoại "Gọi thử" (luồng 8)**: nút X ở đầu hộp thoại
   (`gh-dialog__close`, chỉ có `aria-label="Đóng"`) và nút phụ ở chân hộp thoại (`gh-dialog__actions`, có chữ
   "Đóng") cùng khớp `getByRole('button', { name: 'Đóng' })` → strict mode violation. Sửa: thu hẹp về đúng nút
   chân hộp thoại qua `.gh-dialog__actions` ở cả 3 lượt gọi thử (tool bị chặn, tool đọc OK, tool ghi chờ duyệt).
3. **`.check()` với checkbox cấp quyền MCP không cập nhật lạc quan**: checkbox "Cấp `<tool>` cho `<agent>`" ở
   ma trận cấp quyền MCP là controlled (`checked={t.grants.includes(agentKey)}`), không cập nhật lạc quan — chỉ
   phản ánh đúng sau khi mutation `POST /grants` hoàn tất. `.check()` của Playwright tự xác minh `checked` NGAY
   sau lượt click nên báo lỗi "Clicking the checkbox did not change its state" dù thao tác đúng. Sửa: dùng
   `.click()` thuần, đợi đúng response `POST /grants`, rồi `expect(...).toBeChecked()` (có retry) — cho cả hai
   checkbox cấp quyền (đọc `list_customer`, ghi `update_crm`).

Cả ba lỗi trên là lỗi thật của kịch bản test khi chạy trên hệ thống 2 tiến trình thật (không phải lỗi sản
phẩm) — điểm 1 đáng chú ý vì nó lộ ra một đặc điểm kiến trúc thật (breaker không đồng bộ giữa các tiến trình
chạy cùng plugin), không chỉ là vấn đề của riêng bộ test.

## 8. Kiểm tra tĩnh

```
npm run -w apps/web lint       # sạch
npm run -w apps/web typecheck  # sạch
```

## 9. Cách chạy lại

```
cd apps/web
PW_CHROMIUM=/opt/pw-browsers/chromium-1194/chrome-linux/chrome bash e2e-live/run.sh
```

Yêu cầu: Postgres 16 + Redis chạy sẵn trên máy (không Docker), Playwright Chromium tại
`/opt/pw-browsers/chromium-1194/chrome-linux/chrome` (máy này không có cache Playwright mặc định — bắt buộc
truyền `PW_CHROMIUM`).

## 10. Chế độ CI (v0.1.35, F-14)

Mục tiêu: lỗi kiểu "mock xanh, máy thật hỏng" (vd ô chọn người dùng viết cứng id `u-lan` → API thật trả 422) bị CI
chặn ngay ở PR. Job `api` của `.github/workflows/ci.yml`, sau hai lượt pytest, chạy bản e2e thật RÚT GỌN trên đúng
Postgres (image `gh-db`) + Redis của job:

```
cd apps/web
LIVE_SPECS=live-ci GH_LIVE_PG=postgresql://postgres:postgres@localhost:5432 bash e2e-live/run.sh
```

- `LIVE_SPECS` chọn tệp spec; mặc định `"live-phase2 live-phase3"` (chạy tay như mục 9, không đổi).
- `run.sh` báo rõ nếu thiếu `psql` / `redis-cli` / `curl`; CI tự cài `redis-tools` (và `postgresql-client` nếu thiếu).
- `e2e-live/live-ci.spec.ts` tự đủ (không cần live-phase2): thiết lập NHANH bằng API — bước 1–3, PIN, nhà cung
  cấp `openai_compat` tới model giả `fake_llm.py`, bước 4, đăng nhập Zalo qua bridge giả, bật nghe nhóm g-si, bước 7,
  bơm tin "Cần 3 container thép cuộn, báo giá giúp chị", sàng lọc → có mục Hộp thư; tạo người dùng thứ hai THẬT
  (operator, dữ liệu nội bộ trên CSDL `gh_live` bị xoá mỗi lần chạy) và một trợ lý từ mẫu `commercial`. Id thật lấy
  từ `GET /pickers/users`, `/pickers/agents`.
- 4 luồng qua GIAO DIỆN thật (Chromium):
  1. (a) Hộp thư → thẻ đầu → 'Giao cho người khác' → tên người dùng mới → `POST /inbox/{id}/assign` 200, `user_id`
     là UUID thật; đọc lại qua `/audit` (`queue.assigned`) + `core.assignments` trong CSDL thử.
  2. (b) Vụ việc tạo qua API (chủ thể: Nguyễn Thị Lan từ kho sạch) → Deal & Vụ việc › 'Vụ việc' → 'Đổi' → 'Gán người
     xử lý' → `PATCH /cases/{id}` 200; `GET /cases/{id}` có `assignee.id` đúng UUID.
  3. (c) Nhóm & Con người › Nhóm → 'Chợ thép sỉ miền Nam' → 'Đổi' → 'Gán BOT trực nhóm' → trợ lý → 'Lưu' →
     `POST /directory/groups/{id}/bot` 200; `GET /directory/groups` có `bot.id` đúng UUID trợ lý.
  4. (d) Khung Gen: "Giao việc hỏi giá thép cho Lê Văn Hải" → `fake_llm.py` (nhánh Gen) gọi `staff.list` rồi
     `queue.list`, đề xuất `assign` với id LẤY TỪ KẾT QUẢ TOOL → thẻ 'Đề xuất: Giao người phụ trách' → 'Xác nhận' →
     `POST /gen/proposals/{id}/confirm` 200; mục Hộp thư được giao đúng UUID người đó (trước đó trả mục về 'Tôi').
- Thao tác PIN dùng helper khoan dung `maybeEnterOwnerPin` (xanh cả trước và sau gói f20).

Thời gian đo (máy phát triển, Postgres/Redis có sẵn, Chromium đã cài): `run.sh` trọn gói **27 giây**, trong đó
Playwright 19,6 giây (thiết lập API 9 s, 4 luồng ~10 s). Trên CI cộng thêm `npm ci` + cài Chromium (cache theo phiên
bản `@playwright/test`) — mục tiêu tổng thêm ≤ 5 phút; bước e2e có `timeout-minutes: 8`, job `api` nâng 40 → 48 phút.

Khi đỏ:
- Log bước CI in tên test đỏ + lỗi Playwright, rồi 120 dòng cuối `api.log` và `worker.log` (không có khoá: khoá
  `GH_*` sinh ngẫu nhiên chỉ nằm trong env).
- Artifact `e2e-live-<lần chạy>` (giữ 7 ngày): `apps/web/test-results/live-shots` (ảnh `ci-a…ci-d-*.png`, `api.log`,
  `worker.log`, `llm.log`, `bridge.log`, `web.log`) và `apps/web/test-results/live` (`error-context.md` = ảnh chụp cây
  truy cập của trang lúc lỗi).
- Chạy lại cục bộ đúng lệnh trên (cần Postgres có pgvector + pg_partman, Redis, cổng 8000/5175/9911 trống).

Cũng trong v0.1.35: `live-phase2` gõ PIN khoan dung sau 'Thêm & kiểm tra' (bước 4) và ở bước 5, và bấm 'Để sau' cho
bước 8–11 (bước 8 nay là form tạo agent thật, 'Tiếp tục' khoá tới khi điền đủ — trước đây làm live-phase2 đỏ).
Job `web` thêm bước 'Cấm ID giả viết cứng trong apps/web/src (F-1)' (`.github/scripts/check_no_fake_ids.py`).
