/**
 * v0.1.43 (F-30) — thang tự trị 3 mức ở giao diện (backend giữ 0–6). "Chỉ hiển thị không bao giờ ghi": mọi control
 * tự trị chỉ gửi autonomy_level khi Sếp chọn một mức KHÁC mức đang lưu; mở/đóng dialog hay <details> không ghi.
 */
import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import {
  AUTONOMY_AUTO_LABEL,
  AUTONOMY_CHOICES,
  autonomyChoice,
  autonomyLegacyHint,
  autonomyPatch,
  type AgentIdentity,
  type DirCursorPage,
  type DirPerson,
} from '@gen-harness/contracts';
import { AutonomySelect } from '../../src/screens/AutonomySelect';
import { AgentsScreen } from '../../src/screens/agents/AgentsScreen';
import { DirectoryScreen } from '../../src/screens/relations/DirectoryScreen';
import { qk } from '../../src/lib/queries';
import { useUrlStateStore } from '../../src/lib/uiStore';
import { AGENT_IDS } from '../mock-ids';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

interface Call {
  url: string;
  method: string;
  body: Record<string, unknown> | undefined;
}

function mockFetch(handler: (c: Call) => Response) {
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

const ME = {
  id: 'u', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A (Chủ)',
  role: { code: 'owner', name: 'Owner — Sếp' },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
  permissions: { 'system.read': 'all', 'system.manage': 'all', 'profile.read': 'all', 'profile.write': 'all' },
};

function renderScreen(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, ME);
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  useUrlStateStore.setState({ params: {} });
});

const pressed = (group: HTMLElement, name: string) => within(group).getByRole('button', { name }).getAttribute('aria-pressed');

describe('autonomyChoice / autonomyPatch', () => {
  it('0..6 → observe ×3, suggest, draft, auto ×2; ngoài thang → null', () => {
    expect([0, 1, 2, 3, 4, 5, 6].map((n) => autonomyChoice(n)?.key)).toEqual(['observe', 'observe', 'observe', 'suggest', 'draft', 'auto', 'auto']);
    expect(autonomyChoice(5)?.label).toBe(AUTONOMY_AUTO_LABEL);
    expect(autonomyChoice(6)?.label).not.toBe('Soạn sẵn chờ duyệt');
    expect(autonomyChoice(null)).toBeNull();
    expect(autonomyChoice(7)).toBeNull();
    expect(autonomyChoice(-1)).toBeNull();
    expect(AUTONOMY_CHOICES.map((c) => [c.label, c.level])).toEqual([
      ['Chỉ ghi nhận', 0],
      ['Gợi ý', 3],
      ['Soạn sẵn chờ duyệt', 4],
    ]);
  });

  it('autonomyPatch chỉ có autonomy_level khi chọn mức khác', () => {
    expect(autonomyPatch(4, null)).toEqual({});
    expect(autonomyPatch(4, 4)).toEqual({});
    expect(autonomyPatch(5, 4)).toEqual({ autonomy_level: 4 });
    expect(autonomyPatch(null, 0)).toEqual({ autonomy_level: 0 });
  });
});

