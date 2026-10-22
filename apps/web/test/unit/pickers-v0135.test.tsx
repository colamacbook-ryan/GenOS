/**
 * v0.1.35 (F-1): ô chọn người / trợ lý THẬT — hook dùng chung (`src/lib/pickers.ts`) + 3 hộp thoại giao/gán gửi
 * đúng id lấy từ `/pickers/users` / `/pickers/agents` (không còn danh sách cứng 'u-…' / 'agent-…').
 */
import type { ReactElement, ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, renderHook, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { CaseItem, DirChannel, DirCursorPage, DirGroup, DirPerson, InboxPage } from '@gen-harness/contracts';
import { InboxScreen } from '../../src/screens/queue/InboxScreen';
import { DealsScreen } from '../../src/screens/market/DealsScreen';
import { DirectoryScreen } from '../../src/screens/relations/DirectoryScreen';
import { queryClient } from '../../src/lib/queryClient';
import { errorText } from '../../src/lib/errorText';
import {
  EMPTY_AGENTS_TEXT,
  EMPTY_AGENTS_TEXT_ASK,
  EMPTY_USERS_TEXT,
  EMPTY_USERS_TEXT_ASK,
  TRUNCATED_USERS_TEXT,
  useAgentOptions,
  useAssignees,
} from '../../src/lib/pickers';
import { qk } from '../../src/lib/queries';
import { useUrlStateStore } from '../../src/lib/uiStore';
import { AGENT_IDS, USER_IDS } from '../mock-ids';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const problem = (status: number, code: string, title: string, extra: Record<string, unknown> = {}) =>
  new Response(JSON.stringify({ type: `https://gen-harness.local/errors/${code.toLowerCase()}`, title, status, code, detail: null, ...extra }), {
    status,
    headers: { 'Content-Type': 'application/problem+json' },
  });

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

const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;

/** Đặt /auth/me với bảng quyền cho trước (gợi ý khi rỗng phụ thuộc quyền mời người / tạo agent). */
function meWith(permissions: Record<string, string>, roleCode = 'custom') {
  const me = {
    id: USER_IDS.owner, email: 'x@genesis.local', display_name: 'X', role: { code: roleCode, name: 'Tuỳ biến' },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
    addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions,
  };
  queryClient.setQueryData(qk.me, me);
  return me;
}

function renderScreen(ui: ReactElement) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  queryClient.clear();
  useUrlStateStore.setState({ params: {} });
});

const USERS = {
  items: [
    { id: USER_IDS.minh, name: 'Anh Minh Kiểm', me: false },
    { id: USER_IDS.owner, name: 'Anh Nguyễn Văn A (Chủ)', me: true },
    { id: USER_IDS.lan, name: 'Chị Lan Phạm', me: false },
  ],
};
const AGENTS = {
  items: [
    { id: AGENT_IDS.hc, name: 'Admin hậu cần' },
    { id: AGENT_IDS.tls, name: 'Trợ lý thương mại' },
  ],
};

describe('hook dùng chung', () => {
  it('useAssignees: người đang đăng nhập là "Tôi" và đứng đầu, người khác giữ tên', async () => {
    mockFetch((c) => (c.url.includes('/pickers/users') ? json(200, USERS) : json(404)));
    const { result } = renderHook(() => useAssignees(), { wrapper });
    await waitFor(() => expect(result.current.query.isSuccess).toBe(true));
    expect(result.current.options).toEqual([
      { id: USER_IDS.owner, label: 'Tôi' },
      { id: USER_IDS.minh, label: 'Anh Minh Kiểm' },
      { id: USER_IDS.lan, label: 'Chị Lan Phạm' },
    ]);
    expect(result.current.hasOthers).toBe(true);
  });

  it('useAgentOptions đọc /pickers/agents', async () => {
    const calls = mockFetch((c) => (c.url.includes('/pickers/agents') ? json(200, AGENTS) : json(404)));
    const { result } = renderHook(() => useAgentOptions(), { wrapper });
    await waitFor(() => expect(result.current.query.isSuccess).toBe(true));
    expect(calls.some((c) => c.url.endsWith('/api/v1/pickers/agents'))).toBe(true);
    expect(result.current.options).toEqual([
      { id: AGENT_IDS.hc, label: 'Admin hậu cần' },
      { id: AGENT_IDS.tls, label: 'Trợ lý thương mại' },
    ]);
  });

  it('lỗi 403 → errorText là chuỗi thân thiện, không fallback danh sách cứng', async () => {
    mockFetch(() => problem(403, 'FORBIDDEN', 'Vai trò của bạn không có quyền thao tác này'));
    const { result } = renderHook(() => useAgentOptions(), { wrapper });
    await waitFor(() => expect(result.current.query.isError).toBe(true));
    expect(result.current.options).toEqual([]);
    const text = errorText(result.current.query.error);
    expect(typeof text).toBe('string');
    expect(text).toBe('Vai trò của bạn không có quyền làm thao tác này.');
  });
});

