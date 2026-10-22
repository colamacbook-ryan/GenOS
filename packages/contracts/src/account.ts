/**
 * Hợp đồng "Tài khoản của tôi" (v0.1.19, `apps/api/gh/auth/account.py`). Mọi vai trò dùng được, chỉ cho CHÍNH
 * tài khoản đang đăng nhập. Lỗi theo ô trả 422 `errors` (sai mật khẩu hiện tại → `errors.current_password`).
 */
import type { ApiClient } from './client';
import type { Me } from './schema';

export interface AccountSession {
  id: string;
  created_at: string;
  last_seen_at: string | null;
  ip: string | null;
  user_agent: string | null;
  expires_at: string;
  /** Phiên của chính trình duyệt đang xem. */
  current: boolean;
}

export interface Account {
  display_name: string;
  email: string;
  role: Me['role'];
  created_at: string;
  must_change_password: boolean;
  /** Tài khoản có mã PIN (Owner) — mới hiện mục "Đổi mã PIN". */
  has_pin: boolean;
  sessions: AccountSession[];
}

export interface AccountProfileBody {
  display_name?: string;
  email?: string;
  /** Bắt buộc khi đổi email. */
  current_password?: string;
}

export interface AccountPasswordBody {
  current_password: string;
  /** Tối thiểu 12 ký tự, khác mật khẩu hiện tại. */
  new_password: string;
}

export interface AccountPinBody {
  current_password: string;
  new_pin: string;
  new_pin_confirm: string;
}

export interface SessionsRevoked {
  sessions_revoked: number;
}

export function accountEndpoints(r: ApiClient['request']) {
  return {
    account: {
      get: (signal?: AbortSignal) => r<Account>('/account', { signal }),
      update: (body: AccountProfileBody) => r<Account>('/account', { method: 'PATCH', body }),
      /** Đổi mật khẩu — thu hồi mọi phiên KHÁC, tắt cờ buộc đổi mật khẩu. */
      changePassword: (body: AccountPasswordBody) =>
        r<SessionsRevoked>('/account/password', { method: 'POST', body }),
      /** 409 NO_PIN khi tài khoản không dùng PIN. */
      changePin: (body: AccountPinBody) => r<void>('/account/pin', { method: 'POST', body }),
      revokeOtherSessions: () => r<SessionsRevoked>('/account/sessions/revoke-others', { method: 'POST' }),
      /** 409 CURRENT_SESSION với phiên đang dùng (hãy Đăng xuất). */
      revokeSession: (id: string) =>
        r<void>(`/account/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }),
    },
  };
}