describe('AutonomySelect', () => {
  it('current=2, bấm "Soạn sẵn chờ duyệt" → onChange(4); current=1/2 bấm "Chỉ ghi nhận" → onChange(0) (hạ thật về 0)', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { unmount } = render(<AutonomySelect current={2} value={null} onChange={onChange} />);
    const group = screen.getByRole('group', { name: 'Mức tự trị' });
    expect(pressed(group, 'Chỉ ghi nhận')).toBe('true');
    // Mức 2 không hứa "chỉ đọc": nói đúng mức thật và việc vẫn gọi được công cụ.
    expect(screen.getByText(/Đang ở mức 2 · Chấm điểm \+ giải thích — vẫn gọi được công cụ/)).toBeInTheDocument();
    expect(screen.queryByText(/chỉ đọc/i)).toBeNull();
    await user.click(within(group).getByRole('button', { name: 'Chỉ ghi nhận' }));
    expect(onChange).toHaveBeenLastCalledWith(0);
    await user.click(within(group).getByRole('button', { name: 'Soạn sẵn chờ duyệt' }));
    expect(onChange).toHaveBeenLastCalledWith(4);
    unmount();

    const onChange2 = vi.fn();
    const r2 = render(<AutonomySelect current={1} value={null} onChange={onChange2} />);
    await user.click(within(screen.getByRole('group', { name: 'Mức tự trị' })).getByRole('button', { name: 'Chỉ ghi nhận' }));
    expect(onChange2).toHaveBeenCalledTimes(1);
    expect(onChange2).toHaveBeenLastCalledWith(0);
    r2.unmount();

    // Đúng mức 0 đang lưu → giữ nguyên; gợi ý mức 0 mới được nói "chỉ đọc".
    const onChange3 = vi.fn();
    render(<AutonomySelect current={0} value={null} onChange={onChange3} />);
    expect(screen.getByText(/Mức 0: chỉ đọc và ghi lại/)).toBeInTheDocument();
    await user.click(within(screen.getByRole('group', { name: 'Mức tự trị' })).getByRole('button', { name: 'Chỉ ghi nhận' }));
    expect(onChange3).toHaveBeenLastCalledWith(null);
  });

  it('autonomyLegacyHint chỉ cho mức 1/2', () => {
    expect(autonomyLegacyHint(0)).toBeNull();
    expect(autonomyLegacyHint(3)).toBeNull();
    expect(autonomyLegacyHint(null)).toBeNull();
    expect(autonomyLegacyHint(1)).not.toMatch(/gọi được công cụ/);
    expect(autonomyLegacyHint(2)).toMatch(/vẫn gọi được công cụ/);
  });

  it('current=5: viên "Tự làm (đặt ở Nâng cao)" đang nhấn, không bấm được; "Soạn sẵn chờ duyệt" KHÔNG nhấn', () => {
    render(<AutonomySelect current={5} value={null} onChange={() => undefined} />);
    const group = screen.getByRole('group', { name: 'Mức tự trị' });
    const auto = within(group).getByRole('button', { name: AUTONOMY_AUTO_LABEL });
    expect(auto).toHaveAttribute('aria-pressed', 'true');
    expect(auto).toBeDisabled();
    expect(pressed(group, 'Soạn sẵn chờ duyệt')).toBe('false');
    expect(within(group).getAllByRole('button').filter((b) => b.getAttribute('aria-pressed') === 'true')).toHaveLength(1);
  });

  it('mở/đóng <details> Nâng cao không gọi onChange; mức 5–6 chỉ đặt trong đó', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { container } = render(<AutonomySelect current={3} value={null} onChange={onChange} />);
    const details = container.querySelector('details.brain-advanced') as HTMLDetailsElement;
    const summary = screen.getByText('Nâng cao — mức 5–6 (tự làm việc nội bộ)');
    await user.click(summary);
    expect(details.open).toBe(true);
    expect(screen.getByText(/Tin gửi ra ngoài vẫn luôn chờ Sếp duyệt/)).toBeInTheDocument();
    await user.click(summary);
    expect(details.open).toBe(false);
    expect(onChange).not.toHaveBeenCalled();

    await user.click(summary);
    await user.click(screen.getByRole('button', { name: 'Mức 6 · Tự làm việc đã whitelist' }));
    expect(onChange).toHaveBeenLastCalledWith(6);
    expect(screen.getByRole('button', { name: 'Mức 5 · Tự làm việc thấp rủi ro' })).toBeInTheDocument();
  });

  it('hộp hàng loạt: nút "Giữ nguyên" → onChange(null), mặc định đang nhấn', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<AutonomySelect current={null} value={null} onChange={onChange} keepLabel="Giữ nguyên" />);
    const group = screen.getByRole('group', { name: 'Mức tự trị' });
    expect(pressed(group, 'Giữ nguyên')).toBe('true');
    expect(pressed(group, 'Chỉ ghi nhận')).toBe('false');
    await user.click(within(group).getByRole('button', { name: 'Gợi ý' }));
    expect(onChange).toHaveBeenLastCalledWith(3);
    await user.click(within(group).getByRole('button', { name: 'Giữ nguyên' }));
    expect(onChange).toHaveBeenLastCalledWith(null);
  });
});

