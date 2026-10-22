import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { Account } from '@gen-harness/contracts';
import { AccountPage } from '../../src/account/AccountPage';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';
import { usePinStore } from '../../src/lib/pinStore';
import { useToasts } from '../../src/lib/toast';
import { PinDialogHost } from '../../src/shell/PinDialogHost';

/**
 * v0.1.61 (gói A1; F071/F097) — người được mời (Quản lý…) chưa có mã PIN:
 *  - Tài khoản của tôi vẫn hiện thẻ "Đặt mã PIN" (trước đây ẩn hẳn ⇒ không có đường nào đặt PIN);
 *  - đặt xong thẻ đổi thành "Đổi mã PIN";
 *  - hộp PIN nói "Tài khoản chưa có mã PIN — đặt ở Tài khoản của tôi", KHÔNG nói "Mã PIN không đúng".
 */

const NOT_SET = 'Tài khoản chưa có mã PIN — đặt ở Tài khoản của tôi';
const ACCOUNT: Account = {
  display_name: 'Chị Quản lý', email: 'quanly@example.vn', role: { code: 'manager', name: 'Quản lý' },
  created_at: '2026-10-10T02:15:00Z', must_change_password: false, has_pin: false, sessions: [],
};

type Call = { method: string; url: string; body: unknown };
const calls: Call[] = [];
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json' },
  });

function stub(handler: (c: Call) => Response | undefined) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const c: Call = {
        method: (init?.method ?? 'GET').toUpperCase(),
        url: String(input),
        body: init?.body ? JSON.parse(String(init.body)) : null,
      };
      calls.push(c);
      return handler(c) ?? json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
}

