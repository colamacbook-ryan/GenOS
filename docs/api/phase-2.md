# API giai đoạn 2 — Tầng dữ liệu, kênh, bộ não AI

Quy ước giống `phase-1.md` (tiền tố `/api/v1`, cookie + CSRF, lỗi RFC 7807, 🔒 = cần phiên PIN → 423). Thời gian ISO 8601 UTC; web hiển thị theo `org.timezone`. Số liệu trả **số thô** (web tự định dạng `18.412`, `0,94`, `15 phút`). Danh sách lớn phân trang con trỏ `?cursor=&limit=` → `{"items": [...], "next_cursor": "…"|null, "total": n}`.

Mã công khai: nhóm `GRP-ZL-0114` (ZL = Zalo, WA = WhatsApp, TG, LI), người `PER-0042`, bản ghi thô `RAW-918422`, quy tắc `R-01`. Mọi đối tượng trả cả `id` (uuid) và `code`.

Quyền: màn dữ liệu cần `data.read`; ghi (quy tắc, lịch, chạy ngay, gộp/tách) cần `data.manage`. Kênh, CLI, provider cần `system.read` / `system.manage`.

## Dải pipeline (dùng chung Kho thô · Quy tắc · Kho sạch)

`GET /data/pipeline` →
```json
{"channels_live": 2, "groups_listening": 42, "raw_total": 18412, "raw_pending": 4204,
 "raw_error": 0, "interval_seconds": 900, "count_threshold": 500, "clean_total": 14208}
```
`raw_error` (v0.1.64): số tin ở trạng thái "Lỗi xử lý" (model trả sai định dạng đủ 3 lần). Bộ lập lịch tự đưa tin
`error` về `pending` sau 30 phút, tối đa 3 lần; hết lần vẫn nằm ở `error` và hiện ở dải pipeline.
Quy tắc sàng lọc có kết quả `set person_type` (R-05 …) ghi ngược vào `core.persons.person_type` — chỉ nâng từ
`unknown` và không đè loại Sếp đã tự đặt ở hồ sơ.

## Kho dữ liệu thô (`raw`)

- `GET /raw?cursor&limit=50&channel=zalo|whatsapp&group_id=&state=&since=24h|7d|30d|all&label=&min_confidence=` →
  ```json
  {"items": [{"id": "uuid", "code": "RAW-918422", "received_at": "…", "occurred_at": "…",
     "channel": {"type": "zalo", "name": "Zalo"},
     "group": {"id": "uuid", "code": "GRP-ZL-0114", "name": "Vận hành Genesis — Quý 4"} | null,
     "person": {"id": "uuid", "code": "PER-0042", "name": "Nguyễn Văn Bảo"} | null,
     "direction": "inbound" | "outbound", "kind": "text", "text": "…",
     "label": "Complained" | null, "confidence": 0.94 | null,
     "state": "pending" | "processing" | "clean" | "lowconf" | "discarded" | "error"}],
   "next_cursor": null, "total": 18412}
  ```
  Nhãn trạng thái (web): pending "Chờ chu kỳ tới", processing "Đang phân loại", clean "Đã vào kho sạch", lowconf "Tin cậy thấp", discarded "Loại — nhiễu", error "Lỗi xử lý".
- `GET /raw/{id}` → dòng trên + `"payload": {…nguyên văn từ bridge}`, `"meaning_units": [{"id","event_type","conclusion"}]`.
- `GET /raw/by-group?since=24h&limit=7` → `[{"group": {"id","code","name"}, "n": 812}]` (sắp giảm dần).
- `GET /raw/export?…cùng bộ lọc…` (`data.manage`) 🔒 PIN `data.export` → `text/csv; charset=utf-8` (có BOM để Excel đọc đúng tiếng Việt).
- WebSocket: sự kiện `raw.new` (cùng hình dạng một item) và `raw.state` `{"id", "state", "label", "confidence"}`.

