/**
 * Hợp đồng Đợt B6 (v0.1.23) — chuông thông báo ở header (`apps/api/gh/notifications.py`, bảng core.notifications).
 * Mỗi thông báo thuộc đúng một người nhận; API chỉ trả của người đang đăng nhập. Sự kiện WS `notification.new`
 * (chỉ gửi tới người nhận) mang đúng một `NotificationItem`; v0.1.69: `notification.resolved` cùng dạng, báo chuông vừa đóng.
 */
import type { ApiClient } from './client';

export interface NotificationItem {
  id: string;
  /**
   * Ví dụ `user.role_changed`, `user.password_reset`, `user.reactivated`, `backup.done`, `backup.failed`; v0.1.36 (F-6)
   * thêm sự cố sức khoẻ (khử trùng lặp theo `ops.health_alerts`): `channel.down`, `model.auth_expired`, `update.failed`,
   * `backup.stale`, `worker.silent`, `disk.low`.
   */
  kind: string;
  title: string;
  body: string;
  /** Đường dẫn trong Console để mở khi bấm (vd `/system?tab=storage`); null = không có. */
  link: string | null;
  created_at: string;
  read: boolean;
  /** v0.1.69 (F098): sự cố sinh ra chuông này đã đóng ⇒ chuông hiện "Đã xử lý" (đã đọc, hết đỏ). */
  resolved?: boolean;
  resolved_at?: string | null;
}

export interface NotificationsPage {
  items: NotificationItem[];
  /** Số chưa đọc của TOÀN BỘ thông báo (không chỉ phần `items` đang liệt kê). */
  unread: number;
  /** v0.1.69 (F098): tổng số thông báo — lớn hơn `items.length` thì chuông hiện "Xem thêm". */
  total?: number;
}

export function notificationsEndpoints(r: ApiClient['request']) {
  return {
    notifications: {
      /** `offset` (v0.1.69): lật trang khi có quá 100 thông báo (`limit` tối đa 100). */
      list: (limit = 20, signal?: AbortSignal, offset = 0) =>
        r<NotificationsPage>('/notifications', { query: offset > 0 ? { limit, offset } : { limit }, signal }),
      /** `ids` bỏ trống = đánh dấu TẤT CẢ đã đọc. */
      markRead: (ids?: string[]) =>
        r<{ unread: number }>('/notifications/read', { method: 'POST', body: ids?.length ? { ids } : {} }),
    },
  };
}
