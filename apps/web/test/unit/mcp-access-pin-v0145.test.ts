import { describe, expect, it } from 'vitest';
import { ACCESS_PIN_HINT, accessChangeNeedsPin, describeCallArgs, isArgsDigest } from '../../src/screens/mcp/mcpModel';

describe('v0.1.45 (F-20) — đổi loại tool MCP', () => {
  it('ghi → đọc cần mã PIN (tool đọc chạy không qua duyệt)', () => {
    expect(accessChangeNeedsPin('write', 'read')).toBe(true);
    expect(ACCESS_PIN_HINT).toBe('Chuyển tool ghi sang đọc cần mã PIN (tool đọc chạy không qua duyệt)');
  });

  it('đọc → ghi và giữ nguyên không cần PIN', () => {
    expect(accessChangeNeedsPin('read', 'write')).toBe(false);
    expect(accessChangeNeedsPin('read', 'read')).toBe(false);
    expect(accessChangeNeedsPin('write', 'write')).toBe(false);
  });
});

describe('v0.1.45 (F-57) — nhật ký MCP chỉ có dấu vết tham số', () => {
  const digest = { sha256: 'a1b2c3d4e5f6'.padEnd(64, '0'), keys: ['email', 'so_tk'], bytes: 42 };

  it('nhận ra dạng dấu vết', () => {
    expect(isArgsDigest(digest)).toBe(true);
    expect(isArgsDigest({ so_tk: '0123456789012' })).toBe(false);
  });

  it('mô tả ngắn không lộ giá trị', () => {
    expect(describeCallArgs(digest)).toBe('email, so_tk · 42 byte · a1b2c3d4');
    expect(describeCallArgs({ sha256: '0'.repeat(64), keys: [], bytes: 2 })).toContain('không có tham số');
    // Dòng rất cũ (chưa qua job dọn dẹp): chỉ hiện tên khoá, không hiện giá trị.
    expect(describeCallArgs({ so_tk: '0123456789012' })).toBe('so_tk');
  });
});
