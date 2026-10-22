# API giai đoạn 3 · Bản đồ quan hệ (`graph`)

Nền chung ở [`phase-3.md`](phase-3.md): hình dạng dùng chung (`PersonRef`, `GroupRef`, `Score`, `EvidenceRef`),
phạm vi dữ liệu (`ScopeFilter`), chứng cứ. Cụm này phụ trách **một màn, bốn chế độ** (PLAN §3.5, quy mô XL):
**danh sách** (bộ lọc mạnh), **Người↔Người**, **Nhóm↔Nhóm**, **Luồng chủ đề** — cộng vị trí node đã lưu
("đồ thị tương tác d3-force/elkjs, ≤ 200 node, lưu vị trí"). Bấm một node ra Hồ sơ sống (`GET
/profile/{person_id}`, cụm `relations`).

Mã: `apps/api/gh/biz/graph/` (`jobs.py` tính lại cạnh — **nguồn dữ liệu duy nhất của toàn màn**, `service.py`
giới hạn 200 node dùng chung, `routes.py` mọi endpoint). Migration: `db/sql/0007_p3_graph.sql` (chỉ thêm chỉ
mục — bảng gốc `clean.relationships`, `core.group_members`, `ops.saved_views` đã có từ giai đoạn 1/3-core).

Quyền: `profile.read` (đọc, cùng cột "Hồ sơ khách" của ma trận mà `gh/auth/rbac.py: SCREEN_PERMISSIONS` đã ánh
xạ cho `graph`) / `profile.write` (lưu vị trí node, dựng lại đồ thị tay).

## Nguồn dữ liệu: `clean.relationships` và việc nền tính lại nó

Bảng `clean.relationships` (đã có từ giai đoạn 1, cột `from_type/from_id/to_type/to_id/kind/window_days/
weight/interactions/last_at/state/topic/computed_at`) **chưa có job nào ghi** trước cụm này — `gh.biz.graph.
jobs.recompute_org` là nơi duy nhất tính nó, chạy nền mỗi 15 phút (`graph_recompute`, `JOBS`) và có thể bấm tay
(`POST /graph/recompute`, xem dưới). Bốn `kind` đúng enum đã chú thích sẵn trong schema:

| `kind` | Chiều | Ý nghĩa | Trọng số (`weight`) | `interactions` |
|---|---|---|---|---|
| `interacts` | person↔person | hai người cùng nhắn trong cùng nhóm | tổng, theo mọi nhóm chung, của `min(số tin của A trong ngày, số tin của B trong ngày)` — mỗi ngày cả hai cùng hoạt động cộng thêm phần chung nhỏ hơn | số **ngày** cả hai cùng hoạt động chung nhóm |
| `shares_members` | group↔group | hai nhóm có thành viên chung | `số thành viên chung / min(tổng thành viên nhóm A, tổng thành viên nhóm B)` (hệ số chồng lấp, 0–1) | số thành viên chung |
| `owns` | person→group | người có vai trò `admin` trong nhóm (`core.group_members.role`) — "ai đang nắm" | số tin người đó gửi trong nhóm trong `window_days` (tối thiểu 1.0 — admin im lặng vẫn là ownership hợp lệ) | như `weight` (0 nếu chưa từng nhắn) |
| `bridges` | person→group | **cầu nối**: người là thành viên của ≥ 2 nhóm mà, bỏ người đó ra, cặp nhóm không còn thành viên chung nào khác | số cặp nhóm người đó bắc cầu theo định nghĩa trên (dùng chung cho mọi cạnh `bridges` của người đó) | = `weight` |

`window_days` mặc định 90 (cửa sổ tính trọng số `interacts`/`owns`; `shares_members` đọc toàn bộ thành viên
hiện tại, không theo cửa sổ — thành viên nhóm không "hết hạn"). **Ngưỡng lạnh cố định 30 ngày**, tách khỏi
`window_days` (PLAN §3.5: "lạnh > 30 ngày"): `state = "cold"` khi `last_at` là `NULL` hoặc quá 30 ngày, ngược
lại `"active"`. `topic` (chỉ trên `interacts`) = sản phẩm được nhắc nhiều nhất (`entities->>'product'`, cùng
quy ước `gh.biz.queue.routes._signals` đã dùng) trong (các) nhóm chung của cặp người, trong `window_days`.

