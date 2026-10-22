import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import type { Provider, ProviderDiagnosis, ProviderTestResult } from '@gen-harness/contracts';
import { ModelPicker } from '../../src/screens/api/ModelPicker';
import { CliDiagnose } from '../../src/screens/system/CliDiagnose';
import { choiceText, currentChoice, diagnosisText, modelOptionText, pickEffort, testOkText } from '../../src/screens/api/apiModel';
import { fmtLatency } from '../../src/lib/format';
import { qk } from '../../src/lib/queries';
import { ToastHost } from '../../src/shell/ToastHost';
import { queryClient } from '../../src/lib/queryClient';

/**
 * v0.1.32 — Boss 01/10: "high" là MỨC SUY NGHĨ, không phải tên model. Ô model chỉ có model gốc; "Mức suy nghĩ" là ô
 * riêng; danh sách không bao giờ thu gọn còn model đã lưu; "Gọi thử OK" luôn kèm giờ; nút "Chẩn đoán" chỉ Owner.
 */
const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const SRC = 'agy 1.2.9 — chưa xác minh';
const TEST: ProviderTestResult = {
  ok: true,
  latency_ms: 4630,
  error: null,
  models: ['gemini-3.8-flash', 'gemini-3.1-pro'],
  models_source: 'cli',
  probe_model: 'gemini-3.8-flash',
  probe_effort: 'high',
  at: '2026-10-01T03:21:00Z',
  model_groups: [
    {
      label: 'Gemini',
      models: [
        { id: 'gemini-3.8-flash', label: 'Gemini 3.8 Flash', group: 'Gemini', tier: 'fast', hint: 'nhanh, rẻ', source: 'cli', efforts: ['low', 'medium', 'high'], default_effort: 'high', verified: true },
        { id: 'gemini-3.1-pro', label: 'Gemini 3.1 Pro', group: 'Gemini', tier: 'strong', hint: 'mạnh, chậm hơn, tốn hạn mức hơn', source: 'catalog', efforts: ['low', 'high'], verified: false, source_ref: SRC },
      ],
    },
  ],
};
const AGY: Provider = { id: 'agy', kind: 'antigravity_cli', name: 'Antigravity CLI', endpoint: null, failover_rank: 1, enabled: true, auth_state: 'ok', keys: [], models: [] };

beforeEach(() => {
  queryClient.clear();
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } });
  vi.unstubAllGlobals();
  document.cookie = 'gh_csrf=test-csrf';
});

describe('mức suy nghĩ (logic)', () => {
  it('tách model / mức, chọn mức mặc định hợp lệ, ghi "chưa xác minh"', () => {
    expect(choiceText('gemini-3.8-flash', 'high')).toBe('gemini-3.8-flash · Cao');
    expect(choiceText('haiku', null)).toBe('haiku');
    const [flash, pro] = TEST.model_groups![0].models;
    expect(pickEffort(flash, null)).toBe('high');          // mức CLI đang dùng
    expect(pickEffort(flash, 'low')).toBe('low');          // mức đã lưu
    expect(pickEffort(pro, 'medium')).toBe('low');         // Pro không có "Vừa" → mức đầu
    expect(pickEffort({ efforts: [] }, 'high')).toBeNull(); // model không chỉnh mức → không gửi --effort
    expect(modelOptionText(pro)).toBe('Gemini 3.1 Pro · mạnh, chậm hơn, tốn hạn mức hơn · chưa xác minh');
    expect(currentChoice({ models: [{ id: '1', model_name: 'gemini-3.1-pro', effort: 'low', daily_quota: null, used_today: 0, is_default: true }] })).toEqual({ model: 'gemini-3.1-pro', effort: 'low' });
  });

  it('"Gọi thử OK" luôn kèm giờ của lần gọi thật', () => {
    expect(testOkText(TEST, fmtLatency)).toMatch(/^Gọi thử OK · 4,63\s?s · gemini-3.8-flash · Cao · lúc \d{2}\/10 \d{2}:21/);
    expect(testOkText({ ...TEST, at: undefined }, fmtLatency)).toMatch(/chưa rõ giờ/);
  });
});

