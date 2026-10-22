import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { BindingsPage, McpCall, McpServer, McpTool } from '@gen-harness/contracts';
import { McpScreen } from '../../src/screens/mcp/McpScreen';
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

const SERVERS: McpServer[] = [
  { id: 'srv-erp', name: 'ERP Genesis', transport: 'stdio', endpoint: 'mcp://localhost:7011', has_auth: true, is_enabled: true, health: 'healthy', note: 'Chỉ đọc tồn kho.', allow_public_network: false, tool_count: 2, exposed_count: 1 },
];

const TOOLS: McpTool[] = [
  { id: 'tool-read', server_id: 'srv-erp', server_name: 'ERP Genesis', name: 'inventory.check', access: 'read', is_exposed: true, schema: {}, grants: ['agent:agent-tls'] },
  { id: 'tool-write', server_id: 'srv-erp', server_name: 'ERP Genesis', name: 'order.createDraft', access: 'write', is_exposed: false, schema: {}, grants: [] },
];

const CALLS: { items: McpCall[]; next_cursor: null } = {
  items: [
    { id: 'call-1', at: '2026-09-25T02:00:00Z', tool_id: 'tool-read', tool_name: 'inventory.check', access: 'read', server_name: 'ERP Genesis', agent_key: 'agent:agent-tls', args: {}, result_summary: 'còn 6 container', latency_ms: 412, outcome: 'ok', draft_id: null },
  ],
  next_cursor: null,
};

const BINDINGS: BindingsPage = {
  items: [{ agent_key: 'agent:agent-tls', label: 'Trợ lý thương mại', binding: null }],
  models: [],
};

function baseHandler(c: Call): Response | null {
  if (c.url.includes('/mcp/servers') && c.method === 'GET') return json(200, SERVERS);
  if (c.url.includes('/mcp/tools') && c.method === 'GET') return json(200, TOOLS);
  if (c.url.includes('/mcp/calls') && c.method === 'GET') return json(200, CALLS);
  if (c.url.includes('/agents/bindings')) return json(200, BINDINGS);
  return null;
}

describe('MCP Hub', () => {
  it('hiện máy chủ, tool (mở/đóng), rào chắn khoá cứng và nhật ký gọi', async () => {
    mockFetch((c) => baseHandler(c) ?? json(404));
    renderScreen(<McpScreen />);

    expect(await screen.findByText('ERP Genesis', { selector: '.mcp-server__name' })).toBeInTheDocument();
    expect(screen.getByText('Rào chắn khoá cứng')).toBeInTheDocument();
    expect(screen.getByText('Tool có ghi phải qua Bàn làm việc trước khi thực thi')).toBeInTheDocument();
    const writeRow = screen.getByText('order.createDraft', { selector: '.mcp-tool-table__name' }).closest('tr') as HTMLElement;
    expect(within(writeRow).getByLabelText('Mở tool order.createDraft')).not.toBeChecked();
    expect(await screen.findByText('ERP Genesis · inventory.check', { selector: '.mcp-log td' })).toBeInTheDocument();
  });

  it('mở một tool đang đóng gọi PATCH /mcp/tools/{id}/expose với is_exposed=true', async () => {
    const calls = mockFetch((c) => {
      if (c.url.includes('/mcp/tools/tool-write/expose') && c.method === 'PATCH') return json(200, { ...TOOLS[1], is_exposed: true });
      return baseHandler(c) ?? json(404);
    });
    renderScreen(<McpScreen />);
    await screen.findByText('ERP Genesis', { selector: '.mcp-server__name' });
    const user = userEvent.setup();
    await user.click(screen.getByLabelText('Mở tool order.createDraft'));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/expose') && (c.body as { is_exposed: boolean }).is_exposed === true)).toBe(true));
  });

  it("đổi ghi → đọc lỗi rồi bấm 'Giữ nguyên': lỗi không ở lại trong ô", async () => {
    mockFetch((c) => {
      if (c.url.includes('/mcp/tools/tool-write') && c.method === 'PATCH') {
        return json(409, { status: 409, code: 'CONFLICT', title: 'Tool đang bận, thử lại sau' });
      }
      return baseHandler(c) ?? json(404);
    });
    renderScreen(<McpScreen />);
    await screen.findByText('ERP Genesis', { selector: '.mcp-server__name' });
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText('Loại tool order.createDraft'), 'read');
    const dlg = await screen.findByRole('dialog', { name: 'Chuyển order.createDraft sang Chỉ đọc?' });
    await user.click(within(dlg).getByRole('button', { name: 'Chuyển sang Chỉ đọc' }));
    expect(await within(dlg).findByText('Tool đang bận, thử lại sau')).toBeInTheDocument();
    await user.click(within(dlg).getByRole('button', { name: 'Giữ nguyên' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.queryByText('Tool đang bận, thử lại sau')).toBeNull();
  });

  it('ma trận cấp quyền: tick ô agent × tool gọi POST /mcp/tools/{id}/grants', async () => {
    const calls = mockFetch((c) => {
      if (c.url.includes('/mcp/tools/tool-write/grants') && c.method === 'POST') return json(201, { ...TOOLS[1], grants: ['agent:agent-tls'] });
      return baseHandler(c) ?? json(404);
    });
    renderScreen(<McpScreen />);
    await screen.findByText('Ma trận cấp quyền');
    const user = userEvent.setup();
    await user.click(screen.getByLabelText('Cấp order.createDraft cho Trợ lý thương mại'));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/grants') && c.method === 'POST')).toBe(true));
    const grantCall = calls.find((c) => c.url.includes('/grants') && c.method === 'POST');
    expect((grantCall?.body as { agent_key: string }).agent_key).toBe('agent:agent-tls');
  });

  it('gọi thử tool chưa mở → hiện "Bị chặn" đúng như backend trả về', async () => {
    mockFetch((c) => {
      if (c.url.includes('/mcp/tools/tool-write/call') && c.method === 'POST') {
        return json(403, { status: 403, code: 'MCP_TOOL_NOT_EXPOSED', title: 'Bị chặn', detail: 'Bị chặn: tool chưa được Owner mở' });
      }
      return baseHandler(c) ?? json(404);
    });
    renderScreen(<McpScreen />);
    await screen.findByText('ERP Genesis', { selector: '.mcp-server__name' });
    const user = userEvent.setup();
    const writeRow = screen.getByText('order.createDraft', { selector: '.mcp-tool-table__name' }).closest('tr') as HTMLElement;
    await user.click(within(writeRow).getByRole('button', { name: 'Gọi thử' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Gọi tool' }));
    expect(await within(dialog).findByText(/Bị chặn: tool chưa được Owner mở/)).toBeInTheDocument();
  });
});
