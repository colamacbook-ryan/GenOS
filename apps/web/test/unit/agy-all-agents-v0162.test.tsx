import { createRef, type ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { BindingsPage, CliProfile, Provider, SetupStepStatus } from '@gen-harness/contracts';
import { ApiScreen } from '../../src/screens/api/ApiScreen';
import { CliCard } from '../../src/screens/system/CliCard';
import { Step4Brain } from '../../src/setup/Step4Brain';
import { StepAiSource } from '../../src/setup/one/StepAiSource';
import { SETUP_STEPS } from '../../src/setup/steps';
import { qk } from '../../src/lib/queries';
import { queryClient } from '../../src/lib/queryClient';

/**
 * v0.1.62: Antigravity CLI (agy) mở cho MỌI vai (Owner chọn 16/10/2026) — thay luật v0.1.38 (F-22) "chỉ dùng cho Gen".
 * Thẻ CLI nói đúng phạm vi mới; bảng gán model hiện "Chuẩn: <model agy>" cho mọi dòng, không còn nhãn "Chỉ cho Gen";
 * ô chọn model của MỌI dòng liệt kê model agy; gán agy cho khoá bất kỳ lưu được; bước thiết lập không còn nhắc "thêm
 * khoá API hoặc Claude Code CLI" khi chỉ có agy.
 */
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

// Gói G + H2 (16/10/2026): câu phạm vi theo chỗ chạy (apps/web/src/lib/agyLayers.ts::AGY_LAYERS_TEXT) thay câu "mọi việc" của v0.1.62.
const SCOPE_RE = /Dùng được cho: Gen của Sếp và agent nội bộ \(chạy trong hệ thống\); Bản tin, sàng lọc tin người lạ/;
const OLD_RULE_RE = /chỉ dùng cho Gen|chỉ cho Gen|luật an toàn, không tắt được/i;

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

const BINDINGS: BindingsPage = {
  items: [
    {
      // Bản cài cũ gán agy cho sàng lọc: máy chủ v0.1.62 KHÔNG còn `blocked_reason` — nhưng nếu máy chủ cũ còn gửi thì web cũng bỏ qua.
      agent_key: 'core.refinery', label: 'Sàng lọc & suy luận chính',
      binding: { model_id: 'm-agy', model_name: 'gemini-3-pro', provider_name: 'Antigravity CLI', temperature: 0.2, context_tokens: 64000, rule_codes: [], blocked_reason: 'Antigravity CLI chỉ dùng cho Gen' } as never,
      source: 'custom',
    },
    { agent_key: 'core.gen', label: 'Gen — trợ lý quản trị', binding: null, source: 'standard', standard: { ...STD_AGY, effort: 'medium' }, standard_reason: null },
    { agent_key: 'core.briefing', label: 'Bản tin Gen', binding: null, source: 'standard', standard: STD_AGY, standard_reason: null },
    { agent_key: 'core.reply_fast', label: 'Trả lời nhanh', binding: null, source: 'standard', standard: STD_AGY, standard_reason: null },
    { agent_key: 'agent:a1', label: 'Trợ lý thương mại', binding: null, source: 'standard', standard: STD_AGY, standard_reason: null },
  ],
  models: [
    { id: 'm-agy', model_name: 'gemini-3-pro', provider_name: 'Antigravity CLI', enabled: true },
    { id: 'm1', model_name: 'gemini-2.5-flash', provider_name: 'Gemini API', enabled: true },
  ],
};

function apiHandler(c: Call): Response | null {
  if (c.url.includes('/agents/bindings') && c.method === 'GET') return json(200, BINDINGS);
  if (c.url.includes('/failover-rules')) return json(200, []);
  if (c.url.includes('/providers/credentials')) return json(200, []);
  if (c.url.includes('/cli/profiles')) return json(200, []);
  if (c.url.endsWith('/providers') && c.method === 'GET') return json(200, PROVIDERS);
  return null;
}

beforeEach(() => {
  document.cookie = 'gh_csrf=test-csrf';
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe('v0.1.62 — Antigravity CLI dùng được cho mọi vai', () => {
  it('thẻ CLI Antigravity có câu phạm vi mới (mọi việc + rủi ro còn lại), không còn "chỉ cho Gen"; thẻ Claude Code không có', async () => {
    mockFetch(() => json(200, [] as CliProfile[]));
    renderWith(
      <>
        <CliCard canManage showCredentials={false} />
        <CliCard canManage showCredentials={false} kind="claude_code_cli" />
      </>,
      freshClient(),
    );
    const agy = screen.getByTestId('cli-card-antigravity_cli');
    const claude = screen.getByTestId('cli-card-claude_code_cli');
    expect(within(agy).getByText(SCOPE_RE)).toBeInTheDocument();
    expect(within(agy).getByText(/dùng chung máy chủ với khoá hệ thống/)).toBeInTheDocument();
    expect(agy).not.toHaveTextContent(OLD_RULE_RE);
    expect(within(claude).queryByText(SCOPE_RE)).toBeNull();
  });

  it('bảng gán model: mọi dòng hiện "Chuẩn: <model agy>" hoặc model đã gán; KHÔNG có nhãn / câu "Chỉ cho Gen", kể cả khi dòng gán agy cho sàng lọc', async () => {
    mockFetch((c) => apiHandler(c) ?? json(404));
    renderWith(<ApiScreen />, freshClient());
    const table = await screen.findByRole('table');
    await within(table).findByText('Bản tin Gen', { selector: '.apm-table__agent' });
    const rowOf = (label: string) => within(table).getByText(label, { selector: '.apm-table__agent' }).closest('tr') as HTMLElement;
    expect(rowOf('Sàng lọc & suy luận chính')).toHaveTextContent('gemini-3-pro');
    for (const label of ['Gen — trợ lý quản trị', 'Bản tin Gen', 'Trả lời nhanh', 'Trợ lý thương mại']) {
      expect(rowOf(label)).toHaveTextContent('Chuẩn: gemini-3-pro (tự chọn)');
    }
    expect(screen.queryByTestId('binding-blocked-core.refinery')).toBeNull();
    expect(screen.queryByTestId('binding-blocked-hint-core.refinery')).toBeNull();
    expect(table).not.toHaveTextContent(OLD_RULE_RE);
    expect(document.body.textContent).not.toContain('[object Object]');
  });

  it('ô chọn model của dòng KHÔNG phải Gen: model agy chọn được, không làm mờ / chú thích, mặc định đúng model "Chuẩn" của vai (agy) và Lưu gửi đúng', async () => {
    const calls = mockFetch((c) => (c.method === 'PUT' ? json(200, { agent_key: 'core.reply_fast', binding: null }) : apiHandler(c) ?? json(404)));
    renderWith(<ApiScreen />, freshClient());
    const user = userEvent.setup();
    const row = (await screen.findByText('Trả lời nhanh', { selector: '.apm-table__agent' })).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /Chuẩn: gemini-3-pro/ }));
    const dialog = await screen.findByRole('dialog');
    const agyOption = await within(dialog).findByRole('option', { name: 'Antigravity CLI · gemini-3-pro' });
    expect(agyOption).toBeInTheDocument();
    expect(within(dialog).queryByRole('option', { name: /chỉ cho Gen/ })).toBeNull();
    expect(within(dialog).queryByTestId('binding-agy-note')).toBeNull();
    expect((within(dialog).getByLabelText('Model') as HTMLSelectElement).value).toBe('m-agy');
    await user.click(within(dialog).getByRole('button', { name: 'Lưu' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toMatchObject({ model_id: 'm-agy' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('gán model agy cho sàng lọc (trước đây 409 AGY_OWNER_GEN_ONLY) → lưu được, hộp đóng, không có lỗi', async () => {
    const calls = mockFetch((c) => {
      if (c.url.includes('/agents/bindings/core.refinery') && c.method === 'PUT') {
        return json(200, { agent_key: 'core.refinery', label: 'Sàng lọc & suy luận chính', binding: BINDINGS.items[0].binding, source: 'custom', standard: null });
      }
      return apiHandler(c) ?? json(404);
    });
    renderWith(<ApiScreen />, freshClient());
    const user = userEvent.setup();
    const row = (await screen.findByText('Sàng lọc & suy luận chính', { selector: '.apm-table__agent' })).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /gemini-3-pro/ }));
    const dialog = await screen.findByRole('dialog');
    await user.selectOptions(within(dialog).getByLabelText('Model'), 'm-agy');
    await user.click(within(dialog).getByRole('button', { name: 'Lưu' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toMatchObject({ model_id: 'm-agy' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(document.body.textContent).not.toContain('AGY_OWNER_GEN_ONLY');
  });

  it('lỗi lưu gán model chung (500) vẫn là câu thân thiện + "Chi tiết kỹ thuật", không đối tượng thô', async () => {
    mockFetch((c) => {
      if (c.url.includes('/agents/bindings/') && c.method === 'PUT') {
        return json(500, { type: 'about:blank', title: 'Hệ thống gặp lỗi khi xử lý yêu cầu — đã ghi nhật ký', status: 500, code: 'INTERNAL', error_id: 'err-9' });
      }
      return apiHandler(c) ?? json(404);
    });
    renderWith(<ApiScreen />, freshClient());
    const user = userEvent.setup();
    const row = (await screen.findByText('Sàng lọc & suy luận chính', { selector: '.apm-table__agent' })).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /gemini-3-pro/ }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Lưu' }));
    const alert = await within(dialog).findByRole('alert');
    expect(alert).toHaveTextContent('Hệ thống gặp lỗi khi xử lý yêu cầu');
    expect(document.body.textContent).not.toContain('[object Object]');
  });

  it('bước 4: chỉ có Antigravity CLI sẵn sàng → chỉ có câu phạm vi mới, KHÔNG nhắc thêm khoá API hoặc Claude Code CLI', async () => {
    queryClient.clear();
    queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } });
    const agyProfile: CliProfile = { id: 'a', email: 'an@example.vn', plan_label: null, active: true, expires_at: null, state: 'ok' };
    mockFetch((c) => {
      if (c.url.includes('/cli/profiles')) return json(200, c.url.includes('kind=claude_code_cli') ? [] : [agyProfile]);
      if (c.url.endsWith('/providers') && c.method === 'GET') return json(200, [PROVIDERS[0]]);
      return json(200, []);
    });
    const meta = SETUP_STEPS.find((s) => s.n === 4)!;
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <Step4Brain
            meta={meta}
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
    const scope = await screen.findByTestId('setup-cli-scope-antigravity_cli');
    expect(scope).toHaveTextContent(SCOPE_RE);
    expect(scope).not.toHaveTextContent(OLD_RULE_RE);
    expect(screen.queryByTestId('setup-agy-only-hint')).toBeNull();
    expect(screen.queryByTestId('setup-cli-scope-claude_code_cli')).toBeNull();
  });

  it('màn Nguồn AI của Thiết lập một màn dùng chung CliLoginPanel: chỉ có Antigravity CLI sẵn sàng → Tiếp tục mở, không câu nhắc thêm nguồn', async () => {
    const agyProfile: CliProfile = { id: 'a', email: 'an@example.vn', plan_label: null, active: true, expires_at: null, state: 'ok' };
    queryClient.clear();
    queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } });
    mockFetch((c) => {
      if (c.url.includes('/cli/profiles')) return json(200, c.url.includes('kind=claude_code_cli') ? [] : [agyProfile]);
      if (c.url.endsWith('/providers') && c.method === 'GET') return json(200, [PROVIDERS[0]]);
      return json(200, []);
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <StepAiSource onSaved={() => {}} onLater={() => {}} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByTestId('setup-cli-scope-antigravity_cli')).toHaveTextContent(SCOPE_RE);
    await waitFor(() => expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeEnabled()); // Antigravity có model ⇒ mọi vai dùng được ngay
    expect(screen.queryByTestId('setup-agy-only-hint')).toBeNull();
  });
});
