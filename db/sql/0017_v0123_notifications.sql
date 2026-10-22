-- Gen-Harness · v0.1.23 — Đợt B6: chuông thông báo ở header.
-- Mỗi dòng thuộc ĐÚNG một người nhận (user_id); API chỉ trả thông báo của chính người đang đăng nhập.
-- Nguồn ghi: gh/notifications.py::notify (đổi vai trò / đặt lại mật khẩu / mở khoá tài khoản, sao lưu xong/lỗi …).
CREATE TABLE IF NOT EXISTS core.notifications (
  id          uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id      uuid NOT NULL,
  user_id     uuid NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
  kind        text NOT NULL,
  title       text NOT NULL,
  body        text NOT NULL DEFAULT '',
  link        text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  read_at     timestamptz
);
CREATE INDEX IF NOT EXISTS notifications_user_idx ON core.notifications (org_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS notifications_unread_idx ON core.notifications (user_id) WHERE read_at IS NULL;

-- RLS theo org_id như 0012/0016 (lớp phòng thủ thứ hai cho đường request web).
ALTER TABLE core.notifications ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON core.notifications;
CREATE POLICY org_isolation ON core.notifications
  USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '')
  WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '');

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gh_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON core.notifications TO gh_app;
  END IF;
END $$;
