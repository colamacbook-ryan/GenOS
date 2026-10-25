-- v0.1.76 — gói T1 «nền tín hiệu + 6 cổng INT-19 + một hàng Cần quyết» (spec r6.6.1 §4.1, §4.2, §4.2.1, §4.4, §4.7 bước 2, §4B.2).
-- CHỈ THÊM, mọi câu lệnh chạy lại an toàn (IF NOT EXISTS / DO-block kiểm tên ràng buộc / backfill có điều kiện).
--   a) biz.alerts mở rộng tại chỗ thành bảng tín hiệu (không đổi tên — bên đọc cũ không gãy) + VIEW biz.signals
--   b) backfill hàng cũ theo bảng alert_type §4B.2 (một lần, đánh dấu detector = 'backfill_0044')
--   c) biz.signal_links / signal_feedback (chỉ-thêm) / signal_mutes; ops.asks (một hàng Cần quyết)
--   d) biz.promises (+ state conditional, conditions, fact_id), biz.payment_identifiers (+ verified_by/at),
--      biz.catalog_items (spec ghi ở 0043 nhưng L1 chưa tạo — T1 tạo ở đây), ops.owner_outbox.refs, ops.backup_outbox (kênh phụ)
--   e) RLS org_isolation + GRANT gh_app (khuôn 0042/0043)

