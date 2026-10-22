# Bàn giao Gen-Harness — hiện trạng + việc dở (cập nhật 19/10/2026, bản v0.1.74 — bản tính năng trên v0.1.73, rồi v0.1.73 — bản tính năng trên v0.1.72, rồi v0.1.72 trên v0.1.71 (bản vá trên v0.1.70), gộp v0.1.69 + v0.1.70, bản gộp v0.1.68, bản vá v0.1.66 và bản gộp v0.1.65)

File này nay chỉ ghi **hiện trạng + việc dở** (tên cũ `HANDOFF-v0.1.1.md` giữ nguyên vì mã nguồn có chú thích trỏ tới).
Lịch sử từng bản nằm ở [CHANGELOG.md](../../CHANGELOG.md) (3–5 dòng/bản) và `docs/releases/vX.Y.Z.md` (chi tiết, chuyển nguyên từ HANDOFF cũ):
mục "Lỗi cần sửa", "Chia việc", "Kiểm tra tích hợp" của v0.1.1/v0.1.2 nay ở [v0.1.1.md](../releases/v0.1.1.md) và [v0.1.2.md](../releases/v0.1.2.md);
chú thích trong mã trỏ "HANDOFF mục vX.Y.Z" thì đọc tệp `docs/releases/vX.Y.Z.md` tương ứng.
Tiến độ, bản phản ứng (hotfix) và **mục Nợ**: [ROADMAP](../ROADMAP.md). Vận hành hằng ngày: [runbook](../runbook.md).
Ghi chú vận hành riêng của Sếp (máy, tài khoản, địa chỉ, việc riêng) nằm ở repo Brain › Project/gen-harness (riêng tư) — không ghi vào repo công khai này.

## Hợp đồng chung

Còn hiệu lực từ v0.1.1 (vai trò DB, volume, gói hồ sơ); chi tiết đầy đủ ở [v0.1.1.md](../releases/v0.1.1.md).

- **Vai trò DB**: `api`/`worker` kết nối bằng vai trò `gh_app` (không superuser, không BYPASSRLS) qua `GH_DATABASE_URL`, mật khẩu `GH_APP_DB_PASSWORD`.
  `GH_ADMIN_DATABASE_URL` → superuser `gh` (migrate, backup, bundle, bảo trì phân vùng); rỗng ⇒ dùng `GH_DATABASE_URL` (`Settings.admin_database_url`).
  Migration 0014 tạo/ALTER `gh_app` với mật khẩu lấy từ env lúc migrate. `migrate` là dịch vụ duy nhất chạy bằng superuser.
- **Tệp**: `GH_OBJECTS_DIR=/var/lib/gh/objects` (volume `gh_objects`, gắn vào api + worker; tài liệu + sao lưu). Không còn MinIO/S3.
- **Postgres tuning**: `GH_PG_SHARED_BUFFERS`, `GH_PG_EFFECTIVE_CACHE_SIZE`, `GH_PG_WORK_MEM`, `GH_PG_MAINTENANCE_WORK_MEM` (genh tính từ RAM, ghi overlay env).
- **Khoá**: `gh_master_key` (mã hoá bí mật ứng dụng), `gh_bridge_key`, `gh_browser_key` (genh sinh); `GH_BACKUP_KEY` riêng cho sao lưu (backup cũ vẫn đọc bằng khoá master).
  Khoá chỉ nằm trên máy này ⇒ phải có `genh export` hoặc `genh offsite` ra ổ khác (README › Dành cho Boss).
- **Gói hồ sơ `.ghbundle`**: Python tạo/đọc (`python -m gh.bundle export|import`), Go chỉ chuyển bytes; mật khẩu qua `GH_BUNDLE_PASSWORD` (≥ 12 ký tự);
  mã thoát `0` ok · `2` sai mật khẩu/gói hỏng · `3` không tương thích · `1` lỗi khác; mã hoá lại mọi bí mật bằng khoá master máy đích.

## Hiện trạng (v0.1.68)

