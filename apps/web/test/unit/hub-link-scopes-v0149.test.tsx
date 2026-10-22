/**
 * v0.1.49 (QD-16) — Kết nối › Gen-hub: "Quyền đọc thêm (tuỳ chọn)" (lịch, mail, việc, Drive), báo thiếu quyền sau Kiểm tra
 * (thiếu quyền đọc KHÔNG làm Kiểm tra đỏ), dải bộ ngắt F-83; Việc Sếp cần làm: dòng phụ ở hàng Gen-hub.
 */
import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { BossCheck, BossOverview, HubLink, HubLinkTestResult } from '@gen-harness/contracts';
import { HubLinkCard } from '../../src/screens/mcp/HubLinkCard';
import {
  SCOPES_ENOUGH_TEXT,
  missingFromScopes,
  missingText,
  scopeRows,
  scopesKnown,
  scopesMessage,
} from '../../src/screens/mcp/mcpModel';
import { BossChecksPage } from '../../src/guide/BossChecksPage';
import { hubScopesHint, hubScopesLine, hubScopesOf } from '../../src/guide/bossChecksModel';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';
import { HEALTH_KINDS } from '../../src/screens/system/queries';
import { createMock } from '../mock-p4-mcp';
import type { P2Ctx } from '../mock-phase2';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const me = (role: string) => ({
  id: 'u', email: 'x@genesis.local', display_name: 'Anh Cơ', role: { code: role, name: role },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
  permissions: { 'system.read': 'all', 'system.manage': role === 'owner' ? 'all' : 'none' },
});

const SAVED: HubLink = {
  configured: true, enabled: true, status: 'ok', server_id: 's1', endpoint: 'https://hub.example.test/mcp', has_token: true,
  allow_public_network: true, token_expires_at: '2026-12-28T16:59:00Z', days_left: 80, last_ok_at: '2026-10-09T01:00:00Z', last_error: null, health: 'healthy',
};
const FULL = { calendar: true, mail: true, tasks: true, drive: true };
const NO_CAL_MAIL = { calendar: false, mail: false, tasks: true, drive: true };

const testOut = (over: Partial<HubLinkTestResult> = {}): HubLinkTestResult => ({
  ok: true, error: null, latency_ms: 240, exposed_tools: ['a__kho_tom_tat', 'a__kho_search', 'a__kho_find_by_id'], missing_tools: [],
  link: SAVED, ...over,
});

function stubHub(link: HubLink, test: HubLinkTestResult) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const u = String(url);
      const method = init?.method ?? 'GET';
      if (u.includes('/hub/link/test') && method === 'POST') return json(200, test);
      if (u.includes('/hub/link') && method === 'GET') return json(200, link);
      return json(404, { code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
}

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
  cleanup();
  vi.unstubAllGlobals();
});