-- ─── a) biz.alerts → tín hiệu ───────────────────────────────────────────────
ALTER TABLE biz.alerts
  ADD COLUMN IF NOT EXISTS kind               text NOT NULL DEFAULT 'legacy' REFERENCES biz.signal_kinds(code),
  ADD COLUMN IF NOT EXISTS level              text NOT NULL DEFAULT 'grey',
  ADD COLUMN IF NOT EXISTS lane               text NOT NULL DEFAULT 'fyi',
  ADD COLUMN IF NOT EXISTS privacy            text NOT NULL DEFAULT 'chat_ok',
  ADD COLUMN IF NOT EXISTS gate               text,
  ADD COLUMN IF NOT EXISTS candidate          text,
  ADD COLUMN IF NOT EXISTS delivery           text NOT NULL DEFAULT 'none',
  ADD COLUMN IF NOT EXISTS demoted            text,
  ADD COLUMN IF NOT EXISTS reasons            text[] NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS capped_at          timestamptz,
  ADD COLUMN IF NOT EXISTS would_push_at      timestamptz,
  ADD COLUMN IF NOT EXISTS pushed_at          timestamptz,
  ADD COLUMN IF NOT EXISTS digest_at          timestamptz,
  ADD COLUMN IF NOT EXISTS fact_ids           uuid[] NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS owner_fact_ids     uuid[] NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS projection_version int NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS case_key           text,
  ADD COLUMN IF NOT EXISTS dedupe_key         text,
  ADD COLUMN IF NOT EXISTS raw_event_id       uuid,
  ADD COLUMN IF NOT EXISTS thread_key         text,
  ADD COLUMN IF NOT EXISTS group_id           uuid,
  ADD COLUMN IF NOT EXISTS detector           text,
  ADD COLUMN IF NOT EXISTS detector_version   text,
  ADD COLUMN IF NOT EXISTS impact             smallint,
  ADD COLUMN IF NOT EXISTS urgency            smallint,
  ADD COLUMN IF NOT EXISTS confidence_exist   numeric(4,3),
  ADD COLUMN IF NOT EXISTS confidence_content numeric(4,3),
  ADD COLUMN IF NOT EXISTS confidence_level   text,
  ADD COLUMN IF NOT EXISTS evidence_n         int NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS facts              jsonb NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS level_input        jsonb,          -- SignalIn đã kiểm (đầu vào levels.compute) — tính lại theo đồng hồ
  ADD COLUMN IF NOT EXISTS level_at           timestamptz,    -- lần tính mức gần nhất
  ADD COLUMN IF NOT EXISTS next_eval_at       timestamptz,    -- levels.next_change_at: mốc đồng hồ kế tiếp mức có thể đổi
  ADD COLUMN IF NOT EXISTS due_at             timestamptz,
  ADD COLUMN IF NOT EXISTS expires_at         timestamptz,
  ADD COLUMN IF NOT EXISTS ask_id             uuid,
  ADD COLUMN IF NOT EXISTS feedback_verdict   text,
  ADD COLUMN IF NOT EXISTS verdict_by         text,
  ADD COLUMN IF NOT EXISTS outcome            text,
  ADD COLUMN IF NOT EXISTS outcome_result     text,
  ADD COLUMN IF NOT EXISTS outcome_value_vnd  bigint,
  ADD COLUMN IF NOT EXISTS outcome_by         text,
  ADD COLUMN IF NOT EXISTS outcome_at         timestamptz,
  ADD COLUMN IF NOT EXISTS outcome_note       text,
  ADD COLUMN IF NOT EXISTS updated_at         timestamptz NOT NULL DEFAULT now();

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'alerts_level_chk') THEN
    ALTER TABLE biz.alerts ADD CONSTRAINT alerts_level_chk CHECK (level IN ('red', 'amber', 'grey', 'off'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'alerts_delivery_chk') THEN
    ALTER TABLE biz.alerts ADD CONSTRAINT alerts_delivery_chk
      CHECK (delivery IN ('push_now', 'digest_top', 'digest_morning', 'digest', 'digest_low', 'merge', 'none'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'alerts_lane_chk') THEN
    ALTER TABLE biz.alerts ADD CONSTRAINT alerts_lane_chk CHECK (lane IN ('decide', 'handling', 'delegated', 'fyi'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'alerts_privacy_chk') THEN
    ALTER TABLE biz.alerts ADD CONSTRAINT alerts_privacy_chk CHECK (privacy IN ('chat_ok', 'console_pin'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'alerts_gate_chk') THEN
    ALTER TABLE biz.alerts ADD CONSTRAINT alerts_gate_chk CHECK (
      (gate IS NULL OR gate IN ('lua_dao_dang_dien_ra', 'an_toan', 'khach_che_cong_khai_doa_bo', 'loi_hua_khach_sap_vo',
                                'co_hoi_lon_sap_troi', 'su_co_cong_viec_gap'))
      AND (candidate IS NULL OR candidate IN ('lua_dao_dang_dien_ra', 'an_toan', 'khach_che_cong_khai_doa_bo',
                                'loi_hua_khach_sap_vo', 'co_hoi_lon_sap_troi', 'su_co_cong_viec_gap')));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'alerts_demoted_chk') THEN
    ALTER TABLE biz.alerts ADD CONSTRAINT alerts_demoted_chk CHECK (demoted IS NULL OR demoted IN ('precision', 'cap'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'alerts_status_chk') THEN
    ALTER TABLE biz.alerts ADD CONSTRAINT alerts_status_chk
      CHECK (status IN ('open', 'acknowledged', 'resolved', 'broken', 'expired', 'dismissed')) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'alerts_scores_chk') THEN
    ALTER TABLE biz.alerts ADD CONSTRAINT alerts_scores_chk CHECK (
      (impact IS NULL OR impact BETWEEN 1 AND 3) AND (urgency IS NULL OR urgency BETWEEN 1 AND 3)
      AND (confidence_level IS NULL OR confidence_level IN ('high', 'medium', 'low'))
      AND (feedback_verdict IS NULL OR feedback_verdict IN ('correct', 'wrong', 'unimportant', 'missed'))
      AND (verdict_by IS NULL OR verdict_by IN ('owner', 'system'))
      AND (outcome IS NULL OR outcome IN ('acted', 'ignored'))
      AND (outcome_result IS NULL OR outcome_result IN ('saved', 'lost', 'neutral', 'unknown'))
      AND (outcome_by IS NULL OR outcome_by IN ('auto', 'owner', 'staff')));
  END IF;
END $$;

-- ─── b) backfill hàng cũ (§4B.2; r4 §4.7): chỉ dòng chưa có detector — chạy lại không đụng dòng mới ────────
UPDATE biz.alerts a SET
  kind = CASE WHEN jsonb_array_length(a.evidence) = 0 AND m.kind NOT IN ('system') THEN 'legacy' ELSE m.kind END,
  privacy = m.privacy, impact = m.impact, urgency = m.urgency, confidence_level = m.conf,
  level = CASE a.priority WHEN 'P1' THEN 'amber' WHEN 'P2' THEN 'amber' ELSE 'grey' END,
  delivery = 'none',
  evidence_n = jsonb_array_length(a.evidence),
  pushed_at = a.created_at, digest_at = a.created_at,
  status = CASE WHEN a.status = 'open' AND a.created_at < now() - interval '7 days' THEN 'expired' ELSE a.status END,
  dedupe_key = COALESCE(a.dedupe_key, 'legacy:' || a.id::text),
  detector = 'backfill_0044', detector_version = '0044'
FROM (
  SELECT t.alert_type, COALESCE(x.kind, 'legacy') AS kind, COALESCE(x.privacy, 'console_pin') AS privacy,
         x.impact, x.urgency, COALESCE(x.conf, 'low') AS conf
  FROM (SELECT DISTINCT alert_type FROM biz.alerts) t
  LEFT JOIN (VALUES
    ('customer_cooling',      'quan_he_nong_nguoi',   'chat_ok',     2, 1, 'medium'),
    ('slow_response',         'cau_hoi_cho_xu_ly',    'chat_ok',     2, 2, 'high'),
    ('unclaimed_opportunity', 'co_hoi_ket_noi',       'chat_ok',     2, 2, 'medium'),
    ('opportunity_signal',    'co_hoi_ket_noi',       'chat_ok',     2, 1, 'medium'),
    ('competitor',            'thi_truong_thong_tin', 'chat_ok',     1, 1, 'medium'),
    ('forgotten_deadline',    'viec_han',             'chat_ok',     2, 3, 'medium'),
    ('repeated_complaint',    'xung_dot_khieu_nai',   'chat_ok',     2, 2, 'medium'),
    ('people_signal',         'legacy',               'console_pin', 1, 1, 'medium'),
    ('rule_alert',            'legacy',               'chat_ok',     1, 1, 'low'),
    ('data_conflict',         'system',               'chat_ok',     NULL, NULL, NULL),
    ('model_quota_low',       'system',               'chat_ok',     NULL, NULL, NULL),
    ('model_chain_exhausted', 'system',               'chat_ok',     NULL, NULL, NULL),
    ('model_chain_agy_only',  'system',               'chat_ok',     NULL, NULL, NULL)
  ) AS x(alert_type, kind, privacy, impact, urgency, conf) ON x.alert_type = t.alert_type
) m
WHERE m.alert_type = a.alert_type AND a.detector IS NULL;
-- Loại system không mang mức tình báo.
UPDATE biz.alerts SET impact = NULL, urgency = NULL, confidence_level = NULL WHERE kind = 'system' AND detector = 'backfill_0044';

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'alerts_evidence_n_chk') THEN
    ALTER TABLE biz.alerts ADD CONSTRAINT alerts_evidence_n_chk CHECK (kind IN ('system', 'legacy') OR evidence_n >= 1) NOT VALID;
  END IF;
END $$;
ALTER TABLE biz.alerts VALIDATE CONSTRAINT alerts_evidence_n_chk;

-- Khoá chống trùng (case_key, kind): một vụ đang mở = MỘT tín hiệu (spec §4.2 bảng case_key; G-KB điểm yếu "băm 2 mục").
CREATE UNIQUE INDEX IF NOT EXISTS alerts_case_open_uq ON biz.alerts (org_id, case_key, kind)
  WHERE case_key IS NOT NULL AND status IN ('open', 'acknowledged');
CREATE INDEX IF NOT EXISTS alerts_next_eval_idx ON biz.alerts (next_eval_at)
  WHERE next_eval_at IS NOT NULL AND status IN ('open', 'acknowledged');
CREATE INDEX IF NOT EXISTS alerts_level_idx ON biz.alerts (org_id, status, level, created_at DESC);
CREATE INDEX IF NOT EXISTS alerts_owner_fact_ids_idx ON biz.alerts USING gin (owner_fact_ids);

CREATE OR REPLACE VIEW biz.signals WITH (security_invoker = true) AS
  SELECT * FROM biz.alerts WHERE kind NOT IN ('system', 'legacy');

-- ─── c) liên kết / phản hồi / mute / một hàng Cần quyết ─────────────────────
CREATE TABLE IF NOT EXISTS biz.signal_links (
  org_id     uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  signal_id  uuid NOT NULL REFERENCES biz.alerts(id) ON DELETE CASCADE,
  ref_type   text NOT NULL CHECK (ref_type IN ('promise', 'opportunity', 'match', 'task', 'draft', 'case', 'signal', 'dispute', 'fact')),
  ref_id     uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (signal_id, ref_type, ref_id)
);

