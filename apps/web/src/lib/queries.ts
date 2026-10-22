import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { api } from './api';

export const qk = {
  me: ['auth', 'me'] as const,
  navigation: ['shell', 'navigation'] as const,
  header: ['shell', 'header'] as const,
  setupState: ['setup', 'state'] as const,
  /** Agent + mức tự trị hiện tại của bước 9 — lưu bước 9 / sửa agent phải cập nhật (form điền sẵn không được cũ). */
  setupStep9: ['setup', 'step9'] as const,
  /** v0.1.23 (B6): chuông thông báo — `notification.new` qua WS chèn thẳng vào đây (lib/realtime.ts). */
  notifications: ['notifications'] as const,
};

export const useMe = () => useQuery({ queryKey: qk.me, queryFn: ({ signal }) => api.auth.me(signal) });
export const useNavigation = () =>
  useQuery({ queryKey: qk.navigation, queryFn: ({ signal }) => api.shell.navigation(signal) });
export const useHeaderStatus = () =>
  useQuery({
    queryKey: qk.header,
    queryFn: ({ signal }) => api.shell.header(signal),
    refetchInterval: 60_000,
  });

/**
 * v0.1.69 (F098): chuông "Xem thêm" — tải từng trang 100 thông báo (giới hạn của API) bằng `offset`, bấm "Xem thêm" tiếp thì
 * lấy trang kế cho tới hết `total`; chỉ chạy khi `enabled`.
 */
export const NOTIFICATIONS_PAGE_SIZE = 100;
export const useAllNotifications = (enabled: boolean) =>
  useInfiniteQuery({
    queryKey: [...qk.notifications, 'all'] as const,
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => api.notifications.list(NOTIFICATIONS_PAGE_SIZE, signal, pageParam),
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, p) => n + p.items.length, 0);
      return last.items.length > 0 && loaded < (last.total ?? loaded) ? loaded : undefined;
    },
    enabled,
  });

/** B6: 20 thông báo gần nhất + số chưa đọc. WS là đường chính; hỏi lại mỗi 2 phút phòng khi socket rớt. */
export const useNotifications = (enabled = true) =>
  useQuery({
    queryKey: qk.notifications,
    queryFn: ({ signal }) => api.notifications.list(20, signal),
    refetchInterval: 120_000,
    enabled,
  });
