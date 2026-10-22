# Gen hướng dẫn — Gen chủ động nhắc việc, giới thiệu tính năng, dạy mỗi ngày một bài (thiết kế + hiện trạng)

> Trạng thái (10/2026): **đã thi công ở v0.1.54** (Boss duyệt 10/10/2026). **v0.1.55 đổi số dòng bắt buộc**: "Việc Sếp cần làm" có 10 dòng nhưng chỉ dòng `ai` (có ít nhất 1 nguồn AI chạy được) bắt buộc — nguồn sự thật là `ROWS` trong `apps/api/gh/boss_checks/service.py`; `.github/scripts/check_doc_facts.py` giữ tài liệu này khớp mã. Bản tóm tắt ở [CHANGELOG.md](../../CHANGELOG.md), chi tiết phát hành ở [v0.1.54.md](../releases/v0.1.54.md),
> nợ còn lại ở mục 18 dưới đây và [ROADMAP](../ROADMAP.md). Gen nói chung: [gen-v1.md](gen-v1.md). Tài liệu này đã cập nhật theo mã thật sau khi gộp ba gói
> (g1-api, g2-web, g3-noi-dung): tên mô-đun, payload, mã lỗi, từ vựng tín hiệu, 16 mục tiêu làm sáng đều là của mã đang chạy.

## 1. Vì sao

Console có rất nhiều việc vận hành mà chỉ Sếp tự tay làm được: 10 dòng "Việc Sếp cần làm" (từ v0.1.55 chỉ 1 dòng bắt buộc: có ít nhất 1 nguồn AI chạy được), 9 việc thiết lập tuỳ chọn, lịch sao lưu, bản sao ngoài máy, token Gen-hub sắp hết hạn,
bản nháp chờ duyệt, sự cố sức khoẻ… Trước v0.1.54 Gen chỉ trả lời khi được hỏi, nên việc nào Sếp quên thì nằm im cho tới khi thành sự cố. Sếp cũng không có cách biết Console còn tính năng nào chưa dùng.

v0.1.54 cho Gen **chủ động nhưng nhẹ nhàng**: một thẻ "Hôm nay của Sếp" ở đầu khung Gen với tối đa 3 việc cần làm ngay, một mẹo "Sếp biết chưa?", một "Bài học hôm nay · k/19";
một chuông mỗi ngày khi có việc khẩn; một dòng trong Bản tin cho biết đã đạt bao nhiêu việc bắt buộc. Tất cả đều **khuyên chứ không ép** (QD-12): Sếp luôn có đường lùi.

## 2. Nguyên tắc và ranh giới cứng

- **Chỉ Owner (Sếp).** Vai trò khác gọi `/gen/coach/*` nhận 403 `FORBIDDEN`; web không vẽ thẻ và không gọi API cho vai trò khác.
- **0 gọi model, 0 ghi `agent.gen_messages`** ở `/gen/coach/*`, ở cron `gen_coach` và ở dòng "Việc bắt buộc" của Bản tin. Mọi chữ trên thẻ là chữ tĩnh trong mã hoặc trong `content/*.json`.
- **Payload hẹp**: chỉ khoá, tiêu đề tĩnh, câu "vì sao" tĩnh, số đếm. Không bao giờ trả chi tiết sự cố, thông điệp, email hay token.
- **Chỉ đọc tín hiệu**: dựng thẻ không ghi gì vào dữ liệu nghiệp vụ. Ghi duy nhất là trạng thái của chính Gen hướng dẫn (mục 12) và mốc `stable_since`.
- **Khuyên không ép**: mỗi việc có "Để mai"; việc P1/P3 có "Không dùng việc này" (có câu nói rõ hậu quả + xác nhận); cả thẻ có "Hoãn tất cả 1/3/7 ngày" và "Tắt hướng dẫn"; mọi lựa chọn bật lại được ("Bật lại").
  Ngoại lệ duy nhất: **việc khẩn (P0) không tắt được** — đó là sự cố đang làm hỏng việc hằng ngày.
- **Không đẩy ra ngoài Console**: chuông chỉ trong Console (không Telegram), tối đa một chuông mỗi Owner mỗi ngày, có giờ yên lặng.
- **Không hứa tính năng không có**: chữ trong bài/mẹo phải khớp màn thật; test nội dung chặn lỗi hình thức, còn việc đối chiếu nhãn nút với màn do người viết làm và ghi vào báo cáo gói (mục 7.3).

## 3. Thuật ngữ cố định (UI và tài liệu)

Gen xưng "em", gọi "Sếp". Tên và nhãn dưới đây là chữ trên màn thật; không đổi nếu không đổi cả web.

