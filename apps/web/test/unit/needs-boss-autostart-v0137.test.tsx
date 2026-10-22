import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { HealthIssue, SystemHealth } from '@gen-harness/contracts';
import { NeedsBossStrip } from '../../src/screens/queue/NeedsBossStrip';
import { HEALTH_KINDS } from '../../src/screens/system/queries';
import { healthRows, healthTips } from '../../src/screens/system/healthModel';
import { qk } from '../../src/lib/queries';

/**
 * v0.1.37 (F-73): sự cố `host.autostart` (máy chủ có thể không tự chạy lại Gen-Harness khi bật lại máy) — dải "Cần Sếp
 * xử lý" hiện dòng kèm lệnh cần chạy trên máy chủ và nút "Xem cách bật" tới thẻ Sức khoẻ (hướng dẫn từng bước, lệnh dạng
 * mã chép được); chuông thuộc kind sự cố sức khoẻ (làm mới `/system/health` ngay khi nhận).
 */

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const AUTOSTART: HealthIssue = {
  key: 'host.autostart', kind: 'host.autostart', severity: 'warn',
  title: 'Máy chủ có thể không tự chạy lại Gen-Harness khi bật lại máy',
  body: 'Docker chưa bật tự chạy khi mở máy — chạy một lần trên máy chủ: sudo systemctl enable docker · Chạy xong thì chạy genh status để cảnh báo tự hết',
  link: '/system?tab=storage', action: 'Xem cách bật', raised_at: '2026-10-02T01:00:00Z',
};

function health(issues: HealthIssue[]): SystemHealth {
  return {
    checked_at: '2026-10-02T03:00:00Z', overall: 'warn',
    worker: { state: 'ok', alive: true, last_seen_at: null, silent_minutes: null },
    browser: { state: 'off', last_heartbeat_at: null }, queues: [], crons: [],
    backup: { configured: true, latest_at: '2026-10-02T02:00:00Z', age_hours: 1, stale: false },
    update: { state: 'idle', failed: false, blocked_version: null, finished_at: null },
    disk: { state: 'unknown', free_bytes: null, min_bytes: null, checked_at: null },
    issues,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe('v0.1.37 — host.autostart', () => {
  it('dải hiện dòng warn kèm lệnh + nút "Xem cách bật" tới thẻ Sức khoẻ, không "[object Object]"', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => {
      const u = String(url);
      if (u.includes('/system/health')) return json(200, health([AUTOSTART]));
      if (u.includes('/setup/follow-up')) return json(200, [{ n: 4, key: 'brain', title: 'Bộ não AI', status: 'done', done: true }]);
      return json(404, { title: 'Không tìm thấy', status: 404 });
    }));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(qk.me, {
      id: 'u', email: 'owner@genesis.local', display_name: 'Anh Cơ', role: { code: 'owner', name: 'owner' },
      org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
      addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
      permissions: { 'overview.read': 'all', 'system.read': 'all', 'system.manage': 'all' },
    });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter><NeedsBossStrip /></MemoryRouter>
      </QueryClientProvider>,
    );
    const strip = await screen.findByTestId('needs-boss');
    const [row] = within(strip).getAllByTestId('needs-boss-row');
    expect(row).toHaveAttribute('data-severity', 'warn');
    expect(row).toHaveTextContent('Máy chủ có thể không tự chạy lại Gen-Harness khi bật lại máy');
    expect(row).toHaveTextContent('sudo systemctl enable docker');
    expect(row).toHaveTextContent('genh status');
    expect(within(row).getByRole('link', { name: /Xem cách bật/ })).toHaveAttribute('href', '/system?tab=storage');
    expect(strip.textContent).not.toContain('[object Object]');
  });

  it('chuông host.autostart là kind sự cố sức khoẻ (làm mới /system/health ngay)', () => {
    expect(HEALTH_KINDS.has('host.autostart')).toBe(true);
  });
});

