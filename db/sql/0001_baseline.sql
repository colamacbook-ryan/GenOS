-- ═══════════════════════════════════════════════════════════════════════════
-- Gen-Harness · lược đồ khởi điểm · PostgreSQL 16
-- Quy ước: xem docs/03-database.md. Được chỉnh, nhưng giữ các quy ước.
-- Chạy qua công cụ migration (Alembic/Atlas/sqitch) — không chạy tay ở production.
-- Bản trong repo = docs/handoff/schema.sql (đã bỏ ở v0.1.48, xem lịch sử git) + 3 chỉnh:
--   (1) pg_partman 5.x: extension cài vào schema partman; (2) create_parent không còn p_retention → đặt qua
--   part_config; (3) core.uuid_v7(): lấy & 255 trước khi ép kiểu int (bản gốc tràn số "integer out of range").
-- ═══════════════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;          -- pgvector: tìm kiếm ngữ nghĩa kho hội thoại
CREATE SCHEMA IF NOT EXISTS partman; CREATE EXTENSION IF NOT EXISTS pg_partman SCHEMA partman;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

CREATE SCHEMA IF NOT EXISTS core;       -- tổ chức, người dùng, quyền, kênh, danh tính
CREATE SCHEMA IF NOT EXISTS raw;        -- kho thô: chỉ INSERT
CREATE SCHEMA IF NOT EXISTS refinery;   -- quy tắc sàng lọc & lượt chạy core agent
CREATE SCHEMA IF NOT EXISTS clean;      -- kho sạch SSOT: đơn vị ý nghĩa, điểm số, chứng cứ
CREATE SCHEMA IF NOT EXISTS memory;     -- sổ tay nhận thức / trí nhớ tạm theo ID
CREATE SCHEMA IF NOT EXISTS biz;        -- cơ hội, cung cầu, deal, việc, tài liệu, duyệt, đánh giá
CREATE SCHEMA IF NOT EXISTS agent;      -- danh tính agent, provider, model, quota, MCP
CREATE SCHEMA IF NOT EXISTS ops;        -- plugin, circuit breaker, action log, chính sách, cài đặt
CREATE SCHEMA IF NOT EXISTS analytics;  -- dimension + materialized view cho thống kê

-- ─── Hàm tiện ích ─────────────────────────────────────────────────────────────

-- UUIDv7: sắp theo thời gian → index B-tree chèn tuần tự, phân vùng tự nhiên.
CREATE OR REPLACE FUNCTION core.uuid_v7() RETURNS uuid LANGUAGE plpgsql VOLATILE AS $$
DECLARE ts bigint := (extract(epoch FROM clock_timestamp()) * 1000)::bigint; b bytea := gen_random_bytes(16);
BEGIN
  b := set_byte(b,0,((ts>>40) & 255)::int); b := set_byte(b,1,((ts>>32) & 255)::int);
  b := set_byte(b,2,((ts>>24) & 255)::int); b := set_byte(b,3,((ts>>16) & 255)::int);
  b := set_byte(b,4,((ts>>8) & 255)::int);  b := set_byte(b,5,(ts & 255)::int);
  b := set_byte(b,6,(get_byte(b,6) & 15) | 112); b := set_byte(b,8,(get_byte(b,8) & 63) | 128);
  RETURN encode(b,'hex')::uuid;
END $$;

CREATE OR REPLACE FUNCTION core.touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END $$;

-- Chặn UPDATE/DELETE trên bảng bất biến (kho thô, action log).
CREATE OR REPLACE FUNCTION core.forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION '% is append-only', TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME; END $$;

-- Mã công khai dễ đọc: GRP-ZL-0114, PER-0042, OPP-1842…
CREATE TABLE core.code_sequences (prefix text PRIMARY KEY, next_value bigint NOT NULL DEFAULT 1);
CREATE OR REPLACE FUNCTION core.next_code(p text, width int DEFAULT 4) RETURNS text LANGUAGE plpgsql AS $$
DECLARE v bigint;
BEGIN
  INSERT INTO core.code_sequences(prefix) VALUES (p) ON CONFLICT DO NOTHING;
  UPDATE core.code_sequences SET next_value = next_value + 1 WHERE prefix = p RETURNING next_value - 1 INTO v;
  RETURN p || '-' || lpad(v::text, width, '0');
END $$;

-- ─── Bảng tra cứu (thay cho ENUM để mở rộng không cần migration khoá bảng) ───

CREATE TABLE core.lookup (
  kind        text NOT NULL,           -- 'channel_type','intent','event_type','stage','priority','role',…
  code        text NOT NULL,           -- 'zalo','AskedPrice','negotiating','P1','owner'
  label_vi    text NOT NULL,
  label_en    text,
  sort_order  int  NOT NULL DEFAULT 0,
  attrs       jsonb NOT NULL DEFAULT '{}',
  is_active   boolean NOT NULL DEFAULT true,
  PRIMARY KEY (kind, code)
);

-- ═══ CORE ═══════════════════════════════════════════════════════════════════

