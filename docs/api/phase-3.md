# API giai đoạn 3 — Nền chung cho các màn kinh doanh

Quy ước giống `phase-1.md`, `phase-2.md`: tiền tố `/api/v1`, cookie + CSRF, lỗi RFC 7807, 🔒 = cần phiên PIN (423), thời gian ISO 8601 UTC, số trả **số thô** (web tự định dạng). Danh sách lớn phân trang con trỏ `{"items", "next_cursor", "total"}`. Mọi đối tượng trả cả `id` (uuid) và `code`.

Mỗi cụm màn có tệp hợp đồng riêng, cùng quy ước với tệp này:

| Cụm | Tệp | Màn |
|---|---|---|
| Nền chung | tệp này | chứng cứ, phạm vi dữ liệu, góc nhìn đã lưu, Bàn làm việc (bản nháp), agent trực kênh |
| Hàng đợi & Hành động | `phase-3-queue.md` | Tổng quan, Hộp thư ý nghĩa, Bàn làm việc (giao diện), Việc & Nhắc hẹn, cảnh báo sớm |
| Quan hệ & Đối tượng | `phase-3-relations.md` | Nhóm & Con người, Hồ sơ sống, Sổ tay nhận thức, Tài liệu |
| Bản đồ quan hệ | `phase-3-graph.md` | Bản đồ quan hệ (4 chế độ) |
| Cơ hội & Thị trường | `phase-3-market.md` | Bảng cơ hội, Cung ↔ Cầu, Kho hội thoại, Deal & Vụ việc |
| Con người & Chất lượng | `phase-3-people.md` | Đánh giá con người + Phản biện, Chất lượng chăm sóc, trình thiết lập bước 8–9 |

## Hình dạng dùng chung

```json
PersonRef  {"id": "uuid", "code": "PER-0042", "name": "Nguyễn Văn Bảo", "type": "customer|partner|staff|candidate|learner|supplier|unknown", "org_name": "Công ty in Thành Phát" | null}
GroupRef   {"id": "uuid", "code": "GRP-ZL-0114", "name": "…", "channel": "zalo|whatsapp|telegram|linkedin"}
UserRef    {"id": "uuid", "name": "Trần Minh", "role": "owner|manager|operator|agent_staff|auditor"}
AgentRef   {"id": "uuid", "name": "Trợ lý thương mại"}
Score      {"value": 87, "trend": "up|down|flat" | null, "confidence": 0.91 | null}
EvidenceRef {"type": "meaning_unit|raw|score|alert|opportunity|draft|review|task", "id": "uuid", "code": "…" | null, "label": "…" | null}
```
`confidence` của đơn vị/điểm là số 0–1; web đổi sang chip **cao** (≥ 0,8), **trung bình** (≥ 0,6), **thấp** (< 0,6).
Tên và nội dung trả cho vai trò dưới Owner đi qua bộ che dữ liệu nhạy cảm (khoá cứng 8): dãy ≥ 8 chữ số chỉ giữ 3 số cuối.

## Phạm vi dữ liệu (ScopeFilter)

Mọi truy vấn màn kinh doanh lọc theo phạm vi của người gọi cho quyền tương ứng (`gh/biz/core/scope.py`):

| Phạm vi | Người (`core.persons`) thấy được | Nhóm |
|---|---|---|
| `all` | mọi người | mọi nhóm |
| `team` | người có `owner_user_id` là thành viên team của mình, hoặc được phân cho thành viên team (`core.assignments`) | nhóm được phân cho thành viên team |
| `assigned` | người có `owner_user_id` = mình, hoặc được phân cho mình | nhóm được phân cho mình |

Cơ hội, bản nháp, cảnh báo, việc, tài liệu đi theo người/nhóm mà chúng gắn vào (hoặc người phụ trách = mình). Đối tượng ngoài phạm vi trả **404**, không phải 403. Operator có `queue.read = all` nên thấy toàn bộ hàng đợi nhưng chỉ hành động (`action.draft = assigned`) trên phần được phân.

## Chứng cứ — "Vì sao hệ thống nghĩ vậy" / "Xem chứng cứ gốc"

`GET /explain/{kind}/{id}` với `kind` ∈ `meaning_unit | score | alert | opportunity | draft | task | review`. Với `score`, `id` là `"{subject_type}:{subject_id}:{dimension}"` (vd `person:uuid:heat`). Quyền: quyền đọc của màn chứa đối tượng + phạm vi; `review` cần `people_review.read` 🔒 và ghi Action Log mỗi lần xem.

