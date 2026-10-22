import { createRef } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { Provider, SetupStepStatus } from '@gen-harness/contracts';
import { ApiScreen } from '../../src/screens/api/ApiScreen';
import { PROVIDER_PRESETS } from '../../src/screens/api/apiModel';
import { Step4Brain } from '../../src/setup/Step4Brain';
import { SETUP_STEPS } from '../../src/setup/steps';
import { qk } from '../../src/lib/queries';
import { queryClient } from '../../src/lib/queryClient';

/**
 * v0.1.41 (F-84): mẫu nhà cung cấp OpenRouter — chọn "OpenRouter" ⇒ gửi kind `openai_compat`, endpoint
 * `https://openrouter.ai/api/v1`, tên "OpenRouter" (vẫn sửa được). Một nguồn mẫu (`PROVIDER_PRESETS`) dùng chung cho
 * API & Model và Hướng dẫn bước 4. Khoá thử dạng giả — không phải khoá thật.
 */

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

interface Call {
  url: string;
  method: string;
  body: unknown;
}
function mockFetch(handler: (c: Call) => Response | undefined) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const c = { url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(c);
      return handler(c) ?? json(200, []);
    }),
  );
  return calls;
}

const ME = {
  id: 'u', email: 'owner@genesis.local', display_name: 'Owner', role: { code: 'owner', name: 'Owner — Sếp' },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
  pin_verified_until: null, permissions: { 'system.read': 'all', 'system.manage': 'all' },
};

const created = (body: { kind: string; name: string; endpoint?: string }): Provider => ({
  id: 'pv-or', kind: body.kind as Provider['kind'], name: body.name, endpoint: body.endpoint ?? null, failover_rank: 1, enabled: true,
  auth_state: 'unconfigured', keys: [{ id: 'k', label: 'KEY-01', last4: '9911', enabled: true, cooldown_until: null, quota_left_pct: null }], models: [],
});

function handler(c: Call): Response | undefined {
  if (c.url.endsWith('/providers') && c.method === 'POST') return json(201, created(c.body as { kind: string; name: string; endpoint?: string }));
  if (c.url.includes('/agents/bindings')) return json(200, { items: [], models: [] });
  return undefined;
}

beforeEach(() => {
  queryClient.clear();
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } });
  queryClient.setQueryData(qk.me, ME);
  document.cookie = 'gh_csrf=test-csrf';
});
afterEach(() => vi.unstubAllGlobals());

