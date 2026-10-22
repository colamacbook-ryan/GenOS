/**
 * v0.1.42 (F-7, F-61): Kết nối — mỗi thứ một thẻ, cùng một kiểu viên trạng thái (Đang chạy · Cần Sếp xử lý · Chưa
 * nối), đúng một nút chính; hàm thuần trong connectionsModel.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { Channel, ChannelState } from '@gen-harness/contracts';
import { ConnectionsScreen } from '../../src/screens/connections/ConnectionsScreen';
import {
  activeCliState,
  brainStatus,
  channelConnStatus,
  facebookStatus,
  hubStatus,
  mcpEnabledCount,
  mcpStatus,
  orderChannels,
} from '../../src/screens/connections/connectionsModel';
import { qk } from '../../src/lib/queries';
import { permissionsOf, type RoleCode } from '../mock-api';

describe('connectionsModel', () => {
  it('channelConnStatus: mỗi trạng thái kênh', () => {
    const cases: Array<[ChannelState, string]> = [
      ['active', 'running'],
      ['expired', 'needs_boss'],
      ['error', 'needs_boss'],
      ['pending_qr', 'needs_boss'],
      ['logged_out', 'not_connected'],
      ['not_installed', 'not_connected'],
      ['identity_only', 'not_connected'],
    ];
    for (const [state, want] of cases) expect(channelConnStatus({ state }), state).toBe(want);
  });

  it('brainStatus: chưa nguồn nào / chưa chọn model / nguồn hết hạn / ổn', () => {
    const ok = { kind: 'gemini' as const, enabled: true, auth_state: 'ok' as const };
    expect(brainStatus({ providers: [] })).toBe('not_connected');
    expect(brainStatus({ providers: [{ kind: 'system_one', enabled: true, auth_state: 'ok' }] })).toBe('not_connected');
    expect(brainStatus({ providers: [ok] })).toBe('running');
    expect(brainStatus({ providers: [ok], noModel: true })).toBe('needs_boss');
    expect(brainStatus({ providers: [{ ...ok, auth_state: 'expired' }] })).toBe('needs_boss');
    expect(brainStatus({ providers: [ok, { ...ok, auth_state: 'error' }] })).toBe('needs_boss');
    expect(brainStatus({ providers: [{ ...ok, enabled: false }] })).toBe('needs_boss');
    // Chỉ có tài khoản CLI.
    expect(brainStatus({ providers: [], cliStatus: 'ok' })).toBe('running');
    expect(brainStatus({ providers: [], cliStatus: ['expired', null] })).toBe('needs_boss');
    expect(brainStatus({ providers: [ok], cliStatus: [null, 'expiring'] })).toBe('running');
    expect(activeCliState([{ id: 'a', email: null, plan_label: null, active: true, expires_at: null, state: 'expiring' }])).toBe('expiring');
    expect(activeCliState([])).toBeNull();
    expect(activeCliState(undefined)).toBeNull();
  });

  it('facebookStatus: chưa có / cần đăng nhập lại / dừng khẩn / đang đọc', () => {
    const fb = (status: 'active' | 'needs_login' | 'paused' | 'pending_login' | 'revoked') => ({ platform: 'facebook', status });
    expect(facebookStatus({ accounts: undefined })).toBe('not_connected');
    expect(facebookStatus({ accounts: [] })).toBe('not_connected');
    expect(facebookStatus({ accounts: [fb('revoked')] })).toBe('not_connected');
    expect(facebookStatus({ accounts: [fb('active')] })).toBe('running');
    expect(facebookStatus({ accounts: [fb('active'), fb('needs_login')] })).toBe('needs_boss');
    expect(facebookStatus({ accounts: [fb('paused')] })).toBe('needs_boss');
    expect(facebookStatus({ accounts: [fb('active')], halted: true })).toBe('needs_boss');
  });

  it('hubStatus: ok / sắp hết hạn / hết hạn / lỗi / tắt', () => {
    expect(hubStatus(undefined)).toBe('not_connected');
    expect(hubStatus({ status: 'ok', configured: true })).toBe('running');
    expect(hubStatus({ status: 'expiring', configured: true })).toBe('needs_boss');
    expect(hubStatus({ status: 'expired', configured: true })).toBe('needs_boss');
    expect(hubStatus({ status: 'error', configured: true })).toBe('needs_boss');
    expect(hubStatus({ status: 'off', configured: false })).toBe('not_connected');
    // Đã điền địa chỉ/token nhưng còn tắt (chờ Kiểm tra) → việc Sếp đang làm dở.
    expect(hubStatus({ status: 'off', configured: true })).toBe('needs_boss');
    expect(hubStatus({ status: 'ok', configured: false })).toBe('not_connected');
  });

  it('mcpStatus: không máy chủ bật / có máy lỗi / ổn; đếm máy chủ đang bật', () => {
    expect(mcpStatus(undefined)).toBe('not_connected');
    expect(mcpStatus([{ is_enabled: false, health: 'healthy' }])).toBe('not_connected');
    expect(mcpStatus([{ is_enabled: true, health: 'healthy' }])).toBe('running');
    expect(mcpStatus([{ is_enabled: true, health: 'healthy' }, { is_enabled: true, health: 'error' }])).toBe('needs_boss');
    expect(mcpStatus([{ is_enabled: true, health: 'healthy' }, { is_enabled: false, health: 'error' }])).toBe('running');
    expect(mcpEnabledCount([{ is_enabled: true }, { is_enabled: false }, { is_enabled: true }])).toBe(2);
  });

  it('orderChannels: Zalo · WhatsApp · Telegram rồi kênh khác theo thứ tự API', () => {
    const t = (type: string) => ({ type }) as Pick<Channel, 'type'>;
    expect(orderChannels([t('linkedin'), t('telegram'), t('whatsapp'), t('zalo'), t('signal')]).map((c) => c.type)).toEqual([
      'zalo', 'whatsapp', 'telegram', 'linkedin', 'signal',
    ]);
  });
});

function channel(type: string, name: string, state: ChannelState): Channel {
  return {
    type: type as Channel['type'], name, installed: state !== 'not_installed', id: null, state, account_label: null,
    started_at: state === 'active' ? new Date(Date.now() - 3600_000).toISOString() : null, groups_listening: state === 'active' ? 3 : 0,
    outbound_queued: 0, last_heartbeat_at: null, stats: null, qr: null,
  };
}

const CHANNELS: Channel[] = [
  channel('telegram', 'Telegram', 'not_installed'),
  channel('whatsapp', 'WhatsApp', 'active'),
  channel('zalo', 'Zalo', 'expired'),
];

function me(role: RoleCode) {
  return {
    id: 'u', email: `${role}@genesis.local`, display_name: 'Anh Nguyễn Văn A (Chủ)', role: { code: role, name: role },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
    addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: permissionsOf(role),
  };
}

function body(url: string): unknown {
  if (url.includes('/providers/credentials')) return [];
  if (url.includes('/providers')) return [{ id: 'p1', kind: 'gemini', name: 'Gemini', endpoint: null, failover_rank: 1, enabled: true, auth_state: 'ok', keys: [], models: [] }];
  if (url.includes('/cli/profiles')) return [];
  if (url.includes('/setup/follow-up')) return [{ n: 4, done: true }];
  if (url.includes('/channels')) return CHANNELS;
  if (url.includes('/social/accounts')) return { items: [] };
  if (url.includes('/social/status')) return { halted: false };
  if (url.includes('/hub/link')) {
    return { configured: false, enabled: false, status: 'off', server_id: null, endpoint: null, has_token: false, allow_public_network: false, token_expires_at: null, days_left: null, last_ok_at: null, last_error: null, health: null };
  }
  if (url.includes('/mcp/servers')) return [{ id: 's1', name: 'ERP', transport: 'stdio', endpoint: 'x', has_auth: false, is_enabled: true, health: 'healthy', note: null, allow_public_network: false, tool_count: 3, exposed_count: 1 }];
  return [];
}

function renderPage(role: RoleCode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  qc.setQueryData(qk.me, me(role));
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/connections']}>
        <ConnectionsScreen />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Thẻ = <section aria-label> (Panel) hoặc <article aria-label="Kênh …"> (ChannelCard). */
