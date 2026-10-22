/**
 * v0.1.68 — Nhật ký lắng nghe (màn): phần logic thuần, vẽ dòng tin + sự kiện + "hệ thống đã làm gì", ghi chú nhóm Không
 * nghe, "Xem thêm" theo con trỏ, lọc gửi đúng tham số cho máy chủ, 403 thân thiện, cập nhật trực tiếp qua WebSocket.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { SCREEN_BY_KEY, type ListenLogEvent, type ListenLogItem, type ListenLogMessage, type ListenLogStats } from '@gen-harness/contracts';
import { ListenLogScreen } from '../../src/screens/data/ListenLogScreen';
import {
  DEFAULT_FILTERS,
  NOT_LISTENING_NOTE,
  activeFilterCount,
  dayLabel,
  eventsHiddenByFilters,
  groupByDay,
  screeningText,
  toQuery,
} from '../../src/screens/data/listenLogModel';
import { applyEvent } from '../../src/lib/realtime';
import { qkLog, resetListenLogRefresh, setListenLogLive } from '../../src/lib/listenLogQueries';
import { queryClient } from '../../src/lib/queryClient';
import { useUrlStateStore } from '../../src/lib/uiStore';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const TZ = 'Asia/Ho_Chi_Minh';

function msg(n: number, over: Partial<ListenLogMessage> = {}): ListenLogMessage {
  return {
    kind: 'message', id: `m-${n}`, at: new Date(Date.UTC(2026, 9, 17, 8, 0) - n * 60_000).toISOString(), code: `RAW-${n}`,
    channel: { type: 'zalo', name: 'Zalo' }, group: { id: 'g1', code: 'GRP-ZL-0114', name: 'Vận hành Genesis' }, direct: false,
    sender: { id: 'p1', code: 'PER-0042', name: 'Nguyễn Văn Bảo' }, direction: 'inbound', msg_kind: 'text', text: `Báo giá container số ${n}`,
    tagged: false, listen_mode: 'silent', listen_mode_label: 'Lắng nghe im lặng', listen_mode_known: true,
    screening: { state: 'clean', label: 'AskedPrice', confidence: 0.87, note: 'Đã vào kho sạch' }, decisions: [], note: null, ...over,
  };
}

function ev(n: number, over: Partial<ListenLogEvent> = {}): ListenLogEvent {
  return {
    kind: 'event', id: `e-${n}`, at: new Date(Date.UTC(2026, 9, 17, 8, 0) - n * 60_000).toISOString(), event_type: 'group_new', icon: 'ph ph-users-three',
    tone: 'accent', text: 'Tài khoản Zalo của Sếp vừa được thêm vào nhóm «Đại lý Tây Nguyên». Nhóm tự vào chế độ: Lắng nghe im lặng.',
    channel: { type: 'zalo', name: 'Zalo' }, group: null, person: null, direct: false, listen_mode: 'silent', listen_mode_label: 'Lắng nghe im lặng',
    raw_event_id: null, ...over,
  };
}

const STATS: ListenLogStats = {
  by_channel: [
    { type: 'zalo', name: 'Zalo', messages_24h: 128, events_24h: 3, groups_listening: 4 },
    { type: 'whatsapp', name: 'WhatsApp', messages_24h: 12, events_24h: 1, groups_listening: 2 },
  ],
  messages_24h: 140,
  events_24h: 4,
  groups_listening: 6,
  groups: [{ id: 'g1', code: 'GRP-ZL-0114', name: 'Vận hành Genesis', channel: 'zalo', listen_mode: 'silent' }],
};

function server(pages: ListenLogItem[][], opts: { forbidden?: boolean; decisionsVisible?: boolean } = {}) {
  const urls: URL[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL) => {
      const u = new URL(String(url), 'http://x');
      if (u.pathname.endsWith('/listen-log/stats')) return opts.forbidden ? json(403, { code: 'FORBIDDEN', title: 'Vai trò của bạn không có quyền thao tác này', detail: 'data.read' }) : json(200, STATS);
      if (u.pathname.endsWith('/listen-log')) {
        urls.push(u);
        if (opts.forbidden) return json(403, { code: 'FORBIDDEN', title: 'Vai trò của bạn không có quyền thao tác này', detail: 'data.read' });
        const cursor = u.searchParams.get('cursor');
        const idx = cursor ? Number(cursor.slice(1)) : 0;
        return json(200, { items: pages[idx] ?? [], next_cursor: idx + 1 < pages.length ? `c${idx + 1}` : null, decisions_visible: opts.decisionsVisible ?? true });
      }
      return json(404);
    }),
  );
  return urls;
}

function renderScreen(qc: QueryClient = queryClient) {
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ListenLogScreen />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useUrlStateStore.setState({ params: {} });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  queryClient.clear();
  resetListenLogRefresh();
});

describe('logic thuần', () => {
  it('màn có trong danh mục, thuộc Tầng dữ liệu, không nằm trong screens.json (màn bổ sung)', () => {
    const s = SCREEN_BY_KEY.listenlog;
    expect(s).toMatchObject({ domain: 'tech', parent: 'Tầng dữ liệu', name: 'Nhật ký lắng nghe', extra: true });
    expect(s.description).toMatch(/hệ thống đã làm gì|sàng lọc/);
  });

  it('toQuery bỏ giá trị mặc định/rỗng; activeFilterCount đếm đúng', () => {
    expect(toQuery(DEFAULT_FILTERS)).toEqual({ channel: undefined, group_id: undefined, kind: undefined, since: '7d', tagged: undefined, acted: undefined, q: undefined });
    const f = { ...DEFAULT_FILTERS, channel: 'zalo', kind: 'message' as const, tagged: true, q: ' giá ' };
    expect(toQuery(f)).toMatchObject({ channel: 'zalo', kind: 'message', tagged: true, q: 'giá' });
    expect(activeFilterCount(DEFAULT_FILTERS)).toBe(0);
    expect(activeFilterCount(f)).toBe(4);
    expect(eventsHiddenByFilters(f)).toBe(false);
    expect(eventsHiddenByFilters({ ...DEFAULT_FILTERS, acted: true })).toBe(true);
  });

  it('groupByDay nhóm theo ngày địa phương; nhãn Hôm nay / Hôm qua', () => {
    const now = new Date(Date.UTC(2026, 9, 17, 9, 0));
    const items = [msg(1), msg(2), ev(3, { at: new Date(Date.UTC(2026, 9, 16, 8, 0)).toISOString() })];
    const days = groupByDay(items, TZ, now);
    expect(days.map((d) => d.label)).toEqual(['Hôm nay · 17/10', 'Hôm qua · 16/10']);
    expect(days[0].items).toHaveLength(2);
    expect(dayLabel(new Date(Date.UTC(2026, 9, 10, 8, 0)).toISOString(), TZ, now)).toMatch(/^Thứ .+ · 10\/10$/);
  });

  it('screeningText ghép trạng thái · nhãn · độ tin cậy (dấu phẩy)', () => {
    expect(screeningText('clean', 'AskedPrice', 0.87)).toBe('Đã vào kho sạch · AskedPrice · tin cậy 0,87');
    expect(screeningText('pending', null, null)).toBe('Chờ chu kỳ tới');
    expect(screeningText('discarded', 'Noise', null)).toBe('Loại — nhiễu');
  });
});

describe('vẽ màn', () => {
  it('ghi chú nhóm Không nghe ở đầu màn + số liệu 24 giờ theo kênh', async () => {
    server([[msg(1)]]);
    renderScreen();
    expect(screen.getByTestId('ll-not-listening-note')).toHaveTextContent(NOT_LISTENING_NOTE);
    expect(await screen.findByText('Báo giá container số 1')).toBeInTheDocument();
    const stats = await screen.findByRole('group', { name: /Số liệu 24 giờ/ });
    expect(within(stats).getByText('128')).toBeInTheDocument();
    expect(within(stats).getAllByText(/tin trong 24 giờ/)).toHaveLength(2);
    expect(within(stats).getByText('6')).toBeInTheDocument(); // số nhóm đang nghe
  });

  it('dòng tin: giờ, kênh, nhóm, người gửi, chế độ nghe, chuỗi hệ thống đã làm gì + link nháp ở Bàn làm việc', async () => {
    const m = msg(1, {
      tagged: true,
      listen_mode: 'tagged_only',
      listen_mode_label: 'Nghe hết, chỉ trả lời khi được tag',
      decisions: [
        {
          id: 'd1', at: '2026-10-17T08:00:30Z', agent: { id: 'a1', name: 'Trợ lý Mai' }, decision: 'draft', outcome: 'held',
          label: 'Nháp trả lời: chờ duyệt', draft: { id: 'draft-9', code: 'ACT-0231', status: 'pending' },
        },
      ],
    });
    server([[m]]);
    renderScreen();
    const row = await screen.findByTestId('ll-message');
    expect(within(row).getByText('Vận hành Genesis')).toBeInTheDocument();
    expect(within(row).getByText('Nguyễn Văn Bảo')).toBeInTheDocument();
    expect(within(row).getByText('Được tag')).toBeInTheDocument();
    expect(within(row).getByText('Nghe hết, chỉ trả lời khi được tag')).toBeInTheDocument();
    expect(within(row).getByText('14:59')).toBeInTheDocument(); // 08:00Z − 1 phút = 07:59Z = 14:59 giờ tổ chức (UTC+7)
    const trail = within(row).getByRole('list', { name: 'Hệ thống đã làm gì' });
    expect(within(trail).getByText('Sàng lọc')).toBeInTheDocument();
    expect(within(trail).getByText(/Đã vào kho sạch · AskedPrice · tin cậy 0,87/)).toBeInTheDocument();
    expect(within(trail).getByText('Trợ lý Mai')).toBeInTheDocument();
    expect(within(trail).getByText('Nháp trả lời: chờ duyệt')).toBeInTheDocument();
    const link = within(trail).getByRole('link', { name: /ACT-0231/ });
    expect(link).toHaveAttribute('href', '/workbench?id=draft-9');
  });

  it('tin 1-1 ghi "Tin riêng"; tin không chữ ghi loại; ghi chú vì sao hệ thống không làm gì; không có object trần', async () => {
    server([
      [
        msg(1, { group: null, direct: true, text: 'Alo anh', note: 'Tin riêng ở chế độ lưu im lặng — không đưa cho agent trực', listen_mode_label: 'Tin riêng — lưu im lặng' }),
        msg(2, { msg_kind: 'sticker', text: null }),
        msg(3, { screening: { state: 'discarded', label: 'Noise', confidence: null, note: 'Đã loại khi sàng lọc: Trùng hẳn một tin đã có' } }),
      ],
    ]);
    const { container } = renderScreen();
    expect(await screen.findByText('Tin riêng')).toBeInTheDocument();
    expect(screen.getByText('Tin riêng ở chế độ lưu im lặng — không đưa cho agent trực')).toBeInTheDocument();
    expect(screen.getByText('[Nhãn dán]')).toBeInTheDocument();
    expect(screen.getByText('Đã loại khi sàng lọc: Trùng hẳn một tin đã có')).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/\[object Object\]/);
  });

  it('không có quyền xem quyết định (decisions_visible=false) thì không vẽ bước agent', async () => {
    server([[msg(1, { decisions: [{ id: 'd1', at: '2026-10-17T08:00:30Z', agent: { id: 'a', name: 'Trợ lý Mai' }, decision: 'note', outcome: 'noted', label: 'Ghi chú vào sổ tay', draft: null }] })]], { decisionsVisible: false });
    renderScreen();
    await screen.findByTestId('ll-message');
    expect(screen.queryByText('Trợ lý Mai')).not.toBeInTheDocument();
  });

  it('sự kiện cùng dòng thời gian: câu tiếng Việt + icon + loại', async () => {
    server([[msg(1), ev(2), ev(3, { event_type: 'channel_ended', icon: 'ph ph-plugs', tone: 'bad', text: 'Kênh Zalo đã đăng xuất.' })]]);
    renderScreen();
    const events = await screen.findAllByTestId('ll-event');
    expect(events).toHaveLength(2);
    expect(events[0]).toHaveTextContent('Tài khoản Zalo của Sếp vừa được thêm vào nhóm «Đại lý Tây Nguyên». Nhóm tự vào chế độ: Lắng nghe im lặng.');
    expect(events[1]).toHaveAttribute('data-event-type', 'channel_ended');
    expect(events[1]).toHaveTextContent('Kênh Zalo đã đăng xuất.');
  });

  it('"Xem thêm" nối trang kế bằng con trỏ, hết trang thì báo đã hết', async () => {
    const urls = server([[msg(1), msg(2)], [msg(3), ev(4)], [msg(5)]]);
    const user = userEvent.setup();
    renderScreen();
    expect(await screen.findByText('Báo giá container số 1')).toBeInTheDocument();
    expect(screen.queryByText('Báo giá container số 3')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Xem thêm/ }));
    expect(await screen.findByText('Báo giá container số 3')).toBeInTheDocument();
    expect(urls[1].searchParams.get('cursor')).toBe('c1');
    await user.click(screen.getByRole('button', { name: /Xem thêm/ }));
    expect(await screen.findByText('Báo giá container số 5')).toBeInTheDocument();
    expect(urls[2].searchParams.get('cursor')).toBe('c2');
    expect(screen.queryByRole('button', { name: /Xem thêm/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Đã hết dòng/)).toBeInTheDocument();
  });

  it('lọc gửi đúng tham số phía máy chủ (chỉ tin được tag, chỉ sự kiện, tìm chữ, xoá bộ lọc)', async () => {
    const urls = server([[msg(1)]]);
    const user = userEvent.setup();
    renderScreen();
    await screen.findByText('Báo giá container số 1');
    await user.click(screen.getByRole('button', { name: 'Chỉ tin được tag' }));
    await waitFor(() => expect(urls.at(-1)!.searchParams.get('tagged')).toBe('true'));
    expect(await screen.findByText(/các sự kiện của kênh được ẩn/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Chỉ tin agent đã xử lý' }));
    await waitFor(() => expect(urls.at(-1)!.searchParams.get('acted')).toBe('true'));
    await user.click(screen.getByRole('radio', { name: 'Chỉ sự kiện' }));
    await waitFor(() => expect(urls.at(-1)!.searchParams.get('kind')).toBe('event'));
    await user.type(screen.getByRole('searchbox', { name: 'Tìm chữ' }), 'báo giá');
    await waitFor(() => expect(urls.at(-1)!.searchParams.get('q')).toBe('báo giá'));
    await user.click(screen.getByRole('button', { name: /Xoá \d+ bộ lọc/ }));
    // Về bộ lọc mặc định: mọi công tắc tắt, ô tìm trống, nút xoá biến mất (truy vấn mặc định đã có trong bộ nhớ đệm nên không gọi lại).
    await waitFor(() => expect(screen.queryByRole('button', { name: /Xoá \d+ bộ lọc/ })).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Chỉ tin được tag' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: 'Chỉ tin agent đã xử lý' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('radio', { name: 'Tin và sự kiện' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('searchbox', { name: 'Tìm chữ' })).toHaveValue('');
  });

  it('không có dòng nào: trạng thái trống có hướng dẫn', async () => {
    server([[]]);
    renderScreen();
    expect(await screen.findByText('Chưa có gì để hiện')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Kết nối' })).toHaveAttribute('href', '/connections');
  });

  it('403: thẻ "Việc này cần quyền …" thân thiện + Chi tiết kỹ thuật, không lộ object', async () => {
    server([[]], { forbidden: true });
    const { container } = renderScreen();
    const card = await screen.findByRole('alert');
    expect(card).toHaveTextContent('Việc này cần quyền Xem tầng dữ liệu');
    expect(card).toHaveTextContent('Chi tiết kỹ thuật');
    expect(container.textContent).not.toMatch(/\[object Object\]/);
    expect(screen.queryByTestId('ll-not-listening-note')).not.toBeInTheDocument();
  });
});

describe('cập nhật trực tiếp qua WebSocket', () => {
  const ping = (type: string, data: unknown = {}) => applyEvent(queryClient, { type, data } as never);

  async function mounted() {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const urls = server([[msg(1)]]);
    renderScreen();
    await screen.findByText('Báo giá container số 1');
    return urls;
  }

  it('raw.new / listen.event / agent.decision gom thành MỘT lần tải lại danh sách', async () => {
    const urls = await mounted();
    const before = urls.length;
    act(() => {
      ping('raw.new', { id: 'x' });
      ping('raw.state', { id: 'x', state: 'clean' });
      ping('listen.event', { id: 'e', event_type: 'group_new', owner_only: false });
      ping('agent.decision', { id: 'd' });
    });
    expect(urls.length).toBe(before); // chưa tải ngay — chờ gom nhịp
    await act(async () => {
      await vi.advanceTimersByTimeAsync(900);
    });
    await waitFor(() => expect(urls.length).toBe(before + 1));
  });

  it('tắt "Cập nhật trực tiếp" thì tín hiệu bị bỏ qua', async () => {
    const urls = await mounted();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    await user.click(screen.getByRole('switch', { name: 'Cập nhật trực tiếp' }));
    expect(screen.getByText('Tạm dừng cập nhật')).toBeInTheDocument();
    const before = urls.length;
    act(() => ping('raw.new', { id: 'y' }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(urls.length).toBe(before);
    setListenLogLive(true);
  });

  it('bật lại "Cập nhật trực tiếp" sau lúc tạm dừng ⇒ tải bù ngay một lần (tin đến lúc dừng hiện ra)', async () => {
    const urls = await mounted();
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const sw = screen.getByRole('switch', { name: 'Cập nhật trực tiếp' });
    await user.click(sw);
    const paused = urls.length;
    act(() => ping('raw.new', { id: 'z' }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(urls.length).toBe(paused); // đang dừng: không tải
    await user.click(sw);
    await waitFor(() => expect(urls.length).toBe(paused + 1)); // bật lại: tải bù ngay, không đợi tín hiệu
  });

  it('setListenLogLive: chỉ tải bù khi chuyển tắt → bật và có qc; bật sẵn / thiếu qc thì không', () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, 'invalidateQueries');
    setListenLogLive(true, qc); // đang bật sẵn
    expect(spy).not.toHaveBeenCalled();
    setListenLogLive(false, qc);
    setListenLogLive(true); // thiếu qc (rời màn)
    expect(spy).not.toHaveBeenCalled();
    setListenLogLive(false);
    setListenLogLive(true, qc);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith({ queryKey: qkLog.listRoot });
  });

  it('tín hiệu WebSocket chỉ làm mới danh sách, không làm mới số liệu 24 giờ (review Opus P3)', async () => {
    const qc = new QueryClient();
    qc.setQueryData([...qkLog.listRoot, {}], { pages: [], pageParams: [] });
    qc.setQueryData(qkLog.stats, STATS);
    const spy = vi.spyOn(qc, 'invalidateQueries');
    vi.useFakeTimers();
    applyEvent(qc, { type: 'listen.event', data: {} } as never);
    await vi.advanceTimersByTimeAsync(5000);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith({ queryKey: qkLog.listRoot });
  });

  it('không tải lại khi màn không mở (chưa có truy vấn nhật ký trong bộ nhớ đệm)', async () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, 'invalidateQueries');
    vi.useFakeTimers();
    applyEvent(qc, { type: 'raw.new', data: {} } as never);
    await vi.advanceTimersByTimeAsync(5000);
    expect(spy).not.toHaveBeenCalled();
    expect(qkLog.root).toEqual(['listenlog']);
  });
});
