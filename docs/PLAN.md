# Gen-Harness — Kế hoạch thi công

**Tài liệu:** PLAN.md · **Phiên bản:** 1.0 · **Trạng thái:** ĐÃ DUYỆT 23/09/2026 kèm quyết định Q1–Q7 (mục Quyết định).
Kiến trúc: `docs/ARCHITECTURE.md`. Giao diện: `docs/design/`. Đặc tả kỹ thuật đi kèm thiết kế: `docs/handoff/`.

> **Nguồn hiện hành (10/2026).** Đây là kế hoạch thi công gốc theo giai đoạn (đã xong). Tiến độ thật và việc còn lại: [ROADMAP.md](ROADMAP.md) (mục Nợ);
> từng bản đã phát hành: [CHANGELOG.md](../CHANGELOG.md); thiết kế sau giai đoạn 6: [gen-v1.md](design/gen-v1.md), [gen-hub-link.md](design/gen-hub-link.md),
> [gen-browser-agent.md](design/gen-browser-agent.md). Mâu thuẫn thì các tài liệu đó thắng.

---

## 0. Cách làm việc

- Làm tuần tự theo giai đoạn. Mỗi giai đoạn một nhánh + một PR, commit nhỏ theo hạng mục, push khi xong.
- **Báo cáo cuối giai đoạn**: đã làm · còn lại · chỗ lệch spec/thiết kế và lý do · cách tự kiểm tra (lệnh, tài khoản thử, màn cần xem).
- Spec và thiết kế mâu thuẫn hoặc thiếu → hỏi, không đoán; làm tiếp hạng mục khác trong lúc chờ.
- CI (GitHub Actions) mỗi push: lint, typecheck, test api, test web, build image, Playwright.

### Định nghĩa "xong" cho mỗi màn (theo yêu cầu dự án + `handoff/07-acceptance.md`)
1. Đặt cạnh thiết kế ở **1440px** và **1280px**: bố cục, màu, chữ, khoảng cách, icon, văn bản khớp, chênh ≤ 2px. Kiểm bằng Playwright chụp màn hình thiết kế và màn thật cùng dữ liệu seed, so ảnh, cộng soát mắt.
2. Đọc/ghi qua API thật; với seed, hiện đúng dữ liệu như thiết kế. Không hardcode.
3. Có trạng thái **trống**, **đang tải** (skeleton cùng khung), **lỗi** (thông báo + thử lại).
4. Quyền kiểm ở backend: đăng nhập từng vai trò, màn/tác vụ bị ẩn **và** API trả 403 (ngoài phạm vi dữ liệu trả 404).
5. Bộ lọc, tab, mục mở rộng phản ánh vào URL; tải lại giữ trạng thái.
6. Tương phản chữ ≥ 4.5:1; không dùng `neutral-700` làm màu chữ; đi hết bằng bàn phím, focus ring accent.
7. Test: unit cho logic nghiệp vụ, API test cho endpoint, Playwright cho luồng chính.

### Quy mô
Mỗi hạng mục ghi quy mô tương đối **S / M / L / XL** thay cho số giờ (không ước được giờ đáng tin khi chưa có code). Tổng mỗi giai đoạn ghi ở tiêu đề.

---

## Giai đoạn 1 — Nền (XL)

Mục tiêu: `docker compose up` dựng toàn bộ; Owner đầu tiên tạo qua trình thiết lập; đăng nhập, PIN, RBAC đúng ma trận; plugin bật/tắt nóng; mọi hành động có log; khung Console đúng thiết kế.

