import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import type { BossOverview, SetupFollowUpItem, SetupState } from '@gen-harness/contracts';
import { GuidePage } from '../../src/guide/GuidePage';
import { GuideStepPage } from '../../src/guide/GuideStepPage';
import { GUIDE } from '../../src/guide/guideContent';
import { queryClient } from '../../src/lib/queryClient';

const finished = (done: number[] = [], skipped: number[] = []): SetupState => ({
  finished: true,
  current_step: 12,
  steps: Array.from({ length: 12 }, (_, i) => ({
    n: i + 1,
    key: `s${i + 1}`,
    title: `Bước ${i + 1}`,
    required: i + 1 <= 4 || i + 1 === 12,
    status: done.includes(i + 1) || i + 1 <= 4 || i + 1 === 12 ? 'done' : skipped.includes(i + 1) ? 'skipped' : 'todo',
  })),
});

function followUp(done: number[]): SetupFollowUpItem[] {
  return [5, 6, 7, 8, 9, 10, 11, 13, 14].map((n) => ({ n, key: `k${n}`, title: `Bước ${n}`, status: done.includes(n) ? 'done' : n > 12 ? 'todo' : 'skipped', done: done.includes(n) }));
}

const BOSS: BossOverview = {
  rows: [],
  results: { hub: null, facebook: null, agy_login: null, agy_call: null, agy_switch: null, claude_login: null, claude_call: null, jev: null, telegram: null, remote_access: null },
  required_done: 0,
  required_total: 1,
};