Bản mới nhất **v0.1.74** (19/10/2026, `VERSION` = v0.1.74, không migration): **bot quản trị Zalo/WhatsApp dùng chung bộ não Gen** (`gh/owner_channel/gen_chat.py` → `engine.run_channel_turn`: trí nhớ hội thoại, công cụ đọc theo quyền Owner qua phiên `chat_gen` ngắn hạn, Agent ngoại giao, câu tự nhiên → đề xuất → OK), tool `group.list/person.list/group.recent`, router bỏ chữ rỗng + envelope trích JSON/văn xuôi ([v0.1.74.md](../releases/v0.1.74.md)). Trước đó **v0.1.73** (19/10/2026, `VERSION` = v0.1.73, không migration, head vẫn 0042): **Xác nhận trong chat** ở kênh quản trị Zalo/WhatsApp (OK thay Console + PIN cho việc nội bộ; tin gửi ngoài OK riêng có mã; phiên «chat_ok» ngắn hạn gọi đúng endpoint Console), **Agent ngoại giao** (Gen chỉ ở Console, kênh ngoài xưng bằng agent Sếp chọn), bridge «đã xem»/«đang nhập» chỉ ở kênh quản trị 1-1, sửa sổ `ops.auto_sends` kẹt «issued»; Telegram giữ cách cũ; Nợ #67–#70 ([v0.1.73.md](../releases/v0.1.73.md)). Trước đó **v0.1.72** (18/10/2026, `VERSION` = v0.1.72) là **bản tính năng «Bot quản trị 1-1»** nằm TRÊN v0.1.71, migration 0042 (head = 0042): với tài khoản Zalo/WhatsApp RIÊNG của bot, bot tự gửi trong đúng ba trường hợp qua một cửa `send_auto` (ngoại lệ hẹp của luật cứng, Sếp duyệt 18/10) ([v0.1.72.md](../releases/v0.1.72.md)). Trước đó **v0.1.71** (18/10/2026, `VERSION` = v0.1.71) là **bản vá nhỏ** sửa 15 lỗi tìm thấy khi giả lập toàn luồng trên v0.1.70, không migration (head vẫn 0041); THAY ĐỔI QUYỀN: «Quyền xem» của nhóm nay thật sự ẩn nội dung nhóm «Chỉ Sếp» với vai khác ([v0.1.71.md](../releases/v0.1.71.md)). Trước đó **v0.1.70** (18/10/2026) là **bản gộp hai bản** theo quyết định Sếp (một lần `genh update`; không có bản v0.1.69 riêng), nằm TRÊN bản gộp v0.1.68: **v0.1.69** chuông có vòng đời (sự cố đóng thì chuông «Đã xử lý», chưa đặt lịch sao lưu là sự cố; migration 0041) + mặc định đúng quyết định của Sếp (Gen yên lặng 21–07, «Kho dữ liệu», lưới L6 `test_quyet_dinh_sep.py`) + vai Quản lý «Việc được giao» + một bảng nhãn sự kiện ([v0.1.69.md](../releases/v0.1.69.md)) · **v0.1.70** câu chữ sạch (`check_terms` 46 → 6), Telegram Tạm dừng + không link `localhost`, Gen gộp việc thiết lập tuỳ chọn, một đích «Thêm nguồn AI», thêm / sửa / tắt-bật / gỡ model cần PIN `ai.route_change` ([v0.1.70.md](../releases/v0.1.70.md)). **Kế hoạch sửa sau kiểm kê 16/10 (37 gói) hoàn tất.** Chưa push — chờ mốc.
Trước đó **v0.1.68** (17/10/2026, bản gộp v0.1.67 + v0.1.68): model hiển thị = model chạy, agent làm và nói thật, một danh sách việc, màn **Nhật ký lắng nghe** (migration 0039, 0040; [v0.1.68.md](../releases/v0.1.68.md)). Trước đó **v0.1.66** (18/10/2026, bản vá phát hành của v0.1.65): chỉ sửa bước kiểm E2E nâng cấp F-22 lỗi thời trong `e2e-install.yml` + test, mã sản phẩm không đổi ([v0.1.66.md](../releases/v0.1.66.md)).
Trước đó **v0.1.65** (17/10/2026, bản gộp v0.1.63 + v0.1.64 + v0.1.65): hộp riêng Antigravity `agy-box` + proxy `agy-egress`, Telegram 2 chiều + sàng lọc ghi loại người, chế độ nghe & danh bạ (nhóm mới mặc định Lắng nghe im lặng; tin 1-1 chỉ Owner/người phụ trách; migration 0037, 0038; [v0.1.65.md](../releases/v0.1.65.md)); không phát hành riêng v0.1.63/v0.1.64.
Trước đó **v0.1.61** (16/10/2026, đã vào main): đợt A phần 1 — mã PIN cho người được giao quyền, bảng quyền/tên vai trò (migration 0036), nháp & gửi tin trung thực, gộp người đủ bảng, lưới `check_terms.py` + route-crawl ([v0.1.61.md](../releases/v0.1.61.md)).
Các bản cũ (v0.1.50 – v0.1.60: Gen nhớ + ghi Kho, tự cập nhật đêm tự lành, Gen hướng dẫn, Mặt tiền Owner v2, thiết lập một màn, sửa QR WhatsApp, khoá thông tin riêng…) xem [CHANGELOG.md](../../CHANGELOG.md) và `docs/releases/`.
Đang chạy, theo nhóm:

