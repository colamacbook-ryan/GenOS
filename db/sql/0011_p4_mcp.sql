-- Gen-Harness · giai đoạn 4.3 (MCP Hub) + 4.4 (Plugin & Tiện ích)
-- Giữ quy ước docs/handoff/03-database.md: UUIDv7, org_id, timestamptz, không ENUM.
--
-- `agent.mcp_servers` / `agent.mcp_tools` / `agent.mcp_grants` / `agent.mcp_calls` ĐÃ có từ 0001_baseline.sql
-- (docs/handoff/schema.sql §10, đã bỏ ở v0.1.48 — xem lịch sử git) — cụm này chỉ thêm chỉ mục còn thiếu cho truy
-- vấn thật + cột cho luồng nạp
-- plugin từ tệp (spec 4.4, ARCHITECTURE §6.4 "Cài: kiểm chữ ký, hiện danh sách quyền xin, yêu cầu PIN").

-- ─── MCP Hub: chỉ mục cho liệt kê / phân trang / khoá cứng #4 ─────────────────
CREATE INDEX ON agent.mcp_calls (org_id, at DESC);
CREATE INDEX ON agent.mcp_tools (server_id);
CREATE INDEX ON agent.mcp_grants (agent_key);

-- ─── Plugin cài từ tệp (local_file): trạng thái chờ duyệt quyền ───────────────
-- Phạm vi tối thiểu (không chạy mã tải lên trong tiến trình làm việc): cài xong luôn ở trạng thái 'pending' và
-- is_enabled = false — chưa có bước "duyệt xong thì chạy" vì chưa có cơ chế nạp runtime thật cho local_file
-- (khác plugin nền đọc từ đĩa lúc khởi động qua `read_manifests`). `code_sha256` chỉ lưu vân tay để đối chiếu chữ
-- ký, KHÔNG lưu mã nguồn plugin trong CSDL.
ALTER TABLE ops.plugins
  ADD COLUMN permissions_status text NOT NULL DEFAULT 'approved'
    CHECK (permissions_status IN ('approved', 'pending')),
  ADD COLUMN code_sha256 bytea;
