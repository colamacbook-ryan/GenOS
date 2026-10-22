/**
 * v0.1.42 (F-26): trang chủ "/" chuyển tới màn đầu tiên KHÔNG ẩn trong danh mục của vai trò (GET /navigation) —
 * không còn cứng "/overview" (Agent NV từng gặp ổ khoá ngay khi đăng nhập). Giữ ?gen=; danh mục rỗng/lỗi có trạng thái
 * riêng. Đổi mật khẩu bắt buộc xong và thiết lập đã xong cũng về "/".
 *
 * v0.1.55 (G5): vai Owner ⇒ "/" chuyển tới "/owner" (Mặt tiền), vẫn giữ ?gen=; nhân viên (mọi vai khác) như cũ.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider, createMemoryRouter, useLocation, type RouteObject } from 'react-router-dom';
import type { NavDomain } from '@gen-harness/contracts';
import { ForcePasswordPage } from '../../src/account/ForcePasswordPage';
import { qk } from '../../src/lib/queries';
import { queryClient } from '../../src/lib/queryClient';
import { safeNext } from '../../src/lib/safeNext';
import { SetupPage } from '../../src/setup/SetupPage';
import { HomeRedirect } from '../../src/shell/HomeRedirect';
import { buildNavigation, hiddenScreens } from '../mock-api';

const ME = {
  id: 'u1', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A (Chủ)', role: { code: 'owner', name: 'Owner — Sếp' },
  org: { id: 'o1', name: 'Genesis', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: {}, must_change_password: false,
};

function Where() {
  const l = useLocation();
  return <div data-testid="where">{`${l.pathname}${l.search}`}</div>;
}

const TARGETS: RouteObject[] = ['inbox', 'overview', 'account', 'owner'].map((k) => ({ path: `/${k}`, element: <Where /> }));

function renderAt(path: string, extra: RouteObject[] = [], qc: QueryClient = queryClient) {
  const router = createMemoryRouter([{ path: '/', element: <HomeRedirect /> }, ...TARGETS, ...extra], { initialEntries: [path] });
  render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

/** `role`: vai trả về ở GET /auth/me (null = chưa đăng nhập được ⇒ 404, HomeRedirect coi như không phải Owner). */
function stubNav(nav: NavDomain[] | 'error', extra: (url: string) => unknown = () => undefined, role: string | null = null) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const other = extra(url);
      if (other !== undefined) return new Response(JSON.stringify(other), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.endsWith('/auth/me') && role) {
        return new Response(JSON.stringify({ ...ME, role: { code: role, name: role } }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.endsWith('/navigation')) {
        if (nav === 'error') {
          return new Response(JSON.stringify({ status: 500, code: 'INTERNAL', title: 'Lỗi máy chủ', error_id: 'e-42' }), {
            status: 500,
            headers: { 'Content-Type': 'application/problem+json' },
          });
        }
        return new Response(JSON.stringify(nav), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(JSON.stringify({ status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' }), {
        status: 404,
        headers: { 'Content-Type': 'application/problem+json' },
      });
    }),
  );
}

beforeEach(() => {
  queryClient.clear();
  vi.stubGlobal('WebSocket', undefined);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe('HomeRedirect', () => {
  it('Agent NV (không có overview.read): "/" → /inbox, không gặp ổ khoá', async () => {
    const nav = buildNavigation(hiddenScreens('agent_staff'));
    expect(nav[0].groups.some((g) => g.key === 'overview')).toBe(false);
    stubNav(nav);
    const router = renderAt('/');
    expect(await screen.findByTestId('where')).toHaveTextContent('/inbox');
    expect(router.state.location.pathname).toBe('/inbox');
    expect(screen.queryByText(/không có quyền|không xem được/i)).not.toBeInTheDocument();
  });

  it('Owner: "/" → /owner (Mặt tiền), không còn /overview', async () => {
    stubNav(buildNavigation(), undefined, 'owner');
    const router = renderAt('/');
    expect(await screen.findByTestId('where')).toHaveTextContent('/owner');
    expect(router.state.location.pathname).toBe('/owner');
  });

  it('Owner: giữ ?gen= (Bản tin Gen từ chuông) và ?gen=coach (thẻ Hôm nay của Sếp)', async () => {
    stubNav(buildNavigation(), undefined, 'owner');
    renderAt('/?gen=0192aaaa-bbbb-4ccc-8ddd-eeeeffff0000');
    expect(await screen.findByTestId('where')).toHaveTextContent('/owner?gen=0192aaaa-bbbb-4ccc-8ddd-eeeeffff0000');
    cleanup();
    renderAt('/?gen=coach');
    expect(await screen.findByTestId('where')).toHaveTextContent('/owner?gen=coach');
  });

  it('Owner vẫn về /owner dù danh mục lỗi (không cần /navigation)', async () => {
    stubNav('error', undefined, 'owner');
    const router = renderAt('/', [], new QueryClient({ defaultOptions: { queries: { retry: false } } }));
    expect(await screen.findByTestId('where')).toHaveTextContent('/owner');
    expect(router.state.location.pathname).toBe('/owner');
    expect(screen.queryByText('Không tải được danh mục')).not.toBeInTheDocument();
  });

  it.each(['manager', 'operator', 'auditor'])('nhân viên (%s): "/" → /overview như cũ, không bị đưa tới /owner', async (role) => {
    stubNav(buildNavigation(), undefined, role);
    const router = renderAt('/');
    expect(await screen.findByTestId('where')).toHaveTextContent('/overview');
    expect(router.state.location.pathname).toBe('/overview');
  });

  it('nhân viên giữ ?gen= (Bản tin Gen từ chuông) như cũ', async () => {
    stubNav(buildNavigation(), undefined, 'manager');
    renderAt('/?gen=0192aaaa-bbbb-4ccc-8ddd-eeeeffff0000');
    expect(await screen.findByTestId('where')).toHaveTextContent('/overview?gen=0192aaaa-bbbb-4ccc-8ddd-eeeeffff0000');
  });

  it('chưa biết vai (đang tải /auth/me): khung chờ, không chuyển vội', async () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)));
    const router = renderAt('/');
    expect(await screen.findByLabelText('Đang mở trang chủ')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/');
  });

  it('bỏ qua màn ẩn: chỉ còn nhóm có con ẩn thì đi tiếp nhóm sau', async () => {
    const nav = buildNavigation(new Set(['overview', 'inbox', 'workbench', 'tasks']));
    stubNav(nav);
    renderAt('/', [{ path: '/directory', element: <Where /> }]);
    expect(await screen.findByTestId('where')).toHaveTextContent('/directory');
  });

  it('danh mục rỗng → EmptyState + link Tài khoản của tôi', async () => {
    stubNav([]);
    renderAt('/');
    expect(await screen.findByText('Vai trò này chưa được cấp màn hình nào')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Tài khoản của tôi' })).toHaveAttribute('href', '/account');
  });

  it('lỗi tải danh mục → ErrorState có Thử lại + Chi tiết kỹ thuật (chuỗi, không phải object)', async () => {
    stubNav('error');
    renderAt('/', [], new QueryClient({ defaultOptions: { queries: { retry: false } } }));
    expect(await screen.findByText('Không tải được danh mục')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Thử lại/ })).toBeInTheDocument();
    expect(screen.getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('[object Object]');
  });

  it('safeNext: mặc định "/" (để HomeRedirect chọn màn), không còn cứng /overview', () => {
    expect(safeNext(null)).toBe('/');
    expect(safeNext('//evil.example')).toBe('/');
    expect(safeNext('https://evil.example')).toBe('/');
    expect(safeNext('/login')).toBe('/');
    expect(safeNext('/inbox?tab=all')).toBe('/inbox?tab=all');
  });
});

