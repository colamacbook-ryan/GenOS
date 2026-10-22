# Gen-Harness — Kiến trúc hệ thống

**Tài liệu:** ARCHITECTURE.md · **Phiên bản:** 1.0 (duyệt 23/09/2026, kèm quyết định Q1–Q7 trong PLAN.md) · **Ngày:** 23/09/2026

> **Nguồn hiện hành (10/2026).** Đây là kiến trúc gốc 23/09/2026. Phần đã thi công thêm hoặc đã đổi sau đó nằm ở: thiết kế Gen
> [gen-v1.md](design/gen-v1.md), nối Gen-hub [gen-hub-link.md](design/gen-hub-link.md), mạng xã hội [gen-browser-agent.md](design/gen-browser-agent.md);
> tiến độ + mục Nợ ở [ROADMAP.md](ROADMAP.md); lịch sử từng bản ở [CHANGELOG.md](../CHANGELOG.md); vận hành ở [runbook.md](runbook.md). Mâu thuẫn thì các tài liệu đó thắng.
> Chưa có trong bản gốc (đã có trong mã): Gen (trợ lý, đề xuất có xác nhận, Gen nhớ), Gen-hub (Kho dữ liệu, lịch, mail), Facebook cá nhân qua trình duyệt riêng,
> nguồn model Jev và Claude Code CLI (§11 mới nhắc Antigravity CLI), Telegram, trực canh máy chủ, `genh remote`.

## 0. Nguồn sự thật và thứ tự ưu tiên

| Ưu tiên | Tài liệu | Phạm vi |
|---|---|---|
| 1 | `docs/Gen-Harness-Product-Spec-LOCKED.md` (v2.2) | Mục đích, nghiệp vụ, thực thể, quyền, nguyên tắc có trách nhiệm. Thắng mọi mâu thuẫn. |
| 1b | `docs/spec/Gen-Harness-Spec-BoSung-01-Bot-Quan-Tri.md` | Bổ sung (Sếp duyệt 18/10/2026, v0.1.72): bot dùng tài khoản riêng, kênh quản trị, Sếp gọi bot, Chế độ hỗ trợ. Không đổi nghĩa cũ của bản LOCKED. |
| 2 | `docs/design/Gen-Harness Console.dc.html` (+ `screens.json`, `seed-data.json`, `tokens.json`, `_ds/…/styles.css`) | Giao diện 21 màn, dữ liệu mẫu. Dựng lại pixel-perfect ở 1440px/1280px, không "cải tiến". |
| 3 | `docs/handoff/` (01–07) | Đặc tả kỹ thuật đi kèm thiết kế: màn hình, token, database, kiến trúc, trình cài, thiết lập Owner, nghiệm thu. |
| 4 | `docs/github.md` | Ánh xạ màn ↔ mục spec. |

Trong repo, gói bàn giao được xếp lại: `spec/` → `docs/`, `design/` → `docs/design/`, `docs/01–07` → `docs/handoff/` (đường dẫn trong `docs/handoff/README.md` là đường dẫn gốc của gói). Lược đồ hiện hành nằm ở `db/sql/0001_baseline.sql` + các migration sau (`apps/api/migrations`) (`schema.sql` gốc của gói bàn giao đã bỏ ở v0.1.48, xem lịch sử git).

Tài liệu này **không chép lại** `docs/handoff/`; nó chốt các quyết định kiến trúc, lấp chỗ trống, và ghi rõ **chỗ lệch** so với handoff kèm lý do (§14). Quyết định của chủ dự án nằm ở `docs/PLAN.md` §Quyết định.

Tên cũ Heo-Harness và persona "Bé Heo" không được dùng ở bất kỳ đâu trong mã, cấu hình hay dữ liệu, trừ mẫu agent hoài niệm "Bé Heo" **tắt mặc định** mà spec E13 và thiết kế (`agentTemplates`) giữ lại như một template tuỳ chọn.

---

## 1. Mục tiêu kiến trúc

Theo spec J: **(1) không sập → (2) dữ liệu không vỡ → (3) Console đủ để nghĩ → (4) plugin thêm được → (5) model thay được.** Kỹ thuật "đủ dùng".

Ràng buộc bắt buộc và nơi hiện thực:

| # | Ràng buộc | § |
|---|---|---|
| R1 | Luồng một chiều: Bridge → Kho thô (nguyên trạng) → Core agent sàng lọc → Kho sạch SSOT gắn ID nhóm + ID người + thời gian | 4 |
| R2 | Sàng lọc theo chu kỳ thời gian **HOẶC** ngưỡng số lượng, cả hai cấu hình được | 4.3 |
| R3 | Agent trực kênh quyết định từ ID nhóm, ID người, dữ liệu sạch liên quan, lịch sử tương quan, Sổ tay nhận thức | 5 |
| R4 | Mọi thành phần là plugin cắm rút nóng, cách ly lỗi, circuit breaker; plugin nền không gỡ được | 6 |
| R5 | Thang tự trị 0–6; ghi ra ngoài / vượt ngưỡng tiền / liên quan nhân sự → dừng ở Bàn làm việc | 7 |
| R6 | 5 vai trò theo ma trận quyền của thiết kế; dữ liệu đánh giá nhân sự khoá mức Owner | 8.3 |
| R7 | Mọi điểm số truy về bản ghi thô; không bịa; không tự quyết nhân sự | 9 |
| R8 | Mọi hành động của người và agent vào Action Log bất biến | 8.4 |
| R9 | MCP: agent chỉ gọi tool Owner đã mở; tool ghi phải qua duyệt | 10 |
| R10 | PIN 6 số; đăng nhập kênh bằng QR; đổi tài khoản Antigravity CLI; xoay vòng nhiều khoá API có failover | 8.2, 11 |

---

## 2. Stack

| Lớp | Lựa chọn | Lý do |
|---|---|---|
| Web | React 18 + TypeScript + Vite; React Router; TanStack Query (dữ liệu server); Zustand (trạng thái UI, đồng bộ vào URL); `@phosphor-icons/react` 2.1.1; font Inter tự host | Theo gợi ý + handoff 04. Token Nocturne xuất thành CSS custom properties trong **một** tệp (`packages/tokens`, sinh từ `tokens.json`), component chỉ tham chiếu biến. Không dùng thư viện UI có sẵn để tránh lệch thiết kế. |
| Hợp đồng API | OpenAPI tự sinh từ FastAPI → client TS (`openapi-typescript`) | Không viết tay kiểu dữ liệu ở frontend; đổi API là lỗi biên dịch, không phải lỗi runtime. |
| API | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic | Theo gợi ý; tái dùng tư duy chassis Python repo cũ. |
| Worker | Cùng mã với api, tiến trình riêng; lịch + job bằng **arq** (Redis) | Tách tải nặng (sàng lọc, LLM, chấm điểm, nén sổ tay, làm mới MV, partman, backup) khỏi API. arq nhẹ, async-native, có cron. |
| CSDL | PostgreSQL 16 + pgvector + pg_partman + pg_trgm | SSOT duy nhất (spec G4). Lược đồ hiện hành: `db/sql/0001_baseline.sql` + các migration sau (`apps/api/migrations`) (`schema.sql` gốc của gói bàn giao đã bỏ ở v0.1.48, xem lịch sử git). |
| Bus & realtime | Redis 7: **Streams** cho event bus (consumer group, ack, phát lại), **pub/sub** cho đẩy realtime tới WebSocket, khoá phân tán, rate-limit, bộ đếm hạn mức | Streams bền: consumer chết không mất sự kiện (ưu tiên 2). |
| Bridge | Node 20 + TypeScript; Zalo `zca-js`, WhatsApp `@whiskeysockets/baileys`; mỗi kênh một adapter | Theo gợi ý; hai thư viện đã chạy ở dự án cũ. Viết lại gọn, không mang persona/tên cũ. |
| Tệp | Volume Docker `gh_objects` (đĩa cục bộ, `/var/lib/gh/objects`) | Tài liệu, tệp đính kèm, ảnh bằng chứng, backup. Chưa có kho đối tượng S3: bản sao ngoài máy là ổ USB/NAS do `genh` chép (Cài đặt › Sao lưu & cập nhật). |
| Proxy | Caddy 2 | TLS nội bộ tự ký `https://localhost:8443`, định tuyến `/` web, `/api` + `/ws` api. Chỉ proxy mở cổng ra host. |
| MCP | SDK MCP chính thức cho Python; máy chủ stdio chạy như sidecar khi Owner bật | §10 |
| Trình cài | Bootstrap `install.sh`/`install.ps1` + binary Go `genh` (Bubble Tea) | Handoff 05. Phát triển hằng ngày vẫn dùng `docker compose up`. |
| Test | pytest (+ testcontainers PG/Redis), Vitest + Testing Library, Playwright (luồng + so ảnh 1440/1280) | Định nghĩa "xong". |

