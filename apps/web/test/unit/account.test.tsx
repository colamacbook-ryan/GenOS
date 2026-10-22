import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider, createMemoryRouter } from 'react-router-dom';
import type { Account } from '@gen-harness/contracts';
import { AccountPage } from '../../src/account/AccountPage';
import { ForcePasswordPage } from '../../src/account/ForcePasswordPage';
import { describeDevice, passwordErrors, pinErrors, profileErrors } from '../../src/account/accountModel';
import { useMe } from '../../src/lib/queries';
import { queryClient } from '../../src/lib/queryClient';
import { useToasts } from '../../src/lib/toast';
import { AppShell } from '../../src/shell/AppShell';
import { HomeRedirect } from '../../src/shell/HomeRedirect';
import { buildNavigation } from '../mock-api';

const ME = {
  id: 'u1', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A (Chủ)', role: { code: 'owner', name: 'Owner — Sếp' },
  org: { id: 'o1', name: 'Genesis', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: {}, must_change_password: false,
};
const ACCOUNT: Account = {
  display_name: 'Anh Nguyễn Văn A (Chủ)', email: 'owner@genesis.local', role: { code: 'owner', name: 'Owner — Sếp' },
  created_at: '2026-05-04T02:15:00Z', must_change_password: false, has_pin: true,
  sessions: [
    { id: 's1', created_at: '2026-09-29T01:00:00Z', last_seen_at: '2026-09-29T01:00:00Z', ip: '127.0.0.1',
      user_agent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36', expires_at: '2026-10-06T01:00:00Z', current: true },
    { id: 's2', created_at: '2026-09-26T01:00:00Z', last_seen_at: '2026-09-28T23:00:00Z', ip: '113.161.42.7',
      user_agent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 Mobile Safari/604.1', expires_at: '2026-10-03T01:00:00Z', current: false },
  ],
};

type Handler = (url: string, init: RequestInit | undefined) => { status: number; body?: unknown } | undefined;
const calls: Array<{ method: string; url: string; body: unknown }> = [];

function stubApi(handler: Handler) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : null });
      const r = handler(url, init) ?? { status: 404, body: { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' } };
      if (r.status === 204) return new Response(null, { status: 204 });
      return new Response(JSON.stringify(r.body ?? {}), {
        status: r.status,
        headers: { 'Content-Type': r.status >= 400 ? 'application/problem+json' : 'application/json' },
      });
    }),
  );
}

const invalid = (errors: Record<string, string>) => ({ status: 422, body: { status: 422, code: 'VALIDATION', title: 'Dữ liệu chưa hợp lệ', errors } });

