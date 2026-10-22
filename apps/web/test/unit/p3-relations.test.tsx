import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { DirChannel, DirCursorPage, DirGroup, DirPerson, DocumentItem, NbNotebook, NbSubject, Profile } from '@gen-harness/contracts';
import { DirectoryScreen } from '../../src/screens/relations/DirectoryScreen';
import { DocumentsScreen } from '../../src/screens/relations/DocumentsScreen';
import { NotebookScreen } from '../../src/screens/relations/NotebookScreen';
import { ProfileScreen } from '../../src/screens/relations/ProfileScreen';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';
import { useUrlStateStore } from '../../src/lib/uiStore';
import { AGENT_IDS } from '../mock-ids';

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

function renderScreen(ui: ReactElement, initialEntries = ['/']) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

/**
 * `useUrlState` đọc từ `useUrlStateStore`, chỉ được đồng bộ từ URL bởi
 * `UrlStateSync` (mount trong router thật, không mount ở test đơn vị này) —
 * set thẳng store để mô phỏng một đường link sâu (`?id=p1`, `?dt=people`).
 */
function seedUrl(params: Record<string, string>) {
  useUrlStateStore.setState({ params });
}

afterEach(() => {
  vi.unstubAllGlobals();
  queryClient.clear();
  useUrlStateStore.setState({ params: {} });
});

// ─── Nhóm & Con người ────────────────────────────────────────────────────────
const CHANNELS: DirChannel[] = [{ id: 'ch-zalo', type: 'zalo', name: 'Zalo', state: 'active', group_count: 1, events_24h: 412 }];
const GROUPS: DirCursorPage<DirGroup> = {
  items: [
    {
      id: 'g1', code: 'GRP-ZL-0114', name: 'Vận hành Genesis — Quý 4', kind: 'internal', listen_mode: 'tagged_only',
      member_count: 24, events_24h: 412, heat: 87, channel: { type: 'zalo', name: 'Zalo' }, bot: { id: AGENT_IDS.tls, name: 'Trợ lý thương mại' },
      created_at: '2026-01-01T00:00:00Z',
    },
  ],
  next_cursor: null,
  total: 1,
};
const PEOPLE: DirCursorPage<DirPerson> = {
  items: [
    {
      id: 'p1', code: 'PER-0042', name: 'Nguyễn Văn Bảo', type: 'customer', org_name: 'Công ty in Thành Phát',
      relation: 'direct', channels: ['zalo'], heat: 87, heat_trend: 'up', value_vnd: 84_000_000, priority: 'P1',
      bot: { id: AGENT_IDS.tls, name: 'Trợ lý thương mại' }, autonomy_level: 3, owner_user_id: null,
    },
    {
      id: 'p2', code: 'PER-0951', name: 'Trịnh Mỹ Duyên', type: 'customer', org_name: 'Bao bì Sài Gòn Mới',
      relation: 'via_staff', channels: ['zalo'], heat: 18, heat_trend: null, value_vnd: 510_000_000, priority: 'P3',
      bot: null, autonomy_level: null, owner_user_id: null,
    },
  ],
  next_cursor: null,
  total: 2,
};

