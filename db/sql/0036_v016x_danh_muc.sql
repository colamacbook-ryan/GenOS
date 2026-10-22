-- Gen-Harness · v0.1.61: đồng bộ danh mục giữa web và máy chủ (kiểm kê toàn hệ thống 16/10/2026).
-- (1) F107 — tên vai trò: máy cũ lưu tên tiếng Anh ("Manager", "Operator", "Auditor") trong core.roles.name nên chuông,
--     toast đổi vai trò, chân trang, hộp mời hiện khác ô chọn vai trò. Đổi sang tên Việt thống nhất với rbac.ROLES.
--     Chỉ đổi dòng vai trò HỆ THỐNG còn đúng tên cũ do hệ thống tự đặt (tên Owner tự sửa, vai trò tuỳ biến không bị đụng).
-- (2) F070 — mẫu agent: bước 8 của bản cũ lưu template 'sales'/'ops' (không có trong danh mục mẫu của máy chủ) nên bấm Lưu
--     ở Danh tính Agent bị 422 "Mẫu không tồn tại". Đổi sang khoá thật: sales → commercial, ops → admin.
-- Chạy lại an toàn: mọi câu đều WHERE theo giá trị cũ ⇒ lần hai không còn dòng nào khớp; không đổi lược đồ, không thêm/xoá dòng.
UPDATE core.roles SET name = 'Quản lý'            WHERE is_system AND code = 'manager'     AND name = 'Manager';
UPDATE core.roles SET name = 'Vận hành'           WHERE is_system AND code = 'operator'    AND name = 'Operator';
UPDATE core.roles SET name = 'Kiểm soát'          WHERE is_system AND code = 'auditor'     AND name = 'Auditor';
UPDATE core.roles SET name = 'Nhân viên phụ trách' WHERE is_system AND code = 'agent_staff' AND name = 'Agent nhân viên';

UPDATE agent.identities SET template = 'commercial' WHERE template = 'sales';
UPDATE agent.identities SET template = 'admin'      WHERE template = 'ops';