---

## 3. Monorepo và dịch vụ

```
Gen-Harness/
├─ apps/
│  ├─ web/          React · Console + trình thiết lập Owner (/setup)
│  ├─ api/          FastAPI · REST /api/v1 + WebSocket /ws · auth, RBAC, policy, plugin manager
│  ├─ worker/       entrypoint worker (dùng lại package của api)
│  └─ bridge/       Node · adapters/zalo, adapters/whatsapp, streams, health
├─ packages/
│  ├─ ui/           component React theo Nocturne (Button, Tabs, Segmented, Table, KPI, Heatmap, GraphCanvas, PinDialog, EmptyState, ErrorState, Skeleton…)
│  ├─ tokens/       tokens.json → CSS variables + TS
│  └─ contracts/    OpenAPI → client TS
├─ plugins/         plugin nền và plugin mẫu (@gen/*), mỗi plugin một manifest.json
├─ db/              Alembic migrations + seed (từ design/seed-data.json)
├─ installer/       bootstrap/ + genh/ (Go)
├─ deploy/          compose.yaml · caddy/ · images/ (Dockerfile multi-stage)
└─ docs/            ARCHITECTURE.md · PLAN.md · spec · design/ · handoff/ · github.md
```

Dịch vụ compose (mạng `internal`, healthcheck từng dịch vụ, `api` chờ `db` + `redis` healthy):

| Dịch vụ | Vai trò |
|---|---|
| `proxy` | Caddy, cổng 8443 duy nhất ra host |
| `web` | bundle tĩnh (nginx-unprivileged) |
| `api` | REST + WS, auth, RBAC, policy, plugin manager, ingest consumer |
| `worker` | refinery, chấm điểm, agent trực kênh, compaction, MV, partman, backup |
| `bridge` | phiên kênh, QR, nhận/gửi |
| `db` | postgres:16 + pgvector + pg_partman |
| `redis` | streams, pub/sub, khoá, bộ đếm |
| (volume) `gh_objects` | tệp, ảnh bằng chứng, backup — không có dịch vụ lưu trữ riêng |
| `mcp-*` | sidecar máy chủ MCP stdio, chỉ khi Owner bật |

```
                 Console (web) ──HTTPS / WS──▶ proxy ──▶ api ◀──────────────┐
                                                        │  ▲                │
  Zalo/WhatsApp ⇄ bridge ──gh.bridge.inbound──▶ ingest ─┘  │ gh.* streams   │
                    ▲                              │ INSERT                  │
                    │                              ▼                         │
                    │      raw.events + refinery.event_state(pending) ─NOTIFY─▶ worker.refinery
                    │                                                        │
                    │               clean.* · memory.* · biz.* · analytics ◀─┘
                    │                                │
                    └── gh.bridge.outbound (permit) ◀── agent trực kênh ──▶ policy ──▶ biz.action_drafts (Bàn làm việc)
```

---

## 4. Luồng dữ liệu một chiều (R1, R2)

Luật cứng:
- Mỗi tầng chỉ **đọc** tầng trước và chỉ **ghi** tầng mình. Không tầng nào ghi ngược.
- `raw.events` và `ops.action_log` **chỉ INSERT** (trigger `core.forbid_mutation`). Trạng thái xử lý nằm ở `refinery.event_state`, tách khỏi `raw` để `raw` bất biến tuyệt đối.
- Mọi kết luận sạch có `clean.evidence` trỏ về `raw.events` → drill-down tới nguyên văn luôn được.
- Chạy lại quy tắc không xoá kết luận cũ: bản mới trỏ qua `superseded_by`.

### 4.1 Bridge
- Zalo (quyết định Q7): Owner quét QR do hệ thống sinh bằng **tài khoản Zalo thật**; bridge giữ phiên đăng nhập (mã hoá, `core.channel_sessions.credential_enc`), tự đăng nhập lại bằng phiên đã lưu khi khởi động, hết hạn mới xin QR mới, rồi bắt tin bằng `zca-js` (`loginQR`, `listener.on('message')`). WhatsApp tương tự với Baileys. Cảnh báo rủi ro khoá tài khoản cá nhân hiện trước QR.
- Mỗi tài khoản kênh là một phiên (`core.channel_sessions`: `pending_qr → active → expired | logged_out | error`). QR sinh trong bridge, đẩy qua `gh.bridge.status` → api → WebSocket → Console (khối QR 88px ở Điều khiển hệ thống, 240px ở trình thiết lập), đếm ngược 60s tự làm mới. Không in QR ra terminal hay ghi file như repo cũ.
- Tin vào/ra được đóng gói *envelope* `{channel, session_id, external_msg_id, external_group_id|null, sender_external_id, occurred_at, kind, body_text, payload (nguyên văn từ thư viện)}` rồi `XADD gh.bridge.inbound`. Bridge **không** gọi LLM, **không** quyết định trả lời (khác `bot.js` cũ tự gọi `/api/chat`).
- Gửi đi chỉ nhận từ `gh.bridge.outbound` và chỉ thực hiện khi lệnh mang **permit** hợp lệ (§7.3). Tin đã gửi quay lại `gh.bridge.inbound` như tin `outbound` → cũng vào kho thô và được sàng lọc (handoff 03).
- Đồng bộ danh sách nhóm/thành viên định kỳ → `gh.bridge.directory`.
- Heartbeat mỗi 15s → `core.channel_sessions.last_heartbeat_at`; mất heartbeat → plugin kênh `degraded`.
- Ranh giới `listen_authorized_only` (khoá, không tắt được): tin của nhóm không nằm trong tập nghe (`off`, `paused`, `pending`) bị bridge bỏ ngay, chỉ metadata nhóm được đồng bộ. **Nhóm mới (Sếp chốt 17/10/2026): khi tài khoản được add vào một nhóm, MẶC ĐỊNH vào «Lắng nghe im lặng» (`silent`) — lưu hết tin, KHÔNG BAO GIỜ trả lời, kể cả khi được tag** (mọi bản cài, kể cả cũ; nhóm cũ đang `off` giữ nguyên). `settings.new_group_mode.<kênh>` đổi được: `silent` (mặc định) · `ask` (nhóm vào `pending` + hộp hỏi) · `off`. Thẻ kênh báo «N nhóm mới đã vào Lắng nghe im lặng». **Ngoại lệ (v0.1.72, chỉ tài khoản riêng của bot, §5.2–5.3): bot vẫn không trả lời trong nhóm `silent`, TRỪ KHI chính Sếp (nhận diện theo ID tài khoản) tag bot, hoặc nhóm đã được Sếp bật Chế độ hỗ trợ.**
- **Tài khoản bot (v0.1.72)**: `core.channel_sessions.account_kind` = `bot` (tài khoản Zalo/WhatsApp RIÊNG của bot, Sếp đánh dấu bằng PIN) | `owner_self` | NULL (bản cài cũ = tài khoản cá nhân của Sếp ⇒ mọi tính năng bot tắt, hành vi y v0.1.70). Với tài khoản bot, bridge thêm các sự kiện `group.self_joined` (ai thêm bot vào nhóm), `group.self_removed`, `group.owner_ping` (Sếp tag bot ở nhóm không nghe — không có nội dung), `owner.dm` (tin 1-1 của Sếp, theo ID), `owner.pair` (mã ghép 6 số), cờ `replies_to_self`. Nhóm do **Sếp** thêm bot → vào chế độ nghe mặc định; nhóm do **người khác hoặc không rõ ai** thêm → `pending` (không lưu nội dung) và hỏi Sếp qua kênh quản trị. Bridge kiểm lại mọi tin tự gửi theo `basis` của permit (lớp hai).