## Nhật ký lắng nghe (v0.1.68) — `/listen-log`

Màn «Nhật ký lắng nghe» (Nâng cao › Tầng dữ liệu): MỘT dòng thời gian gộp tin nghe được (Kho thô) + sự kiện trên mọi kênh, kèm «hệ thống đã làm gì». Chỉ đọc. Quyền vào màn = `data.read` (thiếu quyền ⇒ `403 FORBIDDEN`, web hiện thẻ «Việc này cần quyền Xem tầng dữ liệu» + «Chi tiết kỹ thuật»).

- `GET /listen-log?cursor=&limit=50&channel=&group_id=&since=7d&kind=all|message|event&tagged=false&acted=false&q=` →
  ```json
  {"items": [
     {"kind": "message", "id": "…", "at": "2026-10-17T08:11:02Z", "code": "RAW-918431",
      "channel": {"type": "zalo", "name": "Zalo"}, "group": {"id","code","name"} | null, "direct": false,
      "sender": {"id","code","name"} | null, "direction": "inbound", "msg_kind": "text", "text": "…(≤ 400 ký tự, đã che)",
      "tagged": true, "listen_mode": "tagged_only", "listen_mode_label": "Nghe hết, chỉ trả lời khi được tag", "listen_mode_known": true,
      "screening": {"state": "clean", "label": "AskedPrice", "confidence": 0.87, "note": "Đã vào kho sạch"},
      "decisions": [{"id","at","agent": {"id","name"}, "decision": "draft", "outcome": "held", "label": "Nháp trả lời: chờ duyệt",
                     "draft": {"id","code","status"} | null}],
      "note": "Vì sao hệ thống không làm gì thêm" | null},
     {"kind": "event", "id": "…", "at": "…", "event_type": "group_new", "icon": "ph ph-users-three", "tone": "accent",
      "text": "Tài khoản Zalo của Sếp vừa được thêm vào nhóm «…». Nhóm tự vào chế độ: Lắng nghe im lặng.",
      "channel": {…} | null, "group": {…} | null, "person": {…} | null, "direct": false, "listen_mode": "silent" | null, "raw_event_id": null}],
   "next_cursor": "1789632000000000_01a1…" | null, "decisions_visible": true}
  ```
  Mới nhất trên cùng, gộp hai nguồn theo `(thời điểm, id)`. `next_cursor` là chuỗi mờ — đưa nguyên vào `cursor` để lấy trang kế (không bỏ sót/trùng dòng); `null` = hết. Tham số lạ ⇒ `422`.
  - Bộ lọc phía máy chủ: `channel`, `group_id`, `since` (`24h|7d|30d|all`), `kind` (Chỉ tin / Chỉ sự kiện), `tagged` (chỉ tin có tag agent), `acted` (chỉ tin agent đã ghi chú/gợi ý/soạn nháp — không tính im lặng), `q` (tìm chữ trong nội dung, tên người gửi, tên nhóm / câu sự kiện). `tagged`/`acted` tự loại sự kiện.
  - `listen_mode`: chế độ nghe LÚC tin vào kho (cột `raw.events.listen_mode`; tin 1-1: `silent|reply`). `listen_mode_known: false` = tin cũ trước v0.1.68, đang hiện chế độ hiện tại của nhóm (web ghi «(hiện tại)»).
  - Sự kiện (`event_type`): `group_new` (tài khoản được thêm vào nhóm — kèm chế độ nghe nhóm tự vào), `group_left`, `group_rejoined`, `group_renamed`, `members_changed` (thành viên vào/rời của nhóm ĐANG nghe, kèm ≤ 5 tên), `groups_synced` (lần quét danh bạ đầu tiên của kênh — chỉ một dòng tóm tắt), `channel_connected`, `channel_error`, `channel_ended` (đăng nhập/nối lại/mất kết nối/đăng xuất/hết hạn), `direct_new` (người mới nhắn riêng lần đầu). Lưu ở `ops.listen_events` (không chứa nội dung tin); câu tiếng Việt do máy chủ viết lúc đọc, đã che theo quyền.