| # | Hạng mục | Quy mô | Kiểm được khi |
|---|---|---|---|
| 1.1 | Monorepo theo ARCHITECTURE §3; `deploy/compose.yaml` 8 dịch vụ + healthcheck; `.env.example`; Makefile; CI | M | `docker compose up` → mọi dịch vụ healthy, chỉ cổng 8443 mở |
| 1.2 | Đưa lược đồ khởi điểm (`schema.sql` gốc của gói bàn giao, đã bỏ ở v0.1.48) vào Alembic (tách theo schema), extension, pg_partman, trigger chỉ-INSERT, `core.uuid_v7`, `core.next_code`, `core.lookup` seed | L | Migration lên/xuống sạch trên DB trống; `UPDATE raw.events` lỗi |
| 1.3 | Auth: đăng nhập, phiên cookie băm, đăng xuất, thu hồi, CSRF, Idempotency-Key (chưa thi hành — server không đọc header này), TOTP tuỳ chọn *(chưa làm — ROADMAP mục Nợ)* | M | Test sai mật khẩu, phiên hết hạn/thu hồi, CSRF thiếu |
| 1.4 | PIN: đặt/đổi, phiên PIN 30 phút, `423 PIN_REQUIRED`, khoá 15 phút sau 5 lần sai, danh mục thao tác cần PIN ở một chỗ | M | Test đủ nhánh; mọi lần nhập vào log |
| 1.5 | RBAC: 5 vai trò, ma trận 7 cột × 5 hàng của thiết kế, `require()` + `ScopeFilter` ở tầng service, `core.assignments` | L | Test ma trận: mỗi endpoint × mỗi vai trò × trong/ngoài phạm vi |
| 1.6 | Event bus Redis Streams: publish/consume, consumer group, ack, retry, dead-letter; WebSocket `/ws` + pub/sub | M | Consumer chết giữa chừng → sự kiện không mất |
| 1.7 | Plugin Manager: manifest, `load_order`, phụ thuộc, bật/tắt nóng, plugin nền không gỡ (409), circuit breaker, `ops.breaker_events`, sandbox tiến trình con cho plugin cài thêm | L | Plugin lỗi liên tục → isolated, plugin khác chạy; nửa mở → đóng lại |
| 1.8 | Action Log: service ghi duy nhất, chuỗi băm, middleware, job kiểm chuỗi | M | Sửa tay một dòng (bằng superuser) → kiểm chuỗi báo đứt; test quét route không đường ghi nào thiếu log |
| 1.9 | Policy lõi: registry loại hành động (cờ), mức tự trị lấy min theo lớp, luật cứng, `biz.action_drafts`, permit HMAC một lần | M | Bảng chân trị: mọi tổ hợp cờ × mức 0–6 |
| 1.10 | Web: `packages/tokens` từ `tokens.json`, Inter tự host, Phosphor; shell sidebar 244/60px + header 58px; danh mục hai miền sinh từ `screens.json` (lọc quyền, badge, tự bung nhóm chứa màn đang mở); PinDialog; EmptyState/ErrorState/Skeleton; client TS sinh từ OpenAPI | L | So ảnh shell 1440/1280; điều hướng theo từng vai trò |
| 1.11 | Trình thiết lập `/setup`: khung 12 bước, lưu `ops.setup_state`, tiếp tục khi tải lại; làm thật **bước 1–3** (mã thiết lập, tài khoản Owner + PIN, tổ chức & xưng hô) | M | Tải lại giữa chừng vẫn đúng bước; không vào lại `/setup` sau khi xong |

---

## Giai đoạn 2 — Dữ liệu (XL)

Mục tiêu: tin thật từ Zalo/WhatsApp chảy một chiều Bridge → Kho thô → Sàng lọc → Kho sạch; danh tính hợp nhất; 4 màn Tầng dữ liệu chạy thật.

