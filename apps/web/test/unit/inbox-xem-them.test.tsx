import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { InboxItem, InboxPage, Task, TaskPage } from '@gen-harness/contracts';
import { InboxScreen } from '../../src/screens/queue/InboxScreen';
import { TasksScreen } from '../../src/screens/queue/TasksScreen';
import { queryClient } from '../../src/lib/queryClient';
import { useUrlStateStore } from '../../src/lib/uiStore';

// F052 (v0.1.62, gói A4): Hộp thư và Việc phải xem được HẾT — "Xem thêm" nối trang kế theo `next_cursor`,
// kèm "Đang xem 50/130". Trước đây chỉ vẽ trang đầu (50 dòng) dù tab ghi tổng 130.

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

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

const TOTAL = 130;
const PAGE = 50;

function inboxItem(n: number): InboxItem {
  return {
    id: `iq-${n}`, code: `OPP-${n}`, item_type: 'unit', tab: 'opportunity', title: 'AskedPrice',
    summary: `Cơ hội số ${n}`, priority: 'P2', created_at: new Date(Date.UTC(2026, 8, 24) - n * 60_000).toISOString(),
    score: 0.5, confidence_band: null, subject: null, group: null, agent: null,
    alert_type: null, alert_type_label: null, suggested_action: null,
  };
}

/** Máy chủ giả: 130 mục xếp mới → cũ, phân trang theo con trỏ = chỉ số bắt đầu (`c<số>`), giống hợp đồng `next_cursor`. */
function inboxServer(opts: { failCursor?: string } = {}) {
  const urls: URL[] = [];
  let failed = false;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL) => {
      const u = new URL(String(url), 'http://x');
      if (!u.pathname.endsWith('/inbox')) return json(404);
      urls.push(u);
      const cursor = u.searchParams.get('cursor');
      if (opts.failCursor && cursor === opts.failCursor && !failed) {
        failed = true;
        // 4xx: ứng dụng không tự thử lại ⇒ lỗi hiện ngay.
        return json(403, { error: { code: 'FORBIDDEN', message: 'boom' } });
      }
      const start = cursor ? Number(cursor.slice(1)) : 0;
      const limit = Number(u.searchParams.get('limit') ?? 50);
      const items = Array.from({ length: Math.min(limit, TOTAL - start) }, (_, i) => inboxItem(start + i + 1));
      const body: InboxPage = {
        items,
        next_cursor: start + limit < TOTAL ? `c${start + limit}` : null,
        total: TOTAL,
        counts: { all: TOTAL, opportunity: TOTAL, alert: 0, approval: 0, reply: 0, candidate: 0 },
        triage: { enabled: false, min_score: null, hidden: 0 },
      };
      return json(200, body);
    }),
  );
  return urls;
}