- `GET /listen-log/stats` → `{"by_channel": [{"type","name","messages_24h","events_24h","groups_listening"}], "messages_24h", "events_24h", "groups_listening", "groups": [{"id","code","name","channel","listen_mode"}]}` — số 24 giờ theo kênh (cùng phạm vi/quy tắc 1-1 với danh sách) và các nhóm đang nghe cho ô lọc.
- Quyền và che (một nguồn với Kho thô/Hộp thư, không đường lách): tin nhóm theo phạm vi `data.read` của vai (`Scope.group_id_sql`); tin 1-1 và sự kiện `direct_new` chỉ Owner hoặc người phụ trách đúng người đó (`scope.direct_visible_sql`, v0.1.65); sự kiện của cả kênh chỉ phạm vi `all`; vai dưới Owner bị che số dài trong `text`/tên, và tìm chữ chạy trên bản đã bỏ số dài (không dò được số bị che). `decisions` chỉ có khi vai có `system.read`/`action.approve` (`decisions_visible`); `draft` (link `/workbench?id=<draft.id>`) chỉ khi bản nháp nằm trong phạm vi người xem.
- Nhóm «Không nghe» / «Tạm dừng» / «Chờ Sếp chọn» KHÔNG lưu tin ⇒ không có dòng tin nào của chúng (chỉ có sự kiện nhóm); màn ghi chú rõ ở đầu.
- WebSocket `listen.event` (`data.read`): `{id, at, event_type, channel_type, group_id, owner_only}` — chỉ là TÍN HIỆU (không câu chữ, không tên); sự kiện 1-1 có `owner_only: true` ⇒ chỉ Owner nhận. Console gom `raw.new`, `raw.state`, `listen.event`, `agent.decision`, `draft.*` thành một lần tải lại `/listen-log` (0,7–3 giây), nên phạm vi và việc che luôn do máy chủ quyết.

## Sàng lọc (`refinery`)

- `GET /refinery/schedule` →
  ```json
  {"interval_seconds": 900, "count_threshold": 500, "batch_size": 250, "min_confidence": 0.6,
   "pending": 4204, "next_run_at": "…", "next_trigger": "interval" | "count"}
  ```
- `PUT /refinery/schedule` (`data.manage`) cùng 4 trường cấu hình → như trên. Ràng buộc: interval 60–86400, threshold 1–100000, batch 1–2000, min_confidence 0–1.
- `POST /refinery/run` (`data.manage`) → `202 {"run_id": "uuid"}`; `409 REFINERY_BUSY` nếu đang có lượt thủ công chạy.
- `GET /refinery/runs?limit=5` → `[{"id","trigger":"schedule|threshold|manual|fast","started_at","finished_at","input_count","clean_count","lowconf_count","noise_count","error_count","status":"queued|running|done|failed","error"}]`. `POST /refinery/run` tạo lượt `queued` (worker nhận ngay); đang có lượt thủ công chưa xong → `409 REFINERY_BUSY`.
- WebSocket `refinery.progress` `{"run_id","processed","total","clean","lowconf","noise","errors","status"}`.

## Quy tắc sàng lọc (`rules`)

Một quy tắc gồm điều kiện chạy được (tất định) và nhãn hiển thị do người viết.
```json
{"id": "uuid", "code": "R-01", "name": "Nhận diện nhu cầu mua", "kind": "intent|risk|competition|hr|hygiene|custom",
 "kind_label": "Ý định", "enabled": true, "version": 3, "threshold": 0.7, "hits_24h": 1842,
 "conditions": [{"type": "keyword_any", "values": ["cần", "mua", "báo giá"], "label": "có từ khoá số lượng + đơn vị"}],
 "outputs": [{"set": "intent", "value": "AskedPrice", "label": "intent = AskedPrice"}],
 "prompt_hint": "…" | null, "updated_at": "…"}
```
Loại điều kiện: `keyword_any {values}`, `keyword_all {values}`, `regex {pattern}`, `has_entity {entity: qty|price|budget|phone|product|date}`, `min_words {n}`, `max_words {n}`, `is_question`, `kind_in {values: [sticker,image,…]}`, `repeat_unanswered {n}`, `llm {hint}` (để LLM xét). Kết quả: `set {field: intent|side|label|person_type, value}`, `add {field: heat|potential|churn_risk|fit, value}`, `discard`, `alert {priority}`.