| Chỗ | Chữ |
|---|---|
| Tên tính năng | **Gen hướng dẫn** |
| Thẻ ở đầu khung Gen | **Hôm nay của Sếp**; ba khối: **Việc cần làm ngay** · **Sếp biết chưa?** · **Bài học hôm nay · k/19** |
| Nút của một việc | **Chỉ cho em** · **Để mai** · **Không dùng việc này** |
| Nút của mẹo | **Thử ngay** · **Đã hiểu** |
| Nút của bài học | **Làm thử** · **Đã hiểu** · **Hỏi Gen thêm** · **Hoãn** |
| Chân thẻ | **Hoãn tất cả 1/3/7 ngày** · **Tắt hướng dẫn** · nút **Bật lại** (trong Cài đặt) |
| Trạng thái | **Hệ thống đã ổn định** |
| Trợ giúp | thẻ **Lộ trình học cùng Gen** (19 bài, nút **Làm thử** / **Học lại**) |
| Cài đặt › Bộ não AI | thẻ **Gen hướng dẫn**; danh sách **Việc Sếp đã chọn không dùng** |
| Tổng quan (Hôm nay) | **Đã đạt x/N việc bắt buộc** |
| Dòng Bản tin | **Việc bắt buộc: đã đạt x/N, xem Việc Sếp cần làm** |

## 4. Kiến trúc và đường dẫn mã

```
apps/api/gh/gen/coach/
  signals.py   đọc tín hiệu hệ thống (chỉ đọc, cache 60 giây) + TODO_RULES, TODO_TARGETS, TOPICS, câu tĩnh
  lessons.py   nạp content/*.json (kiểm schema), sinh 9 bài G05..G14 từ registry, curriculum() 19 bài, eval_cond
  engine.py    THUẦN (không I/O, đồng hồ truyền vào): build_today, plan_today, update_stable, coach_intent, prompt_block
  store.py     CSDL: agent.gen_coach_prefs, agent.gen_coach_items (migration 0033)
  routes.py    router prefix /gen/coach (chỉ Owner)
  cron.py      run_coach() — job gen_coach (chuông)
  content/lessons.json   10 bài N01–N10 (gói nội dung)
  content/tips.json      6 mẹo (gói nội dung)
apps/api/gh/gen/briefing.py        thêm bước "Việc bắt buộc: đã đạt x/N…" (chỉ bước hiển thị)
apps/api/gh/gen/engine.py, tools.py, envelope.py   tool coach.status + khối "VIỆC VẬN HÀNH ĐANG DỞ" vào system prompt
apps/api/gh/setup/routes.py        tách follow_up_status() để dùng chung với signals (đầu ra /setup/follow-up không đổi)
apps/api/gh/worker.py              job gen_coach 09:05 / 11:05 / 14:05 (giờ Asia/Ho_Chi_Minh)
db/sql/0033_v0154_gen_coach.sql + apps/api/migrations/versions/0033_v0154_gen_coach.py
packages/contracts/src/gen.ts, genTargets.ts                          kiểu dữ liệu + 16 mục tiêu mới
apps/api/gh/gen/registry.json      bản xuất của genTargets.ts (chỉ sinh bằng GEN_WRITE=1 npx vitest run gen-targets)
apps/web/src/gen/{CoachTodayCard.tsx,coachModel.ts,coachQueries.ts}   thẻ, phần thuần, truy vấn
apps/web/src/gen/GenToggle.tsx (chấm đỏ), apps/web/src/shell/{AppShell,NotificationBell}.tsx
apps/web/src/help/CurriculumCard.tsx           Lộ trình học cùng Gen
apps/web/src/screens/system/GenCoachCard.tsx   Cài đặt › Bộ não AI › Gen hướng dẫn
apps/web/src/screens/queue/{SetupFollowUp,NeedsBossStrip}.tsx   "Để sau 7 ngày", x/N ở Tổng quan
```

Bốn lớp từ thuần tới có I/O: `signals` (đọc) → `engine` (thuần) → `store`/`routes`/`cron` (CSDL, API, job). `engine` không import CSDL; test dựng thẻ không cần Postgres.

## 5. Tín hiệu

`signals.collect(db, redis, org_id)` gom tín hiệu của MỘT tổ chức từ 11 nguồn có sẵn (`health`, `boss`, `followup`, `settings`, `offsite`, `hub`, `drafts`, `telegram`, `memory`, `api_key`, `pin`).
Cache Redis `gh:gen:coach:sig:<org_id>` TTL 60 giây (nhiều lần mở thẻ trong một phút chỉ đọc nguồn một lần). Mỗi nguồn bọc `asyncio.wait_for` 2 giây + try/except: nguồn lỗi thì **bỏ mục của nó**, thẻ vẫn trả;
tín hiệu chưa biết làm cả biểu thức điều kiện sai (không khẳng định điều chưa đọc được). Nguồn quyết định việc P0/P1 (`health`, `boss`, `followup`, `settings`, `hub`) lỗi ⇒ `p01_known()` sai ⇒ không đếm ngày ổn định (mục 9).

**Biểu thức điều kiện** = danh sách tên tín hiệu, AND, tiền tố `!` = phủ định (`lessons.eval_cond`). Danh sách rỗng ⇒ đúng.

**Từ vựng tín hiệu trạng thái** (`signals.STATE_SIGNALS`, mọi giá trị là boolean):