### 4.2 Ingest → Kho thô
Consumer `ingest` (trong api) đọc `gh.bridge.inbound`, trong **một transaction**: ánh xạ nhóm/người gửi (`core.groups`, `core.person_identities`, tạo mới nếu chưa có), `INSERT raw.events` (khử trùng `(channel_id, external_msg_id)` + `content_hash`), `INSERT refinery.event_state(state='pending')`, `NOTIFY raw_ingested`; rồi `XACK`. Đẩy dòng mới tới màn Kho dữ liệu thô qua WebSocket (mục tiêu ≤ 2 giây từ lúc tin đến — handoff 07).

**Nhật ký lắng nghe (v0.1.68).** Cột `raw.events.listen_mode` ghi chế độ nghe của nhóm (hoặc `silent|reply` của tin 1-1) LÚC tin vào kho. Ingest cũng ghi sự kiện nhẹ vào `ops.listen_events` (nhóm mới/rời/đổi tên/trở lại, thành viên vào-rời của nhóm đang nghe, phiên kênh nối/ngắt/đăng xuất, người lạ nhắn riêng lần đầu; lần quét danh bạ đầu tiên chỉ ghi một dòng tóm tắt) rồi phát WebSocket `listen.event` (chỉ tín hiệu). `GET /listen-log` (`gh/data_api/listen_log.py`) gộp `raw.events` + `ops.listen_events` thành một dòng thời gian, kèm sàng lọc (`refinery.event_state`), quyết định agent (`clean.evidence → agent.decisions`) và bản nháp; quyền = `data.read` + phạm vi nhóm + `direct_visible_sql` (tin 1-1), che số dài cho vai dưới Owner — không đường lách (xem `docs/api/phase-2.md`).

### 4.3 Core agent sàng lọc (refinery) — chu kỳ HOẶC ngưỡng
- Cấu hình `refinery.schedule`: `interval_seconds` (mặc định 900), `count_threshold` (500), `batch_size` (250), `min_confidence` (0,60) — khớp `triggerConfig` của thiết kế. Chạy khi **điều kiện nào tới trước**.
- **Đường nhanh** (quyết định Q3): tin tag agent và tin 1-1 được sàng lọc ngay khi tới, một lô 1 bản ghi, cùng quy tắc, cùng `SKIP LOCKED` → agent trực kênh trả lời không phải chờ chu kỳ.
- Kích hoạt: cron arq kiểm chu kỳ; listener `LISTEN raw_ingested` đếm `pending` và enqueue khi đạt ngưỡng; nút "Chạy ngay" (manual); "Thử quy tắc" (test, không ghi).
- Lấy lô: `SELECT … FROM refinery.event_state WHERE state='pending' … FOR UPDATE SKIP LOCKED LIMIT batch_size` → nhiều worker song song an toàn, không trùng, không sót. Mỗi lượt ghi `refinery.runs`.
- Pipeline một lô:
  1. **Quy tắc tất định** theo `refinery.rules` + `rule_versions` (Khi → Thì). Nhiễu → `discarded` kèm rule id (giải thích được).
  2. **Trích xuất bằng LLM** (provider theo vai trò "tách ý định, phân loại"): ý định, thực thể, sự kiện có nghĩa (spec G3), phía cung/cầu, kết luận 1–2 câu, độ tin. Mỗi kết luận **bắt buộc** trích `raw_event_id` làm chứng cứ; bộ kiểm tra loại mọi kết luận trỏ tới ID không có trong lô (chống bịa, R7).
  3. `confidence ≥ min_confidence` → `clean.meaning_units` + `clean.evidence`, state `clean`; dưới ngưỡng → `lowconf` (chờ Sếp xem, thiết kế: "Tin cậy thấp").
  4. Embedding `vector(768)`; chấm điểm theo `refinery.scoring_weights` → `clean.score_snapshots` → `clean.current_scores`; cập nhật `clean.relationships`, `biz.market_signals`, `biz.opportunities`; ghi `memory.entries` cho sổ tay các ID liên quan.
  5. Phát `gh.clean.ready` cho agent trực kênh và plugin trí tuệ.
- Model chết: bước 1 vẫn chạy; tin còn `pending`, lượt sau thử lại; hệ thống không sập (spec M7).

### 4.4 Kho sạch SSOT
`clean.meaning_units` luôn có `group_id` (NULL = tin riêng), `person_id` (sau hợp nhất danh tính), `observed_at`. Mọi màn kinh doanh đọc từ `clean.*`, `biz.*`, `memory.*`, `analytics.*` — không màn nào quét `raw` lúc tải trang ngoài màn Kho dữ liệu thô. Plugin không giữ "sự thật riêng" (spec G4).

---

## 5. Agent trực kênh (R3)

Một **Agent Identity** (`agent.identities` + `agent.channel_scopes`) được gán vào kênh/nhóm với chế độ nghe (Nghe hết, chỉ trả lời khi được tag · Lắng nghe im lặng — không bao giờ trả lời, kể cả khi được tag · Chủ động bắt tín hiệu). Khi `gh.clean.ready` có đơn vị ý nghĩa trong phạm vi:

1. **Dựng ngữ cảnh** (tất định, có ghi lại):
   - danh tính agent: tên, vai trò, xưng hô, giọng, được nói khi, cấm, mức tự trị;
   - **ID nhóm** → hồ sơ nhóm, chế độ nghe, mức tự trị nhóm;
   - **ID người** → hồ sơ sống, `clean.current_scores` + lý do, người phụ trách;
   - **dữ liệu sạch liên quan**: tối đa N `meaning_units` theo cửa sổ thời gian + độ tương đồng pgvector, trong phạm vi được phép;
   - **lịch sử tương quan**: tương tác trước giữa người ↔ nhóm ↔ agent ↔ nhân viên, cơ hội/việc/lời hứa đang mở;
   - **Sổ tay nhận thức** của ID người và ID nhóm (gồm mục ghim).
