# API giai đoạn 3 · Con người & Chất lượng (`people`)

Nền chung ở [`phase-3.md`](phase-3.md): hình dạng dùng chung (`PersonRef`, `UserRef`, `EvidenceRef`), phạm vi dữ
liệu (`ScopeFilter`), chứng cứ (`GET /explain/{kind}/{id}`). Cụm này phụ trách **Đánh giá con người** (`people`)
+ **Phản biện** (spec I), **Chất lượng chăm sóc** (`care`), và **trình thiết lập bước 8–9** (agent đầu tiên +
thử trò chuyện; tự trị & ranh giới).

Mã: `apps/api/gh/biz/people/` (`service.py` hình dạng + ghép "tin đến → tin đi cùng luồng" dùng chung cho
`care`/job tính điểm, `routes.py` mọi endpoint `people`/`care` + `try_chat` cho bước 8, `jobs.py` việc tính
điểm hiệu suất định kỳ). Migration: `db/sql/0009_p3_people.sql`. Bước 8–9 nằm trong `apps/api/gh/setup/routes.py`
(cùng file với bước 1–7/12, không tách riêng).

## Bảng gốc đã có sẵn — không phải bảng mới

`biz.people_reviews`, `biz.review_disputes`, `biz.promises` đều đã có từ giai đoạn 1 (`db/sql/0001_baseline.sql`; `docs/handoff/schema.sql` gốc đã bỏ ở v0.1.48).
Cụm này chỉ `ALTER` cột còn thiếu (lịch sử sửa tay + PIN xem trên `people_reviews`, người giải quyết trên
`review_disputes`) và thêm chỉ mục — không tạo lại bảng nào.

## 0) Quy tắc hiển thị bắt buộc — Q4 (quyết định của chủ dự án, `docs/PLAN.md`)

> "Đánh giá nhân sự: mặc định chỉ Owner thấy nội dung; Auditor thấy nhật ký ai đã xem; Manager không thấy. Owner
> có thể tự cấp thêm cho vai trò khác trong Quyền hạn (cần PIN, ghi log)."

`gh.biz.people.routes._access_mode(user)` tính một trong ba nhánh cho **mọi** endpoint `people/reviews*`,
KHÔNG dùng `Depends(require(...))` như các cụm khác (vì Auditor phải nhận `200`, không phải `403`):

| Nhánh | Ai | Hành vi |
|---|---|---|
| `full` | Owner (`people_review.read = all` mặc định); vai trò khác nếu Owner tự cấp thêm trong Quyền hạn (GĐ 4) | Thấy đầy đủ điểm/tín hiệu/khuyến nghị/chứng cứ. Mọi đọc (danh sách + chi tiết) đòi phiên PIN còn hiệu lực (🔒 `people_review.read`, `423 PIN_REQUIRED`). Xem **một** đánh giá cụ thể (`GET /people/reviews/{id}`) hoặc chứng cứ (`GET /explain/review/{id}`) ghi Action Log mỗi lần (`people_review.viewed`/`people_review.explained`) — xem **danh sách** không ghi mỗi lần tải trang (quá dày, quyết định tự đưa ra) |
| `log` | đúng vai trò Auditor | `200` cho mọi GET, nhưng payload chỉ có `id/person/period/created_at/has_content/dispute_count` — KHÔNG có `score/trend/signal/recommendation/evidence`. Kèm `viewed_by`: tối đa 10 dòng nhật ký gần nhất (`ops.action_log`, `action IN ('people_review.viewed','people_review.explained')`) — "nhật ký ai đã xem". Mỗi lần Auditor gọi cũng tự ghi thêm một dòng (`people_review.audit_viewed`) vào chính nhật ký đó — không cần PIN (không xem nội dung) |
| `none` | Manager, Operator, Agent nhân viên (mặc định) | `403 FORBIDDEN` — ẩn hẳn, đúng "Manager không thấy" |

