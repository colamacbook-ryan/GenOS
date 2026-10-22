import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { SocialAccount, SocialPlatforms } from '@gen-harness/contracts';
import { SocialPage } from '../../src/social/SocialPage';
import { LoginViewer } from '../../src/social/LoginViewer';
import { setViewerSocketFactory, type ViewerSocket } from '../../src/social/viewerSocket';
import { accountStatus, keyToInput, mapPoint, parseTimes } from '../../src/social/socialModel';
import { qk } from '../../src/lib/queries';

/** v0.1.29 — Tài khoản mạng xã hội: chỉ Owner, hộp chấp nhận rủi ro bắt buộc tích, cửa sổ đăng nhập từ xa. */

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

const me = (role: string) => ({
  id: 'u', email: 'x@genesis.local', display_name: 'Anh Cơ', role: { code: role, name: role },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
  permissions: { 'system.read': 'all', 'system.manage': 'all' },
});

function renderPage(ui: ReactElement, role = 'owner') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, me(role));
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const PLATFORMS: SocialPlatforms = {
  items: [{
    key: 'facebook_personal', name: 'Facebook cá nhân', mode: 'browser', read_kinds: ['notifications', 'inbox'], write_kinds: [],
    risk: ['Điều khoản của Meta không cho phép truy cập tự động.'], will_do: ['Chỉ ĐỌC thông báo.'], wont_do: ['Không đăng bài.'],
    risk_version: '2026-09-30',
  }],
  hard_rules: ['Không tạo tài khoản giả, tài khoản phụ hay nick ảo.', 'Không lách chống bot: không giải CAPTCHA tự động.'],
  risk_version: '2026-09-30',
};
const ACC: SocialAccount = {
  id: 'a1', platform: 'facebook_personal', platform_name: 'Facebook cá nhân', mode: 'browser', label: 'Facebook của Sếp',
  external_handle: null, status: 'pending_login', pause_reason: null, has_session: false, session_updated_at: null, last_health: null,
  risk_accepted_at: '2026-09-30T01:00:00Z', risk_version: '2026-09-30', schedule: { enabled: false, times: ['08:00', '17:00'] },
  daily_read_limit: 6, last_read_at: null, created_at: '2026-09-30T01:00:00Z', active_job: null,
};
const STATUS = { halted: false, halted_at: null, worker: { version: '0.1.29', at: '', running: 0 }, hard_rules: PLATFORMS.hard_rules, limits: { reads_per_day_max: 6, read_min_interval_minutes: 10, quiet_hours: [23, 6], concurrency_per_account: 1 } };

describe('socialModel', () => {
  it('nhãn trạng thái nói việc cần làm; checkpoint là dừng đỏ', () => {
    expect(accountStatus({ status: 'active', pause_reason: null, active_job: null }).label).toBe('Đang kết nối');
    const cp = accountStatus({ status: 'paused', pause_reason: 'checkpoint', active_job: null });
    expect(cp.tone).toBe('bad');
    expect(cp.hint).toMatch(/không tự giải/);
    expect(accountStatus({ status: 'needs_login', pause_reason: null, active_job: null }).label).toBe('Cần đăng nhập lại');
  });
  it('giờ lịch: HH:MM, tối đa 4, không trong 23:00–06:00', () => {
    expect(parseTimes('17:00, 8:00')).toEqual({ times: ['08:00', '17:00'], error: null });
    expect(parseTimes('23:30').error).toMatch(/giờ nghỉ/);
    expect(parseTimes('8h').error).toMatch(/HH:MM/);
    expect(parseTimes('07:00 08:00 09:00 10:00 11:00').error).toMatch(/Tối đa 4/);
  });
  it('phím: chữ thường → text, phím đặc biệt → key, phím tắt của trình duyệt Sếp bị bỏ', () => {
    expect(keyToInput({ key: 'a', ctrlKey: false, metaKey: false, altKey: false })).toEqual({ type: 'text', text: 'a' });
    expect(keyToInput({ key: 'Enter', ctrlKey: false, metaKey: false, altKey: false })).toEqual({ type: 'key', action: 'press', key: 'Enter' });
    expect(keyToInput({ key: 'c', ctrlKey: true, metaKey: false, altKey: false })).toBeNull();
    expect(keyToInput({ key: 'F12', ctrlKey: false, metaKey: false, altKey: false })).toBeNull();
    expect(mapPoint(150, 75, { left: 100, top: 50, width: 640, height: 400 }, { w: 1280, h: 800 })).toEqual({ x: 100, y: 50 });
  });
});