describe('ModelPicker — model gốc + ô "Mức suy nghĩ"', () => {
  it('không còn tên biến thể; đổi model thì mức về mức hợp lệ; lỗi CLI hiện "Chi tiết kỹ thuật"', async () => {
    const user = userEvent.setup();
    const bodies: Array<Record<string, unknown>> = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        if (String(url).endsWith('/providers/agy/models')) {
          const b = JSON.parse(String(init?.body));
          bodies.push(b);
          if (b.effort === 'low' && b.model_name === 'gemini-3.1-pro')
            return json(422, {
              status: 422,
              code: 'VALIDATION',
              title: 'Dữ liệu chưa hợp lệ',
              errors: { model_name: 'CLI không nhận mức suy nghĩ “Thấp” cho model “gemini-3.1-pro” — chọn mức khác' },
              technical: 'ModelRejected: invalid --effort "low" (valid: high)',
            });
          return json(201, { ...AGY, models: [{ id: 'm1', model_name: b.model_name, effort: b.effort, daily_quota: null, used_today: 0, is_default: true }] });
        }
        return json(200, []);
      }),
    );
    render(
      <QueryClientProvider client={queryClient}>
        <ModelPicker provider={{ ...AGY, models: [{ id: 'old', model_name: 'gemini-3.8-flash', effort: 'high', daily_quota: null, used_today: 0, is_default: true }] }} test={TEST} />
        <ToastHost />
      </QueryClientProvider>,
    );
    const model = screen.getByRole('combobox', { name: 'Model cho Antigravity CLI' });
    const options = within(model).getAllByRole('option').map((o) => (o as HTMLOptionElement).value);
    expect(options).toEqual(['gemini-3.8-flash', 'gemini-3.1-pro']);    // không thu gọn, không có "-high"
    const effort = screen.getByRole('combobox', { name: 'Mức suy nghĩ (effort) cho Antigravity CLI' });
    expect(within(effort).getAllByRole('option').map((o) => o.textContent)).toEqual(['Thấp · nhanh, rẻ', 'Vừa · cân bằng', 'Cao · kỹ, chậm hơn']);
    expect((effort as HTMLSelectElement).value).toBe('high');
    expect(screen.getByRole('button', { name: 'Đang dùng' })).toBeDisabled();

    await user.selectOptions(effort, 'low');
    await user.click(screen.getByRole('button', { name: 'Dùng model này' }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ model_name: 'gemini-3.8-flash', make_default: true, effort: 'low' });

    await user.selectOptions(model, 'gemini-3.1-pro');
    expect(screen.getByText(/Chưa xác minh bằng CLI/)).toBeInTheDocument();
    const effort2 = screen.getByRole('combobox', { name: 'Mức suy nghĩ (effort) cho Antigravity CLI' }) as HTMLSelectElement;
    expect([...effort2.options].map((o) => o.value)).toEqual(['low', 'high']);
    expect(effort2.value).toBe('low');
    await user.click(screen.getByRole('button', { name: 'Dùng model này' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('mức suy nghĩ');
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(alert).toHaveTextContent('invalid --effort "low" (valid: high)');
  });
});

describe('ModelPicker — model đã lưu ngoài danh sách (review v0.1.32)', () => {
  it('mang theo mức đã lưu: "Đang dùng", không ghi đè mức thành rỗng', () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(200, [])));
    const saved: Provider = { ...AGY, models: [{ id: 'm', model_name: 'gemini-2.5-pro', effort: 'high', daily_quota: null, used_today: 0, is_default: true }] };
    render(
      <QueryClientProvider client={queryClient}>
        <ModelPicker provider={saved} test={TEST} />
      </QueryClientProvider>,
    );
    const model = screen.getByRole('combobox', { name: 'Model cho Antigravity CLI' }) as HTMLSelectElement;
    expect(model.value).toBe('gemini-2.5-pro');
    const effort = screen.getByRole('combobox', { name: 'Mức suy nghĩ (effort) cho Antigravity CLI' }) as HTMLSelectElement;
    expect([...effort.options].map((o) => o.value)).toEqual(['high']);
    expect(effort.value).toBe('high');
    expect(screen.getByRole('button', { name: 'Đang dùng' })).toBeDisabled();
    expect(screen.queryByText(/không chỉnh mức suy nghĩ/)).toBeNull();
  });
});

describe('Chẩn đoán (chỉ Owner)', () => {
  const DIAG: ProviderDiagnosis = {
    provider: 'Antigravity CLI',
    kind: 'antigravity_cli',
    model: 'gemini-3.8-flash',
    effort: 'high',
    at: '2026-10-01T03:25:00Z',
    steps: [
      { label: 'Phiên bản', command: 'agy --version', exit_code: 0, stdout: '1.2.9\n', stderr: '', ms: 40, note: null },
      { label: 'Danh sách model', command: 'agy models', exit_code: 0, stdout: 'Available models:\n  gemini-3.8-flash-high\n', stderr: '', ms: 900, note: null },
      { label: 'Gọi thử 1 lượt', command: 'agy -p … --model gemini-3.8-flash --effort high', exit_code: 3, stdout: '', stderr: 'AGY_ERROR: {"status":"X"}', ms: 1200, note: null },
    ],
  };

  it('Owner: chạy, hiện mã thoát + stdout/stderr thô, có "Chép"; vai trò khác không thấy nút', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => (String(url).endsWith('/providers/agy/diagnose') ? json(200, DIAG) : json(200, []))));
    queryClient.setQueryData(qk.me, { role: { code: 'owner' } });
    const { unmount } = render(
      <QueryClientProvider client={queryClient}>
        <CliDiagnose provider={AGY} />
        <ToastHost />
      </QueryClientProvider>,
    );
    await user.click(screen.getByRole('button', { name: 'Chẩn đoán' }));
    expect(await screen.findByText('mã thoát 3')).toBeInTheDocument();
    expect(screen.getByText(/AGY_ERROR/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Chép' }));
    expect(writeText).toHaveBeenCalledWith(diagnosisText(DIAG));
    expect(diagnosisText(DIAG)).toContain('mã thoát: 3');
    unmount();

    queryClient.setQueryData(qk.me, { role: { code: 'manager' } });
    render(
      <QueryClientProvider client={queryClient}>
        <CliDiagnose provider={AGY} />
      </QueryClientProvider>,
    );
    expect(screen.queryByRole('button', { name: 'Chẩn đoán' })).toBeNull();
  });
});
