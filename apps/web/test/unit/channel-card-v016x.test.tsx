import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { Channel } from '@gen-harness/contracts';
import { ChannelCard } from '../../src/screens/system/ChannelCard';
import {
  channelAction,
  channelErrorText,
  channelLogoutAction,
  channelMeta,
  channelResumeNeedsConfirm,
  channelSecondaryAction,
  channelStatus,
  qrHint,
  qrRemaining,
} from '../../src/screens/system/systemModel';
import { PinDialogHost } from '../../src/shell/PinDialogHost';
import { ToastHost } from '../../src/shell/ToastHost';
import { queryClient } from '../../src/lib/queryClient';
import { qk2, useChannels } from '../../src/lib/dataQueries';
import { qk } from '../../src/lib/queries';
import { qkSystem } from '../../src/screens/system/queries';

// Gói B4 (v0.1.62): kênh bền — nối lại bằng phiên đã lưu, "Mất liên lạc", QR đếm ngược theo expires_at, bỏ lời hứa "tự gửi lại".

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

type View = Channel;
const zalo = (over: Partial<View> = {}): View => ({
  type: 'zalo', name: 'Zalo', installed: true, id: 'c1', state: 'active', account_label: 'iPhone của Sếp',
  started_at: new Date(Date.now() - 3600_000).toISOString(), groups_listening: 3, outbound_queued: 0,
  last_heartbeat_at: new Date().toISOString(), stats: null, qr: null, ...over,
});

function Card() {
  const channels = useChannels();
  const c = channels.data?.[0];
  return c ? <ChannelCard channel={c} canManage /> : null;
}

function renderCard(c: View) {
  queryClient.setQueryData(qk2.channels, [c]);
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
});

