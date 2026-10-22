import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { BossCheck, BossCheckKey, BossOverview } from '@gen-harness/contracts';
import { BossChecksPage } from '../../src/guide/BossChecksPage';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';

/**
 * v0.1.47 (F-79) — dòng 8 "Facebook trả lời" (không bắt buộc, không có nút chạy kiểm): hướng dẫn 4 bước, chưa đạt →
 * nút mở /social, đạt → "Đạt" + thời điểm. v0.1.55: dòng 8 vẫn không bắt buộc (chip "Không bắt buộc" thay cho chữ trong tên dòng);
 * `required_total` do máy chủ trả (chỉ dòng 0 "nguồn AI" bắt buộc ⇒ 1).
 */

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const me = {
  id: 'u', email: 'x@genesis.local', display_name: 'Anh Cơ', role: { code: 'owner', name: 'owner' },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
  permissions: { 'system.read': 'all', 'system.manage': 'all' },
};

const EMPTY: Record<string, BossCheck | null> = {
  hub: null, facebook: null, agy_login: null, agy_call: null, agy_switch: null, claude_login: null, claude_call: null,
  jev: null, telegram: null, remote_access: null, facebook_reply: null, ai_source: null,
};
const ROWS: BossOverview['rows'] = [
  { row: 0, key: 'ai', title: 'Có ít nhất 1 nguồn AI chạy được', optional: false, checks: ['ai_source'], done: false },
  { row: 1, key: 'hub', title: 'Nối Gen-hub', optional: true, checks: ['hub'], done: false },
  { row: 2, key: 'facebook', title: 'Kết nối Facebook', optional: true, checks: ['facebook'], done: false },
  { row: 3, key: 'agy', title: 'Google', optional: true, checks: ['agy_login', 'agy_call'], done: false },
  { row: 4, key: 'claude', title: 'Claude Code', optional: true, checks: ['claude_login', 'claude_call'], done: false },
  { row: 5, key: 'jev', title: 'Jev', optional: true, checks: ['jev'], done: false },
  { row: 6, key: 'telegram', title: 'Telegram (báo động & bản tin)', optional: true, checks: ['telegram'], done: false },
  { row: 7, key: 'remote', title: 'Truy cập từ xa', optional: true, checks: ['remote_access'], done: false },
  { row: 8, key: 'facebook_reply', title: 'Facebook trả lời', optional: true, checks: ['facebook_reply'], done: false },
];

const pass = (key: BossCheckKey): BossCheck => ({
  key, status: 'pass', error_code: null, message: null, detail: {}, checked_at: '2026-10-03T07:05:00Z', runs: 1,
});

function setup(opts: { reply?: BossCheck | null; withRow?: boolean } = {}) {
  const calls: string[] = [];
  const rows = (opts.withRow ?? true) ? ROWS.map((r) => (r.row === 8 ? { ...r, done: opts.reply?.status === 'pass' } : r)) : ROWS.slice(0, 8);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(url), 'http://x').pathname.replace('/api/v1', '');
      calls.push(`${init?.method ?? 'GET'} ${path}`);
      if (path === '/boss-checks') {
        return json(200, { rows, results: { ...EMPTY, facebook_reply: opts.reply ?? null }, required_done: 0, required_total: 1 });
      }
      if (path === '/social/accounts') return json(200, { items: [] });
      if (path === '/cli/profiles') return json(200, []);
      if (path === '/providers') return json(200, []);
      if (path === '/hub/link') return json(200, { configured: false, enabled: false, status: 'off', has_token: false, endpoint: null, allow_public_network: false });
      return json(404, { code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
  return calls;
}

function renderPage() {
  queryClient.setQueryData(qk.me, me);
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/guide/viec-sep']}>
        <BossChecksPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => queryClient.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  queryClient.clear();
});

describe('Việc Sếp cần làm — dòng 8 Facebook trả lời', () => {
  it('hiện chip "Không bắt buộc", hướng dẫn 4 bước, chưa đạt → nút mở /social, không có nút chạy kiểm', async () => {
    const calls = setup();
    renderPage();
    expect(await screen.findByText('Đã đạt 0/1 dòng bắt buộc')).toBeInTheDocument();
    const row = screen.getByRole('region', { name: 'Facebook trả lời' });
    expect(within(row).getByText('Không bắt buộc')).toBeInTheDocument();
    expect(within(row).getByText('Chưa kiểm')).toBeInTheDocument();
    const steps = within(row).getAllByRole('listitem');
    expect(steps.map((s) => s.textContent)).toEqual([
      'Hỏi Gen: “đọc Facebook”',
      'Hỏi Gen: “trả lời bình luận của <tên> trên bài của tôi: …”',
      'Đọc kỹ thẻ, bấm Xác nhận và gửi, nhập mã PIN',
      'Đợi “Đã gửi”, bấm Xem ảnh chụp, mở Facebook xem lại',
    ]);
    const open = within(row).getByRole('link', { name: /Mở Tài khoản mạng xã hội/ });
    expect(open).toHaveAttribute('href', '/social');
    expect(within(row).queryByRole('button')).toBeNull();
    expect(calls.some((c) => c.startsWith('POST'))).toBe(false);
  });

  it('đạt → "Đạt" + thời điểm, dòng Xong, không còn nút mở /social', async () => {
    setup({ reply: pass('facebook_reply') });
    renderPage();
    const row = await screen.findByRole('region', { name: 'Facebook trả lời' });
    expect(within(row).getByTestId('boss-result').textContent).toMatch(/^Đạt · \d\d:\d\d \d\d\/\d\d$/);
    expect(within(row).getByText('Xong')).toBeInTheDocument();
    expect(within(row).queryByRole('link', { name: /Mở Tài khoản mạng xã hội/ })).toBeNull();
  });

  it('máy chủ cũ không có dòng 8 → không hiện dòng chết', async () => {
    setup({ withRow: false });
    renderPage();
    expect(await screen.findByText('Đã đạt 0/1 dòng bắt buộc')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: /Facebook trả lời/ })).toBeNull();
  });
});
