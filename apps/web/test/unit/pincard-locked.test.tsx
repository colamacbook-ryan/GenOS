import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AccountPage } from '../../src/account/AccountPage';
import { qk } from '../../src/lib/queries';

/**
 * v0.1.35: «Đổi mã PIN» khi PIN đang bị khoá — nói rõ bị khoá + giờ địa phương, không hiện ISO UTC thô.
 * v0.1.42 (F-61): thẻ mã PIN chỉ còn ở Tài khoản của tôi (AccountPage) — kiểm trên đúng chỗ đó.
 */

afterEach(() => {
  vi.unstubAllGlobals();
});

const ACCOUNT = {
  display_name: 'Owner', email: 'owner@genesis.local', role: { code: 'owner', name: 'Owner' },
  created_at: '2026-05-04T02:15:00Z', must_change_password: false, has_pin: true, sessions: [],
};

describe('<PinCard> (Tài khoản của tôi) khi PIN bị khoá', () => {
  it('POST /account/pin → 423 PIN_LOCKED: hiện title + giờ theo múi giờ tổ chức', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('WebSocket', undefined);
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith('/account/pin') && init?.method === 'POST') {
          return new Response(
            JSON.stringify({
              type: 'https://gen-harness.local/errors/pin_locked',
              title: 'Mã PIN đang bị khoá do nhập sai nhiều lần',
              status: 423,
              code: 'PIN_LOCKED',
              detail: 'Thử lại sau ít phút',
              locked_until: '2026-10-02T08:15:00Z',
            }),
            { status: 423, headers: { 'Content-Type': 'application/problem+json' } },
          );
        }
        if (url.endsWith('/account')) return new Response(JSON.stringify(ACCOUNT), { status: 200, headers: { 'Content-Type': 'application/json' } });
        return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
      }),
    );
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    qc.setQueryData(qk.me, {
      id: 'u', email: 'owner@genesis.local', display_name: 'Owner', role: { code: 'owner', name: 'Owner' },
      org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
      addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: {},
    });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <AccountPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const form = await screen.findByRole('form', { name: 'Đổi mã PIN' });
    const f = within(form);
    await user.type(f.getByLabelText('Mật khẩu hiện tại'), 'mat-khau-dang-nhap');
    await user.click(f.getByLabelText('Mã PIN mới — chữ số 1/6'));
    await user.keyboard('246810');
    await user.click(f.getByLabelText('Nhập lại mã PIN mới — chữ số 1/6'));
    await user.keyboard('246810');
    await user.click(f.getByRole('button', { name: /Đổi mã PIN/ }));
    await waitFor(() =>
      expect(f.getByText('Mã PIN đang bị khoá do nhập sai nhiều lần. Thử lại sau 02/10 15:15:00.')).toBeInTheDocument(),
    );
    expect(form.textContent).not.toContain('2026-10-02T08:15');
  });
});
