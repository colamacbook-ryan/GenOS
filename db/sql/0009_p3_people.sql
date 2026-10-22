-- Gen-Harness · giai đoạn 3 · Con người & Chất lượng
-- Giữ quy ước docs/handoff/03-database.md: UUIDv7, org_id, timestamptz, không ENUM.
--
-- Mọi bảng gốc đã có từ giai đoạn 1: biz.people_reviews, biz.review_disputes, biz.promises (xem
-- docs/handoff/schema.sql — đã bỏ ở v0.1.48, xem lịch sử git; nay ở 0001_baseline.sql). Cụm này chỉ ALTER cột
-- còn thiếu + thêm chỉ mục — không tạo lại bảng nào.

-- ─── Đánh giá con người: sửa điểm tay giữ lịch sử ──────────────────────────────
-- KHÔNG ghi đè `biz.people_reviews` khi sửa tay: mỗi lần sửa chèn một dòng mới, `supersedes_id` trỏ về dòng
-- trước, `overridden_by/overridden_at/override_reason` đánh dấu đây là bản sửa tay (không phải hệ thống tự
-- tính). "Bản hiện hành" của một (người, kỳ) = dòng mới nhất theo `created_at` trong cùng
-- (person_id, period_start, period_end) — xem `gh/biz/people/routes.py`.
--
-- Việc tự động tính lại điểm mỗi kỳ (`gh.biz.people.jobs.recompute_people_reviews_org`) chỉ UPDATE tại-chỗ đúng
-- dòng hệ thống của kỳ đó (`overridden_by IS NULL`) qua `ON CONFLICT` trên chỉ mục riêng phần dưới — không sinh
-- rác lịch sử mỗi lần chạy lại, và không bao giờ âm thầm đè lên một bản Owner đã sửa tay.
ALTER TABLE biz.people_reviews
  ADD COLUMN supersedes_id   uuid REFERENCES biz.people_reviews(id),
  ADD COLUMN overridden_by   uuid REFERENCES core.users(id),
  ADD COLUMN overridden_at   timestamptz,
  ADD COLUMN override_reason text;

-- Khoá cứng 7 (ARCHITECTURE §7.4): điểm số nhân sự phải có chứng cứ — cùng cách `biz.alerts` đã ràng buộc
-- `personnel_related` ở `db/sql/0002_phase1.sql`. `biz.people_reviews` luôn "personnel_related" nên CHECK thẳng,
-- không cần cột cờ riêng.
ALTER TABLE biz.people_reviews
  ADD CONSTRAINT people_reviews_evidence_nonempty CHECK (jsonb_array_length(evidence) > 0);

CREATE UNIQUE INDEX people_reviews_system_period ON biz.people_reviews (org_id, person_id, period_start, period_end)
  WHERE overridden_by IS NULL;
CREATE INDEX ON biz.people_reviews (org_id, person_id, created_at DESC);
CREATE INDEX ON biz.people_reviews (org_id, created_at DESC);

-- ─── Phản biện (spec I) ─────────────────────────────────────────────────────────
ALTER TABLE biz.review_disputes
  ADD COLUMN resolved_by uuid REFERENCES core.users(id),
  ADD COLUMN resolved_at timestamptz;
CREATE INDEX ON biz.review_disputes (review_id, created_at DESC);

-- ─── Chất lượng chăm sóc: nguồn "hứa rồi quên" ──────────────────────────────────
-- `biz.promises` do cụm Hàng đợi ghi/sửa (`PATCH /tasks/promises/{id}`) nhưng chưa có chỉ mục nào — cụm này là
-- nơi đầu tiên quét bảng theo tổ chức/người/hạn với khối lượng đáng kể (`GET /care/repeated-issues`).
CREATE INDEX ON biz.promises (org_id, promiser_person_id, due_at);
CREATE INDEX ON biz.promises (org_id, to_person_id);
CREATE INDEX ON biz.promises (org_id, broken) WHERE broken = true;

-- Auditor thấy "nhật ký ai đã xem" một đánh giá cụ thể (PLAN Q4) — tra theo (org, target_type, target_id) mà
-- `ops.action_log` chưa có chỉ mục nào phủ (các chỉ mục sẵn có ở `docs/handoff/schema.sql` — đã bỏ ở v0.1.48, nay ở
-- 0001_baseline.sql — chỉ theo actor/action).
CREATE INDEX ON ops.action_log (org_id, target_type, target_id, at DESC);