```json
{"kind": "score", "id": "person:…:heat", "title": "Nguyễn Văn Bảo — độ nóng",
 "statement": "87/100 · tin cậy 0,91", "method": "rules+model|manual|rule|model",
 "factors": [{"label": "Complained: Khách hỏi lại ba lần chưa ai trả lời", "value": 87.0,
              "evidence": [{"type": "meaning_unit", "id": "uuid"}]}],
 "units": [{"id": "uuid", "event_type": "Complained", "conclusion": "…", "confidence": 0.93,
            "observed_at": "…", "group": GroupRef | null, "person": PersonRef | null,
            "quotes": [{"raw_id": "uuid", "raw_code": "RAW-918422", "quote": "…", "occurred_at": "…",
                        "channel": "zalo", "sender": PersonRef | null}]}],
 "history": [{"value": 72.0, "computed_at": "…", "method": "rules+model", "by": UserRef | null}]}
```
- `units` là chuỗi điểm → đơn vị ý nghĩa → trích dẫn → bản ghi thô (tối đa 20 đơn vị, mới nhất trước). Mỗi trích dẫn mở được nguyên văn bằng `GET /explain/raw/{raw_id}` (cùng hình dạng item của `GET /raw/{id}` giai đoạn 2; gọi được bằng quyền đọc màn kinh doanh, không cần `data.read`, chỉ cho bản ghi nằm trong chuỗi chứng cứ của một đơn vị trong phạm vi, ngoài phạm vi → 404; người không phải Owner thấy nội dung đã che số điện thoại/tài khoản). `GET /raw/{id}` vẫn chỉ dành cho màn kỹ thuật.
- `history` chỉ có với `score` (tối đa 30 snapshot, mới nhất trước).
- Không có chứng cứ → `units: []` và `statement` ghi rõ "Chưa có chứng cứ"; web không hiện điểm như một kết luận.

## Góc nhìn đã lưu (header, mọi màn)

- `GET /views?screen=inbox` → `[{"id", "screen", "name", "filters": {…query của màn…}, "created_at"}]` (của chính người gọi; không truyền `screen` → mọi màn).
- `POST /views` `{"screen", "name" (1–80 ký tự), "filters": {…}}` → góc nhìn. Trùng tên trên cùng màn → `409 VIEW_EXISTS`.
- `DELETE /views/{id}` → 204. Chỉ xoá được góc nhìn của mình (khác → 404).
`filters` là chính các tham số URL của màn (bộ lọc, tab, mục mở rộng) nên mở góc nhìn = điều hướng tới `/{screen}?{filters}`.

## Bàn làm việc — bản nháp chờ duyệt

Bản nháp do agent trực kênh soạn, hoặc do người tạo (Giới thiệu hai bên, hành động hàng loạt, "Soạn trả lời" ở Hộp thư). Mọi thứ ghi ra ngoài, vượt ngưỡng tiền, liên quan nhân sự dừng ở đây ở **mọi** mức tự trị (khoá cứng 3).

Loại (`kind`): `message` "Tin nhắn" · `quotation` "Báo giá" · `contract` "Hợp đồng" · `reminder` "Nhắc việc" · `report` "Báo cáo" · `mcp_write` "Ghi hệ thống ngoài". Trạng thái: `pending` (chờ duyệt) · `approved` (đã duyệt, đang gửi) · `edited` (đã sửa, đang gửi) · `sent` (đã gửi / đã thực hiện) · `failed` · `rejected`.

- `GET /drafts?status=pending|decided|failed|all&kind=&cursor&limit=50` (`action.draft` hoặc `action.approve`, theo phạm vi; `failed` = gửi lỗi CHƯA có bản gửi lại, `total` là số đó) →
  ```json
  {"items": [{"id", "code": "ACT-0231", "kind": "quotation", "kind_label": "Báo giá", "title": "…",
     "agent": AgentRef | null, "created_by": UserRef | null, "created_at": "…", "status": "pending",
     "hold_reason": "vượt ngưỡng 50.000.000 ₫" | null, "subject": PersonRef | GroupRef | null,
     "resent_by": {"id", "code"} | null}],   // bản gửi lỗi đã được gửi lại bằng bản mới này
   "next_cursor": null, "total": 6}
  ```
- `GET /drafts/{id}` → item trên, cộng:
  ```json
  {"paragraphs": ["…"], "text": "…toàn văn…", "lang": "vi",
   "target": {"channel": "zalo", "thread_type": "group|user", "group": GroupRef | null, "person": PersonRef | null,
              "identity_id": "…" | null, "account": {"label": "…"} | null} | null,   // tin 1-1: đúng tài khoản nhận
   "amount_vnd": 84000000 | null, "autonomy_level": 4,
   "flags": {"writes_external": true, "personnel_related": false, "over_threshold": true},
   "approve_label": "Duyệt và gửi qua Zalo",
   "sources": [{"label": "Hồ sơ Thành Phát · 214 sự kiện", "ref": EvidenceRef | null}],
   "context": [{"key": "độ nóng", "value": "87 — ba tin chưa trả lời", "ref": EvidenceRef | null}],
   "side_actions": [{"key": "task.follow_up", "label": "Tạo việc theo dõi hạn giao 03/10", "on": true}],
   "decision": {"by": UserRef, "at": "…", "reason": "…" | null} | null,
   "send_result": {"ok": true, "error": null, "at": "…"} | null,
   "versions": [{"at": "…", "by": "agent|user", "text": "…"}]}
  ```
  `sources` là **đúng** dữ liệu agent đã dùng (từ `agent.decisions.context_refs`), không có mục nào không truy được. `context` dựng từ hồ sơ + điểm hiện tại của đối tượng (độ nóng, rủi ro churn, giai đoạn cơ hội, phong cách, ghi chú Sếp).