const AGENT: AgentIdentity = {
  id: 'agent-hc', name: 'Admin hậu cần', role_desc: 'Nhắc việc nội bộ.', template: 'admin',
  addressing: {}, voice: 'Ngắn gọn', speak_when: 'Khi có việc quá hạn',
  forbidden: [], autonomy_level: 5, is_enabled: true, limits: {},
  created_at: '2026-08-01T00:00:00Z', updated_at: '2026-09-20T00:00:00Z',
  channel_scopes: [], binding: null,
};

function agentsHandler(c: Call): Response {
  if (c.url.includes('/agents/templates')) return json(200, []);
  if (c.url.includes('/agents/decisions')) return json(200, { items: [], next_cursor: null, total: 0 });
  if (c.url.endsWith('/agents') && c.method === 'GET') return json(200, [AGENT]);
  if (c.url.includes('/channels') && c.method === 'GET') return json(200, []);
  if (c.url.endsWith('/agents/agent-hc') && c.method === 'PATCH') return json(200, { ...AGENT, ...c.body });
  return json(404);
}

describe('Danh tính Agent — sửa không ghi lại mức tự trị', () => {
  it('thẻ agent mức 5 hiện "Tự làm (đặt ở Nâng cao)", không phải "Soạn sẵn chờ duyệt"', async () => {
    mockFetch(agentsHandler);
    renderScreen(<AgentsScreen />);
    const pill = await screen.findByText(AUTONOMY_AUTO_LABEL, { selector: '.ag-autonomy' });
    expect(pill).toHaveAttribute('title', 'Mức 5 — Tự làm việc thấp rủi ro');
  });

  it('mở rồi Huỷ → 0 PATCH; sửa tên rồi Lưu → không có autonomy_level; chọn "Soạn sẵn chờ duyệt" → autonomy_level 4', async () => {
    const calls = mockFetch(agentsHandler);
    const user = userEvent.setup();
    renderScreen(<AgentsScreen />);
    const patches = () => calls.filter((c) => c.method === 'PATCH');

    await user.click(await screen.findByRole('button', { name: 'Sửa agent Admin hậu cần' }));
    let dlg = await screen.findByRole('dialog', { name: 'Sửa Admin hậu cần' });
    // Mức 5 đang lưu: viên Tự làm nhấn, "Soạn sẵn chờ duyệt" không nhấn.
    const group = within(dlg).getByRole('group', { name: 'Mức tự trị' });
    expect(pressed(group, AUTONOMY_AUTO_LABEL)).toBe('true');
    expect(pressed(group, 'Soạn sẵn chờ duyệt')).toBe('false');
    await user.click(within(dlg).getByRole('button', { name: 'Huỷ' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(patches()).toHaveLength(0);

    await user.click(screen.getByRole('button', { name: 'Sửa agent Admin hậu cần' }));
    dlg = await screen.findByRole('dialog', { name: 'Sửa Admin hậu cần' });
    const nameInput = within(dlg).getByLabelText('Tên hiển thị');
    await user.clear(nameInput);
    await user.type(nameInput, 'Admin hậu cần 2');
    await user.click(within(dlg).getByRole('button', { name: 'Lưu thay đổi' }));
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0].body).toMatchObject({ name: 'Admin hậu cần 2' });
    expect(patches()[0].body).not.toHaveProperty('autonomy_level');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    await user.click(screen.getByRole('button', { name: 'Sửa agent Admin hậu cần' }));
    dlg = await screen.findByRole('dialog', { name: 'Sửa Admin hậu cần' });
    await user.click(within(within(dlg).getByRole('group', { name: 'Mức tự trị' })).getByRole('button', { name: 'Soạn sẵn chờ duyệt' }));
    await user.click(within(dlg).getByRole('button', { name: 'Lưu thay đổi' }));
    await waitFor(() => expect(patches()).toHaveLength(2));
    expect(patches()[1].body).toMatchObject({ autonomy_level: 4 });
  });
});

describe('Danh tính Agent — tạo mới', () => {
  const createHandler = (c: Call): Response => {
    if (c.url.endsWith('/agents') && c.method === 'POST') return json(201, { ...AGENT, ...c.body, id: 'agent-new' });
    return agentsHandler(c);
  };
  const openCreate = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(await screen.findByRole('button', { name: 'Tạo agent mới' }));
    const dlg = await screen.findByRole('dialog', { name: 'Tạo agent mới' });
    for (const [label, v] of [['Tên hiển thị', 'Ghi chép'], ['Vai trò', 'Ghi lại'], ['Giọng điệu', 'Ngắn'], ['Khi nào được nói', 'Không bao giờ']]) {
      await user.type(within(dlg).getByLabelText(label), v);
    }
    return dlg;
  };

  it('chọn "Chỉ ghi nhận" → autonomy_level 0 (không phải mặc định); không chọn → mức mặc định chung 4 (v0.1.67, F078)', async () => {
    const calls = mockFetch(createHandler);
    const user = userEvent.setup();
    renderScreen(<AgentsScreen />);
    const posts = () => calls.filter((c) => c.method === 'POST' && c.url.endsWith('/agents'));

    let dlg = await openCreate(user);
    await user.click(within(within(dlg).getByRole('group', { name: 'Mức tự trị' })).getByRole('button', { name: 'Chỉ ghi nhận' }));
    await user.click(within(dlg).getByRole('button', { name: 'Tạo agent' }));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0].body).toMatchObject({ autonomy_level: 0 });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    dlg = await openCreate(user);
    await user.click(within(dlg).getByRole('button', { name: 'Tạo agent' }));
    await waitFor(() => expect(posts()).toHaveLength(2));
    expect(posts()[1].body).toMatchObject({ autonomy_level: 4 });
  });
});

