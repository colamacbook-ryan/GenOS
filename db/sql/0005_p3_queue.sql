-- Gen-Harness · giai đoạn 3 · Hàng đợi & Hành động
-- Giữ quy ước docs/handoff/03-database.md: UUIDv7, org_id, timestamptz, không ENUM.
--
-- `biz.alerts` (cảnh báo sớm, spec E9) đã có từ 0002_phase1.sql (đã được refinery R-03 và ModelRouter dùng) —
-- không tạo lại ở đây. Cụm này chỉ hợp nhất hàng đợi và thêm chỗ lưu "im lặng có chủ đích".

-- ─── Hàng đợi hợp nhất (PLAN §12: `biz.inbox_items` view) ────────────────────────────────────────────────
-- Nguồn: đơn vị ý nghĩa mới (unit), cảnh báo đang mở (alert), bản nháp chờ duyệt (draft). Việc & Nhắc hẹn có
-- màn riêng (biz.tasks) nên không hợp nhất vào đây; khối "Hàng đợi" của Tổng quan lấy thêm "Đến hạn" trực tiếp
-- từ biz.tasks khi dựng câu trả lời (không qua view này) vì hình dạng khác hẳn (có due_at, không có confidence).
CREATE VIEW biz.inbox_items AS
SELECT mu.org_id, 'unit'::text AS item_type, mu.id AS item_id, NULL::text AS code,
       mu.event_type AS title, mu.conclusion AS summary,
       CASE WHEN mu.person_id IS NOT NULL THEN 'person' WHEN mu.group_id IS NOT NULL THEN 'group' END AS subject_type,
       COALESCE(mu.person_id, mu.group_id) AS subject_id,
       mu.group_id, mu.person_id,
       CASE WHEN mu.event_type = 'Complained' THEN 'P1' WHEN mu.confidence >= 0.8 THEN 'P2' ELSE 'P3' END AS priority,
       mu.observed_at AS created_at, mu.confidence AS score
FROM clean.meaning_units mu
WHERE mu.superseded_by IS NULL

UNION ALL

SELECT a.org_id, 'alert'::text, a.id, a.code, a.title, a.summary,
       a.subject_type, a.subject_id,
       CASE WHEN a.subject_type = 'group' THEN a.subject_id END,
       CASE WHEN a.subject_type = 'person' THEN a.subject_id END,
       a.priority, a.created_at, NULL::numeric
FROM biz.alerts a
WHERE a.status = 'open'

UNION ALL

SELECT d.org_id, 'draft'::text, d.id, d.code, COALESCE(d.title, d.kind), d.body->>'text',
       d.subject_type, d.subject_id, d.group_id,
       CASE WHEN d.subject_type = 'person' THEN d.subject_id END,
       CASE WHEN (d.flags->>'over_threshold')::boolean THEN 'P1' ELSE 'P2' END,
       d.created_at, NULL::numeric
FROM biz.action_drafts d
WHERE d.status = 'pending';

-- ─── Im lặng có chủ đích (inbox: "Im lặng có chủ đích", handoff/01 §inbox) ────────────────────────────────
-- Một dòng còn hiệu lực = item không hiện trong hàng đợi nữa cho tới `until` (NULL = mãi mãi, tới khi bỏ tay).
-- Ghi đè (im lặng lại) bằng ON CONFLICT; không phải bảng chỉ-INSERT vì đây là trạng thái hiện tại, không phải sổ.
CREATE TABLE biz.queue_silences (
  org_id      uuid NOT NULL,
  item_type   text NOT NULL,                    -- unit | alert | draft
  item_id     uuid NOT NULL,
  reason      text,
  until       timestamptz,                       -- NULL = im lặng vô thời hạn
  silenced_by uuid NOT NULL REFERENCES core.users(id),
  silenced_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (item_type, item_id)
);
CREATE INDEX ON biz.queue_silences (org_id, until);
