/**
 * Mock v0.1.57 (Nợ #30) — trạng thái "Tên Kho" dùng chung giữa mock liên kết Gen-hub (`mock-p4-mcp`: `GET/PATCH /hub/link`) và mock sổ mặc
 * định (`mock-defaults`: mục `kho_label`), như máy chủ lưu MỘT khoá `kho_label` trong cài đặt tổ chức. Chuỗi rỗng = chưa đổi (mặc định).
 * Mỗi lần dựng mock mới thì đặt lại (`resetKhoLabel`).
 */
export const KHO_DEFAULT = 'Kho dữ liệu';
export const KHO_MAX = 40;
export const khoLabelState = { label: '' };
export const resetKhoLabel = () => {
  khoLabelState.label = '';
};
