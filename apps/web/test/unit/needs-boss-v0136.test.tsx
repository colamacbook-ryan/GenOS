import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { HealthIssue, Overview, SystemHealth } from '@gen-harness/contracts';
import { OverviewScreen } from '../../src/screens/queue/OverviewScreen';
import { healthRows, healthTips, sortIssues } from '../../src/screens/system/healthModel';
import { HealthCard } from '../../src/screens/system/HealthCard';
import { UpdateCard } from '../../src/update/UpdateCard';
import { qk } from '../../src/lib/queries';
import { queryClient as appQueryClient } from '../../src/lib/queryClient';

/**
 * v0.1.36 (F-6): dải "Cần Sếp xử lý" đầu Tổng quan (sự cố `GET /system/health` + "Chưa có model"), hàm thuần
 * `healthModel` của thẻ "Sức khoẻ hệ thống", và thẻ cập nhật ở Tổng quan không lặp lại lỗi đã có trong dải.
 */

const json = (status: number, body?: unknown, type = 'application/json') =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': type } });

interface Call {
  url: string;
  method: string;
}
function mockFetch(handler: (c: Call) => Response | Promise<Response>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const c = { url: String(url), method: init?.method ?? 'GET' };
      calls.push(c);
      return handler(c);
    }),
  );
  return calls;
}

const OWNER_PERMS = { 'overview.read': 'all', 'system.read': 'all', 'system.manage': 'all' };
function meWith(permissions: Record<string, string>, role = 'owner') {
  return {
    id: 'u', email: 'owner@genesis.local', display_name: 'Anh Cơ',
    role: { code: role, name: role },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
    addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
    permissions,
  };
}

function renderUi(ui: ReactElement, permissions: Record<string, string> = OWNER_PERMS) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, meWith(permissions));
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  appQueryClient.clear();
});

const OVERVIEW: Overview = {
  kpis: [{ key: 'channels_live', label: 'Kênh sống', value: 4, unit: null, row: 1, status: 'ok', sublabel: null, pct: null, filter: null }],
  queue: [],
  spotlight: [],
  signals: [],
  health: { channels: [{ type: 'zalo', active: 1 }], plugins: { healthy: 9, degraded: 0, isolated: 0 }, backlog_pending: 0 },
  dataQuality: { missing_identity_pct: 0, low_confidence_score_pct: 0, unassigned_event_pct: 0 },
  hourly: [],
};

const NOW = Date.parse('2026-10-02T03:00:00Z');
function health(over: Partial<SystemHealth> = {}): SystemHealth {
  return {
    checked_at: new Date(NOW).toISOString(),
    overall: 'ok',
    worker: { state: 'ok', alive: true, last_seen_at: new Date(NOW - 60_000).toISOString(), silent_minutes: null },
    browser: { state: 'ok', last_heartbeat_at: new Date(NOW - 30_000).toISOString() },
    queues: [{ stream: 'gh:raw', dlq: 0 }],
    crons: [{ name: 'backup_scheduled', last_at: new Date(NOW - 3_600_000).toISOString(), ok: true }],
    backup: { configured: true, latest_at: new Date(NOW - 3_600_000).toISOString(), age_hours: 1, stale: false },
    update: { state: 'idle', failed: false, blocked_version: null, finished_at: null },
    disk: { state: 'ok', free_bytes: 40 * 1024 ** 3, min_bytes: 5 * 1024 ** 3, checked_at: new Date(NOW).toISOString() },
    issues: [],
    ...over,
  };
}
const CHANNEL_DOWN: HealthIssue = {
  key: 'channel.down:zalo', kind: 'channel.down', severity: 'bad', title: 'Kênh Zalo đã ngắt kết nối',
  body: 'Tin nhắn mới không về kho thô tới khi Sếp đăng nhập lại.', link: '/system?tab=channels', action: 'Đăng nhập lại', raised_at: '2026-10-02T01:00:00Z',
};
const MODEL_EXPIRED: HealthIssue = {
  key: 'model.auth_expired:claude', kind: 'model.auth_expired', severity: 'warn', title: 'Model hết phiên đăng nhập',
  body: 'Gen đang dùng model dự phòng.', link: '/system?tab=brain', action: 'Đăng nhập lại model', raised_at: '2026-10-02T02:00:00Z',
};

const followUp = (done: boolean) => [{ n: 4, key: 'brain', title: 'Bộ não AI', status: done ? 'done' : 'skipped', done }];

