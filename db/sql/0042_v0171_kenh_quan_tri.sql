-- Gen-Harness · v0.1.71 (G1 «lược đồ»): bot dùng TÀI KHOẢN RIÊNG, kênh quản trị 1-1 Zalo/WhatsApp, chế độ hỗ trợ nhóm.
-- Thiết kế: ke-hoach/thiet-ke-v0171.md §2 (tên cột/bảng là hợp đồng giữa các gói G2–G8). Mọi câu chạy lại an toàn
-- (IF NOT EXISTS / DROP … IF EXISTS rồi CREATE / ghi mặc định chỉ khi thiếu khoá); không backfill, không đổi dòng cũ.
--
-- a) core.channel_sessions.account_kind — NULL = bản cài cũ = coi như `owner_self` (tài khoản cá nhân của Sếp) ⇒ MỌI
--    tính năng mới tắt, hành vi y v0.1.70. Sếp đổi sang `bot` trên Console (PIN) mới bật.
-- b) core.groups.added_by_* — ai thêm bot vào nhóm. Nhóm cũ để NULL (không đụng).
-- c) Bảng mới (đều có org_id + RLS org_isolation theo app.org_id, GRANT cho gh_app):
--      ops.owner_channels   kênh quản trị 1-1 của Owner (ghép bằng mã 6 số — chỉ lưu BĂM)
--      ops.group_support    lượt bật «chế độ hỗ trợ» của từng nhóm (giữ lịch sử, tối đa một lượt đang bật / nhóm)
--      ops.auto_sends       SỔ mọi tin tự gửi (không qua bản nháp + PIN): chỉ-thêm, UPDATE chỉ 4 cột kết quả, không thân tin
--      ops.owner_outbox     hàng báo cáo chủ động cho kênh quản trị (chỉ sự kiện đặc biệt — Sếp 18/10)
--      ops.owner_questions  câu hỏi đang chờ Owner trả lời qua kênh quản trị
-- d) Mặc định settings.owner_report / settings.support_limits — chỉ ghi khi thiếu khoá.

-- ─── a) loại tài khoản kênh ─────────────────────────────────────────────────
ALTER TABLE core.channel_sessions ADD COLUMN IF NOT EXISTS account_kind text;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'channel_sessions_account_kind_chk') THEN
    ALTER TABLE core.channel_sessions ADD CONSTRAINT channel_sessions_account_kind_chk
      CHECK (account_kind IS NULL OR account_kind IN ('owner_self', 'bot'));
  END IF;
END $$;

-- ─── b) ai thêm bot vào nhóm ────────────────────────────────────────────────
ALTER TABLE core.groups
  ADD COLUMN IF NOT EXISTS added_by_external_id text,
  ADD COLUMN IF NOT EXISTS added_by_kind        text,
  ADD COLUMN IF NOT EXISTS added_at             timestamptz;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'groups_added_by_kind_chk') THEN
    ALTER TABLE core.groups ADD CONSTRAINT groups_added_by_kind_chk
      CHECK (added_by_kind IS NULL OR added_by_kind IN ('owner', 'other', 'unknown'));
  END IF;
END $$;

-- ─── c) bảng mới ────────────────────────────────────────────────────────────

-- Kênh quản trị 1-1: một dòng / (tổ chức, kênh). Nhận diện Owner CHỈ theo ID nền tảng (owner_external_id/alt), không theo tên.
CREATE TABLE IF NOT EXISTS ops.owner_channels (
  org_id             uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  channel_type       text NOT NULL CHECK (channel_type IN ('zalo', 'whatsapp')),
  session_id         uuid REFERENCES core.channel_sessions(id) ON DELETE SET NULL,   -- tài khoản bot đã ghép
  owner_user_id      uuid REFERENCES core.users(id) ON DELETE SET NULL,              -- tài khoản Console của Owner
  owner_external_id  text,                       -- Zalo uid | WA JID (số) — dạng chuẩn hoá (bare)
  owner_external_alt text,                       -- WA LID (hoặc ngược lại); Zalo: NULL
  owner_thread_id    text,                       -- thread 1-1 để gửi (Zalo = uid, WA = jid)
  owner_display      text,                       -- chỉ để hiển thị, KHÔNG dùng nhận diện
  paired_at          timestamptz,
  enabled            boolean NOT NULL DEFAULT false,
  pair_code_hash     bytea,                      -- mã ghép 6 số: chỉ lưu băm có khoá
  pair_expires_at    timestamptz,
  pair_attempts      integer NOT NULL DEFAULT 0,
  pair_user_id       uuid REFERENCES core.users(id) ON DELETE SET NULL,
  report_prefs       jsonb NOT NULL DEFAULT '{}',  -- ngưỡng/giờ yên lặng; thiếu khoá = mặc định (settings.owner_report)
  updated_at         timestamptz NOT NULL DEFAULT now(),
  updated_by         uuid,
  PRIMARY KEY (org_id, channel_type),
  CHECK (owner_external_id IS NULL OR length(owner_external_id) <= 64)
);

