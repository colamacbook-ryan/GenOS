"""Lõi lệnh của Owner dùng chung cho mọi kênh quản trị (v0.1.71, G5): Telegram, Zalo, WhatsApp.

- `gh.owner_cmd.core`  — `OwnerCommand`, `Reply`, `Context`, `handle` (hom_nay · viec · suc_khoe · nhap · hỏi tự do chỉ
                         đọc · tro_giup), giới hạn tốc độ `rate_ok`.
- `gh.owner_cmd.parse` — `parse_text`: tin → (kind, phần sau tên lệnh).
- `gh.owner_cmd.text`  — `defang`, `line`, `briefing_items`, `public_url` (nhẹ, không import `gh.gen`).

Gói này KHÔNG import gì khi nạp (`text` được `gh.telegram.service` import sớm, trong khi `core` kéo `gh.gen.briefing` —
lại import `gh.telegram.service`; nạp sẵn `core` ở đây sẽ tạo vòng import).
Module chỉ ĐỌC hoặc ĐỀ XUẤT và chỉ TRẢ chữ cho kênh gọi — KHÔNG có đường gửi tin, ghi Kho, kiểm PIN nào ở đây.
"""
