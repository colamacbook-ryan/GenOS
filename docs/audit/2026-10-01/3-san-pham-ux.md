# Rà soát 3 — Sản phẩm & độ liền mạch UX (nhìn toàn cảnh) · Gen-Harness v0.1.31

Ngày 01/10/2026 · Bản rà: snapshot `origin/main` = v0.1.31 (`audit/src`) · Chỉ đọc, không sửa repo.
Đường dẫn trong báo cáo tính từ gốc repo (`apps/…`, `deploy/…`).

**Không lặp lại:** phần chọn model ở bước 4 (model với "mức suy nghĩ", nút chẩn đoán) đang sửa ở v0.1.32, coi như đã xử lý.
Rà soát UX có ảnh chụp trước đó (`scratchpad/ux-audit/ux-audit.md`, bản v0.1.27) không làm lại. Mục nào ở đó **vẫn còn** thì chỉ nhắc ngắn.

**Cách làm:** đọc router, thanh bên, ma trận quyền, các màn, Gen (web + API), thông báo, cài đặt. Chạy giao diện ở chế độ mock
(`vite --mode mock`, `MOCK_UPDATE=1`) rồi chụp 16 ảnh mới vào `audit/shots/` (Owner, Manager, Operator, 1440px và 375px).
Đã gỡ `node_modules` và bộ đệm `.vite` tạm khỏi snapshot. Repo chính không bị đụng tới.

---

## Tóm tắt nhanh

1. **Có lỗi "giả" chạy trên app thật.** Ba hộp thoại giao việc/gán người (Hộp thư, Deal & Vụ việc, Nhóm & Con người) đang
   dùng danh sách người và agent **viết cứng từ dữ liệu mock** ("Chị Lan Phạm", "Anh Minh Kiểm", "Trợ lý thương mại"…), mã
   không phải UUID. Ở hệ thống thật, API sẽ từ chối các mã này. Lỗi cùng họ với các lỗi Boss vừa gặp: test chạy trên mock thì xanh, máy thật thì hỏng.
2. **Menu xếp theo kiến trúc kỹ thuật, không theo việc của Boss.** Có 25 màn, thêm 7 tab và 5 trang nằm ngoài danh mục. Mọi việc quản trị
   (nối AI, Gen-hub, Telegram, mời người, sao lưu, cập nhật) đều nằm trong "Kỹ thuật · Backend". Việc "kết nối" bị rải ra 5 nơi.
   Có 4 thẻ cài đặt bị lặp ở 2–4 chỗ.
3. **Hệ thống hỏng mà không báo.** Kênh rớt, phiên WhatsApp hết hạn, model lỗi, có bản mới: không cái nào vào chuông thông báo.
   Gen cũng không chủ động nói. Như vậy chưa đạt mục tiêu "tự lo".
4. **Gen mới là "trợ lý trong khung chat".** Gen đọc được 20 nguồn và đề xuất 3 loại việc có xác nhận. Gen chưa: chủ động báo, nhắc
   Boss ngoài Console, nhớ giữa các phiên, giữ lịch sử khi tải lại trang, gửi/trả lời thật (Facebook chỉ đọc 6 lượt/ngày, Kho chỉ đọc).
5. **Vài lời hứa trên giao diện không thật:** nút "Dùng dữ liệu mẫu" không làm gì; 4 dòng gán model chưa nối vào đâu; Gen báo "Đã xác nhận"
   nháp tin nhưng tin chưa được gửi.

---

## (a) Bản đồ màn hình & hành trình

### a1. Các màn và lối vào

Nguồn danh mục: `packages/contracts/src/screens.ts:64-230` (web) và `apps/api/gh/shell/navigation.py:24-66` (API, lọc theo quyền).

| Khu | Màn (đường dẫn) | Lối vào | Ai thấy (mặc định) | Ghi chú |
|---|---|---|---|---|
| Kinh doanh | Tổng quan điều hành `/overview` | thanh bên, trang mặc định sau đăng nhập | Owner, Manager, Operator, Auditor (**không** có Nhân viên phụ trách) | 11 thẻ số, có 2 thẻ trùng nhau (xem V4) |
| | › Hàng đợi & Hành động: Hộp thư ý nghĩa `/inbox`, Bàn làm việc `/workbench`, Việc & Nhắc hẹn `/tasks` | thanh bên | mọi vai trò có `queue.read` | Hộp thư có hộp giao việc dùng danh sách giả (Đ1) |
| | Nhóm & Con người `/directory` | thanh bên | profile.read | "Gán BOT" dùng danh sách agent giả (Đ1) |
| | Tài liệu `/documents` | thanh bên | profile.read | Gen không đọc được |
| | Bản đồ quan hệ `/graph` › Hồ sơ sống `/profile`, Sổ tay nhận thức `/notebook` | thanh bên | profile.read | "Hồ sơ sống" là màn chi tiết nhưng đặt ở menu → "Chưa chọn hồ sơ" (V5) |
| | › Cơ hội & Thị trường: Bảng cơ hội, Cung ↔ Cầu, Kho hội thoại `/search`, Deal & Vụ việc `/deals` | thanh bên. Nút kính lúp ở header "Tìm theo ý định" mở `/search` | opportunity.read | Tên nút ≠ tên màn (V2) |
| | › Con người & Chất lượng: Đánh giá con người `/people`, Chất lượng chăm sóc `/care` | thanh bên | chỉ Owner | "Con người" ở đây là **nhân viên**, ở "Nhóm & Con người" lại là **khách** (a3) |
| Kỹ thuật · Backend | › Tầng dữ liệu: Kho dữ liệu thô `/raw`, Quy tắc sàng lọc `/rules`, Kho sạch SSOT `/clean`, Hợp nhất danh tính `/identity` | thanh bên | Owner, Auditor | |
| | › Agent & Model: Danh tính Agent `/agents`, API & Model `/api`, MCP Hub `/mcp` | thanh bên | Owner, Auditor | **Thẻ Gen-hub nằm giữa MCP Hub** (`apps/web/src/screens/mcp/McpScreen.tsx:99`) |
| | Plugin & Tiện ích `/plugins` | thanh bên | Owner, Auditor | Cài Telegram ở đây |
| | Điều khiển hệ thống `/system` (7 tab: Kênh & đăng nhập · Bộ não AI · Quyền hạn · Người dùng · Tổ chức · Nhật ký · Dữ liệu & lưu trữ) | thanh bên | Owner, Auditor, **Manager** (nhờ `audit.read`, `apps/api/gh/auth/rbac.py:116`) | Mời người, sao lưu, cập nhật, đổi tài khoản Google đều ở đây |
| Ngoài danh mục | Hướng dẫn thiết lập `/guide`, `/guide/:n` | mục cố định ở thanh bên + menu tài khoản + Trợ giúp + thẻ ở Tổng quan (chỉ Owner) | Owner | Header không có tiêu đề (không có `handle`, `apps/web/src/router.tsx:74-75`) |
| | Tài khoản của tôi `/account` | menu tài khoản | mọi người | Đổi PIN (trùng với tab Kênh) |
| | Trợ giúp `/help` | menu tài khoản | mọi người | Lại có thêm một thẻ Cập nhật |
| | **Tài khoản mạng xã hội `/social`** | **chỉ có ở menu tài khoản** (`apps/web/src/shell/AccountFooter.tsx:136-149`) | Owner | Breadcrumb ghi "KẾT NỐI", một khu không có trên thanh bên (`apps/web/src/shell/routeHandles.ts:15`) |
| | Thiết lập `/setup` (12 bước), Đăng nhập `/login`, Đặt mật khẩu mới `/change-password` | luồng riêng | | |