describe('mcpModel — scopeRows / missingText (hàm thuần)', () => {
  it('scopeRows: 4 dòng đúng nhãn, Có / Chưa theo boolean', () => {
    const rows = scopeRows(NO_CAL_MAIL);
    expect(rows.map((r) => r.label)).toEqual(['Đọc lịch', 'Đọc mail', 'Đọc việc (Google Tasks)', 'Tìm tệp Drive']);
    expect(rows.map((r) => r.text)).toEqual(['Chưa', 'Chưa', 'Có', 'Có']);
    expect(rows.map((r) => r.state)).toEqual(['no', 'no', 'yes', 'yes']);
  });

  it('scopeRows: không có dữ liệu / giá trị không phải boolean ⇒ "Chưa kiểm"', () => {
    for (const bad of [undefined, null, {}, 'x', 5]) {
      expect(scopeRows(bad as never).map((r) => r.text)).toEqual(['Chưa kiểm', 'Chưa kiểm', 'Chưa kiểm', 'Chưa kiểm']);
    }
    expect(scopeRows({ calendar: true, mail: 'yes', tasks: null, drive: 1 } as never).map((r) => r.text)).toEqual(['Có', 'Chưa kiểm', 'Chưa kiểm', 'Chưa kiểm']);
    expect(scopesKnown(FULL)).toBe(true);
    expect(scopesKnown({ calendar: true })).toBe(false);
  });

  it('missingText: câu đúng khuôn; rỗng / không phải mảng chuỗi ⇒ null', () => {
    expect(missingText(['đọc lịch', 'đọc mail'])).toBe('Còn thiếu quyền: đọc lịch, đọc mail — vào Gen-hub tick thêm cho token của Gen-Harness rồi bấm Kiểm tra lại.');
    expect(missingText(['tìm tệp Drive'])).toContain('Còn thiếu quyền: tìm tệp Drive —');
    expect(missingText([])).toBeNull();
    expect(missingText(undefined)).toBeNull();
    expect(missingText('đọc lịch')).toBeNull();
    expect(missingText([{ a: 1 }, 3, null, ' ', 'đọc mail'])).toContain('Còn thiếu quyền: đọc mail —');
    expect(missingText([{ a: 1 }])).toBeNull();
  });

  it('missingFromScopes + scopesMessage: thiếu ⇒ cảnh báo, đủ ⇒ câu đủ quyền, chưa biết ⇒ null', () => {
    expect(missingFromScopes(NO_CAL_MAIL)).toEqual(['đọc lịch', 'đọc mail']);
    expect(scopesMessage(['đọc lịch', 'đọc mail'], NO_CAL_MAIL)).toEqual({ tone: 'warn', text: missingText(['đọc lịch', 'đọc mail']) });
    expect(scopesMessage([], FULL)).toEqual({ tone: 'ok', text: SCOPES_ENOUGH_TEXT });
    expect(scopesMessage(undefined, FULL)).toEqual({ tone: 'ok', text: 'Đủ quyền đọc lịch, mail, việc và Drive.' });
    expect(scopesMessage(undefined, NO_CAL_MAIL)?.tone).toBe('warn');
    expect(scopesMessage(undefined, undefined)).toBeNull();
    expect(scopesMessage([{ x: 1 }], undefined)).toBeNull();
  });
});