| Tín hiệu | Nghĩa |
|---|---|
| `model.bound` | Việc thiết lập bước 4 xong: Gen có model để trả lời |
| `api_key.present` | Có nguồn khoá API cho việc nền |
| `ai_budget.set` | Đã đặt trần chi phí AI mỗi ngày |
| `pin.set` | Owner đã đặt mã PIN |
| `telegram.briefing_on` | Telegram đang bật và công tắc bản tin 07:30/17:30 bật |
| `hub.kho_write_missing` | Lần Kiểm tra Gen-hub gần nhất báo thiếu quyền ghi Kho |
| `memory.empty` | Gen nhớ chưa có ghi chú nào |
| `backup.scheduled` | Đã có lịch sao lưu |
| `offsite.chosen` | Đã chọn nơi lưu bản sao ngoài máy |
| `drafts.any` | Có bản nháp chờ duyệt |
| `boss.<key>.done` | Dòng "Việc Sếp cần làm" đã Đạt — 10 khoá `ai`, `hub`, `facebook`, `agy`, `claude`, `jev`, `telegram`, `remote`, `facebook_reply`, `kho_write` (chỉ `ai` bắt buộc) |
| `followup.<n>.done` | Việc thiết lập tuỳ chọn n xong — n ∈ {5, 6, 7, 8, 9, 10, 11, 13, 14} |

Ngoài tín hiệu boolean, `Signals` giữ vài giá trị hẹp: sự cố đang mở (chỉ `kind`, `severity`, `link`, `raised_at`), `required_done/required_total`, số nháp chờ, số ngày còn lại của token Gen-hub.

**Chủ đề** (`signals.TOPICS`, dùng để một mẹo không nói trùng chuyện với một việc đang hiện): `model`, `hub`, `facebook`, `agy`, `claude`, `telegram`, `remote`, `backup`, `offsite`, `drafts`, `memory`, `ai_cost`, `setup`, `health`.

## 6. Việc cần làm ngay

Mỗi việc có **khoá**, **mức**, **đích làm sáng** (hoặc `link` của sự cố), tiêu đề + câu "vì sao" tĩnh. Bảng quy tắc `signals.TODO_RULES` (thứ tự có ý nghĩa: P0 > P1 > P2 > P3, cùng mức theo thứ tự bảng):

| Khoá | Mức | Khi nào | Đích (`TODO_TARGETS`) |
|---|---|---|---|
| `health.<kind>` | P0 nếu sự cố `bad`, P1 nếu `warn` | có sự cố sức khoẻ đang mở (gộp theo `kind`) | `link` của sự cố |
| `model.missing` | P0 | `!model.bound` | `api.bindings` |
| `boss.ai` (1 dòng bắt buộc duy nhất — sinh từ `boss_checks.service.ROWS`; từ v0.1.55 Gen-hub, Facebook, Google, Claude, Telegram, Truy cập từ xa là tuỳ chọn) | P1 | `model.bound` và `!boss.ai.done` (chưa có model thì `model.missing` P0 đã nói) | `connections.brain` |
| `backup.unset` | P1 | `!backup.scheduled` | `system.backup.schedule` |
| `hub.token_expiring` | P1 | token Gen-hub còn ≤ 7 ngày | `mcp.hub_link.token` |
| `drafts.pending` | P2 | `drafts.any` (tiêu đề có số bản nháp) | `workbench.drafts` |
| `boss.hub` | P3 | `!boss.hub.done` (gợi ý nhẹ, không chuông) | `boss_checks.row.hub` |
| `boss.remote` | P3 | `staff.active` và `!boss.remote.done` (chỉ nhắc khi đã có người dùng khác Owner) | `boss_checks.row.remote` |
| `followup.<n>`, n = 5..10 | P3 | `!followup.<n>.done` — **không đứng riêng** (v0.1.70): các việc còn hiện gộp thành MỘT việc `followup` "Việc thiết lập tiếp" ("Còn N việc tuỳ chọn", N đếm như thẻ cùng tên ở Tổng quan: mọi `followup.<n>.done` chưa xong, n ∈ 5–11, 13, 14) | `overview.setup_followup` (chính thẻ ở Tổng quan; khoá `followup.<n>` giữ cho hàng "Không dùng việc này" cũ và bài `G<n>`) |

Các việc thiết lập 11, 13, 14 không thành việc riêng vì trùng `backup.unset`, `boss.facebook`, `boss.hub`. Dòng "Việc Sếp cần làm" mới thêm sau này (chưa có câu riêng) vẫn có câu chung — thẻ không hỏng.

- **Xếp hạng và cắt**: bỏ việc đang hoãn ("Để mai") hoặc Sếp đã chọn không dùng (việc P0 không bao giờ bị bỏ), rồi lấy **tối đa 3** việc đầu.
- **`can_dismiss`** = P1 hoặc P3; kèm `dismiss_warning` là câu hậu quả tĩnh theo khoá (`DISMISS_WARNINGS`, có `_default`). P0 và P2 không tắt được (P2 chỉ "Để mai").
- **Tiêu đề sự cố** là chuỗi tĩnh theo `kind` (`COACH_HEALTH_TITLES`), phủ mọi `kind` trong `health.ACTIONS` **và `host.nightly`** — nên Gen hướng dẫn hoạt động đúng dù bản đang chạy đã có cảnh báo lịch đêm (v0.1.53) hay chưa:
  chưa có thì sự cố đó không bao giờ phát sinh, có rồi thì hiện đúng tiêu đề, không phụ thuộc thứ tự gộp nhánh.