describe('Về "/" sau đổi mật khẩu bắt buộc và sau thiết lập', () => {
  it('ForcePasswordPage khi đã đổi xong → "/" → Mặt tiền (Owner)', async () => {
    stubNav(buildNavigation(), (url) => (url.endsWith('/auth/me') ? ME : undefined));
    const router = renderAt('/change-password', [{ path: '/change-password', element: <ForcePasswordPage /> }]);
    expect(await screen.findByTestId('where')).toHaveTextContent('/owner');
    expect(router.state.location.pathname).toBe('/owner');
  });

  // v0.1.59 (g5-thiet-lap): thiết lập đã xong ⇒ /setup về /owner (trừ ?buoc=nguon-ai). Vai khác Owner bị OwnerShell chuyển tiếp về "/" →
  // màn đầu tiên của vai trò (e2e/owner.spec.ts "operator/manager/auditor: mở /owner bị chuyển"); ở đây chỉ kiểm bước chuyển của SetupPage.
  it('SetupPage khi đã hoàn tất → /owner (Mặt tiền); vai khác Owner được OwnerShell đưa về "/" → màn đầu tiên của vai trò', async () => {
    const nav = buildNavigation(hiddenScreens('agent_staff'));
    const finished = { finished: true, current_step: 12, steps: [], org: null };
    stubNav(nav, (url) => (url.includes('/setup/state') ? finished : undefined));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    qc.setQueryData(qk.setupState, finished);
    const router = renderAt('/setup', [{ path: '/setup', element: <SetupPage /> }], qc);
    expect(await screen.findByTestId('where')).toHaveTextContent('/owner');
    expect(router.state.location.pathname).toBe('/owner');
  });
});
