-- Gen-Harness · v0.1.25 — Đợt C1: lọc đầu (triage) cho mục Hộp thư — trùng, rác, điểm chất lượng 0–100.
-- Một dòng / mục hàng đợi (hiện chỉ item_type = 'unit' — đơn vị ý nghĩa mới vào). Ghi bởi job nền
-- gh.refinery.triage (hook sau sàng lọc + quét định kỳ), KHÔNG chặn đường nhập. Cấu hình ở
-- core.organizations.settings->'triage' ({"enabled", "min_score", "use_jev"}).
CREATE TABLE IF NOT EXISTS refinery.item_marks (
  org_id            uuid NOT NULL,
  item_type         text NOT NULL,                       -- unit
  item_id           uuid NOT NULL,
  observed_at       timestamptz NOT NULL,
  subject_id        uuid,                                -- người/nhóm của mục (dò trùng tin ngắn cùng người)
  text_hash         bytea NOT NULL,                      -- sha256 văn bản đã chuẩn hoá → trùng y hệt
  simhash           bigint NOT NULL,                     -- simhash 64 bit (3-gram ký tự) — lọc thô trước khi so
  text_len          int NOT NULL,
  norm_text         text NOT NULL DEFAULT '',            -- văn bản chuẩn hoá (≤ 600 ký tự) → Jaccard 3-gram = gần trùng
  duplicate_of      uuid,                                -- mục gốc (xuất hiện trước) khi là bản trùng
  duplicate_kind    text CHECK (duplicate_kind IN ('exact', 'near')),
  is_spam           boolean NOT NULL DEFAULT false,
  spam_reason       text,
  quality           smallint NOT NULL CHECK (quality BETWEEN 0 AND 100),
  reason            text NOT NULL DEFAULT '',
  source            text NOT NULL CHECK (source IN ('heuristic', 'jev')),
  heuristic_quality smallint NOT NULL,                   -- luôn tính, để đo độ khớp Jev ↔ quy tắc
  heuristic_spam    boolean NOT NULL,
  latency_ms        int,                                 -- thời gian gọi Jev (NULL = không gọi)
  version           smallint NOT NULL DEFAULT 1,
  marked_at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (item_type, item_id)
);
CREATE INDEX IF NOT EXISTS item_marks_hash_idx ON refinery.item_marks (org_id, text_hash);
CREATE INDEX IF NOT EXISTS item_marks_observed_idx ON refinery.item_marks (org_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS item_marks_marked_idx ON refinery.item_marks (org_id, marked_at DESC);

-- RLS theo org_id như 0012/0016/0017 (lớp phòng thủ thứ hai cho đường request web).
ALTER TABLE refinery.item_marks ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON refinery.item_marks;
CREATE POLICY org_isolation ON refinery.item_marks
  USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '')
  WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
         OR current_setting('app.org_id', true) IS NULL
         OR current_setting('app.org_id', true) = '');

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gh_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON refinery.item_marks TO gh_app;
  END IF;
END $$;
