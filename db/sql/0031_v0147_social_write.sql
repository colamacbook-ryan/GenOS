-- Gen-Harness · v0.1.47 (F-79, F-85): ghi mạng xã hội CÓ XÁC NHẬN (trả lời bình luận / nhắn tin) qua đề xuất của Gen
-- + PIN + permit ký. Chạy lại an toàn (IF NOT EXISTS / DROP ... IF EXISTS), theo khuôn 0029/0030.
-- a) agent.browser_jobs: thêm loại việc 'write' + cột action / proof_key / proof_sha256 (ảnh chụp bằng chứng được MÃ HOÁ
--    ở object store, chỉ lưu khoá + sha256 ở đây; KHÔNG có nội dung ảnh trong CSDL).
ALTER TABLE agent.browser_jobs DROP CONSTRAINT IF EXISTS browser_jobs_kind_check;
ALTER TABLE agent.browser_jobs ADD CONSTRAINT browser_jobs_kind_check CHECK (kind IN ('login', 'health', 'read', 'write'));
ALTER TABLE agent.browser_jobs ADD COLUMN IF NOT EXISTS action text
  CHECK (action IS NULL OR action IN ('reply_comment', 'send_message'));
ALTER TABLE agent.browser_jobs ADD COLUMN IF NOT EXISTS proof_key text;
ALTER TABLE agent.browser_jobs ADD COLUMN IF NOT EXISTS proof_sha256 text;
CREATE INDEX IF NOT EXISTS browser_jobs_write_idx ON agent.browser_jobs (account_id, created_at DESC)
  WHERE kind = 'write';

-- b) Trần lượt gửi/ngày của từng tài khoản (Owner chỉ chỉnh XUỐNG; trần cứng WRITES_PER_DAY_MAX = 20 nằm trong code).
ALTER TABLE core.social_accounts ADD COLUMN IF NOT EXISTS daily_write_limit smallint NOT NULL DEFAULT 10
  CHECK (daily_write_limit BETWEEN 1 AND 20);

-- c) Đồng ý rủi ro theo chủ đề (F-85: trình duyệt nền chạy không có sandbox của Chromium) — ai, lúc nào, bản cảnh báo nào.
CREATE TABLE IF NOT EXISTS ops.risk_consents (
  org_id       uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  topic        text NOT NULL CHECK (topic ~ '^[a-z_]{2,40}$'),
  version      text NOT NULL,
  accepted_by  uuid NOT NULL,
  accepted_at  timestamptz NOT NULL DEFAULT now(),
  revoked_at   timestamptz,
  revoked_by   uuid,
  PRIMARY KEY (org_id, topic)
);

ALTER TABLE ops.risk_consents ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON ops.risk_consents;
CREATE POLICY org_isolation ON ops.risk_consents
  USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '')
  WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '');

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gh_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON ops.risk_consents TO gh_app;
  END IF;
END $$;