**Màn mồ côi / chỉ vào được bằng đường phụ:** `/social` (Facebook) chỉ có trong menu tài khoản, không ở thanh bên, không ở Hướng dẫn,
không ở danh sách kênh. Gen-hub không có lối riêng, phải biết vào MCP Hub. `/guide/4` (chọn model sau khi "Để sau") chỉ mở được từ dải "Chưa có model".
Lịch sử hội thoại Gen có API (`apps/api/gh/gen/routes.py:70-84`) nhưng giao diện không có lối vào: `loadConversation` không được gọi ở đâu
(`apps/web/src/gen/genClient.ts:99`). Cài đặt Gen (bật cho vai trò khác, thời gian giữ hội thoại) có API (`apps/api/gh/gen/routes.py:47-57`) nhưng không có giao diện.

### a2. Một việc, nhiều chỗ làm (lặp)

| Việc | Đang nằm ở | Bằng chứng |
|---|---|---|
| Tài khoản Google/CLI cho AI | Hệ thống › Kênh, Hệ thống › Bộ não AI, API & Model (2 thẻ), bước 4 | `screens/system/SystemScreen.tsx:108`, `screens/system/BrainTab.tsx:135`, `screens/api/ApiScreen.tsx:95-96`, `setup/Step4Brain.tsx:337` |
| Chọn/gán model, thứ tự nguồn | bước 4 + `/guide/4`, API & Model, Hệ thống › Bộ não AI (chỉ đọc) | `ApiScreen.tsx:416` gọi là "Chuỗi ưu tiên", `BrainTab.tsx:93` gọi là "Chuỗi chuyển hướng" |
| Mã PIN | Tài khoản của tôi, Hệ thống › Kênh | `account/AccountPage.tsx:71`, `SystemScreen.tsx:74,107` |
| Cập nhật phần mềm | Tổng quan (chỉ khi có bản mới), Trợ giúp, Hệ thống › Dữ liệu & lưu trữ | `screens/queue/OverviewScreen.tsx:162`, `help/HelpPage.tsx:92`, `screens/system/StorageTab.tsx:30` |
| Hướng dẫn thiết lập | thanh bên, menu tài khoản, Trợ giúp, thẻ Tổng quan | `shell/Sidebar.tsx:84-109`, `AccountFooter.tsx:162-175`, `HelpPage.tsx:94-101`, `SetupFollowUp.tsx:36-39` |
| Lọc tin | Quy tắc sàng lọc (Tầng dữ liệu) + "Lọc đầu Hộp thư" (Hệ thống › Bộ não AI) + Jev | `screens/system/TriageCard.tsx:27` |
| Kênh | bước 5, Hệ thống › Kênh, Plugin (Telegram), `/social` (Facebook) | |
| Mức tự trị | bước 9, Danh tính Agent, Nhóm & Con người, viên thuốc "tự trị 4" ở header | nhãn khai 3 lần: `shell/headerModel.ts:2-10`, `screens/agents/agentsModel.ts:13-21`, `screens/relations/relationsModel.ts:66` |
| Cây danh mục | web `screens.ts` **và** API `navigation.py` | `screens.ts:5` tự ghi "không khai báo hai nơi" nhưng thực tế khai hai nơi |

Mỗi bản sao là một chỗ dễ lệch nhau. Các lỗi gần đây đều thuộc loại này: đổi tài khoản Google, nút cập nhật biến mất, hướng dẫn biến mất.

### a3. Cùng một khái niệm, nhiều tên gọi

| Khái niệm | Các tên đang dùng | Đề xuất |
|---|---|---|
| Trang hướng dẫn | "Hướng dẫn thiết lập" (`Sidebar.tsx:101`, `AccountFooter.tsx:173`), "Hướng dẫn kết nối" (`guide/GuidePage.tsx:34`, `HelpPage.tsx:95-98`), "Hướng dẫn từng bước" (`SetupFollowUp.tsx:38`, `setup/Step10Team.tsx:119`) | "Hướng dẫn thiết lập" |
| Trợ lý AI trên kênh | Agent, BOT (`relations/DirectoryScreen.tsx:160` "Gán BOT trực nhóm"), "nhân viên AI" (`guide/guideContent.ts:75`), "trợ lý AI" (`setup/steps.ts:87`), core agent | "Trợ lý kênh" (Gen là trợ lý của Sếp) |
| Thực thể AI | Gen, Agent, core agent, Jev, Bộ não AI, nhà cung cấp, nguồn, model, CLI | Boss chỉ cần 2 tên: **Gen** và **Bộ não AI**. Jev và core agent đưa vào Nâng cao |
| "Con người" | khách/đối tác (Nhóm & Con người) và nhân viên (Đánh giá con người) | "Khách & Nhóm" / "Nhân viên" |
| Tìm kiếm | nút "Tìm theo ý định" (`shell/Header.tsx:85`) mở màn "Kho hội thoại" | một tên |
| Công tắc mạng công cộng (Gen-hub) | "Cho phép Gen-hub ở mạng công cộng" (`screens/mcp/HubLinkCard.tsx:111`) và câu lỗi "Cho phép máy chủ MCP ngoài mạng nội bộ" (`apps/api/gh/chassis/mcp_client.py:157`) | một tên |
| Việc mời người | bước 10 nói "Điều khiển hệ thống › Người dùng" (`steps.ts:89`, đúng), Hướng dẫn việc 10 dẫn sang tab **Quyền hạn** (`guideContent.ts:115`, sai), bước 10 lại nói "Tổng quan › Hướng dẫn từng bước" (`Step10Team.tsx:119`) | một chỗ: Người dùng |