CREATE TABLE core.organizations (
  id          uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  name        text NOT NULL,
  locale      text NOT NULL DEFAULT 'vi-VN',
  timezone    text NOT NULL DEFAULT 'Asia/Ho_Chi_Minh',
  currency    char(3) NOT NULL DEFAULT 'VND',
  settings    jsonb NOT NULL DEFAULT '{}',
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE core.users (
  id             uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id         uuid NOT NULL REFERENCES core.organizations(id),
  email          citext NOT NULL,
  display_name   text NOT NULL,
  password_hash  text NOT NULL,                 -- argon2id
  pin_hash       text,                          -- argon2id, 6 chữ số
  pin_failed     int  NOT NULL DEFAULT 0,
  pin_locked_until timestamptz,
  totp_secret_enc bytea,                        -- mã hoá bằng khoá master
  person_id      uuid,                          -- liên kết tới hồ sơ người trong hệ thống (FK thêm sau)
  addressing     jsonb NOT NULL DEFAULT '{}',   -- {"self":"Anh","bot_calls_me":"Sếp"}
  is_active      boolean NOT NULL DEFAULT true,
  last_login_at  timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  deleted_at     timestamptz,
  UNIQUE (org_id, email)
);

CREATE TABLE core.roles (
  id      uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id  uuid NOT NULL REFERENCES core.organizations(id),
  code    text NOT NULL,                 -- owner | manager | operator | agent_staff | auditor | tuỳ biến
  name    text NOT NULL,
  is_system boolean NOT NULL DEFAULT false,
  UNIQUE (org_id, code)
);

-- Quyền theo "năng lực × phạm vi": resource.action + scope.
CREATE TABLE core.permissions (
  code  text PRIMARY KEY,                -- 'profile.read','people_review.read','action.approve','audit.read',…
  label_vi text NOT NULL
);

CREATE TABLE core.role_permissions (
  role_id  uuid NOT NULL REFERENCES core.roles(id) ON DELETE CASCADE,
  permission_code text NOT NULL REFERENCES core.permissions(code),
  scope    text NOT NULL DEFAULT 'all',  -- 'all' | 'team' | 'assigned' | 'none'  (khớp ma trận ✓ / – / ✕)
  PRIMARY KEY (role_id, permission_code)
);

CREATE TABLE core.user_roles (
  user_id uuid NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
  role_id uuid NOT NULL REFERENCES core.roles(id) ON DELETE CASCADE,
  team_id uuid,                           -- phạm vi 'team'
  PRIMARY KEY (user_id, role_id)
);

CREATE TABLE core.teams (
  id      uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id  uuid NOT NULL REFERENCES core.organizations(id),
  name    text NOT NULL,
  parent_id uuid REFERENCES core.teams(id)
);

CREATE TABLE core.sessions (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  user_id      uuid NOT NULL REFERENCES core.users(id) ON DELETE CASCADE,
  token_hash   bytea NOT NULL UNIQUE,
  pin_verified_until timestamptz,          -- phiên PIN 30 phút
  ip           inet,
  user_agent   text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  expires_at   timestamptz NOT NULL,
  revoked_at   timestamptz
);

-- ─── Kênh, tài khoản kênh, nhóm ─────────────────────────────────────────────

CREATE TABLE core.channels (
  id          uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id      uuid NOT NULL REFERENCES core.organizations(id),
  type        text NOT NULL,                  -- lookup channel_type: zalo | whatsapp | telegram | linkedin | email | webhook
  name        text NOT NULL,
  plugin_id   uuid,                           -- plugin bridge phục vụ kênh này
  capabilities text[] NOT NULL DEFAULT '{}',  -- {'receive','send','identity_only'}
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, type, name)
);

-- Phiên đăng nhập QR của kênh — có lịch sử, không ghi đè.
CREATE TABLE core.channel_sessions (
  id            uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  channel_id    uuid NOT NULL REFERENCES core.channels(id),
  account_label text NOT NULL,                -- "iPhone của Sếp"
  state         text NOT NULL,                -- pending_qr | active | expired | logged_out | error
  credential_enc bytea,                       -- khoá phiên mã hoá
  qr_issued_at  timestamptz,
  started_at    timestamptz,
  expires_at    timestamptz,
  ended_at      timestamptz,
  last_heartbeat_at timestamptz,
  meta          jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX ON core.channel_sessions (channel_id, started_at DESC);

CREATE TABLE core.groups (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL REFERENCES core.organizations(id),
  code         text NOT NULL,                  -- GRP-ZL-0114
  channel_id   uuid NOT NULL REFERENCES core.channels(id),
  external_id  text NOT NULL,                  -- id nhóm phía nền tảng
  name         text NOT NULL,
  kind         text NOT NULL DEFAULT 'internal', -- internal | market | partner | customer | private
  listen_mode  text NOT NULL DEFAULT 'off',      -- off | tagged_only | silent | proactive | paused
  view_scope   text NOT NULL DEFAULT 'owner',    -- owner | manager | all_members
  assigned_agent_id uuid,
  member_count int,
  attrs        jsonb NOT NULL DEFAULT '{}',
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, code),
  UNIQUE (channel_id, external_id)
);

-- ─── Con người & danh tính đa kênh ──────────────────────────────────────────

CREATE TABLE core.persons (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL REFERENCES core.organizations(id),
  code         text NOT NULL,                  -- PER-0042
  display_name text NOT NULL,
  person_type  text NOT NULL DEFAULT 'unknown', -- customer | partner | staff | candidate | learner | supplier | unknown
  organization_name text,
  title        text,
  relation_to_owner text,                      -- direct | via_staff | staff | stranger
  owner_user_id uuid REFERENCES core.users(id),-- người phụ trách
  merged_into_id uuid REFERENCES core.persons(id), -- khi bị gộp: trỏ tới hồ sơ gốc, không xoá
  attrs        jsonb NOT NULL DEFAULT '{}',
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz,
  UNIQUE (org_id, code)
);
CREATE INDEX ON core.persons USING gin (display_name gin_trgm_ops);
CREATE INDEX ON core.persons (org_id, person_type) WHERE deleted_at IS NULL AND merged_into_id IS NULL;
ALTER TABLE core.users ADD CONSTRAINT users_person_fk FOREIGN KEY (person_id) REFERENCES core.persons(id);

CREATE TABLE core.person_identities (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  person_id    uuid NOT NULL REFERENCES core.persons(id),
  channel_id   uuid NOT NULL REFERENCES core.channels(id),
  external_id  text NOT NULL,                  -- uid Zalo, số WhatsApp, slug LinkedIn
  handle       text,
  phone_e164   text,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (channel_id, external_id)
);
CREATE INDEX ON core.person_identities (person_id);
CREATE INDEX ON core.person_identities (phone_e164) WHERE phone_e164 IS NOT NULL;

