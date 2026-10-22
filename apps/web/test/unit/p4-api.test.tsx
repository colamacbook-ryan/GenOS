import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { BindingsPage, CliProfile, Credential, FailoverRule, Provider } from '@gen-harness/contracts';
import { ApiScreen } from '../../src/screens/api/ApiScreen';
import { qk } from '../../src/lib/queries';

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

function renderScreen(ui: ReactElement, entries: string[] = ['/api']) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, ME);
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={entries}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const PROVIDERS: Provider[] = [
  {
    id: 'pv-gemini', kind: 'gemini', name: 'Gemini API', endpoint: null, failover_rank: 1, enabled: true, auth_state: 'ok',
    keys: [{ id: 'k1', label: 'GEM-KEY-01', last4: '9f2a', enabled: true, cooldown_until: null, quota_left_pct: null }],
    models: [{ id: 'm1', model_name: 'gemini-2.5-flash', daily_quota: 3000, used_today: 400 }],
  },
  {
    id: 'pv-deepseek', kind: 'deepseek', name: 'DeepSeek API', endpoint: null, failover_rank: 2, enabled: true, auth_state: 'expiring',
    keys: [{ id: 'k2', label: 'DS-KEY-01', last4: '11cd', enabled: true, cooldown_until: null, quota_left_pct: null }],
    models: [{ id: 'm2', model_name: 'deepseek-chat', daily_quota: null, used_today: 12 }],
  },
];

const BINDINGS: BindingsPage = {
  items: [
    { agent_key: 'core.refinery', label: 'Sàng lọc & suy luận chính', binding: { model_id: 'm1', model_name: 'gemini-2.5-flash', provider_name: 'Gemini API', temperature: 0.2, context_tokens: 64000, rule_codes: ['R-01', 'R-02'] } },
    // v0.1.43 (F-25): core.reply = 'Soạn lại / dịch nháp' (bỏ core.intent/scoring/indexing).
    { agent_key: 'core.reply', label: 'Soạn lại / dịch nháp', binding: null },
    { agent_key: 'agent:agent-tls', label: 'Trợ lý thương mại', binding: null },
  ],
  models: [
    { id: 'm1', model_name: 'gemini-2.5-flash', provider_name: 'Gemini API', enabled: true },
    { id: 'm2', model_name: 'deepseek-chat', provider_name: 'DeepSeek API', enabled: true },
  ],
};

const RULES: FailoverRule[] = [{ key: 'hết hạn mức', value: 'chuyển xuống nhà cung cấp kế tiếp trong chuỗi' }];
const CLI_PROFILES: CliProfile[] = [];
const CREDENTIALS: Credential[] = [];

function baseHandler(c: Call): Response | null {
  if (c.url.includes('/agents/bindings')) return json(200, BINDINGS);
  if (c.url.includes('/failover-rules')) return json(200, RULES);
  if (c.url.includes('/providers/credentials')) return json(200, CREDENTIALS);
  if (c.url.includes('/cli/profiles')) return json(200, CLI_PROFILES);
  if (c.url.endsWith('/providers') && c.method === 'GET') return json(200, PROVIDERS);
  return null;
}