describe('F046: mất mạng không bắt quét lại QR', () => {
  const lostNet = (over: Partial<View> = {}) => zalo({ state: 'error', error: 'NETWORK_UNAVAILABLE', resumable: true, ended_at: null, ...over });

  it('phiên "Lỗi kết nối" còn credential: nút chính là Nối lại bằng phiên đã lưu, quét lại QR chỉ là nút phụ', () => {
    const c = lostNet();
    expect(channelAction(c)).toMatchObject({ action: 'resume', label: 'Nối lại bằng phiên đã lưu' });
    expect(channelSecondaryAction(c)).toMatchObject({ action: 'rescan', label: 'Quét lại QR' });
    // Không còn credential (đã bị từ chối thật) ⇒ chỉ còn quét lại QR như trước.
    expect(channelAction(zalo({ state: 'error', resumable: false }))).toMatchObject({ action: 'rescan', label: 'Quét lại QR' });
    expect(channelSecondaryAction(zalo({ state: 'error', resumable: false }))).toBeNull();
    expect(channelAction(zalo({ state: 'expired' }))).toMatchObject({ action: 'rescan' });
    // Máy chủ cũ không gửi `resumable` ⇒ hành vi cũ.
    expect(channelAction(zalo({ state: 'error' }))).toMatchObject({ action: 'rescan' });
  });

  it('thẻ nói đúng nguyên nhân: mất mạng, đang tự nối lại, không cần quét QR', () => {
    renderCard(lostNet());
    expect(screen.getByText('Mất mạng — đang nối lại')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Nối lại bằng phiên đã lưu/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Quét lại QR/ })).toBeInTheDocument();
    const fail = document.querySelector('.ch-fail') as HTMLElement;
    expect(fail).toHaveTextContent(/không cần quét lại QR/);
    expect(within(fail).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(fail).not.toHaveTextContent(/hết hạn/);
  });

  it('bấm Nối lại gọi POST /channels/{type}/resume (không mở hộp cảnh báo rủi ro / QR)', async () => {
    const user = userEvent.setup();
    const calls = mockFetch((c) => (c.url.endsWith('/channels/zalo/resume') ? json(202, { session_id: 's1' }) : json(200, [])));
    renderCard(lostNet());
    await user.click(screen.getByRole('button', { name: /Nối lại bằng phiên đã lưu/ }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/channels/zalo/resume'))).toBe(true));
    expect(screen.queryByRole('dialog', { name: 'Trước khi hiện mã QR' })).not.toBeInTheDocument();
    expect(await screen.findByText(/Đang nối lại Zalo bằng phiên đã lưu/)).toBeInTheDocument();
  });

  it('Nối lại khi bộ nhận tin đang tắt: câu tiếng Việt thường + Chi tiết kỹ thuật, không chữ "Bridge"', async () => {
    const user = userEvent.setup();
    mockFetch((c) =>
      c.url.endsWith('/channels/zalo/resume')
        ? json(503, { status: 503, code: 'BRIDGE_OFFLINE', title: 'Bộ nhận tin Zalo/WhatsApp đang tắt — thử lại sau ít phút' })
        : json(200, []),
    );
    renderCard(lostNet());
    await user.click(screen.getByRole('button', { name: /Nối lại bằng phiên đã lưu/ }));
    await screen.findByText(/Bộ nhận tin Zalo\/WhatsApp đang tắt/);
    const alert = document.querySelector('.ch-alert') as HTMLElement;
    expect(alert).toHaveTextContent('Bộ nhận tin Zalo/WhatsApp đang tắt');
    expect(alert.querySelector('.ch-alert__title')?.textContent).not.toMatch(/bridge/i);
    expect(alert.querySelector('.ch-alert__text')?.textContent).not.toMatch(/bridge/i);
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(alert).toHaveTextContent('BRIDGE_OFFLINE');
  });

  it('câu lỗi: mất mạng / bị mở nơi khác (nối lại được) / bị từ chối thật', () => {
    expect(channelErrorText('NETWORK_UNAVAILABLE', 'Zalo', 'zalo')).toMatch(/không cần quét lại QR/);
    expect(channelErrorText('CONNECTION_REPLACED', 'Zalo', 'zalo', true)).toMatch(/Nối lại bằng phiên đã lưu/);
    expect(channelErrorText('CONNECTION_REPLACED', 'Zalo', 'zalo', false)).toMatch(/Quét lại QR/);
    expect(channelErrorText('CREDENTIAL_REJECTED', 'Zalo', 'zalo')).toMatch(/không còn dùng được/);
    for (const code of ['NETWORK_UNAVAILABLE', 'WA_NETWORK', 'CHANNEL_UNAVAILABLE', 'BRIDGE_KEY_MISSING', 'RECONNECT_FAILED']) {
      expect(channelErrorText(code, 'WhatsApp', 'whatsapp')).not.toMatch(/bridge/i);
    }
  });
});

describe('F051 (phần thẻ): bộ nhận tin mất nhịp tim ⇒ "Mất liên lạc"', () => {
  it('còn phiên "Đang kết nối" nhưng bridge_online=false ⇒ Mất liên lạc + cảnh báo, pill không còn báo Đang chạy', () => {
    const c = zalo({ bridge_online: false });
    expect(channelStatus(c).label).toBe('Mất liên lạc');
    renderCard(c);
    expect(screen.getByText('Mất liên lạc')).toBeInTheDocument();
    expect(screen.queryByText('Đang kết nối')).not.toBeInTheDocument();
    const alert = screen.getByTestId('lost-zalo');
    expect(alert).toHaveTextContent('Mất liên lạc với bộ nhận tin Zalo/WhatsApp');
    expect(alert).toHaveTextContent('Gói chẩn đoán');
    expect(screen.queryByText('Đang chạy')).not.toBeInTheDocument();
  });

  it('bridge_online=true hoặc máy chủ cũ không gửi trường này ⇒ như trước', () => {
    expect(channelStatus(zalo({ bridge_online: true })).label).toBe('Đang kết nối');
    expect(channelStatus(zalo()).label).toBe('Đang kết nối');
    expect(channelStatus(zalo({ state: 'logged_out', bridge_online: false })).label).toBe('Chưa đăng nhập');
    renderCard(zalo({ bridge_online: true }));
    expect(screen.queryByTestId('lost-zalo')).not.toBeInTheDocument();
  });
});

describe('F051 (tích hợp B5↔B4): /system/health bridge.silent ⇒ thẻ kênh không còn "Đang kết nối"', () => {
  const me = (permissions: Record<string, string>) => ({
    id: 'u', email: 'owner@genesis.local', display_name: 'Anh Cơ', role: { code: 'owner', name: 'Owner' },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
    addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions,
  });
  const healthWith = (bridge?: { state: 'ok' | 'silent'; last_seen_at: string | null; silent_minutes: number | null }) => ({
    checked_at: new Date().toISOString(), overall: bridge?.state === 'silent' ? 'bad' : 'ok',
    worker: { state: 'ok', alive: true, last_seen_at: null, silent_minutes: null }, browser: { state: 'off', last_heartbeat_at: null },
    ...(bridge ? { bridge } : {}), queues: [], crons: [], issues: [],
  });
  const silent = { state: 'silent' as const, last_seen_at: new Date(Date.now() - 7 * 60_000).toISOString(), silent_minutes: 7 };

  it('kênh còn báo active (bridge_online chưa biết) nhưng health.bridge.state="silent" ⇒ "Mất liên lạc" + cảnh báo', async () => {
    mockFetch((c) => (c.url.endsWith('/system/health') ? json(200, healthWith(silent)) : json(200, [])));
    queryClient.setQueryData(qk.me, me({ 'system.read': 'all' }));
    renderCard(zalo());
    expect(await screen.findByText('Mất liên lạc')).toBeInTheDocument();
    expect(screen.queryByText('Đang kết nối')).not.toBeInTheDocument();
    expect(screen.getByTestId('lost-zalo')).toHaveTextContent('Mất liên lạc với bộ nhận tin Zalo/WhatsApp');
  });

  it('health.bridge.state="ok" hoặc không có khối bridge ⇒ vẫn "Đang kết nối"', async () => {
    const calls = mockFetch((c) => (c.url.endsWith('/system/health') ? json(200, healthWith({ state: 'ok', last_seen_at: null, silent_minutes: null })) : json(200, [])));
    queryClient.setQueryData(qk.me, me({ 'system.read': 'all' }));
    renderCard(zalo());
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/system/health'))).toBe(true));
    await waitFor(() => expect(queryClient.getQueryData(qkSystem.health)).toBeDefined());
    expect(screen.getByText('Đang kết nối')).toBeInTheDocument();
    expect(screen.queryByTestId('lost-zalo')).not.toBeInTheDocument();
  });

  it('vai không có system.read: không gọi /system/health, thẻ giữ nguyên', async () => {
    const calls = mockFetch(() => json(200, []));
    queryClient.setQueryData(qk.me, me({}));
    renderCard(zalo());
    expect(screen.getByText('Đang kết nối')).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith('/system/health'))).toBe(false);
  });

  it('chỉ áp cho kênh QR đang chạy: phiên đã đăng xuất vẫn "Chưa đăng nhập" dù bridge im', async () => {
    mockFetch((c) => (c.url.endsWith('/system/health') ? json(200, healthWith(silent)) : json(200, [])));
    queryClient.setQueryData(qk.me, me({ 'system.read': 'all' }));
    renderCard(zalo({ state: 'logged_out' }));
    await waitFor(() => expect(queryClient.getQueryData(qkSystem.health)).toBeDefined());
    expect(screen.getByText('Chưa đăng nhập')).toBeInTheDocument();
    expect(screen.queryByText('Mất liên lạc')).not.toBeInTheDocument();
  });
});

