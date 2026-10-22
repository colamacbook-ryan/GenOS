/**
 * v0.1.42 (F-64, F-61): Hôm nay (Tổng quan) — MỘT hàng 4 số chính từ `kpis`, 4 số kỹ thuật trong thẻ "Sức khoẻ hệ
 * thống" (`health.tech`, thiếu thì bỏ qua), không còn đếm plugin; không có thẻ cập nhật — chỉ một dòng báo bản mới
 * dẫn tới Cài đặt › Sao lưu & cập nhật.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { Overview } from '@gen-harness/contracts';
import { OverviewScreen } from '../../src/screens/queue/OverviewScreen';
import { UpdateNotice } from '../../src/update/UpdateNotice';
import { qk } from '../../src/lib/queries';
import { queryClient as appQueryClient } from '../../src/lib/queryClient';
import { createMock } from '../mock-p3-queue';
import { permissionsOf } from '../mock-api';

/** Lấy đúng payload `GET /overview` của máy chủ giả (hợp đồng gói menu-api). */
function mockOverview(): Overview {
  const m = createMock({ fresh: false, emit: () => {} });
  let out: unknown;
  m.handle({
    method: 'GET', path: '/overview', url: new URL('http://x/api/v1/overview'), body: {}, perms: { 'overview.read': 'all' },
    reply: (_s: number, b?: unknown) => {
      out = b;
      return true;
    },
    problem: () => true, text: () => true, needPin: () => false,
  } as never);
  return out as Overview;
}

const UPDATE = {
  current: 'v0.1.41', latest: 'v0.1.42', update_available: true, updater: 'systemd', linked: true, can_request: true,
  state: 'idle', message: null, from: null, to: null, started_at: null, finished_at: null, requested_at: null,
  release_url: null, release_notes: null, checked_at: null,
};

function stub(overview: Overview, update: unknown = { ...UPDATE, latest: 'v0.1.41', update_available: false }) {
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      const json = (status: number, body: unknown) =>
        new Response(JSON.stringify(body), { status, headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json' } });
      if (url.includes('/overview')) return json(200, overview);
      if (url.includes('/system/update')) return json(200, update);
      if (url.includes('/system/health')) return json(200, { overall: 'ok', issues: [], checks: [] });
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
  return calls;
}

const ME_OWNER = {
  id: 'u', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A (Chủ)', role: { code: 'owner', name: 'Owner' },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: permissionsOf('owner'),
};

function renderOverview() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, {
    id: 'u', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A (Chủ)', role: { code: 'owner', name: 'Owner' },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
    addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: permissionsOf('owner'),
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <OverviewScreen />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  appQueryClient.clear();
});

describe('Hôm nay — hàng số chính và Sức khoẻ hệ thống', () => {
  it('máy chủ giả trả đúng hợp đồng: 4 ô row=1 theo thứ tự, health.tech, không còn ô kỹ thuật/plugin', () => {
    const d = mockOverview();
    expect(d.kpis.map((k) => k.key)).toEqual(['opportunity_claim_rate', 'time_to_contact', 'quotations_sent', 'pending_ratio']);
    expect(d.kpis.every((k) => k.row === 1)).toBe(true);
    expect(d.health.tech).toEqual({ channels_live: 4, groups_listening: 42, events_today: 3184, processing_latency_s: 1.2 });
  });

  it('render đúng MỘT hàng 4 ô số; thẻ Sức khoẻ có 4 số kỹ thuật; kicker không đếm plugin; nút "Mở hộp thư"', async () => {
    stub(mockOverview());
    const { container } = renderOverview();
    await screen.findByText('Tỉ lệ cơ hội được nhận');
    const rows = container.querySelectorAll('.ov-kpi-row');
    expect(rows).toHaveLength(1);
    expect(rows[0].querySelectorAll('.ov-kpi')).toHaveLength(4);
    expect(within(rows[0] as HTMLElement).queryByText('Kênh sống')).toBeNull();

    const card = container.querySelector('[data-gen-target="overview.health"]') as HTMLElement;
    expect(card).toBeTruthy();
    const tech = within(card).getAllByTestId('ov-tech-row');
    expect(tech).toHaveLength(4);
    expect(tech.map((r) => r.textContent)).toEqual([
      expect.stringContaining('Kênh đang sống4'),
      expect.stringContaining('Nhóm đang lắng nghe42'),
      expect.stringContaining('Sự kiện 24 giờ qua3.184'),
      expect.stringContaining('Độ trễ xử lý1,2 giây'),
    ]);
    expect(card.textContent).not.toMatch(/khoẻ · .* suy giảm|cách ly|plugin/i);
    expect(screen.getByRole('link', { name: /^Mở hộp thư$/ })).toHaveAttribute('href', '/inbox');
  });

  it('API cũ (không có health.tech): bỏ qua dòng số kỹ thuật, vẫn render', async () => {
    const d = mockOverview();
    const { tech: _drop, ...rest } = d.health;
    void _drop;
    stub({ ...d, health: rest });
    const { container } = renderOverview();
    await screen.findByText('Tỉ lệ cơ hội được nhận');
    expect(container.querySelectorAll('[data-testid="ov-tech-row"]')).toHaveLength(0);
    expect(within(container.querySelector('[data-gen-target="overview.health"]') as HTMLElement).getByText('Tin chờ sàng lọc')).toBeInTheDocument();
  });

  it('v0.1.70 (F143): dòng kênh ở Sức khoẻ có tên đẹp (Telegram), bỏ kênh chỉ đọc LinkedIn, không in khoá thô', async () => {
    const d = mockOverview();
    stub({ ...d, health: { ...d.health, channels: [{ type: 'zalo', active: 1 }, { type: 'telegram', active: 0 }, { type: 'linkedin', active: 0 }] } });
    const { container } = renderOverview();
    await screen.findByText('Tỉ lệ cơ hội được nhận');
    const card = container.querySelector('[data-gen-target="overview.health"]') as HTMLElement;
    expect(within(card).getByText('Kênh Zalo')).toBeInTheDocument();
    expect(within(card).getByText('Kênh Telegram')).toBeInTheDocument();
    expect(card.textContent).not.toMatch(/linkedin/i);
  });

  it('skeleton 4 ô khi đang tải', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    const { container } = renderOverview();
    expect(container.querySelectorAll('.ov-kpi-row .ov-kpi')).toHaveLength(4);
  });
});

