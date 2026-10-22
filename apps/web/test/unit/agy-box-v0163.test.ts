import { describe, expect, it } from 'vitest';
import type { SystemHealth } from '@gen-harness/contracts';
import { healthRows, healthTips } from '../../src/screens/system/healthModel';

/** v0.1.63 (gói H1): dòng "Hộp riêng Antigravity" của thẻ Sức khoẻ — chỉ có khi hộp đã được cấu hình. */

const NOW = Date.parse('2026-10-16T03:00:00Z');

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
const row = (h: SystemHealth) => healthRows(h, NOW).find((r) => r.key === 'agy_box');

describe('healthModel — Hộp riêng Antigravity (H1)', () => {
  it('không có khối agy_box ⇒ không có dòng và không có hướng dẫn', () => {
    expect(row(health())).toBeUndefined();
    expect(healthTips(health()).some((t) => t.key === 'agy_box')).toBe(false);
  });

  it('ok ⇒ xanh, nêu phiên bản; thiếu phiên bản vẫn là chuỗi', () => {
    const r = row(health({ agy_box: { state: 'ok', checked_at: new Date(NOW).toISOString(), agy_version: '1.2.9' } }));
    expect(r?.label).toBe('Hộp riêng Antigravity');
    expect(r?.tone).toBe('ok');
    expect(r?.value).toBe('Đang chạy · Antigravity 1.2.9');
    expect(row(health({ agy_box: { state: 'ok', checked_at: null, agy_version: null } }))?.value).toBe('Đang chạy');
  });

  it('down ⇒ vàng (không đỏ), nói rõ hậu quả, kèm hướng dẫn có lệnh chép được', () => {
    const h = health({ agy_box: { state: 'down', checked_at: new Date(NOW).toISOString(), agy_version: null } });
    const r = row(h);
    expect(r?.tone).toBe('warn');
    expect(r?.value).toContain('tạm dùng nguồn AI khác');
    expect(typeof r?.value).toBe('string');
    const tip = healthTips(h).find((t) => t.key === 'agy_box');
    expect(tip?.title).toBe('Cách bật lại Hộp riêng Antigravity');
    expect(tip?.steps.map((s) => s.cmd).filter(Boolean)).toEqual(['genh logs agy-box', 'genh start']);
    // Review v0.1.63 (P3): `genh update` tải bản mới / có thể không làm gì khi đã mới nhất — dựng lại hộp là `genh start`.
    expect(tip?.steps.some((s) => s.cmd === 'genh update')).toBe(false);
  });
});
