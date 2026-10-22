import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { NotificationItem, NotificationsPage, OffsiteState } from '@gen-harness/contracts';
import { OffsitePanel } from '../../src/screens/system/OffsitePanel';
import { NotificationBell } from '../../src/shell/NotificationBell';
import { PinDialogHost } from '../../src/shell/PinDialogHost';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';
import { qkSystem } from '../../src/screens/system/queries';
import type { SystemHealth } from '@gen-harness/contracts';
import {
  ageDays,
  clearPortablePreparing,
  offsiteApiErrorText,
  offsiteErrorText,
  offsiteRequestView,
  offsiteScheduleText,
  offsiteView,
} from '../../src/screens/system/offsiteModel';
import { healthRows } from '../../src/screens/system/healthModel';
import { useToasts } from '../../src/lib/toast';
import { ApiError } from '@gen-harness/contracts';

/**
 * v0.1.40 (F-12): thẻ "Bản sao ngoài máy" (Dữ liệu & lưu trữ) + Bộ khôi phục + chuông kind mới. Khuôn API theo hợp đồng
 * `GET /system/offsite` — mọi trường là chuỗi/số/bool/null.
 */

const DAY = 24 * 3600 * 1000;
const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json' } });

type Role = 'owner' | 'manager' | 'auditor';
function meAs(role: Role, pinUntil: string | null = null) {
  return {
    id: 'u', email: `${role}@genesis.local`, display_name: role, role: { code: role, name: role },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
    pin_verified_until: pinUntil,
    permissions: role === 'auditor' ? { 'system.read': 'all' } : { 'system.read': 'all', 'system.manage': 'all' },
  };
}

function state(over: Partial<OffsiteState> = {}): OffsiteState {
  return {
    configured: true, dest: '/media/sep/GEN-USB', state: 'ok', error_code: null, message: null,
    last_attempt_at: new Date(Date.now() - 2 * DAY).toISOString(), last_success_at: new Date(Date.now() - 2 * DAY).toISOString(),
    age_days: 2, stale: false, last_size_bytes: 1_288_490_189, verified: true, key_id: 'a1b2c3d4', schedule: 'systemd',
    request: { state: 'idle', action: null, requested_at: null }, can_request: true, manual_command: null, key_present: true,
    ...over,
  };
}

interface Call {
  url: string;
  method: string;
  body: unknown;
}
function mockFetch(handler: (c: Call) => Response | undefined, role: Role = 'owner') {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const c = { url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(c);
      const r = handler(c);
      if (r) return r;
      if (c.url.endsWith('/auth/me')) return json(200, meAs(role));
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tìm thấy' });
    }),
  );
  return calls;
}

function renderPanel(role: Role = 'owner') {
  queryClient.setQueryData(qk.me, meAs(role));
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <OffsitePanel />
        <PinDialogHost />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function enterPin(user: ReturnType<typeof userEvent.setup>) {
  const pin = await screen.findByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
  const boxes = within(pin).getAllByLabelText(/Mã PIN — chữ số/);
  await waitFor(() => expect(boxes[0]).toHaveFocus());
  await user.keyboard('246810');
}

const PIN_REQUIRED = () => json(423, { status: 423, code: 'PIN_REQUIRED', title: 'Thao tác này cần nhập mã PIN' });
const PIN_OK = () => json(200, { pin_verified_until: new Date(Date.now() + 1800_000).toISOString() });

beforeEach(() => {
  queryClient.clear();
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } });
  document.cookie = 'gh_csrf=test-csrf';
});
afterEach(() => {
  vi.unstubAllGlobals();
  clearPortablePreparing();
  document.getElementById('gh-portable-frame')?.remove();
});

