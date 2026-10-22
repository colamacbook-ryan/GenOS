import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { BossCheck, BossCheckKey, BossOverview, Provider } from '@gen-harness/contracts';
import { BossChecksPage } from '../../src/guide/BossChecksPage';
import { GuidePage } from '../../src/guide/GuidePage';
import { aiSourceOkText } from '../../src/guide/bossChecksModel';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';
import { createMock } from '../mock-boss-checks';
import type { P2Ctx } from '../mock-phase2';

/**
 * v0.1.55 (G2) — "Việc Sếp cần làm" chỉ có MỘT dòng bắt buộc: dòng 0 "Có ít nhất 1 nguồn AI chạy được" (ai_source). Số dòng bắt
 * buộc (x/N) lấy từ máy chủ (`required_total`) — web không ghi cứng. Mọi dòng kết nối khác "Không bắt buộc".
 */

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const me = {
  id: 'u', email: 'x@genesis.local', display_name: 'Anh Cơ', role: { code: 'owner', name: 'owner' },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
  permissions: { 'system.read': 'all', 'system.manage': 'all' },
};

const EMPTY: Record<string, BossCheck | null> = {
  hub: null, facebook: null, agy_login: null, agy_call: null, agy_switch: null, claude_login: null, claude_call: null,
  jev: null, telegram: null, remote_access: null, facebook_reply: null, kho_write: null, ai_source: null,
};
const ROWS: BossOverview['rows'] = [
  { row: 0, key: 'ai', title: 'Có ít nhất 1 nguồn AI chạy được', optional: false, checks: ['ai_source'], done: false },
  { row: 1, key: 'hub', title: 'Gen-hub', optional: true, checks: ['hub'], done: false },
  { row: 2, key: 'facebook', title: 'Facebook', optional: true, checks: ['facebook'], done: false },
  { row: 3, key: 'agy', title: 'Google / Antigravity', optional: true, checks: ['agy_login', 'agy_call'], done: false },
  { row: 4, key: 'claude', title: 'Claude Code CLI', optional: true, checks: ['claude_login', 'claude_call'], done: false },
  { row: 5, key: 'jev', title: 'Jev', optional: true, checks: ['jev'], done: false },
  { row: 6, key: 'telegram', title: 'Telegram (báo động & bản tin)', optional: true, checks: ['telegram'], done: false },
  { row: 7, key: 'remote', title: 'Truy cập từ xa', optional: true, checks: ['remote_access'], done: false },
  { row: 8, key: 'facebook_reply', title: 'Facebook trả lời', optional: true, checks: ['facebook_reply'], done: false },
  { row: 9, key: 'kho_write', title: 'Gen ghi Kho', optional: true, checks: ['kho_write'], done: false },
];
const check = (key: BossCheckKey, status: BossCheck['status'], extra: Partial<BossCheck> = {}): BossCheck => ({
  key, status, error_code: null, message: null, detail: {}, checked_at: '2026-10-10T07:05:00Z', runs: 1, ...extra,
});