-- Lượt bật chế độ hỗ trợ của một nhóm. Tắt = điền disabled_*; bật lại = lượt mới (lịch sử không xoá).
CREATE TABLE IF NOT EXISTS ops.group_support (
  id              uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id          uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  group_id        uuid NOT NULL REFERENCES core.groups(id) ON DELETE CASCADE,
  enabled_at      timestamptz NOT NULL DEFAULT now(),
  enabled_by      uuid,
  enabled_via     text NOT NULL CHECK (enabled_via IN ('console', 'zalo', 'whatsapp')),
  agent_id        uuid REFERENCES agent.identities(id) ON DELETE SET NULL,   -- NULL = danh tính mặc định của tổ chức
  limits          jsonb NOT NULL DEFAULT '{}',                               -- ghi đè settings.support_limits
  until_at        timestamptz,                                               -- NULL = tới khi tắt / bot bị mời ra
  disabled_at     timestamptz,
  disabled_by     uuid,
  disabled_reason text CHECK (disabled_reason IS NULL OR disabled_reason IN
                       ('owner', 'bot_removed', 'group_left', 'session_changed', 'account_kind_changed', 'abuse_guard', 'expired'))
);
CREATE UNIQUE INDEX IF NOT EXISTS group_support_one_active_uq ON ops.group_support (group_id) WHERE disabled_at IS NULL;
CREATE INDEX IF NOT EXISTS group_support_org_idx ON ops.group_support (org_id, enabled_at DESC);

