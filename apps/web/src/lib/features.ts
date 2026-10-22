/**
 * Cờ tính năng theo trạng thái THẬT của máy chủ (v0.1.71, giả lập toàn luồng P3-4).
 *
 * `PROMISES_TRACKED`: `biz.promises` (lời hứa trong hội thoại) CHƯA có bộ nào ghi vào — không việc nền nào trích lời
 * hứa ra khỏi tin nhắn. Để các chỗ «Lời hứa» trống mãi (và tệ hơn, báo «không có lời hứa nào quá hạn» như thể đã theo
 * dõi) thì ẩn chúng đi: tab Lời hứa ở Việc & Nhắc hẹn, ô «Lời hứa quá hạn» ở Hôm nay / Tình báo, «Hứa rồi quên» ở
 * Chăm sóc khách. Khi có bộ trích lời hứa (Nợ #62 ở ROADMAP) thì bật lại — mã và API giữ nguyên.
 */
export const PROMISES_TRACKED = false;
