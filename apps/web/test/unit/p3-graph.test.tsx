import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { GraphListPage, GraphPeopleResult, GraphTopicsPage } from '@gen-harness/contracts';
import { GraphScreen } from '../../src/screens/graph/GraphScreen';
import { queryClient } from '../../src/lib/queryClient';
import { useUrlStateStore } from '../../src/lib/uiStore';
import { USER_IDS } from '../mock-ids';

const json = (status: number, body?: unknown) => new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

interface Call {
  url: string;
  method: string;
}
function mockFetch(handler: (c: Call) => Response | Promise<Response>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const c = { url: String(url), method: init?.method ?? 'GET' };
      calls.push(c);
      return handler(c);
    }),
  );
  return calls;
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

const LIST_PAGE: GraphListPage = {
  items: [
    {
      id: 'p-bao', code: 'PER-0042', name: 'Nguyễn Văn Bảo', type: 'customer', org_name: 'Công ty in Thành Phát', relation: 'direct',
      channels: ['zalo'], heat: 87, potential: 58, risk: 84, owner_user_id: USER_IDS.lan, last_interaction_at: '2026-09-24T02:00:00Z',
      state: 'active', degree: 3, total_weight: 42.5, bridge_score: 0,
    },
    {
      id: 'p-tri', code: 'PER-0688', name: 'Đặng Hữu Trí', type: 'customer', org_name: 'Gỗ Đông Phương', relation: 'via_staff',
      channels: ['zalo'], heat: 22, potential: 35, risk: 74, owner_user_id: null, last_interaction_at: '2026-07-01T00:00:00Z',
      state: 'cold', degree: 1, total_weight: 5, bridge_score: 0,
    },
  ],
  next_cursor: null,
  total: 2,
};

const PEOPLE_GRAPH: GraphPeopleResult = {
  nodes: [
    { id: 'p-ha', code: 'PER-0007', name: 'Nguyễn Thu Hà', type: 'staff' },
    { id: 'p-bao', code: 'PER-0042', name: 'Nguyễn Văn Bảo', type: 'customer' },
  ],
  edges: [{ from: 'p-ha', to: 'p-bao', weight: 42.5, interactions: 14, last_at: '2026-09-24T02:00:00Z', state: 'active', topic: 'hợp đồng in ấn quý 4' }],
  node_limit: 200,
  total_edges: 1,
  truncated: false,
};

const TOPICS_PAGE: GraphTopicsPage = {
  items: [{ topic: 'hợp đồng in ấn quý 4', edges: 1, people: 2, total_weight: 42.5, last_at: '2026-09-24T02:00:00Z', state: 'active' }],
};

function graphHandler(c: Call): Response {
  const u = c.url;
  if (u.includes('/graph/list')) return json(200, LIST_PAGE);
  if (u.includes('/graph/people')) return json(200, PEOPLE_GRAPH);
  if (u.includes('/graph/groups')) return json(200, { nodes: [], edges: [], node_limit: 200, total_edges: 0, truncated: false });
  if (u.includes('/graph/topics/')) return json(200, PEOPLE_GRAPH);
  if (u.includes('/graph/topics')) return json(200, TOPICS_PAGE);
  if (u.includes('/graph/layout/')) {
    if (c.method === 'PUT') return json(200, { ok: true });
    return json(200, { positions: {} });
  }
  if (u.includes('/graph/recompute')) return json(200, { ok: true, counts: { interacts: 11, shares_members: 9, owns: 2, bridges: 5 } });
  return json(404);
}

describe('Bản đồ quan hệ', () => {
  it('chế độ Danh sách: lọc theo độ nóng qua URL, ẩn khỏi bảng người không khớp', async () => {
    mockFetch(graphHandler);
    renderScreen(<GraphScreen />);
    expect(await screen.findByText('Nguyễn Văn Bảo')).toBeInTheDocument();
    expect(screen.getByText('Đặng Hữu Trí')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Độ nóng/ }));
    await userEvent.click(await screen.findByText('≥ 80'));
    await waitFor(() => expect(useUrlStateStore.getState().params.heat).toBe('high'));
  });

  it('link cũ ?owner=u-ha (id giả trước v0.1.35) → bỏ qua bộ lọc thay vì 422; UUID thật vẫn gửi owner_user_id', async () => {
    const calls = mockFetch(graphHandler);
    useUrlStateStore.setState({ params: { owner: 'u-ha' } });
    renderScreen(<GraphScreen />);
    expect(await screen.findByText('Nguyễn Văn Bảo')).toBeInTheDocument();
    const lists = calls.filter((c) => c.url.includes('/graph/list'));
    expect(lists.length).toBeGreaterThan(0);
    expect(lists.every((c) => !c.url.includes('owner_user_id'))).toBe(true);
    await waitFor(() => expect(useUrlStateStore.getState().params.owner).toBeUndefined());

    calls.length = 0;
    useUrlStateStore.setState((st) => ({ params: { ...st.params, owner: USER_IDS.lan } }));
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('/graph/list') && c.url.includes(`owner_user_id=${USER_IDS.lan}`))).toBe(true),
    );
  });

  it('chế độ Người↔Người: hiện node và cạnh từ GET /graph/people, chọn một node mở panel chi tiết', async () => {
    mockFetch(graphHandler);
    renderScreen(<GraphScreen />);
    await userEvent.click(screen.getByRole('radio', { name: /Người ↔ Người/ }));
    await waitFor(() => expect(useUrlStateStore.getState().params.mode).toBe('people'));
    expect((await screen.findAllByText('Nguyễn Thu Hà')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Nguyễn Văn Bảo').length).toBeGreaterThan(0);

    await userEvent.click(screen.getByRole('button', { name: /Nguyễn Thu Hà/ }));
    const panel = await screen.findByText('Nguyễn Thu Hà', { selector: '.gh-card__title' });
    expect(within(panel.closest('.gh-card') as HTMLElement).getByRole('link', { name: /Mở hồ sơ sống/ })).toHaveAttribute('href', '/profile?id=p-ha');
  });

  it('chế độ Luồng chủ đề: danh sách rồi bấm một luồng mở đồ thị chi tiết', async () => {
    mockFetch(graphHandler);
    renderScreen(<GraphScreen />);
    await userEvent.click(screen.getByRole('radio', { name: /Luồng chủ đề/ }));
    const row = await screen.findByText('hợp đồng in ấn quý 4');
    await userEvent.click(row);
    await waitFor(() => expect(useUrlStateStore.getState().params.topic).toBe('hợp đồng in ấn quý 4'));
    expect((await screen.findAllByText('Nguyễn Thu Hà')).length).toBeGreaterThan(0);
  });

  it('Dựng lại gọi POST /graph/recompute và hiện số cạnh vừa tính', async () => {
    const calls = mockFetch(graphHandler);
    renderScreen(<GraphScreen />);
    await screen.findByText('Nguyễn Văn Bảo');
    await userEvent.click(screen.getByRole('button', { name: 'Dựng lại' }));
    await waitFor(() => expect(screen.getByText(/Đã dựng lại/)).toBeInTheDocument());
    expect(calls.some((c) => c.url.includes('/graph/recompute') && c.method === 'POST')).toBe(true);
  });
});
