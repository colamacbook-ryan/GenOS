import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { IntelCard } from '../../src/intel/IntelCard';
import { AiBudgetCard } from '../../src/screens/system/AiBudgetCard';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';
import type { IntelConsole } from '../../src/intel/intelModel';

/** v0.1.76 (T11): thẻ "Tình báo" + khoá `intel_*` ở thẻ Chi phí — gọi đúng route, báo lỗi thân thiện, không lộ object. */

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json' } });

const me = (role: string) => ({
  id: 'u', email: 'o@x.vn', display_name: 'Owner', role: { code: role, name: role },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
  pin_verified_until: null, permissions: { 'system.read': 'all', 'system.manage': 'all' },
});

function state(over: Partial<IntelConsole> = {}): IntelConsole {
  return {
    pin_active: false, mode: 'live', ramp_gates: 'digest_top',
    assistant: { locked: false, locked_at: null, locked_via: null },
    outbound: { unlocked_today: false, locked_until: null },
    backup_channel: { paired: false, kind: 'none' },
    mutes: [],
    payees: [{ signal_id: 'sig-1', title: 'Đổi số tài khoản nhận tiền', last3: '789', bank: 'VCB' }],
    groups: [{ id: 'g1', name: 'Nhóm Quán', labels: [] }, { id: 'g2', name: 'Nhóm Bạn', labels: [{ context: 'ban_be', is_primary: true }] }],
    contexts: ['kinh_doanh', 'ban_be', 'khac'],
    ...over,
  };
}

interface Call { url: string; method: string; body: unknown }
function mockApi(s: IntelConsole, extra?: (c: Call) => Response | undefined) {
  const calls: Call[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const call: Call = { url: String(url).replace('/api/v1', ''), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    const x = extra?.(call);
    if (x) return x;
    if (call.url === '/intel/console') return json(200, s);
    if (call.url === '/system/ai-cost/intel') {
      return json(200, { values: { intel_daily_call_cap: 400, intel_safety_call_cap: 60, intel_gate_call_cap: 40, intel_daily_budget_vnd: 0, intel_safety_budget_vnd: 0, intel_gate_budget_vnd: 0, intel_media_daily_budget_vnd: 26000 },
        defaults: { intel_daily_call_cap: 400, intel_safety_call_cap: 60, intel_gate_call_cap: 40, intel_daily_budget_vnd: 0, intel_safety_budget_vnd: 0, intel_gate_budget_vnd: 0, intel_media_daily_budget_vnd: 26000 } });
    }
    if (call.url.startsWith('/system/ai-cost')) return json(200, { date: '2026-10-25', timezone: 'Asia/Ho_Chi_Minh', total_vnd: 0, budget_vnd: null, over_budget: false, unpriced_calls: 0, agents: [], models: [], last_7_days: [], feedback_7d: { helpful: 0, not_helpful: 0, briefing_helpful: 0, briefing_not_helpful: 0 } });
    if (call.method !== 'GET') return json(200, { ok: true });
    return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tìm thấy' });
  }));
  return calls;
}

