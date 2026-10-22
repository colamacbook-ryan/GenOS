import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { AiCost, BackgroundSources } from '@gen-harness/contracts';
import { BackgroundSourcesCard } from '../../src/screens/system/BackgroundSourcesCard';
import { AiBudgetCard } from '../../src/screens/system/AiBudgetCard';
import { parseVnd, priceStr } from '../../src/screens/system/moneyInput';
import { PinDialogHost } from '../../src/shell/PinDialogHost';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';

/**
 * v0.1.41 (F-86, QD-12): "Nguồn AI cho việc nền" — bật Claude Code CLI cần cảnh báo (risk_text NGUYÊN VĂN) + ô tích +
 * PIN (423 ⇒ hộp PIN tự mở rồi gửi lại); tắt không cần cảnh báo. F-84: "Chi phí & trần ngân sách" gọi đúng endpoint.
 */

const RISK =
  'Claude Code CLI đăng nhập bằng gói Claude cá nhân — để app tự động gọi cho việc nền có thể bị hạn chế hoặc khoá tài khoản. Sếp tự chịu rủi ro.';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json' } });
const PIN_REQUIRED = () => json(423, { status: 423, code: 'PIN_REQUIRED', title: 'Thao tác này cần nhập mã PIN' });
const PIN_OK = () => json(200, { pin_verified_until: new Date(Date.now() + 1800_000).toISOString() });

type Role = 'owner' | 'manager' | 'auditor';
function meAs(role: Role) {
  return {
    id: 'u', email: `${role}@genesis.local`, display_name: role, role: { code: role, name: role },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
    pin_verified_until: null,
    permissions: role === 'auditor' ? { 'system.read': 'all' } : { 'system.read': 'all', 'system.manage': 'all' },
  };
}

/** Antigravity CLI đã đăng nhập nhưng chưa có model ⇒ chưa chạy được việc nền. */
const AGY_NO_MODEL = { provider_id: 'p-agy', name: 'Antigravity Brain', kind: 'antigravity_cli', used: false, reason: 'Chưa chọn model' };

function bg(over: Partial<BackgroundSources> = {}): BackgroundSources {
  return {
    allow_cli: [], accepted_at: null, risk_text: RISK, purposes: ['refinery', 'duty_decide', 'gen.briefing'], has_api_source: true,
    has_agy_source: true,
    sources: [
      // v0.1.62: Antigravity CLI chạy được việc nền, không cần Owner cho phép.
      { provider_id: 'p-agy', name: 'Antigravity Brain', kind: 'antigravity_cli', used: true, reason: null },
      { provider_id: 'p-gem', name: 'Gemini API', kind: 'gemini', used: true, reason: null },
      { provider_id: 'p-cc', name: 'Claude Code CLI', kind: 'claude_code_cli', used: false, reason: 'Chưa cho phép' },
    ],
    ...over,
  };
}

interface Call {
  url: string;
  method: string;
  body: unknown;
}
function mockFetch(handler: (c: Call) => Response | undefined, role: Role = 'owner') {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const c = { url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(c);
      const r = handler(c);
      if (r) return r;
      if (c.url.endsWith('/auth/me')) return json(200, meAs(role));
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tìm thấy' });
    }),
  );
  return calls;
}

