-- Gen-Harness · v0.1.68: "Nhật ký lắng nghe" — một dòng thời gian gộp tin nghe được + sự kiện trên mọi kênh.
-- (1) raw.events.listen_mode — chế độ nghe LÚC tin vào kho (nhóm: tagged_only | silent | proactive; tin 1-1: silent | reply).
--     NULL = tin cũ (trước bản này, không biết). Thêm cột NULL là thay đổi siêu dữ liệu, không ghi lại bảng; bảng thô vẫn
--     chỉ-INSERT (trigger raw_events_append_only không bị đụng).
-- (2) ops.listen_events — sự kiện nhẹ trên kênh mà trước đây không ai ghi: tài khoản vào/ra nhóm, nhóm đổi tên, thành viên
--     vào/rời, phiên kênh nối/ngắt/đăng xuất, người lạ nhắn riêng lần đầu. `detail` chỉ chứa tên/số đếm cần để viết lại câu,
--     KHÔNG chứa nội dung tin. Đọc theo (org_id, at DESC, id DESC) nên chỉ mục đó là chỉ mục duy nhất cần thêm.
-- Chạy lại an toàn: IF NOT EXISTS / DROP POLICY IF EXISTS, theo khuôn 0024.
ALTER TABLE raw.events ADD COLUMN IF NOT EXISTS listen_mode text;

CREATE TABLE IF NOT EXISTS ops.listen_events (
  id           uuid PRIMARY KEY DEFAULT core.uuid_v7(),
  org_id       uuid NOT NULL,
  at           timestamptz NOT NULL DEFAULT now(),
  kind         text NOT NULL,     -- group_new | group_left | group_rejoined | group_renamed | members_changed |
                                  -- groups_synced | channel_connected | channel_error | channel_ended | direct_new
  channel_id   uuid,
  channel_type text,
  group_id     uuid,
  person_id    uuid,
  detail       jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS listen_events_org_at_idx ON ops.listen_events (org_id, at DESC, id DESC);
CREATE INDEX IF NOT EXISTS listen_events_group_idx ON ops.listen_events (group_id, at DESC) WHERE group_id IS NOT NULL;

ALTER TABLE ops.listen_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON ops.listen_events;
CREATE POLICY org_isolation ON ops.listen_events
  USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '')
  WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '');

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gh_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON ops.listen_events TO gh_app;
  END IF;
END $$;
