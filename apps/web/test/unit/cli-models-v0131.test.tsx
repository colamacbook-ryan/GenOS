import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { CliProfile, Provider, ProviderTestResult } from '@gen-harness/contracts';
import { CliCard } from '../../src/screens/system/CliCard';
import { ModelPicker } from '../../src/screens/api/ModelPicker';
import { currentModelName, isCliKind, modelOptionText, offeredGroups } from '../../src/screens/api/apiModel';
import { cliChip, cliMeta } from '../../src/screens/system/systemModel';
import { providerReady } from '../../src/setup/phase2Model';
import { ToastHost } from '../../src/shell/ToastHost';
import { queryClient } from '../../src/lib/queryClient';

/**
 * v0.1.31 — Boss 01/10: "Không thấy model và nhóm model nào để chọn" (Antigravity CLI chỉ 1 model), thẻ tài khoản
 * "Hết hạn" mà dòng nguồn "Gọi thử OK", và cần Claude Code CLI (gói Claude Pro/Max).
 */
const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const GROUPS: ProviderTestResult['model_groups'] = [
  {
    label: 'Gemini',
    models: [
      { id: 'gemini-3.8-flash-high', label: 'Gemini 3.8 Flash (High)', group: 'Gemini', tier: 'strong', hint: 'mạnh, chậm hơn, tốn hạn mức hơn', source: 'cli' },
      { id: 'gemini-3.8-flash-low', label: 'Gemini 3.8 Flash (Low)', group: 'Gemini', tier: 'fast', hint: 'nhanh, rẻ', source: 'cli' },
    ],
  },
  {
    label: 'Claude (qua Antigravity)',
    models: [{ id: 'claude-sonnet-4-6-thinking', label: 'Claude Sonnet 4.6 (Thinking)', group: 'Claude (qua Antigravity)', tier: 'balanced', hint: 'cân bằng', source: 'cli' }],
  },
];
const TEST: ProviderTestResult = { ok: true, latency_ms: 4630, models: ['gemini-3.8-flash-high', 'gemini-3.8-flash-low', 'claude-sonnet-4-6-thinking'], error: null, model_groups: GROUPS, models_source: 'cli', probe_model: 'gemini-3.8-flash-high' };
const AGY: Provider = { id: 'agy', kind: 'antigravity_cli', name: 'Antigravity CLI', endpoint: null, failover_rank: 1, enabled: true, auth_state: 'ok', keys: [], models: [] };
const prof = (over: Partial<CliProfile>): CliProfile => ({ id: 'p1', email: 'an@example.vn', plan_label: null, active: true, expires_at: null, state: 'ok', ...over });

beforeEach(() => {
  queryClient.clear();
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } });
  vi.unstubAllGlobals();
  document.cookie = 'gh_csrf=test-csrf';
});

describe('model theo nhóm (logic)', () => {
  it('offeredGroups: chỉ khi kiểm tra OK, bỏ embedding, máy chủ cũ → một nhóm', () => {
    expect(offeredGroups(TEST).map((g) => g.label)).toEqual(['Gemini', 'Claude (qua Antigravity)']);
    expect(offeredGroups({ ...TEST, ok: false })).toEqual([]);
    const old = offeredGroups({ ok: true, latency_ms: 1, models: ['gemini-2.5-flash', 'text-embedding-004'], error: null });
    expect(old).toHaveLength(1);
    expect(old[0].models.map((m) => m.id)).toEqual(['gemini-2.5-flash']);
    expect(modelOptionText(GROUPS![0].models[1])).toBe('Gemini 3.8 Flash (Low) · nhanh, rẻ');
    expect(currentModelName({ models: [{ id: '1', model_name: 'a', daily_quota: null, used_today: 0 }, { id: '2', model_name: 'b', daily_quota: null, used_today: 0, is_default: true }] })).toBe('b');
    expect(isCliKind('claude_code_cli') && isCliKind('antigravity_cli') && !isCliKind('gemini')).toBe(true);
  });

  it('một sự thật: token quá giờ nhưng tự gia hạn → "Đang hoạt động"; hết hạn thật → nói việc cần làm', () => {
    const past = new Date(Date.now() - 3600_000).toISOString();
    expect(cliChip(prof({ expires_at: past, refreshable: true, state: 'ok' })).label).toBe('Đang hoạt động');
    expect(cliMeta(prof({ expires_at: past, refreshable: true, state: 'ok' }))).toBe('tự gia hạn');
    expect(cliMeta(prof({ state: 'expired', plan_label: 'Claude Max' }))).toMatch(/Đăng nhập lại/);
  });

  it('providerReady theo từng loại CLI', () => {
    const cc: Provider = { ...AGY, id: 'cc', kind: 'claude_code_cli' };
    expect(providerReady(cc, {}, { antigravity_cli: true, claude_code_cli: false })).toBe(false);
    expect(providerReady(cc, {}, { claude_code_cli: true })).toBe(true);
    expect(providerReady(AGY, {}, true)).toBe(true);
  });
});