describe('Mẫu OpenRouter (F-84)', () => {
  it('PROVIDER_PRESETS có đúng mẫu OpenRouter', () => {
    expect(PROVIDER_PRESETS.find((p) => p.id === 'openrouter')).toMatchObject({
      label: 'OpenRouter (nhiều model, một khoá)', kind: 'openai_compat', name: 'OpenRouter', endpoint: 'https://openrouter.ai/api/v1', modelHint: 'google/gemini-2.5-flash',
    });
  });

  it('API & Model › Thêm nhà cung cấp › OpenRouter ⇒ điền sẵn Tên + Endpoint, gợi ý model; gửi kind openai_compat', async () => {
    const calls = mockFetch(handler);
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <ApiScreen />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await user.click(await screen.findByRole('button', { name: /Thêm nhà cung cấp/ }));
    const dlg = await screen.findByRole('dialog', { name: 'Thêm nhà cung cấp' });
    await user.selectOptions(within(dlg).getByLabelText('Loại'), 'openrouter');
    expect(within(dlg).getByLabelText('Tên hiển thị')).toHaveValue('OpenRouter');
    expect(within(dlg).getByLabelText('Địa chỉ gọi (Endpoint)')).toHaveValue('https://openrouter.ai/api/v1');
    expect(within(dlg).getByTestId('provider-preset-hint')).toHaveTextContent('Tạo khoá ở openrouter.ai › Keys rồi dán vào đây');
    expect(dlg).toHaveTextContent('google/gemini-2.5-flash');
    // Model gợi ý là GIÁ TRỊ thật (không chỉ placeholder) ⇒ để nguyên vẫn gửi model.
    expect(within(dlg).getByLabelText('Model ban đầu (tuỳ chọn, cách nhau dấu phẩy)')).toHaveValue('google/gemini-2.5-flash');
    // Vẫn sửa được tên.
    const name = within(dlg).getByLabelText('Tên hiển thị');
    await user.clear(name);
    await user.type(name, 'OpenRouter');
    await user.type(within(dlg).getByLabelText('Khoá API (mỗi dòng một khoá)'), 'sk-test-or-0001');
    await user.click(within(dlg).getByRole('button', { name: 'Thêm' }));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith('/providers') && c.method === 'POST')).toHaveLength(1));
    expect(calls.find((c) => c.method === 'POST')!.body).toMatchObject({
      kind: 'openai_compat', name: 'OpenRouter', endpoint: 'https://openrouter.ai/api/v1', keys: ['sk-test-or-0001'], models: ['google/gemini-2.5-flash'],
    });
  });

  it('rời mẫu về Gemini ⇒ không gửi endpoint', async () => {
    const calls = mockFetch(handler);
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <ApiScreen />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await user.click(await screen.findByRole('button', { name: /Thêm nhà cung cấp/ }));
    const dlg = await screen.findByRole('dialog', { name: 'Thêm nhà cung cấp' });
    await user.selectOptions(within(dlg).getByLabelText('Loại'), 'openrouter');
    await user.selectOptions(within(dlg).getByLabelText('Loại'), 'gemini');
    expect(within(dlg).getByLabelText('Tên hiển thị')).toHaveValue('');
    expect(within(dlg).queryByLabelText('Địa chỉ gọi (Endpoint)')).toBeNull();
    expect(within(dlg).getByLabelText('Model ban đầu (tuỳ chọn, cách nhau dấu phẩy)')).toHaveValue('');
    await user.type(within(dlg).getByLabelText('Tên hiển thị'), 'Gemini API');
    await user.type(within(dlg).getByLabelText('Khoá API (mỗi dòng một khoá)'), 'AIza-test-0001');
    await user.click(within(dlg).getByRole('button', { name: 'Thêm' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1));
    const body = calls.find((c) => c.method === 'POST')!.body as Record<string, unknown>;
    expect(body.kind).toBe('gemini');
    expect(body.endpoint).toBeUndefined();
  });

  it('Hướng dẫn bước 4 có cùng mẫu OpenRouter (kind openai_compat + endpoint điền sẵn)', async () => {
    const calls = mockFetch(handler);
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <Step4Brain
            meta={SETUP_STEPS.find((s) => s.n === 4)!}
            description="Bộ não AI"
            status={'pending' as SetupStepStatus}
            token=""
            setToken={() => {}}
            onSaved={() => {}}
            onNext={() => {}}
            formRef={createRef<HTMLFormElement>()}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const kind = await screen.findByLabelText('Loại');
    expect(within(kind).getByRole('option', { name: 'OpenRouter (nhiều model, một khoá)' })).toBeInTheDocument();
    await user.selectOptions(kind, 'openrouter');
    expect(screen.getByLabelText('Tên hiển thị')).toHaveValue('OpenRouter');
    expect(screen.getByLabelText('Địa chỉ gọi (Endpoint)')).toHaveValue('https://openrouter.ai/api/v1');
    const hint = screen.getByTestId('setup-provider-preset-hint');
    expect(hint).toHaveTextContent('Tạo khoá ở openrouter.ai › Keys rồi dán vào đây');
    expect(hint).toHaveTextContent('Gợi ý model: google/gemini-2.5-flash');
    // Gợi ý nằm TRÊN nút "Thêm & kiểm tra" (đọc trước khi bấm).
    const btn = screen.getByRole('button', { name: /Thêm & kiểm tra/ });
    expect(hint.compareDocumentPosition(btn) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await user.type(screen.getByLabelText('Khoá API'), 'sk-test-or-0002');
    await user.click(screen.getByRole('button', { name: /Thêm & kiểm tra/ }));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith('/providers') && c.method === 'POST')).toHaveLength(1));
    expect(calls.find((c) => c.method === 'POST')!.body).toMatchObject({ kind: 'openai_compat', name: 'OpenRouter', endpoint: 'https://openrouter.ai/api/v1' });
  });
});
