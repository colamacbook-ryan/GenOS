import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { GEN_SCREEN_BY_KEY, GEN_TARGET_BY_ID } from '@gen-harness/contracts';
import { Sidebar } from '../../src/shell/Sidebar';
import { ConnectionsScreen } from '../../src/screens/connections/ConnectionsScreen';
import { executeUiAction } from '../../src/gen/director';
import { setNavigator } from '../../src/lib/navigation';
import { qk } from '../../src/lib/queries';
import { useUiStore, useUrlStateStore } from '../../src/lib/uiStore';
import { buildNavigation } from '../mock-api';

/**
 * v0.1.39 (F-32): trang Tài khoản mạng xã hội — Gen mở được, thẻ Facebook dẫn tới.
 * v0.1.42 (F-7): không còn mục riêng ở thanh bên — Facebook vào Kết nối (thẻ Facebook), /social tô sáng "Kết nối";
 * menu tài khoản của Owner vẫn có "Tài khoản mạng xã hội".
 */

const OWNER = { code: 'owner', name: 'Owner — Sếp' };
const OPERATOR = { code: 'operator', name: 'Vận hành' };

function me(role: { code: string; name: string }, permissions: Record<string, string> = {}) {
  return {
    id: 'u', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A (Chủ)',
    role,
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
    addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions,
  };
}

function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname}</div>;
}

function renderSidebar(role: { code: string; name: string }, path = '/overview', activeKey = path.slice(1)) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  qc.setQueryData(qk.navigation, buildNavigation());
  qc.setQueryData(qk.me, me(role));
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

function renderChannels(role: { code: string; name: string }, permissions: Record<string, string>) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes('/social/accounts') ? { items: [] } : url.includes('/hub/link') ? { configured: false, enabled: false, status: 'off' } : [];
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  qc.setQueryData(qk.me, me(role, permissions));
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/connections']}>
        <Routes>
          <Route
            path="*"
            element={
              <>
                <ConnectionsScreen />
                <Where />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useUiStore.setState({ sidebarMode: 'full', navOpen: {}, domainOpen: {} });
  useUrlStateStore.setState({ params: {} });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe('v0.1.42 — Facebook vào Kết nối (không còn mục "Mạng xã hội" ở thanh bên)', () => {
  it('Owner (rộng): không có mục "Mạng xã hội"; menu tài khoản vẫn mở /social', async () => {
    const user = userEvent.setup();
    renderSidebar(OWNER);
    const nav = screen.getByRole('navigation', { name: 'Danh mục màn hình' });
    expect(within(nav).queryByRole('link', { name: /Mạng xã hội/ })).not.toBeInTheDocument();
    expect(document.querySelector('[data-screen="social"]')).toBeNull();
    expect(within(nav).getByRole('link', { name: /Kết nối/ })).toHaveAttribute('href', '/connections');
    await user.click(screen.getByRole('button', { name: /Anh Nguyễn Văn A/ }));
    await user.click(screen.getByRole('menuitem', { name: /Tài khoản mạng xã hội/ }));
    expect(screen.getByTestId('where')).toHaveTextContent('/social');
  });

  it('Owner (hẹp): đang ở /social (navKey = connections) thì "Kết nối" có aria-current', () => {
    useUiStore.setState({ sidebarMode: 'rail' });
    renderSidebar(OWNER, '/social', 'connections');
    const conn = screen.getByRole('link', { name: 'Kết nối' });
    expect(conn).toHaveAttribute('href', '/connections');
    expect(conn).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByRole('link', { name: 'Mạng xã hội' })).not.toBeInTheDocument();
  });

  it('Vận hành (Operator): không có mục "Mạng xã hội"', () => {
    renderSidebar(OPERATOR);
    expect(screen.queryByRole('link', { name: /Mạng xã hội/ })).not.toBeInTheDocument();
    expect(document.querySelector('[data-screen="social"]')).toBeNull();
  });
});

describe('v0.1.39 — Gen điều hướng tới /social', () => {
  it('registry có màn social và target thẻ Facebook', () => {
    expect(GEN_SCREEN_BY_KEY.social).toEqual({ key: 'social', path: '/social', title: 'Tài khoản mạng xã hội' });
    expect(GEN_SCREEN_BY_KEY.guide.title).toBe('Hướng dẫn thiết lập');
    // v0.1.42 (F-7): thẻ Facebook ở Kết nối — id giữ nguyên, không còn tham số tab.
    expect(GEN_TARGET_BY_ID['system.channels.facebook']).toMatchObject({ screen: 'connections', permission: 'roles.manage' }); // chỉ Owner thấy thẻ → target chỉ Owner (Admin có system.manage không được chỉ tới)
    expect(GEN_TARGET_BY_ID['system.channels.facebook'].params).toBeUndefined();
  });

  it('hành động {type:navigate, screen:social} → navigateTo("/social")', async () => {
    const nav = vi.fn();
    setNavigator(nav);
    await executeUiAction({ type: 'navigate', screen: 'social' });
    expect(nav).toHaveBeenCalledWith('/social', undefined);
  });
});

describe('v0.1.42 — thẻ Facebook ở Kết nối', () => {
  it('Owner: có thẻ Facebook, nút "Mở Facebook" tới /social', async () => {
    renderChannels(OWNER, { 'system.read': 'all', 'system.manage': 'all' });
    const card = await screen.findByRole('region', { name: 'Facebook' });
    expect(card).toHaveAttribute('data-gen-target', 'system.channels.facebook');
    expect(await within(card).findByText('Đọc thông báo và tin nhắn — đăng nhập ngay trong app')).toBeInTheDocument();
    expect(card.querySelector('.conn-pill')).toHaveAttribute('data-status', 'not_connected');
    const link = within(card).getByRole('link', { name: /Mở Facebook/ });
    expect(link).toHaveAttribute('href', '/social');
    await userEvent.setup().click(link);
    expect(screen.getByTestId('where')).toHaveTextContent('/social');
  });

  it('Vận hành: không có thẻ Facebook', () => {
    renderChannels(OPERATOR, { 'system.read': 'all' });
    expect(screen.queryByRole('region', { name: 'Facebook' })).not.toBeInTheDocument();
    expect(document.querySelector('[data-gen-target="system.channels.facebook"]')).toBeNull();
  });
});