describe('Hôm nay — cập nhật: không có thẻ, chỉ một dòng báo', () => {
  it('v0.1.71 (P3-9): vai không có system.manage (Vận hành, Kiểm soát…) KHÔNG gọi GET /system/update', async () => {
    const calls = stub(mockOverview(), UPDATE);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(qk.me, { ...ME_OWNER, role: { code: 'operator', name: 'Vận hành' }, permissions: permissionsOf('operator') });
    const { container } = render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <UpdateNotice />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await new Promise((r) => setTimeout(r, 50));
    expect(calls.some((u) => u.includes('/system/update'))).toBe(false);
    expect(container).toBeEmptyDOMElement();
  });

  it('có bản mới → dòng "Có bản mới v0.1.42 — cập nhật ở Cài đặt" dẫn tới /system?tab=storage; không có thẻ cập nhật', async () => {
    stub(mockOverview(), UPDATE);
    renderOverview();
    const notice = await screen.findByTestId('update-notice');
    expect(notice).toHaveTextContent('Có bản mới v0.1.42 — cập nhật ở Cài đặt');
    expect(notice).toHaveAttribute('href', '/system?tab=storage');
    expect(screen.queryByRole('button', { name: /Cập nhật ngay/ })).toBeNull();
    expect(screen.queryByRole('region', { name: /Cập nhật phiên bản|Cập nhật phần mềm/ })).toBeNull();
  });

  it('đang dùng bản mới nhất → không có dòng báo', async () => {
    const calls = stub(mockOverview());
    renderOverview();
    await screen.findByText('Tỉ lệ cơ hội được nhận');
    await waitFor(() => expect(calls.some((u) => u.includes('/system/update'))).toBe(true));
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId('update-notice')).toBeNull();
  });

  it('UpdateNotice hideFailed (F-6): cập nhật lỗi đã có trong dải "Cần Sếp xử lý" → không nhắc lại', async () => {
    const failed = { ...UPDATE, state: 'failed', message: 'lỗi (GH-E945)', to: 'v0.1.42', finished_at: new Date().toISOString() };
    stub(mockOverview(), failed);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(qk.me, ME_OWNER);
    const shown = render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <UpdateNotice />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByTestId('update-notice')).toHaveTextContent('Cập nhật lên v0.1.42 chưa thành công — xem ở Cài đặt');
    shown.unmount();
    const qc2 = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc2.setQueryData(qk.me, ME_OWNER);
    const hidden = render(
      <QueryClientProvider client={qc2}>
        <MemoryRouter>
          <UpdateNotice hideFailed />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await new Promise((r) => setTimeout(r, 30));
    expect(hidden.container).toBeEmptyDOMElement();
  });
});