**Tiếng Anh / chữ kỹ thuật còn sót:**
- Tooltip thanh bên **luôn** kèm tiếng Anh, kể cả khi đã tắt "Phụ đề tiếng Anh" (`shell/navModel.ts:126-128`, `Sidebar.tsx:187`). Ví dụ: "Plugin & Tiện ích — DSH base plugins & external add-ons".
- Nhiều chữ nằm ngay trên giao diện:
  - "Kỹ thuật · Backend"
  - "Kho sạch SSOT" (`screens/data/dataModel.ts:143`, `navigation.py:55`)
  - "Tự làm việc đã whitelist"
  - "Giọng / persona"
  - "Template tùy chọn"
  - "Pipeline đang mở"
  - "Plugin nền DSH"
  - "Endpoint"
  - "đơn vị ý nghĩa" (`screens/queue/InboxScreen.tsx:96`)
  - logo vẫn ghi "v2.2" (`shell/Logo.tsx:37`)

### a4. Các hành trình chính, đi như Boss

| # | Hành trình | Bước tiếp có rõ không | Trạng thái rỗng/lỗi/đang tải | Cần kiến thức kỹ thuật? | Đánh giá |
|---|---|---|---|---|---|
| 1 | Cài → thiết lập 12 bước | Rõ. Từ v0.1.28–30, bước 12 nói thật việc còn thiếu (`setup/Step12Finish.tsx:113-141`) | Tốt | Bước 1 "Dùng dữ liệu mẫu" là **lời hứa rỗng** (C1). Bước 5–11 vẫn nhiều khái niệm (chế độ nghe, ngưỡng, trọng số, tự trị 0–6) | 🟢/🟠 |
| 2 | Nối bộ não / model | Dải "Chưa có model" → `/guide/4` rõ | Tốt (đã dịch lỗi) | Cấu hình đầy đủ nằm ở API & Model: 9 dòng gán model, trong đó **4 dòng không có tác dụng** (C3). Tài khoản Google ở 4 nơi | 🟠 |
| 3 | Dùng hằng ngày (Tổng quan → Hộp thư → Bàn làm việc → Việc; Gen; chuông) | Tổng quan có "Hàng đợi cần xử lý" dẫn đúng màn | Ngày đầu: 15 màn kinh doanh trống, 47/87 trạng thái rỗng chỉ có tiêu đề, không màn nào nói "chưa nối kênh" (C7) | "Đơn vị ý nghĩa", "điểm lọc đầu" và "độ tin cậy" | 🟠. **Giao việc/gán người hỏng trên máy thật** (Đ1) |
| 4 | Nối Gen-hub | Không có lối: phải biết vào Kỹ thuật › Agent & Model › MCP Hub, rồi kéo qua "Rào chắn khoá cứng" (ảnh `shots/own-mcp-genhub.png`) | Có trạng thái và lỗi rõ | Có: địa chỉ MCP, token, ngày hết hạn (ô ngày `mm/dd/yyyy`), công tắc "mạng công cộng" **mặc định tắt** (`apps/api/gh/hub_link/service.py:176`) trong khi địa chỉ gợi ý là Internet (`HubLinkCard.tsx:99`). Phải "Lưu" xong mới bấm được "Kiểm tra" (`HubLinkCard.tsx:123`) | 🟠 (C9) |
| 5 | Nối Facebook | Chỉ có ở menu tài khoản. Không ở thanh bên, không ở Hướng dẫn, không ở danh sách kênh | Trang rất rõ: chỉ đọc, luật cứng, nút dừng khẩn, "Hỏi Gen" (ảnh `shots/own-social.png`) | Ít | 🟢 nội dung / 🟠 lối vào |
| 6 | Cập nhật phần mềm | Thẻ cố định ở Trợ giúp và Hệ thống. Thẻ Tổng quan chỉ hiện khi đã biết có bản mới (`update/UpdateCard.tsx:86`) | Tốt | Không. Nhưng **không có thông báo chuông** khi có bản mới. Ghi chú phát hành vẫn là tiêu đề PR ("feat(providers): …") | 🟢/🟡 |
| 7 | Mời nhân viên | Mời ở Hệ thống › Người dùng. Lời nhắn chép sẵn gồm địa chỉ, email, mật khẩu tạm | Tốt | Địa chỉ trong lời mời là `window.location.origin` (`screens/system/usersModel.ts:26`). Mặc định Console là `https://localhost:8443` (`deploy/proxy/Caddyfile:6`). Nhân viên ở máy khác **có thể không vào được**, app không hướng dẫn gì (C5, cần kiểm trên máy thật) | 🟠 |
| 8 | Nhân viên đăng nhập lần đầu | Bắt đổi mật khẩu → `/overview` | Vai trò "Nhân viên phụ trách" **không có quyền Tổng quan** (`rbac.py:63`) nhưng vẫn bị đưa vào đó (`lib/safeNext.ts:3`, `router.tsx:71`), nên màn đầu tiên là ổ khoá | | 🟠 (C4) |

### a5. Mỗi vai trò thấy gì (theo `apps/api/gh/auth/rbac.py:61-80`, `:95-121`)