CREATE TABLE core.group_members (
  group_id   uuid NOT NULL REFERENCES core.groups(id),
  person_id  uuid NOT NULL REFERENCES core.persons(id),
  role       text,                              -- admin | member
  joined_at  timestamptz,
  left_at    timestamptz,
  PRIMARY KEY (group_id, person_id)
);

CREATE TABLE core.identity_merge_candidates (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL REFERENCES core.organizations(id),
  identity_a   uuid NOT NULL REFERENCES core.person_identities(id),
  identity_b   uuid NOT NULL REFERENCES core.person_identities(id),
  confidence   numeric(4,3) NOT NULL,
  basis        jsonb NOT NULL,                  -- {"phone":true,"org_name":0.91,"style":0.7}
  status       text NOT NULL DEFAULT 'pending', -- pending | merged | rejected
  decided_by   uuid REFERENCES core.users(id),
  decided_at   timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  CHECK (identity_a <> identity_b)
);

CREATE TABLE core.identity_merge_log (
  id          uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  op          text NOT NULL,                    -- merge | split
  from_person uuid NOT NULL REFERENCES core.persons(id),
  to_person   uuid NOT NULL REFERENCES core.persons(id),
  identities  uuid[] NOT NULL,
  actor_user  uuid REFERENCES core.users(id),
  at          timestamptz NOT NULL DEFAULT now()
);

-- ═══ RAW — kho thô, bất biến, phân vùng theo tháng ═════════════════════════

CREATE TABLE raw.events (
  id             uuid NOT NULL DEFAULT core.uuid_v7(),
  org_id         uuid NOT NULL,
  received_at    timestamptz NOT NULL DEFAULT now(),
  occurred_at    timestamptz NOT NULL,          -- thời điểm trên nền tảng
  channel_id     uuid NOT NULL,
  group_id       uuid,                          -- NULL = tin nhắn riêng
  sender_identity_id uuid,
  external_msg_id text NOT NULL,
  kind           text NOT NULL,                 -- text | image | file | sticker | reaction | system
  body_text      text,
  payload        jsonb NOT NULL,                -- bản gốc nguyên trạng từ bridge
  content_hash   bytea NOT NULL,                -- chống trùng khi bridge gửi lại
  PRIMARY KEY (id, received_at),
  UNIQUE (channel_id, external_msg_id, received_at)
) PARTITION BY RANGE (received_at);
CREATE INDEX ON raw.events (org_id, received_at DESC);
CREATE INDEX ON raw.events (group_id, occurred_at DESC);
CREATE INDEX ON raw.events (sender_identity_id, occurred_at DESC);
CREATE INDEX ON raw.events USING gin (body_text gin_trgm_ops);
SELECT partman.create_parent('raw.events', 'received_at', '1 month', p_premake => 3);

-- Trạng thái xử lý nằm ở refinery.event_state để bảng thô khoá chỉ-INSERT tuyệt đối.
CREATE TRIGGER raw_events_append_only BEFORE UPDATE OR DELETE ON raw.events
  FOR EACH ROW EXECUTE FUNCTION core.forbid_mutation();

CREATE TABLE raw.attachments (
  id          uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  event_id    uuid NOT NULL,
  event_received_at timestamptz NOT NULL,
  storage_key text NOT NULL,                    -- đường dẫn trong object store (MinIO)
  mime        text NOT NULL,
  bytes       bigint NOT NULL,
  sha256      bytea NOT NULL
);

-- ═══ REFINERY — quy tắc & lượt chạy ═══════════════════════════════════════

CREATE TABLE refinery.rules (
  id          uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id      uuid NOT NULL REFERENCES core.organizations(id),
  code        text NOT NULL,                    -- R-01
  name        text NOT NULL,
  kind        text NOT NULL,                    -- intent | risk | competition | hr | hygiene | custom
  is_enabled  boolean NOT NULL DEFAULT true,
  current_version int NOT NULL DEFAULT 1,
  UNIQUE (org_id, code)
);

-- Mỗi lần sửa quy tắc tạo phiên bản mới → kết luận cũ luôn biết đã dùng phiên bản nào.
CREATE TABLE refinery.rule_versions (
  rule_id     uuid NOT NULL REFERENCES refinery.rules(id),
  version     int  NOT NULL,
  conditions  jsonb NOT NULL,                   -- [{"type":"keyword_any","values":[…]},{"type":"has_entity","entity":"price"}]
  outputs     jsonb NOT NULL,                   -- [{"set":"intent","value":"AskedPrice"},{"add":"heat","value":30}]
  threshold   numeric(4,3) NOT NULL,
  prompt_hint text,                             -- chỉ dẫn bổ sung cho LLM
  created_by  uuid REFERENCES core.users(id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (rule_id, version)
);

CREATE TABLE refinery.scoring_weights (
  org_id      uuid NOT NULL REFERENCES core.organizations(id),
  dimension   text NOT NULL,                    -- heat | potential | churn_risk | fit | engagement | data_confidence
  weight      numeric(5,4) NOT NULL CHECK (weight >= 0 AND weight <= 1),
  valid_from  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, dimension, valid_from)
);