`GET /explain/review/{id}` (đăng ký ở `gh.biz.people.routes`, permission `people_review.read`) chỉ có nhánh
`full` (dùng `gh.biz.core.explain.explain()` sẵn có, tự `403` qua `scope_for` khi quyền là `none`) — Auditor
**không** vào được chuỗi chứng cứ gốc (trích dẫn nguyên văn), chỉ nhánh liệt kê/chi tiết ở trên mới có "nhật ký
ai đã xem"; quyết định tự đưa ra để chuỗi chứng cứ sâu (trích dẫn hội thoại) vẫn khoá chặt hơn bản tóm tắt.

`PATCH /people/reviews/{id}`, `POST .../disputes`, `PATCH .../disputes/{id}` dùng thẳng
`Depends(require("people_review.write"))` (mặc định chỉ Owner) + `Depends(require_pin("people_review.read"))` —
không có nhánh `log` cho ghi, vì Auditor không bao giờ có quyền ghi (khoá cứng, `rbac.WRITE_PERMISSIONS`).

## 1) Đánh giá con người (`people`)

4 board (F2 §6) ánh xạ từ `core.persons.person_type` sẵn có (`gh.biz.people.service.BOARD_PERSON_TYPE`):
`employee→staff`, `customer→customer`, `candidate→candidate`, `student→learner`.

- `GET /people/reviews?board=employee|customer|candidate|student&person_id=&period_start=&period_end=&cursor=&limit=`
  → nhánh theo Q4 ở trên. Hình dạng `full`:
  ```json
  {"items": [{"id", "person": PersonRef, "period_start": "2026-09-01", "period_end": "2026-09-07",
     "score": 82.5, "trend": "up|down|flat" | null, "signal": "…", "recommendation": "…",
     "evidence": [{"type": "meaning_unit", "id": "…"}], "visibility": "owner", "created_at": "…",
     "overridden": false, "overridden_by": UserRef | null, "overridden_at": "…" | null,
     "override_reason": "…" | null, "supersedes_id": "uuid" | null}],
   "next_cursor", "total"}
  ```
  Một dòng = **bản hiện hành** của một (người, kỳ) — dòng mới nhất theo `created_at` trong cùng
  `(person_id, period_start, period_end)`; các bản cũ hơn không hiện ở danh sách, xem ở `history` của chi tiết.
- `GET /people/reviews/{id}` → item trên, cộng (chỉ nhánh `full`):
  - `history`: mọi bản của cùng (người, kỳ), mới nhất trước — `[{"id", "score", "trend", "created_at",
    "overridden_by": UserRef | null, "override_reason": "…" | null}]`.
  - `disputes`: mọi Phản biện của đánh giá này (mục 2).
- `PATCH /people/reviews/{id}` 🔒 `{"score", "reason", "evidence": [EvidenceRef, …1–20], "trend"?, "signal"?,
  "recommendation"?}` → **sửa điểm tay, giữ lịch sử**: KHÔNG `UPDATE` dòng cũ — chèn một dòng **mới** cùng
  `(person_id, period_start, period_end)`, `supersedes_id` trỏ về dòng vừa sửa, `overridden_by`/`overridden_at`
  = mình/giờ hiện tại, `override_reason` = `reason`. Dòng cũ vẫn nguyên vẹn, đọc lại được qua `history` ở trên.
  `evidence` bắt buộc khác rỗng (khoá cứng 7 "điểm số nhân sự phải có chứng cứ" — ràng buộc cả ở CHECK của DB,
  `people_reviews_evidence_nonempty`). Trả về bản mới (tương đương `GET /people/reviews/{new_id}`).

  **Quyết định tự đưa ra** (PLAN §3.11 chỉ nói "giữ lịch sử", không nói cách): thêm 4 cột
  (`supersedes_id`/`overridden_by`/`overridden_at`/`override_reason`) thay vì một bảng lịch sử riêng, vì
  `biz.people_reviews` vốn đã là "một dòng = một lần chấm" (có `period_start/period_end`) — coi mỗi lần sửa tay
  là một lần chấm mới, cùng kỳ, do người chấm thay vì hệ thống. Việc tự động tính lại điểm mỗi kỳ
  (`gh.biz.people.jobs.recompute_people_reviews_org`, mục 4) chỉ `UPDATE` tại-chỗ dòng **hệ thống** của đúng kỳ
  đó (`overridden_by IS NULL`, chặn bằng chỉ mục riêng phần `people_reviews_system_period`) — không sinh rác
  lịch sử mỗi lần chạy lại, và không bao giờ âm thầm đè lên một bản Owner đã sửa tay.

