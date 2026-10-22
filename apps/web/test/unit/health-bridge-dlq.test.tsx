import type { ReactElement } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { SystemHealth } from '@gen-harness/contracts';
import { HealthCard } from '../../src/screens/system/HealthCard';
import { healthQueues, healthRows, healthTechRows, healthTips, INBOUND_STREAM } from '../../src/screens/system/healthModel';
import { HEALTH_KINDS } from '../../src/screens/system/queries';
import { qk } from '../../src/lib/queries';
import { queryClient as appQueryClient } from '../../src/lib/queryClient';

/**
 * v0.1.62 (gói B5): thẻ Sức khoẻ có dòng "Bộ nhận tin Zalo/WhatsApp" (F051), hàng lỗi theo loại với "Phát lại" / "Đã xem,
 * xoá" và câu gợi ý đúng — hàng lỗi KHÔNG tự hết (F064, F102).
 */

const NOW = Date.parse('2026-10-16T03:00:00Z');
const json = (status: number, body?: unknown, type = 'application/json') =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': type } });

function health(over: Partial<SystemHealth> = {}): SystemHealth {
  return {
    checked_at: new Date(NOW).toISOString(),
    overall: 'ok',
    worker: { state: 'ok', alive: true, last_seen_at: new Date(NOW - 60_000).toISOString(), silent_minutes: null },
    browser: { state: 'off', last_heartbeat_at: null },
    queues: [],
    crons: [],
    backup: { configured: true, latest_at: new Date(NOW - 3_600_000).toISOString(), age_hours: 1, stale: false },
    update: { state: 'idle', failed: false, blocked_version: null, finished_at: null },
    disk: { state: 'ok', free_bytes: 40 * 1024 ** 3, min_bytes: 5 * 1024 ** 3, checked_at: new Date(NOW).toISOString() },
    issues: [],
    ...over,
  };
}
const byKey = (h: SystemHealth) => Object.fromEntries(healthRows(h, NOW).map((r) => [r.key, r]));

describe('healthModel — cầu nối tin nhắn (F051)', () => {
  it('không có khối bridge ⇒ không có dòng (chưa dùng cầu nối)', () => {
    expect(byKey(health()).bridge).toBeUndefined();
  });

  it('bridge ok ⇒ "Đang chạy · lần cuối …" (xanh); silent ⇒ đỏ, nói thẳng tin khách chưa vào được', () => {
    const ok = byKey(health({ bridge: { state: 'ok', last_seen_at: new Date(NOW - 20_000).toISOString(), silent_minutes: null } })).bridge;
    expect(ok.label).toBe('Bộ nhận tin Zalo/WhatsApp');
    expect(ok.tone).toBe('ok');
    expect(ok.value).toContain('Đang chạy');
    const silent = byKey(health({ bridge: { state: 'silent', last_seen_at: new Date(NOW - 7 * 60_000).toISOString(), silent_minutes: 7 } })).bridge;
    expect(silent.tone).toBe('bad');
    expect(silent.value).toBe('Đã ngừng 7 phút — tin khách chưa vào được');
    expect(typeof silent.value).toBe('string');
  });

  it('bridge ngừng ⇒ hướng dẫn khởi động lại có lệnh chép được (đích của nút "Xem sức khoẻ")', () => {
    expect(healthTips(health()).some((t) => t.key === 'bridge')).toBe(false);
    const tip = healthTips(health({ bridge: { state: 'silent', last_seen_at: null, silent_minutes: 9 } })).find((t) => t.key === 'bridge');
    expect(tip?.title).toBe('Cách khởi động lại Bộ nhận tin Zalo/WhatsApp');
    expect(tip?.steps.map((s) => s.cmd).filter(Boolean)).toEqual(['genh logs bridge', 'genh stop', 'genh start']);
  });

  it('HEALTH_KINDS có bridge.silent và dlq.inbound ⇒ chuông làm mới thẻ ngay', () => {
    expect(HEALTH_KINDS.has('bridge.silent')).toBe(true);
    expect(HEALTH_KINDS.has('dlq.inbound')).toBe(true);
  });
});

