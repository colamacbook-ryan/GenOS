-- Gen-Harness · v0.1.40 (F-2, F-16): hạn lưu thật + job nặng.
-- - F-16: bảng mốc tiến độ `ops.job_watermarks` (dò trùng chỉ xét định danh mới/hồ sơ vừa sửa; quét đủ mỗi ngày
--   một lần — cột full_at), similarity_op LEAKPROOF để toán tử `%` dùng được chỉ mục gin sẵn có trên display_name.
-- - F-2: chỉ mục hỗ trợ dọn theo lô (gh/retention.py): browser_jobs.result 14 ngày, mục sổ tay đã nén,
--   tệp đính kèm của sự kiện thô đã bị partman xoá theo tháng.
-- - F-2: ops.retention_policies.confirmed_at — hạn lưu đặt TRƯỚC v0.1.40 chỉ để hiển thị ("Chưa tự xoá — sẽ áp dụng
--   ở bản sau") ⇒ để NULL (chưa xác nhận) và việc dọn KHÔNG thi hành dòng chưa xác nhận; Owner phải lưu lại (có cảnh
--   báo xoá vĩnh viễn) thì mới bắt đầu xoá.
-- Chạy lại an toàn (IF NOT EXISTS / DROP POLICY IF EXISTS), theo khuôn 0024.
-- Bản nháp đầu của 0026 tạo chỉ mục trigram trên lower(display_name) — dưới RLS không dùng được (lower() không
-- leakproof) mà vẫn tốn ghi ⇒ bỏ.
DROP INDEX IF EXISTS core.persons_lower_name_trgm_idx;
ALTER TABLE ops.retention_policies ADD COLUMN IF NOT EXISTS confirmed_at timestamptz;
-- Dò tên chạy dưới role gh_app ⇒ RLS của core.persons là "security barrier": chỉ phép so LEAKPROOF mới được đẩy vào
-- điều kiện chỉ mục. lower() (pg_catalog) không leakproof nên dò dùng chỉ mục gin sẵn có trên display_name (0001;
-- pg_trgm tự gộp hoa/thường khi tách trigram ⇒ `display_name % x` ≡ `lower(display_name) % lower(x)`) và đánh dấu
-- similarity_op (toán tử `%`) LEAKPROOF — hàm chỉ tính trigram, không ném lỗi theo giá trị đầu vào. Cần superuser;
-- thiếu quyền chỉ ghi NOTICE (dò vẫn đúng, chỉ chậm hơn). pg_dump/pg_restore KHÔNG giữ LEAKPROOF của hàm thuộc
-- extension ⇒ worker `partition_maintenance` (admin) đặt lại sau `genh import` (gh/retention.ensure_leakproof).
-- fastupdate = off: không có "pending list" (danh sách chờ quét tuần tự mỗi lần dò) — hồ sơ mới chèn ít, dò tên
-- chạy mỗi 10 phút nên ưu tiên dò ổn định.
ALTER INDEX IF EXISTS core.persons_display_name_idx SET (fastupdate = off);
DO $$
BEGIN
  IF to_regprocedure('public.similarity_op(text, text)') IS NOT NULL THEN
    ALTER FUNCTION public.similarity_op(text, text) LEAKPROOF;
  END IF;
EXCEPTION WHEN insufficient_privilege THEN
  RAISE NOTICE 'Bỏ qua LEAKPROOF cho similarity_op: thiếu quyền superuser';
END $$;

CREATE TABLE IF NOT EXISTS ops.job_watermarks (
  org_id      uuid NOT NULL,
  job         text NOT NULL,
  last_id     uuid,
  last_at     timestamptz,
  full_at     timestamptz,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, job)
);
ALTER TABLE ops.job_watermarks ADD COLUMN IF NOT EXISTS full_at timestamptz;

ALTER TABLE ops.job_watermarks ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON ops.job_watermarks;
CREATE POLICY org_isolation ON ops.job_watermarks
  USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '')
  WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '');

CREATE INDEX IF NOT EXISTS browser_jobs_finished_result_idx ON agent.browser_jobs (finished_at)
  WHERE result IS NOT NULL;
CREATE INDEX IF NOT EXISTS memory_entries_archived_idx ON memory.entries (archived_at)
  WHERE archived_at IS NOT NULL AND NOT is_pinned;
CREATE INDEX IF NOT EXISTS attachments_event_received_idx ON raw.attachments (event_received_at);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gh_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON ops.job_watermarks TO gh_app;
  END IF;
END $$;