async function card(name: string | RegExp) {
  return screen.findByRole(typeof name === 'string' && name.startsWith('Kênh') ? 'article' : 'region', { name });
}

async function expectOnePillOneAction(el: HTMLElement, status?: string) {
  await within(el).findByText(/Đang chạy|Cần Sếp xử lý|Chưa nối/, { selector: '.conn-pill' });
  const pills = el.querySelectorAll('[data-status]');
  expect(pills).toHaveLength(1);
  if (status) expect(pills[0]).toHaveAttribute('data-status', status);
  expect(el.querySelectorAll('[data-main-action]')).toHaveLength(1);
}

beforeEach(() => {
  vi.stubGlobal('WebSocket', undefined);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(body(String(input))), { status: 200, headers: { 'Content-Type': 'application/json' } })),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Trang Kết nối', () => {
  it('Owner: Bộ não AI, Zalo, WhatsApp, Telegram, Facebook, Gen-hub, MCP — mỗi thẻ 1 viên + 1 nút chính, đúng thứ tự', async () => {
    renderPage('owner');
    const brain = await card('Bộ não AI');
    await expectOnePillOneAction(brain, 'running');
    // v0.1.68 (F029): Owner: nút chính "Thêm / kiểm tra nguồn AI" → Thiết lập nguồn AI; tab Cài đặt chỉ là liên kết phụ.
    expect(within(brain).getByRole('link', { name: /Thêm \/ kiểm tra nguồn AI/ })).toHaveAttribute('href', '/setup?buoc=nguon-ai');
    expect(within(brain).getByRole('link', { name: /Mở Bộ não AI/ })).toHaveAttribute('href', '/system?tab=brain');

    const zalo = await card('Kênh Zalo');
    await expectOnePillOneAction(zalo, 'needs_boss');
    expect(within(zalo).getByText('Cần Sếp xử lý')).toBeInTheDocument();
    await expectOnePillOneAction(await card('Kênh WhatsApp'), 'running');
    // v0.1.68 (F060/F135): thẻ kênh "Telegram — Chưa có trong bản này" đã ẩn (đã có thẻ Telegram thật bên dưới).
    expect(screen.queryByRole('article', { name: 'Kênh Telegram' })).toBeNull();
    expect(screen.queryByText('Chưa có trong bản này')).toBeNull();

    const fb = await card('Facebook');
    await expectOnePillOneAction(fb, 'not_connected');
    expect(within(fb).getByRole('link', { name: /Mở Facebook/ })).toHaveAttribute('href', '/social');
    await expectOnePillOneAction(await card('Gen-hub'), 'not_connected');
    const mcp = await card('MCP');
    await expectOnePillOneAction(mcp, 'running');
    expect(within(mcp).getByText('1 máy chủ đang bật')).toBeInTheDocument();
    expect(within(mcp).getByRole('link', { name: /Mở MCP Hub/ })).toHaveAttribute('href', '/mcp');

    // Thứ tự trên trang: Bộ não AI · Zalo · WhatsApp · (thẻ Telegram thật) · Facebook · Gen-hub · MCP.
    const order = ['Bộ não AI', 'Kênh Zalo', 'Kênh WhatsApp', 'Facebook', 'Gen-hub', 'MCP'];
    const all = Array.from(document.querySelectorAll<HTMLElement>('section[aria-label], article[aria-label]'))
      .map((e) => e.getAttribute('aria-label'))
      .filter((l): l is string => !!l && order.includes(l));
    expect(all).toEqual(order);
    // Hai thẻ tài khoản CLI ở mục Bộ não AI (#brain); thẻ Gen-hub có neo #genhub.
    expect(document.getElementById('brain')).toContainElement(brain);
    expect(document.getElementById('genhub')).toContainElement(await card('Gen-hub'));
    expect(document.body.textContent).not.toContain('[object Object]');
  });

  it('Auditor (không phải Owner): không có thẻ Facebook; kênh không có nút thao tác; Bộ não AI không có viên trạng thái', async () => {
    renderPage('auditor');
    const brain = await card('Bộ não AI');
    // Auditor không đọc được follow-up (chưa chọn model) ⇒ không hiện viên để khỏi báo "Đang chạy" sai.
    expect(brain.querySelectorAll('[data-status]')).toHaveLength(0);
    expect(brain).not.toHaveTextContent('bên dưới');
    await card('Kênh Zalo');
    expect(screen.queryByRole('region', { name: 'Facebook' })).not.toBeInTheDocument();
    const zalo = await card('Kênh Zalo');
    expect(zalo.querySelectorAll('[data-status]')).toHaveLength(1);
    expect(within(zalo).queryByRole('button', { name: /Quét lại QR/ })).not.toBeInTheDocument();
  });

  it('lỗi tải kênh → CardError (chuỗi thân thiện + Chi tiết kỹ thuật), không render object', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/channels')) {
          return new Response(JSON.stringify({ status: 500, code: 'INTERNAL', title: 'Lỗi máy chủ', error_id: 'e-1', detail: { nested: true } }), {
            status: 500,
            headers: { 'Content-Type': 'application/problem+json' },
          });
        }
        return new Response(JSON.stringify(body(url)), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }),
    );
    renderPage('owner');
    expect(await screen.findByText(/Lỗi máy chủ/)).toBeInTheDocument();
    expect(screen.getAllByText('Chi tiết kỹ thuật').length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toContain('[object Object]');
  });
});

