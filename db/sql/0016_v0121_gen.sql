-- Gen-Harness · v0.1.21 — Gen v1 (docs/design/gen-v1.md §4): lưu hội thoại khung chat Gen.
-- Chỉ chủ hội thoại đọc được (lọc user_id ở tầng API — Owner KHÔNG đọc nội dung chat của nhân viên, §9.3).
-- Hạn lưu mặc định 90 ngày (§9.4): job `gh.gen.store.purge_expired` trong worker xoá hội thoại có last_at quá hạn.
CREATE TABLE IF NOT EXISTS agent.gen_conversations (
  id          uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id      uuid NOT NULL,
  user_id     uuid NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
  title       text NOT NULL DEFAULT '',
  created_at  timestamptz NOT NULL DEFAULT now(),
  last_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS gen_conversations_user_idx ON agent.gen_conversations (org_id, user_id, last_at DESC);
CREATE INDEX IF NOT EXISTS gen_conversations_last_idx ON agent.gen_conversations (last_at);

CREATE TABLE IF NOT EXISTS agent.gen_messages (
  id              uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id          uuid NOT NULL,
  conversation_id uuid NOT NULL REFERENCES agent.gen_conversations(id) ON DELETE CASCADE,
  turn_id         uuid,
  role            text NOT NULL CHECK (role IN ('user', 'assistant')),
  content         jsonb NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS gen_messages_conv_idx ON agent.gen_messages (conversation_id, created_at);

-- RLS theo org_id như 0012 (lớp phòng thủ thứ hai cho đường request web).
DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['agent.gen_conversations', 'agent.gen_messages']
  LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS org_isolation ON %s', t);
    EXECUTE format($p$
      CREATE POLICY org_isolation ON %s
        USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
               OR current_setting('app.org_id', true) IS NULL
               OR current_setting('app.org_id', true) = '')
        WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
               OR current_setting('app.org_id', true) IS NULL
               OR current_setting('app.org_id', true) = '')
    $p$, t);
  END LOOP;
END $$;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gh_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON agent.gen_conversations, agent.gen_messages TO gh_app;
  END IF;
END $$;
