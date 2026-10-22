/**
 * v0.1.68 (gói C1) — một danh sách việc, một đường tới "thêm nguồn AI":
 * F017 (đếm khoá API), F031/F138 (một cách đếm, nhãn "không bắt buộc", không nói "sẵn sàng" khi nguồn AI chưa chạy thật),
 * F029 (một đích AI_SOURCE_PATH), F039 (dải "Cần Sếp xử lý" luôn có đích làm sáng), F040 (mã chết), F060/F135 (một thẻ Telegram).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { BossOverview, Channel, SetupFollowUpItem } from '@gen-harness/contracts';
import { GuidePage } from '../../src/guide/GuidePage';
import * as bossModel from '../../src/guide/bossChecksModel';
import * as noModel from '../../src/screens/queue/NoModelBanner';
import { NeedsBossStrip } from '../../src/screens/queue/NeedsBossStrip';
import { SetupFollowUp } from '../../src/screens/queue/SetupFollowUp';
import { ConnectionsScreen } from '../../src/screens/connections/ConnectionsScreen';
import { isKeySource, visibleChannels } from '../../src/screens/connections/connectionsModel';
import { AI_SOURCE_PATH } from '../../src/setup/steps';
import { qk } from '../../src/lib/queries';
import { COACH_PREFS_KEY } from '../../src/gen/coachModel';
import { permissionsOf } from '../mock-api';

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
const ME = {
  id: 'u', email: 'owner@genesis.local', display_name: 'Anh Cơ', role: { code: 'owner', name: 'owner' },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: permissionsOf('owner'),
};

afterEach(() => vi.unstubAllGlobals());

describe('F040 — mã chết đã gỡ', () => {
  it('NoModelBanner (hàm) và switchesOf không còn; NoModelNotice còn', () => {
    expect('NoModelBanner' in noModel).toBe(false);
    expect(typeof noModel.NoModelNotice).toBe('function');
    expect('switchesOf' in bossModel).toBe(false);
  });
});

describe('F017/F060/F135 — Kết nối', () => {
  const ch = (type: Channel['type'], state: Channel['state']): Channel => ({
    type, name: { zalo: 'Zalo', whatsapp: 'WhatsApp', telegram: 'Telegram', linkedin: 'LinkedIn' }[type], installed: state !== 'not_installed', id: null, state, account_label: null, started_at: null,
    groups_listening: 0, outbound_queued: 0, last_heartbeat_at: null, stats: null, qr: null,
  });

  it('isKeySource chỉ nhận nguồn dán khoá (không CLI, không Jev)', () => {
    expect(['gemini', 'deepseek', 'openai_compat'].every((kind) => isKeySource({ kind: kind as 'gemini' }))).toBe(true);
    expect(isKeySource({ kind: 'antigravity_cli' })).toBe(false);
    expect(isKeySource({ kind: 'claude_code_cli' })).toBe(false);
    expect(isKeySource({ kind: 'system_one' })).toBe(false);
  });

  it('visibleChannels: ẩn kênh Telegram (có thẻ riêng) và LinkedIn chỉ-danh-tính; giữ Zalo/WhatsApp', () => {
    const got = visibleChannels([ch('zalo', 'active'), ch('whatsapp', 'logged_out'), ch('telegram', 'not_installed'), ch('linkedin', 'identity_only')]);
    expect(got.map((c) => c.type)).toEqual(['zalo', 'whatsapp']);
    expect(visibleChannels([ch('linkedin', 'active')]).map((c) => c.type)).toEqual(['linkedin']);
  });

  function renderConnections(providers: unknown[]) {
    vi.stubGlobal('WebSocket', undefined);
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/providers/credentials')) return json([]);
        if (url.includes('/providers')) return json(providers);
        if (url.includes('/cli/profiles')) return json([{ id: 'a', email: 'a@x.vn', plan_label: null, active: true, expires_at: null, state: 'ok' }]);
        if (url.includes('/setup/follow-up')) return json([{ n: 4, done: true }]);
        if (url.includes('/channels')) return json([ch('telegram', 'not_installed'), ch('zalo', 'active'), ch('linkedin', 'identity_only')]);
        if (url.includes('/social/accounts')) return json({ items: [] });
        if (url.includes('/social/status')) return json({ halted: false });
        if (url.includes('/notify/telegram')) return json({ configured: false });
        if (url.includes('/hub/link')) return json({ configured: false, enabled: false, status: 'off', server_id: null, endpoint: null, has_token: false, allow_public_network: false, token_expires_at: null, days_left: null, last_ok_at: null, last_error: null, health: null });
        return json([]);
      }),
    );
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    qc.setQueryData(qk.me, ME);
    return render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={['/connections']}>
          <ConnectionsScreen />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it('thẻ Bộ não AI: chỉ đếm khoá API (CLI không tính), nút chính "Thêm / kiểm tra nguồn AI" → màn Thiết lập nguồn AI', async () => {
    const prov = (id: string, kind: string, enabled = true) => ({ id, kind, name: id, endpoint: null, failover_rank: 1, enabled, auth_state: 'ok', keys: [], models: [] });
    renderConnections([prov('g', 'gemini'), prov('d', 'deepseek', false), prov('agy', 'antigravity_cli'), prov('cc', 'claude_code_cli')]);
    const brain = await screen.findByRole('region', { name: 'Bộ não AI' });
    await within(brain).findByText(/1\/2 nguồn khoá API đang bật/);
    const main = brain.querySelectorAll('[data-main-action]');
    expect(main).toHaveLength(1);
    expect(main[0]).toHaveAttribute('href', AI_SOURCE_PATH);
    expect(main[0]).toHaveTextContent('Thêm / kiểm tra nguồn AI');
    expect(within(brain).getByRole('link', { name: 'Mở Bộ não AI' })).toHaveAttribute('href', '/system?tab=brain');
  });

  it('chỉ có CLI: không nói "1/1 nguồn khoá API" (F130)', async () => {
    renderConnections([{ id: 'agy', kind: 'antigravity_cli', name: 'agy', endpoint: null, failover_rank: 1, enabled: true, auth_state: 'ok', keys: [], models: [] }]);
    const brain = await screen.findByRole('region', { name: 'Bộ não AI' });
    await within(brain).findByText(/Chưa có nguồn khoá API/);
    expect(brain.textContent).not.toMatch(/\d\/\d nguồn khoá API/);
  });

  it('lưới kênh: không còn thẻ Telegram "chưa có trong bản này" cạnh thẻ Telegram thật; không thẻ LinkedIn ngõ cụt', async () => {
    renderConnections([]);
    await screen.findByRole('article', { name: 'Kênh Zalo' });
    expect(screen.queryByRole('article', { name: 'Kênh Telegram' })).toBeNull();
    expect(screen.queryByRole('article', { name: 'Kênh LinkedIn' })).toBeNull();
    expect(screen.queryByText('Chưa có trong bản này')).toBeNull();
    expect((await screen.findAllByRole('region', { name: /Telegram|Báo động/ })).length).toBeGreaterThan(0);   // chỉ còn thẻ Telegram thật
  });
});

describe('F031/F138 — một cách đếm', () => {
  const item = (n: number, done: boolean): SetupFollowUpItem => ({ n, key: `k${n}`, title: `Bước ${n}`, status: done ? 'done' : 'skipped', done });
  const ALL = [5, 6, 7, 8, 9, 10, 11, 13, 14];
  const boss = (done: number, total = 1): BossOverview => ({ rows: [], results: {} as BossOverview['results'], required_done: done, required_total: total });

  function renderGuide(doneN: number[], b: BossOverview | null) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    qc.setQueryData(['setup', 'follow-up'], ALL.map((n) => item(n, doneN.includes(n))));
    if (b) qc.setQueryData(['boss-checks'], b);
    qc.setQueryData(qk.me, ME);
    vi.stubGlobal('fetch', vi.fn(async () => json(b ?? {})));
    return render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <GuidePage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it('đủ 9 việc tuỳ chọn mà nguồn AI chưa đạt ⇒ KHÔNG nói "Đã xong tất cả — sẵn sàng"; chỉ sang Việc Sếp cần làm', () => {
    renderGuide(ALL, boss(0));
    expect(screen.queryByText(/Đã xong tất cả/)).toBeNull();
    expect(screen.getByText('Đã xong 9/9 việc')).toBeInTheDocument();
    expect(screen.getByText(/nguồn AI chưa chạy thật/)).toBeInTheDocument();
  });

  it('đủ 9 việc + nguồn AI đạt ⇒ "Đã xong tất cả"; mọi thẻ ghi "Không bắt buộc"', () => {
    renderGuide(ALL, boss(1));
    expect(screen.getByText(/Đã xong tất cả — hệ thống sẵn sàng làm việc/)).toBeInTheDocument();
    expect(screen.getAllByText('Không bắt buộc')).toHaveLength(9);
  });

  it('chưa biết kết quả nguồn AI (đang tải) ⇒ không khẳng định "sẵn sàng"', () => {
    renderGuide(ALL, null);
    expect(screen.queryByText(/Đã xong tất cả/)).toBeNull();
  });

  it('thẻ "Việc thiết lập tiếp": nói việc TUỲ CHỌN (không phải "còn lại" bắt buộc) và trỏ về Việc Sếp cần làm', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    qc.setQueryData(['setup', 'follow-up'], [item(5, false), item(13, false), item(14, false)]);
    qc.setQueryData(qk.me, ME);
    qc.setQueryData(COACH_PREFS_KEY, { enabled: true, bell: true, lessons_per_day: 1, quiet_start: 21, quiet_end: 7, snooze_until: null, followup_snoozed_until: null, dismissed: [] });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <SetupFollowUp />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByText(/3 việc tuỳ chọn/)).toBeInTheDocument();
    expect(screen.queryByText(/việc thiết lập còn lại/)).toBeNull();
    expect(screen.getByText(/không bắt buộc/i)).toBeInTheDocument();
  });
});

describe('F039 — dải "Cần Sếp xử lý" luôn có đích làm sáng', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes('/auth/me')) return json(ME);
        if (url.includes('/system/health')) {
          return json({ checked_at: new Date().toISOString(), overall: 'ok', issues: [] });
        }
        if (url.includes('/setup/follow-up')) return json([{ n: 4, key: 'brain', title: 'Bộ não AI', status: 'done', done: true }]);
        if (url.includes('/boss-checks')) return json({ rows: [], results: {}, required_done: 1, required_total: 1 });
        return json([]);
      }),
    );
  });

  it('lúc khoẻ vẫn có phần tử data-gen-target="overview.needs_boss" (một dòng báo yên tâm)', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(qk.me, ME);
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <NeedsBossStrip />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const calm = await screen.findByTestId('needs-boss-calm');
    expect(calm).toHaveAttribute('data-gen-target', 'overview.needs_boss');
    expect(calm).toHaveTextContent('Không có việc nào cần Sếp xử lý');
    await waitFor(() => expect(document.querySelectorAll('[data-gen-target="overview.needs_boss"]')).toHaveLength(1));
    expect(screen.queryByTestId('needs-boss')).toBeNull();
  });
});
