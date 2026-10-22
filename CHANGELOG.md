# Nhật ký thay đổi Gen-Harness

Mới nhất ở trên. Mỗi bản 3–5 dòng; chi tiết (vì sao, thay đổi theo mã, kiểm tra, việc Boss phải làm) ở `docs/releases/vX.Y.Z.md`.
Hiện trạng và việc dở: [docs/reports/HANDOFF-v0.1.1.md](docs/reports/HANDOFF-v0.1.1.md) · tiến độ và mục Nợ: [docs/ROADMAP.md](docs/ROADMAP.md) ·
vận hành: [docs/runbook.md](docs/runbook.md).
Ngày = ngày Release trên GitHub theo giờ Việt Nam; tiêu đề trong `docs/releases/` có thể ghi ngày làm việc nên lệch 1 ngày
(v0.1.32–v0.1.34, v0.1.40, v0.1.46) hoặc vài ngày (v0.1.47, v0.1.48: làm 03/10, phát hành 09/10).
Việc sửa nóng không đổi số bản (PR #46, #48, #53, #55, #58, #59) ghi ở [ROADMAP › Bản phản ứng](docs/ROADMAP.md).

## v0.1.74 — Bot quản trị Zalo/WhatsApp dùng chung bộ não Gen đầy đủ, nói bằng Agent ngoại giao (19/10/2026)
- **Bản tính năng trên v0.1.73, không migration.** Sếp hỏi bot trên Zalo/WhatsApp (kênh quản trị 1-1) giờ chạy ĐÚNG động cơ Gen của Console: có **trí nhớ hội thoại** (một hội thoại riêng mỗi kênh), **công cụ đọc dữ liệu** áp quyền Owner, nói bằng **Agent ngoại giao** (không xưng Gen), văn bản thường; không còn giới hạn 6 dòng (quá dài thì tóm + mời hỏi tiếp).
- Câu tự nhiên «nhắc tôi 3h chiều gọi anh Tuấn», «ghi nhớ…», «soạn tin cho nhóm X…» không cần `/nhap`: Gen tự ĐỀ XUẤT, đề xuất vào sổ chờ để Sếp «OK» (mọi luật v0.1.73 giữ nguyên). Tool mới `group.list`, `person.list`, `group.recent` (nhóm nào có tin mới; theo «Quyền xem») — đóng Nợ #68.
- Bền hơn khi model lỗi định dạng: chữ rỗng ⇒ router thử model/nguồn kế tiếp; văn xuôi ⇒ làm một bước `say`; JSON lẫn trong chữ ⇒ được trích ra (cả Console lẫn kênh chat). Chi tiết: [docs/releases/v0.1.74.md](docs/releases/v0.1.74.md).

## v0.1.73 — Xác nhận ngay trong chat, bot xưng bằng Agent ngoại giao, «đã xem / đang nhập», sửa sổ tin tự gửi (19/10/2026)
- **Bản tính năng trên v0.1.72, không migration.** Kênh quản trị Zalo/WhatsApp: Gen soạn đề xuất thì bot hỏi «Trả lời OK để em làm (mã #X), hoặc HUỶ» — Sếp nhắn OK là làm đúng đường Xác nhận của Console, không cần mở máy tính hay nhập PIN (luật cứng Sếp duyệt 19/10); tin gửi người ngoài vẫn OK riêng từng tin kèm mã, nêu người nhận + nguyên văn. Telegram giữ nguyên.
- **Phân vai nội bộ / ngoại giao**: Gen chỉ nói trong Console; mọi kênh ngoài (Zalo/WhatsApp quản trị, Telegram, nhóm, Chế độ hỗ trợ) nói bằng «Agent ngoại giao» Sếp chọn ở màn Agent (tên + giọng; luật an toàn và bộ lọc không đổi). Chưa chọn ⇒ bot xưng «trợ lý».
- Bridge: «đã xem» + «đang nhập…» CHỈ trong kênh quản trị 1-1 (nhóm im lặng tuyệt đối). Sửa lỗi sổ `ops.auto_sends` kẹt «issued» (và nháp kẹt «approved»): kết quả gửi của bridge không mang `session_id` nên bị bỏ. Chi tiết: [docs/releases/v0.1.73.md](docs/releases/v0.1.73.md).

## v0.1.72 — Bot quản trị 1-1: tài khoản riêng của bot, kênh quản trị, Sếp gọi bot ở nhóm, Chế độ hỗ trợ (18/10/2026)
- **Bản tính năng trên v0.1.71, migration `0042`.** Ngoại lệ hẹp của luật cứng (Sếp duyệt 18/10): khi kênh Zalo/WhatsApp chạy bằng **tài khoản riêng của bot**, bot được tự gửi, không qua PIN, trong ĐÚNG ba trường hợp qua một cửa `send_auto`: trả lời/báo Sếp ở kênh quản trị 1-1 (ghép bằng mã 6 số), trả lời tại chỗ khi chính Sếp tag bot ở nhóm, trả lời thành viên nhóm đã bật Chế độ hỗ trợ (sau bộ lọc an toàn, model chạy trong hộp agy).
- Bot chỉ nhắn Sếp khi có sự kiện đặc biệt (không báo cáo định kỳ). Có sổ `ops.auto_sends` (không lưu nội dung tin), thẻ «Kênh quản trị», công tắc hỗ trợ + cột «Ai thêm bot» trên Console; test AST chứng minh không có đường gửi thứ tư.
- **Việc Boss làm** (tất cả tuỳ chọn, mặc định bot không tự gửi gì): nối tài khoản riêng cho bot, đánh dấu «Tài khoản riêng của bot», ghép kênh quản trị. Chi tiết: [docs/releases/v0.1.72.md](docs/releases/v0.1.72.md).

## v0.1.71 — Sửa lỗi tìm thấy khi giả lập toàn luồng (18/10/2026)
- **Bản vá nằm trên v0.1.70**, từ báo cáo giả lập toàn luồng: 3 lỗi P2 + 12 lỗi P3, mỗi lỗi kiểm lại trên mã trước khi sửa (cả 15 đều đúng), mỗi lỗi một commit có test đỏ trước xanh sau. Không migration (head vẫn `0041`), không thêm tính năng gửi tin tự động.
- **THAY ĐỔI QUYỀN**: «Quyền xem» của nhóm (Chỉ Sếp / Sếp + quản lý / Mọi thành viên) nay **thật sự ẩn** nội dung nhóm với vai khác ở Hộp thư, Kho thô / sạch, Nhật ký lắng nghe, hồ sơ, tìm kiếm, việc, cơ hội, tài liệu, WebSocket và Gen nhân viên — nhóm «Chỉ Sếp» (mặc định) cần Sếp mở cho nhân viên nếu họ cần xem. Cảnh báo P1 nay rung chuông Owner.
- **Sửa**: chứng cứ cảnh báo «Phản hồi chậm» xem được; OTP / mật khẩu / SĐT che trước khi vào prompt model và khỏi vai dưới Owner; tin đi của Sếp không còn thành cơ hội; tin nhân viên không tính «cần trả lời», thẻ «Khách chưa ai trả lời» đếm khách; số «Cơ hội» Hộp thư = Mặt tiền; vai thấp hết gọi `GET /system/update` (route-crawl baseline −14); tài khoản kênh của Sếp không còn «Người lạ»; nháp giới thiệu nguồn cung nêu số lượng / giá / khu vực; thẻ nhóm mới giảm khi đổi chế độ; «lượt/24h» tính cả khớp do model. «Lời hứa» chưa có bộ ghi nên **ẩn** (Nợ #62). Chi tiết: [docs/releases/v0.1.71.md](docs/releases/v0.1.71.md)

## v0.1.70 — Câu chữ sạch, thuật ngữ nói đúng, dọn sót; bản gộp v0.1.69 + v0.1.70 (18/10/2026)
- **Câu chữ sạch** (E1, E2, E3): «Kho tri thức», «nhập PIN», «khóa», «TUI», `kho_create` hết khỏi chữ chính; một câu sao lưu «7 bản theo ngày, 4 theo tuần, 12 theo tháng»; Bản tin Gen / chi phí AI / dọn đêm ghi rõ theo giờ Việt Nam; `check_terms` baseline 46 → 6; Jev giữ theo Q7 (test khoá). Telegram không chèn link `localhost` (cả tin trực canh của `genh`) và có nút **Tạm dừng / Bật lại** giữ token.
- **Dọn sót** của bản gộp: Gen gộp việc thiết lập 5–10 vào một thẻ «Việc thiết lập tiếp», «Để sau 7 ngày» ẩn mọi việc P3, một tên «Nối Gen-hub»; một đích «Thêm / kiểm tra nguồn AI» ở Bộ não AI / API & Model / Hướng dẫn, thêm 2 luật việc nền vào quy tắc chuyển hướng; sổ mặc định không liệt kê agent đã Lưu trữ; chuông đóng ngay khi sự cố ở Gen-hub, Telegram, phiên Claude Code hết.
- **THAY ĐỔI QUYỀN**: thêm / sửa hạn mức / tắt-bật / gỡ model cần mã PIN `ai.route_change` (cùng cổng với xoá nguồn AI và xoá khoá API). Sửa theo review: `channels_connected` bỏ phiên đã đăng xuất, form Telegram không tự bật lại sau Tạm dừng, chip «Đã tra Kho» lấy tên Kho thật. Nợ #53–#55 mới (tên màn «Kho …», múi giờ job nền, nhãn `chat_id`).
- Gộp hai bản: v0.1.69 (chuông có vòng đời + mặc định đúng quyết định của Sếp) **phát hành chung trong v0.1.70** — Sếp chỉ cần một lần `genh update`; nằm trên bản gộp v0.1.68. Migration 0041. Chi tiết: [docs/releases/v0.1.70.md](docs/releases/v0.1.70.md)

## v0.1.69 — Chuông có vòng đời, mặc định đúng quyết định của Sếp, vai Quản lý nói thật, nhãn sự kiện thống nhất (18/10/2026)
- **Chuông có vòng đời** (C4): sự cố đóng thì chuông tự chuyển «Đã xử lý» (đóng đúng chuông của sự cố, lưu khoá sự cố; WebSocket `notification.resolved`), không trùng lặp (lỗi cập nhật, sao lưu ngoài máy cùng nguyên nhân gộp một), «Xem thêm» lật trang quá 100 thông báo; **chưa đặt lịch sao lưu tự động quá 24 giờ là sự cố có chuông**.
- **Mặc định đúng quyết định của Sếp** (D3 + lưới L6 `test_quyet_dinh_sep.py`): Gen hướng dẫn yên lặng **21:00–07:00** (QD-20), «Kho <tên cũ>» đổi thành «Kho dữ liệu» trong dữ liệu đã lưu (QD-22), ghi chú Gen-hub nói đúng «đọc; ghi Kho chỉ khi Xác nhận + mã PIN» (QD-18); migration 0041 chỉ đổi dòng Sếp chưa từng chỉnh.
- **Vai Quản lý nói thật** (D4, Q4=2): «Việc được giao», không hứa «thấy team mình» khi chưa có tính năng Nhóm (Nợ #52); **một bảng nhãn 15 loại sự kiện** tiếng Việt cho mọi màn (D5).
- Phát hành chung trong v0.1.70 (không có bản v0.1.69 riêng). Chi tiết: [docs/releases/v0.1.69.md](docs/releases/v0.1.69.md)

## v0.1.68 — Một danh sách việc, thiết lập nói thật, màn «Nhật ký lắng nghe»; bản gộp v0.1.67 + v0.1.68 (17/10/2026)
- **Thiết lập và «Việc Sếp cần làm» nói thật** (D1, C1, C2): chỉ MỘT cách đếm việc và MỘT đường tới «Thêm nguồn AI» (`/setup?buoc=nguon-ai`); nguồn Sếp đã tắt không bị tự bật lại; «Mời người trong đội» chỉ xong khi có người khác Owner đang hoạt động; làm lại bước 8 không tạo agent trùng, agent bước 8 nghe sẵn mọi kênh đã nối; vai không phải Owner mở `/guide/n` thấy khung «Việc này do Owner làm» (hết 403 ở console); Kết nối › Gen-hub «Lưu & kiểm tra» tự ghi kết quả kiểm. Route-crawl duyệt thêm vai Operator và Auditor.
- **Màn mới «Nhật ký lắng nghe»** (Nâng cao › Tầng dữ liệu, sau Kho dữ liệu thô): một dòng thời gian gộp tin và sự kiện kênh (nhóm mới/rời, thành viên vào-rời, kênh nối/ngắt), kèm chế độ nghe lúc tin vào kho, sàng lọc, quyết định của agent trực; tìm chữ, lọc, tự cập nhật. Migration 0040.
- **THAY ĐỔI QUYỀN**: đổi hạng chuỗi nguồn AI sau khi Hoàn tất thiết lập, xoá nguồn AI và xoá khoá API cần mã PIN `ai.route_change`; xem thêm v0.1.67 ở dưới.
- Gộp hai bản: v0.1.67 (model hiển thị = model chạy + agent làm và nói thật) **phát hành chung trong v0.1.68** — Sếp chỉ cần một lần `genh update`; kèm bản gộp v0.1.65 / bản vá v0.1.66 (hộp riêng Antigravity, Telegram 2 chiều, chế độ nghe) nếu máy chưa lên. Chi tiết: [docs/releases/v0.1.68.md](docs/releases/v0.1.68.md)

## v0.1.67 — Model hiển thị = model chạy + Agent làm và nói thật: một bộ chọn model, xoá khoá / gỡ model / hạn mức, lỗi model đúng loại, Lưu trữ agent, «Chưa trả lời» đúng người (17/10/2026)
- **Model hiển thị = model chạy** (B3, C3 + D2): «Chuẩn: …», chuỗi chạy và bảng gán dùng chung một hàm; model Sếp đã chốt hiện đúng; Gen không ép tầng Nhanh khi Sếp đã ghim model; flash = Cân bằng, flash-lite = Nhanh; ô chọn model luôn hiện cả khi Kiểm tra đỏ (ghi «chưa xác minh»). Việc mới ở API & Model: Xoá khoá, Gỡ model (chặn nếu còn lượt gọi trong 30 ngày, có «Tắt model»), ô Hạn mức/ngày và Lượt/phút. Bản cài cũ chỉ có 1 model Antigravity ⇒ tự thêm model ở ô chọn nếu cần tầng «Kỹ hơn».
- **Agent nói thật** (B7, C5): thẻ agent có dòng MODEL («Chuẩn: …» hoặc model riêng, cảnh báo khi nguồn đã tắt); lỗi 503 phân biệt «chưa có model» với «nguồn đang lỗi / hết hạn mức»; bảng «Agent đã nói gì» hiện câu agent gợi ý, «Đã gửi» chỉ khi nháp thật sự gửi; Soạn lại theo giọng, điều cấm, xưng hô của agent.
- **Lưu trữ agent** (PIN `agent.manage`, giữ lịch sử quyết định), chặn trùng tên, tắt agent không cần PIN, bật cần PIN, nhân bản chép cả model riêng, mức tự trị mặc định 4, mẫu Việt hoá. Migration 0039. **THAY ĐỔI QUYỀN**: Quản lý chỉ thấy nháp trong phạm vi của mình; «Sửa rồi gửi» của vai dưới Owner chặn chuỗi số đã che (422). **«Chưa trả lời» đúng người, đúng tin** (C6): tin 1-1 ghép theo người nhận, mỗi người một cảnh báo.
- Phát hành chung trong v0.1.68 (không có bản v0.1.67 riêng). Chi tiết: [docs/releases/v0.1.67.md](docs/releases/v0.1.67.md)

## v0.1.66 — Bản vá phát hành của v0.1.65: sửa bước kiểm E2E nâng cấp lỗi thời; nội dung sản phẩm như v0.1.65 (18/10/2026)
- v0.1.65 đã có bản thử nhưng E2E «cài đặt thật» chế độ release đỏ ở hai job nâng cấp (từ v0.1.60 và v0.1.62) vì bước F-22 còn đòi api tự chuyển phiên Claude từ đường dẫn cũ — việc v0.1.63 đã cố ý bỏ. Sửa bước kiểm + thêm test giữ hợp đồng (`.github/workflows/e2e-install.yml`, `test_e2e_upgrade_steps.py`).
- **Mã sản phẩm KHÔNG đổi**, nội dung như v0.1.65 (gộp v0.1.63 + v0.1.64 + v0.1.65). Ai đang ở v0.1.62 chạy `genh update` sẽ lên thẳng v0.1.66; không có bản chính thức v0.1.65 riêng.
- Chi tiết: [docs/releases/v0.1.66.md](docs/releases/v0.1.66.md)

## v0.1.65 — Chế độ nghe & danh bạ: nhóm mới tự vào Lắng nghe im lặng, thu thành viên mọi nhóm, tin 1-1 lưu im lặng; quyền xem tin 1-1 chặt hơn (17/10/2026)
- **Nhóm mới mặc định Lắng nghe im lặng** (Sếp chốt 17/10): lưu hết tin, KHÔNG BAO GIỜ trả lời kể cả khi được tag (`tagged_only` mới trả lời khi tag); tin ĐẦU TIÊN của nhóm lạ cũng được lưu; nhóm cũ đang Không nghe giữ nguyên; đổi mặc định ở thẻ kênh (cần mã PIN). **Danh bạ** quét 2 giờ/lượt, thu thành viên MỌI nhóm (kể cả Không nghe), đánh dấu người/nhóm đã rời; nút «Cập nhật danh sách nhóm».
- **Tin 1-1 lưu im lặng** (bản cài cũ đang tắt ⇒ nay lưu im lặng, đang bật giữ trả lời): ba mức Lưu im lặng / Lưu và cho trợ lý trả lời / Không lưu; «Gán BOT» cho một người có tác dụng với tin 1-1 của người đó. **THAY ĐỔI QUYỀN**: tin 1-1 chỉ Owner (hoặc người phụ trách/được giao đúng người đó) xem nguyên văn; gán/bỏ BOT cho một người chỉ Owner + mã PIN; SĐT/JID che với vai dưới Owner. Migration 0038 (sau 0037).
- **Gộp ba bản lớn**: v0.1.63 (hộp riêng Antigravity) và v0.1.64 (Telegram 2 chiều, sàng lọc ghi loại người) **phát hành chung trong v0.1.65** — Sếp chỉ cần một lần `genh update`.
- Chi tiết: [docs/releases/v0.1.65.md](docs/releases/v0.1.65.md)

## v0.1.64 — Telegram 2 chiều: Sếp nhắn Gen qua Telegram; sàng lọc ghi loại người, Sếp tự đánh dấu nhân viên (17/10/2026)
- **Telegram 2 chiều (Nợ #13)**: ghép Telegram bằng mã 6 số (`/start <mã>`, chỉ lưu băm HMAC, sống 10 phút), công tắc «Nhận tin từ Sếp» (mặc định TẮT); chỉ nhận chat riêng đúng tài khoản đã ghép; lệnh `/hom_nay` `/viec` `/suc_khoe` `/nhap` + câu hỏi tự do (Gen chỉ đọc/đề xuất, dữ liệu đã che); đề xuất chỉ có nút «Mở Console để Xác nhận» — không nút Duyệt, không nhận mã PIN qua Telegram. Migration 0037.
- **Sàng lọc ghi loại người (B6)**: kết quả quy tắc ghi ngược vào hồ sơ (chỉ nâng từ «chưa rõ», không đè loại Sếp đã đặt); hồ sơ có nút «Đánh dấu là nhân viên»; tin lỗi không kẹt (tự nhận lại sau 30 phút, tối đa 3 lần), dải pipeline hiện số tin lỗi.
- Phát hành chung trong v0.1.65 (không có bản v0.1.64 riêng). Chi tiết: [docs/releases/v0.1.64.md](docs/releases/v0.1.64.md)

## v0.1.63 — Hộp riêng Antigravity: mỗi vai một chỗ chạy, "nguồn AI dùng được" một hàm duy nhất (17/10/2026)
- **Hộp riêng `agy-box`**: container chạy Antigravity không có khoá master, DB hay Redis (chỉ khoá ký `gh_agy_key`, không volume — tệp phiên agy gửi theo từng lượt, mọi lượt agy chạy HOME tạm; chỉ mạng internal, ra Internet qua proxy `agy-egress` chặn IP nội bộ/LAN; sau mỗi lượt hộp giết mọi tiến trình agy để lại và xoá `/tmp`, `/dev/shm`); `genh update` tự sinh khoá và dựng hộp, `genh doctor` + thẻ Sức khoẻ có dòng "Hộp riêng Antigravity".
- **Mỗi vai một chỗ chạy**: Gen của Sếp, agent nội bộ chạy agy trong tiến trình; Bản tin, sàng lọc tin người lạ, Soạn lại / Dịch nháp, trực việc, agent trực kênh (ngoại giao), Gen của nhân viên, lượt thử agent chạy qua hộp. Nên đăng nhập Antigravity bằng tài khoản Google riêng (token đi vào hộp). Hộp tắt ⇒ dùng nguồn AI kế, không bao giờ rơi về agy trong tiến trình; "Thử quy tắc" vẫn không dùng agy.
- **Một hàm "nguồn AI dùng được"** (`providers.readiness`): bước 4 thiết lập, dòng "nguồn AI" ở Việc Sếp cần làm và nút Kiểm tra cùng hỏi nó (đóng F002/F010/F014/F035), có lý do `box_down`.
- **Cổng phát hành**: `check_agy_box_isolation.py` + E2E `e2e_agy_box.sh` (cài mới và nâng cấp) trước khi ra bản chính thức.
- Phát hành chung trong v0.1.65 (không có bản v0.1.63 riêng). Chi tiết: [docs/releases/v0.1.63.md](docs/releases/v0.1.63.md)

## v0.1.62 — Đợt A phần 2, Antigravity phân lớp theo kiến trúc, kênh bền, sức khoẻ, dev cục bộ (17/10/2026)
- **Xem hết & tìm ở máy chủ**: Hộp thư, Việc, Nhật ký (câu tiếng Việt, ẩn `http.*`), Khách & Nhóm (con trỏ `(created_at, id)`, gán BOT hàng loạt theo bộ lọc, xuất/ẩn danh hoá một người); lỗi sao lưu và 403 nói tiếng người; Cung–Cầu không ghép bừa; che OTP/mật khẩu (kể cả mẫu Việt Nam) trước khi gửi model.
- **Antigravity phân lớp**: agy dùng cho lớp core/nội bộ (Gen của Sếp, Bản tin, sàng lọc, agent không gắn kênh); chưa dùng cho ngoại giao (`core.reply`, agent giao tiếp người ngoài) và Gen nhân viên — chờ hộp riêng v0.1.63; thêm `prctl` che môi trường của chính api/worker (chưa phải cô lập thật — `/run/secrets` và tiến trình con còn đọc được, hộp riêng ở v0.1.63), `pg_dump`/`pg_restore` chỉ nhận môi trường tối thiểu, timeout việc nền + khoá tệp token, ca canary đọc `/proc/1/environ` (dự kiến báo hở, không chặn phát hành).
- **Kênh bền & sức khoẻ**: mất mạng không xoá phiên Zalo/WhatsApp, nút Nối lại (hỏi lại khi phiên bị mở nơi khác), phiên lỗi có nút Đăng xuất; Sức khoẻ canh bridge im lặng, hàng lỗi có Phát lại/Xoá (mã PIN); Dockerfile api luôn cài mã mới + CI so băm; chỉ api sinh mã thiết lập; môi trường dev riêng (`docs/dev-local.md`).
- Chi tiết: [docs/releases/v0.1.62.md](docs/releases/v0.1.62.md)

## v0.1.61 — Đợt A sửa lỗi sau kiểm kê: mã PIN cho người được giao quyền, bảng quyền, nháp & gửi tin trung thực, lưới chống tái phát (16/10/2026)
- **Mã PIN & quyền**: người được giao quyền duyệt tự đặt mã PIN lần đầu ở Tài khoản của tôi (hộp PIN nói rõ "chưa có mã PIN"); phiên PIN hết hạn thật (xem/WebSocket không gia hạn); bảng quyền đổi cả cột, "Soạn nháp" tách khỏi "Duyệt & gửi"; tên vai trò tiếng Việt thống nhất; mẫu agent bước 8 trùng máy chủ (migration 0036, chạy lại an toàn).
- **Nháp & gửi tin trung thực**: gửi lỗi có chuông + thẻ "Gửi lỗi" + nút Gửi lại; tin 1-1 đi đúng tài khoản; nháp công cụ ghi (mcp_write) không còn giả "đã làm"; gộp người dời đủ bảng (cơ hội, deal, việc, nháp, phân quyền…) và đảo ngược chính xác.
- **Cập nhật & lưới**: người gác dự phòng không chặn "Cập nhật ngay", "Kiểm tra bản mới" có Nhật ký riêng; thêm `check_terms.py` (chữ cấm, baseline chỉ giảm) và Playwright duyệt mọi route 390/1440px (job `crawl` riêng).
- Chi tiết: [docs/releases/v0.1.61.md](docs/releases/v0.1.61.md)

## v0.1.60 — Mặt tiền Owner v2: 5 trụ cột có số thật + Thiết lập một màn 3 bước (16/10/2026)
- **Mặt tiền 5 trụ cột**: Tình báo (bản tin, Việc phải quyết có Duyệt + mã PIN, cảnh báo, 4 ô số), Thông tin (lọc Quan trọng · Tất cả · Cơ hội · Cảnh báo · Chờ duyệt, tin xếp Khẩn/Quan trọng/Thường, hội thoại 10 tin, Trả lời chỉ tạo nháp → "Duyệt và gửi"), Quan hệ (Cần chăm, Khách nóng, Cầu nối, Cung–cầu, hồ sơ sống), Phân tích (7/30 ngày, biểu đồ, phễu, chi phí, 9 chỉ số), Việc (tick Xong, Dời sang mai). Thanh trái ở máy tính, thanh dưới 5 nút ở điện thoại; Hỏi Gen là ngăn kéo.
- **API chỉ đọc, chỉ Owner**: `GET /owner/brief|feed|feed/{id}/context|insights|proposals`, `relations?list=care`, `today.nav`; không gọi model, không migration.
- **Thiết lập một màn** Tài khoản → Nguồn AI → Xong; mọi nút "Chọn model" dẫn `/setup?buoc=nguon-ai`; Cài đặt `/owner/them` đúng 4 khối. Hệ hình ảnh mới (token sáng/tối, 8 thành phần kit) + lính gác thiết kế.
- Chi tiết: [docs/releases/v0.1.60.md](docs/releases/v0.1.60.md)

## v0.1.59 — Bản vá: sửa quét QR WhatsApp (chưa kiểm với máy chủ WhatsApp thật), thẻ "Hôm nay của Sếp" đọc lại ngay (16/10/2026)
- **Sửa quét QR WhatsApp**: mã QR WhatsApp dày gấp đôi Zalo nay vẽ lớn ở màn Kết nối (trước 88px nên điện thoại không đọc); ghép cặp luôn đổi khoá khi máy chủ gửi `companion_reg_refresh` (kể cả trước mã đầu); socket lỗi trước khi ra mã thử lại 4 lần có giãn cách thay vì mở lại vô hạn; ghim Baileys `7.0.0-rc14`; yêu cầu lấy phiên bản WA Web tự nhận là bridge Gen-Harness (không giả trình duyệt); thẻ kênh nói lý do lỗi bằng tiếng Việt kèm "Chi tiết kỹ thuật".
- **Hôm nay của Sếp**: "Kiểm tra kết nối" của nguồn CLI xanh do Owner bấm được tính là bằng chứng nguồn AI chạy được (dòng bắt buộc đạt ngay khi nguồn đang bật); thẻ cập nhật ngay khi đổi nguồn, khoá, model hay gán model, không trễ 60 giây.
- Chưa thử được với máy chủ WhatsApp thật (máy kiểm thử bị chặn mạng); kiểm trên máy Owner là Nợ #35.
- Chi tiết: [docs/releases/v0.1.59.md](docs/releases/v0.1.59.md)

## v0.1.58 — Bản vá nóng: Antigravity CLI đòi mức suy nghĩ làm Gen im lặng, "Kiểm tra kết nối" đỏ và mọi vai "chưa có nguồn phù hợp" (11/10/2026)
- **Gốc rễ**: agy của Sếp không đánh dấu model "current" nên lượt gọi thử không gửi `--effort`, agy từ chối kèm `available: low, medium, high`; `_cli_probe` không thử lại với một mức nên đỏ và báo sai `CLI không nhận model ""` (ô chọn model bị ẩn, Sếp kẹt). Nay thử lại cùng model với 'medium' (≤ 4 lượt), câu lỗi nêu đúng model + mức và không bao giờ in model rỗng; `_complete` cũng gọi lại một lần khi CLI đòi mức.
- **Tự lành**: Kiểm tra kết nối xanh mà nguồn CLI chưa có dòng model ⇒ tự lưu (model, mức) vừa chạy được (không đặt mặc định, Action Log `provider.model_auto`); lượt Gen của Owner tự kiểm tra đúng một lần/10 phút khi agy mất dòng model; việc nền không bao giờ gọi agy.
- **Hiển thị**: bảng gán model nói lý do thật thay "Chuẩn: chưa có nguồn phù hợp" (`standard_reason`: "cần khoá API (Antigravity chỉ dùng cho Gen)", "chưa có model — bấm Kiểm tra kết nối ở …"); nhãn "Sắp hết hạn" của agy không còn khi tệp phiên có khoá làm mới token (chỉ xét tên khoá). Không migration.
- Chi tiết: [docs/releases/v0.1.58.md](docs/releases/v0.1.58.md)

## v0.1.57 — Khoá nạp dữ liệu mẫu, Owner tự đặt Tên Kho, CI api song song, bộ kiểm chống lộ không ghi danh tính (10/10/2026)
- **Dữ liệu mẫu có khoá**: `gh.seed_demo seed` chỉ chạy khi có `--force` / `GH_ALLOW_SEED_DEMO=1` và cơ sở dữ liệu chưa có dữ liệu thật (người dùng, liên hệ, tin nhắn); không thì in câu tiếng Việt + "Chi tiết kỹ thuật", thoát mã 2. Makefile và E2E đã truyền cờ.
- **Ô "Tên Kho (tuỳ chọn)"** ở thẻ Gen-hub (≤ 40 ký tự, chỉ Owner, trống = "Kho dữ liệu", có Về mặc định): `kho_label(settings)` thay mọi chữ tên Kho chạy thật (thẻ đề xuất, prompt Gen, chuông, bài học/mẹo…). Không migration; Nợ #30 xong.
- **CI `api`**: hai lượt pytest (superuser + role `gh_app`) chạy song song trong cùng job, CSDL mẫu + Redis riêng, khoá migrate; tên job và timeout 75 giữ nguyên.
- **Bộ kiểm chống lộ** chỉ giữ quy tắc chung trong repo (email phải `example.*`/noreply, địa chỉ `/mcp` phải là `<…>`/`example.*`); mẫu riêng của Sếp nằm ở secret `GH_PERSONAL_PATTERNS`, log không in giá trị mẫu. Test dùng mẫu giả. Bản công khai đã gọt danh tính riêng (tên, địa chỉ, ghi chú vận hành); ghi chú riêng của Sếp ở Brain (riêng tư).
- Chi tiết: [docs/releases/v0.1.57.md](docs/releases/v0.1.57.md)

## v0.1.56 — Không lộ thông tin riêng của Sếp cho Owner khác (10/10/2026)
- Bỏ địa chỉ Gen-hub của Sếp khỏi mọi thứ giao cho Owner khác (ô nhập, hướng dẫn bước 14, câu lỗi web + API): chỉ còn chỗ giữ chỗ `https://<địa-chỉ-gen-hub-của-bạn>/mcp`; tên Kho đổi thành **"Kho dữ liệu"** (`KHO_LABEL` một chỗ ở API + contracts; thẻ đề xuất, bài học/mẹo Gen, prompt, nhãn quyền PIN).
- Migration **0035** (chạy lại an toàn) đổi ghi chú máy chủ Gen-hub cũ đã lưu trong DB Owner; dữ liệu mẫu `seed_demo.py` và mock/test dùng tên hư cấu (`example.test`, "Công ty Mẫu…").
- CI mới **"Chống lộ thông tin riêng của Sếp"** (`check_no_personal_info.py`) chặn địa chỉ/tên riêng lọt lại vào mã giao đi; tài liệu hướng dẫn chung dùng chỗ giữ chỗ. Cần Boss quyết: repo private hay chuyển ghi chú riêng sang Brain.
- Chi tiết: [docs/releases/v0.1.56.md](docs/releases/v0.1.56.md)

## v0.1.55 — Gọn cho Sếp: Mặt tiền Owner, mặc định tiêu chuẩn, thiết lập gọn, chọn model trong chat, Jev lọc trước (10/10/2026)
- **Mặt tiền Owner** `/owner/*`: Owner mở trang chủ thấy Hôm nay (4 số + việc chờ duyệt + Bản tin + "Gen lọc giúp"), Việc, Quan hệ, Hỏi Gen; Console cũ ở Thêm › Cài đặt nâng cao. API chỉ đọc, chỉ Owner.
- **Hồ sơ model tiêu chuẩn theo vai** + **Về mặc định** (từng mục / tất cả có mã PIN, không chạm khoá, nguồn AI, PIN, Gen-hub…): cài xong không phải gán model; migration 0034 (`agent.bindings.effort`).
- **Thiết lập ≤ 4 lần nhập**, Việc Sếp cần làm chỉ còn 1 dòng bắt buộc ("Có ít nhất 1 nguồn AI chạy được"); thẻ Cập nhật không có nút thì luôn nói lý do + việc cần làm.
- **Chọn Nhanh / Kỹ hơn ngay dưới ô nhập Gen** (nhớ theo hội thoại); câu ngoài phạm vi trả câu mẫu không gọi model. **Jev lọc trước** tin trùng/rác (che dữ liệu bắt buộc), Bật Jev 1 chạm + "Thử 12 câu mẫu".
- Chi tiết: [docs/releases/v0.1.55.md](docs/releases/v0.1.55.md)

## v0.1.54 — Gen hướng dẫn: Gen chủ động nhắc việc Sếp cần làm, giới thiệu tính năng, bài học mỗi ngày (10/10/2026)
- Thẻ **Hôm nay của Sếp** ở đầu khung Gen (chỉ Owner): tối đa 3 **Việc cần làm ngay** (sự cố, dòng bắt buộc, sao lưu, token Gen-hub, nháp chờ duyệt…), một mẹo **Sếp biết chưa?** và **Bài học hôm nay · k/19**; mỗi việc có Chỉ cho em / Để mai / Không dùng việc này (việc khẩn không tắt được).
- Lộ trình 19 bài (10 bài nội dung + 9 bài từ Hướng dẫn thiết lập) ở Trợ giúp › Lộ trình học cùng Gen; chấm đỏ ở nút Gen; chuông `gen.coach` tối đa 1/ngày (09:05/11:05/14:05, có giờ yên lặng, không đẩy Telegram); Bản tin thêm dòng "Việc bắt buộc: đã đạt x/N".
- Cài đặt › Bộ não AI › Gen hướng dẫn (bật/tắt, chuông, số bài mỗi ngày, giờ yên lặng, Bật lại); Gen có tool `coach.status` để trả lời "em cần làm gì?". Không gọi model, không ghi hội thoại; migration 0033.
- **Người gác yêu cầu tự chữa**: bài học từ một bản cài thật — `gen-harness-update-request.path` rơi vào failed "Result: resources" vì hết hạn mức inotify (mặc định 128, đang dùng 174) nên nút Cập nhật ngay không có ai nhận; `genh auto-update enable|status` và lần cài/cập nhật nay `reset-failed` + restart khi còn inotify, vẫn lỗi thì bật timer dự phòng quét mỗi phút và nói rõ lệnh `sudo sysctl -w fs.inotify.max_user_instances=1024`; chỉ coi `.path` khoẻ khi `SubState=waiting` (đang `running` trong service thì systemd báo sống giả — giữ dự phòng); `handle-requests` không còn tự chữa; thẻ Sức khoẻ có dòng cảnh báo, E2E thật có ca inotify (đã chạy, đã sửa gốc rễ).
- Chi tiết: [docs/releases/v0.1.54.md](docs/releases/v0.1.54.md) · thiết kế: [docs/design/gen-coach.md](docs/design/gen-coach.md)

## v0.1.53 — Tự cập nhật đêm tự lành + trung thực (10/10/2026)
- Một bản cài kẹt ở v0.1.44 từ 03/10 đến 09/10: lịch tự cập nhật đêm bị tắt mà `genh auto-update status` đọc sai và không ai biết, nút Cập nhật ngay trên Console chỉ nói "chưa nhận yêu cầu" (F-93, F-94, F-99).
- `genh update` tự lành: bật lại lịch đêm khi unit mất/tắt/không chạy (trừ khi Sếp đã chủ động tắt); `genh auto-update status` nói thật (5 thông tin: bật/tắt thật, cơ chế, enabled/active, lần chạy gần nhất/kế tiếp, linger) và cảnh báo khi log im quá 36 giờ; kiểm linger sau `enable-linger` (F-93, F-94, F-95).
  **Thay đổi hành vi**: máy đã tắt lịch đêm từ trước v0.1.53 (chưa có dấu "Sếp đã tắt") được bật lại một lần — muốn tắt hẳn: `genh auto-update disable`; `install --no-auto-update` nay tắt luôn lịch đêm đang có của bản cài đó.
- Lịch đêm chọn bản cao nhất đã đủ 24 giờ trong 10 bản gần nhất, không bị bản mới chưa chín chặn "đói" (F-96); nút Cập nhật ngay không kích lặp và không kẹt khi tệp yêu cầu không xoá được (GH-E94C, F-97); unit đêm mang `--install-dir`/`--port` và bản cài phụ không gỡ/ghi đè lịch của bản chính (F-98).
- Console nói rõ nguyên nhân "chưa nhận yêu cầu" (linger tắt / trình nhận yêu cầu lỗi) và cảnh báo lịch đêm im (F-99); E2E chạy thật timer và `.path` dưới user manager có linger, bắt buộc trước promote (F-100).
- Chi tiết: [docs/releases/v0.1.53.md](docs/releases/v0.1.53.md)

## v0.1.52 — Sửa cổng phát hành lần 2: genh gỡ digest cũ của ảnh đang chạy bằng `rmi -f` (10/10/2026)
- Thay v0.1.51 chưa promote: E2E nâng cấp vẫn đỏ ở bước "chỉ còn ảnh của bản hiện tại và bản liền trước" vì Docker từ chối gỡ digest cũ của `gen-harness-db` (container db đang chạy ảnh này). v0.1.52 chứa toàn bộ v0.1.51 + v0.1.50 + sửa này.
- Docker coi mọi digest của cùng một repo là MỘT tham chiếu nên `rmi repo@digest` bị chặn dù còn digest khác trỏ cùng ảnh; `genh update` nay dùng `rmi -f` chỉ cho digest mà ảnh còn được tham chiếu giữ (chỉ untag, không xoá ảnh).
- Chi tiết: [docs/releases/v0.1.52.md](docs/releases/v0.1.52.md)

## v0.1.51 — Sửa cổng phát hành: genh dọn cả tham chiếu digest cũ của ảnh trùng nội dung (10/10/2026)
- Thay v0.1.50 chưa promote: E2E nâng cấp đỏ ở bước "chỉ còn ảnh của bản hiện tại và bản liền trước" nên v0.1.50 nằm yên ở bản thử. v0.1.51 chứa toàn bộ v0.1.50 + sửa này.
- `genh update` dọn ảnh cũ theo CHÍNH tham chiếu (repo@digest / repo:tag) thay vì theo IMAGE ID: ảnh `gen-harness-db` không đổi nội dung qua nhiều bản (cùng ID, khác digest) nên digest cũ trước đây không bao giờ bị gỡ.
- Chi tiết: [docs/releases/v0.1.51.md](docs/releases/v0.1.51.md)

## v0.1.50 — Gen nhớ + Gen ghi Kho có xác nhận và mã PIN (09/10/2026)
- Gen nhớ: ghi chú quy ước/sở thích của Sếp lưu cục bộ (tối đa 30), Gen đề xuất — Sếp xác nhận — sửa/xoá ở Cài đặt › Bộ não AI (F-81, migration 0032).
- Ghi vào Kho dữ liệu: Gen đề xuất `kho_create`/`kho_update` cho Phiên/Việc; chỉ ghi sau Xác nhận + mã PIN + permit ký, qua Gen-hub (QD-18, F-81).
- Mỗi bản mới Gen tự đề xuất một Phiên (chuông + thẻ); Kết nối › Gen-hub có "Quyền ghi Kho"; Việc Sếp cần làm thêm dòng 9 "Gen ghi Kho" (F-87).
- Tài liệu đồng bộ: CHANGELOG + `docs/releases/`, HANDOFF ≤ 200 dòng, ROADMAP có mục Nợ, README/runbook, script kiểm link (F-90, F-69, F-47, F-91, F-92, F-42, F-39).
- Gộp từ main: sửa trình bày (PR #58 — chữ dính biên khung, Icon rớt dòng, lính gác bố cục 4 cỡ màn) và genh đọc tệp trạng thái thử lại khi tệp vừa bị thay (PR #59).
- Chi tiết: [docs/releases/v0.1.50.md](docs/releases/v0.1.50.md)

## v0.1.49 — Gen đọc lịch / mail / việc / Drive qua Gen-hub, chỉ đọc (09/10/2026)
- Gen (chỉ Owner) trả lời được lịch, mail, việc Google, tệp Drive qua Gen-hub và Tài liệu/Deal/Vụ việc nội bộ — đã che; tool ghi bị chặn ở mọi đường (QD-16).
- Ngắt mạch riêng cho Gen-hub (3 lỗi → mở 60 giây; im 15 phút → sự cố + chuông) (F-83).
- Bản tin Gen thêm "Lịch hôm nay", "Mail cần trả lời", "Việc Google đang mở"; Telegram chỉ có số đếm (F-8).
- Chi tiết: [docs/releases/v0.1.49.md](docs/releases/v0.1.49.md)

## v0.1.48 — Bản build tái lập & pipeline gọn (09/10/2026)
- Ảnh nền, caddy, redis ghim digest đa kiến trúc; api/browser cài từ `uv.lock` (`uv sync --frozen`); Renovate đề xuất nâng (F-19, F-36).
- Mọi action ghim SHA, quyền mặc định `contents: read`, bỏ ảnh `:latest`, build thử lại và kiểm tái lập (F-71).
- Quét bảo mật dạng báo cáo (pip-audit, npm audit, govulncheck); CI đỏ khi bản nhúng genh lệch `deploy/` (F-13, F-44).
- Chi tiết: [docs/releases/v0.1.48.md](docs/releases/v0.1.48.md)

## v0.1.47 — Facebook ghi, lát 1 (09/10/2026)
- Gen đề xuất Trả lời bình luận và Nhắn tin; chỉ gửi sau Xác nhận + mã PIN + permit ký, có ảnh chụp bằng chứng và Dừng tất cả (F-79).
- Sandbox Chromium bật bằng user namespace + seccomp riêng; không bật được thì chỉ mở gửi khi Owner đồng ý rủi ro (F-85).
- Kiểm phiên Facebook hằng ngày 09:10 → sự cố + chuông + Telegram (F-83); trễ cố định 3 giây (F-59); gỡ số bản khỏi chú thích "chỗ cắm" (F-92).
- Chi tiết: [docs/releases/v0.1.47.md](docs/releases/v0.1.47.md)

## v0.1.46 — Nhân viên & điện thoại vào được (04/10/2026)
- Cài mới chỉ nghe 127.0.0.1; máy cũ giữ 0.0.0.0 và nhận đúng một chuông "Cổng đang mở cho cả mạng" (F-27, F-21).
- `genh remote tailscale|cloudflare|--lan|--local`; lời mời lấy địa chỉ theo `GH_PUBLIC_URL`; dòng 7 "Truy cập từ xa" ở Việc Sếp cần làm.
- Chặn dò mật khẩu 10 lần/15 phút (theo IP và email), phiên tối đa 30 ngày (F-1, F-3).
- Chi tiết: [docs/releases/v0.1.46.md](docs/releases/v0.1.46.md)

## v0.1.45 — Khoá cấu hình nhạy cảm & vệ sinh bảo mật (03/10/2026)
- Mã PIN chỉ ở đúng đường hạ rào (mức tự trị, điều cấm, giới hạn, tool MCP ghi, tài khoản CLI) (F-20, F-60).
- MCP và nhà cung cấp AI ghim DNS, cấm địa chỉ nội bộ, có token thì phải https (F-49); nhật ký MCP chỉ lưu dấu vết (F-57).
- Hộp thư `run/` 2770, mật khẩu pg_dump qua `PGPASSWORD`, WebSocket kiểm Origin, cờ "Đáng ngờ" cho điểm nhân sự (F-52, F-54, F-55, F-56, F-58).
- Chi tiết: [docs/releases/v0.1.45.md](docs/releases/v0.1.45.md)

## v0.1.44 — Kênh Telegram tới Sếp + Trực canh máy chủ + Gói chẩn đoán (03/10/2026)
- Trực canh `gen-harness-watchdog` mỗi 12 phút, tự khởi động lại dịch vụ chết, báo Telegram chống spam (F-6).
- Bot Telegram của Sếp ở Kết nối › Telegram nhận Bản tin 07:30/17:30 và nhắc việc, một chiều (F-8).
- `X-Request-ID` + "Mã yêu cầu" cạnh mã ERR; Gói chẩn đoán ở Trợ giúp, đã lọc bí mật (F-4).
- Chi tiết: [docs/releases/v0.1.44.md](docs/releases/v0.1.44.md)

## v0.1.43 — Bỏ lời hứa không thật & chữ khó hiểu (03/10/2026)
- Bỏ "Dùng dữ liệu mẫu" ở bước 1; gán model chỉ còn 3 khoá lõi; màn trống dẫn đường (F-23, F-25, F-29).
- Thang tự trị 3 mức ở giao diện, Lọc tin Thấp/Vừa/Cao, ẩn độ tin cậy trên thẻ Hộp thư (F-30, F-62).
- Chuẩn hoá chữ dùng chung (`gh/textnorm.py`), nháp tin Gen ghi rõ "chưa gửi" (F-38, F-24).
- Chi tiết: [docs/releases/v0.1.43.md](docs/releases/v0.1.43.md)

## v0.1.42 — "Chế độ Boss": một menu gọn theo việc (03/10/2026)
- Thanh bên 6 mục + "Nâng cao" thu gọn, không có công tắc hay cây menu thứ hai (F-7, F-26).
- Trang Kết nối một trang với trạng thái thống nhất; Đội ngũ; Cài đặt lọc tab theo quyền (F-61, F-63–F-67).
- Mỗi thẻ một chỗ; Plugin ẩn và đóng băng (F-41).
- Chi tiết: [docs/releases/v0.1.42.md](docs/releases/v0.1.42.md)

## v0.1.41 — Gen trợ lý thật, lát 1 (03/10/2026)
- Gen nhớ hội thoại qua tải lại; Bản tin Gen 07:30/17:30; nút Hữu ích / Không hữu ích (F-8).
- Việc nền mặc định chỉ dùng khoá API; Claude Code CLI chỉ khi Owner bật (cảnh báo + PIN) (F-86, QD-12).
- Chi phí AI ₫/ngày theo agent có trần; mẫu nhà cung cấp OpenRouter (F-84); migration 0027.
- Chi tiết: [docs/releases/v0.1.41.md](docs/releases/v0.1.41.md)

## v0.1.40 — Dữ liệu an toàn: bản sao ngoài máy + hạn lưu thật (03/10/2026)
- `genh offsite`: bản sao mã hoá hằng tuần ra USB/NAS, tự kiểm gói, Bộ khôi phục, "Tải gói mang đi" (F-12).
- Hạn lưu dữ liệu thật (`gh/retention.py`); job nặng không chậm dần, job quá giờ có chuông (F-2, F-16).
- Bỏ S3/MinIO giả; `genh uninstall` mặc định giữ dữ liệu; migration 0026.
- Chi tiết: [docs/releases/v0.1.40.md](docs/releases/v0.1.40.md)

## v0.1.39 — Kết nối chạy thật cùng Boss (02/10/2026)
- Trang "Việc Sếp cần làm" (Gen-hub, Facebook, Google/agy, Claude Code CLI, Jev); kết quả lưu ở `ops.boss_checks` (F-74, F-76, F-77, F-78).
- Mạng xã hội trên thanh bên, một tên "Hướng dẫn thiết lập", lỗi Gen-hub có mã thống nhất (F-31, F-32, F-28).
- Migration 0025; `GET /boss-checks` để Claude tự đọc kết quả.
- Chi tiết: [docs/releases/v0.1.39.md](docs/releases/v0.1.39.md)

## v0.1.38 — Cô lập Antigravity CLI + gói chuyển máy (02/10/2026)
- agy chạy cwd rỗng 0700, env sạch, prompt qua stdin; chỉ dùng cho Gen của Sếp (F-22).
- Phiên Claude Code sang volume `claude_state`; canary offline "không lộ".
- Gói chuyển máy mã hoá lại phiên mạng xã hội (F-17).
- Chi tiết: [docs/releases/v0.1.38.md](docs/releases/v0.1.38.md)

## v0.1.37 — Cập nhật tự lành (02/10/2026)
- Khoá loại trừ `genh.lock`, bắt SIGTERM, rollback không bị huỷ; Console hiện "bị dừng giữa chừng" + Thử lại (F-34, F-73).
- Tải binary genh theo thời gian rảnh, thử lại 3 lần (F-72); kiểm linger và `docker.service` (F-73); E2E nâng cấp nhiều bản (F-35).
- Chi tiết: [docs/releases/v0.1.37.md](docs/releases/v0.1.37.md)

## v0.1.36 — Hệ thống tự báo khi hỏng + sao lưu chắc (02/10/2026)
- Chuông sự cố khử trùng lặp, `GET /system/health`, dải "Cần Sếp xử lý" (F-6, F-3, F-4).
- Sao lưu timeout 3600 giây, bị huỷ thì báo; log JSON có error_id; cron theo giờ VN (F-45, F-46, F-2).
- Chi tiết: [docs/releases/v0.1.36.md](docs/releases/v0.1.36.md)

## v0.1.35 — Sửa lỗi đỏ trong ứng dụng (02/10/2026)
- Giao việc / gán người / gán BOT dùng người và trợ lý thật (`/pickers/*`) (F-1).
- Tài liệu không phải PDF/ảnh buộc tải xuống + CSP sandbox; Sổ tay theo quyền Kho (F-5, F-15).
- PIN cho nhà cung cấp AI; lỗi thân thiện có mã; e2e thật rút gọn trong CI (F-20, F-43, F-14).
- Chi tiết: [docs/releases/v0.1.35.md](docs/releases/v0.1.35.md)

## v0.1.34 — `genh update` an toàn (02/10/2026)
- Tải bản mới trước khi sao lưu; chỉ khôi phục CSDL khi đã migrate; bản lỗi tự quay về bản cũ (F-10, F-33).
- Đã mới nhất thì không sao lưu/tải; kiểm đĩa, dọn ảnh cũ, giới hạn log 10 MB × 3 (F-11, F-37).
- E2E có dữ liệu mẫu và job bản hỏng cố ý (F-35).
- Chi tiết: [docs/releases/v0.1.34.md](docs/releases/v0.1.34.md)

## v0.1.33 — Cổng phát hành & CI đủ test (02/10/2026)
- CI chạy trước Release; Release ra dạng bản thử, E2E cài thật xanh mới nâng thành bản chính thức; lịch đêm đợi chín 24 giờ (F-9).
- CI thêm go vet/test 4 hệ điều hành, pytest dưới vai app, Playwright mock, kiểm alembic 1 head (F-13).
- Chi tiết: [docs/releases/v0.1.33.md](docs/releases/v0.1.33.md)

## v0.1.32 — Model và mức suy nghĩ tách riêng (02/10/2026)
- Model và mức suy nghĩ (effort) tách riêng cho agy và Claude Code; chuyển dữ liệu cũ (migration 0023) (F-88).
- Mỗi model có nguồn (CLI / tài liệu chính thức / "chưa xác minh"); nút Chẩn đoán CLI (chỉ Owner).
- Chi tiết: [docs/releases/v0.1.32.md](docs/releases/v0.1.32.md)

## v0.1.31 — Model CLI theo nhóm, nguồn Claude Code CLI (01/10/2026)
- Danh sách model CLI thật theo nhóm, gọi thử thật trước khi lưu; một sự thật cho trạng thái phiên CLI.
- Nguồn mới Claude Code CLI (gói Pro/Max của Owner), cảnh báo điều khoản — Owner tự quyết (QD-12).
- Chi tiết: [docs/releases/v0.1.31.md](docs/releases/v0.1.31.md)

## v0.1.30 — Sửa nóng: màn sập React #31, đổi tài khoản Google (30/09/2026)
- Sửa màn /guide/8 sập "React error #31 {reasons}" khi chưa có model (`MODEL_UNAVAILABLE`); web không vẽ đối tượng lỗi thô.
- Lối vào "Hướng dẫn thiết lập" cố định; mục "Cập nhật phần mềm" có "Kiểm tra bản mới".
- Đổi tài khoản Google của Antigravity CLI hoạt động đúng (gửi tạm tệp phiên, `409 CLI_LOGIN_IN_PROGRESS`).
- Chi tiết: [docs/releases/v0.1.30.md](docs/releases/v0.1.30.md)

## v0.1.29 — Bước 4 "Để sau" + Gen đọc Facebook cá nhân (30/09/2026)
- Bước 4 "Để sau" có hộp cảnh báo; dải "Chưa có model" ở bước 12 và Tổng quan.
- Đợt D3 lát đầu: dịch vụ `browser` + `browser-egress`, màn Tài khoản mạng xã hội, Gen chỉ đọc thông báo và hội thoại Facebook (QD-12).
- Chi tiết: [docs/releases/v0.1.29.md](docs/releases/v0.1.29.md)

## v0.1.28 — Sửa theo rà soát UX (30/09/2026)
- Bước 4 phải có model; bước 12 nói thật việc còn thiếu; lỗi kỹ thuật thành câu dễ hiểu.
- Ma trận quyền tiếng Việt, bớt ngõ cụt cho vai trò khác Owner, giao diện điện thoại (bảng → thẻ).
- Chi tiết: [docs/releases/v0.1.28.md](docs/releases/v0.1.28.md)

## Bản cũ (v0.1.1 – v0.1.27)

Một dòng mỗi bản; chi tiết ở `docs/releases/`.

- v0.1.27 (30/09/2026) — gia cố & phủ test: ghim DNS Gen-hub, hạn lưu chuông, nhắc việc chịu lỗi — [chi tiết](docs/releases/v0.1.27.md)
- v0.1.26 (30/09/2026) — Đợt D1: Gen đọc Kho dữ liệu qua Gen-hub (chỉ đọc, chỉ Owner) — [chi tiết](docs/releases/v0.1.26.md)
- v0.1.25 (29/09/2026) — Đợt C1: lọc đầu Hộp thư (trùng, rác, điểm), dùng Jev khi có — [chi tiết](docs/releases/v0.1.25.md)
- v0.1.24 (29/09/2026) — Đợt A4: Gen v2 bước 1, đề xuất thao tác có xác nhận — [chi tiết](docs/releases/v0.1.24.md)
- v0.1.23 (29/09/2026) — Đợt B4–B7: điện thoại, trang lỗi/404, chuông thông báo, sáng/tối — [chi tiết](docs/releases/v0.1.23.md)
- v0.1.22 (29/09/2026) — Đợt B1–B3: quản lý người dùng, thông tin công ty, Trợ giúp — [chi tiết](docs/releases/v0.1.22.md)
- v0.1.21 (29/09/2026) — Đợt A1–A3: Gen v1 (khung chat, dẫn đường trên UI, nguồn Jev) — [chi tiết](docs/releases/v0.1.21.md)
- v0.1.20 (29/09/2026) — sao lưu & khôi phục trên giao diện — [chi tiết](docs/releases/v0.1.20.md)
- v0.1.19 (29/09/2026) — "Tài khoản của tôi" + bắt buộc đổi mật khẩu tạm — [chi tiết](docs/releases/v0.1.19.md)
- v0.1.18 (28/09/2026) — đăng nhập lại sau cập nhật, `genh reset-password`, `trust-ca` — [chi tiết](docs/releases/v0.1.18.md)
- v0.1.17 (28/09/2026) — nút "Cập nhật ngay" trong Console — [chi tiết](docs/releases/v0.1.17.md)
- v0.1.16 (28/09/2026) — trang "Hướng dẫn kết nối" từng bước — [chi tiết](docs/releases/v0.1.16.md)
- v0.1.15 (28/09/2026) — "Để sau" thay cho "Bỏ qua" ở trình thiết lập — [chi tiết](docs/releases/v0.1.15.md)
- v0.1.14 (28/09/2026) — trình thiết lập chỉ bắt buộc bước 1–4 — [chi tiết](docs/releases/v0.1.14.md)
- v0.1.13 (28/09/2026) — cài xong trình duyệt không còn báo "Not secure" — [chi tiết](docs/releases/v0.1.13.md)
- v0.1.12 (28/09/2026) — đăng nhập Antigravity CLI trong Console ra link — [chi tiết](docs/releases/v0.1.12.md)
- v0.1.11 (28/09/2026) — ngưỡng đĩa trống 20 GB quá cao — [chi tiết](docs/releases/v0.1.11.md)
- v0.1.10 (28/09/2026) — genh dùng đúng `GEN_HARNESS_HOME` — [chi tiết](docs/releases/v0.1.10.md)
- v0.1.9 (28/09/2026) — api và worker đụng nhau khi chép volume lần đầu — [chi tiết](docs/releases/v0.1.9.md)
- v0.1.8 (28/09/2026) — cài từ bản phát hành: proxy thiếu Caddyfile — [chi tiết](docs/releases/v0.1.8.md)
- v0.1.7 (28/09/2026) — Release không còn treo ở ảnh web — [chi tiết](docs/releases/v0.1.7.md)
- v0.1.6 (28/09/2026, không có Release riêng) — lỗi phát hiện nhờ E2E cài thật — [chi tiết](docs/releases/v0.1.6.md)
- v0.1.5 (28/09/2026) — genh tự thay binary + tự cập nhật theo lịch — [chi tiết](docs/releases/v0.1.5.md)
- v0.1.4 (28/09/2026) — tự động phát hành qua `VERSION` + sửa cài Docker hỏng trên Linux — [chi tiết](docs/releases/v0.1.4.md)
- v0.1.3 (28/09/2026) — sửa mất hồ sơ khi tự nâng cấp genh — [chi tiết](docs/releases/v0.1.3.md)
- v0.1.2 (27/09/2026) — sửa sau rà soát độc lập; khoá backup riêng `GH_BACKUP_KEY` — [chi tiết](docs/releases/v0.1.2.md)
- v0.1.1 (27/09/2026, chỉ có thẻ tag) — sửa lưu trữ, hồ sơ Owner (`.ghbundle`), CSDL, RLS; mục "Lỗi cần sửa" và "Hợp đồng chung" — [chi tiết](docs/releases/v0.1.1.md)