function renderGuide(done: number[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  qc.setQueryData(['setup', 'follow-up'], followUp(done));
  qc.setQueryData(['boss-checks'], BOSS);
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <GuidePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Hướng dẫn thiết lập (/guide)', () => {
  it('mỗi việc có chuẩn bị, các bước đánh số, dấu hiệu xong và nút mở đúng form', () => {
    for (const g of GUIDE) {
      expect(g.steps.length).toBeGreaterThanOrEqual(3);
      expect(g.prepare.length).toBeGreaterThan(0);
      expect(g.doneWhen).not.toBe('');
    }
    expect(GUIDE.map((g) => g.n)).toEqual([5, 6, 7, 8, 9, 10, 11, 13, 14]);

    renderGuide([5, 7]);
    expect(screen.getByRole('heading', { name: 'Hướng dẫn thiết lập' })).toBeInTheDocument();
    expect(document.title).toBe('Hướng dẫn thiết lập · Gen-Harness');
    expect(screen.getByText('Đã xong 2/9 việc')).toBeInTheDocument();
    expect(screen.getAllByText('Đã xong')).toHaveLength(2);
    // Việc chưa xong đầu tiên (06) mở sẵn; việc đã xong thì nút đổi thành "Làm lại / chỉnh".
    const cards = screen.getAllByRole('listitem').filter((li) => li.classList.contains('guide-card'));
    expect(cards[1].querySelector('details')).toHaveAttribute('open');
    expect(cards[0].querySelector('details')).not.toHaveAttribute('open');
    expect(within(cards[1]).getByRole('link', { name: /Làm bước này/ })).toHaveAttribute('href', '/guide/6');
    expect(within(cards[0]).getByRole('link', { name: /Làm lại/ })).toHaveAttribute('href', '/guide/5');
    expect(within(cards[1]).getByRole('link', { name: /Hoặc làm ở Khách & Cơ hội › Khách & Nhóm/ })).toHaveAttribute('href', '/directory');
    // Đặt agent (thứ tự 05) cần agent (thứ tự 04) chưa có → nhắc theo SỐ THỨ TỰ trong danh sách.
    expect(within(cards[4]).getByText(/Nên làm việc 04 trước/)).toBeInTheDocument();
  });

  it('v0.1.39: 9 việc đánh số 01…09; Mời người trong đội → Người dùng; Facebook, Gen-hub mở thẳng màn làm việc', () => {
    renderGuide([13, 14]);
    const cards = screen.getAllByRole('listitem').filter((li) => li.classList.contains('guide-card'));
    expect(cards).toHaveLength(9);
    expect(cards.map((c) => c.querySelector('.guide-card__num')?.textContent)).toEqual(['01', '02', '03', '04', '05', '06', '07', '08', '09']);
    // data-gen-target vẫn theo số bước.
    expect(cards[7]).toHaveAttribute('data-gen-target', 'guide.item:13');
    const team = cards.find((c) => within(c).queryByText('Mời người trong đội'))!;
    // v0.1.42 (F-7): người dùng ở Đội ngũ.
    expect(within(team).getByRole('link', { name: /Hoặc làm ở Đội ngũ › Người dùng/ })).toHaveAttribute('href', '/team');
    const fb = cards[7];
    expect(within(fb).getByText('Kết nối Facebook')).toBeInTheDocument();
    expect(within(fb).getByRole('link', { name: /Mở trang Tài khoản mạng xã hội/ })).toHaveAttribute('href', '/social');
    expect(within(fb).getByText('Đã xong')).toBeInTheDocument();
    const hub = cards[8];
    expect(within(hub).getByText('Nối Gen-hub')).toBeInTheDocument();
    // v0.1.42 (F-61): thẻ Gen-hub chỉ ở Kết nối.
    expect(within(hub).getByRole('link', { name: /Mở thẻ Gen-hub/ })).toHaveAttribute('href', '/connections#genhub');
    expect(within(hub).getByText('Đã xong')).toBeInTheDocument();
    expect(screen.getByText('Đã xong 2/9 việc')).toBeInTheDocument();
  });

  it('v0.1.39: thẻ "Việc Sếp cần làm" ở đầu trang dẫn tới /guide/viec-sep, kèm tiến độ dòng bắt buộc', () => {
    renderGuide([]);
    const link = screen.getByRole('link', { name: /Việc Sếp cần làm — nguồn AI chạy thật/ });
    expect(link).toHaveAttribute('href', '/guide/viec-sep');
    expect(link).toHaveTextContent('Đã đạt 0/1 dòng bắt buộc');
    // Đứng trước danh sách việc.
    const list = document.querySelector('.guide-list')!;
    expect(link.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe('Làm một việc từ hướng dẫn (/guide/:n) — sau khi đã Hoàn tất', () => {
  beforeEach(() => {
    queryClient.clear();
    vi.unstubAllGlobals();
  });

  it('bước 11 mở đúng form sao lưu, lưu được sau Hoàn tất rồi quay về trang hướng dẫn', async () => {
    const user = userEvent.setup();
    let saved: unknown = null;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        const u = String(url);
        const reply = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
        if (u.endsWith('/setup/steps/11') && init?.method === 'PUT') {
          saved = JSON.parse(String(init.body));
          return reply({ ...finished([11], [5, 6, 7, 8, 9, 10]), backup: saved });
        }
        if (u.includes('/auth/me')) return reply({ role: { code: 'owner' } }); // GuideStepPage đọc /auth/me thật (review v0.1.68)
        if (u.endsWith('/setup/follow-up')) return reply(followUp(saved ? [11] : []));
        return reply(finished([], [5, 6, 7, 8, 9, 10, 11]));
      }),
    );
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/guide/11']}>
          <Routes>
            <Route path="/guide" element={<GuidePage />} />
            <Route path="/guide/:n" element={<GuideStepPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const time = await screen.findByLabelText('Giờ chạy (HH:MM)');
    expect(screen.queryByRole('button', { name: 'Để sau' })).not.toBeInTheDocument();
    await user.clear(time);
    await user.type(time, '03:30');
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await waitFor(() => expect(saved).toMatchObject({ frequency: 'daily', time_of_day: '03:30' }));
    expect(await screen.findByText('Đã xong 1/9 việc')).toBeInTheDocument();
  });

  it('số việc không có trong hướng dẫn thì về trang hướng dẫn', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(followUp([])), { status: 200, headers: { 'Content-Type': 'application/json' } })));
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/guide/3']}>
          <Routes>
            <Route path="/guide" element={<GuidePage />} />
            <Route path="/guide/:n" element={<GuideStepPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText('Đã xong 0/9 việc')).toBeInTheDocument();
  });

  it('v0.1.39: /guide/13 và /guide/14 mở thẳng màn làm việc (không có form trình thiết lập)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(followUp([])), { status: 200, headers: { 'Content-Type': 'application/json' } })));
    for (const [n, to] of [[13, '/social'], [14, '/connections']] as const) {
      const { unmount } = render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[`/guide/${n}`]}>
            <Routes>
              <Route path="/guide/:n" element={<GuideStepPage />} />
              <Route path={to} element={<p>Đã tới {to}</p>} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
      expect(await screen.findByText(`Đã tới ${to}`)).toBeInTheDocument();
      unmount();
    }
  });
});