function renderCards(role: Role = 'owner') {
  queryClient.setQueryData(qk.me, meAs(role));
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <BackgroundSourcesCard />
        <AiBudgetCard />
        <PinDialogHost />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function enterPin(user: ReturnType<typeof userEvent.setup>) {
  const pin = await screen.findByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
  const boxes = within(pin).getAllByLabelText(/Mã PIN — chữ số/);
  await waitFor(() => expect(boxes[0]).toHaveFocus());
  await user.keyboard('246810');
}

function cost(over: Partial<AiCost> = {}): AiCost {
  return {
    date: '2026-10-02', timezone: 'Asia/Ho_Chi_Minh', total_vnd: 12_500, budget_vnd: 20_000, over_budget: false, unpriced_calls: 3,
    agents: [],
    models: [
      { model_id: 'm-gem', provider_name: 'Gemini API', provider_kind: 'gemini', model_name: 'gemini-2.5-flash', in_vnd_per_mtok: 7500, out_vnd_per_mtok: 62500, price_source: 'owner', calls_today: 42 },
      { model_id: 'm-ds', provider_name: 'DeepSeek API', provider_kind: 'deepseek', model_name: 'deepseek-reasoner', in_vnd_per_mtok: null, out_vnd_per_mtok: null, price_source: 'none', calls_today: 3 },
      { model_id: 'm-cc', provider_name: 'Claude Code CLI', provider_kind: 'claude_code_cli', model_name: 'claude-sonnet', in_vnd_per_mtok: 0, out_vnd_per_mtok: 0, price_source: 'subscription', calls_today: 5 },
    ],
    last_7_days: [], feedback_7d: { helpful: 0, not_helpful: 0, briefing_helpful: 0, briefing_not_helpful: 0 },
    ...over,
  };
}

beforeEach(() => {
  queryClient.clear();
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } });
  document.cookie = 'gh_csrf=test-csrf';
});
afterEach(() => vi.unstubAllGlobals());

