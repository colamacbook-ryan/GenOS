-- Gen-Harness · v0.1.50 (F-81, F-87, QD-18): "Gen nhớ" (ghi chú sở thích của Sếp) + Gen đề xuất ghi Kho dữ liệu có Xác nhận + PIN
-- + job đề xuất Phiên mỗi bản mới. Chạy lại an toàn (IF NOT EXISTS / DROP ... IF EXISTS), theo khuôn 0031.
-- a) agent.gen_memory_notes: tối đa 30 ghi chú / tổ chức (giới hạn ở code, gh/gen/memory_notes.py); mỗi ghi chú <= 280 ký tự.
--    proposal_id UNIQUE: một đề xuất Gen chỉ lưu được MỘT lần (xác nhận hai lần -> 409). Chỉ Owner đọc/ghi (kiểm ở API).
CREATE TABLE IF NOT EXISTS agent.gen_memory_notes (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  text         text NOT NULL CHECK (char_length(text) BETWEEN 1 AND 280),
  reason       text CHECK (reason IS NULL OR char_length(reason) <= 200),
  source       text NOT NULL CHECK (source IN ('gen', 'owner')),
  proposal_id  uuid UNIQUE,
  created_by   uuid,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_by   uuid,
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS gen_memory_notes_org_idx ON agent.gen_memory_notes (org_id, created_at);

ALTER TABLE agent.gen_memory_notes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON agent.gen_memory_notes;
CREATE POLICY org_isolation ON agent.gen_memory_notes
  USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '')
  WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '');

-- b) agent.hub_release_proposals: mỗi (tổ chức, phiên bản) đề xuất ghi Phiên vào Kho ĐÚNG MỘT lần (F-87).
--    pending -> writing (một Owner đang ghi) -> written | pending (ghi lỗi chắc chắn, thử lại được) | uncertain (lỗi mạng /
--    timeout / 5xx SAU khi đã gửi: chưa chắc đã ghi — CHỈ Owner đó (uncertain_by) bấm lại hoặc huỷ, Owner khác bị chặn) |
--    cancelled | expired.
CREATE TABLE IF NOT EXISTS agent.hub_release_proposals (
  org_id        uuid NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
  version       text NOT NULL CHECK (version ~ '^v\d+\.\d+\.\d+$'),
  status        text NOT NULL CHECK (status IN ('pending', 'writing', 'uncertain', 'written', 'cancelled', 'expired')),
  proposal_ids  uuid[] NOT NULL DEFAULT '{}',
  kho_ma        text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  decided_at    timestamptz,
  decided_by    uuid,
  uncertain_by  uuid,
  PRIMARY KEY (org_id, version)
);
-- Bản dựng trước có bảng thiếu cột / ràng buộc trạng thái cũ: thêm cột + thay ràng buộc (chạy lại an toàn).
ALTER TABLE agent.hub_release_proposals ADD COLUMN IF NOT EXISTS uncertain_by uuid;
ALTER TABLE agent.hub_release_proposals DROP CONSTRAINT IF EXISTS hub_release_proposals_status_check;
ALTER TABLE agent.hub_release_proposals ADD CONSTRAINT hub_release_proposals_status_check
  CHECK (status IN ('pending', 'writing', 'uncertain', 'written', 'cancelled', 'expired'));

ALTER TABLE agent.hub_release_proposals ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON agent.hub_release_proposals;
CREATE POLICY org_isolation ON agent.hub_release_proposals
  USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '')
  WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '');

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gh_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON agent.gen_memory_notes TO gh_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON agent.hub_release_proposals TO gh_app;
  END IF;
END $$;
