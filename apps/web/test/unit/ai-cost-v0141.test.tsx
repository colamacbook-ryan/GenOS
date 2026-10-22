import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { AiCost } from '@gen-harness/contracts';
import { AiCostPanel } from '../../src/screens/queue/AiCostPanel';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';
import { HEALTH_KINDS } from '../../src/screens/system/queries';

/**
 * v0.1.41 (F-84): "Chi phí AI hôm nay" ở Tổng quan › Sức khoẻ — khuôn `GET /system/ai-cost` (hợp đồng JSON giữa các gói).
 * Mọi trường là chuỗi/số/bool/null; tiền là số nguyên VND, phân cách nghìn bằng dấu chấm + " ₫".
 */

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json' } });

function meWith(perms: Record<string, string>) {
  return {
    id: 'u', email: 'owner@genesis.local', display_name: 'Owner', role: { code: 'owner', name: 'Owner' },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
    pin_verified_until: null, permissions: perms,
  };
}

function cost(over: Partial<AiCost> = {}): AiCost {
  return {
    date: '2026-10-02', timezone: 'Asia/Ho_Chi_Minh', total_vnd: 12_500, budget_vnd: 10_000, over_budget: true, unpriced_calls: 3,
    agents: [
      { agent_key: 'core.refinery', label: 'Sàng lọc & suy luận chính', calls: 30, tokens_in: 200_000, tokens_out: 48_000, cost_vnd: 4_500, unpriced_calls: 0 },
      { agent_key: 'duty.decide', label: 'Trực việc', calls: 3, tokens_in: 30_000, tokens_out: 6_000, cost_vnd: 0, unpriced_calls: 3 },
      { agent_key: 'core.gen', label: 'Gen — trợ lý quản trị', calls: 17, tokens_in: 460_000, tokens_out: 89_000, cost_vnd: 8_000, unpriced_calls: 0 },
    ],
    models: [
      { model_id: 'm1', provider_name: 'Gemini API', provider_kind: 'gemini', model_name: 'gemini-2.5-flash', in_vnd_per_mtok: 7500, out_vnd_per_mtok: 62500, price_source: 'owner', calls_today: 42 },
      { model_id: 'm2', provider_name: 'Claude Code CLI', provider_kind: 'claude_code_cli', model_name: 'claude-sonnet', in_vnd_per_mtok: 0, out_vnd_per_mtok: 0, price_source: 'subscription', calls_today: 5 },
    ],
    last_7_days: [{ date: '2026-10-02', total_vnd: 12_500 }],
    feedback_7d: { helpful: 14, not_helpful: 3, briefing_helpful: 5, briefing_not_helpful: 1 },
    ...over,
  };
}

function mockFetch(handler: (url: string) => Response | undefined) {
  const urls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL) => {
      urls.push(String(url));
      return handler(String(url)) ?? json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tìm thấy' });
    }),
  );
  return urls;
}

function renderPanel(perms: Record<string, string> = { 'system.read': 'all' }) {
  queryClient.setQueryData(qk.me, meWith(perms));
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AiCostPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  queryClient.clear();
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } });
});
afterEach(() => vi.unstubAllGlobals());

