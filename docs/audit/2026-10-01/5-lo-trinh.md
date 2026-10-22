# Rà soát 5 — Lộ trình so với thực tế, và hướng chiến lược (Gen-Harness v0.1.31)

Ngày rà: 01/10/2026 · Ảnh mã nguồn: origin/main = v0.1.31 (`eb5a71b`) · Chỉ đọc, không sửa repo/Kho/GitHub.
Nguồn đã đọc: `docs/ROADMAP.md`, `docs/design/{gen-v1,gen-browser-agent,gen-hub-link}.md`, `docs/reports/HANDOFF-v0.1.1.md`
(v0.1.21→v0.1.31), `README.md`, `docs/ARCHITECTURE.md`, `docs/PLAN.md`, Product Spec LOCKED v2.2; mã `apps/api/gh/**`,
`apps/web/src/**`, `apps/browser/ghb/**`, `deploy/compose.yaml`; nhánh trên origin; Kho: `kho_tom_tat`, DA-1, QD-8, QD-10,
QD-11, QD-12, PHIEN-6, PHIEN-7, PHIEN-8, cùng các lượt tìm "Gen-Harness", "Jev", "mạng xã hội", "v0.1.3".

---

## Tóm tắt 30 giây

- **Phần đã làm thì chắc**: Đợt A (Gen v1 + đề xuất có xác nhận), B (B1–B7), C1, D1 lát đọc, D3 lát đọc đều có mã, test và
  migration. Chưa thấy chỗ nào tài liệu ghi "xong" mà mã thật ra không làm.
- **Lời hứa trễ**: QD-12 hứa "v0.1.30: đăng/trả lời/nhắn qua đề xuất có Xác nhận". ROADMAP lại ghi "v0.1.31". Thực tế
  v0.1.30 và v0.1.31 đều dành cho sửa lỗi và nguồn model CLI. Ghi lên mạng xã hội **vẫn là chỗ cắm ném lỗi**
  (`apps/api/gh/social/permit.py`, `apps/browser/ghb/adapters/base.py:24-27`).
- **Món nợ lớn nhất là nghiệm thu thật, không phải tính năng**: Gen-hub thật, Facebook thật, Jev thật, đăng nhập Claude/agy
  thật đều ghi "chưa kiểm" (PHIEN-6; HANDOFF v0.1.29/0.1.30/0.1.31). Từ 29/09 đến 01/10 đã ra 11 bản, nhanh hơn tốc độ Boss
  kịp dùng.
- **Gen đang an toàn đúng kiểu, nhưng chưa là trợ lý thật**: Gen chỉ trả lời khi được hỏi trong Console. Gen chưa tự báo
  việc, chưa chạm lịch/mail của Boss và chưa ghi nhớ gì (không ghi Kho). Ba bản gần nhất (v0.1.30–v0.1.32) dồn vào "ống
  nước" nguồn model CLI.
- **Đề xuất**: (1) chạy thật và dọn nợ, (2) bản tin sáng của Gen, (3) Gen đọc lịch/mail/việc qua Gen-hub, (4) đồng hồ chi
  phí và chấm điểm Gen, (5) ghi có xác nhận ra ngoài (Kho → Lịch/Gmail → Facebook). Đóng băng: thêm nền tảng mạng xã hội,
  làm riêng cho Jev, phương án B, Playwright cho agent vòng ngoài, đánh bóng thêm nguồn CLI.

---

## (a) Bảng kế hoạch so với thực tế

Ký hiệu trạng thái: **Xong** · **Dở dang** · **Chưa làm** · **Bỏ** · **Thay thế** (làm theo cách khác thiết kế).
"Chưa kiểm thật" nghĩa là có mã và test giả, nhưng chưa chạy với dịch vụ hoặc tài khoản thật.

### Đợt A — Gen v1/v2 (thiết kế `docs/design/gen-v1.md`)