## 7. Mẹo "Sếp biết chưa?" và bài học "Bài học hôm nay · k/19"

### 7.1 Mẹo (`content/tips.json`, đúng 6 mẹo)

Schema: `{key, topic, when: [biểu thức], title, body (1–3 câu, ≤ 300 ký tự), try: {label: "Thử ngay", target}}`. Chọn **một** mẹo: biểu thức `when` đúng, chưa hiện hoặc đã hiện hôm nay (giữ ổn định trong ngày), chủ đề không trùng một việc đang hiện, theo thứ tự trong tệp. Hệ thống ổn định thì không có mẹo.

| key | topic | when | đích "Thử ngay" |
|---|---|---|---|
| `telegram_briefing` | telegram | `boss.telegram.done`, `!telegram.briefing_on` | `system.channels.telegram` |
| `hub_kho_write` | hub | `boss.hub.done`, `hub.kho_write_missing` | `boss_checks.row.kho_write` |
| `memory_empty` | memory | `model.bound`, `memory.empty` | `system.brain.memory` |
| `ai_budget` | ai_cost | `api_key.present`, `!ai_budget.set` | `system.ai_cost` |
| `facebook_reply` | facebook | `boss.facebook.done`, `!boss.facebook_reply.done` | `boss_checks.row.facebook_reply` |
| `offsite_unset` | offsite | `backup.scheduled`, `!offsite.chosen` | `system.offsite.choose` |

### 7.2 Bài học (`content/lessons.json` + 9 bài sinh lúc chạy)

Lộ trình có **19 bài**: 10 bài nội dung `N01`–`N10` (do gói nội dung viết, schema `{id, order, title, body (3–5 câu, ≤ 600 ký tự), try?: {label: "Làm thử", target}, unlock?: [biểu thức], done_signal?}`)
và 9 bài `G05`–`G11`, `G13`, `G14` **sinh từ Hướng dẫn thiết lập** (`registry.guide`): tiêu đề = tên việc, nội dung = "vì sao" + nơi làm, `order` = n × 10, nút "Làm thử" tới `guide.item.do:<n>`, xong khi `followup.<n>.done`.
`curriculum()` sắp theo `order` (cùng order thì theo id), `k` = vị trí 1..19.

| id | order | Tên bài | Đích "Làm thử" | Mở khoá khi | Xong khi |
|---|---|---|---|---|---|
| N01 | 10 | Hỏi Gen mọi lúc | `help.ask_gen` | — | Sếp bấm Đã hiểu |
| N02 | 20 | Mã PIN của Sếp | `account.pin` | — | `pin.set` |
| G05 | 50 | (Kết nối Zalo / WhatsApp) | `guide.item.do:5` | — | `followup.5.done` |
| N03 | 55 | Bản tin sáng chiều qua Telegram | `system.channels.telegram` | `boss.telegram.done` | `telegram.briefing_on` |
| G06 | 60 | (Chọn nhóm cho agent lắng nghe) | `guide.item.do:6` | — | `followup.6.done` |
| N04 | 65 | Duyệt bản nháp ở Bàn làm việc | `workbench.drafts` | — | Sếp bấm Đã hiểu |
| G07 | 70 | (Bật sàng lọc dữ liệu) | `guide.item.do:7` | — | `followup.7.done` |
| N05 | 75 | Gen nhớ sở thích của Sếp | `system.brain.memory` | `model.bound` | Sếp bấm Đã hiểu |
| G08 | 80 | (Tạo agent đầu tiên) | `guide.item.do:8` | — | `followup.8.done` |
| N07 | 85 | Dải Cần Sếp xử lý | `overview.needs_boss` | — | Sếp bấm Đã hiểu |
| G09 | 90 | (Đặt mức tự trị cho agent) | `guide.item.do:9` | — | `followup.9.done` |
| N08 | 95 | Máy tự cập nhật mỗi đêm | `help.genh` | — | Sếp bấm Đã hiểu |
| G10 | 100 | (Mời người trong đội) | `guide.item.do:10` | — | `followup.10.done` |
| G11 | 110 | (Đặt lịch sao lưu) | `guide.item.do:11` | — | `followup.11.done` |
| N10 | 115 | Bản sao ngoài máy | `system.offsite.choose` | `backup.scheduled` | `offsite.chosen` |
| G13 | 130 | (Kết nối Facebook) | `guide.item.do:13` | — | `followup.13.done` |
| N09 | 135 | Gia hạn token Gen-hub | `mcp.hub_link.token` | `boss.hub.done` | Sếp bấm Đã hiểu |
| G14 | 140 | (Nối Gen-hub) | `guide.item.do:14` | — | `followup.14.done` |
| N06 | 145 | Cho Gen ghi vào Kho dữ liệu | `boss_checks.row.kho_write` | `boss.hub.done` | `boss.kho_write.done` |