- **Cài đặt & vận hành (genh)**: cài một lệnh; `genh update` an toàn (tải trước, sao lưu, migrate, tự quay về bản cũ khi lỗi, khoá `genh.lock`, báo "bị dừng giữa chừng");
  lịch đêm ~03:00 **tự lành** (bật lại khi mất/tắt/không chạy, trừ khi Sếp đã chủ động tắt), **người gác nút Cập nhật ngay tự chữa** (v0.1.54: `.path` failed vì hết hạn mức inotify ⇒ reset-failed + restart, vẫn lỗi ⇒ timer dự phòng quét mỗi phút), `genh auto-update status` nói thật, chọn bản cao nhất đã đủ 24 giờ; `genh export/import`, `genh offsite` (bản sao tuần ra USB/NAS), `genh remote` (Tailscale/Cloudflare/LAN/local),
  trực canh 12 phút → Telegram, gói chẩn đoán. Cổng mặc định chỉ nghe 127.0.0.1 (máy cũ giữ 0.0.0.0 + một chuông nhắc).
- **Phát hành & CI**: xem "Quy trình phát hành" dưới; ảnh ghim digest, `uv.lock --frozen`, action ghim SHA, quét bảo mật dạng báo cáo, `check_*` trong `.github/scripts`. Môi trường dev riêng `genh-dev` (cổng 184xx, `make dev-up`; [dev-local.md](../dev-local.md)) — không đụng bản cài thật.
- **Console**: 13 dịch vụ compose (`proxy web migrate api worker agy-box agy-egress bridge browser browser-redis browser-egress db redis`); menu 6 mục + Nâng cao (Tầng dữ liệu có màn **Nhật ký lắng nghe**, v0.1.68); trình thiết lập; Hướng dẫn thiết lập;
  **Mặt tiền Owner** `/owner/*` (v0.1.60: 5 trụ cột Tình báo/Thông tin/Quan hệ/Phân tích/Việc + Cài đặt 4 khối; Owner mở `/` vào đây; Console ở Cài đặt › Cài đặt nâng cao); thiết lập một màn `/setup` (Tài khoản → Nguồn AI → Xong, mọi "Chọn model" về `/setup?buoc=nguon-ai`); Việc Sếp cần làm (10 dòng, 1 bắt buộc = nguồn AI, kết quả ở `ops.boss_checks`); chuông + dải "Cần Sếp xử lý"; Trợ giúp (gói chẩn đoán, cập nhật, **Lộ trình học cùng Gen**); phiên 7 ngày trượt, tối đa 30 ngày; PIN ở đường hạ rào.
- **Gen (chỉ Owner)**: khung chat nhớ hội thoại; Bản tin 07:30/17:30; dẫn đường trên UI; **8 loại đề xuất** có Xác nhận (draft_message, reminder, assign, social_reply, social_dm,
  **memory_note, kho_create, kho_update**); **Gen nhớ** (30 ghi chú, Cài đặt › Bộ não AI); đọc Kho + lịch/mail/việc/Drive qua Gen-hub (đã che) và Tài liệu/Deal/Vụ việc nội bộ;
  **ghi Kho** (Phiên, Việc) chỉ qua đề xuất + mã PIN + permit; mỗi bản mới Gen đề xuất một Phiên (F-87);
  **Gen hướng dẫn** (v0.1.54): thẻ Hôm nay của Sếp (≤ 3 việc cần làm ngay, mẹo, bài k/19), chấm đỏ, chuông `gen.coach` ≤ 1/ngày, dòng Bản tin x/N, tool `coach.status` — không gọi model, chỉ Owner, khuyên không ép.
- **Model AI**: **hồ sơ tiêu chuẩn theo vai** (v0.1.55: không cần gán model; dòng gán của Owner thắng; Về mặc định), chọn Nhanh/Kỹ hơn trong khung chat; ModelRouter (khoá API Gemini/OpenRouter/OpenAI-compat), agy mỗi vai một chỗ chạy (v0.1.63: Gen của Sếp, Bản tin, agent không gắn kênh chạy trong tiến trình; `core.reply`, sàng lọc, agent ngoại giao, Gen của nhân viên, thử agent, Soạn lại / Dịch nháp chạy qua `agy-box`, hộp tắt ⇒ nguồn kế; Thử quy tắc không dùng agy; một hàm quyết định `agy_policy.decide`, luật ở `providers.placement`), Claude Code CLI (Owner tự quyết rủi ro, QD-12); model và mức suy nghĩ tách riêng;
  việc nền mặc định chỉ khoá API; chi phí ₫/ngày + trần; Jev tuỳ chọn: lọc trước tin trùng/rác (che dữ liệu bắt buộc, v0.1.55; QD-10 bỏ Jules).
