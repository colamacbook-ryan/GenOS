import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { Channel, ChannelGroup } from '@gen-harness/contracts';
import { ChannelCard } from '../../src/screens/system/ChannelCard';
import { LISTEN_MODES, NEW_GROUP_MODES, newGroupModeOf } from '../../src/screens/system/systemModel';
import { PinDialogHost } from '../../src/shell/PinDialogHost';
import { ToastHost } from '../../src/shell/ToastHost';
import { queryClient } from '../../src/lib/queryClient';
import { qk2, useChannels } from '../../src/lib/dataQueries';

// v0.1.65 (F1d) + QD 17/10: «Nhóm mới» có mặc định cho các nhóm SAU — MẶC ĐỊNH Lắng nghe im lặng (lưu hết, không bao giờ
// trả lời, kể cả khi được tag); hộp hỏi chỉ hiện khi Sếp chọn «Hỏi Sếp», ô «Áp dụng cho các nhóm mới sau này» không bật sẵn,
// ô chỉnh «Nhóm mới» ở thẻ kênh (đổi cần PIN), khối «N nhóm mới đã vào Lắng nghe im lặng». Máy chủ giả bằng fetch.

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

const grp = (id: string, name: string, mode: ChannelGroup['listen_mode']): ChannelGroup => ({
  id, code: `GRP-ZL-${id}`, name, members: 5, kind: 'internal', listen_mode: mode, view_scope: 'owner',
});

const zalo = (over: Partial<Channel> = {}): Channel => ({
  type: 'zalo', name: 'Zalo', installed: true, id: 'c1', state: 'active', account_label: 'iPhone của Sếp',
  started_at: new Date(Date.now() - 3600_000).toISOString(), groups_listening: 1, outbound_queued: 0,
  last_heartbeat_at: new Date().toISOString(), stats: null, qr: null, listen_direct: true, direct_mode: 'silent', groups_pending: 2,
  new_group_mode: 'ask', new_group_asked: false, ...over,   // 'ask' = Sếp đã chọn «Hỏi Sếp» (mặc định thật là 'silent')
});

const PENDING = [grp('1', 'Khách sỉ Hà Nội', 'pending'), grp('2', 'Đối tác kho', 'pending')];
const fallback = (c: Call, over: Partial<Channel> = {}) => json(200, c.url.endsWith('/channels') ? [zalo(over)] : []);

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

describe('hằng «Nhóm mới»', () => {
  it('ba mức, mặc định Lắng nghe im lặng; máy chủ cũ (không có khoá) = không hiện; giá trị lạ = Lắng nghe im lặng', () => {
    expect(NEW_GROUP_MODES.map((m) => [m.value, m.label])).toEqual([
      ['silent', 'Lắng nghe im lặng — lưu hết, không bao giờ trả lời (kể cả khi được tag)'],
      ['ask', 'Hỏi Sếp'],
      ['off', 'Không nghe'],
    ]);
    expect(NEW_GROUP_MODES[0]!.hint).toMatch(/Mặc định/);
    expect(newGroupModeOf({})).toBeUndefined();
    expect(newGroupModeOf({ new_group_mode: 'ask' })).toBe('ask');
    expect(newGroupModeOf({ new_group_mode: 'bậy' as never })).toBe('silent');
  });

  it('bảng chế độ nghe của nhóm: «Lắng nghe im lặng — … không bao giờ trả lời (kể cả khi được tag)»; tagged_only vẫn trả lời khi được tag', () => {
    expect(LISTEN_MODES.find((m) => m.value === 'silent')!.label).toBe('Lắng nghe im lặng — lưu hết, không bao giờ trả lời (kể cả khi được tag)');
    expect(LISTEN_MODES.find((m) => m.value === 'tagged_only')!.label).toBe('Nghe hết, chỉ trả lời khi được tag');
  });
});

