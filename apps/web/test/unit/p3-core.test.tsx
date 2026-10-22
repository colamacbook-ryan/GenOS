import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider, createMemoryRouter, useLocation } from 'react-router-dom';
import { confidenceBand, type DraftPage, type Explain } from '@gen-harness/contracts';
import { WhyButton } from '../../src/screens/core/Evidence';
import { SavedViewsButton } from '../../src/screens/core/SavedViews';
import { searchToFilters, viewHref } from '../../src/screens/core/viewsModel';
import { qk3 } from '../../src/screens/core/queries';
import { queryClient } from '../../src/lib/queryClient';
import { applyEvent } from '../../src/lib/realtime';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });

function mockFetch(handler: (url: string, method: string, body: unknown) => Response) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const c = {
        url: String(url),
        method: init?.method ?? 'GET',
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      };
      calls.push(c);
      return handler(c.url, c.method, c.body);
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
  queryClient.clear();
});

const EXPLAIN: Explain = {
  kind: 'score',
  id: 'person:p1:heat',
  title: 'Nguyễn Văn Bảo — độ nóng',
  statement: '87/100 · tin cậy 0,91',
  method: 'rules+model',
  factors: [
    {
      label: 'Complained: hỏi ba lần chưa ai trả lời',
      value: 87,
      evidence: [{ type: 'meaning_unit', id: 'u1' }],
    },
  ],
  units: [
    {
      id: 'u1',
      event_type: 'Complained',
      conclusion: 'Khách phàn nàn chậm trả lời',
      confidence: 0.93,
      observed_at: '2026-09-24T02:00:00Z',
      group: null,
      person: null,
      quotes: [
        {
          raw_id: 'r1',
          raw_code: 'RAW-918422',
          quote: 'Anh hỏi ba lần rồi',
          occurred_at: '2026-09-24T02:00:00Z',
          channel: 'zalo',
          sender: null,
        },
      ],
    },
  ],
  history: [
    {
      value: 87,
      computed_at: '2026-09-24T02:00:00Z',
      method: 'rules+model',
      by: null,
    },
    {
      value: 72,
      computed_at: '2026-09-23T02:00:00Z',
      method: 'rules+model',
      by: null,
    },
  ],
};

describe('chứng cứ', () => {
  it('confidence bands follow the contract thresholds', () => {
    expect([0.95, 0.8, 0.79, 0.6, 0.59].map(confidenceBand)).toEqual(['high', 'high', 'medium', 'medium', 'low']);
  });

  it('WhyButton opens the evidence chain and loads the raw record on demand', async () => {
    const user = userEvent.setup();
    const calls = mockFetch((url) =>
      url.includes('/explain/raw/r1')
        ? json(200, {
            id: 'r1',
            text: 'Anh hỏi ba lần rồi mà không ai trả lời. Nguyên văn đầy đủ.',
          })
        : json(200, EXPLAIN),
    );
    render(
      <QueryClientProvider client={queryClient}>
        <WhyButton kind="score" id="person:p1:heat">
          Vì sao
        </WhyButton>
      </QueryClientProvider>,
    );
    await user.click(screen.getByRole('button', { name: 'Vì sao' }));
    const dialog = await screen.findByRole('dialog', {
      name: 'Nguyễn Văn Bảo — độ nóng',
    });
    expect(calls[0].url).toContain('/explain/score/person%3Ap1%3Aheat');
    expect(within(dialog).getByText('87/100 · tin cậy 0,91')).toBeInTheDocument();
    expect(within(dialog).getByText('tin cậy cao')).toBeInTheDocument();
    expect(within(dialog).getByText('Lịch sử điểm')).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes('/explain/raw/'))).toBe(false);
    await user.click(within(dialog).getByRole('button', { name: 'Xem nguyên văn' }));
    expect(await within(dialog).findByText(/Nguyên văn đầy đủ/)).toBeInTheDocument();
  });

  it('says there is no evidence instead of presenting a bare score', async () => {
    const user = userEvent.setup();
    mockFetch(() =>
      json(200, {
        ...EXPLAIN,
        statement: 'Chưa có điểm · Chưa có chứng cứ',
        factors: [],
        units: [],
        history: [],
      }),
    );
    render(
      <QueryClientProvider client={queryClient}>
        <WhyButton kind="score" id="person:p1:heat" />
      </QueryClientProvider>,
    );
    await user.click(screen.getByRole('button', { name: 'Vì sao hệ thống nghĩ vậy' }));
    expect(await screen.findByText('Chưa có chứng cứ')).toBeInTheDocument();
  });
});

function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname + l.search}</div>;
}

describe('góc nhìn đã lưu', () => {
  it('URL params round-trip into a view link', () => {
    const f = searchToFilters('?tab=hot&owner=me');
    expect(f).toEqual({ tab: 'hot', owner: 'me' });
    expect(viewHref('inbox', f)).toBe('/inbox?tab=hot&owner=me');
    expect(viewHref('inbox', {})).toBe('/inbox');
  });

  it('saves the current filters and opens a saved view', async () => {
    const user = userEvent.setup();
    let views: unknown[] = [];
    const calls = mockFetch((_url, method, body) => {
      if (method === 'POST') {
        const v = {
          id: 'v1',
          created_at: '2026-09-24T00:00:00Z',
          ...(body as object),
        };
        views = [v];
        return json(201, v);
      }
      return json(200, views);
    });
    const router = createMemoryRouter(
      [
        {
          path: '/inbox',
          handle: { screen: 'inbox' },
          element: (
            <>
              <SavedViewsButton />
              <Where />
            </>
          ),
        },
      ],
      { initialEntries: ['/inbox?tab=hot'] },
    );
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    await user.click(screen.getByRole('button', { name: 'Góc nhìn đã lưu' }));
    await screen.findByText('Chưa có góc nhìn nào');
    await user.type(screen.getByLabelText('Lưu góc nhìn hiện tại'), 'Khách nóng');
    await user.click(screen.getByRole('button', { name: 'Lưu góc nhìn' }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === 'POST')?.body).toEqual({
        screen: 'inbox',
        name: 'Khách nóng',
        filters: { tab: 'hot' },
      }),
    );
    await user.click(await screen.findByRole('button', { name: /^Khách nóng/ }));
    expect(screen.getByTestId('where')).toHaveTextContent('/inbox?tab=hot');
  });
});

describe('bản nháp realtime', () => {
  it('draft.new prepends to the pending list once', () => {
    const page: DraftPage = { items: [], next_cursor: null, total: 0 };
    queryClient.setQueryData(qk3.draftList('pending'), page);
    const item = {
      id: 'd1',
      code: 'ACT-0240',
      kind: 'message',
      kind_label: 'Tin nhắn',
      title: 'x',
      agent: null,
      created_by: null,
      created_at: '',
      status: 'pending',
      hold_reason: null,
      subject: null,
    };
    applyEvent(queryClient, { type: 'draft.new', data: item } as never);
    applyEvent(queryClient, { type: 'draft.new', data: item } as never);
    const after = queryClient.getQueryData<DraftPage>(qk3.draftList('pending'));
    expect(after?.items.map((i) => i.id)).toEqual(['d1']);
    expect(after?.total).toBe(1);
  });
});