Luật chọn bài hôm nay: bài kế tiếp trong lộ trình chưa hiểu / chưa xong / chưa hoãn, đã mở khoá, chưa đạt `done_signal`; giữ bài đã hiện hôm nay (không đổi giữa ngày); không quá `lessons_per_day` (0–2, mặc định 1; 0 ⇒ không bài).
Bài đã hiện ≥ 3 ngày mà Sếp chưa phản hồi coi như hoãn (quay lại sau 10 ngày kể từ lần hiện đầu). Bài `G<n>` bị bỏ khi việc `followup.<n>` đang nằm trong 3 việc hôm nay (nay: nằm trong việc gộp `followup` đang hiện) hoặc Sếp đã chọn không dùng việc đó (không nói hai lần một chuyện).

**"Để sau 7 ngày" của thẻ "Việc thiết lập tiếp"** (`card:setup_followup`, v0.1.70) ẩn luôn MỌI việc P3 của Gen (`followup`, `boss.hub`, `boss.remote`) cho tới hết hạn; việc P0/P1/P2 không bị ảnh hưởng.
Trạng thái bài: `new` · `shown` · `understood` · `snoozed` · `done` (`done_signal` đạt = xong thật, không cần bấm).

### 7.3 Quy ước viết nội dung

Tiếng Việt có dấu, Gen xưng "em" gọi "Sếp", câu ngắn đời thường, không thuật ngữ khó, không chứa `<<<` hay `>>>` (khung dữ liệu của model), không hứa tính năng không có.
Mỗi nhãn nút và đường dẫn (Cài đặt › Bộ não AI › Gen nhớ…) phải khớp màn thật: người viết đối chiếu từng câu với mã `apps/web/src` và ghi danh sách vào báo cáo gói để duyệt giọng văn.
`apps/api/tests/test_coach_content_v0154.py` chặn lỗi hình thức (schema, số câu, độ dài, trùng khoá, tín hiệu lạ, đích không có trong registry, thứ tự lộ trình) và **không có test nào theo `VERSION`**.

**Bài N08 mô tả hành vi từ v0.1.53** (lịch tự cập nhật đêm ~03:00 chỉ lấy bản đủ 24 giờ; lịch bị tắt/mất thì `genh update` tự bật lại; `genh auto-update status` nói thật trạng thái; muốn tắt hẳn: `genh auto-update disable`;
Console cảnh báo khi lịch đêm tắt hoặc im quá 36 giờ). Bản phát hành v0.1.53 là nguồn chuẩn; v0.1.54 chứa toàn bộ v0.1.53 nên câu chữ luôn đúng với bản đang chạy.

## 8. Chuông

Job `gen_coach` (worker, **09:05 / 11:05 / 14:05 giờ Asia/Ho_Chi_Minh**, `gh.gen.coach.cron.run_coach`) chạy cho mọi tổ chức, mọi Owner. Mỗi lượt: cập nhật mốc ổn định; rồi nếu `engine.bell_due` và `store.claim_bell` thắng thì gửi **một chuông**:

- kind **`gen.coach`**, tiêu đề "Hôm nay Sếp còn n việc cần làm", thân "Em đã xếp sẵn việc ưu tiên trên thẻ Hôm nay của Sếp — mở ra xem khi Sếp tiện."
- **Điều kiện** (`bell_due`): hướng dẫn bật + chuông bật + không "Hoãn tất cả" + không ổn định + ngoài giờ yên lặng + có việc P0/P1 hiện trên thẻ + (có khoá P0/P1 **mới** so với lần chuông trước, hoặc cùng tập khoá mà lần chuông trước đã ≥ 3 ngày).
  Sự cố `health.*` và `hub.token_expiring` không tính là "mới" vì đã có chuông riêng.
- **Cổng nguyên tử** `claim_bell`: MỘT câu `UPDATE … RETURNING` — chỉ thắng khi hôm nay chưa chuông **và** Sếp chưa xem thẻ hôm nay; hai tiến trình chạy cùng lúc chỉ một bên chuông; chạy lại cùng ngày không chuông thứ hai.
- **Giờ yên lặng** theo `core.organizations.timezone` (mặc định 21:00 → 07:00, QD-20; `quiet_start == quiet_end` ⇒ không có giờ yên lặng).
- **Liên kết chuông**: `/overview?gen=coach` khi Gen đang bật cho Owner (mở khung Gen với thẻ), `/guide/viec-sep` khi Gen tắt. **Không đẩy Telegram.**

## 9. "Hệ thống đã ổn định"

`prefs.stable_since` là mốc bắt đầu chuỗi ngày không có việc P0/P1 (kể cả việc đã hoãn) và không có sự cố sức khoẻ mới mở. Còn việc P0/P1 ⇒ mốc về trống; chưa có mốc mà không còn việc ⇒ mốc = bây giờ;
có sự cố `bad`/`warn` mở sau mốc (kể cả đã đóng) ⇒ mốc = lúc đó. **≥ 7 ngày liên tục** ⇒ `progress.stable = true`: thẻ ghi "Hệ thống đã ổn định", không mẹo, không chấm đỏ "mới", không chuông. Bài học vẫn có.
Một nguồn sinh việc P0/P1 đọc lỗi ⇒ giữ nguyên mốc và **không** báo ổn định (lỗi đọc không được thành 7 ngày "yên"). Mốc được cập nhật ở mọi lần gọi `GET /today` và ở mỗi lượt cron.