## 2) Phản biện (spec I)

`biz.review_disputes` — mỗi Phản biện gắn với đúng một đánh giá.

- `POST /people/reviews/{review_id}/disputes` 🔒 `{"body"}` → `201`, `status='open'`.
  ```json
  {"id", "review_id", "raised_by": UserRef, "body", "status": "open", "resolution": null,
   "resolved_by": null, "resolved_at": null, "created_at"}
  ```
- `PATCH /people/reviews/disputes/{id}` 🔒 `{"status": "resolved"|"rejected", "resolution"}` → item trên, cộng
  `resolved_by`/`resolved_at`. Đã xử lý → `409 DISPUTE_DECIDED`.

  **Không có hành động kỷ luật tự động** (PLAN §3.11, khoá cứng 2 "hệ thống không tự ra quyết định nhân sự"):
  giải quyết Phản biện CHỈ ghi `status`/`resolution` bằng chữ, không tự đổi `score`, không tự đổi quyền, không
  tự tạo việc kỷ luật. Owner thấy phản biện có lý và muốn sửa điểm thì gọi `PATCH /people/reviews/{id}` riêng —
  một hành động tường minh, có lý do, tự giữ lịch sử của chính nó (mục 1) — hai bước tách biệt để mọi quyết định
  nhân sự đều do NGƯỜI bấm, có ghi lý do, chứ không phải một tác dụng phụ tự động của việc đóng phản biện.

## 3) Chất lượng chăm sóc (`care`)

Quyền `care.read` (mặc định chỉ Owner — `rbac.DEFAULT_MATRIX`, không có nhánh Auditor đặc biệt như `people`, vì
Q4 chỉ nói về Đánh giá nhân sự). Mọi endpoint dùng `Depends(require("care.read"))` bình thường.

Cả 3 endpoint dùng chung cách ghép **"tin đến → tin đi cùng luồng"** (`gh.biz.people.service.PAIR_CTE`) — một
luồng là `(channel_id, group_id)` (tin 1-1 có `group_id IS NULL`, ghép qua `IS NOT DISTINCT FROM`), mỗi tin đến
(`direction='inbound'`) ghép với tin đi (`direction='outbound'`) **sớm nhất** sau nó trên cùng luồng — đúng cách
`gh.biz.queue.jobs._scan_slow_response` đã ghép để sinh cảnh báo `slow_response`, chỉ khác là lấy **hết** cặp
thay vì chỉ phần chưa trả lời. Nguồn thời điểm là `raw.events.occurred_at` (không phải
`clean.meaning_units`/`biz.action_drafts.sent_at`) — quyết định tự đưa ra sau khi đọc `phase-3-queue.md`: đây là
cách hệ thống đã đo tốc độ phản hồi ở nơi khác, giữ một nguồn số duy nhất cho "tốc độ phản hồi" trong toàn hệ
thống thay vì bịa thêm cách đo thứ hai; `meaning_units` không sinh cho tin nhân viên gửi (chỉ sinh cho tin
khách), và `action_drafts.sent_at` chỉ có cho tin đi qua Bàn làm việc, bỏ sót phần lớn hội thoại thật.

