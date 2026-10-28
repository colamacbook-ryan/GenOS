import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { IntelCard } from '../../src/intel/IntelCard';
import { IntelSetupCard } from '../../src/intel/IntelSetupCard';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';
import { rampBannerText, reasonText, setupPendingText, type IntelConsole, type IntelRampState, type IntelSetup } from '../../src/intel/intelModel';

/** v0.1.77 (P4): banner chạy thử R4, dòng bóng + nút Sai 3 lý do, bước 3 "Khách chủ chốt" (R2) — đúng API, tiếng Việt có dấu, lỗi thân thiện. */

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json' } });

const me = (role: string) => ({
  id: 'u', email: 'o@x.vn', display_name: 'Owner', role: { code: role, name: role },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
  pin_verified_until: null, permissions: { 'system.read': 'all', 'system.manage': 'all' },
});

const ramp = (over: Partial<IntelRampState> = {}): IntelRampState => ({
  started_at: '2026-10-26T03:00:00Z', ends_at: '2026-11-09T03:00:00Z', days: 14, days_left: 12, in_trial: true, passed_gates: [],
  m1: { n: 4, k: 3, p: 0.75, lo95: 0.3, ok: false, min_n: 10, wrong_by_reason: { still_buying: 1 }, low_precision_gates: [] }, ...over,
});

function consoleState(over: Partial<IntelConsole> = {}): IntelConsole {
  return {
    pin_active: false, mode: 'live', ramp_gates: 'digest_top', assistant: { locked: false, locked_at: null, locked_via: null },
    outbound: { unlocked_today: false, locked_until: null }, backup_channel: { paired: true, kind: 'telegram' }, mutes: [], payees: [], groups: [],
    contexts: ['khac'], regular_push_enabled: true, ramp: ramp(),
    shadow_items: [
      { id: 's1', kind: 'xung_dot_khieu_nai', gate: 'khach_che_cong_khai_doa_bo', title: 'Chị Mai chê công khai', reasons: ['shadow', 'key_customer', 'ma_la'], would_push_at: '2026-10-27T03:00:00Z', verdict: null },
      { id: 's2', kind: 'nghia_vu_loi_hua', gate: 'loi_hua_khach_sap_vo', title: 'Hứa giao 15h', reasons: ['shadow'], would_push_at: null, verdict: 'wrong' },
    ],
    ...over,
  };
}

function setup(over: Partial<IntelSetup> = {}): IntelSetup {
  return {
    ready: false,
    groups: { done: true, total: 1, unlabeled_count: 0, unlabeled: [] },
    staff: { done: true, confirmed_at: '2026-10-25T02:00:00Z', staff_count: 0, people: [] },
    key_customers: { done: false, confirmed_at: null, key_count: 1, customers: [
      { id: 'p1', entity_id: 'e1', name: 'Chị Mai', key_fact_id: 'f1', suggested: false },
      { id: 'p2', entity_id: 'e2', name: 'Chú Bảy', key_fact_id: null, suggested: true },
      { id: 'p3', entity_id: 'e3', name: 'Anh Tư', key_fact_id: null, suggested: false },
    ] },
    contexts: ['kinh_doanh'], gated_kinds: ['lời hứa với khách'], ...over,
  };
}

