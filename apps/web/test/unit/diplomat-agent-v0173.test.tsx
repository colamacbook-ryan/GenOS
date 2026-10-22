import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { AgentIdentity } from '@gen-harness/contracts';
import { DiplomatPanel } from '../../src/screens/agents/AgentsScreen';
import { queryClient as appQueryClient } from '../../src/lib/queryClient';

/** v0.1.73: «Agent ngoại giao» — Gen chỉ ở Console; kênh ngoài nói bằng agent Sếp chọn (Owner + PIN). */

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const agent = (id: string, name: string, is_enabled = true) => ({ id, name, is_enabled }) as unknown as AgentIdentity;
const AGENTS = [agent('a1', 'Heo'), agent('a2', 'A.Ly'), agent('a3', 'Tắt', false)];

interface Call {
  url: string;
  method: string;
  body: unknown;
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
function renderPanel(canManage: boolean) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <DiplomatPanel agents={AGENTS} canManage={canManage} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  appQueryClient.clear();
});

describe('<DiplomatPanel> — Agent ngoại giao', () => {
  it('chưa chọn: nói bot xưng "trợ lý"; chỉ liệt kê agent đang bật; chọn rồi Lưu gọi PUT đúng agent', async () => {
    let current: { agent_id: string | null; name: string | null; neutral_name: string } = { agent_id: null, name: null, neutral_name: 'trợ lý' };
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/agents/diplomat') && c.method === 'GET') return json(200, current);
      if (c.url.endsWith('/agents/diplomat') && c.method === 'PUT') {
        current = { agent_id: (c.body as { agent_id: string }).agent_id, name: 'Heo', neutral_name: 'trợ lý' };
        return json(200, current);
      }
      return json(404);
    });
    renderPanel(true);
    const select = (await screen.findByLabelText('Agent ngoại giao')) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.text)).toEqual(['Chưa chọn — bot xưng “trợ lý”', 'Heo', 'A.Ly']);
    await waitFor(() => expect(select).toBeEnabled());
    const save = screen.getByRole('button', { name: 'Lưu' });
    expect(save).toBeDisabled();
    const user = userEvent.setup();
    await user.selectOptions(select, 'a1');
    expect(save).toBeEnabled();
    await user.click(save);
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ agent_id: 'a1' });
    await waitFor(() => expect((screen.getByLabelText('Agent ngoại giao') as HTMLSelectElement).value).toBe('a1'));
    expect(screen.getByText(/Đổi Agent ngoại giao cần mã PIN/)).toBeInTheDocument();
  });

  it('không có quyền quản lý: chỉ xem, không có nút Lưu', async () => {
    mockFetch((c) => (c.url.endsWith('/agents/diplomat') ? json(200, { agent_id: 'a2', name: 'A.Ly', neutral_name: 'trợ lý' }) : json(404)));
    renderPanel(false);
    const select = (await screen.findByLabelText('Agent ngoại giao')) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe('a2'));
    expect(select).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Lưu' })).toBeNull();
  });
});
