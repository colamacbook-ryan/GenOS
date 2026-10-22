import { describe, expect, it } from 'vitest';
import { can } from '../../src/lib/permissions';
import { scopeOptionsFor } from '../../src/screens/system/systemModel';

/** F-58 (sửa review v0.1.45): system.manage chỉ có nghĩa ở phạm vi `all` — UI khớp máy chủ (deps.ALL_ONLY). */
describe('system.manage chỉ tính ở phạm vi all', () => {
  it('can(): team/assigned của system.manage = không có quyền (không hiện nút chết 403)', () => {
    expect(can({ permissions: { 'system.manage': 'all' } }, 'system.manage')).toBe(true);
    expect(can({ permissions: { 'system.manage': 'team' } }, 'system.manage')).toBe(false);
    expect(can({ permissions: { 'system.manage': 'assigned' } }, 'system.manage')).toBe(false);
    expect(can({ permissions: { 'system.manage': 'none' } }, 'system.manage')).toBe(false);
    // Quyền thường: team vẫn là có quyền.
    expect(can({ permissions: { 'queue.act': 'team' } }, 'queue.act')).toBe(true);
  });

  it('scopeOptionsFor(): system.manage chỉ cho "Tất cả"/"Không"; giá trị cũ hiện kèm ghi chú và bị khoá', () => {
    expect(scopeOptionsFor('system.manage', 'all').map((o) => o.value)).toEqual(['all', 'none']);
    const legacy = scopeOptionsFor('system.manage', 'team');
    expect(legacy.map((o) => o.value)).toEqual(['all', 'none', 'team']);
    expect(legacy[2]).toMatchObject({ disabled: true, label: 'Việc được giao (không có tác dụng)' });
    expect(scopeOptionsFor('queue.read', 'team').map((o) => o.value)).toEqual(['all', 'team', 'assigned', 'none']);
  });
});
