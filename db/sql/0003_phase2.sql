-- Gen-Harness · giai đoạn 2 · tầng dữ liệu, kênh, bộ não AI (PLAN 2.1–2.9)
-- Giữ quy ước: UUIDv7, org_id, timestamptz, core.lookup thay ENUM, kho thô chỉ-INSERT.

-- ─── Kho thô ────────────────────────────────────────────────────────────────

-- Mã công khai RAW-918422: số thứ tự tăng dần, gán lúc INSERT (không cần UPDATE).
CREATE SEQUENCE raw.events_seq;
ALTER TABLE raw.events ADD COLUMN seq bigint NOT NULL DEFAULT nextval('raw.events_seq');
ALTER TABLE raw.events ADD COLUMN direction text NOT NULL DEFAULT 'inbound';   -- inbound | outbound
ALTER TABLE raw.events ADD COLUMN mentions_agent boolean NOT NULL DEFAULT false; -- tin tag agent → đường nhanh
CREATE INDEX ON raw.events (org_id, seq DESC);

-- Khử trùng: khoá (kênh, mã tin phía nền tảng) không phụ thuộc thời điểm nhận.
-- Bảng khoá cũng chỉ-INSERT, như kho thô.
CREATE TABLE raw.event_keys (
  channel_id      uuid NOT NULL,
  external_msg_id text NOT NULL,
  event_id        uuid NOT NULL,
  received_at     timestamptz NOT NULL,
  PRIMARY KEY (channel_id, external_msg_id)
);
CREATE TRIGGER event_keys_append_only BEFORE UPDATE OR DELETE ON raw.event_keys
  FOR EACH ROW EXECUTE FUNCTION core.forbid_mutation();
CREATE TRIGGER event_keys_no_truncate BEFORE TRUNCATE ON raw.event_keys
  FOR EACH STATEMENT EXECUTE FUNCTION core.forbid_truncate();

-- ─── Sàng lọc ───────────────────────────────────────────────────────────────

ALTER TABLE refinery.event_state
  ADD COLUMN label      text,                     -- nhãn hiển thị ở Kho thô (event_type chính)
  ADD COLUMN confidence numeric(4,3),
  ADD COLUMN fast       boolean NOT NULL DEFAULT false,  -- đường nhanh: tag agent / tin 1-1
  ADD COLUMN attempts   int NOT NULL DEFAULT 0,
  ADD COLUMN detail     jsonb NOT NULL DEFAULT '{}';     -- {"rules":["R-01"],"discarded_by":"R-06","error":…}
CREATE INDEX ON refinery.event_state (org_id, event_received_at) WHERE state = 'pending';
CREATE INDEX ON refinery.event_state (org_id, state, updated_at DESC);

ALTER TABLE refinery.runs
  ADD COLUMN requested_by uuid REFERENCES core.users(id),
  ADD COLUMN processed    int NOT NULL DEFAULT 0,
  ADD COLUMN error        text;
CREATE INDEX ON refinery.runs (org_id, started_at DESC);
CREATE INDEX ON refinery.runs (org_id, status) WHERE status IN ('queued', 'running');

ALTER TABLE refinery.rules
  ADD COLUMN created_at timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();

-- Số lượt khớp theo giờ (đọc nhanh "1.842 lượt / 24h").
CREATE TABLE refinery.rule_hits_hourly (
  rule_id  uuid NOT NULL REFERENCES refinery.rules(id) ON DELETE CASCADE,
  hour     timestamptz NOT NULL,
  hits     int NOT NULL DEFAULT 0,
  PRIMARY KEY (rule_id, hour)
);

-- ─── Kho sạch ───────────────────────────────────────────────────────────────

ALTER TABLE clean.meaning_units
  ADD COLUMN score  smallint,                         -- điểm tổng theo trọng số 0–100
  ADD COLUMN scores jsonb NOT NULL DEFAULT '{}',      -- {"heat":80,"potential":60,…}
  ADD COLUMN rule_codes text[] NOT NULL DEFAULT '{}';
CREATE INDEX ON clean.meaning_units (org_id, observed_at DESC) WHERE superseded_by IS NULL;

-- ─── Danh tính ─────────────────────────────────────────────────────────────

ALTER TABLE core.identity_merge_candidates
  ADD COLUMN basis_text text,
  ADD COLUMN evidence   jsonb NOT NULL DEFAULT '[]';   -- [{"raw_event_id","received_at","note"}]
CREATE UNIQUE INDEX ON core.identity_merge_candidates (LEAST(identity_a, identity_b), GREATEST(identity_a, identity_b));

