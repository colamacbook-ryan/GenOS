-- Gen-Harness · v0.1.1 mục 1a — CSDL
-- 2 chỉ mục thiếu (PLAN §5.6 lỗi 🟡) + quyền cho vai trò ứng dụng phi-superuser `gh_app` (khuyến nghị ở
-- docstring 0012_p5_rls.sql — trình cài giai đoạn 6 sẽ cho container api/worker dùng role này thay `postgres`).
--
-- Về FORCE ROW LEVEL SECURITY (kiểm lại chính sách 0012 cho mục này): Postgres CHỈ áp policy cho CHỦ BẢNG khi
-- bảng có FORCE ROW LEVEL SECURITY; superuser luôn bỏ qua RLS bất kể ENABLE/FORCE. Các bảng ở 0012 do vai trò
-- chạy migration (superuser) làm chủ, còn `gh_app` là vai trò MỚI, KHÔNG phải chủ bảng và KHÔNG superuser —
-- với vai trò như vậy, `ENABLE ROW LEVEL SECURITY` (đã bật ở 0012) là ĐỦ để policy có hiệu lực, không cần
-- FORCE (kiểm bằng SET ROLE trong test 0012 — tests/test_rls.py — đã xác nhận đúng behaviour này với vai trò
-- không phải chủ bảng, không phải superuser). FORCE chỉ cần nếu sau này chính vai trò CHỦ BẢNG (vd. đổi
-- owner các bảng sang `gh_app`) cũng phải bị RLS ràng buộc — không phải trường hợp ở đây.

-- ═══ 1. Chỉ mục còn thiếu ════════════════════════════════════════════════════

-- refinery.event_state không phân vùng (PK (event_id, event_received_at) thường) → tạo chỉ mục thẳng, không
-- cần lo bảng cha/con như raw.events/clean.meaning_units (partman).
CREATE INDEX IF NOT EXISTS event_state_run_id_idx ON refinery.event_state (run_id);

-- core.sessions cũng không phân vùng — dọn phiên hết hạn theo user_id (gh/worker.py::expire_sessions) và tra
-- cứu phiên hiện có của một người dùng đều lọc theo cột này.
CREATE INDEX IF NOT EXISTS sessions_user_id_idx ON core.sessions (user_id);

-- ═══ 2. Vai trò ứng dụng gh_app (LOGIN, KHÔNG superuser, KHÔNG BYPASSRLS) ════
-- Idempotent: tạo NOLOGIN trước (an toàn nếu chạy migration mà chưa có GH_APP_DB_PASSWORD), mật khẩu/LOGIN
-- được ALTER riêng bên Python (migrations/versions/0014_v011_db.py::upgrade) — nơi có os.environ và tự quote
-- an toàn (không nối chuỗi SQL thô ở đây).
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gh_app') THEN
    CREATE ROLE gh_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;

-- ═══ 3. Quyền cho gh_app trên mọi schema nghiệp vụ (không gồm `partman`: bảo trì phân vùng chạy bằng
--    GH_ADMIN_DATABASE_URL — xem gh/config.py + apps/api/gh/worker.py::partition_maintenance) ═══
DO $$
DECLARE
  s text;
BEGIN
  FOREACH s IN ARRAY ARRAY[
    'core', 'raw', 'refinery', 'clean', 'memory', 'biz', 'agent', 'ops', 'analytics'
  ]
  LOOP
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO gh_app', s);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO gh_app', s);
    EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %I TO gh_app', s);
    EXECUTE format('GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA %I TO gh_app', s);

    -- Bảng/sequence/hàm tạo SAU migration này (kể cả phân vùng partman tạo hằng tháng cho raw.events,
    -- clean.meaning_units, clean.score_snapshots, agent.model_calls, agent.mcp_calls, ops.breaker_events,
    -- ops.plugin_logs, ops.action_log) cũng phải được cấp — đặt theo vai trò ĐANG chạy migration này
    -- (current_user: superuser/GH_ADMIN_DATABASE_URL, cũng là vai trò tạo các phân vùng mới qua
    -- partman.run_maintenance), không hard-code tên vai trò cụ thể của môi trường.
    EXECUTE format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gh_app',
      current_user, s);
    EXECUTE format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT USAGE, SELECT ON SEQUENCES TO gh_app',
      current_user, s);
    EXECUTE format(
      'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT EXECUTE ON FUNCTIONS TO gh_app',
      current_user, s);
  END LOOP;
END $$;