describe('healthModel — hàng lỗi theo loại (F064, F102)', () => {
  it('tin khách vào: nói đúng "tin khách chưa nhập kho", gợi ý Phát lại, KHÔNG nói "thường tự hết"', () => {
    const h = health({ queues: [{ stream: INBOUND_STREAM, dlq: 3 }] });
    const dlq = byKey(h).dlq;
    expect(dlq.value).toBe('3 tin khách chưa nhập kho');
    expect(dlq.tone).toBe('warn');
    expect(dlq.hint).toContain('Phát lại');
    expect(dlq.hint).toContain('chưa mất');
    expect(dlq.hint).not.toContain('tự hết');
    expect(dlq.hint).toContain('Báo lỗi');
  });

  it('lẫn tin khách và việc khác: đếm riêng, gợi ý nhắc cả "Đã xem, xoá"', () => {
    const h = health({ queues: [{ stream: 'gh.clean.ready', dlq: 2 }, { stream: INBOUND_STREAM, dlq: 1 }, { stream: 'gh.x', dlq: 0 }] });
    expect(byKey(h).dlq.value).toBe('1 tin khách chưa nhập kho · 2 việc khác');
    expect(byKey(h).dlq.hint).toContain('Đã xem, xoá');
    const q = healthQueues(h);
    expect(q.map((x) => [x.stream, x.label, x.count, x.replayable])).toEqual([
      [INBOUND_STREAM, 'Tin khách chưa nhập kho được', 1, true],
      ['gh.clean.ready', 'Tin chờ sàng lọc bị lỗi', 2, false],
    ]);
  });

  it('hàng khác: nói thật là sẽ không tự hết, chỉ có "Đã xem, xoá"; hàng lạ gọi là "Việc nền khác"', () => {
    const h = health({ queues: [{ stream: 'gh.la.hoac', dlq: 4 }] });
    const dlq = byKey(h).dlq;
    expect(dlq.value).toBe('4 việc');
    expect(dlq.hint).toContain('sẽ không tự hết');
    expect(dlq.hint).toContain('Đã xem, xoá');
    expect(healthQueues(h)[0]).toMatchObject({ label: 'Việc nền khác', replayable: false });
    // tên kỹ thuật (.dlq) chỉ ở "Chi tiết kỹ thuật"
    expect(healthTechRows(h).queues[0]).toMatchObject({ name: 'gh.la.hoac.dlq', value: '4 · Việc nền khác' });
    expect(dlq.label).not.toMatch(/dlq/i);
  });

  it('không có việc lỗi ⇒ "Không có", không gợi ý', () => {
    const dlq = byKey(health({ queues: [{ stream: INBOUND_STREAM, dlq: 0 }] })).dlq;
    expect(dlq.value).toBe('Không có');
    expect(dlq.hint).toBeUndefined();
    expect(healthQueues(health({ queues: [{ stream: INBOUND_STREAM, dlq: 0 }] }))).toEqual([]);
  });
});

// ─── thẻ Sức khoẻ: nút Phát lại / Đã xem, xoá ────────────────────────────────────────────────────────────

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
function meWith(permissions: Record<string, string>) {
  return {
    id: 'u', email: 'owner@genesis.local', display_name: 'Anh Cơ', role: { code: 'owner', name: 'owner' },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
    addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions,
  };
}
function renderCard(ui: ReactElement, permissions: Record<string, string>) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, meWith(permissions));
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}
const MANAGE = { 'system.read': 'all', 'system.manage': 'all' };
const READ_ONLY = { 'system.read': 'all' };
const WITH_DLQ = health({
  overall: 'warn',
  queues: [{ stream: INBOUND_STREAM, dlq: 3 }, { stream: 'gh.clean.ready', dlq: 2 }],
  bridge: { state: 'silent', last_seen_at: new Date(NOW - 600_000).toISOString(), silent_minutes: 10 },
});

afterEach(() => {
  vi.unstubAllGlobals();
  appQueryClient.clear();
});

