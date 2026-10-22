-- Gen-Harness · v0.1.19 — "Tài khoản của tôi"
-- Cờ buộc đổi mật khẩu: bật khi mật khẩu do hệ thống đặt (genh reset-password → gh.auth.reset_owner);
-- tắt khi chính người dùng đổi mật khẩu qua POST /account/password. Console chặn mọi màn hình khác tới khi đổi.
ALTER TABLE core.users ADD COLUMN IF NOT EXISTS must_change_password boolean NOT NULL DEFAULT false;