- `GET /rules` → mảng như trên. `GET /rules/{id}/versions` → `[{"version","conditions","outputs","threshold","created_at","created_by"}]`.
- `POST /rules` (`data.manage`) `{name, kind, conditions, outputs, threshold, prompt_hint}` → quy tắc (mã `R-nn` tự sinh).
- `PUT /rules/{id}` (`data.manage`) cùng trường → **phiên bản mới**, kết luận cũ giữ phiên bản cũ.
- `PATCH /rules/{id}` `{enabled}` (`data.manage`).
- `GET /rules/weights` → `[{"dimension":"heat","label":"Độ nóng của tín hiệu","value":30}]` (phần trăm nguyên, tổng 100).
- `PUT /rules/weights` `[{"dimension","value"}]` → như trên; tổng ≠ 100 → `422 {"errors": {"_": "Tổng trọng số phải bằng 100%"}}`.
- `POST /rules/test` `{"raw_event_id": "uuid"}` hoặc `{"text": "…", "group_id"?, "person_id"?}` → **không ghi gì**:
  ```json
  {"input": {"code": "RAW-918422", "text": "…"},
   "output": [{"key": "intent", "value": "AskedPrice · side = CẦU"}, {"key": "confidence", "value": "0,96 — trên ngưỡng 0,60, được ghi vào kho sạch"}],
   "matched_rules": ["R-01"], "discarded_by": null, "confidence": 0.96, "would_write": "clean|lowconf|discarded"}
  ```
- `POST /rules/test-batch` `{"n": 100}` → `{"n": 100, "clean": 61, "lowconf": 9, "discarded": 30, "by_rule": [{"code": "R-01", "hits": 22}]}` (không ghi).

## Kho sạch & trí nhớ (`clean`)

- `GET /clean?cursor&limit&group_id&person_id&since=24h|7d|30d|all` →
  `{"items": [{"id","observed_at","group": {…}|null,"person": {…}|null,"event_type":"AskedPrice","conclusion":"…","score": 91,"confidence":0.96,"cycle_at":"…","raw_event_ids":["uuid"]}], "next_cursor", "total"}`. `score` = điểm tổng theo trọng số (0–100) của đơn vị đó.
- `GET /clean/{id}/evidence` → `[{"raw": {…một item kho thô…}, "quote": "…"}]`.
- `GET /clean/agent-params?group_id&person_id` → `[{"key","label","value","icon"}]` theo `agentParams` của thiết kế (ID nhóm đang trực, ID người đang nói, bản ghi sạch được đọc, mức tự trị, trí nhớ tạm, lịch sử tương quan).

### Sổ tay nhận thức (lõi — màn Sổ tay đầy đủ ở GĐ 3)

- `GET /notebooks/{person|group}/{subject_id}` →
  ```json
  {"id","subject": {"type","id","code","name"}, "token_used": 1842, "token_budget": 4000, "compaction_no": 14,
   "last_compacted_at": "…", "sections": [{"key": "attention_now", "title": "Điều cần chú ý ngay", "updated_at": "…",
     "entries": [{"id","body","refs":[{"type","id","code"}],"author":{"type":"agent|user","label"},"pinned":false,"created_at"}]}]}
  ```
  Mục: `attention_now` "Điều cần chú ý ngay" · `rolling_context` "Ngữ cảnh ngắn lũy tiến" · `guardrails` "Giới hạn cho agent" · `preferences` "Sở thích" · `open_threads` "Việc dở".