beforeEach(() => {
  queryClient.clear();
  calls.length = 0;
  useToasts.setState({ toasts: [] });
  vi.stubGlobal('WebSocket', undefined);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

/** Như khối tài khoản ở chân thanh bên: đọc tên từ /auth/me. */
function SidebarName() {
  const me = useMe();
  return <div data-testid="sidebar-name">{me.data?.display_name}</div>;
}

function renderAt(path: string, routes: Parameters<typeof createMemoryRouter>[0]) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe('accountModel', () => {
  it('kiểm mật khẩu mới: đủ 12 ký tự, khác mật khẩu cũ, nhập lại khớp', () => {
    expect(passwordErrors({ current_password: '', new_password: 'ngan', new_password_confirm: 'x' })).toEqual({
      current_password: 'Nhập mật khẩu hiện tại.',
      new_password: 'Mật khẩu mới cần ít nhất 12 ký tự (hiện 4).',
      new_password_confirm: 'Hai lần nhập mật khẩu chưa khớp.',
    });
    const same = 'mat-khau-rat-dai-1';
    expect(passwordErrors({ current_password: same, new_password: same, new_password_confirm: same }).new_password).toBe(
      'Mật khẩu mới phải khác mật khẩu hiện tại.',
    );
    expect(passwordErrors({ current_password: 'cu', new_password: same, new_password_confirm: same })).toEqual({});
  });
  it('đổi email mới cần mật khẩu; PIN đúng 6 số và khớp', () => {
    expect(profileErrors({ display_name: 'A', email: 'a@b.vn', current_password: '' }, 'a@b.vn')).toEqual({});
    expect(profileErrors({ display_name: ' ', email: 'moi@b.vn', current_password: '' }, 'a@b.vn')).toEqual({
      display_name: 'Nhập tên hiển thị.',
      current_password: 'Nhập mật khẩu hiện tại để đổi email.',
    });
    expect(pinErrors({ current_password: 'x', new_pin: '12345', new_pin_confirm: '12345' }).new_pin).toBe('PIN gồm đúng 6 chữ số.');
    expect(pinErrors({ current_password: 'x', new_pin: '123456', new_pin_confirm: '123457' }).new_pin_confirm).toBe(
      'Hai lần nhập mã PIN chưa khớp.',
    );
  });
  it('mô tả thiết bị từ user-agent', () => {
    expect(describeDevice(ACCOUNT.sessions[1].user_agent)).toBe('Safari · iPhone');
    expect(describeDevice(ACCOUNT.sessions[0].user_agent)).toBe('Chrome · Linux');
    expect(describeDevice(null)).toBe('Trình duyệt không rõ');
  });
});

describe('<AccountPage>', () => {
  it('hiện đủ 4 mục; lưu tên hiển thị → toast và tải lại /auth/me', async () => {
    const user = userEvent.setup();
    let name = ACCOUNT.display_name;
    stubApi((url, init) => {
      if (url.endsWith('/account') && init?.method === 'PATCH') {
        name = (JSON.parse(String(init.body)) as { display_name: string }).display_name;
        return { status: 200, body: { ...ACCOUNT, display_name: name } };
      }
      if (url.endsWith('/account')) return { status: 200, body: { ...ACCOUNT, display_name: name } };
      if (url.endsWith('/auth/me')) return { status: 200, body: { ...ME, display_name: name } };
      return undefined;
    });
    renderAt('/account', [{ path: '/account', element: <><SidebarName /><AccountPage /></> }]);
    expect(await screen.findByText('Hồ sơ')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('sidebar-name')).toHaveTextContent('Anh Nguyễn Văn A (Chủ)'));
    expect(screen.getByText('Đổi mật khẩu', { selector: '.gh-card__title' })).toBeInTheDocument();
    expect(screen.getByText('Đổi mã PIN', { selector: '.gh-card__title' })).toBeInTheDocument();
    expect(screen.getByText('Phiên đăng nhập')).toBeInTheDocument();
    expect(screen.getByText('Thiết bị này')).toBeInTheDocument();
    expect(screen.getByText('Safari · iPhone')).toBeInTheDocument();

    const nameField = screen.getByLabelText('Tên hiển thị');
    await user.clear(nameField);
    await user.type(nameField, 'Anh Nguyễn Văn A');
    await user.click(screen.getByRole('button', { name: /Lưu hồ sơ/ }));
    await waitFor(() => expect(useToasts.getState().toasts.map((t) => t.text)).toContain('Đã lưu hồ sơ.'));
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ display_name: 'Anh Nguyễn Văn A' });
    // Tên ở thanh bên đổi ngay (invalidate me).
    await waitFor(() => expect(screen.getByTestId('sidebar-name')).toHaveTextContent(/^Anh Nguyễn Văn A$/));
  });

  it('sai mật khẩu hiện tại → lỗi ngay dưới ô; đổi xong báo số thiết bị đã đăng xuất', async () => {
    const user = userEvent.setup();
    let attempt = 0;
    stubApi((url) => {
      if (url.endsWith('/account/password')) {
        attempt += 1;
        return attempt === 1 ? invalid({ current_password: 'Mật khẩu hiện tại không đúng' }) : { status: 200, body: { sessions_revoked: 1 } };
      }
      if (url.endsWith('/account')) return { status: 200, body: ACCOUNT };
      if (url.endsWith('/auth/me')) return { status: 200, body: ME };
      return undefined;
    });
    renderAt('/account', [{ path: '/account', element: <AccountPage /> }]);
    const form = await screen.findByRole('form', { name: 'Đổi mật khẩu' });
    const f = within(form);
    // Kiểm ở trình duyệt trước khi gửi.
    await user.click(f.getByRole('button', { name: /Đổi mật khẩu/ }));
    expect(f.getByText('Nhập mật khẩu hiện tại.')).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith('/account/password'))).toBe(false);

    await user.type(f.getByLabelText('Mật khẩu hiện tại'), 'sai-roi-nhe');
    await user.type(f.getByLabelText('Mật khẩu mới'), 'mat-khau-moi-rat-dai');
    await user.type(f.getByLabelText('Nhập lại mật khẩu mới'), 'mat-khau-moi-rat-dai');
    await user.click(f.getByRole('button', { name: /Đổi mật khẩu/ }));
    expect(await f.findByText('Mật khẩu hiện tại không đúng')).toBeInTheDocument();

    await user.clear(f.getByLabelText('Mật khẩu hiện tại'));
    await user.type(f.getByLabelText('Mật khẩu hiện tại'), 'mat-khau-cu-dung');
    await user.click(f.getByRole('button', { name: /Đổi mật khẩu/ }));
    await waitFor(() =>
      expect(useToasts.getState().toasts.map((t) => t.text)).toContain('Đã đổi mật khẩu — đã đăng xuất 1 thiết bị khác.'),
    );
    expect(calls.filter((c) => c.url.endsWith('/account/password')).at(-1)?.body).toEqual({
      current_password: 'mat-khau-cu-dung',
      new_password: 'mat-khau-moi-rat-dai',
    });
  });

  it('Đăng xuất các thiết bị khác gọi revoke-others', async () => {
    const user = userEvent.setup();
    stubApi((url) => {
      if (url.endsWith('/account/sessions/revoke-others')) return { status: 200, body: { sessions_revoked: 1 } };
      if (url.endsWith('/account')) return { status: 200, body: ACCOUNT };
      return undefined;
    });
    renderAt('/account', [{ path: '/account', element: <AccountPage /> }]);
    await user.click(await screen.findByRole('button', { name: /Đăng xuất các thiết bị khác/ }));
    await waitFor(() => expect(useToasts.getState().toasts.map((t) => t.text)).toContain('Đã đăng xuất 1 thiết bị khác.'));
  });

  // v0.1.61 (F071): tài khoản chưa có PIN nay hiện thẻ "Đặt mã PIN" — xem account-pin-lan-dau.test.tsx.
});

