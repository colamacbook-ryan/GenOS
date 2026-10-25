/**
 * Cờ tính năng theo trạng thái THẬT của máy chủ (v0.1.71, giả lập toàn luồng P3-4).
 *
 * `PROMISES_TRACKED`: bật từ v0.1.76 — gói T4 đã có đúng một bộ ghi `biz.promises` (detector W1 trích lời hứa ra khỏi tin
 * nhắn; máy chủ `gh/biz/queue/service.py::PROMISES_TRACKED = True`). Cờ web phải khớp cờ máy chủ: tab Lời hứa ở Việc & Nhắc
 * hẹn, ô «Lời hứa quá hạn» ở Hôm nay / Tình báo, «Hứa rồi quên» ở Chăm sóc khách. Đặt lại false nếu bộ trích bị gỡ.
 */
export const PROMISES_TRACKED = true;