- **Kênh**: Zalo/WhatsApp qua bridge (v0.1.62 bền: mất mạng không xoá phiên, nút Nối lại bằng phiên đã lưu, Đăng xuất khi phiên lỗi; QR; WhatsApp v0.1.59: mã vẽ lớn, Baileys ghim 7.0.0-rc14, thử lại có giãn cách); Telegram hai chiều với Sếp (v0.1.64: ghép mã 6 số, lệnh chỉ đọc/đề xuất); Facebook cá nhân đọc + Trả lời bình luận/Nhắn tin có Xác nhận + PIN + ảnh chụp (đăng bài = lát 2).
- **Dữ liệu & bảo mật**: Postgres 16 (pgvector, pg_partman), RLS chỉ là phòng thủ phụ (mỗi bản cài 1 tổ chức), hạn lưu thật, bí mật mã hoá phong bì, DNS ghim cho MCP/Gen-hub, ngắt mạch Gen-hub 60 giây.

## v0.1.72 — Bot quản trị 1-1 (nhánh `local/v0172-bot`, gốc `06bea1c` = v0.1.71)

- **Làm gì**: tài khoản riêng của bot (`account_kind`), kênh quản trị 1-1 ghép bằng mã 6 số, Sếp tag bot ở nhóm ⇒ trả lời tại chỗ (`core.owner_reply`), Chế độ hỗ trợ nhóm (`core.support`), sổ `ops.auto_sends` (không lưu nội dung). Mọi tin tự gửi đi qua MỘT cửa `gh/owner_channel/autosend.py::send_auto`; test AST chứng minh không có đường thứ tư.
- **Trạng thái**: gói G1–G9 xong và đã kiểm trên máy cục bộ (số liệu ở mục «Kiểm cuối» của [v0.1.72.md](../releases/v0.1.72.md)). **Chưa push**, chưa kiểm bằng tài khoản Zalo/WhatsApp thật (Nợ #64), chưa nghiệm thu giao diện bản thật 390/1440 (Nợ #66), chưa có lệnh «rời nhóm» (Nợ #65).
- **Boss cần làm**: tuỳ chọn — xem việc 5c ở «Sếp cần làm bây giờ». Chi tiết: [v0.1.72.md](../releases/v0.1.72.md).

## v0.1.71 — bản vá (nhánh `local/v0171-sua`, gốc `rel/v0170`)

- **Vì sao**: giả lập toàn luồng trên v0.1.70 tìm ra 15 lỗi (3 P2 + 12 P3), cả 15 đều đúng khi kiểm lại trên mã.
- **Thay đổi**: `gh/biz/core/scope.py` («Quyền xem» của nhóm, một chỗ), `explain.py` (chứng cứ `raw`), `chassis/masking.py` (`mask_for_prompt`, `mask_context_secrets`), `realtime.py`, `providers/router.py` (chuông P1), `owner/feed.py`, `biz/people/*`, `biz/market/*`, `data/ingest.py`, `refinery/runner.py`; web `lib/features.ts` (cờ `PROMISES_TRACKED`), `UpdateNotice.tsx`. Không migration.
- **Chưa làm**: Nợ #62 (bộ ghi lời hứa), #63 (phần còn hở của «Quyền xem»).
- **Boss cần làm**: không bắt buộc; xem việc 5b ở «Sếp cần làm bây giờ». Chi tiết: [v0.1.71.md](../releases/v0.1.71.md).

## v0.1.70 — bản gộp (nhánh `rel/v0170`, xếp trên `rel/v0169` ← `rel/v0168`)

- **Vì sao**: Sếp gộp hai bản; hệ thống không được nói một đằng làm một nẻo (chuông, mặc định trôi khỏi quyết định, vai Quản lý bị hứa «team») và câu chữ phải một tên một nghĩa, không lộ tên kỹ thuật.
- **Thay đổi**: `health.py` + `notifications.py` + migration 0041 + `NotificationBell.tsx` (chuông vòng đời, C4); `gen/coach/engine.py`, lưới `test_quyet_dinh_sep.py` (D3); `rbac.py`, `usersModel.ts` (D4); `eventTypes.ts` (D5); `check_terms` + thuật ngữ máy chủ/màn hệ thống (E1, E2); `telegram/service.py` + `TelegramCard.tsx` + `genh watchdog.go` (Tạm dừng, không link `localhost`); `system_api/routes.py` (model cần PIN); `gen/coach/engine.py` (việc gộp); `providers/cli.py`, `hub_link`, `telegram` (truyền Redis cho `health.clear`).
- **Chưa làm**: Nợ #52 (tính năng Nhóm), #53 (tên màn «Kho …», cần Sếp), #54 (múi giờ job nền), #55 (nhãn `chat_id`), #50, #51, #56–#61; không còn việc dở nào khác của kế hoạch 37 gói.
- **Boss cần làm**: xem «Sếp cần làm bây giờ» ở mục Việc dở. Chi tiết: [v0.1.70.md](../releases/v0.1.70.md).

## v0.1.68 — bản gộp (nhánh `rel/v0168`, xếp trên `rel/v0165` + bản vá v0.1.66 + v0.1.67; đã nằm trong v0.1.70)

- **Vì sao**: Sếp gộp hai bản; model giao diện phải bằng model chạy và agent nói và làm đúng sự thật (v0.1.67), Sếp thấy MỘT danh sách việc, thiết lập không nói sai, và có một chỗ xem mọi thứ hệ thống đã nghe (v0.1.68).
- **Thay đổi**: `providers/router.py` + `defaults/profiles.py` (một hàm chọn model), `DELETE /providers/{id}/models/{mid}`, hạn mức trên giao diện (B3, C3 + D2); `gh/agents*` + migration 0039 (Lưu trữ, `inactive_reason`, dòng MODEL), `/agents/decisions`, Soạn lại theo giọng agent, `outbound_to_sql` (B7, C5, C6); `readiness.FIX_AI`, `record_hub_result`, `setup/routes.py`, `GuideStepPage` (C2, D1, C1); `check_doc_facts.py` (B9); `gh/data_api/listen_log.py` + `/listenlog` + migration 0040.
- **Chưa làm** (nay xong ở v0.1.70, trừ Nợ): Nợ #50, #51, #56–#61.
- **Boss cần làm**: xem «Sếp cần làm bây giờ» ở mục Việc dở. Chi tiết: [v0.1.68.md](../releases/v0.1.68.md).

## v0.1.65 — bản gộp (nhánh `rel/v0165`, xếp trên `local/v0163-int` + gói sửa review P1/P2/P3)

- **Vì sao**: Sếp gộp ba bản lớn thành một lần phát hành; v0.1.63 cho việc có người ngoài chạy Antigravity trong container riêng, v0.1.64 cho Sếp nhắn Gen qua Telegram, v0.1.65 cho thu hết tình báo (mọi nhóm, thành viên, tin 1-1) mà không trả lời nhầm.
- **Thay đổi**: hộp `agy-box` (`gh/agybox`, `placement.py`, `agy_policy.decide`, router), proxy `agy-egress`, HOME tạm mỗi lượt agy, `readiness.py`; `gh/telegram/*` (ghép, `inbound`, `poller`, `commands`), B6 loại người; `settings.new_group_mode` / `direct_mode`, `core.groups.left_at`, `direct_visible_sql` (tin 1-1), gán BOT người.
- **Chưa làm**: Nợ #43 (nút Chạy lại tin lỗi); #44–#46 (allowlist egress mặc định, core vào hộp, định dạng/xoay vòng token agy); #47–#49 (quét danh bạ theo phần thay đổi, `view_scope`, tin 1-1 còn hở); đệm ở bridge cho loạt tin dồn < 1 giây trước khi tập nghe đồng bộ; chưa kiểm dịch vụ thật (WhatsApp QR #35, bot Telegram thật, bridge nhận nhóm lạ).
- **Boss cần làm**: xem "Boss phải làm — v0.1.65" dưới. Chi tiết: [v0.1.65.md](../releases/v0.1.65.md).

## Người lái và kế hoạch sau kiểm kê (v0.1.63 – v0.1.70, hoàn tất)

Claude Code chạy trên máy cục bộ là người lái DUY NHẤT (từ 16/10/2026; phiên đám mây đã dừng). Mỗi bản ≤ 4 gói không chồng tệp (QD-14), tối đa 1 migration/bản; **kế hoạch 37 gói đã hoàn tất**, Sếp gộp thành ba lần phát hành: v0.1.65 (v0.1.63–65; v0.1.66 là bản vá phát hành của nó), v0.1.68 (v0.1.67–68) và v0.1.70 (v0.1.69–70); mỗi lần kiểm đầy đủ + review + push + PR.

- **v0.1.65** (xong) bản gộp: hộp riêng Antigravity (v0.1.63) + Telegram 2 chiều và sàng lọc ghi loại người (v0.1.64) + chế độ nghe & danh bạ (v0.1.65, migration 0037, 0038).
- **v0.1.66** (xong) bản vá phát hành của v0.1.65 (sửa bước kiểm E2E nâng cấp F-22; mã sản phẩm không đổi).
- **v0.1.68** (xong, đã nằm trong v0.1.70) bản gộp: model hiển thị = model chạy + agent làm và nói thật (v0.1.67, migration 0039) + một danh sách việc, thiết lập nói thật, Nhật ký lắng nghe (v0.1.68, migration 0040).
- **v0.1.70** (xong, chờ push) bản gộp: chuông có vòng đời + mặc định đúng quyết định + vai Quản lý nói thật + nhãn sự kiện (v0.1.69, migration 0041) và câu chữ sạch + dọn sót + model cần PIN (v0.1.70).

Nhánh local (CHƯA push): **`rel/v0165`** = bản gộp v0.1.65; **`rel/v0166`**, **`rel/v0167`** là bước trung gian; **`rel/v0168`** = bản gộp v0.1.68 (đã chứa v0.1.65); **`rel/v0169`** (bước trung gian: v0.1.69 trên v0.1.68) và **`rel/v0170`** = bản gộp v0.1.70 (đã chứa v0.1.68). Mỗi lần phát hành: gộp gói → bộ kiểm đầy đủ MỘT lần → `/code-review` (+ `/security-review` khi chạm khoá, PIN, quyền, container) → thử trên dev → ảnh 390/1440.

## Quy trình phát hành & cổng

1. Nhánh làm việc → PR vào `main`; CI xanh thì tự merge squash (Boss đã cho phép), sau đó **nối lịch sử main vào nhánh bằng merge** (không reset/force-push).
2. Tăng `VERSION` ⇒ `release.yml`: CI chạy trước → Release dạng **bản thử** (prerelease) → `e2e-install` + mọi ô `e2e-upgrade` + `e2e-rollback` + `e2e-nightly-real` (v0.1.53) xanh → **promote** thành bản chính thức (latest);
   lịch đêm đợi thêm 24 giờ. Chi tiết: `docs/handoff/05-installer.md` mục "Cổng phát hành".
3. Báo Boss "đã phát hành" chỉ sau khi **kiểm genh tải về** (checksum + `genh version` khớp tag) và `releases/latest` là đúng tag mới. Promote tay (`skip_e2e`) chỉ khi E2E lỗi ngoài mã.
4. Mỗi bản ghi: [CHANGELOG.md](../../CHANGELOG.md) (3–5 dòng) + `docs/releases/vX.Y.Z.md`; file này chỉ cập nhật hiện trạng + việc dở (≤ 200 dòng, có test).
5. Bật bảo vệ nhánh `main` + tag `v*` (cần quyền admin repo, ~2 phút): xem [v0.1.33.md](../releases/v0.1.33.md). Không thêm `paths-ignore: docs/**` cho `ci.yml`.

## Việc dở

### Sếp cần làm bây giờ (MỘT danh sách — gộp v0.1.65–71; chỉ việc 1 là bắt buộc; chi tiết ở [v0.1.71.md](../releases/v0.1.71.md#việc-boss-phải-làm), [v0.1.70.md](../releases/v0.1.70.md#việc-boss-phải-làm), [v0.1.68.md](../releases/v0.1.68.md#việc-boss-phải-làm) và [v0.1.65.md](../releases/v0.1.65.md#việc-boss-phải-làm))

1. **Bắt buộc duy nhất**: có ít nhất một nguồn AI chạy được — màn **Thiết lập nguồn AI** (`/setup?buoc=nguon-ai`) → dán khoá API hoặc đăng nhập Google / Claude Code → **Kiểm tra**. Antigravity báo hết hạn ⇒ đăng nhập lại ở đó. Chưa có khoá API cũng được: agent trực kênh, Soạn lại / Dịch nháp chạy bằng Antigravity qua hộp riêng.
2. Sau khi bản chính thức ra: `genh update` (hoặc đợi lịch đêm) — migration 0037–0041 tự chạy (máy đang ở v0.1.62 trở xuống; máy ở v0.1.68 chỉ chạy 0041: giờ yên lặng của Gen 22 → 21 nếu chưa chỉnh, «Kho <tên cũ>» → «Kho dữ liệu» trong tin Gen); hộp `agy-box` + proxy `agy-egress` và khoá `gh_agy_key` tự lên (Sức khoẻ: «Hộp riêng Antigravity: Đang chạy»); bridge khởi động lại. Thẻ Zalo/WhatsApp «Mất liên lạc» ⇒ **Nối lại bằng phiên đã lưu**.
3. Antigravity: đăng nhập ở **Kết nối › Bộ não AI** bằng **một tài khoản Google riêng** (token đi vào hộp mỗi lượt có người ngoài). Máy cài cũ chỉ có MỘT model Antigravity: cần tầng «Kỹ hơn» thì **tự thêm model** ở API & Model › ô chọn model.
4. Biết trước (**THAY ĐỔI QUYỀN**): đổi hạng / xoá nguồn AI, xoá khoá API **và thêm / sửa hạn mức / tắt-bật / gỡ model** cần PIN `ai.route_change`; Quản lý = «Việc được giao» (chưa có tính năng Nhóm); Lưu trữ agent và bật agent cần PIN, tắt không cần; Quản lý chỉ thấy nháp trong phạm vi; «Sửa rồi gửi» chặn chuỗi số đã che; tin 1-1 chỉ Owner / người phụ trách; gán BOT cho người = Owner + PIN. **Nhóm mới tự vào Lắng nghe im lặng** (không trả lời kể cả khi tag, không đưa cho agent trực — sàng lọc vẫn chạy; đổi ở thẻ kênh, PIN).
5b. **v0.1.71 — THAY ĐỔI QUYỀN (không bắt buộc, nên biết)**: ô **Quyền xem** của nhóm (Kênh › nhóm) nay có tác dụng thật; mọi nhóm đang ở «Chỉ Sếp» (mặc định) bị ẩn nội dung với Quản lý / Vận hành / Nhân viên / Kiểm soát. Nhân viên đang làm việc với dữ liệu nhóm thấy trống tới khi Sếp đổi nhóm đó sang «Sếp + quản lý» hoặc «Mọi thành viên». Chuông có thêm «Cảnh báo khẩn» (P1). «Lời hứa» tạm ẩn (Nợ #62).
5c. **v0.1.72 — bot tự gửi (tuỳ chọn, mặc định bot không tự gửi gì)**: nối một tài khoản Zalo/WhatsApp RIÊNG cho bot (SIM thật, không tài khoản giả) → ở thẻ kênh đánh dấu «Tài khoản riêng của bot» (PIN) → Kết nối › «Kênh quản trị» › Ghép (PIN) rồi nhắn mã 6 số từ tài khoản của Sếp → bật Chế độ hỗ trợ từng nhóm (Quan hệ › Danh bạ nhóm) nếu muốn. Cần hộp agy chạy. Rủi ro nền tảng khoá nằm ở tài khoản riêng này.
5. Chuông «Chưa đặt lịch sao lưu tự động» có thể kêu ngay máy cũ — đặt lịch ở Cài đặt › Sao lưu & cập nhật. Dùng thử màn **Nhật ký lắng nghe** (Nâng cao › Tầng dữ liệu) và Telegram ghép bot + nút **Tạm dừng** (tuỳ chọn: thẻ Telegram › Nhận tin từ Sếp › Ghép Telegram). Tuỳ chọn: «Câu hỏi mở — mặc định đã chọn» và Nợ ở [ROADMAP](../ROADMAP.md).

### Tuỳ chọn — không bắt buộc, làm khi muốn (cách làm chi tiết ở `docs/releases/vX.Y.Z.md` của từng bản)

- **Gen-hub** ([v0.1.49](../releases/v0.1.49.md), [v0.1.50](../releases/v0.1.50.md)): tick quyền ĐỌC (lịch, mail, việc, Drive) và GHI Kho (`kho_create`, `kho_update`, không tick gì khác) → Kết nối › Gen-hub › **Kiểm tra** (mã PIN);
  rồi duyệt đề xuất "Phiên" đầu tiên (Xác nhận + mã PIN) và thử Gen nhớ ("nhớ giúp em: …"). Chưa tick thì thẻ Ghi vào Kho dữ liệu bị khoá nút Xác nhận; phần còn lại vẫn chạy. Tên Kho: ô "Tên Kho" (≤ 40 ký tự, [v0.1.57](../releases/v0.1.57.md)).
- **Telegram** ([v0.1.44](../releases/v0.1.44.md)): tạo bot qua @BotFather → Kết nối › Telegram: dán mã, **Tìm chat_id**, **Lưu** (PIN), **Gửi thử**. Linux: nếu Console nhắc, chạy một lần `sudo loginctl enable-linger $USER`.
- **Truy cập từ xa** ([v0.1.46](../releases/v0.1.46.md)): chọn cách truy cập (khuyên Tailscale: `genh remote tailscale`), bấm "Kiểm tra NGAY TRÊN ĐIỆN THOẠI", mời thử một nhân viên.
- **Facebook trả lời** ([v0.1.47](../releases/v0.1.47.md)): thẻ "Gửi trả lời & tin nhắn" — nếu ghi "Khoá" thì đọc cảnh báo và tự quyết; nghiệm thu thật một lần bằng bình luận trên bài của chính Sếp.
- **WhatsApp quét QR thật** ([v0.1.59](../releases/v0.1.59.md), Nợ #35): Kết nối › WhatsApp › Tạo mã QR → tích "Tôi hiểu rủi ro…" → quét (điện thoại cách màn hình 20–30 cm). Vẫn lỗi ⇒ chụp thẻ WhatsApp (có "Chi tiết kỹ thuật") gửi Claude.
- **Gen hướng dẫn** ([v0.1.54](../releases/v0.1.54.md)): nút Gen (chấm đỏ) → thẻ Hôm nay của Sếp → **Chỉ cho em**; việc không dùng thì **Không dùng việc này**; dùng thử một tuần rồi báo Claude thẻ có đúng việc, chuông có phiền không.
- **Tự cập nhật đêm** ([v0.1.53](../releases/v0.1.53.md)): `genh auto-update status` phải ghi **BẬT** (có dòng CẢNH BÁO ⇒ chạy đúng lệnh nó in); muốn tắt hẳn: `genh auto-update disable`. Cập nhật không có nút "Cập nhật ngay" ⇒ đọc lý do trên thẻ.
- **Mặt tiền** ([v0.1.55](../releases/v0.1.55.md), [v0.1.60](../releases/v0.1.60.md)): mở `/owner` (Tình báo, Thông tin, Quan hệ); thẻ "Áp model chuẩn theo vai?" ⇒ **Mở** → **Áp model chuẩn theo vai** → Xác nhận. Đổi nguồn AI sau thiết lập: Cài đặt › Nguồn AI › Đổi nguồn AI.
- **Jev** ([v0.1.55](../releases/v0.1.55.md)): Cài đặt › Bộ não AI › thẻ Jev → "Dùng khóa OpenRouter đang có" → PIN → **Thử 12 câu mẫu** → gửi kết quả cho Claude để chốt ngưỡng lọc. Không bật thì vẫn lọc bằng quy tắc.
- **Repo / CI** ([v0.1.48](../releases/v0.1.48.md), [v0.1.56](../releases/v0.1.56.md), [v0.1.57](../releases/v0.1.57.md)): thêm secret `GH_PERSONAL_PATTERNS` (chưa thêm thì CI chỉ cảnh báo); duyệt PR #137 ở repo Brain; cài Renovate App; xem `docs/design/seed-data.json` có tên khách thật không. Repo giữ công khai (đã quyết 10/10).
- **Chờ Sếp cho phép (một câu mỗi việc)**: "cho xoá nhánh" (F-70, ROADMAP › Nợ); dặn Gen đề xuất Phiên bù (Nợ #7 — cần Sếp mở lại quy trình Kho, đang tạm ngưng từ 16/10).

### Claude / điều phối viên còn dở

- **Phát hành v0.1.74** (đã gồm v0.1.73): nhánh `local/v0174-heo` (xếp trên `local/v0173-chat` = PR #81, gốc `local/v0172-bot` = main v0.1.72) kiểm đầy đủ → Opus review (+ `/security-review`: phiên «chat_ok», PIN, đường gửi) → push → PR → CI xanh → merge squash → bản thử → E2E → promote → kiểm genh tải về khớp tag → `genh update` → báo Boss. Phiên chính tự tạo agent «Heo» và dọn bản A.Ly trùng trên bản thật sau khi phát hành (bản này không đụng dữ liệu thật). Các nhánh `rel/v0170`, `rel/v0168`, `rel/v0165` như cũ.
- **Chưa làm (mang từ v0.1.58)**: bước rẻ ở `e2e-install` (`agy --help | grep -- --effort`, `missing_reason` trong container); ô chọn model vẫn ẩn khi Kiểm tra đỏ (`offeredGroups`). **Nợ #44–#47, #49–#55, #56–#63** (allowlist egress hộp, core vào hộp, định dạng/xoay vòng token agy, quét danh bạ theo phần thay đổi, tin 1-1 còn hở); đệm ở bridge cho loạt tin dồn < 1 giây trước khi tập nghe đồng bộ · **Nợ #35** kiểm WhatsApp QR với máy chủ thật.
- **Bản kế tiếp (đã hẹn)**: nhận kết quả "Thử 12 câu mẫu" của Jev từ Boss rồi quyết ngưỡng lọc trước; Boss dùng thử Mặt tiền v2 rồi góp ý; chuông Gen dẫn về Mặt tiền; xoá hội thoại Gen thì xoá luôn lựa chọn model đã nhớ (Nợ #29); dọn mã chết trình thiết lập cũ (Nợ #36).
- **H-b còn mở**: timer đêm mất lịch khi `daemon-reload`/`enable` chạy từ bên trong service đêm — không tái hiện được bằng mã lẫn `e2e-nightly-real` (systemd thật: sau lần chạy timer vẫn có lần kế tiếp); tự lành bao ca này. Máy thật còn tái diễn thì ghi vào v0.1.53.md.
- **PR Renovate** không tự merge nằm chờ tới khi Boss nhắn "xử lý PR phụ thuộc"; chưa có lịch tự động nào gọi Claude.
- **Selector ghi Facebook** mới kiểm trên trang mẫu — chờ nghiệm thu thật (dòng 8). Chuông phiên hết có thể hiện hai lần (`social.paused` + `social.session_expired`), gộp ở bản sau nếu phiền.
- **Giới hạn đã biết**: TOTP chưa làm; Redis lỗi ⇒ giới hạn đăng nhập tạm không áp; gói apt trong Dockerfile chưa ghim phiên bản; tag GHCR `:latest` cũ đứng yên (không dùng).
- **Mục Nợ đầy đủ** (bộ câu hỏi chuẩn so model, F-70, TOTP, Facebook đăng bài lát 2, cosign/minisign, PHIEN bù, trường Kho "Công cụ"/"Người làm", lời hẹn trượt F-91/F-92, Gen hướng dẫn: `release_todos`, danh mục tính năng, "Làm giúp" G8, bộ bài cho nhân viên, tắt chuông theo loại…; Nợ #35–#37 và #42–#49: WhatsApp QR thật, mã chết trình thiết lập cũ, bảng Quan hệ ở 1024px, `list_promises` con trỏ, Chạy lại tin lỗi, hộp agy (egress, core, token), danh bạ, `view_scope`, tin 1-1): [ROADMAP › Nợ](../ROADMAP.md).