| # | Hạng mục | Quy mô | Kiểm được khi |
|---|---|---|---|
| 2.1 | Bridge Node/TS: adapter Zalo (`zca-js`) + WhatsApp (Baileys) viết lại từ logic repo cũ; phiên QR → WebSocket; envelope; heartbeat; đồng bộ nhóm; gửi chỉ khi permit hợp lệ; cảnh báo rủi ro tài khoản cá nhân trước khi hiện QR | L | Đăng nhập QR thật; test envelope bằng dữ liệu ghi lại từ thư viện; permit sai → không gửi |
| 2.2 | Ingest: stream → `raw.events` + `refinery.event_state` + NOTIFY trong một transaction; khử trùng; bỏ tin nhóm chưa bật | M | Tin trùng, tin nhóm chưa bật; ≤ 2 giây tới màn Kho thô |
| 2.3 | Provider lõi + xoay vòng khoá + chuỗi chuyển hướng + hạn mức theo model (màn cấu hình ở GĐ 4) | M | 429 → đổi khoá; hết chuỗi → xếp hàng + báo; còn < 20% → cảnh báo |
| 2.4 | Refinery: chu kỳ **HOẶC** ngưỡng (cái nào tới trước), `SKIP LOCKED`, `refinery.runs`, quy tắc có phiên bản, trích xuất LLM có kiểm chứng chứng cứ, ngưỡng tin cậy → `clean`/`lowconf`, embedding, chấm điểm theo trọng số, `superseded_by` khi chạy lại | XL | Test chỉ chu kỳ / chỉ ngưỡng / cả hai; 2 worker song song không trùng; LLM trả ID bịa → bị loại; model chết → tin giữ `pending` |
| 2.5 | Hợp nhất danh tính: đề xuất có % + cơ sở, gộp/tách có PIN, lịch sử, đảo ngược | M | Gộp → tách trả đúng trạng thái; hồ sơ + sổ tay gộp theo |
| 2.6 | Sổ tay nhận thức (lõi): ghi lũy tiến từ refinery, nén theo 90% ngân sách hoặc 24 giờ, không nén mục ghim | M | Test nén, ghim, lịch sử nén |
| 2.7 | Màn **Kho dữ liệu thô** (LIVE), **Quy tắc sàng lọc** (phiên bản, trọng số 100%, thử trên một tin), **Kho sạch SSOT** (+ trí nhớ tạm, tham số agent), **Hợp nhất danh tính** | L | Định nghĩa "xong" |
| 2.8 | Điều khiển hệ thống › tab **Kênh & đăng nhập** (thẻ kênh, QR, PIN) | M | Định nghĩa "xong" |
| 2.9 | Trình thiết lập **bước 4–7** (bộ não AI, kết nối kênh, chọn nhóm lắng nghe, sàng lọc) và **bước 12** (tiến độ lần sàng lọc đầu tiên realtime) | M | Luồng thiết lập chạy tới sàng lọc đầu tiên |

---

## Giai đoạn 3 — Kinh doanh (XL)

Nền chung trước: `explain` (điểm → đơn vị ý nghĩa → trích dẫn → bản ghi thô), hàng đợi hợp nhất (`biz.inbox_items`), `biz.alerts` + cảnh báo sớm spec E9, agent trực kênh (§5 kiến trúc) + `agent.decisions`, lớp `analytics` MV dùng cho các màn, góc nhìn đã lưu.

| # | Màn (khoá) | Quy mô | Logic nghiệp vụ cần test |
|---|---|---|---|
| 3.1 | Tổng quan điều hành (`overview`) + hàng KPI F4 bổ sung | L | Chỉ số F4 tính đúng; mỗi ô dẫn tới danh sách đã lọc |
| 3.2 | Hộp thư ý nghĩa (`inbox`) | M | Ưu tiên, tab đếm đúng, giao người khác, im lặng có chủ đích, phạm vi theo vai trò |
| 3.3 | Bàn làm việc (`workbench`) | L | Luật cứng; duyệt → permit → gửi thật → log; sửa rồi gửi; huỷ; PIN khi hết phiên |
| 3.4 | Nhóm & Con người (`directory`) | M | 5 hàng bộ lọc; Thiết lập BOT + tự trị riêng từng người |
| 3.5 | Bản đồ quan hệ (`graph`): danh sách, Người↔Người, Nhóm↔Nhóm, Luồng chủ đề; đồ thị tương tác (d3-force/elkjs, ≤ 200 node, lưu vị trí) | XL | Trọng số cạnh, cầu nối, tải quan hệ, lạnh > 30 ngày |
| 3.6 | Hồ sơ sống (`profile`) | M | Danh tính đa kênh, 5 điểm + vì sao, mức tự trị 0–6, ghi chú tay hệ thống không sửa |
| 3.7 | Sổ tay nhận thức (`notebook`) | M | Owner ghim/sửa/xoá/nén ngay/đặt lại; lịch sử nén; đã nén vẫn truy được |
| 3.8 | Bảng cơ hội (`opportunity`) | M | Kéo thả ghi `opportunity_stage_history`; tổng pipeline |
| 3.9 | Cung ↔ Cầu (`supply`) | M | Chấm điểm ghép có lý do; Giới thiệu hai bên tạo bản nháp chờ duyệt |
| 3.10 | Kho hội thoại (`search`) | L | Tìm ngôn ngữ tự nhiên + facet + ngữ nghĩa; kết quả là người; hành động hàng loạt qua duyệt |
| 3.11 | Đánh giá con người (`people`) + Phản biện (spec I) | M | Khoá mức Owner; xem vào log; sửa điểm tay giữ lịch sử; không hành động kỷ luật tự động |
| 3.12 | Chất lượng chăm sóc (`care`) | M | Lưới phản hồi theo khung giờ (<15 / 15–60 / >60 phút), lỗi chăm sóc lặp, kịch bản thắng/mất |
| 3.13 | Trình thiết lập **bước 8–9** (agent đầu tiên + thử trò chuyện; tự trị & ranh giới) | S | |
| 3.14 | Việc & Nhắc hẹn (`tasks`), Tài liệu (`documents`), Deal & Vụ việc (`deals`) — spec G1, dựng theo `handoff/01` | L | Hạn quá đỏ, lời hứa sắp đến hạn; ACL tài liệu; trạng thái deal/vụ việc và người xử lý |