- `POST /drafts/{id}/approve` (`action.approve`) 🔒 `draft.decide` `{"side_actions": {"task.follow_up": true, …}}` → bản nháp (trạng thái `approved`, hoặc `sent` với loại nội bộ). Loại `message|quotation|contract` có `target`: cấp **permit dùng một lần** (hạn 5 phút, gắn `draft_id` + `sha256(text)` + kênh/luồng) rồi đẩy `message.send` sang bridge; kết quả `send.result` đổi trạng thái thành `sent` hoặc `failed` và phát WS. Bridge offline → vẫn `approved`, lệnh chờ trong stream. Công tắc "Tạo kèm theo" được thực hiện sau khi duyệt (tạo việc, đặt nhắc, ghi chú sổ tay); mục ghi ra ngoài (CRM/ERP) tạo bản nháp con chờ duyệt riêng.
- `POST /drafts/{id}/edit-send` (`action.approve`) 🔒 `{"text": "…", "side_actions": {…}}` → như duyệt nhưng gửi nội dung đã sửa (trạng thái `edited` → `sent|failed`), giữ bản cũ trong `versions`.
- `POST /drafts/{id}/reject` (`action.approve`) 🔒 `{"reason": "…" | null}` → `rejected`.
- `POST /drafts/{id}/translate` (`action.draft`) `{"lang": "en|vi|zh|ja|ko"}` → `{"lang", "text"}` (không đổi bản nháp; model vai trò trả lời nhanh). Model không chạy được → `503 MODEL_UNAVAILABLE`.
- `POST /drafts/{id}/regenerate` (`action.draft`) `{"instruction": "…" | null}` → bản nháp với nội dung mới (bản cũ vào `versions`), vẫn `pending`.
- Quyết định lại một bản nháp đã quyết → `409 DRAFT_DECIDED`. Mọi thao tác vào Action Log (`draft.approved|edited|rejected|sent|failed`, kèm mức tự trị).
- WebSocket: `draft.new` (item danh sách) và `draft.updated` `{"id", "status", "send_result"}`.

Tạo bản nháp từ mã khác: `gh.biz.core.drafts.create_draft(...)` (xem docstring). Người tạo bản nháp bằng tay dùng `POST /drafts` (`action.draft`) `{"kind", "title", "text", "target": {"channel", "thread_type", "group_id"|"person_id", "identity_id"?}, "amount_vnd"?, "subject": {"type": "person|group", "id"}?, "sources"?: [...], "resend_of"?: "<id bản gửi lỗi>"}` → `201` bản nháp `pending`. `resend_of` ("Gửi lại") nối bản mới về bản lỗi cũ (`parent_draft_id`): bản cũ ra khỏi `status=failed`; bản lỗi đã gửi lại hoặc không phải `failed`/khác loại → `409 ALREADY_RESENT` / `NOT_RESENDABLE`; huỷ bản mới thì bản cũ về lại.

## Agent trực kênh

Khi sàng lọc ghi xong đơn vị ý nghĩa (`gh.clean.ready`), worker chạy các **hook sau sàng lọc** đã đăng ký (`HOOKS` trong `gh/biz/<cụm>/jobs.py`, xem `gh/biz/hooks.py`), mỗi hook một consumer group riêng nên lỗi được cách ly. Agent trực kênh là một hook: với mỗi đơn vị trong phạm vi của một agent đang bật (`agent.channel_scopes`), dựng ngữ cảnh (ID nhóm, ID người, dữ liệu sạch liên quan, lịch sử tương quan, sổ tay), hỏi model quyết định `silent | note | suggest | draft | send`, qua policy, rồi ghi `agent.decisions` (kèm ID ngữ cảnh đã dùng) và Action Log. `send` luôn thành bản nháp chờ duyệt (quyết định Q2).

- `GET /agents/decisions?agent_id=&decision=&cursor&limit=50` (`system.read` hoặc `action.approve`) →
  `{"items": [{"id", "at", "agent": AgentRef, "decision": "draft", "rationale": "…", "trigger": EvidenceRef, "context_refs": [EvidenceRef], "outcome": "held|suggested|noted|blocked|rejected|none", "proposal_text": "…" | null, "draft": {"id", "code", "status"} | null}], …}` — nguồn cho "Agent đã nói gì, nhân danh gì" (giai đoạn 4) và khối nguồn của Bàn làm việc. v0.1.67: `outcome` + `draft.status` cho web ghi đúng "Soạn sẵn chờ duyệt" / "Đã gửi" (quyết định `send` chỉ là nháp chờ duyệt); `proposal_text` = câu agent gợi ý/soạn (số nhạy cảm bị che với vai dưới Owner).