CREATE TABLE refinery.schedule (
  org_id            uuid PRIMARY KEY REFERENCES core.organizations(id),
  interval_seconds  int NOT NULL DEFAULT 900,     -- chu kỳ thời gian
  count_threshold   int NOT NULL DEFAULT 500,     -- ngưỡng số lượng — chạy khi đạt bất kỳ điều kiện nào trước
  batch_size        int NOT NULL DEFAULT 250,
  min_confidence    numeric(4,3) NOT NULL DEFAULT 0.600,
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE refinery.runs (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  trigger      text NOT NULL,                   -- schedule | threshold | manual | test
  started_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz,
  input_count  int NOT NULL DEFAULT 0,
  clean_count  int NOT NULL DEFAULT 0,
  lowconf_count int NOT NULL DEFAULT 0,
  noise_count  int NOT NULL DEFAULT 0,
  error_count  int NOT NULL DEFAULT 0,
  model        text,
  tokens_in    int, tokens_out int,
  status       text NOT NULL DEFAULT 'running'  -- running | done | failed
);

CREATE TABLE refinery.event_state (
  event_id     uuid NOT NULL,
  event_received_at timestamptz NOT NULL,
  org_id       uuid NOT NULL,
  state        text NOT NULL,                   -- pending | processing | clean | lowconf | discarded | error
  run_id       uuid REFERENCES refinery.runs(id),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (event_id, event_received_at)
);
CREATE INDEX ON refinery.event_state (org_id, state) WHERE state IN ('pending','lowconf');

-- ═══ CLEAN — kho sạch SSOT ═════════════════════════════════════════════════

CREATE TABLE clean.meaning_units (
  id           uuid NOT NULL DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  observed_at  timestamptz NOT NULL,            -- thời điểm sự việc (từ raw)
  group_id     uuid,
  person_id    uuid,                            -- sau hợp nhất danh tính
  event_type   text NOT NULL,                   -- lookup event_type: AskedPrice, Complained, OfferedSupply, …
  side         text,                            -- demand | supply | NULL
  conclusion   text NOT NULL,                   -- kết luận 1–2 câu
  entities     jsonb NOT NULL DEFAULT '{}',     -- {"product":"MDF E1 17mm","qty":3,"unit":"container","budget_vnd":1200000000}
  confidence   numeric(4,3) NOT NULL,
  run_id       uuid NOT NULL,
  rule_id      uuid, rule_version int,
  embedding    vector(768),
  created_at   timestamptz NOT NULL DEFAULT now(),
  superseded_by uuid,                           -- khi chạy lại với quy tắc mới: giữ bản cũ, trỏ sang bản mới
  PRIMARY KEY (id, observed_at)
) PARTITION BY RANGE (observed_at);
CREATE INDEX ON clean.meaning_units (org_id, event_type, observed_at DESC);
CREATE INDEX ON clean.meaning_units (person_id, observed_at DESC);
CREATE INDEX ON clean.meaning_units (group_id, observed_at DESC);
CREATE INDEX ON clean.meaning_units USING gin (entities jsonb_path_ops);
SELECT partman.create_parent('clean.meaning_units', 'observed_at', '1 month', p_premake => 3);
-- Index vector tạo trên từng phân vùng bởi job bảo trì (HNSW không kế thừa qua bảng cha ở mọi phiên bản).

-- Chứng cứ: mọi đơn vị ý nghĩa trỏ về ≥1 bản ghi thô.
CREATE TABLE clean.evidence (
  meaning_unit_id uuid NOT NULL,
  meaning_observed_at timestamptz NOT NULL,
  raw_event_id    uuid NOT NULL,
  raw_received_at timestamptz NOT NULL,
  quote           text,                         -- đoạn trích được hiển thị ở UI
  PRIMARY KEY (meaning_unit_id, raw_event_id)
);
CREATE INDEX ON clean.evidence (raw_event_id);

-- Điểm số theo thời gian (không ghi đè) → vẽ xu hướng, giải thích "vì sao".
CREATE TABLE clean.score_snapshots (
  id           uuid NOT NULL DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  subject_type text NOT NULL,                   -- person | group | opportunity | staff
  subject_id   uuid NOT NULL,
  dimension    text NOT NULL,                   -- heat | potential | churn_risk | care | fit | data_confidence
  value        numeric(6,2) NOT NULL,
  confidence   numeric(4,3),
  explanation  jsonb NOT NULL,                  -- {"factors":[{"label":"3 tin chưa trả lời","delta":+30,"evidence":[…]}]}
  computed_at  timestamptz NOT NULL DEFAULT now(),
  overridden_by uuid,                           -- user sửa tay
  PRIMARY KEY (id, computed_at)
) PARTITION BY RANGE (computed_at);
CREATE INDEX ON clean.score_snapshots (subject_type, subject_id, dimension, computed_at DESC);
SELECT partman.create_parent('clean.score_snapshots', 'computed_at', '1 month', p_premake => 3);

-- Giá trị hiện tại để đọc nhanh (cập nhật bởi worker, nguồn thật vẫn là snapshots).
CREATE TABLE clean.current_scores (
  subject_type text NOT NULL, subject_id uuid NOT NULL, dimension text NOT NULL,
  value numeric(6,2) NOT NULL, trend text, snapshot_id uuid NOT NULL, updated_at timestamptz NOT NULL,
  PRIMARY KEY (subject_type, subject_id, dimension)
);

-- Quan hệ đã phân tích (đồ thị). Cạnh có hướng, trọng số theo cửa sổ thời gian.
CREATE TABLE clean.relationships (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  from_type    text NOT NULL, from_id uuid NOT NULL,   -- person | group
  to_type      text NOT NULL, to_id   uuid NOT NULL,
  kind         text NOT NULL,                   -- interacts | shares_members | bridges | owns
  window_days  int  NOT NULL DEFAULT 90,
  weight       numeric(8,3) NOT NULL,
  interactions int  NOT NULL DEFAULT 0,
  last_at      timestamptz,
  state        text NOT NULL DEFAULT 'active',  -- active | cold | risk
  topic        text,
  computed_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, from_type, from_id, to_type, to_id, kind, window_days)
);
CREATE INDEX ON clean.relationships (org_id, to_type, to_id);