| Kế hoạch | Trạng thái | Bằng chứng trong mã | Ghi chú |
|---|---|---|---|
| A1 khung chat, Gen trả lời chỉ đọc, lưu hội thoại 90 ngày, Nhật ký | Xong (v0.1.21) | `gh/gen/{routes,engine,store}.py`, `apps/web/src/gen/GenPanel.tsx`, migration `0016` | Gen chỉ bật cho Owner (`gh/gen/store.py:17` `roles: ["owner"]`) |
| A2 mở trang, khoanh sáng, tour | Xong | `apps/web/src/gen/{director.ts,Spotlight.tsx}`, `packages/contracts/src/genTargets.ts`, `gh/gen/validator.py` | Validator chặn id/màn do model bịa ra |
| A3 nguồn Jev (System One) làm bộ quyết định nhanh | Xong về mã, **chưa kiểm thật** | `gh/gen/{jev,decider}.py`, `screens/system/JevCard.tsx` | `jev.py:11` `TODO(jev-schema)`: schema là giả định. Model mặc định `typesafe/jev-1.13` chưa đối chiếu. QD-10: "Jev chưa cần dùng lúc này" |
| A4 đề xuất có xác nhận (nháp tin, nhắc việc, gán người) + PIN | Xong (v0.1.24) | `gh/gen/proposals.py` (3 loại, dòng 37–50), `web/src/gen/ProposalCard.tsx`, migration `0018` | |
| `prefill` điền form (gen-v1 §3.3, v2) | Thay thế | `gh/gen/envelope.py` không có `prefill` | Đã thay bằng `propose` + Xác nhận, nhưng gen-v1 chưa ghi điều này |
| Bước sau của A4: duyệt/gửi nháp thay người, đề xuất cho Deal/Vụ việc | Chưa làm | — | HANDOFF v0.1.24 "Chưa làm" |
| Số đo Gen: nút Hữu ích/Không hữu ích, thời gian tới chữ đầu, tỉ lệ bị chặn (gen-v1 §6) | Chưa làm | không có trường đánh giá trong `gh/gen`, `web/src/gen` | Đây là chỗ thiếu "eval" |
| Stream token | Chưa làm | `gh/providers` không stream | HANDOFF v0.1.21: "stream" theo từng bước |
| Mở Gen cho vai trò khác (gen-v1 §9.1, "v1.x") | Chưa làm | `store.py:17` | |

### Đợt B — Cơ bản còn thiếu

| Kế hoạch | Trạng thái | Bằng chứng |
|---|---|---|
| B1 quản lý người dùng | Xong (v0.1.22) | `gh/auth/users.py`, `screens/system/UsersTab.tsx` |
| B2 thông tin công ty | Xong | `screens/system/OrgTab.tsx`, `GET/PATCH /system/org` |
| B3 Trợ giúp / phiên bản / Báo lỗi | Xong | `web/src/help/HelpPage.tsx` |
| B4 giao diện điện thoại | Xong (v0.1.23) | `lib/useMediaQuery.ts`, e2e `basics.spec.ts` |
| B5 trang lỗi / 404 | Xong | `web/src/shell/ErrorPage.tsx` |
| B6 chuông thông báo | Xong | `gh/notifications.py`, `shell/NotificationBell.tsx`, migration `0017` |
| B7 sáng/tối | Xong | `styles/theme.css`, `public/theme-init.js` |
| UX còn lại từ rà soát v0.1.28: ghi chú phát hành tiếng Việt, số đếm Hộp thư lệch (V8), các mục Nhẹ L1–L3, L5, L6, L8–L16 | Chưa làm | HANDOFF v0.1.28 "Để lại"; ROADMAP dòng v0.1.28 |

### Đợt C — Hạ tầng dữ liệu

| Kế hoạch | Trạng thái | Bằng chứng | Ghi chú |
|---|---|---|---|
| C1 lọc đầu Hộp thư (trùng, rác, điểm), Jev nếu có | Xong (v0.1.25, gia cố v0.1.27) | `gh/refinery/triage.py`, `triage_routes.py`, `screens/system/TriageCard.tsx`, migration `0019` | Jev chưa kiểm thật, nên hiện chỉ chạy bằng quy tắc |
| Đo độ chính xác có nhãn người ("Không phải rác"), gộp mục trùng | Chưa làm | không có trong mã | HANDOFF v0.1.25 |

### Đợt D — Phòng làm việc chung và mạng xã hội

