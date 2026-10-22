# Gen v1 — Trợ lý quản trị trong Console (thiết kế + hiện trạng)

> Trạng thái (10/2026): đã thi công tới v0.1.50 — xem [CHANGELOG.md](../../CHANGELOG.md); phần còn lại ở [ROADMAP](../ROADMAP.md) mục Nợ.
> Gen chủ động nhắc việc, mẹo và bài học mỗi ngày (v0.1.54): xem [gen-coach.md](gen-coach.md).

> Thiết kế gốc 29/09/2026 (Sếp đã duyệt, §9) · Phạm vi: Gen-Harness Console (apps/web + apps/api)
> Gen = trợ lý mặc định ở khung chat bên phải, vai trò "quản trị/vận hành trong app".
> Gen KHÔNG phải lập trình viên (không sửa code), KHÔNG phải agent thị trường bên ngoài (Zalo/WhatsApp).

## 1. Gen làm được gì (v1)

- Sếp hỏi "hôm nay có gì gấp?" → Gen tóm tắt Tổng quan + Hộp thư ý nghĩa, mở đúng màn và chỉ vào dòng cần xem.
- Sếp hỏi về một khách/nhóm/cơ hội → Gen tra dữ liệu Sếp được xem, trả lời kèm link chứng cứ gốc.
- Sếp xin báo cáo (tuần, theo nhân viên, theo kênh) → Gen soạn bản tóm tắt ngắn trong khung chat, có số liệu.
- Sếp hỏi "làm sao để…" (kết nối Zalo, thêm khoá API, đổi mức tự trị) → Gen dẫn đi từng bước:
  mở trang, làm sáng (glow) đúng nút cần bấm, chờ Sếp bấm rồi sang bước kế.
- Sếp hỏi "sao bản nháp này bị giữ?" → Gen đọc lý do giữ (luật chờ duyệt) và giải thích bằng lời thường.
- Cuối mỗi câu trả lời, Gen đề xuất 1–3 việc nên làm tiếp (nút bấm được: "Mở", "Chỉ cho tôi", "Làm giúp" [v2]).
- Mọi thứ Gen làm (mở trang, tra dữ liệu, đề xuất) đều ghi vào Nhật ký hành động — Sếp xem lại được.
- v2: Gen điền sẵn form và thao tác thay Sếp — luôn hỏi xác nhận, việc nhạy cảm vẫn cần PIN.

**Ví dụ 1 — Tìm việc gấp**
> Sếp: Sáng nay có gì cần tôi xử lý?
> Gen: Có 3 bản nháp đang chờ duyệt, 1 cái vượt ngưỡng 50 triệu. *(mở "Hộp thư ý nghĩa", lọc "Chờ duyệt")*
> Gen: Cái quan trọng nhất là báo giá cho Anh Tuấn — 82 triệu. *(làm sáng dòng đó, bong bóng: "Vượt ngưỡng → cần Sếp duyệt")*
> Gen: Sếp muốn xem chứng cứ gốc không? [Mở chứng cứ] [Để sau]

**Ví dụ 2 — Dẫn đi từng bước**
> Sếp: Chỉ tôi cách thêm khoá Gemini.
> Gen: Bước 1/3 — *(mở "Agent & Model" → thẻ Nhà cung cấp; làm sáng nút "Thêm khoá")* Sếp bấm vào đây.
> *(Sếp bấm → Gen nhận sự kiện)* Bước 2/3 — *(làm sáng ô "Khoá API")* Dán khoá vào ô này.
> Bước 3/3 — *(làm sáng nút "Kiểm tra")* Bấm để thử; xanh là xong. Thao tác này sẽ hỏi PIN.

**Ví dụ 3 — Hỏi về nhân sự**
> Sếp: Tuần này bạn Lan chăm khách thế nào?
> Gen: Điểm chăm sóc của Lan 7.8 (tuần trước 8.4), 2 khách phản hồi chậm >2 giờ.
> *(mở "Chất lượng chăm sóc", làm sáng hàng của Lan, rồi làm sáng cột "Thời gian phản hồi")*
> Gen: Đề xuất: [Xem 2 cuộc hội thoại] [Tạo nhắc cho Lan]

## 2. Không làm gì / ranh giới

