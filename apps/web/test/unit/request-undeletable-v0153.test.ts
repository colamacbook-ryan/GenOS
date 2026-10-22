import { describe, expect, it } from 'vitest';
import type { DiagnosticsState, OffsiteState, SystemHealth } from '@gen-harness/contracts';
import { REQUEST_UNDELETABLE_CODE, REQUEST_UNDELETABLE_TEXT } from '../../src/lib/genhCodes';
import { OFFSITE_ERROR_TEXT, offsiteErrorText, offsiteNextStep, offsiteView } from '../../src/screens/system/offsiteModel';
import { healthRows } from '../../src/screens/system/healthModel';
import { DIAG_ERROR_TEXT, diagFailedText } from '../../src/help/diagnosticsModel';
import { TELEGRAM_ERROR_TEXT, telegramErrorText } from '../../src/screens/connections/telegramModel';

/**
 * v0.1.53 (F-97): genh không xoá được tệp yêu cầu trong run/request ⇒ ghi mã GH-E94C và KHÔNG làm yêu cầu. Bản sao ngoài
 * máy, gói chẩn đoán và Gửi thử trực canh đều nhận mã này — mỗi nơi phải nói đúng việc cần làm (kiểm quyền thư mục
 * run/request), không rơi về câu chung "thử lại" / "cắm ổ" (thử lại vẫn lỗi y như cũ).
 */

const DAY = 24 * 3600 * 1000;
const NOW = Date.parse('2026-10-10T03:00:00Z');
const OWNER = { isOwner: true, canManage: true };
const VIEWER = { isOwner: false, canManage: false };

const offsite = (over: Partial<OffsiteState> = {}): OffsiteState =>
  ({
    configured: true, dest: '/media/usb/gen-harness', state: 'failed', error_code: REQUEST_UNDELETABLE_CODE,
    message: null, last_attempt_at: new Date(NOW - 60_000).toISOString(), last_success_at: new Date(NOW - 9 * DAY).toISOString(),
    last_file: null, last_size_bytes: null, verified: false, kept: 1, schedule: 'systemd', key_id: null, age_days: 9,
    stale: true, request: null, ...over,
  }) as OffsiteState;

describe('GH-E94C — không xoá được tệp yêu cầu', () => {
  it('câu dùng chung: nói rõ chưa làm gì + kiểm quyền run/request, kết thúc bằng dấu chấm', () => {
    expect(REQUEST_UNDELETABLE_CODE).toBe('GH-E94C');
    expect(REQUEST_UNDELETABLE_TEXT).toMatch(/^Máy chủ không xoá được tệp yêu cầu — chưa làm gì\./);
    expect(REQUEST_UNDELETABLE_TEXT).toContain('kiểm quyền thư mục run/request');
    expect(REQUEST_UNDELETABLE_TEXT.endsWith('.')).toBe(true);
  });

  it('bản sao ngoài máy: câu lỗi riêng, bước tiếp KHÔNG bảo cắm ổ', () => {
    expect(OFFSITE_ERROR_TEXT['GH-E94C']).toBe(REQUEST_UNDELETABLE_TEXT);
    expect(offsiteErrorText('GH-E94C', null)).toBe(REQUEST_UNDELETABLE_TEXT);
    expect(offsiteErrorText('GH-E94C', null)).not.toBe('Lần sao lưu ra ổ ngoài gần nhất chưa thành công.');
    for (const who of [OWNER, VIEWER]) {
      const next = offsiteNextStep(true, who, 'GH-E94C');
      expect(next).toContain('run/request');
      expect(next.toLowerCase()).not.toContain('cắm ổ');
    }
    expect(offsiteNextStep(true, VIEWER, 'GH-E94C')).not.toContain('bấm');
    const v = offsiteView(offsite(), NOW, 'Asia/Ho_Chi_Minh', OWNER);
    expect(v.error).toEqual({ text: REQUEST_UNDELETABLE_TEXT, code: 'GH-E94C' });
    // Cảnh báo "đã 9 ngày chưa có bản sao" cũng không bảo cắm ổ khi lỗi là quyền thư mục.
    expect(v.warning).toContain('run/request');
    expect(v.warning?.toLowerCase()).not.toContain('cắm ổ');
  });

  it('thẻ Sức khoẻ — dòng "Bản sao ngoài máy" theo mã GH-E94C, không bảo cắm ổ', () => {
    const h = {
      checked_at: new Date(NOW).toISOString(), overall: 'warn',
      worker: { state: 'ok', alive: true, last_seen_at: null, silent_minutes: null },
      browser: { state: 'off', last_heartbeat_at: null }, queues: [], crons: [],
      backup: { configured: true, latest_at: new Date(NOW - 3_600_000).toISOString(), age_hours: 1, stale: false },
      update: { state: 'idle', failed: false, blocked_version: null, finished_at: null },
      disk: { state: 'unknown', free_bytes: null, min_bytes: null, checked_at: null },
      issues: [],
      offsite: { state: 'failed', configured: true, last_success_at: new Date(NOW - 9 * DAY).toISOString(), age_days: 9, stale: true, error_code: 'GH-E94C', schedule: 'systemd' },
    } as SystemHealth;
    const hint = healthRows(h, NOW, 'Asia/Ho_Chi_Minh', OWNER).find((r) => r.key === 'offsite')?.hint ?? '';
    expect(hint).toContain('run/request');
    expect(hint.toLowerCase()).not.toContain('cắm ổ');
  });

  it('gói chẩn đoán: câu riêng thay vì câu chung "bấm Tạo gói chẩn đoán lần nữa"', () => {
    expect(DIAG_ERROR_TEXT['GH-E94C']).toBe(REQUEST_UNDELETABLE_TEXT);
    const d = { error_code: 'GH-E94C', message: 'Không xoá được yêu cầu gói chẩn đoán…' } as Pick<DiagnosticsState, 'error_code' | 'message'>;
    expect(diagFailedText(d)).toBe(REQUEST_UNDELETABLE_TEXT);
  });

  it('Gửi thử trực canh (Telegram): câu riêng thay vì "Chưa gửi được — thử lại sau ít phút"', () => {
    expect(TELEGRAM_ERROR_TEXT['GH-E94C']).toBe(REQUEST_UNDELETABLE_TEXT);
    expect(telegramErrorText('GH-E94C', null)).toBe(REQUEST_UNDELETABLE_TEXT);
    expect(telegramErrorText('GH-E94C', null)).not.toMatch(/thử lại sau ít phút/);
  });
});