describe('hộp hỏi hiện khi còn nhóm chờ chọn (QD 17/10 + review v0.1.65)', () => {
  it('đổi «Nhóm mới» sang silent/off khi còn nhóm pending: VẪN hiện hộp cho tới khi hết nhóm chờ (không để nhóm kẹt pending, không lưu tin)', async () => {
    for (const mode of ['silent', 'off'] as const) {
      queryClient.clear();
      const calls = mockFetch((c) => (c.url.includes('pending=1') ? json(200, PENDING) : fallback(c, { new_group_mode: mode })));
      const view = renderCard(zalo({ new_group_mode: mode }));
      const box = await screen.findByTestId('newgroups-zalo');
      expect(box).toHaveTextContent('Nhóm mới — chọn Lắng nghe im lặng hay Không nghe (2)');
      expect(calls.some((c) => c.url.includes('pending=1'))).toBe(true);
      view.unmount();
    }
  });

  it('mặc định silent và KHÔNG còn nhóm pending: không hiện hộp và không gọi máy chủ lấy nhóm chờ', async () => {
    const calls = mockFetch((c) => (c.url.includes('pending=1') ? json(200, PENDING) : fallback(c, { new_group_mode: 'silent', groups_pending: 0 })));
    renderCard(zalo({ new_group_mode: 'silent', groups_pending: 0 }));
    await screen.findByLabelText('Nhóm mới trên Zalo');
    expect(screen.queryByTestId('newgroups-zalo')).not.toBeInTheDocument();
    expect(calls.some((c) => c.url.includes('pending=1'))).toBe(false);
  });

  it('máy chủ cũ (không có groups_pending, không có new_group_mode): hộp hiện như trước; có new_group_mode ≠ ask mà không có groups_pending: không hiện', async () => {
    mockFetch((c) => (c.url.includes('pending=1') ? json(200, PENDING) : fallback(c)));
    const { groups_pending: _drop, ...legacy } = zalo({ new_group_mode: undefined });
    void _drop;
    const v = renderCard(legacy as Channel);
    await screen.findByTestId('newgroups-zalo');
    v.unmount();
    queryClient.clear();
    const { groups_pending: _drop2, ...silentNoCount } = zalo({ new_group_mode: 'silent' });
    void _drop2;
    renderCard(silentNoCount as Channel);
    await screen.findByLabelText('Nhóm mới trên Zalo');
    expect(screen.queryByTestId('newgroups-zalo')).not.toBeInTheDocument();
  });

  it('chọn «Hỏi Sếp» ⇒ hộp hiện như cũ, nút ghi «Lắng nghe im lặng»', async () => {
    mockFetch((c) => (c.url.includes('pending=1') ? json(200, PENDING) : fallback(c)));
    renderCard(zalo());
    const box = await screen.findByTestId('newgroups-zalo');
    expect(box).toHaveTextContent('Nhóm mới — chọn Lắng nghe im lặng hay Không nghe (2)');
    expect(within(box).getByRole('button', { name: 'Lắng nghe im lặng — nhóm Khách sỉ Hà Nội' })).toBeInTheDocument();
    expect(box).toHaveTextContent(/không bao giờ trả lời \(kể cả khi được tag\)/);
  });
});