| Kế hoạch | Trạng thái | Bằng chứng | Ghi chú |
|---|---|---|---|
| D1 Gen đọc Kho qua Gen-hub (chỉ Owner, chỉ đọc, che dữ liệu, đệm 5 phút, nhắc hạn token) | Xong về mã, **chưa kiểm với Gen-hub thật** | `gh/hub_link/{routes,service}.py` (`KHO_READ_SUFFIXES` dòng 39), `screens/mcp/HubLinkCard.tsx`, migration `0020`, ghim DNS trong `chassis/mcp_client.py` | PHIEN-6: "Chưa thử với Gen-hub thật"; quy tắc che dữ liệu là heuristic |
| D1 ngắt mạch 60 s riêng cho Gen-hub | Chưa làm | không có `breaker` trong `gh/hub_link` | HANDOFF v0.1.26/27 "Chưa làm" |
| D1 Gen đề xuất ghi kanban/warroom/Kho | Chưa làm, chờ Boss | — | QD-11(3) |
| Phương án B: Gen-hub đọc Gen-Harness (máy chủ MCP chỉ đọc) | Chưa làm, chờ Boss | — | QD-11(3); chưa ai cần dùng |
| D2 Jules | Bỏ | — | QD-10, QD-11(2). Riêng `gen-hub-link.md` §1 và §4.1 vẫn mô tả Jules |
| Playwright cho agent vòng ngoài | Chưa làm, chờ Boss | — | QD-11(3); nay trùng một phần với D3 |
| D3 lát đầu: Facebook cá nhân, đăng nhập trong app, CHỈ ĐỌC thông báo và hội thoại, Dừng tất cả, giới hạn tốc độ | Xong về mã (v0.1.29), **chưa kiểm với Facebook thật** | `gh/social/*`, `apps/browser/ghb/**`, `web/src/social/{SocialPage,LoginViewer}.tsx`, migration `0021`, các dịch vụ `browser`/`browser-egress`/`browser-redis` | Nghiệm thu thật cần Boss đăng nhập và đọc 1 lần |
| D3 ghi có xác nhận (đăng/trả lời/nhắn) qua đề xuất + PIN + permit | **Chưa làm (trễ hẹn 2 lần)** | `gh/social/permit.py` (`issue()` ném `WriteNotEnabled`), `ghb/adapters/base.py:24-27` (`NotImplementedError`), `gh/social/platforms.py` (`write_kinds=()`) | QD-12 hẹn v0.1.30, ROADMAP hẹn v0.1.31, thực tế chưa có |
| D3 API chính thức: Trang FB, IG chuyên nghiệp, Zalo OA, TikTok, LinkedIn, X | Chưa làm | `platforms.py` chỉ có `facebook_personal` | Thiết kế §5 hẹn v0.1.29–v0.1.31, đều trượt. Câu hỏi §6.3 "Công ty có Trang/OA không?" Boss chưa trả lời |
| D3 ảnh chụp + trace mỗi việc (thiết kế §3.3), Jev phân loại từng tin (§4), kiểm phiên tự động hằng ngày (§3.2) | Chưa làm / Dở dang | `ghb/runner.py` chỉ ghi `cost{ms,pages}`; kiểm phiên chỉ khi bấm tay (`social/service.py:465`) | Ảnh chụp/trace là bằng chứng cần có **trước** khi cho ghi |
| Sandbox Chromium | Chưa làm (đã ghi nợ) | `deploy/compose.yaml:139-140`, `docs/api/browser-protocol.md:12,31` | Đang bù bằng cách ly container và Redis riêng |

### Ngoài kế hoạch đợt (có mã nhưng không nằm trong A–D)

| Hạng mục | Bản | Bằng chứng | Nhận xét |
|---|---|---|---|
| Bước 4 "Để sau" kèm cảnh báo | v0.1.29 | `gh/setup/routes.py` | Theo QD-12, hợp lý |
| Đổi tài khoản Google cho Antigravity CLI, nhiều hồ sơ | v0.1.30 | `gh/providers/cli.py`, `CliCard.tsx` | Sửa lỗi phản ứng |
| Liệt kê model CLI theo nhóm, gọi thử thật, nguồn **Claude Code CLI** (gói Pro/Max) | v0.1.31 | `gh/providers/{catalog,cli,router}.py`, migration `0022` | Có rủi ro điều khoản (HANDOFF v0.1.31, "Chưa kiểm"); QD-12 cho Owner tự quyết |
| Tách model và mức suy nghĩ (effort), nút Chẩn đoán | v0.1.32 **đang làm** | nhánh `origin/claude/effort-wip` (5 commit, 01/10 17:30, có migration 0023) | Bản thứ 3 liên tiếp cho ống nước CLI |
| arq chuyển từ pickle sang JSON; Redis riêng cho trình duyệt | v0.1.29 | `gh/jobcodec.py`, `compose.yaml` `browser-redis` | Gia cố bảo mật tốt nhưng không có trong lộ trình |
| Kênh `telegram`, `linkedin` trong danh mục kênh | (cũ) | `gh/system_api/routes.py:38` | Bridge chỉ có `zalo.js`, `whatsapp.js`. Telegram đang "ngủ": có tên kênh nhưng không có bridge |

Quy mô hiện tại để tham chiếu: khoảng 26 nghìn dòng Python api, 25 nghìn dòng TS web, 20 nghìn dòng Go genh, 1,5 nghìn dòng
browser, 1,5 nghìn dòng bridge; 11 dịch vụ trong compose; 57 tệp pytest (khoảng 469 hàm test, README ghi "860+" nhưng chưa đối chiếu).

---

## (b) Danh sách nợ hợp nhất (đã kiểm từng mục)

Thứ tự trong mỗi nhóm: quan trọng trước.