const renderCard = (ui: React.ReactElement, role = 'owner') => {
  queryClient.setQueryData(qk.me, me(role));
  return render(<QueryClientProvider client={queryClient}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
};

beforeEach(() => {
  queryClient.clear();
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } });
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('<IntelCard> Tình báo', () => {
  it('vai không phải Owner không thấy thẻ và không gọi máy chủ', () => {
    const calls = mockApi(state());
    renderCard(<><IntelCard part="trial" /><IntelCard part="controls" /></>, 'manager');
    expect(screen.queryByRole('region', { name: 'Khoá, xác minh và cảnh báo' })).toBeNull();
    expect(calls.length).toBe(0);
  });

  it('hiện trạng thái: trợ lý hoạt động, chưa có kênh phụ (cảnh báo vàng), STK chỉ 3 số cuối', async () => {
    mockApi(state());
    renderCard(<><IntelCard part="trial" /><IntelCard part="controls" /></>);
    const card = await screen.findByRole('region', { name: 'Khoá, xác minh và cảnh báo' });
    expect(await within(card).findByText('Đang hoạt động')).toBeInTheDocument();
    expect(within(card).getByText('Chưa có kênh phụ')).toBeInTheDocument();
    expect(card).toHaveTextContent('…789');
    expect(card).not.toHaveTextContent('[object Object]');
  });

  it('khoá trợ lý gọi POST /intel/lock-assistant; khi đã khoá chỉ còn nút Mở khoá (cần PIN)', async () => {
    const calls = mockApi(state());
    renderCard(<><IntelCard part="trial" /><IntelCard part="controls" /></>);
    await userEvent.click(await screen.findByRole('button', { name: 'Khoá trợ lý' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/intel/lock-assistant')).toBe(true));
  });

  it('đang khoá: Mở khoá trợ lý gọi unlock-assistant, Mở khoá hôm nay bị tắt', async () => {
    const calls = mockApi(state({ assistant: { locked: true, locked_at: '2026-10-25T01:00:00Z', locked_via: 'console' } }));
    renderCard(<><IntelCard part="trial" /><IntelCard part="controls" /></>);
    const card = await screen.findByRole('region', { name: 'Khoá, xác minh và cảnh báo' });
    expect(await within(card).findByText(/Đang khoá/)).toBeInTheDocument();
    expect(within(card).queryByRole('button', { name: 'Khoá trợ lý' })).toBeNull();
    expect(within(card).getByRole('button', { name: /Mở khoá hôm nay/ })).toBeDisabled();
    await userEvent.click(within(card).getByRole('button', { name: /Mở khoá trợ lý/ }));
    await waitFor(() => expect(calls.some((c) => c.url === '/intel/unlock-assistant' && c.method === 'POST')).toBe(true));
  });

  it('Mở khoá hôm nay gọi POST /intel/unlock-today', async () => {
    const calls = mockApi(state());
    renderCard(<><IntelCard part="trial" /><IntelCard part="controls" /></>);
    await userEvent.click(await screen.findByRole('button', { name: /Mở khoá hôm nay/ }));
    await waitFor(() => expect(calls.some((c) => c.url === '/intel/unlock-today')).toBe(true));
  });

  it('chuyển sang Chạy thử gửi PUT /intel/settings {mode:"shadow"}', async () => {
    const calls = mockApi(state());
    renderCard(<><IntelCard part="trial" /><IntelCard part="controls" /></>);
    await userEvent.click(await screen.findByRole('radio', { name: 'Chạy thử' }));
    await waitFor(() => expect(calls.find((c) => c.url === '/intel/settings')?.body).toEqual({ mode: 'shadow' }));
  });

  it('tắt báo lừa đảo gọi POST /intel/mutes kind=rui_ro_lua_dao; bật lại gọi DELETE', async () => {
    const calls = mockApi(state());
    renderCard(<><IntelCard part="trial" /><IntelCard part="controls" /></>);
    await userEvent.click(await screen.findByRole('switch', { name: 'Báo Rủi ro lừa đảo' }));
    await waitFor(() => expect(calls.find((c) => c.url === '/intel/mutes' && c.method === 'POST')?.body).toEqual({ scope_type: 'kind', scope_value: 'rui_ro_lua_dao' }));
  });

  it('đang tắt báo an toàn: công tắc tắt + huy hiệu, bật lại gọi DELETE /intel/mutes/{id}', async () => {
    const calls = mockApi(state({ mutes: [{ id: 'm1', scope_value: 'suc_khoe_an_toan', source: 'pin', until: null }] }));
    renderCard(<><IntelCard part="trial" /><IntelCard part="controls" /></>);
    const sw = await screen.findByRole('switch', { name: 'Báo Sức khoẻ & an toàn' });
    expect(sw).toHaveAttribute('aria-checked', 'false');
    await userEvent.click(sw);
    await waitFor(() => expect(calls.some((c) => c.url === '/intel/mutes/m1' && c.method === 'DELETE')).toBe(true));
  });

  it('xác minh STK gọi verify-payee của đúng tín hiệu', async () => {
    const calls = mockApi(state());
    renderCard(<><IntelCard part="trial" /><IntelCard part="controls" /></>);
    await userEvent.click(await screen.findByRole('button', { name: /Xác minh số tài khoản VCB/ }));
    await waitFor(() => expect(calls.some((c) => c.url === '/intel/signals/sig-1/verify-payee' && c.method === 'POST')).toBe(true));
  });

  it('gán bối cảnh hàng loạt: chọn 2 nhóm + Kinh doanh gửi đúng thân', async () => {
    const calls = mockApi(state());
    renderCard(<><IntelCard part="trial" /><IntelCard part="controls" /></>);
    const card = await screen.findByRole('region', { name: 'Khoá, xác minh và cảnh báo' });
    const btn = await within(card).findByRole('button', { name: 'Gán cho nhóm đã chọn' });
    expect(btn).toBeDisabled();
    await userEvent.click(within(card).getByRole('checkbox', { name: /Nhóm Quán/ }));
    await userEvent.click(within(card).getByRole('checkbox', { name: /Nhóm Bạn/ }));
    fireEvent.change(within(card).getByLabelText('Bối cảnh'), { target: { value: 'kinh_doanh' } });
    await userEvent.click(within(card).getByRole('button', { name: 'Gán cho 2 nhóm' }));
    await waitFor(() => expect(calls.find((c) => c.url === '/intel/group-contexts/bulk')?.body).toEqual({ group_ids: ['g1', 'g2'], labels: [{ context: 'kinh_doanh', is_primary: true }] }));
  });

  it('lỗi máy chủ hiện chuỗi thân thiện + Chi tiết kỹ thuật, không render object', async () => {
    mockApi(state(), (c) => (c.url === '/intel/lock-assistant' ? json(403, { status: 403, code: 'NOT_OWNER', title: 'Không có quyền', detail: 'Chỉ Sếp mới khoá được trợ lý' }) : undefined));
    renderCard(<><IntelCard part="trial" /><IntelCard part="controls" /></>);
    await userEvent.click(await screen.findByRole('button', { name: 'Khoá trợ lý' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Chỉ Sếp mới khoá được trợ lý');
    expect(alert).toHaveTextContent('Chi tiết kỹ thuật');
    expect(alert).not.toHaveTextContent('[object Object]');
  });

  it('máy chủ lỗi khi tải: báo lỗi có nút Thử lại', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json(500, { status: 500, code: 'INTERNAL', title: 'Lỗi máy chủ' })));
    renderCard(<IntelCard part="controls" />);
    expect(await screen.findByRole('button', { name: /Thử lại/ })).toBeInTheDocument();
  });
});

describe('<AiBudgetCard> trần tình báo intel_*', () => {
  it('hiện 7 khoá, chỉ gửi khoá đã sửa; ô trống về mặc định (null); giá trị sai chặn nút Lưu', async () => {
    const calls = mockApi(state());
    renderCard(<AiBudgetCard />);
    const box = await screen.findByTestId('intel-caps');
    expect(within(box).getAllByRole('textbox')).toHaveLength(7);
    const field = within(box).getByLabelText(/Ngân sách tình báo mỗi ngày/);
    const save = within(box).getByRole('button', { name: /Lưu trần tình báo/ });
    expect(save).toBeDisabled();
    fireEvent.change(field, { target: { value: '12abc' } });
    expect(save).toBeDisabled();
    expect(within(box).getByText('Nhập số nguyên không âm')).toBeInTheDocument();
    fireEvent.change(field, { target: { value: '50.000' } });
    fireEvent.change(within(box).getByLabelText(/Trần lượt gọi mỗi ngày/), { target: { value: '' } });
    await userEvent.click(save);
    await waitFor(() => expect(calls.find((c) => c.url === '/system/ai-cost/intel' && c.method === 'PUT')?.body).toEqual({ intel_daily_budget_vnd: 50000, intel_daily_call_cap: null }));
  });
});

describe('phản hồi sai khuôn', () => {
  it('máy chủ trả {} cho /intel/console ⇒ lỗi tại chỗ có Thử lại, thẻ không vỡ', async () => {
    mockApi(state(), (c) => (c.url === '/intel/console' ? json(200, {}) : undefined));
    renderCard(<IntelCard part="controls" />);
    expect(await screen.findByRole('button', { name: /Thử lại/ })).toBeInTheDocument();
  });
});

describe('hộp xác nhận cho việc hạ phòng thủ và nhãn nhóm', () => {
  it('Huỷ xác nhận ⇒ không gọi máy chủ: tắt báo lừa đảo, chạy thử, mở khoá trợ lý', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    const calls = mockApi(state({ assistant: { locked: true, locked_at: null, locked_via: 'console' } }));
    renderCard(<><IntelCard part="trial" /><IntelCard part="controls" /></>);
    await userEvent.click(await screen.findByRole('switch', { name: 'Báo Rủi ro lừa đảo' }));
    await userEvent.click(screen.getByRole('radio', { name: 'Chạy thử' }));
    await userEvent.click(screen.getByRole('button', { name: /Mở khoá trợ lý/ }));
    expect(window.confirm).toHaveBeenCalledTimes(3);
    expect(calls.filter((c) => c.method !== 'GET')).toEqual([]);
  });

  it('nói rõ gán hàng loạt THAY TOÀN BỘ bối cảnh đã gán', async () => {
    mockApi(state());
    renderCard(<><IntelCard part="trial" /><IntelCard part="controls" /></>);
    expect(await screen.findByText(/THAY TOÀN BỘ bối cảnh/)).toBeInTheDocument();
  });

  it.each([
    ['nhóm thiếu name', { groups: [{ id: 'g1', labels: [] }] }],
    ['nhóm labels không phải mảng', { groups: [{ id: 'g1', name: 'A', labels: null }] }],
    ['STK thiếu title', { payees: [{ signal_id: 's', last3: '1', bank: null }] }],
  ])('%s ⇒ lỗi tại chỗ, thẻ không vỡ', async (_n, over) => {
    mockApi(state(over as Partial<IntelConsole>));
    renderCard(<IntelCard part="controls" />);
    expect(await screen.findByRole('button', { name: /Thử lại/ })).toBeInTheDocument();
  });
});
