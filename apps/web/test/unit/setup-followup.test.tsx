import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CoachPrefs, SetupFollowUpItem } from '@gen-harness/contracts';
import { COACH_PREFS_KEY } from '../../src/gen/coachModel';
import { SetupFollowUp } from '../../src/screens/queue/SetupFollowUp';

const ME = (id: string, role: string) => ({
  id, email: `${id}@genesis.local`, display_name: id, role: { code: role, name: role },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: {},
});

function item(n: number, done: boolean): SetupFollowUpItem {
  return { n, key: `k${n}`, title: `Bước ${n}`, status: 'skipped', done };
}

const PREFS = (over: Partial<CoachPrefs> = {}): CoachPrefs => ({
  enabled: true, bell: true, lessons_per_day: 1, quiet_start: 21, quiet_end: 7, snooze_until: null, followup_snoozed_until: null, dismissed: [], ...over,
});

/** v0.1.54: "Để sau 7 ngày" gọi máy chủ (không còn lưu ở trình duyệt) — ghi lại mọi lời gọi POST. */
const posts: Array<{ url: string; body: unknown }> = [];

function renderWith(items: SetupFollowUpItem[], me = ME('owner-1', 'owner'), prefs: CoachPrefs = PREFS()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  qc.setQueryData(['setup', 'follow-up'], items);
  qc.setQueryData(['auth', 'me'], me);
  qc.setQueryData(COACH_PREFS_KEY, prefs);
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <SetupFollowUp />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Việc thiết lập tiếp (Tổng quan)', () => {
  beforeEach(() => {
    posts.length = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if ((init?.method ?? 'GET').toUpperCase() === 'POST') {
          posts.push({ url: String(input), body: init?.body ? JSON.parse(String(init.body)) : null });
          return new Response(null, { status: 204 });
        }
        // Tải lại cài đặt sau khi hoãn: máy chủ đã ghi hạn hoãn.
        const until = new Date(Date.now() + 7 * 86_400_000).toISOString();
        return new Response(JSON.stringify(PREFS({ followup_snoozed_until: until })), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it('liệt kê bước chưa xong, mỗi bước mở thẳng form làm việc đó, đầu thẻ dẫn tới Hướng dẫn thiết lập', () => {
    renderWith([item(5, false), item(8, false), item(11, false)]);
    expect(screen.getByText('Việc thiết lập tiếp')).toBeInTheDocument();
    const links = screen.getAllByRole('link', { name: /Làm ngay/ }).map((a) => a.getAttribute('href'));
    expect(links).toEqual(['/guide/5', '/guide/8', '/guide/11']);
    expect(screen.getByRole('link', { name: /Hướng dẫn thiết lập/ })).toHaveAttribute('href', '/guide');
    expect(screen.queryByRole('link', { name: /Hướng dẫn từng bước/ })).toBeNull();
    expect(screen.getByText('Kết nối Zalo / WhatsApp')).toBeInTheDocument();
  });

  it('v0.1.39: Kết nối Facebook (13) và Nối Gen-hub (14) chưa xong → mở thẳng /social, /connections (v0.1.42); xong thì biến mất', () => {
    const { unmount } = renderWith([item(5, true), item(13, false), item(14, false)]);
    expect(screen.getByText('Kết nối Facebook')).toBeInTheDocument();
    expect(screen.getByText('Nối Gen-hub')).toBeInTheDocument();
    const links = screen.getAllByRole('link', { name: /Làm ngay/ }).map((a) => a.getAttribute('href'));
    expect(links).toEqual(['/social', '/connections#genhub']);
    unmount();
    renderWith([item(5, false), item(13, true), item(14, true)]);
    expect(screen.queryByText('Kết nối Facebook')).not.toBeInTheDocument();
    expect(screen.queryByText('Nối Gen-hub')).not.toBeInTheDocument();
    expect(screen.getByText('Kết nối Zalo / WhatsApp')).toBeInTheDocument();
  });

  it('bước đã làm xong ở Console (done=true) tự biến mất', () => {
    renderWith([item(5, true), item(6, false)]);
    expect(screen.queryByText('Kết nối Zalo / WhatsApp')).not.toBeInTheDocument();
    expect(screen.getByText('Chọn nhóm cho agent lắng nghe')).toBeInTheDocument();
  });

  it('không hiện gì khi mọi việc đã xong', () => {
    const { container } = renderWith([item(5, true)]);
    expect(container).toBeEmptyDOMElement();
  });

  it('v0.1.54: không còn nút "Ẩn" lưu ở trình duyệt; "Để sau 7 ngày" gọi máy chủ rồi ẩn thẻ', async () => {
    renderWith([item(5, false), item(8, false)]);
    expect(screen.queryByRole('button', { name: 'Ẩn' })).toBeNull();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Để sau 7 ngày' }));
    await waitFor(() => expect(screen.queryByText('Việc thiết lập tiếp')).not.toBeInTheDocument());
    expect(posts).toHaveLength(1);
    expect(posts[0].url).toBe('/api/v1/gen/coach/items/card%3Asetup_followup');
    expect(posts[0].body).toEqual({ action: 'snooze', days: 7 });
  });

  it('v0.1.54: prefs.followup_snoozed_until còn ở tương lai ⇒ ẩn thẻ trên mọi máy; hết hạn ⇒ hiện lại', () => {
    const future = new Date(Date.now() + 3 * 86_400_000).toISOString();
    const hidden = renderWith([item(5, false)], ME('owner-1', 'owner'), PREFS({ followup_snoozed_until: future }));
    expect(hidden.container).toBeEmptyDOMElement();
    hidden.unmount();
    const past = new Date(Date.now() - 60_000).toISOString();
    renderWith([item(5, false)], ME('owner-1', 'owner'), PREFS({ followup_snoozed_until: past }));
    expect(screen.getByText('Việc thiết lập tiếp')).toBeInTheDocument();
  });

  it('v0.1.30: không phải Owner → không hiện (API /setup/* chỉ cho Owner)', () => {
    const { container } = renderWith([item(5, false)], ME('m', 'manager'));
    expect(container).toBeEmptyDOMElement();
  });
});
