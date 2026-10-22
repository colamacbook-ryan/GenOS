import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { JevBenchmark, Provider, ProviderTestResult, SkippedPage, ValueSummary } from '@gen-harness/contracts';
import { JevCard } from '../../src/screens/system/JevCard';
import { qk } from '../../src/lib/queries';

/**
 * v0.1.55 (G4) — thẻ Jev: "Bật Jev 1 chạm", cảnh báo QD-12 (tin đã che số điện thoại/email gửi sang OpenRouter),
 * "Thử 12 câu mẫu" chỉ Owner + bảng kết quả, lỗi thiếu khoá = chuỗi + "Chi tiết kỹ thuật" (không render object),
 * số đo giá trị, công tắc "Lọc trước khi trích xuất", link "Tin đã bỏ qua (N)", địa chỉ/model trong "Nâng cao".
 */

const OR_KEY = 'sk-or-v1-khoa-openrouter-that-12345678';
const WARNING = /Tin đã che số điện thoại\/email được gửi sang OpenRouter/;

function meOf(role: 'owner' | 'manager') {
  return {
    id: 'u', email: `${role}@genesis.local`, display_name: role, role: { code: role, name: role },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
    addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
    permissions: { 'system.read': 'all', 'system.manage': 'all', 'queue.read': 'all' },
  };
}

function provider(kind: Provider['kind'], over: Partial<Provider> = {}): Provider {
  return {
    id: kind === 'system_one' ? 'jev1' : 'or1', kind, name: kind === 'system_one' ? 'Jev (System One)' : 'OpenRouter',
    endpoint: 'https://openrouter.ai/api/v1', failover_rank: 9, enabled: true, auth_state: 'ok',
    keys: [{ id: 'k', label: kind === 'system_one' ? 'JEV-KEY-01' : 'API-KEY-01', last4: '5678' }] as Provider['keys'],
    models: [{ id: 'm', model_name: kind === 'system_one' ? 'typesafe/jev-1.13' : 'openrouter/auto', daily_quota: null, used_today: 0 }] as unknown as Provider['models'],
    last_test: null,
    ...over,
  };
}

const JEV = provider('system_one');
const OPENROUTER = provider('openai_compat');

const ROWS: [string, string, string | null, boolean, number, string?][] = Array.from({ length: 12 }, (_, i) => [
  `Câu mẫu số ${i + 1}`, 'hỏi dữ liệu / tóm tắt tình hình', i === 3 ? 'xin báo cáo có số liệu' : 'hỏi dữ liệu / tóm tắt tình hình', i !== 3 && i !== 7, 400 + i,
]);
const BENCH: JevBenchmark = {
  total: 12, correct: 10, avg_latency_ms: 420,
  items: ROWS.map(([question, expected, got, ok, latency_ms], i) => ({ question, expected, got: i === 7 ? null : got, ok, latency_ms, ...(i === 7 ? { error_text: 'HTTP 500: hỏng' } : {}) })),
};
const VALUE: ValueSummary = { filtered: 18, spam_blocked: 11, calls_saved: 7, jev_on: true };
const SKIPPED: SkippedPage = {
  total: 2, days: 30,
  items: [
    { id: 's1', code: 'RAW-000101', at: '2026-10-10T03:00:00Z', kind: 'text', reason: 'spam_rule_jev', reason_text: 'Rác — quy tắc và Jev cùng chấm rác', text: 'KHUYẾN MÃI SỐC — liên hệ •••••••678', group: 'Nhóm Thép Phát', person: 'Số lạ 7' },
    { id: 's2', code: 'RAW-000102', at: '2026-10-10T02:00:00Z', kind: 'text', reason: 'exact_dup', reason_text: 'Trùng hẳn một tin đã có', text: 'Cần 3 container thép cuộn giao Bình Dương', group: null, person: 'Chị Lan Phạm' },
  ],
};
const OK_TEST: ProviderTestResult = { ok: true, latency_ms: 164, models: ['typesafe/jev-1.13'], error: null };

const reply = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json' } });
const problem = (status: number, code: string, title: string, extra: Record<string, unknown> = {}) =>
  reply({ type: `https://gen-harness.local/errors/${code.toLowerCase()}`, title, status, code, detail: null, ...extra }, status);