| Vai trò | Số màn | Màn đầu tiên | Ngõ cụt còn lại |
|---|---|---|---|
| Owner | 25 + 5 trang | Tổng quan | Không có |
| Manager | 13 kinh doanh + Điều khiển hệ thống | Tổng quan | Vào Hệ thống: tab mặc định "Kênh" bị khoá, 6/7 tab bị khoá. Tab Bộ não AI có **5 thẻ "Không tải được dữ liệu"**, mỗi thẻ một nút "Thử lại" vô ích, cộng nút "Mở API & Model" dẫn tới màn họ không xem được (`BrainTab.tsx:19-40`, ảnh `shots/mgr-system-brain.png`, `shots/mgr-system-default.png`) |
| Operator | 13 kinh doanh | Tổng quan (thấy cả số liệu kỹ thuật: plugin, độ trễ) | Hộp thoại giao việc dùng danh sách giả |
| Nhân viên phụ trách | 12 kinh doanh | **Ổ khoá "không có quyền xem màn này"** | Như trên |
| Auditor | 21 (gồm toàn bộ màn kỹ thuật, chỉ xem) | Tổng quan | Không có |
| Gen | Chỉ Owner (`apps/api/gh/gen/store.py:18`). Không có giao diện để bật cho người khác | | |

### a6. Gen làm được gì thật (so với điều giao diện ngụ ý)

| Giao diện nói / ngụ ý | Thực tế v0.1.31 |
|---|---|
| "Hỏi về tình hình hôm nay" (`gen/GenPanel.tsx:155`) | ✅ 20 công cụ đọc: tổng quan, hộp thư, nháp, tìm kiếm, hồ sơ, cơ hội, chăm sóc, nhật ký, sức khoẻ, hướng dẫn, việc, nhân sự, lọc đầu, Kho ×3, mạng xã hội ×2 (`apps/api/gh/gen/tools.py:46-94`). ❌ Không đọc Tài liệu, Deal & Vụ việc, Cung–Cầu |
| "Chỉ chỗ bấm" | ✅ Mở màn, khoanh sáng, dẫn từng bước |
| "Soạn nháp tin" | ⚠️ Chỉ tạo nháp ở Bàn làm việc. Mọi tin gửi ra ngoài đều phải duyệt thêm một lần nữa (`apps/api/gh/biz/core/drafts.py:1-6`). Thẻ lại báo "**Đã xác nhận** · ACT-…" (`gen/ProposalCard.tsx:204-207`) nên dễ hiểu là đã gửi (C2) |
| "Đặt nhắc việc" | ⚠️ Tạo được, nhưng đến giờ chỉ báo ở **chuông trong Console** (`apps/api/gh/biz/queue/jobs.py:236`). Không có Zalo/Telegram/điện thoại |
| "Giao người" | ✅ Dùng danh sách người thật (`/gen/assignees`). Ngược lại, màn Hộp thư giao người bằng danh sách giả |
| "Facebook có gì mới?" (`social/SocialPage.tsx`) | ⚠️ Chỉ đọc, tối đa 6 lượt/ngày/tài khoản (`apps/api/gh/social/service.py:7`). Không trả lời, không đăng |
| Gen-hub / Kho tri thức | ⚠️ Chỉ đọc (tóm tắt/tìm/xem). Không ghi Việc hay ghi chú vào Kho |
| Trí nhớ | ❌ Chỉ nhớ 10 tin gần nhất **trong một hội thoại** (`apps/api/gh/gen/engine.py:39`). Tải lại trang là mất hội thoại (`gen/genStore.ts:65` chỉ lưu trạng thái mở/đóng). Không nhớ thói quen của Sếp |
| Chủ động | ❌ Không có bản tin sáng/tối. Không tự báo khi có cơ hội nóng, kênh rớt hay nháp chờ lâu |
| Kênh với Boss | ❌ Chỉ có khung chat trong Console. Không chat với Gen qua Zalo/Telegram dù hệ thống đã có kênh |

**Những chỗ đang chặn Gen thành "trợ lý thật":**
1. Không có đường đi tới Boss khi Boss không mở Console.
2. Không chủ động theo lịch.
3. Không có trí nhớ dài hạn và lịch sử hội thoại.
4. Hành động chưa khép vòng: duyệt và gửi, trả lời Facebook, ghi Kho.
5. Phạm vi dữ liệu còn thiếu: tài liệu, deal; email và lịch có thể nối qua MCP.

---

## (b) Điểm mạnh

- **Quyền hạn đi từ một nguồn API**: danh mục lọc theo vai trò (`navigation.py:83-107`). Màn không có quyền thì báo rõ, không vỡ trang. Trang 404 và trang lỗi nằm trong khung Console.
- **An toàn đặt đúng chỗ:**
  - Gen không tự ghi gì: mọi đề xuất phải xác nhận, việc nhạy cảm cần PIN, có Nhật ký hành động.
  - Tin gửi ra ngoài luôn qua duyệt.
  - Mạng xã hội có luật cứng, công tắc dừng khẩn và chấp nhận rủi ro theo từng tài khoản.
  - Nội dung Kho được che trước khi đưa vào model.
- **Các bản vá v0.1.28–0.1.31 đi đúng hướng:**
  - Bước 12 nói thật việc còn thiếu.
  - Dải "Chưa có model" kèm nút sửa.
  - Hướng dẫn và Cập nhật có lối vào cố định.
  - Trợ giúp hiển thị theo vai trò.
  - Lỗi kỹ thuật được dịch thành câu dễ hiểu (`lib/friendlyError.ts`). Lỗi luôn hiện dạng chữ, không còn kiểu #31.
  - Mời người có sẵn lời nhắn để chép.
- **"Để sau" thông minh**: Việc thiết lập tiếp tự đánh dấu xong dựa trên dữ liệu thật, không cần bấm tay (`SetupFollowUp.tsx:11-15`).
- **Trang Mạng xã hội** là mẫu tốt về cách viết cho Boss: nói rõ "CHỈ ĐỌC", nêu rủi ro, có nút "Hỏi Gen" ngay trên trang.
- Điện thoại 375px và chế độ tối dùng được (đã kiểm ở lần rà trước, ảnh mới `shots/m-own-social.png`).

---

## (c) Phát hiện

Thang effort: **S** ≤ 1 ngày · **M** 2–5 ngày · **L** > 1 tuần.

### 🔴 Đỏ — chặn giá trị cốt lõi hoặc gây hiểu sai

