/**
 * v0.1.66 (gói C3) — "Model hiển thị = model chạy" ở màn API & Model + Bộ não AI:
 * F008 xoá khoá / gỡ model · F009 đặt hạn mức · F018 thẻ "Tham số sàng lọc" trùng bỏ · F019 nút "Kiểm tra kết nối" đầu trang
 * không báo xanh 0/0 · F020 chuỗi chuyển hướng ghi đúng model đang dùng · F021 chỉ agent có nhiệt độ/ngữ cảnh · F116 ô chọn
 * model không biến mất khi Kiểm tra đỏ · F133 "Đang dùng" chỉ khi Sếp đã chốt · F137 Bộ não AI không chép lại quy tắc.
 */
import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { BindingsPage, Provider, ProviderTestResult } from '@gen-harness/contracts';
import { ApiScreen } from '../../src/screens/api/ApiScreen';
import { ModelPicker } from '../../src/screens/api/ModelPicker';
import { BrainTab } from '../../src/screens/system/BrainTab';
import { DEFAULT_CONTEXT_TOKENS, offeredGroups, parseQuotaInput, pickerGroups } from '../../src/screens/api/apiModel';
import { ToastHost } from '../../src/shell/ToastHost';
import { qk } from '../../src/lib/queries';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

interface Call {
  url: string;
  method: string;
  body: unknown;
}
function mockFetch(handler: (c: Call) => Response | Promise<Response> | null) {
  const calls: Call[] = [];
  vi.stubGlobal('WebSocket', undefined);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const c = { url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(c);
      return (await handler(c)) ?? json(404);
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

function renderUi(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, ME);
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/api']}>
        {ui}
        <ToastHost />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal('confirm', () => true);
  window.confirm = () => true;
});
afterEach(() => {
  vi.unstubAllGlobals();
});

const KEY = { id: 'k1', label: 'GEM-KEY-01', last4: '9f2a', enabled: true, cooldown_until: null, quota_left_pct: null };
// models[0] = flash (xếp theo id), pro là model Sếp đã chốt ⇒ chuỗi chạy pro, không phải flash (F005 / F020).
const GEMINI: Provider = {
  id: 'pv-gemini', kind: 'gemini', name: 'Gemini API', endpoint: null, failover_rank: 1, enabled: true, auth_state: 'ok',
  keys: [KEY, { ...KEY, id: 'k2', label: 'GEM-KEY-02', last4: '77bb' }],
  models: [
    { id: 'm-flash', model_name: 'gemini-2.5-flash', daily_quota: 3000, rate_limit_per_min: null, used_today: 400 },
    { id: 'm-pro', model_name: 'gemini-2.5-pro', daily_quota: null, used_today: 0, is_default: true },
  ],
};
const AGY: Provider = {
  id: 'pv-agy', kind: 'antigravity_cli', name: 'Antigravity CLI', endpoint: null, failover_rank: 2, enabled: true, auth_state: 'error',
  keys: [], models: [{ id: 'a-flash', model_name: 'gemini-3.8-flash', effort: 'high', daily_quota: null, used_today: 0 }],
  last_test: {
    ok: false, latency_ms: null, models: [], error: 'Model bị từ chối', models_source: 'catalog',
    model_groups: [{
      label: 'Gemini',
      models: [
        { id: 'gemini-3.8-flash', label: 'Gemini 3.8 Flash', group: 'Gemini', tier: 'balanced', hint: 'cân bằng', source: 'catalog', efforts: ['low', 'medium', 'high'], verified: true },
        { id: 'gemini-3.1-pro', label: 'Gemini 3.1 Pro', group: 'Gemini', tier: 'strong', hint: 'mạnh', source: 'catalog', efforts: ['low', 'high'], verified: true },
      ],
    }],
  },
};

const BINDINGS: BindingsPage = {
  items: [
    { agent_key: 'core.refinery', label: 'Sàng lọc & suy luận chính', binding: null, source: 'standard',
      standard: { model_name: 'gemini-2.5-pro', provider_name: 'Gemini API', tier: 'strong', tier_label: 'Kỹ hơn', effort: null, temperature: 0.2, context_tokens: 6000 } },
    { agent_key: 'agent:agent-tls', label: 'Trợ lý thương mại', binding: null, source: 'standard',
      standard: { model_name: 'gemini-2.5-pro', provider_name: 'Gemini API', tier: 'balanced', tier_label: 'Cân bằng', effort: null, temperature: 0.3, context_tokens: 6000 } },
  ],
  models: [{ id: 'm-pro', model_name: 'gemini-2.5-pro', provider_name: 'Gemini API', enabled: true }],
};