describe('khối «N nhóm mới đã vào Lắng nghe im lặng»', () => {
  it('có nhóm mới: hiện số nhóm + giải thích không bao giờ trả lời; Đã biết gọi POST /groups/seen và ẩn khối', async () => {
    const user = userEvent.setup();
    const calls = mockFetch((c) =>
      c.method === 'POST' && c.url.endsWith('/channels/zalo/groups/seen')
        ? json(200, zalo({ new_group_mode: 'silent', groups_pending: 0, new_groups_silent: 0 }))
        : fallback(c, { new_group_mode: 'silent', groups_pending: 0, new_groups_silent: 3 }),
    );
    renderCard(zalo({ new_group_mode: 'silent', groups_pending: 0, new_groups_silent: 3 }));
    const box = await screen.findByTestId('newsilent-zalo');
    expect(box).toHaveTextContent('3 nhóm mới đã vào Lắng nghe im lặng');
    expect(box).toHaveTextContent(/không bao giờ trả lời \(kể cả khi được tag\)/);
    await user.click(within(box).getByRole('button', { name: 'Đã biết' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/channels/zalo/groups/seen'))).toBe(true));
    await waitFor(() => expect(screen.queryByTestId('newsilent-zalo')).not.toBeInTheDocument());
  });

  it('không có nhóm mới / máy chủ cũ không gửi số: không hiện; vai chỉ đọc: có khối nhưng không có nút Đã biết', async () => {
    mockFetch((c) => fallback(c, { new_group_mode: 'silent', groups_pending: 0 }));
    const { unmount } = renderCard(zalo({ new_group_mode: 'silent', groups_pending: 0 }));
    await screen.findByLabelText('Nhóm mới trên Zalo');
    expect(screen.queryByTestId('newsilent-zalo')).not.toBeInTheDocument();
    unmount();
    queryClient.clear();
    mockFetch((c) => fallback(c, { new_group_mode: 'silent', groups_pending: 0, new_groups_silent: 2 }));
    renderCard(zalo({ new_group_mode: 'silent', groups_pending: 0, new_groups_silent: 2 }), false);
    const box = await screen.findByTestId('newsilent-zalo');
    expect(box).toHaveTextContent('2 nhóm mới đã vào Lắng nghe im lặng');
    expect(within(box).queryByRole('button', { name: 'Đã biết' })).not.toBeInTheDocument();
  });
});

describe('hộp hỏi: ô «Áp dụng cho các nhóm mới sau này»', () => {
  it('KHÔNG bật sẵn (kể cả lần hỏi đầu sau khi nối kênh); giải thích cần PIN', async () => {
    mockFetch((c) => (c.url.includes('pending=1') ? json(200, PENDING) : fallback(c)));
    renderCard(zalo({ new_group_asked: false }));
    const apply = await screen.findByTestId('newgroups-apply-zalo');
    expect(within(apply).getByRole('checkbox')).not.toBeChecked();
    expect(apply).toHaveTextContent(/Áp dụng cho các nhóm mới sau này/);
    expect(apply).toHaveTextContent(/Cần mã PIN/);
  });

  it('máy chủ cũ (không có new_group_mode) hoặc vai chỉ đọc: không có ô', async () => {
    mockFetch((c) => (c.url.includes('pending=1') ? json(200, PENDING) : fallback(c)));
    const old = zalo();
    delete old.new_group_mode;
    delete old.new_group_asked;
    const { unmount } = renderCard(old);
    await screen.findByTestId('newgroups-zalo');
    expect(screen.queryByTestId('newgroups-apply-zalo')).not.toBeInTheDocument();
    unmount();
    queryClient.clear();
    renderCard(zalo(), false);
    await screen.findByTestId('newgroups-zalo');
    expect(screen.queryByTestId('newgroups-apply-zalo')).not.toBeInTheDocument();
  });

  it('không tick: bấm Lắng nghe im lặng chỉ chọn cho nhóm này (không gửi apply_to_new)', async () => {
    const user = userEvent.setup();
    const calls = mockFetch((c) =>
      c.method === 'PATCH' ? json(200, grp('1', 'Khách sỉ Hà Nội', 'silent')) : c.url.includes('pending=1') ? json(200, PENDING) : fallback(c),
    );
    renderCard(zalo());
    await user.click(await screen.findByRole('button', { name: 'Lắng nghe im lặng — nhóm Khách sỉ Hà Nội' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    const p = calls.find((c) => c.method === 'PATCH')!;
    expect(p.url).toMatch(/\/channels\/zalo\/groups\/1\/mode$/);
    expect(p.body).toEqual({ mode: 'silent' });
    expect(await screen.findByText('Đã chọn: Lắng nghe im lặng — lưu tin, không bao giờ trả lời')).toBeInTheDocument();
  });

  it('tick ô: gửi apply_to_new và báo nhóm sau cũng vào Lắng nghe im lặng', async () => {
    const user = userEvent.setup();
    const calls = mockFetch((c) =>
      c.method === 'PATCH' ? json(200, grp('1', 'Khách sỉ Hà Nội', 'silent')) : c.url.includes('pending=1') ? json(200, PENDING) : fallback(c),
    );
    renderCard(zalo());
    await user.click(within(await screen.findByTestId('newgroups-apply-zalo')).getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: 'Lắng nghe im lặng — nhóm Khách sỉ Hà Nội' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    expect(calls.find((c) => c.method === 'PATCH')!.body).toEqual({ mode: 'silent', apply_to_new: true });
    expect(await screen.findByText(/Nhóm mới sau này cũng vào Lắng nghe im lặng/)).toBeInTheDocument();
  });

  it('«Tất cả 2 nhóm: Không nghe» + tick ô: mọi lượt gửi mode off kèm apply_to_new', async () => {
    const user = userEvent.setup();
    const calls = mockFetch((c) =>
      c.method === 'PATCH' ? json(200, grp('1', 'x', 'off')) : c.url.includes('pending=1') ? json(200, PENDING) : fallback(c),
    );
    renderCard(zalo());
    await user.click(within(await screen.findByTestId('newgroups-apply-zalo')).getByRole('checkbox'));
    await user.click(await screen.findByRole('button', { name: /Tất cả 2 nhóm: Không nghe/ }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'PATCH')).toHaveLength(2));
    expect(calls.filter((c) => c.method === 'PATCH').map((c) => c.body)).toEqual([
      { mode: 'off', apply_to_new: true },
      { mode: 'off', apply_to_new: true },
    ]);
  });
});

describe('thẻ kênh: ô «Nhóm mới»', () => {
  function renderWith(c: Channel, canManage = true) {
    const calls = mockFetch((call) => {
      if (call.method === 'PATCH' && call.url.endsWith('/channels/zalo')) {
        return json(200, { ...c, new_group_mode: (call.body as { new_group_mode: Channel['new_group_mode'] }).new_group_mode, new_group_asked: true });
      }
      return fallback(call, { groups_pending: 0, ...c });
    });
    renderCard({ ...c, groups_pending: 0 }, canManage);
    return calls;
  }

  it('hiện mặc định «Lắng nghe im lặng» với giải thích và 3 lựa chọn; ghi chú cần mã PIN', async () => {
    renderWith(zalo({ new_group_mode: 'silent' }));
    const sel = (await screen.findByLabelText('Nhóm mới trên Zalo')) as HTMLSelectElement;
    expect(sel.value).toBe('silent');
    expect(within(sel).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Lắng nghe im lặng — lưu hết, không bao giờ trả lời (kể cả khi được tag)',
      'Hỏi Sếp',
      'Không nghe',
    ]);
    expect(screen.getByTestId('newmode-hint-zalo')).toHaveTextContent(/không bao giờ trả lời, kể cả khi được tag.*Đổi cần mã PIN/s);
    expect(screen.getByTestId('newmode-hint-zalo')).toHaveTextContent(/Chỉ áp cho nhóm phát hiện sau này/);
  });

  it('chọn «Hỏi Sếp»: giải thích nhóm mới chờ Sếp chọn', async () => {
    renderWith(zalo({ new_group_mode: 'ask' }));
    await screen.findByLabelText('Nhóm mới trên Zalo');
    expect(screen.getByTestId('newmode-hint-zalo')).toHaveTextContent(/chờ Sếp chọn/);
  });

  it('đổi sang Lắng nghe im lặng gọi PATCH /channels/zalo {new_group_mode: silent} và báo kết quả', async () => {
    const user = userEvent.setup();
    const calls = renderWith(zalo());
    await user.selectOptions(await screen.findByLabelText('Nhóm mới trên Zalo'), 'silent');
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    const p = calls.find((c) => c.method === 'PATCH')!;
    expect(p.url).toMatch(/\/channels\/zalo$/);
    expect(p.body).toEqual({ new_group_mode: 'silent' });
    expect(await screen.findByText(/nhóm mới tự vào Lắng nghe im lặng — lưu hết tin, không bao giờ trả lời/)).toBeInTheDocument();
    expect((screen.getByLabelText('Nhóm mới trên Zalo') as HTMLSelectElement).value).toBe('silent');
  });

  it('thiếu PIN (423): hộp nhập PIN hiện lên, chưa đổi gì', async () => {
    const user = userEvent.setup();
    const calls = mockFetch((call) =>
      call.method === 'PATCH'
        ? json(423, { type: 'about:blank', title: 'Thao tác này cần nhập mã PIN', status: 423, code: 'PIN_REQUIRED' })
        : fallback(call, { groups_pending: 0 }),
    );
    renderCard(zalo({ groups_pending: 0 }));
    await user.selectOptions(await screen.findByLabelText('Nhóm mới trên Zalo'), 'off');
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('vai chỉ đọc: thấy giá trị nhưng không đổi được; máy chủ cũ: không có ô', async () => {
    mockFetch((c) => fallback(c, { groups_pending: 0, new_group_mode: 'off' }));
    const { unmount } = renderCard(zalo({ groups_pending: 0, new_group_mode: 'off' }), false);
    const sel = (await screen.findByLabelText('Nhóm mới trên Zalo')) as HTMLSelectElement;
    expect(sel.value).toBe('off');
    expect(sel).toBeDisabled();
    unmount();
    queryClient.clear();
    const old = zalo({ groups_pending: 0 });
    delete old.new_group_mode;
    mockFetch((c) => json(200, c.url.endsWith('/channels') ? [old] : []));
    renderCard(old);
    await screen.findByText('Zalo');
    expect(screen.queryByLabelText('Nhóm mới trên Zalo')).not.toBeInTheDocument();
  });
});