**Đ1. Hộp thoại giao việc và gán trợ lý dùng dữ liệu mock viết cứng, nên hỏng trên hệ thống thật.**
- Bằng chứng:
  - `screens/queue/InboxScreen.tsx:25-30` và `:234` (Hộp thư › "Giao cho người khác"): `u-lan` "Chị Lan Phạm", `u-minh` "Anh Minh Kiểm", `u-me` "Tôi".
  - `screens/market/DealsScreen.tsx:21-26` và `:239` (Vụ việc › "Gán người xử lý"): cùng danh sách trên.
  - `screens/relations/DirectoryScreen.tsx:28-34` và `:183, :403, :462` ("Gán BOT trực nhóm"/người): `agent-tls`, `agent-ka`…
  - API đòi UUID: `apps/api/gh/biz/queue/routes.py:226-227`, `apps/api/gh/biz/relations/routes.py:101-103`, `apps/api/gh/biz/market/routes.py:878-880`.
  - Mock lại dùng đúng các mã giả này (`apps/web/test/mock-p3-relations.ts:70-74`, `mock-p3-market.ts:230`), nên e2e vẫn xanh.
  - Chú thích code còn ghi "tạm — chưa có màn Danh mục người dùng (GĐ 4)". Màn Người dùng đã có từ v0.1.22.
- Ảnh hưởng tới Boss: hộp thoại hiện tên người **không có trong công ty**, bấm vào thì báo lỗi. Không giao được việc, không gán được trợ lý cho nhóm. Đây là vòng "quản lý đội" cốt lõi.
- Cách sửa:
  - Lấy danh sách người từ `/gen/assignees` hoặc `/users`, danh sách trợ lý từ `/agents`. Có "Tôi" là chính người đang đăng nhập.
  - Đưa mock về UUID và cho mock kiểm UUID như API thật.
  - Thêm một kiểm tra tĩnh: cấm `{ id: 'u-…' }` và `agent-…` trong `src/`.
- Effort: **S**.

**Đ2. Hệ thống hỏng mà không báo cho Boss (trái mục tiêu "tự lo").**
- Bằng chứng:
  - Chuông chỉ có 8 loại thông báo: đổi vai trò, mở khoá, đặt lại mật khẩu (`apps/api/gh/auth/users.py:172,202,217`); đọc/tạm dừng mạng xã hội (`apps/api/gh/social/service.py:637,706`); sao lưu xong/lỗi (`apps/api/gh/backup.py:407`); token Gen-hub sắp hết hạn (`apps/api/gh/hub_link/service.py:512`); nhắc việc (`apps/api/gh/biz/queue/jobs.py:236`).
  - **Không có** thông báo cho: kênh rớt hoặc phiên hết hạn, model lỗi hoặc hết hạn mức, đăng nhập CLI hết hạn, có bản mới, nháp chờ duyệt quá lâu, lần sàng lọc lỗi.
  - Sự kiện `channel.status` chỉ cập nhật bộ đệm, không báo gì (`lib/realtime.ts:323-342`). Ảnh `shots/own-system-channels.png`: WhatsApp "Phiên hết hạn — Ngừng nhận tin từ 15:05", chỉ thấy khi tự vào Hệ thống › Kênh.
- Ảnh hưởng tới Boss: dữ liệu ngừng chảy về mà Boss không biết. Biết có bản mới chỉ khi tình cờ mở Tổng quan. Đúng kiểu "nút cập nhật biến mất" Boss từng gặp.
- Cách sửa:
  - Thêm một job "Sức khoẻ cần Sếp": mỗi sự cố sinh **một** thông báo chuông (có khử trùng lặp) kèm liên kết sửa.
  - Thêm thẻ "Cần Sếp xử lý" ở đầu Tổng quan, gộp dải Chưa có model, thẻ Cập nhật và kênh rớt.
  - Giai đoạn 2: gửi cùng nội dung đó qua kênh của Boss (Telegram/Zalo) — xem Đ4.
- Effort: **M**.

**Đ3. Menu xếp theo kiến trúc kỹ thuật, việc quản trị của Boss nằm trong "Kỹ thuật · Backend".**
- Bằng chứng:
  - Có 25 màn, 7 tab Hệ thống và 5 trang ngoài danh mục.
  - Mô tả màn Hệ thống ghi "Việc hằng ngày không cần vào đây" (`screens.ts:227`). Thực tế mời người, sao lưu, cập nhật, đổi tài khoản Google, xem kênh rớt đều phải vào đây.
  - Việc "kết nối" rải ở 5 nơi:
    - Zalo/WA/Telegram/LinkedIn: Hệ thống › Kênh
    - cài Telegram: Plugin
    - Facebook: menu tài khoản
    - Gen-hub: MCP Hub
    - tài khoản AI: 4 chỗ
  - 4 thẻ bị lặp (a2) và thuật ngữ không thống nhất (a3).
- Ảnh hưởng tới Boss: phải nhớ "cái gì nằm ở đâu". Mỗi chỗ lặp là một chỗ dễ lệch. Đây là gốc của chuỗi lỗi v0.1.28–31: hướng dẫn mất, nút cập nhật mất, đổi tài khoản Google không ăn.
- Cách sửa: thêm "Chế độ Boss" (mặc định) cho thanh bên, xem khuyến nghị 1. Không cần sửa backend, chỉ sắp lại menu và gom các thẻ đã có.
- Effort: **M**.

**Đ4. Gen chưa phải trợ lý thật: chỉ trả lời trong khung chat Console.**
- Bằng chứng: bảng a6. Không chủ động. Nhắc việc chỉ vào chuông. Mất hội thoại khi tải lại (`genStore.ts:65`, `genClient.ts:99` không được dùng). Không trí nhớ dài hạn (`engine.py:39`). Mạng xã hội và Kho chỉ đọc. Không có kênh chat với Gen ngoài Console.
- Ảnh hưởng tới Boss: Boss muốn "Gen quản lý thông tin và tương tác thay mình". Hiện Boss vẫn phải mở máy, vào Console, tự hỏi, rồi tự đi duyệt ở màn khác.
- Cách sửa: theo thứ tự giá trị/công sức ở khuyến nghị 4. Bước gần nhất: lịch sử hội thoại (S), "Bản tin Gen" sáng/tối (M), nhắc việc và bản tin qua Telegram/Zalo tới chính Boss (M–L).
- Effort: **L** (chia thành các lát S/M).