function baseHandler(providers: Provider[]) {
  return (c: Call): Response | null => {
    if (c.url.includes('/agents/bindings')) return json(200, BINDINGS);
    if (c.url.includes('/failover-rules')) return json(200, [{ key: 'hết hạn mức', value: 'chuyển xuống nhà cung cấp kế tiếp' }]);
    if (c.url.includes('/providers/credentials')) return json(200, []);
    if (c.url.includes('/cli/profiles')) return json(200, []);
    if (c.url.endsWith('/providers') && c.method === 'GET') return json(200, providers);
    return null;
  };
}

const cardOf = (name: string) => screen.getByText(name, { selector: '.apm-provider__name' }).closest('.apm-provider') as HTMLElement;

describe('logic', () => {
  it('pickerGroups: kiểm xanh ⇒ danh sách thật; đỏ ⇒ danh mục dự phòng gắn "chưa xác minh"; không có gì ⇒ rỗng', () => {
    const red = AGY.last_test as ProviderTestResult;
    expect(offeredGroups(red)).toEqual([]);                                  // (cũ) chỉ khi kiểm tra OK
    const r = pickerGroups(red);
    expect(r.live).toBe(false);
    expect(r.groups[0].models.every((m) => m.verified === false)).toBe(true);
    expect(pickerGroups({ ...red, ok: true }).live).toBe(true);
    expect(pickerGroups(null)).toEqual({ groups: [], live: false });
  });

  it('parseQuotaInput: trống = bỏ hạn mức, số nguyên ≥ 1 hợp lệ, còn lại không hợp lệ', () => {
    expect(parseQuotaInput('')).toBeNull();
    expect(parseQuotaInput(' 500 ')).toBe(500);
    for (const bad of ['0', '-3', '1.5', 'abc']) expect(parseQuotaInput(bad)).toBeUndefined();
    expect(DEFAULT_CONTEXT_TOKENS).toBe(6000);
  });
});

describe('F116 — ô chọn model không biến mất khi Kiểm tra đỏ', () => {
  it('Kiểm tra đỏ: vẫn có ô chọn (model đã lưu + danh mục dự phòng) và câu giải thích', async () => {
    mockFetch(() => json(200, []));
    renderUi(<ModelPicker provider={AGY} test={AGY.last_test} />);
    const model = screen.getByRole('combobox', { name: 'Model cho Antigravity CLI' });
    expect(within(model).getAllByRole('option').map((o) => (o as HTMLOptionElement).value)).toEqual(['gemini-3.8-flash', 'gemini-3.1-pro']);
    expect(screen.getByTestId('model-picker-unverified-pv-agy')).toHaveTextContent('chưa xác minh');
    expect(screen.getByRole('button', { name: 'Dùng model này' })).toBeEnabled();
  });

  it('chưa kiểm lần nào, không có danh mục: vẫn chọn lại được model đã lưu', async () => {
    mockFetch(() => json(200, []));
    const saved: Provider = { ...GEMINI, last_test: null };
    renderUi(<ModelPicker provider={saved} test={null} />);
    const model = screen.getByRole('combobox', { name: 'Model cho Gemini API' });
    expect(within(model).getAllByRole('option').map((o) => (o as HTMLOptionElement).value)).toEqual(['gemini-2.5-flash', 'gemini-2.5-pro']);
    expect(screen.getByTestId('model-picker-unverified-pv-gemini')).toBeInTheDocument();
  });

  it('nguồn chưa có model và không có danh mục ⇒ không có gì để chọn (không vẽ ô trống)', () => {
    mockFetch(() => json(200, []));
    const { container } = renderUi(<ModelPicker provider={{ ...GEMINI, models: [] }} test={null} />);
    expect(container.querySelector('.prov-model')).toBeNull();
  });
});

