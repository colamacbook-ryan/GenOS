# API giai đoạn 3 · Quan hệ & Đối tượng (`relations`)

Nền chung ở [`phase-3.md`](phase-3.md): hình dạng dùng chung (`PersonRef`, `GroupRef`, `UserRef`, `AgentRef`,
`Score`, `EvidenceRef`), phạm vi dữ liệu (`ScopeFilter`), chứng cứ (`GET /explain/{kind}/{id}`), góc nhìn đã
lưu. Cụm này phụ trách **Nhóm & Con người** (`directory`), **Hồ sơ sống** (`profile`), **Sổ tay nhận thức**
(`notebook`) và **Tài liệu** (`documents`, spec G1).

Mã: `apps/api/gh/biz/relations/` (`service.py` hình dạng + tiện ích dùng chung, `routes.py` mọi endpoint,
`jobs.py` rỗng — không cần hook/cron riêng, nén sổ tay hằng ngày đã chạy ở `gh.worker` từ giai đoạn 2). Migration:
`db/sql/0006_p3_relations.sql` (chỉ ALTER/thêm chỉ mục — bảng gốc `core.persons`, `core.groups`,
`core.person_identities`, `memory.notebooks/entries/compactions`, `biz.documents/document_acl`,
`clean.current_scores/relationships` đã có từ giai đoạn 1/2).

Quyền: mọi endpoint dùng chung cột "Hồ sơ khách" của ma trận — `profile.read` (đọc) / `profile.write` (ghi) —
đúng như `gh/auth/rbac.py: SCREEN_PERMISSION` đã ánh xạ 4 màn này từ trước.

## Sổ tay nhận thức: hai lớp API cho hai màn khác nhau

Engine (ghi lũy tiến từ refinery, nén 90%/24h, ghim không nén) đã có ở `gh.memory.notebook` từ giai đoạn 2 —
cụm này **không viết lại**. `gh.data_api.routes` (`GET/POST/PATCH/DELETE /notebooks/{type}/{sid}/...`, đã có
sẵn) là lớp API cho màn kỹ thuật **Kho sạch** (`clean.read`, không lọc theo phạm vi người/nhóm — Owner/Auditor
xem trí nhớ tạm của bất kỳ ID nào để gỡ lỗi sàng lọc). Cụm `relations` thêm một lớp API **khác**, ở đường dẫn
`/notebook/...` (số ít, không `s`, để phân biệt path với `/notebooks/...` của data_api), cho màn kinh doanh
**Sổ tay nhận thức**: cùng gọi `gh.memory.notebook`, nhưng **luôn kiểm phạm vi** (`ensure_person`/`ensure_group`)
trước, và thêm 3 việc lớp kia chưa có: danh sách chủ thể có sổ tay (`nbSubjects`), "đặt lại" (khác nén — không
sinh mục tóm tắt), và xem mục đã nén (`nbDropped`). Không sửa `gh/data_api/routes.py`.

## Endpoint

### Nhóm & Con người (`directory`)

`GET /directory/channels` (`profile.read`) → thẻ đầu mỗi kênh (không lọc theo phạm vi — không có gì nhạy cảm
riêng cá nhân, giống các khối không phải `queue` của `GET /overview`):
```json
[{"id", "type": "zalo", "name": "…", "state": "active" | "pending_qr" | "expired" | "logged_out" | null,
  "group_count": 34, "events_24h": 1244}]
```

`GET /directory/groups?channel_id=&kind=&listen_mode=&cursor=&limit=` (`profile.read`, `sc.group_sql`) →
```json
{"items": [{"id", "code": "GRP-ZL-0114", "name", "kind": "internal|market|partner|customer|private",
            "listen_mode", "member_count", "events_24h": 412, "heat": 87.0 | null,
            "channel": {"type": "zalo", "name": "…"}, "bot": AgentRef | null, "created_at"}],
 "next_cursor", "total"}
```
`POST /directory/groups/{id}/bot` (`profile.write`) `{"agent_id": uuid|null, "autonomy_level"?: 0-6}` → gán/gỡ
BOT trực nhóm (`core.groups.assigned_agent_id`) + mức tự trị riêng nhóm (`attrs.autonomy_level` — cùng cột
`gh.biz.core.drafts.effective_level` đã đọc).

