-- Gen-Harness · giai đoạn 3 · Bản đồ quan hệ (graph)
-- Giữ quy ước docs/handoff/03-database.md: UUIDv7, org_id, timestamptz, không ENUM.
--
-- Bảng gốc đã có từ giai đoạn 1: `clean.relationships` (cạnh — kind interacts | shares_members | owns |
-- bridges, xem docs/api/phase-3-graph.md), `core.group_members` (thành viên nhóm), `ops.saved_views` (tái dùng
-- để lưu vị trí node: screen='graph', name='layout:<mode>', filters={"positions": {...}} — không thêm bảng
-- mới, xem lý do ở docs/api/phase-3-graph.md §Lưu vị trí). Cụm này chỉ thêm chỉ mục còn thiếu cho truy vấn hai
-- chiều của cạnh và tra cứu nhóm theo người (đồ thị hai phía person↔group dùng để dựng Nhóm↔Nhóm/cầu nối).

CREATE INDEX ON clean.relationships (org_id, from_type, from_id);
CREATE INDEX ON clean.relationships (org_id, kind, window_days);
CREATE INDEX ON core.group_members (person_id) WHERE left_at IS NULL;