interface Call { url: string; method: string; body: unknown }
function mockApi(c: IntelConsole, s: IntelSetup, extra?: (c: Call) => Response | undefined) {
  const calls: Call[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const call: Call = { url: String(url).replace('/api/v1', ''), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    const x = extra?.(call);
    if (x) return x;
    if (call.url === '/intel/console') return json(200, c);
    if (call.url === '/intel/setup') return json(200, s);
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
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('intelModel v0.1.77', () => {
  it('rampBannerText: nói từ ngày nào, còn mấy ngày, đã chấm mấy lượt; hết thử thì rỗng', () => {
    const t = rampBannerText(ramp());
    expect(t).toContain('Chạy thử lại 14 ngày (từ 26/10), còn 12 ngày');
    expect(t).toContain('Sếp đã chấm 4/10 lượt');
    expect(t).toContain('Lừa đảo và an toàn vẫn báo ngay');
    expect(rampBannerText(ramp({ in_trial: false }))).toBe('');
    expect(rampBannerText(undefined)).toBe('');
    expect(rampBannerText(ramp({ m1: { ...ramp().m1, n: 12, ok: false } }))).toContain('chưa đủ chính xác để nhắn ngay');
  });

  it('rampBannerText: cài mới (renewed=false) nói "Chạy thử", không "lại"; reasonText dùng trần đã cấu hình', () => {
    expect(rampBannerText(ramp({ renewed: false }))).toMatch(/^Chạy thử 14 ngày/);
    expect(rampBannerText(ramp({ renewed: true }))).toMatch(/^Chạy thử lại 14 ngày/);
    expect(reasonText(['capped'], 5)).toEqual(['quá 5 tin báo ngay hôm nay']);
    expect(reasonText(['capped'])).toEqual(['quá 3 tin báo ngay hôm nay']);
  });

  it('reasonText: mã lạ bị bỏ, không lộ mã máy', () => {
    expect(reasonText(['shadow', 'key_customer', 'ma_la'])).toEqual(['đang chạy thử, chưa nhắn riêng', 'khách chủ chốt (Sếp đã đánh dấu)']);
  });

  it('setupPendingText nêu thêm khách chủ chốt khi chưa khai', () => {
    expect(setupPendingText(setup())).toBe('chưa khai khách chủ chốt');
  });
});

describe('<IntelCard> chạy thử R4 + nút Sai 3 lý do', () => {
  it('banner chạy thử hiện; hết thử thì không có banner', async () => {
    mockApi(consoleState(), setup());
    const { unmount } = renderCard(<IntelCard part="trial" />);
    expect(await screen.findByTestId('intel-ramp-banner')).toHaveTextContent('Chạy thử lại 14 ngày (từ 26/10)');
    unmount();
    queryClient.clear();
    mockApi(consoleState({ ramp: ramp({ in_trial: false }) }), setup());
    renderCard(<IntelCard part="trial" />);
    await screen.findByRole('region', { name: 'Chạy thử 14 ngày' });
    await waitFor(() => expect(screen.queryByTestId('intel-ramp-banner')).toBeNull());
  });

  it('dòng bóng: hiện loại, giờ "đáng lẽ báo ngay", lý do bằng chữ (không mã máy); mục đã chấm hiện nhãn', async () => {
    mockApi(consoleState(), setup());
    renderCard(<IntelCard part="trial" />);
    const row = await screen.findByTestId('intel-shadow');
    expect(row).toHaveTextContent('Khách chê / doạ bỏ');
    expect(row).toHaveTextContent('Chị Mai chê công khai');
    expect(row).toHaveTextContent('Đáng lẽ báo ngay lúc');
    expect(row).toHaveTextContent('khách chủ chốt (Sếp đã đánh dấu)');
    expect(row).not.toHaveTextContent('ma_la');
    expect(row).not.toHaveTextContent('[object Object]');
    expect(within(row).getByText('Đã chấm Sai')).toBeInTheDocument();
  });

  it('bấm Đúng ⇒ POST feedback verdict correct, không kèm lý do', async () => {
    const calls = mockApi(consoleState(), setup());
    renderCard(<IntelCard part="trial" />);
    const row = await screen.findByTestId('intel-shadow');
    await userEvent.click(within(row).getByRole('button', { name: 'Báo đúng: Chị Mai chê công khai' }));
    await waitFor(() => expect(calls.find((c) => c.url === '/signals/s1/feedback')).toMatchObject({ method: 'POST', body: { verdict: 'correct' } }));
    expect(calls.find((c) => c.url === '/signals/s1/feedback')?.body).not.toHaveProperty('reason');
  });

  it('bấm Sai ⇒ hỏi 3 lý do; chọn lý do ⇒ POST feedback verdict wrong + reason; Huỷ ⇒ không gửi', async () => {
    const calls = mockApi(consoleState(), setup());
    renderCard(<IntelCard part="trial" />);
    const row = await screen.findByTestId('intel-shadow');
    await userEvent.click(within(row).getByRole('button', { name: 'Báo sai: Chị Mai chê công khai' }));
    const grp = within(row).getByRole('group', { name: 'Vì sao báo sai?' });
    expect(within(grp).getAllByRole('button').map((b) => b.textContent)).toEqual(['Khách vẫn mua', 'Không gấp', 'NV lo rồi', 'Huỷ']);
    await userEvent.click(within(grp).getByRole('button', { name: 'Huỷ' }));
    expect(calls.some((c) => c.url.endsWith('/feedback'))).toBe(false);
    await userEvent.click(within(row).getByRole('button', { name: 'Báo sai: Chị Mai chê công khai' }));
    await userEvent.click(within(row).getByRole('button', { name: 'Sai: Khách vẫn mua' }));
    await waitFor(() => expect(calls.find((c) => c.url === '/signals/s1/feedback')).toMatchObject({ method: 'POST', body: { verdict: 'wrong', reason: 'still_buying' } }));
  });

  it('lỗi máy chủ khi chấm: chữ thân thiện + Chi tiết kỹ thuật, không render object', async () => {
    mockApi(consoleState(), setup(), (c) => (c.url === '/signals/s1/feedback' ? json(422, { status: 422, code: 'VALIDATION', title: 'Lý do chỉ dùng khi bấm “Sai”', detail: 'reason chỉ cho wrong' }) : undefined));
    renderCard(<IntelCard part="trial" />);
    const row = await screen.findByTestId('intel-shadow');
    await userEvent.click(within(row).getByRole('button', { name: 'Báo đúng: Chị Mai chê công khai' }));
    const alert = await within(row).findByRole('alert');
    expect(alert).toHaveTextContent('reason chỉ cho wrong');
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(row).not.toHaveTextContent('[object Object]');
  });

  it('máy chủ cũ không gửi ramp/shadow_items ⇒ thẻ không vỡ, không banner, không dòng bóng', async () => {
    const old = consoleState();
    delete old.ramp;
    delete old.shadow_items;
    mockApi(old, setup());
    renderCard(<IntelCard part="trial" />);
    await screen.findByRole('region', { name: 'Chạy thử 14 ngày' });
    expect(screen.queryByTestId('intel-ramp-banner')).toBeNull();
    expect(screen.queryByTestId('intel-shadow')).toBeNull();
  });

  it('bốn cổng thường mở: có lựa chọn "Nhắn ngay từ sớm" (regular_push_enabled = true)', async () => {
    mockApi(consoleState(), setup());
    renderCard(<IntelCard part="trial" />);
    const row = await screen.findByTestId('intel-mode');
    expect(within(row).getByRole('radio', { name: 'Nhắn ngay từ sớm' })).toBeInTheDocument();
    expect(within(row).queryByText('Đầu bản tin (bản này)')).toBeNull();
  });
});

describe('<IntelSetupCard> bước 3 Khách chủ chốt (R2)', () => {
  it('ba bước: tiến độ n/3, gợi ý của trợ lý hiện nhưng chưa tính, khách chủ chốt hiện bật', async () => {
    mockApi(consoleState(), setup());
    renderCard(<IntelSetupCard />);
    const card = await screen.findByRole('region', { name: 'Thiết lập Tình báo' });
    expect(await within(card).findByText('Ba bước để Gen hiểu đúng nhóm, nhân viên và khách chủ chốt của Sếp')).toBeInTheDocument();
    expect(within(card).getByRole('img', { name: 'Đã xong 2 trên 3 bước' })).toBeInTheDocument();
    const step = within(card).getByTestId('intel-setup-key-customers');
    expect(within(step).getByRole('switch', { name: 'Chị Mai là khách chủ chốt' })).toBeChecked();
    expect(within(step).getByRole('switch', { name: 'Chú Bảy là khách chủ chốt' })).not.toBeChecked();
    expect(step).toHaveTextContent('Trợ lý gợi ý — chờ Sếp xác nhận');
    expect(step).toHaveTextContent('Gen chỉ gợi ý tên, không tự chấm điểm');
    expect(step).not.toHaveTextContent('kênh phụ');                          // không hứa điều chưa có
    expect(step).toHaveTextContent('Bật hoặc tắt cần mã PIN');
  });

  it('bật ⇒ POST /me/profile/items key_relations + object_entity_id (đường Hồ sơ, máy chủ đòi PIN)', async () => {
    const calls = mockApi(consoleState(), setup());
    renderCard(<IntelSetupCard />);
    const step = await screen.findByTestId('intel-setup-key-customers');
    await userEvent.click(within(step).getByRole('switch', { name: 'Anh Tư là khách chủ chốt' }));
    await waitFor(() => expect(calls.find((c) => c.url === '/me/profile/items')).toMatchObject({ method: 'POST', body: { section: 'key_relations', object_entity_id: 'e3' } }));
  });

  it('tắt ⇒ DELETE mục Hồ sơ đã xác nhận', async () => {
    const calls = mockApi(consoleState(), setup());
    renderCard(<IntelSetupCard />);
    const step = await screen.findByTestId('intel-setup-key-customers');
    await userEvent.click(within(step).getByRole('switch', { name: 'Chị Mai là khách chủ chốt' }));
    await waitFor(() => expect(calls.find((c) => c.url === '/me/profile/items/f1')).toMatchObject({ method: 'DELETE' }));
  });

  it('"Đã khai xong" ⇒ POST key-customers-confirmed; không có khách thì nút nói rõ', async () => {
    const calls = mockApi(consoleState(), setup());
    renderCard(<IntelSetupCard />);
    const step = await screen.findByTestId('intel-setup-key-customers');
    await userEvent.click(within(step).getByRole('button', { name: 'Đã khai xong (1 khách chủ chốt)' }));
    await waitFor(() => expect(calls.some((c) => c.url === '/intel/setup/key-customers-confirmed' && c.method === 'POST')).toBe(true));
    queryClient.clear();
    mockApi(consoleState(), setup({ key_customers: { done: false, confirmed_at: null, key_count: 0, customers: [] } }));
    const second = renderCard(<IntelSetupCard />);
    expect(await within(second.container).findByRole('button', { name: 'Đã khai xong — chưa có khách chủ chốt' })).toBeInTheDocument();
  });

  it('lỗi khi bật (vd 403/422): chữ thân thiện + Chi tiết kỹ thuật, không render object', async () => {
    mockApi(consoleState(), setup(), (c) => (c.url === '/me/profile/items' ? json(422, { status: 422, code: 'VALIDATION', title: 'Không ghi được mục Hồ sơ', detail: 'bad_entity' }) : undefined));
    renderCard(<IntelSetupCard />);
    const step = await screen.findByTestId('intel-setup-key-customers');
    await userEvent.click(within(step).getByRole('switch', { name: 'Anh Tư là khách chủ chốt' }));
    const alert = await within(step).findByRole('alert');
    expect(alert).toHaveTextContent('bad_entity');
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(step).not.toHaveTextContent('[object Object]');
  });

  it('máy chủ cũ không có key_customers ⇒ vẫn hai bước như cũ', async () => {
    const s = setup({ groups: { done: false, total: 1, unlabeled_count: 1, unlabeled: [{ id: 'g1', name: 'N', member_count: 1 }] } });
    delete s.key_customers;
    mockApi(consoleState(), s);
    renderCard(<IntelSetupCard />);
    const card = await screen.findByRole('region', { name: 'Thiết lập Tình báo' });
    expect(await within(card).findByText('Hai bước để Gen hiểu đúng nhóm và người của Sếp')).toBeInTheDocument();
    expect(within(card).queryByTestId('intel-setup-key-customers')).toBeNull();
  });

  it('đã xong cả ba: tóm tắt nêu số khách chủ chốt, có mục sửa danh sách', async () => {
    const s = setup({ ready: true, key_customers: { done: true, confirmed_at: '2026-10-26T01:00:00Z', key_count: 1, customers: setup().key_customers!.customers } });
    mockApi(consoleState(), s);
    renderCard(<IntelSetupCard />);
    expect(await screen.findByTestId('intel-setup-ready')).toHaveTextContent('1 khách chủ chốt');
    expect(screen.getByText('Sửa danh sách khách chủ chốt')).toBeInTheDocument();
  });
});