describe('API & Model', () => {
  it('/api?add=openrouter ⇒ mở thẳng hộp "Thêm nhà cung cấp", chọn sẵn mẫu OpenRouter; lỗi tạo ⇒ có "Chi tiết kỹ thuật"', async () => {
    mockFetch((c) => {
      if (c.url.endsWith('/providers') && c.method === 'POST') return json(400, { status: 400, code: 'PROVIDER_TEST_FAILED', title: 'Khoá không hợp lệ' });
      return baseHandler(c) ?? json(404);
    });
    renderScreen(<ApiScreen />, ['/api?add=openrouter']);
    const dlg = await screen.findByRole('dialog', { name: 'Thêm nhà cung cấp' });
    expect(within(dlg).getByLabelText('Địa chỉ gọi (Endpoint)')).toHaveValue('https://openrouter.ai/api/v1');
    expect(within(dlg).getByTestId('provider-preset-hint')).toBeInTheDocument();
    const user = userEvent.setup();
    await user.type(within(dlg).getByLabelText('Khoá API (mỗi dòng một khoá)'), 'sk-or-khoa-thu-12345');
    await user.click(within(dlg).getByRole('button', { name: 'Thêm' }));
    expect(await within(dlg).findByText(/Chi tiết kỹ thuật/)).toBeInTheDocument();
    expect(dlg).toHaveTextContent('HTTP 400');
  });

  it('hiện thẻ nhà cung cấp, bảng gán model và quy tắc chuyển hướng', async () => {
    mockFetch((c) => baseHandler(c) ?? json(404));
    renderScreen(<ApiScreen />);

    expect(await screen.findByText('Gemini API', { selector: '.apm-provider__name' })).toBeInTheDocument();
    expect(screen.getByText('DeepSeek API', { selector: '.apm-provider__name' })).toBeInTheDocument();
    expect(screen.getByText(/GEM-KEY-01 ····9f2a/, { selector: '.apm-field__val' })).toBeInTheDocument();
    expect(screen.getByText('Sàng lọc & suy luận chính')).toBeInTheDocument();
    expect(screen.getByText('Soạn lại / dịch nháp', { selector: '.apm-table__agent' })).toBeInTheDocument();
    expect(screen.queryByText('Trả lời nhanh trong nhóm')).toBeNull();
    expect(screen.getByText('Trợ lý thương mại', { selector: '.apm-table__agent' })).toBeInTheDocument();
    expect(screen.getByText('hết hạn mức')).toBeInTheDocument();
  });

  it('thêm khoá mới gửi POST /providers/{id}/keys với khoá đã nhập', async () => {
    const calls = mockFetch((c) => {
      if (c.url.includes('/providers/pv-gemini/keys') && c.method === 'POST') {
        const secret = (c.body as { secret: string }).secret;
        return json(201, { ...PROVIDERS[0], keys: [...PROVIDERS[0].keys, { id: 'k3', label: 'GEM-KEY-02', last4: secret.slice(-4), enabled: true, cooldown_until: null, quota_left_pct: null }] });
      }
      return baseHandler(c) ?? json(404);
    });
    renderScreen(<ApiScreen />);
    await screen.findByText('Gemini API', { selector: '.apm-provider__name' });
    const user = userEvent.setup();
    const card = screen.getByText('Gemini API', { selector: '.apm-provider__name' }).closest('.apm-provider') as HTMLElement;
    await user.click(within(card).getByRole('button', { name: 'Thêm khoá' }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Khoá API mới'), 'sk-test-khoa-moi-12345');
    await user.click(within(dialog).getByRole('button', { name: 'Thêm khoá' }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/keys') && c.method === 'POST')).toBe(true));
  });

  it('kiểm tra kết nối một nhà cung cấp gọi POST /providers/{id}/test và hiện kết quả', async () => {
    mockFetch((c) => {
      if (c.url.includes('/providers/pv-gemini/test') && c.method === 'POST') {
        return json(200, { ok: true, latency_ms: 812, models: ['gemini-2.5-flash'], error: null });
      }
      return baseHandler(c) ?? json(404);
    });
    renderScreen(<ApiScreen />);
    await screen.findByText('Gemini API', { selector: '.apm-provider__name' });
    const user = userEvent.setup();
    const card = screen.getByText('Gemini API', { selector: '.apm-provider__name' }).closest('.apm-provider') as HTMLElement;
    await user.click(within(card).getAllByRole('button', { name: 'Kiểm tra kết nối' })[0]);
    expect(await within(card).findByText(/812 ms/)).toBeInTheDocument();
  });

  it('kéo-thả thay bằng nút lên/xuống gọi PATCH /providers/chain với thứ tự mới', async () => {
    const calls = mockFetch((c) => {
      if (c.url.includes('/providers/chain') && c.method === 'PATCH') {
        return json(200, [
          { ...PROVIDERS[1], failover_rank: 1 },
          { ...PROVIDERS[0], failover_rank: 2 },
        ]);
      }
      return baseHandler(c) ?? json(404);
    });
    renderScreen(<ApiScreen />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Đưa DeepSeek API lên trước' }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/providers/chain') && c.method === 'PATCH')).toBe(true));
    const chainCall = calls.find((c) => c.url.includes('/providers/chain'));
    expect((chainCall?.body as { provider_ids: string[] }).provider_ids).toEqual(['pv-deepseek', 'pv-gemini']);
  });
});
