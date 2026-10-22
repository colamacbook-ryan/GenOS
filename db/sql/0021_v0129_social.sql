-- Gen-Harness · v0.1.29 — Đợt D3 (lát đầu): tài khoản mạng xã hội + việc trình duyệt (docs/design/gen-browser-agent.md
-- §5.1). Chỉ ĐỌC (thông báo, danh sách hội thoại); ghi (đăng/trả lời/nhắn) để v0.1.30 qua đề xuất + permit.
--
-- `state_enc`: phiên đăng nhập (cookie + localStorage, Playwright storageState) mã hoá phong bì bằng khoá master
-- (gh.crypto.encrypt, AAD `social:<org_id>:<account_id>`). KHÔNG lưu mật khẩu/mã 2FA — Owner tự gõ trong cửa sổ trình
-- duyệt từ xa. Gỡ tài khoản = xoá `state_enc` + xoá nội dung đã đọc của các việc (`browser_jobs.result`).
CREATE TABLE IF NOT EXISTS core.social_accounts (
  id                uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id            uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  platform          text NOT NULL,                                   -- gh.social.platforms (vd facebook_personal)
  mode              text NOT NULL DEFAULT 'browser' CHECK (mode IN ('api', 'browser')),
  label             text NOT NULL,
  external_handle   text,
  status            text NOT NULL DEFAULT 'pending_login'
                      CHECK (status IN ('pending_login', 'active', 'needs_login', 'paused', 'revoked')),
  pause_reason      text,
  state_enc         bytea,
  state_updated_at  timestamptz,
  last_health       jsonb,
  risk_accepted_by  uuid,
  risk_accepted_at  timestamptz,
  risk_version      text,
  -- Lịch đọc tự động: TẮT mặc định; Owner bật và chọn giờ (theo múi giờ tổ chức).
  schedule          jsonb NOT NULL DEFAULT '{"enabled": false, "times": ["08:00", "17:00"]}'::jsonb,
  -- Owner chỉ chỉnh XUỐNG; trần cứng nằm trong code (gh.social.service.READS_PER_DAY_MAX).
  daily_read_limit  smallint NOT NULL DEFAULT 6 CHECK (daily_read_limit BETWEEN 1 AND 6),
  fail_streak       smallint NOT NULL DEFAULT 0,
  last_read_at      timestamptz,
  created_by        uuid,
  created_at        timestamptz NOT NULL DEFAULT now(),
  revoked_at        timestamptz,
  revoked_by        uuid
);
CREATE INDEX IF NOT EXISTS social_accounts_org_idx ON core.social_accounts (org_id, created_at);

CREATE TABLE IF NOT EXISTS agent.browser_jobs (
  id            uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id        uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  account_id    uuid NOT NULL REFERENCES core.social_accounts(id) ON DELETE CASCADE,
  kind          text NOT NULL CHECK (kind IN ('login', 'health', 'read')),
  status        text NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'running', 'done', 'failed', 'halted', 'cancelled')),
  requested_by  uuid,
  via           text NOT NULL DEFAULT 'user' CHECK (via IN ('gen', 'user', 'schedule')),
  result        jsonb,
  error         text,
  cost          jsonb,
  created_at    timestamptz NOT NULL DEFAULT now(),
  started_at    timestamptz,
  finished_at   timestamptz
);
CREATE INDEX IF NOT EXISTS browser_jobs_account_idx ON agent.browser_jobs (account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS browser_jobs_active_idx ON agent.browser_jobs (org_id, status)
  WHERE status IN ('queued', 'running');

-- RLS theo org_id như 0012/0016/0017/0019/0020 (lớp phòng thủ thứ hai cho đường request web).
ALTER TABLE core.social_accounts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON core.social_accounts;
CREATE POLICY org_isolation ON core.social_accounts
  USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '')
  WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '');

ALTER TABLE agent.browser_jobs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON agent.browser_jobs;
CREATE POLICY org_isolation ON agent.browser_jobs
  USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '')
  WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '');

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gh_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON core.social_accounts TO gh_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON agent.browser_jobs TO gh_app;
  END IF;
END $$;