describe('<BackgroundSourcesCard> Nguồn AI cho việc nền', () => {
  it('máy chủ trả nhãn mục đích (BACKGROUND_PURPOSE_LABELS) ⇒ câu đầu thẻ vẫn gọn, không lặp nhãn thô', async () => {
    mockFetch((c) =>
      c.url.endsWith('/providers/background')
        ? json(200, bg({ purposes: ['Sàng lọc tin', 'Trực việc (agent soạn nháp)', 'Bản tin Gen'] }))
        : c.url.includes('/system/ai-cost')
          ? json(200, cost())
          : undefined,
    );
    renderCards();
    const panel = await screen.findByRole('region', { name: 'Nguồn AI cho việc nền' });
    await waitFor(() => expect(panel).toHaveTextContent('Dùng cho sàng lọc tin, trực việc, Bản tin Gen'));
  });

  it('lỗi tải thẻ ⇒ câu tiếng Việt + "Chi tiết kỹ thuật" (mã HTTP/mã lỗi), không "[object Object]"', async () => {
    mockFetch((c) =>
      c.url.endsWith('/providers/background')
        ? json(500, { status: 500, code: 'INTERNAL', title: 'Hệ thống gặp lỗi khi xử lý yêu cầu', error_id: 'err-123', detail: { x: 1 } })
        : c.url.includes('/system/ai-cost')
          ? json(200, cost())
          : undefined,
    );
    renderCards();
    const panel = await screen.findByRole('region', { name: 'Nguồn AI cho việc nền' });
    const alert = await within(panel).findByRole('alert');
    expect(alert).toHaveTextContent('Hệ thống gặp lỗi khi xử lý yêu cầu');
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(alert).toHaveTextContent('HTTP 500 · INTERNAL · error_id err-123');
    expect(document.body).not.toHaveTextContent('[object Object]');
  });

  it('liệt kê nguồn theo chuỗi với viên trạng thái + lý do; Antigravity CLI dùng được việc nền, không có công tắc bật', async () => {
    mockFetch((c) => (c.url.endsWith('/providers/background') ? json(200, bg()) : c.url.includes('/system/ai-cost') ? json(200, cost()) : undefined));
    renderCards();
    const panel = await screen.findByRole('region', { name: 'Nguồn AI cho việc nền' });
    expect(panel).toHaveTextContent('Dùng cho sàng lọc tin, trực việc, Bản tin Gen');
    expect(await within(panel).findByTestId('bg-src-p-gem')).toHaveTextContent('Dùng cho việc nền');
    expect(within(panel).getByTestId('bg-src-p-agy')).toHaveTextContent('Dùng cho việc nền');
    expect(within(panel).getByTestId('bg-src-p-agy')).not.toHaveTextContent('chỉ dùng khi Sếp hỏi Gen');
    expect(within(panel).getByTestId('bg-src-p-cc')).toHaveTextContent('Không dùng');
    expect(within(panel).getByTestId('bg-src-agy-note')).toHaveTextContent('Antigravity CLI chạy được Bản tin và sàng lọc tin người lạ (qua hộp riêng), không cần Sếp cho phép');
    const switches = within(panel).getAllByRole('switch');
    expect(switches).toHaveLength(1);
    expect(switches[0]).toHaveAccessibleName('Cho Claude Code CLI chạy việc nền');
    expect(within(panel).getByText(/Cần mã PIN 6 số/)).toBeInTheDocument();
    expect(within(panel).queryByTestId('bg-src-no-key')).toBeNull();
  });

  it('bật CLI ⇒ hộp cảnh báo hiện đúng risk_text, Cho phép khoá tới khi tích; 423 ⇒ PIN ⇒ gửi lại thành công', async () => {
    let pinOk = false;
    let current = bg();
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/providers/background') && c.method === 'GET') return json(200, current);
      if (c.url.endsWith('/providers/background') && c.method === 'PUT') {
        if (!pinOk) return PIN_REQUIRED();
        current = bg({ allow_cli: ['claude_code_cli'], accepted_at: new Date().toISOString() });
        current.sources[2] = { ...current.sources[2], used: true, reason: null };
        return json(200, current);
      }
      if (c.url.endsWith('/auth/pin/verify')) {
        pinOk = true;
        return PIN_OK();
      }
      if (c.url.includes('/system/ai-cost')) return json(200, cost());
      return undefined;
    });
    const user = userEvent.setup();
    renderCards();
    const panel = await screen.findByRole('region', { name: 'Nguồn AI cho việc nền' });
    await user.click(await within(panel).findByRole('switch', { name: 'Cho Claude Code CLI chạy việc nền' }));
    const dlg = await screen.findByRole('dialog', { name: 'Cho Claude Code CLI chạy việc nền' });
    expect(within(dlg).getByTestId('bg-cli-risk').textContent).toBe(RISK);
    const allow = within(dlg).getByRole('button', { name: 'Cho phép' });
    expect(allow).toBeDisabled();
    expect(calls.some((c) => c.method === 'PUT')).toBe(false);
    await user.click(within(dlg).getByRole('checkbox', { name: 'Tôi đã đọc cảnh báo và tự chịu rủi ro' }));
    expect(allow).toBeEnabled();
    await user.click(allow);
    await enterPin(user);
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith('/providers/background') && c.method === 'PUT')).toHaveLength(2));
    for (const c of calls.filter((x) => x.method === 'PUT')) expect(c.body).toEqual({ allow_cli: ['claude_code_cli'], accept_risk: true });
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Cho Claude Code CLI chạy việc nền' })).toBeNull());
    await waitFor(() => expect(within(panel).getByRole('switch', { name: 'Cho Claude Code CLI chạy việc nền' })).toHaveAttribute('aria-checked', 'true'));
    expect(within(panel).getByTestId('bg-src-p-cc')).toHaveTextContent('Dùng cho việc nền');
  });

  it('422 errors.accept_risk ⇒ hiện lỗi trường trong hộp (chuỗi, không object)', async () => {
    mockFetch((c) => {
      if (c.url.endsWith('/providers/background') && c.method === 'GET') return json(200, bg());
      if (c.url.endsWith('/providers/background') && c.method === 'PUT')
        return json(422, { status: 422, code: 'VALIDATION_ERROR', title: 'Dữ liệu chưa hợp lệ', errors: { accept_risk: 'Sếp cần tích xác nhận đã đọc cảnh báo' } });
      if (c.url.includes('/system/ai-cost')) return json(200, cost());
      return undefined;
    });
    const user = userEvent.setup();
    renderCards();
    await user.click(await screen.findByRole('switch', { name: 'Cho Claude Code CLI chạy việc nền' }));
    const dlg = await screen.findByRole('dialog', { name: 'Cho Claude Code CLI chạy việc nền' });
    await user.click(within(dlg).getByRole('checkbox'));
    await user.click(within(dlg).getByRole('button', { name: 'Cho phép' }));
    expect(await within(dlg).findByRole('alert')).toHaveTextContent('Sếp cần tích xác nhận đã đọc cảnh báo');
    expect(dlg).not.toHaveTextContent('[object Object]');
  });

  it('tắt CLI ⇒ gửi allow_cli: [] ngay, không hiện cảnh báo, không PIN', async () => {
    let current = bg({ allow_cli: ['claude_code_cli'], accepted_at: '2026-10-01T02:00:00Z' });
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/providers/background') && c.method === 'GET') return json(200, current);
      if (c.url.endsWith('/providers/background') && c.method === 'PUT') {
        current = bg();
        return json(200, current);
      }
      if (c.url.includes('/system/ai-cost')) return json(200, cost());
      return undefined;
    });
    const user = userEvent.setup();
    renderCards();
    const sw = await screen.findByRole('switch', { name: 'Cho Claude Code CLI chạy việc nền' });
    expect(sw).toHaveAttribute('aria-checked', 'true');
    await user.click(sw);
    await waitFor(() => expect(calls.filter((c) => c.method === 'PUT')).toHaveLength(1));
    expect(calls.find((c) => c.method === 'PUT')!.body).toEqual({ allow_cli: [], accept_risk: false });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(calls.some((c) => c.url.endsWith('/auth/pin/verify'))).toBe(false);
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Cho Claude Code CLI chạy việc nền' })).toHaveAttribute('aria-checked', 'false'));
  });

  it('có Antigravity CLI chạy được mà chưa có khoá API ⇒ KHÔNG hiện khung nhắc dán khoá', async () => {
    mockFetch((c) =>
      c.url.endsWith('/providers/background')
        ? json(200, bg({ has_api_source: false, has_agy_source: true, sources: [bg().sources[0]] }))
        : c.url.includes('/system/ai-cost')
          ? json(200, cost())
          : undefined,
    );
    renderCards();
    const panel = await screen.findByRole('region', { name: 'Nguồn AI cho việc nền' });
    expect(await within(panel).findByTestId('bg-src-p-agy')).toHaveTextContent('Dùng cho việc nền');
    expect(within(panel).queryByTestId('bg-src-no-key')).toBeNull();
  });

  it('chưa có khoá API và Antigravity CLI chưa chạy được ⇒ khung nhắc dán khoá / đăng nhập Antigravity + nút Thêm nhà cung cấp (mở thẳng hộp Thêm, mẫu OpenRouter)', async () => {
    mockFetch((c) =>
      c.url.endsWith('/providers/background')
        ? json(200, bg({ has_api_source: false, has_agy_source: false, sources: [AGY_NO_MODEL] }))
        : c.url.includes('/system/ai-cost')
          ? json(200, cost())
          : undefined,
    );
    renderCards();
    const box = await screen.findByTestId('bg-src-no-key');
    expect(box).toHaveTextContent('Chưa có nguồn AI cho việc nền — dán khoá OpenRouter/Gemini hoặc đăng nhập Antigravity CLI');
    expect(within(box).getByRole('link', { name: /Thêm nhà cung cấp/ })).toHaveAttribute('href', '/api?add=openrouter');
    expect(screen.queryByRole('switch', { name: 'Cho Claude Code CLI chạy việc nền' })).toBeNull();
  });

  it('has_api_source=false + đã cho Claude Code CLI ⇒ KHÔNG nói "việc nền không chạy", chỉ khuyên thêm khoá API', async () => {
    mockFetch((c) =>
      c.url.endsWith('/providers/background')
        ? json(200, bg({ has_api_source: false, has_agy_source: false, allow_cli: ['claude_code_cli'], sources: [AGY_NO_MODEL, { ...bg().sources[2], used: true, reason: null }] }))
        : c.url.includes('/system/ai-cost')
          ? json(200, cost())
          : undefined,
    );
    renderCards();
    const box = await screen.findByTestId('bg-src-no-key');
    expect(box).toHaveTextContent('Việc nền đang chạy bằng Claude Code CLI — nên dán khoá API OpenRouter/Gemini để an toàn hơn');
    expect(box).not.toHaveTextContent('để việc nền chạy');
    expect(box).not.toHaveTextContent('cần một nguồn AI bằng khoá API');
    expect(within(box).getByRole('link', { name: /Thêm nhà cung cấp/ })).toHaveAttribute('href', '/api?add=openrouter');
    expect(screen.getByRole('switch', { name: 'Cho Claude Code CLI chạy việc nền' })).toHaveAttribute('aria-checked', 'true');
  });

  it('Auditor + chưa có khoá API ⇒ không có nút Thêm nhà cung cấp (ngõ cụt), chỉ câu báo Owner', async () => {
    mockFetch(
      (c) =>
        c.url.endsWith('/providers/background')
          ? json(200, bg({ has_api_source: false, has_agy_source: false, sources: [AGY_NO_MODEL] }))
          : c.url.includes('/system/ai-cost')
            ? json(200, cost())
            : undefined,
      'auditor',
    );
    renderCards('auditor');
    const box = await screen.findByTestId('bg-src-no-key');
    expect(within(box).queryByRole('link')).toBeNull();
    expect(box).toHaveTextContent('Báo Owner thêm khoá API ở API & Model.');
  });

  it('người không phải Owner chỉ đọc: công tắc bị khoá, có câu "Chỉ Sếp (Owner)…"', async () => {
    const calls = mockFetch((c) => (c.url.endsWith('/providers/background') ? json(200, bg()) : c.url.includes('/system/ai-cost') ? json(200, cost()) : undefined), 'manager');
    const user = userEvent.setup();
    renderCards('manager');
    const sw = await screen.findByRole('switch', { name: 'Cho Claude Code CLI chạy việc nền' });
    expect(sw).toHaveAttribute('aria-disabled', 'true');
    await user.click(sw);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(calls.some((c) => c.method === 'PUT')).toBe(false);
    expect(screen.getByText('Chỉ Sếp (Owner) thay đổi được nguồn AI cho việc nền.')).toBeInTheDocument();
  });
});