describe('F063: mã QR đếm ngược theo expires_at', () => {
  it('Zalo sống 100 giây: đếm ngược không bị cắt ở 60, thanh theo 100 giây, câu hướng dẫn không hứa "60 giây"', () => {
    const now = Date.parse('2026-10-16T03:00:00Z');
    const q = qrRemaining('2026-10-16T03:01:30Z', now, 100_000);
    expect(q.label).toBe('90 giây');
    expect(Math.round(q.pct)).toBe(90);
    expect(qrHint({ type: 'zalo' })).not.toMatch(/60 giây/);
    expect(qrHint({ type: 'zalo' })).toMatch(/thanh đếm ngược/);
  });

  it('thẻ Zalo đang chờ quét hiện đúng số giây còn lại (~95) chứ không phải tối đa 60', () => {
    renderCard(
      zalo({
        state: 'pending_qr',
        qr: { session_id: 's1', image: 'data:image/png;base64,AA', expires_at: new Date(Date.now() + 95_000).toISOString(), scanned: false },
      }),
    );
    const block = screen.getByTestId('qr-zalo');
    expect(block.querySelector('.qr-count__val')?.textContent).toMatch(/^9[45] giây$/);
    expect(block).not.toHaveTextContent(/mỗi 60 giây/);
  });
});

describe('F069: không hứa tự gửi lại khi phiên nối lại', () => {
  it('thẻ lỗi không còn "tin trong hàng đợi gửi"; hướng dẫn QR không hứa tự gửi', () => {
    const m = channelMeta(zalo({ state: 'error', outbound_queued: 4, resumable: true }));
    expect(`${m.before}${m.groups}${m.after}`).not.toMatch(/hàng đợi/);
    for (const type of ['zalo', 'whatsapp'] as const) {
      expect(qrHint({ type, outbound_queued: 4 })).not.toMatch(/tự gửi|hàng đợi/);
    }
  });

  it('hộp xác nhận Đăng xuất không còn nói tin gửi đi được giữ lại', async () => {
    const user = userEvent.setup();
    renderCard(zalo({ outbound_queued: 2 }));
    await user.click(screen.getByRole('button', { name: /Đăng xuất/ }));
    const dlg = await screen.findByRole('dialog', { name: /Đăng xuất Zalo/ });
    expect(dlg).not.toHaveTextContent(/hàng đợi được giữ/);
    expect(dlg).not.toHaveTextContent(/Bridge/);
  });
});