-- Sổ tin tự gửi: nguồn thật để kiểm «chỉ 3 trường hợp». KHÔNG lưu thân tin (chỉ băm + độ dài + mã luật bộ lọc).
CREATE TABLE IF NOT EXISTS ops.auto_sends (
  id              uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id          uuid NOT NULL REFERENCES core.organizations(id),
  basis           text NOT NULL CHECK (basis IN ('owner_dm', 'owner_tag', 'support')),
  channel_type    text NOT NULL,
  session_id      uuid,
  thread_type     text NOT NULL CHECK (thread_type IN ('user', 'group')),
  thread_id       text NOT NULL,
  group_id        uuid,
  trigger_kind    text NOT NULL CHECK (trigger_kind IN ('owner_message', 'raw_event', 'report')),
  trigger_ref     text NOT NULL,
  body_sha256     text NOT NULL,
  body_len        integer NOT NULL,
  guard           jsonb NOT NULL,
  status          text NOT NULL DEFAULT 'issued' CHECK (status IN ('issued', 'sent', 'failed', 'expired')),
  error           text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  sent_at         timestamptz,
  external_msg_id text,
  UNIQUE (org_id, basis, trigger_ref)             -- mỗi tin kích hoạt chỉ một trả lời
);
CREATE INDEX IF NOT EXISTS auto_sends_org_created_idx ON ops.auto_sends (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS auto_sends_group_idx ON ops.auto_sends (group_id, created_at DESC) WHERE group_id IS NOT NULL;

-- Chỉ-thêm: cấm DELETE/TRUNCATE; UPDATE chỉ được đổi status/error/sent_at/external_msg_id (mọi cột khác phải y nguyên).
CREATE OR REPLACE FUNCTION ops.auto_sends_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'ops.auto_sends is append-only (no delete)';
  END IF;
  IF (to_jsonb(NEW) - ARRAY['status', 'error', 'sent_at', 'external_msg_id'])
     IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status', 'error', 'sent_at', 'external_msg_id']) THEN
    RAISE EXCEPTION 'ops.auto_sends is append-only (only status, error, sent_at, external_msg_id may change)';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS auto_sends_append_only ON ops.auto_sends;
CREATE TRIGGER auto_sends_append_only BEFORE UPDATE OR DELETE ON ops.auto_sends
  FOR EACH ROW EXECUTE FUNCTION ops.auto_sends_guard();
DROP TRIGGER IF EXISTS auto_sends_no_truncate ON ops.auto_sends;
CREATE TRIGGER auto_sends_no_truncate BEFORE TRUNCATE ON ops.auto_sends
  FOR EACH STATEMENT EXECUTE FUNCTION core.forbid_truncate();

-- Hàng báo cáo chủ động cho kênh quản trị (giống ops.telegram_outbox). Khử trùng lặp theo (org, kênh, dedupe_key).
CREATE TABLE IF NOT EXISTS ops.owner_outbox (
  id              uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id          uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  channel_type    text NOT NULL CHECK (channel_type IN ('zalo', 'whatsapp')),
  kind            text NOT NULL CHECK (kind IN ('ask', 'alert', 'digest', 'reply')),
  severity        text NOT NULL CHECK (severity IN ('info', 'important', 'urgent')),
  dedupe_key      text,
  text            text NOT NULL,
  buttons         jsonb NOT NULL DEFAULT '[]',    -- chỉ link Console, không nút hành động
  created_at      timestamptz NOT NULL DEFAULT now(),
  not_before      timestamptz NOT NULL DEFAULT now(),
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  attempts        integer NOT NULL DEFAULT 0,
  sent_at         timestamptz,
  failed_code     text
);
CREATE UNIQUE INDEX IF NOT EXISTS owner_outbox_dedupe_uq ON ops.owner_outbox (org_id, channel_type, dedupe_key);
CREATE INDEX IF NOT EXISTS owner_outbox_pending_idx ON ops.owner_outbox (next_attempt_at)
  WHERE sent_at IS NULL AND failed_code IS NULL;

-- Câu hỏi chờ Owner trả lời qua kênh quản trị. Mã ngắn 4 ký tự A-Z (trừ I, O) + 2-9 (không 0, 1) để gõ tay không nhầm.
CREATE TABLE IF NOT EXISTS ops.owner_questions (
  id          uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id      uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  short_code  text NOT NULL CHECK (short_code ~ '^[A-HJ-NP-Z2-9]{4}$'),
  kind        text NOT NULL CHECK (kind IN ('group_added', 'support_confirm', 'listen_confirm')),
  subject_id  uuid,                                -- thường là group_id
  payload     jsonb NOT NULL DEFAULT '{}',
  created_at  timestamptz NOT NULL DEFAULT now(),
  expires_at  timestamptz NOT NULL,
  answered_at timestamptz,
  answer      text
);
CREATE UNIQUE INDEX IF NOT EXISTS owner_questions_open_code_uq ON ops.owner_questions (org_id, short_code)
  WHERE answered_at IS NULL;

-- ─── RLS theo org_id + GRANT cho gh_app (khuôn 0029/0040) ───────────────────
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['owner_channels', 'group_support', 'auto_sends', 'owner_outbox', 'owner_questions'] LOOP
    EXECUTE format('ALTER TABLE ops.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS org_isolation ON ops.%I', t);
    EXECUTE format($p$CREATE POLICY org_isolation ON ops.%I
      USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
             OR current_setting('app.org_id', true) IS NULL
             OR current_setting('app.org_id', true) = '')
      WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
             OR current_setting('app.org_id', true) IS NULL
             OR current_setting('app.org_id', true) = '')$p$, t);
  END LOOP;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gh_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON ops.owner_channels, ops.group_support, ops.owner_outbox, ops.owner_questions TO gh_app;
    GRANT SELECT, INSERT, UPDATE ON ops.auto_sends TO gh_app;
    -- Sổ tự gửi: không cấp DELETE (quyền mặc định của 0014 tự cấp cả DELETE cho bảng mới ⇒ thu lại; trigger vẫn chặn thêm).
    REVOKE DELETE ON ops.auto_sends FROM gh_app;
  END IF;
END $$;

-- ─── d) mặc định cài đặt (chỉ khi thiếu khoá) ───────────────────────────────
-- owner_report: QUYẾT ĐỊNH SẾP 18/10 — KHÔNG báo cáo định kỳ/gộp; bot chỉ chủ động nhắn khi có sự kiện đặc biệt (từ mức
-- «quan trọng»). batch_minutes = 0 (không gộp), digest = false (không bản tóm tắt). Giờ yên lặng 21:00–07:00 (QD-20).
-- support_limits: giới hạn an toàn của chế độ hỗ trợ (thiết kế §7.4).
UPDATE core.organizations
   SET settings = COALESCE(settings, '{}'::jsonb) || jsonb_build_object('owner_report',
         '{"quiet_start": 21, "quiet_end": 7, "batch_minutes": 0, "digest": false, "max_per_hour": 6, "min_severity": "important"}'::jsonb)
 WHERE NOT (COALESCE(settings, '{}'::jsonb) ? 'owner_report');
UPDATE core.organizations
   SET settings = COALESCE(settings, '{}'::jsonb) || jsonb_build_object('support_limits',
         '{"per_group_hour": 20, "per_member_hour": 5, "min_gap_s": 20, "per_channel_day": 150, "max_len": 600, "max_age_s": 300}'::jsonb)
 WHERE NOT (COALESCE(settings, '{}'::jsonb) ? 'support_limits');