describe('Hộp thư — Xem thêm (F052)', () => {
  it('vẽ 50 dòng đầu + "Đang xem 50/130"; mỗi lần bấm "Xem thêm" nối trang kế bằng con trỏ; hết thì mất nút', async () => {
    const urls = inboxServer();
    const user = userEvent.setup();
    renderScreen(<InboxScreen />);

    expect(await screen.findByText('Cơ hội số 1')).toBeInTheDocument();
    expect(screen.getAllByRole('article')).toHaveLength(PAGE);
    expect(screen.queryByText('Cơ hội số 51')).not.toBeInTheDocument();
    expect(screen.getByTestId('inbox-xem-them-dem')).toHaveTextContent('Đang xem 50/130 mục');

    await user.click(screen.getByRole('button', { name: /Xem thêm/ }));
    expect(await screen.findByText('Cơ hội số 51')).toBeInTheDocument();
    expect(screen.getAllByRole('article')).toHaveLength(100);
    expect(screen.getByTestId('inbox-xem-them-dem')).toHaveTextContent('Đang xem 100/130 mục');
    expect(urls[1].searchParams.get('cursor')).toBe('c50');

    await user.click(screen.getByRole('button', { name: /Xem thêm/ }));
    expect(await screen.findByText('Cơ hội số 130')).toBeInTheDocument();
    expect(screen.getAllByRole('article')).toHaveLength(TOTAL);
    expect(urls[2].searchParams.get('cursor')).toBe('c100');
    // Đã xem hết: không còn nút và không còn dòng đếm.
    expect(screen.queryByRole('button', { name: /Xem thêm/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId('inbox-xem-them')).not.toBeInTheDocument();
    // Ca đầu bấm 2 lần + dựng 130 dòng: sát ngưỡng 5 giây mặc định khi máy tải ⇒ nâng timeout riêng cho ca này.
  }, 20_000);

  it('đang lọc theo Ý định thì không bịa tổng (số tab không tính theo ý định): chỉ ghi số đã xem', async () => {
    inboxServer();
    useUrlStateStore.setState({ params: { intent: 'AskedPrice' } });
    try {
      renderScreen(<InboxScreen />);
      expect(await screen.findByText('Cơ hội số 1')).toBeInTheDocument();
      expect(screen.getByTestId('inbox-xem-them-dem')).toHaveTextContent('Đang xem 50 mục, còn nữa');
    } finally {
      useUrlStateStore.setState({ params: {} });
    }
  });

  it('tải trang kế lỗi: GIỮ các dòng đã tải, báo lỗi thân thiện + "Chi tiết kỹ thuật", bấm thử lại thì nối tiếp', async () => {
    inboxServer({ failCursor: 'c50' });
    const user = userEvent.setup();
    renderScreen(<InboxScreen />);

    expect(await screen.findByText('Cơ hội số 1')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Xem thêm/ }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/Chi tiết kỹ thuật/);
    expect(alert.textContent).not.toMatch(/\[object Object\]/);
    // 50 dòng đã tải vẫn còn, không bị thay bằng màn lỗi.
    expect(screen.getAllByRole('article')).toHaveLength(PAGE);
    expect(screen.getByText('Cơ hội số 1')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Thử tải lại/ }));
    expect(await screen.findByText('Cơ hội số 51')).toBeInTheDocument();
    expect(screen.getAllByRole('article')).toHaveLength(100);
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  });
});

function task(n: number): Task {
  return {
    id: `t-${n}`, code: `VIEC-${n}`, title: `Việc số ${n}`, priority: 'P2', status: 'todo', assignee: null, subject: null,
    due_at: null, remind_at: null, overdue: false, source: 'manual', created_at: '2026-09-24T02:00:00Z', completed_at: null,
  };
}

describe('Việc & Nhắc hẹn — Xem thêm (F052)', () => {
  it('130 việc: 50 dòng đầu, "Đang xem 50/130 việc", Xem thêm đến hết', async () => {
    const urls: URL[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL) => {
        const u = new URL(String(url), 'http://x');
        if (u.pathname.endsWith('/tasks/promises')) return json(200, { items: [], next_cursor: null, total: 0 });
        if (!u.pathname.endsWith('/tasks')) return json(404);
        urls.push(u);
        const cursor = u.searchParams.get('cursor');
        const start = cursor ? Number(cursor.slice(1)) : 0;
        const limit = Number(u.searchParams.get('limit') ?? 50);
        const items = Array.from({ length: Math.min(limit, TOTAL - start) }, (_, i) => task(start + i + 1));
        const body: TaskPage = { items, next_cursor: start + limit < TOTAL ? `c${start + limit}` : null, total: TOTAL };
        return json(200, body);
      }),
    );
    const user = userEvent.setup();
    const { container } = renderScreen(<TasksScreen />);

    expect(await screen.findByText('Việc số 1')).toBeInTheDocument();
    expect(container.querySelectorAll('.tk-row')).toHaveLength(PAGE);
    expect(screen.queryByText('Việc số 51')).not.toBeInTheDocument();
    expect(screen.getByTestId('tasks-xem-them-dem')).toHaveTextContent('Đang xem 50/130 việc');

    await user.click(screen.getByRole('button', { name: /Xem thêm/ }));
    expect(await screen.findByText('Việc số 51')).toBeInTheDocument();
    expect(screen.getByTestId('tasks-xem-them-dem')).toHaveTextContent('Đang xem 100/130 việc');
    expect(urls[1].searchParams.get('cursor')).toBe('c50');

    await user.click(screen.getByRole('button', { name: /Xem thêm/ }));
    expect(await screen.findByText('Việc số 130')).toBeInTheDocument();
    expect(container.querySelectorAll('.tk-row')).toHaveLength(TOTAL);
    expect(screen.queryByRole('button', { name: /Xem thêm/ })).not.toBeInTheDocument();
  });
});
