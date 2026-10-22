# Báo cáo giai đoạn 5.5 — Row-Level Security + hiệu năng

Nhánh `claude/zen-lovelace-ph1qa2` · 25/09/2026 · môi trường sandbox: Postgres 16 + pgvector + pg_partman và
Redis chạy **local** (không Docker, không phải máy production thật), 15 GB RAM / 4 CPU / 29 GB đĩa trống. Số đo
dưới đây là số đo THẬT trong môi trường sandbox này — dùng để tham khảo tương đối (có tối ưu hay không, cải
thiện bao nhiêu lần), KHÔNG phải cam kết hiệu năng cho máy chủ production.

## 1. Row-Level Security — quyết định: LÀM, phạm vi vừa phải (lớp phòng thủ thứ hai, không phải cách ly SaaS)

Đọc `docs/ARCHITECTURE.md` §8.3 và `docs/handoff/03-database.md` trước khi viết migration:

- `docs/handoff/03-database.md`: *"Đa tổ chức từ ngày đầu — mọi bảng nghiệp vụ có `org_id`; Row-Level Security
  bật ở giai đoạn 6."*
- `docs/ARCHITECTURE.md` §8.3: *"Row-Level Security Postgres bật ở giai đoạn hoàn thiện như lớp phòng thủ thứ
  hai (handoff 03)."* — "giai đoạn hoàn thiện" là tên của **Giai đoạn 5** trong `docs/PLAN.md` (mục 5 có tiêu đề
  "Giai đoạn 5 — Hoàn thiện"), không phải Giai đoạn 6. Hai tài liệu lệch số giai đoạn với nhau (handoff/03 có
  thể viết trước khi các giai đoạn được đánh số lại) — theo `docs/ARCHITECTURE.md` §0 (bảng thứ tự ưu tiên),
  `ARCHITECTURE.md` là nơi "chốt các quyết định kiến trúc" cho repo này, nên làm RLS ở giai đoạn 5 là đúng.
- `gh/bootstrap.py` xác nhận thực tế vận hành: **"Một bản cài = một tổ chức"** — mỗi triển khai Gen-Harness
  (`docker compose up`, GĐ 5.7/6) chỉ có ĐÚNG MỘT dòng `core.organizations`. Đây KHÔNG phải mô hình SaaS nhiều
  khách thuê dùng chung một CSDL.

