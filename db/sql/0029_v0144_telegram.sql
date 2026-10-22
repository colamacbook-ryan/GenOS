-- Gen-Harness · v0.1.44 (F-8c, F-6b): kênh "Báo động & bản tin" qua Telegram Bot API chính thức (một chiều).
-- `ops.notify_channels`: MỘT cấu hình mỗi tổ chức — token bot MÃ HOÁ (`token_enc`, AAD b"telegram_token", có trong
-- gh/bundle.py::REENCRYPT_TARGETS), chat_id dạng số. Không bao giờ lưu token dạng rõ.
-- `ops.telegram_outbox`: hàng đợi tin bản tin/nhắc việc (worker `telegram_flush` gửi mỗi phút). Ghi trong CÙNG
-- transaction với việc gây ra; khử trùng lặp theo (org_id, dedupe_key). KHÔNG chứa token.
-- Chạy lại an toàn (IF NOT EXISTS / DROP POLICY IF EXISTS), theo khuôn 0025.
CREATE TABLE IF NOT EXISTS ops.notify_channels (
  org_id        uuid PRIMARY KEY REFERENCES core.organizations(id) ON DELETE CASCADE,
  kind          text NOT NULL DEFAULT 'telegram' CHECK (kind = 'telegram'),
  token_enc     bytea NOT NULL,
  chat_id       text NOT NULL CHECK (chat_id ~ '^-?[0-9]{1,20}$'),
  bot_username  text,
  enabled       boolean NOT NULL DEFAULT true,
  briefing      boolean NOT NULL DEFAULT true,
  reminders     boolean NOT NULL DEFAULT true,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  updated_by    uuid
);

CREATE TABLE IF NOT EXISTS ops.telegram_outbox (
  id               uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id           uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  kind             text NOT NULL CHECK (kind IN ('briefing', 'reminder')),
  dedupe_key       text,
  text             text NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  next_attempt_at  timestamptz NOT NULL DEFAULT now(),
  attempts         integer NOT NULL DEFAULT 0,
  sent_at          timestamptz,
  failed_code      text
);
CREATE UNIQUE INDEX IF NOT EXISTS telegram_outbox_dedupe_uq ON ops.telegram_outbox (org_id, dedupe_key);
CREATE INDEX IF NOT EXISTS telegram_outbox_pending_idx ON ops.telegram_outbox (next_attempt_at)
  WHERE sent_at IS NULL AND failed_code IS NULL;

-- RLS theo org_id như 0024/0025 (lớp phòng thủ thứ hai cho đường request web).
ALTER TABLE ops.notify_channels ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON ops.notify_channels;
CREATE POLICY org_isolation ON ops.notify_channels
  USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '')
  WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '');

ALTER TABLE ops.telegram_outbox ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON ops.telegram_outbox;
CREATE POLICY org_isolation ON ops.telegram_outbox
  USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '')
  WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '');

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gh_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON ops.notify_channels TO gh_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ops.telegram_outbox TO gh_app;
  END IF;
END $$;
