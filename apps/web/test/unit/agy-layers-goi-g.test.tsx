import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { BindingsPage, Provider } from '@gen-harness/contracts';
import { ApiScreen } from '../../src/screens/api/ApiScreen';
import { AGY_LAYERS_TEXT, agyBlockedReason, isAgyModel } from '../../src/lib/agyLayers';
import { qk } from '../../src/lib/queries';

/**
 * Gói G (Sếp chốt 16/10/2026) — Antigravity CLI PHÂN LỚP: lớp ngoại giao (Soạn lại / dịch nháp, agent gắn kênh / nhóm) CHƯA
 * dùng agy; máy chủ nêu lý do ở `agy_blocked_reason` và từ chối 422 khi vẫn gán. Web làm mờ model agy ở dòng đó + giải
 * thích; lớp core (Gen, sàng lọc, Bản tin, agent nội bộ) giữ nguyên.
 */
const REASON =
  'Hộp riêng Antigravity chưa chạy — việc có người ngoài (trả lời khách, sàng lọc tin, Gen của nhân viên) tạm dùng nguồn AI kế, không dùng Antigravity trong tiến trình';

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

const ME = {
  id: 'u', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A (Chủ)',
  role: { code: 'owner', name: 'Owner — Sếp' },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
  permissions: { 'system.read': 'all', 'system.manage': 'all' },
};
function renderWith(ui: ReactElement, qc: QueryClient) {
  qc.setQueryData(qk.me, ME);
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}
const freshClient = () => new QueryClient({ defaultOptions: { queries: { retry: false } } });

const PROVIDERS: Provider[] = [
  {
    id: 'pv-agy', kind: 'antigravity_cli', name: 'Antigravity CLI', endpoint: null, failover_rank: 1, enabled: true, auth_state: 'ok',
    keys: [], models: [{ id: 'm-agy', model_name: 'gemini-3-pro', daily_quota: null, used_today: 0 }],
  },
  {
    id: 'pv-gemini', kind: 'gemini', name: 'Gemini API', endpoint: null, failover_rank: 2, enabled: true, auth_state: 'ok',
    keys: [{ id: 'k1', label: 'GEM-KEY-01', last4: '9f2a', enabled: true, cooldown_until: null, quota_left_pct: null }],
    models: [{ id: 'm1', model_name: 'gemini-2.5-flash', daily_quota: 3000, used_today: 400 }],
  },
];
const STD_AGY = { model_name: 'gemini-3-pro', provider_name: 'Antigravity CLI', tier: 'balanced' as const, tier_label: 'Cân bằng', effort: 'low' as const, temperature: 0.3, context_tokens: 6000 };
const STD_API = { ...STD_AGY, model_name: 'gemini-2.5-flash', provider_name: 'Gemini API', effort: null };

function bindings(over: Partial<BindingsPage['items'][number]> = {}): BindingsPage {
  return {
    items: [
      { agent_key: 'core.gen', label: 'Gen — trợ lý quản trị', binding: null, source: 'standard', standard: STD_AGY, standard_reason: null, agy_blocked_reason: null },
      { agent_key: 'core.reply', label: 'Soạn lại / dịch nháp', binding: null, source: 'standard', standard: STD_API, standard_reason: null, agy_blocked_reason: REASON, ...over },
      { agent_key: 'agent:ext', label: 'Trợ lý chăm khách', binding: null, source: 'standard', standard: null, standard_reason: REASON, agy_blocked_reason: REASON },
      { agent_key: 'agent:in', label: 'Trợ lý nội bộ', binding: null, source: 'standard', standard: STD_AGY, standard_reason: null, agy_blocked_reason: null },
    ],
    models: [
      { id: 'm-agy', model_name: 'gemini-3-pro', provider_name: 'Antigravity CLI', kind: 'antigravity_cli', enabled: true },
      { id: 'm1', model_name: 'gemini-2.5-flash', provider_name: 'Gemini API', kind: 'gemini', enabled: true },
    ],
  };
}

function handler(page: BindingsPage) {
  return (c: Call): Response => {
    if (c.url.includes('/agents/bindings') && c.method === 'GET') return json(200, page);
    if (c.url.includes('/failover-rules')) return json(200, []);
    if (c.url.includes('/providers/credentials')) return json(200, []);
    if (c.url.includes('/cli/profiles')) return json(200, []);
    if (c.url.endsWith('/providers') && c.method === 'GET') return json(200, PROVIDERS);
    return json(404);
  };
}

