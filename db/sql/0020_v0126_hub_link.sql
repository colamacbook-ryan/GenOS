-- Gen-Harness · v0.1.26 — Đợt D1 (lát đầu): nối Gen-hub để Gen ĐỌC Kho dữ liệu (docs/design/gen-hub-link.md §3.1).
-- Một dòng / tổ chức. Liên kết trỏ tới một máy chủ trong MCP Hub sẵn có (`agent.mcp_servers`) — token agent của
-- Gen-hub KHÔNG nằm ở bảng này: nó được mã hoá phong bì (gh.crypto, AAD `mcp_server_auth`) trong
-- `agent.mcp_servers.auth_enc` như mọi máy chủ MCP khác, không bao giờ lưu dạng rõ, không ghi log, không trả qua API.
-- Không có bảng nào chứa dữ liệu Kho (không chép Kho vào Postgres — chỉ đệm Redis 5 phút).
-- `enabled` mặc định false: tính năng TẮT tới khi Owner cấu hình và bấm "Kiểm tra" xanh.
CREATE TABLE IF NOT EXISTS agent.hub_links (
  org_id            uuid PRIMARY KEY REFERENCES core.organizations(id) ON DELETE CASCADE,
  server_id         uuid REFERENCES agent.mcp_servers(id) ON DELETE SET NULL,
  enabled           boolean NOT NULL DEFAULT false,
  token_expires_at  timestamptz,                         -- Owner nhập (token thủ công Gen-hub hết hạn 90 ngày)
  expiry_notified_at timestamptz,                        -- lần nhắc sắp hết hạn gần nhất (job nhắc 1 lần / token)
  last_ok_at        timestamptz,
  last_error        text,
  updated_by        uuid,
  updated_at        timestamptz NOT NULL DEFAULT now()
);

-- RLS theo org_id như 0012/0016/0017/0019 (lớp phòng thủ thứ hai cho đường request web).
ALTER TABLE agent.hub_links ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON agent.hub_links;
CREATE POLICY org_isolation ON agent.hub_links
  USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '')
  WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '');

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gh_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON agent.hub_links TO gh_app;
  END IF;
END $$;
