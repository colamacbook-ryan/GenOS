/**
 * Hợp đồng Đợt B1–B3 (v0.1.22):
 * - Quản lý người dùng `/users` (`apps/api/gh/auth/users.py`) — chỉ `roles.manage` (Owner); mọi thao tác ghi cần PIN
 *   (client tự hỏi PIN qua 423). 409 `SELF_CHANGE` (tài khoản của chính mình), `LAST_OWNER` (Owner cuối cùng).
 * - Tổ chức `/system/org` (`apps/api/gh/system_api/org.py`) — kiểm như bước 3 của trình thiết lập; PATCH chỉ Owner.
 * - Giới thiệu `/system/about` — mọi người đã đăng nhập (trang Trợ giúp).
 */
import type { ApiClient } from './client';
import type { Me } from './schema';

export type InviteRole = 'manager' | 'operator' | 'agent_staff' | 'auditor';

export interface ManagedUser {
  id: string;
  display_name: string;
  email: string;
  role: Me['role'];
  status: 'active' | 'inactive';
  /** Đang dùng mật khẩu tạm (mới mời / vừa đặt lại) — lần đăng nhập tới phải đổi. */
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string;
  is_self: boolean;
}

export interface UserRoleOption {
  code: string;
  name: string;
  meta: string;
  /** Mời / gán được ở đây (mọi vai trò trừ Owner). */
  assignable: boolean;
}

export interface UsersPage {
  items: ManagedUser[];
  roles: UserRoleOption[];
}

export interface InviteUserBody {
  display_name: string;
  email: string;
  role: InviteRole;
}

/** Mật khẩu tạm chỉ trả MỘT lần (chưa có SMTP) — Sếp tự gửi qua kênh riêng. */
export interface TempPasswordResult {
  user: ManagedUser;
  temp_password: string;
}

export interface OrgSettings {
  org_name: string;
  timezone: string;
  currency: string;
  self_name: string;
  bot_calls_me: string;
  currencies: string[];
  /** Chỉ Owner sửa được. */
  can_edit: boolean;
}

export type OrgSettingsBody = Omit<OrgSettings, 'currencies' | 'can_edit'>;

export interface AboutInfo {
  /** Phiên bản genh đã cài (vd `v0.1.22`); null = bản phát triển. Giữ tương thích: = genh_version ?? image_version. */
  version: string | null;
  /** v0.1.36 (F-46): phiên bản ảnh đang chạy (GH_VERSION / tệp VERSION); máy chủ ≤ v0.1.35 không trả — web hiện "—". */
  image_version: string;
  /** v0.1.36 (F-46): phiên bản genh ghi trong genh.json; null = không cài bằng genh / không đọc được. */
  genh_version: string | null;
  org_name: string;
  timezone: string;
  role: Me['role'];
}

export function usersEndpoints(r: ApiClient['request']) {
  const enc = encodeURIComponent;
  return {
    users: {
      list: (signal?: AbortSignal) => r<UsersPage>('/users', { signal }),
      invite: (body: InviteUserBody) => r<TempPasswordResult>('/users', { method: 'POST', body }),
      changeRole: (id: string, role: InviteRole) => r<ManagedUser>(`/users/${enc(id)}/role`, { method: 'PATCH', body: { role } }),
      /** Khoá tài khoản — thu hồi mọi phiên đang mở. */
      deactivate: (id: string) => r<ManagedUser>(`/users/${enc(id)}/deactivate`, { method: 'POST' }),
      reactivate: (id: string) => r<ManagedUser>(`/users/${enc(id)}/reactivate`, { method: 'POST' }),
      /** Mật khẩu tạm mới + buộc đổi; thu hồi mọi phiên. */
      resetPassword: (id: string) => r<TempPasswordResult>(`/users/${enc(id)}/reset-password`, { method: 'POST' }),
    },
    org: {
      get: (signal?: AbortSignal) => r<OrgSettings>('/system/org', { signal }),
      update: (body: OrgSettingsBody) => r<OrgSettings>('/system/org', { method: 'PATCH', body }),
    },
    about: (signal?: AbortSignal) => r<AboutInfo>('/system/about', { signal }),
  };
}
