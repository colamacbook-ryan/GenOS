-- v0.1.77 — gói P2 «ưu tiên» (spec r7.2 §4.2.1 (h)). CHỈ THÊM, mọi câu lệnh chạy lại an toàn.
--   a) biz.signal_feedback: reason (nút Sai 3 lý do, CNS-168) + rule_version (M1 R4 chỉ đếm 'r7', CNS-187)
--   b) settings Tình báo ở core.organizations.settings -> 'intel' (jsonb_set CHỈ KHI khoá chưa có, CNS-187):
--      work_start, work_end, ramp_started_at; đặt lại ramp_gates = 'digest_top' đúng MỘT lần (ramp_reset_at đánh dấu)
--   c) KHÔNG đổi alerts_gate_chk / alerts_delivery_chk / alerts_lane_chk / meaning_units_gate_candidate_chk / policy bối cảnh;
--      biz.alerts.level_input (jsonb đã có) và deadline_granularity (entities.v) không cần cột mới.

-- ─── a) biz.signal_feedback ─────────────────────────────────────────────────
ALTER TABLE biz.signal_feedback ADD COLUMN IF NOT EXISTS reason text;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'signal_feedback_reason_chk' AND conrelid = 'biz.signal_feedback'::regclass) THEN
    ALTER TABLE biz.signal_feedback ADD CONSTRAINT signal_feedback_reason_chk
      CHECK (reason IS NULL OR reason IN ('still_buying', 'not_urgent', 'staff_handled'));
  END IF;
END $$;
-- Dòng cũ nhận 'r6' (DEFAULT lúc thêm cột); sau đó DEFAULT đổi sang 'r7' để lệnh ghi quên gán không bị loại khỏi M1.
ALTER TABLE biz.signal_feedback ADD COLUMN IF NOT EXISTS rule_version text NOT NULL DEFAULT 'r6';
ALTER TABLE biz.signal_feedback ALTER COLUMN rule_version SET DEFAULT 'r7';

-- ─── b) settings.intel ──────────────────────────────────────────────────────
UPDATE core.organizations SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb), '{intel}', '{}'::jsonb)
 WHERE jsonb_typeof(settings -> 'intel') IS DISTINCT FROM 'object';

UPDATE core.organizations SET settings = jsonb_set(settings, '{intel,work_start}', '"08:00"'::jsonb)
 WHERE NOT (settings -> 'intel' ? 'work_start');
UPDATE core.organizations SET settings = jsonb_set(settings, '{intel,work_end}', '"17:30"'::jsonb)
 WHERE NOT (settings -> 'intel' ? 'work_end');
UPDATE core.organizations SET settings = jsonb_set(settings, '{intel,ramp_started_at}', to_jsonb(now()))
 WHERE NOT (settings -> 'intel' ? 'ramp_started_at');

-- Chạy thử 14 ngày lại (R4): đặt lại ramp_gates một lần; Sếp chọn push sau đó mới có hiệu lực. Lần chạy lại: đã có ramp_reset_at ⇒ bỏ qua.
UPDATE core.organizations
   SET settings = jsonb_set(jsonb_set(settings, '{intel,ramp_gates}', '"digest_top"'::jsonb), '{intel,ramp_reset_at}', to_jsonb(now()))
 WHERE NOT (settings -> 'intel' ? 'ramp_reset_at');