ALTER TABLE core.identity_merge_log
  ADD COLUMN org_id       uuid REFERENCES core.organizations(id),
  ADD COLUMN candidate_id uuid REFERENCES core.identity_merge_candidates(id),
  ADD COLUMN snapshot     jsonb NOT NULL DEFAULT '{}',  -- đủ để đảo ngược: identities, meaning units, sổ tay
  ADD COLUMN reverted_at  timestamptz,
  ADD COLUMN reverted_by  uuid REFERENCES core.users(id);
CREATE INDEX ON core.identity_merge_log (org_id, at DESC);

-- ─── Sổ tay ─────────────────────────────────────────────────────────────────

ALTER TABLE memory.entries
  ADD COLUMN replaced_by uuid,                          -- sửa → bản mới, bản cũ lưu trữ
  ADD COLUMN tokens      int NOT NULL DEFAULT 0;
ALTER TABLE memory.notebooks
  ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();

-- ─── Kênh ───────────────────────────────────────────────────────────────────

ALTER TABLE core.channel_sessions
  ADD COLUMN org_id uuid REFERENCES core.organizations(id),
  ADD COLUMN risk_accepted_by uuid REFERENCES core.users(id),
  ADD COLUMN external_account text;                      -- uid Zalo / số WhatsApp của tài khoản đăng nhập
CREATE INDEX ON core.channel_sessions (channel_id) WHERE ended_at IS NULL;

-- ─── Bộ não AI ─────────────────────────────────────────────────────────────

ALTER TABLE agent.providers
  ADD COLUMN created_at timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN last_test  jsonb;                           -- {"ok":true,"latency_ms":812,"at":"…","error":null}
ALTER TABLE agent.models ADD COLUMN is_enabled boolean NOT NULL DEFAULT true;
CREATE INDEX ON agent.model_calls (org_id, at DESC);

-- ─── Bảng tra cứu ───────────────────────────────────────────────────────────

INSERT INTO core.lookup (kind, code, label_vi, sort_order) VALUES
  ('event_type', 'AskedPrice', 'Hỏi giá / nhu cầu mua', 1),
  ('event_type', 'OfferedSupply', 'Chào nguồn cung', 2),
  ('event_type', 'RequestedPartnership', 'Đề nghị hợp tác', 3),
  ('event_type', 'Complained', 'Phàn nàn', 4),
  ('event_type', 'PromisedDelivery', 'Hứa giao hàng', 5),
  ('event_type', 'ScheduledMeeting', 'Hẹn gặp', 6),
  ('event_type', 'SentQuotation', 'Gửi báo giá', 7),
  ('event_type', 'SentDocument', 'Gửi tài liệu', 8),
  ('event_type', 'MentionsCompetitor', 'Nhắc đối thủ', 9),
  ('event_type', 'ComparedVendor', 'So sánh nhà cung cấp', 10),
  ('event_type', 'RequestedSample', 'Xin mẫu', 11),
  ('event_type', 'AskedStatus', 'Hỏi tiến độ', 12),
  ('event_type', 'JobSignal', 'Tín hiệu tìm việc', 13),
  ('event_type', 'DealWon', 'Chốt đơn', 14),
  ('event_type', 'WentSilent', 'Im lặng', 15),
  ('rule_kind', 'intent', 'Ý định', 1),
  ('rule_kind', 'risk', 'Rủi ro', 2),
  ('rule_kind', 'competition', 'Cạnh tranh', 3),
  ('rule_kind', 'hr', 'Nhân sự', 4),
  ('rule_kind', 'hygiene', 'Vệ sinh', 5),
  ('rule_kind', 'custom', 'Tuỳ chỉnh', 6),
  ('score_dimension', 'heat', 'Độ nóng của tín hiệu', 1),
  ('score_dimension', 'potential', 'Tiềm năng giá trị', 2),
  ('score_dimension', 'churn_risk', 'Rủi ro mất khách', 3),
  ('score_dimension', 'fit', 'Mức độ phù hợp', 4),
  ('score_dimension', 'engagement', 'Độ gắn kết lịch sử', 5),
  ('score_dimension', 'data_confidence', 'Độ tin cậy dữ liệu', 6),
  ('channel_type', 'zalo', 'Zalo', 1),
  ('channel_type', 'whatsapp', 'WhatsApp', 2),
  ('channel_type', 'telegram', 'Telegram', 3),
  ('channel_type', 'linkedin', 'LinkedIn', 4)
ON CONFLICT (kind, code) DO UPDATE SET label_vi = EXCLUDED.label_vi, sort_order = EXCLUDED.sort_order;
