/**
 * v0.1.53 (F-97): mã genh khi KHÔNG xoá được tệp yêu cầu trong run/request (bản sao ngoài máy, gói chẩn đoán, Gửi thử
 * trực canh, khôi phục, cập nhật) — genh không làm yêu cầu đó để trình nhận yêu cầu không kích lặp. Bấm lại/cắm ổ vẫn
 * lỗi y như cũ cho tới khi sửa quyền thư mục ⇒ mọi bảng mã lỗi của Console dùng chung một câu (không chỉ bảo "thử lại").
 */
export const REQUEST_UNDELETABLE_CODE = 'GH-E94C';
export const REQUEST_UNDELETABLE_TEXT =
  'Máy chủ không xoá được tệp yêu cầu — chưa làm gì. Nhờ người quản trị kiểm quyền thư mục run/request trong thư mục cài đặt rồi thử lại.';