interface World {
  results: Record<string, BossCheck | null>;
  total: number;
  done: number;
  run: (key: string) => BossCheck;
  rows: BossOverview['rows'];
}
function setup(w: Partial<World> = {}) {
  const world: World = { results: { ...EMPTY }, total: 1, done: 0, run: (k) => check(k as BossCheckKey, 'pass'), rows: ROWS, ...w };
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(url), 'http://x').pathname.replace('/api/v1', '');
      const method = init?.method ?? 'GET';
      calls.push(`${method} ${path}`);
      if (path === '/boss-checks' && method === 'GET') {
        const aiDone = world.results.ai_source?.status === 'pass';
        const rows = world.rows.map((r) => (r.key === 'ai' ? { ...r, done: aiDone } : r));
        return json(200, { rows, results: world.results, required_done: aiDone ? Math.max(world.done, 1) : world.done, required_total: world.total });
      }
      const run = /^\/boss-checks\/([a-z_]+)\/run$/.exec(path);
      if (run && method === 'POST') {
        const c = world.run(run[1]);
        if (!c.transient) world.results[c.key] = c;
        return json(200, c);
      }
      if (path === '/hub/link') return json(200, { configured: false, enabled: false, status: 'off', has_token: false, endpoint: null, allow_public_network: false });
      if (path === '/social/accounts') return json(200, { items: [] });
      if (path === '/cli/profiles') return json(200, []);
      if (path === '/providers') return json(200, []);
      return json(404, { code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
  return { world, calls };
}
function renderPage() {
  queryClient.setQueryData(qk.me, me);
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/guide/viec-sep']}>
        <BossChecksPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
beforeEach(() => queryClient.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  queryClient.clear();
});

describe('Việc Sếp cần làm — chỉ dòng "nguồn AI" bắt buộc (x/N động)', () => {
  it('0/1 khi chưa đạt; dòng 0 đứng đầu, bắt buộc (không chip), hiện ký hiệu AI; mọi dòng khác "Không bắt buộc"', async () => {
    setup();
    renderPage();
    expect(await screen.findByText('Đã đạt 0/1 dòng bắt buộc')).toBeInTheDocument();
    const regions = screen.getAllByRole('region');
    expect(regions[0].getAttribute('aria-label')).toBe('Có ít nhất 1 nguồn AI chạy được');
    expect(within(regions[0]).queryByText('Không bắt buộc')).toBeNull();
    expect(within(regions[0]).getByText('AI')).toBeInTheDocument();
    for (const r of regions.slice(1)) expect(within(r).getByText('Không bắt buộc'), r.getAttribute('aria-label') ?? '').toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\/6\b/);
    expect(document.body.textContent).not.toContain('Bảy việc');
    expect(screen.getByRole('progressbar', { name: 'Tiến độ việc Sếp cần làm' })).toHaveAttribute('aria-valuemax', '1');
  });

  it('N lấy từ máy chủ, không ghi cứng: required_total 3 ⇒ "Đã đạt 1/3 dòng bắt buộc"', async () => {
    setup({ total: 3, done: 1 });
    renderPage();
    expect(await screen.findByText('Đã đạt 1/3 dòng bắt buộc')).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuemax', '3');
  });

  it('bấm "Kiểm tra" ở dòng 0 ⇒ POST /boss-checks/ai_source/run (không PIN) ⇒ "Đạt" và 1/1', async () => {
    const { calls } = setup({ run: (k) => check(k as BossCheckKey, 'pass', { detail: { latency_ms: 120, probe_model: 'gemini-2.5-flash' }, checked_at: new Date().toISOString() }) });
    renderPage();
    const user = userEvent.setup();
    const ai = await screen.findByRole('region', { name: 'Có ít nhất 1 nguồn AI chạy được' });
    expect(within(ai).getByText('Chưa kiểm')).toBeInTheDocument();
    await user.click(within(ai).getByRole('button', { name: 'Kiểm tra' }));
    await waitFor(() => expect(within(ai).getByText(/^Đạt · \d\d:\d\d \d\d\/\d\d$/)).toBeInTheDocument());
    expect(calls).toContain('POST /boss-checks/ai_source/run');
    expect(await screen.findByText('Đã đạt đủ 1 dòng bắt buộc — nguồn AI chạy thật.')).toBeInTheDocument();
    expect(within(ai).getByText('Xong')).toBeInTheDocument();
    expect(within(ai).getByRole('link', { name: /Mở Thiết lập nguồn AI/ })).toHaveAttribute('href', '/setup?buoc=nguon-ai');
  });

  it('lỗi AI_NO_SOURCE ⇒ câu thân thiện + "Chi tiết kỹ thuật" có mã lỗi, không render object', async () => {
    setup({ run: (k) => check(k as BossCheckKey, 'fail', { error_code: 'AI_NO_SOURCE', message: 'x' }) });
    const { container } = renderPage();
    const user = userEvent.setup();
    const ai = await screen.findByRole('region', { name: 'Có ít nhất 1 nguồn AI chạy được' });
    await user.click(within(ai).getByRole('button', { name: 'Kiểm tra' }));
    expect(await within(ai).findByText(/Lỗi · Chưa có nguồn AI nào/)).toBeInTheDocument();
    expect(within(ai).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(within(ai).getByText('Mã lỗi AI_NO_SOURCE')).toBeInTheDocument();
    expect(container.innerHTML).not.toContain('[object Object]');
  });

  it('Đạt do máy chủ tự thấy lượt gọi model thật (via = model_calls) ⇒ nói rõ cách chứng minh', async () => {
    setup({ results: { ...EMPTY, ai_source: check('ai_source', 'pass', { runs: 0, detail: { via: 'model_calls', calls: 5 } }) } });
    renderPage();
    const ai = await screen.findByRole('region', { name: 'Có ít nhất 1 nguồn AI chạy được' });
    expect(await within(ai).findByText(/Đạt · Gen đã gọi model thật thành công/)).toBeInTheDocument();
    expect(aiSourceOkText(check('ai_source', 'pass', { detail: { via: 'claude_call' } }))).toMatch(/^Đạt · Claude Code đã gọi thử thành công/);
    expect(aiSourceOkText(check('ai_source', 'pass', { detail: { via: 'agy_call' } }))).toMatch(/^Đạt · Google \(Antigravity\) đã gọi thử/);
    expect(aiSourceOkText(check('ai_source', 'pass'))).toMatch(/^Đạt · \d\d:\d\d \d\d\/\d\d$/);
  });

  it('máy chủ cũ không có dòng "ai" ⇒ không hiện dòng chết; tổng 0 không chia cho 0', async () => {
    setup({ rows: ROWS.slice(1), total: 0 });
    renderPage();
    expect(await screen.findByText('Đã đạt đủ 0 dòng bắt buộc — nguồn AI chạy thật.')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Có ít nhất 1 nguồn AI chạy được' })).toBeNull();
  });
});

describe('Hướng dẫn thiết lập — thẻ Việc Sếp cần làm không liệt kê 6 dịch vụ', () => {
  it('chưa có số liệu ⇒ câu chung "chỉ cần ít nhất một nguồn AI"; không nhắc Telegram/Facebook/Claude/Google…', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => {
      const path = new URL(String(url), 'http://x').pathname.replace('/api/v1', '');
      if (path === '/boss-checks') return json(500, { code: 'X', title: 'lỗi' });
      if (path === '/setup/follow-up') return json(200, []);
      return json(404, {});
    }));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(['setup', 'follow-up'], []);
    qc.setQueryData(qk.me, me);
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <GuidePage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const link = await screen.findByRole('link', { name: /Việc Sếp cần làm — nguồn AI chạy thật/ });
    expect(link).toHaveTextContent('Chỉ cần ít nhất một nguồn AI chạy được');
    for (const word of ['Telegram', 'Facebook', 'Claude', 'Google', 'Truy cập từ xa', 'Gen-hub']) expect(link.textContent).not.toContain(word);
    expect(link.textContent).not.toMatch(/25 phút/);
  });
});

