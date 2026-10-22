/**
 * v0.1.59 (G2) — khung Mặt tiền Owner (`/owner/*`): anh em với khung Console (AppShell), mobile-first, 5 trụ cột.
 *
 * ┌─ BẢNG TESTID (HỢP ĐỒNG cho G3/G4/G5 — đổi tên là gãy spec của họ) ─────────────────────────────────────────────────────┐
 * │ owner-nav-intel | owner-nav-feed | owner-nav-relations | owner-nav-analytics | owner-nav-work | owner-nav-settings      │
 * │     thanh trái ≥ 760px, rộng 232px, vạch ngăn trước Cài đặt; mục đang mở có aria-current="page"                       │
 * │ owner-tabbar                          thanh dưới < 760px (đúng 5 trụ cột, không có Cài đặt)                           │
 * │ owner-tabbar-intel | -feed | -relations | -analytics | -work     icon 24 + nhãn 12; tối đa MỘT số đếm mỗi nút:          │
 * │     feed = today.nav.urgent_open (tin Khẩn đang mở) · work = today.nav.decide (mục chờ Sếp quyết)                      │
 * │ owner-ask-gen                         nút "Hỏi Gen" ở đầu trang → mở ngăn Hỏi Gen (AskGenScreen.openAskGen)           │
 * │ owner-avatar-menu                     ảnh đại diện → menu: Cài đặt · Trợ giúp · Đổi giao diện sáng/tối · Đăng xuất      │
 * │ owner-boot                            khung chờ lúc đang hỏi tài khoản                                                │
 * │ owner-shell-error                     không tải được tài khoản (câu thân thiện + "Chi tiết kỹ thuật")                 │
 * │ first-run-card                        (parts.tsx) thẻ ngày đầu — ngày đầu CHỈ có đúng một thẻ này, không chồng EmptyState │
 * └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
 *
 * - Tự kiểm phiên bằng các hook sẵn có như AppShell (không chép logic): 401 → `apiClient` đưa về /login, 428 → /setup,
 *   mật khẩu tạm (`must_change_password`) → /change-password, vai KHÁC Owner → về "/" (HomeRedirect chọn màn đầu của vai đó).
 * - Tiêu đề màn 18px/600 (h1). Chuông dùng lại NotificationBell; đổi giao diện + đăng xuất dùng lại cơ chế sẵn có (uiStore/lib/theme, api.auth).
 * - `?gen=` (link chuông): dùng lại `useOpenGenFromUrl` của AppShell — `?gen=coach` mở ngăn Hỏi Gen (thẻ "Hôm nay của Sếp" nằm trong
 *   khung Gen), `?gen=<mã hội thoại>` đáp ở /owner/gen; Gen tắt ⇒ `/guide/viec-sep` như hiện nay.
 * - Chỉ dùng token + @gen-harness/ui; CSS riêng trong owner.css (lính gác thiết kế quét tệp đó).
 */
import { Fragment, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Link, NavLink, Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Badge, Icon, Skeleton, useMediaQuery } from '@gen-harness/ui';
import { Spotlight } from '../gen/Spotlight';
import { useGenStore } from '../gen/genStore';
import { api } from '../lib/api';
import { useMe } from '../lib/queries';
import { queryClient } from '../lib/queryClient';
import { useRealtime } from '../lib/realtime';
import { useThemePref } from '../lib/theme';
import { useUiStore } from '../lib/uiStore';
import { ErrorBoundary } from '../shell/ErrorPage';
import { NotificationBell } from '../shell/NotificationBell';
import { PigMark } from '../shell/Logo';
import { initials } from '../shell/people';
import { AskGenDrawer, focusGenInput, openAskGen } from './AskGenScreen';
import {
  OWNER_NAV,
  OWNER_PATHS,
  OWNER_TABBAR,
  activeNavKey,
  asText,
  countText,
  navBadge,
  navCounts,
  ownerTitle,
  type NavCounts,
  type OwnerActiveKey,
  type OwnerNavItem,
} from './ownerModel';
import { OwnerError } from './parts';
import { useOwnerToday } from './queries';
import { useOpenGenFromUrl } from './useOpenGenFromUrl';
import './owner.css';

/** Điểm gãy thanh trái ↔ thanh dưới (khớp @media (min-width: 760px) trong owner.css / owner-kit.css). */
const WIDE_QUERY = '(min-width: 760px)';

/** Câu đọc cho trình đọc màn hình của số đếm trên nút. */
const BADGE_LABEL: Partial<Record<OwnerNavItem['key'], (n: string) => string>> = {
  feed: (n) => `${n} tin khẩn đang mở`,
  work: (n) => `${n} việc chờ Sếp quyết`,
};