### B1. Nghiệm thu thật (có mã, chưa chạy với hệ thống thật). Đây là nợ đắt nhất.
1. **Gen-hub thật**: Boss chưa tạo token và chưa bấm Kiểm tra (PHIEN-6 "Đang bàn"). Quy tắc che dữ liệu Kho cần chỉnh theo dữ liệu thật.
2. **Facebook thật**: đăng nhập và đọc 1 lần; bộ chọn (selector) mới chỉ thử trên trang mẫu (HANDOFF v0.1.29). Ảnh arm64 chưa chạy thử.
3. **Antigravity CLI + Google thật**: thêm tài khoản thứ hai và đổi qua lại (v0.1.30). Định dạng `agy models` khi đã đăng nhập, mã model Claude qua Antigravity (v0.1.31).
4. **Claude Code CLI**: đăng nhập thật tới cuối và tệp `.credentials.json` (v0.1.31).
5. **Jev**: chưa có khoá thật; schema và mã model là giả định (`gh/gen/jev.py:11`; PHIEN-6 "Cách gọi Jev chưa thử với Jev thật").

### B2. Tính năng đã hứa, chưa làm
6. **Ghi lên mạng xã hội** (đăng/trả lời/nhắn) qua đề xuất Gen + PIN + permit: QD-12 hẹn v0.1.30, ROADMAP hẹn v0.1.31. Chỗ cắm nằm ở `gh/social/permit.py`, `ghb/adapters/base.py`, `platforms.py`. Điều kiện trước: ảnh chụp/trace làm bằng chứng (thiết kế §3.3), cân nhắc sandbox Chromium (mục 12).
7. **Cổng API chính thức** cho Trang FB, IG chuyên nghiệp, Zalo OA (rồi TikTok, LinkedIn, X): chưa có dòng mã nào. Cần Boss trả lời trước: có Trang/OA đang dùng không (`gen-browser-agent.md` §6.3).
8. **Gen ghi sang Gen-hub** (kanban/warroom, và ghi Phiên/Việc vào Kho): chờ Boss cho phép (QD-11(3); QD-12 "Gen-hub nối để sau").
9. **Phương án B** (Gen-hub đọc số liệu Gen-Harness) và **Playwright cho agent vòng ngoài**: chờ Boss (QD-11(3)).
10. Ngắt mạch 60 s cho Gen-hub; kiểm phiên mạng xã hội tự động hằng ngày; Jev phân loại từng tin mạng xã hội.
11. Gen: đánh giá Hữu ích/Không hữu ích và số đo (gen-v1 §6); duyệt/gửi nháp thay người; đề xuất cho Deal/Vụ việc; mở Gen cho vai trò khác; stream. Lọc đầu: nút "Không phải rác", gộp trùng. UX: ghi chú phát hành tiếng Việt, số đếm Hộp thư (V8), các mục Nhẹ còn lại.

### B3. Bảo mật / hạ tầng
12. **Sandbox Chromium đang TẮT** (`compose.yaml:139-140`, `browser-protocol.md:31`). Đang bù bằng cách ly. Phải xử lý hoặc chốt chấp nhận rủi ro **trước** khi cho ghi lên mạng xã hội.
13. Claude Code CLI dùng gói Pro/Max qua app tự động: rủi ro bị hạn chế theo điều khoản (HANDOFF v0.1.31). QD-12 đã chấp nhận, nhưng việc nền (lọc đầu, đọc theo lịch, bản tin) nên đi bằng khoá API.

### B4. Quy trình, Kho, nhánh
14. **Sổ phiên trên Kho bị hụt**: phiên Gen-Harness mới nhất là PHIEN-6 (v0.1.23–v0.1.27). **Chưa có PHIEN** cho v0.1.28, .29, .30, .31 và v0.1.32 đang làm. PHIEN-6 ghi lý do: "Ghi Kho từ phiên Claude Code bị lớp kiểm soát quyền chặn cho tới khi Boss nói 'ghi Kho đi'".
15. **Việc tồn của Gen-Harness không có trong Kho**: DA-1 có `Việc: []`; tìm "Gen-Harness" trong bảng Việc ra 0 kết quả. Tồn đọng chỉ nằm trong `ROADMAP.md`, trái tinh thần QD-6 ("Việc… có bản gốc ở Kho").
16. Bản ghi Kho đã cũ: QD-12 vẫn ghi "v0.1.30: đăng/trả lời/nhắn" (đã trượt); mô tả DA-1 vẫn chỉ là "Console cho SME… 6 giai đoạn đầu xong", chưa nói gì về Gen hay vai trò trợ lý.
17. **Nhánh cũ**: trên origin còn 11 nhánh `claude/*` ngoài main. 9 nhánh đã merge kiểu squash (`cli-models-wip`, `hardening-wip`, `hublink-wip`, `refinery-wip`, `social-wip`, `ux-wip`, `phase-1-2-nen-du-lieu`, `project-thread-bnesk5`, `zen-lovelace-ph1qa2`), nhánh `admiring-goodall-6dmk8k` trùng main, còn `effort-wip` đang làm. Máy local còn khoảng 45 nhánh `worktree-agent-*`. Gen-workplace đã bật tự xoá nhánh khi merge (PHIEN-8); Gen-Harness chưa kiểm cài đặt này.
18. **v0.1.32 (`effort-wip`) đang dở**: cần làm xong hoặc gác lại rõ ràng.
19. Việc phía Gen-hub G1–G4 (`gen-hub-link.md` §3.2: trả `expires_at`, 2 token song song, hướng dẫn MCP tuỳ chỉnh, ghi chú bảng Tri thức): chưa kiểm, thuộc repo Gen-hub.
20. Lệch tài liệu: xem mục (e).

