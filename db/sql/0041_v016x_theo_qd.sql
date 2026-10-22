-- Gen-Harness · v0.1.69: (C4) chuông có vòng đời + (D3) dữ liệu/mặc định đúng quyết định của Sếp (QD-20, QD-22, QD-18).
-- Mọi câu lệnh chạy lại an toàn (IF NOT EXISTS / UPDATE có điều kiện khớp giá trị cũ); không xoá dòng, không đổi quyền.

-- a) (C4, F098) core.notifications.resolved_at — chuông của sự cố đã đóng được gắn "Đã xử lý" (cùng read_at).
--    Cột NULL, không cần backfill; gh_app đã có quyền cả bảng.
ALTER TABLE core.notifications ADD COLUMN IF NOT EXISTS resolved_at timestamptz;
--    `incident_key` = khoá sự cố (ops.health_alerts.key) sinh ra chuông — đóng sự cố nào tắt đúng chuông của sự cố đó (trước
--    đây chọn theo tiêu đề nên hai nguồn trùng tên tắt nhầm nhau). Chuông cũ để NULL ⇒ vẫn tắt theo kind/tiêu đề như trước.
ALTER TABLE core.notifications ADD COLUMN IF NOT EXISTS incident_key text;

-- b) (D3, F121 / QD-20) giờ yên lặng của Gen hướng dẫn: 21:00–07:00 (bản v0.1.54 đặt 22 do không đối chiếu QD-20).
--    DEFAULT của cột đổi sang 21. Hàng ĐÃ có chỉ đổi khi còn đúng cặp mặc định cũ (22, 7) VÀ Sếp chưa từng chỉnh giờ yên
--    lặng (Nhật ký hành động không có gen.coach_prefs_changed nào ghi quiet_start của đúng người đó) — giá trị Sếp đã đổi
--    không bị đè; `updated_at` không đổi.
ALTER TABLE agent.gen_coach_prefs ALTER COLUMN quiet_start SET DEFAULT 21;
UPDATE agent.gen_coach_prefs p
   SET quiet_start = 21
 WHERE p.quiet_start = 22 AND p.quiet_end = 7
   AND NOT EXISTS (SELECT 1 FROM ops.action_log a
                    WHERE a.org_id = p.org_id AND a.action = 'gen.coach_prefs_changed'
                      AND a.actor_id = 'user:' || p.user_id::text
                      AND a.detail -> 'fields' ? 'quiet_start');

-- c) (D3, F122 / QD-22) bản v0.1.50–v0.1.55 đã lưu chữ "Kho <tên riêng cũ>" vào hội thoại Gen và chuông của Owner khác;
--    migration 0035 chỉ sửa ghi chú máy chủ. Thay bằng tên Kho chung "Kho dữ liệu" (không phân biệt hoa thường), chỉ ở dòng
--    có chuỗi cũ. CHỈ sửa lời Gen (role = 'assistant'): lời Sếp gõ (role = 'user') là nguyên văn, không viết lại. Chỉ khớp
--    NGUYÊN TỪ ("Kho <tên>x", "xKho <tên>" giữ nguyên); content là jsonb nên chữ xuống dòng nằm dưới dạng \n ngay trước
--    chữ Kho — mẫu coi \n \r \t là ranh giới trái và giữ lại nó (\1). Các dòng chuỗi cũ bên dưới là NGUỒN KHỚP nên mang
--    chú thích cho phép của kiểm CI chống lộ thông tin riêng.
UPDATE agent.gen_messages
   SET content = regexp_replace(content::text, '(^|[^[:alnum:]_]|\\[nrt])Kho Ryan(?![[:alnum:]_])', '\1Kho dữ liệu', 'gi')::jsonb -- allow-personal-info
 WHERE role = 'assistant' AND content::text ~* '(^|[^[:alnum:]_]|\\[nrt])Kho Ryan(?![[:alnum:]_])'; -- allow-personal-info
UPDATE core.notifications
   SET title = regexp_replace(title, '\yKho Ryan\y', 'Kho dữ liệu', 'gi'), -- allow-personal-info
       body = regexp_replace(body, '\yKho Ryan\y', 'Kho dữ liệu', 'gi') -- allow-personal-info
 WHERE title ~* '\yKho Ryan\y' OR body ~* '\yKho Ryan\y'; -- allow-personal-info

-- d) (D3, F124 / QD-18) ghi chú máy chủ Gen-hub đã lưu nói "(chỉ đọc)" dù Gen ghi được Kho khi Sếp Xác nhận + mã PIN.
--    Khớp mọi tên Kho (mặc định hoặc Owner tự đặt) và chỉ dòng còn đúng ghi chú hệ thống cũ — ghi chú Owner tự sửa không đụng.
UPDATE agent.mcp_servers
   SET note = replace(note, '(chỉ đọc). Quản lý ở thẻ Gen-hub.',
                      '(đọc; ghi Kho chỉ khi Sếp Xác nhận + mã PIN). Quản lý ở thẻ Gen-hub.')
 WHERE name = 'Gen-hub'
   AND note LIKE 'Liên kết Gen-hub — Gen đọc %, lịch, mail, việc, Drive (chỉ đọc). Quản lý ở thẻ Gen-hub.';