describe('mock-boss-checks — v0.1.55', () => {
  function makeMock(over: { providers?: () => Provider[]; cliProfiles?: (k: string) => never[] } = {}) {
    return createMock({
      fresh: true,
      emit: () => undefined,
      hubLink: () => ({ configured: true }) as never,
      hubTest: () => ({ ok: true, error: null, error_code: null }),
      socialAccounts: () => [],
      cliProfiles: over.cliProfiles ?? (() => []),
      activateCli: () => false,
      providers: over.providers ?? (() => []),
      onCliLogin: () => undefined,
      telegramTest: () => ({ status: 'fail', error_code: 'TELEGRAM_NOT_CONFIGURED', message: null, detail: {} }) as never,
    });
  }
  function call(mock: ReturnType<typeof makeMock>, method: string, path: string, body: Record<string, unknown> = {}) {
    let out: { status: number; body: unknown } | null = null;
    const ctx = {
      method, path, url: new URL(`http://x/api/v1${path}`), body, perms: {},
      reply: (status: number, b?: unknown) => ((out = { status, body: b }), true as const),
      problem: (status: number, code: string, title: string) => ((out = { status, body: { code, title } }), true as const),
      text: () => true as const, needPin: () => false, userLabel: 'Sếp', owner: true, role: 'owner',
    } as unknown as P2Ctx;
    expect(mock.handle(ctx)).toBe(true);
    return out as unknown as { status: number; body: Record<string, unknown> };
  }
  const overview = (mock: ReturnType<typeof makeMock>) => call(mock, 'GET', '/boss-checks').body as unknown as BossOverview;

  it('dòng 0 "ai" bắt buộc duy nhất ⇒ required_total 1; mọi dòng khác tuỳ chọn; agy không còn agy_switch', () => {
    const ov = overview(makeMock());
    expect(ov.required_total).toBe(1);
    expect(ov.rows.map((r) => [r.key, r.optional])).toEqual([
      ['ai', false], ['hub', true], ['facebook', true], ['agy', true], ['claude', true], ['jev', true], ['telegram', true], ['remote', true], ['facebook_reply', true], ['kho_write', true],
    ]);
    expect(ov.rows[0]).toMatchObject({ row: 0, title: 'Có ít nhất 1 nguồn AI chạy được', checks: ['ai_source'], done: false });
    expect(ov.rows.find((r) => r.key === 'agy')?.checks).toEqual(['agy_login', 'agy_call']);
    expect(ov.results.ai_source).toBeNull();
    expect(ov.required_done).toBe(0);
  });

  it('POST ai_source/run: không nguồn nào ⇒ AI_NO_SOURCE; có nhà cung cấp gọi thử OK có model ⇒ Đạt và 1/1; seedAi cũng đạt', () => {
    const none = makeMock();
    const fail = call(none, 'POST', '/boss-checks/ai_source/run').body as unknown as BossCheck;
    expect(fail).toMatchObject({ key: 'ai_source', status: 'fail', error_code: 'AI_NO_SOURCE' });
    expect(overview(none).required_done).toBe(0);

    const provider = { id: 'p', kind: 'gemini', enabled: true, auth_state: 'ok', models: [{ id: 'm', model_name: 'gemini-2.5-flash' }] } as unknown as Provider;
    const ok = makeMock({ providers: () => [provider] });
    const pass = call(ok, 'POST', '/boss-checks/ai_source/run').body as unknown as BossCheck;
    expect(pass).toMatchObject({ key: 'ai_source', status: 'pass', error_code: null });
    expect(JSON.stringify(pass)).not.toMatch(/token|secret|sk-/i);
    const ov = overview(ok);
    expect(ov.required_done).toBe(1);
    expect(ov.rows[0].done).toBe(true);

    const seeded = makeMock();
    (seeded.hooks.seedAi as unknown as () => unknown)();
    expect(overview(seeded).required_done).toBe(1);
  });

  it('Claude / Google gọi thử đạt ⇒ dòng 0 tự đạt (kết quả giả via claude_call / agy_call, runs 0); agy xong KHÔNG cần đổi 2 lần', () => {
    const profile = { id: 'c1', email: 'a@example.test', plan_label: null, active: true, expires_at: null, state: 'ok' } as never;
    const mock = makeMock({ cliProfiles: (k) => (k === 'claude_code_cli' ? [profile] : []) });
    expect(call(mock, 'POST', '/boss-checks/claude_call/run').status).toBe(200);
    const ov = overview(mock);
    expect(ov.results.ai_source).toMatchObject({ status: 'pass', runs: 0, detail: { via: 'claude_call' } });
    expect(ov.rows[0].done).toBe(true);
    expect(ov.required_done).toBe(1);

    const agyProfile = { id: 'a1', email: 'binh@genesis.vn', plan_label: null, active: true, expires_at: null, state: 'ok' } as never;
    const agy = makeMock({ cliProfiles: (k) => (k === 'antigravity_cli' ? [agyProfile] : []) });
    call(agy, 'POST', '/boss-checks/agy_call/run');
    const ovAgy = overview(agy);
    expect(ovAgy.rows.find((r) => r.key === 'agy')?.done).toBe(true); // chỉ cần Gọi thử, không đòi đổi qua lại
    expect(ovAgy.results.ai_source).toMatchObject({ detail: { via: 'agy_call' } });
  });
});

describe('không còn số 6 cứng cho "việc bắt buộc"', () => {
  const walk = (dir: string): string[] =>
    readdirSync(dir).flatMap((f) => {
      const p = join(dir, f);
      if (f === 'node_modules' || f === '__pycache__' || f === '.venv') return [];
      return statSync(p).isDirectory() ? walk(p) : /\.(tsx?|py)$/.test(f) ? [p] : [];
    });

  it("apps/web/src và apps/api/gh không còn 'required_total ?? 6' / '/6 việc'", () => {
    const roots = [resolve(__dirname, '../../src'), resolve(__dirname, '../../../api/gh')];
    const offenders: string[] = [];
    for (const root of roots) {
      for (const f of walk(root)) {
        const text = readFileSync(f, 'utf8');
        if (/required_total\s*\?\?\s*6/.test(text) || /\/6 việc/.test(text)) offenders.push(f);
      }
    }
    expect(offenders).toEqual([]);
  });
});
