import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { TriageSettings, TriageSummary } from '@gen-harness/contracts';
import { TriageCard } from '../../src/screens/system/TriageCard';
import { TRIAGE_LEVELS, triageLevelOf } from '../../src/screens/system/triageModel';
import { queryClient } from '../../src/lib/queryClient';

/**
 * v0.1.43 (F-30): thẻ "Lọc tin" — mức Thấp/Vừa/Cao = min_score 15/30/50. Chỉ gửi PATCH khi Owner chọn mức KHÁC
 * mức đang nhấn; mở/đóng "Nâng cao" không ghi gì; vai trò khác chỉ xem.
 */

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

interface Call {
  url: string;
  method: string;
  body: unknown;
}

const SUMMARY: TriageSummary = {
  days: 7, enabled: true, min_score: 30, use_jev: false, total: 3, kept: 2, duplicates: 0, exact_duplicates: 0,
  near_duplicates: 0, spam: 1, low_score: 0, pending: 0, avg_quality: 50,
  jev: { count: 0, heuristic_count: 3, avg_latency_ms: null, spam_agreement: null },
};

function setup(role: string, minScore: number) {
  let settings: TriageSettings = { enabled: true, min_score: minScore, use_jev: false };
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const c = { url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : null };
      calls.push(c);
      if (c.url.includes('/auth/me')) return json(200, { id: 'me', role: { code: role, name: role } });
      if (c.url.includes('/refinery/triage/settings')) {
        if (c.method === 'PATCH') settings = { ...settings, ...(c.body as object) };
        return json(200, settings);
      }
      if (c.url.includes('/refinery/triage/summary')) return json(200, SUMMARY);
      return json(404);
    }),
  );
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <TriageCard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const writes = () => calls.filter((c) => c.method !== 'GET');
  return { calls, writes };
}

const level = (name: string) => screen.getByRole('button', { name });

afterEach(() => {
  vi.unstubAllGlobals();
  queryClient.clear();
});

describe('triageLevelOf', () => {
  it('15/30/50 → low/medium/high, giá trị khác → custom', () => {
    expect(triageLevelOf(15)).toBe('low');
    expect(triageLevelOf(30)).toBe('medium');
    expect(triageLevelOf(50)).toBe('high');
    expect(triageLevelOf(42)).toBe('custom');
    expect(TRIAGE_LEVELS.map((l) => l.label)).toEqual(['Thấp', 'Vừa', 'Cao']);
  });
});

describe('<TriageCard> mức lọc', () => {
  it('nói đúng: lọc chỉ ĐÁNH DẤU, ẩn khỏi Hộp thư cần bật "Ẩn rác & trùng" — có link /inbox?hide=1', async () => {
    setup('owner', 30);
    expect(await screen.findByText(/Đánh dấu tin trùng, rác, điểm thấp/)).toBeInTheDocument();
    expect(screen.queryByText(/Ẩn tin trùng, tin rác và tin điểm thấp khỏi Hộp thư/)).toBeNull();
    expect(await screen.findByRole('link', { name: /Ẩn rác & trùng/ })).toHaveAttribute('href', '/inbox?hide=1');
  });

  it('Owner, min_score=30: "Vừa" nhấn; bấm "Cao" → PATCH { min_score: 50 }', async () => {
    const { writes } = setup('owner', 30);
    expect(await screen.findByText('Lọc tin')).toBeInTheDocument();
    await waitFor(() => expect(level('Vừa')).toHaveAttribute('aria-pressed', 'true'));
    expect(level('Thấp')).toHaveAttribute('aria-pressed', 'false');
    expect(level('Cao')).toHaveAttribute('aria-pressed', 'false');
    await waitFor(() => expect(level('Cao')).not.toBeDisabled());
    expect(writes()).toEqual([]);
    await userEvent.click(level('Cao'));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'PATCH', body: { min_score: 50 } });
    await waitFor(() => expect(level('Cao')).toHaveAttribute('aria-pressed', 'true'));
  });

  it('bấm "Vừa" khi đang Vừa → không request; mở rồi đóng "Nâng cao" → không request', async () => {
    const { writes } = setup('owner', 30);
    await waitFor(() => expect(level('Vừa')).toHaveAttribute('aria-pressed', 'true'));
    await waitFor(() => expect(level('Vừa')).not.toBeDisabled());
    await userEvent.click(level('Vừa'));
    const summary = screen.getByText('Nâng cao — Jev và ngưỡng điểm');
    const details = summary.closest('details')!;
    expect(details).toHaveClass('brain-advanced');
    expect(details).not.toHaveAttribute('open');
    await userEvent.click(summary);
    expect(details).toHaveAttribute('open');
    expect(screen.getByLabelText('Ngưỡng điểm (0–100)')).toBeVisible();
    expect(screen.getByRole('switch', { name: 'Dùng Jev để chấm' })).toBeVisible();
    await userEvent.click(summary);
    expect(details).not.toHaveAttribute('open');
    await new Promise((r) => setTimeout(r, 50));
    expect(writes()).toEqual([]);
  });

  it('ngưỡng tuỳ chỉnh (42): không nút nào nhấn, có dòng "Đang dùng ngưỡng tuỳ chỉnh (42 điểm)"', async () => {
    setup('owner', 42);
    expect(await screen.findByText('Đang dùng ngưỡng tuỳ chỉnh (42 điểm) — xem Nâng cao')).toBeInTheDocument();
    for (const name of ['Thấp', 'Vừa', 'Cao']) expect(level(name)).toHaveAttribute('aria-pressed', 'false');
  });

  it('vai trò khác: nút mức lọc tắt, không request', async () => {
    const { writes } = setup('manager', 30);
    expect(await screen.findByText(/Chỉ Sếp \(Owner\)/)).toBeInTheDocument();
    for (const name of ['Thấp', 'Vừa', 'Cao']) expect(level(name)).toBeDisabled();
    await userEvent.click(level('Thấp'));
    expect(writes()).toEqual([]);
  });
});