### 🟠 Cam — lệch logic, ngõ cụt, lời hứa không thật

**C1. "Dùng dữ liệu mẫu" ở bước 1 không làm gì.**
- Bằng chứng:
  - `setup/Step1Welcome.tsx:17-21` hứa "Nạp dữ liệu mẫu…; xoá được sau ở Điều khiển hệ thống".
  - Server chỉ lưu `mode` (`apps/api/gh/setup/routes.py:77, 176`). Dữ liệu mẫu chỉ nạp được bằng `make seed` (`Makefile:63-70`). Không có nút xoá dữ liệu mẫu.
- Ảnh hưởng: chọn xong vẫn thấy Console trống, tưởng hỏng.
- Cách sửa: bỏ lựa chọn. Hoặc nối `seed_demo` vào bước 1 và thêm nút "Xoá dữ liệu mẫu" ở Hệ thống.
- Effort: **S** (bỏ) / **M** (làm thật).

**C2. Gen báo "Đã xác nhận" nháp tin nhưng tin chưa được gửi.**
- Bằng chứng: `apps/api/gh/gen/proposals.py:322-328` (kết quả chỉ trỏ về Bàn làm việc), `gen/ProposalCard.tsx:204-207`, `apps/api/gh/biz/core/drafts.py:1-6`.
- Ảnh hưởng: Boss tưởng đã nhắn khách. Khách không nhận được gì.
- Cách sửa:
  - Đổi câu thành "Đã lưu nháp ACT-… — **chưa gửi**".
  - Owner có `action.approve` thì cho nút "Duyệt & gửi" (PIN) ngay trong thẻ.
- Effort: **S**.

**C3. Bảng "Gán model cho từng agent" có 4 dòng không có tác dụng.**
- Bằng chứng: `apps/api/gh/agents_api/routes.py:131-143` tự ghi `core.reply/intent/scoring/indexing` là "chỗ cấu hình trước… không tự xưng đã nối dây". Ảnh `shots/own-api-model.png` cho thấy 9 dòng.
- Ảnh hưởng: Boss gán model mà không có gì thay đổi. Màn chọn model càng rối, cùng họ với lỗi "picker hiện sai".
- Cách sửa: ẩn các dòng chưa nối. Chỉ giữ Gen, Sàng lọc và từng trợ lý kênh.
- Effort: **S**.

**C4. Người không phải Owner vẫn gặp ngõ cụt.**
- Bằng chứng: a5. `rbac.py:63` (Nhân viên phụ trách không có overview), `safeNext.ts:3`, `router.tsx:71`. `SystemScreen.tsx:33` (tab mặc định "Kênh"). `BrainTab.tsx:19-40` không kiểm quyền. `NoModelBanner.tsx:31` gọi `/setup/follow-up` mà không giới hạn Owner, nên vai trò khác nhận 403 thừa.
- Ảnh hưởng: nhân viên mới đăng nhập là gặp ổ khoá. Manager thấy cả loạt thẻ lỗi đỏ.
- Cách sửa:
  - Trang đích = màn đầu tiên trong danh mục của vai trò.
  - Ẩn tab không có quyền.
  - Manager mặc định vào tab Nhật ký.
  - Bộ não AI kiểm `system.read`.
- Effort: **S**.

**C5. Mời nhân viên nhưng có thể nhân viên không vào được.**
- Bằng chứng: `usersModel.ts:26` dùng `window.location.origin`. Proxy mặc định là `{$GH_SITE_ADDRESS:localhost}:8443` với `tls internal` (`deploy/proxy/Caddyfile:6`). README và app không có hướng dẫn truy cập từ máy khác hoặc điện thoại.
- Ảnh hưởng: lời mời trỏ về `https://localhost:8443`. Nhân viên mở ra lỗi. Boss cũng không mở được Console trên điện thoại.
- Cách sửa:
  - Nếu địa chỉ là localhost: cảnh báo ngay trong hộp mời.
  - Thêm thẻ "Địa chỉ cho nhân viên và điện thoại" ở trang Đội ngũ. `genh` có thể đặt `GH_SITE_ADDRESS` và tên miền hoặc đường hầm.
  - Cần kiểm trên máy Owner.
- Effort: **M**.

**C6. Hướng dẫn: 3 tên gọi, một liên kết sai, thiếu các việc Boss thật sự muốn làm.**
- Bằng chứng: a3. `guideContent.ts:115` dẫn việc 10 sang tab Quyền hạn trong khi mời người ở tab Người dùng. Danh sách hướng dẫn chỉ có các việc 5–11 (`guideContent.ts:26-131`), không có Facebook, Gen-hub, Telegram, truy cập từ xa.
- Ảnh hưởng: Boss theo hướng dẫn mà vào nhầm chỗ. Các kết nối quan trọng nhất không có hướng dẫn.
- Cách sửa:
  - Một tên: "Hướng dẫn thiết lập".
  - Sửa `to: '/system?tab=users'`.
  - Thêm việc 12–15: Facebook, Gen-hub, Telegram, địa chỉ cho nhân viên.
- Effort: **S**.

**C7. Trạng thái rỗng không dẫn đường.**
- Bằng chứng: 47/87 `EmptyState` chỉ có tiêu đề. Các màn kinh doanh không nói lý do trống, ví dụ `InboxScreen.tsx:93-96` "Không có đơn vị ý nghĩa…". Chỉ Tổng quan có thẻ việc thiết lập.
- Ảnh hưởng: ngày đầu 15 màn trống. Boss không biết là do chưa nối kênh.
- Cách sửa: dùng một `EmptyState` chung theo ngữ cảnh. Nếu 0 kênh hoạt động: "Chưa có dữ liệu vì chưa nối kênh — [Nối Zalo]". Nếu có kênh nhưng trống: nói rõ đang chờ gì.
- Effort: **M**.

