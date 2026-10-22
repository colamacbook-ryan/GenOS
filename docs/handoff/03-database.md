# 03 · Database

**PostgreSQL 16** là SSOT duy nhất. Tệp khởi điểm gốc `db/schema.sql` đã bỏ ở v0.1.48 (xem lịch sử git); lược đồ hiện hành = `db/sql/0001_baseline.sql` + các migration sau (`apps/api/migrations`). Mọi thay đổi đi qua migration có phiên bản (Alembic nếu backend Python), chạy tự động khi container `api` khởi động và được trình cài gọi lúc cài/cập nhật.

## Mục tiêu

1. **Không mất dữ liệu gốc** — kho thô chỉ INSERT; mọi kết luận truy về bản ghi thô.
2. **Mở rộng không cần khoá bảng** — thêm loại kênh, loại sự kiện, loại quy tắc, loại plugin bằng dữ liệu, không bằng migration.
3. **Truy vấn thống kê nhanh ở quy mô lớn** — phân vùng theo thời gian, lớp `analytics` riêng, materialized view làm mới đồng thời.
4. **Đa tổ chức từ ngày đầu** — mọi bảng nghiệp vụ có `org_id`; Row-Level Security bật ở giai đoạn 6.

## Schema

| Schema | Nội dung | Đặc tính |
|---|---|---|
| `core` | tổ chức, người dùng, vai trò, quyền, phiên, kênh, phiên QR, nhóm, người, danh tính đa kênh, hợp nhất | OLTP, đọc nhiều |
| `raw` | `events`, `attachments` | **chỉ INSERT**, phân vùng tháng, trigger chặn UPDATE/DELETE |
| `refinery` | quy tắc + phiên bản, trọng số, lịch chạy, lượt chạy, trạng thái xử lý từng bản ghi thô | trạng thái xử lý tách khỏi `raw` để `raw` bất biến tuyệt đối |
| `clean` | `meaning_units`, `evidence`, `score_snapshots`, `current_scores`, `relationships` | phân vùng tháng; `embedding vector(768)` |
| `memory` | `notebooks`, `entries`, `compactions` | sổ tay nhận thức theo ID người/nhóm |
| `biz` | cơ hội + lịch sử giai đoạn, cung/cầu, ghép, deal, vụ việc, việc/nhắc, tài liệu + ACL, bản nháp hành động, đánh giá + phản biện, lời hứa | OLTP |
| `agent` | danh tính agent, phạm vi kênh, provider, khoá (mã hoá), model, binding, lượt gọi model, MCP server/tool/grant/call | lượt gọi phân vùng tháng |
| `ops` | plugin, phụ thuộc, breaker, log plugin, **action log bất biến có chuỗi băm**, ranh giới chính sách, lưu trữ, yêu cầu dữ liệu, tiến độ thiết lập, góc nhìn đã lưu | |
| `analytics` | `dim_date`, materialized views | chỉ đọc với ứng dụng |

## Quy ước bắt buộc

- **Khoá chính** `uuid` sinh bằng `core.uuid_v7()` — sắp theo thời gian, chèn tuần tự, không lộ số lượng.
- **Mã công khai** (`GRP-ZL-0114`, `PER-0042`, `OPP-1842`, `ACT-0231`, `R-01`…) nằm ở cột `code`, sinh bằng `core.next_code(prefix)`. UI hiển thị `code`; API nhận cả `id` và `code`.
- **Thời gian** luôn `timestamptz`, lưu UTC, hiển thị theo `organizations.timezone` (mặc định `Asia/Ho_Chi_Minh`).
- **Tiền** là `bigint` đơn vị đồng (`value_vnd`). Không dùng float cho tiền.
- **Phân loại mở rộng** bằng `core.lookup(kind, code)` thay cho `ENUM` — thêm loại mới là một dòng INSERT.
- **Thuộc tính mở rộng** vào cột `attrs jsonb`; khi một thuộc tính được truy vấn thường xuyên, nâng thành cột thật bằng migration.
- **Không xoá cứng** hồ sơ người/nhóm: `deleted_at` (xoá mềm) hoặc `merged_into_id` (gộp). Xoá thật chỉ qua `ops.data_requests` (quyền xoá theo spec) và phải ghi action log.
- **Không ghi đè điểm số**: `clean.score_snapshots` giữ lịch sử; `clean.current_scores` chỉ là bộ đệm đọc nhanh.
- **Chạy lại quy tắc** không xoá kết luận cũ: bản mới trỏ qua `superseded_by`. Mọi truy vấn nghiệp vụ lọc `superseded_by IS NULL`.
- **Bí mật** (khoá API, phiên kênh, TOTP, auth MCP) lưu `bytea` mã hoá phong bì AES-256-GCM. Khoá master nằm ở Docker secret do trình cài sinh, không bao giờ ở DB hay repo.
- **Action log**: mỗi dòng tính `row_hash = sha256(prev_hash || nội dung chuẩn hoá)`. Job hằng đêm kiểm chuỗi và báo lên hàng đợi nếu đứt.

