import { describe, expect, it } from 'vitest';
import type { SystemHealth } from '@gen-harness/contracts';
import { INOTIFY_FIX_COMMAND, healthRows, healthTips } from '../../src/screens/system/healthModel';

/**
 * v0.1.54: người gác yêu cầu (`gen-harness-update-request.path`) tự chữa — khi api báo `nightly.watcher` (không ok) thẻ
 * "Sức khoẻ hệ thống" có một dòng "Người gác cập nhật" + hướng dẫn chép được; thiếu khối (api cũ / người gác khoẻ) thì
 * không có gì thêm. Chữ do web tự ghép từ chuỗi cố định, không lấy `hint` của tệp/api; không render object.
 */

const NOW = Date.parse('2026-10-10T10:00:00Z');
const health = (watcher?: NonNullable<NonNullable<SystemHealth['nightly']>['watcher']>): SystemHealth => ({
  checked_at: '2026-10-10T09:59:00Z', overall: 'ok',
  worker: { state: 'ok', alive: true, last_seen_at: null, silent_minutes: null },
  browser: { state: 'off', last_heartbeat_at: null }, queues: [], crons: [],
  backup: { configured: true, latest_at: '2026-10-10T02:00:00Z', age_hours: 8, stale: false },
  update: { state: 'idle', failed: false, blocked_version: null, finished_at: null },
  disk: { state: 'unknown', free_bytes: null, min_bytes: null, checked_at: null },
  issues: [],
  nightly: {
    state: 'ok', last_run_at: '2026-10-09T20:00:00Z', next_run_at: '2026-10-10T20:00:00Z', days_since: 0, opted_out: false,
    linger: 'yes', checked_at: '2026-10-10T09:55:00Z', ...(watcher ? { watcher } : {}),
  },
});

describe('Người gác cập nhật chạy dự phòng (v0.1.54)', () => {
  it('fallback + inotify ⇒ dòng cảnh báo vàng và hướng dẫn: sysctl rồi genh auto-update enable', () => {
    const h = health({ state: 'fallback', reason: 'inotify', hint: 'chữ lạ từ api, không được dùng' });
    const row = healthRows(h, NOW, 'Asia/Ho_Chi_Minh').find((r) => r.key === 'watcher');
    expect(row).toMatchObject({ label: 'Người gác cập nhật', value: 'Đang chạy dự phòng (hết hạn mức inotify)', tone: 'warn' });

    const tip = healthTips(h).find((t) => t.key === 'watcher');
    expect(tip?.title).toBe('Người gác cập nhật đang chạy dự phòng');
    expect(tip?.steps).toEqual([
      { text: 'Người gác cập nhật đang chạy dự phòng (hết hạn mức inotify). Chạy trên máy:', cmd: 'sudo sysctl -w fs.inotify.max_user_instances=1024' },
      { text: 'rồi:', cmd: 'genh auto-update enable' },
    ]);
    expect(INOTIFY_FIX_COMMAND).toBe('sudo sysctl -w fs.inotify.max_user_instances=1024');
    for (const s of tip?.steps ?? []) expect(s.cmd?.endsWith('.')).toBe(false);
    // Chữ lấy từ `hint` không lọt vào giao diện; không có object nào bị ép thành chuỗi.
    const all = JSON.stringify([row, tip]);
    expect(all).not.toContain('chữ lạ');
    expect(all).not.toContain('[object Object]');
  });

  it('failed ⇒ đỏ, nói nút Cập nhật ngay chưa có người nhận; thiếu khối watcher ⇒ không có dòng/hướng dẫn', () => {
    const failed = health({ state: 'failed', reason: 'resources', hint: '' });
    expect(healthRows(failed, NOW).find((r) => r.key === 'watcher')).toMatchObject({
      value: 'Đang lỗi — nút Cập nhật ngay chưa có người nhận (lỗi tài nguyên — thường là hạn mức inotify)', tone: 'bad',
    });
    expect(healthTips(failed).find((t) => t.key === 'watcher')?.title).toBe('Người gác cập nhật đang lỗi');

    const other = healthTips(health({ state: 'fallback', reason: 'other', hint: '' })).find((t) => t.key === 'watcher');
    expect(other?.steps.map((s) => s.cmd)).toEqual(['systemctl --user status gen-harness-update-request.path', 'genh auto-update enable']);

    const ok = health();
    expect(healthRows(ok, NOW).some((r) => r.key === 'watcher')).toBe(false);
    expect(healthTips(ok).some((t) => t.key === 'watcher')).toBe(false);
  });
});
