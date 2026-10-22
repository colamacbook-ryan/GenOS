-- Gen-Harness · v0.1.41 (F-84): đánh giá "Hữu ích / Không hữu ích" cho câu trả lời Gen + bảng giá model do Owner
-- nhập (VND / 1 triệu token) để tính "Chi phí AI hôm nay" theo agent.
-- - agent.gen_feedback: mỗi người một đánh giá cho mỗi lượt (PRIMARY KEY (user_id, turn_id)); xoá hội thoại (kể cả
--   hạn lưu purge_gen) ⇒ đánh giá xoá theo (ON DELETE CASCADE). kind = 'briefing' khi tin là Bản tin Gen.
-- - agent.model_prices: giá theo model của tổ chức; KHÔNG có dòng = chưa có giá (không bịa giá mặc định). Giá áp lại
--   cho lịch sử khi Owner đổi (gh/ai_cost.py tính lúc đọc, router không ghi cost_vnd).
-- - Trần chi phí mỗi ngày nằm ở core.organizations.settings->'ai_cost'->'daily_budget_vnd' — không cần cột.
-- - Chỉ mục agent.model_calls (org_id, at DESC) đã có từ 0003 — không thêm.
-- Chạy lại an toàn (IF NOT EXISTS / DROP POLICY IF EXISTS), theo khuôn 0024/0026.
CREATE TABLE IF NOT EXISTS agent.gen_feedback (
  org_id           uuid NOT NULL,
  user_id          uuid NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
  conversation_id  uuid NOT NULL REFERENCES agent.gen_conversations(id) ON DELETE CASCADE,
  turn_id          uuid NOT NULL,
  kind             text NOT NULL CHECK (kind IN ('reply', 'briefing')),
  rating           text NOT NULL CHECK (rating IN ('helpful', 'not_helpful')),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, turn_id)
);
CREATE INDEX IF NOT EXISTS gen_feedback_org_idx ON agent.gen_feedback (org_id, created_at DESC);
-- LEFT JOIN theo hội thoại khi đọc tin nhắn (store.list_messages) + xoá CASCADE theo conversation_id.
CREATE INDEX IF NOT EXISTS gen_feedback_conv_idx ON agent.gen_feedback (conversation_id, turn_id);

CREATE TABLE IF NOT EXISTS agent.model_prices (
  model_id          uuid PRIMARY KEY REFERENCES agent.models(id) ON DELETE CASCADE,
  org_id            uuid NOT NULL,
  in_vnd_per_mtok   numeric(14,2) CHECK (in_vnd_per_mtok >= 0),
  out_vnd_per_mtok  numeric(14,2) CHECK (out_vnd_per_mtok >= 0),
  updated_by        uuid,
  updated_at        timestamptz NOT NULL DEFAULT now()
);

-- RLS theo org_id như 0024/0026 (lớp phòng thủ thứ hai cho đường request web).
DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['agent.gen_feedback', 'agent.model_prices']
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
    GRANT SELECT, INSERT, UPDATE, DELETE ON agent.gen_feedback, agent.model_prices TO gh_app;
  END IF;
END $$;
