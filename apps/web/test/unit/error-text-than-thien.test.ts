import { describe, expect, it } from 'vitest';
import { ApiError } from '@gen-harness/contracts';
import { errorText } from '../../src/lib/errorText';
import { BACKUP_FAILED_TEXT, jobView, restoreView } from '../../src/screens/system/backupModel';

const IDLE = {
  can_request: true, state: 'idle', key: null, safety_key: null, message: null, started_at: null, finished_at: null, requested_at: null,
} as const;
const NOW = Date.parse('2026-10-16T10:00:00Z');

describe('F110 — lỗi 403 phân theo mã, không phải câu quyền cho mọi trường hợp', () => {
  it('CSRF_INVALID ⇒ phiên đã cũ, tải lại trang (không nói về vai trò)', () => {
    const t = errorText(new ApiError(403, { code: 'CSRF_INVALID', title: 'Phiên không hợp lệ, hãy tải lại trang' }));
    expect(t).toMatch(/Phiên đã cũ/);
    expect(t).toMatch(/tải lại trang/);
    expect(t).not.toMatch(/Vai trò/);
  });

  it('FORBIDDEN hoặc không mã ⇒ câu quyền', () => {
    expect(errorText(new ApiError(403, { code: 'FORBIDDEN', title: 'Không có quyền' }))).toBe('Vai trò của bạn không có quyền làm thao tác này.');
    expect(errorText(new ApiError(403, {}))).toBe('Vai trò của bạn không có quyền làm thao tác này.');
  });

  it('mã 403 khác mang câu riêng của máy chủ thì hiện câu đó, không thay bằng câu quyền', () => {
    const t = errorText(new ApiError(403, { code: 'GEN_DISABLED', title: 'Gen chưa bật cho vai trò này' }));
    expect(t).toBe('Gen chưa bật cho vai trò này');
  });
});

describe('F106 — không còn "worker"/"api" trước mặt Sếp', () => {
  it('mất kết nối máy chủ nói "máy chủ", không nói "dịch vụ api"', () => {
    const t = errorText(new ApiError(0, { title: 'x' }));
    expect(t).not.toMatch(/\bapi\b/i);
    expect(t).toMatch(/máy chủ/);
  });

  it('jobView: chờ / treo dùng "Bộ xử lý nền"', () => {
    for (const state of ['queued', 'stalled'] as const) {
      const v = jobView({ state });
      expect('text' in v && v.text).toMatch(/Bộ xử lý nền/);
      expect('text' in v && v.text).not.toMatch(/worker/i);
    }
  });

  it('jobView failed: câu thân thiện của máy chủ + technical tách riêng; thiếu message thì câu mặc định', () => {
    const v = jobView({ state: 'failed', message: BACKUP_FAILED_TEXT, technical: 'pg_dump: error: FATAL' });
    expect(v).toEqual({ kind: 'failed', text: BACKUP_FAILED_TEXT, technical: 'pg_dump: error: FATAL' });
    const bare = jobView({ state: 'failed' });
    expect(bare).toEqual({ kind: 'failed', text: BACKUP_FAILED_TEXT, technical: null });
    expect(BACKUP_FAILED_TEXT).not.toMatch(/worker|pg_dump/i);
  });

  it('restoreView failed: thân bài thân thiện cố định, thông điệp gốc nằm ở technical', () => {
    const v = restoreView({ ...IDLE, state: 'failed', message: 'pg_restore: error: could not open', finished_at: '2026-10-16T09:00:00Z' }, {
      waiting: false, offline: false, now: NOW,
    });
    if (v.kind !== 'failed') throw new Error('phải là failed');
    expect(v.body).not.toMatch(/pg_restore|auto-update\.log/);
    expect(v.technical).toBe('pg_restore: error: could not open');
  });

  it('restoreView failed không có message ⇒ technical null', () => {
    const v = restoreView({ ...IDLE, state: 'failed', finished_at: '2026-10-16T09:00:00Z' }, { waiting: false, offline: false, now: NOW });
    expect(v.kind === 'failed' && v.technical).toBeNull();
  });
});