2. **Quyết định** (LLM): `silent` · `note` (ghi sổ tay) · `suggest` · `draft` · `send`. Im lặng đúng lúc là năng lực (spec C3.3). Nhóm «Lắng nghe im lặng» không vào ứng viên agent trực (QD 17/10 + #22: không tốn lượt LLM; sàng lọc vẫn chạy); phòng thủ: nhóm vừa đổi sang `silent` giữa chừng thì `apply` đọc lại chế độ và chặn `suggest`/`draft`/`send` (chỉ còn `silent`/`note`); `tagged_only` được tag thì trả lời như thường.
3. **Qua policy** (§7): `send`/`draft` thành hành động có cờ; chỉ tới bridge khi có permit.
4. **Ghi vết**: bảng bổ sung `agent.decisions` lưu quyết định + danh sách ID ngữ cảnh đã dùng → màn "Agent đã nói gì, nhân danh gì" và khối "Dữ liệu agent đã dùng — không có chỗ nào bịa" ở Bàn làm việc đọc từ đây. Action Log ghi mọi quyết định.

### 5.1 Sổ tay nhận thức
- `memory.notebooks` theo `(subject_type person|group, subject_id)`, `token_budget` (mặc định 4000), `token_used`, `compaction_no`.
- `memory.entries` theo mục thiết kế: Cần chú ý ngay · Ngữ cảnh lũy tiến · Giới hạn cho agent · Sở thích · Việc dở · Đã nén; mỗi mục có `refs` (ID bấm được), tác giả (agent/Sếp), `is_pinned`.
- **Lũy tiến**: refinery và agent thêm mục sau mỗi lượt.
- **Nén** khi `token_used ≥ 90% token_budget` hoặc mỗi 24 giờ: gộp mục không ghim thành tóm tắt, ghi `memory.compactions`, mục cũ đánh `archived_at` (vẫn truy được trong kho sạch — khối "Đã nén khỏi ngữ cảnh").
- **Owner**: ghim, sửa, xoá mục, nén ngay, đặt lại. Mọi thao tác vào Action Log; sửa giữ bản cũ.
- Redis giữ bản đệm sổ tay đang nóng để dựng ngữ cảnh nhanh; Postgres là nguồn thật.

### 5.2 Kênh quản trị và Sếp gọi bot (v0.1.72)
Chỉ khi kênh Zalo/WhatsApp chạy bằng **tài khoản riêng của bot** (`account_kind = 'bot'`).
- **Ghép**: Console (PIN) cấp mã 6 số, sống 10 phút, chỉ lưu băm có khoá (`ops.owner_channels`); Sếp nhắn mã cho bot từ tài khoản của mình. Lõi kiểm lại (tin 1-1, ≤ 5 phút, không chuyển tiếp, đúng phiên bot, so sánh hằng thời gian, sai 5 lần huỷ mã; sai mã thì KHÔNG trả lời). **Nhận diện Sếp chỉ theo ID** (Zalo `uid`; WhatsApp JID và LID), không bao giờ theo tên. Đổi tài khoản bot ⇒ huỷ ghép + tắt hỗ trợ.
- **Tin 1-1 của Sếp** (`owner.dm`) không vào Kho thô; qua `gh.owner.inbound` tới lõi lệnh dùng chung với Telegram (`gh/owner_cmd`: chỉ đọc + ĐỀ XUẤT) và lệnh nhóm (`/nhom`, `/nghe`, `/bo`, `/ho_tro`, `/xac_nhan`: chỉ đổi cấu hình, bật hỗ trợ cần 2 bước, không PIN qua chat). Lời đáp đi qua cửa `send_auto(owner_dm)`.
- **Báo chủ động — chỉ SỰ KIỆN ĐẶC BIỆT** (Sếp quyết 18/10/2026; KHÔNG báo định kỳ, KHÔNG bản tóm tắt, KHÔNG gộp theo giờ): người lạ thêm bot vào nhóm, bot bị mời ra, kênh mất kết nối, câu khó trong chế độ hỗ trợ cần Sếp, hỗ trợ tự tắt vì bị tấn công lặp. Mức tối thiểu «quan trọng», giờ yên lặng 21:00–07:00 giờ VN, trần 6 tin/giờ (`ops.owner_outbox`).
- **Sếp tag bot trong nhóm** (kể cả nhóm `silent`): model `core.owner_reply` chạy **trong hộp riêng agy**, ngữ cảnh chỉ nhóm đó; trả lời tại chỗ qua `send_auto(owner_tag)`. Câu có giá/tiền/hợp đồng/nhân sự/thông tin riêng, hoặc mức tự trị tổ chức ≤ 2 ⇒ trong nhóm chỉ «Dạ em gửi riêng Sếp ạ», nội dung gửi riêng Sếp kèm bản nháp chờ Xác nhận + PIN (đường cũ).

### 5.3 Chế độ hỗ trợ nhóm (v0.1.72)
Sếp bật cho từng nhóm (`ops.group_support`; Console có PIN hoặc lệnh 2 bước ở kênh quản trị; nhóm phải đang nghe, do Sếp thêm bot hoặc Sếp đã duyệt nghe). Thành viên tag bot hoặc trả lời tin của bot ⇒ `core.support` (hộp riêng agy, không công cụ, ngữ cảnh chỉ nhóm này): phân loại (`faq`/`product_info` mới được trả lời) → soạn → **bộ lọc tất định sau model** (`gh/owner_channel/guard.py`: không giá/tiền, hợp đồng/cam kết, nhân sự, thông tin cá nhân, người ngoài nhóm, @, link, lộ lời nhắc/canary, quá dài) → `send_auto(support)`. Lần đầu trả lời một thành viên trong mỗi lượt bật: tự giới thiệu là trợ lý tự động. Câu khó / bị lọc ⇒ câu mẫu «xin chuyển anh/chị phụ trách» (≤ 1 lần/thành viên/30 phút) + báo Sếp. Giới hạn (Sếp chỉnh): 20 tin/giờ/nhóm, 5/giờ/người, cách ≥ 20 giây, 150/ngày/kênh, tin cũ > 5 phút không trả lời, người gửi > 10 tin/phút bị bỏ. Tắt khi: Sếp tắt · hết hạn · bot bị mời ra · đổi phiên/loại tài khoản · ≥ 3 lần bị chặn lộ lời nhắc trong 10 phút.

### 5.4 Một cửa tự gửi `send_auto`
Mọi tin KHÔNG qua bản nháp + PIN đi **một** cửa `gh/owner_channel/autosend.py::send_auto(basis ∈ {owner_dm, owner_tag, support})`. Trong một transaction nó tự đọc lại từ DB/Redis mọi điều kiện (tài khoản bot đang chạy, Sếp theo ID, nhóm, hỗ trợ đang bật, tuổi tin kích hoạt, giới hạn), chạy bộ lọc, ghi `ops.auto_sends` (băm + độ dài + mã luật, KHÔNG nội dung; chỉ-thêm) và Action Log, COMMIT rồi mới ký permit (có `basis`, không `draft_id`) và phát lên `gh.bridge.outbound`. Mỗi tin kích hoạt chỉ một trả lời (`UNIQUE(org, basis, trigger_ref)`). Chỉ có HAI module phát lên luồng gửi: `biz/core/drafts.py` (nháp + PIN) và `owner_channel/autosend.py` — test AST khoá điều này.

---

## 6. Chassis và plugin (R4)

### 6.1 Manifest
`manifest.json`: `package` (`@gen/<tên>`), `version`, `layer` (chassis · channel · intelligence · provider · action · ui · extension), `entry`, `permissions[]`, `events.subscribe[]`, `events.publish[]`, `settings_schema` (JSON Schema → Console tự sinh form), `sandbox` (`memory_mb`, `timeout_s`, `net`), `dependencies` (package + version range), `removable`.

### 6.2 Plugin nền (origin=core, không gỡ được)
Khớp `plugRows`/`loadOrder` của thiết kế: `@gen/chassis-kernel` (Kernel & Plugin Manager) → `@gen/chassis-bus` → `@gen/chassis-store` → `@gen/chassis-policy` + `@gen/chassis-auth` → `@gen/intel-core` (Core Agent) → kênh Zalo/WhatsApp → plugin cài thêm. Nạp theo `load_order`, kiểm phụ thuộc trước khi nạp. API từ chối gỡ (409) và từ chối tắt nếu manifest không cho phép.

### 6.3 Vòng đời và cắm rút nóng
`installed → enabled ⇄ disabled → uninstalled`, sức khoẻ `healthy · degraded · isolated`. Bật/tắt/cài/gỡ không cần khởi động lại: lệnh qua `gh.plugin.control`, Plugin Manager ở api và worker gọi `on_enable/on_disable`, đăng ký/huỷ consumer của plugin. Trạng thái trong `ops.plugins` (không lưu JSON file như repo cũ).

### 6.4 Cách ly
- **Plugin nền** chạy trong tiến trình api/worker; mỗi handler chạy trong task riêng có timeout, bắt ngoại lệ (tư duy `safe_execute` của repo cũ).
- **Plugin cài thêm** (`marketplace | local_file`) chạy trong **tiến trình con** (hoặc container riêng theo `sandbox.mode`), giới hạn RAM/timeout/mạng, chỉ giao tiếp qua Redis Streams. Cài: kiểm chữ ký, hiện danh sách quyền xin, yêu cầu PIN.
- **Circuit breaker** mỗi plugin (và mỗi provider/khoá API): mở khi tỉ lệ lỗi vượt ngưỡng trong cửa sổ → `isolated`, sự kiện của nó dồn trong stream, không mất; hết thời gian nghỉ → nửa mở thử 1 sự kiện → đóng nếu thành công. Ghi `ops.breaker_events`, phát `gh.plugin.health`, hiện "lỗi đã cách ly" ở Tổng quan.

### 6.5 Stream chính
| Stream | Hướng | Nội dung |
|---|---|---|
| `gh.bridge.inbound` | bridge → ingest | envelope tin vào/ra |
| `gh.bridge.outbound` | api/worker → bridge | lệnh gửi + permit |
| `gh.bridge.status` / `gh.bridge.directory` | bridge → api/worker | QR, phiên, heartbeat / nhóm, thành viên, bot được thêm / mời ra (`group.self_*`), Sếp tag ở nhóm không nghe (`group.owner_ping`) |
| `gh.owner.inbound` | ingest → kênh quản trị (api) | `dm`, `pair`, `call`, `support_ask` (v0.1.72; mang chữ của Sếp / mã ghép nên bản ghi bị xoá sau khi xử lý) |
| `gh.clean.ready` | refinery → agent, plugin | ID đơn vị ý nghĩa mới |
| `gh.action.requested` / `gh.action.decided` | agent/người ↔ policy → executor | hành động và quyết định |
| `gh.plugin.control` / `gh.plugin.health` | api ↔ plugin manager | điều khiển, sức khoẻ |

Mọi message có `event_id` (UUIDv7), `org_id`, `correlation_id`, `actor`, `occurred_at`, `schema_version`.

---

## 7. Tự trị, chính sách, Bàn làm việc (R5)

### 7.1 Thang 0–6 (spec H1, `autonomySteps`)
0 Chỉ ghi nhận · 1 Tóm tắt · 2 Chấm điểm + giải thích · 3 Gợi ý hành động · 4 Soạn sẵn chờ duyệt · 5 Tự làm việc thấp rủi ro · 6 Tự làm việc đã whitelist.
Mặc định **4** (thiết kế: pill header "tự trị 4", trình thiết lập bước 9; spec cho phép 3 hoặc 4).
Mức hiệu lực = **mức thấp nhất** trong các lớp: tổ chức → kênh → nhóm → người → loại việc → agent identity. Lớp dưới không nới được quyền lớp trên.

### 7.2 Luật cứng
Hành động **bắt buộc dừng ở Bàn làm việc** (`biz.action_drafts`, `hold_reason` ghi lý do) nếu có bất kỳ cờ nào:
- `writes_external`: gửi tin, ghi CRM/ERP, tool MCP loại ghi, gửi tài liệu ra ngoài;
- `amount_vnd > approval_threshold_vnd` (mặc định 50.000.000 ₫, `ops.policy_boundaries`);
- `personnel_related`: đánh giá/quyết định về nhân viên, ứng viên, học viên.

**Ngoại lệ đã Sếp duyệt (18/10/2026, v0.1.72) — không theo thang tự trị**: tài khoản riêng của bot tự gửi trong đúng ba trường hợp qua cửa `send_auto` (§5.4); trong ba trường hợp đó tiền, hợp đồng, nhân sự vẫn bị bộ lọc chặn và chuyển duyệt.

Cờ gắn ở **registry loại hành động** do chassis quản, không do LLM tự khai → model không tự hạ cờ, không tự cấp quyền. Luật này áp ở **mọi** mức tự trị (quyết định Q2): mức 5–6 chỉ tự làm việc nội bộ (tạo việc, nhắc, ghi chú, gắn nhãn, ghi sổ tay); mọi tin gửi ra ngoài đều chờ duyệt. Ngưỡng tiền do Owner đặt.

### 7.3 Luồng
`requested` → policy → `auto` (cấp permit, thực thi) | `held` (vào Bàn làm việc) | `blocked`.
Bàn làm việc: Duyệt và gửi · Sửa rồi gửi · Huỷ → `approved | edited | rejected` → executor thực thi với permit → `sent | failed`. Permit: dùng một lần, hạn 5 phút, ký HMAC gồm `draft_id + hash(body) + channel/tool`; bridge và MCP executor kiểm trước khi làm. Duyệt/huỷ đòi PIN nếu phiên PIN hết hạn. Tất cả vào Action Log.

### 7.4 Giới hạn mặc định (quyết định của chủ dự án)
**Mặc định mở hết, trừ quyền nguy hiểm nghiêm trọng.** Mọi giới hạn khác là cài đặt Owner tự bật/tắt (PIN + log).

Khoá cứng, không tắt được bằng cài đặt:
1. Chỉ lưu tin của nhóm Sếp đã chọn nghe (`listen_authorized_only`).
2. Hệ thống không tự ra quyết định nhân sự.
3. Gửi ra ngoài, vượt ngưỡng tiền, liên quan nhân sự → chờ duyệt (ngưỡng tiền Owner đặt), trừ ba trường hợp tự gửi của tài khoản riêng của bot (§5.2–5.4); trong ba trường hợp đó tiền, hợp đồng, nhân sự vẫn bị bộ lọc chặn và chuyển duyệt.
4. MCP: tool ghi qua duyệt; agent chỉ gọi tool Owner đã mở.
5. Kho thô và Action Log chỉ INSERT.
6. PIN cho thao tác nhạy cảm; bí mật mã hoá.
7. Điểm số, cảnh báo nhân sự phải có chứng cứ.
8. Ẩn dữ liệu nhạy cảm (số tài khoản, sức khoẻ, đời tư) khỏi vai trò dưới Owner.
9. Chỉ Sếp đưa bot vào nhóm: nhóm do người khác hoặc không rõ ai thêm thì bot không nghe tới khi Sếp đồng ý (v0.1.72).

Còn lại mặc định **mở** khi cài mới (ví dụ: quan sát nhóm thị trường bên ngoài, cho máy chủ MCP gọi ra mạng ngoài, giới hạn tốc độ, hạn mức model, giới hạn tài nguyên plugin) và Owner đổi được trong Điều khiển hệ thống. Riêng chế độ "Dùng dữ liệu mẫu" nạp đúng trạng thái như thiết kế để màn hình khớp.

---

## 8. Bảo mật (R6, R8, R10)

### 8.1 Đăng nhập
Mật khẩu argon2id (≥12 ký tự), TOTP tuỳ chọn *(chưa làm — ROADMAP mục Nợ)*. Phiên là token ngẫu nhiên trong cookie `HttpOnly; Secure; SameSite=Strict`, lưu băm ở `core.sessions` (thu hồi được). CSRF token cho request ghi; `Idempotency-Key` cho mọi endpoint ghi (chưa thi hành — server không đọc header này). Owner đầu tiên tạo qua trình thiết lập bằng **mã thiết lập một lần** do trình cài sinh — không có tài khoản mặc định.

### 8.2 PIN 6 số (theo `pinRules` của thiết kế)
- PIN riêng mỗi người, argon2id. Nhập đúng mở **phiên PIN 30 phút** (`core.sessions.pin_verified_until`), gia hạn khi có thao tác.
- Thao tác cần PIN: đăng xuất/đăng nhập kênh, đổi tài khoản Antigravity CLI, cài/gỡ plugin, đổi quyền, xem khoá API, gộp/tách danh tính, duyệt/huỷ ở Bàn làm việc, mở tool MCP, đổi ngưỡng tự trị/tiền, xuất/xoá dữ liệu, xem dữ liệu đánh giá nhân sự. Danh mục nằm trong code (một chỗ), backend kiểm; thiếu/hết hạn trả **`423 PIN_REQUIRED`** → UI bật PinDialog.
- Sai 5 lần → khoá Console 15 phút và báo Owner qua kênh riêng (Zalo của Sếp). Đây là thông báo bảo mật của hệ thống tới chính tài khoản Owner, không phải hành động của agent. Mọi lần nhập, kể cả sai, vào Action Log.

### 8.3 RBAC
- Vai trò: Owner · Manager · Operator · Agent nhân viên · Auditor. Trong code vai trò người là `agent_staff` để không nhầm với Agent Identity (bot).
- Ma trận thiết kế (`permCols` × `permRows`): 7 cột Tổng quan · Hàng đợi · Hồ sơ khách · Đánh giá nhân sự · Cơ hội · Hành động · Nhật ký; ô ✓ toàn quyền / – có giới hạn / ✕ không. Lưu ở `core.role_permissions(scope = all | team | assigned | none)`. Ý nghĩa cụ thể của "có giới hạn" cho từng ô được định nghĩa ở PLAN §1.5 và test hoá.
- Hai tầng, đều ở backend, **ở tầng service** (không chỉ route):
  1. quyền chức năng — `require(permission)`;
  2. phạm vi dữ liệu — mọi truy vấn qua `ScopeFilter(user)`: Manager = team mình; Operator = hàng đợi được giao; Agent nhân viên = khách được phân; Auditor = đọc, không có quyền ghi nào.
- Đánh giá nhân sự: khoá mức Owner (yêu cầu của dự án, chip "Dữ liệu khoá ở cấp Owner", guard MCP "Dữ liệu nhân sự chỉ ở mức Owner"). Mặc định (quyết định Q4): chỉ Owner thấy nội dung; Auditor thấy nhật ký ai đã xem; Manager không thấy. Owner có thể tự cấp thêm cho vai trò khác trong Quyền hạn (PIN + log). Mọi lần xem vào Action Log.
- Tin riêng 1-1 (`group_id IS NULL`, v0.1.65): chỉ Owner xem nguyên văn, vai khác chỉ khi người đó do chính mình phụ trách / được giao thẳng — dù phạm vi `all` (Vận hành, Kiểm soát). MỘT quy tắc `gh.biz.core.scope.direct_visible_sql` ghép vào Hộp thư, Kho thô, Kho sạch, hồ sơ, tìm kiếm, chứng cứ và sự kiện WebSocket `raw.new`; Gen của nhân viên đọc qua các API này nên theo cùng. Gán/bỏ BOT cho **người** (bỏ qua chế độ im lặng của tin 1-1) chỉ Owner + mã PIN `policy.change`.
- Ranh giới "Ẩn dữ liệu nhạy cảm khỏi mọi vai trò dưới Owner" áp ở serializer (trường được gắn nhãn nhạy cảm bị che).
- Row-Level Security Postgres bật ở giai đoạn hoàn thiện như lớp phòng thủ thứ hai (handoff 03).
  RLS chỉ là phòng thủ phụ (mỗi bản cài 1 tổ chức) — không mở rộng thêm.

### 8.4 Action Log
`ops.action_log` phân vùng tháng, chỉ INSERT, `row_hash = sha256(prev_hash || nội dung chuẩn hoá)`. Ghi qua **một** service duy nhất; middleware ghi mọi request ghi, worker ghi mọi hành động agent/plugin/hệ thống. Job hằng đêm kiểm chuỗi, đứt → đẩy cảnh báo vào hàng đợi. Test quét route bảo đảm không đường ghi nào bỏ qua log.

### 8.5 Bí mật
Mã hoá phong bì AES-256-GCM cho khoá API, phiên kênh, TOTP, auth MCP (`bytea`). Khoá master ở Docker secret `gh_master_key` do trình cài sinh; không ở DB, repo, image hay log. API chỉ trả `last4`; "hiện khoá" cần PIN và vào log.

---

## 9. Điểm số, chứng cứ, danh tính (R7)

- `clean.score_snapshots` (không ghi đè, giữ lịch sử để vẽ xu hướng) + `clean.current_scores` (bộ đệm đọc). Mỗi snapshot có thành phần theo trọng số, độ tin, phương pháp (quy tắc/model/tay) và chứng cứ (meaning units → `clean.evidence` → `raw.events`).
- **Không chứng cứ thì không có điểm**: service từ chối lưu điểm không có evidence. Độ tin thấp hiển thị nhãn và tính vào chỉ số "% điểm số có độ tin thấp" (spec F4).
- Sửa tay tạo snapshot mới `method=manual` + lý do.
- `GET /api/v1/explain/{kind}/{id}` trả chuỗi điểm → đơn vị ý nghĩa → trích dẫn → bản ghi thô (nút "Vì sao hệ thống nghĩ vậy" / "Xem chứng cứ gốc" ở mọi màn).
- **Ranh giới nhân sự**: hệ thống chỉ sinh tín hiệu + khuyến nghị coaching. Không có loại hành động kỷ luật tự động (ranh giới khoá "Tự động ra quyết định nhân sự" = tắt). Cảnh báo nhân sự không có chứng cứ không được hiện. Có luồng **Phản biện** (`biz.review_disputes`, spec I).
- **Hợp nhất danh tính** (spec G2): `core.identity_merge_candidates` (điểm khớp % + cơ sở: số điện thoại, tên chuẩn hoá, đồng xuất hiện nhóm, tự giới thiệu), gộp/tách tay có PIN, `core.identity_merge_log`, đảo ngược được; gộp thì hồ sơ sống, sổ tay và lịch sử gộp theo; không xoá cứng (`merged_into_id`).

---

## 10. MCP Hub (R9)
- `agent.mcp_servers` (stdio | http+sse | streamable_http, breaker riêng), `agent.mcp_tools` (`access read|write`, `is_exposed` **mặc định false**), `agent.mcp_grants` (tool × agent), `agent.mcp_calls` (nhật ký LIVE).
- Chỉ Owner (PIN) mở tool và cấp cho agent. Agent chỉ thấy tool đã mở + được cấp; gọi tool chưa mở → chặn, ghi log (kết quả "Bị chặn").
- Tool `write` luôn tạo `biz.action_drafts(kind='mcp_write')` trước (guard khoá). Tool `read` chạy ngay nếu mức tự trị cho phép, vẫn ghi log.
  Ngoại lệ duy nhất (v0.1.50, QD-18): `kho_create` / `kho_update` của Gen-hub qua `gh.hub_link.service.write_kho` — bước duyệt là Xác nhận + mã PIN của Owner + permit ký (`invoke_tool(approved_write=True)`); route MCP chung vẫn chặn hai tool này (403 `HUB_TOOL_NOT_ALLOWED`).
- Mặc định chặn mạng công cộng cho máy chủ MCP (guard "Cho phép máy chủ MCP ngoài mạng nội bộ" = tắt).
- Quyền tệp/nhóm áp cả qua MCP: tham số gọi được lọc theo phạm vi của agent.

---

## 11. Model, khoá API, Antigravity CLI (R10)

- Provider plugin: `antigravity_cli`, `gemini`, `deepseek`, `openai_compat`, `embedding` (`agent.providers`). Giao diện chung `generate(messages, schema?, tools?)` và `embed(texts)`.
- **Gán model theo vai trò/agent** (`agent.bindings`): core agent suy luận chính, trả lời nhanh trong nhóm, tách ý định/phân loại, chấm điểm suy luận dài, đánh chỉ mục (khớp `models`, `agentBindings`).
- **Xoay vòng khoá**: `agent.provider_keys.rotation_order`; chọn khoá còn hạn mức, không trong cooldown. 429/hết hạn mức → cooldown khoá đó, sang khoá kế.
- **Hạn mức theo model**: `agent.models.daily_quota`, `rate_limit_per_min`; bộ đếm nóng ở Redis, chốt vào `agent.model_calls` (phân vùng tháng) → `analytics.mv_model_usage_daily`.
- **Chuỗi chuyển hướng** (`failoverRules` thiết kế): hết hạn mức → nhà cung cấp kế tiếp; ngắt mạch → giữ nguyên hội thoại, thử lại sau 60s; hết chuỗi → xếp hàng và báo Sếp qua hàng đợi; còn < 20% hạn mức → cảnh báo.
- **Antigravity CLI** (quyết định Q6: bản cài chính hãng, đăng nhập/đổi tài khoản như dự án cũ):
  - Image `worker` cài **binary `agy` chính hãng** của Google lúc build (nguồn tải + checksum ghim trong Dockerfile), không đóng gói lại.
  - Như dự án cũ: CLI lưu phiên đăng nhập ở tệp OAuth `~/.gemini/antigravity-cli/antigravity-oauth-token`; email tài khoản đọc từ `id_token` trong tệp đó; đăng xuất = xoá tệp.
  - **Đăng nhập**: Console bấm Đăng nhập → worker chạy luồng đăng nhập của chính CLI trong container, chuyển link/mã xác thực lên Console qua WebSocket; xong thì tệp token được đọc, **mã hoá** và lưu vào `agent.cli_profiles` (email, gói, hạn).
  - **Đổi tài khoản**: nhiều hồ sơ trong `agent.cli_profiles`, một hồ sơ hoạt động; chuyển hồ sơ = ghi tệp token của hồ sơ đó vào thư mục cấu hình CLI trong volume (api và worker mount chung `agy_state`; worker chạy `agy -p` mới cho mỗi lượt nên lượt kế tiếp dùng ngay tài khoản mới). Cần PIN, vào Action Log. Hồ sơ không có phiên đã lưu → `409 CLI_PROFILE_NO_SESSION` (không "đổi giả").
  - **Thêm tài khoản khi đang đăng nhập** (v0.1.30): CLI đã đăng nhập không in link đăng nhập, nên trước khi chạy CLI tệp phiên hiện tại được gửi tạm sang `antigravity-oauth-token.before-login`; xong → bỏ bản gửi tạm (tài khoản mới thành tài khoản đang dùng), lỗi/huỷ/quá giờ → trả về chỗ cũ; api/worker khởi động mà thấy bản gửi tạm thì trả về trước. Trong lúc đó đổi/xoá tài khoản trả `409 CLI_LOGIN_IN_PROGRESS`; lượt gọi AI qua CLI chuyển nhà cung cấp kế tiếp mà không đánh dấu CLI hết hạn. Trạng thái đăng nhập còn đọc được qua `GET /cli/login/{id}` (UI hỏi 2 giây/lần, không phụ thuộc WebSocket).
  - Hết hạn/lỗi → provider `expired`, chuỗi chuyển hướng sang khoá API kế tiếp.
  - **Phạm vi dùng** (v0.1.62): Antigravity CLI chạy được MỌI vai — Gen của Owner và nhân viên (kể cả lượt đã đọc nội dung bên ngoài), sàng lọc & suy luận chính, soạn lại / dịch nháp, Bản tin Gen, agent tự tạo, việc nền. Owner chọn mở ngày 16/10/2026 sau khi được báo rủi ro (luật v0.1.38 "chỉ cho Gen của Sếp" đã gỡ). Rủi ro còn lại: agy có công cụ đọc tệp / chạy lệnh và chạy cùng container với khoá hệ thống nên nội dung bên ngoài có thể lừa nó đọc tệp trong container; lớp cô lập mỗi lượt (cwd rỗng 0700, env sạch, prompt qua stdin) vẫn giữ. Bước kế tiếp (chưa làm): tách agy ra container riêng không gắn khoá. Riêng Claude Code CLI vẫn không chạy việc nền khi Owner chưa cho phép (điều khoản gói Claude).

---

## 12. Mô hình dữ liệu

Lược đồ hiện hành: `db/sql/0001_baseline.sql` + các migration sau (`apps/api/migrations`) (`schema.sql` gốc của gói bàn giao đã bỏ ở v0.1.48, xem lịch sử git) (9 schema: `core, raw, refinery, clean, memory, biz, agent, ops, analytics`), giữ nguyên mọi quy ước của `docs/handoff/03-database.md`: khoá UUIDv7 (`core.uuid_v7()`), mã công khai ở cột `code` (`GRP-ZL-0114`, `PER-0042`, `OPP-1842`, `ACT-0231`…), mọi bảng có `org_id`, `timestamptz` UTC hiển thị theo `organizations.timezone`, tiền `bigint` đồng, `core.lookup` thay ENUM, `attrs jsonb`, xoá mềm/`merged_into_id`, phân vùng tháng bằng pg_partman, lớp `analytics` materialized view làm mới `CONCURRENTLY`.

Thực thể spec G1 → bảng:

| Spec G1 | Bảng |
|---|---|
| Identity / Channel Account | `core.persons` / `core.person_identities` |
| Organization | `core.organizations` (tổ chức sử dụng) + tổ chức của đối tượng trong `attrs` của person |
| Group / Space | `core.groups`, `core.group_members` |
| Relationship | `clean.relationships` |
| Conversation Thread | nhóm/tin riêng qua `raw.events.group_id` + `clean.meaning_units` |
| Message / Event | `raw.events` / `clean.meaning_units` |
| Intent | `clean.meaning_units.event_type` + `core.lookup(kind='intent')` |
| Opportunity | `biz.opportunities`, `biz.opportunity_stage_history`, `biz.market_signals`, `biz.matches` |
| Deal / Case | `biz.deals`, `biz.cases` |
| Document | `biz.documents`, `biz.document_acl` |
| Task / Reminder | `biz.tasks`, `biz.promises` |
| Evaluation Snapshot | `clean.score_snapshots`, `biz.people_reviews`, `biz.review_disputes` |
| Alert | hàng đợi ý nghĩa — xem bổ sung dưới |
| Action Log | `ops.action_log` |
| Plugin / Connector State | `ops.plugins`, `ops.plugin_dependencies`, `ops.breaker_events`, `ops.plugin_logs` |

**Bổ sung dự kiến** (migration riêng, giữ quy ước):
| Bảng | Lý do |
|---|---|
| `agent.decisions` | Lưu quyết định của agent trực kênh + ID ngữ cảnh đã dùng (§5). |
| `biz.alerts` | Spec E9 yêu cầu cảnh báo có mức ưu tiên, người nhận, hành động đề xuất; schema chưa có bảng riêng. Hộp thư ý nghĩa hợp nhất `meaning_units` + `alerts` + `action_drafts` + `tasks` thành một hàng đợi. |
| `biz.inbox_items` (view) | Hàng đợi hợp nhất cho Hộp thư ý nghĩa và khối Hàng đợi ở Tổng quan. |
| cột `permit_hash`, `permit_expires_at` ở `biz.action_drafts` | Permit dùng một lần (§7.3). |
| `agent.cli_profiles` | Nhiều tài khoản Antigravity CLI, một hồ sơ hoạt động (§11). |
| `core.assignments` | Khách được phân cho Agent nhân viên, hàng đợi cho Operator (phạm vi `assigned`). |
| `ops.listen_events` + cột `raw.events.listen_mode` (v0.1.68, migration 0040) | Sự kiện kênh và chế độ nghe lúc tin vào cho Nhật ký lắng nghe (§4.2). |

---

## 13. API

REST JSON `/api/v1`, OpenAPI tự sinh; phân trang con trỏ `?cursor=&limit=`; lọc/sắp xếp qua query phản ánh vào URL; lỗi RFC 7807; `Idempotency-Key` cho ghi (chưa thi hành — server không đọc header này); 🔒 = cần phiên PIN (423 khi thiếu). WebSocket `/ws`: hàng đợi, tin thô LIVE, tiến độ refinery, trạng thái phiên QR, log plugin LIVE, nhật ký MCP LIVE, tiến độ thiết lập. Mọi route có `require(permission)` + `ScopeFilter`.

| Nhóm | Endpoint | Màn |
|---|---|---|
| Auth | `POST /auth/login` · `POST /auth/logout` · `GET /auth/me` · `POST /auth/pin/verify` · `PUT /auth/pin` 🔒 · `POST /auth/totp` | đăng nhập, PinDialog |
| Thiết lập | `GET /setup/state` · `PUT /setup/steps/{n}` · `POST /setup/seed` · `POST /setup/finish` | `/setup` |
| Khung | `GET /navigation` (cây từ `screens.json`, lọc theo quyền, kèm badge) · `GET /header` (kênh/nhóm LIVE, tự trị, độ tin dữ liệu) · `GET/POST/DELETE /views` | shell |
| Tổng quan | `GET /overview` (kpis, queue, spotlight, signals, health, dataQuality, hourly + KPI F4) | overview |
| Hộp thư | `GET /inbox?tab=` · `GET /inbox/{id}` · `POST /inbox/{id}/act` · `POST /inbox/{id}/assign` · `POST /inbox/{id}/silence` | inbox |
| Bàn làm việc | `GET /drafts` · `GET /drafts/{id}` · `POST /drafts/{id}/approve` 🔒 · `POST /drafts/{id}/edit-send` 🔒 · `POST /drafts/{id}/reject` 🔒 · `POST /drafts/{id}/translate` · `POST /drafts/{id}/regenerate` | workbench |
| Nhóm & Con người | `GET /groups?channel=` · `GET /groups/{id}` · `PATCH /groups/{id}` (chế độ nghe 🔒, BOT) · `GET /persons?filters` · `PUT /persons/{id}/bot` | directory |
| Bản đồ | `GET /graph?mode=list|people|groups|topics&filters` · `PUT /graph/layout` | graph |
| Hồ sơ sống | `GET /profiles/{id}` · `GET /profiles/{id}/timeline` · `PUT /profiles/{id}/autonomy` · `PUT /profiles/{id}/owner` · `GET/POST /profiles/{id}/notes` | profile |
| Sổ tay | `GET /notebooks?type=person|group` · `GET /notebooks/{type}/{id}` · `PATCH /notebooks/entries/{id}` (sửa/ghim) · `DELETE /notebooks/entries/{id}` · `POST /notebooks/{type}/{id}/compact` · `POST /notebooks/{type}/{id}/reset` 🔒 | notebook |
| Cơ hội | `GET /opportunities` · `PATCH /opportunities/{id}/stage` · `PATCH /opportunities/{id}` | opportunity |
| Cung ↔ Cầu | `GET /market` · `GET /market/matches` · `POST /market/matches/{id}/introduce` (tạo bản nháp) · `POST /market/signals/{id}/act` | supply |
| Kho hội thoại | `GET /search?q=&facets` · `GET /search/saved` · `GET /search/patterns` · `POST /search/bulk-action` (qua duyệt) | search |
| Đánh giá | `GET /reviews?board=staff|customer|candidate|learner` 🔒 · `GET /reviews/{id}/evidence` 🔒 · `POST /reviews/{id}/override` 🔒 · `POST /reviews/{id}/disputes` | people |
| Chăm sóc | `GET /care/kpis` · `GET /care/response-grid` · `GET /care/patterns` · `GET /care/scripts` | care |
| Chứng cứ | `GET /explain/{kind}/{id}` · `GET /raw/{id}` | mọi màn |
| Kho thô | `GET /raw?filters` · `GET /raw/by-group` · `GET/PUT /refinery/schedule` · `POST /refinery/run` · `GET /refinery/runs` | raw |
| Quy tắc | `GET /rules` · `POST /rules` · `PUT /rules/{id}` (tạo phiên bản) · `PATCH /rules/{id}/toggle` · `GET/PUT /rules/weights` · `POST /rules/test` | rules |
| Kho sạch | `GET /clean?filters` · `GET /clean/{id}/memory` · `GET /clean/agent-params` | clean |
| Danh tính | `GET /identity/stats` · `GET /identity/candidates` · `POST /identity/merge` 🔒 · `POST /identity/{id}/split` 🔒 · `POST /identity/candidates/{id}/reject` · `GET /identity/{id}/history` | identity |
| Agent | `GET/POST /agents` · `GET/PATCH /agents/{id}` · `POST /agents/{id}/clone` · `PATCH /agents/{id}/disable` · `POST /agents/{id}/archive` (Lưu trữ) · `GET /agents/decisions` (nhật ký quyết định) · `GET /agents/templates` | agents |
| Provider & model | `GET /providers` · `POST /providers` 🔒 · `PATCH /providers/chain` 🔒 (thứ tự chuyển dự phòng) · `PATCH/DELETE /providers/{id}` 🔒 (xoá cần PIN từ v0.1.68) · `POST /providers/{id}/keys` 🔒 · `DELETE /providers/{id}/keys/{kid}` 🔒 · `POST /providers/{id}/models` 🔒 · `DELETE /providers/{id}/models/{mid}` 🔒 (PIN `ai.route_change` từ v0.1.70) · `POST /providers/{id}/test` · `POST /providers/{id}/diagnose` · `GET /providers/credentials` · `GET/PUT /providers/background` (việc nền) · `GET /agents/bindings` · `PUT/DELETE /agents/bindings/{agent_key}` (gán model riêng; mặc định là hồ sơ tiêu chuẩn theo vai) · `GET /cli/profiles` · `POST /cli/login` (+ `/cli/login/{id}/code`, `/cancel`) 🔒 · `POST /cli/profiles/{id}/activate` 🔒 · `DELETE /cli/profiles/{id}` 🔒 | api, system › Bộ não AI |
| MCP | `GET /mcp/stats` · `GET /mcp/servers` · `POST /mcp/servers` 🔒 · `PATCH /mcp/servers/{id}` 🔒 · `PATCH /mcp/tools/{id}` (mở/đóng) 🔒 · `PUT /mcp/tools/{id}/grants` 🔒 · `GET /mcp/calls` · `GET /mcp/guards` · `GET /mcp/market` | mcp |
| Plugin | `GET /plugins?tab=core|addon` · `GET /plugins/load-order` · `GET /plugins/events` · `POST /plugins/install` 🔒 · `PATCH /plugins/{id}/toggle` · `DELETE /plugins/{id}` 🔒 · `PUT /plugins/{id}/settings` · `POST /plugins/{id}/breaker/reset` · `GET /plugins/market` | plugins |
| Kênh | `GET /channels` · `POST /channels/{id}/sessions` (tạo QR) · `POST /channels/sessions/{id}/logout` 🔒 · `POST /channels/sessions/{id}/rescan` | system › Kênh & đăng nhập |
| Quyền | `GET /roles/matrix` · `PUT /roles/matrix` 🔒 · `GET/POST /users` · `PATCH /users/{id}` 🔒 · `POST /users/invite` | system › Quyền hạn |
| Chính sách | `GET/PUT /policy/boundaries` 🔒 · `GET/PUT /policy/autonomy` 🔒 · `GET/PUT /policy/listen-groups` 🔒 | system |
| Nhật ký | `GET /audit?filters` · `GET /audit/export.csv` · `GET /audit/verify` | system › Nhật ký |
| Dữ liệu | `GET/PUT /data/retention` 🔒 · `POST /data/requests` 🔒 · `DELETE /data/sample` 🔒 · `POST /data/backup` 🔒 | system › Dữ liệu & lưu trữ |
| Vận hành | `GET /health` · `GET /ready` | trình cài, `genh status` |

---

## 14. Chỗ lệch so với handoff và lý do

| # | Handoff | Ở đây | Lý do |
|---|---|---|---|
| D1 | Bridge `INSERT` thẳng `raw.events` (03, 04) | Bridge đẩy vào event bus `gh.bridge.inbound`, consumer `ingest` INSERT | Yêu cầu của dự án ghi rõ "đẩy sự kiện vào kho thô qua event bus". Thêm lợi ích: bridge không cần quyền DB (ít quyền nhất), tin được đệm trong stream khi DB tạm chết. Kho thô vẫn là điểm lưu bền đầu tiên, nguyên trạng. |
| D2 | Không nêu bảng cảnh báo, quyết định agent, permit, hồ sơ CLI, phân công | Thêm theo §12 | Spec E9, E13 ("agent nào đã nói gì, nhân danh gì"), R5, R10, H2 cần chúng. |
| D3 | 6 giai đoạn (có trình cài `genh`, trình thiết lập Owner 12 bước) | Gộp vào kế hoạch 5 + 1 giai đoạn của PLAN.md | Tin nhắn giao việc của dự án có 5 giai đoạn, không nhắc trình cài. Chủ dự án đã duyệt cách gộp (Q1). |

Không có chỗ lệch nào về giao diện.