---

## (c) Đánh giá chiến lược

### c1. Dự án có đang hội tụ về "Gen là trợ lý thật" cho Boss?

**Nền móng thì có, giá trị Boss nhận được thì chưa.**

Điểm mạnh, đúng kiểu kỹ sư AI có kinh nghiệm:
- Gen chỉ có quyền bằng người đang hỏi: tool gọi nội bộ qua endpoint sẵn có bằng cookie của người hỏi (`gh/gen/tools.py`).
- Đầu ra của model có kiểu, khai báo `extra="forbid"`; validator chặn id/màn bịa; mọi nội dung ngoài được bọc khối "DỮ LIỆU
  KHÔNG TIN CẬY"; ghi chỉ qua đề xuất + Xác nhận + PIN; Action Log có chuỗi băm; mạng xã hội dùng permit ký HMAC.
- Đây là khung đúng để sau này mở dần quyền "làm thay" mà không mất kiểm soát.

Điểm chưa hội tụ:
1. **Gen bị động và ở sai chỗ**: Gen chỉ trả lời khi Boss mở Console và hỏi. Không có việc định kỳ nào của Gen (danh sách
   cron trong `gh/worker.py:192-201` không có Gen), không có push về điện thoại. North Star của dự án là "10 phút trước
   Console là biết hôm nay có gì" (Spec A4, DA-1), nhưng chưa có bản tin chủ động nào.
2. **Gen chưa chạm công cụ hằng ngày của Boss**: Gen-hub đã có sẵn tool Gmail, Lịch, Drive, Tasks (connector `mcp-46634`),
   nhưng liên kết Gen-hub chỉ cho phép hậu tố `kho_*` (`hub_link/service.py:39`). Gen cũng không có đường gọi MCP tổng quát.
3. **Gen không có trí nhớ ngoài hội thoại**: không ghi Kho, nên không tự ghi phiên hay việc. Đây cũng là lý do sổ phiên Kho
   bị hụt (nợ 14–15).
4. **Phần lớn tích hợp mới chưa chạy thật** (nợ 1–5). Mỗi bản lại đẩy thêm việc cho Boss: tạo token, đăng nhập FB, đăng
   nhập CLI, khoá Jev. Danh sách việc Boss phải làm tăng nhanh hơn việc Boss thật sự làm.
5. **Năng lực bị hút vào ống nước model**: v0.1.30, v0.1.31, v0.1.32 đều xoay quanh đăng nhập CLI, liệt kê model, effort.
   Phụ thuộc vào CLI dành cho người dùng cá nhân chạy trong container vốn mong manh: luồng đăng nhập, tệp phiên, mã model
   đều chưa xác minh. Thêm vào đó là rủi ro điều khoản (nợ 13).
6. **Hai sản phẩm trong một repo**: Spec LOCKED v2.2 và ARCHITECTURE mô tả Console SME lắng nghe Zalo/WhatsApp. Lộ trình
   từ v0.1.21 lại đi về phía "trợ lý cá nhân / OS công ty của Boss" (Gen, Kho, mạng xã hội cá nhân). Chưa văn bản nào
   chốt cái nào là mặt tiền chính. Gen trả lời về dữ liệu kinh doanh chỉ có giá trị nếu bridge Zalo/WhatsApp đang đổ dữ
   liệu thật; repo và Kho chưa có bằng chứng điều này (chưa kiểm).
7. **Quy mô lớn so với một Owner**: khoảng 70 nghìn dòng mã, 11 container. Mỗi tích hợp mới cộng thêm chi phí bảo trì
   (vd so ảnh pixel với thiết kế gốc phải liên tục ẩn phần mới: HANDOFF v0.1.23, v0.1.30).

### c2. Chi phí model: CLI subscription, API hay OpenRouter/Jev?

