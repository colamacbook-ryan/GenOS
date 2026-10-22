import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { ApiError, createApiClient, createEndpoints, type BackupRestoreStatus, type BackupsPage } from '@gen-harness/contracts';
import { BackupPanel } from '../../src/screens/system/BackupPanel';
import { downloadName, jobView, restoreView, scheduleText, triggerLabel } from '../../src/screens/system/backupModel';
import { qk } from '../../src/lib/queries';

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

const KEY1 = 'backups/20260928T190000Z-1a2b3c4d.pgcustom.enc';
const KEY2 = 'backups/20260927T190000Z-5e6f7a8b.pgcustom.enc';
const IDLE: BackupRestoreStatus = {
  can_request: true, state: 'idle', key: null, safety_key: null, message: null, started_at: null, finished_at: null, requested_at: null,
};
const PAGE: BackupsPage = {
  items: [
    { key: KEY1, taken_at: '2026-09-28T19:00:00Z', size_bytes: 48_300_000, trigger: 'scheduled', encrypted: true, key_id: 'backup' },
    { key: KEY2, taken_at: '2026-09-27T19:00:00Z', size_bytes: 47_000_000, trigger: null, encrypted: true, key_id: 'master' },
  ],
  schedule: { frequency: 'daily', time_of_day: '02:00' },
  timezone: 'Asia/Ho_Chi_Minh',
  retention: { daily: 7, weekly: 4, monthly: 12, recent_hours: 24 },
  job: null,
  restore: IDLE,
};

function me(role: 'owner' | 'manager') {
  return {
    id: 'u', email: 'owner@genesis.local', display_name: 'Anh Cơ', role: { code: role, name: role },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
    addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, must_change_password: false,
    permissions: { 'system.read': 'all', 'system.manage': 'all' },
  };
}

function renderPanel(role: 'owner' | 'manager' = 'owner') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, me(role));
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <BackupPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return qc;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('backupModel', () => {
  it('nhãn nguồn, lịch và tên tệp tải về', () => {
    expect(triggerLabel('pre-update')).toBe('Trước khi cập nhật');
    expect(triggerLabel(null)).toBe('Không rõ');
    expect(scheduleText({ frequency: 'weekly', time_of_day: '03:30' })).toBe('Hằng tuần (thứ Hai) lúc 03:30');
    expect(scheduleText(null)).toBe('Chưa đặt lịch tự động');
    expect(downloadName(KEY1)).toBe('gen-harness-20260928T190000Z-1a2b3c4d.pgcustom.enc');
    expect(jobView({ state: 'running' }).kind).toBe('working');
    expect(jobView({ state: 'failed', message: 'Chưa sao lưu được — thử lại.', technical: 'pg_dump lỗi' })).toEqual({
      kind: 'failed', text: 'Chưa sao lưu được — thử lại.', technical: 'pg_dump lỗi',
    });
  });

  it('tiến trình khôi phục: requested → running → api tắt → xong thì tải lại', () => {
    const NOW = Date.parse('2026-09-29T10:00:00Z');
    const steps = (r: BackupRestoreStatus | undefined, offline = false) => {
      const v = restoreView(r, { waiting: true, offline, now: NOW });
      return v.kind === 'working' ? v.steps.map((s) => s.state) : v.kind;
    };
    expect(steps({ ...IDLE, state: 'requested', key: KEY1 })).toEqual(['active', 'todo', 'todo']);
    expect(steps({ ...IDLE, state: 'running' })).toEqual(['done', 'active', 'todo']);
    expect(steps(undefined, true)).toEqual(['done', 'active', 'todo']);
    expect(steps({ ...IDLE, state: 'done', finished_at: '2026-09-29T09:59:00Z' })).toBe('finished');
    const failed = restoreView({ ...IDLE, state: 'failed', message: 'pg_restore lỗi', finished_at: '2026-09-29T09:00:00Z' }, { waiting: false, offline: false, now: NOW });
    expect(failed.kind === 'failed' && failed.technical).toBe('pg_restore lỗi');
    expect(failed.kind === 'failed' && failed.body).not.toMatch(/pg_restore/);
    const stalled = restoreView({ ...IDLE, state: 'stalled', key: KEY1 }, { waiting: false, offline: false, now: NOW });
    expect(stalled.kind === 'stalled' && stalled.command).toBe(`~/.gen-harness/bin/genh restore ${KEY1}`);
    expect(restoreView(IDLE, { waiting: false, offline: false }).kind).toBe('hidden');
  });
});

