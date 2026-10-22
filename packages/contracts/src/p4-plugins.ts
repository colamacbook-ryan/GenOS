/**
 * Hợp đồng API giai đoạn 4 · Plugin & Tiện ích (PLAN 4.4, ARCHITECTURE §6, `apps/api/gh/plugins_api/routes.py`).
 *
 * Thay hẳn namespace `plugins` cũ ở `endpoints.ts` (chỉ có `list/toggle/remove`, kiểu `Plugin` rời rạc từ
 * `schema.ts` — sản phẩm phase 1 khi màn này chưa dựng) bằng bản đầy đủ khớp `list_plugins` thật (origin,
 * removable/can_disable, sandbox, permissions, breaker, health…) cộng thêm reset breaker, nhật ký, và nạp từ
 * tệp. Không có `/plugins/market` hay `/plugins/load-order`/`/plugins/events` riêng — thứ tự nạp đã nằm trong
 * `load_order` + cách API sắp xếp danh sách, sự kiện LIVE đi qua WS `plugin.log` (không phải REST riêng).
 */
import type { ApiClient } from './client';

export type PluginOrigin = 'core' | 'marketplace' | 'local_file';
export type PluginHealth = 'healthy' | 'degraded' | 'isolated' | 'disabled';
export type PluginBreakerState = 'closed' | 'open' | 'half_open';
export type PluginPermissionsStatus = 'active' | 'pending' | null;

export interface PluginBreaker {
  state: PluginBreakerState;
  total_errors: number;
  last_error: string | null;
}

export interface PluginItem {
  package: string;
  name: string;
  layer: string;
  origin: PluginOrigin;
  version: string;
  description: string | null;
  enabled: boolean;
  load_order: number | null;
  /** `false` khi `origin === 'core'` (khoá cứng §6.2) hoặc manifest khai `removable: false`. */
  removable: boolean;
  /** `false` khi manifest khai `can_disable: false` — nút bật/tắt cũng phải vô hiệu, không riêng nút gỡ. */
  can_disable: boolean;
  sandbox: Record<string, unknown> | null;
  permissions: string[];
  signature_ok: boolean;
  permissions_status: PluginPermissionsStatus;
  installed_at: string;
  dependencies: string[];
  health: PluginHealth;
  breaker: PluginBreaker;
}

export interface PluginLogEntry {
  id: string;
  at: string;
  level: string;
  message: string;
  ctx: unknown;
}

export interface PluginLogPage {
  items: PluginLogEntry[];
  next_cursor: string | null;
}

/** Sự kiện LIVE `plugin.log` (WS) — một dòng nhật ký vừa ghi, kèm gói để màn biết gắn vào đâu. */
export type PluginLogEvent = PluginLogEntry & { package: string };

export interface PluginLocalInstallBody {
  manifest: Record<string, unknown>;
  code_sha256: string;
  signature: string;
}

export interface PluginLocalInstallResult {
  id: string;
  package: string;
  name: string;
  version: string;
  origin: 'local_file';
  is_enabled: false;
  permissions_status: 'pending';
  signature_ok: boolean;
  permissions: string[];
  installed_at: string;
}

const enc = encodeURIComponent;

export function pluginsEndpoints(r: ApiClient['request']) {
  return {
    plugins: {
      list: (signal?: AbortSignal) => r<PluginItem[]>('/plugins', { signal }),
      toggle: (pkg: string, enabled: boolean) => r<PluginItem>(`/plugins/${enc(pkg)}/toggle`, { method: 'PATCH', body: { enabled } }),
      remove: (pkg: string) => r<void>(`/plugins/${enc(pkg)}`, { method: 'DELETE' }),
      breakerReset: (pkg: string) => r<PluginItem>(`/plugins/${enc(pkg)}/breaker/reset`, { method: 'POST' }),
      logs: (pkg: string, q: { cursor?: string; limit?: number } = {}, signal?: AbortSignal) =>
        r<PluginLogPage>(`/plugins/${enc(pkg)}/logs`, { signal, query: q }),
      installLocal: (body: PluginLocalInstallBody) => r<PluginLocalInstallResult>('/plugins/local', { method: 'POST', body }),
    },
  };
}