| Đường | Có trong mã | Hợp với | Rủi ro / ghi chú |
|---|---|---|---|
| Antigravity CLI (gói Google) | Có, `antigravity_cli` | Owner hỏi Gen trực tiếp | Mong manh (3 bản sửa), mã model Claude qua agy chưa xác nhận |
| Claude Code CLI (gói Pro/Max) | Có, `claude_code_cli` (v0.1.31) | Owner dùng cá nhân, chất lượng cao | Điều khoản: gói cá nhân dùng qua app tự động có thể bị hạn chế (HANDOFF v0.1.31). Không nên dùng cho việc nền |
| Khoá API (Gemini, DeepSeek, `openai_compat`) | Có (`system_api/routes.py:252`) | Việc nền chạy đều: lọc đầu, đọc theo lịch, bản tin sáng | Trả theo lượt, sạch về điều khoản, đoán trước được |
| **OpenRouter như nguồn sinh chữ** | Về lý thuyết **không cần mã mới**: thêm nguồn `openai_compat`, endpoint `https://openrouter.ai/api/v1` (`router.py:173`) | Một khoá dùng nhiều model, chọn model rẻ/mạnh theo việc | Chưa thử thật |
| **Jev (System One)** | Có, `system_one`, chỉ là **bộ chọn** (`JevClient.choose`), **không** nằm trong chuỗi sinh chữ | Phân loại rẻ: ý định, rác/trùng, trạng thái trang | Không "nâng năng lực" câu trả lời. Schema/mã model là giả định. QD-10 ghi "chưa cần" |

Kết luận về chi phí:
- Jev chỉ có thể cắt chi phí ở khâu phân loại. Muốn Gen trả lời giỏi hơn thì cần model sinh chữ tốt hơn. Đường rẻ nhất cho
  việc đó là OpenRouter qua `openai_compat` (có sẵn) hoặc khoá Gemini; không phải làm thêm cho Jev.
- Thiếu một thứ để quyết bằng số: `agent.model_calls` đã ghi `tokens_in/out` theo `agent_key` (`router.py:180-184`), nhưng
  chưa có bảng giá, chưa quy ra VND/ngày, chưa có trần ngân sách (hiện chỉ có `daily_quota` tính theo số lượt).
  Có đồng hồ chi phí và một bộ câu hỏi chuẩn thì mới so được "CLI và OpenRouter và Jev" bằng dữ liệu, không phải bằng cảm giác.

### c3. Mục tiêu học của Boss (trở thành kỹ sư AI có kinh nghiệm)

- Repo đã chứa nhiều mẫu đáng học: giao thức tool có kiểu, chống model bịa id, chống prompt injection, permit ký HMAC, định
  tuyến và dự phòng model, cách ly container cho trình duyệt.
- **Hai mảng thiếu lại là hai mảng cốt lõi của nghề**: (1) **eval**, tức đo chất lượng Gen bằng bộ câu hỏi chuẩn và đánh giá Hữu ích/Không hữu ích;
  (2) **quan sát chi phí và độ trễ**. Nên đưa hai mảng này vào lộ trình như công cụ học, không chỉ như tính năng.
- Cách học hợp với Boss: mỗi mốc có 1 "bài học" ngắn kèm 1 con số Boss tự xem được trên Console (điểm Gen, VND/ngày).

---

## (d) Đề xuất 3–5 mốc tiếp theo (xếp theo giá trị cho Boss ÷ công sức) và những gì nên đóng băng/cắt

Quy mô: S ≤ 1 bản · M 1–2 bản · L > 2 bản.

### Mốc 1 — v0.1.33 "Chạy thật & dọn nợ" (S, giá trị rất cao vì mở khoá mọi thứ phía sau)
- Làm xong hoặc gác rõ v0.1.32 (`effort-wip`).
- **Một trang "Việc Boss cần làm"** trong Hướng dẫn thiết lập, khoảng 20 phút: dán token Gen-hub → Kiểm tra; đăng nhập FB →
  Đọc ngay; đăng nhập agy/Claude → Gọi thử; (tuỳ chọn) khoá Jev → Kiểm tra. Claude sửa những lỗi lộ ra.
- **Bộ 10–15 câu hỏi chuẩn** Boss hay hỏi Gen. Chạy lại mỗi bản và ghi điểm vào báo cáo phát hành (bước đầu của eval).
- Đồng bộ tài liệu (mục e); cập nhật QD-12 (hạn v0.1.30 đã trượt); ghi PHIEN cho v0.1.28–v0.1.32; tạo VIEC trên Kho cho
  danh sách nợ (b); xoá 10 nhánh `claude/*` đã merge và nhánh `worktree-agent-*` local; bật tự xoá nhánh khi merge.
  Các việc ghi Kho và xoá nhánh cần Boss cho phép.

