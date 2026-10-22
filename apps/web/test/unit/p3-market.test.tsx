import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import type {
  CasePage,
  DealPage,
  MatchPage,
  MarketSignalPage,
  OpportunityPage,
  OpportunityPipeline,
  SearchPage,
} from '@gen-harness/contracts';
import { DealsScreen } from '../../src/screens/market/DealsScreen';
import { OpportunityScreen } from '../../src/screens/market/OpportunityScreen';
import { SearchScreen } from '../../src/screens/market/SearchScreen';
import { SupplyScreen } from '../../src/screens/market/SupplyScreen';
import { queryClient } from '../../src/lib/queryClient';

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

const BAO = { id: 'p-bao', code: 'PER-0042', name: 'Nguyễn Văn Bảo', type: 'customer' as const, org_name: 'Công ty in Thành Phát' };
const HAU = { id: 'p-hau', code: 'PER-0311', name: 'Trần Văn Hậu', type: 'customer' as const, org_name: 'Xưởng gỗ Bình Dương' };

describe('Bảng cơ hội', () => {
  const OPPS: OpportunityPage = {
    items: [
      {
        id: 'opp-1', code: 'OPP-1842', need: '3 container ván MDF E1 17mm', stage: 'validated', value_vnd: 1_200_000_000,
        confidence: 'high', heat: 91, person: HAU, group: null, owner: null, first_signal_at: '2026-09-24T00:00:00Z',
        first_contact_at: null, closed_at: null, created_at: '2026-09-24T00:00:00Z', updated_at: '2026-09-24T00:00:00Z',
        suggested_match: null, risk_note: null,
      },
      {
        id: 'opp-2', code: 'OPP-1815', need: 'Hợp đồng in ấn quý 4', stage: 'negotiating', value_vnd: 84_000_000,
        confidence: 'high', heat: 87, person: BAO, group: null, owner: null, first_signal_at: '2026-09-24T00:00:00Z',
        first_contact_at: null, closed_at: null, created_at: '2026-09-24T00:00:00Z', updated_at: '2026-09-24T00:00:00Z',
        suggested_match: null, risk_note: 'Chưa tiếp cận sau 24 giờ kể từ tín hiệu đầu tiên — dễ mất vào tay đối thủ',
      },
    ],
    next_cursor: null,
    total: 2,
  };
  const PIPELINE: OpportunityPipeline = {
    stages: [
      { stage: 'raw_signal', count: 0, value_vnd: 0 },
      { stage: 'validated', count: 1, value_vnd: 1_200_000_000 },
      { stage: 'matched', count: 0, value_vnd: 0 },
      { stage: 'approaching', count: 0, value_vnd: 0 },
      { stage: 'negotiating', count: 1, value_vnd: 84_000_000 },
      { stage: 'handed_off', count: 0, value_vnd: 0 },
      { stage: 'won', count: 0, value_vnd: 0 },
      { stage: 'lost', count: 0, value_vnd: 0 },
      { stage: 'dormant', count: 0, value_vnd: 0 },
    ],
    open_pipeline_value_vnd: 1_284_000_000,
    open_pipeline_count: 2,
  };

  it('hiện cột theo giai đoạn, tổng pipeline, và rủi ro; chuyển giai đoạn bằng bàn phím ghi PATCH /stage', async () => {
    const calls = mockFetch((c) => {
      if (c.url.includes('/opportunities/pipeline')) return json(200, PIPELINE);
      if (c.method === 'PATCH' && /\/opportunities\/opp-1\/stage$/.test(c.url)) {
        const body = c.body as { to_stage: string };
        return json(200, { ...OPPS.items[0], stage: body.to_stage, stage_history: [] });
      }
      if (c.url.includes('/opportunities')) return json(200, OPPS);
      return json(404);
    });
    const user = userEvent.setup();
    renderScreen(<OpportunityScreen />);

    expect(await screen.findByText('3 container ván MDF E1 17mm')).toBeInTheDocument();
    expect(screen.getByText('Hợp đồng in ấn quý 4')).toBeInTheDocument();
    expect(screen.getByText(/dễ mất vào tay đối thủ/)).toBeInTheDocument();
    expect(screen.getByText('1.284.000.000 ₫')).toBeInTheDocument();

    const validatedCol = screen.getByText('Đã xác thực').closest('.opp-col') as HTMLElement;
    expect(within(validatedCol).getByText('1')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Chuyển giai đoạn cho OPP-1842/ }));
    const dlg = screen.getByRole('dialog', { name: 'Chuyển sang giai đoạn…' });
    await user.click(within(dlg).getByText('Đã ráp khớp'));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH' && c.url.includes('/opportunities/opp-1/stage'))).toBe(true));
    const patchCall = calls.find((c) => c.method === 'PATCH' && c.url.includes('/opportunities/opp-1/stage'))!;
    expect(patchCall.body).toEqual({ to_stage: 'matched' });
  });
});

