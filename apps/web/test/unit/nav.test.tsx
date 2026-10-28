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
const daily = NAV[0];
const admin = NAV[1];
const panorama = daily.groups.find((g) => g.key === 'panorama')!;

// Nhóm thuần (không phải màn) — cây v0.1.78 không còn nhóm thuần, nhưng navModel vẫn hỗ trợ: giữ phủ bằng một cây dựng tay.
const PURE: NavDomain = {
  domain: 'x', label: 'X', crumb: 'X', icon: 'ph ph-gear', tone: 'ok', count: 2,
  groups: [{ key: null, name: 'Nhóm', icon: 'ph ph-folder', children: [{ key: 'a', name: 'A', icon: 'ph ph-gear' }, { key: 'b', name: 'B', icon: 'ph ph-gear' }] }],
};
const pure = PURE.groups[0];

describe('navModel', () => {
  it('finds the active screen, its domain and parent group (màn cũ là con ẩn của trang mới)', () => {
    const hit = findActive(NAV, 'workbench')!;
    expect(hit.domain.crumb).toBe('HẰNG NGÀY');
    expect(hit.group.name).toBe('Cần quyết');
    expect(hit.isChild).toBe(true);
    expect(hit.item.hidden).toBe(true);
    expect(findActive(NAV, 'backup')!.isChild).toBe(false);
    expect(findActive(NAV, 'backup')!.domain.crumb).toBe('QUẢN TRỊ');
    expect(findActive(NAV, 'nope')).toBeNull();
  });

  it('lists every screen key in the tree, hidden ones included (6 + 7 mục thanh bên + 25 màn cũ)', () => {
    expect(screenKeys(NAV).size).toBe(38);
    expect(screenKeys(NAV).has('profile')).toBe(true);
    expect(screenKeys(NAV).has('plugins')).toBe(true);
  });

  it('count = số mục không ẩn; Quản trị thu gọn mặc định', () => {
    expect(NAV.map((d) => d.count)).toEqual([6, 7]);
    expect(buildNavigation(new Set(), true, { hasStaff: false }).map((d) => d.count)).toEqual([5, 7]);
    expect(daily.collapsed).toBe(false);
    expect(admin.collapsed).toBe(true);
    expect(visibleGroups(admin).map((g) => g.key)).toEqual(['start', 'connections', 'access', 'backup', 'org', 'log', 'advanced']);
  });

  it('firstScreenKey bỏ qua màn ẩn: Tình báo; thiếu Tình báo thì tới mục kế', () => {
    expect(firstScreenKey(NAV)).toBe('intel');
    expect(firstScreenKey(buildNavigation(new Set(['intel'])))).toBe('decide');
    expect(firstScreenKey([])).toBeNull();
    expect(firstScreenKey(undefined)).toBeNull();
  });

  it('domain thu gọn mở khi màn đang mở thuộc domain, hoặc khi người dùng bấm', () => {
    expect(domainOpen(admin, 'intel', undefined)).toBe(false);
    expect(domainOpen(admin, 'backup', undefined)).toBe(true);
    expect(domainOpen(admin, 'advanced', undefined)).toBe(true);
    expect(domainOpen(admin, 'raw', undefined)).toBe(true); // màn cũ ẩn thuộc Nâng cao
    expect(domainOpen(admin, 'intel', true)).toBe(true);
    expect(domainOpen(daily, 'raw', false)).toBe(true);
  });

  it('màn cũ đang mở tô sáng mục cha (không mở con vì con ẩn)', () => {
    expect(groupView(panorama, 'graph', {}, true)).toMatchObject({ on: true, activeKid: true, open: false, showCaret: false });
    expect(groupView(panorama, 'panorama', {}, true)).toMatchObject({ on: true, self: true, open: false });
    expect(groupView(panorama, 'intel', {}, true)).toMatchObject({ on: false });
  });

  it('mục là một trang thì bấm là mở trang đó', () => {
    expect(groupAction(panorama, 'intel', {}, true)).toEqual({ type: 'navigate', key: 'panorama' });
    expect(groupAction(panorama, 'intel', {}, false)).toEqual({ type: 'navigate', key: 'panorama' });
  });

  it('nhóm thuần: tự mở theo màn đang mở, tôn trọng lựa chọn của người dùng, thanh hẹp không bao giờ hiện con', () => {
    expect(groupView(pure, 'a', {}, true)).toMatchObject({ open: true, on: true, activeKid: true, self: false });
    expect(groupView(pure, 'z', {}, true)).toMatchObject({ open: false, on: false });
    expect(groupView(pure, 'a', { Nhóm: false }, true).open).toBe(false);
    expect(groupView(pure, 'a', { Nhóm: true }, false)).toMatchObject({ open: false, showCaret: false });
    expect(groupAction(pure, 'z', {}, true)).toEqual({ type: 'toggle', group: 'Nhóm', open: true });
    expect(groupAction(pure, 'a', {}, true)).toEqual({ type: 'toggle', group: 'Nhóm', open: false });
    expect(groupAction(pure, 'z', {}, false)).toEqual({ type: 'navigate', key: 'a' });
  });

  it('Đội ngũ chưa có nhân viên: ẩn; có nhân viên: hiện, không có caret (tab chứ không phải mục con)', () => {
    const noStaff = buildNavigation(new Set(), true, { hasStaff: false });
    expect(visibleGroups(noStaff[0]).map((g) => g.key)).not.toContain('team');
    const team = visibleGroups(daily).find((g) => g.key === 'team')!;
    expect(team.children!.every((c) => c.hidden)).toBe(true);
    expect(groupView(team, 'team', {}, true)).toMatchObject({ self: true, open: false, showCaret: false });
  });
});

