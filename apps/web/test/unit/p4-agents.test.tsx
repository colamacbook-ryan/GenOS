import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { AgentDecision, AgentIdentity, AgentTemplate, Channel } from '@gen-harness/contracts';
import { AgentsScreen } from '../../src/screens/agents/AgentsScreen';
import { qk } from '../../src/lib/queries';

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

const ME = {
  id: 'u', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A (Chủ)',
  role: { code: 'owner', name: 'Owner — Sếp' },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
  permissions: { 'system.read': 'all', 'system.manage': 'all' },
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
});

const AGENTS: AgentIdentity[] = [
  {
    id: 'agent-tls', name: 'Trợ lý thương mại', role_desc: 'Theo dõi cơ hội, nhắc việc quá hạn.', template: 'commercial',
    addressing: {}, voice: 'Thân thiện, chuyên nghiệp', speak_when: 'Khi được hỏi trực tiếp',
    forbidden: ['Cam kết giá ngoài bảng giá'], autonomy_level: 4, is_enabled: true, limits: {},
    created_at: '2026-08-01T00:00:00Z', updated_at: '2026-09-20T00:00:00Z',
    channel_scopes: [{ channel_id: 'c1', channel_type: 'zalo', group_id: null, group_name: null }],
    binding: { model_id: 'm1', model_name: 'gemini-2.5-flash', provider_name: 'Gemini API', temperature: 0.4, context_tokens: 32000, rule_codes: ['R-01'] },
    model: { custom: true, ok: true, label: 'gemini-2.5-flash · Gemini API' },
    last_spoke_at: '2026-09-23T02:00:00Z', last_spoke_decision: 'draft',
  },
  {
    id: 'agent-mascot', name: 'Bé Heo', role_desc: 'Mẫu hoài niệm.', template: 'mascot',
    addressing: {}, voice: 'Dí dỏm', speak_when: 'Chỉ khi được gọi trực tiếp',
    forbidden: [], autonomy_level: 0, is_enabled: false, limits: {},
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    channel_scopes: [], binding: null,
    model: { custom: false, ok: false, label: 'Chuẩn: cần khoá API (Google/Claude chưa chạy việc nền)' },
    last_spoke_at: null, last_spoke_decision: null,
  },
];

const TEMPLATES: AgentTemplate[] = [
  { code: 'commercial', name: 'Trợ lý thương mại', role_desc: 'Theo dõi cơ hội.', voice: 'Thân thiện', speak_when: 'Khi được hỏi', forbidden: [], default_enabled: true },
  { code: 'mascot', name: 'Bé Heo', role_desc: 'Mẫu trò chuyện phiếm.', voice: 'Dí dỏm', speak_when: 'Chỉ khi được gọi', forbidden: [], default_enabled: false },
  { code: 'cs', name: 'CSKH', role_desc: 'Trả lời khách.', voice: 'Ấm áp', speak_when: 'Khi khách hỏi', forbidden: [], default_enabled: true },
];

const DECISIONS: { items: AgentDecision[]; next_cursor: null; total: number } = {
  items: [
    {
      id: 'd1', at: '2026-09-24T02:00:00Z', agent: { id: 'agent-tls', name: 'Trợ lý thương mại' }, decision: 'draft',
      rationale: 'Khách hỏi giá lần hai.', trigger: { type: 'meaning_unit', id: 'mu1', code: 'OPP-1842', label: 'Hỏi giá' },
      context_refs: [], draft: { id: 'draft-1', code: 'ACT-0231' },
    },
  ],
  next_cursor: null,
  total: 1,
};

const CHANNELS: Channel[] = [
  { type: 'zalo', name: 'Zalo', installed: true, id: 'c1', state: 'active', account_label: null, started_at: null, groups_listening: 0, outbound_queued: 0, last_heartbeat_at: null, stats: null, qr: null },
];

function baseHandler(c: Call): Response | null {
  if (c.url.includes('/agents/templates')) return json(200, TEMPLATES);
  if (c.url.includes('/agents/decisions')) return json(200, DECISIONS);
  if (c.url.endsWith('/agents') && c.method === 'GET') return json(200, AGENTS);
  if (c.url.includes('/channels') && c.method === 'GET') return json(200, CHANNELS);
  return null;
}

