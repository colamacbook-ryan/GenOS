/**
 * Hợp đồng v0.1.46 (F-21) — Truy cập từ xa: `GET /system/access` (`system.read`). Địa chỉ đăng nhập lấy từ
 * GH_PUBLIC_URL (KHÔNG phải địa chỉ trình duyệt đang mở); chế độ/bind do `genh remote` ghi vào `run/network-status.json`.
 * Mọi trường là chuỗi/bool/null.
 */
import type { ApiClient } from './client';

export type AccessMode = 'local' | 'lan' | 'lan_legacy' | 'tailscale' | 'cloudflare' | 'unknown';

export interface AccessInfo {
  /** GH_PUBLIC_URL bỏ dấu '/' cuối. */
  public_url: string;
  /** `public_url` + '/login' — địa chỉ gửi cho nhân viên. */
  login_url: string;
  /** true khi `public_url` chỉ mở được trên chính máy chủ (localhost, 127.x, ::1, 0.0.0.0…). */
  public_url_local: boolean;
  mode: AccessMode;
  bind_addr: '127.0.0.1' | '0.0.0.0' | null;
  site_address: string | null;
  checked_at: string | null;
  /** Chỉ Owner. */
  can_manage: boolean;
}

export function accessEndpoints(r: ApiClient['request']) {
  return {
    system: {
      access: (signal?: AbortSignal) => r<AccessInfo>('/system/access', { signal }),
    },
  };
}