describe('Màn Tài khoản mạng xã hội', () => {
  it('vai trò khác Owner: không gọi API, báo chỉ Owner', async () => {
    const calls = mockFetch(() => json(200, {}));
    renderPage(<SocialPage />, 'manager');
    expect(await screen.findByText('Chỉ Owner dùng được')).toBeInTheDocument();
    expect(calls.filter((c) => c.url.includes('/social/'))).toHaveLength(0);
  });

  it('thêm tài khoản: phải tích CẢ HAI ô chấp nhận rủi ro mới gửi; luật cứng luôn hiện', async () => {
    const user = userEvent.setup();
    const created: unknown[] = [];
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/social/status')) return json(200, STATUS);
      if (c.url.endsWith('/social/platforms')) return json(200, PLATFORMS);
      if (c.url.endsWith('/social/accounts') && c.method === 'GET') return json(200, { items: created.length ? [ACC] : [] });
      if (c.url.endsWith('/social/accounts') && c.method === 'POST') {
        created.push(c.body);
        return json(201, ACC);
      }
      return json(404, { code: 'NOT_FOUND', title: 'x', status: 404 });
    });
    renderPage(<SocialPage />);
    expect(await screen.findByText('Chưa có tài khoản nào')).toBeInTheDocument();
    const rules = screen.getByTestId('social-hard-rules');
    expect(rules).toHaveTextContent('Không tạo tài khoản giả');
    expect(rules).toHaveTextContent('không giải CAPTCHA');
    await user.click(screen.getByRole('button', { name: /Thêm tài khoản/ }));
    await user.type(screen.getByLabelText('Tên để nhận ra'), 'Facebook của Sếp');
    await user.click(screen.getByRole('button', { name: /Tiếp/ }));
    const dlg = await screen.findByTestId('social-risk-dialog');
    expect(dlg).toHaveTextContent('Điều khoản của Meta');
    expect(dlg).toHaveTextContent('Không đăng bài.');
    const submit = screen.getByRole('button', { name: /Tôi chấp nhận, thêm tài khoản/ });
    expect(submit).toBeDisabled();
    await user.click(screen.getByLabelText(/Tôi hiểu và chấp nhận rủi ro/));
    expect(submit).toBeDisabled();
    await user.click(screen.getByLabelText(/tài khoản thật của chính tôi/));
    expect(submit).toBeEnabled();
    await user.click(submit);
    await waitFor(() => expect(created).toHaveLength(1));
    expect(created[0]).toEqual({ platform: 'facebook_personal', label: 'Facebook của Sếp', risk_version: '2026-09-30', accept_risk: true, accept_rules: true });
    expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/social/accounts'))).toBe(true);
  });

  it('F-17: needs_login + key_changed + has_session=false → nút "Đăng nhập lại" khớp gợi ý', async () => {
    mockFetch((c) => {
      if (c.url.endsWith('/social/status')) return json(200, STATUS);
      if (c.url.endsWith('/social/platforms')) return json(200, PLATFORMS);
      if (c.url.endsWith('/social/accounts')) {
        return json(200, { items: [{ ...ACC, status: 'needs_login', pause_reason: 'key_changed', has_session: false }] });
      }
      return json(404, { code: 'NOT_FOUND', title: 'x', status: 404 });
    });
    renderPage(<SocialPage />);
    expect(await screen.findByText('Phiên đã lưu không mở được trên máy này (chuyển máy hoặc đổi khoá) — bấm Đăng nhập lại.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Đăng nhập lại' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Đăng nhập' })).toBeNull();
  });

  it('Dừng tất cả: hỏi xác nhận rồi POST /social/halt; đang dừng thì hiện dải đỏ + Bật lại', async () => {
    const user = userEvent.setup();
    let halted = false;
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/social/status')) return json(200, { ...STATUS, halted });
      if (c.url.endsWith('/social/platforms')) return json(200, PLATFORMS);
      if (c.url.endsWith('/social/accounts')) return json(200, { items: [{ ...ACC, status: 'active', has_session: true }] });
      if (c.url.endsWith('/social/halt') && c.method === 'POST') {
        halted = true;
        return json(200, { ...STATUS, halted: true });
      }
      return json(404, { code: 'NOT_FOUND', title: 'x', status: 404 });
    });
    renderPage(<SocialPage />);
    await user.click(await screen.findByRole('button', { name: 'Dừng tất cả' }));
    await user.click(await screen.findByRole('button', { name: 'Dừng ngay' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/social/halt'))).toBe(true));
  });
});

class FakeSocket implements ViewerSocket {
  readyState = 1;
  sent: unknown[] = [];
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: { code: number }) => void) | null = null;
  onopen: (() => void) | null = null;
  url = '';
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close() {
    this.readyState = 3;
  }
  emit(msg: unknown) {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }
}

describe('Cửa sổ đăng nhập từ xa', () => {
  it('nhận khung hình, gửi phím/chuột, báo đăng nhập xong; Huỷ gửi cancel', async () => {
    const user = userEvent.setup();
    const sock = new FakeSocket();
    setViewerSocketFactory((url) => {
      sock.url = url;
      return sock;
    });
    const loggedIn = vi.fn();
    const onClose = vi.fn();
    renderPage(<LoginViewer open ticket="ve-123" label="Facebook của Sếp" onClose={onClose} onLoggedIn={loggedIn} />);
    expect(sock.url).toMatch(/\/api\/v1\/social\/login\/ve-123$/);
    const canvas = screen.getByTestId('social-viewer-canvas');
    act(() => sock.emit({ type: 'frame', data: 'AAAA', w: 1280, h: 800 }));
    expect(canvas.getAttribute('data-frames')).toBe('1');
    act(() => sock.emit({ type: 'status', state: 'waiting', message: 'Sếp tự đăng nhập trong khung dưới' }));
    expect(screen.getByRole('status')).toHaveTextContent('Sếp tự đăng nhập');
    await user.click(canvas);
    await user.keyboard('ab{Enter}');
    expect(sock.sent).toEqual([
      expect.objectContaining({ type: 'mouse', action: 'click' }),
      { type: 'text', text: 'a' },
      { type: 'text', text: 'b' },
      { type: 'key', action: 'press', key: 'Enter' },
    ]);
    await user.click(screen.getByRole('button', { name: /Tôi đã đăng nhập xong/ }));
    expect(sock.sent.at(-1)).toEqual({ type: 'done' });
    act(() => sock.emit({ type: 'status', state: 'logged_in', message: 'Đã đăng nhập' }));
    expect(loggedIn).toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Huỷ' }));
    expect(sock.sent.at(-1)).toEqual({ type: 'cancel' });
    expect(onClose).toHaveBeenCalled();
  });
});
