-- Gen-Harness · giai đoạn 1 · bổ sung theo ARCHITECTURE §12
-- Giữ quy ước docs/handoff/03-database.md: UUIDv7, org_id, timestamptz, không ENUM.

-- Chặn TRUNCATE trên bảng chỉ-INSERT (trigger dòng không bắt được TRUNCATE).
CREATE OR REPLACE FUNCTION core.forbid_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME; END $$;
CREATE TRIGGER raw_events_no_truncate BEFORE TRUNCATE ON raw.events
  FOR EACH STATEMENT EXECUTE FUNCTION core.forbid_truncate();
CREATE TRIGGER action_log_no_truncate BEFORE TRUNCATE ON ops.action_log
  FOR EACH STATEMENT EXECUTE FUNCTION core.forbid_truncate();

-- Phân công: khách được phân cho Agent nhân viên, hàng đợi cho Operator (phạm vi 'assigned').
CREATE TABLE core.assignments (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL REFERENCES core.organizations(id),
  user_id      uuid NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
  subject_type text NOT NULL,                   -- person | group | opportunity | queue
  subject_id   uuid NOT NULL,
  active_from  timestamptz NOT NULL DEFAULT now(),
  active_to    timestamptz,
  created_by   uuid REFERENCES core.users(id)
);
CREATE INDEX ON core.assignments (org_id, user_id, subject_type) WHERE active_to IS NULL;
CREATE INDEX ON core.assignments (subject_type, subject_id) WHERE active_to IS NULL;

-- Quyết định của agent trực kênh + ngữ cảnh đã dùng ("agent đã nói gì, nhân danh gì, vì sao").
CREATE TABLE agent.decisions (
  id             uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id         uuid NOT NULL,
  agent_id       uuid NOT NULL REFERENCES agent.identities(id),
  trigger_ref    jsonb NOT NULL,                -- {"type":"meaning_unit","id":"…"}
  decision       text NOT NULL,                 -- silent | note | suggest | draft | send
  rationale      text,
  context_refs   jsonb NOT NULL DEFAULT '[]',   -- ID notebook entries, meaning units, scores đã dùng
  draft_id       uuid REFERENCES biz.action_drafts(id),
  model_call_id  uuid,
  at             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON agent.decisions (org_id, agent_id, at DESC);

-- Cảnh báo sớm (spec E9): mức ưu tiên, người nhận, hành động đề xuất, chứng cứ.
CREATE TABLE biz.alerts (
  id              uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id          uuid NOT NULL,
  code            text NOT NULL,                -- ALR-0001
  alert_type      text NOT NULL,                -- lookup alert_type: customer_cooling | slow_response | repeated_complaint | unclaimed_opportunity | competitor | forgotten_deadline | data_conflict
  priority        text NOT NULL DEFAULT 'P2',   -- P1 | P2 | P3
  recipient_user_id uuid REFERENCES core.users(id),
  subject_type    text, subject_id uuid,
  title           text NOT NULL,
  summary         text,
  suggested_action text,
  evidence        jsonb NOT NULL DEFAULT '[]',  -- [{"type":"meaning_unit","id":"…"}]
  personnel_related boolean NOT NULL DEFAULT false,
  status          text NOT NULL DEFAULT 'open', -- open | acknowledged | resolved | dismissed
  created_at      timestamptz NOT NULL DEFAULT now(),
  resolved_at     timestamptz,
  UNIQUE (org_id, code),
  CHECK (jsonb_array_length(evidence) > 0 OR NOT personnel_related)
);
CREATE INDEX ON biz.alerts (org_id, status, priority, created_at DESC);

-- Hồ sơ tài khoản Antigravity CLI (nhiều tài khoản, một hồ sơ hoạt động).
CREATE TABLE agent.cli_profiles (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  provider_id  uuid NOT NULL REFERENCES agent.providers(id) ON DELETE CASCADE,
  email        citext,
  plan_label   text,
  token_enc    bytea,                           -- tệp OAuth token của CLI, mã hoá phong bì
  is_active    boolean NOT NULL DEFAULT false,
  expires_at   timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ON agent.cli_profiles (provider_id) WHERE is_active;

-- Permit dùng một lần cho hành động đã được cho phép / đã duyệt (ARCHITECTURE §7.3).
ALTER TABLE biz.action_drafts
  ADD COLUMN flags jsonb NOT NULL DEFAULT '{}',          -- {"writes_external":true,"personnel_related":false,"amount_vnd":0}
  ADD COLUMN permit_hash bytea,
  ADD COLUMN permit_expires_at timestamptz,
  ADD COLUMN permit_used_at timestamptz;

-- Phiên PIN: lần thao tác gần nhất để gia hạn 30 phút không thao tác.
ALTER TABLE core.sessions ADD COLUMN csrf_hash bytea, ADD COLUMN last_seen_at timestamptz;

-- Plugin: lưu manifest đầy đủ và cấu hình theo settings_schema.
ALTER TABLE ops.plugins
  ADD COLUMN manifest jsonb NOT NULL DEFAULT '{}',
  ADD COLUMN settings jsonb NOT NULL DEFAULT '{}';
