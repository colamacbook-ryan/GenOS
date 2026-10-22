-- Gen-Harness · v0.1.56: ghi chú máy chủ MCP của liên kết Gen-hub dùng tên Kho chung ("Kho dữ liệu").
-- Bản cũ lưu SERVER_NOTE mang tên riêng của chủ Gen-hub vào agent.mcp_servers.note của MỌI Owner (hiện ở thẻ Gen-hub / MCP).
-- Chạy lại an toàn: chỉ UPDATE dòng có đúng ghi chú cũ do hệ thống tự đặt (WHERE note = chuỗi cũ) — ghi chú Owner tự
-- sửa, hay dòng đã cập nhật, không bị đụng; không đổi cột nào khác, không thêm/xoá dòng, không đổi lược đồ.
-- Dòng chuỗi cũ bên dưới là NGUỒN KHỚP của câu UPDATE nên mang chú thích cho phép của kiểm CI chống lộ thông tin riêng.
UPDATE agent.mcp_servers
   SET note = 'Liên kết Gen-hub — Gen đọc Kho dữ liệu, lịch, mail, việc, Drive (chỉ đọc). Quản lý ở thẻ Gen-hub.'
 WHERE name = 'Gen-hub'
   AND note = 'Liên kết Gen-hub — Gen đọc Kho Ryan, lịch, mail, việc, Drive (chỉ đọc). Quản lý ở thẻ Gen-hub.'; -- allow-personal-info