// v0.1.44: thẻ #telegram chỉ có khi đã biết là Owner — `me` về SAU danh sách kênh vẫn phải cuộn tới thẻ.
describe('Kết nối #telegram', () => {
  it('me về sau danh sách kênh → vẫn cuộn tới thẻ Telegram', async () => {
    const scrolled: string[] = [];
    const orig = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) {
      scrolled.push(this.id);
    };
    let releaseMe: () => void = () => {};
    const meReady = new Promise<void>((r) => {
      releaseMe = r;
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/auth/me')) await meReady;
        const data = url.includes('/auth/me') ? me('owner') : url.includes('/notify/telegram') ? { configured: false } : body(url);
        return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }),
    );
    try {
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
      render(
        <QueryClientProvider client={qc}>
          <MemoryRouter initialEntries={['/connections#telegram']}>
            <ConnectionsScreen />
          </MemoryRouter>
        </QueryClientProvider>,
      );
      await card('Kênh Zalo'); // danh sách kênh đã tải xong
      await new Promise((r) => setTimeout(r, 120));
      expect(document.getElementById('telegram')).toBeNull();
      await act(async () => {
        releaseMe();
      });
      await waitFor(() => expect(document.getElementById('telegram')).not.toBeNull());
      await waitFor(() => expect(scrolled).toContain('telegram'));
    } finally {
      Element.prototype.scrollIntoView = orig;
    }
  });
});