---

## Giai đoạn 4 — Kỹ thuật (L)

| # | Màn (khoá) | Quy mô | Logic nghiệp vụ cần test |
|---|---|---|---|
| 4.1 | Danh tính Agent (`agents`) | M | Tạo/sửa/nhân bản/tắt; mẫu có sẵn, không mặc định bắt buộc; "đã nói gì, nhân danh gì" |
| 4.2 | API & Model (`api`) | M | Khoá chỉ lộ `last4`, hiện khoá cần PIN; kiểm tra kết nối; gán model theo agent; tham số core; giới hạn tốc độ |
| 4.3 | MCP Hub (`mcp`) | L | Tool mới đóng mặc định; chỉ Owner mở; tool ghi → bản nháp; tool chưa mở → chặn + log; chặn mạng công cộng mặc định |
| 4.4 | Plugin & Tiện ích (`plugins`) | M | Nạp từ tệp (PIN + quyền xin + chữ ký); plugin nền không gỡ; thứ tự nạp; sự kiện LIVE; reset breaker |
| 4.5 | Điều khiển hệ thống (`system`): Bộ não AI (hạn mức theo model, chuỗi kéo thả, quy tắc chuyển hướng, tài khoản Antigravity CLI), Quyền hạn (ma trận, nhóm lắng nghe, ranh giới), Nhật ký (tìm, xuất CSV), tab bổ sung Dữ liệu & lưu trữ (spec I) | L | Đổi quyền cần PIN; ranh giới khoá không tắt được; xuất/xoá dữ liệu một người ghi log |
| 4.6 | Trình thiết lập **bước 10–11** (mời đội ngũ, sao lưu) | S | |

---

## Giai đoạn 5 — Hoàn thiện (L)

| # | Hạng mục | Quy mô | Kiểm được khi |
|---|---|---|---|
| 5.1 | Seed từ `design/seed-data.json` (115 tập) **đi qua đúng luồng** raw → refinery → clean để chứng cứ drill-down được; lệnh xoá dữ liệu mẫu | L | 21 màn với seed khớp thiết kế |
| 5.2 | So ảnh 21 màn ở 1440/1280, báo cáo đính kèm PR | M | Chênh ≤ 2px |
| 5.3 | E2E luồng 2–8 của `handoff/07` (thiết lập → QR → tin thật → sàng lọc → cơ hội → agent soạn → duyệt → gửi → log; hợp nhất; plugin lỗi; MCP) | L | Xanh trong CI |
| 5.4 | Chịu lỗi: tắt Redis / Postgres / bridge / provider giữa chừng | M | Không sập, không mất dữ liệu (spec M7) |
| 5.5 | Row-Level Security; benchmark 10 triệu `raw.events` (Tổng quan < 150ms p95); refinery ≥ 500 bản ghi/phút; RAM rảnh < 2 GB | M | Số đo trong báo cáo |
| 5.6 | Backup/restore trong container (`pg_dump` + MinIO, mã hoá, vòng 7 ngày/4 tuần/12 tháng) | M | Backup → xoá → restore nguyên vẹn |
| 5.7 | README: cài bằng `docker compose up`, khởi tạo, đăng nhập kênh, rủi ro Zalo cá nhân, sao lưu | S | Người mới dựng được từ README |

---

## Giai đoạn 6 — Trình cài một lệnh (L) — theo `handoff/05-installer.md`

`install.sh` / `install.ps1` + binary Go `genh` (Bubble Tea): kiểm tra máy, tự cung cấp container runtime (Docker rootless / Colima / WSL2), tải image có % theo byte thật, sinh bí mật, migrate, health, mở `/setup?token=`; lệnh `status, open, logs, update, backup, restore, doctor, reset-setup, stop, start, uninstall`; phát hành GitHub Releases + GHCR đa kiến trúc + checksums + cosign; ma trận CI Ubuntu/Debian/Fedora/macOS/Windows.

