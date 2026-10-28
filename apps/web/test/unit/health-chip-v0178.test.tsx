/** v0.1.78 (G0): chip sức khoẻ ở header — sự cố hệ thống gom một chỗ, không trộn vào cảnh báo tình báo. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { qk } from '../../src/lib/queries';
import { HealthChip } from '../../src/shell/HealthChip';

const ISSUES = [
  { key: 'bridge.silent', kind: 'bridge.silent', severity: 'warn', title: 'Zalo im 6 phút', body: '', link: '/system?tab=channels', action: 'Mở kênh', raised_at: '2026-10-28T01:00:00Z' },
  { key: 'model.chain', kind: 'model.chain_exhausted', severity: 'bad', title: 'Hết chuỗi model', body: '', link: null, action: '', raised_at: '2026-10-28T02:00:00Z' },
];

function mount(issues: unknown[] | 'error', perms: Record<string, string> = { 'system.read': 'all' }) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/system/health')) {
        if (issues === 'error') return new Response(JSON.stringify({ title: 'x' }), { status: 500, headers: { 'Content-Type': 'application/problem+json' } });
        return new Response(JSON.stringify({ issues, queues: [], crons: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response('{}', { status: 404, headers: { 'Content-Type': 'application/problem+json' } });
    }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, {
    id: 'u', email: 'a@b.c', display_name: 'A', role: { code: 'owner', name: 'o' },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
    pin_verified_until: null, permissions: perms,
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <HealthChip />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
afterEach(() => vi.unstubAllGlobals());

describe('HealthChip', () => {
  it('không sự cố ⇒ "Hệ thống ổn"', async () => {
    mount([]);
    expect(await screen.findByTestId('health-chip')).toHaveTextContent('Hệ thống ổn');
    expect(screen.getByTestId('health-chip')).toHaveAttribute('data-tone', 'ok');
  });

  it('có sự cố ⇒ đếm, đỏ khi có mức bad; bấm mở danh sách với nút tới chỗ xử lý (sự cố bad trước)', async () => {
    const user = userEvent.setup();
    mount(ISSUES);
    const chip = await screen.findByTestId('health-chip');
    expect(chip).toHaveTextContent('2 sự cố');
    expect(chip).toHaveAttribute('data-tone', 'bad');
    await user.click(chip);
    const menu = screen.getByTestId('health-menu');
    const rows = menu.querySelectorAll('.hd-health__item');
    expect(rows[0]).toHaveTextContent('Hết chuỗi model');
    expect(rows[1]).toHaveTextContent('Zalo im 6 phút');
    expect(screen.getByRole('link', { name: 'Mở kênh' })).toHaveAttribute('href', '/system?tab=channels');
    expect(screen.getByRole('link', { name: 'Mở Sức khoẻ hệ thống' })).toHaveAttribute('href', '/admin/backup?focus=health');
    await user.keyboard('{Escape}');
    expect(screen.queryByTestId('health-menu')).toBeNull();
  });

  it('không có system.read ⇒ không vẽ; lỗi tải ⇒ không báo sai là "ổn"', async () => {
    mount([], {});
    expect(screen.queryByTestId('health-chip')).toBeNull();
  });

  it('lỗi tải sức khoẻ ⇒ không vẽ chip', async () => {
    mount('error');
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId('health-chip')).toBeNull();
  });
});
