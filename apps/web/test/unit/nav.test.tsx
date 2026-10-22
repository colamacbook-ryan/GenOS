import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, RouterProvider, Routes, createMemoryRouter, useLocation } from 'react-router-dom';
import type { NavDomain } from '@gen-harness/contracts';
import { domainOpen, findActive, firstScreenKey, groupAction, groupView, screenKeys, visibleGroups } from '../../src/shell/navModel';
import { useActiveNavKey } from '../../src/shell/routeHandles';
import { Sidebar } from '../../src/shell/Sidebar';
import { qk } from '../../src/lib/queries';
import { useUiStore } from '../../src/lib/uiStore';
import { buildNavigation } from '../mock-api';

const NAV: NavDomain[] = buildNavigation();
const business = NAV[0];
const tech = NAV[1];
const queueGroup = business.groups.find((g) => g.name === 'Hộp thư & Việc')!;
const graph = tech.groups.find((g) => g.key === 'graph')!;

describe('navModel', () => {
  it('finds the active screen, its domain and parent group', () => {
    const hit = findActive(NAV, 'workbench')!;
    expect(hit.domain.crumb).toBe('HẰNG NGÀY');
    expect(hit.group.name).toBe('Hộp thư & Việc');
    expect(hit.isChild).toBe(true);
    expect(findActive(NAV, 'system')!.isChild).toBe(false);
    expect(findActive(NAV, 'nope')).toBeNull();
  });

  it('lists every screen key in the tree, hidden ones included (21 design + 3 spec extras + Kết nối, Đội ngũ + Nhật ký lắng nghe)', () => {
    expect(screenKeys(NAV).size).toBe(27);
    expect(screenKeys(NAV).has('profile')).toBe(true);
    expect(screenKeys(NAV).has('plugins')).toBe(true);
  });

  it('v0.1.42: count = số màn không ẩn; Nâng cao thu gọn mặc định', () => {
    expect(NAV.map((d) => d.count)).toEqual([14, 11]);
    expect(buildNavigation(new Set(), true, { hasStaff: false }).map((d) => d.count)).toEqual([12, 11]);
    expect(business.collapsed).toBe(false);
    expect(tech.collapsed).toBe(true);
    expect(visibleGroups(tech).map((g) => g.key ?? g.name)).toEqual(['Tầng dữ liệu', 'Agent & Model', 'graph', 'supply']);
  });

  it('v0.1.42 (F-26): firstScreenKey bỏ qua màn ẩn', () => {
    expect(firstScreenKey(NAV)).toBe('overview');
    expect(firstScreenKey(buildNavigation(new Set(['overview'])))).toBe('inbox');
    expect(firstScreenKey([])).toBeNull();
    expect(firstScreenKey(undefined)).toBeNull();
  });

  it('v0.1.42: domain thu gọn mở khi màn đang mở thuộc domain, hoặc khi người dùng bấm', () => {
    expect(domainOpen(tech, 'overview', undefined)).toBe(false);
    expect(domainOpen(tech, 'raw', undefined)).toBe(true);
    expect(domainOpen(tech, 'notebook', undefined)).toBe(true);
    expect(domainOpen(tech, 'overview', true)).toBe(true);
    expect(domainOpen(business, 'raw', false)).toBe(true);
  });

  it('auto-expands the group that contains the active screen', () => {
    expect(groupView(queueGroup, 'inbox', {}, true)).toMatchObject({ open: true, on: true, activeKid: true, self: false });
    expect(groupView(queueGroup, 'overview', {}, true)).toMatchObject({ open: false, on: false });
  });

  it('respects an explicit collapse even when the group contains the active screen', () => {
    expect(groupView(queueGroup, 'inbox', { 'Hộp thư & Việc': false }, true).open).toBe(false);
  });

  it('never shows children in rail mode', () => {
    expect(groupView(queueGroup, 'inbox', { 'Hộp thư & Việc': true }, false)).toMatchObject({ open: false, showCaret: false });
  });

  it('a pure group toggles in full mode, starting from its auto state', () => {
    expect(groupAction(queueGroup, 'overview', {}, true)).toEqual({ type: 'toggle', group: 'Hộp thư & Việc', open: true });
    expect(groupAction(queueGroup, 'inbox', {}, true)).toEqual({ type: 'toggle', group: 'Hộp thư & Việc', open: false });
  });

  it('a pure group opens its first child in rail mode', () => {
    expect(groupAction(queueGroup, 'overview', {}, false)).toEqual({ type: 'navigate', key: 'inbox' });
  });

  it('an entry that is also a screen opens it and expands (Bản đồ quan hệ)', () => {
    expect(groupAction(graph, 'overview', {}, true)).toEqual({ type: 'navigate', key: 'graph' });
    expect(groupView(graph, 'graph', {}, true)).toMatchObject({ self: true, open: true });
    expect(groupView(graph, 'notebook', {}, true)).toMatchObject({ self: false, activeKid: true, open: true });
  });

  it('v0.1.42: Đội ngũ không có caret khi Đánh giá/Chăm sóc ẩn (chưa có nhân viên)', () => {
    const team = buildNavigation(new Set(), true, { hasStaff: false })[0].groups.find((g) => g.key === 'team')!;
    expect(team.children!.every((c) => c.hidden)).toBe(true);
    expect(groupView(team, 'team', {}, true)).toMatchObject({ self: true, open: false, showCaret: false });
  });
});