## Phân vùng & lưu trữ

- `pg_partman` tạo trước 3 phân vùng tháng cho `raw.events`, `clean.meaning_units`, `clean.score_snapshots`, `agent.model_calls`, `agent.mcp_calls`, `ops.breaker_events`, `ops.action_log`; `ops.plugin_logs` theo tuần, giữ 30 ngày.
- Job `partman.run_maintenance()` chạy mỗi giờ trong container `worker`.
- `ops.retention_policies` quyết định phân vùng cũ bị **tách ra và nén thành Parquet** vào object store (MinIO) hay xoá. Mặc định: giữ vĩnh viễn kho sạch và action log; kho thô giữ 24 tháng trong DB rồi lưu trữ lạnh.

## Luồng ghi (khớp nguyên tắc spec)

```
bridge ─INSERT→ raw.events ─(NOTIFY raw_ingested)→ refinery worker
refinery worker: SELECT … FROM refinery.event_state WHERE state='pending' FOR UPDATE SKIP LOCKED LIMIT batch_size
   ├─ áp quy tắc (rule_versions) + LLM → clean.meaning_units + clean.evidence
   ├─ cập nhật refinery.event_state (clean | lowconf | discarded | error)
   ├─ tính clean.score_snapshots → clean.current_scores
   ├─ cập nhật clean.relationships, biz.market_signals, biz.opportunities
   └─ ghi memory.entries cho sổ tay của các ID liên quan
Kích hoạt: interval_seconds HOẶC count_threshold (refinery.schedule), cái nào tới trước.
```

Agent trực kênh đọc (không ghi trực tiếp vào clean): `memory.notebooks/entries` của ID người + ID nhóm, `clean.meaning_units` liên quan trong cửa sổ thời gian, `clean.current_scores`. Phản hồi của agent đi ra như mọi tin nhắn khác — quay lại `raw.events` qua bridge — nên cũng được sàng lọc và lưu vết.

## Truy vấn thống kê

Ứng dụng đọc chỉ số tổng hợp từ `analytics.*`, không quét bảng thô lúc tải trang.

| View | Phục vụ | Làm mới |
|---|---|---|
| `mv_daily_group_activity` | Nhóm & Con người, Kho thô theo nhóm | 15 phút |
| `mv_hourly_activity` | Nhiệt kế hoạt động (Tổng quan) | 5 phút |
| `mv_daily_meaning` | Kho sạch, tín hiệu nổi | 15 phút |
| `mv_opportunity_funnel` | Bảng cơ hội, chỉ số F4 "tín hiệu → tiếp cận", "chưa có người nhận" | 5 phút |
| `mv_model_usage_daily` | API & Model, quota theo model, chi phí | 5 phút |

Bổ sung khi dựng màn tương ứng: `mv_care_response_hourly` (lưới phản hồi theo khung giờ), `mv_care_patterns_30d`, `mv_people_load` (tải quan hệ theo người), `mv_mcp_calls_daily`. Tất cả `REFRESH MATERIALIZED VIEW CONCURRENTLY` (cần unique index — đã có ở các view mẫu).

Nếu dữ liệu vượt ~50 triệu sự kiện/tháng: bật TimescaleDB (hypertable + continuous aggregates) thay pg_partman cho các bảng thời gian — schema được thiết kế để chuyển không đổi API.

## Index

- B-tree tổ hợp theo mẫu truy vấn chính: `(org_id, …, thời gian DESC)`.
- `gin_trgm_ops` cho tìm tên người và nội dung thô.
- `jsonb_path_ops` cho `clean.meaning_units.entities` (lọc theo mặt hàng, giá, địa điểm ở Kho hội thoại và Cung ↔ Cầu).
- HNSW trên `embedding` từng phân vùng, tạo bởi job bảo trì khi phân vùng mới xuất hiện.
- Partial index cho hàng đợi: `refinery.event_state WHERE state IN ('pending','lowconf')`.

## Seed

`design/seed-data.json` chứa 115 tập dữ liệu hiển thị trích từ thiết kế. Viết `db/seed/` ánh xạ từng tập vào bảng tương ứng (ví dụ `rawRows` → `raw.events` + `refinery.event_state`, `peopleRows` → `core.persons` + `person_identities` + `clean.current_scores`, `refineryRules` → `refinery.rules` + `rule_versions`). Seed chỉ chạy khi Owner chọn "Dùng dữ liệu mẫu" ở bước 1 của trình thiết lập, và có lệnh xoá sạch dữ liệu mẫu.

## Kiểm thử database

- Test migration lên/xuống trên DB trống và trên DB đã seed.
- Test trigger chỉ-INSERT (`UPDATE raw.events` phải lỗi).
- Test chuỗi băm action log.
- Test RLS: user tổ chức A không đọc được dòng tổ chức B.
- Benchmark: 10 triệu `raw.events` giả lập, các truy vấn màn Tổng quan < 150ms p95.
