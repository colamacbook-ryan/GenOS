-- Gen-Harness · giai đoạn 5.5 — chỉ mục cho benchmark 10 triệu raw.events (Tổng quan p95 < 150ms)
--
-- GET /overview (gh/biz/queue/routes.py) lọc raw.events trực tiếp bằng e.org_id + e.occurred_at (đã sửa để
-- không JOIN core.channels chỉ để lấy org_id — cột đã có sẵn trên chính raw.events, xem commit liên quan).
-- Chỉ mục hiện có (org_id, received_at DESC) không phục vụ được lọc theo occurred_at (thời điểm nghiệp vụ,
-- khác received_at là thời điểm ingest) — thêm chỉ mục riêng. Xem docs/reports/phase-5-performance.md để có
-- số đo EXPLAIN ANALYZE trước/sau trên 10 triệu dòng.

CREATE INDEX IF NOT EXISTS events_org_occurred_idx ON raw.events (org_id, occurred_at DESC);
