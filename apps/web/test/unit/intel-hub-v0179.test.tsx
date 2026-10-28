import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { IntelCard } from '../../src/intel/IntelCard';
import { SignalGrid } from '../../src/intel/SignalGrid';
import { TodayTab } from '../../src/intel/TodayTab';
import {
  DEFAULT_FILTERS,
  gateLabel,
  groupByLevel,
  isAskList,
  isSignalList,
  levelMeta,
  signalKindLabel,
  signalQuery,
  statusLabel,
  txt,
  type SignalItem,
} from '../../src/intel/intelHubModel';
import type { IntelConsole } from '../../src/intel/intelModel';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';

/** v0.1.79 (G1) — trang Tình báo: hàm thuần, bảng Tín hiệu + ngăn chi tiết, Hôm nay, nút Đúng/Sai/Không cần báo, thẻ Chạy thử. */

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json' } });

const me = {
  id: 'u', email: 'o@x.vn', display_name: 'Owner', role: { code: 'owner', name: 'owner' },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
  pin_verified_until: null, permissions: { 'system.read': 'all', 'system.manage': 'all', 'queue.read': 'all' },
};

const sig = (over: Partial<SignalItem> = {}): SignalItem => ({
  id: 's1', code: 'SG-0001', kind: 'rui_ro_lua_dao', level: 'red', gate: null, status: 'open', delivery: 'push_now', privacy: 'chat_ok',
  title: 'Người mới gửi STK lạ', summary: 'Người mới vào nhóm gửi STK, yêu cầu chuyển khoản', suggested_action: 'Gọi xác minh, chưa chuyển tiền',
  confidence_level: 'high', reasons: ['asks_for_owner'], created_at: '2026-10-28T11:24:00Z', would_push_at: null, due_at: null, group_id: null,
  evidence_n: 1, feedback_verdict: null, ...over,
});
const SIGNALS: SignalItem[] = [
  sig(),
  sig({ id: 's2', code: 'SG-0002', kind: 'xung_dot_khieu_nai', level: 'amber', title: 'Khách doạ huỷ đơn', suggested_action: 'Gọi lại trong hôm nay' }),
  sig({ id: 's3', code: 'SG-0003', kind: 'su_kien_dip', level: 'grey', title: 'Sinh nhật chị Lan', suggested_action: null, feedback_verdict: 'correct' }),
];

interface Call { url: string; method: string; body: unknown }
function mockApi(opts: { signals?: SignalItem[]; asks?: unknown[]; fail?: string; console?: Partial<IntelConsole>; setupReady?: boolean; channels?: number } = {}) {
  const calls: Call[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const call: Call = { url: String(url).replace('/api/v1', ''), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    if (opts.fail && call.url.startsWith(opts.fail)) return json(500, { status: 500, code: 'INTERNAL', title: 'Lỗi', detail: 'boom-chi-tiet' });
    if (call.method === 'GET' && call.url.startsWith('/signals?')) {
      const q = new URLSearchParams(call.url.split('?')[1]);
      const items = (opts.signals ?? SIGNALS).filter((s) => (!q.get('level') || s.level === q.get('level')) && (!q.get('kind') || s.kind === q.get('kind')));
      return json(200, { items, hidden_pin_n: 0 });
    }
    if (call.method === 'GET' && call.url.startsWith('/signals/')) {
      const id = call.url.split('/')[2];
      const s = (opts.signals ?? SIGNALS).find((x) => x.id === id);
      return s ? json(200, { ...s, evidence: [{ type: 'raw_event', id: 'e1', quote_status: 'ok', quote_display: 'Em chuyển khoản ngay nhé ***456', received_at: '2026-10-28T11:23:00Z' }] }) : json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tìm thấy tín hiệu' });
    }
    if (call.url === '/asks') return json(200, { items: opts.asks ?? [], hidden_pin_n: 0 });
    if (call.url === '/owner/brief') {
      return json(200, { latest: null, live: { at: '2026-10-28T10:00:00Z', sections: [] }, alerts: [], history: [], next_at: '2026-10-29T00:30:00Z', ai_ready: true, channels_connected: opts.channels ?? 2 });
    }
    if (call.url === '/intel/setup') {
      return json(200, { ready: opts.setupReady ?? true, groups: { done: false, total: 3, unlabeled_count: 3, unlabeled: [] }, staff: { done: true, confirmed_at: null, staff_count: 0, people: [] }, contexts: [], gated_kinds: [] });
    }
    if (call.url === '/intel/console') {
      return json(200, {
        pin_active: false, mode: 'live', ramp_gates: 'digest_top', assistant: { locked: false, locked_at: null, locked_via: null }, outbound: { unlocked_today: false, locked_until: null },
        backup_channel: { paired: true, kind: 'telegram' }, mutes: [], payees: [], groups: [], contexts: ['khac'],
        ramp: { started_at: '2026-10-26T03:00:00Z', ends_at: '2026-11-09T03:00:00Z', days: 14, days_left: 12, in_trial: true, passed_gates: ['loi_hua_khach_sap_vo'],
          m1: { n: 4, k: 3, p: 0.75, lo95: 0.3, ok: false, min_n: 10, wrong_by_reason: {}, low_precision_gates: ['co_hoi_lon_sap_troi'] } },
        shadow_items: [], ...opts.console,
      });
    }
    if (call.method !== 'GET') return json(200, { ok: true, status: 'resolved', level: 'red' });
    return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tìm thấy' });
  }));
  return calls;
}

