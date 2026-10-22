-- Gen-Harness · giai đoạn 3 · Cơ hội & Thị trường (Bảng cơ hội, Cung ↔ Cầu, Kho hội thoại, Deal & Vụ việc)
-- Giữ quy ước docs/handoff/03-database.md: UUIDv7, org_id, timestamptz, không ENUM.
--
-- Mọi bảng gốc đã có từ giai đoạn 1: biz.opportunities, biz.opportunity_stage_history, biz.market_signals,
-- biz.matches, biz.deals, biz.cases (xem docs/handoff/schema.sql — đã bỏ ở v0.1.48, xem lịch sử git; nay ở
-- 0001_baseline.sql). Cụm này chỉ ALTER cột còn thiếu + thêm chỉ
-- mục cho truy vấn của cụm — không tạo lại bảng nào. `biz.cases` trước đây chưa cụm nào dùng (queue dùng
-- `biz.alerts` cho cảnh báo sớm — khác bảng, không đụng tới).

-- `biz.deals` thiếu created_at/updated_at (cần cho phân trang con trỏ + theo dõi sửa).
ALTER TABLE biz.deals
  ADD COLUMN created_at timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

-- `biz.cases` thiếu updated_at (đổi trạng thái/người xử lý nhiều lần trước khi đóng).
ALTER TABLE biz.cases
  ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

-- `biz.matches` thiếu updated_at (chấm lại điểm định kỳ, đổi trạng thái khi giới thiệu/từ chối).
ALTER TABLE biz.matches
  ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

-- Idempotent khi hook sau sàng lọc chạy lại / retry: một đơn vị ý nghĩa chỉ sinh một tín hiệu cung/cầu.
CREATE UNIQUE INDEX ON biz.market_signals (meaning_unit_id) WHERE meaning_unit_id IS NOT NULL;

CREATE INDEX ON biz.matches (org_id, status);
CREATE INDEX ON biz.matches (demand_id);
CREATE INDEX ON biz.matches (supply_id);

CREATE INDEX ON biz.opportunities (org_id, owner_user_id);

CREATE INDEX ON biz.deals (org_id, status);
CREATE INDEX ON biz.deals (person_id);

CREATE INDEX ON biz.cases (org_id, kind, status);
CREATE INDEX ON biz.cases (assignee_user_id);

-- Kho hội thoại: tìm theo từ khoá trên kết luận + JSON thực thể, cộng ngữ nghĩa qua embedding đã có sẵn từ
-- giai đoạn 2 (`clean.meaning_units.embedding`, cột `vector(768)` — xem `gh.refinery.runner._embed`). Không
-- thêm chỉ mục full-text/HNSW mới ở đây: chỉ mục vector đã được job bảo trì tạo theo từng phân vùng (ghi chú ở
-- `0001_baseline.sql`); tìm từ khoá dùng ILIKE trên tập đã lọc theo phạm vi/facet trước (đủ nhanh ở quy mô một
-- tổ chức, không cần GIN trigram cho bản này).