- `GET /care/response-times?date_from=&date_to=&person_id=` → lưới phản hồi theo khung giờ **<15 / 15–60 / >60
  phút** (PLAN §3.12), theo từng nhân viên:
  ```json
  {"from": "…", "to": "…",
   "items": [{"staff": PersonRef, "fast": 12, "normal": 5, "slow": 2, "total_answered": 19,
              "fast_pct": 63.2, "avg_minutes": 18.4}],
   "totals": {"fast": …, "normal": …, "slow": …, "total_answered": …, "fast_pct": …},
   "unattended": 3}
  ```
  Mặc định 30 ngày gần nhất (`DEFAULT_WINDOW_DAYS`). `unattended` (tin chưa có ai trả lời) đứng riêng ngoài
  lưới, không phải cột của từng nhân viên — chưa ai trả lời thì không biết quy cho ai (quyết định tự đưa ra).
  Không phải danh sách phân trang — một bảng tổng hợp, cùng cách `GET /opportunities/pipeline` không theo khuôn
  `items/next_cursor/total` đầy đủ.
- `GET /care/repeated-issues?date_from=&date_to=&issue_type=broken_promise|abandoned_customer&limit=` → lỗi
  chăm sóc **lặp lại**:
  ```json
  {"items": [{"kind": "broken_promise", "subject": PersonRef, "count": 3, "repeated": true, "last_at": "…"}],
   "next_cursor": null, "total": …}
  ```
  - `broken_promise` ("hứa rồi quên"): `biz.promises.broken = true` trong khoảng thời gian, nhóm theo
    `promiser_person_id` — cột `broken` do cụm Hàng đợi đánh dấu (`PATCH /tasks/promises/{id}` hoặc cảnh báo
    `forgotten_deadline` khi quá hạn — `docs/api/phase-3-queue.md`), cụm này chỉ **đọc**.
  - `abandoned_customer` ("khách bị bỏ rơi"): tin đến không có tin đi cùng luồng trong `ABANDON_HOURS` (24 giờ,
    hằng nội bộ) — nhóm theo khách.
  - `repeated: true` khi `count ≥ REPEAT_THRESHOLD` (2) — quyết định tự đưa ra, "lặp lại" nghĩa là từ lần thứ
    hai trở lên, không phải một lần lỡ tay.
  - Không phải danh sách phân trang con trỏ thật (bảng tổng hợp theo chủ thể, `limit` chặn số dòng) —
    `next_cursor` luôn `null`, quyết định tự đưa ra vì PLAN không tả rõ hình dạng.
- `GET /care/scenarios?status=won|lost&cursor=&limit=` → kịch bản thắng/mất khách (PLAN §3.12), liên hệ
  `biz.deals.status`:
  ```json
  {"items": [{"deal": {"id", "code", "amount_vnd", "status": "won", "won_at": "…", "opportunity_id": "…" | null},
     "person": PersonRef | null,
     "response": {"fast": 3, "normal": 1, "slow": 0, "unanswered": 0, "fast_pct": 75.0, "avg_minutes": 9.4}
       | null,
     "broken_promises": 0,
     "note": "Kịch bản thắng: phản hồi nhanh 75%, không có lời hứa bị vỡ."}],
   "next_cursor", "total",
   "summary": {"won": {"count": 8, "avg_fast_pct": 71.2, "avg_broken_promises": 0.1},
               "lost": {"count": 3, "avg_fast_pct": 22.0, "avg_broken_promises": 1.3}}}
  ```
  `response`/`broken_promises` tính trong `SCENARIO_WINDOW_DAYS` (14 ngày) trước khi deal chốt (`won_at`, hoặc
  `updated_at` cho deal `lost` — deal không có "ngày mất" riêng). `summary` so sánh won/lost tính trên **trang
  hiện tại** (không quét lại toàn bộ tổ chức mỗi lượt gọi) — quyết định tự đưa ra để tránh quét chi tiết từng
  deal hai lần trên toàn bộ dữ liệu; đủ cho một bảng so sánh nhanh, xem từng deal ở `items` để đối chiếu sâu hơn.

## 4) Tính điểm hiệu suất tự động (nền, không phải endpoint)

