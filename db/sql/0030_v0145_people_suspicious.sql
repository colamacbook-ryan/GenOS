-- Gen-Harness · v0.1.45 (F-60): cờ "Đáng ngờ" cho điểm đánh giá nhân sự.
-- Nhân viên có thể chèn câu lệnh cho AI / xin điểm vào tin nhắn để lách điểm. Job tính điểm chỉ GẮN CỜ
-- (không đổi điểm, không kỷ luật tự động — khoá cứng 2); Sếp xem chứng cứ trước khi dùng điểm.
-- Sếp xem chứng cứ thấy báo nhầm thì BỎ CỜ (có lý do, ghi Nhật ký): suspicious_cleared_* ghi ai bỏ, lúc nào, vì sao;
-- cột `suspicious` giữ nguyên kết quả quét để còn đối chiếu (giống overridden_by — không khoá ngoại).
-- Chạy lại an toàn (IF NOT EXISTS).
ALTER TABLE biz.people_reviews
    ADD COLUMN IF NOT EXISTS suspicious boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS suspicious_reason text,
    ADD COLUMN IF NOT EXISTS suspicious_cleared_by uuid,
    ADD COLUMN IF NOT EXISTS suspicious_cleared_at timestamptz,
    ADD COLUMN IF NOT EXISTS suspicious_cleared_reason text;