### Mốc 2 — "Bản tin sáng của Gen" (S–M, đúng North Star)
- Job worker khoảng 07:30 giờ VN: Gen tổng hợp Tổng quan, hàng chờ duyệt, việc đến hạn, việc đang mở và quyết định mới
  trên Kho, cùng thông báo FB (nếu Boss bật). Gửi thành 1 thẻ chuông, bấm vào thì mở Gen với ngữ cảnh đó; Boss bấm Hữu ích/Không hữu ích.
- Dùng tool đọc sẵn có. Chạy bằng **khoá API** (không dùng CLI cá nhân) cho việc nền.
- Sau đó mới tính kênh ngoài Console (thông báo đẩy về điện thoại / PWA).

### Mốc 3 — "Gen đọc công cụ hằng ngày qua Gen-hub" (M, cần Boss duyệt phạm vi)
- Mở rộng danh sách hậu tố cho phép (hiện chỉ `kho_*`) sang tool **chỉ đọc** đã có trên Gen-hub: `calendar_list_events`,
  `tasks_list`, `gmail_search`/`gmail_read_message`, `drive_search`. Tái dùng `invoke_tool`, ghim DNS, `mask_for_model`,
  đệm 5 phút và giới hạn chỉ Owner.
- Gen-hub không phải sửa mã, chỉ cần tick thêm quyền cho token. Ghép vào Bản tin sáng ("hôm nay có 3 lịch, 2 mail cần trả lời").
- Rủi ro riêng tư: nội dung mail đi sang model đám mây. Cần Boss chốt giống QD-11(1): che dữ liệu, chỉ Owner.

### Mốc 4 — "Đồng hồ chi phí & chấm điểm Gen" (S–M, giá trị học cao nhất)
- Thêm bảng giá theo model, quy `agent.model_calls` ra VND/ngày theo từng agent, đặt trần ngân sách/ngày, lưu đánh giá Hữu ích/Không hữu ích cho mỗi
  câu trả lời của Gen.
- Chạy bộ câu hỏi chuẩn trên 2–3 nguồn (CLI, OpenRouter qua `openai_compat`, Gemini) rồi chốt chuỗi model bằng số liệu.
  Jev chỉ giữ lại nếu đo thấy rẻ và đúng ở khâu phân loại.

### Mốc 5 — "Gen ghi có xác nhận ra ngoài" (M–L, làm theo thứ tự rủi ro tăng dần)
- Một khung permit chung (đã có mẫu ở bridge và chỗ cắm `gh/social/permit.py`), rồi lần lượt:
  - (a) ghi Phiên/Việc vào Kho: trả nợ sổ phiên và cho Gen có trí nhớ;
  - (b) nháp Gmail, tạo sự kiện Lịch;
  - (c) trả lời/nhắn Facebook cá nhân qua trình duyệt.
- Điều kiện trước bước (c): có ảnh chụp/trace mỗi việc, và hoặc bật sandbox Chromium, hoặc Boss chốt chấp nhận rủi ro bằng
  văn bản. Cổng API Trang FB/IG/Zalo OA chỉ làm khi Boss xác nhận có Trang/OA cần dùng.

### Nên đóng băng / cắt để giữ đơn giản
- **Đóng băng thêm nền tảng mạng xã hội** (IG, TikTok, LinkedIn, X, Zalo OA) tới khi FB cá nhân chạy thật ≥ 2 tuần và Boss nói rõ có nhu cầu.
- **Đóng băng làm riêng cho Jev** (khớp QD-10): chỉ 1 lần kiểm với khoá thật. Lỗi thì ẩn thẻ Jev. Không xem Jev là cách nâng năng lực.
- **Cắt khỏi lộ trình gần**: phương án B (Gen-hub đọc Gen-Harness); Playwright cho agent vòng ngoài (trùng D3); mở Gen cho
  vai trò khác (đợi có nhân viên dùng thật).
- **Ngừng đánh bóng thêm nguồn CLI** sau v0.1.32: CLI chỉ dành cho Owner hỏi trực tiếp; việc nền đi bằng khoá API.
- **Cân nhắc hạ mức so ảnh pixel** với thiết kế gốc xuống kiểm khói, vì phần mới (Gen, chuông, hướng dẫn) liên tục phải ẩn khi so.
- **Chốt một câu định vị** trong Spec/DA-1: "Gen là mặt tiền chính, Console là nơi xem chi tiết". Có câu này thì mới lọc
  được tính năng nào đáng làm.

---

## (e) Sức khoẻ tài liệu

Nhìn chung: HANDOFF ghi rất chi tiết và trung thực, kể cả mục "Đã kiểm / chưa kiểm". Ngược lại, ROADMAP, ba tài liệu thiết
kế, README và ARCHITECTURE đã lệch so với mã và với Kho.