function renderAccount() {
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AccountPage />
        <PinDialogHost />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  queryClient.clear();
  calls.length = 0;
  useToasts.setState({ toasts: [] });
  vi.stubGlobal('WebSocket', undefined);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Tài khoản của tôi — đặt mã PIN lần đầu', () => {
  it('lời nhắc theo quyền thật: vai duyệt được thấy "cần để duyệt bản nháp", vai chỉ xem thấy "chưa có thao tác nào cần PIN"', async () => {
    const me = (permissions: Record<string, string>) => ({
      id: 'u', email: 'x@example.vn', display_name: 'Người dùng thử', role: { code: 'x', name: 'x' },
      org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
      addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions,
    });
    stub((c) => (c.url.endsWith('/account') ? json(200, ACCOUNT) : undefined));
    queryClient.setQueryData(qk.me, me({ 'action.approve': 'team' }));
    renderAccount();
    expect(await screen.findByText(/cần để duyệt bản nháp/)).toBeInTheDocument();
    document.body.innerHTML = '';
    queryClient.clear();
    queryClient.setQueryData(qk.me, me({ 'system.read': 'all' }));
    renderAccount();
    expect(await screen.findByText(/chưa có thao tác nào cần PIN/)).toBeInTheDocument();
    expect(screen.queryByText(/cần để duyệt bản nháp/)).toBeNull();
  });

  it('chưa có PIN → hiện thẻ "Đặt mã PIN"; đặt xong thẻ đổi thành "Đổi mã PIN"', async () => {
    const user = userEvent.setup();
    let hasPin = false;
    stub((c) => {
      if (c.url.endsWith('/account/pin') && c.method === 'POST') {
        hasPin = true;
        return new Response(null, { status: 204 });
      }
      if (c.url.endsWith('/account')) return json(200, { ...ACCOUNT, has_pin: hasPin });
      return undefined;
    });
    renderAccount();

    const form = await screen.findByRole('form', { name: 'Đặt mã PIN' });
    expect(screen.getByText('Đặt mã PIN', { selector: '.gh-card__title' })).toBeInTheDocument();
    expect(screen.queryByText('Đổi mã PIN', { selector: '.gh-card__title' })).not.toBeInTheDocument();
    expect(screen.getByText(/Bạn chưa có mã PIN/)).toBeInTheDocument();

    const f = within(form);
    await user.type(f.getByLabelText('Mật khẩu hiện tại'), 'mat-khau-dang-nhap');
    await user.click(f.getByLabelText('Mã PIN mới — chữ số 1/6'));
    await user.keyboard('135790');
    await user.click(f.getByLabelText('Nhập lại mã PIN mới — chữ số 1/6'));
    await user.keyboard('135790');
    await user.click(f.getByRole('button', { name: /Đặt mã PIN/ }));

    await waitFor(() =>
      expect(useToasts.getState().toasts.map((t) => t.text)).toContain('Đã đặt mã PIN — dùng được ngay cho thao tác cần PIN.'),
    );
    expect(calls.find((c) => c.method === 'POST' && c.url.endsWith('/account/pin'))?.body).toEqual({
      current_password: 'mat-khau-dang-nhap',
      new_pin: '135790',
      new_pin_confirm: '135790',
    });
    // Tải lại hồ sơ → has_pin = true → thẻ thành "Đổi mã PIN".
    expect(await screen.findByText('Đổi mã PIN', { selector: '.gh-card__title' })).toBeInTheDocument();
    expect(screen.queryByText('Đặt mã PIN', { selector: '.gh-card__title' })).not.toBeInTheDocument();
  });

  it('đã có PIN → vẫn là thẻ "Đổi mã PIN" như cũ', async () => {
    stub((c) => (c.url.endsWith('/account') ? json(200, { ...ACCOUNT, has_pin: true }) : undefined));
    renderAccount();
    expect(await screen.findByRole('form', { name: 'Đổi mã PIN' })).toBeInTheDocument();
    expect(screen.queryByText('Đặt mã PIN', { selector: '.gh-card__title' })).not.toBeInTheDocument();
  });

  it('sai mật khẩu → lỗi theo ô, không có chữ "[object Object]"', async () => {
    const user = userEvent.setup();
    stub((c) => {
      if (c.url.endsWith('/account/pin') && c.method === 'POST') {
        return json(422, {
          status: 422, code: 'VALIDATION', title: 'Dữ liệu chưa hợp lệ',
          errors: { current_password: 'Mật khẩu hiện tại không đúng' },
        });
      }
      if (c.url.endsWith('/account')) return json(200, ACCOUNT);
      return undefined;
    });
    renderAccount();
    const form = await screen.findByRole('form', { name: 'Đặt mã PIN' });
    const f = within(form);
    await user.type(f.getByLabelText('Mật khẩu hiện tại'), 'sai-mat-khau');
    await user.click(f.getByLabelText('Mã PIN mới — chữ số 1/6'));
    await user.keyboard('135790');
    await user.click(f.getByLabelText('Nhập lại mã PIN mới — chữ số 1/6'));
    await user.keyboard('135790');
    await user.click(f.getByRole('button', { name: /Đặt mã PIN/ }));
    expect(await f.findByText('Mật khẩu hiện tại không đúng')).toBeInTheDocument();
    expect(form.textContent).not.toContain('[object Object]');
    expect(screen.getByRole('form', { name: 'Đặt mã PIN' })).toBeInTheDocument(); // vẫn chưa có PIN
  });
});

describe('Hộp PIN khi tài khoản chưa có mã PIN', () => {
  it('409 PIN_NOT_SET → nói "chưa có mã PIN", không báo "không đúng" và không có số lần thử', async () => {
    const user = userEvent.setup();
    stub((c) => {
      if (c.url.endsWith('/auth/pin/verify')) {
        return json(409, { status: 409, code: 'PIN_NOT_SET', title: NOT_SET });
      }
      if (c.url.endsWith('/account')) return json(200, ACCOUNT);
      return undefined;
    });
    renderAccount();
    await screen.findByRole('form', { name: 'Đặt mã PIN' });
    let done: Promise<boolean> = Promise.resolve(false);
    act(() => {
      done = usePinStore.getState().request();
    });
    const dialog = await screen.findByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    const boxes = within(dialog).getAllByLabelText(/Mã PIN — chữ số/);
    await waitFor(() => expect(boxes[0]).toHaveFocus());
    await user.keyboard('123456');

    const status = await within(dialog).findByText(NOT_SET);
    expect(status).toHaveAttribute('role', 'status');
    expect(dialog.textContent).not.toMatch(/không đúng/);
    expect(dialog.textContent).not.toMatch(/còn \d+ lần thử/);
    // Đóng hộp: thao tác bị huỷ, không treo.
    await user.click(within(dialog).getByRole('button', { name: 'Huỷ' }));
    await expect(done).resolves.toBe(false);
  });
});