function route(opts: { health?: () => Response; step4Done?: boolean }) {
  return (c: Call) => {
    if (c.url.includes('/system/health')) return opts.health ? opts.health() : json(200, health());
    if (c.url.includes('/setup/follow-up')) return json(200, followUp(opts.step4Done ?? true));
    if (c.url.includes('/overview')) return json(200, OVERVIEW);
    return json(404, { title: 'Không tìm thấy', status: 404, code: 'NOT_FOUND' }, 'application/problem+json');
  };
}

describe('v0.1.36 — dải "Cần Sếp xử lý" đầu Tổng quan', () => {
  it('(a) có 2 sự cố ⇒ tiêu đề + 2 dòng theo thứ tự bad → warn, nút "Đăng nhập lại" mở /system?tab=channels', async () => {
    mockFetch(route({ health: () => json(200, health({ overall: 'bad', issues: [MODEL_EXPIRED, CHANNEL_DOWN] })) }));
    renderUi(<OverviewScreen />);
    const strip = await screen.findByTestId('needs-boss');
    expect(within(strip).getByRole('heading', { name: 'Cần Sếp xử lý' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Cần Sếp xử lý' })).toBe(strip);
    const rows = within(strip).getAllByTestId('needs-boss-row');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('Kênh Zalo đã ngắt kết nối');
    expect(rows[0]).toHaveAttribute('data-severity', 'bad');
    expect(rows[1]).toHaveTextContent('Model hết phiên đăng nhập');
    expect(rows[1]).toHaveAttribute('data-severity', 'warn');
    expect(within(rows[0]).getByRole('link', { name: 'Đăng nhập lại' })).toHaveAttribute('href', '/system?tab=channels');
    expect(within(rows[1]).getByRole('link', { name: 'Đăng nhập lại model' })).toHaveAttribute('href', '/system?tab=brain');
    // Dải đứng ĐẦU trang (trước hàng KPI).
    const kpi = await screen.findByText('Kênh sống');
    expect(strip.compareDocumentPosition(kpi) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('(b) không sự cố + bước 4 đã xong ⇒ không vẽ dải', async () => {
    const calls = mockFetch(route({ step4Done: true }));
    renderUi(<OverviewScreen />);
    await screen.findByText('Kênh sống');
    await waitFor(() => expect(calls.some((c) => c.url.includes('/system/health'))).toBe(true));
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId('needs-boss')).toBeNull();
  });

  it('(c) bước 4 chưa xong ⇒ "Chưa có model" (data-testid no-model) nằm trong dải', async () => {
    mockFetch(route({ step4Done: false }));
    renderUi(<OverviewScreen />);
    const strip = await screen.findByTestId('needs-boss');
    const noModel = within(strip).getByTestId('no-model');
    expect(noModel).toHaveTextContent('Chưa có model');
    expect(within(noModel).getByRole('link', { name: /Chọn model/ })).toHaveAttribute('href', '/setup?buoc=nguon-ai');
  });

  it('(d2) Auditor (system.read, không system.manage) ⇒ không gọi /system/health từ Tổng quan, không có dải/nút hành động', async () => {
    const calls = mockFetch(route({ step4Done: true, health: () => json(200, health({ overall: 'bad', issues: [CHANNEL_DOWN] })) }));
    const { container } = renderUi(<OverviewScreen />, { 'overview.read': 'all', 'system.read': 'all' });
    await screen.findByText('Kênh sống');
    await new Promise((r) => setTimeout(r, 30));
    expect(calls.some((c) => c.url.includes('/system/health'))).toBe(false);
    expect(screen.queryByTestId('needs-boss')).toBeNull();
    expect(container.textContent).not.toContain('Cần Sếp');
    expect(screen.queryByRole('link', { name: 'Đăng nhập lại' })).toBeNull();
  });

  it('(d) vai trò không có system.read ⇒ không gọi /system/health', async () => {
    const calls = mockFetch(route({ step4Done: true }));
    renderUi(<OverviewScreen />, { 'overview.read': 'all' });
    await screen.findByText('Kênh sống');
    await new Promise((r) => setTimeout(r, 30));
    expect(calls.some((c) => c.url.includes('/system/health'))).toBe(false);
    expect(screen.queryByTestId('needs-boss')).toBeNull();
  });

  it('(e) /system/health lỗi 500 ⇒ Tổng quan vẫn hiện, không có "[object Object]"', async () => {
    const calls = mockFetch(
      route({
        step4Done: true,
        health: () => json(500, { type: 'about:blank', title: 'Lỗi máy chủ', status: 500, code: 'INTERNAL', error_id: 'e-1', detail: { boom: true } }, 'application/problem+json'),
      }),
    );
    const { container } = renderUi(<OverviewScreen />);
    await screen.findByText('Kênh sống');
    await waitFor(() => expect(calls.some((c) => c.url.includes('/system/health'))).toBe(true));
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId('needs-boss')).toBeNull();
    expect(container.textContent).not.toContain('[object Object]');
  });
});

describe('v0.1.36 — healthModel (thẻ "Sức khoẻ hệ thống")', () => {
  const byKey = (h: SystemHealth) => Object.fromEntries(healthRows(h, NOW).map((r) => [r.key, r]));

  it('(f) Bộ xử lý nền ngừng 14 phút ⇒ "Đã ngừng 14 phút" (bad); sao lưu quá hạn ⇒ "Quá 36 giờ chưa sao lưu" (bad)', () => {
    const rows = byKey(
      health({
        worker: { state: 'silent', alive: false, last_seen_at: new Date(NOW - 14 * 60_000).toISOString(), silent_minutes: 14 },
        backup: { configured: true, latest_at: new Date(NOW - 40 * 3_600_000).toISOString(), age_hours: 40, stale: true },
      }),
    );
    expect(rows.worker.value).toBe('Đã ngừng 14 phút');
    expect(rows.worker.tone).toBe('bad');
    expect(rows.backup.value).toBe('Quá 36 giờ chưa sao lưu');
    expect(rows.backup.tone).toBe('bad');
  });

  it('sao lưu hằng tuần quá hạn ⇒ chữ theo hạn API trả ("Quá một tuần chưa sao lưu")', () => {
    const rows = byKey(health({ backup: { configured: true, latest_at: new Date(NOW - 9 * 86_400_000).toISOString(), age_hours: 216, stale: true, frequency: 'weekly', stale_after: 'một tuần' } }));
    expect(rows.backup.value).toBe('Quá một tuần chưa sao lưu');
  });

  it('cập nhật lỗi đã quá 24 giờ (API trả failed=false, state vẫn failed) ⇒ "Bình thường", không còn báo lỗi', () => {
    const rows = byKey(health({ update: { state: 'failed', failed: false, blocked_version: null, finished_at: new Date(NOW - 25 * 3_600_000).toISOString() } }));
    expect(rows.update.value).toBe('Bình thường');
    expect(rows.update.tone).toBe('ok');
  });

  it('nhãn "Việc nền bị lỗi" (không lộ "DLQ" ở dòng chính), trình duyệt nền ngừng ⇒ "Ngừng từ …"', () => {
    const rows = byKey(health({ queues: [{ stream: 'gh:raw', dlq: 2 }], browser: { state: 'silent', last_heartbeat_at: new Date(NOW - 5 * 60_000).toISOString() } }));
    expect(rows.dlq.label).toBe('Việc nền bị lỗi');
    expect(rows.dlq.value).toBe('2 việc');
    // Dòng vàng phải nói Sếp cần làm gì: hàng lỗi KHÔNG tự hết (F102) ⇒ xem rồi "Đã xem, xoá".
    expect(rows.dlq.hint).not.toContain('Thường tự hết');
    expect(rows.dlq.hint).toContain('sẽ không tự hết');
    expect(rows.dlq.hint).toContain('Đã xem, xoá');
    expect(rows.dlq.hint).toContain('Báo lỗi');
    expect(rows.browser.value).toBe('Ngừng từ 5 phút trước');
  });

  it('healthTips: ổ đĩa sắp đầy ⇒ hướng dẫn giải phóng kèm lệnh + cảnh báo không xoá volume; Bộ xử lý nền ngừng ⇒ lệnh khởi động lại', () => {
    expect(healthTips(health())).toEqual([]);
    const tips = healthTips(
      health({
        disk: { state: 'low', free_bytes: 2 * 1024 ** 3, min_bytes: 5 * 1024 ** 3, checked_at: null },
        worker: { state: 'silent', alive: false, last_seen_at: null, silent_minutes: 20 },
      }),
    );
    expect(tips.map((t) => t.key)).toEqual(['disk', 'worker']);
    expect(tips[0].steps.map((s) => s.cmd).filter(Boolean)).toEqual(['genh status', 'docker system prune', 'genh update']);
    // Ổ đĩa chỉ đo lại khi genh cập nhật — không hứa "tự cập nhật mỗi phút".
    expect(tips[0].steps.some((s) => s.text.includes('mỗi phút'))).toBe(false);
    expect(tips[0].steps[3].text).toContain('lần cập nhật tự động đêm nay');
    expect(tips[0].steps.some((s) => s.text.includes('5,0 GB'))).toBe(true);
    expect(tips[0].warning).toContain('volume');
    expect(tips[1].steps.map((s) => s.cmd).filter(Boolean)).toEqual(['genh stop', 'genh start', 'genh logs worker']);
  });

  it('HealthCard: ổ đĩa sắp đầy ⇒ thẻ có mục "Cách giải phóng chỗ trống" (đích của nút "Xem cách giải phóng")', async () => {
    mockFetch((c) =>
      c.url.includes('/system/health')
        ? json(200, health({ overall: 'bad', disk: { state: 'low', free_bytes: 2 * 1024 ** 3, min_bytes: 5 * 1024 ** 3, checked_at: null } }))
        : json(404),
    );
    renderUi(<HealthCard />);
    const tip = await screen.findByTestId('health-tip-disk');
    expect(tip).toHaveTextContent('Cách giải phóng chỗ trống');
    expect(within(tip).getByText('docker system prune')).toBeInTheDocument();
    expect(screen.queryByTestId('health-tip-worker')).toBeNull();
  });

  it('khoẻ: "Đang chạy · lần cuối …", sao lưu "Bản mới nhất …", ổ đĩa "Còn … GB trống", hàng lỗi 0', () => {
    const rows = byKey(health());
    expect(rows.worker.value).toBe('Đang chạy · lần cuối 1 phút trước');
    expect(rows.backup.value).toBe('Bản mới nhất 1 giờ trước');
    expect(rows.disk.value).toBe('Còn 40,0 GB trống · đo lúc 02/10 10:00');
    expect(rows.dlq.tone).toBe('ok');
    expect(rows.dlq.hint).toBeUndefined();
    expect(rows.update.value).toBe('Bình thường');
    for (const r of Object.values(rows)) expect(typeof r.value).toBe('string');
  });

  it('các trạng thái khác: chưa có tín hiệu, trình duyệt chưa bật, DLQ > 0, cập nhật lỗi, ổ đĩa sắp hết / chưa đo, chưa cấu hình sao lưu', () => {
    const rows = byKey(
      health({
        worker: { state: 'unknown', alive: false, last_seen_at: null, silent_minutes: null },
        browser: { state: 'off', last_heartbeat_at: null },
        queues: [{ stream: 'gh:raw', dlq: 2 }, { stream: 'gh:refinery', dlq: 1 }],
        update: { state: 'failed', failed: true, blocked_version: null, finished_at: null },
        disk: { state: 'low', free_bytes: 1024 ** 3, min_bytes: 5 * 1024 ** 3, checked_at: null },
        backup: { configured: false, latest_at: null, age_hours: null, stale: false },
      }),
    );
    expect(rows.worker.value).toBe('Chưa có tín hiệu');
    expect(rows.browser.value).toBe('Chưa bật');
    expect(rows.dlq.value).toContain('3');
    expect(rows.dlq.tone).toBe('warn');
    expect(rows.update.value).toBe('Lần cập nhật gần nhất lỗi');
    expect(rows.dlq.label).toBe('Việc nền bị lỗi');
    expect(rows.disk.value).toBe('Sắp hết chỗ — còn 1,0 GB');
    expect(rows.disk.tone).toBe('bad');
    expect(rows.backup.value).toBe('Chưa cấu hình');
    expect(byKey(health({ disk: { state: 'unknown', free_bytes: null, min_bytes: null, checked_at: null } })).disk.value).toBe('Chưa đo');
  });

  it('sortIssues: bad trước warn', () => {
    expect(sortIssues([MODEL_EXPIRED, CHANNEL_DOWN]).map((i) => i.kind)).toEqual(['channel.down', 'model.auth_expired']);
  });
});

describe('v0.1.36 — thẻ cập nhật ở Tổng quan', () => {
  const failed = {
    current: 'v0.1.35', latest: 'v0.1.36', update_available: true, updater: 'systemd', linked: true, can_request: true,
    state: 'failed', message: 'lỗi (GH-E945)', from: 'v0.1.35', to: 'v0.1.36', started_at: null,
    // Thẻ "chưa thành công" chỉ vẽ khi lỗi trong 24 giờ gần nhất (updateModel RECENT_MS) — mốc phải tương đối theo giờ chạy test.
    finished_at: new Date(Date.now() - 3600 * 1000).toISOString(),
    requested_at: null, release_url: null, release_notes: null,
  };

  it('(g) hideFailed + cập nhật lỗi ⇒ không vẽ gì (lỗi đã có trong dải "Cần Sếp xử lý")', async () => {
    mockFetch((c) => (c.url.includes('/system/update') ? json(200, failed) : json(404)));
    const shown = render(
      <QueryClientProvider client={appQueryClient}>
        <MemoryRouter>
          <UpdateCard />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText(/Cập nhật lên v0.1.36 chưa thành công/)).toBeInTheDocument();
    shown.unmount();
    const hidden = render(
      <QueryClientProvider client={appQueryClient}>
        <MemoryRouter>
          <UpdateCard hideFailed />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await new Promise((r) => setTimeout(r, 30));
    expect(hidden.container).toBeEmptyDOMElement();
  });

  it('(h) hideFailed + "Máy chủ chưa nhận yêu cầu" (stalled) ⇒ thẻ VẪN hiện (dải không có dòng cho trạng thái này)', async () => {
    mockFetch((c) => (c.url.includes('/system/update') ? json(200, { ...failed, state: 'stalled', finished_at: null }) : json(404)));
    render(
      <QueryClientProvider client={appQueryClient}>
        <MemoryRouter>
          <UpdateCard hideFailed />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText('Máy chủ chưa nhận yêu cầu cập nhật')).toBeInTheDocument();
  });

  const overviewWithUpdate = (healthRes: () => Response) =>
    mockFetch((c) => {
      if (c.url.includes('/system/update')) return json(200, failed);
      return route({ step4Done: true, health: healthRes })(c);
    });

  it('(i) Tổng quan: cập nhật lỗi có trong dải ⇒ chỉ hiện MỘT lần (dải), không lặp thẻ', async () => {
    const UPDATE_FAILED: HealthIssue = {
      key: 'update.failed', kind: 'update.failed', severity: 'bad', title: 'Cập nhật lên v0.1.36 chưa thành công',
      body: 'Hệ thống đã tự quay về bản cũ, dữ liệu an toàn.', link: '/system?tab=storage', action: 'Xem & thử lại', raised_at: '2026-10-02T02:00:00Z',
    };
    overviewWithUpdate(() => json(200, health({ overall: 'bad', issues: [UPDATE_FAILED] })));
    renderUi(<OverviewScreen />);
    const strip = await screen.findByTestId('needs-boss');
    expect(within(strip).getByRole('link', { name: /Xem & thử lại/ })).toHaveAttribute('href', '/system?tab=storage');
    await screen.findByText('Kênh sống');
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getAllByText(/Cập nhật lên v0.1.36 chưa thành công/)).toHaveLength(1);
  });

  it('(k) Tổng quan: /system/health đang tải lần đầu ⇒ chưa vẽ thẻ cập nhật lỗi (không nhảy bố cục); tải xong không có dòng ⇒ thẻ hiện', async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    const calls = mockFetch((c) => {
      if (c.url.includes('/system/update')) return json(200, failed);
      if (c.url.includes('/system/health')) return gate.then(() => json(200, health()));
      return route({ step4Done: true })(c);
    });
    renderUi(<OverviewScreen />);
    await screen.findByText('Kênh sống');
    await waitFor(() => expect(calls.some((c) => c.url.includes('/system/update'))).toBe(true));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText(/Cập nhật lên v0.1.36 chưa thành công/)).toBeNull();
    release();
    expect(await screen.findByText(/Cập nhật lên v0.1.36 chưa thành công/)).toBeInTheDocument();
  });

  it('(j) Tổng quan: /system/health lỗi 500 ⇒ thẻ cập nhật lỗi vẫn hiện (không mất cảnh báo)', async () => {
    overviewWithUpdate(() => json(500, { title: 'Lỗi máy chủ', status: 500, code: 'INTERNAL' }, 'application/problem+json'));
    renderUi(<OverviewScreen />);
    expect(await screen.findByText(/Cập nhật lên v0.1.36 chưa thành công/)).toBeInTheDocument();
    expect(screen.queryByTestId('needs-boss')).toBeNull();
  });
});