describe('offsiteModel', () => {
  const NOW = Date.parse('2026-10-02T03:00:00Z');
  const ago = (d: number) => new Date(NOW - d * DAY).toISOString();

  it('tuổi theo ngày (làm tròn xuống), null khi không có / không đọc được', () => {
    expect(ageDays(ago(8.9), NOW)).toBe(8);
    expect(ageDays(ago(0.2), NOW)).toBe(0);
    expect(ageDays(null, NOW)).toBeNull();
    expect(ageDays('không phải ngày', NOW)).toBeNull();
  });

  it('stale/tông: chưa có ⇒ vàng; 2 ngày ⇒ không cảnh báo; 8 ngày ⇒ vàng; 31 ngày ⇒ đỏ', () => {
    const none = offsiteView(state({ last_success_at: null, age_days: null, stale: true, configured: false, dest: null }), NOW);
    expect(none).toMatchObject({ headline: 'Chưa có bản sao ngoài máy', stale: true, tone: 'warn', hasCopy: false });
    expect(none.warning).toMatch(/^Hỏng ổ đĩa là mất hết dữ liệu — /);
    expect(offsiteView(state({ last_success_at: ago(2) }), NOW)).toMatchObject({ stale: false, tone: 'none', warning: null, days: 2 });
    expect(offsiteView(state({ last_success_at: ago(8) }), NOW)).toMatchObject({ stale: true, tone: 'warn', days: 8 });
    expect(offsiteView(state({ last_success_at: ago(31) }), NOW)).toMatchObject({ stale: true, tone: 'bad', days: 31 });
    const ok = offsiteView(state({ last_success_at: ago(2) }), NOW, 'Asia/Ho_Chi_Minh');
    expect(ok.headline).toMatch(/^Bản sao ngoài máy gần nhất: .+ \(2 ngày trước\) · 1,2 GB · đã kiểm đọc lại được$/);
  });

  it('nhãn lỗi: ưu tiên message của API, không có thì theo mã GH-EBxx; mã lạ ⇒ câu chung', () => {
    expect(offsiteErrorText('GH-EB01', null)).toContain('Chưa thấy ổ USB/NAS');
    expect(offsiteErrorText('GH-EB01', 'Câu của API')).toBe('Câu của API');
    expect(offsiteErrorText('GH-EB99', null)).toBe('Lần sao lưu ra ổ ngoài gần nhất chưa thành công.');
    const v = offsiteView(state({ state: 'not_mounted', error_code: 'GH-EB01' }), NOW);
    expect(v.error).toEqual({ text: expect.stringContaining('Chưa thấy ổ USB/NAS'), code: 'GH-EB01' });
  });

  it('lịch và yêu cầu: "Mỗi Chủ nhật ~05:30" / "Chưa bật lịch"; chờ nhận ⇒ waiting, quá 15 phút ⇒ stalled', () => {
    expect(offsiteScheduleText('systemd')).toBe('Mỗi Chủ nhật ~05:30');
    expect(offsiteScheduleText('')).toBe('Chưa bật lịch');
    expect(offsiteScheduleText(null)).toBe('Chưa bật lịch');
    const req = (minutes: number) => state({ request: { state: 'requested', action: 'set', requested_at: new Date(NOW - minutes * 60_000).toISOString() } });
    expect(offsiteRequestView(req(1), NOW)).toEqual({ kind: 'waiting', text: 'Đang chờ máy chủ nhận yêu cầu đổi nơi lưu…' });
    expect(offsiteRequestView(req(20), NOW).kind).toBe('stalled');
    expect(offsiteRequestView(state({ state: 'running' }), NOW).kind).toBe('running');
    expect(offsiteRequestView(state(), NOW).kind).toBe('none');
  });
});

