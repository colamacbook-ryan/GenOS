/** v0.1.23 (Đợt B4–B7): trang lỗi/404, chuông thông báo, sáng/tối, ngăn kéo danh mục trên điện thoại. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';
import type { NotificationItem, NotificationsPage, RealtimeEvent } from '@gen-harness/contracts';
import { newErrorId } from '../../src/lib/errorId';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';
import { applyEvent } from '../../src/lib/realtime';
import { nextTheme, resolveTheme, useThemeSync } from '../../src/lib/theme';
import { useToasts } from '../../src/lib/toast';
import { useUiStore } from '../../src/lib/uiStore';
import { ErrorBoundary, NotFoundPage } from '../../src/shell/ErrorPage';
import { badgeText } from '../../src/shell/headerModel';
import { NotificationBell } from '../../src/shell/NotificationBell';
import { ThemeToggle } from '../../src/shell/ThemeToggle';

const me = { id: 'u1', email: 'owner@genesis.local', display_name: 'Anh Cơ', role: { code: 'owner', name: 'Owner — Sếp' },
  org: { id: 'o1', name: 'Genesis', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
  pin_verified_until: null, permissions: {}, must_change_password: false, features: { gen: false } };

const N = (over: Partial<NotificationItem>): NotificationItem => ({
  id: 'n', kind: 'backup.done', title: 'Sao lưu đã xong', body: 'Bản mới đã lưu.', link: '/system?tab=storage',
  created_at: new Date(Date.now() - 5 * 60_000).toISOString(), read: false, ...over,
});

const calls: Array<{ method: string; url: string; body: unknown }> = [];
function stubApi(handler: (method: string, url: string, body: unknown) => { status: number; body?: unknown } | undefined) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ method, url, body });
    const r = url.endsWith('/auth/me') ? { status: 200, body: me } : handler(method, url, body) ?? { status: 404, body: { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' } };
    return new Response(JSON.stringify(r.body ?? {}), { status: r.status, headers: { 'Content-Type': r.status >= 400 ? 'application/problem+json' : 'application/json' } });
  }));
}

function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname + l.search}</div>;
}

const wrap = (ui: ReactNode, path = '/overview') =>
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        {ui}
        <Where />
      </MemoryRouter>
    </QueryClientProvider>,
  );

beforeEach(() => {
  queryClient.clear();
  calls.length = 0;
  useToasts.setState({ toasts: [] });
  useUiStore.setState({ theme: 'system', themeByUser: {}, drawerOpen: false });
  vi.stubGlobal('WebSocket', undefined);
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.theme;
});

describe('B5 — trang lỗi & 404', () => {
  it('mã lỗi có dạng ERR-XXXXX-XXXX', () => {
    expect(newErrorId(1_700_000_000_000, () => 0.5)).toMatch(/^ERR-[0-9A-Z]{5}-[0-9A-Z]{4}$/);
    expect(newErrorId()).not.toBe(newErrorId());
  });

  it('lỗi vẽ giao diện → trang lỗi tiếng Việt có mã lỗi, Thử lại vẽ lại được', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    let fail = true;
    function Boom() {
      if (fail) throw new Error('hỏng rồi');
      return <p>Đã ổn</p>;
    }
    render(
      <ErrorBoundary variant="inline">
        <Boom />
      </ErrorBoundary>,
    );
    const alert = screen.getByRole('alert');
    expect(within(alert).getByRole('heading', { name: 'Đã có lỗi xảy ra' })).toBeInTheDocument();
    expect(screen.getByTestId('error-id').textContent).toMatch(/^ERR-/);
    expect(within(alert).getByRole('button', { name: /Tải lại trang/ })).toBeInTheDocument();
    expect(within(alert).getByRole('button', { name: /Về trang chủ/ })).toBeInTheDocument();
    expect(within(alert).getByText(/hỏng rồi/)).toBeInTheDocument();
    fail = false;
    await userEvent.click(within(alert).getByRole('button', { name: /Thử lại/ }));
    expect(screen.getByText('Đã ổn')).toBeInTheDocument();
    spy.mockRestore();
  });

  it('đổi resetKey (đổi trang) xoá lỗi', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const Boom = ({ on }: { on: boolean }) => {
      if (on) throw new Error('x');
      return <p>trang mới</p>;
    };
    const { rerender } = render(
      <ErrorBoundary resetKey="/a">
        <Boom on />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('alert')).toBeInTheDocument();
    rerender(
      <ErrorBoundary resetKey="/b">
        <Boom on={false} />
      </ErrorBoundary>,
    );
    expect(screen.getByText('trang mới')).toBeInTheDocument();
    spy.mockRestore();
  });

  it('404: nêu đường dẫn, về trang chủ', async () => {
    wrap(
      <Routes>
        <Route path="*" element={<NotFoundPage />} />
      </Routes>,
      '/khong-co',
    );
    expect(screen.getByRole('heading', { name: 'Không tìm thấy trang' })).toBeInTheDocument();
    expect(screen.getByText('/khong-co', { selector: 'code' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Về trang chủ/ }));
    expect(screen.getByTestId('where').textContent).toBe('/');
  });
});

describe('B6 — chuông thông báo', () => {
  const page: NotificationsPage = {
    items: [N({ id: 'a', title: 'Sao lưu đã xong' }), N({ id: 'b', kind: 'user.role_changed', title: 'Vai trò của bạn đã đổi', link: null, read: true })],
    unread: 1,
  };

  it('huy hiệu chưa đọc, mở danh sách, bấm → đánh dấu đã đọc + mở trang liên quan', async () => {
    stubApi((m, url) => {
      if (m === 'GET' && url.includes('/notifications')) return { status: 200, body: page };
      if (m === 'POST' && url.endsWith('/notifications/read')) return { status: 200, body: { unread: 0 } };
      return undefined;
    });
    wrap(<NotificationBell />);
    const bell = await screen.findByRole('button', { name: 'Thông báo — 1 chưa đọc' });
    expect(within(bell).getByText('1')).toBeInTheDocument();
    await userEvent.click(bell);
    const dlg = screen.getByRole('dialog', { name: 'Thông báo' });
    expect(within(dlg).getByText('1 chưa đọc')).toBeInTheDocument();
    expect(within(dlg).getAllByText('5 phút trước')).toHaveLength(2);
    await userEvent.click(within(dlg).getByRole('button', { name: /Sao lưu đã xong/ }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/notifications/read'))).toBe(true));
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ ids: ['a'] });
    expect(screen.getByTestId('where').textContent).toBe('/system?tab=storage');
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Thông báo' })).toBeInTheDocument());
  });

  it('đánh dấu đã đọc hết; trống thì báo chưa có thông báo', async () => {
    stubApi((m, url) => {
      if (m === 'GET' && url.includes('/notifications')) return { status: 200, body: page };
      if (m === 'POST') return { status: 200, body: { unread: 0 } };
      return undefined;
    });
    wrap(<NotificationBell />);
    await userEvent.click(await screen.findByRole('button', { name: /Thông báo — 1 chưa đọc/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Đánh dấu đã đọc hết' }));
    await waitFor(() => expect(calls.find((c) => c.method === 'POST')?.body).toEqual({}));
    expect(screen.getByRole('button', { name: 'Đánh dấu đã đọc hết' })).toBeDisabled();
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();

    queryClient.clear();
    stubApi((m, url) => (m === 'GET' && url.includes('/notifications') ? { status: 200, body: { items: [], unread: 0 } } : undefined));
    wrap(<NotificationBell />);
    const bells = await screen.findAllByRole('button', { name: 'Thông báo' });
    await userEvent.click(bells[bells.length - 1]);
    expect(await screen.findByText('Chưa có thông báo nào.')).toBeInTheDocument();
  });

  it('F098: chuông đã xử lý hiện nhãn "Đã xử lý"; nhiều hơn 20 thì "Xem thêm" tải đủ', async () => {
    const first: NotificationsPage = {
      items: [N({ id: 'a', title: 'Cập nhật chưa thành công', read: true, resolved: true }), N({ id: 'b', title: 'Ổ đĩa sắp đầy' })],
      unread: 3,
      total: 4,
    };
    const full: NotificationsPage = {
      items: [...first.items, N({ id: 'c', title: 'Việc nền c' }), N({ id: 'd', title: 'Việc nền d', read: true })],
      unread: 3,
      total: 4,
    };
    stubApi((m, url) => {
      if (m === 'GET' && url.includes('limit=100')) return { status: 200, body: full };
      if (m === 'GET' && url.includes('/notifications')) return { status: 200, body: first };
      return undefined;
    });
    wrap(<NotificationBell />);
    await userEvent.click(await screen.findByRole('button', { name: /Thông báo — 3 chưa đọc/ }));
    const dlg = screen.getByRole('dialog', { name: 'Thông báo' });
    const resolvedItem = within(dlg).getByRole('button', { name: /Cập nhật chưa thành công/ });
    expect(within(resolvedItem).getByText('Đã xử lý')).toBeInTheDocument();
    expect(within(dlg).queryByText('Việc nền c')).toBeNull();
    await userEvent.click(within(dlg).getByRole('button', { name: /Xem thêm \(2\)/ }));
    expect(await within(dlg).findByText('Việc nền c')).toBeInTheDocument();
    expect(within(dlg).getByText('Việc nền d')).toBeInTheDocument();
    expect(within(dlg).queryByRole('button', { name: /Xem thêm/ })).toBeNull();
  });

  it('"Xem thêm" lật tiếp trang kế khi có quá 100 thông báo (offset), không sót không trùng', async () => {
    const mk = (from: number, to: number) => Array.from({ length: to - from }, (_, i) => N({ id: `n${from + i}`, title: `Việc ${from + i}`, read: true }));
    const body = (items: ReturnType<typeof mk>) => ({ items, unread: 1, total: 130 });
    stubApi((m, url) => {
      if (m !== 'GET' || !url.includes('/notifications')) return undefined;
      if (url.includes('offset=100')) return { status: 200, body: body(mk(100, 130)) };
      if (url.includes('limit=100')) return { status: 200, body: body(mk(0, 100)) };
      return { status: 200, body: body(mk(0, 20)) };
    });
    wrap(<NotificationBell />);
    await userEvent.click(await screen.findByRole('button', { name: /Thông báo — 1 chưa đọc/ }));
    const dlg = screen.getByRole('dialog', { name: 'Thông báo' });
    await userEvent.click(await within(dlg).findByRole('button', { name: /Xem thêm \(110\)/ }));
    expect(await within(dlg).findByText('Việc 99')).toBeInTheDocument();
    expect(within(dlg).queryByText('Việc 100')).toBeNull();
    await userEvent.click(await within(dlg).findByRole('button', { name: /Xem thêm \(30\)/ }));
    expect(await within(dlg).findByText('Việc 129')).toBeInTheDocument();
    expect(within(dlg).getAllByRole('listitem')).toHaveLength(130);
    expect(within(dlg).queryByRole('button', { name: /Xem thêm/ })).toBeNull();
  });

  it('WS notification.new chèn đầu danh sách, tăng số chưa đọc, không trùng', () => {
    const qc = new QueryClient();
    qc.setQueryData<NotificationsPage>(qk.notifications, page);
    const ev = { type: 'notification.new', data: N({ id: 'c', title: 'Mới' }) } as RealtimeEvent;
    applyEvent(qc, ev);
    applyEvent(qc, ev);
    const d = qc.getQueryData<NotificationsPage>(qk.notifications)!;
    expect(d.items.map((n) => n.id)).toEqual(['c', 'a', 'b']);
    expect(d.unread).toBe(2);
  });

  it('WS notification.resolved làm mới chuông (đã đọc / "Đã xử lý") thay vì đợi lượt tải 120 giây', () => {
    const qc = new QueryClient();
    qc.setQueryData<NotificationsPage>(qk.notifications, page);
    qc.setQueryData([...qk.notifications, 'all'], page);
    expect(qc.getQueryState(qk.notifications)?.isInvalidated).toBe(false);
    applyEvent(qc, { type: 'notification.resolved', data: N({ id: 'a', read: true, resolved: true }) } as RealtimeEvent);
    expect(qc.getQueryState(qk.notifications)?.isInvalidated).toBe(true);
    expect(qc.getQueryState([...qk.notifications, 'all'])?.isInvalidated).toBe(true);
  });

  it('huy hiệu gọn: 9+', () => {
    expect(badgeText(3)).toBe('3');
    expect(badgeText(12)).toBe('9+');
  });
});

describe('B7 — sáng/tối', () => {
  it('mặc định theo hệ thống; vòng hệ thống → sáng → tối', () => {
    expect(resolveTheme('system', true)).toBe('light');
    expect(resolveTheme('system', false)).toBe('dark');
    expect(resolveTheme('light', false)).toBe('light');
    expect(nextTheme('system')).toBe('light');
    expect(nextTheme('light')).toBe('dark');
    expect(nextTheme('dark')).toBe('system');
  });

  it('nút ở header đổi giao diện, lưu theo từng người dùng, gắn data-theme lên <html>', async () => {
    stubApi(() => undefined);
    function Sync() {
      useThemeSync('u1');
      return null;
    }
    wrap(
      <>
        <Sync />
        <ThemeToggle />
      </>,
    );
    // jsdom không có matchMedia → "theo hệ thống" rơi về tối (thiết kế gốc).
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe('dark'));
    const btn = await screen.findByRole('button', { name: /Giao diện: Theo hệ thống/ });
    await userEvent.click(btn);
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(useUiStore.getState().themeByUser).toEqual({ u1: 'light' });
    expect(useUiStore.getState().theme).toBe('light');
    await userEvent.click(screen.getByRole('button', { name: /Giao diện: Sáng/ }));
    expect(document.documentElement.dataset.theme).toBe('dark');
    // Người khác trên cùng máy giữ lựa chọn riêng.
    act(() => useUiStore.getState().setTheme('light', 'u2'));
    expect(useUiStore.getState().themeByUser).toEqual({ u1: 'dark', u2: 'light' });
    expect(document.documentElement.dataset.theme).toBe('dark');
  });
});
