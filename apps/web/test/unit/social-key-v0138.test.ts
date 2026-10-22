import { describe, expect, it } from 'vitest';
import { accountStatus, loginLabel } from '../../src/social/socialModel';

/** F-17 (v0.1.38) — phiên mạng xã hội không mở được sau khi chuyển máy/đổi khoá → "Cần đăng nhập lại" có lý do. */
describe('accountStatus — needs_login + key_changed', () => {
  it('key_changed → gợi ý chuyển máy/đổi khoá', () => {
    const v = accountStatus({ status: 'needs_login', pause_reason: 'key_changed', active_job: null });
    expect(v.label).toBe('Cần đăng nhập lại');
    expect(v.tone).toBe('warn');
    expect(v.hint).toBe('Phiên đã lưu không mở được trên máy này (chuyển máy hoặc đổi khoá) — bấm Đăng nhập lại.');
  });

  it('needs_login không lý do (hoặc logged_out) → giữ gợi ý cũ', () => {
    for (const pause_reason of [null, 'logged_out']) {
      const v = accountStatus({ status: 'needs_login', pause_reason, active_job: null });
      expect(v.label).toBe('Cần đăng nhập lại');
      expect(v.tone).toBe('warn');
      expect(v.hint).toBe('Phiên đã hết hoặc bị đăng xuất — bấm Đăng nhập lại.');
    }
  });
});

describe('loginLabel — khớp gợi ý "bấm Đăng nhập lại" (review F-17)', () => {
  it('needs_login không còn phiên vẫn là "Đăng nhập lại"', () => {
    expect(loginLabel({ status: 'needs_login', has_session: false })).toBe('Đăng nhập lại');
    expect(loginLabel({ status: 'active', has_session: true })).toBe('Đăng nhập lại');
    expect(loginLabel({ status: 'pending_login', has_session: false })).toBe('Đăng nhập');
  });
});
