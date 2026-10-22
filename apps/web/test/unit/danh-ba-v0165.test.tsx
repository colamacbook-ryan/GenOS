import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { Channel, ChannelGroup } from '@gen-harness/contracts';
import { ChannelCard, GroupsDialog } from '../../src/screens/system/ChannelCard';
import { LISTEN_MODES, directModeOf, isListeningMode } from '../../src/screens/system/systemModel';
import { LISTEN_MODE_LABEL, listenModeTone } from '../../src/screens/relations/relationsModel';
import { anyListening, step6Payload } from '../../src/setup/phase2Model';
import { Step6Groups } from '../../src/setup/Step6Groups';
import type { StepProps } from '../../src/setup/types';
import { PinDialogHost } from '../../src/shell/PinDialogHost';
import { ToastHost } from '../../src/shell/ToastHost';
import { queryClient } from '../../src/lib/queryClient';
import { qk2, useChannels } from '../../src/lib/dataQueries';
import { applyEvent } from '../../src/lib/realtime';
import { GUIDE } from '../../src/guide/guideContent';
import { STEP_DESCRIPTIONS } from '../../src/setup/steps';

// v0.1.65 (F1c): nhóm mới CHỜ chọn (Lắng nghe im lặng / Không nghe — chỉ khi Sếp đặt «Hỏi Sếp», QD 17/10), Cập nhật danh sách nhóm, nhãn "Nghe hết, chỉ trả lời khi được tag",
// bỏ "chờ QR", tin 1-1 ba mức. Máy chủ giả bằng fetch; không gọi Zalo/WhatsApp thật.

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

const grp = (id: string, name: string, mode: ChannelGroup['listen_mode'], members = 5, over: Partial<ChannelGroup> = {}): ChannelGroup => ({
  id, code: `GRP-ZL-${id}`, name, members, kind: 'internal', listen_mode: mode, view_scope: 'owner', ...over,
});

const zalo = (over: Partial<Channel> = {}): Channel => ({
  type: 'zalo', name: 'Zalo', installed: true, id: 'c1', state: 'active', account_label: 'iPhone của Sếp',
  started_at: new Date(Date.now() - 3600_000).toISOString(), groups_listening: 1, outbound_queued: 0,
  last_heartbeat_at: new Date().toISOString(), stats: null, qr: null, listen_direct: true, direct_mode: 'silent', groups_pending: 2, ...over,
});

/** Trả lời mặc định cho các GET khác: danh sách kênh giữ nguyên (để thẻ không biến mất khi làm mới). */
const fallback = (c: Call, over: Partial<Channel> = {}) => json(200, c.url.endsWith('/channels') ? [zalo(over)] : []);

const PENDING = [grp('1', 'Khách sỉ Hà Nội', 'pending', 40), grp('2', 'Đối tác kho', 'pending', 9)];

function Card({ canManage = true }: { canManage?: boolean }) {
  const channels = useChannels();
  const c = channels.data?.[0];
  return c ? <ChannelCard channel={c} canManage={canManage} /> : null;
}

function renderCard(c: Channel, canManage = true) {
  queryClient.setQueryData(qk2.channels, [c]);
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Card canManage={canManage} />
        <PinDialogHost />
        <ToastHost />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  queryClient.clear();
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } });
  vi.unstubAllGlobals();
});