describe('Bản sao ngoài máy — thẻ', () => {
  it('chưa có bản sao ⇒ "Chưa có bản sao ngoài máy" + cảnh báo vàng', async () => {
    mockFetch((c) => (c.url.endsWith('/system/offsite') ? json(200, state({ configured: false, dest: null, last_success_at: null, last_attempt_at: null, age_days: null, stale: true, verified: false, schedule: null, state: 'not_configured', error_code: 'GH-EB00' })) : undefined));
    renderPanel();
    expect(await screen.findByText('Chưa có bản sao ngoài máy')).toBeInTheDocument();
    const warn = screen.getByTestId('offsite-warning');
    expect(warn).toHaveAttribute('data-tone', 'warn');
    expect(warn).toHaveTextContent('Hỏng ổ đĩa là mất hết dữ liệu');
    expect(screen.getByText('Chưa chọn')).toBeInTheDocument();
    expect(screen.getByText('Chưa bật lịch')).toBeInTheDocument();
    // Chưa chọn nơi lưu không phải "lỗi" — không có khối lỗi riêng.
    expect(screen.queryByTestId('offsite-error')).toBeNull();
  });

  it.each([
    [8, 'warn'],
    [31, 'bad'],
  ])('lần thành công gần nhất %i ngày trước ⇒ cảnh báo tông %s', async (days, tone) => {
    mockFetch((c) => (c.url.endsWith('/system/offsite') ? json(200, state({ last_success_at: new Date(Date.now() - days * DAY - 3600_000).toISOString(), age_days: days, stale: true })) : undefined));
    renderPanel();
    const warn = await screen.findByTestId('offsite-warning');
    expect(warn).toHaveAttribute('data-tone', tone);
    expect(screen.getByTestId('offsite-latest')).toHaveTextContent(`(${days} ngày trước)`);
  });

  it('2 ngày ⇒ không cảnh báo; hiện nơi lưu, lịch tuần, mã khoá', async () => {
    mockFetch((c) => (c.url.endsWith('/system/offsite') ? json(200, state({ last_success_at: new Date(Date.now() - 2 * DAY - 3600_000).toISOString() })) : undefined));
    renderPanel();
    const latest = await screen.findByTestId('offsite-latest');
    expect(latest).toHaveTextContent(/Bản sao ngoài máy gần nhất: .* \(2 ngày trước\) · 1,2 GB · đã kiểm đọc lại được/);
    expect(screen.queryByTestId('offsite-warning')).toBeNull();
    expect(screen.getByText('/media/sep/GEN-USB')).toBeInTheDocument();
    expect(screen.getByText('Mỗi Chủ nhật ~05:30')).toBeInTheDocument();
    expect(screen.getByText('a1b2c3d4')).toBeInTheDocument();
  });

  it('state not_mounted ⇒ câu thân thiện + "Chi tiết kỹ thuật" chứa GH-EB01 (chuỗi, không object)', async () => {
    mockFetch((c) =>
      c.url.endsWith('/system/offsite')
        ? json(200, state({ state: 'not_mounted', error_code: 'GH-EB01', message: 'Chưa thấy ổ USB/NAS — cắm ổ vào máy chủ rồi thử lại.' }))
        : undefined,
    );
    renderPanel();
    const err = await screen.findByTestId('offsite-error');
    expect(err).toHaveTextContent('Chưa thấy ổ USB/NAS — cắm ổ vào máy chủ rồi thử lại.');
    const details = err.querySelector('details') as HTMLElement;
    expect(within(details).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(within(details).getByText('GH-EB01')).toBeInTheDocument();
    expect(screen.queryByText('[object Object]')).toBeNull();
  });

  it('Chọn nơi lưu: PUT đúng body; 423 ⇒ hộp PIN rồi gửi lại; xong ⇒ "Đang chờ máy chủ nhận…"', async () => {
    let pinOk = false;
    let current = state();
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/system/offsite') && c.method === 'GET') return json(200, current);
      if (c.url.endsWith('/system/offsite/destination') && c.method === 'PUT') {
        if (!pinOk) return PIN_REQUIRED();
        current = state({ request: { state: 'requested', action: 'set', requested_at: new Date().toISOString() } });
        return json(202, current);
      }
      if (c.url.endsWith('/auth/pin/verify')) {
        pinOk = true;
        return PIN_OK();
      }
      return undefined;
    });
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: 'Chọn nơi lưu bản sao ngoài máy' }));
    const dlg = await screen.findByRole('dialog', { name: 'Chọn nơi lưu bản sao ngoài máy' });
    expect(dlg).toHaveTextContent('Ổ phải đang cắm/đã mount; hệ thống không ghi vào ổ chính');
    expect(dlg).toHaveTextContent('/Volumes/<ổ>');
    expect(dlg).toHaveTextContent('E:\\GenBackup');
    const input = within(dlg).getByLabelText('Đường dẫn ổ USB/NAS trên máy chủ');
    await user.clear(input);
    await user.type(input, '/media/sep/USB2');
    await user.click(within(dlg).getByRole('button', { name: 'Lưu' }));
    await enterPin(user);

    await waitFor(() => expect(calls.filter((c) => c.url.endsWith('/system/offsite/destination'))).toHaveLength(2));
    for (const c of calls.filter((x) => x.url.endsWith('/destination'))) expect(c.body).toEqual({ path: '/media/sep/USB2' });
    expect(await screen.findByTestId('offsite-request')).toHaveTextContent('Đang chờ máy chủ nhận yêu cầu đổi nơi lưu…');
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Chọn nơi lưu bản sao ngoài máy' })).toBeNull());
  });

  it('409 OFFSITE_UNAVAILABLE ⇒ hiện manual_command trong khối mã có nút Chép', async () => {
    mockFetch((c) => {
      if (c.url.endsWith('/system/offsite') && c.method === 'GET') return json(200, state());
      if (c.url.endsWith('/system/offsite/destination')) {
        return json(409, { status: 409, code: 'OFFSITE_UNAVAILABLE', title: 'Máy chủ chưa nhận lệnh từ Console — chạy lệnh sau một lần trên máy chủ', manual_command: 'genh offsite set "E:\\GenBackup"' });
      }
      return undefined;
    });
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: 'Chọn nơi lưu bản sao ngoài máy' }));
    const dlg = await screen.findByRole('dialog', { name: 'Chọn nơi lưu bản sao ngoài máy' });
    const input = within(dlg).getByLabelText('Đường dẫn ổ USB/NAS trên máy chủ');
    await user.clear(input);
    await user.type(input, 'E:\\GenBackup');
    await user.click(within(dlg).getByRole('button', { name: 'Lưu' }));
    const cmd = await within(dlg).findByTestId('offsite-manual-command');
    expect(within(cmd).getByText('genh offsite set "E:\\GenBackup"')).toBeInTheDocument();
    expect(cmd).toHaveTextContent('Chạy lệnh dưới đây một lần trên máy chủ:');
    expect(within(cmd).getByRole('button', { name: 'Chép lệnh' })).toBeInTheDocument();
  });

  it('Manager chỉ thấy trạng thái + "Sao lưu ra ổ ngoài ngay", không thấy nút Owner', async () => {
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/system/offsite') && c.method === 'GET') return json(200, state());
      if (c.url.endsWith('/system/offsite/run') && c.method === 'POST') return json(202, state({ request: { state: 'requested', action: 'run', requested_at: new Date().toISOString() } }));
      return undefined;
    }, 'manager');
    const user = userEvent.setup();
    renderPanel('manager');
    await screen.findByTestId('offsite-latest');
    expect(screen.queryByRole('button', { name: 'Chọn nơi lưu bản sao ngoài máy' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Tải gói mang đi' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Bộ khôi phục' })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Sao lưu ra ổ ngoài ngay' }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/system/offsite/run') && c.method === 'POST')).toBe(true));
    expect(await screen.findByTestId('offsite-request')).toHaveTextContent('Đang chờ máy chủ nhận yêu cầu sao lưu ra ổ ngoài…');
  });

  it('Bộ khôi phục: PIN ⇒ khoá chữ to + mã QR SVG; đóng hộp ⇒ khoá biến khỏi DOM', async () => {
    const KEY = 'ABCDE-FGHIJ-KLMN2-OPQR3-STUV4-WXYZ5';
    let pinOk = false;
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/system/offsite')) return json(200, state());
      if (c.url.endsWith('/system/offsite/recovery-kit')) {
        if (!pinOk) return PIN_REQUIRED();
        return json(200, { key: KEY, key_id: 'a1b2c3d4', created_hint: '2026-09-28', steps: ['Cài Gen-Harness trên máy mới.', 'Nhập Khoá khôi phục khi được hỏi.'], warning: 'Ai có khoá này và ổ USB là mở được dữ liệu.' });
      }
      if (c.url.endsWith('/auth/pin/verify')) {
        pinOk = true;
        return PIN_OK();
      }
      return undefined;
    });
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: 'Bộ khôi phục' }));
    await enterPin(user);
    const dlg = await screen.findByRole('dialog', { name: 'Bộ khôi phục' });
    expect(await within(dlg).findByTestId('recovery-key')).toHaveTextContent(KEY);
    expect(dlg).toHaveTextContent('Cất TÁCH khỏi ổ USB');
    expect(within(dlg).getByTestId('recovery-key-created')).toHaveTextContent('Khoá tạo ngày 28/09/2026');
    expect(dlg).toHaveTextContent('Nhập Khoá khôi phục khi được hỏi.');
    const svg = dlg.querySelector('svg.rk-qr') as SVGElement;
    expect(svg).not.toBeNull();
    expect(svg.getAttribute('aria-label')).toBe('Mã QR của Khoá khôi phục');
    expect(svg.querySelector('path')?.getAttribute('d')?.length ?? 0).toBeGreaterThan(100);
    expect(calls.filter((c) => c.url.endsWith('/recovery-kit'))).toHaveLength(2);
    // Khoá không nằm lại trong bộ đệm truy vấn/mutation.
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(KEY);
    expect(JSON.stringify(queryClient.getMutationCache().getAll().map((m) => m.state.data))).not.toContain(KEY);

    await user.click(within(dlg).getByRole('button', { name: 'Đã cất xong' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Bộ khôi phục' })).toBeNull());
    expect(document.body.textContent).not.toContain(KEY);
    expect(document.body.classList.contains('rk-open')).toBe(false);
  });

  it('Tải gói mang đi: xác nhận + PIN rồi điều hướng khung ẩn tới URL portable (không fetch blob)', async () => {
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/system/offsite')) return json(200, state());
      if (c.url.endsWith('/auth/pin/verify')) return PIN_OK();
      return undefined;
    });
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: 'Tải gói mang đi' }));
    const dlg = await screen.findByRole('dialog', { name: 'Tải gói mang đi?' });
    expect(dlg).toHaveTextContent('mở bằng Bộ khôi phục');
    await user.click(within(dlg).getByRole('button', { name: 'Tải về' }));
    await enterPin(user);
    await waitFor(() => expect(document.getElementById('gh-portable-frame')).not.toBeNull());
    const frame = document.getElementById('gh-portable-frame') as HTMLIFrameElement;
    expect(frame.getAttribute('src')).toBe('/api/v1/system/offsite/portable');
    expect(calls.some((c) => c.url.includes('/system/offsite/portable'))).toBe(false);
    expect(calls.some((c) => c.url.endsWith('/auth/pin/verify'))).toBe(true);
  });
});