function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname}</div>;
}

function renderSidebar(nav: NavDomain[], path: string, role: { code: string; name: string } = { code: 'owner', name: 'Owner — Sếp' }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  qc.setQueryData(qk.navigation, nav);
  qc.setQueryData(qk.me, {
    id: 'u', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A (Chủ)',
    role,
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
    addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: {},
  });
  const key = path.slice(1);
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="*"
            element={
              <>
                <Sidebar activeKey={key} />
                <Where />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('<Sidebar>', () => {
  beforeEach(() => {
    useUiStore.setState({ sidebarMode: 'full', navOpen: {}, domainOpen: {} });
  });

  it('v0.1.42: Owner — cây mới, ≤ 7 mục cấp 1 (6 + Nâng cao), Nâng cao thu gọn', () => {
    renderSidebar(NAV, '/overview');
    const nav = screen.getByRole('navigation', { name: 'Danh mục màn hình' });
    expect(within(nav).getByText('Việc hằng ngày')).toBeInTheDocument();
    expect(within(nav).getByText('14 màn')).toBeInTheDocument();
    const level1 = Array.from(nav.querySelectorAll('[data-level1]'));
    expect(level1.length).toBeLessThanOrEqual(7);
    expect(level1.map((el) => el.querySelector('.sb-item__name')?.textContent)).toEqual([
      'Hôm nay', 'Hộp thư & Việc', 'Khách & Cơ hội', 'Kết nối', 'Đội ngũ', 'Cài đặt', 'Nâng cao',
    ]);
    const overview = within(nav).getByRole('link', { name: /Hôm nay/ });
    expect(overview).toHaveAttribute('aria-current', 'page');
    expect(within(overview).getByText('9')).toBeInTheDocument();
    // Nâng cao: đóng, không hiện mục con.
    const adv = within(nav).getByRole('button', { name: /Nâng cao/ });
    expect(adv).toHaveAttribute('aria-expanded', 'false');
    expect(within(nav).queryByText('Tầng dữ liệu')).not.toBeInTheDocument();
    expect(within(nav).queryByText('Kho dữ liệu thô')).not.toBeInTheDocument();
    // children of collapsed groups are not rendered
    expect(within(nav).queryByText('Hộp thư')).not.toBeInTheDocument();
    expect(screen.getByText('VA')).toBeInTheDocument();
    expect(screen.getByText('Owner · thấy toàn cảnh')).toBeInTheDocument();
  });

  it('v0.1.42: bấm Nâng cao thì hiện, bấm Tầng dữ liệu thì thấy Kho dữ liệu thô', async () => {
    const user = userEvent.setup();
    renderSidebar(NAV, '/overview');
    const adv = screen.getByRole('button', { name: /Nâng cao/ });
    await user.click(adv);
    expect(adv).toHaveAttribute('aria-expanded', 'true');
    await user.click(screen.getByRole('button', { name: /Tầng dữ liệu/ }));
    expect(screen.getByRole('link', { name: /Kho dữ liệu thô/ })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Bản đồ quan hệ/ })).toBeInTheDocument();
    await user.click(adv);
    expect(screen.queryByText('Kho dữ liệu thô')).not.toBeInTheDocument();
  });

  it('v0.1.42: đang ở /raw thì Nâng cao tự mở; trạng thái mở không lưu', () => {
    renderSidebar(NAV, '/raw');
    expect(screen.getByRole('button', { name: /Nâng cao/ })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('link', { name: /Kho dữ liệu thô/ })).toHaveAttribute('aria-current', 'page');
    useUiStore.getState().setDomainOpen('tech', true);
    expect(JSON.stringify(useUiStore.persist.getOptions().partialize!(useUiStore.getState()))).not.toContain('domainOpen');
  });

  it('v0.1.42: node ẩn (Hồ sơ sống, Plugin) không hiện', async () => {
    const user = userEvent.setup();
    renderSidebar(NAV, '/directory');
    expect(screen.getByRole('link', { name: /Khách & Nhóm/ })).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByText('Hồ sơ sống')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Nâng cao/ }));
    expect(screen.queryByText('Plugin & Tiện ích')).not.toBeInTheDocument();
  });

  it('v0.1.42: chưa có nhân viên → không có Đánh giá con người / Chất lượng chăm sóc', () => {
    renderSidebar(buildNavigation(new Set(), true, { hasStaff: false }), '/team');
    expect(screen.getByRole('link', { name: /Đội ngũ/ })).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByText('Đánh giá con người')).not.toBeInTheDocument();
    expect(screen.queryByText('Chất lượng chăm sóc')).not.toBeInTheDocument();
  });

  it('có nhân viên: đang ở Đội ngũ thì thấy Đánh giá con người', () => {
    renderSidebar(NAV, '/team');
    expect(screen.getByRole('link', { name: /Đánh giá con người/ })).toBeInTheDocument();
  });

  it('v0.1.42: không còn mục "Hướng dẫn thiết lập" / "Mạng xã hội" trên thanh bên', async () => {
    const user = userEvent.setup();
    renderSidebar(NAV, '/overview');
    const nav = screen.getByRole('navigation', { name: 'Danh mục màn hình' });
    expect(within(nav).queryByText('Hướng dẫn thiết lập')).not.toBeInTheDocument();
    expect(within(nav).queryByText('Mạng xã hội')).not.toBeInTheDocument();
    // Menu tài khoản của Owner vẫn có Hướng dẫn thiết lập.
    await user.click(screen.getByRole('button', { name: /Anh Nguyễn Văn A/ }));
    expect(screen.getByRole('menuitem', { name: /Hướng dẫn thiết lập/ })).toBeInTheDocument();
  });

  it('role filtering: hides what the API leaves out', () => {
    renderSidebar(buildNavigation(new Set(['people', 'care', 'system', 'team'])), '/overview');
    expect(screen.queryByText('Đội ngũ')).not.toBeInTheDocument();
    expect(screen.queryByText('Cài đặt')).not.toBeInTheDocument();
    expect(screen.getByText('Kết nối')).toBeInTheDocument();
  });

  it('auto-expands the group of the active child and marks it', () => {
    renderSidebar(NAV, '/workbench');
    const group = screen.getByRole('button', { name: /Hộp thư & Việc/ });
    expect(group).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('link', { name: /Bàn làm việc/ })).toHaveAttribute('aria-current', 'page');
  });

  it('a pure group only toggles; it does not navigate', async () => {
    const user = userEvent.setup();
    renderSidebar(NAV, '/overview');
    const group = screen.getByRole('button', { name: /Khách & Cơ hội/ });
    await user.click(group);
    expect(group).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Bảng cơ hội')).toBeInTheDocument();
    expect(screen.getByTestId('where')).toHaveTextContent('/overview');
    await user.click(group);
    expect(group).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Bảng cơ hội')).not.toBeInTheDocument();
  });

  it('an item that is also a screen navigates', async () => {
    const user = userEvent.setup();
    renderSidebar(NAV, '/overview');
    await user.click(screen.getByRole('link', { name: /Kết nối/ }));
    expect(screen.getByTestId('where')).toHaveTextContent('/connections');
  });

  it('rail mode hides labels and domain names; Nâng cao is one icon; a group opens its first child', async () => {
    useUiStore.setState({ sidebarMode: 'rail' });
    const user = userEvent.setup();
    renderSidebar(NAV, '/overview');
    expect(screen.queryByText('Việc hằng ngày')).not.toBeInTheDocument();
    expect(screen.queryByText('GEN‑HARNESS')).not.toBeInTheDocument();
    const adv = screen.getByRole('button', { name: 'Nâng cao' });
    expect(screen.queryByRole('button', { name: 'Tầng dữ liệu' })).not.toBeInTheDocument();
    await user.click(adv);
    await user.click(screen.getByRole('button', { name: 'Tầng dữ liệu' }));
    expect(screen.getByTestId('where')).toHaveTextContent('/raw');
  });

  it('v0.1.42: /social tô sáng "Kết nối", /help tô sáng "Cài đặt" (navKey của route)', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    qc.setQueryData(qk.navigation, NAV);
    function Shell() {
      return <Sidebar activeKey={useActiveNavKey()} />;
    }
    const router = createMemoryRouter(
      [
        { path: '/social', handle: { navKey: 'connections' }, element: <Shell /> },
        { path: '/help', handle: { navKey: 'system' }, element: <Shell /> },
      ],
      { initialEntries: ['/social'] },
    );
    render(
      <QueryClientProvider client={qc}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    expect(screen.getByRole('link', { name: /Kết nối/ })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: /Cài đặt/ })).not.toHaveAttribute('aria-current');
    await router.navigate('/help');
    expect(await screen.findByRole('link', { name: /Cài đặt/, current: 'page' })).toBeInTheDocument();
  });

  it('shows an error state with retry when navigation fails', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryDefaults(qk.navigation, { queryFn: () => Promise.reject(new Error('boom')) });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <Sidebar activeKey="overview" />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    return screen.findByText('Không tải được danh mục').then((el) => {
      expect(el).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Thử lại/ })).toBeInTheDocument();
    });
  });
});