describe('<AiCostPanel> Chi phí AI hôm nay', () => {
  it('hiện tổng 12.500 ₫, trần, Vượt trần, bảng agent sắp giảm dần, dòng chưa có giá, Hữu ích 7 ngày, CLI trả theo gói', async () => {
    const urls = mockFetch((u) => (u.includes('/system/ai-cost') ? json(200, cost()) : undefined));
    renderPanel({ 'system.read': 'all', 'system.manage': 'all' });
    const panel = await screen.findByRole('region', { name: 'Chi phí AI hôm nay' });
    expect(await within(panel).findByTestId('ai-cost-total')).toHaveTextContent('12.500 ₫');
    expect(panel).toHaveTextContent('12.500 ₫ / trần 10.000 ₫');
    expect(within(panel).getByText('Vượt trần')).toBeInTheDocument();
    const rows = within(panel).getAllByTestId(/^ai-cost-agent-/);
    expect(rows.map((r) => r.getAttribute('data-testid'))).toEqual(['ai-cost-agent-core.gen', 'ai-cost-agent-core.refinery', 'ai-cost-agent-duty.decide']);
    expect(rows[0]).toHaveTextContent('Gen — trợ lý quản trị17' + '8.000 ₫');
    expect(panel).toHaveTextContent('3 lượt gọi chưa có giá — nhập giá ở Bộ não AI');
    expect(within(panel).getByRole('link', { name: 'nhập giá ở Bộ não AI' })).toHaveAttribute('href', '/system?tab=brain');
    expect(panel).toHaveTextContent('Hữu ích 7 ngày: 14/17');
    expect(panel).toHaveTextContent('Claude Code CLI: 5 lượt — trả theo gói (0 ₫)');
    expect(within(panel).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
    expect(urls.some((u) => u.includes('/system/ai-cost'))).toBe(true);
  });

  it('Auditor (system.read, không system.manage) ⇒ không có link nhập giá (ngõ cụt), chỉ câu báo Owner', async () => {
    mockFetch((u) => (u.includes('/system/ai-cost') ? json(200, cost()) : undefined));
    renderPanel({ 'system.read': 'all' });
    const panel = await screen.findByRole('region', { name: 'Chi phí AI hôm nay' });
    await within(panel).findByTestId('ai-cost-total');
    expect(panel).toHaveTextContent('3 lượt gọi chưa có giá — báo Owner nhập giá ở Bộ não AI');
    expect(within(panel).queryByRole('link')).toBeNull();
  });

  it('chưa ai đánh giá ⇒ "Hữu ích 7 ngày: chưa có đánh giá" (không "0/0")', async () => {
    mockFetch((u) =>
      u.includes('/system/ai-cost') ? json(200, cost({ feedback_7d: { helpful: 0, not_helpful: 0, briefing_helpful: 0, briefing_not_helpful: 0 } })) : undefined,
    );
    renderPanel();
    const panel = await screen.findByRole('region', { name: 'Chi phí AI hôm nay' });
    await within(panel).findByTestId('ai-cost-total');
    expect(panel).toHaveTextContent('Hữu ích 7 ngày: chưa có đánh giá');
    expect(panel).not.toHaveTextContent('0/0');
  });

  it('chưa đặt trần ⇒ "chưa đặt trần", không thanh tiến độ, không Vượt trần', async () => {
    mockFetch((u) => (u.includes('/system/ai-cost') ? json(200, cost({ budget_vnd: null, over_budget: false, unpriced_calls: 0 })) : undefined));
    renderPanel();
    const panel = await screen.findByRole('region', { name: 'Chi phí AI hôm nay' });
    await within(panel).findByTestId('ai-cost-total');
    expect(panel).toHaveTextContent('12.500 ₫ · chưa đặt trần');
    expect(within(panel).queryByRole('progressbar')).toBeNull();
    expect(within(panel).queryByText('Vượt trần')).toBeNull();
    expect(panel).not.toHaveTextContent('chưa có giá');
  });

  it('lỗi API ⇒ câu thân thiện, không "[object Object]"', async () => {
    mockFetch((u) =>
      u.includes('/system/ai-cost')
        ? json(500, { status: 500, code: 'INTERNAL', title: 'Hệ thống gặp lỗi khi xử lý yêu cầu — đã ghi nhật ký', detail: { reasons: ['x'] }, error_id: 'E-123' })
        : undefined,
    );
    renderPanel();
    const panel = await screen.findByRole('region', { name: 'Chi phí AI hôm nay' });
    await waitFor(() => expect(panel).toHaveTextContent('Hệ thống gặp lỗi khi xử lý yêu cầu'));
    expect(panel).not.toHaveTextContent('[object Object]');
  });

  it('người không có system.read ⇒ không hiện panel, không gọi API', async () => {
    const urls = mockFetch(() => undefined);
    const { container } = renderPanel({ 'overview.read': 'all' });
    await new Promise((r) => setTimeout(r, 20));
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText('Chi phí AI hôm nay')).toBeNull();
    expect(urls.some((u) => u.includes('/system/ai-cost'))).toBe(false);
  });

  it('HEALTH_KINDS có ai.budget_exceeded và ai.background_no_source', () => {
    expect(HEALTH_KINDS.has('ai.budget_exceeded')).toBe(true);
    expect(HEALTH_KINDS.has('ai.background_no_source')).toBe(true);
  });
});
