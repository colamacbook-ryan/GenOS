import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { InboxItem, InboxPage, TriageSettings, TriageSummary } from '@gen-harness/contracts';
import { InboxScreen } from '../../src/screens/queue/InboxScreen';
import { triageBadges } from '../../src/screens/queue/queueModel';
import { TriageCard } from '../../src/screens/system/TriageCard';
import { queryClient } from '../../src/lib/queryClient';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

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
      const c = { url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : null };
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
});

const base: Omit<InboxItem, 'id' | 'summary' | 'triage'> = {
  code: null, item_type: 'unit', tab: 'opportunity', title: 'Hỏi giá', priority: 'P2', created_at: '2026-09-29T01:00:00Z',
  score: 0.8, confidence_band: 'cao', subject: null, group: null, agent: null, alert_type: null, alert_type_label: null,
  suggested_action: null,
};
const GOOD: InboxItem = {
  ...base, id: 'u1', summary: 'Khách cần 3 container MDF',
  triage: { duplicate_of: null, duplicate_kind: null, spam: false, spam_reason: null, score: 82, low_score: false, reason: 'độ tin 0.80', source: 'jev' },
};
const JUNK: InboxItem = {
  ...base, id: 'u2', summary: 'KHUYẾN MÃI SỐC click link',
  triage: { duplicate_of: 'u1', duplicate_kind: 'exact', spam: true, spam_reason: 'có đường link', score: 5, low_score: true, reason: 'Rác: có đường link', source: 'heuristic' },
};

function page(items: InboxItem[], hidden = 0): InboxPage {
  return {
    items, next_cursor: null, total: items.length,
    counts: { all: items.length, opportunity: items.length, alert: 0, approval: 0, reply: 0, candidate: 0 },
    triage: { enabled: true, min_score: 30, hidden },
  };
}

describe('Lọc đầu Hộp thư — huy hiệu', () => {
  it('Trùng / Rác / điểm, không dấu → không huy hiệu', () => {
    expect(triageBadges(null)).toEqual([]);
    expect(triageBadges(GOOD.triage).map((b) => b.label)).toEqual(['Điểm lọc 82']);
    const junk = triageBadges(JUNK.triage);
    expect(junk.map((b) => b.label)).toEqual(['Trùng', 'Rác', 'Điểm lọc 5']);
    expect(junk[0].title).toContain('y hệt');
    expect(junk[2].title).toContain('quy tắc');
  });
});

describe('Hộp thư — "Ẩn rác & trùng"', () => {
  it('hiện huy hiệu và gửi hide_junk=true khi bật', async () => {
    const calls = mockFetch((c) => {
      if (!c.url.includes('/inbox')) return json(404);
      return c.url.includes('hide_junk=true') ? json(200, page([GOOD], 1)) : json(200, page([GOOD, JUNK]));
    });
    renderScreen(<InboxScreen />);
    expect(await screen.findByText('Rác')).toBeInTheDocument();
    expect(screen.getByText('Trùng')).toBeInTheDocument();
    expect(screen.getByText('Điểm lọc 82')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('switch', { name: 'Ẩn rác & trùng' }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('hide_junk=true'))).toBe(true));
    expect(await screen.findByText(/Đã ẩn 1 mục/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('Rác')).not.toBeInTheDocument());
  });
});

const SETTINGS: TriageSettings = { enabled: true, min_score: 30, use_jev: true };
const SUMMARY: TriageSummary = {
  days: 7, enabled: true, min_score: 30, use_jev: true, total: 12, kept: 8, duplicates: 2, exact_duplicates: 1,
  near_duplicates: 1, spam: 1, low_score: 1, pending: 0, avg_quality: 55,
  jev: { count: 4, heuristic_count: 8, avg_latency_ms: 420, spam_agreement: 1 },
};

function triageHandler(role: string) {
  return (c: Call) => {
    if (c.url.includes('/auth/me')) return json(200, { id: 'me', role: { code: role, name: role } });
    if (c.url.includes('/refinery/triage/settings')) {
      return json(200, c.method === 'PATCH' ? { ...SETTINGS, ...(c.body as object) } : SETTINGS);
    }
    if (c.url.includes('/refinery/triage/summary')) return json(200, SUMMARY);
    return json(404);
  };
}

describe('Điều khiển hệ thống — thẻ Lọc tin', () => {
  it('Owner đổi ngưỡng điểm (Nâng cao) và tắt/bật được', async () => {
    const calls = mockFetch(triageHandler('owner'));
    renderScreen(<TriageCard />);
    expect(await screen.findByText(/12 mục đã lọc · 2 trùng · 1 rác/)).toBeInTheDocument();
    await userEvent.click(screen.getByText('Nâng cao — Jev và ngưỡng điểm'));
    expect(screen.getByText(/Jev 4 lượt, ~420 ms/)).toBeVisible();
    const input = await screen.findByLabelText('Ngưỡng điểm (0–100)');
    await waitFor(() => expect(input).not.toBeDisabled());
    await userEvent.clear(input);
    await userEvent.type(input, '45');
    await userEvent.click(screen.getByRole('button', { name: 'Lưu ngưỡng' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH' && (c.body as { min_score?: number })?.min_score === 45)).toBe(true));
    await userEvent.click(screen.getByRole('switch', { name: 'Bật lọc tin' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH' && (c.body as { enabled?: boolean })?.enabled === false)).toBe(true));
  });

  it('vai trò khác chỉ xem', async () => {
    const calls = mockFetch(triageHandler('manager'));
    renderScreen(<TriageCard />);
    expect(await screen.findByText(/Chỉ Sếp \(Owner\)/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Lưu ngưỡng' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('switch', { name: 'Bật lọc tin' }));
    expect(calls.some((c) => c.method === 'PATCH')).toBe(false);
  });
});
