/** Presentation logic for Plugin & Tiện ích — cùng khuôn `apiModel.ts`/`agentsModel.ts`. */
import type { PluginBreakerState, PluginHealth, PluginItem } from '@gen-harness/contracts';

export const OK = 'var(--color-ok)';
export const WARN = 'var(--color-warn)';
export const BAD = 'var(--color-bad)';
export const N5 = 'var(--color-neutral-500)';

export const HEALTH_LABEL: Record<PluginHealth, string> = {
  healthy: 'Khoẻ mạnh',
  degraded: 'Suy giảm',
  isolated: 'Bị cách ly',
  disabled: 'Đang tắt',
};

export function healthTone(h: PluginHealth): string {
  if (h === 'healthy') return OK;
  if (h === 'degraded') return WARN;
  if (h === 'isolated') return BAD;
  return N5;
}

export const BREAKER_LABEL: Record<PluginBreakerState, string> = {
  closed: 'Đóng',
  half_open: 'Nửa mở — đang thử',
  open: 'Mở — đã cách ly',
};

export function breakerTone(s: PluginBreakerState): string {
  if (s === 'closed') return OK;
  if (s === 'half_open') return WARN;
  return BAD;
}

export const ORIGIN_LABEL: Record<PluginItem['origin'], string> = {
  core: 'DSH · dựng sẵn',
  marketplace: 'Chợ tiện ích',
  local_file: 'Nạp từ tệp',
};

export function fmtMem(mb: unknown): string {
  return typeof mb === 'number' ? `${mb} MB` : '—';
}

/** Vì sao nút bật/tắt bị vô hiệu — khớp `PluginError` của `gh.chassis.plugins.PluginManager.disable`. */
export function disableBlockReason(p: PluginItem, all: PluginItem[]): string | null {
  if (!p.can_disable) return `${p.name} là plugin nền, không tắt được`;
  if (p.enabled) {
    const dependent = all.find((o) => o.enabled && o.package !== p.package && o.dependencies?.includes?.(p.package));
    if (dependent) return `${dependent.name} đang cần plugin này`;
  } else {
    const off = (p.dependencies ?? []).map((d) => all.find((x) => x.package === d)).find((d) => d && !d.enabled);
    if (off) return `Cần bật ${off.name} trước`;
  }
  return null;
}

export function removeBlockReason(p: PluginItem): string | null {
  if (!p.removable) return `${p.name} là plugin nền, không gỡ được`;
  return null;
}

export function sha256Hex(buf: ArrayBuffer): Promise<string> {
  return crypto.subtle.digest('SHA-256', buf).then((h) => Array.from(new Uint8Array(h)).map((b) => b.toString(16).padStart(2, '0')).join(''));
}
