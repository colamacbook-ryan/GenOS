import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { CleanItem, Notebook, Rule } from '@gen-harness/contracts';
import { RulesScreen } from '../../src/screens/data/RulesScreen';
import { MemoryCard } from '../../src/screens/data/CleanScreen';
import { LegacyFilterNotice, LEGACY_READONLY_REASON } from '../../src/screens/data/LegacyFilterNotice';
import { qk } from '../../src/lib/queries';
import { qk2 } from '../../src/lib/dataQueries';

/** v0.1.78 (G9): Quy tắc sàng lọc + Kho sạch là "đường lọc cũ" — có dải giải thích, nút sửa/chạy disabled kèm tooltip (không ẩn). */

const RULE = {
  id: 'r1', code: 'R-01', name: 'Giá', kind: 'risk', kind_label: 'Rủi ro', enabled: true, threshold: 0.6, hits: 3,
  conditions: [], outputs: [],
} as unknown as Rule;
const ROW: CleanItem = {
  id: 'c1', observed_at: '2026-10-01T01:00:00Z', group: null, person: { id: 'p1', code: 'P-1', name: 'Chị Hà' },
  event_type: 'Complained', conclusion: 'x', score: 50, confidence: 0.9, cycle_at: '2026-10-01T01:00:00Z', raw_event_ids: [],
};
const NB: Notebook = {
  id: 'nb1', subject: { type: 'person', id: 'p1', code: 'P-1', name: 'Chị Hà' }, token_used: 10, token_budget: 100,
  compaction_no: 0, last_compacted_at: null, sections: [],
};
const ME = {
  id: 'u', email: 'x@genesis.local', display_name: 'X', role: { code: 'owner', name: 'Owner' },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: { 'data.read': 'all', 'data.manage': 'all' },
};

function stubFetch() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.includes('/notebooks/') ? NB : url.includes('/auth/me') ? ME : url.includes('/raw') ? { items: [], total: 0 } : [];
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }),
  );
}
function client() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  qc.setQueryData(qk.me, ME);
  return qc;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('đường lọc cũ (G9)', () => {
  it('dải giải thích nói rõ chạy song song, Tình báo không dựa vào, sẽ xem xét bỏ', () => {
    render(<LegacyFilterNotice screen="rules" />);
    const note = screen.getByTestId('legacy-filter-notice');
    expect(note).toHaveTextContent('Đường lọc cũ');
    expect(note).toHaveTextContent('chạy song song');
    expect(note).toHaveTextContent('Tình báo không dựa vào');
    expect(note).toHaveTextContent('xem xét bỏ ở bản sau');
  });

  it('Quy tắc sàng lọc: Owner vẫn thấy nút nhưng disabled + tooltip lý do; công tắc không bấm được', () => {
    stubFetch();
    const qc = client();
    qc.setQueryData(qk2.rules, [RULE]);
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <RulesScreen />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByTestId('legacy-filter-notice')).toBeInTheDocument();
    for (const name of [/Chạy thử trên 100 bản ghi/, /Thêm quy tắc/]) {
      const b = screen.getByRole('button', { name });
      expect(b).toBeDisabled();
      expect(b).toHaveAttribute('title', LEGACY_READONLY_REASON);
    }
    expect(screen.getByRole('switch', { name: /quy tắc R-01/ })).toHaveAttribute('aria-disabled', 'true');
    // Xem chi tiết (chỉ đọc) vẫn mở được.
    expect(screen.getByRole('button', { name: /Xem quy tắc R-01/ })).toBeEnabled();
    expect(screen.queryByRole('button', { name: /Sửa quy tắc/ })).toBeNull();
  });

  it('Kho sạch: «Sếp ghim thêm» disabled + tooltip, không ẩn', async () => {
    stubFetch();
    render(
      <QueryClientProvider client={client()}>
        <MemoryCard row={ROW} loading={false} tz="Asia/Ho_Chi_Minh" />
      </QueryClientProvider>,
    );
    const b = await screen.findByRole('button', { name: /Sếp ghim thêm/ });
    expect(b).toBeDisabled();
    expect(b).toHaveAttribute('title', LEGACY_READONLY_REASON);
  });
});
