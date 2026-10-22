# API giai đoạn 3 · Agent trực kênh (`duty`)

Cụm chỉ có backend (màn "Danh tính Agent" / "Agent đã nói gì, nhân danh gì" là giai đoạn 4). Nền chung ở
[`phase-3.md`](phase-3.md) mục "Agent trực kênh" (hook sau sàng lọc, `GET /agents/decisions`) và "Bàn làm việc".
Mã: `apps/api/gh/biz/duty/` (`context.py` phạm vi + ngữ cảnh, `engine.py` quyết định + policy + ghi vết,
`jobs.py` hook + việc định kỳ). Migration: `db/sql/0010_p3_duty.sql`.

## Agent trực kênh — chi tiết

### Luồng

```
gh.clean.ready {ids} ──▶ hook "duty" (consumer group hook:duty)
   └─ cặp (agent đang bật × đơn vị trong phạm vi, chưa quyết định) — tin tag agent xếp trước
        ├─ khoá Redis gh:duty:claim:{agent}:{unit} (5 phút) · giới hạn tần suất
        ├─ dựng ngữ cảnh C1…Cn (tất định)             ─┐
        ├─ model: agent_key "agent:{id}", purpose "duty_decide", json_mode
        ├─ kiểm phản hồi (JSON, mã ngữ cảnh, ID bịa)   │ context_refs = đúng các ref C1…Cn
        ├─ mức tự trị hiệu lực + policy                 │ sources của bản nháp = cùng danh sách
        └─ một transaction: bản nháp / mục sổ tay + agent.decisions + Action Log ─┘
```

### Phạm vi (ai nghe gì)

Một đơn vị ý nghĩa thuộc phạm vi agent khi **agent đang bật** (`agent.identities.is_enabled`) và:

