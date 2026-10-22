# API giai đoạn 3 · Hàng đợi & Hành động (`queue`)

Nền chung ở [`phase-3.md`](phase-3.md): hình dạng dùng chung (`PersonRef`, `GroupRef`, `Score`, `EvidenceRef`),
phạm vi dữ liệu (`ScopeFilter`), chứng cứ (`GET /explain/{kind}/{id}`), góc nhìn đã lưu, Bàn làm việc (bản nháp).
Cụm này phụ trách **Hôm nay** (`overview`, trước là "Tổng quan điều hành"), **Hộp thư** (`inbox`), **Việc & Nhắc hẹn**
(`tasks`, không có màn riêng trong thiết kế gốc — dựng theo `docs/handoff/01-ui-screens.md` §"Màn còn thiếu")
và **cảnh báo sớm** (spec E9). Bàn làm việc (giao diện duyệt) đã có đủ ở `gh/biz/core`.

Mã: `apps/api/gh/biz/queue/` (`service.py` hình dạng + phạm vi dùng chung, `routes.py` mọi endpoint, `jobs.py`
việc quét cảnh báo sớm định kỳ). Migration: `db/sql/0005_p3_queue.sql`.

## `biz.alerts` đã có sẵn — không phải bảng mới

`docs/PLAN.md` §12 dự kiến một bảng `biz.alerts`; bảng này **đã được tạo ở giai đoạn 1** (`db/sql/0002_phase1.sql`)
và đã có nơi khác dùng trước khi cụm này tồn tại:

- `gh.refinery.runner` gọi `raise_alert(...)` (định nghĩa ở `gh.providers.router` — đặt cạnh `ModelRouter` vì cả
  hai đều cần sinh cảnh báo hệ thống, không có nơi nào khác dùng chung trước GĐ 3) ngay khi luật `R-03 "Tín hiệu
  bất mãn"` khớp → `alert_type = repeated_complaint`.
- `ModelRouter` gọi khi hạn mức model dưới 20% (`model_quota_low`) hoặc hết chuỗi chuyển hướng
  (`model_chain_exhausted`).
- `gh.worker.verify_action_log` (kiểm chuỗi băm hằng đêm) gọi khi chuỗi Action Log đứt (`data_conflict`).

Cụm `queue` **không tạo lại bảng**, chỉ: (1) đọc/hành động trên các dòng đó qua Hộp thư, (2) thêm việc quét định
kỳ cho các loại cảnh báo còn thiếu của spec E9 (`gh/biz/queue/jobs.py`). `CHECK` sẵn có:
`personnel_related` chỉ được `true` khi `evidence` không rỗng (khoá cứng 7 — không chứng cứ thì không cảnh báo).

## Hàng đợi hợp nhất — `biz.inbox_items` (view)

`db/sql/0005_p3_queue.sql` tạo view `biz.inbox_items` hợp nhất ba nguồn, mỗi dòng có `item_type` ∈
`unit | alert | draft`, `item_id` (= id thật của dòng gốc — `clean.meaning_units.id` / `biz.alerts.id` /
`biz.action_drafts.id`, không sinh id riêng), `title`, `summary`, `subject_type/subject_id`, `group_id`,
`person_id`, `priority`, `created_at`, `score`:

| Nguồn | Điều kiện | `priority` |
|---|---|---|
| `clean.meaning_units` | `superseded_by IS NULL` (mọi đơn vị, kể cả đã có quyết định — Hộp thư không có trạng thái "đã đọc") | `P1` nếu `event_type = 'Complained'`, `P2` nếu `confidence ≥ 0,8`, còn lại `P3` |
| `biz.alerts` | `status = 'open'` | cột `priority` của cảnh báo |
| `biz.action_drafts` | `status = 'pending'` | `P1` nếu `flags.over_threshold`, còn lại `P2` |

**Việc (`biz.tasks`) không nằm trong view này** — có màn riêng; khối "Đến hạn" của Tổng quan lấy thẳng từ
`biz.tasks` (hình dạng khác hẳn: có `due_at`, không có `confidence`).

Tab của một dòng (cột tính toán `tab`, dùng cho `GET /inbox?tab=`):

```
alert  → item_type = 'alert'
approval → item_type = 'draft'
candidate → person.person_type = 'candidate'
opportunity → event_type ∈ {AskedPrice, OfferedSupply, RequestedPartnership}   -- tín hiệu thương mại mới
reply → event_type ∈ {Complained, ScheduledMeeting, SentQuotation, PromisedDelivery} hoặc bất kỳ đơn vị nào khác
        không rơi vào 3 nhóm trên (mặc định "cần soạn phản hồi", vì mọi đơn vị ý nghĩa mà Sếp chưa xử lý đều
        đáng được soạn một phản hồi hoặc xem qua)
```

