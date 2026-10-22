-- Gen-Harness · v0.1.64 (T1/T3): Telegram 2 chiều — Sếp nhắn Gen qua bot (Nợ #13).
-- Thêm vào `ops.notify_channels` (một dòng mỗi tổ chức): trạng thái GHÉP tài khoản Sếp + công tắc "Nhận tin từ Sếp"
-- + `inbound_offset` (update_id cuối đã xử lý của getUpdates long-poll) + mã ghép 6 số (chỉ lưu BĂM, sống 10 phút).
-- Bản cài cũ: `inbound_enabled` mặc định FALSE ⇒ không nhận tin từ ai cho tới khi Sếp ghép và bật.
-- Chạy lại an toàn (ADD COLUMN IF NOT EXISTS; ràng buộc thêm trong khối DO kiểm tên trước).
ALTER TABLE ops.notify_channels
  ADD COLUMN IF NOT EXISTS inbound_enabled boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS owner_chat_id   text,
  ADD COLUMN IF NOT EXISTS owner_tg_user_id text,
  ADD COLUMN IF NOT EXISTS owner_user_id   uuid,
  ADD COLUMN IF NOT EXISTS paired_at       timestamptz,
  ADD COLUMN IF NOT EXISTS inbound_offset  bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS pair_code_hash  bytea,
  ADD COLUMN IF NOT EXISTS pair_expires_at timestamptz,
  ADD COLUMN IF NOT EXISTS pair_attempts   integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS pair_user_id    uuid;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'notify_channels_owner_ids_chk') THEN
    ALTER TABLE ops.notify_channels ADD CONSTRAINT notify_channels_owner_ids_chk
      CHECK ((owner_chat_id IS NULL OR owner_chat_id ~ '^[0-9]{1,20}$')
         AND (owner_tg_user_id IS NULL OR owner_tg_user_id ~ '^[0-9]{1,20}$'));
  END IF;
END $$;
