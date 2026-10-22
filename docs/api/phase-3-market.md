# API giai đoạn 3 · Cơ hội & Thị trường (`market`)

Nền chung ở [`phase-3.md`](phase-3.md): hình dạng dùng chung (`PersonRef`, `GroupRef`, `UserRef`, `EvidenceRef`),
phạm vi dữ liệu (`ScopeFilter`), chứng cứ (`GET /explain/{kind}/{id}`), Bàn làm việc (bản nháp). Cụm này phụ
trách **Bảng cơ hội** (`opportunity`), **Cung ↔ Cầu** (`supply`), **Kho hội thoại** (`search`) và **Deal & Vụ
việc** (`deals`, không có màn riêng trong thiết kế gốc — dựng theo `docs/handoff/01-ui-screens.md` §"Màn còn
thiếu", cùng ngôn ngữ). Cả 4 màn dùng chung hai quyền: `opportunity.read` / `opportunity.write`.

Mã: `apps/api/gh/biz/market/` (`service.py` hình dạng + phạm vi + thuật toán chấm điểm, `routes.py` mọi
endpoint, `jobs.py` hook ghi tín hiệu + việc chấm lại điểm định kỳ). Migration: `db/sql/0008_p3_market.sql`.

## Bảng gốc đã có sẵn — không phải bảng mới

`biz.opportunities` + `biz.opportunity_stage_history`, `biz.market_signals` + `biz.matches`, `biz.deals`,
`biz.cases` đều đã có từ giai đoạn 1 (`db/sql/0001_baseline.sql`; `docs/handoff/schema.sql` gốc đã bỏ ở v0.1.48). Cụm này chỉ `ALTER` cột còn thiếu
(`created_at`/`updated_at` trên `deals`, `updated_at` trên `cases`/`matches`) và thêm chỉ mục.

`biz.cases` **khác** `biz.alerts` (cụm `queue` dùng `biz.alerts` cho cảnh báo sớm spec E9) — trước cụm này chưa
ai dùng `biz.cases`. Ở đây chỉ dùng `kind = 'complaint'` ("Vụ việc"); `kind IN ('alert', 'system')` để dành cho
sau, không xuất hiện ở endpoint nào của cụm này.

## Tín hiệu cung/cầu → cơ hội (tự động, hook sau sàng lọc)

`gh.biz.market.jobs.market_signal_capture` (hook `gh.clean.ready`) đọc `clean.meaning_units.side`
(`demand`/`supply`, do quy tắc R-01 "Nhận diện nhu cầu mua" / R-02 "Nhận diện nguồn cung" của
`gh.refinery.presets` gán) và sinh một dòng `biz.market_signals`:

| `market_signals` | Nguồn (`entities`, xem `gh.refinery.extract`) |
|---|---|
| `item` | `entities.product`, rỗng thì lấy `conclusion` |
| `category` | `entities.category` (LLM hiếm khi điền — thường `NULL`) |
| `quantity` / `unit` | `entities.qty` / `entities.unit` |
| `value_vnd` | `entities.budget_vnd` |
| `location` | `entities.place` |
| `needed_by` | `entities.deadline`, chỉ nhận dạng `YYYY-MM-DD…`; văn bản tự do khác (vd "cuối tháng sau") để trống — không tự suy diễn ngày (quyết định tự đưa ra, tránh đoán sai) |
| `heat` | `confidence × 100` |

Idempotent qua `ON CONFLICT (meaning_unit_id) DO NOTHING` (chỉ mục riêng phần ở `0008_p3_market.sql`) — hook
chạy lại (retry/DLQ) không sinh tín hiệu trùng.

Tín hiệu **cầu** gắn với một người còn mở (hoặc cộng dồn giá trị vào) một `biz.opportunities` ở giai đoạn
`raw_signal` — đúng spec E3 "phát hiện người đang cần hàng… tạo hàng đợi cơ hội cho người dùng xử lý". Cùng một
người có cơ hội còn mở trong 30 ngày gần nhất (`DEMAND_DEDUPE_DAYS`) thì không mở trùng, chỉ nâng `value_vnd`
nếu tín hiệu mới có giá trị cao hơn — quyết định tự đưa ra: spec không nói khi nào một tín hiệu cầu là "cùng
một nhu cầu đang nói tiếp" hay "nhu cầu mới", 30 ngày là ngưỡng hợp lý để tránh vỡ một hội thoại dài thành nhiều
thẻ cơ hội. Tín hiệu **cung** không tự mở cơ hội (cơ hội luôn đại diện phía cầu).

## 1) Bảng cơ hội (`opportunity`)

9 giai đoạn theo spec (`gh.biz.market.service.STAGES`): `raw_signal → validated → matched → approaching →
negotiating → handed_off → won | lost | dormant`.

- `GET /opportunities?stage=&owner_user_id=&confidence=high|medium|low&cursor=&limit=` (`opportunity.read`) →
  ```json
  {"items": [{"id", "code": "OPP-1842", "need": "…", "stage": "matched", "value_vnd": 1200000000 | null,
     "confidence": "high", "heat": 87.0 | null, "person": PersonRef | null, "group": GroupRef | null,
     "owner": UserRef | null, "first_signal_at", "first_contact_at", "closed_at", "created_at", "updated_at",
     "suggested_match": {"item": "…", "score": 76.0, "person": PersonRef | null, "group": GroupRef | null}
       | null,
     "risk_note": "Chưa tiếp cận sau 24 giờ kể từ tín hiệu đầu tiên — dễ mất vào tay đối thủ" | null}],
   "next_cursor", "total"}
  ```
  `heat` đọc `clean.current_scores` (`subject_type='person', dimension='heat'`) của người gắn với cơ hội — điểm
  hiện tại của **người**, không phải điểm riêng của cơ hội (không có cột riêng, dùng lại đúng nguồn "vì sao hệ
  thống nghĩ vậy" đã có). `suggested_match` là gợi ý ghép Cung↔Cầu điểm cao nhất còn `suggested` cho tín hiệu
  cầu gắn với cơ hội này (nếu có) — trả lời "nên ghép với ai / hàng gì" của spec §5. `risk_note` tính tại lúc
  trả lời: chưa tiếp cận (`first_contact_at IS NULL`) quá 24 giờ kể từ tín hiệu đầu, hoặc đứng yên (không đổi
  `updated_at`) quá 7 ngày ở giai đoạn hiện tại — hai ngưỡng là quyết định tự đưa ra (spec chỉ nói "rủi ro nếu
  không làm gì", không định lượng).
- `GET /opportunities/pipeline` (`opportunity.read`) →
  `{"stages": [{"stage": "raw_signal", "count": 12, "value_vnd": 3400000000}, …9 giai đoạn…],
   "open_pipeline_value_vnd": …, "open_pipeline_count": …}` — **`won`/`lost`/`dormant` không tính vào
  `open_pipeline_value_vnd`/`open_pipeline_count`** (đã chốt/đóng, không còn "đang chạy") — quyết định tự đưa
  ra vì PLAN §3.8 chỉ nói "tổng pipeline" không định nghĩa có tính giai đoạn đóng; số theo từng giai đoạn (kể
  cả đóng) vẫn trả đủ để đối chiếu tỉ lệ thắng/thua.
- `GET /opportunities/{id}` (`opportunity.read`) → item trên, cộng `"stage_history": [{"from_stage", "to_stage",
  "actor", "at"}]` (mới nhất trước). Chứng cứ: `GET /explain/opportunity/{id}`.
- `POST /opportunities` (`opportunity.write`) `{"person_id", "need", "value_vnd"?, "confidence"?}` → `201`, mở
  tay ở `raw_signal` (khi biết tin ngoài luồng chat).
- `PATCH /opportunities/{id}/stage` (`opportunity.write`) `{"to_stage"}` → **kéo thả**: ghi
  `biz.opportunity_stage_history` (from/to/actor/at), rời `raw_signal` lần đầu tự đặt `first_contact_at`, vào
  `won|lost|dormant` tự đặt `closed_at`. `to_stage` không hợp lệ → `422`.

## 2) Cung ↔ Cầu (`supply`)

- `GET /supply?side=demand|supply&status=open|matched|closed|ignored&category=&cursor=&limit=`
  (`opportunity.read`) → danh sách `biz.market_signals` (`item`, `category`, `quantity`, `unit`, `value_vnd`,
  `location`, `needed_by`, `heat`, `status`, `person`/`group`).
- `GET /supply/{id}` (`opportunity.read`) → item trên, cộng `"matches": [{"id", "score", "reasons": […],
  "status", "item", "person", "group"}]` (phía đối diện, điểm cao nhất trước).
- `GET /matches?status=&min_score=&cursor=&limit=` (`opportunity.read`, phạm vi theo **phía cầu**) → danh sách
  `{"id", "score", "reasons": […], "status", "opportunity_id", "created_at",
  "demand": {"id", "item", "person", "group"}, "supply": {"id", "item", "person", "group"}}`.
- `POST /matches/recompute` (`opportunity.write`) → chấm lại điểm ngay (ngoài lịch nền mỗi 15 phút).
- `POST /matches/{id}/introduce` (`opportunity.write`) — **"Giới thiệu hai bên"**: mở (hoặc gắn vào) một cơ hội
  ở giai đoạn `matched`, chuyển `matches.status → introduced`, và tạo **bản nháp chờ duyệt** (`kind=message`
  nếu tìm được kênh của người bên cầu, `kind=report` nếu không) giới thiệu tín hiệu cung cho người đang cần —
  qua `gh.biz.core.drafts.create_draft(...)` có sẵn, luôn dừng ở Bàn làm việc (luật cứng Q2). Hướng giới thiệu
  cố định **cầu → cung** (báo cho khách đang cần biết có nguồn phù hợp): quyết định tự đưa ra vì đó là chiều có
  người liên hệ được trong hệ thống của mình; không có kênh cho phía cầu vẫn tạo được bản nháp (loại `report`,
  nội bộ) để không chặn luồng. Đã quyết (`introduced|accepted|rejected`) → `409 MATCH_DECIDED`.
- `POST /matches/{id}/reject` (`opportunity.write`) → `matches.status → rejected`. Đã quyết → `409
  MATCH_DECIDED`.

### Thuật toán chấm điểm (`gh.biz.market.service.score_match`, hàm thuần — test trực tiếp được)

Cổng bắt buộc: **không cùng mặt hàng** (từ khoá chung trong `item`) **và không cùng ngành hàng** (`category`
trùng) → điểm 0, không lưu gợi ý (tránh ghép bừa, spec E3). Sau đó cộng dồn, tối đa 100:

| Yếu tố | Điểm | Điều kiện |
|---|---|---|
| Cùng mặt hàng (từ khoá chung) | +50 | có ít nhất 1 từ chung giữa hai `item` (casefold) |
| Cùng ngành hàng (khi không cùng mặt hàng) | +30 | `category` trùng, cả hai khác rỗng |
| Số lượng khớp | +0–20 | cả hai có `quantity` — `20 × min/max` |
| Trong ngân sách | +20 | cả hai có `value_vnd`, cung ≤ ngân sách cầu |
| Vượt ngân sách | +0–20 (giảm dần) | cung > ngân sách cầu — `20 × (1 − phần trăm vượt)`, sàn 0 |
| Cùng khu vực | +10 | `location` trùng (casefold), cả hai khác rỗng |

Mỗi lý do trong `reasons` ghi rõ số điểm đã cộng (vd `"Cùng mặt hàng: "Thép cuộn" ~ "Thép tấm" (+50)"`) — kiểm
chứng lại được bằng tay. Ngưỡng lưu: `score ≥ 40` (`MIN_MATCH_SCORE`, quyết định tự đưa ra — dưới ngưỡng này
không đáng để đưa vào Cung↔Cầu, tránh nhiễu). Việc chạy lại (`gh.biz.market.jobs.recompute_matches_org`) chấm
mọi cặp (tín hiệu cầu `open`) × (tín hiệu cung `open`), tối đa 500 tín hiệu mỗi phía một lượt; không đụng cặp
đã `introduced`/`accepted`/`rejected` (`ON CONFLICT … WHERE status = 'suggested'`).

## 3) Kho hội thoại (`search`)

- `GET /search?q=&event_type=&channel=&date_from=&date_to=&cursor=&limit=` (`opportunity.read`) →
  ```json
  {"items": [{"person": PersonRef, "match_count": 3, "last_at": "…", "last_snippet": "…",
     "last_event_type": "AskedPrice", "evidence": {"type": "meaning_unit", "id": "…"}}],
   "next_cursor", "total",
   "facets": {"event_type": [{"value": "AskedPrice", "count": 12}, …],
              "channel": [{"value": "zalo", "count": 8}, …]}}
  ```
  Kết quả **là người**: các đơn vị ý nghĩa khớp được gộp theo `person_id`, mới tương tác nhất trước; đơn vị
  không gắn với người nào (chỉ có `group_id`) bị bỏ qua khỏi kết quả — quyết định tự đưa ra vì spec §8 nói rõ
  "search không chỉ theo chữ… tìm ra mẫu" và màn hiển thị theo người, không dựng được một dòng người từ một đơn
  vị vô danh. Facet tính trên tập đã lọc theo phạm vi + `event_type`/`channel`/khoảng ngày (không lọc theo `q`)
  để bộ lọc không tự thu hẹp chính nó.

  Tìm là **từ khoá + ngữ nghĩa cộng lại**, không phải hai chế độ tách biệt: có `q` thì vừa quét `ILIKE` trên
  `conclusion`/`entities` (recall rộng, tối đa 500 đơn vị gần nhất), vừa hỏi model embedding (nếu tổ chức đã
  cấu hình — `clean.meaning_units.embedding`, cột `vector(768)` có sẵn từ giai đoạn 2,
  `gh.refinery.runner._embed`) lấy 80 đơn vị gần nghĩa nhất (`ORDER BY embedding <=> …`), rồi gộp hai tập trước
  khi nhóm theo người. Không có model embedding, hoặc lỗi gọi model → im lặng bỏ qua phần ngữ nghĩa, không
  chặn kết quả từ khoá (cùng nguyên tắc "embedding không được làm hỏng lượt sàng lọc" đã áp dụng ở giai đoạn 2).
  Không có `q` → duyệt theo facet/thời gian, không cần từ khoá.
- `POST /search/bulk` (`opportunity.write`) `{"person_ids": […, tối đa 200], "action": "tag"|"task", "text",
  "due_at"?, "priority"?}` → hành động hàng loạt trên kết quả. Cả hai loại đều **nội bộ** (không ghi ra ngoài)
  nên làm **trực tiếp**, không qua Bàn làm việc — `tag` ghi một mục sổ tay (`note.write`), `task` tạo một việc
  theo dõi (`task.create`) cho mỗi người, qua đúng cơ chế hành động nội bộ đã có
  (`gh.biz.core.drafts._execute_internal`, cùng cơ chế "Tạo kèm theo" của bản nháp) — quyết định tự đưa ra:
  spec nói "hành động hàng loạt… đi qua Bàn làm việc/bản nháp nếu ghi ra ngoài, hoặc trực tiếp nếu chỉ nội bộ",
  hai loại này không ghi ra kênh ngoài nên đi thẳng.

## 4) Deal & Vụ việc (`deals`)

`biz.deals` (đã chốt) và `biz.cases` (`kind='complaint'` — khiếu nại) theo spec G1 + PLAN §3.14.

- `GET /deals?status=open|won|lost&person_id=&cursor=&limit=` (`opportunity.read`) → `{"items": [{"id", "code":
  "DEA-0091", "opportunity_id" | null, "person": PersonRef | null, "amount_vnd", "status", "won_at" | null,
  "erp_ref" | null, "created_at", "updated_at"}], "next_cursor", "total"}`.
- `GET /deals/{id}` (`opportunity.read`) → item trên.
- `POST /deals` (`opportunity.write`) `{"person_id", "amount_vnd", "opportunity_id"?, "erp_ref"?}` → `201`,
  `status='open'`.
- `PATCH /deals/{id}` (`opportunity.write`) `{"status"?, "amount_vnd"?, "erp_ref"?}` → `status='won'` tự đặt
  `won_at`; đổi sang `won`/`lost` còn đồng bộ cơ hội gắn kèm (nếu có) sang cùng giai đoạn (không ghi đè cơ hội
  đã `won`/`lost` từ trước) — quyết định tự đưa ra: một deal chốt/mất mà cơ hội gốc vẫn "đang đàm phán" là dữ
  liệu mâu thuẫn, đồng bộ một chiều deal → cơ hội cho nhất quán.
- `GET /cases?status=&assignee_user_id=&priority=&cursor=&limit=` (`opportunity.read`) → chỉ `kind='complaint'`
  → `{"items": [{"id", "code": "CAS-0018", "kind": "complaint", "priority", "title", "status", "assignee":
  UserRef | null, "subject": PersonRef | GroupRef | null, "opened_at", "resolved_at" | null, "updated_at"}],
   "next_cursor", "total"}`.
- `GET /cases/{id}` (`opportunity.read`) → item trên.
- `POST /cases` (`opportunity.write`) `{"title", "priority"?, "subject"?: {"type", "id"}, "assignee_user_id"?}`
  → `201`, `kind='complaint'`, `status='open'`.
- `PATCH /cases/{id}` (`opportunity.write`) `{"status"?: "open"|"in_progress"|"resolved"|"closed",
  "assignee_user_id"?, "priority"?}` → `resolved`/`closed` tự đặt `resolved_at`.

Phạm vi: `deals` theo người của deal (`person_id`); `cases` theo đối tượng gắn vào (`subject_type`/
`subject_id`) **hoặc** người xử lý (`assignee_user_id`) là mình/team mình — cùng mẫu `biz.tasks` của cụm
`queue` (docs/api/phase-3-queue.md).

## Chứng cứ

`GET /explain/opportunity/{id}` (`opportunity.read`) đăng ký ở `gh.biz.market.routes`, cùng hình dạng
`docs/api/phase-3.md` §Chứng cứ.
