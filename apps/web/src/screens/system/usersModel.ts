/** Người dùng (v0.1.22, Đợt B1) + Tổ chức (Đợt B2) — hằng số và hàm thuần dùng chung với test. */
import type { InviteRole, ManagedUser, TempPasswordResult } from '@gen-harness/contracts';

export const ORG_KEY = ['system', 'org'] as const;

export const USERS_KEY = ['system', 'users'] as const;

export const INVITE_ROLES: Array<{ value: InviteRole; label: string }> = [
  { value: 'manager', label: 'Quản lý — thấy việc được giao' },
  { value: 'operator', label: 'Vận hành — xử lý hàng đợi việc' },
  { value: 'agent_staff', label: 'Nhân viên phụ trách — chỉ khách được phân' },
  { value: 'auditor', label: 'Kiểm soát — chỉ xem' },
];

/** Trạng thái hiển thị của một người dùng. */
export function userStatus(u: Pick<ManagedUser, 'status' | 'must_change_password' | 'last_login_at'>): { label: string; tone: 'ok' | 'warn' | 'neutral' } {
  if (u.status === 'inactive') return { label: 'Đã khoá', tone: 'neutral' };
  if (u.must_change_password) return { label: u.last_login_at ? 'Chờ đổi mật khẩu' : 'Chưa đăng nhập', tone: 'warn' };
  return { label: 'Hoạt động', tone: 'ok' };
}

/**
 * v0.1.46 (F-21): cùng luật với `is_local_url` của api — địa chỉ chỉ mở được trên chính máy chủ (localhost, *.localhost,
 * 127.0.0.0/8, ::1, 0.0.0.0); rỗng/hỏng cũng coi là local.
 */
export function isLocalAddress(url: string | null | undefined): boolean {
  const raw = (url ?? '').trim();
  if (!raw) return true;
  let host: string;
  try {
    host = new URL(raw.includes('//') ? raw : `//${raw}`, 'http://x.invalid').hostname;
  } catch {
    return true;
  }
  host = host.toLowerCase().replace(/^\[|\]$/g, '').replace(/\.$/, '');
  if (!host) return true;
  if (host === 'localhost' || host.endsWith('.localhost')) return true;
  if (host === '::1' || host === '::' || host === '0:0:0:0:0:0:0:1') return true;
  const v4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host);
  if (v4) return Number(v4[1]) === 127 || v4.slice(1).every((x) => Number(x) === 0);
  return false;
}

/**
 * v0.1.28 (UX V4): một lời nhắn gửi một lần là đủ — địa chỉ đăng nhập + email + mật khẩu tạm. v0.1.46 (F-21): nhận
 * `loginUrl` đầy đủ từ `GET /system/access` (KHÔNG dùng địa chỉ trình duyệt đang mở); `null` (chưa đọc được) ⇒ bỏ dòng địa chỉ.
 */
export function inviteMessage(r: Pick<TempPasswordResult, 'user' | 'temp_password'>, loginUrl: string | null): string {
  return [
    `Chào ${r.user.display_name}, mời bạn vào Gen-Harness:`,
    ...(loginUrl ? [`Địa chỉ: ${loginUrl}`] : []),
    `Email: ${r.user.email}`,
    `Mật khẩu tạm: ${r.temp_password}`,
    'Lần đăng nhập đầu hệ thống sẽ yêu cầu bạn đặt mật khẩu mới.',
  ].join('\n');
}