## 10. API (chỉ Owner; khác ⇒ 403 `FORBIDDEN`)

**`GET /api/v1/gen/coach/today[?mark_shown=1]`** — thẻ hôm nay. Không có `mark_shown` thì không ghi mục nào (trừ mốc `stable_since`); `mark_shown=1` (web gọi khi khung mở và thẻ hiện thật) ghi các mục đang hiện + `last_seen_at`.

```json
{
  "date": "2026-10-12", "enabled": true, "snoozed_until": null,
  "todos": [{"key": "boss.ai", "level": "P1", "title": "Kiểm tra nguồn AI chạy được",
             "why": "Em đã có nguồn AI nhưng chưa gọi thật được lần nào — Sếp bấm Kiểm tra để em chắc là trả lời và lọc tin được.",
             "target": "connections.brain", "can_dismiss": true, "dismiss_warning": "Không kiểm tra thì em chưa biết nguồn AI có chạy được không — tới lúc cần trả lời mới biết lỗi."}],
  "tip": {"key": "memory_empty", "title": "Dặn Gen nhớ sở thích của Sếp", "body": "…", "try": {"label": "Thử ngay", "target": "system.brain.memory"}},
  "lesson": {"id": "N01", "k": 1, "total": 19, "title": "Hỏi Gen mọi lúc", "body": "…", "try": {"label": "Làm thử", "target": "help.ask_gen"}, "status": "shown"},
  "progress": {"required_done": 0, "required_total": 1, "lessons_done": 2, "lessons_total": 19, "stable": false, "stable_since": null},
  "unseen": true
}
```

`todos[].target` hoặc `link` (đường dẫn trong app của sự cố) — hoặc cả hai; `dismiss_warning` chỉ có khi `can_dismiss`. Hướng dẫn đang tắt hoặc đang "Hoãn tất cả" ⇒ `todos` rỗng, `tip` và `lesson` là `null` (đang hoãn thì `snoozed_until` có giờ).
`unseen` = có việc P0/P1 chưa từng hiện, sự cố sức khoẻ mở lại sau lần hiện cuối, hoặc mẹo chưa hiện (bài học không bật chấm).

**`POST /api/v1/gen/coach/items/{item_key}`** body `{action, days?, confirm?}` → **204**. `action` ∈ `understood` · `snooze` (`days` ∈ 1, 3, 7) · `done` · `dismiss` (cần `confirm: true`; chỉ việc `todo:` mức P1/P3) · `restore`.
`item_key` ∈ `todo:<khoá>` (chỉ `snooze`/`dismiss`/`restore`; `understood`/`done` → 422 `COACH_ACTION_NOT_ALLOWED` vì sẽ giải quyết vĩnh viễn việc không qua kiểm mức + xác nhận) · `tip:<key>` · `lesson:<N01..N10|G05..G14>` · `card:setup_followup` (thẻ "Việc thiết lập tiếp" ở Tổng quan; chỉ `snooze`/`restore`).

**`GET /api/v1/gen/coach/prefs`** → `{enabled, bell, lessons_per_day, quiet_start, quiet_end, snooze_until, followup_snoozed_until, dismissed: [{key, level, title}]}`.
**`PATCH /api/v1/gen/coach/prefs`** `{enabled?, bell?, lessons_per_day? (0..2), quiet_start?, quiet_end? (0..23), snooze_all_days? (0|1|3|7)}` → prefs mới (`snooze_all_days: 0` bỏ hoãn).

**`GET /api/v1/gen/coach/curriculum`** → `{total: 19, lessons: [{id, k, title, body, try?, status}]}`.

## 11. Mã lỗi (ApiError, tiêu đề tiếng Việt thân thiện; web hiện tiêu đề + "Chi tiết kỹ thuật" chứa mã, không render object)

| HTTP | Mã | Tiêu đề |
|---|---|---|
| 403 | `FORBIDDEN` | Chỉ Sếp (Owner) dùng được Gen hướng dẫn |
| 404 | `COACH_ITEM_UNKNOWN` | Em không biết việc này |
| 422 | `COACH_DISMISS_NOT_ALLOWED` | Việc khẩn cấp không tắt được — Sếp xử lý giúp em nhé (P0); P2: Việc này không tắt được — Sếp chọn Để mai giúp em nhé |
| 422 | `COACH_CONFIRM_REQUIRED` | Sếp xác nhận giúp em trước khi tắt việc này |
| 422 | `COACH_ACTION_NOT_ALLOWED` | Thao tác này không dùng được cho mục đó (vd `dismiss` mẹo/bài, `done` thẻ) |
| 422 | `VALIDATION` | `days` ngoài {1,3,7}, `lessons_per_day` ngoài 0..2, giờ yên lặng ngoài 0..23 |

## 12. Dữ liệu — migration `0033` (`db/sql/0033_v0154_gen_coach.sql`, `revision 0033 ← 0032`)