describe('buộc đổi mật khẩu (must_change_password)', () => {
  it('mọi màn Console chuyển về "Đặt mật khẩu mới"; đổi xong vào lại Console', async () => {
    const user = userEvent.setup();
    let mustChange = true;
    stubApi((url) => {
      if (url.endsWith('/auth/me')) return { status: 200, body: { ...ME, must_change_password: mustChange } };
      if (url.endsWith('/navigation')) return { status: 200, body: buildNavigation() };
      if (url.endsWith('/account/password')) {
        mustChange = false;
        return { status: 200, body: { sessions_revoked: 0 } };
      }
      return undefined;
    });
    const router = renderAt('/inbox', [
      { path: '/change-password', element: <ForcePasswordPage /> },
      // v0.1.55 (G5): Owner "/" ⇒ Mặt tiền /owner.
      { path: '/owner', element: <div>Mặt tiền</div> },
      {
        path: '/',
        element: <AppShell />,
        children: [
          // v0.1.42 (F-26): đổi xong → "/" → màn đầu tiên của vai trò (HomeRedirect).
          { index: true, element: <HomeRedirect /> },
          { path: 'inbox', element: <div>Hộp việc</div> },
          { path: 'overview', element: <div>Tổng quan</div> },
        ],
      },
    ]);
    expect(await screen.findByRole('heading', { name: 'Đặt mật khẩu mới' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/change-password');
    expect(screen.getByRole('button', { name: /Đăng xuất/ })).toBeInTheDocument();

    await user.type(screen.getByLabelText('Mật khẩu tạm hiện tại'), 'tam-thoi-123');
    await user.type(screen.getByLabelText('Mật khẩu mới'), 'mat-khau-rieng-cua-sep');
    await user.type(screen.getByLabelText('Nhập lại mật khẩu mới'), 'mat-khau-rieng-cua-sep');
    await user.click(screen.getByRole('button', { name: /Lưu mật khẩu mới/ }));
    expect(await screen.findByText('Mặt tiền')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/owner');
  });

  it('không bị buộc thì trang /change-password tự về "/" rồi màn đầu tiên (Owner ⇒ Mặt tiền /owner)', async () => {
    stubApi((url) =>
      url.endsWith('/auth/me') ? { status: 200, body: ME } : url.endsWith('/navigation') ? { status: 200, body: buildNavigation() } : undefined,
    );
    const router = renderAt('/change-password', [
      { path: '/change-password', element: <ForcePasswordPage /> },
      { path: '/', element: <HomeRedirect /> },
      { path: '/overview', element: <div>Tổng quan</div> },
      { path: '/owner', element: <div>Mặt tiền</div> },
    ]);
    expect(await screen.findByText('Mặt tiền')).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/owner');
  });
});