`gh.biz.people.jobs.recompute_people_reviews_org` — chạy hằng ngày (cron `people_review_recompute`, 02:30) và
gọi tay được, tính một dòng `biz.people_reviews` cho mỗi nhân viên (`person_type='staff'`) có trả lời tin trong
kỳ 7 ngày gần nhất đã trọn vẹn (kết thúc hôm qua). Không có agent trực kênh sinh dữ liệu này — đây là việc quét
định kỳ theo tổ chức, không đăng ký `HOOKS`.

Công thức điểm (quyết định tự đưa ra — PLAN không cho công thức, cùng tinh thần `market.service.score_match` tự
định nghĩa cách chấm điểm ghép, có lý do đi kèm để kiểm chứng lại được): bắt đầu 70 (trung tính), cộng theo tỉ lệ
phản hồi nhanh (< 15 phút, tối đa +25), trừ theo tỉ lệ phản hồi chậm (> 60 phút, tối đa −15), trừ theo số lời
hứa bị vỡ trong kỳ (−7 mỗi lời hứa, tối đa −20), kẹp về 0–100. `trend` so điểm kỳ này với dòng gần nhất **trước**
kỳ này của cùng người (chênh > 2 điểm mới tính lên/xuống). Chứng cứ (khoá cứng 7) là tối đa 5 đơn vị ý nghĩa mới
nhất của những khách mà nhân viên này đã trả lời trong kỳ — không có đơn vị nào (không đủ chứng cứ) thì **bỏ
qua** người đó trong lượt tính, không ghi dòng thiếu chứng cứ. **Không có hành động kỷ luật tự động**: job chỉ
ghi điểm + tín hiệu + khuyến nghị coaching bằng chữ, không tạo cảnh báo, không đổi quyền, không đổi việc của ai.

## 5) Trình thiết lập bước 8–9

Tối thiểu để trình thiết lập đi hết được (PLAN Q1 "trình thiết lập Owner làm dần qua GĐ 1–4") — quản lý Agent
đầy đủ (nhân bản, mẫu có sẵn, "đã nói gì nhân danh gì"…) là GĐ 4 mục 4.1, không dựng ở đây. Cả hai bước đòi bước
4–7 đã `done` (cùng cách bước 4 đòi bước 1–3 qua `console_ready` — `gh.setup.routes._owner_step_after`).

- `PUT /setup/steps/8` `{"name", "role_desc", "voice"?, "speak_when"?, "template"?, "try_message"}` → tạo
  `agent.identities` đầu tiên (mức tự trị khởi tạo = mặc định chung `policy.DEFAULT_AUTONOMY`, đặt lại chính xác
  3 hay 4 ở bước 9) rồi **thử trò chuyện một lượt** qua `ModelRouter.generate` (agent chưa có gán model riêng
  → dùng chuỗi mặc định của tổ chức đã cấu hình ở bước 4) — **không lưu vào hội thoại thật**, chỉ để Owner nghe
  thử giọng agent. Model chưa gọi được (lỗi tạm thời, hoặc bước 4 mới test qua Antigravity CLI chưa hẳn ổn định)
  không chặn việc tạo agent:
  ```json
  {..., "agent": {"id", "name", "try_reply": "…" | null, "try_error": {"reasons": […]} | null}}
  ```
- `PUT /setup/steps/9` `{"autonomy_level": 3|4, "ack_boundaries": true}` → đặt mức tự trị cho agent vừa tạo
  (spec H1 cho phép 3 hoặc 4, mặc định 4) và bắt Owner xác nhận đã đọc danh sách **ranh giới khoá cứng**
  (ARCHITECTURE §7.4, `HARD_BOUNDARIES` — 8 mục, không tắt được ở đây hay bất cứ đâu trong hệ thống; xác nhận
  chỉ để Owner biết trước khi vào Console, không phải một cài đặt). `ack_boundaries` sai/thiếu → `422`. Chưa có
  agent (bước 8 chưa xong) → `409 STEP_INCOMPLETE`.
  ```json
  {..., "agent": {"id", "name", "autonomy_level": 4}, "hard_boundaries": ["Chỉ lưu tin của nhóm Sếp đã chọn nghe", …]}
  ```