describe('Nhóm & Con người', () => {
  it('hiện bảng nhóm theo kênh, và lọc người theo độ nhiệt qua URL', async () => {
    const calls = mockFetch((c) => {
      if (c.url.includes('/directory/channels')) return json(200, CHANNELS);
      if (c.url.includes('/directory/groups')) return json(200, GROUPS);
      if (c.url.includes('/directory/people')) {
        const heat = new URL(c.url, 'http://x').searchParams.get('heat');
        const items = heat === 'cold' ? PEOPLE.items.filter((p) => p.heat! < 50) : PEOPLE.items;
        return json(200, { items, next_cursor: null, total: items.length });
      }
      return json(404);
    });
    const user = userEvent.setup();
    renderScreen(<DirectoryScreen />);

    // v0.1.42: tiêu đề màn trùng tên mục thanh bên / breadcrumb.
    expect(screen.getByRole('heading', { level: 2, name: 'Khách & Nhóm' })).toBeInTheDocument();
    expect(await screen.findByText('Vận hành Genesis — Quý 4')).toBeInTheDocument();
    expect(screen.getByText('GRP-ZL-0114')).toBeInTheDocument();
    expect(screen.getByText('Trợ lý thương mại')).toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: /Con người/ }));
    expect(await screen.findByText('Nguyễn Văn Bảo')).toBeInTheDocument();
    expect(screen.getByText('Trịnh Mỹ Duyên')).toBeInTheDocument();

    const heatGroup = screen.getByRole('group', { name: 'Độ nhiệt' });
    await user.click(within(heatGroup).getByRole('button', { name: 'Lạnh' }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('heat=cold'))).toBe(true));
    expect(await screen.findByText('Trịnh Mỹ Duyên')).toBeInTheDocument();
    expect(screen.queryByText('Nguyễn Văn Bảo')).not.toBeInTheDocument();
  });

  it('gán BOT cho một người qua hộp thoại', async () => {
    // Nút "Đổi" (BOT cho người) chỉ hiện với Owner có quyền profile.write (review v0.1.65 P2-3).
    queryClient.setQueryData(qk.me, { id: 'u', role: { code: 'owner', name: 'Owner' }, permissions: { 'profile.read': 'all', 'profile.write': 'all' } });
    const calls = mockFetch((c) => {
      if (c.url.includes('/directory/channels')) return json(200, CHANNELS);
      if (c.url.includes('/directory/groups')) return json(200, GROUPS);
      if (c.url.includes('/pickers/agents')) {
        return json(200, { items: [{ id: AGENT_IDS.tls, name: 'Trợ lý thương mại' }, { id: AGENT_IDS.ka, name: 'Key Account junior' }] });
      }
      if (c.url.match(/\/directory\/people\/p2\/bot$/)) return json(200, { ...PEOPLE.items[1], bot: { id: AGENT_IDS.ka, name: 'Key Account junior' } });
      if (c.url.includes('/directory/people')) return json(200, PEOPLE);
      return json(404);
    });
    const user = userEvent.setup();
    seedUrl({ dt: 'people' });
    renderScreen(<DirectoryScreen />);

    const row = (await screen.findByText('Trịnh Mỹ Duyên')).closest('tr')!;
    await user.click(within(row).getByRole('button', { name: 'Đổi' }));
    const dlg = await screen.findByRole('dialog', { name: 'Thiết lập BOT + tự trị' });
    // v0.1.65 (B8, F080): gán BOT cho một người có tác dụng thật với tin nhắn riêng của người đó — hộp nói rõ điều này.
    expect(within(dlg).getByTestId('person-bot-hint')).toHaveTextContent(/phụ trách tin nhắn riêng \(1-1\) của người này/);
    expect(within(dlg).getByTestId('person-bot-hint')).toHaveTextContent(/kể cả khi kênh đang ở chế độ “Lưu im lặng”/);
    await user.click(within(dlg).getByText('Key Account junior'));
    await user.click(within(dlg).getByRole('button', { name: 'Lưu' }));

    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.includes('/directory/people/p2/bot'))).toBe(true));
    const call = calls.find((c) => c.method === 'POST' && c.url.includes('/directory/people/p2/bot'))!;
    expect(call.body).toMatchObject({ agent_id: AGENT_IDS.ka });
  });
});

