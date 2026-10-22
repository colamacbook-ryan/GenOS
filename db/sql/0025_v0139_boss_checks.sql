-- Gen-Harness · v0.1.39: kết quả kiểm thật từng dòng của trang "Việc Sếp cần làm" (Gen-hub, Facebook, Google /
-- Antigravity, Claude Code CLI, Jev). Mỗi lần bấm Kiểm tra / Gọi thử / Đổi tài khoản = MỘT dòng; giữ 50 dòng mới nhất
-- mỗi (org_id, check_key) — gh/boss_checks/service.py::record tự dọn.
-- KHÔNG lưu token, mật khẩu, cookie hay giá trị mã đăng nhập; email chỉ ở dạng che (b***@tên-miền) trong `detail`.
-- Chạy lại an toàn (IF NOT EXISTS / DROP POLICY IF EXISTS), theo khuôn 0024.
CREATE TABLE IF NOT EXISTS ops.boss_checks (
  id          uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id      uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  check_key   text NOT NULL CHECK (check_key ~ '^[a-z_]{2,32}$'),
  status      text NOT NULL CHECK (status IN ('pass', 'fail', 'pending')),
  error_code  text,
  message     text,
  detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
  ref_id      uuid,
  checked_by  uuid,
  checked_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS boss_checks_key_idx ON ops.boss_checks (org_id, check_key, checked_at DESC);

-- RLS theo org_id như 0024 (lớp phòng thủ thứ hai cho đường request web).
ALTER TABLE ops.boss_checks ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON ops.boss_checks;
CREATE POLICY org_isolation ON ops.boss_checks
  USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '')
  WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '');

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gh_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON ops.boss_checks TO gh_app;
  END IF;
END $$;