function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname}</div>;
}

function renderSidebar(
  nav: NavDomain[],
  path: string,
  activeKey: string | null = path.slice(1).split('/').pop() ?? null,
  role: { code: string; name: string } = { code: 'owner', name: 'Owner — Sếp' },
) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  qc.setQueryData(qk.navigation, nav);
  qc.setQueryData(qk.me, {
    id: 'u', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A (Chủ)',
    role,
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
    addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: {},
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="*"
            element={
              <>
                <Sidebar activeKey={activeKey} />
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

  it('Sếp: 6 mục + Quản trị (thu gọn); mục đang mở có aria-current, có số đếm', () => {
    renderSidebar(NAV, '/intel');
    const nav = screen.getByRole('navigation', { name: 'Danh mục màn hình' });
    expect(within(nav).getByText('Việc hằng ngày')).toBeInTheDocument();
    expect(within(nav).getByText('6 màn')).toBeInTheDocument();
    const level1 = Array.from(nav.querySelectorAll('[data-level1]'));
    expect(level1.map((el) => el.querySelector('.sb-item__name')?.textContent)).toEqual([
      'Tình báo', 'Cần quyết', 'Việc', 'Toàn cảnh', 'Tôi', 'Đội ngũ', 'Quản trị',
    ]);
    const intel = within(nav).getByRole('link', { name: /Tình báo/ });
    expect(intel).toHaveAttribute('aria-current', 'page');
    expect(intel).toHaveAttribute('href', '/intel');
    expect(within(intel).getByText('9')).toBeInTheDocument();
    expect(within(nav).getByRole('link', { name: /Toàn cảnh/ })).toHaveAttribute('href', '/panorama');
    // Quản trị: đóng, không hiện trang con.
    const adm = within(nav).getByRole('button', { name: /Quản trị/ });
    expect(adm).toHaveAttribute('aria-expanded', 'false');
    expect(within(nav).queryByText('Bắt đầu')).not.toBeInTheDocument();
    // Màn cũ không bao giờ là mục thanh bên.
    for (const old of ['Hộp thư', 'Bàn làm việc', 'Khách & Nhóm', 'Kho dữ liệu thô', 'Hồ sơ sống']) {
      expect(within(nav).queryByText(old)).not.toBeInTheDocument();
    }
    expect(screen.getByText('VA')).toBeInTheDocument();
    expect(screen.getByText('Owner · thấy toàn cảnh')).toBeInTheDocument();
  });

  it('bấm Quản trị: 7 trang con đúng thứ tự, mỗi trang ở /admin/…', async () => {
    const user = userEvent.setup();
    renderSidebar(NAV, '/intel');
    const adm = screen.getByRole('button', { name: /Quản trị/ });
    await user.click(adm);
    expect(adm).toHaveAttribute('aria-expanded', 'true');
    const nav = screen.getByRole('navigation', { name: 'Danh mục màn hình' });
    const items = within(nav).getAllByRole('link').filter((a) => a.getAttribute('href')?.startsWith('/admin/'));
    expect(items.map((a) => a.textContent?.trim())).toEqual([
      'Bắt đầu', 'Kết nối', 'Người & quyền', 'Sao lưu & cập nhật', 'Tổ chức', 'Nhật ký', 'Nâng cao',
    ]);
    expect(items.map((a) => a.getAttribute('href'))).toEqual([
      '/admin/start', '/admin/connections', '/admin/access', '/admin/backup', '/admin/org', '/admin/log', '/admin/advanced',
    ]);
    await user.click(adm);
    expect(screen.queryByText('Bắt đầu')).not.toBeInTheDocument();
  });

  it('đang ở trang Quản trị thì Quản trị tự mở và tô sáng đúng trang; màn cũ Nâng cao (raw) cũng mở Quản trị', () => {
    renderSidebar(NAV, '/admin/backup', 'backup');
    expect(screen.getByRole('button', { name: /Quản trị/ })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('link', { name: /Sao lưu & cập nhật/ })).toHaveAttribute('aria-current', 'page');
    expect(JSON.stringify(useUiStore.persist.getOptions().partialize!(useUiStore.getState()))).not.toContain('domainOpen');
  });

  it('màn cũ đang mở (workbench) tô sáng mục cha Cần quyết', () => {
    renderSidebar(NAV, '/decide', 'workbench');
    const nav = screen.getByRole('navigation', { name: 'Danh mục màn hình' });
    expect(within(nav).getByRole('link', { name: /Cần quyết/ })).not.toHaveAttribute('aria-current');
    expect(nav.querySelector('[data-screen="decide"] [data-on]')).not.toBeNull();
  });

  it('chưa có nhân viên → không có Đội ngũ; có nhân viên → có, không có mục con Đánh giá/Chăm sóc', () => {
    renderSidebar(buildNavigation(new Set(), true, { hasStaff: false }), '/intel');
    expect(screen.queryByRole('link', { name: /Đội ngũ/ })).not.toBeInTheDocument();
  });

  it('có nhân viên: Đội ngũ là một mục /team (Đánh giá, Chăm sóc là tab, không phải con thanh bên)', () => {
    renderSidebar(NAV, '/team', 'team');
    const team = screen.getByRole('link', { name: /Đội ngũ/ });
    expect(team).toHaveAttribute('aria-current', 'page');
    expect(team).toHaveAttribute('href', '/team');
    expect(screen.queryByText('Đánh giá con người')).not.toBeInTheDocument();
  });

  it('không còn mục "Hướng dẫn thiết lập" / "Mạng xã hội" trên thanh bên', async () => {
    const user = userEvent.setup();
    renderSidebar(NAV, '/intel');
    const nav = screen.getByRole('navigation', { name: 'Danh mục màn hình' });
    expect(within(nav).queryByText('Hướng dẫn thiết lập')).not.toBeInTheDocument();
    expect(within(nav).queryByText('Mạng xã hội')).not.toBeInTheDocument();
    // Menu tài khoản của Owner vẫn có Hướng dẫn thiết lập.
    await user.click(screen.getByRole('button', { name: /Anh Nguyễn Văn A/ }));
    expect(screen.getByRole('menuitem', { name: /Hướng dẫn thiết lập/ })).toBeInTheDocument();
  });

  it('role filtering: hides what the API leaves out', () => {
    renderSidebar(buildNavigation(new Set(['team', 'me', 'decide'])), '/intel');
    expect(screen.queryByText('Đội ngũ')).not.toBeInTheDocument();
    expect(screen.queryByText('Tôi')).not.toBeInTheDocument();
    expect(screen.queryByText('Cần quyết')).not.toBeInTheDocument();
    expect(screen.getByText('Toàn cảnh')).toBeInTheDocument();
  });

  it('an item that is also a page navigates', async () => {
    const user = userEvent.setup();
    renderSidebar(NAV, '/intel');
    await user.click(screen.getByRole('link', { name: /Toàn cảnh/ }));
    expect(screen.getByTestId('where')).toHaveTextContent('/panorama');
  });

  it('thanh hẹp: ẩn nhãn và tên miền; Quản trị là một icon, mở ra thì từng trang là icon', async () => {
    useUiStore.setState({ sidebarMode: 'rail' });
    const user = userEvent.setup();
    renderSidebar(NAV, '/intel');
    expect(screen.queryByText('Việc hằng ngày')).not.toBeInTheDocument();
    expect(document.querySelector('.sb-logo__name')).toBeNull();
    const adm = screen.getByRole('button', { name: 'Quản trị' });
    expect(screen.queryByRole('link', { name: 'Sao lưu & cập nhật' })).not.toBeInTheDocument();
    await user.click(adm);
    await user.click(screen.getByRole('link', { name: 'Sao lưu & cập nhật' }));
    expect(screen.getByTestId('where')).toHaveTextContent('/admin/backup');
  });

  it('/social tô sáng "Kết nối" (navKey của route); /help không tô sáng mục nào', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    qc.setQueryData(qk.navigation, NAV);
    function Shell() {
      return <Sidebar activeKey={useActiveNavKey()} />;
    }
    const router = createMemoryRouter(
      [
        { path: '/social', handle: { navKey: 'connections' }, element: <Shell /> },
        { path: '/help', element: <Shell /> },
      ],
      { initialEntries: ['/social'] },
    );
    render(
      <QueryClientProvider client={qc}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    expect(screen.getByRole('button', { name: /Quản trị/ })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('link', { name: /Kết nối/ })).toHaveAttribute('aria-current', 'page');
    await router.navigate('/help');
    expect(await screen.findByRole('button', { name: /Quản trị/ })).toHaveAttribute('aria-expanded', 'false');
    expect(document.querySelector('[aria-current="page"]')).toBeNull();
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
    renderSidebar(NAV, '/intel');
    await user.click(screen.getByRole('button', { name: /Anh Nguyễn Văn A/ }));
    const items = screen.getAllByRole('menuitem').map((m) => m.textContent);
    const help = items.findIndex((t) => t?.includes('Trợ giúp'));
    expect(items[help + 1]).toContain('Hướng dẫn thiết lập');
    await user.click(screen.getByRole('menuitem', { name: /Hướng dẫn thiết lập/ }));
    expect(screen.getByTestId('where')).toHaveTextContent('/guide');
  });

  it('không phải Owner: không có mục hướng dẫn ở thanh bên lẫn menu', async () => {
    const user = userEvent.setup();
    renderSidebar(NAV, '/intel', 'intel', { code: 'manager', name: 'Quản lý' });
    expect(screen.queryByRole('link', { name: /Hướng dẫn thiết lập/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Anh Nguyễn Văn A/ }));
    expect(screen.queryByRole('menuitem', { name: /Hướng dẫn thiết lập/ })).not.toBeInTheDocument();
  });
});