const INBOX: InboxPage = {
  items: [
    {
      id: 'iq-1', code: 'OPP-1842', item_type: 'unit', tab: 'opportunity', title: 'AskedPrice',
      summary: 'Xưởng gỗ Bình Dương hỏi giá 3 container MDF.', priority: 'P1', created_at: '2026-09-24T02:00:00Z',
      score: 0.91, confidence_band: 'cao', subject: null, group: null, agent: null,
      alert_type: null, alert_type_label: null, suggested_action: null,
    },
  ],
  next_cursor: null,
  total: 1,
  counts: { all: 1, opportunity: 1, alert: 0, approval: 0, reply: 0, candidate: 0 },
};

describe('Hộp thư ý nghĩa › Giao cho người khác', () => {
  it('gửi user_id lấy từ /pickers/users', async () => {
    const calls = mockFetch((c) => {
      if (c.url.includes('/pickers/users')) return json(200, USERS);
      if (c.method === 'POST' && c.url.includes('/inbox/iq-1/assign')) return json(200, { ok: true, assigned_to: { id: USER_IDS.lan, name: 'Chị Lan Phạm' } });
      if (c.url.includes('/inbox')) return json(200, INBOX);
      return json(404);
    });
    const user = userEvent.setup();
    renderScreen(<InboxScreen />);
    await screen.findByText(/Xưởng gỗ Bình Dương hỏi giá/);
    await user.click(screen.getByRole('button', { name: 'Giao cho người khác' }));
    const dlg = await screen.findByRole('dialog', { name: 'Giao cho người khác' });
    await within(dlg).findByText('Chị Lan Phạm');
    const names = within(within(dlg).getByRole('list')).getAllByRole('button').map((b) => b.textContent);
    expect(names).toEqual(['Tôi', 'Anh Minh Kiểm', 'Chị Lan Phạm']);
    await user.click(within(dlg).getByText('Chị Lan Phạm'));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.includes('/inbox/iq-1/assign'))).toBe(true));
    const call = calls.find((c) => c.method === 'POST' && c.url.includes('/inbox/iq-1/assign'))!;
    expect(call.body).toEqual({ user_id: USER_IDS.lan });
  });

  it('lỗi tải danh sách hiện chuỗi thân thiện (không render object)', async () => {
    mockFetch((c) => {
      if (c.url.includes('/pickers/users')) return problem(503, 'DB_UNAVAILABLE', 'Cơ sở dữ liệu đang không phản hồi');
      if (c.url.includes('/inbox')) return json(200, INBOX);
      return json(404);
    });
    const user = userEvent.setup();
    renderScreen(<InboxScreen />);
    await screen.findByText(/Xưởng gỗ Bình Dương hỏi giá/);
    await user.click(screen.getByRole('button', { name: 'Giao cho người khác' }));
    const dlg = await screen.findByRole('dialog', { name: 'Giao cho người khác' });
    const alert = await within(dlg).findByRole('alert', {}, { timeout: 8000 });
    expect(alert.textContent).toMatch(/Cơ sở dữ liệu/);
    expect(alert.textContent).not.toContain('[object Object]');
  });

  it.each([
    ['có roles.manage → chỉ đường Điều khiển hệ thống › Người dùng', { 'queue.act': 'all', 'roles.manage': 'all' }, EMPTY_USERS_TEXT],
    ['operator (không roles.manage) → nhờ Owner mời', { 'queue.act': 'team' }, EMPTY_USERS_TEXT_ASK],
  ])('chỉ có người đang đăng nhập → "Tôi" + gợi ý: %s', async (_name, perms, text) => {
    const me = meWith(perms);
    mockFetch((c) => {
      if (c.url.includes('/auth/me')) return json(200, me);
      if (c.url.includes('/pickers/users')) return json(200, { items: [{ id: USER_IDS.owner, name: 'Anh Nguyễn Văn A (Chủ)', me: true }] });
      if (c.url.includes('/inbox')) return json(200, INBOX);
      return json(404);
    });
    const user = userEvent.setup();
    renderScreen(<InboxScreen />);
    await screen.findByText(/Xưởng gỗ Bình Dương hỏi giá/);
    await user.click(screen.getByRole('button', { name: 'Giao cho người khác' }));
    const dlg = await screen.findByRole('dialog', { name: 'Giao cho người khác' });
    expect(await within(dlg).findByText(text)).toBeInTheDocument();
    expect(within(dlg).getByText('Tôi')).toBeInTheDocument();
  });
});

