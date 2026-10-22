import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { HubLink } from '@gen-harness/contracts';
import { HubLinkCard } from '../../src/screens/mcp/HubLinkCard';
import { expiryToIso, isPublicHttpsUrl, isoToDay } from '../../src/screens/mcp/mcpModel';
import { usePinStore } from '../../src/lib/pinStore';
import { qk } from '../../src/lib/queries';

/**
 * v0.1.26 — thẻ Gen-hub (Gen đọc Kho dữ liệu): token chỉ ghi, Kiểm tra bật liên kết, chỉ Owner cấu hình.
 * v0.1.55: ô "Ngày hết hạn token" điền sẵn hôm nay + 90 ngày khi còn trống (sửa được).
 */

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

const me = (role: string) => ({
  id: 'u', email: 'x@genesis.local', display_name: 'Anh Cơ', role: { code: role, name: role },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
  permissions: { 'system.read': 'all', 'system.manage': role === 'owner' ? 'all' : 'none' },
});

function renderCard(ui: ReactElement, role = 'owner') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, me(role));
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const OFF: HubLink = {
  configured: false, enabled: false, status: 'off', server_id: null, endpoint: null, has_token: false,
  allow_public_network: false, token_expires_at: null, days_left: null, last_ok_at: null, last_error: null, health: null,
};
const SAVED: HubLink = { ...OFF, configured: true, server_id: 's1', endpoint: 'https://hub.example.test/mcp', has_token: true, allow_public_network: true, token_expires_at: '2026-12-28T16:59:00Z', days_left: 90 };
const TOKEN = 'ghtok_SieuBiMat_123456';

/** Hạn gửi kèm token mới (v0.1.55): hôm nay + 90 ngày, ISO — sai số < 1 ngày. */
const isNinetyDaysOut = (iso: unknown) => {
  const days = (Date.parse(String(iso)) - Date.now()) / 86_400_000;
  return days > 89 && days < 91.1;
};