export function OwnerShell() {
  const me = useMe();
  const { pathname } = useLocation();
  const wide = useMediaQuery(WIDE_QUERY);
  useRealtime();
  const genOn = !!me.data?.features?.gen;
  const today = useOwnerToday();
  const counts = navCounts(today.data);
  const coachFocus = useGenStore((s) => s.coachFocus);
  useOpenGenFromUrl(me.data?.id ?? null, genOn, { coachPath: OWNER_PATHS.intel, conversationPath: OWNER_PATHS.gen });
  // `?gen=coach` (chuông "Gen hướng dẫn"): thẻ "Hôm nay của Sếp" nằm trong khung Gen ⇒ mở ngăn Hỏi Gen để thẻ nhận cờ.
  useEffect(() => {
    if (coachFocus && genOn && pathname !== OWNER_PATHS.gen) openAskGen();
  }, [coachFocus, genOn, pathname]);

  if (me.isPending) return <OwnerBoot />;
  if (me.isError) {
    return (
      <div className="owner-boot" data-testid="owner-shell-error">
        <OwnerError error={me.error} onRetry={() => void me.refetch()} retrying={me.isFetching} />
      </div>
    );
  }
  // v0.1.19: mật khẩu tạm → mọi màn chuyển về "Đặt mật khẩu mới".
  if (me.data.must_change_password) return <Navigate to="/change-password" replace />;
  // Chỉ Owner có Mặt tiền; vai khác về "/" (HomeRedirect đưa tới màn đầu tiên của vai đó).
  if (me.data.role?.code !== 'owner') return <Navigate to="/" replace />;

  const active = activeNavKey(pathname);
  const onGenPage = active === 'gen';
  return (
    <div className="owner-app kit-scope" data-screen={active}>
      <a className="skip-link" href="#owner-main">
        Bỏ qua tới nội dung
      </a>
      {wide ? <OwnerRail active={active} counts={counts} name={asText(me.data.display_name)} org={asText(me.data.org?.name)} /> : null}
      <div className="owner-body">
        <header className="owner-top">
          <h1 className="owner-top__title">{ownerTitle(pathname)}</h1>
          <div className="owner-top__right">
            <button
              type="button"
              className="kit-btn kit-btn--secondary owner-top__ask"
              data-testid="owner-ask-gen"
              aria-label="Hỏi Gen"
              onClick={() => (onGenPage ? focusGenInput() : openAskGen())}
            >
              <Icon name="ph-fill ph-sparkle" size={16} />
              <span className="owner-top__ask-label" aria-hidden>
                Hỏi Gen
              </span>
            </button>
            <div className="owner-top__bell">
              <NotificationBell />
            </div>
            <AvatarMenu userId={me.data.id} name={asText(me.data.display_name)} email={asText(me.data.email)} />
          </div>
        </header>
        <main className="owner-main" id="owner-main" tabIndex={-1}>
          <ErrorBoundary variant="inline" resetKey={pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
      {wide ? null : <OwnerTabbar active={active} counts={counts} />}
      {genOn ? <Spotlight /> : null}
      <AskGenDrawer userId={me.data.id} genOn={genOn} />
    </div>
  );
}

function OwnerBoot() {
  return (
    <div className="owner-boot" aria-busy="true" aria-label="Đang mở Mặt tiền" data-testid="owner-boot">
      <Skeleton width={180} height={14} />
      <Skeleton width="100%" height={92} radius={8} style={{ marginTop: 16 }} />
      <Skeleton width="100%" height={92} radius={8} style={{ marginTop: 16 }} />
    </div>
  );
}

function CountBadge({ item, counts }: { item: OwnerNavItem; counts: NavCounts }) {
  const n = navBadge(item.key, counts);
  if (n <= 0) return null;
  const text = countText(n);
  return (
    <Badge kind="count" count={n} aria-label={BADGE_LABEL[item.key]?.(text)}>
      {text}
    </Badge>
  );
}

function OwnerRail({ active, counts, name, org }: { active: OwnerActiveKey; counts: NavCounts; name: string; org: string }) {
  return (
    <aside className="owner-rail" aria-label="Thanh bên Mặt tiền">
      <Link to={OWNER_PATHS.intel} className="owner-brand" aria-label="Về Tình báo">
        <span className="owner-brand__tile" aria-hidden>
          <PigMark size={20} />
        </span>
        <span className="owner-brand__text">
          <span className="owner-brand__name">Gen-Harness</span>
          {org ? <span className="owner-brand__org">{org}</span> : null}
        </span>
      </Link>
      <nav className="owner-rail__nav" aria-label="Mặt tiền">
        {OWNER_NAV.map((item) => (
          <Fragment key={item.key}>
            {item.separatorBefore ? <div className="owner-rail__sep" role="separator" /> : null}
            <NavLink
              to={item.to}
              end={item.to === OWNER_PATHS.intel}
              className="owner-nav"
              data-on={active === item.key || undefined}
              aria-current={active === item.key ? 'page' : undefined}
              data-testid={`owner-nav-${item.key}`}
            >
              <Icon name={item.icon} size={20} />
              <span className="owner-nav__name">{item.label}</span>
              <CountBadge item={item} counts={counts} />
            </NavLink>
          </Fragment>
        ))}
      </nav>
      <div className="owner-rail__foot">
        <span className="kit-avatar" aria-hidden>
          {initials(name)}
        </span>
        <span className="owner-rail__who">
          <span className="owner-rail__name">{name || 'Sếp'}</span>
          <span className="owner-rail__role">Sếp</span>
        </span>
      </div>
    </aside>
  );
}

function OwnerTabbar({ active, counts }: { active: OwnerActiveKey; counts: NavCounts }) {
  return (
    <nav className="owner-tabbar" aria-label="Thanh dưới" data-testid="owner-tabbar">
      {OWNER_TABBAR.map((item) => (
        <NavLink
          key={item.key}
          to={item.to}
          end={item.to === OWNER_PATHS.intel}
          className="owner-tabbar__item"
          data-on={active === item.key || undefined}
          aria-current={active === item.key ? 'page' : undefined}
          data-testid={`owner-tabbar-${item.key}`}
        >
          <span className="owner-tabbar__icon">
            <Icon name={item.icon} size={24} />
            <CountBadge item={item} counts={counts} />
          </span>
          <span className="owner-tabbar__name">{item.label}</span>
        </NavLink>
      ))}
    </nav>
  );
}

/**
 * Ảnh đại diện → menu: Cài đặt · Trợ giúp · Đổi giao diện sáng/tối · Đăng xuất. Đổi giao diện dùng lại `uiStore.setTheme` (cùng
 * nơi lưu với ThemeToggle của Console); đăng xuất dùng lại `api.auth.logout` như AccountFooter. Esc / bấm ra ngoài đóng menu.
 */
function AvatarMenu({ userId, name, email }: { userId: string; name: string; email: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const wrap = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const pref = useThemePref(userId);
  const setTheme = useUiStore((s) => s.setTheme);

  useEffect(() => {
    if (!open) return;
    menu.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    const onDown = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const close = (refocus: boolean) => {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  };
  const go = (to: string) => {
    close(false);
    navigate(to);
  };
  const isLight = typeof document !== 'undefined' && document.documentElement.dataset.theme === 'light';
  const logout = async () => {
    setBusy(true);
    try {
      await api.auth.logout();
    } catch {
      // phiên đã mất thì cũng về trang đăng nhập
    }
    queryClient.clear();
    navigate('/login', { replace: true });
  };
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      items[(i + 1) % items.length]?.focus();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      items[(i - 1 + items.length) % items.length]?.focus();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      close(true);
    } else if (e.key === 'Tab') {
      close(false);
    }
  };

  return (
    <div className="owner-menu-wrap" ref={wrap}>
      <button
        ref={trigger}
        type="button"
        className="owner-avatar-btn"
        data-testid="owner-avatar-menu"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={name ? `Tài khoản của ${name}` : 'Tài khoản của Sếp'}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="kit-avatar" aria-hidden>
          {initials(name)}
        </span>
      </button>
      {open ? (
        <div className="owner-menu" role="menu" aria-label="Tài khoản" ref={menu} onKeyDown={onKeyDown}>
          {name || email ? (
            <div className="owner-menu__who">
              {name ? <div className="owner-menu__name">{name}</div> : null}
              {email ? <div className="owner-menu__email">{email}</div> : null}
            </div>
          ) : null}
          <button type="button" role="menuitem" className="owner-menu__item" onClick={() => go(OWNER_PATHS.settings)}>
            <Icon name="ph ph-sliders-horizontal" size={20} />
            Cài đặt
          </button>
          <button type="button" role="menuitem" className="owner-menu__item" onClick={() => go('/help')}>
            <Icon name="ph ph-question" size={20} />
            Trợ giúp
          </button>
          <button
            type="button"
            role="menuitem"
            className="owner-menu__item"
            onClick={() => {
              setTheme(isLight ? 'dark' : 'light', userId);
              close(true);
            }}
          >
            <Icon name={isLight ? 'ph ph-moon' : 'ph ph-sun'} size={20} />
            <span className="owner-menu__text">
              Đổi giao diện sáng/tối
              <span className="owner-menu__hint">{pref === 'system' ? 'Đang theo hệ thống' : isLight ? 'Đang sáng' : 'Đang tối'}</span>
            </span>
          </button>
          <div className="owner-menu__sep" role="separator" />
          <button type="button" role="menuitem" className="owner-menu__item" onClick={() => void logout()} disabled={busy}>
            <Icon name="ph ph-sign-out" size={20} />
            Đăng xuất
          </button>
        </div>
      ) : null}
    </div>
  );
}