| Tài liệu | Chỗ lệch | Vị trí |
|---|---|---|
| `docs/ROADMAP.md` | Tiêu đề ghi "cập nhật 30/09" nhưng đã chứa v0.1.31 (01/10) | dòng 1 |
| | "Tiếp: v0.1.31 ghi có xác nhận" là sai: v0.1.31 là bản model CLI; QD-12 lại ghi v0.1.30 | dòng 73 |
| | Bảng vai trò ghi "Vòng ngoài … (+ Playwright sau)", trong khi Playwright nay dùng cho Gen (D3) | dòng 12 |
| | "repo Gen-hub, cần mở quyền repo cho phiên này" đã cũ, vì D1 làm được mà không cần sửa Gen-hub | dòng 66 |
| | Chưa có mục cho các bản phản ứng (v0.1.30–0.1.32) trong đợt nào; không có mục "Nợ" | — |
| `docs/design/gen-browser-agent.md` | Vừa ghi "ĐÃ LÀM ở v0.1.29" vừa ghi "Chỉ là thiết kế — chưa có dòng code nào" | dòng 3, 11 |
| | "Bản đầu v0.1.28", bảng §5 hẹn v0.1.29/30/31: trượt toàn bộ | dòng 23, §5 |
| | §3.1 dùng arq / `gh.social.browser_worker`, thực tế là `apps/browser/ghb` + Redis Stream | dòng 76 |
| | §3.7 "Cờ tổng `social.enabled`" trái với ghi chú đầu tài liệu ("không có cờ") | dòng 136 |
| | Migration ghi `0021_v0128_social`, thực tế là `0021_v0129_social`; quyền `social.read/manage`, thực tế là `require_owner` | dòng 170, 181 |
| `docs/design/gen-hub-link.md` | Trạng thái vẫn "NHÁP để Boss duyệt" | dòng 3 |
| | Bảng vai trò vẫn có **Jules worker**; §4.1 mô tả Jules chi tiết (đã bỏ theo QD-10) | dòng 28, §4.1 |
| | "Gen **không** dùng Playwright" trái với D3 | dòng 29 |
| `docs/design/gen-v1.md` | Trạng thái vẫn "NHÁP để Sếp duyệt" | dòng 3 |
| | "cờ `gen.enabled` (tắt mặc định tới lát 4)", thực tế mặc định BẬT | dòng 159 |
| | "Gen không dùng Playwright" trái với D3; `prefill` chưa ghi là đã thay bằng `propose` | dòng 178, §3.3 |
| `README.md` | "8 dịch vụ", thực tế 11 (thêm `browser`, `browser-egress`, `browser-redis`) | dòng 34 |
| | "Giai đoạn 6 … dự kiến thêm `genh`": genh đã là đường cài chính | dòng 18 |
| | Trỏ tới `docs/reports/HANDOFF-v0.1.2.md`, tệp **không tồn tại** | dòng 125 |
| | Không nhắc Gen, Gen-hub, mạng xã hội, Claude Code CLI; bước 4 chỉ ghi "API key" | toàn tệp |
| `docs/ARCHITECTURE.md`, `docs/PLAN.md` | Không có Gen, Gen-hub, mạng xã hội, Jev, Claude Code CLI; §11 chỉ liệt kê provider cũ | ARCH dòng 290 |
| `docs/reports/HANDOFF-v0.1.1.md` | Tên tệp gây hiểu nhầm (chứa v0.1.1→v0.1.31, 1053 dòng); hai mục cùng tên "v0.1.30" | dòng 917, 952 |
| | v0.1.29 "Để lại: ghi có xác nhận (v0.1.30…)" đã trượt hẹn | mục v0.1.29 |
| Mã (chú thích hứa phiên bản) | "CHỖ CẮM v0.1.30", "v0.1.30: 'post' …" đã trượt | `gh/social/permit.py:1`, `platforms.py` (`write_kinds`), `ghb/adapters/base.py:27` |
| Kho | QD-12 hẹn v0.1.30; DA-1 mô tả cũ và không có Việc; thiếu PHIEN v0.1.28+ | QD-12, DA-1, PHIEN-6 |

Đề xuất sửa (thuộc Mốc 1):
- **ROADMAP** là nguồn sự thật duy nhất về tiến độ trong repo; thêm mục "Nợ" khớp với VIEC trên Kho.
- Mỗi tài liệu thiết kế có một dòng trạng thái đầu tệp ("Đã làm tới vX; phần còn lại xem ROADMAP"); bỏ hoặc đánh dấu "lịch
  sử" các đoạn về Jules và câu "Gen không dùng Playwright".
- README cập nhật dịch vụ, genh, Gen. Nhật ký thay đổi nên đổi tên (vd `docs/CHANGELOG.md`); việc này cần sửa `CLAUDE.md`,
  Boss duyệt.
- Bỏ con số phiên bản ra khỏi chú thích trong mã (ghi "bản sau" kèm VIEC-n).
