import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { IntelSetupCard } from '../../src/intel/IntelSetupCard';
import { IntelCard } from '../../src/intel/IntelCard';
import { SetupBanner } from '../../src/intel/TodayTab';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';
import { setupPendingText, type IntelConsole, type IntelSetup } from '../../src/intel/intelModel';

/** v0.1.76 (T14, Ryan chốt D3): thẻ "Thiết lập Tình báo" — hai bước, gọi đúng API sẵn có, banner nhắc ở thẻ Tình báo, lỗi thân thiện. */

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json' } });

const me = (role: string) => ({
  id: 'u', email: 'o@x.vn', display_name: 'Owner', role: { code: role, name: role },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
  pin_verified_until: null, permissions: { 'system.read': 'all', 'system.manage': 'all' },
});

function setup(over: Partial<IntelSetup> = {}): IntelSetup {
  return {
    ready: false,
    groups: { done: false, total: 3, unlabeled_count: 2, unlabeled: [{ id: 'g1', name: 'Khách sỉ Nhà Xinh', member_count: 40 }, { id: 'g2', name: 'Nhân viên cửa hàng', member_count: 6 }] },
    staff: { done: false, confirmed_at: null, staff_count: 0, people: [{ id: 'p1', name: 'Ngọc bán hàng', person_type: 'unknown', prev_type: null }, { id: 'p2', name: 'Chú Bảy', person_type: 'customer', prev_type: null },
      { id: 'p3', name: 'Chị Mai', person_type: 'staff', prev_type: 'customer' },
      { id: 'p4', name: 'Hùng cũ', person_type: 'staff', prev_type: null, needs_reconfirm: true }] },
    contexts: ['kinh_doanh', 'nhan_vien', 'gia_dinh', 'khac'],
    gated_kinds: ['lời hứa với khách', 'câu hỏi chờ trả lời'],
    ...over,
  };
}

const consoleState: IntelConsole = {
  pin_active: false, mode: 'live', ramp_gates: 'digest_top', assistant: { locked: false, locked_at: null, locked_via: null },
  outbound: { unlocked_today: false, locked_until: null }, backup_channel: { paired: true, kind: 'telegram' }, mutes: [], payees: [], groups: [], contexts: ['khac'],
  regular_push_enabled: false,
};