const PEOPLE: DirCursorPage<DirPerson> = {
  items: [
    {
      id: 'p1', code: 'PER-0042', name: 'Nguyễn Văn Bảo', type: 'customer', org_name: 'Công ty in Thành Phát',
      relation: 'direct', channels: ['zalo'], heat: 87, heat_trend: 'up', value_vnd: 84_000_000, priority: 'P1',
      bot: { id: AGENT_IDS.tls, name: 'Trợ lý thương mại' }, autonomy_level: 1, owner_user_id: null,
    },
    {
      id: 'p2', code: 'PER-0619', name: 'Võ Thanh Sơn', type: 'candidate', org_name: null,
      relation: 'stranger', channels: ['zalo'], heat: 74, heat_trend: null, value_vnd: null, priority: 'P2',
      bot: null, autonomy_level: 6, owner_user_id: null,
    },
  ],
  next_cursor: null,
  total: 2,
};

describe('Nhóm & Con người — PersonBotDialog', () => {
  it('cột Tự trị hiện nhãn 3 mức; người mức 1 bấm "Chỉ ghi nhận" → ghi mức 0 thật', async () => {
    const calls = mockFetch((c) => {
      if (c.url.includes('/pickers/agents')) return json(200, { items: [{ id: AGENT_IDS.tls, name: 'Trợ lý thương mại' }] });
      if (c.url.match(/\/directory\/people\/p1\/bot$/)) return json(200, PEOPLE.items[0]);
      if (c.url.includes('/directory/people')) return json(200, PEOPLE);
      return json(404);
    });
    const user = userEvent.setup();
    useUrlStateStore.setState({ params: { dt: 'people' } });
    renderScreen(<DirectoryScreen />);

    const row = (await screen.findByText('Nguyễn Văn Bảo')).closest('tr')!;
    expect(within(row).getByText('Chỉ ghi nhận')).toBeInTheDocument();
    const row2 = screen.getByText('Võ Thanh Sơn').closest('tr')!;
    expect(within(row2).getByText(AUTONOMY_AUTO_LABEL)).toBeInTheDocument();
    expect(within(row2).queryByText('Soạn sẵn chờ duyệt')).toBeNull();

    await user.click(within(row).getByRole('button', { name: 'Đổi' }));
    const dlg = await screen.findByRole('dialog', { name: 'Thiết lập BOT + tự trị' });
    // Mức 1 hiện trong nhóm "Chỉ ghi nhận"; bấm "Chỉ ghi nhận" hạ thật về 0.
    await user.click(within(within(dlg).getByRole('group', { name: 'Mức tự trị' })).getByRole('button', { name: 'Chỉ ghi nhận' }));
    await user.click(within(dlg).getByRole('button', { name: 'Lưu' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.includes('/directory/people/p1/bot'))).toBe(true));
    const call = calls.find((c) => c.method === 'POST' && c.url.includes('/directory/people/p1/bot'))!;
    expect(call.body).toEqual({ agent_id: AGENT_IDS.tls, autonomy_level: 0 });
  });
});