-- ═══ MEMORY — sổ tay nhận thức (trí nhớ tạm lũy tiến theo ID) ══════════════

CREATE TABLE memory.notebooks (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  subject_type text NOT NULL,                   -- person | group
  subject_id   uuid NOT NULL,
  token_budget int  NOT NULL DEFAULT 4000,
  token_used   int  NOT NULL DEFAULT 0,
  compaction_no int NOT NULL DEFAULT 0,
  last_compacted_at timestamptz,
  UNIQUE (org_id, subject_type, subject_id)
);

CREATE TABLE memory.entries (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  notebook_id  uuid NOT NULL REFERENCES memory.notebooks(id) ON DELETE CASCADE,
  section      text NOT NULL,                   -- attention_now | rolling_context | guardrails | preferences | open_threads | dropped
  body         text NOT NULL,
  refs         jsonb NOT NULL DEFAULT '[]',     -- [{"type":"meaning_unit","id":"…"},{"type":"deal","code":"OPP-1815"}]
  author       text NOT NULL,                   -- agent:<id> | user:<id>
  is_pinned    boolean NOT NULL DEFAULT false,  -- Owner ghim → không bị nén
  valid_until  timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  archived_at  timestamptz                      -- bị nén ra khỏi ngữ cảnh (vẫn giữ để truy vết)
);
CREATE INDEX ON memory.entries (notebook_id, section) WHERE archived_at IS NULL;

CREATE TABLE memory.compactions (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  notebook_id  uuid NOT NULL REFERENCES memory.notebooks(id) ON DELETE CASCADE,
  compaction_no int NOT NULL,
  tokens_before int NOT NULL, tokens_after int NOT NULL,
  archived_entries uuid[] NOT NULL,
  summary      text,
  model        text,
  at           timestamptz NOT NULL DEFAULT now()
);

-- ═══ BIZ — nghiệp vụ ══════════════════════════════════════════════════════

CREATE TABLE biz.opportunities (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  code         text NOT NULL,                   -- OPP-1842
  person_id    uuid REFERENCES core.persons(id),
  source_group_id uuid REFERENCES core.groups(id),
  need         text NOT NULL,
  stage        text NOT NULL,                   -- raw_signal | validated | matched | approaching | negotiating | handed_off | won | lost | dormant
  value_vnd    bigint,
  confidence   text NOT NULL,                   -- high | medium | low
  owner_user_id uuid REFERENCES core.users(id),
  first_signal_at timestamptz NOT NULL,         -- dùng cho chỉ số "tín hiệu → tiếp cận"
  first_contact_at timestamptz,
  closed_at    timestamptz,
  attrs        jsonb NOT NULL DEFAULT '{}',
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, code)
);
CREATE INDEX ON biz.opportunities (org_id, stage);