beforeEach(() => {
  document.cookie = 'gh_csrf=test-csrf';
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe('gói G — agy phân lớp trên màn API & Model', () => {
  it('hàm nhận biết: model agy theo kind, lý do chỉ nhận chuỗi', () => {
    expect(isAgyModel({ kind: 'antigravity_cli' })).toBe(true);
    expect(isAgyModel({ kind: 'gemini' })).toBe(false);
    expect(isAgyModel({})).toBe(false);                         // máy chủ cũ không gửi kind
    expect(agyBlockedReason({ agy_blocked_reason: ` ${REASON} ` })).toBe(REASON);
    expect(agyBlockedReason({ agy_blocked_reason: null })).toBeNull();
    expect(agyBlockedReason({})).toBeNull();
    expect(AGY_LAYERS_TEXT).toMatch(/qua hộp riêng/);
    expect(AGY_LAYERS_TEXT).not.toMatch(/chỉ dùng cho Gen|chỉ cho Gen/i);
  });

  it('bảng: dòng ngoại giao nói đúng lý do (không phải "cần khoá API"), dòng core vẫn "Chuẩn: <model agy>"', async () => {
    mockFetch(handler(bindings()));
    renderWith(<ApiScreen />, freshClient());
    const table = await screen.findByRole('table');
    await within(table).findByText('Trợ lý nội bộ', { selector: '.apm-table__agent' });
    const rowOf = (label: string) => within(table).getByText(label, { selector: '.apm-table__agent' }).closest('tr') as HTMLElement;
    expect(rowOf('Trợ lý chăm khách')).toHaveTextContent(`Chuẩn: ${REASON}`);
    expect(rowOf('Gen — trợ lý quản trị')).toHaveTextContent('Chuẩn: gemini-3-pro (tự chọn)');
    expect(rowOf('Trợ lý nội bộ')).toHaveTextContent('Chuẩn: gemini-3-pro (tự chọn)');
    expect(rowOf('Soạn lại / dịch nháp')).toHaveTextContent('Chuẩn: gemini-2.5-flash (tự chọn)');
    expect(document.body.textContent).not.toContain('[object Object]');
  });

  it('ô chọn model của dòng ngoại giao: model agy bị làm mờ + giải thích, mặc định chọn model khác, Lưu gửi model khác', async () => {
    const calls = mockFetch((c) => (c.method === 'PUT' ? json(200, { agent_key: 'core.reply', binding: null }) : handler(bindings())(c)));
    renderWith(<ApiScreen />, freshClient());
    const user = userEvent.setup();
    const row = (await screen.findByText('Soạn lại / dịch nháp', { selector: '.apm-table__agent' })).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /Chuẩn: gemini-2.5-flash/ }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByRole('option', { name: /Antigravity CLI · gemini-3-pro/ })).toBeDisabled();
    expect(within(dialog).getByRole('option', { name: 'Gemini API · gemini-2.5-flash' })).toBeEnabled();
    expect(within(dialog).getByTestId('binding-agy-blocked-note')).toHaveTextContent(REASON);
    expect((within(dialog).getByLabelText('Model') as HTMLSelectElement).value).toBe('m1');
    await user.click(within(dialog).getByRole('button', { name: 'Lưu' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toMatchObject({ model_id: 'm1' }));
  });

  it('ô chọn model của dòng core / agent nội bộ: model agy chọn được, không chú thích', async () => {
    mockFetch(handler(bindings()));
    renderWith(<ApiScreen />, freshClient());
    const user = userEvent.setup();
    const row = (await screen.findByText('Trợ lý nội bộ', { selector: '.apm-table__agent' })).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /Chuẩn: gemini-3-pro/ }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByRole('option', { name: 'Antigravity CLI · gemini-3-pro' })).toBeEnabled();
    expect(within(dialog).queryByTestId('binding-agy-blocked-note')).toBeNull();
    expect((within(dialog).getByLabelText('Model') as HTMLSelectElement).value).toBe('m-agy');
  });

  it('dòng gán agy cũ ở khoá ngoại giao: có cảnh báo "đang chạy bằng model khác"; hộp mở ra không chọn sẵn model agy', async () => {
    const legacy = { model_id: 'm-agy', model_name: 'gemini-3-pro', provider_name: 'Antigravity CLI', temperature: 0.3, context_tokens: 8000, rule_codes: [], effort: null };
    mockFetch(handler(bindings({ binding: legacy, source: 'custom', standard: null })));
    renderWith(<ApiScreen />, freshClient());
    const user = userEvent.setup();
    const warn = await screen.findByTestId('binding-agy-blocked-core.reply');
    expect(warn).toHaveTextContent(REASON);
    expect(warn).toHaveTextContent('đang chạy bằng model khác');
    const row = warn.closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /gemini-3-pro/ }));
    const dialog = await screen.findByRole('dialog');
    expect((within(dialog).getByLabelText('Model') as HTMLSelectElement).value).toBe('m1');
  });

  it('máy chủ trả 422 khi vẫn gán agy cho dòng ngoại giao: câu thân thiện, không đối tượng thô', async () => {
    mockFetch((c) =>
      c.method === 'PUT'
        ? json(422, { type: 'about:blank', title: 'Dữ liệu chưa hợp lệ', status: 422, code: 'VALIDATION', errors: { model_id: REASON } })
        : handler(bindings())(c),
    );
    renderWith(<ApiScreen />, freshClient());
    const user = userEvent.setup();
    const row = (await screen.findByText('Soạn lại / dịch nháp', { selector: '.apm-table__agent' })).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /Chuẩn: gemini-2.5-flash/ }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Lưu' }));
    const alert = await within(dialog).findByRole('alert');
    expect(alert).toHaveTextContent(/Hộp riêng Antigravity chưa chạy|Dữ liệu chưa hợp lệ/);
    expect(document.body.textContent).not.toContain('[object Object]');
  });
});
