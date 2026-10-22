-- Gen-Harness · v0.1.31 — nguồn CLI (Antigravity, Claude Code) có nhiều model: "Dùng model này" đặt model mặc định
-- của nguồn (chuỗi chuyển hướng dùng model này khi agent không gán riêng). Trước đây: model thêm ĐẦU TIÊN luôn thắng.
-- Chỉ thêm cột (bảng đã có quyền gh_app từ migration 0014).
ALTER TABLE agent.models ADD COLUMN IF NOT EXISTS is_default boolean NOT NULL DEFAULT false;
CREATE UNIQUE INDEX IF NOT EXISTS models_one_default_per_provider ON agent.models (provider_id) WHERE is_default;