describe('F133 — "Đang dùng" chỉ khi Sếp đã chốt model', () => {
  it('chưa chốt model nào: không nói "Đang dùng … Cao"; nút "Dùng model này" bấm được; nói rõ mỗi vai tự chọn', () => {
    mockFetch(() => json(200, []));
    const unpinned: Provider = { ...AGY, auth_state: 'ok', last_test: { ...(AGY.last_test as ProviderTestResult), ok: true } };
    renderUi(<ModelPicker provider={unpinned} test={unpinned.last_test} />);
    const box = screen.getByTestId('model-picker-pv-agy');
    expect(box).not.toHaveTextContent(/Đang dùng gemini/);
    expect(box).toHaveTextContent('Chưa chốt model cho nguồn này');
    expect(screen.getByRole('button', { name: 'Dùng model này' })).toBeEnabled();
  });
});

describe('API & Model — thẻ nhà cung cấp', () => {
  it('F008: xoá một khoá gọi DELETE /providers/{id}/keys/{kid}; gỡ model gọi DELETE /providers/{id}/models/{mid}', async () => {
    const calls = mockFetch((c) => {
      if (c.method === 'DELETE') return json(204);
      return baseHandler([GEMINI, AGY])(c);
    });
    renderUi(<ApiScreen />);
    await screen.findByText('Gemini API', { selector: '.apm-provider__name' });
    const user = userEvent.setup();
    const manage = within(cardOf('Gemini API')).getByTestId('provider-manage-pv-gemini');
    await user.click(within(manage).getByText('Quản lý khoá và model'));
    await user.click(within(manage).getByRole('button', { name: 'Xoá khoá GEM-KEY-02' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/providers/pv-gemini/keys/k2'))).toBe(true));
    await user.click(within(manage).getByRole('button', { name: 'Gỡ model gemini-2.5-flash' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/providers/pv-gemini/models/m-flash'))).toBe(true));
  });

  it('F009: đặt hạn mức ngày / lượt-phút gửi daily_quota + rate_limit_per_min; để trống = null; chữ lạ bị chặn', async () => {
    const calls = mockFetch((c) => {
      if (c.method === 'POST' && c.url.endsWith('/providers/pv-gemini/models')) return json(201, GEMINI);
      return baseHandler([GEMINI])(c);
    });
    renderUi(<ApiScreen />);
    await screen.findByText('Gemini API', { selector: '.apm-provider__name' });
    const user = userEvent.setup();
    const manage = within(cardOf('Gemini API')).getByTestId('provider-manage-pv-gemini');
    await user.click(within(manage).getByText('Quản lý khoá và model'));
    const daily = within(manage).getByLabelText('Hạn mức ngày của gemini-2.5-pro');
    const perMin = within(manage).getByLabelText('Lượt mỗi phút của gemini-2.5-pro');
    await user.type(daily, 'abc');
    await user.click(within(manage).getByRole('button', { name: 'Lưu hạn mức của gemini-2.5-pro' }));
    expect(await within(manage).findByText(/Nhập số nguyên từ 1 trở lên/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    await user.clear(daily);
    await user.type(daily, '500');
    await user.type(perMin, '20');
    await user.click(within(manage).getByRole('button', { name: 'Lưu hạn mức của gemini-2.5-pro' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ model_name: 'gemini-2.5-pro', daily_quota: 500, rate_limit_per_min: 20 }));
    // model đang có hạn mức 3000: sửa về trống ⇒ gửi null (bỏ hạn mức)
    await user.clear(within(manage).getByLabelText('Hạn mức ngày của gemini-2.5-flash'));
    await user.click(within(manage).getByRole('button', { name: 'Lưu hạn mức của gemini-2.5-flash' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST')).toHaveLength(2));
    expect(calls.filter((c) => c.method === 'POST')[1].body).toEqual({ model_name: 'gemini-2.5-flash', daily_quota: null, rate_limit_per_min: null });
  });

  it('P2-2 (review): model còn lượt gọi 30 ngày ⇒ máy chủ 409, màn hiện nguyên câu thân thiện; "Tắt model" / "Bật model" gửi enabled', async () => {
    const MSG = 'Model “gemini-2.5-flash” đã có lượt gọi trong 30 ngày qua — gỡ đi sẽ mất lịch sử chi phí. Bấm “Tắt model” để ngừng dùng mà vẫn giữ lịch sử.';
    const off: Provider = { ...GEMINI, models: [GEMINI.models[0], { ...GEMINI.models[1], is_default: false, enabled: false }] };
    const calls = mockFetch((c) => {
      if (c.method === 'DELETE') return json(409, { code: 'MODEL_HAS_CALLS', title: MSG, status: 409 });
      if (c.method === 'POST' && c.url.endsWith('/providers/pv-gemini/models')) return json(201, off);
      return baseHandler([off])(c);
    });
    renderUi(<ApiScreen />);
    await screen.findByText('Gemini API', { selector: '.apm-provider__name' });
    const user = userEvent.setup();
    const manage = within(cardOf('Gemini API')).getByTestId('provider-manage-pv-gemini');
    await user.click(within(manage).getByText('Quản lý khoá và model'));
    await user.click(within(manage).getByRole('button', { name: 'Gỡ model gemini-2.5-flash' }));
    expect(await within(manage).findByText(MSG)).toBeInTheDocument();
    // Tắt model đang bật (không gửi trường nào khác ⇒ máy chủ giữ hạn mức)
    await user.click(within(manage).getByRole('button', { name: 'Tắt model gemini-2.5-flash' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ model_name: 'gemini-2.5-flash', enabled: false }));
    // Model đã tắt ⇒ nút là "Bật model"
    await user.click(within(manage).getByRole('button', { name: 'Bật model gemini-2.5-pro' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST')[1].body).toEqual({ model_name: 'gemini-2.5-pro', enabled: true }));
  });

  it('F019: chỉ có nguồn CLI ⇒ nút "Kiểm tra kết nối" đầu trang tắt (không báo xanh 0/0); có nguồn khoá API ⇒ kiểm', async () => {
    mockFetch(baseHandler([AGY]));
    const { unmount } = renderUi(<ApiScreen />);
    await screen.findByText('Antigravity CLI', { selector: '.apm-provider__name' });
    const head = screen.getAllByRole('button', { name: 'Kiểm tra kết nối' }).find((b) => !b.closest('.apm-provider')) as HTMLElement;
    expect(head).toBeDisabled();
    expect(head).toHaveAttribute('title', expect.stringContaining('Chưa có nguồn khoá API'));
    unmount();
    const calls = mockFetch((c) => (c.method === 'POST' && c.url.endsWith('/test') ? json(200, { ok: true, latency_ms: 5, models: [], error: null }) : baseHandler([GEMINI, AGY])(c)));
    renderUi(<ApiScreen />);
    await screen.findByText('Gemini API', { selector: '.apm-provider__name' });
    const head2 = screen.getAllByRole('button', { name: 'Kiểm tra kết nối' }).find((b) => !b.closest('.apm-provider')) as HTMLElement;
    expect(head2).toBeEnabled();
    await userEvent.setup().click(head2);
    expect(await screen.findByText('Đã kiểm 1 nhà cung cấp — 1 kết nối được')).toBeInTheDocument();
    expect(calls.filter((c) => c.url.endsWith('/test')).map((c) => c.url)).toEqual([expect.stringContaining('/providers/pv-gemini/test')]);
  });

  it('F020: chuỗi chuyển hướng ghi model Sếp đã chốt (gemini-2.5-pro), không phải model đầu danh sách', async () => {
    mockFetch(baseHandler([GEMINI, AGY]));
    renderUi(<ApiScreen />);
    const list = await screen.findByRole('list', { name: 'Chuỗi chuyển hướng nhà cung cấp' });
    const first = within(list).getAllByRole('listitem')[0];
    expect(first).toHaveTextContent('gemini-2.5-pro');
    expect(first).not.toHaveTextContent('gemini-2.5-flash');
  });

  it('P2-1 (review): nguồn chưa chốt model ⇒ chuỗi chuyển hướng KHÔNG ghi model lưu đầu tiên mà ghi "theo vai"', async () => {
    mockFetch(baseHandler([GEMINI, AGY]));
    renderUi(<ApiScreen />);
    const list = await screen.findByRole('list', { name: 'Chuỗi chuyển hướng nhà cung cấp' });
    const agyRow = within(list).getAllByRole('listitem')[1];
    expect(agyRow).toHaveTextContent('Antigravity CLI');
    expect(agyRow).toHaveTextContent('theo vai — xem bảng Gán model cho từng agent');
    expect(agyRow).not.toHaveTextContent('gemini-3.8-flash');
  });

  it('F018: không còn thẻ "Tham số sàng lọc" (trùng "Tham số theo vai")', async () => {
    mockFetch(baseHandler([GEMINI]));
    renderUi(<ApiScreen />);
    await screen.findByText('Gemini API', { selector: '.apm-provider__name' });
    expect(screen.queryByText('Tham số sàng lọc')).toBeNull();
    expect(screen.queryByText('Chưa gán model cho việc sàng lọc')).toBeNull();
    expect(screen.getByText('Tham số theo vai')).toBeInTheDocument();
  });
});

describe('F021 — chỉ agent trực việc có nhiệt độ / ngữ cảnh do Sếp đặt', () => {
  it('hộp gán model của agent: có ô nhiệt độ + ngữ cảnh (mặc định 6000), không có "Bộ quy tắc"; gửi rule_codes cũ', async () => {
    const calls = mockFetch((c) => (c.method === 'PUT' ? json(200, { agent_key: 'agent:agent-tls', label: 'x', binding: null, source: 'custom' }) : baseHandler([GEMINI])(c)));
    renderUi(<ApiScreen />);
    const user = userEvent.setup();
    const row = (await screen.findByText('Trợ lý thương mại', { selector: '.apm-table__agent' })).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /Chuẩn: gemini-2.5-pro/ }));
    const dlg = await screen.findByRole('dialog');
    const adv = within(dlg).getByTestId('binding-edit-advanced');
    expect(within(adv).getByLabelText('Ngữ cảnh (token)')).toHaveValue(6000);
    expect(within(adv).getByLabelText('Nhiệt độ (0–2)')).toHaveValue(0.3);
    expect(within(dlg).queryByLabelText(/Bộ quy tắc/)).toBeNull();
    await user.click(within(dlg).getByRole('button', { name: 'Lưu' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toMatchObject({ model_id: 'm-pro', temperature: 0.3, context_tokens: 6000, rule_codes: [] }));
  });

  it('vai lõi (sàng lọc): không có ô nhiệt độ / ngữ cảnh, có câu "theo hồ sơ chuẩn (cố định)"', async () => {
    mockFetch(baseHandler([GEMINI]));
    renderUi(<ApiScreen />);
    const user = userEvent.setup();
    const row = (await screen.findByText('Sàng lọc & suy luận chính', { selector: '.apm-table__agent' })).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /Chuẩn: gemini-2.5-pro/ }));
    const dlg = await screen.findByRole('dialog');
    expect(within(dlg).queryByTestId('binding-edit-advanced')).toBeNull();
    expect(within(dlg).getByTestId('binding-core-fixed-note')).toBeInTheDocument();
  });
});

describe('F020 / F137 — Bộ não AI', () => {
  it('chuỗi chuyển hướng ghi model đã chốt; không còn thẻ "Quy tắc chuyển hướng" chép lại', async () => {
    mockFetch(baseHandler([GEMINI, AGY]));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(qk.me, ME);
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <BrainTab />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(document.querySelector('.brain-chain-row__model')).not.toBeNull());
    expect(document.querySelector('.brain-chain-row__model')).toHaveTextContent('gemini-2.5-pro');
    expect(screen.queryByText('Quy tắc chuyển hướng')).toBeNull();
  });

  it('P2-1 (review): nguồn chưa chốt model ghi "theo vai", không ghi model lưu đầu tiên', async () => {
    mockFetch(baseHandler([GEMINI, AGY]));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(qk.me, ME);
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <BrainTab />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(document.querySelectorAll('.brain-chain-row__model')).toHaveLength(2));
    const rows = document.querySelectorAll('.brain-chain-row__model');
    expect(rows[1]).toHaveTextContent('theo vai — xem bảng Gán model cho từng agent');
    expect(rows[1]).not.toHaveTextContent('gemini-3.8-flash');
  });
});