describe('Thẻ Gen-hub', () => {
  it('lần đầu: nhập địa chỉ + token → PATCH /hub/link, ô token xoá trắng, không hiện lại token', async () => {
    let state = OFF;
    const calls = mockFetch((c) => {
      if (c.url.includes('/hub/link') && c.method === 'GET') return json(200, state);
      if (c.url.includes('/hub/link') && c.method === 'PATCH') {
        state = SAVED;
        return json(200, SAVED);
      }
      return json(404);
    });
    const { container } = renderCard(<HubLinkCard />);
    expect(await screen.findByText('Chưa nối', { selector: 'dd' })).toBeInTheDocument();
    // v0.1.42 (F-7): viên trạng thái chung của Kết nối; chữ chi tiết ("Đang tắt") ở dòng phụ.
    expect(container.querySelector('.conn-pill')).toHaveAttribute('data-status', 'not_connected');
    expect(screen.getByText('Đang tắt')).toBeInTheDocument();
    const user = userEvent.setup();
    const save = screen.getByRole('button', { name: 'Lưu' });
    expect(save).toBeDisabled();
    await user.type(screen.getByLabelText('Địa chỉ Gen-hub'), 'https://hub.example.test/mcp');
    const tokenInput = screen.getByLabelText('Token Gen-hub');
    expect(tokenInput).toHaveAttribute('type', 'password');
    expect(tokenInput).toHaveAttribute('data-gen-target', 'mcp.hub_link.token');
    await user.type(tokenInput, TOKEN);
    // v0.1.39 (F-31): địa chỉ https công khai → công tắc tự bật sẵn, kèm cảnh báo.
    expect(screen.getByLabelText('Cho phép Gen-hub ở mạng công cộng')).toHaveAttribute('aria-checked', 'true');
    await user.click(save);
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    const patch = calls.find((c) => c.method === 'PATCH')!;
    // v0.1.55: token mới + chưa có hạn ⇒ gửi kèm hạn điền sẵn (hôm nay + 90 ngày).
    expect(patch.body).toEqual({ endpoint: 'https://hub.example.test/mcp', token: TOKEN, token_expires_at: expect.any(String), allow_public_network: true });
    expect(isNinetyDaysOut((patch.body as { token_expires_at: string }).token_expires_at)).toBe(true);
    expect(await screen.findByText('Đã lưu (mã hoá, không hiện lại)')).toBeInTheDocument();
    expect(screen.getByLabelText('Token mới (bỏ trống để giữ token đã lưu)')).toHaveValue('');
    expect(container.innerHTML).not.toContain(TOKEN);
  });

  it('Kiểm tra → POST /hub/link/test, hiện kết quả và trạng thái "Đang nối"', async () => {
    const calls = mockFetch((c) => {
      if (c.url.includes('/hub/link/test') && c.method === 'POST') {
        return json(200, { ok: true, error: null, latency_ms: 312, exposed_tools: ['a__kho_tom_tat', 'a__kho_search'], missing_tools: [], link: { ...SAVED, enabled: true, status: 'ok' } });
      }
      if (c.url.includes('/hub/link') && c.method === 'GET') return json(200, SAVED);
      return json(404);
    });
    renderCard(<HubLinkCard />);
    const user = userEvent.setup();
    const btn = await screen.findByRole('button', { name: 'Kiểm tra' });
    expect(btn).toHaveAttribute('data-gen-target', 'mcp.hub_link.test');
    await user.click(btn);
    expect(await screen.findByText(/Đã nối Kho · 312 ms · mở 2 tool đọc cho Gen/)).toBeInTheDocument();
    expect(screen.getByText('Đang nối')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Tắt' })).toBeInTheDocument();
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
  });

  it('v0.1.55: ô "Ngày hết hạn token" điền sẵn hôm nay + 90 ngày khi trống, sửa được; có hạn đã lưu thì giữ hạn đó', async () => {
    mockFetch((c) => (c.url.includes('/hub/link') && c.method === 'GET' ? json(200, OFF) : json(404)));
    const first = renderCard(<HubLinkCard />);
    const user = userEvent.setup();
    const field = (await screen.findByLabelText('Ngày hết hạn token')) as HTMLInputElement;
    const expected = isoToDay(new Date(Date.now() + 90 * 86_400_000).toISOString());
    expect(field.value).toBe(expected);
    expect(field).toHaveAccessibleDescription(/điền sẵn hôm nay \+ 90 ngày/);
    // Chưa gõ gì ⇒ chưa có gì để lưu: điền sẵn không tự làm form "bẩn".
    expect(screen.getByRole('button', { name: 'Lưu' })).toBeDisabled();
    // Sửa được; sửa rồi thì là thay đổi thật.
    await user.clear(field);
    await user.type(field, '2027-01-31');
    expect(field.value).toBe('2027-01-31');
    expect(field).not.toHaveAccessibleDescription(/điền sẵn/);
    first.unmount();

    mockFetch((c) => (c.url.includes('/hub/link') && c.method === 'GET' ? json(200, SAVED) : json(404)));
    renderCard(<HubLinkCard />);
    const saved = (await screen.findByLabelText('Ngày hết hạn token')) as HTMLInputElement;
    expect(saved.value).toBe(isoToDay(SAVED.token_expires_at));
  });

  it('v0.1.55: đã nối mà chưa có hạn — mở thẻ rồi bấm Kiểm tra KHÔNG tự ghi hạn điền sẵn (chỉ gửi kèm khi lưu token mới)', async () => {
    const noExpiry = { ...SAVED, token_expires_at: null, days_left: null };
    const calls = mockFetch((c) => {
      if (c.url.includes('/hub/link/test')) return json(200, { ok: true, error: null, latency_ms: 10, exposed_tools: ['a__kho_tom_tat'], missing_tools: [], link: { ...noExpiry, enabled: true, status: 'ok' } });
      if (c.url.includes('/hub/link') && c.method === 'GET') return json(200, noExpiry);
      if (c.url.includes('/hub/link') && c.method === 'PATCH') return json(200, { ...noExpiry, token_expires_at: (c.body as { token_expires_at: string }).token_expires_at });
      return json(404);
    });
    renderCard(<HubLinkCard />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Kiểm tra' }));
    await screen.findByText(/Đã nối Kho/);
    expect(calls.filter((c) => c.method === 'PATCH')).toHaveLength(0);
    // Dán token mới thì hạn điền sẵn đi kèm.
    await user.type(screen.getByLabelText('Token mới (bỏ trống để giữ token đã lưu)'), TOKEN);
    await user.click(screen.getByRole('button', { name: 'Lưu' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    const patch = calls.find((c) => c.method === 'PATCH')!.body as { token: string; token_expires_at: string };
    expect(patch.token).toBe(TOKEN);
    expect(isNinetyDaysOut(patch.token_expires_at)).toBe(true);
  });

  it('Kiểm tra lỗi → hiện lý do; token hết hạn → trạng thái đỏ', async () => {
    mockFetch((c) => {
      if (c.url.includes('/hub/link/test')) {
        return json(200, { ok: false, error: '401: Token Gen-hub hết hạn hoặc đã bị thu hồi', latency_ms: 20, exposed_tools: [], missing_tools: [], link: { ...SAVED, status: 'expired', last_error: '401: Token Gen-hub hết hạn hoặc đã bị thu hồi' } });
      }
      if (c.url.includes('/hub/link')) return json(200, SAVED);
      return json(404);
    });
    renderCard(<HubLinkCard />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Kiểm tra' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Token Gen-hub hết hạn');
    expect(screen.getByText('Token hết hạn')).toBeInTheDocument();
  });

  it('vai trò khác Owner chỉ xem trạng thái, không có ô nhập', async () => {
    mockFetch((c) => (c.url.includes('/hub/link') ? json(200, { ...SAVED, enabled: true, status: 'expiring', days_left: 9 }) : json(404)));
    renderCard(<HubLinkCard />, 'auditor');
    expect(await screen.findByText('Token sắp hết hạn')).toBeInTheDocument();
    expect(screen.getByText('Chỉ Sếp (Owner) cấu hình và dùng Gen-hub.')).toBeInTheDocument();
    expect(screen.queryByLabelText(/Token/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Kiểm tra' })).toBeNull();
  });

  it('ngày hết hạn: ô ngày ↔ ISO giờ VN', () => {
    expect(expiryToIso('2026-12-28')).toBe('2026-12-28T23:59:00+07:00');
    expect(expiryToIso('')).toBeNull();
    expect(isoToDay('2026-12-28T16:59:00Z')).toBe('2026-12-28');
    expect(isoToDay(null)).toBe('');
  });
});

describe('v0.1.39 (F-31) — mạng công cộng bật sẵn, Kiểm tra tự lưu', () => {
  afterEach(() => {
    usePinStore.setState({ open: false, waiters: [] });
  });

  it('isPublicHttpsUrl: chỉ https trỏ ra Internet', () => {
    const cases: Array<[string, boolean]> = [
      ['https://hub.example.test/mcp', true],
      ['https://8.8.8.8/mcp', true],
      ['https://100.64.1.2/mcp', true], // 100.64/10 coi là công khai như máy chủ
      ['https://[2001:4860::8888]/mcp', true],
      ['http://hub.example.test/mcp', false],
      ['http://192.168.1.5/mcp', false],
      ['https://192.168.1.5/mcp', false],
      ['https://10.0.0.2/mcp', false],
      ['https://172.16.0.1/mcp', false],
      ['https://172.31.255.1/mcp', false],
      ['https://172.32.0.1/mcp', true],
      ['https://127.0.0.1/mcp', false],
      ['https://169.254.169.254/latest', false],
      ['https://0.0.0.0/mcp', false],
      ['https://localhost/mcp', false],
      ['https://hub.localhost/mcp', false],
      ['https://hub.local', false],
      ['https://hub.lan/mcp', false],
      ['https://hub.corp.internal/mcp', false],
      ['https://hub.home.arpa/mcp', false],
      ['https://genhub/mcp', false],
      ['https://[::1]/mcp', false],
      ['https://[fd00::1]/mcp', false],
      ['https://[fe80::1]/mcp', false],
      ['https://[::ffff:192.168.1.5]/mcp', false],
      ['khong-phai-url', false],
      ['', false],
    ];
    for (const [url, want] of cases) expect([url, isPublicHttpsUrl(url)]).toEqual([url, want]);
  });

  it('gõ địa chỉ: https công khai → bật sẵn + cảnh báo; mạng nội bộ → tắt; Owner bỏ tích thì không tự bật lại', async () => {
    mockFetch((c) => (c.url.includes('/hub/link') ? json(200, OFF) : json(404)));
    renderCard(<HubLinkCard />);
    const user = userEvent.setup();
    const addr = await screen.findByLabelText('Địa chỉ Gen-hub');
    const sw = screen.getByLabelText('Cho phép Gen-hub ở mạng công cộng');
    await user.type(addr, 'https://hub.example.test/mcp');
    expect(sw).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('note')).toHaveTextContent('Đã bật sẵn vì địa chỉ là https công khai — Gen-hub sẽ được gọi qua Internet. Bỏ tích nếu Gen-hub nằm trong mạng nội bộ.');
    await user.clear(addr);
    await user.type(addr, 'http://192.168.1.5/mcp');
    expect(sw).toHaveAttribute('aria-checked', 'false');
    expect(screen.queryByRole('note')).toBeNull();
    await user.clear(addr);
    await user.type(addr, 'https://hub.local');
    expect(sw).toHaveAttribute('aria-checked', 'false');
    await user.clear(addr);
    await user.type(addr, 'https://hub.example.test/mcp');
    expect(sw).toHaveAttribute('aria-checked', 'true');
    // Owner tự bỏ tích → sửa địa chỉ không tự bật lại.
    await user.click(sw);
    expect(sw).toHaveAttribute('aria-checked', 'false');
    await user.clear(addr);
    await user.type(addr, 'https://kho.example.test/mcp');
    expect(sw).toHaveAttribute('aria-checked', 'false');
    expect(screen.queryByRole('note')).toBeNull();
  });

  it('Kiểm tra khi còn thay đổi → PATCH rồi POST /hub/link/test; hộp PIN chỉ mở 1 lần', async () => {
    let state = OFF;
    let pinOk = false;
    const calls = mockFetch((c) => {
      if (c.url.includes('/hub/link') && c.method === 'GET') return json(200, state);
      if (!pinOk && c.method !== 'GET') return json(423, { code: 'PIN_REQUIRED', title: 'Cần nhập mã PIN', status: 423 });
      if (c.url.includes('/hub/link/test')) return json(200, { ok: true, error: null, error_code: null, latency_ms: 99, exposed_tools: ['a__kho_search'], missing_tools: [], link: { ...state, enabled: true, status: 'ok' } });
      if (c.url.includes('/hub/link') && c.method === 'PATCH') {
        state = SAVED;
        return json(200, SAVED);
      }
      return json(404);
    });
    let opens = 0;
    const unsub = usePinStore.subscribe((s, prev) => {
      if (s.open && !prev.open) {
        opens += 1;
        pinOk = true;
        queueMicrotask(() => usePinStore.getState().finish(true));
      }
    });
    renderCard(<HubLinkCard />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText('Địa chỉ Gen-hub'), 'https://hub.example.test/mcp');
    await user.type(screen.getByLabelText('Token Gen-hub'), TOKEN);
    const btn = screen.getByRole('button', { name: 'Lưu & kiểm tra' });
    expect(btn).toHaveAttribute('data-gen-target', 'mcp.hub_link.test');
    await user.click(btn);
    expect(await screen.findByText(/Đã nối Kho · 99 ms/)).toBeInTheDocument();
    unsub();
    const writes = calls.filter((c) => c.method !== 'GET').map((c) => `${c.method} ${c.url.replace(/^.*\/api\/v1/, '')}`);
    // PATCH bị 423 một lần → nhập PIN → PATCH lại → POST test (không hỏi PIN lần nữa).
    expect(writes).toEqual(['PATCH /hub/link', 'PATCH /hub/link', 'POST /hub/link/test']);
    expect(calls.find((c) => c.method === 'PATCH')!.body).toEqual({ endpoint: 'https://hub.example.test/mcp', token: TOKEN, token_expires_at: expect.any(String), allow_public_network: true });
    expect(opens).toBe(1);
    expect(screen.getByLabelText('Token mới (bỏ trống để giữ token đã lưu)')).toHaveValue('');
  });

  it('Kiểm tra bị chặn mạng công cộng (MCP_NETWORK_BLOCKED) → câu hướng dẫn bật công tắc + mã lỗi', async () => {
    const saved = { ...SAVED, allow_public_network: false };
    mockFetch((c) => {
      if (c.url.includes('/hub/link/test')) {
        return json(200, { ok: false, error: { raw: 'blocked' }, error_code: 'MCP_NETWORK_BLOCKED', latency_ms: 0, exposed_tools: [], missing_tools: [], link: saved });
      }
      if (c.url.includes('/hub/link')) return json(200, saved);
      return json(404);
    });
    const { container } = renderCard(<HubLinkCard />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Kiểm tra' }));
    const status = await screen.findByRole('status');
    expect(status).toHaveTextContent("Bật 'Cho phép Gen-hub ở mạng công cộng' ngay trong thẻ này.");
    expect(status).toHaveTextContent('Chi tiết kỹ thuật');
    expect(status).toHaveTextContent('MCP_NETWORK_BLOCKED');
    expect(container.innerHTML).not.toContain('[object Object]');
  });
});
