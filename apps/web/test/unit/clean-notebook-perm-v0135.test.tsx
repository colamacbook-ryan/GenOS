import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { CleanItem, Notebook } from '@gen-harness/contracts';
import { MemoryCard } from '../../src/screens/data/CleanScreen';
import { qk } from '../../src/lib/queries';

/** v0.1.35 (F-15): nút ghi Sổ tay («Sếp ghim thêm») theo quyền data.manage — khớp backend NB_WRITE. */

const ROW: CleanItem = {
  id: 'c1', observed_at: '2026-10-01T01:00:00Z', group: null, person: { id: 'p1', code: 'P-1', name: 'Chị Hà' },
  event_type: 'Complained', conclusion: 'x', score: 50, confidence: 0.9, cycle_at: '2026-10-01T01:00:00Z', raw_event_ids: [],
};
const NB: Notebook = {
  id: 'nb1', subject: { type: 'person', id: 'p1', code: 'P-1', name: 'Chị Hà' }, token_used: 10, token_budget: 100,
  compaction_no: 0, last_compacted_at: null, sections: [],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderWith(permissions: Record<string, string>) {
  const me = {
    id: 'u', email: 'x@genesis.local', display_name: 'X', role: { code: 'custom', name: 'Tuỳ biến' },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
    addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions,
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const body = String(input).includes('/auth/me') ? me : String(input).includes('/notebooks/') ? NB : {};
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  qc.setQueryData(qk.me, me);
  render(
    <QueryClientProvider client={qc}>
      <MemoryCard row={ROW} loading={false} tz="Asia/Ho_Chi_Minh" />
    </QueryClientProvider>,
  );
}

describe('<MemoryCard> quyền ghi Sổ tay', () => {
  it('profile.write mà KHÔNG có data.manage → không hiện «Sếp ghim thêm» (backend sẽ 403)', async () => {
    renderWith({ 'data.read': 'all', 'profile.write': 'all' });
    expect(await screen.findByRole('button', { name: /Lịch sử nén/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Sếp ghim thêm/ })).not.toBeInTheDocument();
  });

  it('data.manage → hiện «Sếp ghim thêm»', async () => {
    renderWith({ 'data.read': 'all', 'data.manage': 'all' });
    expect(await screen.findByRole('button', { name: /Sếp ghim thêm/ })).toBeInTheDocument();
  });
});