interface Opts {
  role?: 'owner' | 'manager';
  providers?: Provider[];
  benchmark?: () => Response;
  enable?: (body: Record<string, unknown>) => Response;
  /** Trả nguyên văn (kể cả sai kiểu) cho các đường số liệu — kiểm tra thẻ không sập. */
  weird?: boolean;
}

function setup(o: Opts = {}) {
  const role = o.role ?? 'owner';
  const calls: { method: string; path: string; body: Record<string, unknown> | null }[] = [];
  let settings = { enabled: true, min_score: 30, use_jev: true, prefilter: true };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), 'http://x').pathname.replace(/^\/api\/v1/, '');
    const method = init?.method ?? 'GET';
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : null;
    calls.push({ method, path, body });
    if (path === '/auth/me') return reply(meOf(role));
    if (path === '/providers') return reply(o.providers ?? []);
    if (/^\/providers\/[^/]+\/test$/.test(path)) return reply(OK_TEST);
    if (o.weird && /\/(jev|refinery)\//.test(path)) return reply([]);
    if (path === '/refinery/triage/settings') {
      if (method === 'PATCH') settings = { ...settings, ...(body as object) };
      return reply(settings);
    }
    if (path === '/refinery/triage/skipped') return reply(SKIPPED);
    if (path === '/jev/value-summary') return role === 'owner' ? reply(VALUE) : problem(403, 'FORBIDDEN', 'Chỉ Owner');
    if (path === '/jev/benchmark') return role !== 'owner' ? problem(403, 'FORBIDDEN', 'Chỉ Owner') : (o.benchmark?.() ?? reply(BENCH));
    if (path === '/jev/enable') {
      return o.enable?.(body ?? {}) ?? reply({ provider_id: 'jev1', created: true, key_source: body?.use_existing_openrouter ? 'existing_openrouter' : 'pasted', endpoint: 'https://openrouter.ai/api/v1', model: 'typesafe/jev-1.13' });
    }
    return reply([]);
  });
  vi.stubGlobal('fetch', fetchMock);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, meOf(role));
  const view = render(
    <QueryClientProvider client={qc}>
      <JevCard />
    </QueryClientProvider>,
  );
  const sent = (path: string, method = 'POST') => calls.filter((c) => c.path === path && c.method === method);
  return { view, calls, sent };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('v0.1.55 — "Bật Jev 1 chạm" và cảnh báo QD-12', () => {
  it('chưa có Jev: preset điền sẵn, ô dán khoá (không có nút dùng khoá OpenRouter), cảnh báo QD-12 một dòng', async () => {
    setup({ providers: [] });
    expect(await screen.findByText('Bật Jev 1 chạm')).toBeInTheDocument();
    expect(screen.getByText('typesafe/jev-1.13')).toBeInTheDocument();
    expect(screen.getByText(WARNING)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Dùng khoá OpenRouter đang có/ })).not.toBeInTheDocument();
    const bat = screen.getByRole('button', { name: /Bật Jev$/ });
    expect(bat).toBeDisabled();
    // vẫn giữ rào PIN của việc tạo nguồn: báo trước ngay cạnh nút
    expect(within(bat.parentElement as HTMLElement).getByText(/Cần mã PIN 6 số/)).toBeInTheDocument();
  });

  it('đã có Jev: cảnh báo QD-12 vẫn hiện; địa chỉ + model nằm trong khối "Nâng cao" (đóng sẵn)', async () => {
    setup({ providers: [JEV] });
    expect(await screen.findByText(WARNING)).toBeInTheDocument();
    const model = screen.getByText('typesafe/jev-1.13');
    const adv = model.closest('details')!;
    expect(adv).toHaveClass('brain-advanced');
    expect(adv).not.toHaveAttribute('open');
    expect(within(adv).getByText('https://openrouter.ai/api/v1')).toBeInTheDocument();
    expect(adv.querySelector('summary')).toHaveTextContent('Nâng cao');
    // phần thân thẻ không còn dòng "Địa chỉ" ngoài khối Nâng cao
    expect(screen.getAllByText('Địa chỉ')).toHaveLength(1);
  });

  it('có nguồn OpenRouter: một nút "Dùng khoá OpenRouter đang có" — gửi use_existing_openrouter, KHÔNG gửi khoá, rồi kiểm tra', async () => {
    const { sent, view } = setup({ providers: [OPENROUTER] });
    await userEvent.setup().click(await screen.findByRole('button', { name: /Dùng khoá OpenRouter đang có/ }));
    await waitFor(() => expect(sent('/jev/enable')).toHaveLength(1));
    expect(sent('/jev/enable')[0].body).toEqual({ use_existing_openrouter: true });
    await waitFor(() => expect(sent('/providers/jev1/test')).toHaveLength(1));    // dùng provider_id máy chủ trả về
    expect(view.container.textContent).not.toContain('[object Object]');
  });

  it('dán khoá: nút "Bật Jev" chỉ mở khi đủ dài; gửi {use_existing_openrouter:false, key}; ô khoá được xoá sau khi bật', async () => {
    const { sent } = setup({ providers: [] });
    const user = userEvent.setup();
    const field = await screen.findByLabelText('Khoá OpenRouter');
    const bat = screen.getByRole('button', { name: /Bật Jev$/ });
    await user.type(field, 'ngan');
    expect(bat).toBeDisabled();
    await user.clear(field);
    await user.type(field, OR_KEY);
    expect(bat).toBeEnabled();
    await user.click(bat);
    await waitFor(() => expect(sent('/jev/enable')).toHaveLength(1));
    expect(sent('/jev/enable')[0].body).toEqual({ use_existing_openrouter: false, key: OR_KEY });
    await waitFor(() => expect(field).toHaveValue(''));
    await waitFor(() => expect(sent('/providers/jev1/test')).toHaveLength(1));
  });

  it('bật lỗi (thiếu khoá): hiện chuỗi thân thiện + "Chi tiết kỹ thuật", không render object', async () => {
    const { view } = setup({
      providers: [OPENROUTER],
      enable: () => problem(409, 'JEV_KEY_MISSING', 'Chưa có khoá OpenRouter cho Jev', { reasons: ['Không có nguồn kind=system_one'] }),
    });
    await userEvent.setup().click(await screen.findByRole('button', { name: /Dùng khoá OpenRouter đang có/ }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Chưa có khoá OpenRouter cho Jev');
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(alert).toHaveTextContent('JEV_KEY_MISSING');
    expect(view.container.textContent).not.toContain('[object Object]');
  });
});

