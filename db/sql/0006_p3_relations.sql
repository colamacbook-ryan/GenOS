-- Gen-Harness · giai đoạn 3 · Quan hệ & Đối tượng (Nhóm & Con người, Hồ sơ sống, Sổ tay nhận thức, Tài liệu)
-- Giữ quy ước docs/handoff/03-database.md: UUIDv7, org_id, timestamptz, không ENUM.
--
-- Phần lớn bảng cần cho cụm này đã có từ giai đoạn 1/2: core.persons, core.groups, core.person_identities,
-- core.assignments, memory.notebooks/entries/compactions, clean.current_scores, clean.relationships,
-- biz.documents, biz.document_acl, core.identity_merge_log. Cụm này chỉ ALTER/thêm chỉ mục còn thiếu.

-- ─── Tài liệu ────────────────────────────────────────────────────────────────
-- `storage_key` đã có; thêm mô tả ngắn + soft-delete (Tài liệu không phải bảng chỉ-INSERT — có thể gỡ/thay ACL).
ALTER TABLE biz.documents
  ADD COLUMN description text,
  ADD COLUMN updated_at  timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN deleted_at  timestamptz;
CREATE INDEX ON biz.documents (org_id, owner_group_id) WHERE deleted_at IS NULL;
CREATE INDEX ON biz.documents (org_id, owner_person_id) WHERE deleted_at IS NULL;
CREATE INDEX ON biz.document_acl (principal);

-- ─── Hồ sơ sống / Hợp nhất danh tính ──────────────────────────────────────────
-- "Xem lịch sử hợp nhất" của một hồ sơ tra theo cả hai chiều (từ/sang) — thêm chỉ mục còn thiếu.
CREATE INDEX ON core.identity_merge_log (from_person);
CREATE INDEX ON core.identity_merge_log (to_person);

-- ─── Sổ tay nhận thức ─────────────────────────────────────────────────────────
-- Danh sách chủ thể có sổ tay (nbSubjects: theo người/theo nhóm, mới cập nhật trước) — chỉ mục theo loại.
CREATE INDEX ON memory.notebooks (org_id, subject_type, last_compacted_at DESC NULLS LAST);

-- ─── Nhóm & Con người ─────────────────────────────────────────────────────────
-- Lọc "Giá trị" (tổng giá trị cơ hội đang mở của một người) và "Ưu tiên" đọc trực tiếp từ biz.opportunities /
-- biz.inbox_items (view của cụm queue) nên không cần cột mới trên core.persons. BOT + mức tự trị riêng từng
-- người dùng `core.persons.attrs.agent_id` / `attrs.autonomy_level` — cùng quy ước `attrs.autonomy_level` đã
-- dùng ở `gh.biz.core.drafts.effective_level` (giai đoạn 2/3 core) nên không cần cột mới.
CREATE INDEX ON biz.opportunities (org_id, person_id) WHERE closed_at IS NULL;
