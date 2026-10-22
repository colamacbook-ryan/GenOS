-- Gen-Harness · v0.1.65 (F1): chế độ nghe & danh bạ (kiểm kê toàn hệ thống 16/10/2026: F056 F057 F058 F065 F066).
-- (1) core.groups.left_at — nhóm vắng mặt trong danh bạ đầy đủ gần nhất = Sếp đã rời nhóm; không còn đếm "đang lắng nghe".
-- (2) Tin riêng 1-1 (quyết định Sếp): mặc định LƯU IM LẶNG. `settings.direct_mode.<kênh>` = off | silent | reply.
--     Bản cài cũ: `listen_direct` bật ⇒ reply (giữ nguyên hành vi: agent trực vẫn được trả lời); tắt/chưa đặt ⇒ silent
--     (từ nay LƯU tin 1-1 nhưng không tự trả lời, không đưa cho agent trực). Khoá cũ `listen_direct` giữ nguyên, chỉ còn dùng làm nguồn đọc.
-- (3) KHÔNG đổi chế độ nghe của nhóm hiện có: nhóm đang 'off' giữ 'off' (không tự bật nghe nhóm cũ); chỉ nhóm phát hiện từ
--     bây giờ mới theo thiết lập «Nhóm mới» của kênh — mặc định vào 'silent' (Lắng nghe im lặng, QĐ 17/10/2026); 'pending'
--     (chờ Sếp chọn) chỉ khi Sếp đặt «Hỏi Sếp». `listen_mode` là text tự do, không có ràng buộc nên không đổi lược đồ.
-- Chạy lại an toàn: ADD COLUMN IF NOT EXISTS; mỗi kênh chỉ ghi `direct_mode` khi CHƯA có (lần hai không còn dòng nào khớp).
ALTER TABLE core.groups ADD COLUMN IF NOT EXISTS left_at timestamptz;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['zalo', 'whatsapp'] LOOP
    UPDATE core.organizations SET settings = jsonb_set(
        CASE WHEN jsonb_typeof(settings -> 'direct_mode') = 'object' THEN settings
             ELSE settings || '{"direct_mode": {}}'::jsonb END,
        ARRAY['direct_mode', t],
        to_jsonb(CASE WHEN settings #>> ARRAY['listen_direct', t] = 'true' THEN 'reply' ELSE 'silent' END::text))
    WHERE NOT (COALESCE(settings -> 'direct_mode', '{}'::jsonb) ? t);
  END LOOP;
END $$;
