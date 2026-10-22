/**
 * v0.1.71 (giả lập toàn luồng P3-4) — `biz.promises` chưa có bộ nào ghi vào, nên mọi chỗ «Lời hứa» bị ẩn thay vì để trống
 * mãi (hoặc báo «không có lời hứa nào quá hạn» như thể đã theo dõi). Cờ: `src/lib/features.ts` › `PROMISES_TRACKED`.
 */
import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { CareIssuesPage, CareResponseTimes, CareScenariosPage, TaskPage } from '@gen-harness/contracts';
import { PROMISES_TRACKED } from '../../src/lib/features';
import { kpiCards } from '../../src/owner/ownerModel';
import { CareScreen } from '../../src/screens/people/CareScreen';
import { TasksScreen } from '../../src/screens/queue/TasksScreen';
import { queryClient } from '../../src/lib/queryClient';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function mockFetch(handler: (url: string) => Response) {
  const urls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL) => {
      urls.push(String(url));
      return handler(String(url));
    }),
  );
  return urls;
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

describe('Lời hứa — chưa có bộ ghi nên ẩn (PROMISES_TRACKED = false)', () => {
  it('cờ đang tắt', () => {
    expect(PROMISES_TRACKED).toBe(false);
  });

  it('Hôm nay: 3 số, không có «Lời hứa quá hạn»', () => {
    const cards = kpiCards({ hot: 3, cooling: 2, open_opps: 12, open_value_vnd: 2_400_000_000, overdue_promises: 7 });
    expect(cards.map((c) => c.key)).toEqual(['hot', 'cooling', 'open_opps']);
  });

  it('Việc & Nhắc hẹn: không có khối «Lời hứa», không gọi /tasks/promises', async () => {
    const TASKS: TaskPage = { items: [], next_cursor: null, total: 0 };
    const urls = mockFetch((u) => (u.includes('/tasks') ? json(200, TASKS) : json(404)));
    renderScreen(<TasksScreen />);
    expect(await screen.findByText('Việc & Nhắc hẹn')).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 40));
    expect(screen.queryByText('Lời hứa')).toBeNull();
    expect(screen.queryByLabelText('Tab lời hứa')).toBeNull();
    expect(urls.some((u) => u.includes('/tasks/promises'))).toBe(false);
    expect(document.body.textContent).not.toMatch(/lời hứa/i);
  });

  it('Chăm sóc khách: không có ô «Hứa rồi quên», không nói số «hứa vỡ»', async () => {
    const RESPONSE: CareResponseTimes = {
      from: '2026-08-25T00:00:00Z', to: '2026-09-24T00:00:00Z', items: [],
      totals: { fast: 5, normal: 5, slow: 2, total_answered: 12, fast_pct: 41.7 }, unattended: 3,
    };
    const ISSUES: CareIssuesPage = { items: [], next_cursor: null, total: 0 };
    const SCENARIOS: CareScenariosPage = {
      items: [], next_cursor: null, total: 0,
      summary: { won: { count: 1, avg_fast_pct: 83.3, avg_broken_promises: 0 }, lost: { count: 0, avg_fast_pct: 0, avg_broken_promises: 0 } },
    };
    mockFetch((u) => {
      if (u.includes('/care/response-times')) return json(200, RESPONSE);
      if (u.includes('/care/repeated-issues')) return json(200, ISSUES);
      if (u.includes('/care/scenarios')) return json(200, SCENARIOS);
      return json(404);
    });
    renderScreen(<CareScreen />);
    expect(await screen.findByText('Khách chưa ai trả lời')).toBeInTheDocument();
    expect(screen.queryByText('Hứa rồi quên')).toBeNull();
    expect(screen.getByText('Khách bị bỏ rơi')).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/hứa/i);
  });
});