interface Call { url: string; method: string; body: unknown }
function mockApi(s: IntelSetup | (() => Response), extra?: (c: Call) => Response | undefined) {
  const calls: Call[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const call: Call = { url: String(url).replace('/api/v1', ''), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    const x = extra?.(call);
    if (x) return x;
    if (call.url === '/intel/setup') return typeof s === 'function' ? s() : json(200, s);
    if (call.url === '/intel/console') return json(200, consoleState);
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

describe('<IntelSetupCard> Thiết lập Tình báo', () => {
  it('vai không phải Owner không thấy thẻ và không gọi máy chủ', () => {
    const calls = mockApi(setup());
    renderCard(<IntelSetupCard />, 'manager');
    expect(screen.queryByRole('region', { name: 'Thiết lập Tình báo' })).toBeNull();
    expect(calls.length).toBe(0);
  });

  it('chưa xong: hai bước, tiến độ 0/2, chữ đúng việc backend làm (không hứa "bật khi xong 2 bước"), lừa đảo/an toàn vẫn chạy', async () => {
    mockApi(setup());
    renderCard(<IntelSetupCard />);
    const card = await screen.findByRole('region', { name: 'Thiết lập Tình báo' });
    expect(await within(card).findByText('Hai bước để Gen hiểu đúng nhóm và người của Sếp')).toBeInTheDocument();
    expect(card).not.toHaveTextContent('bật khi xong 2 bước');
    expect(card).toHaveTextContent('chỉ chạy ở nhóm đã gán bối cảnh');
    expect(card).toHaveTextContent('phân biệt khách và nhân viên');
    expect(within(card).getByRole('img', { name: 'Đã xong 0 trên 2 bước' })).toBeInTheDocument();
    expect(within(card).getByText('Còn 2 nhóm')).toBeInTheDocument();
    expect(card).toHaveTextContent('Lừa đảo và an toàn luôn báo');
    expect(card).toHaveTextContent('Khách hàng');                 // loại người hiện bằng chữ, không mã máy
    expect(card).not.toHaveTextContent('[object Object]');
  });

  it('bước 1: chọn bối cảnh từng nhóm rồi lưu ⇒ gọi API hàng loạt, gom theo bối cảnh', async () => {
    const calls = mockApi(setup());
    renderCard(<IntelSetupCard />);
    const step = await screen.findByTestId('intel-setup-groups');
    const save = within(step).getByRole('button', { name: 'Chọn bối cảnh để lưu' });
    expect(save).toBeDisabled();
    await userEvent.selectOptions(within(step).getByRole('combobox', { name: 'Bối cảnh của nhóm Khách sỉ Nhà Xinh' }), 'kinh_doanh');
    await userEvent.selectOptions(within(step).getByRole('combobox', { name: 'Bối cảnh của nhóm Nhân viên cửa hàng' }), 'nhan_vien');
    await userEvent.click(within(step).getByRole('button', { name: 'Lưu bối cảnh cho 2 nhóm' }));
    await waitFor(() => expect(calls.filter((c) => c.url === '/intel/group-contexts/bulk').map((c) => c.body)).toEqual([
      { group_ids: ['g1'], labels: [{ context: 'kinh_doanh', is_primary: true }] },
      { group_ids: ['g2'], labels: [{ context: 'nhan_vien', is_primary: true }] },
    ]));
  });

  it('bước 2: đánh dấu nhân viên qua PATCH /profile/{id}, rồi "Đã khai xong" gọi POST staff-confirmed', async () => {
    const calls = mockApi(setup());
    renderCard(<IntelSetupCard />);
    const step = await screen.findByTestId('intel-setup-staff');
    await userEvent.click(within(step).getByRole('switch', { name: 'Ngọc bán hàng là nhân viên' }));
    await waitFor(() => expect(calls.find((c) => c.url === '/profile/p1')).toMatchObject({ method: 'PATCH', body: { person_type: 'staff', relation_to_owner: 'staff' } }));
    await userEvent.click(within(step).getByRole('button', { name: 'Đã khai xong — không có nhân viên' }));
    await waitFor(() => expect(calls.some((c) => c.url === '/intel/setup/staff-confirmed' && c.method === 'POST')).toBe(true));
  });

  it('tắt nhân viên ⇒ trả về đúng loại cũ (khách), không về "chưa phân loại"', async () => {
    const calls = mockApi(setup());
    renderCard(<IntelSetupCard />);
    const step = await screen.findByTestId('intel-setup-staff');
    await userEvent.click(within(step).getByRole('switch', { name: 'Chị Mai là nhân viên' }));
    await waitFor(() => expect(calls.find((c) => c.url === '/profile/p3')).toMatchObject({ method: 'PATCH', body: { person_type: 'customer' } }));
  });

  it('nhân viên đánh dấu từ trước: hiện "Cần xác nhận lại", bấm ⇒ PATCH person_type staff (máy chủ đòi mã PIN)', async () => {
    const calls = mockApi(setup());
    renderCard(<IntelSetupCard />);
    const step = await screen.findByTestId('intel-setup-staff');
    expect(within(step).getByText('Cần xác nhận lại')).toBeInTheDocument();
    await userEvent.click(within(step).getByRole('button', { name: 'Xác nhận lại Hùng cũ là nhân viên (cần mã PIN)' }));
    await waitFor(() => expect(calls.find((c) => c.url === '/profile/p4')).toMatchObject({ method: 'PATCH', body: { person_type: 'staff', relation_to_owner: 'staff' } }));
  });

  it('đã xong: một dòng tóm tắt, tiến độ 2/2, không còn danh sách nhóm', async () => {
    mockApi(setup({ ready: true, groups: { done: true, total: 3, unlabeled_count: 0, unlabeled: [] },
      staff: { done: true, confirmed_at: '2026-10-25T02:00:00Z', staff_count: 1, people: [{ id: 'p1', name: 'Ngọc bán hàng', person_type: 'staff', prev_type: 'unknown' }] } }));
    renderCard(<IntelSetupCard />);
    expect(await screen.findByTestId('intel-setup-ready')).toHaveTextContent('khai 1 nhân viên');
    expect(screen.getByRole('img', { name: 'Đã xong 2 trên 2 bước' })).toBeInTheDocument();
    expect(screen.queryByTestId('intel-setup-groups')).toBeNull();
  });

  it('lỗi máy chủ khi lưu: chữ thân thiện + Chi tiết kỹ thuật, không render object', async () => {
    mockApi(setup(), (c) => (c.url === '/intel/group-contexts/bulk' ? json(422, { status: 422, code: 'VALIDATION', title: 'Nhãn bối cảnh chưa hợp lệ', detail: 'nhãn lạ' }) : undefined));
    renderCard(<IntelSetupCard />);
    const step = await screen.findByTestId('intel-setup-groups');
    await userEvent.selectOptions(within(step).getByRole('combobox', { name: 'Bối cảnh của nhóm Khách sỉ Nhà Xinh' }), 'khac');
    await userEvent.click(within(step).getByRole('button', { name: 'Lưu bối cảnh cho 1 nhóm' }));
    const alert = await within(step).findByRole('alert');
    expect(alert).toHaveTextContent('nhãn lạ');
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(step).not.toHaveTextContent('[object Object]');
  });

  it('máy chủ trả sai khuôn ⇒ lỗi tại chỗ, thẻ không vỡ', async () => {
    mockApi(() => json(200, { ready: 'x' }));
    renderCard(<IntelSetupCard />);
    const card = await screen.findByRole('region', { name: 'Thiết lập Tình báo' });
    expect(await within(card).findByRole('button', { name: /Thử lại/ })).toBeInTheDocument();
  });
});

describe('<SetupBanner> dải nhắc Thiết lập Tình báo (Hôm nay)', () => {
  it('chưa xong ⇒ banner nêu phần còn thiếu; đã xong ⇒ không banner', async () => {
    mockApi(setup());
    const { unmount } = renderCard(<SetupBanner />);
    const banner = await screen.findByTestId('intel-setup-banner');
    expect(banner).toHaveTextContent('còn 2 nhóm chưa gán bối cảnh, chưa khai nhân viên');
    expect(banner).toHaveTextContent('Thiết lập Tình báo chưa xong');
    expect(banner).not.toHaveTextContent('Tình báo đầy đủ chưa bật');
    expect(within(banner).getByRole('link', { name: 'Làm ngay' })).toHaveAttribute('href', '/intel?tab=setup');
    unmount();
    queryClient.clear();
    mockApi(setup({ ready: true, groups: { done: true, total: 1, unlabeled_count: 0, unlabeled: [] }, staff: { done: true, confirmed_at: null, staff_count: 0, people: [] } }));
    renderCard(<SetupBanner />);
    await waitFor(() => expect(queryClient.getQueryState(['intel', 'setup'])?.status).toBe('success'));
    expect(screen.queryByTestId('intel-setup-banner')).toBeNull();
  });

  it('setupPendingText chỉ nêu phần thiếu', () => {
    expect(setupPendingText(setup({ staff: { ...setup().staff, done: true } }))).toBe('còn 2 nhóm chưa gán bối cảnh');
    expect(setupPendingText(setup({ groups: { ...setup().groups, done: true } }))).toBe('chưa khai nhân viên');
  });
});

describe('<IntelCard> bốn cổng thường trong v0.1.76', () => {
  it('chỉ lên đầu bản tin: hiện "Đầu bản tin (bản này)", không có lựa chọn Báo ngay', async () => {
    const calls = mockApi(setup());
    renderCard(<IntelCard part="trial" />);
    const row = await screen.findByTestId('intel-mode');
    expect(within(row).getByText('Đầu bản tin (bản này)')).toBeInTheDocument();
    expect(within(row).queryByRole('radio', { name: 'Nhắn ngay từ sớm' })).toBeNull();
    expect(row).toHaveTextContent('Lừa đảo và an toàn vẫn báo ngay');
    expect(calls.some((c) => c.url === '/intel/settings')).toBe(false);
  });
});