const CASE: CaseItem = {
  id: 'case-1', code: 'CAS-0018', kind: 'complaint', priority: 'P1', title: 'Khách phàn nàn giao trễ', status: 'open',
  assignee: null, subject: null, opened_at: '2026-09-24T00:00:00Z', resolved_at: null, updated_at: '2026-09-24T00:00:00Z',
};

const CASE_WRITER = { 'opportunity.read': 'all', 'opportunity.write': 'all' };
const DIR_WRITER = { 'profile.read': 'all', 'profile.write': 'all' };

describe('Deal & Vụ việc › Gán người xử lý', () => {
  it('gửi assignee_user_id lấy từ /pickers/users, giữ nút "Chưa gán"', async () => {
    const me = meWith(CASE_WRITER);
    const calls = mockFetch((c) => {
      if (c.url.includes('/auth/me')) return json(200, me);
      if (c.url.includes('/pickers/users')) return json(200, USERS);
      if (c.method === 'PATCH' && c.url.includes('/cases/case-1')) return json(200, { ...CASE, assignee: { id: USER_IDS.lan, name: 'Chị Lan Phạm' } });
      if (c.url.includes('/cases')) return json(200, { items: [CASE], next_cursor: null, total: 1 });
      if (c.url.includes('/deals')) return json(200, { items: [], next_cursor: null, total: 0 });
      if (c.url.includes('/opportunities')) return json(200, { items: [], next_cursor: null, total: 0 });
      return json(404);
    });
    useUrlStateStore.setState({ params: { dtab: 'cases' } });
    const user = userEvent.setup();
    renderScreen(<DealsScreen />);
    const row = (await screen.findByText('CAS-0018')).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: 'Đổi' }));
    const dlg = await screen.findByRole('dialog', { name: 'Gán người xử lý' });
    expect(within(dlg).getByText('Chưa gán')).toBeInTheDocument();
    await user.click(await within(dlg).findByText('Chị Lan Phạm'));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH' && c.url.includes('/cases/case-1'))).toBe(true));
    const call = calls.find((c) => c.method === 'PATCH' && c.url.includes('/cases/case-1'))!;
    expect(call.body).toEqual({ assignee_user_id: USER_IDS.lan });
  });

  it('người đang được gán đã bị khoá/xoá (không có trong /pickers/users) → dòng "(đã khoá/xoá)" được đánh dấu chọn', async () => {
    const LOCKED = { ...CASE, assignee: { id: '0199a000-0000-7000-8000-00000000dead', name: 'Anh Tùng' } };
    const me = meWith(CASE_WRITER);
    mockFetch((c) => {
      if (c.url.includes('/auth/me')) return json(200, me);
      if (c.url.includes('/pickers/users')) return json(200, USERS);
      if (c.url.includes('/cases')) return json(200, { items: [LOCKED], next_cursor: null, total: 1 });
      if (c.url.includes('/deals')) return json(200, { items: [], next_cursor: null, total: 0 });
      if (c.url.includes('/opportunities')) return json(200, { items: [], next_cursor: null, total: 0 });
      return json(404);
    });
    useUrlStateStore.setState({ params: { dtab: 'cases' } });
    const user = userEvent.setup();
    renderScreen(<DealsScreen />);
    const row = (await screen.findByText('CAS-0018')).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: 'Đổi' }));
    const dlg = await screen.findByRole('dialog', { name: 'Gán người xử lý' });
    const locked = (await within(dlg).findByText('Anh Tùng (đã khoá/xoá)')).closest('button') as HTMLElement;
    expect(locked).toHaveAttribute('aria-pressed', 'true');
    expect(within(dlg).getByText('Chưa gán').closest('button')).toHaveAttribute('aria-pressed', 'false');
    expect(within(dlg).getByText('Chị Lan Phạm').closest('button')).toHaveAttribute('aria-pressed', 'false');
    expect(within(dlg).getByText('Anh Tùng (đã khoá/xoá)').closest('button')).toHaveAttribute('title', expect.stringMatching(/khoá hoặc xoá/));
  });

  it('đang lưu → mọi lựa chọn bị khoá (bấm đúp không gửi hai PATCH)', async () => {
    const me = meWith(CASE_WRITER);
    let release: (r: Response) => void = () => {};
    const calls = mockFetch((c) => {
      if (c.url.includes('/auth/me')) return json(200, me);
      if (c.url.includes('/pickers/users')) return json(200, USERS);
      if (c.method === 'PATCH' && c.url.includes('/cases/case-1')) return new Promise<Response>((res) => { release = res; });
      if (c.url.includes('/cases')) return json(200, { items: [CASE], next_cursor: null, total: 1 });
      if (c.url.includes('/deals')) return json(200, { items: [], next_cursor: null, total: 0 });
      if (c.url.includes('/opportunities')) return json(200, { items: [], next_cursor: null, total: 0 });
      return json(404);
    });
    useUrlStateStore.setState({ params: { dtab: 'cases' } });
    const user = userEvent.setup();
    renderScreen(<DealsScreen />);
    const row = (await screen.findByText('CAS-0018')).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: 'Đổi' }));
    const dlg = await screen.findByRole('dialog', { name: 'Gán người xử lý' });
    const lan = (await within(dlg).findByText('Chị Lan Phạm')).closest('button') as HTMLElement;
    await user.click(lan);
    await waitFor(() => expect(lan).toBeDisabled());
    expect(within(dlg).getByText('Chưa gán').closest('button')).toBeDisabled();
    await user.click(lan);
    expect(calls.filter((c) => c.method === 'PATCH').length).toBe(1);
    release(json(200, { ...CASE, assignee: { id: USER_IDS.lan, name: 'Chị Lan Phạm' } }));
  });

  it('danh sách bị cắt ở trần (truncated) → hộp chọn ghi chú rõ', async () => {
    const me = meWith(CASE_WRITER);
    mockFetch((c) => {
      if (c.url.includes('/auth/me')) return json(200, me);
      if (c.url.includes('/pickers/users')) return json(200, { ...USERS, truncated: true });
      if (c.url.includes('/cases')) return json(200, { items: [CASE], next_cursor: null, total: 1 });
      if (c.url.includes('/deals')) return json(200, { items: [], next_cursor: null, total: 0 });
      if (c.url.includes('/opportunities')) return json(200, { items: [], next_cursor: null, total: 0 });
      return json(404);
    });
    useUrlStateStore.setState({ params: { dtab: 'cases' } });
    const user = userEvent.setup();
    renderScreen(<DealsScreen />);
    const row = (await screen.findByText('CAS-0018')).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: 'Đổi' }));
    const dlg = await screen.findByRole('dialog', { name: 'Gán người xử lý' });
    expect(await within(dlg).findByText(TRUNCATED_USERS_TEXT)).toBeInTheDocument();
  });

  it('vai trò không có opportunity.write (Auditor) → không có nút "Đổi"', async () => {
    const me = meWith({ 'opportunity.read': 'all', 'opportunity.write': 'none' });
    mockFetch((c) => {
      if (c.url.includes('/auth/me')) return json(200, me);
      if (c.url.includes('/cases')) return json(200, { items: [CASE], next_cursor: null, total: 1 });
      if (c.url.includes('/deals')) return json(200, { items: [], next_cursor: null, total: 0 });
      if (c.url.includes('/opportunities')) return json(200, { items: [], next_cursor: null, total: 0 });
      return json(404);
    });
    useUrlStateStore.setState({ params: { dtab: 'cases' } });
    renderScreen(<DealsScreen />);
    const row = (await screen.findByText('CAS-0018')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Chưa gán')).toBeInTheDocument();
    expect(within(row).queryByRole('button', { name: 'Đổi' })).toBeNull();
  });
});

