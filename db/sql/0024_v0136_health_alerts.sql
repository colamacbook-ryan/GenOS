-- Gen-Harness · v0.1.36 (F-6): sự cố đang mở — nguồn sự thật cho chuông tự khử trùng lặp và dải "Cần Sếp xử lý".
-- Mỗi sự cố là MỘT dòng theo (org_id, key) — vd. 'channel.down:zalo', 'model.auth_expired:<provider_id>',
-- 'update.failed', 'backup.stale', 'worker.silent', 'disk.low'. `cleared_at` NULL = đang mở.
-- Nguồn ghi: gh/health.py::raise_once (chỉ gửi chuông khi dòng MỚI mở hoặc fingerprint đổi) và ::clear.
-- Chạy lại an toàn (IF NOT EXISTS / DROP POLICY IF EXISTS), theo khuôn 0017.
CREATE TABLE IF NOT EXISTS ops.health_alerts (
  org_id       uuid NOT NULL,
  key          text NOT NULL,
  kind         text NOT NULL,
  severity     text NOT NULL CHECK (severity IN ('bad', 'warn')),
  fingerprint  text NOT NULL DEFAULT '',
  title        text NOT NULL,
  body         text NOT NULL DEFAULT '',
  link         text,
  raised_at    timestamptz NOT NULL DEFAULT now(),
  cleared_at   timestamptz,
  PRIMARY KEY (org_id, key)
);
CREATE INDEX IF NOT EXISTS health_alerts_open_idx ON ops.health_alerts (org_id) WHERE cleared_at IS NULL;

-- RLS theo org_id như 0017 (lớp phòng thủ thứ hai cho đường request web).
ALTER TABLE ops.health_alerts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON ops.health_alerts;
CREATE POLICY org_isolation ON ops.health_alerts
  USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '')
  WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '');

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gh_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON ops.health_alerts TO gh_app;
  END IF;
END $$;
