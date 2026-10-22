-- Gen-Harness · giai đoạn 5.5 — Row-Level Security theo org_id
-- ARCHITECTURE §8.3: "Row-Level Security Postgres bật ở giai đoạn hoàn thiện như lớp phòng thủ thứ hai."
--
-- PHẠM VI VÀ QUYẾT ĐỊNH THIẾT KẾ (xem docs/reports/phase-5-performance.md để có số đo, và báo cáo bàn giao
-- phiên này để có toàn bộ lý giải):
--
-- 1. `gh/bootstrap.py` xác nhận "Một bản cài = một tổ chức": mỗi triển khai Gen-Harness (docker compose) chỉ có
--    ĐÚNG MỘT dòng trong core.organizations, không phải mô hình nhiều khách thuê dùng chung một CSDL. Vì vậy
--    RLS ở đây KHÔNG phải cơ chế cách ly nhiều tổ chức chính (không cần vì thực tế chỉ có 1 org_id/CSDL), mà là
--    LỚP PHÒNG THỦ THỨ HAI đúng như ARCHITECTURE §8.3 chủ định: chặn lỗi lập trình quên lọc org_id ở tầng
--    service/ScopeFilter, và chuẩn bị sẵn cho khả năng SaaS nhiều tổ chức trong tương lai.
-- 2. Biến phiên `app.org_id` được đặt bằng `SELECT set_config('app.org_id', :org_id, true)` (tương đương
--    SET LOCAL, chỉ có hiệu lực trong transaction hiện tại) ngay sau khi phiên đăng nhập được xác thực —
--    xem `gh/auth/deps.py::optional_user`. Việc này chạy MỘT LẦN mỗi request đã đăng nhập (không mỗi câu lệnh).
-- 3. Chính sách cho phép truy cập khi `org_id` CỦA DÒNG khớp biến phiên, HOẶC khi biến phiên CHƯA được đặt
--    (NULL) — để không phá worker nền / job lịch / script quản trị vốn không đi qua request HTTP (refinery,
--    partman, action-log verifier, ...) và vốn đã tự lọc org_id đúng ở tầng service. Đây là lựa chọn thực dụng:
--    RLS bổ sung một lớp kiểm tra cho đường request web (nơi rủi ro quên lọc cao nhất do nhiều route), không
--    bắt buộc phải sửa toàn bộ tầng worker để "biết" org_id trước khi mở transaction.
-- 4. QUAN TRỌNG — giới hạn thật của lớp phòng thủ này: vai trò Postgres ứng dụng hiện dùng (`postgres`, xem
--    gh/config.py DATABASE_URL mặc định) là SUPERUSER, và RLS THEO ĐỊNH NGHĨA CỦA POSTGRES KHÔNG áp dụng cho
--    superuser dù bật ENABLE/FORCE ROW LEVEL SECURITY. Nghĩa là ở trạng thái hạ tầng hiện tại (chưa có vai trò
--    ứng dụng riêng, phi-superuser — đó là việc của trình cài/giai đoạn 6), các policy dưới đây được TẠO SẴN
--    và ĐÚNG (đã kiểm bằng SET ROLE sang vai trò không phải chủ bảng trong test), nhưng CHƯA được vai trò kết
--    nối thật của ứng dụng tôn trọng cho tới khi đổi sang một role không BYPASSRLS. Khuyến nghị: trình cài
--    (giai đoạn 6) tạo role `gh_app` (LOGIN, NOSUPERUSER, NOBYPASSRLS) cho container `api`/`worker` dùng thay
--    `postgres`. Ghi rõ điều này để không hiểu lầm là RLS đã "bật và có hiệu lực" trên vai trò hiện dùng.
-- 5. Phạm vi bảng: các bảng nghiệp vụ đọc/ghi nhiều nhất và nhạy cảm nhất theo yêu cầu (core.persons,
--    core.groups, raw.events, clean.meaning_units, biz.*) — KHÔNG áp cho core.users/core.roles/core.sessions
--    vì luồng đăng nhập cần tra theo email/token TRƯỚC KHI biết org_id (áp RLS ở đó sẽ khoá luôn đăng nhập).

DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'core.persons', 'core.groups',
    'raw.events',
    'clean.meaning_units', 'clean.score_snapshots', 'clean.relationships',
    'biz.opportunities', 'biz.deals', 'biz.cases', 'biz.tasks', 'biz.documents',
    'biz.action_drafts', 'biz.people_reviews', 'biz.promises', 'biz.market_signals',
    'biz.matches', 'biz.alerts'
  ]
  LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format($p$
      CREATE POLICY org_isolation ON %s
        USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
               OR current_setting('app.org_id', true) IS NULL
               OR current_setting('app.org_id', true) = '')
        WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
               OR current_setting('app.org_id', true) IS NULL
               OR current_setting('app.org_id', true) = '')
    $p$, t);
  END LOOP;
END $$;
