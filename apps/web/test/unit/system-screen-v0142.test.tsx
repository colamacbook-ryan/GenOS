/**
 * v0.1.42 (F-7): Cài đặt (/system) — 5 tab theo thứ tự, lọc theo quyền; tab cũ Kênh/Người dùng chuyển trang;
 * Bộ não AI không gọi /providers khi vai trò không có system.read.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, RouterProvider, createMemoryRouter, useLocation } from 'react-router-dom';
import { BrainTab } from '../../src/screens/system/BrainTab';
import { SystemScreen } from '../../src/screens/system/SystemScreen';
import { qk } from '../../src/lib/queries';
import { UrlStateSync, useUrlStateStore } from '../../src/lib/uiStore';
import { permissionsOf, type RoleCode } from '../mock-api';

const calls: string[] = [];

function me(role: RoleCode) {
  return {
    id: 'u', email: `${role}@genesis.local`, display_name: 'Người dùng', role: { code: role, name: role },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
    addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: permissionsOf(role),
  };
}

function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname}</div>;
}

function renderSystem(role: RoleCode, path = '/system') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  qc.setQueryData(qk.me, me(role));
  const router = createMemoryRouter(
    [
      { path: '/system', element: <><UrlStateSync /><SystemScreen /></> },
      { path: '/connections', element: <Where /> },
      { path: '/team', element: <Where /> },
    ],
    { initialEntries: [path] },
  );
  useUrlStateStore.getState().hydrate(new URL(`http://x${path}`).search);
  render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

beforeEach(() => {
  calls.length = 0;
  useUrlStateStore.setState({ params: {} });
  vi.stubGlobal('WebSocket', undefined);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return new Response(JSON.stringify({ status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' }), {
        status: 404,
        headers: { 'Content-Type': 'application/problem+json' },
      });
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Cài đặt — tab theo quyền', () => {
  it('Manager (chỉ audit.read): đúng 1 tab "Nhật ký", không gọi /providers', async () => {
    renderSystem('manager');
    const tabs = await screen.findAllByRole('tab');
    expect(tabs.map((t) => t.textContent)).toEqual([expect.stringContaining('Nhật ký')]);
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
    // Cho các truy vấn kịp chạy rồi mới kiểm.
    await waitFor(() => expect(calls.some((u) => u.includes('/audit'))).toBe(true));
    expect(calls.some((u) => u.includes('/providers'))).toBe(false);
  });

  it('Manager mở link cũ ?tab=brain → vẫn chỉ Nhật ký, không gọi /providers', async () => {
    renderSystem('manager', '/system?tab=brain');
    const tabs = await screen.findAllByRole('tab');
    expect(tabs).toHaveLength(1);
    expect(tabs[0]).toHaveTextContent('Nhật ký');
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(calls.some((u) => u.includes('/providers'))).toBe(false);
  });

  it('Owner: 5 tab theo thứ tự, mặc định "Sao lưu & cập nhật"', async () => {
    renderSystem('owner');
    const tabs = await screen.findAllByRole('tab');
    expect(tabs.map((t) => t.firstChild?.textContent)).toEqual(['Sao lưu & cập nhật', 'Tổ chức', 'Bộ não AI', 'Quyền hạn', 'Nhật ký']);
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
    // Liên kết đầu màn: Tài khoản & PIN, Trợ giúp, Hướng dẫn thiết lập (Owner).
    expect(screen.getByRole('link', { name: /Tài khoản & PIN của tôi/ })).toHaveAttribute('href', '/account');
    expect(screen.getByRole('link', { name: /Trợ giúp/ })).toHaveAttribute('href', '/help');
    expect(screen.getByRole('link', { name: /Hướng dẫn thiết lập/ })).toHaveAttribute('href', '/guide');
  });

  it('Auditor (system.read, không Owner): không có link Hướng dẫn thiết lập', async () => {
    renderSystem('auditor');
    expect(await screen.findAllByRole('tab')).toHaveLength(5);
    expect(screen.queryByRole('link', { name: /Hướng dẫn thiết lập/ })).not.toBeInTheDocument();
  });

  it('link cũ ?tab=channels → /connections, ?tab=users → /team', async () => {
    const r1 = renderSystem('owner', '/system?tab=channels');
    expect(await screen.findByTestId('where')).toHaveTextContent('/connections');
    expect(r1.state.location.pathname).toBe('/connections');
    r1.dispose();
    const r2 = renderSystem('owner', '/system?tab=users');
    await waitFor(() => expect(r2.state.location.pathname).toBe('/team'));
  });

  it('link cũ giữ tham số khác (?gen=…), bỏ tab', async () => {
    const r = renderSystem('owner', '/system?tab=channels&gen=c-1');
    await waitFor(() => expect(r.state.location.pathname).toBe('/connections'));
    expect(r.state.location.search).toBe('?gen=c-1');
  });

  it('link cũ khi vai trò không mở được trang đích → ở lại Cài đặt, tab đầu tiên được phép', async () => {
    // Manager chỉ có audit.read: không vào được Kết nối (system.read) hay Đội ngũ (roles.manage).
    for (const path of ['/system?tab=channels', '/system?tab=users']) {
      const r = renderSystem('manager', path);
      const tabs = await screen.findAllByRole('tab');
      expect(tabs).toHaveLength(1);
      expect(tabs[0]).toHaveTextContent('Nhật ký');
      expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
      expect(r.state.location.pathname).toBe('/system');
      r.dispose();
      cleanup();
    }
  });

  it('?tab=storage&focus=health giữ nguyên (link chuông / sức khoẻ cũ)', async () => {
    const r = renderSystem('owner', '/system?tab=storage&focus=health');
    expect(await screen.findByRole('tab', { name: /Sao lưu & cập nhật/ })).toHaveAttribute('aria-selected', 'true');
    expect(r.state.location.search).toBe('?tab=storage&focus=health');
  });
});

describe('BrainTab', () => {
  it('vai trò không có system.read → ổ khoá, 0 request /providers', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(qk.me, me('manager'));
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <BrainTab />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText('Vai trò của bạn không xem được Bộ não AI')).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 30));
    expect(calls.filter((u) => u.includes('/providers'))).toHaveLength(0);
  });

  it('Owner: không còn thẻ CLI trong Bộ não AI — chỉ liên kết sang Kết nối', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(qk.me, me('owner'));
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <BrainTab />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole('link', { name: /Tài khoản Google \/ Claude CLI ở Kết nối/ })).toHaveAttribute('href', '/connections#brain');
    await waitFor(() => expect(calls.some((u) => u.includes('/providers'))).toBe(true));
    expect(calls.some((u) => u.includes('/cli/profiles'))).toBe(false);
  });
});