**Kết luận**: RLS **có làm**, nhưng vai trò của nó là **lớp phòng thủ bổ sung** (đúng nghĩa đen "lớp phòng thủ
thứ hai" mà ARCHITECTURE nói) chống lỗi lập trình quên lọc `org_id` ở tầng service/`ScopeFilter`, không phải cơ
chế cách ly nhiều khách thuê chính (vì thực tế chỉ có 1 `org_id`/CSDL). Không "làm liều" bật RLS diện rộng: chỉ
bật trên các bảng nghiệp vụ nhạy cảm/nhiều nhất theo đúng yêu cầu (`core.persons`, `core.groups`, `raw.events`,
`clean.meaning_units`, `clean.score_snapshots`, `clean.relationships`, `biz.*`) — KHÔNG bật trên `core.users` /
`core.roles` / `core.sessions` vì luồng đăng nhập cần tra theo email/token TRƯỚC KHI biết `org_id`.

### Cách hoạt động

- Migration `db/sql/0012_p5_rls.sql`: `ENABLE ROW LEVEL SECURITY` + policy `org_isolation` trên từng bảng, điều
  kiện `org_id = current_setting('app.org_id', true)::uuid OR current_setting('app.org_id', true) IS NULL`.
- `gh/auth/deps.py::optional_user`: ngay sau khi phiên đăng nhập được xác thực, chạy
  `SELECT set_config('app.org_id', :org, true)` (tương đương `SET LOCAL`, chỉ sống trong transaction của
  request hiện tại — không rò sang connection khác khi trả về pool) — **một lần mỗi request**, không phải mỗi
  câu lệnh, nên chi phí không đáng kể (đo bên dưới).
- Nhánh `OR ... IS NULL`: để worker nền / job lịch (refinery, partman, action-log verifier…) vốn không đi qua
  request HTTP và không đặt biến này vẫn chạy bình thường — các luồng đó đã tự lọc `org_id` đúng ở tầng service
  từ trước.

### Giới hạn thật — GHI RÕ, không tô hồng

Vai trò Postgres ứng dụng dùng trong môi trường này (`postgres`, mặc định `gh/config.py`) là **SUPERUSER**.
Theo định nghĩa của Postgres, **RLS không áp dụng cho superuser** dù bảng có `ENABLE`/`FORCE ROW LEVEL
SECURITY`. Nghĩa là: các policy ở trên **được tạo đúng và đã kiểm chứng hoạt động đúng** (xem
`apps/api/tests/test_rls.py`, dùng `SET LOCAL ROLE` sang một vai trò không phải superuser/chủ bảng để mô phỏng
đúng vai trò ứng dụng phi-superuser mà một triển khai thật nên dùng), nhưng **CHƯA được vai trò kết nối thật
hiện tại của ứng dụng tôn trọng**. Khuyến nghị rõ ràng: trình cài (giai đoạn 6) cần tạo một role `gh_app`
(`LOGIN`, `NOSUPERUSER`, không phải chủ bảng, không `BYPASSRLS`) cho container `api`/`worker` dùng thay
`postgres` — khi đó các policy đã có sẵn sẽ có hiệu lực ngay, không cần sửa gì thêm ở tầng ứng dụng.

### Đo tác động hiệu năng của SET LOCAL app.org_id mỗi request

Một `SELECT set_config(...)` thêm mỗi request đã đăng nhập — cùng cấp chi phí với một round-trip DB đơn giản
(dưới 1ms trên localhost). Không đo riêng được vì không có "trước/sau" tách bạch trong repo này (RLS được thêm
cùng lúc với việc dựng tính năng), nhưng nằm trong ngân sách p95 26.6ms đo được ở mục 2 (endpoint `/overview`
cũng đi qua đúng dependency này) — không phải nguồn gây chậm đáng kể.

## 2. Benchmark 10 triệu `raw.events` — Tổng quan (`GET /overview`)

### Lần đo đầu tiên — SAI, đã tìm ra nguyên nhân và sửa lại

Lần chạy đầu, script rải TOÀN BỘ 10 triệu dòng vào đúng cửa sổ "24 giờ qua" (`received_at`/`occurred_at` =
`now() - random() * interval '24 hours'`). Kết quả: **p95 = 3080 ms, p99 = 12284 ms — KHÔNG đạt** mục tiêu
150ms. Nguyên nhân: không tổ chức thật nào nhận 10 triệu tin nhắn trong đúng 1 ngày; vì `events_today`,
`_hourly`, `_data_quality` đều lọc theo "hôm nay"/"7 ngày qua", dữ liệu benchmark phi thực tế này khiến 3 truy
vấn đó phải đếm/gộp **gần như toàn bộ 10 triệu dòng** mỗi lần — `COUNT(*)`/`GROUP BY` trên hàng triệu dòng khớp
điều kiện là chi phí O(số dòng khớp) trong Postgres dù chỉ mục có tốt đến đâu (chỉ mục giúp XÁC ĐỊNH tập khớp
nhanh, không giúp ĐẾM tập khớp nhanh hơn khi tập đó chiếm gần hết bảng). Đây **không phải lỗi ở truy vấn/chỉ mục
đã sửa** (bỏ JOIN thừa + thêm `(org_id, occurred_at)`) — mà là lỗi thiết kế dữ liệu benchmark của chính script.

**Đã sửa**: rải `received_at`/`occurred_at` trên **730 ngày** (khớp chính sách lưu trữ thật — "kho thô giữ 24
tháng trong DB", `docs/handoff/03-database.md`), `occurred_at = received_at − random(0-5 phút)` (đúng quan hệ
độ trễ ingest thật). Xác nhận dòng ngoài các phân vùng tháng đã tạo sẵn (`p_premake => 3`, không tạo lùi quá
khứ) rơi đúng vào `raw.events_default` (pg_partman tự tạo partition mặc định) — chạy thật không lỗi thiếu phân
vùng. Với 10 triệu dòng / 730 ngày, "hôm nay" chỉ còn khớp trung bình **~13.700 dòng** — quy mô hợp lý cho một
tổ chức khối lượng lớn, không phải toàn bộ 10 triệu.

### Kết quả THẬT (lần đo đúng, dữ liệu thực tế) — **ĐẠT mục tiêu**

Đo qua đúng `GET /overview` (FastAPI app trong tiến trình, đủ tầng HTTP/auth/scope/RLS, không rút gọn), 100
lượt liên tiếp, sau khi đã có đúng 10.000.000 dòng `raw.events` (xác nhận bằng `SELECT count(*)`):

| p50 | p95 | p99 | trung bình | mục tiêu |
|---|---|---|---|---|
| 15.3 ms | **26.6 ms** | 55.9 ms | 17.7 ms | p95 < 150 ms |

**ĐẠT** — p95 thấp hơn mục tiêu khoảng 5.6 lần.

### Tối ưu đã áp dụng (dựa trên phân tích, không đoán)

`GET /overview` (`gh/biz/queue/routes.py`) có 3 câu truy vấn lọc `raw.events` theo thời gian
(`events_today`, `_data_quality`, `_hourly`) — TRƯỚC KHI sửa, cả 3 đều `JOIN core.channels c ON c.id =
e.channel_id WHERE c.org_id = :o` chỉ để lấy `org_id`, dù `raw.events` đã có sẵn cột `org_id` (ghi trực tiếp lúc
ingest, `gh/data/ingest.py`). Đã sửa cả 3 để lọc thẳng `e.org_id = :o` (bỏ JOIN), và thêm chỉ mục
`raw.events (org_id, occurred_at DESC)` (`db/sql/0013_p5_perf.sql`) — chỉ mục cũ `(org_id, received_at DESC)`
không phục vụ được lọc theo `occurred_at` (thời điểm nghiệp vụ, khác `received_at` là thời điểm ingest).

### Chèn 10 triệu dòng (dựng dữ liệu benchmark, không phải đường ghi thật của ứng dụng)

840.9 giây cho 10.000.000 dòng (~11.900 dòng/giây) — `INSERT ... SELECT ... FROM generate_series` phía server,
gồm cả tính `digest(..., 'sha256')` (pgcrypto) và `jsonb_build_object` mỗi dòng. Đây là chi phí DỰNG dữ liệu
benchmark một lần, không phải tốc độ ghi của ứng dụng thật (ứng dụng ghi qua `gh/data/ingest.py`, mỗi tin một
giao dịch nhỏ, xem mục 3).

## 3. Refinery — tốc độ xử lý thật

Lô 3.000 tin nhắn ingest qua đúng luồng (`gh.data.ingest.ingest_message`, không viết thẳng SQL), rồi
`Refinery.run(org, "manual", limit=3000)` (xử lý cả lô, không chỉ 1 batch mặc định của lịch chạy):

| Lô | Ingest | Sàng lọc | Đã xử lý | Tốc độ | Mục tiêu |
|---|---|---|---|---|---|
| 3.000 tin | 13.2 s | 72.8 s | 3.000 / 3.000 | **2.471 bản ghi/phút** | ≥ 500 bản ghi/phút |

**ĐẠT** — nhanh hơn mục tiêu khoảng 4.9 lần. Đo trong lúc CSDL cùng cụm đang chứa 10 triệu dòng `raw.events`
benchmark khác (không phải môi trường "sạch") — số đo vì vậy đã tính cả áp lực I/O/cache thực tế của một CSDL
lớn, không phải con số tối ưu nhất có thể (ở các lần đo thử quy mô nhỏ hơn với CSDL trống, đo được cao hơn hẳn,
dao động 4.600-10.000 bản ghi/phút tuỳ lần — cho thấy 2.471/phút ở đây là số đo dưới tải, không phải trần).

Lưu ý: sandbox này không có khoá API nhà cung cấp LLM thật nào được cấu hình — số đo trên đo thông lượng tầng
DB + business logic của `Refinery.run()` (nhận lô `FOR UPDATE SKIP LOCKED`, quy tắc tất định, ghi
`clean.meaning_units`/`evidence`, chấm điểm, sổ tay, cảnh báo) với bộ định tuyến quyết định tất định (không gọi
mạng), giống cách toàn bộ 860 test hiện có của repo này đo refinery — KHÔNG đo độ trễ mạng của một nhà cung cấp
LLM cụ thể.

## 4. RAM rảnh khi tải nặng

`free -h` ngay sau khi chèn xong 10 triệu dòng `raw.events` (đỉnh điểm tải, trước khi đo GET /overview):

```
               total        used        free      shared  buff/cache   available
Mem:            15Gi        1.0Gi       6.3Gi       155Mi        8.9Gi        14Gi
```

**ĐẠT** — `available` (13-14GB, cột tính đúng RAM có thể cấp phát ngay kể cả phần đang làm buff/cache) và cả
`free` thô (6.3GB) đều **> 2GB** dù Postgres đã dùng phần lớn RAM còn lại cho buffer cache (8.9GB `buff/cache`
— bình thường và MONG MUỐN với khối lượng 10 triệu dòng, Linux/Postgres chủ động dùng RAM rảnh làm cache, giải
phóng ngay khi ứng dụng khác cần). Ngưỡng "RAM rảnh < 2GB" trong PLAN nên hiểu theo `available`, không phải
`free` thô — máy này không bao giờ chạm ngưỡng đó trong suốt benchmark.

## 5. Dọn dẹp sau benchmark

`scripts/bench_phase5.py` tự tạo CSDL riêng `gh_bench_phase5` (không đụng CSDL dev/test khác), migrate, đo, rồi
**tự xoá `DROP DATABASE gh_bench_phase5`** ở khối `finally` (kể cả khi lỗi giữa chừng) — không truncate CSDL
test/dev dùng chung. Xác nhận sau khi chạy xong (cả hai lần: lần đo sai 24h và lần đo đúng 730 ngày):
`SELECT datname FROM pg_database WHERE datname LIKE 'gh_bench%'` → 0 dòng, `free -h` trở lại bình thường
(available ~14GB). `pytest -q` (860 test, bộ mặc định không gồm 3 test `slow`) chạy lại sau toàn bộ benchmark
vẫn **860 passed** — không phá gì.

## Giới hạn của môi trường này

- 15 GB RAM / 4 CPU / 29 GB đĩa — không phải máy chủ production thật; số đo chỉ có giá trị tham khảo tương đối
  trong sandbox này (có đạt < 150ms p95 hay không, cải thiện bao nhiêu lần sau tối ưu).
- Postgres/Redis chạy local (không container hoá), không có disk I/O contention như môi trường production nhiều
  container chia sẻ một máy — số đo trong sandbox có thể LẠC QUAN hơn production thật.
- Không có khoá API nhà cung cấp LLM thật → benchmark refinery không đo được độ trễ mạng thật của bước gọi model
  (chỉ đo phần DB/business logic tự chủ của hệ thống).