**C8. Quá nhiều khái niệm AI và lọc tin.**
- Bằng chứng: lọc tin có 2 lớp ở 2 nơi (Quy tắc sàng lọc; "Lọc đầu Hộp thư" ở `TriageCard.tsx:27`) kèm Jev. Hộp thư hiện cả "điểm lọc đầu" và "độ tin cậy". Ví dụ Gen đưa cho người mới là "Chỉ tôi cách thêm khoá Jev" (`GenPanel.tsx:10`, `HelpPage.tsx:16`). Thang tự trị 0–6, trong khi mọi tin gửi ra ngoài **luôn** phải duyệt ở mọi mức (`apps/api/gh/biz/duty/engine.py:5-6`).
- Ảnh hưởng: Boss phải học tên nội bộ (Jev, core agent, SSOT). Các mức 5–6 gần như giống mức 4 với tin nhắn.
- Cách sửa:
  - Một trang "Lọc tin": bật/tắt và mức khắt khe Thấp/Vừa/Cao.
  - Jev và trọng số đưa vào Nâng cao.
  - Tự trị còn 3 mức: Chỉ ghi nhận · Gợi ý · Soạn sẵn chờ duyệt.
  - Ví dụ Gen đổi thành việc thật: "Tóm tắt tin khách hôm nay", "Facebook có gì mới?", "Việc nào quá hạn?".
- Effort: **M**.

**C9. Gen-hub khó nối với người không rành kỹ thuật.**
- Bằng chứng: a4 #4. Thẻ nằm giữa màn MCP Hub (`McpScreen.tsx:99`). Công tắc mạng công cộng mặc định tắt (`hub_link/service.py:176`). Câu lỗi nhắc tới một công tắc tên khác (`mcp_client.py:157`). Phải Lưu xong mới Kiểm tra được (`HubLinkCard.tsx:123`).
- Ảnh hưởng: dễ gặp lỗi "mạng công cộng" ngay lần đầu, phải hỏi người hỗ trợ.
- Cách sửa:
  - Đưa thẻ lên trang Kết nối.
  - Địa chỉ là https công khai của Gen-hub thì tự bật mạng công cộng (vẫn ghi nhật ký).
  - Gộp thành một nút "Lưu & kiểm tra".
  - Dùng ô chọn ngày theo kiểu vi-VN.
- Effort: **S**.

**C10. Facebook chỉ vào được từ menu tài khoản, không nằm trong danh sách kênh.**
- Bằng chứng: `AccountFooter.tsx:136-149`, `routeHandles.ts:15` (khu "KẾT NỐI" không có trên thanh bên). Hệ thống › Kênh chỉ có Zalo/WA/Telegram/LinkedIn (ảnh `shots/own-system-channels.png`).
- Ảnh hưởng: Boss không tìm thấy chỗ nối Facebook nếu không nhớ.
- Cách sửa: đưa vào trang Kết nối (khuyến nghị 2). Tạm thời thêm một mục ở thanh bên cho Owner.
- Effort: **S**.

### 🟡 Vàng — đánh bóng và nợ nhỏ

- **V1. Bản sao và khai báo hai nơi** (a2). Gộp lại: mỗi thứ chỉ một thẻ, chỗ khác để liên kết. Danh mục chỉ sinh từ một nguồn (API trả, web chỉ giữ icon và đường dẫn). Gộp 3 nơi khai thang tự trị. **S–M**.
- **V2. Tiếng Anh và chữ kỹ thuật** (a3). Riêng tooltip thanh bên luôn có tiếng Anh. Logo "v2.2". Nút "Tìm theo ý định" khác tên màn. Ghi chú phát hành vẫn dạng "feat(providers): …" (`update/updateModel.ts:85-99` chỉ bỏ link và tên người). **S**.
- **V3. Bỏ "Phụ đề tiếng Anh"** khỏi menu tài khoản (`AccountFooter.tsx:188-201`). Ít giá trị, thêm một mục cần giữ cho đồng bộ. **S**.
- **V4. Tổng quan quá nhiều số**: 11 thẻ. "Độ trễ xử lý" và "Độ trễ xử lý của hệ thống" là **cùng một số** (`apps/api/gh/biz/queue/routes.py:449, 460`). Operator cũng thấy số kỹ thuật (plugin, độ trễ). Nên còn 4 số kinh doanh. Số kỹ thuật chuyển sang Sức khoẻ hệ thống. **S**.
- **V5. "Hồ sơ sống" là màn chi tiết nhưng nằm ở menu** (`navigation.py:36`), bấm vào là "Chưa chọn hồ sơ" (`ProfileScreen.tsx:21`). Bỏ khỏi menu, chỉ mở từ danh sách. **S**.
- **V6. `/guide` và `/guide/:n` không có tiêu đề ở header** (`router.tsx:74-75` thiếu `handle`). **S**.
- **V7. Dải tab Hệ thống vẫn tràn ở 1440px**: "Dữ liệu & lưu trữ sao lưu · c…" (ảnh `shots/own-system-channels.png`). Lỗi V6 cũ còn. **S**.
- **V8. Nút trên header khó hiểu**: "tự trị 4", khiên "78%", nút dấu trang "Góc nhìn đã lưu". Lỗi L10 cũ còn. Với Boss nên đưa vào Nâng cao. **S**.
- **V9. Gen chỉ dành cho Owner, không có chỗ bật cho người khác** (`store.py:18`, API `/gen/settings` chưa có giao diện). Thêm công tắc ở Hệ thống › Tổ chức khi Boss muốn. **S**.
- **V10. Mục cũ còn tồn từ rà soát v0.1.27**: L1 (logo), L15 (tên hướng dẫn), L16 ("Hộp thư ý nghĩa" nghe như dịch máy), V6 (tab tràn), L10 (biểu tượng ở header).

---

## (d) Top 5 khuyến nghị (đơn giản hoá + giá trị trợ lý)