| Điều kiện | Ghi chú |
|---|---|
| có dòng `agent.channel_scopes` `(agent_id, channel_id, group_id)` khớp nhóm của đơn vị, **hoặc** dòng cả kênh `(agent_id, channel_id, NULL)` khớp kênh của đơn vị, **hoặc** `core.groups.assigned_agent_id` = agent | `group_id NULL` = mọi nhóm của kênh **và** tin 1-1 trên kênh đó. Tin 1-1 chỉ tới agent có phạm vi cả kênh. |
| `hours` (ca trực, `tstzrange[]`) rỗng/NULL hoặc chứa `observed_at` của đơn vị | |
| nhóm chủ động: `proactive`; `tagged_only` chỉ với đơn vị có tin tag agent (`raw.events.mentions_agent`) | `silent` (Lắng nghe im lặng — mặc định sổ câu hỏi #22), `off`, `paused`, `pending` → không xét (tin `silent` vẫn được lưu và sàng lọc, chỉ không đưa cho agent trực). Tin 1-1 chỉ vào ứng viên khi kênh đặt `direct_mode = reply` (mặc định `silent`: lưu nhưng không đưa cho agent trực) HOẶC người gửi đã được gán BOT đích danh (`core.persons.attrs.agent_id`, Gán BOT ở Khách & Nhóm › Con người; v0.1.65, F080) — khi đó đúng agent đó phụ trách, kể cả khi kênh im lặng và agent không có phạm vi cả kênh, và agent đọc được tin 1-1 của người đó. |
| chưa có quyết định của agent này cho đơn vị này | chống trùng |

Dữ liệu đưa vào ngữ cảnh cũng chỉ lấy trong phạm vi ("tầm với") của agent: nhóm được gán (từng nhóm, cả kênh, hoặc
`assigned_agent_id`) và tin 1-1 trên kênh được gán cả kênh. Đơn vị liên quan, đơn vị gần nghĩa, cơ hội
(`source_group_id`), mục sổ tay có ref tới nhóm ngoài phạm vi, và lý do của điểm có chứng cứ ngoài phạm vi đều bị
loại. Một người nói ở nhóm ngoài phạm vi không làm lộ nội dung đó sang agent khác.

### Ngữ cảnh (tất định, có ghi lại)

Mỗi mục có mã ngắn `C1…Cn` (model chỉ được trích các mã này) và một ref thật:

| Thứ tự | Mục | `ref` |
|---|---|---|
| C1 | đơn vị kích hoạt: loại, kết luận, thực thể, độ tin cậy, trích dẫn nguyên văn, cờ `tagged_agent`, `from_our_side` | `{"type": "meaning_unit", "id"}` |
| | ID nhóm → hồ sơ nhóm (mã, tên, loại, chế độ nghe, kênh) | `{"type": "group", "id", "code"}` |
| | ID người → hồ sơ sống (mã, tên, loại, tổ chức, quan hệ, người phụ trách) | `{"type": "person", "id", "code"}` |
| | điểm hiện tại (`clean.current_scores`) của người và nhóm + tối đa 2 lý do | `{"type": "score", "id": "person:{uuid}:heat"}` |
| | sổ tay nhận thức của người và nhóm (≤ 12 mục mỗi sổ, "Giới hạn cho agent" và mục ghim trước) | `{"type": "notebook_entry", "id"}` |
| | dữ liệu sạch liên quan: ≤ 8 đơn vị cùng nhóm hoặc cùng người trong 30 ngày + ≤ 3 đơn vị gần nghĩa (pgvector, 90 ngày) | `{"type": "meaning_unit", "id"}` |
| | lịch sử tương quan: ≤ 5 cơ hội đang mở, ≤ 5 việc đang mở, ≤ 5 bản nháp 14 ngày qua cho cùng nhóm/người | `{"type": "opportunity" \| "task" \| "draft", "id", "code"}` |

Ngân sách: `agent.bindings.context_tokens` của `agent:{id}` (mặc định 6000, ước 3 ký tự/token); vượt thì bỏ từ cuối
danh sách, luôn giữ 3 mục đầu. Nhiệt độ lấy từ binding (mặc định 0,3). Danh tính agent (tên, vai trò, xưng hô, giọng,
được nói khi, cấm) và mục "Giới hạn cho agent" của sổ tay nằm trong lời nhắc hệ thống.

`agent.decisions.context_refs` = **đúng** danh sách ref của C1…Cn theo thứ tự; bản nháp agent tạo có
`sources = [{"label", "ref"}]` cùng danh sách, cùng thứ tự. `cited_refs` là tập con model đã trích.

### Phản hồi model

```json
{"decision": "silent|note|suggest|draft|send", "rationale": "…", "context_refs": ["C1", "C3"],
 "note": {"section": "attention_now|rolling_context|preferences|open_threads", "text": "…"},
 "text": "nội dung gợi ý / tin soạn sẵn"}
```

Bị **loại** (ghi `silent`, `outcome = "rejected"`, lý do bắt đầu "Loại phản hồi model: …", Action Log `result = failed`,
không bản nháp, không ghi sổ tay) khi: không phải JSON / không phải đối tượng; `decision` ngoài 5 giá trị;
`context_refs` không phải danh sách chuỗi hoặc có mã không tồn tại (vd `C99`); câu chữ (`rationale`, `text`,
`note.text`) nhắc UUID hay mã đối tượng (`PER-…`, `GRP-ZL-…`, `OPP-…`, `TSK-…`, `ACT-…`, `RAW-…`…) không có trong
lời nhắc; quyết định khác `silent` mà không trích mã nào; `note` thiếu nội dung; `suggest/draft/send` thiếu `text`;
`text` quá 2000 ký tự.

### Quyết định → policy

Mức hiệu lực `L` = `gh.biz.core.drafts.effective_level` (thấp nhất của tổ chức, nhóm, người, agent).

| Model đề xuất | Kết quả ghi (`decision` / `outcome`) |
|---|---|
| `silent` | `silent` / `none` |
| `note` | ghi `memory.entries` (tác giả `agent:{id}`, ref = đơn vị kích hoạt + mục đã trích) → `note` / `noted`; mọi mức (ghi nhận nội bộ) |
| `suggest` | L ≥ 3 → `suggest` / `suggested` (nội dung ở `proposal.text`); L ≤ 2 → `silent` / `blocked` |
| `draft`, `send` | L ≤ 2 → `silent` / `blocked`. L = 3 → `suggest` / `suggested`. L ≥ 4 → `create_draft(kind="message", agent_id, sources=…)` → bản nháp **`pending`** (`hold_reason` "ghi ra ngoài phải chờ duyệt") → `draft`/`send` / `held`. **Mức 5–6 cũng vậy** (khoá cứng 3, quyết định Q2): không bao giờ có lệnh gửi tới bridge từ agent. |

**Nhóm «Lắng nghe im lặng» (`silent`) KHÔNG BAO GIỜ trả lời — kể cả khi được tag** (Sếp chốt 17/10/2026): nhóm `silent`
KHÔNG vào ứng viên agent trực (xem «Phạm vi»: không tốn lượt LLM, không có `agent.decisions` cho tin của nhóm đó; sàng lọc vẫn chạy). Phòng thủ: nếu
nhóm vừa bị đặt sang `silent` giữa lúc chọn ứng viên / hỏi model và lúc `apply`, `apply` đọc lại `listen_mode` hiện tại; với tin của nhóm
`silent` quyết định cho phép chỉ `silent`/`note` ở mọi mức tự trị (câu «Được chọn: silent, note.» trong lời nhắc); model nêu
`suggest`/`draft`/`send` thì bị chặn: `decision = silent`, `requested = <điều model nêu>`, `outcome = blocked`, lý do «nhóm lắng
nghe im lặng: không bao giờ trả lời, kể cả khi được tag». Áp cả khi agent được gán BOT cho nhóm đó hoặc BOT cho người gửi
(gán BOT người chỉ áp cho tin 1-1 của họ — B8). Chế độ `tagged_only` («Nghe hết, chỉ trả lời khi được tag») giữ nguyên:
được tag thì trả lời như bảng trên.

Hạ `draft`/`send` xuống `suggest` (không tạo bản nháp, lý do ghi trong `rationale` trong ngoặc vuông) khi:
tin do phía mình nói ra; đã có bản nháp của agent đang chờ duyệt cho cùng nơi nhận (trừ tin tag); agent đã tạo
≥ `drafts_per_hour` bản nháp trong giờ qua.

Đích gửi của bản nháp: nhóm của đơn vị (`thread_type = group`) hoặc người gửi (`thread_type = user`) với tin 1-1.
Đối tượng (`subject`) là người, không có người thì nhóm. Tiêu đề "Trả lời {người} · {nhóm}".

### Chống trùng, tần suất, cách ly lỗi

- **Một đơn vị một quyết định cho mỗi agent**: khoá Redis khi đang xử lý (worker khác bỏ qua, xử lý lại sau) và chỉ mục
  duy nhất `agent.decisions (agent_id, trigger_unit_id)`. Bản nháp/mục sổ tay và quyết định cùng transaction; nếu
  quyết định bị trùng thì rollback, không để lại bản nháp.
- **Tần suất** (`agent.identities.limits`, thiếu khoá → mặc định): `decisions_per_min` 20 lượt model/phút/agent —
  vượt thì cặp bị hoãn (không mất); **tin tag agent không bị giới hạn này chặn** và luôn được xử lý trước.
  `drafts_per_hour` 30 — vượt thì hạ xuống gợi ý.
- **Model chết** (`ModelUnavailable`): dừng lô, không ghi gì; hook ném `Deferred` → sự kiện nằm lại trong consumer
  group `hook:duty`, được nhận lại (không tính lỗi, không vào DLQ) cho tới khi model sống lại.
- **Lỗi khác** của một cặp: cặp khác vẫn chạy; cuối lô ném lỗi để bus thử lại (quá 5 lần → DLQ); cặp lỗi lặp lại 5 lần
  được chốt `silent` / `rejected` "Bỏ qua sau 5 lần lỗi xử lý: …" để không thử mãi.
- **Quét vớt** `duty_sweep` (worker, phút 2, 7, 12… mỗi 5 phút): đơn vị tạo trong 24 giờ qua, cũ hơn 2 phút, trong phạm
  vi mà chưa có quyết định (tối đa 50/tổ chức/lượt) — cho trường hợp sự kiện đã vào DLQ hoặc worker chết.
- Hook có hạn 300 giây; sau 240 giây phần còn lại được hoãn.

### Ghi vết

`agent.decisions` (cột thêm ở `0010`):

| Cột | Ý nghĩa |
|---|---|
| `trigger_ref` / `trigger_unit_id` | `{"type": "meaning_unit", "id"}` / id đơn vị |
| `decision` | kết quả cuối sau policy: `silent · note · suggest · draft · send` |
| `requested` | model đề xuất gì (NULL khi không đọc được phản hồi) |
| `outcome` | `none · noted · suggested · held · blocked · rejected` |
| `autonomy_level` | mức hiệu lực lúc quyết định |
| `rationale` | lý do của model + `[lý do hạ/chặn]`, hoặc lý do loại |
| `context_refs` / `cited_refs` | ref của toàn bộ ngữ cảnh / phần model trích |
| `draft_id` | bản nháp đã tạo (chỉ với `outcome = held`) |
| `proposal` | `{"text"}` nội dung gợi ý/tin soạn; `{"note_entry_id", "section", "text"}` với `note` |

Action Log: mỗi quyết định một dòng `agent.decided` (`actor_type = agent`, `actor_id = agent:{id}`,
`target_type = meaning_unit`, `autonomy_level`, `result`: `held` khi có bản nháp, `blocked`, `failed` khi bị loại,
còn lại `ok`; `detail`: `decision, requested, outcome, reason, decision_id, draft_id, draft_code, context_refs (số mục),
tagged`). Bản nháp còn có dòng `draft.created` riêng do `create_draft` ghi.

WebSocket (sau khi commit): `draft.new` (item danh sách, như Bàn làm việc) khi có bản nháp, và
`agent.decision` `{"id", "agent": AgentRef, "decision", "trigger": EvidenceRef, "draft": {"id", "code"} | null}`
(quyền `system.read`).

Đọc lại: `GET /agents/decisions` (nền chung) trả `context_refs`; `GET /drafts/{id}` trả `sources` cùng danh sách;
`GET /explain/draft/{id}` dựng chuỗi đơn vị → trích dẫn → bản ghi thô từ đó.

### Thay đổi schema (`0010_p3_duty.sql`)

- `agent.channel_scopes`: bỏ khoá chính `(agent_id, channel_id, group_id)` (buộc `group_id NOT NULL`), thêm `id`
  làm khoá chính, `group_id` cho phép NULL (= cả kênh), `channel_id NOT NULL`, chỉ mục duy nhất
  `(agent_id, channel_id, group_id) NULLS NOT DISTINCT`.
- `agent.identities.limits jsonb` (giới hạn tần suất).
- `agent.decisions`: `trigger_unit_id, requested, outcome, autonomy_level, cited_refs, proposal` + chỉ mục duy nhất
  `(agent_id, trigger_unit_id)` và chỉ mục `(org_id, decision, at DESC)`.

## Câu hỏi mở

1. `EvidenceRef.type` ở `phase-3.md` chưa có `group`, `person`, `notebook_entry` nhưng ngữ cảnh agent (và sổ tay của
   sàng lọc) dùng các loại này. Tạm: giữ các loại đó trong `context_refs`/`sources`; màn giai đoạn 4 cần hiển thị
   (nhóm/người mở hồ sơ, mục sổ tay mở Sổ tay nhận thức).
2. Mức 0–2 với `note`: ARCHITECTURE xếp "ghi sổ tay" vào việc nội bộ tự làm ở mức 5–6, nhưng sổ tay cũng là trí nhớ
   lũy tiến mà sàng lọc ghi không qua mức tự trị. Tạm: agent ghi sổ tay ở mọi mức (mức 0 "Chỉ ghi nhận").
3. Chế độ nghe «Lắng nghe im lặng» (`silent`): Sếp chốt 17/10/2026 — agent KHÔNG BAO GIỜ soạn tin hay gợi ý trả lời, kể cả khi được tag (thay cách hiểu tạm «chỉ soạn tin khi được tag»); mặc định (sổ câu hỏi #22) nhóm `silent` còn không được đưa cho agent trực, nên cũng không `note`.