describe('Cung ↔ Cầu', () => {
  const DEMAND: MarketSignalPage = {
    items: [{ id: 'sig-d', side: 'demand', item: '3 cont ván MDF E1', category: 'gỗ', quantity: 3, unit: 'container', value_vnd: 1_200_000_000, location: 'Bình Dương', needed_by: null, heat: 91, status: 'open', created_at: '2026-09-24T00:00:00Z', person: HAU, group: null }],
    next_cursor: null,
    total: 1,
  };
  const SUPPLY: MarketSignalPage = {
    items: [{ id: 'sig-s', side: 'supply', item: '6 cont ván E1 tồn kho', category: 'gỗ', quantity: 6, unit: 'container', value_vnd: 2_300_000_000, location: 'Bình Dương', needed_by: null, heat: 84, status: 'open', created_at: '2026-09-24T00:00:00Z', person: null, group: null }],
    next_cursor: null,
    total: 1,
  };
  const MATCHES: MatchPage = {
    items: [
      {
        id: 'match-1', score: 94, reasons: ['Cùng mặt hàng: "3 cont ván MDF E1" ~ "6 cont ván E1 tồn kho" (+50)', 'Cùng khu vực: Bình Dương (+10)'],
        status: 'suggested', opportunity_id: null, created_at: '2026-09-24T00:00:00Z',
        demand: { id: 'sig-d', item: '3 cont ván MDF E1', person: HAU, group: null },
        supply: { id: 'sig-s', item: '6 cont ván E1 tồn kho', person: null, group: null },
      },
    ],
    next_cursor: null,
    total: 1,
  };

  function LocationProbe() {
    const loc = useLocation();
    return <div data-testid="loc">{loc.pathname + loc.search}</div>;
  }

  it('hiện lý do ghép, và "Giới thiệu hai bên" tạo bản nháp rồi điều hướng sang Bàn làm việc', async () => {
    mockFetch((c) => {
      if (c.url.includes('side=demand')) return json(200, DEMAND);
      if (c.url.includes('side=supply')) return json(200, SUPPLY);
      if (c.method === 'POST' && c.url.includes('/matches/match-1/introduce')) {
        return json(200, { ok: true, opportunity_id: 'opp-new', draft: { id: 'draft-99', code: 'ACT-0599', status: 'pending', outcome: 'held', hold_reason: null } });
      }
      if (c.url.includes('/matches')) return json(200, MATCHES);
      return json(404);
    });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/supply']}>
          <Routes>
            <Route path="/supply" element={<SupplyScreen />} />
            <Route path="/workbench" element={<div>Bàn làm việc mở</div>} />
          </Routes>
          <LocationProbe />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText(/Cùng mặt hàng.*\+50/)).toBeInTheDocument();
    expect(screen.getByText(/Cùng khu vực: Bình Dương/)).toBeInTheDocument();
    expect(screen.getByText('khớp 94')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Giới thiệu hai bên' }));
    await waitFor(() => expect(screen.getByText('Bàn làm việc mở')).toBeInTheDocument());
    expect(screen.getByTestId('loc')).toHaveTextContent('/workbench?id=draft-99');
  });
});

