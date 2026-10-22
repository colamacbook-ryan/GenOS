/**
 * v0.1.69 (D4; F096, Q4 = 2 của Sếp 16/10): vai Quản lý KHÔNG hứa "thấy team mình" khi hệ thống chưa có cách tạo team —
 * nhãn mời người, mô tả vai, ô chọn phạm vi, chú thích ma trận và Nhật ký đều nói "việc được giao".
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { INVITE_ROLES } from '../../src/screens/system/usersModel';
import { ROLE_LABEL, SCOPE_CELL, SCOPE_OPTIONS, TEAMS_AVAILABLE, scopeOptionsFor } from '../../src/screens/system/systemModel';

const SRC = resolve(__dirname, '../../src');
const read = (rel: string) => readFileSync(resolve(SRC, rel), 'utf8');

describe('F096 — Quản lý thấy việc được giao, không hứa team', () => {
  it('nhãn vai Quản lý ở hộp mời, bước Mời đội ngũ và bảng quyền', () => {
    expect(INVITE_ROLES.find((r) => r.value === 'manager')?.label).toBe('Quản lý — thấy việc được giao');
    expect(ROLE_LABEL.manager).toEqual({ name: 'Quản lý', meta: 'thấy việc được giao' });
    expect(read('setup/Step10Team.tsx')).toContain("label: 'Quản lý — thấy việc được giao'");
  });

  it('chưa có tính năng Nhóm ⇒ mức "Theo team" không còn để chọn mới; giá trị đã lưu vẫn hiện, gọi là "Việc được giao"', () => {
    expect(TEAMS_AVAILABLE).toBe(false);
    expect(scopeOptionsFor('queue.read', 'all').map((o) => o.value)).toEqual(['all', 'assigned', 'none']);
    expect(scopeOptionsFor('queue.read', 'none').map((o) => o.value)).toEqual(['all', 'assigned', 'none']);
    const kept = scopeOptionsFor('queue.read', 'team');
    expect(kept.map((o) => o.value)).toEqual(['all', 'team', 'assigned', 'none']);
    expect(kept.find((o) => o.value === 'team')?.label).toBe('Việc được giao');
    // Bảng đầy đủ (dùng cho chú thích/nhãn) vẫn có đủ 4 mức.
    expect(SCOPE_OPTIONS.map((o) => o.value)).toEqual(['all', 'team', 'assigned', 'none']);
    expect(SCOPE_CELL.team.title).toBe('Chỉ việc được giao cho họ');
  });

  it('không còn câu hứa "team" trong chuỗi hiển thị của Quyền hạn / Nhật ký / Mời', () => {
    const banned = /thấy team mình|theo team|Theo team|team mình|nhật ký team/;
    for (const f of ['screens/system/usersModel.ts', 'screens/system/LogTab.tsx', 'screens/system/RolesTab.tsx', 'setup/Step10Team.tsx']) {
      expect(read(f).split('\n').filter((l) => banned.test(l)), f).toEqual([]);
    }
    const model = read('screens/system/systemModel.ts');
    expect(model.split('\n').filter((l) => banned.test(l) && !l.trim().startsWith('//') && !l.trim().startsWith('*'))).toEqual([]);
    expect(read('screens/system/LogTab.tsx')).toContain('Quản lý chỉ thấy nhật ký các thao tác của chính mình');
  });
});
