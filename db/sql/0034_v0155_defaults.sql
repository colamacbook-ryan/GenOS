-- Gen-Harness · v0.1.55 (G1 mac-dinh): hồ sơ tiêu chuẩn theo vai + "Về mặc định" — mức suy nghĩ riêng của từng dòng gán model.
-- Chạy lại an toàn (IF NOT EXISTS / khối DO kiểm trước), theo khuôn 0023 (models_effort_check). Chỉ THÊM cột:
-- KHÔNG xoá, KHÔNG sửa dòng nào — dòng agent.bindings cũ giữ nguyên từng byte (effort = NULL ⇒ "không chọn mức riêng":
-- bộ định tuyến dùng mức của hồ sơ tiêu chuẩn rồi mới tới agent.models.effort). Bản cài cũ: dòng gán đã có vẫn là
-- "Đã tuỳ chỉnh" (Owner từng chọn), Console gợi ý "Áp model chuẩn theo vai?" (gh.defaults.registry.suggestions).
-- Không tạo bảng mới ⇒ không cần GRANT mới (gh_app đã có quyền DML trên agent.bindings từ migration 0014; quyền bảng
-- bao trùm cột mới).
ALTER TABLE agent.bindings ADD COLUMN IF NOT EXISTS effort text;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'bindings_effort_check') THEN
    ALTER TABLE agent.bindings ADD CONSTRAINT bindings_effort_check
      CHECK (effort IS NULL OR effort IN ('low', 'medium', 'high', 'xhigh', 'max'));
  END IF;
END $$;