const CHANNELS: DirChannel[] = [{ id: 'ch-zalo', type: 'zalo', name: 'Zalo', state: 'active', group_count: 1, events_24h: 412 }];
const GROUPS: DirCursorPage<DirGroup> = {
  items: [
    {
      id: 'g1', code: 'GRP-ZL-0114', name: 'Vận hành Genesis — Quý 4', kind: 'internal', listen_mode: 'tagged_only',
      member_count: 24, events_24h: 412, heat: 87, channel: { type: 'zalo', name: 'Zalo' },
      bot: { id: AGENT_IDS.ka, name: 'Key Account junior' }, created_at: '2026-09-01T00:00:00Z',
    },
  ],
  next_cursor: null,
  total: 1,
};

describe('Nhóm & Con người › Gán BOT trực nhóm', () => {
  it('gửi agent_id lấy từ /pickers/agents; BOT hiện tại đã tắt vẫn hiện "(đã tắt)" và được chọn', async () => {
    const me = meWith(DIR_WRITER);
    const calls = mockFetch((c) => {
      if (c.url.includes('/auth/me')) return json(200, me);
      if (c.url.includes('/pickers/agents')) return json(200, AGENTS);
      if (c.url.includes('/directory/channels')) return json(200, CHANNELS);
      if (c.method === 'POST' && c.url.includes('/directory/groups/g1/bot')) return json(200, { ok: true });
      if (c.url.includes('/directory/groups')) return json(200, GROUPS);
      return json(404);
    });
    const user = userEvent.setup();
    renderScreen(<DirectoryScreen />);
    const row = (await screen.findByText('GRP-ZL-0114')).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /Đổi|Gán/ }));
    const dlg = await screen.findByRole('dialog', { name: 'Gán BOT trực nhóm' });
    const stale = (await within(dlg).findByText('Key Account junior (đã tắt)')).closest('button')!;
    expect(stale).toHaveAttribute('aria-pressed', 'true');
    await user.click(within(dlg).getByText('Admin hậu cần'));
    await user.click(within(dlg).getByRole('button', { name: 'Lưu' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.includes('/directory/groups/g1/bot'))).toBe(true));
    const call = calls.find((c) => c.method === 'POST' && c.url.includes('/directory/groups/g1/bot'))!;
    expect(call.body).toEqual({ agent_id: AGENT_IDS.hc });
  });

  it('nhóm «Lắng nghe im lặng» (review v0.1.65): chọn BOT thì cảnh báo BOT sẽ không trả lời; bỏ chọn / nhóm khác thì không cảnh báo', async () => {
    const me = meWith(DIR_WRITER);
    const silentGroups: DirCursorPage<DirGroup> = { ...GROUPS, items: [{ ...GROUPS.items[0]!, listen_mode: 'silent' }] };
    mockFetch((c) => {
      if (c.url.includes('/auth/me')) return json(200, me);
      if (c.url.includes('/pickers/agents')) return json(200, AGENTS);
      if (c.url.includes('/directory/channels')) return json(200, CHANNELS);
      if (c.url.includes('/directory/groups')) return json(200, silentGroups);
      return json(404);
    });
    const user = userEvent.setup();
    renderScreen(<DirectoryScreen />);
    const row = (await screen.findByText('GRP-ZL-0114')).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /Đổi|Gán/ }));
    const dlg = await screen.findByRole('dialog', { name: 'Gán BOT trực nhóm' });
    const warn = await within(dlg).findByTestId('group-bot-silent-warning');
    expect(warn).toHaveTextContent('BOT sẽ không trả lời trong nhóm Lắng nghe im lặng');
    expect(warn).toHaveTextContent('Nghe hết, chỉ trả lời khi được tag');
    await user.click(within(dlg).getByText('Chưa gán'));
    expect(within(dlg).queryByTestId('group-bot-silent-warning')).toBeNull();
    await user.click(within(dlg).getByText('Admin hậu cần'));
    expect(within(dlg).getByTestId('group-bot-silent-warning')).toBeInTheDocument();
  });

  it('nhóm «Nghe hết, chỉ trả lời khi được tag»: không cảnh báo', async () => {
    const me = meWith(DIR_WRITER);
    mockFetch((c) => {
      if (c.url.includes('/auth/me')) return json(200, me);
      if (c.url.includes('/pickers/agents')) return json(200, AGENTS);
      if (c.url.includes('/directory/channels')) return json(200, CHANNELS);
      if (c.url.includes('/directory/groups')) return json(200, GROUPS);
      return json(404);
    });
    const user = userEvent.setup();
    renderScreen(<DirectoryScreen />);
    const row = (await screen.findByText('GRP-ZL-0114')).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /Đổi|Gán/ }));
    const dlg = await screen.findByRole('dialog', { name: 'Gán BOT trực nhóm' });
    await within(dlg).findByText('Key Account junior (đã tắt)');
    expect(within(dlg).queryByTestId('group-bot-silent-warning')).toBeNull();
  });

  it.each([
    ['có system.manage → chỉ đường màn Danh tính Agent', { 'profile.write': 'all', 'system.manage': 'all' }, EMPTY_AGENTS_TEXT],
    ['không system.manage → nhờ Owner tạo', { 'profile.write': 'team' }, EMPTY_AGENTS_TEXT_ASK],
  ])('không có trợ lý nào đang bật → %s', async (_name, perms, text) => {
    const me = meWith(perms);
    mockFetch((c) => {
      if (c.url.includes('/auth/me')) return json(200, me);
      if (c.url.includes('/pickers/agents')) return json(200, { items: [] });
      if (c.url.includes('/directory/channels')) return json(200, CHANNELS);
      if (c.url.includes('/directory/groups')) return json(200, { ...GROUPS, items: [{ ...GROUPS.items[0], bot: null }] });
      return json(404);
    });
    const user = userEvent.setup();
    renderScreen(<DirectoryScreen />);
    const row = (await screen.findByText('GRP-ZL-0114')).closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: /Đổi|Gán/ }));
    const dlg = await screen.findByRole('dialog', { name: 'Gán BOT trực nhóm' });
    expect(await within(dlg).findByText(text)).toBeInTheDocument();
  });
});

