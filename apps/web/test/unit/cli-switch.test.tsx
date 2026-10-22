import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { CliProfile } from '@gen-harness/contracts';
import { CliCard } from '../../src/screens/system/CliCard';
import { PinDialogHost } from '../../src/shell/PinDialogHost';
import { ToastHost } from '../../src/shell/ToastHost';
import { queryClient } from '../../src/lib/queryClient';

/**
 * v0.1.30 — Boss: "chức năng đổi tài khoản google không hoạt động".
 * No WebSocket in these tests on purpose: the login link must arrive via GET /cli/login/{id} polling, and the
 * switch must go through the global PIN dialog (423 → PIN → retry) and end with the new account shown.
 */
const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

interface Call {
  url: string;
  method: string;
}

function mockFetch(handler: (c: Call) => Response | Promise<Response>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const c = { url: String(url), method: init?.method ?? 'GET' };
      calls.push(c);
      return handler(c);
    }),
  );
  return calls;
}

const prof = (over: Partial<CliProfile>): CliProfile => ({
  id: 'p1', email: 'an@example.vn', plan_label: null, active: false, expires_at: null, state: 'ok', ...over,
});

function renderCard() {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <CliCard canManage showCredentials={false} />
        <PinDialogHost />
        <ToastHost />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  queryClient.clear();
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } });
  vi.unstubAllGlobals();
  document.cookie = 'gh_csrf=test-csrf';
});