describe('Thẻ Gen-hub — Quyền đọc thêm (tuỳ chọn)', () => {
  it('chưa có dữ liệu: 4 dòng "Chưa kiểm", ghi rõ Gen chỉ đọc, không có cảnh báo', async () => {
    stubHub(SAVED, testOut());
    renderCard(<HubLinkCard />);
    const box = await screen.findByTestId('hub-scopes');
    expect(within(box).getByText('Quyền đọc thêm (tuỳ chọn)')).toBeInTheDocument();
    expect(within(box).getAllByText('Chưa kiểm')).toHaveLength(4);
    for (const l of ['Đọc lịch', 'Đọc mail', 'Đọc việc (Google Tasks)', 'Tìm tệp Drive']) expect(within(box).getByText(l)).toBeInTheDocument();
    expect(within(box).getByText('Gen chỉ đọc — không gửi mail, không tạo lịch hay tệp.')).toBeInTheDocument();
    expect(screen.queryByText(/Còn thiếu quyền/)).toBeNull();
    expect(screen.queryByText(/Đủ quyền đọc/)).toBeNull();
  });

  it('link.read_scopes (lần kiểm gần nhất) hiện trước khi bấm Kiểm tra', async () => {
    stubHub({ ...SAVED, read_scopes: NO_CAL_MAIL }, testOut());
    renderCard(<HubLinkCard />);
    const box = await screen.findByTestId('hub-scopes');
    expect(within(box).getAllByText('Có')).toHaveLength(2);
    expect(within(box).getAllByText('Chưa')).toHaveLength(2);
    expect(within(box).getByText(/Còn thiếu quyền: đọc lịch, đọc mail/)).toBeInTheDocument();
  });

  it('Kiểm tra xanh nhưng thiếu quyền: vẫn "Đã nối Kho", cảnh báo "Còn thiếu quyền: đọc lịch, đọc mail"', async () => {
    stubHub(SAVED, testOut({ read_scopes: NO_CAL_MAIL, read_missing: ['đọc lịch', 'đọc mail'], write_tools: [] }));
    renderCard(<HubLinkCard />);
    await userEvent.click(await screen.findByRole('button', { name: 'Kiểm tra' }));
    expect(await screen.findByText(/Đã nối Kho · 240 ms/)).toBeInTheDocument();
    expect(
      await screen.findByText('Còn thiếu quyền: đọc lịch, đọc mail — vào Gen-hub tick thêm cho token của Gen-Harness rồi bấm Kiểm tra lại.'),
    ).toBeInTheDocument();
    const box = screen.getByTestId('hub-scopes');
    expect(within(box).getAllByText('Chưa')).toHaveLength(2);
    expect(within(box).getAllByText('Có')).toHaveLength(2);
    // Kiểm tra xanh: vùng kết quả không đỏ.
    expect(screen.getByText(/Đã nối Kho/).closest('.apm-test-result')).toHaveClass('apm-test-result--ok');
    expect(document.body.textContent).not.toContain('[object Object]');
  });

  it('Kiểm tra xanh và đủ quyền: "Đủ quyền đọc lịch, mail, việc và Drive."', async () => {
    stubHub(SAVED, testOut({ read_scopes: FULL, read_missing: [], write_tools: [] }));
    renderCard(<HubLinkCard />);
    await userEvent.click(await screen.findByRole('button', { name: 'Kiểm tra' }));
    expect(await screen.findByText('Đủ quyền đọc lịch, mail, việc và Drive.')).toBeInTheDocument();
    expect(within(screen.getByTestId('hub-scopes')).getAllByText('Có')).toHaveLength(4);
    expect(screen.queryByText(/Còn thiếu quyền/)).toBeNull();
  });

  it('token đang có quyền ghi (write_tools) ⇒ nhắc tắt, chỉ in chuỗi', async () => {
    stubHub(SAVED, testOut({ read_scopes: FULL, read_missing: [], write_tools: ['gmail_send', { x: 1 } as unknown as string, 'calendar_create_event'] }));
    renderCard(<HubLinkCard />);
    await userEvent.click(await screen.findByRole('button', { name: 'Kiểm tra' }));
    expect(await screen.findByText(/Token đang có thêm quyền GHI \(gmail_send, calendar_create_event\)/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('[object Object]');
  });

  it('chưa nối / chưa từng Kiểm tra xanh: read_scopes toàn false (máy chủ cũ) vẫn hiện "Chưa kiểm", không giục tick quyền', async () => {
    const NONE = { calendar: false, mail: false, tasks: false, drive: false };
    const OFF: HubLink = {
      configured: false, enabled: false, status: 'off', server_id: null, endpoint: null, has_token: false, allow_public_network: false,
      token_expires_at: null, days_left: null, last_ok_at: null, last_error: null, health: null,
    };
    for (const link of [{ ...OFF, read_scopes: NONE }, { ...SAVED, enabled: false, status: 'off' as const, last_ok_at: null, read_scopes: NONE }, { ...SAVED, read_scopes: null }]) {
      stubHub(link, testOut());
      const view = renderCard(<HubLinkCard />);
      const box = await screen.findByTestId('hub-scopes');
      expect(within(box).getAllByText('Chưa kiểm')).toHaveLength(4);
      expect(within(box).queryByText('Chưa')).toBeNull();
      expect(screen.queryByText(/Còn thiếu quyền|Đủ quyền đọc/)).toBeNull();
      view.unmount();
      vi.unstubAllGlobals();
    }
  });

  it('Kiểm tra đỏ mà phản hồi vẫn kèm read_scopes cũ (toàn false): dòng quyền theo lần kiểm xanh gần nhất, không cảnh báo', async () => {
    const NONE = { calendar: false, mail: false, tasks: false, drive: false };
    stubHub({ ...SAVED, last_ok_at: null }, testOut({ ok: false, error: 'mạng: hết giờ', error_code: 'HUB_UNREACHABLE', exposed_tools: [], read_scopes: NONE, read_missing: ['đọc lịch', 'đọc mail', 'đọc việc (Google Tasks)', 'tìm tệp Drive'] }));
    renderCard(<HubLinkCard />);
    await userEvent.click(await screen.findByRole('button', { name: 'Kiểm tra' }));
    expect(await screen.findByText(/Không gọi được địa chỉ này/)).toBeInTheDocument();
    expect(within(screen.getByTestId('hub-scopes')).getAllByText('Chưa kiểm')).toHaveLength(4);
    expect(screen.queryByText(/Còn thiếu quyền|Đủ quyền đọc/)).toBeNull();
  });

  it('Kiểm tra đỏ (không nối được): không nói gì về quyền đọc', async () => {
    stubHub(SAVED, testOut({ ok: false, error: 'Gen-hub từ chối token', error_code: 'HUB_TOKEN_REJECTED', exposed_tools: [] }));
    renderCard(<HubLinkCard />);
    await userEvent.click(await screen.findByRole('button', { name: 'Kiểm tra' }));
    expect(await screen.findByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(screen.queryByText(/Còn thiếu quyền|Đủ quyền đọc/)).toBeNull();
  });

  it('bộ ngắt đang mở ⇒ dải "Gen-hub tạm không trả lời — Gen tự thử lại sau 1 phút."; đóng ⇒ không có', async () => {
    stubHub({ ...SAVED, breaker: { open: true, retry_in_s: 42 } }, testOut());
    const first = renderCard(<HubLinkCard />);
    expect(await screen.findByText('Gen-hub tạm không trả lời — Gen tự thử lại sau 1 phút.')).toBeInTheDocument();
    first.unmount();
    stubHub({ ...SAVED, breaker: { open: false, retry_in_s: null } }, testOut());
    renderCard(<HubLinkCard />);
    await screen.findByTestId('hub-scopes');
    expect(screen.queryByText(/Gen-hub tạm không trả lời/)).toBeNull();
  });

  it('hướng dẫn tạo token: bật quyền ĐỌC Kho và (tuỳ chọn) đọc lịch, mail, việc, Drive; KHÔNG bật quyền ghi', async () => {
    stubHub(SAVED, testOut());
    renderCard(<HubLinkCard />);
    const step = await screen.findByText(/Bật quyền ĐỌC Kho/);
    expect(step.textContent).toMatch(/đọc lịch, đọc mail, đọc việc, tìm Drive/);
    expect(step.textContent).toMatch(/KHÔNG bật quyền ghi/);
  });

  it('vai trò khác Owner: không có khối quyền đọc (chỉ Sếp cấu hình)', async () => {
    stubHub(SAVED, testOut());
    renderCard(<HubLinkCard />, 'manager');
    expect(await screen.findByText('Chỉ Sếp (Owner) cấu hình và dùng Gen-hub.')).toBeInTheDocument();
    expect(screen.queryByTestId('hub-scopes')).toBeNull();
  });
});

describe('Chuông "Gen-hub không trả lời hơn 15 phút" (F-83)', () => {
  it('hub.unreachable là sự cố sức khoẻ: chuông tới thì dải "Cần Sếp xử lý" làm mới ngay (không chờ 60 giây)', () => {
    expect(HEALTH_KINDS.has('hub.unreachable')).toBe(true);
  });
});

describe('mock-p4-mcp — chế độ giả "thiếu quyền lịch + mail" (token chứa "thieu")', () => {
  function call(mock: ReturnType<typeof createMock>, method: string, path: string, body: Record<string, unknown> = {}) {
    let out: { status: number; body: unknown } | null = null;
    const ctx = {
      method, path, url: new URL(`http://x/api/v1${path}`), body, perms: { 'system.read': 'all', 'system.manage': 'all' },
      reply: (status: number, b?: unknown) => ((out = { status, body: b }), true as const),
      problem: (status: number, code: string, title: string) => ((out = { status, body: { code, title } }), true as const),
      text: () => true as const, needPin: () => false, userLabel: 'Sếp', owner: true, role: 'owner',
    } as unknown as P2Ctx;
    expect(mock.handle(ctx)).toBe(true);
    return out as unknown as { status: number; body: Record<string, unknown> };
  }

  it('token thường ⇒ đủ quyền; token "thieu" ⇒ Kiểm tra xanh + read_missing; GET /hub/link có read_scopes + breaker', () => {
    const mock = createMock({ fresh: true, emit: () => undefined, getAgents: () => [], pushDraft: () => undefined });
    call(mock, 'PATCH', '/hub/link', { endpoint: 'https://hub.example.test/mcp', token: 'ghtok_binh_thuong_1', allow_public_network: true });
    expect(call(mock, 'GET', '/hub/link').body).toMatchObject({ breaker: { open: false } });
    // Như máy chủ: chưa có lần Kiểm tra xanh ⇒ `read_scopes: null` ("Chưa kiểm"), KHÔNG phải 4 quyền false.
    expect((call(mock, 'GET', '/hub/link').body as { read_scopes?: unknown }).read_scopes).toBeNull();
    const full = call(mock, 'POST', '/hub/link/test').body;
    expect(full).toMatchObject({ ok: true, read_scopes: { calendar: true, mail: true, tasks: true, drive: true }, read_missing: [], write_tools: [] });
    // 3 tool đọc Kho; v0.1.50: token mặc định có quyền ghi Kho ⇒ thêm kho_create, kho_update (đếm riêng ở exposed_write_tools).
    expect(full.exposed_tools).toHaveLength(5);
    expect(full.exposed_write_tools).toEqual(['mcp-58450__kho_create', 'mcp-58450__kho_update']);

    call(mock, 'PATCH', '/hub/link', { token: 'ghtok_thieu_quyen_2' });
    const miss = call(mock, 'POST', '/hub/link/test').body;
    expect(miss).toMatchObject({ ok: true, read_scopes: { calendar: false, mail: false, tasks: true, drive: true }, read_missing: ['đọc lịch', 'đọc mail'] });
    expect(call(mock, 'GET', '/hub/link').body).toMatchObject({ status: 'ok', read_scopes: { calendar: false, mail: false } });
    // Đổi token ⇒ quyền đã kiểm không còn đúng cho tới lần Kiểm tra xanh kế tiếp.
    call(mock, 'PATCH', '/hub/link', { token: 'ghtok_moi_hoan_toan_3' });
    expect((call(mock, 'GET', '/hub/link').body as { read_scopes?: unknown }).read_scopes).toBeNull();
    call(mock, 'POST', '/hub/link/test');

    // Hook giả lập bộ ngắt mở / đóng.
    const sim = mock.hooks.hubSim as unknown as (b: unknown) => unknown;
    sim({ breaker: true });
    expect(call(mock, 'GET', '/hub/link').body).toMatchObject({ breaker: { open: true, retry_in_s: 60 } });
    sim({ scopes: 'full', breaker: false });
    expect(call(mock, 'POST', '/hub/link/test').body).toMatchObject({ read_missing: [] });
  });
});

// ── Việc Sếp cần làm: hàng Gen-hub ────────────────────────────────────────────────────────────────────────────

const ROWS: BossOverview['rows'] = [
  { row: 0, key: 'ai', title: 'Có ít nhất 1 nguồn AI chạy được', optional: false, checks: ['ai_source'], done: true },
  { row: 1, key: 'hub', title: 'Nối Gen-hub', optional: true, checks: ['hub'], done: true },
  { row: 2, key: 'facebook', title: 'Kết nối Facebook', optional: true, checks: ['facebook'], done: false },
];
const check = (detail: BossCheck['detail']): BossCheck => ({
  key: 'hub', status: 'pass', error_code: null, message: null, detail, checked_at: '2026-10-09T01:05:00Z', runs: 1,
});

function stubBoss(hub: BossCheck | null) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(url), 'http://x').pathname.replace('/api/v1', '');
      const method = init?.method ?? 'GET';
      if (path === '/boss-checks' && method === 'GET') return json(200, { rows: ROWS, results: { hub }, required_done: 1, required_total: 1 });
      if (path === '/hub/link' && method === 'GET') return json(200, SAVED);
      if (path === '/auth/me') return json(200, me('owner'));
      return json(404, { code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
}

function renderBoss() {
  queryClient.setQueryData(qk.me, me('owner'));
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/guide/viec-sep']}>
        <BossChecksPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Việc Sếp cần làm — dòng phụ quyền đọc thêm ở hàng Gen-hub', () => {
  beforeEach(() => queryClient.clear());
  afterEach(() => queryClient.clear());

  it('model: hubScopesOf kiểm kiểu từng giá trị; thiếu / sai kiểu ⇒ null; line + hint', () => {
    const items = hubScopesOf(check({ tools: 3, read_scopes: { calendar: true, mail: false, tasks: true, drive: false } }));
    expect(items?.map((i) => `${i.label}:${i.has}`)).toEqual(['Lịch:true', 'Mail:false', 'Việc:true', 'Drive:false']);
    expect(hubScopesLine(items!)).toBe('Quyền đọc thêm (không bắt buộc): Lịch ✓ · Mail ✗ · Việc ✓ · Drive ✗');
    expect(hubScopesHint(items!)).toMatch(/tick thêm quyền đọc/);
    expect(hubScopesHint(items!)).toMatch(/mail, Drive:/); // "Drive" là tên riêng — không hạ chữ thường
    expect(hubScopesHint(items!)).not.toMatch(/drive/);
    expect(hubScopesHint(hubScopesOf(check({ read_scopes: { calendar: true, mail: true, tasks: true, drive: true } }))!)).toBeNull();
    expect(hubScopesOf(null)).toBeNull();
    expect(hubScopesOf(check({}))).toBeNull();
    expect(hubScopesOf(check({ read_scopes: 'x' as never }))).toBeNull();
    expect(hubScopesOf(check({ read_scopes: ['a'] as never }))).toBeNull();
    expect(hubScopesOf(check({ read_scopes: { calendar: true, mail: 'có', tasks: true, drive: true } }))).toBeNull();
    expect(hubScopesOf(check({ read_scopes: { calendar: true, mail: true, tasks: true } }))).toBeNull();
  });

  it('hàng Gen-hub hiện "Quyền đọc thêm (không bắt buộc): Lịch ✓ · Mail ✗ …" và câu tick thêm; số mục bắt buộc không đổi', async () => {
    stubBoss(check({ tools: 3, read_scopes: { calendar: true, mail: false, tasks: true, drive: false } }));
    renderBoss();
    const hub = await screen.findByRole('region', { name: 'Nối Gen-hub' });
    const line = await within(hub).findByText(/Quyền đọc thêm \(không bắt buộc\)/);
    expect(line.textContent).toContain('Lịch ✓ · Mail ✗ · Việc ✓ · Drive ✗');
    expect(within(hub).getByText(/tick thêm quyền đọc/)).toBeInTheDocument();
    expect(await screen.findByText('Đã đạt đủ 1 dòng bắt buộc — nguồn AI chạy thật.')).toBeInTheDocument();
  });

  it('lần kiểm Gen-hub LỖI (detail còn read_scopes cũ) ⇒ KHÔNG hiện dòng quyền đọc / "tick thêm quyền" cạnh "Lỗi"', async () => {
    stubBoss({
      ...check({ read_scopes: { calendar: false, mail: false, tasks: false, drive: false }, read_missing: ['đọc lịch'] }),
      status: 'fail', error_code: 'HUB_TOKEN_REJECTED', message: '401: Token Gen-hub hết hạn hoặc đã bị thu hồi',
    });
    renderBoss();
    const hub = await screen.findByRole('region', { name: 'Nối Gen-hub' });
    await waitFor(() => expect(within(hub).getByText(/^Lỗi/)).toBeInTheDocument());
    expect(within(hub).queryByTestId('boss-hub-scopes')).toBeNull();
    expect(within(hub).queryByText(/Quyền đọc thêm|tick thêm quyền đọc/)).toBeNull();
  });

  it('đủ quyền: có dòng, không có câu hướng dẫn; không có read_scopes ⇒ ẩn dòng', async () => {
    stubBoss(check({ read_scopes: { calendar: true, mail: true, tasks: true, drive: true } }));
    const first = renderBoss();
    const hub = await screen.findByRole('region', { name: 'Nối Gen-hub' });
    expect((await within(hub).findByTestId('boss-hub-scopes')).textContent).toBe('Quyền đọc thêm (không bắt buộc): Lịch ✓ · Mail ✓ · Việc ✓ · Drive ✓');
    expect(within(hub).queryByText(/tick thêm quyền đọc/)).toBeNull();
    first.unmount();
    queryClient.clear();

    stubBoss(check({ tools: 3 }));
    renderBoss();
    const hub2 = await screen.findByRole('region', { name: 'Nối Gen-hub' });
    await waitFor(() => expect(within(hub2).getByText(/^Đạt · /)).toBeInTheDocument());
    expect(within(hub2).queryByText(/Quyền đọc thêm/)).toBeNull();
    expect(document.body.textContent).not.toContain('[object Object]');
  });
});