const PEOPLE: DirCursorPage<DirPerson> = {
  items: [
    {
      id: 'p1', code: 'PER-0042', name: 'Nguyễn Văn Bảo', type: 'customer', org_name: null, relation: 'direct',
      channels: ['zalo'], heat: 87, heat_trend: 'up', value_vnd: null, priority: 'P1',
      bot: { id: AGENT_IDS.tls, name: 'Trợ lý thương mại' }, autonomy_level: 3, owner_user_id: null,
    },
    {
      id: 'p2', code: 'PER-0951', name: 'Trịnh Mỹ Duyên', type: 'customer', org_name: null, relation: 'via_staff',
      channels: ['zalo'], heat: 18, heat_trend: null, value_vnd: null, priority: 'P3',
      bot: { id: AGENT_IDS.hc, name: 'Admin hậu cần' }, autonomy_level: null, owner_user_id: null,
    },
  ],
  next_cursor: null,
  total: 2,
};

describe('Nhóm & Con người › Thiết lập BOT cho nhóm đã lọc', () => {
  function mockDir(me: unknown, agents: () => Response) {
    return mockFetch((c) => {
      if (c.url.includes('/auth/me')) return json(200, me);
      if (c.url.includes('/pickers/agents')) return agents();
      if (c.url.includes('/directory/channels')) return json(200, CHANNELS);
      if (c.url.includes('/directory/groups')) return json(200, GROUPS);
      if (c.method === 'POST' && c.url.includes('/directory/people/bulk-bot')) return json(200, { ok: true, updated: 2 });
      if (c.url.includes('/directory/people')) return json(200, PEOPLE);
      return json(404);
    });
  }

  it('mặc định «Giữ nguyên BOT hiện tại»: chỉ đổi tự trị thì KHÔNG gửi agent_id (không gỡ BOT của ai)', async () => {
    const calls = mockDir(meWith(DIR_WRITER, 'owner'), () => json(200, AGENTS));
    useUrlStateStore.setState({ params: { dt: 'people' } });
    const user = userEvent.setup();
    renderScreen(<DirectoryScreen />);
    await screen.findByText('Trịnh Mỹ Duyên');
    await user.click(screen.getByRole('button', { name: 'Thiết lập BOT cho nhóm đã lọc' }));
    const dlg = await screen.findByRole('dialog', { name: 'Thiết lập BOT cho nhóm đã lọc' });
    expect(within(dlg).getByText('Giữ nguyên BOT hiện tại').closest('button')).toHaveAttribute('aria-pressed', 'true');
    const apply = within(dlg).getByRole('button', { name: /Áp dụng cho 2 người/ });
    expect(apply).toBeDisabled(); // chưa chọn gì để đổi
    await user.click(within(within(dlg).getByRole('group', { name: 'Mức tự trị' })).getByRole('button', { name: 'Soạn sẵn chờ duyệt' }));
    expect(apply).toBeEnabled();
    await user.click(apply);
    // F053: MỘT lần gọi gửi bộ lọc + số đã hiện cho máy chủ (không còn lặp từng người ở trình duyệt).
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST' && c.url.includes('/bulk-bot')).length).toBe(1));
    expect(calls.find((x) => x.method === 'POST' && x.url.includes('/bulk-bot'))?.body).toEqual({ filter: {}, expected_total: 2, autonomy_level: 4 });
  });

  it('/pickers/agents lỗi → vẫn mặc định giữ nguyên, nút Áp dụng tắt cho tới khi chọn mức tự trị', async () => {
    mockDir(meWith(DIR_WRITER, 'owner'), () => problem(503, 'DB_UNAVAILABLE', 'Cơ sở dữ liệu đang không phản hồi'));
    useUrlStateStore.setState({ params: { dt: 'people' } });
    const user = userEvent.setup();
    renderScreen(<DirectoryScreen />);
    await screen.findByText('Trịnh Mỹ Duyên');
    await user.click(screen.getByRole('button', { name: 'Thiết lập BOT cho nhóm đã lọc' }));
    const dlg = await screen.findByRole('dialog', { name: 'Thiết lập BOT cho nhóm đã lọc' });
    await within(dlg).findByRole('alert', {}, { timeout: 8000 });
    expect(within(dlg).getByText('Giữ nguyên BOT hiện tại').closest('button')).toHaveAttribute('aria-pressed', 'true');
    expect(within(dlg).getByRole('button', { name: /Áp dụng cho 2 người/ })).toBeDisabled();
  });

  it('gỡ BOT chủ động → gửi agent_id: null', async () => {
    const calls = mockDir(meWith(DIR_WRITER, 'owner'), () => json(200, AGENTS));
    useUrlStateStore.setState({ params: { dt: 'people' } });
    const user = userEvent.setup();
    renderScreen(<DirectoryScreen />);
    await screen.findByText('Trịnh Mỹ Duyên');
    await user.click(screen.getByRole('button', { name: 'Thiết lập BOT cho nhóm đã lọc' }));
    const dlg = await screen.findByRole('dialog', { name: 'Thiết lập BOT cho nhóm đã lọc' });
    await user.click(within(dlg).getByText('Chưa gán (gỡ BOT)'));
    await user.click(within(dlg).getByRole('button', { name: /Áp dụng cho 2 người/ }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST' && c.url.includes('/bulk-bot')).length).toBe(1));
    expect(calls.find((x) => x.method === 'POST' && x.url.includes('/bulk-bot'))?.body).toEqual({ filter: {}, expected_total: 2, agent_id: null });
  });

  it('vai trò chỉ đọc (Auditor: không profile.write) → không có nút "Đổi" và nút thiết lập hàng loạt', async () => {
    mockDir(meWith({ 'profile.read': 'all', 'profile.write': 'none' }), () => json(200, AGENTS));
    useUrlStateStore.setState({ params: { dt: 'people' } });
    renderScreen(<DirectoryScreen />);
    const row = (await screen.findByText('Trịnh Mỹ Duyên')).closest('tr') as HTMLElement;
    expect(within(row).queryByRole('button', { name: 'Đổi' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Thiết lập BOT cho nhóm đã lọc' })).toBeNull();
  });

  it('Vận hành có profile.write nhưng KHÔNG phải Owner → không thấy nút gán BOT cho người (máy chủ chỉ cho Owner + PIN)', async () => {
    mockDir(meWith(DIR_WRITER, 'operator'), () => json(200, AGENTS));
    useUrlStateStore.setState({ params: { dt: 'people' } });
    renderScreen(<DirectoryScreen />);
    const row = (await screen.findByText('Trịnh Mỹ Duyên')).closest('tr') as HTMLElement;
    expect(within(row).queryByRole('button', { name: 'Đổi' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Thiết lập BOT cho nhóm đã lọc' })).toBeNull();
  });

  it('Auditor ở tab Nhóm cũng không thấy "Đổi" BOT trực nhóm', async () => {
    mockDir(meWith({ 'profile.read': 'all', 'profile.write': 'none' }), () => json(200, AGENTS));
    renderScreen(<DirectoryScreen />);
    const row = (await screen.findByText('GRP-ZL-0114')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Key Account junior')).toBeInTheDocument();
    expect(within(row).queryByRole('button', { name: /Đổi/ })).toBeNull();
  });
});
