-- Gen-Harness · v0.1.67: "Agent làm và nói thật" (gói B7 + C6).
-- (1) B7 / F085 — Lưu trữ agent: cột `archived_at` trên agent.identities. Lưu trữ = ẩn khỏi danh sách và bảng Gán model,
--     tắt hẳn; KHÔNG xoá dòng (agent.decisions còn khoá ngoại tới agent). NULL = đang dùng.
-- Chạy lại an toàn: IF NOT EXISTS, không đổi dữ liệu.
ALTER TABLE agent.identities ADD COLUMN IF NOT EXISTS archived_at timestamptz;