- `POST /notebooks/{type}/{id}/entries` (`profile.write`) `{section, body, pinned}` → entry. `PATCH …/entries/{eid}` `{body?, pinned?}` (sửa giữ bản cũ). `DELETE …/entries/{eid}` (lưu trữ, không xoá cứng).
- `POST /notebooks/{type}/{id}/compact` (`profile.write`) → notebook. `GET …/compactions` → `[{"compaction_no","at","tokens_before","tokens_after","archived":n,"summary"}]`.

## Hợp nhất danh tính (`identity`)

- `GET /identity/stats` → `{"merged_people": 136, "live_profiles": 148, "pending_pairs": 12, "manual_splits": 4, "unlinked_accounts": 31}`.
- `GET /identity/candidates?status=pending` →
  `[{"id","confidence":0.96,"level":"high|mid|low","basis":"trùng số điện thoại và tên công ty","basis_detail":{…},
     "a": {"identity_id","person":{"id","code","name"},"channel":"zalo","meta":"Zalo · +84 903 xxx 118"}, "b": {…}}]`.
- `POST /identity/candidates/{id}/merge` 🔒 `identity.merge` → `{"person": {…hồ sơ giữ lại…}, "log_id"}`. `POST /identity/candidates/{id}/reject` (`data.manage`).
- `GET /identity/candidates/{id}/evidence` → `[{"raw": {…}, "note"}]`.
- `POST /identity/split` 🔒 `identity.merge` `{"person_id","identity_ids":[…]}` → người mới. `POST /identity/history/{log_id}/revert` 🔒.
- `GET /identity/history` → `[{"id","op":"merge|split","at","actor","from":{…},"to":{…},"identities":n,"reverted":false}]`.

## Kênh & đăng nhập (Điều khiển hệ thống › tab 1)

- `GET /channels` →
  ```json
  [{"type": "zalo", "name": "Zalo", "installed": true, "id": "uuid", "state": "active|pending_qr|expired|logged_out|error|not_installed|identity_only",
    "account_label": "iPhone của Sếp", "started_at": "…", "groups_listening": 38, "outbound_queued": 0, "last_heartbeat_at": "…",
    "stats": {"msgs_24h": 1244, "tagged_24h": 31, "latency_ms": 420, "uptime_pct": 99.9},
    "qr": {"session_id", "image": "data:image/png;base64,…", "expires_at": "…", "scanned": false} | null}]
  ```
  Luôn trả đủ 4 thẻ theo thiết kế: Zalo, WhatsApp, Telegram (`not_installed` nếu chưa có plugin kênh), LinkedIn (`identity_only`).
