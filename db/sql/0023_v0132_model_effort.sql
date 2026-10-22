-- Gen-Harness · v0.1.32 — tách "mức suy nghĩ" (effort) khỏi tên model của nguồn CLI.
-- Boss 01/10: "high" KHÔNG phải một phần tên model mà là mức suy nghĩ. agy 1.2.9: `--model <model gốc>` + `--effort
-- low|medium|high` (`agy --help`; changelog: "Added an `--effort` flag to select a model's reasoning-effort variant").
-- Claude Code 2.1.285: `--effort <low|medium|high|xhigh|max>` (`claude --help`).
-- Chỉ thêm cột + chuyển dữ liệu cũ; chạy lại nhiều lần vẫn an toàn.
ALTER TABLE agent.models ADD COLUMN IF NOT EXISTS effort text;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'models_effort_check') THEN
    ALTER TABLE agent.models ADD CONSTRAINT models_effort_check
      CHECK (effort IS NULL OR effort IN ('low', 'medium', 'high', 'xhigh', 'max'));
  END IF;
END $$;

-- Bản ≤ v0.1.31 lưu biến thể của Antigravity làm tên model ("gemini-3.8-flash-high"). Đổi thành model gốc + effort.
-- Chỉ đổi tên khi nguồn chưa có dòng trùng tên gốc (UNIQUE provider_id, model_name); dòng còn lại chỉ ghi effort —
-- lúc gọi, AgyClient vẫn tách hậu tố (gh.providers.catalog.split_variant) nên không bao giờ gửi tên biến thể cho CLI.
-- "…-extra-low" (vd gemini-3.5-flash-extra-low trong tệp chạy agy 1.2.9) là tên riêng, không tách.
UPDATE agent.models m
   SET effort = COALESCE(m.effort, substring(m.model_name FROM '-(low|medium|high)$'))
  FROM agent.providers p
 WHERE p.id = m.provider_id AND p.kind = 'antigravity_cli' AND m.model_name ~ '-(low|medium|high)$'
   AND m.model_name !~ '-extra-(low|medium|high)$';

WITH ranked AS (
  SELECT m.id, regexp_replace(m.model_name, '-(low|medium|high)$', '') AS base,
         row_number() OVER (PARTITION BY m.provider_id, regexp_replace(m.model_name, '-(low|medium|high)$', '')
                            ORDER BY m.is_default DESC, m.id) AS rn
    FROM agent.models m JOIN agent.providers p ON p.id = m.provider_id
   WHERE p.kind = 'antigravity_cli' AND m.model_name ~ '-(low|medium|high)$'
     AND m.model_name !~ '-extra-(low|medium|high)$'
)
UPDATE agent.models m SET model_name = r.base
  FROM ranked r
 WHERE m.id = r.id AND r.rn = 1
   AND NOT EXISTS (SELECT 1 FROM agent.models x WHERE x.provider_id = m.provider_id AND x.model_name = r.base);