describe('ModelPicker — ô chọn có nhóm', () => {
  it('hiện optgroup + gợi ý; model CLI không nhận → câu lỗi, model hợp lệ → lưu làm mặc định', async () => {
    const user = userEvent.setup();
    const bodies: unknown[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        if (String(url).endsWith('/providers/agy/models')) {
          const b = JSON.parse(String(init?.body));
          bodies.push(b);
          if (b.model_name === 'gemini-3.8-flash-low')
            return json(422, { status: 422, code: 'VALIDATION_ERROR', title: 'Dữ liệu chưa hợp lệ', errors: { model_name: 'CLI không nhận model “gemini-3.8-flash-low” — chọn model khác trong danh sách' } });
          return json(201, { ...AGY, models: [{ id: 'm1', model_name: b.model_name, daily_quota: null, used_today: 0, is_default: true }] });
        }
        return json(200, []);
      }),
    );
    render(
      <QueryClientProvider client={queryClient}>
        <ModelPicker provider={AGY} test={TEST} />
        <ToastHost />
      </QueryClientProvider>,
    );
    const select = screen.getByRole('combobox', { name: 'Model cho Antigravity CLI' });
    const groups = select.querySelectorAll('optgroup');
    expect([...groups].map((g) => g.label)).toEqual(['Gemini', 'Claude (qua Antigravity)']);
    expect(within(select).getByRole('option', { name: 'Claude Sonnet 4.6 (Thinking) · cân bằng' })).toBeInTheDocument();
    // v0.1.66 (F133): không còn hứa "hệ thống dùng <model thăm dò>" — mỗi vai tự chọn model theo hồ sơ chuẩn.
    expect(screen.getByText(/Chưa lưu model nào — chọn rồi bấm "Dùng model này"/)).toBeInTheDocument();

    await user.selectOptions(select, 'gemini-3.8-flash-low');
    await user.click(screen.getByRole('button', { name: 'Dùng model này' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('CLI không nhận model');

    await user.selectOptions(select, 'claude-sonnet-4-6-thinking');
    await user.click(screen.getByRole('button', { name: 'Dùng model này' }));
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1]).toEqual({ model_name: 'claude-sonnet-4-6-thinking', make_default: true, effort: null });
  });
});

describe('Claude Code CLI card', () => {
  it('chưa đăng nhập: cảnh báo điều khoản + đăng nhập qua link claude.com + dán mã', async () => {
    const user = userEvent.setup();
    let status = 'waiting_code';
    const urls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        const u = String(url);
        urls.push(`${init?.method ?? 'GET'} ${u}`);
        if (u.includes('/cli/profiles')) return json(200, status === 'done' ? [prof({ kind: 'claude_code_cli', email: 'boss@claude.vn', plan_label: 'Claude Max' })] : []);
        if (u.includes('/cli/login/L1/code')) {
          status = 'done';
          return json(202, {});
        }
        if (u.endsWith('/cli/login/L1'))
          return json(200, status === 'done'
            ? { login_id: 'L1', kind: 'claude_code_cli', status: 'done', profile: prof({ kind: 'claude_code_cli', email: 'boss@claude.vn' }) }
            : { login_id: 'L1', kind: 'claude_code_cli', status: 'waiting_code', url: 'https://claude.com/cai/oauth/authorize?code=true&state=X' });
        if (u.includes('/cli/login')) return json(202, { login_id: 'L1', kind: 'claude_code_cli' });
        return json(200, []);
      }),
    );
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <CliCard canManage showCredentials={false} kind="claude_code_cli" />
          <ToastHost />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const card = await screen.findByTestId('cli-card-claude_code_cli');
    expect(within(card).getByText('Tài khoản Claude Code CLI')).toBeInTheDocument();
    const risk = await within(card).findByTestId('claude-risk');
    expect(risk).toHaveTextContent('Sếp tự quyết rủi ro');
    expect(within(risk).getByRole('link', { name: 'điều khoản Claude Code' })).toHaveAttribute('href', 'https://code.claude.com/docs/en/legal-and-compliance');
    await user.click(within(card).getByRole('button', { name: 'Đăng nhập Claude' }));
    expect(urls.some((u) => u.startsWith('POST') && u.includes('/cli/login?kind=claude_code_cli'))).toBe(true);
    const link = await within(card).findByRole('link', { name: /Mở trang đăng nhập Claude/ });
    expect(link).toHaveAttribute('href', expect.stringContaining('https://claude.com/cai/oauth/authorize'));
    await user.type(within(card).getByLabelText('Mã xác thực'), 'abc#def');
    await user.click(within(card).getByRole('button', { name: 'Xác nhận' }));
    expect(await within(card).findByText('boss@claude.vn', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(urls.some((u) => u.includes('/cli/profiles?kind=claude_code_cli'))).toBe(true);
  });
});