describe('Danh tính Agent', () => {
  it('hiện thẻ agent, panel "đã nói gì" và mẫu có sẵn', async () => {
    mockFetch((c) => baseHandler(c) ?? json(404));
    renderScreen(<AgentsScreen />);

    expect(await screen.findByText('Trợ lý thương mại', { selector: '.ag-card__name' })).toBeInTheDocument();
    expect(screen.getByText('Bé Heo', { selector: '.ag-card__name' })).toBeInTheDocument();
    // v0.1.43 (F-30): viên tự trị hiện nhãn 3 mức; title giữ mức thật trên thang 0–6.
    expect(screen.getByText('Soạn sẵn chờ duyệt', { selector: '.ag-autonomy' })).toHaveAttribute('title', 'Mức 4 — Soạn sẵn chờ duyệt');
    expect(screen.queryByText('tự trị 4')).toBeNull();
    // Bé Heo tắt mặc định (spec E13) — thẻ mờ đi (data-off) chứ không xoá khỏi danh mục.
    const heoCard = screen.getByText('Bé Heo', { selector: '.ag-card__name' }).closest('.ag-card');
    expect(heoCard).toHaveAttribute('data-off');

    expect(await screen.findByText(/Soạn nháp ACT-0231/)).toBeInTheDocument();
    expect(screen.getByText('CSKH', { selector: '.ag-template-row__name' })).toBeInTheDocument();
  });

  it('tắt agent qua switch gọi PATCH /agents/{id}/disable', async () => {
    const calls = mockFetch((c) => {
      if (c.url.includes('/agents/agent-tls/disable') && c.method === 'PATCH') {
        return json(200, { ...AGENTS[0], is_enabled: false });
      }
      return baseHandler(c) ?? json(404);
    });
    renderScreen(<AgentsScreen />);
    await screen.findByText('Trợ lý thương mại', { selector: '.ag-card__name' });
    const user = userEvent.setup();
    await user.click(screen.getByLabelText('Tắt agent Trợ lý thương mại'));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/disable') && (c.body as { enabled: boolean } | undefined)?.enabled === false)).toBe(true));
  });

  it('tạo agent mới gửi POST /agents với dữ liệu đã nhập', async () => {
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/agents') && c.method === 'POST') return json(201, { ...AGENTS[0], id: 'agent-new', name: (c.body as { name: string }).name });
      return baseHandler(c) ?? json(404);
    });
    renderScreen(<AgentsScreen />);
    await screen.findByText('Trợ lý thương mại', { selector: '.ag-card__name' });
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Tạo agent mới' }));
    const dialog = await screen.findByRole('dialog');
    // Tạo agent luôn cần PIN agent.manage — báo trước; không nhắc 'giới hạn' (form không có trường này).
    expect(within(dialog).getByText('Tạo agent cần mã PIN')).toBeInTheDocument();
    expect(within(dialog).queryByText(/giới hạn/)).toBeNull();
    await user.type(within(dialog).getByLabelText('Tên hiển thị'), 'Recruiter mới');
    await user.type(within(dialog).getByLabelText('Vai trò'), 'Sàng lọc ứng viên');
    await user.type(within(dialog).getByLabelText('Giọng điệu'), 'Chuyên nghiệp');
    await user.type(within(dialog).getByLabelText('Khi nào được nói'), 'Khi có hồ sơ mới');
    await user.click(within(dialog).getByRole('button', { name: 'Tạo agent' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/agents'))).toBe(true));
    const created = calls.find((c) => c.method === 'POST' && c.url.endsWith('/agents'));
    expect((created?.body as { name: string }).name).toBe('Recruiter mới');
  });

  it('nhân bản agent gửi POST /agents/{id}/clone với tên mới', async () => {
    const calls = mockFetch((c) => {
      if (c.url.includes('/agents/agent-tls/clone') && c.method === 'POST') {
        return json(201, { ...AGENTS[0], id: 'agent-clone', name: (c.body as { name: string }).name, is_enabled: false });
      }
      return baseHandler(c) ?? json(404);
    });
    renderScreen(<AgentsScreen />);
    await screen.findByText('Trợ lý thương mại', { selector: '.ag-card__name' });
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Nhân bản' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Nhân bản agent cần mã PIN')).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText('Tên agent mới'), 'Trợ lý thương mại (bản sao)');
    await user.click(within(dialog).getByRole('button', { name: 'Nhân bản' }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/clone') && c.method === 'POST')).toBe(true));
    const cloneCall = calls.find((c) => c.url.includes('/clone'));
    expect((cloneCall?.body as { name: string }).name).toBe('Trợ lý thương mại (bản sao)');
  });

  // ─── v0.1.67 (B7) ──────────────────────────────────────────────────────────────────────────────────────────────

  it('F022/F083: thẻ nói agent dùng model nào, cảnh báo khi chưa chạy được, lần lên tiếng theo từng agent', async () => {
    mockFetch((c) => baseHandler(c) ?? json(404));
    renderScreen(<AgentsScreen />);
    const tls = (await screen.findByText('Trợ lý thương mại', { selector: '.ag-card__name' })).closest('.ag-card') as HTMLElement;
    expect(within(tls).getByText('gemini-2.5-flash · Gemini API')).toBeInTheDocument();
    expect(within(tls).getByText(/Soạn nháp · /, { selector: '.ag-card__spoke' })).toBeInTheDocument();
    const heo = screen.getByText('Bé Heo', { selector: '.ag-card__name' }).closest('.ag-card') as HTMLElement;
    const warn = within(heo).getByText(/cần khoá API/);
    expect(warn).toHaveAttribute('data-warn');
    // agent chưa lên tiếng lần nào: câu đúng, không phụ thuộc 8 quyết định chung của mọi agent
    expect(within(heo).getByText('Chưa từng lên tiếng')).toBeInTheDocument();
  });

  it('F089: công tắc lỗi hiện câu thân thiện thay vì im lặng', async () => {
    mockFetch((c) => {
      if (c.url.includes('/agents/agent-mascot/disable')) return json(423, { code: 'PIN_LOCKED', title: 'Mã PIN đang bị khoá, thử lại sau 5 phút', status: 423 });
      return baseHandler(c) ?? json(404);
    });
    renderScreen(<AgentsScreen />);
    await screen.findByText('Bé Heo', { selector: '.ag-card__name' });
    const user = userEvent.setup();
    await user.click(screen.getByLabelText('Bật agent Bé Heo'));
    expect(await screen.findByText(/PIN/)).toBeInTheDocument();
  });

  it('F085: Lưu trữ agent gọi POST /agents/{id}/archive sau khi báo cần PIN', async () => {
    const calls = mockFetch((c) => {
      if (c.url.includes('/agents/agent-mascot/archive') && c.method === 'POST') return json(200, { ...AGENTS[1], archived: true });
      return baseHandler(c) ?? json(404);
    });
    renderScreen(<AgentsScreen />);
    await screen.findByText('Bé Heo', { selector: '.ag-card__name' });
    const user = userEvent.setup();
    await user.click(screen.getByLabelText('Lưu trữ agent Bé Heo'));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Lưu trữ agent cần mã PIN')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Lưu trữ agent' }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/agents/agent-mascot/archive') && c.method === 'POST')).toBe(true));
  });

  it('F078: tạo agent không chọn mức ⇒ gửi mức 4 và ô chọn sáng "Soạn sẵn chờ duyệt"', async () => {
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/agents') && c.method === 'POST') return json(201, { ...AGENTS[0], id: 'agent-new' });
      return baseHandler(c) ?? json(404);
    });
    renderScreen(<AgentsScreen />);
    await screen.findByText('Trợ lý thương mại', { selector: '.ag-card__name' });
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Tạo agent mới' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('button', { name: 'Soạn sẵn chờ duyệt' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(dialog).queryByText(/Chưa đặt mức tự trị riêng/)).toBeNull();
    await user.type(within(dialog).getByLabelText('Tên hiển thị'), 'Agent mới');
    await user.type(within(dialog).getByLabelText('Vai trò'), 'Việc A');
    await user.type(within(dialog).getByLabelText('Giọng điệu'), 'Nhẹ nhàng');
    await user.type(within(dialog).getByLabelText('Khi nào được nói'), 'Khi cần');
    await user.click(within(dialog).getByRole('button', { name: 'Tạo agent' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/agents'))).toBe(true));
    const body = calls.find((c) => c.method === 'POST' && c.url.endsWith('/agents'))?.body as { autonomy_level: number; is_enabled: boolean };
    expect(body.autonomy_level).toBe(4);
    expect(body.is_enabled).toBe(true);
  });

  it('F082: Dùng mẫu tắt sẵn (Bé Heo) tạo agent TẮT và nói rõ trong hộp tạo', async () => {
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/agents') && c.method === 'POST') return json(201, { ...AGENTS[1], id: 'agent-new' });
      return baseHandler(c) ?? json(404);
    });
    renderScreen(<AgentsScreen />);
    const row = (await screen.findByText('Bé Heo', { selector: '.ag-template-row__name' })).closest('.ag-template-row') as HTMLElement;
    const user = userEvent.setup();
    await user.click(within(row).getByRole('button', { name: 'Dùng mẫu' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/tạo ra agent đang TẮT/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Tạo agent' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/agents'))).toBe(true));
    const body = calls.find((c) => c.method === 'POST' && c.url.endsWith('/agents'))?.body as { is_enabled: boolean; template: string };
    expect(body.template).toBe('mascot');
    expect(body.is_enabled).toBe(false);
  });
});