- `POST /channels/{type}/login` 🔒 `channel.login` `{"account_label"?, "accept_risk": true}` → `202 {"session_id"}`; QR tới qua WS `channel.qr` `{"type","session_id","image","expires_at"}`, trạng thái qua `channel.status` `{"type","state","account_label","scanned"}`. Thiếu `accept_risk` → `422` (phải hiện cảnh báo rủi ro tài khoản cá nhân trước).
- `POST /channels/{type}/logout` 🔒 `channel.logout` → 204. Zalo không có đăng xuất phía máy chủ: hệ thống xoá phiên đã lưu; Owner nên gỡ thiết bị trong ứng dụng Zalo.
- `PATCH /channels/{type}` 🔒 `policy.change` `{"direct_mode"?: "off|silent|reply", "new_group_mode"?: "ask|silent|off"}` (v0.1.65; hoặc `{"listen_direct": true|false}` của bản cũ: true ⇒ `reply`, false ⇒ `off`) → thẻ kênh. Tin riêng 1-1: `silent` (MẶC ĐỊNH — lưu vào Kho thô, không tự trả lời, không đưa cho agent trực) · `reply` (agent trực được trả lời) · `off` (không lưu). Thẻ kênh có thêm `direct_mode`, `listen_direct` (= khác `off`) và `groups_pending`. **Nhóm mới sau này** (`new_group_mode`, F1d; `settings.new_group_mode.<kênh>`; Sếp chốt 17/10/2026 «khi được add vào một group thì MẶC ĐỊNH lắng nghe trong im lặng, KHÔNG trả lời cho dù được tag»): `silent` (MẶC ĐỊNH, kể cả mọi bản cài cũ chưa có khoá — nhóm mới vào «Lắng nghe im lặng» ngay: lưu hết tin, không bao giờ trả lời kể cả khi được tag) · `ask` (nhóm mới vào `pending` + hộp hỏi «Nhóm mới», không lưu tin cho tới khi Sếp chọn) · `off` (vào Không nghe: không lưu tin); cả ba vẫn thu danh sách thành viên. Chỉ áp cho nhóm tạo SAU khi đặt (ingest `upsert_group` đọc thiết lập; đổi thiết lập KHÔNG đổi nhóm đã có — nhóm cũ đang `off` giữ nguyên `off`, không tự bật nghe). Cần PIN như đổi tin 1-1; Nhật ký hành động `channel.new_group_mode_changed {new_group_mode, from}`. Thẻ kênh có thêm `new_group_mode`, `new_group_asked` (Sếp đã chọn ở hộp hỏi/thẻ kênh kể từ lần nối kênh gần nhất; `POST /channels/{type}/login` đặt lại `false`; chỉ còn để tương thích — ô «Áp dụng cho các nhóm mới sau này» KHÔNG còn bật sẵn) và `new_groups_silent` (số nhóm mới vừa tự vào Lắng nghe im lặng mà Sếp chưa bấm «Đã biết»; thẻ kênh hiện «N nhóm mới đã vào Lắng nghe im lặng»). Quét QR một tài khoản KHÁC tài khoản trước ⇒ `new_group_mode` về mặc định `silent` (+ `new_groups_silent = 0`; Nhật ký `channel.new_group_mode_reset` khi giá trị cũ khác mặc định). Body không có khoá nào trong ba khoá ⇒ `422`.
- `POST /channels/{type}/groups/seen` (`system.manage`, không PIN; QD 17/10) → thẻ kênh: «Đã biết» — đặt `new_groups_silent` về 0, không đổi chế độ nhóm nào. `404` với kênh không phải Zalo/WhatsApp.
- `POST /channels/{type}/groups/refresh` (`system.manage`, không PIN; v0.1.65) → `202 {"session_id","requested":true}`: bảo bridge quét lại danh bạ ngay (lệnh `directory.sync`). `503 BRIDGE_OFFLINE` khi bridge tắt · `409 CHANNEL_NOT_ACTIVE` khi kênh chưa đăng nhập · `429 REFRESH_TOO_SOON` khi vừa làm mới (giãn cách 30 giây).
- `GET /channels/{type}/groups?pending=1` → chỉ nhóm đang chờ Sếp chọn (`listen_mode = pending`, chưa rời). `PATCH /channels/{type}/groups/{id}/mode` (`system.manage`) `{"mode": "off|tagged_only|silent|proactive|paused"}` → nhóm; `pending` không phải lựa chọn (Sếp không đặt lại về chờ). **Hộp hỏi (F1d; chỉ hiện khi Sếp đặt «Nhóm mới: Hỏi Sếp»)**: body thêm `"apply_to_new": true` (chỉ với `silent`/`off`, `422` với chế độ khác) = đồng thời lưu `new_group_mode` của kênh cho nhóm mới sau này; vì đổi chính sách nên cần PIN (`423 PIN_REQUIRED` — không đổi gì cả khi chưa có PIN, kể cả nhóm đang chọn). Không có `apply_to_new` ⇒ như cũ, không PIN. Mọi lượt chọn ở hộp hỏi đặt `new_group_asked = true`.
- Bridge chưa chạy → `POST /channels/{type}/login` trả `503 BRIDGE_OFFLINE`.
- `GET /channels/{type}/groups` → `[{"id","code","name","members":24,"kind":"internal|market|partner|customer|private","listen_mode":"pending|off|tagged_only|silent|proactive|paused","view_scope":"owner|manager|all_members","left":false}]`. `left: true` = Sếp đã rời nhóm (không đếm «đang lắng nghe»).
- Chế độ nghe (hằng `ALL_LISTEN_MODES` ở `api/gh/data/common.py`, web `ListenMode`): ĐANG nghe = `tagged_only` («Nghe hết, chỉ trả lời khi được tag» — vẫn lưu mọi tin, chỉ việc trả lời cần tag) · `silent` («Lắng nghe im lặng — lưu hết, không bao giờ trả lời (kể cả khi được tag)»; MẶC ĐỊNH của nhóm mới, QD 17/10) · `proactive`; KHÔNG nghe = `off` · `paused` (Sếp tạm dừng) · `pending` (chỉ khi Sếp đặt «Nhóm mới: Hỏi Sếp» — chờ Sếp chọn Lắng nghe im lặng/Không nghe, chưa lưu tin, vẫn thu danh sách thành viên).
- `PATCH /groups/{id}` (`system.manage`) `{listen_mode?, view_scope?, kind?}` → nhóm. Nhóm mới vào `silent` (QD 17/10, mặc định) — hoặc `pending` (hộp hỏi) / `off` nếu Sếp đã đặt `new_group_mode` của kênh là `ask` / `off`; nhóm đang `off` của bản cũ giữ nguyên `off`. Không đặt lại về `pending` được (`422`).