describe('Hướng dẫn thiết lập — menu tài khoản (v0.1.42: không còn trên thanh bên)', () => {
  beforeEach(() => {
    useUiStore.setState({ sidebarMode: 'full', navOpen: {}, domainOpen: {} });
  });

  it('Owner: menu tài khoản có "Hướng dẫn thiết lập" cạnh Trợ giúp', async () => {
    const user = userEvent.setup();
    renderSidebar(NAV, '/overview');
    await user.click(screen.getByRole('button', { name: /Anh Nguyễn Văn A/ }));
    const items = screen.getAllByRole('menuitem').map((m) => m.textContent);
    const help = items.findIndex((t) => t?.includes('Trợ giúp'));
    expect(items[help + 1]).toContain('Hướng dẫn thiết lập');
    await user.click(screen.getByRole('menuitem', { name: /Hướng dẫn thiết lập/ }));
    expect(screen.getByTestId('where')).toHaveTextContent('/guide');
  });

  it('không phải Owner: không có mục hướng dẫn ở thanh bên lẫn menu', async () => {
    const user = userEvent.setup();
    renderSidebar(NAV, '/overview', { code: 'manager', name: 'Quản lý' });
    expect(screen.queryByRole('link', { name: /Hướng dẫn thiết lập/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Anh Nguyễn Văn A/ }));
    expect(screen.queryByRole('menuitem', { name: /Hướng dẫn thiết lập/ })).not.toBeInTheDocument();
  });
});
