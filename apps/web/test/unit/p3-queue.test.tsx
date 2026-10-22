import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { InboxPage, Overview, PromisePage, TaskPage } from '@gen-harness/contracts';
import { InboxScreen } from '../../src/screens/queue/InboxScreen';
import { OverviewScreen } from '../../src/screens/queue/OverviewScreen';
import { TasksScreen } from '../../src/screens/queue/TasksScreen';
import { queryClient } from '../../src/lib/queryClient';
import { api } from '../../src/lib/api';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

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
});

const OVERVIEW: Overview = {
  kpis: [
    { key: 'channels_live', label: 'Kênh sống', value: 4, unit: null, row: 1, status: 'ok', sublabel: null, pct: null, filter: { screen: 'system', filters: {} } },
    { key: 'pending_ratio', label: 'Tỉ lệ chờ duyệt', value: 42.9, unit: '%', row: 1, status: 'ok', sublabel: null, pct: null, filter: { screen: 'workbench', filters: { status: 'pending' } } },
    { key: 'active_profiles', label: 'Hồ sơ hoạt động (30 ngày)', value: 214, unit: null, row: 2, status: 'ok', sublabel: null, pct: null, filter: { screen: 'directory', filters: {} } },
  ],
  queue: [{ kind: 'opportunity', id: 'u1', code: 'OPP-1842', title: 'Xưởng gỗ Bình Dương cần 3 container ván MDF', priority: 'P1', at: '2026-09-24T02:00:00Z', due_at: null }],
  spotlight: [{ person: { id: 'p1', code: 'PER-0042', name: 'Nguyễn Văn Bảo', type: 'customer', org_name: null }, dimension: 'churn_risk', value: 87, at: '2026-09-24T02:00:00Z' }],
  signals: [{ topic: 'Giá ván MDF', count: 12, delta_pct: 142 }],
  health: { channels: [{ type: 'zalo', active: 1 }], plugins: { healthy: 9, degraded: 2, isolated: 0 }, backlog_pending: 3 },
  dataQuality: { missing_identity_pct: 12, low_confidence_score_pct: 9, unassigned_event_pct: 4 },
  hourly: [{ hour: '00:00', count: 15 }],
};