CREATE TABLE IF NOT EXISTS biz.signal_feedback (
  id         uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id     uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  signal_id  uuid NOT NULL REFERENCES biz.alerts(id) ON DELETE CASCADE,
  verdict    text NOT NULL CHECK (verdict IN ('correct', 'wrong', 'unimportant', 'missed')),
  verdict_by text NOT NULL CHECK (verdict_by IN ('owner', 'system')),
  user_id    uuid REFERENCES core.users(id),
  gate       text,
  shadow     boolean NOT NULL DEFAULT false,            -- Đúng/Sai trên dòng bóng (đếm M1 theo cổng)
  note       text,
  at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS signal_feedback_signal_idx ON biz.signal_feedback (signal_id, at DESC);
CREATE INDEX IF NOT EXISTS signal_feedback_gate_idx ON biz.signal_feedback (org_id, gate, at DESC);
CREATE OR REPLACE FUNCTION biz.signal_feedback_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'biz.signal_feedback chỉ-thêm';
END $$;
DROP TRIGGER IF EXISTS signal_feedback_append_only ON biz.signal_feedback;
CREATE TRIGGER signal_feedback_append_only BEFORE UPDATE ON biz.signal_feedback
  FOR EACH ROW EXECUTE FUNCTION biz.signal_feedback_guard();

CREATE TABLE IF NOT EXISTS biz.signal_mutes (
  id          uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id      uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  scope_type  text NOT NULL CHECK (scope_type IN ('kind', 'group', 'person', 'detector')),
  scope_value text NOT NULL,
  source      text NOT NULL CHECK (source IN ('chat', 'pin')),   -- mute qua chat không áp cho vụ lừa đảo/an toàn (CNS-123)
  created_by  uuid REFERENCES core.users(id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  until       timestamptz,
  revoked_at  timestamptz
);
CREATE INDEX IF NOT EXISTS signal_mutes_active_idx ON biz.signal_mutes (org_id, scope_type, scope_value) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS ops.asks (
  id            uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id        uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  code          text NOT NULL CHECK (code ~ '^[A-HJ-NP-Z2-9]{5}$'),
  source        text NOT NULL CHECK (source IN ('proposal', 'draft', 'question', 'signal', 'profile', 'identity')),
  ref_id        uuid NOT NULL,
  signal_id     uuid REFERENCES biz.alerts(id) ON DELETE SET NULL,
  decision_kind text NOT NULL CHECK (decision_kind IN ('self', 'internal', 'outbound', 'console')),
  summary       text NOT NULL,
  payload       jsonb NOT NULL DEFAULT '{}',
  created_via   text NOT NULL DEFAULT 'job' CHECK (created_via IN ('console', 'zalo', 'whatsapp', 'telegram', 'job')),
  asked_in      jsonb NOT NULL DEFAULT '[]',
  state         text NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'confirmed', 'executed', 'failed', 'cancelled', 'expired')),
  expires_at    timestamptz,
  decided_by    uuid REFERENCES core.users(id),
  decided_via   text CHECK (decided_via IS NULL OR decided_via IN ('console', 'zalo', 'whatsapp', 'telegram', 'auto_self')),
  decided_at    timestamptz,
  result        jsonb,
  created_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, source, ref_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS asks_open_code_uq ON ops.asks (org_id, code) WHERE state = 'open';
CREATE INDEX IF NOT EXISTS asks_state_idx ON ops.asks (org_id, state, created_at DESC);

-- ─── d) bảng nghiệp vụ đi kèm ───────────────────────────────────────────────
ALTER TABLE biz.promises
  ADD COLUMN IF NOT EXISTS state      text NOT NULL DEFAULT 'open',
  ADD COLUMN IF NOT EXISTS conditions jsonb,
  ADD COLUMN IF NOT EXISTS fact_id    uuid,
  ADD COLUMN IF NOT EXISTS signal_id  uuid,
  ADD COLUMN IF NOT EXISTS to_customer boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'promises_state_chk') THEN
    ALTER TABLE biz.promises ADD CONSTRAINT promises_state_chk
      CHECK (state IN ('open', 'conditional', 'kept', 'broken', 'expired', 'cancelled'));
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS biz.payment_identifiers (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  person_id    uuid REFERENCES core.persons(id),
  entity_id    uuid,
  digest       text NOT NULL,            -- băm STK (không lưu số đầy đủ)
  last3        text CHECK (last3 IS NULL OR last3 ~ '^[0-9]{1,3}$'),
  bank         text,
  source       text NOT NULL DEFAULT 'chat' CHECK (source IN ('chat', 'owner', 'import')),
  verified_by  uuid REFERENCES core.users(id),
  verified_at  timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, digest),
  CHECK ((verified_by IS NULL) = (verified_at IS NULL))
);
ALTER TABLE biz.payment_identifiers
  ADD COLUMN IF NOT EXISTS verified_by uuid REFERENCES core.users(id),
  ADD COLUMN IF NOT EXISTS verified_at timestamptz;