describe('Bản sao ngoài máy — câu theo vai trò, lệnh chạy tay, khoá, gói mang đi', () => {
  const OWNER = { isOwner: true, canManage: true };
  const MANAGER = { isOwner: false, canManage: true };
  const VIEWER = { isOwner: false, canManage: false };

  it('cảnh báo chỉ nhắc nút người xem CÓ: chưa cấu hình ⇒ Owner "Chọn nơi lưu", Manager/Viewer "Nhờ Owner"; đã cấu hình ⇒ Viewer "Báo Owner/quản trị"', () => {
    const fresh = state({ configured: false, state: 'not_configured', last_success_at: null, last_attempt_at: null, age_days: null, stale: true });
    expect(offsiteView(fresh, Date.now(), 'Asia/Ho_Chi_Minh', OWNER).warning).toContain('bấm "Chọn nơi lưu bản sao ngoài máy"');
    for (const who of [MANAGER, VIEWER]) {
      const w = offsiteView(fresh, Date.now(), 'Asia/Ho_Chi_Minh', who).warning ?? '';
      expect(w).toContain('Nhờ Owner cắm ổ USB/NAS vào máy chủ và chọn nơi lưu bản sao ngoài máy.');
      expect(w).not.toContain('Sao lưu ra ổ ngoài ngay');
    }
    const old = state({ last_success_at: new Date(Date.now() - 9 * DAY).toISOString(), age_days: 9, stale: true });
    expect(offsiteView(old, Date.now(), 'Asia/Ho_Chi_Minh', MANAGER).warning).toContain('bấm "Sao lưu ra ổ ngoài ngay"');
    const vw = offsiteView(old, Date.now(), 'Asia/Ho_Chi_Minh', VIEWER).warning ?? '';
    expect(vw).toContain('Báo Owner/quản trị cắm ổ USB/NAS và sao lưu ra ổ ngoài.');
    expect(vw).not.toContain('bấm');
  });

  it('thẻ Sức khoẻ: gợi ý dòng "Bản sao ngoài máy" theo cấu hình + vai trò (không bảo bấm nút đang khoá)', () => {
    const now = Date.now();
    const h: SystemHealth = {
      checked_at: new Date(now).toISOString(),
      overall: 'warn',
      worker: { state: 'ok', alive: true, last_seen_at: new Date(now - 60_000).toISOString(), silent_minutes: null },
      browser: { state: 'ok', last_heartbeat_at: new Date(now - 30_000).toISOString() },
      queues: [{ stream: 'gh:raw', dlq: 0 }],
      crons: [],
      backup: { configured: true, latest_at: new Date(now - 3_600_000).toISOString(), age_hours: 1, stale: false },
      update: { state: 'idle', failed: false, blocked_version: null, finished_at: null },
      disk: { state: 'ok', free_bytes: 40 * 1024 ** 3, min_bytes: 5 * 1024 ** 3, checked_at: new Date(now).toISOString() },
      issues: [],
      offsite: { state: 'not_configured', configured: false, last_success_at: null, age_days: null, stale: true, error_code: null, schedule: null },
    } as SystemHealth;
    const hint = (who: { isOwner: boolean; canManage: boolean }, hh: SystemHealth = h) =>
      healthRows(hh, Date.now(), 'Asia/Ho_Chi_Minh', who).find((r) => r.key === 'offsite')?.hint ?? '';
    expect(hint(OWNER)).toContain('bấm "Chọn nơi lưu bản sao ngoài máy"');
    expect(hint(MANAGER)).toContain('nhờ Owner cắm ổ USB/NAS');
    expect(hint(MANAGER)).not.toContain('Sao lưu ra ổ ngoài ngay');
    const cfg = { ...h, offsite: { ...h.offsite!, configured: true, state: 'ok', last_success_at: new Date(Date.now() - 9 * DAY).toISOString(), age_days: 9 } } as SystemHealth;
    expect(hint(MANAGER, cfg)).toContain('bấm "Sao lưu ra ổ ngoài ngay"');
    expect(hint(VIEWER, cfg)).toContain('báo Owner/quản trị');
    // Lỗi mà "cắm ổ rồi sao lưu lại" không giúp được: gợi ý theo mã lỗi.
    const eb07 = { ...cfg, offsite: { ...cfg.offsite!, state: 'failed', error_code: 'GH-EB07' } } as SystemHealth;
    expect(hint(OWNER, eb07)).toContain('bấm "Chọn nơi lưu bản sao ngoài máy" và chọn thư mục khác');
    expect(hint(MANAGER, eb07)).toContain('nhờ Owner chọn lại nơi lưu');
    expect(hint(MANAGER, eb07)).not.toContain('cắm ổ USB/NAS rồi bấm');
    const eb04 = { ...cfg, offsite: { ...cfg.offsite!, state: 'failed', error_code: 'GH-EB04' } } as SystemHealth;
    expect(hint(OWNER, eb04)).toContain('giải phóng chỗ trống trên ổ USB/NAS, kiểm tra ổ cho phép ghi');
    expect(hint(VIEWER, eb04)).toContain('báo Owner/quản trị giải phóng chỗ trống');
    expect(hint(OWNER, eb04)).not.toContain('cắm ổ USB/NAS rồi bấm');
  });

  it('Manager/Viewer khi chưa cấu hình: nút Sao lưu ngay khoá đúng lý do; Viewer không có nút nào', async () => {
    const fresh = state({ configured: false, state: 'not_configured', last_success_at: null, last_attempt_at: null, age_days: null, stale: true, dest: null });
    mockFetch((c) => (c.url.endsWith('/system/offsite') ? json(200, fresh) : undefined), 'manager');
    const { unmount } = renderPanel('manager');
    await screen.findByTestId('offsite-warning');
    const run = screen.getByRole('button', { name: 'Sao lưu ra ổ ngoài ngay' });
    expect(run).toBeDisabled();
    expect(run).toHaveAttribute('title', 'Owner chưa chọn nơi lưu bản sao ngoài máy');
    expect(screen.getByTestId('offsite-warning')).toHaveTextContent('Nhờ Owner');
    unmount();
    queryClient.clear();
    mockFetch((c) => (c.url.endsWith('/system/offsite') ? json(200, fresh) : undefined), 'auditor');
    renderPanel('auditor');
    expect(await screen.findByTestId('offsite-warning')).toHaveTextContent('Nhờ Owner');
    expect(screen.queryByRole('button', { name: 'Sao lưu ra ổ ngoài ngay' })).toBeNull();
  });

  it('yêu cầu bị kẹt ⇒ lệnh chạy tay của CHÍNH yêu cầu (vd genh offsite run); không ghép được lệnh ⇒ câu hướng dẫn, không chỗ giữ chỗ', async () => {
    const old = new Date(Date.now() - 30 * 60_000).toISOString();
    mockFetch((c) =>
      c.url.endsWith('/system/offsite')
        ? json(200, state({ request: { state: 'stalled', action: 'run', requested_at: old }, can_request: false, manual_command: 'genh offsite run' }))
        : undefined,
    );
    const { unmount } = renderPanel();
    const cmd = await screen.findByTestId('offsite-manual-command');
    expect(cmd).toHaveTextContent('Chạy lệnh dưới đây một lần trên máy chủ:');
    expect(within(cmd).getByText('genh offsite run')).toBeInTheDocument();
    unmount();
    queryClient.clear();
    mockFetch((c) =>
      c.url.endsWith('/system/offsite')
        ? json(200, state({ request: { state: 'stalled', action: 'set', requested_at: old }, can_request: false, manual_command: null }))
        : undefined,
    );
    renderPanel();
    expect(await screen.findByTestId('offsite-manual-fallback')).toHaveTextContent('genh offsite set" kèm đường dẫn đầy đủ');
    expect(screen.queryByTestId('offsite-manual-command')).toBeNull();
    expect(document.body.textContent).not.toContain('<path>');
  });

  it('chưa có Khoá khôi phục ⇒ "Tải gói mang đi"/"Bộ khôi phục" khoá sẵn, ghi lý do "genh update" (không bắt nhập PIN rồi mới báo)', async () => {
    const calls = mockFetch((c) => (c.url.endsWith('/system/offsite') ? json(200, state({ key_present: false })) : undefined));
    renderPanel();
    const portable = await screen.findByRole('button', { name: 'Tải gói mang đi' });
    const kit = screen.getByRole('button', { name: 'Bộ khôi phục' });
    const why = 'Máy chủ chưa có Khoá khôi phục — chạy "genh update" một lần trên máy chủ.';
    expect(portable).toBeDisabled();
    expect(kit).toBeDisabled();
    expect(portable).toHaveAttribute('title', why);
    expect(screen.getByTestId('offsite-key-missing')).toHaveTextContent(why);
    expect(offsiteApiErrorText(new ApiError(409, { code: 'OFFSITE_KEY_MISSING', title: 'x' }))).toBe(why);
    expect(calls.some((c) => c.url.endsWith('/auth/pin/verify'))).toBe(false);
  });

  it('Tải gói mang đi: đang chuẩn bị ⇒ nút khoá + dòng "đừng tải lại"; lỗi máy chủ ⇒ lỗi nằm trên thẻ kèm "Chi tiết kỹ thuật", mở khoá nút', async () => {
    mockFetch((c) => {
      if (c.url.endsWith('/system/offsite')) return json(200, state());
      if (c.url.endsWith('/auth/me')) return json(200, meAs('owner', new Date(Date.now() + 1800_000).toISOString()));
      return undefined;
    });
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: 'Tải gói mang đi' }));
    const dlg = await screen.findByRole('dialog', { name: 'Tải gói mang đi?' });
    expect(dlg).toHaveTextContent('Đừng tải lại hay đóng trang cho tới khi trình duyệt bắt đầu tải');
    await user.click(within(dlg).getByRole('button', { name: 'Tải về' }));
    await waitFor(() => expect(document.getElementById('gh-portable-frame')).not.toBeNull());
    const frame = document.getElementById('gh-portable-frame') as HTMLIFrameElement;
    expect(await screen.findByTestId('offsite-portable-preparing')).toHaveTextContent('đừng tải lại hay đóng trang');
    expect(screen.getByRole('button', { name: 'Tải gói mang đi' })).toBeDisabled();

    // Máy chủ trả trang lỗi JSON vào khung ⇒ báo trên thẻ (không chỉ toast), khung cũ không bị gỡ trước đó.
    const page = JSON.stringify({ status: 500, code: 'PORTABLE_FAILED', title: 'Không tạo được gói mang đi' });
    Object.defineProperty(frame, 'contentDocument', { configurable: true, value: { body: { textContent: page } } });
    act(() => {
      frame.dispatchEvent(new Event('load'));
    });
    const err = await screen.findByTestId('offsite-portable-error');
    expect(err).toHaveTextContent('Không tạo được gói mang đi');
    expect(within(err).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(err).toHaveTextContent('PORTABLE_FAILED');
    expect(screen.queryByTestId('offsite-portable-preparing')).toBeNull();
    expect(screen.getByRole('button', { name: 'Tải gói mang đi' })).toBeEnabled();
    expect(screen.queryByText('[object Object]')).toBeNull();
  });

  it('Bộ khôi phục: huỷ PIN ⇒ không có "Chi tiết kỹ thuật" rỗng, có nút Thử lại gọi lại', async () => {
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/system/offsite')) return json(200, state());
      if (c.url.endsWith('/system/offsite/recovery-kit')) return PIN_REQUIRED();
      return undefined;
    });
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: 'Bộ khôi phục' }));
    const pin = await screen.findByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await user.click(within(pin).getByRole('button', { name: /Huỷ/ }));
    const dlg = await screen.findByRole('dialog', { name: 'Bộ khôi phục' });
    expect(await within(dlg).findByText(/Chưa mở — cần nhập mã PIN\./)).toBeInTheDocument();
    expect(within(dlg).queryByText('Chi tiết kỹ thuật')).toBeNull();
    const before = calls.filter((c) => c.url.endsWith('/recovery-kit')).length;
    await user.click(within(dlg).getByRole('button', { name: 'Thử lại' }));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith('/recovery-kit')).length).toBeGreaterThan(before));
  });

  it('nút Chép khi trình duyệt không có navigator.clipboard (http) ⇒ báo "Không chép được", không im lặng', async () => {
    const old = new Date(Date.now() - 30 * 60_000).toISOString();
    mockFetch((c) =>
      c.url.endsWith('/system/offsite')
        ? json(200, state({ request: { state: 'stalled', action: 'run', requested_at: old }, manual_command: 'genh offsite run' }))
        : undefined,
    );
    const orig = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
    try {
      const user = userEvent.setup();
      renderPanel();
      const cmd = await screen.findByTestId('offsite-manual-command');
      Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true });
      await user.click(within(cmd).getByRole('button', { name: 'Chép lệnh' }));
      expect(useToasts.getState().toasts.some((t) => t.text === 'Không chép được — bôi đen lệnh rồi chép tay.')).toBe(true);
    } finally {
      if (orig) Object.defineProperty(navigator, 'clipboard', orig);
      else delete (navigator as unknown as { clipboard?: unknown }).clipboard;
    }
  });
});

