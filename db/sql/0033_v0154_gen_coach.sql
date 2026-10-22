-- Gen-Harness · v0.1.54 (g1-api): "Gen hướng dẫn" — việc vận hành Sếp cần làm, mẹo "Sếp biết chưa?", bài học hằng ngày.
-- Chạy lại an toàn (IF NOT EXISTS / DROP ... IF EXISTS), theo khuôn 0032. Mỗi hàng thuộc đúng MỘT Owner (user_id) — Gen
-- hướng dẫn chỉ dành cho Owner; Gen KHÔNG gọi model và KHÔNG ghi gen_messages khi dựng thẻ (gh/gen/coach).
-- Hai bảng KHÔNG thuộc purge_gen (hạn lưu hội thoại Gen): chọn "Tắt hướng dẫn"/"Để mai" của Sếp phải sống lâu hơn hội thoại.
-- a) agent.gen_coach_prefs: tuỳ chọn + dấu mốc của từng Owner.
--    stable_since: mốc bắt đầu chuỗi ngày KHÔNG có việc P0/P1 và không có sự cố mới (>= 7 ngày ⇒ "Hệ thống đã ổn định").
--    last_seen_at: lần cuối Sếp XEM thẻ (GET /today?mark_shown=1). last_bell_at/last_bell_keys: cổng chuông `gen.coach`
--    (tối đa 1 chuông/Owner/ngày — claim_bell là MỘT câu UPDATE nguyên tử).
--    Hoãn thẻ "Việc thiết lập tiếp" lưu như một mục của gen_coach_items (item_key 'card:setup_followup') và hiện ở GET /prefs.
CREATE TABLE IF NOT EXISTS agent.gen_coach_prefs (
  user_id          uuid PRIMARY KEY REFERENCES core.users(id) ON DELETE CASCADE,
  org_id           uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  enabled          boolean NOT NULL DEFAULT true,
  bell             boolean NOT NULL DEFAULT true,
  lessons_per_day  smallint NOT NULL DEFAULT 1 CHECK (lessons_per_day BETWEEN 0 AND 2),
  quiet_start      smallint NOT NULL DEFAULT 22 CHECK (quiet_start BETWEEN 0 AND 23),
  quiet_end        smallint NOT NULL DEFAULT 7 CHECK (quiet_end BETWEEN 0 AND 23),
  snooze_until     timestamptz,
  stable_since     timestamptz,
  last_seen_at     timestamptz,
  last_bell_at     timestamptz,
  last_bell_keys   text[] NOT NULL DEFAULT '{}',
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
-- Giờ yên lặng 0–23: ràng buộc đặt tên cố định để chạy lại luôn thay được (bản dựng trước nếu thiếu cũng được nâng).
ALTER TABLE agent.gen_coach_prefs DROP CONSTRAINT IF EXISTS gen_coach_prefs_quiet_start_check;
ALTER TABLE agent.gen_coach_prefs ADD CONSTRAINT gen_coach_prefs_quiet_start_check CHECK (quiet_start BETWEEN 0 AND 23);
ALTER TABLE agent.gen_coach_prefs DROP CONSTRAINT IF EXISTS gen_coach_prefs_quiet_end_check;
ALTER TABLE agent.gen_coach_prefs ADD CONSTRAINT gen_coach_prefs_quiet_end_check CHECK (quiet_end BETWEEN 0 AND 23);

-- b) agent.gen_coach_items: trạng thái từng mục theo Owner — khoá item_key dạng 'todo:<khoá>' | 'tip:<key>' |
--    'lesson:<id>' | 'card:setup_followup'. status: shown (đã hiện) · understood (Đã hiểu) · snoozed (Để mai/Hoãn, kèm
--    snooze_until) · done (Đã làm) · dismissed (Không dùng việc này — chỉ việc P1/P3). Một hàng / (user_id, item_key).
CREATE TABLE IF NOT EXISTS agent.gen_coach_items (
  user_id         uuid NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
  item_key        text NOT NULL CHECK (char_length(item_key) BETWEEN 3 AND 120),
  org_id          uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  status          text NOT NULL DEFAULT 'shown'
                  CHECK (status IN ('shown', 'understood', 'snoozed', 'done', 'dismissed')),
  snooze_until    timestamptz,
  shown_count     integer NOT NULL DEFAULT 0 CHECK (shown_count >= 0),
  first_shown_at  timestamptz,
  last_shown_at   timestamptz,
  updated_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, item_key)
);
CREATE INDEX IF NOT EXISTS gen_coach_items_org_idx ON agent.gen_coach_items (org_id, user_id, status);

ALTER TABLE agent.gen_coach_prefs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON agent.gen_coach_prefs;
CREATE POLICY org_isolation ON agent.gen_coach_prefs
  USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '')
  WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '');

ALTER TABLE agent.gen_coach_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON agent.gen_coach_items;
CREATE POLICY org_isolation ON agent.gen_coach_items
  USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '')
  WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '');

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gh_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON agent.gen_coach_prefs TO gh_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON agent.gen_coach_items TO gh_app;
  END IF;
END $$;