describe('<BackupPanel>', () => {
  beforeEach(() => {
    Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:x'), revokeObjectURL: vi.fn() });
  });

  it('liệt kê bản sao lưu với thời điểm, nguồn, dung lượng, mã hoá và lịch tự động', async () => {
    mockFetch((c) => (c.url.endsWith('/system/backups') ? json(200, PAGE) : json(404)));
    renderPanel();
    const row = (await screen.findByText('29/09 02:00')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Theo lịch')).toBeTruthy();
    expect(within(row).getByText('46.1 MB')).toBeTruthy();
    expect(within(row).getByText('Đã mã hoá')).toBeTruthy();
    expect(screen.getByText('Không rõ')).toBeTruthy();
    expect(screen.getByText('Hằng ngày lúc 02:00')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: /Khôi phục bản/ })).toHaveLength(2);
  });

  it('không phải Owner thì không có nút Tải về / Khôi phục', async () => {
    mockFetch((c) => (c.url.endsWith('/system/backups') ? json(200, PAGE) : json(404)));
    renderPanel('manager');
    await screen.findByText('29/09 02:00');
    expect(screen.queryByRole('button', { name: /Tải về bản/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Khôi phục bản/ })).toBeNull();
    expect(screen.getByRole('button', { name: 'Sao lưu ngay' })).toBeTruthy();
  });

  it('Sao lưu ngay → POST /system/backups rồi hỏi lại tới khi xong', async () => {
    let page: BackupsPage = PAGE;
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/system/backups') && c.method === 'POST') {
        page = { ...PAGE, job: { state: 'queued', requested_at: '2026-09-29T10:00:00Z' } };
        return json(202, page);
      }
      if (c.url.endsWith('/system/backups')) {
        const cur = page;
        page = { ...PAGE, job: { state: 'done', key: KEY1 } };
        return json(200, cur);
      }
      return json(404);
    });
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: 'Sao lưu ngay' }));
    await screen.findByText('Đang chờ Bộ xử lý nền nhận việc sao lưu…');
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
    await waitFor(() => expect(screen.queryByText('Đang chờ Bộ xử lý nền nhận việc sao lưu…')).toBeNull(), { timeout: 4000 });
  });

  it('Sao lưu lỗi: câu thân thiện hiện sẵn, lỗi gốc chỉ nằm trong "Chi tiết kỹ thuật"', async () => {
    const friendly = 'Chưa sao lưu được — thử lại; nếu vẫn lỗi gửi gói chẩn đoán.';
    mockFetch((c) => (c.url.endsWith('/system/backups')
      ? json(200, { ...PAGE, job: { state: 'failed', message: friendly, technical: 'pg_dump: error: FATAL password' } })
      : json(404)));
    renderPanel();
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText(friendly)).toBeTruthy();
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeTruthy();
    const code = within(alert).getByText('pg_dump: error: FATAL password');
    expect(code.closest('details')).not.toBeNull();
    expect(alert.textContent).not.toMatch(/worker/i);
  });

  it('Tải về gọi GET /system/backups/download?key=… và lưu tệp', async () => {
    const calls = mockFetch((c) => {
      if (c.url.includes('/system/backups/download')) return new Response('ENC', { status: 200, headers: { 'Content-Type': 'application/octet-stream' } });
      if (c.url.endsWith('/system/backups')) return json(200, PAGE);
      return json(404);
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: 'Tải về bản 29/09 02:00' }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    const dl = calls.find((c) => c.url.includes('/download'));
    expect(dl?.url).toBe(`/api/v1/system/backups/download?key=${encodeURIComponent(KEY1)}`);
  });

  it('Khôi phục: phải gõ đúng "KHÔI PHỤC", gửi {key, confirm} rồi hiện tiến trình', async () => {
    let page: BackupsPage = PAGE;
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/system/backups/restore')) {
        page = { ...PAGE, restore: { ...IDLE, state: 'requested', key: KEY1, requested_at: new Date().toISOString() } };
        return json(202, page);
      }
      if (c.url.endsWith('/system/backups')) return json(200, page);
      return json(404);
    });
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: 'Khôi phục bản 29/09 02:00' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Khôi phục bản sao lưu 29/09 02:00?')).toBeTruthy();
    const go = within(dialog).getByRole('button', { name: 'Khôi phục' }) as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    await user.type(within(dialog).getByLabelText('Gõ KHÔI PHỤC để xác nhận'), 'khôi phục');
    expect(go.disabled).toBe(true);
    await user.clear(within(dialog).getByLabelText('Gõ KHÔI PHỤC để xác nhận'));
    await user.type(within(dialog).getByLabelText('Gõ KHÔI PHỤC để xác nhận'), 'KHÔI PHỤC');
    expect(go.disabled).toBe(false);
    await user.click(go);

    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/system/backups/restore'))).toBe(true));
    expect(calls.find((c) => c.url.endsWith('/restore'))?.body).toEqual({ key: KEY1, confirm: 'KHÔI PHỤC' });
    await screen.findByText('Đang khôi phục bản 29/09 02:00');
    expect(screen.getByText('Nhận yêu cầu')).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('máy chủ chưa có watcher khôi phục → hiện lệnh genh update, nút Khôi phục tắt', async () => {
    mockFetch((c) => (c.url.endsWith('/system/backups') ? json(200, { ...PAGE, restore: { ...IDLE, can_request: false } }) : json(404)));
    renderPanel();
    await screen.findByText('~/.gen-harness/bin/genh update');
    for (const b of screen.getAllByRole('button', { name: /Khôi phục bản/ })) expect((b as HTMLButtonElement).disabled).toBe(true);
  });

  it('sửa lịch sao lưu gửi PUT /system/backups/schedule', async () => {
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/system/backups/schedule')) return json(200, { ...PAGE, schedule: c.body });
      if (c.url.endsWith('/system/backups')) return json(200, PAGE);
      return json(404);
    });
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole('button', { name: 'Sửa lịch' }));
    await user.click(screen.getByRole('radio', { name: 'Hằng tuần' }));
    const time = screen.getByLabelText('Giờ chạy (HH:MM)');
    await user.clear(time);
    await user.type(time, '25:00');
    expect((screen.getByRole('button', { name: 'Lưu lịch' }) as HTMLButtonElement).disabled).toBe(true);
    await user.clear(time);
    await user.type(time, '03:30');
    await user.click(screen.getByRole('button', { name: 'Lưu lịch' }));
    await screen.findByText('Hằng tuần (thứ Hai) lúc 03:30');
    expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ frequency: 'weekly', time_of_day: '03:30' });
  });
});

describe('API client › PASSWORD_CHANGE_REQUIRED', () => {
  it('403 PASSWORD_CHANGE_REQUIRED gọi onPasswordChangeRequired; 403 khác thì không', async () => {
    const onPasswordChangeRequired = vi.fn();
    const responses = [
      json(403, { code: 'PASSWORD_CHANGE_REQUIRED', title: 'Cần đặt mật khẩu mới' }),
      json(403, { code: 'FORBIDDEN', title: 'Không có quyền' }),
    ];
    const fetchFn = vi.fn(async () => responses.shift() as Response) as unknown as typeof fetch;
    const api = createEndpoints(createApiClient({ fetch: fetchFn, onPasswordChangeRequired }));
    await expect(api.backups.list()).rejects.toBeInstanceOf(ApiError);
    expect(onPasswordChangeRequired).toHaveBeenCalledTimes(1);
    await expect(api.backups.list()).rejects.toBeInstanceOf(ApiError);
    expect(onPasswordChangeRequired).toHaveBeenCalledTimes(1);
  });
});
