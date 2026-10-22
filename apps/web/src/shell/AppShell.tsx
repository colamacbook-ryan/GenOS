import { useEffect, useMemo } from 'react';
import { Navigate, Outlet, useLocation, useMatches } from 'react-router-dom';
import { DOMAINS, SCREEN_BY_KEY, type DomainId } from '@gen-harness/contracts';
import { useActiveNavKey, useActiveScreenKey, type RouteHandle } from './routeHandles';
import { useMe, useNavigation } from '../lib/queries';
import { useRealtime } from '../lib/realtime';
import { useUiStore } from '../lib/uiStore';
import { Header, type Crumbs } from './Header';
import { findActive } from './navModel';
import { Sidebar } from './Sidebar';
import { ErrorBoundary } from './ErrorPage';
import { GenPanel } from '../gen/GenPanel';
import { Spotlight } from '../gen/Spotlight';
import { useGenStore } from '../gen/genStore';
import { useOpenGenFromUrl } from '../owner/useOpenGenFromUrl';

function useCrumbs(activeKey: string | null): Crumbs | null {
  const matches = useMatches();
  const nav = useNavigation();
  return useMemo(() => {
    if (!activeKey) {
      const page = [...matches].reverse().map((m) => (m.handle as RouteHandle | undefined)?.page).find(Boolean);
      return page ? { domain: page.domain, group: null, title: page.title } : null;
    }
    const meta = SCREEN_BY_KEY[activeKey];
    let domainId: DomainId | undefined;
    let group: string | null = null;
    for (const m of matches) {
      const h = m.handle as RouteHandle | undefined;
      if (h?.domain) domainId = h.domain;
      if (h?.group) group = h.group;
    }
    // Prefer the server tree (labels are the API's), fall back to the route tree.
    const hit = findActive(nav.data, activeKey);
    return {
      domain: hit?.domain.crumb ?? (domainId ? DOMAINS[domainId].crumb : ''),
      group: hit ? (hit.isChild ? hit.group.name : null) : group,
      title: meta?.title ?? hit?.item.name ?? activeKey,
    };
  }, [activeKey, matches, nav.data]);
}

export function AppShell() {
  const mode = useUiStore((s) => s.sidebarMode);
  const activeKey = useActiveScreenKey();
  // v0.1.42: trang ngoài danh mục (social, guide, account, help) tô sáng mục thanh bên theo `navKey`.
  const navKey = useActiveNavKey();
  const crumbs = useCrumbs(activeKey);
  const me = useMe();
  const { pathname } = useLocation();
  const drawerOpen = useUiStore((s) => s.drawerOpen);
  const setDrawerOpen = useUiStore((s) => s.setDrawerOpen);
  useRealtime();
  // B4: ngăn kéo danh mục (điện thoại) tự đóng khi đổi trang; Esc cũng đóng.
  useEffect(() => {
    setDrawerOpen(false);
  }, [pathname, setDrawerOpen]);
  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDrawerOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [drawerOpen, setDrawerOpen]);
  // v0.1.21: khung Gen (cờ gen.enabled, v1 chỉ Owner) — mở/đóng nhớ theo từng người dùng.
  const genOn = !!me.data?.features?.gen;
  const genOpen = useGenStore((s) => (me.data ? !!s.openByUser[me.data.id] : false)) && genOn;
  // Điện thoại: khung Gen phủ cả màn — khi Gen đang chỉ vào một phần tử thì tạm ẩn khung để thấy phần tử (gen.css).
  const spotting = useGenStore((s) => !!s.spotlight);
  useOpenGenFromUrl(me.data?.id ?? null, genOn);
  // v0.1.19: mật khẩu tạm (genh reset-password) → mọi màn Console chuyển về "Đặt mật khẩu mới".
  if (me.data?.must_change_password) return <Navigate to="/change-password" replace />;
  return (
    <div
      className="app"
      data-sidebar={mode}
      data-gen={genOpen ? 'open' : undefined}
      data-drawer={drawerOpen ? 'open' : undefined}
      data-spot={spotting ? 'on' : undefined}
    >
      <a className="skip-link" href="#main">
        Bỏ qua tới nội dung
      </a>
      <Sidebar activeKey={navKey} />
      {drawerOpen ? <div className="sb-backdrop" aria-hidden onClick={() => setDrawerOpen(false)} /> : null}
      <main className="main" id="main" tabIndex={-1}>
        <Header
          crumbs={crumbs}
          advanced={!!activeKey && SCREEN_BY_KEY[activeKey]?.domain === 'tech'}
          screenKey={activeKey}
        />
        <div className="content">
          {/* B5: màn lỗi chỉ thay vùng nội dung — thanh bên/header vẫn dùng được; đổi trang là thử vẽ lại. */}
          <ErrorBoundary variant="inline" resetKey={pathname}>
            <Outlet />
          </ErrorBoundary>
        </div>
      </main>
      {genOpen && me.data ? <GenPanel userId={me.data.id} /> : null}
      {genOn ? <Spotlight /> : null}
    </div>
  );
}