- **Gen chỉ có quyền bằng người đang dùng, không hơn.** Manager hỏi thì Gen chỉ thấy team của Manager;
  Auditor hỏi thì Gen chỉ đọc. Dữ liệu nhạy cảm vẫn bị che với vai trò dưới Owner (khoá cứng hiện có).
- **v1 chỉ đọc + dẫn đường.** Gen không bấm nút, không gửi tin, không sửa gì. Sếp là người bấm.
- **v2 (làm thay)**: Gen chỉ điền sẵn; Sếp bấm "Xác nhận". Việc cần PIN (đổi chính sách, khoá, agent…)
  vẫn hiện hộp PIN như cũ — Gen không bao giờ giữ hay nhập PIN.
- **Không vượt thang tự trị & 8 ranh giới**: gửi ra ngoài, có tiền vượt ngưỡng, liên quan nhân sự → luôn chờ
  duyệt, dù Gen đề xuất. Gen không tự ra quyết định nhân sự.
- **Không đụng mã nguồn, máy chủ, deploy, shell** — đó là việc của Dev Claude.
- **Không nói chuyện với khách bên ngoài** — đó là việc của các agent thị trường.
- **Riêng tư**: hội thoại của ai người đó xem; Owner xem được nhật ký hành động của Gen (không xem nội dung
  chat riêng của nhân viên trừ khi bật cấu hình lưu vết — câu hỏi mở #3).

---

## 3. Kiến trúc

```
apps/web  GenPanel (khung phải) ──WS /api/v1/ws (sự kiện gen.*)──┐
            │ GenDirector: thực thi UI-action đã kiểm             │
            │ targets registry (data-gen-target)                  │
            └── POST /api/v1/gen/turns ──► apps/api gh/gen/ ──────┘
                                           ├─ planner (ModelRouter, agent_key "core.gen")
                                           ├─ tools: data (đọc, RBAC) + ui (navigate/highlight/tour/prefill)
                                           ├─ validator (schema + screens registry + targets + quyền)
                                           └─ actionlog.record() mỗi bước
```

### 3.1 Web — khung chat
- `apps/web/src/gen/GenPanel.tsx`: khung phải trong `shell/AppShell.tsx`, bật/tắt bằng nút ở `Header.tsx`,
  trạng thái mở/đóng trong `lib/uiStore.ts`. Hiển thị tin, "đang nghĩ", thẻ đề xuất, nút bước tour.
- `apps/web/src/gen/director.ts`: nhận UI-action đã kiểm từ server, gọi `navigateTo()` (`lib/navigation.ts`),
  chờ phần tử có target xuất hiện (MutationObserver, timeout 4s), rồi vẽ spotlight.
- `apps/web/src/gen/Spotlight.tsx`: lớp phủ mờ + viền glow quanh `getBoundingClientRect()` của target, bong bóng
  thông điệp; tự cuộn vào tầm nhìn; Esc để thoát; tôn trọng `prefers-reduced-motion`.

### 3.2 Gen API
- Module mới `apps/api/gh/gen/` (routes, planner, tools, validator, store), gắn `/api/v1/gen` trong `app.py`.
- `POST /gen/turns {conversation_id, text, context:{route, screen_key, visible_targets[]}}` → 202 + turn_id.
- Kết quả đẩy qua **WS sẵn có** (`gh/realtime.py`): đăng ký `register_event("gen.delta"|"gen.action"|
  "gen.done", None)` và publish kèm `org_id`; thêm lọc theo `user_id` người nhận (Hub hiện chỉ lọc org + quyền
  → cần thêm trường `to_user` trong `dispatch`). Không cần SSE riêng; fallback: `GET /gen/turns/{id}` polling.
- Client gửi lại sự kiện tour: `POST /gen/turns/{id}/ack {step, outcome: done|skipped|target_missing}`.
- *Tên thực tế trong mã (10/2026)*: `planner` = `gh/gen/engine.py`; envelope + kiểu bước = `envelope.py`; kiểm server-side = `validator.py`; đề xuất = `proposals.py`;
  Bản tin = `briefing.py`; sự kiện WS là `gen.step` / `gen.done` (lọc `to_user`).

### 3.3 Gọi model & giao thức hành động có kiểu
- `ModelRouter.generate()` hiện trả **một khối text JSON** (`json_mode=True`), chưa có function-calling gốc và
  chưa stream. v1 dùng vòng lặp "plan → tool → observe" tự quản, mỗi lượt model trả **một envelope JSON**:

```ts
// packages/contracts/src/gen.ts (mới) — nguồn chung cho web + test; API có Pydantic tương ứng
type GenStep =
  | { kind: 'say'; text: string }
  | { kind: 'tool'; name: DataToolName; args: Record<string, unknown> }     // đọc dữ liệu
  | { kind: 'ui'; action: UiAction }
  | { kind: 'suggest'; items: { label: string; action: UiAction | { tool: string } }[] }
  | { kind: 'done' };
type UiAction =
  | { type: 'navigate'; screen: ScreenKey; params?: Record<string, string> }
  | { type: 'highlight'; target: GenTargetId; message: string; waitFor?: 'click' | 'none' }
  | { type: 'tour'; steps: { screen?: ScreenKey; target: GenTargetId; message: string }[] }
  | { type: 'prefill'; form: GenFormId; fields: Record<string, unknown> };   // v2, cần xác nhận
```
- Tối đa 6 vòng/lượt, trần token theo `agent.bindings` của `core.gen`. Envelope sai schema → hỏi lại model 1 lần,
  vẫn sai → trả lời "Gen chưa hiểu, Sếp hỏi lại giúp" (không thực thi gì).
- *Ghi chú 10/2026*: `prefill` (v2) chưa có trong mã — "làm thay" đi bằng bước `propose` (đề xuất + Xác nhận), xem §10.

### 3.4 Công cụ dữ liệu (chỉ đọc)
- Mỗi tool là **một lớp bọc mỏng quanh endpoint GET đã có**, gọi nội bộ bằng chính `CurrentUser` của người hỏi
  → tái dùng RBAC/che dữ liệu sẵn có, không viết truy vấn SQL riêng cho Gen.
- v1: `overview.summary`, `queue.list` (+lọc), `draft.get` (kèm lý do giữ), `profile.search/get`,
  `opportunity.list`, `people.care`, `audit.list`, `system.health` (chỉ khi có `system.read`), `guide.list`
  (đọc `guideContent.ts` đã xuất sang JSON), `screens.list` (từ `packages/contracts/src/screens.ts`).
- Kết quả tool được cắt gọn (≤ 4 KB, 20 dòng) trước khi đưa model; số điện thoại dài vẫn che theo vai trò.

### 3.5 Khai báo mục tiêu làm sáng
- Thuộc tính `data-gen-target="queue.row.approve"` gắn trên phần tử React. Nguồn sự thật:
  `packages/contracts/src/genTargets.ts` = `{ id, screen, label, description, dynamic?: 'row' }`.
  Mục tiêu động dùng hậu tố: `queue.row:<item_id>` (registry khai mẫu, id thật do tool trả về).
- Test đơn vị quét `apps/web/src` đảm bảo mỗi id trong registry có ít nhất một chỗ gắn, và ngược lại
  (giống `test/unit/screens.test.ts` đang kiểm screens.json) — "không khai báo hai nơi".
- Prompt hệ thống chỉ đưa model danh sách target **của màn đang bàn** (tiết kiệm token, giảm bịa).

### 3.6 Kiểm server-side trước khi gửi xuống web
1. Envelope khớp schema (Pydantic, `extra="forbid"`).
2. `screen` có trong registry VÀ `rbac.can_see_screen(user.permissions, screen)`.
3. `target` có trong genTargets và thuộc đúng `screen`; id động phải là id vừa xuất hiện trong kết quả tool
   của lượt này (chống model bịa id).
4. `prefill` (v2): form nằm trong danh sách cho phép; giá trị qua cùng validator của endpoint ghi; không bao giờ
   gửi lệnh "submit" — chỉ người bấm.
5. Không đạt → bỏ action, ghi Action Log `result="blocked"`, model được báo lý do ở vòng kế.

## 4. Dữ liệu & quyền

- **Danh tính**: Gen chạy với `CurrentUser` của phiên đang đăng nhập (cookie WS/HTTP). Không có token riêng,
  không có quyền riêng, không có "chế độ admin".
- **Nhật ký**: mọi bước đi qua `gh/chassis/actionlog.record()` (đường ghi duy nhất, chuỗi băm):
  `actor_type="agent"`, `actor_id="gen"`, `action="gen.navigate|gen.highlight|gen.tour|gen.query|gen.suggest|
  gen.prefill"`, `target_type="screen|record"`, `autonomy_level=1` (v1) / `4` (v2 prefill),
  `detail={on_behalf_of: user_id, conversation_id, turn_id, tool, args_digest, model, provider}`.
  Không ghi nội dung câu hỏi/câu trả lời vào Action Log (đã có bảng hội thoại) — chỉ digest.
- **Lưu hội thoại**: bảng mới `agent.gen_conversations(id, org_id, user_id, title, created_at, last_at)` và
  `agent.gen_messages(id, conversation_id, role, content jsonb, created_at)`; chỉ chủ hội thoại đọc được.
  Hạn lưu mặc định 90 ngày (job dọn trong `gh/worker.py`), Owner chỉnh được; nằm trong `backup.py`.
- **Gọi model**: mỗi lượt đã ghi `agent.model_calls` qua ModelRouter; dữ liệu gửi model đi qua cùng lớp che
  như UI (`mask_text`). Owner có thể chọn chỉ dùng model cục bộ/CLI cho Gen (câu hỏi mở #2).

## 5. Chọn model

- **Hiện tại**: ModelRouter, khoá mục đích mới `core.gen` trong `CORE_AGENT_KEYS` (`agents_api/routes.py`) →
  Owner gán chuỗi model ở màn "Agent & Model" như các agent khác; tận dụng xoay vòng khoá, hạn mức, ngắt mạch,
  chuyển hướng (Antigravity CLI `AgyClient`, Gemini, OpenAI-compat).
- **Chỗ cắm System One (Jev, `/v1/systemone`)** — mô hình quyết định nhanh, đầu ra có kiểu `Choice`/`Score`:
  - `intent`: phân loại câu hỏi → {hỏi dữ liệu, dẫn đường, báo cáo, ngoài phạm vi}; chọn bộ tool/target cần nạp.
  - `next_ui_action`: khi đã biết màn, chọn target từ danh sách hữu hạn (Choice) thay vì để LLM lớn tự viết id.
  - `triage`: chấm điểm (Score) dòng dữ liệu nào đáng nêu trước trong tóm tắt.
  - Giao diện: `gh/gen/decider.py` với `Decider` protocol; bản mặc định `LlmDecider` (ModelRouter, agent_key
    `core.intent` đã có sẵn trong danh sách); bản `JevDecider` bật bằng cờ cấu hình.
- **Fallback**: Jev lỗi/timeout (>800 ms) hoặc điểm tin cậy thấp → dùng `LlmDecider`; ModelRouter hết chuỗi
  (`ModelUnavailable`) → Gen trả lời tĩnh "chưa có model" + làm sáng màn cấu hình model (không gọi model).

## 6. Kế hoạch giao hàng

Mỗi lát ~1–2 ngày, tự merge được, có cờ `gen.enabled` (thực tế mặc định **bật** cho Owner — `gh/gen/store.py`, `DEFAULTS`).

| # | Lát | Kết quả nhìn thấy |
|---|-----|-------------------|
| 1 | Contracts `gen.ts` + `genTargets.ts`, 15 target đầu (Tổng quan, Hộp thư, Agent & Model) + test quét | Chưa có UI |
| 2 | `Spotlight` + `director` + dev-panel gửi action tay | Làm sáng/tour chạy bằng mock |
| 3 | `gh/gen` routes + lưu hội thoại + WS `gen.*` (lọc theo user) + Action Log | Chat vọng lại, log đủ |
| 4 | Planner qua ModelRouter `core.gen`, 3 tool đọc (overview, queue, draft) + validator | Ví dụ 1 chạy thật |
| 5 | Tour nhiều bước + ack; nạp `guideContent` làm tri thức dẫn đường | Ví dụ 2 chạy thật |
| 6 | Thêm tool profile/people/opportunity/audit, thẻ đề xuất | Ví dụ 3 + báo cáo |
| 7 | `Decider` + cắm Jev sau cờ; số đo | Đo được độ trễ/độ chính xác |
| v2 | `prefill` + xác nhận + PIN, form cho phép theo danh sách | Làm thay có xác nhận |

**Test**
- Unit API (pytest): validator (screen/target/quyền/id bịa), RBAC tool theo 5 vai trò, Action Log đúng
  `actor_type/on_behalf_of`, envelope sai → không thực thi. Model giả trả kịch bản JSON cố định.
- Unit web (vitest): director chờ target, timeout → báo `target_missing`; registry ↔ `data-gen-target`.
- E2E Playwright **chạy trên mock** (`apps/web/test/mock-api.ts`, `mock-ws.ts` sẵn có — thêm `mock-gen.ts`):
  kịch bản 3 ví dụ ở mục 1 → khẳng định URL đổi, spotlight bao đúng phần tử, bước sau chỉ chạy khi đã bấm;
  thêm ảnh chụp vào `e2e/visual.spec.ts`.

**Số đo** (bảng `agent.model_calls` + Action Log): thời gian tới chữ đầu (<2.5 s p50), tỉ lệ action bị chặn
(<5%), tỉ lệ `target_missing` (<2%), tour hoàn thành/bắt đầu, câu trả lời được bấm đề xuất, chi phí token/lượt,
đánh giá 👍/👎 mỗi câu.

## 7. Liên kết Gen-hub (v2+)

> Trạng thái (10/2026): đọc Kho ✅ v0.1.26; đọc lịch/mail/việc/Drive ✅ v0.1.49 (QD-16); **ghi Kho (Phiên, Việc) có Xác nhận + mã PIN ✅ v0.1.50 (QD-18)** — §11;
> thẻ kanban, warroom, Gmail, Lịch chưa làm (ROADMAP › Nợ #11). Chi tiết: [gen-hub-link.md](gen-hub-link.md).

- Gen-hub (Kho, warroom, kanban) là **chỗ làm việc chung** của Sếp · Dev Claude · Gen · agent bên ngoài.
- Gen đọc Kho (Việc/Quyết định) để trả lời "việc gì đang mở?"; Gen **đề xuất** tạo thẻ kanban (vd "Lỗi màn X"
  → giao Dev Claude) — tạo thật cần Sếp bấm, ghi Action Log + Kho.
- Warroom: Gen đăng tóm tắt ngày khi Sếp bật; Dev Claude đăng "đã phát hành bản mới" → Gen nhắc Sếp trong app
  và mở tour tính năng mới (tour lấy từ ghi chú phát hành).
- Agent thị trường bên ngoài báo cáo qua hàng đợi hiện có; Gen chỉ đọc/tóm tắt, không điều khiển chúng.
- Cầu nối qua MCP (`gh/mcp_api`, `chassis/mcp_client.py`) chỉ đọc; ghi Kho (v0.1.50) đi **đường riêng**: đề xuất → Xác nhận + mã PIN → permit ký → `POST /hub/kho/write`
  (gen-hub-link.md §7). Tool ghi khác bị route MCP chung chặn (403 `HUB_TOOL_NOT_ALLOWED`).

## 8. Rủi ro & câu hỏi mở cho Sếp

Rủi ro chính: model bịa id/trang (chặn bằng validator + registry); giao diện đổi làm target mất (test quét +
`target_missing`); độ trễ khi model chậm (hiện "đang nghĩ", Jev cho bước nhanh); rò dữ liệu qua model ngoài
(che như UI, tuỳ chọn chỉ dùng model cục bộ).

1. Gen có mặc định bật cho **mọi vai trò** hay chỉ Owner trước?
2. Dữ liệu khách có được gửi sang model đám mây (Gemini/API), hay Gen chỉ dùng Antigravity CLI/model cục bộ?
3. Owner có được đọc **nội dung** hội thoại Gen của nhân viên không, hay chỉ Nhật ký hành động?
4. Lưu hội thoại bao lâu — 90 ngày có ổn?
5. v2 "làm thay": những việc nào Sếp muốn Gen làm trước (duyệt nháp, tạo nhắc, gán người phụ trách…)?

## 9. Quyết định đã chốt (29/09/2026 — Sếp giao Claude quyết)
1. Gen bật cho **Owner trước**; vai trò khác mở ở v1.x khi đã ổn định.
2. Dùng model theo **chuỗi Bộ não AI hiện có** (Antigravity CLI / khoá API); dữ liệu gửi model được che như trên UI.
3. Owner **không đọc nội dung** chat Gen của nhân viên — chỉ xem Nhật ký hành động.
4. Lưu hội thoại **90 ngày**.
5. v2 "làm thay" ưu tiên: **duyệt/nháp tin gửi đi → tạo nhắc việc → gán người phụ trách**.
6. **Jev (System One)** là một *nguồn model* mới (OpenRouter `typesafe/jev-*` hoặc TypeSafe API), không phải một vai: Gen dùng làm bộ quyết định nhanh (ý định, bước UI kế tiếp); Sàng lọc dùng làm lớp lọc đầu (rác, trùng, chấm điểm). Lỗi/chậm → rơi về model lớn.
7. **QD-16 (09/10/2026)**: Gen đọc lịch, mail, việc, Drive Google qua Gen-hub — chỉ đọc, chỉ Owner, đã che (gen-hub-link.md §6).
8. **QD-18 (09/10/2026)**: Gen đề xuất ghi Kho (Phiên, Việc) và ghi nhớ sở thích của Sếp; chỉ ghi khi Sếp Xác nhận (+ mã PIN với Kho). Thay "Gen ghi Gen-hub để sau" (§11).

## 10. v2 bước 1 — Đề xuất thao tác có xác nhận (A4, v0.1.24)

Thứ tự theo §9.5: **nháp tin gửi đi → nhắc việc → gán người phụ trách**. Gen vẫn **không tự ghi**.

- **Model** trả bước `{"kind":"propose","proposal":{"type":"<loại>","fields":{…}}}` với `<loại>` ∈ `draft_message`, `reminder`, `assign` (v0.1.24), `social_reply`, `social_dm` (v0.1.47),
  `memory_note`, `kho_create`, `kho_update` (v0.1.50) — bảng đầy đủ ở cuối mục này
  (envelope có kiểu, `extra="forbid"` — `gh/gen/envelope.py`). Mọi id (đối tượng, việc, mục hộp thư, người được giao)
  phải vừa xuất hiện trong kết quả tool của **chính lượt đó** (tool mới `task.list`, `staff.list` → `GET /gen/assignees`).
- **Server** (`gh/gen/proposals.py`) kiểm: quyền của loại (`action.draft` / `queue.act`) + mục tiêu registry gắn với đề xuất
  (`workbench.drafts` nhạy cảm, `tasks.new` cần `queue.act`, `tasks.row:<id>`, `inbox.row:<id>`) → màn được xem, quyền riêng
  của mục tiêu, cờ `sensitive` ⇒ `requires_pin`. Tóm tắt trên thẻ do **hệ thống** viết từ trường đã kiểm (không dùng lời
  model). Đề xuất lưu Redis 24 giờ (riêng đề xuất Phiên của F-87 sống 7 ngày); tối đa 3 đề xuất/lượt; nhắc việc giờ đã qua → chặn.
- **Web** hiện thẻ (`ProposalCard`): tóm tắt + trường điền sẵn, **Xác nhận / Sửa / Huỷ**. Sửa chỉ các trường cho phép
  (`GEN_PROPOSAL_EDITABLE`); id đối tượng/việc bị khoá.
- **Xác nhận** → `POST /gen/proposals/{id}/confirm {fields}`: kiểm lại tất cả (cờ Gen + vai trò như v1, quyền, PIN nếu
  nhạy cảm → 423 và client tự hỏi PIN), rồi gọi **nội bộ** endpoint sẵn có bằng phiên + CSRF của người bấm:
  `POST /drafts` (bản nháp **chờ duyệt**, chưa gửi), `POST /tasks` (có `remind_at`), `PATCH /tasks/{id}`,
  `POST /inbox/{id}/assign`. Endpoint từ chối → lỗi trả nguyên, đề xuất vẫn chờ. **Huỷ** → `/cancel`.
- **Action Log**: `gen.propose` (actor agent/gen, on_behalf_of), `gen.proposal_confirmed` / `gen.proposal_cancelled`
  (actor_type **user**, `detail.via = "gen"`, endpoint, có sửa hay không); bị chặn/thất bại ghi `result=blocked|failed`.
- **Nhắc việc đến giờ**: không thêm hệ thống mới — `biz.tasks.remind_at` (có sẵn) + cột `reminded_at` (migration 0018);
  job `task_reminder_scan` mỗi phút gửi thông báo chuông (`core.notifications`, 0017) cho người phụ trách (chưa giao → Owner).

**Các loại đề xuất hiện có (8)** — mọi loại: Gen chỉ đề xuất, thẻ do **hệ thống** viết từ trường đã kiểm, Sếp Xác nhận / Sửa / Huỷ:

| Loại | Thẻ | Quyền | Mã PIN | Khi Xác nhận gọi |
|---|---|---|---|---|
| `draft_message` | Soạn nháp tin gửi đi | `action.draft` | khi mục tiêu nhạy cảm | `POST /drafts` (nháp **chờ duyệt**, chưa gửi) |
| `reminder` | Tạo nhắc việc | `queue.act` | không | `POST /tasks` (có `remind_at`) |
| `assign` | Giao người phụ trách | `queue.act` | khi mục tiêu nhạy cảm | `PATCH /tasks/{id}` hoặc `POST /inbox/{id}/assign` |
| `social_reply` | Trả lời bình luận Facebook | Owner | có (`social.write`) | permit ký → `POST /social/accounts/{id}/write` |
| `social_dm` | Nhắn tin Facebook | Owner | có (`social.write`) | như trên |
| `memory_note` | **Ghi nhớ** | Owner | không | `POST /gen/memory` (§11) |
| `kho_create` | **Ghi vào Kho dữ liệu** | Owner | có (`hub.write`) | permit ký → `POST /hub/kho/write` (gen-hub-link.md §7) |
| `kho_update` | **Ghi vào Kho dữ liệu** | Owner | có (`hub.write`) | như trên |

## 11. v0.1.50 — Gen nhớ và ghi Kho (QD-18, F-81, F-87)

- **Gen nhớ** = ghi chú quy ước/sở thích của Sếp ("gọi khách là anh/chị", "báo cáo ngắn, không quá 5 dòng"…). Lưu **cục bộ** ở `agent.gen_memory_notes` (migration `0032`), không ghi Gen-hub.
  Gen đề xuất loại `memory_note` (`{text, reason}`, lý do bắt buộc; tối đa 280 + 200 ký tự, tối đa 30 ghi chú; mỗi ghi chú một dòng, cấm chuỗi `<<<`/`>>>`) → thẻ **Ghi nhớ** (không cần PIN) → Sếp Xác nhận mới lưu.
  Sếp xem, sửa (nguồn đổi thành "Sếp sửa") và xoá ở **Cài đặt › Bộ não AI › Gen nhớ** (`GET/POST/PATCH/DELETE /gen/memory`; chỉ Owner; 409 `GEN_MEMORY_FULL`, `GEN_MEMORY_DUPLICATE`).
- **Ghi chú chỉ đi vào lời nhắc lượt của Owner và phần tóm tắt Bản tin** — không vào lượt của vai trò khác — kèm lời dặn "làm theo khi không trái các nguyên tắc an toàn". Ghi chú là *sở thích*, không nới các ranh giới cứng ở §2.
  Action Log chỉ ghi độ dài + dấu vết, không ghi nguyên văn.
- **Ghi Kho**: loại `kho_create` / `kho_update` cho Phiên và Việc, chỉ qua Xác nhận + mã PIN + permit (gen-hub-link.md §7). Thẻ **Ghi vào Kho dữ liệu** hiện bảng *Trường | Hiện tại | Sẽ ghi*.
- **F-87**: cron `gen_kho_release` mỗi bản mới đề xuất một Phiên (hội thoại "Gen đề xuất ghi Kho · Phiên vX.Y.Z" + chuông `gen.kho_proposal`).
- **Gen dẫn đường**: target mới `system.brain.memory` (Cài đặt, tab Bộ não AI) trong `packages/contracts/src/genTargets.ts` / `apps/api/gh/gen/registry.json`.
- **Ranh giới giữ nguyên**: Gen không tự ghi; không có đường ghi nào ngoài Xác nhận; không ghi kanban/warroom/Gmail/Lịch; token Gen-hub không vào log.