describe('nhãn và hằng dùng chung (F065, F066)', () => {
  it('Q6=1: "Chỉ khi được tag" đổi thành "Nghe hết, chỉ trả lời khi được tag" ở mọi nơi', () => {
    expect(LISTEN_MODES.find((m) => m.value === 'tagged_only')?.label).toBe('Nghe hết, chỉ trả lời khi được tag');
    expect(LISTEN_MODE_LABEL.tagged_only).toBe('Nghe hết, chỉ trả lời khi được tag');
  });

  it('bỏ "chờ QR": Tạm dừng chỉ là Tạm dừng; nhóm mới có nhãn riêng "Chờ Sếp chọn"', () => {
    expect(LISTEN_MODE_LABEL.paused).toBe('Tạm dừng');
    expect(Object.values(LISTEN_MODE_LABEL).join('|')).not.toMatch(/chờ QR/);
    expect(LISTEN_MODE_LABEL.pending).toBe('Chờ Sếp chọn');
    expect(listenModeTone('pending')).toBe(listenModeTone('paused'));
    // Sếp không tự đặt lại "pending": không có trong danh sách chọn.
    expect(LISTEN_MODES.map((m) => m.value)).toEqual(['off', 'tagged_only', 'silent', 'proactive', 'paused']);
  });

  it('"đang nghe" chỉ gồm tagged_only/silent/proactive — off, paused, pending không tính (một định nghĩa)', () => {
    expect(['pending', 'off', 'paused', 'tagged_only', 'silent', 'proactive'].map((m) => isListeningMode(m as ChannelGroup['listen_mode']))).toEqual([
      false, false, false, true, true, true,
    ]);
    expect(anyListening([{ listen_mode: 'pending' }, { listen_mode: 'paused' }, { listen_mode: 'off' }])).toBe(false);
    expect(anyListening([{ listen_mode: 'paused' }, { listen_mode: 'silent' }])).toBe(true);
  });

  it('bước 6 không gửi nhóm còn "pending" lên máy chủ', () => {
    const out = step6Payload([grp('1', 'A', 'pending'), grp('2', 'B', 'silent'), grp('3', 'C', 'off')]);
    expect(out.map((g) => [g.id, g.listen_mode])).toEqual([['2', 'silent'], ['3', 'off']]);
  });

  it('tin 1-1: mặc định im lặng; máy chủ cũ (listen_direct=false, chưa có direct_mode) = không lưu', () => {
    expect(directModeOf({ direct_mode: undefined, listen_direct: undefined })).toBe('silent');
    expect(directModeOf({ direct_mode: undefined, listen_direct: false })).toBe('off');
    expect(directModeOf({ direct_mode: 'reply', listen_direct: true })).toBe('reply');
  });
});

