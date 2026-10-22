import { useEffect, useSyncExternalStore } from 'react';
import { useUiStore, type ThemePref } from './uiStore';

/** v0.1.23 (B7) — giao diện sáng/tối. Bảng màu sáng ở src/styles/theme.css; mặc định theo hệ thống. */
export type ResolvedTheme = 'light' | 'dark';

const LIGHT_QUERY = '(prefers-color-scheme: light)';

function systemMedia(): MediaQueryList | null {
  try {
    return typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia(LIGHT_QUERY) : null;
  } catch {
    return null;
  }
}

/** Không nhận được tín hiệu từ máy → tối (thiết kế gốc Nocturne). */
export function resolveTheme(pref: ThemePref, systemPrefersLight: boolean): ResolvedTheme {
  if (pref === 'light' || pref === 'dark') return pref;
  return systemPrefersLight ? 'light' : 'dark';
}

/** Vòng: theo hệ thống → sáng → tối → theo hệ thống. */
export function nextTheme(pref: ThemePref): ThemePref {
  return pref === 'system' ? 'light' : pref === 'light' ? 'dark' : 'system';
}

export const THEME_LABEL: Record<ThemePref, string> = {
  system: 'Theo hệ thống',
  light: 'Sáng',
  dark: 'Tối',
};

export const THEME_ICON: Record<ThemePref, string> = {
  system: 'ph ph-circle-half',
  light: 'ph ph-sun',
  dark: 'ph ph-moon',
};

const THEME_COLOR: Record<ResolvedTheme, string> = { dark: '#161826', light: '#f5f6fa' };

export function applyTheme(theme: ResolvedTheme): void {
  if (typeof document === 'undefined') return;
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', THEME_COLOR[theme]);
}

function subscribeSystem(cb: () => void): () => void {
  const mq = systemMedia();
  if (!mq) return () => {};
  mq.addEventListener?.('change', cb);
  return () => mq.removeEventListener?.('change', cb);
}

const systemLight = () => !!systemMedia()?.matches;

/** Lựa chọn của người dùng `userId` (nếu có), rơi về lựa chọn gần nhất trên trình duyệt này. */
export function useThemePref(userId?: string | null): ThemePref {
  return useUiStore((s) => (userId ? (s.themeByUser[userId] ?? s.theme) : s.theme));
}

/** Gắn một lần ở gốc cây: giữ `<html data-theme>` khớp lựa chọn + cài đặt máy (đổi máy → đổi ngay). */
export function useThemeSync(userId?: string | null): ResolvedTheme {
  const pref = useThemePref(userId);
  const prefersLight = useSyncExternalStore(subscribeSystem, systemLight, () => false);
  const resolved = resolveTheme(pref, prefersLight);
  useEffect(() => {
    applyTheme(resolved);
  }, [resolved]);
  return resolved;
}
