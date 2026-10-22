import { useCallback, useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';

export type SidebarMode = 'full' | 'rail';
/** v0.1.23 (B7): `system` = theo cài đặt sáng/tối của máy. */
export type ThemePref = 'system' | 'light' | 'dark';

interface UiPrefs {
  /** Design prop `sidebarMode` — 244px full / 60px icon rail. */
  sidebarMode: SidebarMode;
  /** Explicit open/closed state per nav group (by group name); absent = auto. */
  navOpen: Record<string, boolean>;
  /** Lựa chọn giao diện gần nhất trên trình duyệt này (dùng cả trước khi đăng nhập — public/theme-init.js). */
  theme: ThemePref;
  /** Lựa chọn giao diện theo từng người dùng (id) — hai người dùng chung máy không đè lựa chọn của nhau. */
  themeByUser: Record<string, ThemePref>;
  /** Ngăn kéo điều hướng trên điện thoại (B4) — không lưu. */
  drawerOpen: boolean;
  /**
   * v0.1.42: domain thu gọn (Nâng cao) người dùng đã bấm mở/đóng — KHÔNG lưu (không nằm trong partialize): mở lại
   * trang là thu gọn như mặc định.
   */
  domainOpen: Record<string, boolean>;
  setSidebarMode: (m: SidebarMode) => void;
  toggleSidebarMode: () => void;
  setNavOpen: (group: string, open: boolean) => void;
  setTheme: (theme: ThemePref, userId?: string | null) => void;
  setDrawerOpen: (open: boolean) => void;
  setDomainOpen: (domain: string, open: boolean) => void;
}

const safeStorage = createJSONStorage<Partial<UiPrefs>>(() => {
  try {
    return window.localStorage;
  } catch {
    const mem = new Map<string, string>();
    return {
      getItem: (k: string) => mem.get(k) ?? null,
      setItem: (k: string, v: string) => void mem.set(k, v),
      removeItem: (k: string) => void mem.delete(k),
    };
  }
});

/**
 * Nâng bản đã lưu lên bản hiện tại: xoá `showEnglish` (v2) và `followUpHiddenByUser` (v3, v0.1.54); mọi lựa chọn khác giữ
 * nguyên. Dữ liệu hỏng (không phải đối tượng) ⇒ trả đối tượng rỗng (store dùng mặc định), không ném lỗi.
 */
export function migrateUiPrefs(persisted: unknown): Partial<UiPrefs> {
  const base = persisted && typeof persisted === 'object' && !Array.isArray(persisted) ? (persisted as Record<string, unknown>) : {};
  const s = { ...base };
  delete s.showEnglish;
  delete s.followUpHiddenByUser;
  return s as Partial<UiPrefs>;
}

/** Per-viewer layout preferences (persisted in localStorage). */
export const useUiStore = create<UiPrefs>()(
  persist(
    (set) => ({
      sidebarMode: 'full',
      navOpen: {},
      theme: 'system',
      themeByUser: {},
      drawerOpen: false,
      domainOpen: {},
      setSidebarMode: (sidebarMode) => set({ sidebarMode }),
      toggleSidebarMode: () => set((s) => ({ sidebarMode: s.sidebarMode === 'full' ? 'rail' : 'full' })),
      setNavOpen: (group, open) => set((s) => ({ navOpen: { ...s.navOpen, [group]: open } })),
      setTheme: (theme, userId) =>
        set((s) => ({ theme, themeByUser: userId ? { ...s.themeByUser, [userId]: theme } : s.themeByUser })),
      setDrawerOpen: (drawerOpen) => set({ drawerOpen }),
      setDomainOpen: (domain, open) => set((s) => ({ domainOpen: { ...s.domainOpen, [domain]: open } })),
    }),
    {
      name: 'gh-ui',
      storage: safeStorage,
      // v2 (v0.1.42, F-63): bỏ hẳn phụ đề tiếng Anh — xoá field `showEnglish` đã lưu ở mọi bản cũ (v0: mặc định bật,
      // v1: mặc định tắt). Lựa chọn khác (thanh bên, giao diện, nhóm mở/đóng) giữ nguyên.
      // v3 (v0.1.54): bỏ nút "Ẩn" của thẻ "Việc thiết lập tiếp" (nay "Để sau 7 ngày" lưu ở máy chủ) — xoá khoá
      // `followUpHiddenByUser` đã lưu. Dữ liệu hỏng (không phải đối tượng) ⇒ bắt đầu lại từ mặc định, không ném lỗi.
      version: 3,
      migrate: migrateUiPrefs,
      partialize: (s) => ({
        sidebarMode: s.sidebarMode,
        navOpen: s.navOpen,
        theme: s.theme,
        themeByUser: s.themeByUser,
      }),
    },
  ),
);

// ── Screen state mirrored into the URL query (tabs, filters, expanded rows) ──
// docs/handoff/07 §5: "Bộ lọc, tab, mục mở rộng phản ánh vào URL; tải lại giữ nguyên".

interface UrlState {
  params: Record<string, string>;
  hydrate: (search: string) => void;
}

export const useUrlStateStore = create<UrlState>((set) => ({
  params: {},
  hydrate: (search) => set({ params: Object.fromEntries(new URLSearchParams(search)) }),
}));

/** Keeps the URL-state store in step with the location. Mount once inside the router. */
export function UrlStateSync(): null {
  const { search } = useLocation();
  useEffect(() => {
    useUrlStateStore.getState().hydrate(search);
  }, [search]);
  return null;
}

/**
 * `const [tab, setTab] = useUrlState('tab', 'channels')` — reads from the
 * store (hydrated from the URL) and writes back with history.replace.
 */
export function useUrlState<T extends string>(key: string, fallback: T): [T, (v: T) => void] {
  const value = useUrlStateStore((s) => s.params[key]) as T | undefined;
  const navigate = useNavigate();
  const location = useLocation();
  const set = useCallback(
    (v: T) => {
      const qs = new URLSearchParams(location.search);
      if (v === fallback) qs.delete(key);
      else qs.set(key, v);
      const s = qs.toString();
      useUrlStateStore.setState((st) => {
        const params = { ...st.params };
        if (v === fallback) delete params[key];
        else params[key] = v;
        return { params };
      });
      navigate({ pathname: location.pathname, search: s ? `?${s}` : '' }, { replace: true });
    },
    [fallback, key, location.pathname, location.search, navigate],
  );
  return [value ?? fallback, set];
}

/**
 * Đổi NHIỀU khoá URL-state trong một lần (`null`/'' = bỏ khoá). `useUrlState` chỉ đổi một khoá mỗi lần nên gọi liên tiếp
 * nhiều cái trong cùng một sự kiện sẽ ghi đè nhau (mỗi lần dựng URL từ `location.search` cũ) — dùng hàm này khi một thao tác
 * đổi từ hai khoá trở lên (xoá bộ lọc, đổi kênh kèm bỏ nhóm).
 */
export function useUrlPatch(): (patch: Record<string, string | null>) => void {
  const navigate = useNavigate();
  const location = useLocation();
  return useCallback(
    (patch) => {
      const qs = new URLSearchParams(location.search);
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === '') qs.delete(k);
        else qs.set(k, v);
      }
      useUrlStateStore.setState((st) => {
        const params = { ...st.params };
        for (const [k, v] of Object.entries(patch)) {
          if (v === null || v === '') delete params[k];
          else params[k] = v;
        }
        return { params };
      });
      const s = qs.toString();
      navigate({ pathname: location.pathname, search: s ? `?${s}` : '' }, { replace: true });
    },
    [location.pathname, location.search, navigate],
  );
}