describe('v0.1.37 — healthModel: Tự chạy lại khi bật máy', () => {
  const NOW = Date.parse('2026-10-02T03:00:00Z');
  const withAuto = (autostart: SystemHealth['autostart']): SystemHealth => ({ ...health([]), autostart });
  const row = (h: SystemHealth) => healthRows(h, NOW, 'Asia/Ho_Chi_Minh').find((r) => r.key === 'autostart');

  it('không có khối autostart (api cũ / không có hộp thư) ⇒ không có dòng, không có hướng dẫn', () => {
    expect(row(health([]))).toBeUndefined();
    expect(healthTips(health([])).find((t) => t.key === 'autostart')).toBeUndefined();
  });

  it('warn ⇒ "Chưa bật" (vàng) kèm giờ kiểm; ok ⇒ "Có"; unknown ⇒ "Chưa rõ" (xám)', () => {
    const base = { linger: 'yes', linger_required: true, docker_enabled: 'yes', docker_mode: 'system', checked_at: '2026-10-02T01:30:00Z' } as const;
    const warn = row(withAuto({ ...base, state: 'warn', docker_enabled: 'no' }))!;
    expect(warn.label).toBe('Tự chạy lại khi bật máy');
    expect(warn.value).toMatch(/^Chưa bật · kiểm lúc /);
    expect(warn.value).toContain('08:30');
    expect(warn.tone).toBe('warn');
    expect(row(withAuto({ ...base, state: 'ok' }))!).toMatchObject({ value: expect.stringMatching(/^Có · kiểm lúc /), tone: 'ok' });
    expect(row(withAuto({ ...base, state: 'unknown', checked_at: null }))!).toMatchObject({ value: 'Chưa rõ', tone: 'muted' });
  });

  it('hướng dẫn: lệnh dạng mã theo docker_mode/linger, luôn kết bằng genh status; không có dấu chấm sau lệnh', () => {
    const sys = healthTips(withAuto({ state: 'warn', linger: 'no', linger_required: true, docker_enabled: 'no', docker_mode: 'system', checked_at: null }))
      .find((t) => t.key === 'autostart')!;
    expect(sys.steps.map((x) => x.cmd).filter(Boolean)).toEqual(['sudo systemctl enable docker', 'sudo loginctl enable-linger $USER', 'genh status']);
    const rootless = healthTips(withAuto({ state: 'warn', linger: 'no', linger_required: true, docker_enabled: 'no', docker_mode: 'rootless', checked_at: null }))
      .find((t) => t.key === 'autostart')!;
    expect(rootless.steps.map((x) => x.cmd).filter(Boolean)).toEqual(['systemctl --user enable docker', 'sudo loginctl enable-linger $USER', 'genh status']);
    expect(rootless.steps[1].text).toMatch(/Docker rootless/);
    const lingerOnly = healthTips(withAuto({ state: 'warn', linger: 'no', linger_required: true, docker_enabled: 'yes', docker_mode: 'system', checked_at: null }))
      .find((t) => t.key === 'autostart')!;
    expect(lingerOnly.steps.map((x) => x.cmd).filter(Boolean)).toEqual(['sudo loginctl enable-linger $USER', 'genh status']);
    for (const t of [sys, rootless, lingerOnly]) for (const st of t.steps) if (st.cmd) expect(st.cmd.endsWith('.')).toBe(false);
    // Không hứa "đợi tới đêm": thiếu linger thì lịch đêm không chạy, tắt tự cập nhật thì không có lần chạy đêm nào.
    for (const t of [sys, rootless, lingerOnly]) for (const st of t.steps) expect(st.text).not.toMatch(/đêm/);
    expect(healthTips(withAuto({ state: 'ok', linger: 'yes', linger_required: true, docker_enabled: 'yes', docker_mode: 'system', checked_at: null }))
      .find((t) => t.key === 'autostart')).toBeUndefined();
  });
});
