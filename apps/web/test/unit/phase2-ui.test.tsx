import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { Channel } from '@gen-harness/contracts';
import { ChannelCard } from '../../src/screens/system/ChannelCard';
import { PinDialogHost } from '../../src/shell/PinDialogHost';
import { ToastHost } from '../../src/shell/ToastHost';
import { queryClient } from '../../src/lib/queryClient';
import { qk2, useChannels } from '../../src/lib/dataQueries';
import { applyEvent } from '../../src/lib/realtime';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

interface Call {
  url: string;
  method: string;
  body: unknown;
}

function mockFetch(handler: (c: Call) => Response | Promise<Response>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const c = { url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(c);
      return handler(c);
    }),
  );
  return calls;
}

const zalo = (over: Partial<Channel> = {}): Channel => ({
  type: 'zalo', name: 'Zalo', installed: true, id: 'c1', state: 'logged_out', account_label: null, started_at: null,
  groups_listening: 0, outbound_queued: 0, last_heartbeat_at: null, stats: null, qr: null, ...over,
});

/** Reads the channel from the cache like the System screen does, so WS events re-render it. */
function Card() {
  const channels = useChannels();
  const c = channels.data?.[0];
  return c ? <ChannelCard channel={c} canManage /> : null;
}

function renderCard(channel: Channel) {
  queryClient.setQueryData(qk2.channels, [channel]);
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Card />
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

describe('channel login: risk warning before the QR', () => {
  it('shows no QR and sends nothing until the risk is acknowledged; then sends accept_risk: true', async () => {
    const user = userEvent.setup();
    const calls = mockFetch((c) => (c.url.endsWith('/channels/zalo/login') ? json(202, { session_id: 's1' }) : json(200, {})));
    renderCard(zalo());

    await user.click(screen.getByRole('button', { name: /Tạo mã QR/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Trước khi hiện mã QR' });
    expect(screen.queryByTestId('qr-zalo')).not.toBeInTheDocument();
    const go = within(dialog).getByRole('button', { name: /Tôi hiểu, hiện mã QR/ });
    expect(go).toBeDisabled();
    await user.click(go);
    expect(calls.some((c) => c.url.includes('/login'))).toBe(false);

    await user.type(within(dialog).getByLabelText('Tên thiết bị (tuỳ chọn)'), 'iPhone của Sếp');
    await user.click(within(dialog).getByRole('checkbox', { name: /Tôi hiểu rủi ro/ }));
    expect(go).toBeEnabled();
    await user.click(go);

    await waitFor(() => expect(calls.find((c) => c.url.endsWith('/channels/zalo/login'))).toBeDefined());
    const login = calls.find((c) => c.url.endsWith('/channels/zalo/login'))!;
    expect(login.method).toBe('POST');
    expect(login.body).toEqual({ accept_risk: true, account_label: 'iPhone của Sếp' });
    // Placeholder until the QR arrives over the socket.
    expect(await screen.findByText('Đang tạo mã QR…')).toBeInTheDocument();

    act(() =>
      applyEvent(queryClient, {
        type: 'channel.qr',
        data: { type: 'zalo', session_id: 's1', image: 'data:image/png;base64,AA', expires_at: new Date(Date.now() + 60_000).toISOString() },
        at: new Date().toISOString(),
      }),
    );
    expect(await screen.findByRole('img', { name: /Mã QR đăng nhập Zalo/ })).toBeInTheDocument();
    expect(screen.getByText('Chờ quét mã')).toBeInTheDocument();
  });

  it('503 BRIDGE_OFFLINE shows a clear error in the card with a retry', async () => {
    const user = userEvent.setup();
    mockFetch((c) =>
      c.url.endsWith('/channels/zalo/login')
        ? json(503, { status: 503, code: 'BRIDGE_OFFLINE', title: 'Bộ nhận tin Zalo/WhatsApp đang tắt — thử lại sau ít phút; nếu vẫn lỗi, gửi Gói chẩn đoán cho người hỗ trợ' })
        : json(200, {}),
    );
    renderCard(zalo());
    await user.click(screen.getByRole('button', { name: /Tạo mã QR/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Trước khi hiện mã QR' });
    await user.click(within(dialog).getByRole('checkbox'));
    await user.click(within(dialog).getByRole('button', { name: /Tôi hiểu, hiện mã QR/ }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Bộ nhận tin Zalo/WhatsApp đang tắt — chưa tạo được mã QR');
    expect(alert).toHaveTextContent('gửi Gói chẩn đoán');
    expect(alert).toHaveTextContent('Chi tiết kỹ thuật');
    expect(within(alert).getByRole('button', { name: /Thử lại/ })).toBeInTheDocument();
    expect(screen.queryByTestId('qr-zalo')).not.toBeInTheDocument();
  });
});

describe('PIN on 423', () => {
  it('logout → 423 PIN_REQUIRED → PIN dialog → verify → the same request is retried', async () => {
    const user = userEvent.setup();
    let pinOk = false;
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/channels/zalo/logout')) {
        return pinOk ? json(204) : json(423, { status: 423, code: 'PIN_REQUIRED', title: 'Thao tác này cần nhập mã PIN', detail: { operation: 'channel.logout' } });
      }
      if (c.url.endsWith('/auth/pin/verify')) {
        pinOk = (c.body as { pin: string }).pin === '246810';
        return pinOk
          ? json(200, { pin_verified_until: new Date(Date.now() + 1800_000).toISOString() })
          : json(401, { status: 401, code: 'PIN_INVALID', title: 'PIN không đúng', attempts_left: 4 });
      }
      if (c.url.endsWith('/channels')) return json(200, [zalo({ state: 'logged_out' })]);
      return json(200, {});
    });
    renderCard(zalo({ state: 'active', account_label: 'iPhone của Sếp', groups_listening: 38, started_at: new Date().toISOString() }));

    await user.click(screen.getByRole('button', { name: /Đăng xuất/ }));
    const confirm = await screen.findByRole('dialog', { name: 'Đăng xuất Zalo?' });
    await user.click(within(confirm).getByRole('button', { name: /Đăng xuất/ }));

    const pin = await screen.findByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    const boxes = within(pin).getAllByLabelText(/Mã PIN — chữ số/);
    await waitFor(() => expect(boxes[0]).toHaveFocus());
    await user.keyboard('246810');

    await waitFor(() => expect(calls.filter((c) => c.url.endsWith('/channels/zalo/logout'))).toHaveLength(2));
    const [first, retry] = calls.filter((c) => c.url.endsWith('/channels/zalo/logout'));
    expect(first.method).toBe('POST');
    expect(retry.method).toBe('POST');
    expect(await screen.findByText('Đã đăng xuất Zalo')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Mã PIN xác nhận thao tác' })).not.toBeInTheDocument());
  });
});