## Bộ não AI (provider, khoá, CLI) — màn cấu hình đầy đủ ở GĐ 4

- `GET /providers` → `[{"id","kind":"antigravity_cli|gemini|deepseek|openai_compat","name","endpoint","failover_rank","enabled","auth_state":"ok|expiring|expired|error|unconfigured","keys":[{"id","label":"GEM-KEY-01","last4":"x9Qa","enabled":true,"cooldown_until":null,"quota_left_pct":82}],"models":[{"id","model_name","daily_quota","used_today"}]}]`.
- `POST /providers` (`system.manage`) `{kind, name, endpoint?, keys: ["sk-…"], models?: ["gemini-2.5-flash"]}` → provider (khoá chỉ trả `last4`).
- `POST /providers/{id}/models` `{model_name, daily_quota?, rate_limit_per_min?}` → provider.
- `POST /providers/{id}/keys` `{secret}`; `DELETE /providers/{id}/keys/{kid}`; `PATCH /providers/{id}` `{enabled?, failover_rank?}`.
- `POST /providers/{id}/test` → `{"ok": true, "latency_ms": 812, "models": ["gemini-2.5-flash", …], "error": null}` (gọi thử 1 lượt).
- `GET /providers/credentials` → dòng thẻ "Khoá & phiên" của tab Kênh: `[{"icon","name","meta","state":"ok|warn|bad","state_label"}]`.
- Antigravity CLI:
  - `GET /cli/profiles` → `[{"id","email","plan_label","active":true,"expires_at","state":"ok|expiring|expired"}]`.
  - `POST /cli/login` (`system.manage`) → `202 {"login_id"}`; tiến trình qua WS `cli.login` `{"login_id","status":"starting|waiting_code|verifying|done|failed","url","message","profile"?}`. Owner mở `url`, đăng nhập Google, dán mã: `POST /cli/login/{id}/code` `{"code"}` → 202. Huỷ: `POST /cli/login/{id}/cancel` → 204. v0.1.30: `GET /cli/login/{id}` (`system.manage`) → cùng dạng sự kiện WS (dự phòng khi WS không tới; 404 khi không còn phiên).
  - `POST /cli/profiles/{id}/activate` 🔒 `cli.switch_account` → hồ sơ. `DELETE /cli/profiles/{id}` 🔒. 409 `CLI_LOGIN_IN_PROGRESS` khi đang đăng nhập thêm tài khoản; 409 `CLI_PROFILE_NO_SESSION` khi hồ sơ không có phiên đã lưu. `email` có thể `null` (tệp phiên không cho biết email).

