# Bổ sung 01 — Bot quản trị 1-1, Sếp gọi bot, Chế độ hỗ trợ nhóm

> **Sếp duyệt 18/10/2026.** Bổ sung cho `docs/Gen-Harness-Product-Spec-LOCKED.md` (v2.2) — **không đổi nghĩa** điều nào đã khoá, chỉ thêm
> (E13, E11, H1, I, O). Hiện thực: v0.1.72. Thiết kế kỹ thuật: [docs/ARCHITECTURE.md](../ARCHITECTURE.md) §4.1, §5.2–5.4, §7.

## 1. Mô hình Sếp đã duyệt (7 điểm)
1. Bot dùng **tài khoản Zalo/WhatsApp riêng** (SIM thật của Sếp, không tạo tài khoản giả). Sếp nhắn riêng cho bot, ghép bằng **mã 6 số** (Console cấp sau PIN) ⇒ **kênh quản trị**. Nhận diện Sếp **theo ID tài khoản**, không theo tên.
2. Chỉ **Sếp** thêm bot vào nhóm. Người khác (hoặc không rõ ai) thêm ⇒ bot **không nghe** và **hỏi Sếp**.
3. Trong nhóm bot mặc định **Lắng nghe im lặng**.
4. Khi **Sếp tag bot** trong nhóm ⇒ bot trả lời **tại chỗ** (nhận diện theo ID).
5. Bot chủ động báo Sếp qua kênh quản trị **chỉ khi có SỰ KIỆN ĐẶC BIỆT** (Sếp quyết 18/10/2026: không báo định kỳ, không bản tóm tắt).
6. Sếp bật/tắt **Chế độ hỗ trợ** theo nhóm ⇒ bot trả lời thành viên trong giới hạn an toàn; model chạy trong **hộp riêng agy**.
7. Mọi tin khác cho người khác: **Xác nhận + mã PIN** như cũ.

## 2. Ngoại lệ của luật cứng (nguyên văn, ghi ở `CLAUDE.md` › Luật cứng, «Sếp duyệt 18/10/2026»)
> Tin gửi cho người khác luôn chờ Xác nhận + mã PIN của Sếp trên Console. Ngoại lệ duy nhất (Sếp duyệt 18/10/2026, v0.1.72), chỉ khi kênh Zalo/WhatsApp chạy bằng tài khoản riêng của bot: bot tự gửi, không qua PIN, trong đúng ba trường hợp — (1) trả lời và báo cáo cho chính Sếp qua kênh quản trị 1-1 đã ghép bằng mã (chỉ báo khi có sự kiện đặc biệt, không báo định kỳ); (2) trả lời tại chỗ trong nhóm khi chính Sếp (nhận diện theo ID tài khoản, không theo tên) tag bot; (3) trả lời thành viên trong nhóm Sếp đã bật Chế độ hỗ trợ, sau bộ lọc an toàn (không giá/tiền/hợp đồng, không thông tin người khác, không nhân sự, có giới hạn tin/giờ, câu khó chuyển Sếp), model chạy trong hộp riêng agy. Cả ba đi qua một cửa `send_auto`, ghi `ops.auto_sends` và Nhật ký; thêm đường gửi thứ tư phải được Sếp duyệt và có test chứng minh.

## 3. Bổ sung vào các mục của bản LOCKED (không đổi nghĩa cũ)
- **E13 Agent Identity**: một Agent Identity có thể chạy trên tài khoản kênh RIÊNG của bot; Owner được nhận diện theo **ID tài khoản đã ghép**, không theo tên hiển thị.
- **E11 System of Action**: thêm mức «Tự trả lời trong phạm vi Owner bật (Chế độ hỗ trợ)» — luôn kèm bộ lọc an toàn, giới hạn tốc độ, chuyển người khi khó.
- **H1 Thang tự trị**: lệnh trực tiếp của Owner (kênh quản trị, Owner tag bot) **không** phải tự trị; Chế độ hỗ trợ là danh sách trắng theo nhóm do Owner bật. Mức tự trị tổ chức ≤ 2 ⇒ Sếp tag bot được trả lời qua kênh quản trị, không đăng vào nhóm.
- **I (nguyên tắc có trách nhiệm)**: bot trả lời thành viên phải **tự giới thiệu là trợ lý tự động**; không trả lời về giá, tiền, hợp đồng, nhân sự, thông tin người khác.
- **O.5 (nhóm mới)**: mặc định Lắng nghe im lặng **khi Sếp là người thêm bot**; người khác/không rõ thêm ⇒ Không nghe (`pending`) + hỏi Sếp qua kênh quản trị (tài khoản bot, v0.1.72).
- **O (câu 6)**: mô hình bot tài khoản riêng (Sếp duyệt 18/10/2026) gồm đúng 7 điểm ở mục 1.

## 4. Quyết định mặc định (Sếp không sửa = làm theo)
Bản cài hiện có (tài khoản Zalo/WhatsApp của Sếp) coi là **tài khoản cá nhân** ⇒ mọi tính năng mới tắt, hành vi y v0.1.70 · Telegram giữ bản tin 07:30/17:30 + nhắc việc và chỉ đọc; lệnh nhóm chỉ ở kênh quản trị Zalo/WhatsApp · bật hỗ trợ qua chat cần 2 bước (`/xac_nhan <mã>` trong 5 phút), tắt thì một bước, không PIN qua chat · hỗ trợ chỉ trả lời khi thành viên tag bot hoặc trả lời tin của bot · giới hạn 20 tin/giờ/nhóm, 5/giờ/người, cách ≥ 20 giây, 150/ngày/kênh, tối đa 600 ký tự · giờ yên lặng báo cáo 21:00–07:00 giờ VN · người khác thêm bot ⇒ bot ở lại nhưng không nghe (không tự rời nhóm; lệnh rời nhóm để bản sau) · Sếp tag ở nhóm Không nghe ⇒ không lưu nội dung, nhắn riêng Sếp hỏi có bật nghe không · hỗ trợ không tự hết hạn (có tuỳ chọn «trong N giờ») · mỗi kênh một Sếp (ghép lại thay Sếp cũ) · người lạ nhắn riêng bot: lưu im lặng, không trả lời.
