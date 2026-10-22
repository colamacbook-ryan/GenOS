-- Gen-Harness · giai đoạn 3 · nền chung (bản nháp, góc nhìn, chứng cứ)
-- Giữ quy ước docs/handoff/03-database.md: UUIDv7, org_id, timestamptz, không ENUM.

-- Bàn làm việc: tiêu đề, loại hành động trong registry của chassis, người tạo, kết quả gửi.
ALTER TABLE biz.action_drafts
  ADD COLUMN title           text,
  ADD COLUMN action_key      text,                    -- khoá registry policy (message.send, quotation.send…)
  ADD COLUMN created_by      uuid REFERENCES core.users(id),
  ADD COLUMN decision_reason text,
  ADD COLUMN send_result     jsonb,                   -- {"ok":true,"error":null,"external_msg_id":"…","at":"…"}
  ADD COLUMN sent_at         timestamptz,
  ADD COLUMN parent_draft_id uuid REFERENCES biz.action_drafts(id),
  ADD COLUMN updated_at      timestamptz NOT NULL DEFAULT now();
CREATE INDEX ON biz.action_drafts (org_id, status, created_at DESC);
CREATE INDEX ON biz.action_drafts (subject_type, subject_id);

-- Góc nhìn đã lưu: theo tổ chức, tên không trùng trên cùng màn của một người.
ALTER TABLE ops.saved_views
  ADD COLUMN org_id     uuid REFERENCES core.organizations(id),
  ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
CREATE UNIQUE INDEX ON ops.saved_views (user_id, screen, lower(name));

-- Quyết định của agent: truy theo bản nháp.
CREATE INDEX ON agent.decisions (draft_id) WHERE draft_id IS NOT NULL;