// ─── Hồ sơ sống ───────────────────────────────────────────────────────────────
const PROFILE: Profile = {
  person: { id: 'p1', code: 'PER-0042', name: 'Nguyễn Văn Bảo', type: 'customer', org_name: 'Công ty in Thành Phát', title: null, relation_to_owner: 'direct', owner: null },
  autonomy_level: 3, bot: { id: AGENT_IDS.tls, name: 'Trợ lý thương mại' }, owner_note: 'Thích nói chuyện thẳng.',
  identities: [{ id: 'id1', channel: { type: 'zalo', name: 'Zalo' }, external_id: 'zl-1', handle: 'Nguyễn Văn Bảo', phone_e164: null, first_seen_at: '2025-01-01T00:00:00Z' }],
  scores: [
    { dimension: 'heat', label: 'Độ nóng', value: 87, trend: 'up', updated_at: '2026-09-24T01:00:00Z' },
    { dimension: 'churn_risk', label: 'Rủi ro churn', value: 81, trend: 'up', updated_at: '2026-09-24T01:00:00Z' },
  ],
  summary: [{ text: 'Khách in ấn đã mua ba lần trong 18 tháng.', tone: 'ok', evidence: { type: 'meaning_unit', id: 'mu-1' } }],
  timeline: [{ id: 'tl1', event_type: 'Complained', conclusion: 'Nhắc lần thứ ba, giọng gay gắt', confidence: 0.93, observed_at: '2026-09-24T00:00:00Z', group: null, evidence: { type: 'meaning_unit', id: 'mu-1' } }],
  documents: [], touchpoints: [], merge_history: [],
};