describe('Gói K (P1-1): phiên "Lỗi kết nối" đăng xuất được; bị mở nơi khác thì hỏi lại trước khi nối', () => {
  const replaced = (over: Partial<View> = {}) =>
    zalo({ state: 'error', error: 'CONNECTION_REPLACED', resumable: true, ended_at: new Date().toISOString(), ...over });

  it('mô hình: trạng thái error có nút Đăng xuất; các trạng thái khác thì không thêm nút phụ này', () => {
    expect(channelLogoutAction(zalo({ state: 'error' }))).toMatchObject({ label: 'Đăng xuất' });
    expect(channelLogoutAction(zalo({ state: 'error', resumable: false }))).toMatchObject({ label: 'Đăng xuất' });
    for (const state of ['active', 'expired', 'pending_qr', 'logged_out'] as const) {
      expect(channelLogoutAction(zalo({ state }))).toBeNull();
    }
    expect(channelResumeNeedsConfirm(replaced())).toBe(true);
    expect(channelResumeNeedsConfirm(zalo({ state: 'error', error: 'NETWORK_UNAVAILABLE', resumable: true }))).toBe(false);
    expect(channelResumeNeedsConfirm(zalo({ state: 'active' }))).toBe(false);
  });

  it('thẻ error có nút Đăng xuất; bấm → hộp xác nhận → gọi POST /channels/zalo/logout (sau mã PIN)', async () => {
    const user = userEvent.setup();
    const calls = mockFetch((c) => (c.url.endsWith('/channels/zalo/logout') ? json(204) : json(200, [])));
    queryClient.setQueryData(qk.me, {
      id: 'u', email: 'o@x', display_name: 'A', role: { code: 'owner', name: 'Owner' },
      org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
      pin_verified_until: new Date(Date.now() + 600_000).toISOString(), permissions: {},
    });
    renderCard(replaced());
    await user.click(screen.getByRole('button', { name: 'Đăng xuất' }));
    const dlg = await screen.findByRole('dialog', { name: /Đăng xuất Zalo/ });
    await user.click(within(dlg).getByRole('button', { name: 'Đăng xuất' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/channels/zalo/logout'))).toBe(true));
  });

  it('CONNECTION_REPLACED: bấm Nối lại chưa gọi API mà hỏi "Sẽ ngắt … ở nơi khác?"; Huỷ thì không gọi, đồng ý mới gọi', async () => {
    const user = userEvent.setup();
    const calls = mockFetch((c) => (c.url.endsWith('/channels/zalo/resume') ? json(202, { session_id: 's1' }) : json(200, [])));
    renderCard(replaced());
    await user.click(screen.getByRole('button', { name: /Nối lại bằng phiên đã lưu/ }));
    const dlg = await screen.findByRole('dialog', { name: /Nối lại Zalo\?/ });
    expect(dlg).toHaveTextContent(/ngắt/);
    expect(dlg).toHaveTextContent(/Zalo Web/);
    expect(calls.some((c) => c.url.endsWith('/channels/zalo/resume'))).toBe(false);
    await user.click(within(dlg).getByRole('button', { name: 'Huỷ' }));
    expect(calls.some((c) => c.url.endsWith('/channels/zalo/resume'))).toBe(false);
    await user.click(screen.getByRole('button', { name: /Nối lại bằng phiên đã lưu/ }));
    const dlg2 = await screen.findByRole('dialog', { name: /Nối lại Zalo\?/ });
    await user.click(within(dlg2).getByRole('button', { name: /Vẫn nối lại/ }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/channels/zalo/resume'))).toBe(true));
  });

  it('mất mạng (không phải bị mở nơi khác): Nối lại gọi thẳng, không hỏi', async () => {
    const user = userEvent.setup();
    const calls = mockFetch((c) => (c.url.endsWith('/channels/zalo/resume') ? json(202, { session_id: 's1' }) : json(200, [])));
    renderCard(zalo({ state: 'error', error: 'NETWORK_UNAVAILABLE', resumable: true }));
    await user.click(screen.getByRole('button', { name: /Nối lại bằng phiên đã lưu/ }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/channels/zalo/resume'))).toBe(true));
    expect(screen.queryByRole('dialog', { name: /Nối lại Zalo\?/ })).not.toBeInTheDocument();
  });
});