describe('CLI — đổi tài khoản Google', () => {
  it('switch: 423 → PIN → retry → the chosen account is shown as in use', async () => {
    const user = userEvent.setup();
    let profiles = [prof({ id: 'a', email: 'an@example.vn', active: true }), prof({ id: 'b', email: 'binh@example.vn' })];
    let pinOk = false;
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/cli/profiles')) return json(200, profiles);
      if (c.url.endsWith('/cli/profiles/b/activate')) {
        if (!pinOk) return json(423, { status: 423, code: 'PIN_REQUIRED', title: 'Thao tác này cần nhập mã PIN' });
        profiles = profiles.map((p) => ({ ...p, active: p.id === 'b' }));
        return json(200, profiles[1]);
      }
      if (c.url.endsWith('/auth/pin/verify')) {
        pinOk = true;
        return json(200, { pin_verified_until: new Date(Date.now() + 1800_000).toISOString() });
      }
      return json(200, {});
    });
    renderCard();
    const current = await screen.findByTestId('cli-current');
    expect(current).toHaveTextContent('an@example.vn');
    expect(current).toHaveTextContent('1 tài khoản khác đã lưu');

    await user.click(screen.getByRole('button', { name: 'Đổi tài khoản' }));
    const dlg = await screen.findByRole('dialog', { name: 'Đổi tài khoản Google cho AI' });
    expect(within(dlg).getByTestId('cli-dialog-current')).toHaveTextContent('AI đang dùng an@example.vn');
    expect(within(dlg).getByRole('button', { name: 'Thêm tài khoản Google' })).toBeEnabled();
    await user.click(within(dlg).getByRole('button', { name: 'Dùng tài khoản binh@example.vn' }));

    const pin = await screen.findByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    const boxes = within(pin).getAllByLabelText(/Mã PIN — chữ số/);
    await waitFor(() => expect(boxes[0]).toHaveFocus());
    await user.keyboard('246810');

    expect(await screen.findByText(/Đã chuyển sang binh@example\.vn/)).toBeInTheDocument();
    expect(calls.filter((c) => c.url.endsWith('/activate'))).toHaveLength(2);
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Đổi tài khoản Google cho AI' })).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('cli-current')).toHaveTextContent('binh@example.vn'));
  });

  it('PIN cancelled or a login in progress → a clear Vietnamese message, nothing silent', async () => {
    const user = userEvent.setup();
    let attempt = 0;
    mockFetch((c) => {
      if (c.url.endsWith('/cli/profiles')) return json(200, [prof({ id: 'a', active: true }), prof({ id: 'b', email: 'binh@example.vn' })]);
      if (c.url.endsWith('/activate')) {
        attempt += 1;
        return attempt === 1
          ? json(423, { status: 423, code: 'PIN_REQUIRED', title: 'Thao tác này cần nhập mã PIN' })
          : json(409, { status: 409, code: 'CLI_LOGIN_IN_PROGRESS', title: 'x' });
      }
      return json(200, {});
    });
    renderCard();
    await user.click(await screen.findByRole('button', { name: 'Đổi tài khoản' }));
    const dlg = await screen.findByRole('dialog', { name: 'Đổi tài khoản Google cho AI' });
    await user.click(within(dlg).getByRole('button', { name: 'Dùng tài khoản binh@example.vn' }));
    const pin = await screen.findByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await user.click(within(pin).getByRole('button', { name: /Huỷ/ }));
    expect(await within(dlg).findByText(/Chưa đổi — cần nhập mã PIN/)).toBeInTheDocument();

    await user.click(within(dlg).getByRole('button', { name: 'Dùng tài khoản binh@example.vn' }));
    expect(await within(dlg).findByText(/Đang đăng nhập thêm một tài khoản Google/)).toBeInTheDocument();
  });

  it('add a Google account without WebSocket: the link and code box arrive by polling', async () => {
    const user = userEvent.setup();
    let polls = 0;
    const url = 'https://accounts.google.com/o/oauth2/auth?client_id=agy&state=POLL';
    mockFetch((c) => {
      if (c.url.endsWith('/cli/profiles')) return json(200, [prof({ id: 'a', active: true })]);
      if (c.url.endsWith('/cli/login') && c.method === 'POST') return json(202, { login_id: 'L1' });
      if (c.url.endsWith('/cli/login/L1') && c.method === 'GET') {
        polls += 1;
        return json(200, { login_id: 'L1', status: 'waiting_code', url, message: null, profile: null });
      }
      return json(200, {});
    });
    renderCard();
    await user.click(await screen.findByRole('button', { name: 'Đổi tài khoản' }));
    await user.click(screen.getByRole('button', { name: 'Thêm tài khoản Google' }));
    const link = await screen.findByRole('link', { name: 'Mở trang đăng nhập Google' });
    expect(link).toHaveAttribute('href', url);
    expect(screen.getByLabelText('Mã xác thực')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Chép link' })).toBeInTheDocument();
    expect(polls).toBeGreaterThan(0);
  });

  it('a profile without a known email renders a readable label instead of crashing', async () => {
    mockFetch((c) => {
      if (c.url.endsWith('/cli/profiles')) return json(200, [prof({ id: 'a', email: null, active: true })]);
      return json(200, {});
    });
    renderCard();
    expect(await screen.findByText('Tài khoản Google (chưa rõ email)')).toBeInTheDocument();
  });

  it('saved accounts but none in use → "Chọn tài khoản" (before: only "Đăng nhập", saved accounts unreachable)', async () => {
    mockFetch((c) => {
      if (c.url.endsWith('/cli/profiles')) return json(200, [prof({ id: 'a' }), prof({ id: 'b', email: 'binh@example.vn' })]);
      return json(200, {});
    });
    renderCard();
    expect(await screen.findByRole('button', { name: 'Chọn tài khoản' })).toBeInTheDocument();
    expect(screen.getByTestId('cli-current')).toHaveTextContent('2 tài khoản đã lưu');
  });

  it("PIN hint chỉ đi cùng nút đăng nhập: có tài khoản đã lưu ('Đổi tài khoản') thì không hiện", async () => {
    const PIN_HINT = 'Đăng nhập / thêm tài khoản cần mã PIN';
    mockFetch((c) => {
      if (c.url.endsWith('/cli/profiles')) return json(200, [prof({ id: 'a', active: true })]);
      return json(200, {});
    });
    const view = renderCard();
    expect(await screen.findByRole('button', { name: 'Đổi tài khoản' })).toBeInTheDocument();
    expect(screen.queryByText(PIN_HINT)).toBeNull();
    view.unmount();

    queryClient.clear();
    mockFetch((c) => {
      if (c.url.endsWith('/cli/profiles')) return json(200, [prof({ id: 'a', active: true, state: 'expired' })]);
      return json(200, {});
    });
    const expired = renderCard();
    expect(await screen.findByRole('button', { name: 'Đăng nhập lại' })).toBeInTheDocument();
    expect(screen.getByText(PIN_HINT)).toBeInTheDocument();
    expired.unmount();

    queryClient.clear();
    mockFetch((c) => {
      if (c.url.endsWith('/cli/profiles')) return json(200, []);
      return json(200, {});
    });
    renderCard();
    expect(await screen.findByText(PIN_HINT)).toBeInTheDocument();
  });
});