describe('Hồ sơ sống', () => {
  it('hiện 5 điểm, tóm tắt và dòng sự kiện từ GET /profile/{id}', async () => {
    mockFetch((c) => (c.url.includes('/profile/p1') ? json(200, PROFILE) : json(404)));
    seedUrl({ id: 'p1' });
    renderScreen(<ProfileScreen />);
    expect(await screen.findByText('Nguyễn Văn Bảo')).toBeInTheDocument();
    expect(screen.getByText('87')).toBeInTheDocument();
    expect(screen.getByText(/mua ba lần trong 18 tháng/)).toBeInTheDocument();
    expect(screen.getByText('Phàn nàn')).toBeInTheDocument();
    expect(screen.getByText(/Thích nói chuyện thẳng/)).toBeInTheDocument();
  });

  it('vai trò chỉ đọc (không có profile.write) → không có nút "Đổi mức tự trị"', async () => {
    queryClient.setQueryData(qk.me, { id: 'u', permissions: { 'profile.read': 'all' } });
    mockFetch((c) => (c.url.includes('/profile/p1') ? json(200, PROFILE) : json(404)));
    seedUrl({ id: 'p1' });
    renderScreen(<ProfileScreen />);
    expect(await screen.findByText('Đang đặt: Gợi ý.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Đổi mức tự trị' })).toBeNull();
  });

  it('v0.1.43 (F-30): mức tự trị 3 mức; dialog không đổi gì → Lưu tắt, không PATCH; chọn mức khác → PATCH đúng mức', async () => {
    queryClient.setQueryData(qk.me, { id: 'u', role: { code: 'owner', name: 'Owner' }, permissions: { 'profile.read': 'all', 'profile.write': 'all' } });
    const calls = mockFetch((c) => {
      if (c.method === 'PATCH' && c.url.includes('/profile/p1')) return json(200, { ...PROFILE, autonomy_level: 4 });
      return c.url.includes('/profile/p1') ? json(200, PROFILE) : json(404);
    });
    const user = userEvent.setup();
    seedUrl({ id: 'p1' });
    renderScreen(<ProfileScreen />);
    expect(await screen.findByText('Đang đặt: Gợi ý.')).toBeInTheDocument();
    expect(screen.queryByText(/Thang 0–6/)).toBeNull();

    await user.click(screen.getByRole('button', { name: 'Đổi mức tự trị' }));
    let dlg = await screen.findByRole('dialog', { name: 'Mức tự trị với đối tượng này' });
    const group = within(dlg).getByRole('group', { name: 'Mức tự trị' });
    expect(within(group).getByRole('button', { name: 'Gợi ý' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(dlg).getByRole('button', { name: 'Lưu' })).toBeDisabled();
    await user.click(within(dlg).getByRole('button', { name: 'Huỷ' }));
    expect(calls.filter((c) => c.method === 'PATCH')).toHaveLength(0);

    await user.click(screen.getByRole('button', { name: 'Đổi mức tự trị' }));
    dlg = await screen.findByRole('dialog', { name: 'Mức tự trị với đối tượng này' });
    await user.click(within(within(dlg).getByRole('group', { name: 'Mức tự trị' })).getByRole('button', { name: 'Soạn sẵn chờ duyệt' }));
    await user.click(within(dlg).getByRole('button', { name: 'Lưu' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    expect(calls.find((c) => c.method === 'PATCH')!.body).toEqual({ autonomy_level: 4 });
  });

  it('v0.1.43 (F-62): chưa có tóm tắt → câu "tin nhắn có nội dung"', async () => {
    mockFetch((c) => (c.url.includes('/profile/p1') ? json(200, { ...PROFILE, summary: [] }) : json(404)));
    seedUrl({ id: 'p1' });
    renderScreen(<ProfileScreen />);
    expect(await screen.findByText('Cần thêm tin nhắn có nội dung để hệ thống tóm tắt.')).toBeInTheDocument();
  });
});

// ─── Sổ tay nhận thức ────────────────────────────────────────────────────────
const NB_SUBJECTS: NbSubject[] = [{ id: 'p1', code: 'PER-0042', name: 'Nguyễn Văn Bảo', entries: 3, token_used: 1842, token_budget: 4000, updated_at: '2026-09-24T01:00:00Z' }];
function notebookOf(pinned: boolean): NbNotebook {
  return {
    subject: { type: 'person', id: 'p1', code: 'PER-0042', name: 'Nguyễn Văn Bảo' },
    token_used: 1842, token_budget: 4000, compaction_no: 14, last_compacted_at: '2026-09-24T01:00:00Z',
    sections: [
      { key: 'attention_now', title: 'Điều cần chú ý ngay', entries: [{ id: 'e1', body: 'Đã nhắc ba lần chưa được trả lời.', refs: [], pinned, editable: false, author: { type: 'agent', label: 'agent' }, created_at: '2026-09-24T01:00:00Z' }] },
      { key: 'rolling_context', title: 'Ngữ cảnh ngắn lũy tiến', entries: [] },
      { key: 'guardrails', title: 'Giới hạn cho agent', entries: [{ id: 'e2', body: 'Không để agent junior follow khách này.', refs: [], pinned: true, editable: true, author: { type: 'user', label: 'Anh Nguyễn Văn A' }, created_at: '2026-09-24T01:00:00Z' }] },
      { key: 'preferences', title: 'Sở thích', entries: [] },
      { key: 'open_threads', title: 'Việc dở', entries: [] },
    ],
    refs: [{ type: 'draft', id: 'draft-1', code: 'ACT-0231', label: 'Bản nháp báo giá' }],
  };
}

describe('Sổ tay nhận thức', () => {
  it('chọn chủ thể → hiện 5 mục; ghim một mục do agent ghi gọi PATCH pinned', async () => {
    let pinned = false;
    const calls = mockFetch((c) => {
      if (c.url.includes('/notebook/subjects')) return json(200, { items: NB_SUBJECTS, next_cursor: null });
      if (c.method === 'PATCH' && c.url.includes('/notebook/person/p1/entries/e1')) {
        pinned = true;
        return json(200, { id: 'e1' });
      }
      if (c.url.includes('/notebook/person/p1/history')) return json(200, []);
      if (c.url.includes('/notebook/person/p1/dropped')) return json(200, { items: [], next_cursor: null });
      if (c.url.includes('/notebook/person/p1')) return json(200, notebookOf(pinned));
      return json(404);
    });
    const user = userEvent.setup();
    renderScreen(<NotebookScreen />);

    expect(await screen.findByText('Nguyễn Văn Bảo')).toBeInTheDocument();
    expect(await screen.findByText('Đã nhắc ba lần chưa được trả lời.')).toBeInTheDocument();
    expect(screen.getByText('Không để agent junior follow khách này.')).toBeInTheDocument();
    // Mục do agent ghi (editable:false) không có nút xoá, chỉ có ghim.
    const agentLine = screen.getByText('Đã nhắc ba lần chưa được trả lời.').closest('.nb-line') as HTMLElement;
    expect(within(agentLine).queryByLabelText('Xoá mục này')).not.toBeInTheDocument();
    const pinBtn = within(agentLine).getByLabelText('Ghim mục này');
    await user.click(pinBtn);
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH' && c.url.includes('/entries/e1'))).toBe(true));
    const call = calls.find((c) => c.method === 'PATCH' && c.url.includes('/entries/e1'))!;
    expect(call.body).toEqual({ pinned: true });
  });

  it('nén ngay gọi POST compact', async () => {
    const calls = mockFetch((c) => {
      if (c.url.includes('/notebook/subjects')) return json(200, { items: NB_SUBJECTS, next_cursor: null });
      if (c.method === 'POST' && c.url.includes('/notebook/person/p1/compact')) return json(200, notebookOf(false));
      if (c.url.includes('/notebook/person/p1/history')) return json(200, []);
      if (c.url.includes('/notebook/person/p1/dropped')) return json(200, { items: [], next_cursor: null });
      if (c.url.includes('/notebook/person/p1')) return json(200, notebookOf(false));
      return json(404);
    });
    const user = userEvent.setup();
    renderScreen(<NotebookScreen />);
    await screen.findByText('Nguyễn Văn Bảo');
    await user.click(screen.getByRole('button', { name: 'Nén ngay' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.includes('/compact'))).toBe(true));
  });
});

// ─── Tài liệu ─────────────────────────────────────────────────────────────────
const DOCS: DirCursorPage<DocumentItem> = {
  items: [
    {
      id: 'doc-1', title: 'BaoGia_ThanhPhat_Q4.docx', description: 'Báo giá quý 4', mime: 'application/pdf', bytes: 84_200,
      owner: { id: 'p1', code: 'PER-0042', name: 'Nguyễn Văn Bảo', type: 'customer', org_name: null }, source: 'agent',
      created_by: 'Agent Trợ lý thương mại', created_at: '2026-09-20T00:00:00Z', updated_at: '2026-09-20T00:00:00Z',
    },
  ],
  next_cursor: null,
  total: 1,
};

describe('Tài liệu', () => {
  it('hiện danh sách và liên kết tải xuống trỏ đúng /documents/{id}/content', async () => {
    mockFetch((c) => (c.url.includes('/documents') ? json(200, DOCS) : json(404)));
    renderScreen(<DocumentsScreen />);
    expect(await screen.findByText('BaoGia_ThanhPhat_Q4.docx')).toBeInTheDocument();
    const link = screen.getByRole('link', { name: /Tải xuống/ });
    expect(link).toHaveAttribute('href', '/api/v1/documents/doc-1/content');
  });

  it('xem chi tiết mở hộp thoại ACL', async () => {
    const detail = { ...DOCS.items[0], acl: [{ principal: 'role:owner', can_read: true, can_write: true }, { principal: 'role:manager', can_read: true, can_write: false }] };
    mockFetch((c) => {
      if (c.url.includes('/documents/doc-1') && !c.url.includes('content')) return json(200, detail);
      if (c.url.includes('/documents')) return json(200, DOCS);
      return json(404);
    });
    const user = userEvent.setup();
    renderScreen(<DocumentsScreen />);
    await user.click(await screen.findByText('BaoGia_ThanhPhat_Q4.docx'));
    const dlg = await screen.findByRole('dialog');
    expect(within(dlg).getByText('role:owner')).toBeInTheDocument();
    expect(within(dlg).getByText('role:manager')).toBeInTheDocument();
  });
});