describe('v0.1.55 — "Thử 12 câu mẫu"', () => {
  it('chỉ Owner thấy nút; Manager (có system.manage) không thấy, và không gọi /jev/value-summary', async () => {
    const o = setup({ role: 'owner', providers: [JEV] });
    expect(await screen.findByRole('button', { name: 'Thử 12 câu mẫu' })).toBeInTheDocument();
    o.view.unmount();
    const m = setup({ role: 'manager', providers: [JEV] });
    await screen.findByText(WARNING);
    expect(screen.queryByRole('button', { name: 'Thử 12 câu mẫu' })).not.toBeInTheDocument();
    expect(m.calls.some((c) => c.path === '/jev/value-summary')).toBe(false);
  });

  it('chưa có nguồn Jev: không có nút thử', async () => {
    setup({ providers: [] });
    await screen.findByText('Bật Jev 1 chạm');
    expect(screen.queryByRole('button', { name: 'Thử 12 câu mẫu' })).not.toBeInTheDocument();
  });

  it('bấm → bảng 12 dòng: "Đúng 10/12", độ trễ trung bình, Đúng/Sai từng câu, lỗi từng câu là chuỗi', async () => {
    const { sent, view } = setup({ providers: [JEV] });
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Thử 12 câu mẫu' }));
    expect(await screen.findByText('Đúng 10/12 · trung bình 420 ms')).toBeInTheDocument();
    expect(sent('/jev/benchmark')).toHaveLength(1);
    const table = screen.getByRole('table');
    expect(within(table).getAllByRole('row')).toHaveLength(13);                         // 12 câu + dòng tiêu đề
    expect(within(table).getAllByText('Sai')).toHaveLength(2);
    expect(within(table).getAllByText('Đúng')).toHaveLength(10);
    expect(within(table).getByText('Câu mẫu số 1')).toBeInTheDocument();
    expect(within(table).getByText('HTTP 500: hỏng')).toBeInTheDocument();
    expect(within(table).getByText('Độ trễ')).toBeInTheDocument();
    expect(view.container.textContent).not.toContain('[object Object]');
  });

  it('thiếu khoá (409): chuỗi thân thiện + "Chi tiết kỹ thuật", không render object', async () => {
    const { view } = setup({
      providers: [JEV],
      benchmark: () => problem(409, 'JEV_KEY_MISSING', 'Chưa có khoá OpenRouter cho Jev', { reasons: ['Không có nguồn model kind=system_one đang bật kèm khoá'] }),
    });
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Thử 12 câu mẫu' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Chưa có khoá OpenRouter cho Jev');
    const tech = within(alert).getByText('Chi tiết kỹ thuật');
    expect(tech.closest('details')).not.toHaveAttribute('open');
    expect(alert).toHaveTextContent('JEV_KEY_MISSING');
    expect(alert).toHaveTextContent('kind=system_one');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(view.container.textContent).not.toContain('[object Object]');
  });

  it('máy chủ trả kết quả lạ (mảng): báo chuỗi, không sập', async () => {
    const { view } = setup({ providers: [JEV], benchmark: () => reply([]) });
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Thử 12 câu mẫu' }));
    expect(await screen.findByText(/Chưa đọc được kết quả thử/)).toBeInTheDocument();
    expect(view.container.textContent).not.toContain('[object Object]');
  });
});