function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname + l.search}</div>;
}
const renderAt = (ui: React.ReactElement, path = '/intel') => {
  queryClient.setQueryData(qk.me, me);
  return render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={[path]}>{ui}<Where /></MemoryRouter></QueryClientProvider>);
};

beforeEach(() => {
  queryClient.clear();
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('intelHubModel', () => {
  it('groupByLevel: 🔴 → 🟠 → ⚪, mức off/lạ bị bỏ, giữ thứ tự trong mức', () => {
    const g = groupByLevel([sig({ id: 'a', level: 'grey' }), sig({ id: 'b', level: 'off' }), sig({ id: 'c', level: 'red' }), sig({ id: 'd', level: 'red' })]);
    expect(g.map((x) => [x.level, x.items.map((i) => i.id)])).toEqual([['red', ['c', 'd']], ['amber', []], ['grey', ['a']]]);
    expect(g.map((x) => x.dot)).toEqual(['🔴', '🟠', '⚪']);
  });
  it('signalQuery: bỏ bộ lọc rỗng, mặc định status=open', () => {
    expect(signalQuery(DEFAULT_FILTERS)).toBe('status=open&limit=200');
    expect(signalQuery({ level: 'red', status: 'resolved', kind: 'viec_han' })).toBe('level=red&kind=viec_han&status=resolved&limit=200');
  });
  it('nhãn: loại lạ ⇒ "Tín hiệu", mã máy không lộ; txt không bao giờ ra object', () => {
    expect(signalKindLabel('rui_ro_lua_dao')).toBe('Rủi ro lừa đảo');
    expect(signalKindLabel('xyz')).toBe('Tín hiệu');
    expect(statusLabel('lạ')).toBe('Không rõ');
    expect(gateLabel('khong_co')).toBe('Loại khác');
    expect(levelMeta('off').level).toBe('grey');
    expect(txt({ a: 1 })).toBe('');
    expect(txt(5)).toBe('5');
  });
  it('validator: phản hồi sai khuôn bị từ chối', () => {
    expect(isSignalList({ items: [sig()] })).toBe(true);
    expect(isSignalList({ items: [{ id: 1 }] })).toBe(false);
    expect(isSignalList({})).toBe(false);
    expect(isAskList({ items: [{ id: 'a' }] })).toBe(true);
    expect(isAskList({ items: 3 })).toBe(false);
  });
});

describe('<SignalGrid> tab Tín hiệu', () => {
  it('liệt kê mọi tín hiệu với mức, loại, việc Sếp cần làm; lọc theo mức gọi lại máy chủ', async () => {
    const calls = mockApi();
    renderAt(<SignalGrid />, '/intel?tab=signals');
    const rows = await screen.findAllByTestId('intel-signal-row');
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent('Rủi ro lừa đảo');
    expect(rows[0]).toHaveTextContent('Gọi xác minh, chưa chuyển tiền');
    expect(rows[2]).toHaveTextContent('Đã chấm Đúng');
    await userEvent.selectOptions(screen.getByLabelText('Mức'), 'red');
    await waitFor(() => expect(screen.getAllByTestId('intel-signal-row')).toHaveLength(1));
    expect(calls.some((c) => c.url.startsWith('/signals?') && c.url.includes('level=red'))).toBe(true);
    expect(document.body).not.toHaveTextContent('[object Object]');
  });

  it('bấm tiêu đề mở ngăn chi tiết: vì sao, việc Sếp cần làm, bằng chứng đã che; ghi ?tin= trên địa chỉ; Quay lại đóng', async () => {
    mockApi();
    renderAt(<SignalGrid />, '/intel?tab=signals');
    await userEvent.click(await screen.findByRole('button', { name: 'Người mới gửi STK lạ' }));
    const drawer = await screen.findByRole('complementary', { name: 'Chi tiết tín hiệu' });
    expect(await within(drawer).findByText('Em chuyển khoản ngay nhé ***456')).toBeInTheDocument();
    expect(drawer).toHaveTextContent('khách đòi gặp Sếp');
    expect(within(drawer).getByTestId('intel-drawer-action')).toHaveTextContent('Gọi xác minh, chưa chuyển tiền');
    expect(screen.getByTestId('where')).toHaveTextContent('tin=s1');
    await userEvent.click(within(drawer).getByRole('button', { name: 'Đóng chi tiết tín hiệu' }));
    expect(screen.queryByRole('complementary', { name: 'Chi tiết tín hiệu' })).toBeNull();
    expect(screen.getByTestId('where')).not.toHaveTextContent('tin=');
  });

  it('ngăn chi tiết: Đúng / Sai (3 lý do) / Không cần báo gọi đúng POST feedback; chuyển trạng thái gọi POST transition', async () => {
    const calls = mockApi();
    renderAt(<SignalGrid />, '/intel?tab=signals&tin=s1');
    const drawer = await screen.findByRole('complementary', { name: 'Chi tiết tín hiệu' });
    await within(drawer).findByTestId('intel-drawer-action');
    await userEvent.click(within(drawer).getByRole('button', { name: 'Sai ▾' }));
    const grp = within(drawer).getByRole('group', { name: 'Vì sao báo sai?' });
    expect(within(grp).getAllByRole('button').map((b) => b.textContent)).toEqual(['Khách vẫn mua', 'Không gấp', 'NV lo rồi', 'Huỷ']);
    await userEvent.click(within(grp).getByRole('button', { name: 'Sai: NV lo rồi' }));
    await waitFor(() => expect(calls.find((c) => c.url === '/signals/s1/feedback')?.body).toEqual({ verdict: 'wrong', reason: 'staff_handled' }));
    await userEvent.click(within(drawer).getByRole('button', { name: 'Không cần báo' }));
    await waitFor(() => expect(calls.filter((c) => c.url === '/signals/s1/feedback').at(-1)?.body).toEqual({ verdict: 'unimportant' }));
    await userEvent.click(within(drawer).getByRole('button', { name: 'Đúng' }));
    await waitFor(() => expect(calls.filter((c) => c.url === '/signals/s1/feedback').at(-1)?.body).toEqual({ verdict: 'correct' }));
    await userEvent.click(within(drawer).getByRole('button', { name: 'Đã xong' }));
    await waitFor(() => expect(calls.find((c) => c.url === '/signals/s1/transition')?.body).toEqual({ to: 'resolved' }));
  });

  it('tín hiệu không đọc được: câu thân thiện + Chi tiết kỹ thuật, không render object; danh sách lỗi có Thử lại', async () => {
    mockApi({ fail: '/signals?' });
    renderAt(<SignalGrid />, '/intel?tab=signals');
    expect(await screen.findByRole('button', { name: /Thử lại/ })).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('[object Object]');
  });

  it('rỗng: một câu một chỗ, không bảng', async () => {
    mockApi({ signals: [] });
    renderAt(<SignalGrid />, '/intel?tab=signals');
    expect(await screen.findByText('Không có tín hiệu nào khớp bộ lọc.')).toBeInTheDocument();
    expect(screen.queryByRole('table')).toBeNull();
  });
});

describe('<TodayTab> Hôm nay', () => {
  it('tín hiệu mở xếp theo mức; mỗi dòng có "Việc Sếp cần làm"; 3 dòng đầu Cần quyết + link /decide', async () => {
    mockApi({
      asks: ['a', 'b', 'c', 'd'].map((x, i) => ({ id: `q${i}`, code: `AS-000${i}`, source: 'gen', decision_kind: 'internal', summary: `Việc cần quyết ${x}`, signal_id: null, expires_at: null })),
    });
    renderAt(<TodayTab />);
    const red = await screen.findByTestId('intel-level-red');
    expect(within(red).getByText('Người mới gửi STK lạ')).toBeInTheDocument();
    expect(within(red).getByText(/Gọi xác minh, chưa chuyển tiền/)).toBeInTheDocument();
    expect(within(screen.getByTestId('intel-level-amber')).getByText('Khách doạ huỷ đơn')).toBeInTheDocument();
    expect(within(screen.getByTestId('intel-level-grey')).getByText('Sinh nhật chị Lan')).toBeInTheDocument();
    const asks = await screen.findAllByTestId('intel-ask-row');
    expect(asks).toHaveLength(3);
    expect(screen.getByRole('region', { name: 'Cần Sếp quyết' })).toHaveTextContent('Cần Sếp quyết (4)');
    expect(screen.getByTestId('intel-decide-all')).toHaveAttribute('href', '/decide');
  });

  it('chấm Đúng ngay tại dòng gọi POST feedback; dòng đã chấm chỉ còn nhãn', async () => {
    const calls = mockApi();
    renderAt(<TodayTab />);
    await screen.findByTestId('intel-level-red');
    await userEvent.click(screen.getByRole('button', { name: 'Báo đúng: Khách doạ huỷ đơn' }));
    await waitFor(() => expect(calls.find((c) => c.url === '/signals/s2/feedback')?.body).toEqual({ verdict: 'correct' }));
    const grey = screen.getByTestId('intel-level-grey');
    expect(within(grey).getByText('Đã chấm Đúng')).toBeInTheDocument();
    expect(within(grey).queryByRole('button', { name: /Báo đúng/ })).toBeNull();
  });

  it('Thiết lập chưa xong ⇒ dải nhắc dẫn sang tab Thiết lập; xong ⇒ không dải', async () => {
    mockApi({ setupReady: false });
    const { unmount } = renderAt(<TodayTab />);
    const banner = await screen.findByTestId('intel-setup-banner');
    expect(within(banner).getByRole('link', { name: 'Làm ngay' })).toHaveAttribute('href', '/intel?tab=setup');
    unmount();
    queryClient.clear();
    mockApi({ setupReady: true });
    renderAt(<TodayTab />);
    await screen.findByTestId('intel-level-red');
    expect(screen.queryByTestId('intel-setup-banner')).toBeNull();
  });

  it('ngày đầu (chưa nối kênh, chưa có tín hiệu) ⇒ đúng MỘT thẻ "Nối Zalo", không câu rỗng chồng', async () => {
    mockApi({ signals: [], channels: 0 });
    renderAt(<TodayTab />);
    expect(await screen.findByTestId('first-run-card')).toBeInTheDocument();
    expect(screen.getAllByTestId('first-run-card')).toHaveLength(1);
    expect(screen.queryByText('Không có tín hiệu nào đang mở.')).toBeNull();
  });

  it('không có tín hiệu ⇒ một câu rỗng; Cần quyết lỗi ⇒ khối lỗi riêng, tín hiệu vẫn hiện', async () => {
    mockApi({ signals: [] });
    const { unmount } = renderAt(<TodayTab />);
    expect(await screen.findByText('Không có tín hiệu nào đang mở.')).toBeInTheDocument();
    unmount();
    queryClient.clear();
    mockApi({ fail: '/asks' });
    renderAt(<TodayTab />);
    expect(await screen.findByTestId('intel-level-red')).toBeInTheDocument();
    expect(await within(screen.getByRole('region', { name: 'Cần Sếp quyết' })).findByRole('button', { name: /Thử lại/ })).toBeInTheDocument();
  });
});

describe('<IntelCard part="trial"> Chạy thử 14 ngày', () => {
  it('nêu số lượt chấm M1, còn mấy ngày và từng cổng: đủ để nhắn ngay / chấm Sai nhiều / chưa đủ lượt', async () => {
    mockApi();
    renderAt(<IntelCard part="trial" />);
    const gates = await screen.findByTestId('intel-gates');
    expect(gates).toHaveTextContent('Đã chấm 4/10 lượt');
    expect(gates).toHaveTextContent('Còn 12 ngày chạy thử');
    expect(within(screen.getByTestId('intel-gate-loi_hua_khach_sap_vo')).getByText('Đủ để nhắn ngay')).toBeInTheDocument();
    expect(within(screen.getByTestId('intel-gate-co_hoi_lon_sap_troi')).getByText('Chấm Sai nhiều')).toBeInTheDocument();
    expect(within(screen.getByTestId('intel-gate-khach_che_cong_khai_doa_bo')).getByText('Chưa đủ lượt chấm')).toBeInTheDocument();
    expect(screen.getByTestId('intel-mode')).toBeInTheDocument();
    expect(screen.queryByTestId('intel-assistant')).toBeNull();
  });

  it('phần Thiết lập (controls) không có chạy thử; máy chủ cũ không gửi ramp ⇒ không có dòng cổng', async () => {
    mockApi({ console: { ramp: undefined } });
    renderAt(<IntelCard part="controls" />);
    expect(await screen.findByTestId('intel-assistant')).toBeInTheDocument();
    expect(screen.queryByTestId('intel-mode')).toBeNull();
    expect(screen.queryByTestId('intel-gates')).toBeNull();
  });
});