describe('<HealthCard> — hàng lỗi có lối xử lý', () => {
  it('hiện dòng bridge, từng loại hàng lỗi; tin khách có Phát lại, mọi hàng có Đã xem, xoá', async () => {
    mockFetch((c) => (c.url.includes('/system/health') ? json(200, WITH_DLQ) : json(404)));
    renderCard(<HealthCard />, MANAGE);
    const queues = await screen.findByTestId('health-queues');
    expect(screen.getByTestId('health-bridge')).toHaveTextContent('Đã ngừng 10 phút — tin khách chưa vào được');
    const inbound = within(queues).getByTestId(`health-queue-${INBOUND_STREAM}`);
    expect(inbound).toHaveTextContent('Tin khách chưa nhập kho được');
    expect(within(inbound).getByRole('button', { name: /Phát lại/ })).toBeInTheDocument();
    expect(within(inbound).getByRole('button', { name: /Đã xem, xoá/ })).toBeInTheDocument();
    const other = within(queues).getByTestId('health-queue-gh.clean.ready');
    expect(within(other).queryByRole('button', { name: /Phát lại/ })).toBeNull();
    expect(within(other).getByRole('button', { name: /Đã xem, xoá/ })).toBeInTheDocument();
    expect(screen.getByTestId('health-tip-bridge')).toBeInTheDocument();
  });

  it('"Phát lại" gọi đúng đường, báo kết quả bằng câu thân thiện và làm mới thẻ', async () => {
    let served = 0;
    const calls = mockFetch((c) => {
      if (c.url.includes('/system/health')) {
        served += 1;
        return json(200, served === 1 ? WITH_DLQ : health({ queues: [{ stream: 'gh.clean.ready', dlq: 2 }] }));
      }
      if (c.url.endsWith(`/system/queues/${INBOUND_STREAM}/replay`) && c.method === 'POST') {
        return json(200, { stream: INBOUND_STREAM, replayed: 3, remaining: 0 });
      }
      return json(404);
    });
    renderCard(<HealthCard />, MANAGE);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /Phát lại/ }));
    expect(await screen.findByTestId('health-queue-done')).toHaveTextContent('Đã phát lại 3 tin');
    expect(calls.some((c) => c.method === 'POST' && c.url.endsWith(`/system/queues/${INBOUND_STREAM}/replay`))).toBe(true);
    await waitFor(() => expect(served).toBeGreaterThan(1));
    await waitFor(() => expect(screen.queryByTestId(`health-queue-${INBOUND_STREAM}`)).toBeNull());
  });

  it('"Đã xem, xoá" hỏi lại một bước (không hoàn tác); "Giữ lại" không gọi gì; "Xoá luôn" gọi clear', async () => {
    const calls = mockFetch((c) => {
      if (c.url.includes('/system/health')) return json(200, WITH_DLQ);
      if (c.url.endsWith('/system/queues/gh.clean.ready/clear') && c.method === 'POST') return json(200, { stream: 'gh.clean.ready', cleared: 2, remaining: 0 });
      return json(404);
    });
    renderCard(<HealthCard />, MANAGE);
    const user = userEvent.setup();
    const other = await screen.findByTestId('health-queue-gh.clean.ready');
    await user.click(within(other).getByRole('button', { name: /Đã xem, xoá/ }));
    expect(other).toHaveTextContent('Xoá 2 việc, không hoàn tác?');
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    await user.click(within(other).getByRole('button', { name: 'Giữ lại' }));
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
    await user.click(within(other).getByRole('button', { name: /Đã xem, xoá/ }));
    await user.click(within(other).getByRole('button', { name: 'Xoá luôn' }));
    expect(await screen.findByTestId('health-queue-done')).toHaveTextContent('Đã xoá 2 việc lỗi');
    expect(calls.filter((c) => c.method === 'POST').map((c) => c.url.split('/api/v1')[1])).toEqual(['/system/queues/gh.clean.ready/clear']);
  });

  it('lỗi máy chủ ⇒ câu thân thiện (chuỗi, không object); người chỉ có system.read không thấy nút', async () => {
    mockFetch((c) => {
      if (c.url.includes('/system/health')) return json(200, WITH_DLQ);
      return json(500, { title: 'Hệ thống gặp lỗi khi xử lý yêu cầu', status: 500, code: 'INTERNAL' }, 'application/problem+json');
    });
    renderCard(<HealthCard />, MANAGE);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /Phát lại/ }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Hệ thống gặp lỗi');
    expect(alert.textContent).not.toContain('[object');
    // luật cứng: câu thân thiện kèm "Chi tiết kỹ thuật" (mã HTTP, mã lỗi)
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(alert.textContent).toContain('HTTP 500');
    expect(alert.textContent).toContain('INTERNAL');
  });

  it('chỉ có system.read: thấy số lượng + lời nhờ, không có nút', async () => {
    mockFetch((c) => (c.url.includes('/system/health') ? json(200, WITH_DLQ) : json(404)));
    renderCard(<HealthCard />, READ_ONLY);
    const queues = await screen.findByTestId('health-queues');
    expect(within(queues).queryAllByRole('button')).toHaveLength(0);
    expect(queues).toHaveTextContent('Nhờ người có quyền quản lý hệ thống');
  });
});