describe('Tổng quan điều hành', () => {
  it('shows KPI values and the queue widget from GET /overview', async () => {
    mockFetch((c) => (c.url.includes('/overview') ? json(200, OVERVIEW) : json(404)));
    renderScreen(<OverviewScreen />);
    const kenhSong = await screen.findByText('Kênh sống');
    expect(within(kenhSong.closest('.ov-kpi') as HTMLElement).getByText('4')).toBeInTheDocument();
    expect(screen.getByText('42,9', { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/Xưởng gỗ Bình Dương cần 3 container/)).toBeInTheDocument();
    expect(screen.getByText('Nguyễn Văn Bảo')).toBeInTheDocument();
  });
});

function inboxPage(tab: string): InboxPage {
  const all = [
    {
      id: 'iq-1', code: 'OPP-1842', item_type: 'unit' as const, tab: 'opportunity' as const, title: 'AskedPrice',
      summary: 'Xưởng gỗ Bình Dương hỏi giá 3 container MDF.', priority: 'P1' as const, created_at: '2026-09-24T02:00:00Z',
      score: 0.91, confidence_band: 'cao' as const, subject: null, group: null, agent: null,
      alert_type: null, alert_type_label: null, suggested_action: 'Nhận và ráp khớp nhà cung cấp',
    },
    {
      id: 'iq-2', code: 'ALR-0233', item_type: 'alert' as const, tab: 'alert' as const, title: 'Khách đang lạnh / sắp mất',
      summary: 'Khách nhắn ba lần chưa ai trả lời.', priority: 'P1' as const, created_at: '2026-09-24T01:00:00Z',
      score: null, confidence_band: null, subject: null, group: null, agent: null,
      alert_type: 'customer_cooling', alert_type_label: 'Khách đang lạnh / sắp mất', suggested_action: 'Mở hồ sơ và gán người xử lý',
    },
  ];
  const items = tab === 'all' ? all : all.filter((i) => i.tab === tab);
  return { items, next_cursor: null, total: all.length, counts: { all: 2, opportunity: 1, alert: 1, approval: 0, reply: 0, candidate: 0 } };
}

describe('Hộp thư ý nghĩa', () => {
  it('shows tab counts and filters the list when a tab is clicked', async () => {
    const calls = mockFetch((c) => {
      if (!c.url.includes('/inbox')) return json(404);
      const tab = new URL(c.url, 'http://x').searchParams.get('tab') ?? 'all';
      return json(200, inboxPage(tab));
    });
    const user = userEvent.setup();
    renderScreen(<InboxScreen />);

    expect(await screen.findByText(/Xưởng gỗ Bình Dương hỏi giá/)).toBeInTheDocument();
    expect(screen.getByText(/Khách nhắn ba lần/)).toBeInTheDocument();
    const tab = screen.getByRole('tab', { name: /Cảnh báo/ });
    expect(within(tab).getByText('1')).toBeInTheDocument();

    await user.click(tab);
    await waitFor(() => expect(calls.some((c) => c.url.includes('tab=alert'))).toBe(true));
    expect(await screen.findByText(/Khách nhắn ba lần/)).toBeInTheDocument();
    expect(screen.queryByText(/Xưởng gỗ Bình Dương hỏi giá/)).not.toBeInTheDocument();
  });

  it('v0.1.43 (F-30, F-62): độ tin cậy không hiện thường trực — rê chuột huy hiệu ưu tiên mới thấy; câu mô tả mới', async () => {
    mockFetch((c) => (c.url.includes('/inbox') ? json(200, inboxPage('all')) : json(404)));
    const user = userEvent.setup();
    renderScreen(<InboxScreen />);

    const card = (await screen.findByText(/Xưởng gỗ Bình Dương hỏi giá/)).closest('article') as HTMLElement;
    expect(card.textContent).not.toMatch(/độ tin cậy/i);
    expect(
      screen.getByText(
        'Không phải tin nhắn thô. Mỗi dòng là một ý chính rút ra từ tin nhắn: nguồn, người liên quan, điểm ưu tiên, tóm tắt hai câu, gợi ý việc nên làm và tin gốc làm chứng cứ.',
      ),
    ).toBeInTheDocument();

    const prio = within(card).getByLabelText('Ưu tiên P1 · Độ tin cậy: cao');
    expect(prio).not.toHaveAttribute('title'); // chỉ Tooltip — không có tooltip trình duyệt chồng lên
    await user.hover(prio);
    expect(await screen.findByRole('tooltip')).toHaveTextContent('Độ tin cậy: cao');
  });

  it('v0.1.43 (F-62): danh sách trống, header lỗi → trạng thái trống cũ với câu mới', async () => {
    mockFetch((c) => (c.url.includes('/inbox') ? json(200, { ...inboxPage('all'), items: [] }) : json(404)));
    renderScreen(<InboxScreen />);
    expect(await screen.findByText('Hộp thư đang trống')).toBeInTheDocument();
    expect(screen.getByText('Không có tin, cảnh báo hay bản nháp nào khớp bộ lọc hiện tại.')).toBeInTheDocument();
  });
});

const TASKS: TaskPage = {
  items: [
    {
      id: 't1', code: 'TSK-0410', title: 'Gửi hợp đồng đã ký cho Thành Phát', priority: 'P1', status: 'doing',
      assignee: { id: 'u1', name: 'Anh Nguyễn Văn A (Chủ)' }, subject: null, due_at: '2020-01-01T00:00:00Z', remind_at: null,
      overdue: true, source: 'draft', created_at: '2026-09-20T00:00:00Z', completed_at: null,
    },
    {
      id: 't2', code: 'TSK-0412', title: 'Chuẩn bị nội dung giao ban', priority: 'P3', status: 'todo',
      assignee: null, subject: null, due_at: '2030-01-01T00:00:00Z', remind_at: null,
      overdue: false, source: 'manual', created_at: '2026-09-20T00:00:00Z', completed_at: null,
    },
  ],
  next_cursor: null,
  total: 2,
};
const PROMISES: PromisePage = { items: [], next_cursor: null, total: 0 };

describe('Việc & Nhắc hẹn', () => {
  it('tô đỏ việc quá hạn', async () => {
    mockFetch((c) => {
      if (c.url.includes('/tasks/promises')) return json(200, PROMISES);
      if (c.url.includes('/tasks')) return json(200, TASKS);
      return json(404);
    });
    const { container } = renderScreen(<TasksScreen />);
    expect(await screen.findByText('Gửi hợp đồng đã ký cho Thành Phát')).toBeInTheDocument();
    const overdueRow = screen.getByText('Gửi hợp đồng đã ký cho Thành Phát').closest('.tk-row');
    expect(overdueRow).toHaveClass('tk-row--overdue');
    const okRow = screen.getByText('Chuẩn bị nội dung giao ban').closest('.tk-row');
    expect(okRow).not.toHaveClass('tk-row--overdue');
    expect(container.querySelectorAll('.tk-row--overdue')).toHaveLength(1);
  });
});

describe('Endpoint Jev + lọc trước (v0.1.55)', () => {
  it('gọi đúng đường dẫn / method / thân; không bao giờ gửi khoá khi dùng khoá OpenRouter đang có', async () => {
    const bodies: unknown[] = [];
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        const u = new URL(String(url), 'http://x');
        calls.push(`${init?.method ?? 'GET'} ${u.pathname.replace(/^\/api\/v1/, '')}${u.search}`);
        bodies.push(typeof init?.body === 'string' ? JSON.parse(init.body) : null);
        return json(200, { items: [], total: 0, days: 30 });
      }),
    );
    await api.queue.triage.skipped(20);
    await api.queue.jev.valueSummary(14);
    await api.queue.jev.benchmark();
    await api.queue.jev.enable({ use_existing_openrouter: true });
    await api.queue.triage.setSettings({ prefilter: false });
    expect(calls).toEqual([
      'GET /refinery/triage/skipped?limit=20',
      'GET /jev/value-summary?days=14',
      'POST /jev/benchmark',
      'POST /jev/enable',
      'PATCH /refinery/triage/settings',
    ]);
    expect(bodies[3]).toEqual({ use_existing_openrouter: true });
    expect(bodies[4]).toEqual({ prefilter: false });
  });
});