CREATE TABLE IF NOT EXISTS biz.catalog_items (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  name         text NOT NULL CHECK (btrim(name) <> '' AND length(name) <= 200),
  description  text CHECK (description IS NULL OR length(description) <= 2000),
  unit         text,
  price_vnd    bigint CHECK (price_vnd IS NULL OR price_vnd >= 0),
  tags         text[] NOT NULL DEFAULT '{}',
  state        text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'suggested', 'archived')),
  source       text NOT NULL DEFAULT 'owner' CHECK (source IN ('owner', 'suggested')),
  created_by   uuid REFERENCES core.users(id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS catalog_items_org_idx ON biz.catalog_items (org_id, state);

ALTER TABLE ops.owner_outbox ADD COLUMN IF NOT EXISTS refs jsonb NOT NULL DEFAULT '[]';

-- Kênh phụ (CNS-078): bảng RIÊNG, không sửa CHECK kind của ops.telegram_outbox (khuôn migration không DROP).
CREATE TABLE IF NOT EXISTS ops.backup_outbox (
  id              uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id          uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  channel         text NOT NULL CHECK (channel IN ('telegram', 'email', 'console')),
  kind            text NOT NULL CHECK (kind IN ('incident', 'code', 'security', 'digest_fallback')),
  dedupe_key      text,
  text            text NOT NULL CHECK (length(text) <= 4000),
  created_at      timestamptz NOT NULL DEFAULT now(),
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  attempts        integer NOT NULL DEFAULT 0,
  sent_at         timestamptz,
  failed_code     text
);
CREATE UNIQUE INDEX IF NOT EXISTS backup_outbox_dedupe_uq ON ops.backup_outbox (org_id, channel, dedupe_key);
CREATE INDEX IF NOT EXISTS backup_outbox_pending_idx ON ops.backup_outbox (next_attempt_at) WHERE sent_at IS NULL AND failed_code IS NULL;

-- ─── e) RLS + GRANT ─────────────────────────────────────────────────────────
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['biz.signal_links', 'biz.signal_feedback', 'biz.signal_mutes', 'ops.asks', 'biz.payment_identifiers',
                           'biz.catalog_items', 'ops.backup_outbox'] LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS org_isolation ON %s', t);
    EXECUTE format($p$CREATE POLICY org_isolation ON %s
      USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
             OR current_setting('app.org_id', true) IS NULL
             OR current_setting('app.org_id', true) = '')
      WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
             OR current_setting('app.org_id', true) IS NULL
             OR current_setting('app.org_id', true) = '')$p$, t);
  END LOOP;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gh_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON biz.signal_links, biz.signal_mutes, ops.asks, biz.payment_identifiers,
          biz.catalog_items, ops.backup_outbox TO gh_app;
    GRANT SELECT, INSERT, DELETE ON biz.signal_feedback TO gh_app;
    REVOKE UPDATE ON biz.signal_feedback FROM gh_app;            -- sổ Đúng/Sai chỉ-thêm (DELETE chỉ khi quên người)
    GRANT SELECT ON biz.signals TO gh_app;
  END IF;
END $$;