> **Quyết định tự đưa ra** (spec chỉ liệt kê tên tab, không cho thuật toán): việc tách "cơ hội mới" khỏi "hỏi
> giá / than phiền / hẹn / báo giá" dựa theo văn bản F2 §2 liệt kê chúng thành hai cụm khác nhau
> ("cơ hội mới, hỏi giá, than phiền, hẹn, tài liệu cần soạn" — cơ hội đứng tách riêng). Một đơn vị chỉ thuộc
> đúng một tab (thứ tự ưu tiên ở trên) nhưng luôn xuất hiện ở tab "Tất cả".

## Im lặng có chủ đích

`biz.queue_silences (item_type, item_id)` — một dòng còn hiệu lực (`until IS NULL OR until > now()`) làm item biến
mất khỏi `GET /inbox` và khối "Hàng đợi" của Tổng quan cho tới khi hết hạn hoặc bị ghi đè. Không phải sổ chỉ-ghi:
im lặng lại một item đã im lặng thì ghi đè lý do/hạn (mỗi hành động vẫn vào Action Log).

## Giao việc trong hàng đợi

`POST /inbox/{id}/assign` giao **thẳng một item** (không cần sở hữu người/nhóm liên quan) bằng
`core.assignments(subject_type = 'queue', subject_id = <item_id>)` — đúng cách phạm vi `assigned` của Operator
("Tổng quan | Operator | Khối Hàng đợi và KPI thuộc hàng đợi được giao", PLAN §"Định nghĩa có giới hạn") đọc dữ
liệu: `gh.biz.queue.service.item_scope_sql` cho một item thấy được khi đối tượng nó gắn vào (người/nhóm) trong
phạm vi, **hoặc** chính item được giao qua `assignments`. `item_id` duy nhất toàn hệ thống (UUIDv7 từ 3 bảng gốc
khác nhau) nên không cần cột phân biệt loại trong `assignments`.

## Endpoint

### Hôm nay (Tổng quan)

`GET /overview` (`overview.read`) →

```json
{"kpis": [{"key": "opportunity_claim_rate", "label": "Tỉ lệ cơ hội được nhận", "value": 50.0, "unit": "%", "row": 1,
           "status": "ok", "sublabel": null, "pct": null,
           "filter": {"screen": "opportunity", "filters": {"owner": "none"}}}, "… 3 ô nữa"],
 "queue": [{"kind": "opportunity|alert|draft|due", "id", "code", "title", "priority", "at", "due_at"}],
 "spotlight": [{"person": PersonRef, "dimension": "heat|churn_risk", "value": 87.0, "at": "…"}],
 "signals": [{"topic": "thép cuộn", "count": 12, "delta_pct": 33.3}],
 "health": {"channels": [{"type": "zalo", "active": 1}], "backlog_pending": 0,
            "tech": {"channels_live": 2, "groups_listening": 5, "events_today": 340, "processing_latency_s": 1.2}},
 "dataQuality": {"missing_identity_pct": 4.2, "low_confidence_score_pct": 11.0, "unassigned_event_pct": 0.5},
 "hourly": [{"hour": "…", "count": 14}]}
```

**v0.1.42 (F-64):** `kpis` còn **đúng 4 ô kinh doanh**, tất cả `row=1`, theo thứ tự:

| key | label | unit | filter |
|---|---|---|---|
| `opportunity_claim_rate` | Tỉ lệ cơ hội được nhận | `%` | `opportunity`, `{owner: "none"}` |
| `time_to_contact` | Tín hiệu → tiếp cận (trung vị) | `phút` | `opportunity` |
| `quotations_sent` | Báo giá đã gửi (30 ngày) | — | `workbench`, `{kind: "quotation"}` |
| `pending_ratio` | Tỉ lệ chờ duyệt | `%` | `workbench`, `{status: "pending"}` |