## Trình thiết lập bước 4–7, 12

- `PUT /setup/steps/4` `{"provider_ids": [ưu tiên trước → sau]}` → state. Cần ≥1 provider đã `test` OK (hoặc CLI có hồ sơ hoạt động) → nếu không `409 STEP_INCOMPLETE`.
- `PUT /setup/steps/5` `{}` → cần ≥1 kênh `active`.
- `PUT /setup/steps/6` `{"groups": [{"id","listen_mode","view_scope"}]}` → cần ≥1 nhóm đang nghe (`tagged_only`/`silent`/`proactive`; `off`, `paused`, `pending` không tính).
- `PUT /setup/steps/7` `{"interval_seconds","count_threshold","min_confidence","rule_codes":["R-01",…],"weights":[{"dimension","value"}]}` → state.
- `GET /setup/rule-presets` → bộ quy tắc khởi đầu R-01…R-06 (cùng hình dạng quy tắc, `enabled` gợi ý).
- `GET /setup/first-run` → `{"raw_collected": 1244, "classifying": 250, "clean": 812, "lowconf": 31, "discarded": 120, "run": {…}|null}`; realtime qua WS `refinery.progress`.
- `PUT /setup/steps/12` → hoàn tất khi mọi bước bắt buộc đã xong; ở giai đoạn 2 bước 8–9 chưa có nên trả `409 STEP_INCOMPLETE` nêu rõ bước còn thiếu.
- Bước 4–7, 12 cần Owner và bước 1–3 đã xong (`409 STEP_ORDER`).
- `available` của bước 4–7, 12 thành `true`.

## WebSocket `/ws`

Cùng cookie phiên (gửi qua proxy). Server đẩy `{"type": "…", "data": {…}, "at": "…"}`; client có thể gửi `{"type": "ping"}` → `pong`. Sự kiện lọc theo quyền của người nhận:

| type | quyền | nội dung |
|---|---|---|
| `raw.new`, `raw.state` | `data.read` | xem trên |
| `refinery.progress`, `refinery.run` | `data.read` | xem trên |
| `channel.qr`, `channel.status` | `system.read` | xem trên |
| `listen.event` | `data.read` | v0.1.68 — tín hiệu sự kiện kênh cho Nhật ký lắng nghe (xem mục «Nhật ký lắng nghe»); 1-1 chỉ Owner. |
| `group.discovered` | `system.read` | `{id, code, name, channel, listen_mode: "pending"/"silent"/"off"}` — nhóm MỚI vừa phát hiện (v0.1.65); Console làm mới thẻ kênh: mở hộp «Nhóm mới — chọn Lắng nghe im lặng/Không nghe» chỉ khi `pending` (Sếp đặt Hỏi Sếp); `silent` ⇒ khối «N nhóm mới đã vào Lắng nghe im lặng». |
| `cli.login` | `system.manage` | xem trên |
| `header` | mọi người | `{channels_live, channels_connected, groups_listening, autonomy_level, data_confidence}` `data_confidence` (Owner chốt 24/09): số tin sàng lọc hôm nay (múi giờ tổ chức) vào thẳng Kho sạch ÷ (sạch + tin cậy thấp), không tính nhiễu; dạng 0–1, `null` khi hôm nay chưa sàng lọc tin nào. Gửi lại sau mỗi lượt sàng lọc. Cũng trả ở `GET /header`. |

Đóng với mã `4401` nếu chưa đăng nhập, `4428` nếu chưa thiết lập.