**1. "Chế độ Boss": thu thanh bên còn 6 mục theo việc, phần còn lại vào "Nâng cao".** (M · sửa Đ3, V1, V4, V5, V8)
- **Hôm nay**: thẻ "Cần Sếp xử lý" + hàng đợi + 4 số chính.
- **Hộp thư & Việc**: Hộp thư, Bàn làm việc, Việc & Nhắc hẹn thành các tab.
- **Khách & Cơ hội**: Khách & Nhóm, Cơ hội, Deal, Tài liệu.
- **Kết nối**: xem khuyến nghị 2.
- **Đội ngũ**: Người dùng. Đánh giá và Chăm sóc chỉ hiện khi đã có ít nhất 1 nhân viên.
- **Cài đặt**: Tổ chức, Tài khoản/PIN, Sao lưu, Cập nhật, Trợ giúp.
- **Nâng cao** (thu gọn): Kho thô, Kho sạch, Hợp nhất danh tính, Quy tắc chi tiết, MCP Hub, Plugin, API & Model chi tiết, Danh tính Agent, Quyền hạn, Nhật ký, Jev, Góc nhìn đã lưu, Bản đồ quan hệ, Sổ tay, Cung–Cầu.
- Gen luôn ở góc phải. Giữ nguyên các màn, chỉ đổi danh mục (API `navigation.py` trả thêm `mode`).

**2. Một trang "Kết nối" duy nhất, mỗi thứ một thẻ, cùng một kiểu trạng thái.** (M · sửa Đ3, C9, C10, a2)
- Các thẻ: Bộ não AI (tài khoản Google/Claude, khoá API, model đang dùng cho Gen và Sàng lọc) · Zalo · WhatsApp · Telegram · Facebook · Gen-hub · Hệ thống ngoài (MCP).
- Mỗi thẻ có: trạng thái (Đang chạy / Cần Sếp xử lý / Chưa nối), một nút chính, liên kết "Hướng dẫn".
- Các nơi khác **chỉ để liên kết**, không còn bản sao CliCard, PinCard, UpdateCard. Thêm hướng dẫn cho Facebook, Gen-hub và truy cập từ xa (C5, C6).

**3. Dọn các lời hứa không thật và ngõ cụt trong 1 bản vá S.** (S · sửa Đ1, C1–C4, C6, V2, V6)
- Thay danh sách giả bằng danh sách người và trợ lý thật. Mock dùng UUID. Thêm kiểm tra tĩnh.
- Bỏ (hoặc làm thật) "Dùng dữ liệu mẫu".
- Ẩn 4 dòng gán model chưa nối.
- "Đã xác nhận" → "Đã lưu nháp — chưa gửi", kèm "Duyệt & gửi".
- Trang đích theo vai trò. Ẩn tab không có quyền.
- Một tên cho hướng dẫn. Sửa link việc 10.
- Logo lấy phiên bản thật. Tooltip thanh bên chỉ tiếng Việt. Bỏ 1 thẻ độ trễ trùng.

**4. Gen thành trợ lý thật, đi theo lát.**
- (a) **S**: lịch sử hội thoại (đã có API) và giữ hội thoại khi tải lại trang.
- (b) **M**: "Bản tin Gen" 7:30 và 17:30 gồm việc đến hạn, khách nóng, nháp chờ, Facebook mới, Kho có gì mới, sự cố cần Sếp. Gửi vào chuông.
- (c) **M–L**: đường tới Boss ngoài Console. Nhắc việc và bản tin đi qua Telegram/Zalo **của chính Boss**. Boss nhắn lại để hỏi Gen, mọi đề xuất vẫn phải xác nhận và có PIN ở Console.
- (d) **M**: hành động khép vòng có xác nhận: "Duyệt & gửi" ngay trong thẻ Gen, đề xuất ghi Việc/Ghi chú vào Kho Gen-hub, trả lời Facebook (lát D3 tiếp, theo luật cứng).
- (e) **M**: "Gen nhớ": ghi chú sở thích và quy ước của Sếp. Gen đề xuất, Sếp xác nhận, sửa được ở Cài đặt.
- (f) **S–M**: thêm công cụ đọc Tài liệu và Deal/Vụ việc.

**5. "Sức khoẻ cần Sếp", để hệ thống tự lo và tự báo.** (M · sửa Đ2)
- Một job kiểm định kỳ: kênh/phiên hết hạn, model lỗi hoặc hết hạn mức, CLI hết hạn, có bản mới, sao lưu lỗi, nháp chờ quá 24 giờ, lần sàng lọc lỗi.
- Mỗi sự cố sinh một thông báo chuông có nút sửa. Đầu Tổng quan có thẻ "Cần Sếp xử lý". Sau đó đẩy qua kênh của Boss (4c).
- Kèm một "e2e trên API thật" cho 5 hành trình chính (thiết lập, nối model, giao việc, mời người, cập nhật). Mock phải kiểm dữ liệu như API thật, để chặn đúng loại lỗi "mock xanh, máy thật hỏng" (Đ1, #31).

---

## Phụ lục — ảnh chụp mới (`audit/shots/`, chế độ mock, 1440px trừ khi ghi khác)

| Tệp | Nội dung |
|---|---|
| `own-overview-full.png` | Tổng quan Owner: thẻ cập nhật, 11 thẻ số (2 thẻ độ trễ trùng) |
| `own-account-menu.png` | Menu tài khoản: Facebook, Hướng dẫn, Phụ đề tiếng Anh. Khung Gen mở với ví dụ "khoá Jev" |
| `own-social.png`, `m-own-social.png` | Trang Tài khoản mạng xã hội (máy tính và 375px) |
| `own-mcp-genhub.png` | Thẻ Gen-hub nằm giữa MCP Hub kỹ thuật |
| `own-api-model.png` | API & Model: 9 dòng gán model (4 dòng chưa nối), 2 thẻ CLI |
| `own-system-channels.png`, `own-system-brain.png` | Kênh (WhatsApp hết hạn, không báo), thẻ Google lặp, tab tràn |
| `own-guide.png`, `own-help.png` | Hướng dẫn và Trợ giúp (khác tên gọi) |
| `mgr-system-default.png`, `mgr-system-brain.png`, `mgr-overview.png` | Manager: tab khoá, 5 thẻ lỗi ở Bộ não AI |
| `op-overview.png`, `op-help.png` | Operator: Tổng quan có số kỹ thuật, Trợ giúp theo vai trò |
| `own-gen-open.png` | Khung Gen |

Chạy lại: `audit/shoot.mjs` (cần mock `vite --mode mock --port 5199` và `node_modules` liên kết tạm, đã gỡ sau khi chụp).