Đã bỏ khỏi `kpis`: `chassis_latency` (trùng `processing_latency`), `plugins_health` (màn Plugin đóng băng, F-41),
`active_profiles`; `health.plugins` cũng không còn gửi (không truy vấn `ops.plugins` mỗi lần mở Hôm nay). Bốn số kỹ thuật (kênh sống, nhóm đang lắng nghe, sự kiện 24 giờ, độ trễ xử lý trung bình 20 lần
sàng lọc gần nhất — giây, `null` khi chưa có) chuyển sang `health.tech` để phần "Nâng cao" dùng. Mỗi ô vẫn có
`filter: {screen, filters}` để web điều hướng sang danh sách đã lọc tương ứng (định nghĩa "xong" #1 của `PLAN.md`).
Web chỉ render mảng `kpis` và coi `health.tech` là tuỳ chọn ⇒ chạy được với cả API cũ lẫn mới.

**Phạm vi theo vai trò**: theo PLAN §"Định nghĩa có giới hạn" ("Operator: khối Hàng đợi và KPI thuộc hàng đợi
được giao"), chỉ khối `queue` lọc theo phạm vi của `overview.read` (Operator = `assigned`); `kpis`, `spotlight`,
`signals`, `health`, `dataQuality`, `hourly` là số toàn tổ chức, không có gì nhạy cảm riêng cá nhân. Đây là quyết
định tự đưa ra vì spec không liệt kê chính xác widget nào bị giới hạn.

### Hộp thư

`GET /inbox?tab=all|opportunity|alert|approval|reply|candidate&intent=&cursor=&limit=` (`queue.read`) →

```json
{"items": [{"id", "code", "item_type": "unit|alert|draft", "tab": "opportunity", "title": "Hỏi giá",
            "summary": "…", "priority": "P2", "created_at": "…", "score": 0.91, "confidence_band": "cao",
            "subject": PersonRef | GroupRef | null, "group": GroupRef | null, "agent": AgentRef | null,
            "alert_type": "customer_cooling" | null, "alert_type_label": "Khách đang lạnh / sắp mất" | null,
            "suggested_action": "…" | null}],
 "next_cursor": null, "total": 12, "counts": {"all": 12, "opportunity": 5, "alert": 2, "approval": 3, "reply": 2,
                                              "candidate": 0}}
```

`intent` lọc theo `event_type` nguyên văn (chỉ áp cho dòng `item_type = 'unit'`, ví dụ `AskedPrice`) — khớp ô lọc
"theo ý định" của `handoff/01-ui-screens.md` §inbox. `counts` tính trên toàn bộ tập đã lọc theo phạm vi + im lặng
(không theo `tab`/`intent` đang chọn) để hiện đúng số trên mọi tab cùng lúc.

`GET /inbox/{id}` → item ở trên, cộng `units` (chuỗi chứng cứ, `explain.units_payload`) và, tuỳ `item_type`:
`status` (`alert`/`draft`), `kind` (`draft`).

- `POST /inbox/{id}/act` (`queue.act`) — hành động chính, khác nhau theo `item_type`:
  - `unit`: `{"text": "…"}` bắt buộc → soạn nhanh một bản nháp trả lời (`kind=message`, `sources` trỏ đúng đơn vị
    kích hoạt) qua `gh.biz.core.drafts.create_draft` — cùng luật cứng, cùng đường duyệt ở Bàn làm việc.
  - `alert`: `{"create_task": false}` → đánh dấu `acknowledged` (`409 ALERT_DECIDED` nếu hai người cùng bấm gần
    như đồng thời); `create_task` true thì tạo thêm một `biz.tasks` việc theo dõi (`source = 'alert'`). Cảnh báo
    đã xử lý rời khỏi `biz.inbox_items` (view chỉ giữ `status = 'open'`) nên gọi lại `act` sau đó trả `404` như
    mọi item đã ra khỏi hàng đợi, không phải `409`.
  - `draft`: luôn `409 USE_WORKBENCH` — quyết định một bản nháp (duyệt/sửa/huỷ) chỉ làm ở Bàn làm việc vì cần PIN
    và permit; Hộp thư chỉ để thấy nó đang chờ.
- `POST /inbox/{id}/assign` (`queue.act`) `{"user_id"}` → giao thẳng item (xem mục trên).
- `POST /inbox/{id}/silence` (`queue.act`) `{"reason"?, "until"?}` → im lặng có chủ đích (mục trên).

WebSocket: `alert.new` (quyền `queue.read`, không kèm dữ liệu — chỉ báo "có cảnh báo mới, tải lại hàng đợi", vì
cảnh báo có thể tới từ nhiều nơi khác nhau với hình dạng khác nhau; client gọi lại `GET /inbox`/`GET /overview`).

### Việc & Nhắc hẹn

Không có trong 21 màn thiết kế gốc — dựng theo `docs/handoff/01-ui-screens.md` §"Màn còn thiếu": bảng `biz.tasks`
(mã, tiêu đề, ưu tiên, trạng thái, phụ trách, hạn đỏ khi quá, nguồn), lịch tuần, lời hứa sắp đến hạn. Phạm vi: một
việc thấy được khi đối tượng nó gắn vào (`subject_type/subject_id`) trong phạm vi **hoặc** người phụ trách
(`assignee_user_id`) là mình / thành viên team mình.

- `GET /tasks?status=&priority=&overdue=&assignee_user_id=&cursor=&limit=` (`queue.read`) →
  `{"items": [{"id", "code": "TSK-0412", "title", "priority", "status", "assignee": UserRef | null,
     "subject": PersonRef | GroupRef | null, "due_at", "remind_at", "overdue": true, "source": "promise|draft|manual",
     "created_at", "completed_at"}], "next_cursor", "total"}` — `overdue` tính tại lúc trả lời
  (`due_at < now()` và chưa `done|cancelled`); web tô đỏ theo cờ này (định nghĩa "xong" trong `handoff/01`).
- `GET /tasks/{id}` (`queue.read`) → item trên. Chứng cứ: `GET /explain/task/{id}`.
- `POST /tasks` (`queue.act`) `{"title", "priority"?, "assignee_user_id"?, "subject"?: {"type","id"}, "due_at"?,
  "remind_at"?}` → `201`, `source = 'manual'`.
- `PATCH /tasks/{id}` (`queue.act`) `{"status"?, "priority"?, "assignee_user_id"?, "due_at"?, "remind_at"?}` →
  chuyển `status = 'done'` tự đặt `completed_at`.
- `GET /tasks/promises?status=upcoming|overdue|kept|all&cursor=&limit=` (`queue.read`) → lời hứa
  (`biz.promises`), `upcoming` = còn hạn trong 3 ngày, `overdue` = quá hạn chưa giữ. Phạm vi theo người hứa hoặc
  người được hứa.
  ```json
  {"items": [{"id", "text", "due_at", "kept_at", "broken": false, "from": PersonRef, "to": PersonRef | null,
              "evidence": {"type": "meaning_unit", "id": "…"} | null}], "next_cursor", "total"}
  ```
- `PATCH /tasks/promises/{id}` (`queue.act`) `{"kept": true}` → giữ lời hứa (`kept_at = now()`) hoặc đánh dấu vỡ.

Tạo việc từ nơi khác trong hệ thống (Bàn làm việc "Tạo kèm theo", agent trực kênh mức 5–6) đi qua
`gh.biz.core.drafts._execute_internal` (đã có từ trước, không đổi) — không qua `POST /tasks`, vì đó là hành động
nội bộ tự động, không phải người bấm tạo tay.

## Cảnh báo sớm (spec E9) — việc quét định kỳ

`gh/biz/queue/jobs.py: early_warning_scan` — cron mỗi 15 phút, theo tổ chức. Phủ 5/7 loại của E9 mà không gắn
liền một sự kiện sàng lọc cụ thể (2 loại còn lại — than phiền lặp lại, dữ liệu mâu thuẫn — đã sinh ở nơi khác,
xem mục "`biz.alerts` đã có sẵn"):

| Loại (`alert_type`) | Ngưỡng | Đối tượng | Chống trùng |
|---|---|---|---|
| `customer_cooling` | khách hàng có ≥ 2 đơn vị ý nghĩa, đơn vị mới nhất > 14 ngày trước | người | theo (tổ chức, loại, người) trong 7 ngày |
| `slow_response` | tin đến (inbound) > 60 phút chưa có tin đi (outbound) cùng luồng, trong 24 giờ qua | người gửi tin | theo chính bản ghi thô đã có trong `evidence` (không lặp dù quét lại) |
| `unclaimed_opportunity` | `biz.opportunities` chưa có `owner_user_id`, chưa đóng, tạo > 24 giờ trước | cơ hội | theo (tổ chức, loại, cơ hội) trong 7 ngày |
| `competitor` | đơn vị ý nghĩa `event_type = 'MentionsCompetitor'` trong 2 giờ qua | người hoặc nhóm | theo chính đơn vị đã có trong `evidence` |
| `forgotten_deadline` | `biz.promises` quá hạn (`due_at < now()`), chưa giữ (`kept_at IS NULL`), chưa đánh dấu vỡ | người hứa | đánh `broken = true` ngay khi sinh cảnh báo nên chỉ sinh một lần |

`personnel_related` chỉ `true` cho `slow_response` khi xác định được người phụ trách cụ thể (`owner_user_id`) —
không gắn cho một người cụ thể thì không được coi là tín hiệu nhân sự (khoá cứng "không tự quyết nhân sự", R7).

> **Quyết định tự đưa ra**: dùng cron quét ngưỡng thay vì hook `gh.clean.ready` (task mô tả cho phép cả hai).
> Lý do: `customer_cooling`/`slow_response`/`forgotten_deadline`/`unclaimed_opportunity` là điều kiện **kéo dài
> theo thời gian**, không gắn với một sự kiện sàng lọc cụ thể nên hook không giúp gì; `competitor` tuy gắn với
> một đơn vị mới nhưng gộp chung một job cho cách ly lỗi đơn giản hơn (một job lỗi không chặn 4 loại còn lại,
> tương đương cách ly của hook nhưng không cần thêm consumer group). Không đăng ký `HOOKS`.