CREATE TABLE biz.opportunity_stage_history (
  opportunity_id uuid NOT NULL REFERENCES biz.opportunities(id),
  from_stage   text, to_stage text NOT NULL,
  actor        text NOT NULL,                   -- user:<id> | agent:<id> | system
  at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON biz.opportunity_stage_history (opportunity_id, at);

-- Cung ↔ Cầu
CREATE TABLE biz.market_signals (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  side         text NOT NULL CHECK (side IN ('demand','supply')),
  person_id    uuid REFERENCES core.persons(id),
  group_id     uuid REFERENCES core.groups(id),
  item         text NOT NULL,
  category     text,                            -- ngành hàng (lookup)
  quantity     numeric, unit text,
  value_vnd    bigint,
  location     text,
  needed_by    date,
  heat         numeric(5,2),
  status       text NOT NULL DEFAULT 'open',    -- open | matched | closed | ignored
  meaning_unit_id uuid,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON biz.market_signals (org_id, side, status);

CREATE TABLE biz.matches (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  demand_id    uuid NOT NULL REFERENCES biz.market_signals(id),
  supply_id    uuid NOT NULL REFERENCES biz.market_signals(id),
  score        numeric(5,2) NOT NULL,
  reasons      jsonb NOT NULL,
  status       text NOT NULL DEFAULT 'suggested', -- suggested | introduced | accepted | rejected
  opportunity_id uuid REFERENCES biz.opportunities(id),
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (demand_id, supply_id)
);

CREATE TABLE biz.deals (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  code         text NOT NULL,
  opportunity_id uuid REFERENCES biz.opportunities(id),
  person_id    uuid REFERENCES core.persons(id),
  amount_vnd   bigint NOT NULL,
  status       text NOT NULL,                   -- open | won | lost
  won_at       timestamptz,
  erp_ref      text,
  UNIQUE (org_id, code)
);

CREATE TABLE biz.cases (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  code         text NOT NULL,                   -- ALR-0233, SYS-0045
  kind         text NOT NULL,                   -- alert | complaint | system
  priority     text NOT NULL,                   -- P1 | P2 | P3
  subject_type text, subject_id uuid,
  title        text NOT NULL,
  status       text NOT NULL DEFAULT 'open',
  assignee_user_id uuid REFERENCES core.users(id),
  opened_at    timestamptz NOT NULL DEFAULT now(),
  resolved_at  timestamptz,
  UNIQUE (org_id, code)
);

CREATE TABLE biz.tasks (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  code         text NOT NULL,                   -- TSK-0412
  title        text NOT NULL,
  priority     text NOT NULL DEFAULT 'P3',
  status       text NOT NULL DEFAULT 'todo',
  assignee_user_id uuid REFERENCES core.users(id),
  subject_type text, subject_id uuid,
  due_at       timestamptz,
  remind_at    timestamptz,
  source       text,                            -- promise | draft | manual
  created_at   timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  UNIQUE (org_id, code)
);
CREATE INDEX ON biz.tasks (org_id, status, due_at);

-- Tài liệu: quyền theo nhóm & cá nhân
CREATE TABLE biz.documents (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  title        text NOT NULL,
  storage_key  text NOT NULL,
  mime         text NOT NULL,
  bytes        bigint NOT NULL,
  owner_group_id  uuid REFERENCES core.groups(id),
  owner_person_id uuid REFERENCES core.persons(id),
  created_by   text NOT NULL,                   -- user:<id> | agent:<id> | channel
  raw_event_id uuid,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE biz.document_acl (
  document_id  uuid NOT NULL REFERENCES biz.documents(id) ON DELETE CASCADE,
  principal    text NOT NULL,                   -- role:<code> | user:<id> | group:<id> | agent:<id>
  can_read boolean NOT NULL DEFAULT true, can_write boolean NOT NULL DEFAULT false,
  PRIMARY KEY (document_id, principal)
);

-- Bàn làm việc: bản nháp hành động + duyệt
CREATE TABLE biz.action_drafts (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  code         text NOT NULL,                   -- ACT-0231
  kind         text NOT NULL,                   -- message | quotation | contract | reminder | report | mcp_write
  agent_id     uuid,
  subject_type text, subject_id uuid,
  channel_id   uuid, group_id uuid,
  body         jsonb NOT NULL,
  sources      jsonb NOT NULL DEFAULT '[]',     -- dữ liệu agent đã dùng
  side_actions jsonb NOT NULL DEFAULT '[]',     -- ghi CRM, tạo việc, đặt nhắc…
  autonomy_level smallint NOT NULL CHECK (autonomy_level BETWEEN 0 AND 6),
  hold_reason  text,                            -- "vượt ngưỡng 50 triệu ₫"
  status       text NOT NULL DEFAULT 'pending', -- pending | approved | edited | rejected | sent | failed
  decided_by   uuid REFERENCES core.users(id),
  decided_at   timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (org_id, code)
);
CREATE INDEX ON biz.action_drafts (org_id, status, created_at DESC);

-- Đánh giá con người: có chứng cứ và có phản biện
CREATE TABLE biz.people_reviews (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  person_id    uuid NOT NULL REFERENCES core.persons(id),
  period_start date NOT NULL, period_end date NOT NULL,
  score        numeric(5,2) NOT NULL,
  trend        text,
  signal       text NOT NULL,
  recommendation text NOT NULL,
  evidence     jsonb NOT NULL,
  visibility   text NOT NULL DEFAULT 'owner',
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE biz.review_disputes (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  review_id    uuid NOT NULL REFERENCES biz.people_reviews(id),
  raised_by    uuid NOT NULL REFERENCES core.users(id),
  body         text NOT NULL,
  status       text NOT NULL DEFAULT 'open',
  resolution   text,
  created_at   timestamptz NOT NULL DEFAULT now()
);

-- Lời hứa có mốc thời gian (nguồn cho "hứa rồi quên")
CREATE TABLE biz.promises (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  promiser_person_id uuid NOT NULL REFERENCES core.persons(id),
  to_person_id uuid REFERENCES core.persons(id),
  text         text NOT NULL,
  due_at       timestamptz NOT NULL,
  kept_at      timestamptz,
  broken       boolean,
  meaning_unit_id uuid
);

-- ═══ AGENT — danh tính, provider, model, quota, MCP ══════════════════════

CREATE TABLE agent.identities (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  name         text NOT NULL,
  role_desc    text NOT NULL,
  template     text,                            -- commercial | key_account | admin | cs | recruiter | secretary | custom
  addressing   jsonb NOT NULL,                  -- xưng hô
  voice        text NOT NULL,
  speak_when   text NOT NULL,
  forbidden    text[] NOT NULL DEFAULT '{}',
  autonomy_level smallint NOT NULL DEFAULT 3 CHECK (autonomy_level BETWEEN 0 AND 6),
  is_enabled   boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE agent.channel_scopes (
  agent_id     uuid NOT NULL REFERENCES agent.identities(id) ON DELETE CASCADE,
  channel_id   uuid REFERENCES core.channels(id),
  group_id     uuid REFERENCES core.groups(id),
  hours        tstzrange[],                     -- ca trực, NULL = cả ngày
  PRIMARY KEY (agent_id, channel_id, group_id)
);

CREATE TABLE agent.providers (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  kind         text NOT NULL,                   -- antigravity_cli | gemini | deepseek | openai_compat | embedding
  name         text NOT NULL,
  endpoint     text,
  failover_rank smallint,
  is_enabled   boolean NOT NULL DEFAULT true,
  account_label text,                           -- email tài khoản CLI
  auth_state   text NOT NULL DEFAULT 'unconfigured', -- unconfigured | ok | expiring | expired | error
  auth_expires_at timestamptz
);
-- Khoá API: mã hoá phong bì; ứng dụng chỉ giữ khoá dữ liệu, khoá master nằm ở secrets của compose.
CREATE TABLE agent.provider_keys (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  provider_id  uuid NOT NULL REFERENCES agent.providers(id) ON DELETE CASCADE,
  label        text NOT NULL,                   -- GEM-KEY-01
  secret_enc   bytea NOT NULL,
  last4        text NOT NULL,
  rotation_order smallint NOT NULL DEFAULT 0,
  is_enabled   boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE agent.models (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  provider_id  uuid NOT NULL REFERENCES agent.providers(id),
  model_name   text NOT NULL,                   -- gemini-2.5-pro
  role_desc    text,
  daily_quota  bigint,                          -- lượt hoặc token, theo quota_unit
  quota_unit   text NOT NULL DEFAULT 'requests',
  rate_limit_per_min int,
  cost_per_1k_vnd numeric(10,2),
  UNIQUE (provider_id, model_name)
);
CREATE TABLE agent.bindings (
  agent_key    text NOT NULL,                   -- 'core.refinery' | 'core.scoring' | agent:<uuid>
  org_id       uuid NOT NULL,
  model_id     uuid NOT NULL REFERENCES agent.models(id),
  temperature  numeric(3,2) NOT NULL DEFAULT 0.3,
  context_tokens int NOT NULL,
  rule_codes   text[] NOT NULL DEFAULT '{}',
  PRIMARY KEY (org_id, agent_key)
);
-- Lượt gọi model — nguồn cho quota, chi phí, độ trễ. Phân vùng theo tháng.
CREATE TABLE agent.model_calls (
  id           uuid NOT NULL DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  at           timestamptz NOT NULL DEFAULT now(),
  model_id     uuid NOT NULL,
  key_id       uuid,
  agent_key    text NOT NULL,
  purpose      text NOT NULL,                   -- refinery | reply | scoring | embedding | compaction
  tokens_in    int, tokens_out int,
  latency_ms   int,
  status       text NOT NULL,                   -- ok | rate_limited | error | fallback
  cost_vnd     numeric(12,2),
  PRIMARY KEY (id, at)
) PARTITION BY RANGE (at);
CREATE INDEX ON agent.model_calls (org_id, model_id, at DESC);
SELECT partman.create_parent('agent.model_calls', 'at', '1 month', p_premake => 3);

CREATE TABLE agent.mcp_servers (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  name         text NOT NULL,
  transport    text NOT NULL,                   -- stdio | http+sse | streamable_http
  endpoint     text NOT NULL,
  auth_enc     bytea,
  is_enabled   boolean NOT NULL DEFAULT true,
  health       text NOT NULL DEFAULT 'unknown',
  note         text,
  allow_public_network boolean NOT NULL DEFAULT false
);
CREATE TABLE agent.mcp_tools (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  server_id    uuid NOT NULL REFERENCES agent.mcp_servers(id) ON DELETE CASCADE,
  name         text NOT NULL,                   -- inventory.check
  access       text NOT NULL CHECK (access IN ('read','write')),
  is_exposed   boolean NOT NULL DEFAULT false,  -- Owner mở mới gọi được
  schema       jsonb,
  UNIQUE (server_id, name)
);
CREATE TABLE agent.mcp_grants (
  tool_id      uuid NOT NULL REFERENCES agent.mcp_tools(id) ON DELETE CASCADE,
  agent_key    text NOT NULL,
  PRIMARY KEY (tool_id, agent_key)
);
CREATE TABLE agent.mcp_calls (
  id           uuid NOT NULL DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  at           timestamptz NOT NULL DEFAULT now(),
  tool_id      uuid NOT NULL,
  agent_key    text NOT NULL,
  args         jsonb NOT NULL,
  result_summary text,
  latency_ms   int,
  outcome      text NOT NULL,                   -- ok | held_for_approval | blocked | error
  draft_id     uuid,
  PRIMARY KEY (id, at)
) PARTITION BY RANGE (at);
SELECT partman.create_parent('agent.mcp_calls', 'at', '1 month', p_premake => 3);

-- ═══ OPS — plugin, breaker, nhật ký, chính sách ════════════════════════════

CREATE TABLE ops.plugins (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  package      text NOT NULL UNIQUE,            -- @gen/chassis-store
  name         text NOT NULL,
  layer        text NOT NULL,                   -- chassis | channel | intelligence | provider | action | ui | extension
  origin       text NOT NULL,                   -- core (nền, không gỡ được) | marketplace | local_file
  version      text NOT NULL,
  is_enabled   boolean NOT NULL DEFAULT true,
  load_order   int,
  sandbox      jsonb NOT NULL DEFAULT '{}',     -- {"memory_mb":512,"timeout_s":30,"net":"internal"}
  permissions  text[] NOT NULL DEFAULT '{}',    -- quyền plugin xin khi cài
  signature_ok boolean,
  installed_at timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE ops.plugin_dependencies (
  plugin_id    uuid NOT NULL REFERENCES ops.plugins(id) ON DELETE CASCADE,
  depends_on   text NOT NULL,                   -- package
  version_range text NOT NULL,
  PRIMARY KEY (plugin_id, depends_on)
);
CREATE TABLE ops.breaker_events (
  id           uuid NOT NULL DEFAULT core.uuid_v7(),
  at           timestamptz NOT NULL DEFAULT now(),
  plugin_id    uuid NOT NULL,
  state        text NOT NULL,                   -- closed | open | half_open
  reason       text,
  PRIMARY KEY (id, at)
) PARTITION BY RANGE (at);
SELECT partman.create_parent('ops.breaker_events', 'at', '1 month', p_premake => 3);

CREATE TABLE ops.plugin_logs (
  id           uuid NOT NULL DEFAULT core.uuid_v7(),
  at           timestamptz NOT NULL DEFAULT now(),
  plugin_id    uuid NOT NULL,
  level        text NOT NULL,                   -- DEBUG | INFO | WARN | ERROR | BREAK
  message      text NOT NULL,
  ctx          jsonb,
  PRIMARY KEY (id, at)
) PARTITION BY RANGE (at);
SELECT partman.create_parent('ops.plugin_logs', 'at', '1 week', p_premake => 2);
UPDATE partman.part_config SET retention = '30 days', retention_keep_table = false WHERE parent_table = 'ops.plugin_logs';

-- Nhật ký hành động: bất biến, có chuỗi băm để phát hiện sửa ngầm.
CREATE TABLE ops.action_log (
  id           uuid NOT NULL DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  at           timestamptz NOT NULL DEFAULT now(),
  actor_type   text NOT NULL,                   -- user | agent | system | plugin
  actor_id     text NOT NULL,
  action       text NOT NULL,                   -- draft.created, identity.merged, auth.pin_verified …
  target_type  text, target_id text, target_label text,
  autonomy_level smallint,
  result       text NOT NULL,                   -- ok | held | blocked | failed
  detail       jsonb NOT NULL DEFAULT '{}',
  ip           inet,
  prev_hash    bytea,
  row_hash     bytea NOT NULL,
  PRIMARY KEY (id, at)
) PARTITION BY RANGE (at);
CREATE INDEX ON ops.action_log (org_id, at DESC);
CREATE INDEX ON ops.action_log (org_id, actor_type, actor_id, at DESC);
CREATE INDEX ON ops.action_log (org_id, action, at DESC);
SELECT partman.create_parent('ops.action_log', 'at', '1 month', p_premake => 3);
CREATE TRIGGER action_log_append_only BEFORE UPDATE OR DELETE ON ops.action_log
  FOR EACH ROW EXECUTE FUNCTION core.forbid_mutation();

CREATE TABLE ops.policy_boundaries (
  org_id       uuid NOT NULL,
  code         text NOT NULL,                   -- listen_authorized_only | disclose_staff_observation | …
  is_enabled   boolean NOT NULL,
  is_locked    boolean NOT NULL DEFAULT false,  -- không tắt được theo thiết kế
  params       jsonb NOT NULL DEFAULT '{}',     -- {"approval_threshold_vnd":50000000}
  PRIMARY KEY (org_id, code)
);

CREATE TABLE ops.retention_policies (
  org_id       uuid NOT NULL,
  dataset      text NOT NULL,                   -- raw.events | clean.meaning_units | ops.plugin_logs | …
  keep_days    int,                             -- NULL = giữ vĩnh viễn
  anonymize_after_days int,
  PRIMARY KEY (org_id, dataset)
);

CREATE TABLE ops.data_requests (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  person_id    uuid NOT NULL REFERENCES core.persons(id),
  kind         text NOT NULL,                   -- export | erase | restrict
  status       text NOT NULL DEFAULT 'open',
  requested_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz
);

-- Tiến độ trình thiết lập Owner (docs/06)
CREATE TABLE ops.setup_state (
  org_id       uuid PRIMARY KEY,
  step         smallint NOT NULL DEFAULT 1,
  completed    jsonb NOT NULL DEFAULT '{}',     -- {"owner":true,"channels":{"zalo":"active"}}
  setup_token_hash bytea,                       -- token một lần do trình cài sinh
  finished_at  timestamptz
);

CREATE TABLE ops.saved_views (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  user_id      uuid NOT NULL REFERENCES core.users(id),
  screen       text NOT NULL,
  name         text NOT NULL,
  filters      jsonb NOT NULL
);

-- updated_at triggers
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['core.organizations','core.users','core.groups','core.persons','biz.opportunities','agent.identities','ops.plugins'] LOOP
    EXECUTE format('CREATE TRIGGER touch BEFORE UPDATE ON %s FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at()', t);
  END LOOP;
END $$;

-- ═══ ANALYTICS — lớp thống kê ═════════════════════════════════════════════

CREATE TABLE analytics.dim_date (
  d date PRIMARY KEY, year int, quarter int, month int, week int, dow int, is_weekend boolean
);
INSERT INTO analytics.dim_date
SELECT d::date, extract(year FROM d), extract(quarter FROM d), extract(month FROM d),
       extract(week FROM d), extract(isodow FROM d), extract(isodow FROM d) IN (6,7)
FROM generate_series('2024-01-01'::date, '2035-12-31'::date, '1 day') d;

CREATE MATERIALIZED VIEW analytics.mv_daily_group_activity AS
SELECT e.org_id, e.group_id, (e.occurred_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS d,
       count(*) AS raw_events, count(DISTINCT e.sender_identity_id) AS active_senders
FROM raw.events e GROUP BY 1,2,3;
CREATE UNIQUE INDEX ON analytics.mv_daily_group_activity (org_id, group_id, d);

CREATE MATERIALIZED VIEW analytics.mv_hourly_activity AS
SELECT org_id, date_trunc('hour', occurred_at) AS h, count(*) AS events
FROM raw.events GROUP BY 1,2;
CREATE UNIQUE INDEX ON analytics.mv_hourly_activity (org_id, h);

CREATE MATERIALIZED VIEW analytics.mv_daily_meaning AS
SELECT org_id, event_type, side, (observed_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS d,
       count(*) AS units, avg(confidence) AS avg_conf
FROM clean.meaning_units WHERE superseded_by IS NULL GROUP BY 1,2,3,4;
CREATE UNIQUE INDEX ON analytics.mv_daily_meaning (org_id, event_type, side, d);

CREATE MATERIALIZED VIEW analytics.mv_opportunity_funnel AS
SELECT o.org_id, o.stage, count(*) AS n, sum(o.value_vnd) AS value_vnd,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM (o.first_contact_at - o.first_signal_at))/60)
         FILTER (WHERE o.first_contact_at IS NOT NULL) AS median_signal_to_contact_min,
       count(*) FILTER (WHERE o.owner_user_id IS NULL AND o.stage NOT IN ('won','lost','dormant')) AS unowned
FROM biz.opportunities o GROUP BY 1,2;
CREATE UNIQUE INDEX ON analytics.mv_opportunity_funnel (org_id, stage);

CREATE MATERIALIZED VIEW analytics.mv_model_usage_daily AS
SELECT org_id, model_id, (at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS d,
       count(*) AS calls, sum(tokens_in) AS tokens_in, sum(tokens_out) AS tokens_out,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
       count(*) FILTER (WHERE status <> 'ok') AS non_ok, sum(cost_vnd) AS cost_vnd
FROM agent.model_calls GROUP BY 1,2,3;
CREATE UNIQUE INDEX ON analytics.mv_model_usage_daily (org_id, model_id, d);

-- Làm mới: worker gọi REFRESH MATERIALIZED VIEW CONCURRENTLY theo lịch (5–15 phút) — xem docs/03.