describe('hộp "Nhóm mới — chọn Lắng nghe im lặng/Không nghe"', () => {
  it('có nhóm pending: hiện hộp với tên nhóm, giải thích chưa lưu tin + vẫn thu thành viên', async () => {
    mockFetch((c) => (c.url.includes('pending=1') ? json(200, PENDING) : fallback(c)));
    renderCard(zalo());
    const box = await screen.findByTestId('newgroups-zalo');
    expect(within(box).getByText(/Nhóm mới — chọn Lắng nghe im lặng hay Không nghe \(2\)/)).toBeInTheDocument();
    expect(within(box).getByText('Khách sỉ Hà Nội')).toBeInTheDocument();
    expect(box).toHaveTextContent(/chưa lưu tin nào/);
    expect(box).toHaveTextContent(/danh sách thành viên vẫn được ghi nhận/);
  });

  it('không có nhóm chờ: không hiện hộp (và không gọi máy chủ khi groups_pending = 0)', async () => {
    const calls = mockFetch((c) => fallback(c, { groups_pending: 0 }));
    renderCard(zalo({ groups_pending: 0 }));
    await screen.findByText('Zalo');
    expect(screen.queryByTestId('newgroups-zalo')).not.toBeInTheDocument();
    expect(calls.filter((c) => c.url.includes('pending=1'))).toHaveLength(0);
  });

  it('bấm Lắng nghe im lặng gọi PATCH /channels/zalo/groups/{id}/mode {mode: silent}', async () => {
    const user = userEvent.setup();
    const calls = mockFetch((c) => {
      if (c.method === 'PATCH') return json(200, grp('1', 'Khách sỉ Hà Nội', 'silent'));
      return c.url.includes('pending=1') ? json(200, PENDING) : fallback(c);
    });
    renderCard(zalo());
    await user.click(await screen.findByRole('button', { name: 'Lắng nghe im lặng — nhóm Khách sỉ Hà Nội' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    const p = calls.find((c) => c.method === 'PATCH')!;
    expect(p.url).toMatch(/\/channels\/zalo\/groups\/1\/mode$/);
    expect(p.body).toEqual({ mode: 'silent' });
    expect(await screen.findByText(/Đã chọn: Lắng nghe im lặng — lưu tin, không bao giờ trả lời/)).toBeInTheDocument();
  });

  it('"Tất cả 2 nhóm: Không nghe" gửi lần lượt từng nhóm với mode off', async () => {
    const user = userEvent.setup();
    const calls = mockFetch((c) => (c.method === 'PATCH' ? json(200, grp('1', 'x', 'off')) : c.url.includes('pending=1') ? json(200, PENDING) : fallback(c)));
    renderCard(zalo());
    await user.click(await screen.findByRole('button', { name: /Tất cả 2 nhóm: Không nghe/ }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'PATCH')).toHaveLength(2));
    expect(calls.filter((c) => c.method === 'PATCH').map((c) => [c.url.split('/groups/')[1], c.body])).toEqual([
      ['1/mode', { mode: 'off' }],
      ['2/mode', { mode: 'off' }],
    ]);
  });

  it('chọn lỗi: câu thân thiện + Chi tiết kỹ thuật, không dán object', async () => {
    const user = userEvent.setup();
    mockFetch((c) =>
      c.method === 'PATCH'
        ? json(404, { type: 'about:blank', title: 'Không tìm thấy Nhóm', status: 404, code: 'NOT_FOUND' })
        : c.url.includes('pending=1') ? json(200, PENDING) : fallback(c),
    );
    renderCard(zalo());
    await user.click(await screen.findByRole('button', { name: 'Không nghe — nhóm Đối tác kho' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/Không tìm thấy/);
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(alert.textContent).not.toMatch(/\[object Object\]/);
  });

  it('vai chỉ đọc: thấy hộp nhưng không có nút chọn', async () => {
    mockFetch((c) => (c.url.includes('pending=1') ? json(200, PENDING) : fallback(c)));
    renderCard(zalo(), false);
    const box = await screen.findByTestId('newgroups-zalo');
    expect(within(box).queryByRole('button')).not.toBeInTheDocument();
  });

  it('realtime group.discovered làm mới danh sách kênh và nhóm', () => {
    const spy = vi.spyOn(queryClient, 'invalidateQueries');
    applyEvent(queryClient, { type: 'group.discovered', data: { id: 'g', code: 'GRP-ZL-1', name: 'Mới', channel: 'zalo', listen_mode: 'pending' }, at: new Date().toISOString() } as never);
    const keys = spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey));
    expect(keys).toContain(JSON.stringify(qk2.channels));
    expect(keys).toContain(JSON.stringify(qk2.channelGroups('zalo')));
  });
});

describe('Cập nhật danh sách nhóm (F056)', () => {
  it('thẻ kênh đang hoạt động có nút; bấm gọi POST /channels/zalo/groups/refresh', async () => {
    const user = userEvent.setup();
    const calls = mockFetch((c) => (c.url.endsWith('/groups/refresh') ? json(202, { session_id: 's1', requested: true }) : fallback(c, { groups_pending: 0 })));
    renderCard(zalo({ groups_pending: 0 }));
    await user.click(await screen.findByRole('button', { name: /Cập nhật danh sách nhóm/ }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/channels/zalo/groups/refresh'))).toBe(true));
    expect(await screen.findByText(/Đang cập nhật danh sách nhóm Zalo/)).toBeInTheDocument();
  });

  it('bộ nhận tin đang tắt: câu tiếng Việt thường + Chi tiết kỹ thuật (không chữ Bridge)', async () => {
    const user = userEvent.setup();
    mockFetch((c) =>
      c.url.endsWith('/groups/refresh')
        ? json(503, { type: 'about:blank', title: 'Bộ nhận tin đang tắt', status: 503, code: 'BRIDGE_OFFLINE' })
        : fallback(c, { groups_pending: 0 }),
    );
    renderCard(zalo({ groups_pending: 0 }));
    await user.click(await screen.findByRole('button', { name: /Cập nhật danh sách nhóm/ }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/Bộ nhận tin Zalo\/WhatsApp đang tắt/);
    expect(alert).not.toHaveTextContent(/[Bb]ridge/);
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
  });

  it('kênh chưa hoạt động hoặc vai chỉ đọc: không có nút', async () => {
    mockFetch((c) => fallback(c, { groups_pending: 0, state: 'logged_out' }));
    const { unmount } = renderCard(zalo({ state: 'logged_out', groups_pending: 0 }));
    await screen.findByText('Zalo');
    expect(screen.queryByRole('button', { name: /Cập nhật danh sách nhóm/ })).not.toBeInTheDocument();
    unmount();
    queryClient.clear();
    renderCard(zalo({ groups_pending: 0 }), false);
    await screen.findByText('Zalo');
    expect(screen.queryByRole('button', { name: /Cập nhật danh sách nhóm/ })).not.toBeInTheDocument();
  });
});

describe('hộp nhóm của kênh: chế độ + tin 1-1', () => {
  function renderDialog(c: Channel, groups: ChannelGroup[]) {
    mockFetch((call) => (call.method === 'PATCH' && call.url.endsWith('/channels/zalo') ? json(200, { ...c, direct_mode: 'off', listen_direct: false }) : json(200, groups)));
    return render(
      <QueryClientProvider client={queryClient}>
        <GroupsDialog open onClose={() => {}} channel={c} canManage />
        <PinDialogHost />
        <ToastHost />
      </QueryClientProvider>,
    );
  }

  it('nhóm pending hiện "Chờ Sếp chọn" (không chọn được lại), nhóm đã rời được ghi chú', async () => {
    renderDialog(zalo(), [grp('1', 'Nhóm chờ', 'pending'), grp('2', 'Nhóm cũ', 'silent', 3, { left: true })]);
    const sel = (await screen.findByLabelText('Chế độ lắng nghe của Nhóm chờ')) as HTMLSelectElement;
    expect(sel.value).toBe('pending');
    expect(within(sel).getByRole('option', { name: 'Chờ Sếp chọn' })).toBeDisabled();
    expect(within(sel).getByRole('option', { name: 'Nghe hết, chỉ trả lời khi được tag' })).toBeInTheDocument();
    expect(screen.getByText(/Sếp đã rời nhóm/)).toBeInTheDocument();
    expect(screen.getByText(/Nhóm mới mặc định vào Lắng nghe im lặng — lưu hết tin, không bao giờ trả lời \(kể cả khi được tag\)/)).toBeInTheDocument();
  });

  it('tin 1-1: ba mức, mặc định "Lưu im lặng"; đổi gọi PATCH direct_mode', async () => {
    const user = userEvent.setup();
    renderDialog(zalo(), [grp('1', 'A', 'silent')]);
    const sel = (await screen.findByLabelText('Tin nhắn 1-1 trên Zalo')) as HTMLSelectElement;
    expect(sel.value).toBe('silent');
    expect(screen.getByTestId('direct-hint')).toHaveTextContent(/không tự trả lời, không đưa cho trợ lý trực/);
    expect(within(sel).getAllByRole('option').map((o) => o.textContent)).toEqual(['Lưu im lặng', 'Lưu và cho trợ lý trả lời', 'Không lưu']);
    await user.selectOptions(sel, 'off');
    await waitFor(() => expect(screen.getByLabelText('Tin nhắn 1-1 trên Zalo')).toBeInTheDocument());
  });
});

const stepProps = {
  meta: { n: 6, key: 'groups', title: 'Chọn nhóm lắng nghe' },
  description: 'mô tả',
  onBack: () => {},
  onSaved: () => {},
  formRef: { current: null },
  onSkip: () => {},
  skipping: false,
  skipError: null,
} as unknown as StepProps;

describe('Bước 6 Thiết lập', () => {
  it('chưa có nhóm: có nút Cập nhật danh sách nhóm thay cho "chờ hệ thống đồng bộ"', async () => {
    mockFetch((c) => (c.url.endsWith('/channels') ? json(200, [zalo({ groups_pending: 0 })]) : json(200, [])));
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <Step6Groups {...stepProps} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole('button', { name: /Cập nhật danh sách nhóm/ })).toBeInTheDocument();
    expect(screen.queryByText(/chờ hệ thống đồng bộ/)).not.toBeInTheDocument();
  });
});

// Review v0.1.65 (P3): `view_scope` chưa được áp ở máy chủ ⇒ hướng dẫn bước 6 không được hứa "chọn ai được xem dữ liệu nhóm".
describe('Hướng dẫn nhóm: «Phạm vi xem» nói đúng sự thật', () => {
  it('việc hướng dẫn và mô tả bước 6 gọi «Phạm vi xem» là ghi chú, quyền xem thật theo vai trò', () => {
    const item = GUIDE.find((g) => g.steps.some((x) => x.includes('Phạm vi xem')));
    expect(item).toBeDefined();
    const step = item!.steps.find((x) => x.includes('Phạm vi xem'))!;
    expect(step).toMatch(/ghi chú/);
    expect(step).toMatch(/theo vai trò/);
    expect(step).not.toMatch(/chọn ai được xem dữ liệu/);
    expect(STEP_DESCRIPTIONS[6]).toMatch(/ghi chú/);
    expect(STEP_DESCRIPTIONS[6]).not.toMatch(/ai được xem dữ liệu/);
  });
});