describe('v0.1.55 — số đo giá trị, lọc trước khi trích xuất, Tin đã bỏ qua', () => {
  it('Owner thấy dòng số đo 7 ngày (chỉ đếm lần, không có tiền)', async () => {
    setup({ providers: [JEV] });
    const line = await screen.findByTestId('jev-value');
    expect(line).toHaveTextContent('7 ngày qua: lọc 18 tin rác/trùng · chặn 11 tin rác · tiết kiệm 7 lượt gọi model');
    expect(line.textContent).not.toMatch(/₫|đồng|VND|\$/);
  });

  it('công tắc "Lọc trước khi trích xuất": Owner bật/tắt → PATCH {prefilter}; Manager chỉ xem', async () => {
    const o = setup({ providers: [JEV] });
    const sw = await screen.findByRole('switch', { name: 'Lọc trước khi trích xuất' });
    expect(sw).toHaveAttribute('aria-checked', 'true');
    await userEvent.setup().click(sw);
    await waitFor(() => expect(o.sent('/refinery/triage/settings', 'PATCH')).toHaveLength(1));
    expect(o.sent('/refinery/triage/settings', 'PATCH')[0].body).toEqual({ prefilter: false });
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Lọc trước khi trích xuất' })).toHaveAttribute('aria-checked', 'false'));
    o.view.unmount();
    setup({ role: 'manager', providers: [JEV] });
    expect(await screen.findByRole('switch', { name: 'Lọc trước khi trích xuất' })).toHaveAttribute('aria-disabled', 'true');
  });

  it('link "Tin đã bỏ qua (2)" mở danh sách chỉ đọc: lý do, nhóm/người, chữ (đã che do máy chủ)', async () => {
    const { view } = setup({ providers: [JEV] });
    const link = await screen.findByRole('button', { name: 'Tin đã bỏ qua (2)' });
    expect(link).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText(/KHUYẾN MÃI SỐC/)).not.toBeInTheDocument();
    await userEvent.setup().click(link);
    expect(link).toHaveAttribute('aria-expanded', 'true');
    const box = screen.getByTestId('jev-skipped');
    expect(within(box).getByText('Rác — quy tắc và Jev cùng chấm rác')).toBeInTheDocument();
    expect(within(box).getByText('Trùng hẳn một tin đã có')).toBeInTheDocument();
    expect(within(box).getByText(/liên hệ •••••••678/)).toBeInTheDocument();
    expect(within(box).getByText(/Tin riêng/)).toBeInTheDocument();                  // nhóm null → "Tin riêng"
    expect(within(box).queryAllByRole('button')).toHaveLength(1);                     // chỉ đọc: không nút sửa/xoá
    expect(view.container.textContent).not.toContain('[object Object]');
  });

  it('máy chủ cũ/trả lạ cho số đo + bỏ qua + cài đặt: ẩn các dòng đó, thẻ vẫn dùng được', async () => {
    const { view } = setup({ providers: [JEV], weird: true });
    expect(await screen.findByRole('button', { name: 'Thử 12 câu mẫu' })).toBeInTheDocument();
    expect(screen.getByText(WARNING)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId('jev-value')).not.toBeInTheDocument());
    expect(screen.queryByTestId('jev-skipped')).not.toBeInTheDocument();
    expect(screen.queryByRole('switch', { name: 'Lọc trước khi trích xuất' })).not.toBeInTheDocument();
    expect(view.container.textContent).not.toContain('[object Object]');
  });
});