describe('<AiBudgetCard> Chi phí & trần ngân sách', () => {
  it('lưu trần ⇒ PUT /system/ai-cost/budget; lưu giá ⇒ PUT /system/ai-cost/prices/{id}; CLI "Trả theo gói — 0 ₫"', async () => {
    let current = cost();
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/providers/background')) return json(200, bg());
      if (c.url.includes('/system/ai-cost/budget') && c.method === 'PUT') {
        current = cost({ budget_vnd: (c.body as { daily_budget_vnd: number | null }).daily_budget_vnd });
        return json(200, current);
      }
      if (c.url.includes('/system/ai-cost/prices/') && c.method === 'PUT') return json(200, current);
      if (c.url.includes('/system/ai-cost')) return json(200, current);
      return undefined;
    });
    const user = userEvent.setup();
    renderCards();
    const panel = await screen.findByRole('region', { name: 'Chi phí & trần ngân sách' });
    const input = await within(panel).findByLabelText('Trần chi phí mỗi ngày (₫)');
    expect(input).toHaveValue('20000');
    await user.clear(input);
    await user.type(input, '50000');
    await user.click(within(panel).getByRole('button', { name: 'Lưu trần' }));
    await waitFor(() => expect(calls.filter((c) => c.url.includes('/system/ai-cost/budget'))).toHaveLength(1));
    expect(calls.find((c) => c.url.includes('/budget'))).toMatchObject({ method: 'PUT', body: { daily_budget_vnd: 50000 } });

    // Trống = không giới hạn ⇒ null.
    await user.clear(input);
    await user.click(within(panel).getByRole('button', { name: 'Lưu trần' }));
    await waitFor(() => expect(calls.filter((c) => c.url.includes('/system/ai-cost/budget'))).toHaveLength(2));
    expect(calls.filter((c) => c.url.includes('/budget'))[1].body).toEqual({ daily_budget_vnd: null });

    expect(within(panel).getByTestId('ai-price-m-cc')).toHaveTextContent('Trả theo gói — 0 ₫');
    expect(within(within(panel).getByTestId('ai-price-m-cc')).queryByRole('textbox')).toBeNull();
    expect(within(panel).getByTestId('ai-price-m-ds')).toHaveTextContent('Chưa có giá');

    await user.type(within(panel).getByLabelText('Giá token vào của deepseek-reasoner (₫/1M token)'), '14000');
    await user.type(within(panel).getByLabelText('Giá token ra của deepseek-reasoner (₫/1M token)'), '55000');
    await user.click(within(panel).getByRole('button', { name: 'Lưu giá deepseek-reasoner' }));
    await waitFor(() => expect(calls.filter((c) => c.url.includes('/system/ai-cost/prices/'))).toHaveLength(1));
    const put = calls.find((c) => c.url.includes('/prices/'))!;
    expect(put.url).toMatch(/\/system\/ai-cost\/prices\/m-ds$/);
    expect(put).toMatchObject({ method: 'PUT', body: { in_vnd_per_mtok: 14000, out_vnd_per_mtok: 55000 } });
  });

  it('giá có phần lẻ: "0,5"/"2.5" lưu đúng 0.5/2.5 (không gấp 10); giá đã lưu 0.75 hiện "0,75", không bị coi là đã sửa', async () => {
    const base = cost();
    const withDec = cost({
      models: base.models.map((m) => (m.model_id === 'm-gem' ? { ...m, in_vnd_per_mtok: 0.75, out_vnd_per_mtok: 2.5 } : m)),
    });
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/providers/background')) return json(200, bg());
      if (c.url.includes('/system/ai-cost/prices/') && c.method === 'PUT') return json(200, withDec);
      if (c.url.includes('/system/ai-cost')) return json(200, withDec);
      return undefined;
    });
    const user = userEvent.setup();
    renderCards();
    const panel = await screen.findByRole('region', { name: 'Chi phí & trần ngân sách' });
    const gin = await within(panel).findByLabelText('Giá token vào của gemini-2.5-flash (₫/1M token)');
    expect(gin).toHaveValue('0,75');
    expect(within(panel).getByLabelText('Giá token ra của gemini-2.5-flash (₫/1M token)')).toHaveValue('2,5');
    expect(within(panel).getByRole('button', { name: 'Lưu giá gemini-2.5-flash' })).toBeDisabled();

    await user.type(within(panel).getByLabelText('Giá token vào của deepseek-reasoner (₫/1M token)'), '0,5');
    await user.type(within(panel).getByLabelText('Giá token ra của deepseek-reasoner (₫/1M token)'), '2.5');
    await user.click(within(panel).getByRole('button', { name: 'Lưu giá deepseek-reasoner' }));
    await waitFor(() => expect(calls.filter((c) => c.url.includes('/system/ai-cost/prices/'))).toHaveLength(1));
    expect(calls.find((c) => c.url.includes('/prices/'))!.body).toEqual({ in_vnd_per_mtok: 0.5, out_vnd_per_mtok: 2.5 });
  });

  it('không có system.manage ⇒ chỉ đọc (không ô giá, không nút Lưu)', async () => {
    mockFetch((c) => (c.url.endsWith('/providers/background') ? json(200, bg()) : c.url.includes('/system/ai-cost') ? json(200, cost()) : undefined), 'auditor');
    renderCards('auditor');
    const panel = await screen.findByRole('region', { name: 'Chi phí & trần ngân sách' });
    expect(await within(panel).findByLabelText('Trần chi phí mỗi ngày (₫)')).toBeDisabled();
    expect(within(panel).queryByRole('button', { name: 'Lưu trần' })).toBeNull();
    expect(within(panel).queryByRole('button', { name: /Lưu giá/ })).toBeNull();
    expect(within(panel).getByTestId('ai-price-m-gem')).toHaveTextContent('7.500 ₫');
  });
});

describe('parseVnd / priceStr', () => {
  it('phân cách nghìn đúng nhóm 3 số; phần lẻ chỉ khi cho phép; "0.5" không thành 5', () => {
    expect(parseVnd('12.500')).toBe(12500);
    expect(parseVnd('1,250,000 ₫')).toBe(1250000);
    expect(parseVnd('')).toBeNull();
    expect(parseVnd('0.5')).toBeUndefined();
    expect(parseVnd('-3')).toBeUndefined();
    expect(parseVnd('0,5', 2)).toBe(0.5);
    expect(parseVnd('2.5', 2)).toBe(2.5);
    expect(parseVnd('1.250,75', 2)).toBe(1250.75);
    expect(parseVnd('12.500', 2)).toBe(12500);
    expect(parseVnd('1.2.3', 2)).toBeUndefined();
    expect(parseVnd('0,123', 2)).toBeUndefined();
    expect(priceStr(0.75)).toBe('0,75');
    expect(priceStr(14000)).toBe('14000');
    expect(priceStr(null)).toBe('');
  });
});