- **`agent.gen_coach_prefs`** (một hàng / Owner, khoá `user_id`): `enabled`, `bell`, `lessons_per_day` (0..2, mặc định 1), `quiet_start` 21, `quiet_end` 7, `snooze_until`, `stable_since`, `last_seen_at`, `last_bell_at`, `last_bell_keys`.
- **`agent.gen_coach_items`** (khoá `(user_id, item_key)`): `status` ∈ `shown` · `understood` · `snoozed` · `done` · `dismissed`, `snooze_until`, `shown_count`, `first_shown_at`, `last_shown_at`.
- RLS `org_isolation` + `GRANT … TO gh_app` như 0032; chạy lại an toàn (`IF NOT EXISTS`, `DROP … IF EXISTS`). v0.1.53 không có migration nên 0033 nối thẳng sau 0032.
- Hai bảng **không** thuộc `purge_gen` (hạn lưu hội thoại Gen): lựa chọn "Tắt hướng dẫn"/"Để mai" của Sếp phải sống lâu hơn hội thoại.

## 13. Nhật ký hành động

`gen.coach_item_dismissed` và `gen.coach_item_restored` (`target_id` = `item_key`, không kèm tiêu đề hay lý do); `gen.coach_prefs_changed` (`detail.fields` = tên trường đổi, không giá trị). Hoãn / Đã hiểu / Đã làm là thao tác riêng tư của Sếp nên không ghi.

## 14. Web

- **Thẻ "Hôm nay của Sếp"** (`CoachTodayCard`, đầu khung Gen): ba khối + chân thẻ. Chỉ Owner. Thẻ không tạo hội thoại và không gửi câu hỏi: nút chỉ (a) chỉ đường bằng GenDirector (mở màn + làm sáng đích), (b) ghi một hành động lên `/gen/coach/items/*`, hoặc (c) điền sẵn ô nhập ("Hỏi Gen thêm" → "Giải thích thêm cho em bài «tên bài»").
  Khung Gen không tự cuộn xuống khi chưa có tin. Khi có việc khẩn, khung thêm câu mẫu "Hôm nay em cần làm gì?".
- **Chấm đỏ** ở nút Gen khi hướng dẫn bật và `unseen`; tải khi mở app, hỏi lại mỗi 30 phút, chuông `gen.coach` làm mới ngay.
- **Cài đặt › Bộ não AI › Gen hướng dẫn** (`GenCoachCard`, `#gen-coach`): Bật hướng dẫn, Chuông nhắc, Số bài mỗi ngày (0 / 1 / 2), Giờ yên lặng từ–đến, danh sách "Việc Sếp đã chọn không dùng" (nút Bật lại). Mỗi thay đổi lưu ngay (PATCH), không cần mã PIN.
- **Trợ giúp › Lộ trình học cùng Gen** (`CurriculumCard`): 19 bài kèm trạng thái, bấm tên bài để đọc, "Làm thử", "Học lại".
- **Tổng quan**: "Đã đạt x/N việc bắt buộc" (dải Cần Sếp xử lý); thẻ "Việc thiết lập tiếp" bỏ nút "Ẩn" lưu trên trình duyệt, thay bằng **"Để sau 7 ngày"** lưu ở máy chủ (`card:setup_followup`), nên ẩn đúng trên mọi máy; `uiStore` lên phiên bản 3 và xoá khoá cũ.
- Lỗi luôn là chuỗi thân thiện + "Chi tiết kỹ thuật"; 403/404 của `/gen/coach/*` (vai trò khác, api cũ) ẩn thẻ lặng lẽ.

## 15. Gen (hội thoại) và Bản tin

- Tool **`coach.status`** (chỉ Owner, `AGY_SAFE_TOOLS`): gộp `GET /gen/coach/today` (không `mark_shown` — hỏi Gen không tính là đã xem thẻ) và `GET /gen/coach/curriculum`; chỉ khoá, tiêu đề tĩnh, số đếm.
- Khi Owner hỏi kiểu "cần làm gì?", "hệ thống ổn chưa?", "bắt đầu từ đâu?" (`engine.coach_intent`: so khớp cụm sau khi bỏ dấu, KHÔNG gọi model), prompt được chèn khối "VIỆC VẬN HÀNH ĐANG DỞ" (≤ 3 việc, ≤ 500 ký tự) cùng hai luật ngắn: hỏi việc cần làm → `coach.status`; hỏi tính năng → `screens.list`/`guide.list`/`coach.status`, không bịa.
- **Bản tin** 07:30/17:30: chưa đạt hết việc bắt buộc (x < N) ⇒ thêm đúng MỘT bước hiển thị "Việc bắt buộc: đã đạt x/N, xem Việc Sếp cần làm" ngay sau bước tóm tắt. `sections`, đầu vào tóm tắt, thân chuông và tin Telegram **không đổi**.

## 16. Mục tiêu làm sáng — 16 mục mới và ngoại lệ bộ quét

Nguồn sự thật `packages/contracts/src/genTargets.ts`; `apps/api/gh/gen/registry.json` là bản xuất. 16 mục mới của v0.1.54:

- 10 dòng "Việc Sếp cần làm": `boss_checks.row.ai` · `hub` · `facebook` · `agy` · `claude` · `jev` · `telegram` · `remote` · `facebook_reply` · `kho_write` (màn `boss_checks`, mục tiêu tĩnh — không dòng động).
- 2 ở Kết nối: `system.channels.telegram`, `system.channels.telegram.test`.
- 3 ở Cài đặt: `system.remote_access` (tab Sao lưu & cập nhật), `system.ai_cost`, `system.brain.coach` (tab Bộ não AI).
- `gen.coach.card` (màn Hôm nay) và `help.curriculum` (Trợ giúp).

