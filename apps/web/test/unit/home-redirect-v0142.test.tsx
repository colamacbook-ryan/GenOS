/**
 * v0.1.42 (F-26): trang chủ "/" chuyển tới màn đầu tiên KHÔNG ẩn trong danh mục của vai trò (GET /navigation) —
 * không còn cứng "/overview" (Agent NV từng gặp ổ khoá ngay khi đăng nhập). Giữ ?gen=; danh mục rỗng/lỗi có trạng thái
 * riêng. Đổi mật khẩu bắt buộc xong và thiết lập đã xong cũng về "/".
 *
 * v0.1.78 (G0): MỘT shell — mọi vai (Sếp, Quản lý, Vận hành, Nhân viên) vào mục đầu tiên không ẩn: Tình báo `/intel`,
 * vẫn giữ ?gen=. Không còn nhánh riêng cho Owner/Mặt tiền.
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

const TARGETS: RouteObject[] = ['intel', 'decide', 'work', 'panorama', 'account', 'admin/start'].map((k) => ({ path: `/${k}`, element: <Where /> }));

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
  it('Agent NV (không có overview.read): "/" → /intel (Tình báo hiện nhờ queue.read), không gặp ổ khoá', async () => {
    const nav = buildNavigation(hiddenScreens('agent_staff'));
    expect(nav[0].groups.some((g) => g.key === 'intel')).toBe(true);
    expect(nav[0].groups.find((g) => g.key === 'intel')!.children!.some((c) => c.key === 'overview')).toBe(false);
    stubNav(nav);
    const router = renderAt('/');
    expect(await screen.findByTestId('where')).toHaveTextContent('/intel');
    expect(router.state.location.pathname).toBe('/intel');
    expect(screen.queryByText(/không có quyền|không xem được/i)).not.toBeInTheDocument();
  });

  it('Owner: "/" → /intel (Tình báo › Hôm nay), không còn /owner hay /overview', async () => {
    stubNav(buildNavigation(), undefined, 'owner');
    const router = renderAt('/');
    expect(await screen.findByTestId('where')).toHaveTextContent('/intel');
    expect(router.state.location.pathname).toBe('/intel');
  });

  it('Owner: giữ ?gen= (Bản tin Gen từ chuông) và ?gen=coach (thẻ Hôm nay của Sếp)', async () => {
    stubNav(buildNavigation(), undefined, 'owner');
    renderAt('/?gen=0192aaaa-bbbb-4ccc-8ddd-eeeeffff0000');
    expect(await screen.findByTestId('where')).toHaveTextContent('/intel?gen=0192aaaa-bbbb-4ccc-8ddd-eeeeffff0000');
    cleanup();
    renderAt('/?gen=coach');
    expect(await screen.findByTestId('where')).toHaveTextContent('/intel?gen=coach');
  });

  it('danh mục lỗi ⇒ ErrorState (một shell: trang chủ cần /navigation cho mọi vai, kể cả Owner)', async () => {
    stubNav('error', undefined, 'owner');
    renderAt('/', [], new QueryClient({ defaultOptions: { queries: { retry: false } } }));
    expect(await screen.findByText('Không tải được danh mục')).toBeInTheDocument();
  });

  it.each(['manager', 'operator', 'auditor'])('vai %s: "/" → /intel', async (role) => {
    stubNav(buildNavigation(), undefined, role);
    const router = renderAt('/');
    expect(await screen.findByTestId('where')).toHaveTextContent('/intel');
    expect(router.state.location.pathname).toBe('/intel');
  });

  it('vai khác Owner cũng giữ ?gen= (Bản tin Gen từ chuông)', async () => {
    stubNav(buildNavigation(), undefined, 'manager');
    renderAt('/?gen=0192aaaa-bbbb-4ccc-8ddd-eeeeffff0000');
    expect(await screen.findByTestId('where')).toHaveTextContent('/intel?gen=0192aaaa-bbbb-4ccc-8ddd-eeeeffff0000');
  });

  it('chưa biết vai (đang tải /auth/me): khung chờ, không chuyển vội', async () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)));
    const router = renderAt('/');
    expect(await screen.findByLabelText('Đang mở trang chủ')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/');
  });

  it('bỏ qua màn ẩn: chỉ còn mục có con ẩn thì đi tiếp mục sau', async () => {
    const nav = buildNavigation(new Set(['intel', 'decide', 'work']));
    stubNav(nav);
    renderAt('/');
    expect(await screen.findByTestId('where')).toHaveTextContent('/panorama');
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
  it('ForcePasswordPage khi đã đổi xong → "/" → Tình báo', async () => {
    stubNav(buildNavigation(), (url) => (url.endsWith('/auth/me') ? ME : undefined));
    const router = renderAt('/change-password', [{ path: '/change-password', element: <ForcePasswordPage /> }]);
    expect(await screen.findByTestId('where')).toHaveTextContent('/intel');
    expect(router.state.location.pathname).toBe('/intel');
  });

  // v0.1.59 (g5-thiet-lap): thiết lập đã xong ⇒ /setup về /owner (trừ ?buoc=nguon-ai). Vai khác Owner bị OwnerShell chuyển tiếp về "/" →
  // màn đầu tiên của vai trò (e2e/owner.spec.ts "operator/manager/auditor: mở /owner bị chuyển"); ở đây chỉ kiểm bước chuyển của SetupPage.
  it('SetupPage khi đã hoàn tất → /intel (nay là đường cũ, router chuyển tiếp sang /intel — legacy-redirects-v0178.test.ts)', async () => {
    const nav = buildNavigation(hiddenScreens('agent_staff'));
    const finished = { finished: true, current_step: 12, steps: [], org: null };
    stubNav(nav, (url) => (url.includes('/setup/state') ? finished : undefined));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    qc.setQueryData(qk.setupState, finished);
    const router = renderAt('/setup', [{ path: '/setup', element: <SetupPage /> }, { path: '/intel', element: <Where /> }], qc);
    expect(await screen.findByTestId('where')).toHaveTextContent('/intel');
    expect(router.state.location.pathname).toBe('/intel');
  });
});