describe('Kho hội thoại', () => {
  const RESULTS: SearchPage = {
    items: [
      { person: HAU, match_count: 3, last_at: '2026-09-24T00:00:00Z', last_snippet: 'Hỏi giá ván MDF', last_event_type: 'AskedPrice', evidence: { type: 'meaning_unit', id: 'mu-1' } },
      { person: BAO, match_count: 1, last_at: '2026-09-20T00:00:00Z', last_snippet: 'Than phiền giao trễ', last_event_type: 'Complained', evidence: { type: 'meaning_unit', id: 'mu-2' } },
    ],
    next_cursor: null,
    total: 2,
    facets: { event_type: [{ value: 'AskedPrice', count: 1 }, { value: 'Complained', count: 1 }], channel: [{ value: 'zalo', count: 2 }] },
  };
  const FILTERED: SearchPage = { ...RESULTS, items: [RESULTS.items[0]], total: 1 };

  it('lọc theo facet, và hành động hàng loạt gọi POST /search/bulk cho những người đã chọn', async () => {
    const calls = mockFetch((c) => {
      if (c.method === 'POST' && c.url.includes('/search/bulk')) return json(200, { ok: true, count: 1 });
      if (c.url.includes('event_type=AskedPrice')) return json(200, FILTERED);
      if (c.url.includes('/search')) return json(200, RESULTS);
      return json(404);
    });
    const user = userEvent.setup();
    renderScreen(<SearchScreen />);

    expect(await screen.findByText('Trần Văn Hậu')).toBeInTheDocument();
    expect(screen.getByText('Nguyễn Văn Bảo')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Hỏi giá \/ nhu cầu mua · 1/ }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('event_type=AskedPrice'))).toBe(true));
    expect(await screen.findByText('Trần Văn Hậu')).toBeInTheDocument();
    expect(screen.queryByText('Nguyễn Văn Bảo')).not.toBeInTheDocument();

    await user.click(screen.getByLabelText('Chọn Trần Văn Hậu'));
    const bulkBtn = screen.getByRole('button', { name: /Hành động hàng loạt \(1\)/ });
    await user.click(bulkBtn);
    const dlg = screen.getByRole('dialog', { name: 'Hành động hàng loạt' });
    await user.type(within(dlg).getByLabelText('Nội dung ghi vào sổ tay'), 'Đang so sánh giá với đối thủ');
    await user.click(within(dlg).getByRole('button', { name: /Áp dụng cho 1 người/ }));

    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.includes('/search/bulk'))).toBe(true));
    const bulkCall = calls.find((c) => c.method === 'POST' && c.url.includes('/search/bulk'))!;
    expect(bulkCall.body).toMatchObject({ person_ids: ['p-hau'], action: 'tag', text: 'Đang so sánh giá với đối thủ' });
  });
});

describe('Deal & Vụ việc', () => {
  const DEALS: DealPage = {
    items: [{ id: 'deal-1', code: 'DEA-0091', opportunity_id: null, person: BAO, amount_vnd: 84_000_000, status: 'open', won_at: null, erp_ref: null, created_at: '2026-09-24T00:00:00Z', updated_at: '2026-09-24T00:00:00Z' }],
    next_cursor: null,
    total: 1,
  };
  const CASES: CasePage = { items: [], next_cursor: null, total: 0 };

  it('đổi trạng thái deal từ Đang mở sang Đã chốt qua PATCH /deals/{id}', async () => {
    const calls = mockFetch((c) => {
      if (c.method === 'PATCH' && c.url.includes('/deals/deal-1')) return json(200, { ...DEALS.items[0], status: 'won', won_at: '2026-09-24T01:00:00Z' });
      if (c.url.includes('/deals')) return json(200, DEALS);
      if (c.url.includes('/cases')) return json(200, CASES);
      if (c.url.includes('/opportunities')) return json(200, { items: [], next_cursor: null, total: 0 });
      return json(404);
    });
    const user = userEvent.setup();
    renderScreen(<DealsScreen />);

    expect(await screen.findByText('DEA-0091')).toBeInTheDocument();
    const row = screen.getByText('DEA-0091').closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: 'Đã chốt' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH' && c.url.includes('/deals/deal-1'))).toBe(true));
    const patchCall = calls.find((c) => c.method === 'PATCH' && c.url.includes('/deals/deal-1'))!;
    expect(patchCall.body).toEqual({ status: 'won' });
  });
});