---

## Quyết định của chủ dự án (23/09/2026)

| # | Chủ đề | Quyết định |
|---|---|---|
| Q1 | Giai đoạn | 6 giai đoạn như trên: trình thiết lập Owner làm dần qua GĐ 1–4, trình cài `genh` là GĐ 6. |
| Q2 | Tự trị 5–6 và gửi tin | Mọi tin/hành động ghi ra ngoài đều dừng ở Bàn làm việc chờ duyệt, ở mọi mức. Mức 5–6 chỉ tự làm việc nội bộ (tạo việc, nhắc, ghi chú, gắn nhãn, ghi sổ tay). |
| Q3 | Tin tag agent | Có đường nhanh: tin tag agent và tin 1-1 được sàng lọc ngay khi tới, cùng quy tắc, vẫn một chiều Kho thô → Kho sạch. |
| Q4 | Đánh giá nhân sự | Mặc định chỉ Owner thấy nội dung; Auditor thấy nhật ký ai đã xem; Manager không thấy. Owner có thể tự cấp thêm cho vai trò khác trong Quyền hạn (cần PIN, ghi log). |
| Q5 | 3 màn còn thiếu | Dựng luôn trong GĐ 3: **Việc & Nhắc hẹn** (Hàng đợi & Hành động), **Tài liệu** (cạnh Nhóm & Con người), **Deal & Vụ việc** (Cơ hội & Thị trường), cùng ngôn ngữ thiết kế. |
| Q6 | Antigravity CLI | Dùng bản cài chính hãng; đăng nhập và đổi tài khoản theo cách dự án cũ làm (xem ARCHITECTURE §11). |
| Q7 | Zalo | Owner đăng nhập bằng cách quét QR do hệ thống sinh, bằng tài khoản Zalo thật; bridge giữ phiên và bắt tin như dự án cũ (`zca-js`). Vẫn hiện cảnh báo rủi ro trước QR. |
| — | Mặc định giới hạn | **Mặc định mở hết, trừ các quyền nguy hiểm nghiêm trọng.** Mọi giới hạn còn lại là tuỳ chọn Owner tự bật/tắt trong cài đặt. Danh sách khoá cứng ở ARCHITECTURE §7.4. |

## Định nghĩa "có giới hạn" trong ma trận quyền

Ma trận của thiết kế là giá trị khởi tạo; Owner sửa được từng ô trong Điều khiển hệ thống › Quyền hạn (PIN + log). Nghĩa của ô "–":

| Cột | Vai trò có "–" | Nghĩa |
|---|---|---|
| Tổng quan | Operator | Khối Hàng đợi và KPI thuộc hàng đợi được giao |
| Hàng đợi | Agent nhân viên, Auditor | Agent: item về khách được phân. Auditor: xem, không hành động |
| Hồ sơ khách | Agent nhân viên, Auditor | Agent: khách được phân. Auditor: xem |
| Đánh giá nhân sự | Manager, Auditor | Theo Q4: Manager không thấy, Auditor chỉ thấy nhật ký truy cập, cho tới khi Owner cấp thêm |
| Cơ hội | Agent nhân viên, Auditor | Agent: cơ hội của khách được phân. Auditor: xem |
| Hành động | Operator, Agent nhân viên | Soạn nháp và làm việc tự trị cho phép trong phạm vi mình; không duyệt bản nháp đang bị giữ (Owner và Manager trong team duyệt) |
| Nhật ký | Manager | Nhật ký của người và agent thuộc team mình |

---

## Rủi ro chính

| Rủi ro | Giảm thiểu |
|---|---|
| Thư viện Zalo không chính thức đổi hoặc bị chặn | Bridge tách riêng, adapter mỏng, breaker; kho thô giữ dữ liệu đã có |
| LLM bịa chứng cứ | Kiểm ID chứng cứ bắt buộc; điểm không chứng cứ bị từ chối |
| Tải sàng lọc lớn | Lô `SKIP LOCKED`, worker nhân ngang, hạn mức model, phân vùng tháng |
| Pixel-perfect 21 màn tốn công | Component `packages/ui` dùng chung, so ảnh tự động trong CI từ GĐ 1 |
| Trình cài đa nền tảng (tự cài Docker/WSL) | Để GĐ 6 khi hệ thống đã ổn; ma trận CI thật |