**Ngoại lệ bộ quét `gen-targets.test.ts`**: bộ quét tĩnh đọc mã nguồn để chắc mỗi id registry có chỗ gắn `data-gen-target`, và vốn **bỏ qua** cả thư mục `src/gen/` (khung Gen tự nó không phải chỗ gắn).
Thẻ "Hôm nay của Sếp" nằm trong khung Gen nhưng là một mục tiêu thật (`gen.coach.card`), nên v0.1.54 mở một **ngoại lệ hẹp: đúng một tệp** `src/gen/CoachTodayCard.tsx` được quét; một test riêng bảo đảm không tệp nào khác của `src/gen/` gắn mục tiêu.
Các literal `genTarget: '…'` của `BOSS_ROW_TARGETS` (`bossChecksModel.ts`) để nguyên dạng để bộ quét đọc được.

## 17. Việc Sếp làm sau khi lên bản (một lần, khoảng 3 phút, không bắt buộc)

Bản tự lên qua lịch đêm hoặc nút Cập nhật ngay. Không cần làm gì để hệ thống chạy; ba bước dưới đây chỉ để Sếp quen với Gen hướng dẫn:

1. **Xem thẻ**: mở Console, bấm nút Gen ở góc trên bên phải — đầu khung có thẻ **Hôm nay của Sếp**. Đọc **Việc cần làm ngay**, bấm **Chỉ cho em** ở việc đầu tiên và làm theo; việc chưa tiện thì **Để mai**.
   Thấy chấm đỏ ở nút Gen nghĩa là có việc mới Sếp chưa xem.
2. **Chỉnh theo ý Sếp**: Cài đặt › Bộ não AI › thẻ **Gen hướng dẫn** — chọn Bật hướng dẫn, Chuông nhắc, Số bài mỗi ngày (0 nếu chỉ muốn nhắc việc), Giờ yên lặng. Việc nào không dùng thì **Không dùng việc này** (đọc câu hậu quả), bật lại ở đây lúc nào cũng được.
3. **Thử một bài**: Trợ giúp › **Lộ trình học cùng Gen** — mở bài đầu, bấm **Làm thử** để Gen chỉ tận nơi; sáng hôm sau Bản tin 07:30 có dòng **Việc bắt buộc: đã đạt x/N, xem Việc Sếp cần làm** nếu còn việc bắt buộc.

## 18. Nợ — việc để sau (đã nêu trong [ROADMAP](../ROADMAP.md) › Nợ)

- **`release_todos`**: mỗi bản phát hành khai báo "việc Sếp phải làm sau khi lên bản" thành dữ liệu để Gen hiện đúng bản đó, thay cho đoạn "Boss phải làm" chỉ nằm trong HANDOFF.
- **Danh mục tính năng**: Gen có danh sách tính năng Console có cấu trúc (mô tả, đích, điều kiện dùng) để trả lời "có tính năng X không?" và giới thiệu tính năng chưa dùng — hiện chỉ dựa vào `screens.list`, `guide.list`, `coach.status`.
- **Gợi ý cuối câu tất định**: nút "Chỉ cho em" cuối câu trả lời do mã sinh theo ý định (không do model) khi Sếp hỏi "cần làm gì?".
- **"Làm giúp" G8**: Gen điền sẵn form "Tạo agent đầu tiên" (bước 8) theo khuôn đề xuất có Xác nhận, thay vì chỉ chỉ đường.
- **Bộ bài cho nhân viên**: lộ trình riêng cho vai trò khác Owner (Quản lý, Vận hành, Nhân viên phụ trách…); hiện Gen hướng dẫn chỉ dành cho Sếp.
- **Tắt chuông theo loại**: hiện "Chuông nhắc" là một công tắc chung; tách việc khẩn / token sắp hết hạn / bài học.
- Khác: đo tỉ lệ Sếp làm xong việc sau khi được nhắc để chỉnh thứ tự; chuông qua Telegram (hiện cố ý không đẩy).

## 19. Kiểm tra

- API: `apps/api/tests/test_gen_coach_v0154.py` (xếp hạng, bài học, mẹo, ổn định, chuông và cổng nguyên tử, Bản tin x/N, tool `coach.status`, migration + RLS; nội dung giả để không phụ thuộc tệp thật),
  `test_worker_schedule_v0136.py` (job nhẹ được phép chạy trong giờ làm việc).
- Nội dung: `apps/api/tests/test_coach_content_v0154.py` (pytest thuần, đọc tệp thật + `registry.json`; chạy xanh sau khi gộp web-coach → api-coach → nội dung).
- Web: vitest `coach-v0154.test.tsx`, `gen-targets.test.ts` (16 mục tiêu + ngoại lệ bộ quét), `setup-followup.test.tsx`; Playwright mock `gen-coach-v0154.spec.ts` và `layout-guard.spec.ts` (thẻ, hộp cảnh báo ở 4 cỡ màn).