describe('Chuông — kind mới v0.1.40', () => {
  it.each(['offsite.stale', 'job.timeout'])('notification %s ⇒ làm mới [system, health]', async (kind) => {
    const first: NotificationItem = { id: 'a', kind: 'backup.done', title: 'Sao lưu đã xong', body: '', link: null, created_at: new Date().toISOString(), read: true };
    const page: NotificationsPage = { items: [first], unread: 0 };
    mockFetch((c) => (c.url.includes('/notifications') ? json(200, page) : undefined));
    queryClient.setQueryData(qk.me, meAs('owner'));
    const spy = vi.spyOn(queryClient, 'invalidateQueries');
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <NotificationBell />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await screen.findByRole('button', { name: 'Thông báo' });
    await waitFor(() => expect(queryClient.getQueryData(qk.notifications)).toBeDefined());
    // Lần tải đầu chỉ ghi nhận, không làm mới.
    await new Promise((r) => setTimeout(r, 20));
    expect(spy.mock.calls.some(([f]) => JSON.stringify(f?.queryKey) === JSON.stringify(qkSystem.health))).toBe(false);
    const fresh: NotificationItem = { id: 'b', kind, title: 'Sự cố', body: '', link: '/system?tab=storage&focus=offsite', created_at: new Date().toISOString(), read: false };
    act(() => {
      queryClient.setQueryData<NotificationsPage>(qk.notifications, { items: [fresh, first], unread: 1 });
    });
    await waitFor(() => expect(spy.mock.calls.some(([f]) => JSON.stringify(f?.queryKey) === JSON.stringify(qkSystem.health))).toBe(true));
    spy.mockRestore();
  });
});
