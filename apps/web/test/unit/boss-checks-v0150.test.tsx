import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { BossCheck, BossCheckKey, BossOverview } from '@gen-harness/contracts';
import { BossChecksPage } from '../../src/guide/BossChecksPage';
import { KHO_WRITE_STEPS, hubWriteHint, hubWriteLine, hubWriteScopeOf, resultOf } from '../../src/guide/bossChecksModel';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';
import { createMock } from '../mock-boss-checks';
import type { P2Ctx } from '../mock-phase2';

/**
 * v0.1.50 (F-81, QD-18) — dòng 9 "Gen ghi Kho" (không bắt buộc, KHÔNG có nút Kiểm tra): Đạt khi kho_write pass; chưa đạt → hướng dẫn
 * 2 bước (tick quyền ghi ở Gen-hub + Kiểm tra; duyệt đề xuất PHIEN đầu tiên); dòng Gen-hub hiện write_scopes từ detail nếu có;
 * v0.1.55: `required_total` do máy chủ trả (chỉ dòng 0 "nguồn AI" bắt buộc ⇒ 1). Máy chủ cũ (không có dòng 9 / kho_write) → không hiện dòng chết.
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
  jev: null, telegram: null, remote_access: null, facebook_reply: null, ai_source: null,
};
const ROWS: BossOverview['rows'] = [
  { row: 0, key: 'ai', title: 'Có ít nhất 1 nguồn AI chạy được', optional: false, checks: ['ai_source'], done: false },
  { row: 1, key: 'hub', title: 'Nối Gen-hub', optional: true, checks: ['hub'], done: false },
  { row: 2, key: 'facebook', title: 'Kết nối Facebook', optional: true, checks: ['facebook'], done: false },
  { row: 3, key: 'agy', title: 'Google', optional: true, checks: ['agy_login', 'agy_call'], done: false },
  { row: 4, key: 'claude', title: 'Claude Code', optional: true, checks: ['claude_login', 'claude_call'], done: false },
  { row: 5, key: 'jev', title: 'Jev', optional: true, checks: ['jev'], done: false },
  { row: 6, key: 'telegram', title: 'Telegram (báo động & bản tin)', optional: true, checks: ['telegram'], done: false },
  { row: 7, key: 'remote', title: 'Truy cập từ xa', optional: true, checks: ['remote_access'], done: false },
  { row: 8, key: 'facebook_reply', title: 'Facebook trả lời', optional: true, checks: ['facebook_reply'], done: false },
  { row: 9, key: 'kho_write', title: 'Gen ghi Kho', optional: true, checks: ['kho_write'], done: false },
];

const pass = (key: BossCheckKey, detail: BossCheck['detail'] = {}): BossCheck => ({
  key, status: 'pass', error_code: null, message: null, detail, checked_at: '2026-10-09T07:05:00Z', runs: 1,
});

function setup(opts: { kho?: BossCheck | null; hub?: BossCheck | null; withRow?: boolean; withKey?: boolean } = {}) {
  const calls: string[] = [];
  const rows = (opts.withRow ?? true) ? ROWS.map((r) => (r.row === 9 ? { ...r, done: opts.kho?.status === 'pass' } : r)) : ROWS.slice(0, 9);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(url), 'http://x').pathname.replace('/api/v1', '');
      calls.push(`${init?.method ?? 'GET'} ${path}`);
      if (path === '/boss-checks') {
        const results = { ...EMPTY, hub: opts.hub ?? null, ...((opts.withKey ?? true) ? { kho_write: opts.kho ?? null } : {}) };
        return json(200, { rows, results, required_done: 0, required_total: 1 });
      }
      if (path === '/social/accounts') return json(200, { items: [] });
      if (path === '/cli/profiles') return json(200, []);
      if (path === '/providers') return json(200, []);
      if (path === '/hub/link') return json(200, { configured: false, enabled: false, status: 'off', has_token: false, endpoint: null, allow_public_network: false });
      return json(404, { code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
  return calls;
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

describe('Việc Sếp cần làm — dòng 9 Gen ghi Kho', () => {
  it('chưa đạt: "Không bắt buộc", "Chưa kiểm", hướng dẫn 2 bước, nút mở Kết nối › Gen-hub, KHÔNG có nút Kiểm tra', async () => {
    const calls = setup();
    renderPage();
    expect(await screen.findByText('Đã đạt 0/1 dòng bắt buộc')).toBeInTheDocument();
    const row = screen.getByRole('region', { name: 'Gen ghi Kho' });
    expect(within(row).getByText('Không bắt buộc')).toBeInTheDocument();
    expect(within(row).getByText('09')).toBeInTheDocument();
    expect(within(row).getByTestId('boss-result')).toHaveTextContent('Chưa kiểm');
    const steps = within(row).getAllByRole('listitem');
    expect(steps).toHaveLength(2);
    expect(steps[0].textContent).toContain('tick quyền ghi Kho (tạo và sửa bản ghi — tên trong Gen-hub: kho_create, kho_update) cho token của Gen-Harness');
    expect(steps[0].textContent).toContain('bấm Kiểm tra');
    expect(steps[1].textContent).toContain('đề xuất ghi Phiên đầu tiên');
    expect(steps[1].textContent).toContain('Xác nhận và ghi Kho');
    expect(steps.map((s) => s.textContent)).toEqual([...KHO_WRITE_STEPS]);
    const open = within(row).getByRole('link', { name: /Mở Kết nối › Gen-hub/ });
    expect(open).toHaveAttribute('href', '/connections#genhub');
    expect(within(row).queryByRole('button')).toBeNull(); // không có nút Kiểm tra / chạy
    expect(calls.some((c) => c.startsWith('POST'))).toBe(false);
    expect(document.body.textContent).not.toContain('[object Object]');
  });

  it('Đạt khi kho_write pass: "Đạt" + thời điểm, dòng Xong, không còn hướng dẫn / nút mở Gen-hub', async () => {
    setup({ kho: pass('kho_write') });
    renderPage();
    const row = await screen.findByRole('region', { name: 'Gen ghi Kho' });
    expect(within(row).getByTestId('boss-result').textContent).toMatch(/^Đạt · \d\d:\d\d \d\d\/\d\d$/);
    expect(within(row).getByText('Xong')).toBeInTheDocument();
    expect(within(row).queryByTestId('boss-kho-steps')).toBeNull();
    expect(within(row).queryByRole('link', { name: /Mở Kết nối › Gen-hub/ })).toBeNull();
  });

  it('kho_write lỗi (hiếm: bản ghi cũ) → vẫn là "Lỗi" câu thân thiện, chưa Xong, còn hướng dẫn', async () => {
    setup({ kho: { key: 'kho_write', status: 'fail', error_code: 'HUB_WRITE_MISSING', message: 'thiếu quyền ghi Kho', detail: {}, checked_at: '2026-10-09T07:05:00Z', runs: 1 } });
    renderPage();
    const row = await screen.findByRole('region', { name: 'Gen ghi Kho' });
    expect(within(row).getByTestId('boss-result').textContent).toMatch(/^Lỗi · /);
    expect(within(row).queryByText('Xong')).toBeNull();
    expect(within(row).getByTestId('boss-kho-steps')).toBeInTheDocument();
  });

  it('máy chủ cũ không có dòng 9 → không hiện dòng chết; có dòng mà thiếu khoá kho_write → "Chưa kiểm", không lỗi', async () => {
    setup({ withRow: false });
    const first = renderPage();
    expect(await screen.findByText('Đã đạt 0/1 dòng bắt buộc')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Gen ghi Kho' })).toBeNull();
    first.unmount();
    vi.unstubAllGlobals();
    queryClient.clear();
    setup({ withKey: false });
    renderPage();
    const row = await screen.findByRole('region', { name: 'Gen ghi Kho' });
    expect(within(row).getByTestId('boss-result')).toHaveTextContent('Chưa kiểm');
  });

  it('dòng Gen-hub chưa đạt: hướng dẫn tạo token KHÔNG còn "token chỉ đọc" — quyền đọc Kho, tuỳ chọn tick kho_create, kho_update', async () => {
    setup({});
    renderPage();
    const hubRow = await screen.findByRole('region', { name: 'Nối Gen-hub' });
    expect(hubRow).toHaveTextContent('tạo token 90 ngày có quyền đọc Kho (muốn Gen ghi Kho thì tick thêm kho_create, kho_update)');
    expect(hubRow).not.toHaveTextContent('token chỉ đọc');
  });

  it('dòng Gen-hub hiện write_scopes từ detail (Đạt): Có không cần nhắc; Chưa ⇒ hướng dẫn tick; vắng ⇒ ẩn', async () => {
    setup({ hub: pass('hub', { tools: 3, write_scopes: { kho: true } }) });
    const first = renderPage();
    const hubRow = await screen.findByRole('region', { name: 'Nối Gen-hub' });
    const yes = within(hubRow).getByTestId('boss-hub-write-scopes');
    expect(yes).toHaveTextContent('Quyền ghi Kho (không bắt buộc): Có');
    expect(yes).not.toHaveTextContent('tick quyền');
    first.unmount();
    vi.unstubAllGlobals();
    queryClient.clear();

    setup({ hub: pass('hub', { tools: 3, write_scopes: { kho: false } }) });
    const second = renderPage();
    const no = await within(await screen.findByRole('region', { name: 'Nối Gen-hub' })).findByTestId('boss-hub-write-scopes');
    expect(no).toHaveTextContent('Quyền ghi Kho (không bắt buộc): Chưa');
    expect(no).toHaveTextContent('tick quyền ghi Kho (tạo và sửa bản ghi — tên trong Gen-hub: kho_create, kho_update) cho token của Gen-Harness rồi bấm Kiểm tra lại');
    expect(no).toHaveTextContent('Không tick cũng được');
    second.unmount();
    vi.unstubAllGlobals();
    queryClient.clear();

    setup({ hub: pass('hub', { tools: 3 }) });
    renderPage();
    const hubRow3 = await screen.findByRole('region', { name: 'Nối Gen-hub' });
    expect(within(hubRow3).queryByTestId('boss-hub-write-scopes')).toBeNull();
  });
});

describe('bossChecksModel — quyền ghi Kho', () => {
  it('hubWriteScopeOf: chỉ boolean {kho}; còn lại ⇒ null; hubWriteLine / hubWriteHint', () => {
    const c = (detail: BossCheck['detail']) => pass('hub', detail);
    expect(hubWriteScopeOf(c({ write_scopes: { kho: true } }))).toBe(true);
    expect(hubWriteScopeOf(c({ write_scopes: { kho: false } }))).toBe(false);
    for (const bad of [{}, { write_scopes: null }, { write_scopes: [] }, { write_scopes: { kho: 'yes' } }, { write_scopes: { kho: 1 } }, { write_scopes: 'x' }]) {
      expect(hubWriteScopeOf(c(bad as BossCheck['detail']))).toBeNull();
    }
    expect(hubWriteScopeOf(null)).toBeNull();
    expect(hubWriteLine(true)).toBe('Quyền ghi Kho (không bắt buộc): Có');
    expect(hubWriteLine(false)).toBe('Quyền ghi Kho (không bắt buộc): Chưa');
    expect(hubWriteHint(true)).toBeNull();
    expect(hubWriteHint(false)).toMatch(/kho_create, kho_update/);
    expect(KHO_WRITE_STEPS).toHaveLength(2);
  });

  it('resultOf đọc kho_write như facebook_reply: vắng ⇒ null', () => {
    const base = { rows: [], required_done: 0, required_total: 1 };
    expect(resultOf({ ...base, results: { ...EMPTY } } as unknown as BossOverview, 'kho_write')).toBeNull();
    expect(resultOf({ ...base, results: { ...EMPTY, kho_write: pass('kho_write') } } as unknown as BossOverview, 'kho_write')?.status).toBe('pass');
    expect(resultOf(undefined, 'kho_write')).toBeNull();
  });
});

describe('mock-boss-checks — dòng 9', () => {
  function makeMock() {
    return createMock({
      fresh: true,
      emit: () => undefined,
      hubLink: () => ({ configured: true }) as never,
      hubTest: () => ({ ok: true, error: null, error_code: null, read_scopes: { calendar: true, mail: true, tasks: true, drive: true }, write_scopes: { kho: false }, write_missing: ['ghi Kho (kho_create, kho_update)'] }),
      socialAccounts: () => [],
      cliProfiles: () => [],
      activateCli: () => false,
      providers: () => [],
      onCliLogin: () => undefined,
      telegramTest: () => ({ status: 'fail', error_code: 'TELEGRAM_NOT_CONFIGURED', message: null, detail: {} }) as never,
    });
  }
  function call(mock: ReturnType<typeof makeMock>, method: string, path: string) {
    let out: { status: number; body: unknown } | null = null;
    const ctx = {
      method, path, url: new URL(`http://x/api/v1${path}`), body: {}, perms: {},
      reply: (status: number, b?: unknown) => ((out = { status, body: b }), true as const),
      problem: (status: number, code: string, title: string) => ((out = { status, body: { code, title } }), true as const),
      text: () => true as const, needPin: () => false, userLabel: 'Sếp', owner: true, role: 'owner',
    } as unknown as P2Ctx;
    expect(mock.handle(ctx)).toBe(true);
    return out as unknown as { status: number; body: Record<string, unknown> };
  }

  it('có dòng 9 tuỳ chọn, required_total 1 (chỉ dòng "ai"); POST run → 404; recordKhoWrite → Đạt; dòng hub kèm write_scopes', () => {
    const mock = makeMock();
    const ov0 = call(mock, 'GET', '/boss-checks').body as unknown as BossOverview;
    expect(ov0.rows.find((r) => r.row === 9)).toMatchObject({ key: 'kho_write', title: 'Gen ghi Kho', optional: true, done: false });
    expect(ov0.required_total).toBe(1);
    expect(ov0.results.kho_write).toBeNull();
    expect(call(mock, 'POST', '/boss-checks/kho_write/run').status).toBe(404);

    const hub = call(mock, 'POST', '/boss-checks/hub/run').body as unknown as BossCheck;
    expect(hub.status).toBe('pass');
    expect(hub.detail).toMatchObject({ write_scopes: { kho: false }, write_missing: ['ghi Kho (kho_create, kho_update)'] });

    (mock.hooks.recordKhoWrite as unknown as () => unknown)();
    const ov1 = call(mock, 'GET', '/boss-checks').body as unknown as BossOverview;
    expect(ov1.results.kho_write?.status).toBe('pass');
    expect(ov1.rows.find((r) => r.row === 9)?.done).toBe(true);
    expect(ov1.required_done).toBe(0); // dòng hub và dòng 9 đều tuỳ chọn — chỉ dòng 0 "ai" mới tính
  });
});