`GET /directory/people?relation=&heat=&value=&priority=&bot=&cursor=&limit=` (`profile.read`, `sc.person_sql`)
— đúng **5 hàng bộ lọc** của thiết kế (`handoff/01-ui-screens.md` §directory, giá trị nút bấm lấy từ
`design/Gen-Harness Console.dc.html`):

| Tham số | Giá trị | Nguồn |
|---|---|---|
| `relation` | `direct` \| `via_staff` \| `stranger` \| `staff` (Liên quan Sếp) | `core.persons.relation_to_owner` |
| `heat` | `high` (≥80) \| `mid` (50–79) \| `cold` (<50 hoặc chưa có điểm) | `clean.current_scores` dimension `heat` |
| `value` | `high` (≥500tr) \| `mid` (100–500tr) \| `unknown` (0/chưa rõ) | tổng `value_vnd` cơ hội đang mở của người đó |
| `priority` | `P1` \| `P2` \| `P3` | ưu tiên cao nhất trong `biz.inbox_items` gắn với người đó, mặc định `P3` nếu chưa có |
| `bot` | `assigned` \| `unassigned` | `attrs.agent_id` có/không |

```json
{"items": [{"id", "code": "PER-0042", "name", "type", "org_name", "relation": "direct", "channels": ["zalo"],
            "heat": 87.0 | null, "heat_trend": "up|down|flat" | null, "value_vnd": 84000000 | null,
            "priority": "P1", "bot": AgentRef | null, "autonomy_level": 3 | null,
            "owner_user_id": "uuid" | null}], "next_cursor", "total"}
```
`POST /directory/people/{id}/bot` (**chỉ Owner + mã PIN `policy.change`**, v0.1.65 — gán BOT bỏ qua chế độ im lặng của tin 1-1; `POST /directory/people/bulk-bot` cùng quyền; BOT của nhóm vẫn `profile.write`) `{"agent_id"?: uuid|null, "autonomy_level"?: 0-6|null}` →
**Thiết lập BOT + tự trị riêng từng người**. Lưu vào `core.persons.attrs.agent_id` / `attrs.autonomy_level` —
quyết định tự đưa ra vì `core.persons` không có cột `assigned_agent_id` như `core.groups` (spec chỉ nói "gán
BOT cho từng người", không nói thêm cột); dùng `attrs` giữ đúng quy ước `attrs.autonomy_level` đã có từ
`gh.biz.core.drafts` (giai đoạn 2/3 core), không thêm cột/bảng mới. Route agent trực kênh (`gh.biz.duty`) hiện
chỉ đọc phạm vi theo kênh/nhóm (`agent.channel_scopes`, `core.groups.assigned_agent_id`) — nối `attrs.agent_id`
của người vào vòng quyết định của agent trực kênh (nếu cần ưu tiên theo người, không chỉ theo nhóm) là việc của
cụm `duty`, ngoài phạm vi cụm này; ở đây chỉ dựng chỗ lưu + màn đọc/ghi.

### Hồ sơ sống (`profile`)

`GET /profile/{person_id}` (`profile.read`, `ensure_person`) →
```json
{"person": PersonRef & {"title", "relation_to_owner", "owner": UserRef | null},
 "autonomy_level": 0-6 | null, "bot": AgentRef | null, "owner_note": "…" | null,
 "identities": [{"id", "channel": {"type","name"}, "external_id", "handle", "phone_e164", "first_seen_at"}],
 "scores": [{"dimension": "heat", "label": "độ nóng", "value": 87.0, "trend": "up|down|flat"|null, "updated_at"}],
 "summary": [{"text": "…", "tone": "ok|bad|neutral", "evidence": EvidenceRef}],
 "timeline": [{"id", "event_type", "conclusion", "confidence", "observed_at", "group": GroupRef|null, "evidence"}],
 "documents": [{"id", "title", "mime", "bytes", "created_at"}],
 "touchpoints": [UserRef],
 "merge_history": [{"id", "op": "merge|split", "from_person", "to_person", "identities": ["uuid"], "at",
                    "reverted_at"}]}
```
- `scores` trả **mọi** chiều điểm đang có cho người đó (không cứng 5 — số chiều tuỳ `refinery.scoring_weights`
  của tổ chức); "vì sao" mở riêng bằng `GET /explain/score/person:{id}:{dimension}` (đã có ở core).
- `summary` ("Hệ thống hiểu gì") dựng từ 5 đơn vị ý nghĩa gần nhất, tông màu theo `event_type`
  (`Complained|MentionsCompetitor|WentSilent` → `bad`, `AskedPrice|OfferedSupply|SentQuotation|DealWon` →
  `ok`) — quyết định tự đưa ra vì spec không cho thuật toán tính "hệ thống hiểu gì", chỉ mô tả hình dạng hiển
  thị (chấm màu + câu).
- `touchpoints` ("Người nội bộ từng chạm") đọc từ `ops.action_log` (`target_type = 'person'`, mọi user đã có
  hành động trên hồ sơ này) — quyết định tự đưa ra: không có bảng "lượt chạm" riêng, Action Log là nguồn sự
  thật duy nhất mọi hành động của người (ARCHITECTURE R8) nên tái dùng thay vì thêm bảng.
- `merge_history` đọc `core.identity_merge_log` cả hai chiều (`from_person`/`to_person`) — hồ sơ hợp nhất giữ
  đúng lịch sử dù đang xem từ hồ sơ gốc hay hồ sơ đã gộp.

`PATCH /profile/{person_id}` (`profile.write`) `{"owner_user_id"?: uuid|null, "autonomy_level"?: 0-6|null,
"note"?: string|null, "person_type"?: customer|partner|staff|candidate|learner|supplier|unknown,
"relation_to_owner"?: direct|via_staff|staff|stranger}` → **Gán phụ trách**, **mức tự trị với đối tượng này**,
**ghi chú tay của Sếp**, **loại người / Liên quan Sếp** (v0.1.64: nút "Đánh dấu là nhân viên"; loại do Sếp đặt khoá
bằng `attrs.person_type_locked` nên quy tắc sàng lọc không đè lại). `note`
lưu ở `attrs.owner_note` — đây là ghi chú **tay**, không endpoint/mã nào khác của hệ thống (refinery, agent
trực kênh, worker) từng ghi vào khoá này, nên "hệ thống không sửa" là bảo đảm kiến trúc, không chỉ quy ước UI.
Trả về hồ sơ đầy đủ (như `GET`).

### Sổ tay nhận thức (`notebook`)

`GET /notebook/subjects?type=person|group&cursor=&limit=` (`profile.read`, `sc.person_sql`/`sc.group_sql`) —
danh sách chủ thể có sổ tay (`nbSubjects`), mới cập nhật trước:
```json
{"items": [{"id", "code", "name", "entries": 4, "token_used": 1842, "token_budget": 4000, "updated_at"}],
 "next_cursor"}
```
`GET /notebook/{type}/{sid}` (`profile.read`) →
```json
{"subject": {"type","id","code","name"}, "token_used", "token_budget", "compaction_no", "last_compacted_at",
 "sections": [{"key": "attention_now", "title": "Điều cần chú ý ngay",
               "entries": [{"id","body","refs","pinned","editable", "author": {"type":"user|agent","label"},
                            "created_at"}]}],
 "refs": [{"type":"meaning_unit","id":"…"}]}
```
`sections` đúng 5 mục của `gh.memory.notebook.SECTIONS`; `refs` là danh sách ID liên quan gộp không trùng từ
mọi mục đang hiện (`nbRefs`, tối đa 40, chip mono bấm được ở web). `editable = false` khi mục do agent/hệ
thống ghi (`author` không bắt đầu `user:`).

Owner có thể (`nbOwnerActions`):
- `POST /notebook/{type}/{sid}/entries` (`profile.write`) `{"section","body","pinned"?,"refs"?}` → ghi mục tay,
  `author = user:<id>`.
- `PATCH /notebook/{type}/{sid}/entries/{eid}` (`profile.write`) `{"body"?,"pinned"?}` — **ghim** cho phép trên
  **mọi** mục kể cả do agent ghi (đây là cách giữ một quan sát quan trọng khỏi bị nén, không phải sửa nội
  dung); **sửa nội dung** chỉ khi `author` là `user:` → `409 SYSTEM_ENTRY_READONLY` nếu không. Sửa nội dung tạo
  bản mới (bản cũ lưu trữ, trỏ `replaced_by`, như `gh.data_api`), trả `{"id": "<uuid mới>"}`.
- `DELETE /notebook/{type}/{sid}/entries/{eid}` (`profile.write`) → lưu trữ (`archived_at`), cùng ràng buộc
  chỉ mục `user:` mới xoá được → `204`.
- `POST /notebook/{type}/{sid}/compact` (`profile.write`) → **nén ngay** (`gh.memory.notebook.compact`, giữ một
  mục tóm tắt `rolling_context`), trả sổ tay sau khi nén.
- `POST /notebook/{type}/{sid}/reset` (`profile.write`) → **đặt lại**: lưu trữ toàn bộ mục chưa ghim (trừ
  "Giới hạn cho agent" — cùng `NEVER_COMPACT`) mà **không** sinh mục tóm tắt, khác `compact()`. Quyết định tự
  đưa ra (spec chỉ liệt kê tên hành động "đặt lại", không định nghĩa): đây là dọn sạch hẳn để agent bắt đầu lại
  từ số 0 cho một chủ thể, khác nén (giữ tóm tắt để không mất ngữ cảnh). Ghi Action Log riêng (`notebook.reset`,
  không lẫn với `notebook.compacted`).

`GET /notebook/{type}/{sid}/history` (`profile.read`) → lịch sử nén (`nbHistory`, `memory.compactions`, mới
nhất trước): `[{"compaction_no","at","tokens_before","tokens_after","archived","summary"}]`.

`GET /notebook/{type}/{sid}/dropped?cursor=&limit=` (`profile.read`) → mục đã nén khỏi ngữ cảnh, **vẫn truy
được** (`nbDropped`): `{"items": [{"id","section","body","refs","author": {"type"},"archived_at"}], "next_cursor"}`.

### Tài liệu (`documents`, spec G1)

Kho tệp: chưa có client MinIO/S3 nào nối dây ở giai đoạn 1/2 dù ARCHITECTURE §2 định nghĩa MinIO — không gói
`boto3`/`minio`, không biến cấu hình endpoint/khoá trong `gh/config.py`, chỉ có dịch vụ `objects` trong
`deploy/compose.yaml`. **Quyết định tự đưa ra**: dựng `gh.chassis.objects.ObjectStore` — một điểm nối duy nhất
(`get_object_store()`) mà `biz.documents.storage_key` trỏ qua, cài đặt mặc định ghi xuống đĩa cục bộ ngoài repo
(`LocalObjectStore`, thư mục cấu hình qua `GH_OBJECTS_DIR`) để chạy được ngay cả không có MinIO/Docker (đúng
môi trường test của nhiệm vụ này). Đây vẫn là "storage_key + blob", không phải một hệ lưu trữ mới về khái
niệm; khi một cụm hạ tầng khác nối dây MinIO thật, chỉ cần thêm một `ObjectStore` mới và đổi
`get_object_store()`, endpoint dưới đây không đổi. Tải lên/xuống đi qua JSON (`content_base64`), không
multipart — codebase này chưa dùng multipart ở đâu (không có `python-multipart`) và mọi endpoint khác đều thân
JSON thuần; thêm một dạng thân yêu cầu khác (multipart) chỉ cho riêng cụm này là không cần thiết. Giới hạn
20MB/tệp.

`biz.document_acl.principal` ∈ `role:<code>` (vai trò) \| `user:<id>` (cá nhân) \| `group:<id>` (một
`core.teams` — nhóm **nhân viên nội bộ**, khác `core.groups` là nhóm chat của khách; quyết định tự đưa ra vì
schema chỉ ghi "group:<id>" không nói rõ bảng nào — `core.teams` khớp khái niệm "nhóm" dùng để cấp quyền nội
bộ, còn `core.groups` là đối tượng nghiệp vụ được sở hữu, không phải người được cấp quyền) \| `agent:<id>`.
**ACL là danh sách cấp thêm quyền, không phải danh sách chặn**: một tài liệu luôn thấy/sửa được trong phạm vi
mặc định của người/nhóm sở hữu (`owner_person_id`/`owner_group_id`, như mọi đối tượng nghiệp vụ khác —
`docs/api/phase-3.md` §Phạm vi), **cộng thêm** bất kỳ dòng ACL nào khớp vai trò/cá nhân/team của người gọi —
dùng để chia sẻ tài liệu ra ngoài phạm vi mặc định. Quyết định tự đưa ra (spec chỉ nói "ACL theo nhóm & cá
nhân", không nói ACL có ghi đè phạm vi mặc định hay không): chọn cộng thêm vì mọi đối tượng khác trong hệ
thống (cơ hội, việc, bản nháp) đều dùng đúng một khái niệm phạm vi; một khái niệm ACL "chặn" song song sẽ mâu
thuẫn với ScopeFilter dùng chung và dễ khoá nhầm chính người trong phạm vi.

- `GET /documents?owner_person_id=&owner_group_id=&source=channel|agent|tay&cursor=&limit=` (`profile.read`) →
  `{"items": [{"id","title","description","mime","bytes","owner": PersonRef|GroupRef|null,
               "source","created_by","created_at","updated_at"}], "next_cursor", "total"}`. `source` suy từ
  `created_by` (`user:` → tay, `agent:` → agent, còn lại → kênh).
- `GET /documents/{id}` (`profile.read`) → mục trên, cộng `acl: [{"principal","can_read","can_write"}]`.
- `POST /documents` (`profile.write`) `{"title","description"?,"filename","mime","content_base64",
  "owner_person_id"?,"owner_group_id"?,"acl"?: [{"principal","can_read","can_write"}]}` → `201`. Luôn cấp
  `role:owner` và người tải lên (`user:<id>`) đọc/ghi đầy đủ, cộng ACL do người gọi truyền thêm.
- `GET /documents/{id}/content` (`profile.read`) → tải xuống/xem trước, trả nguyên bytes + `Content-Type` đúng
  mime, `Content-Disposition: inline; filename*=UTF-8''<tên đã mã hoá>` (RFC 5987 — tên tệp tiếng Việt).
- `PATCH /documents/{id}` (`profile.write`, cần `can_write`) `{"title"?,"description"?,"owner_person_id"?,
  "owner_group_id"?}` → sửa metadata (không sửa nội dung tệp — tải lại là tạo tài liệu mới).
- `PUT /documents/{id}/acl` (`profile.write`, cần `can_write`) `[{"principal","can_read","can_write"}]` → thay
  toàn bộ ACL.
- `DELETE /documents/{id}` (`profile.write`, cần `can_write`) → xoá mềm (`deleted_at`) + xoá blob (best-effort,
  dòng metadata đã xoá mềm là nguồn sự thật, không chặn API nếu xoá blob lỗi) → `204`.