**Quyết định tự đưa ra** (spec chỉ nói "trọng số cạnh thay đổi theo thời gian… ai là cầu nối… khách nào đang
lạnh… nhân viên nào đang ôm quá nhiều ball", không cho công thức): bốn công thức trên chọn vì đều tính được
trực tiếp từ dữ liệu đã có (`raw.events`, `core.group_members`, `clean.meaning_units`), xác định (chạy lại cho
cùng dữ liệu ra cùng kết quả — `recompute_org` idempotent, có test), và giải thích được bằng một câu cho người
dùng (không phải hộp đen). "Cầu nối" dùng đúng nghĩa lý thuyết đồ thị hẹp (cắt khớp cục bộ giữa hai nhóm qua
một người), không lan ra toàn đồ thị (không tính articulation point toàn cục) — đủ cho PLAN's "nhìn được ai là
cầu nối" mà chi phí tính O(số nhóm mỗi người²) chấp nhận được ở quy mô nhóm chat.

`recompute_org(db, org_id, window_days=90)`: UPSERT theo cạnh (khoá `ON CONFLICT (org_id, from_type, from_id,
to_type, to_id, kind, window_days)`), rồi xoá cạnh không còn hợp lệ (không được đụng ở lượt chạy này). Trả
`{"interacts": n, "shares_members": n, "owns": n, "bridges": n}`.

## Chế độ 1 — Danh sách (`GET /graph/list`)

`GET /graph/list?type=&channel=&heat=&potential=&risk=&owner_user_id=&state=&relation=&cursor=&limit=`
(`profile.read`, `sc.person_sql`) — 8 bộ lọc của spec (LOCKED §"Relationship Map"):

| Tham số | Giá trị | Nguồn |
|---|---|---|
| `type` (loại người) | `customer\|partner\|staff\|candidate\|learner\|supplier\|unknown` | `core.persons.person_type` |
| `channel` (kênh) | `zalo\|whatsapp\|telegram\|linkedin` | có ≥1 danh tính trên kênh đó |
| `heat` (độ nóng) | `high` (≥80) \| `mid` (50–79) \| `cold` (<50/chưa có) | `clean.current_scores` dimension `heat` |
| `potential` (tiềm năng) | `high\|mid\|low`, cùng ngưỡng 80/50 | dimension `potential` |
| `risk` (rủi ro) | `high\|mid\|low`, cùng ngưỡng 80/50 | dimension `churn_risk` |
| `owner_user_id` (người phụ trách) | uuid | `core.persons.owner_user_id` |
| `state` (thời gian tương tác gần nhất) | `active\|cold`, ngưỡng 30 ngày | `max(raw.events.occurred_at)` của người đó (tin đến, không phải cạnh trong `clean.relationships` — độc lập với việc người đó có cạnh đồ thị hay không, vd khách chỉ nhắn riêng chưa từng chung nhóm với ai) |
| `relation` (giai đoạn quan hệ) | `direct\|via_staff\|stranger\|staff` | `core.persons.relation_to_owner` — **quyết định tự đưa ra**: spec không định nghĩa "giai đoạn quan hệ" là trường nào, đây là trường gần nghĩa nhất đã có, tránh thêm cột trùng lặp ý nghĩa với `relation_to_owner` |

```json
{"items": [{"id", "code", "name", "type", "org_name", "relation", "channels": ["zalo"],
            "heat": 87.0 | null, "potential": 40.0 | null, "risk": 20.0 | null,
            "owner_user_id": "uuid" | null, "last_interaction_at": "…" | null, "state": "active|cold",
            "degree": 4, "total_weight": 7.5, "bridge_score": 0}],
 "next_cursor", "total"}
```
`degree`/`total_weight` = **tải quan hệ** (PLAN §3.5) của người đó: số cạnh (mọi `kind`) mà người này là một
đầu, và tổng trọng số các cạnh đó, đọc thẳng từ `clean.relationships` (không giới hạn 200 — đây là con số tổng,
không phải đồ thị vẽ ra). `bridge_score` = trọng số cạnh `bridges` của người đó (0 nếu không phải cầu nối).

## Chế độ 2 — Người↔Người (`GET /graph/people`)

`GET /graph/people?node_id=&min_weight=&node_limit=` (`profile.read`, phạm vi ở **tầng cạnh**: chỉ trả cạnh mà
**cả hai đầu** trong phạm vi của người gọi — `sc.person_sql("pa") AND sc.person_sql("pb")`; một người trong
phạm vi nhưng người kia không, cạnh đó không hiện dù người đầu tiên có hiện ở `/graph/list`. **Quyết định tự
đưa ra**: PLAN chỉ nói "áp phạm vi ở tầng truy vấn cạnh", không nói quy tắc AND hay OR — chọn AND (cả hai đầu)
vì lộ cạnh nối vào một người ngoài phạm vi sẽ lộ luôn sự tồn tại/hoạt động của người đó, đúng nguyên tắc "ngoài
phạm vi → như không tồn tại" mà `ScopeFilter` dùng xuyên suốt hệ thống — hẹp hơn OR nhưng an toàn hơn):

```json
{"nodes": [{"id", "code", "name", "type"}],
 "edges": [{"from": "uuid", "to": "uuid", "weight": 1.0, "interactions": 1, "last_at": "…" | null,
            "state": "active|cold", "topic": "Thép cuộn" | null}],
 "node_limit": 200, "total_edges": 340, "truncated": true,
 "hint": "Quá nhiều node… thu hẹp bằng node_id hoặc min_weight" | (vắng khi không cắt)}
```
`node_id` (xem quanh một người) và `min_weight` thu hẹp tập cạnh trước khi dựng node. **Giới hạn ≤ 200 node**
(PLAN §3.5): cạnh được lấy `ORDER BY weight DESC`, dựng node theo thứ tự đó, bỏ qua cạnh nào sẽ đẩy tổng số
node vượt `node_limit` (mặc định + tối đa 200) — luôn là **top trọng số cao nhất** vừa khít giới hạn, không cắt
ngẫu nhiên (`gh.biz.graph.service.build_graph`). Vượt giới hạn → `truncated: true` + `hint` gợi ý thu hẹp,
không bao giờ 500.

## Chế độ 3 — Nhóm↔Nhóm (`GET /graph/groups`)

`GET /graph/groups?node_id=&min_weight=&node_limit=` (`profile.read`, phạm vi cạnh tương tự — cả hai nhóm
trong phạm vi) — cùng hình dạng `{"nodes", "edges", "node_limit", "total_edges", "truncated", "hint"?}`, node:

```json
{"id", "code", "name", "kind": "internal|market|partner|customer|private", "member_count"}
```
edge:
```json
{"from", "to", "weight", "interactions", "last_at", "state", "bridge_person_codes": ["PER-0042"]}
```
`bridge_person_codes` = mã những người có cạnh `bridges` tới **cả hai** nhóm của cạnh này — cách "nhìn được ai
là cầu nối" ngay trong chế độ Nhóm↔Nhóm (spec E4) mà không cần đổi qua danh sách; mảng rỗng khi cặp nhóm chỉ
chung nhau bởi người không-cầu-nối (còn người khác chung) hoặc không ai bắc cầu.

## Chế độ 4 — Luồng chủ đề (`GET /graph/topics`, `GET /graph/topics/{topic}`)

`GET /graph/topics?limit=` (`profile.read`) → mọi luồng, gộp theo `topic` của cạnh `interacts` (phạm vi cạnh
như chế độ 2), sắp theo tổng trọng số giảm dần:
```json
{"items": [{"topic": "Thép cuộn", "edges": 3, "people": 5, "total_weight": 8.5, "last_at": "…",
            "state": "active|cold"}]}
```
`GET /graph/topics/{topic}` → đồ thị Người↔Người **lọc theo `topic` đó** (cùng hình dạng chế độ 2, cùng giới
hạn 200 node) — "bấm vào một luồng" để xem ai đang tham gia.

## Vị trí node đã lưu

`GET /graph/layout/{mode}` (`profile.read`, `mode` ∈ `people|groups|topics`) → `{"positions": {"<node_id>":
{"x": 12.5, "y": -4.0}}}` (rỗng nếu chưa lưu).
`PUT /graph/layout/{mode}` (`profile.write`) `{"positions": {"<node_id>": {"x", "y"}}}` → lưu, ghi đè toàn bộ.

**Quyết định tự đưa ra**: tái dùng `ops.saved_views` (đã có sẵn `screen` + `filters jsonb`, không thêm bảng
mới) với `screen = 'graph'`, `name = 'layout:<mode>'` cố định, `filters = {"positions": {...}}` — một dòng
riêng mỗi người dùng × mỗi chế độ (cùng `UNIQUE (user_id, screen, lower(name))` đã có từ `0004_p3_core.sql`).
Khác `POST /views`: đây là autosave (kéo node là ghi ngay), nên `PUT` ghi đè trực tiếp qua `ON CONFLICT DO
UPDATE`, không kiểm trùng tên → không `409 VIEW_EXISTS` như góc nhìn thường. Vị trí không theo phạm vi dữ liệu
(là sở thích hiển thị của riêng người dùng, không phải dữ liệu nghiệp vụ) nên không kiểm `ensure_person`/
`ensure_group` trên từng `node_id` — id lạ chỉ đơn giản không vẽ được gì ở lần tải sau.

## Dựng lại đồ thị theo yêu cầu

`POST /graph/recompute` (`profile.write`) → chạy `recompute_org` ngay cho tổ chức của người gọi (ngoài lịch
chạy nền mỗi 15 phút), trả `{"ok": true, "counts": {"interacts", "shares_members", "owns", "bridges"}}`. Dùng
khi vừa nạp dữ liệu và muốn thấy đồ thị mới nhất ngay. Ghi Action Log (`graph.recomputed`).
