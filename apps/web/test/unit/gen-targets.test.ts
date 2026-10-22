/**
 * Gen v1 — registry `data-gen-target` (packages/contracts/src/genTargets.ts) khớp mã nguồn và bản xuất cho API.
 *
 * 1. Mỗi id trong GEN_TARGETS có ít nhất một chỗ gắn trong apps/web/src và ngược lại (không khai báo hai nơi).
 * 2. `apps/api/gh/gen/registry.json` (API đọc để kiểm hành động UI) đúng bằng bản xuất từ TS.
 *    Đổi registry/guide → chạy `GEN_WRITE=1 npx vitest run gen-targets` để ghi lại tệp JSON.
 */
import { readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { GEN_SAFE_MESSAGE, GEN_SCREENS, GEN_TARGETS, resolveTarget } from '@gen-harness/contracts';
import { GUIDE } from '../../src/guide/guideContent';

const SRC = resolve(__dirname, '../../src');
const REGISTRY = resolve(__dirname, '../../../api/gh/gen/registry.json');
/** Tệp duy nhất trong `src/gen/` được quét tìm chỗ gắn `data-gen-target` (xem chú thích trong `scan`). */
const COACH_CARD_FILE = join(SRC, 'gen', 'CoachTodayCard.tsx');

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? files(p) : /\.tsx?$/.test(f) ? [p] : [];
  });
}

/** Gắn tĩnh `data-gen-target="x"` / `genTarget="x"` / `genTarget: 'x'`; động `data-gen-target={`x:${…}`}`. */
function scan(): Set<string> {
  const found = new Set<string>();
  // Cho phép cả '-' trong id: id có gạch nối trước đây lọt qua cả hai chiều kiểm (v0.1.36).
  const statik = /(?:data-gen-target|genTarget)\s*[=:]\s*["']([\w.-]+)["']/g;
  const dynamic = /data-gen-target=\{`([\w.-]+):\$\{/g;
  for (const f of files(SRC)) {
    // Khung Gen tự nó không phải chỗ gắn — NGOẠI LỆ HẸP (v0.1.54): đúng MỘT tệp `src/gen/CoachTodayCard.tsx`, vì thẻ "Hôm nay của
    // Sếp" (Gen hướng dẫn) nằm trong khung Gen nhưng là một mục tiêu `gen.coach.card` để Gen chỉ vào (nút "Chỉ cho em" của
    // chính thẻ, hay câu hỏi của Sếp). Các tệp khác trong `src/gen/` vẫn bị bỏ qua.
    if (f.includes(`${SRC}/gen/`) && f !== COACH_CARD_FILE) continue;
    const text = readFileSync(f, 'utf8');
    for (const m of text.matchAll(statik)) found.add(m[1]);
    for (const m of text.matchAll(dynamic)) found.add(m[1]);
  }
  return found;
}

function exportRegistry() {
  return {
    _comment: 'Sinh từ packages/contracts/src/genTargets.ts + apps/web/src/guide/guideContent.ts — KHÔNG sửa tay. GEN_WRITE=1 npx vitest run gen-targets',
    screens: Object.fromEntries(GEN_SCREENS.map((s) => [s.key, { path: s.path, title: s.title }])),
    targets: GEN_TARGETS.map((t) => ({ id: t.id, screen: t.screen, label: t.label, description: t.description, dynamic: t.dynamic ?? null, params: t.params ?? null, ...(t.permission ? { permission: t.permission } : {}), ...(t.sensitive ? { sensitive: true, safe_message: GEN_SAFE_MESSAGE } : {}) })),
    guide: GUIDE.map((g) => ({ n: g.n, title: g.title, why: g.why, steps: g.steps, done_when: g.doneWhen, console: g.console })),
  };
}

describe('gen targets registry', () => {
  it('every registry id is attached in apps/web/src and every attachment is registered', () => {
    const found = scan();
    const ids = new Set(GEN_TARGETS.map((t) => t.id));
    expect([...ids].filter((id) => !found.has(id)), 'registry ids with no data-gen-target').toEqual([]);
    expect([...found].filter((id) => !ids.has(id)), 'data-gen-target not in registry').toEqual([]);
  });

  it('targets point at known screens; ids are unique', () => {
    const screens = new Set(GEN_SCREENS.map((s) => s.key));
    expect(GEN_TARGETS.filter((t) => !screens.has(t.screen))).toEqual([]);
    expect(new Set(GEN_TARGETS.map((t) => t.id)).size).toBe(GEN_TARGETS.length);
  });

  it('resolveTarget accepts registered static/dynamic ids only', () => {
    expect(resolveTarget('system.brain.jev')?.screen).toBe('system');
    expect(resolveTarget('overview.queue.row:abc')?.dynamic).toBe('row');
    expect(resolveTarget('overview.queue.row')).toBeNull();
    expect(resolveTarget('system.brain.jev:1')).toBeNull();
    expect(resolveTarget('made.up')).toBeNull();
  });

  it('backup / PIN / password / sessions targets are sensitive', () => {
    const sens = new Set(GEN_TARGETS.filter((t) => t.sensitive).map((t) => t.id));
    // v0.1.42 (F-61): thẻ PIN chỉ ở Tài khoản của tôi — `system.channels.pin` bỏ, `account.pin` giữ.
    for (const id of ['system.backup.panel', 'system.backup.now', 'system.backup.schedule', 'account.password', 'account.pin', 'account.sessions']) {
      expect(sens.has(id), id).toBe(true);
    }
    expect(resolveTarget('system.channels.pin')).toBeNull();
  });

  it('v0.1.42: id cũ giữ nguyên, chỉ đổi chỗ (Kết nối, Đội ngũ); bỏ tab Kênh/Người dùng; Gen không mở Plugin', () => {
    expect(resolveTarget('system.channels.list')).toMatchObject({ screen: 'connections' });
    expect(resolveTarget('system.channels.list')?.params).toBeUndefined();
    expect(resolveTarget('system.channels.facebook')).toMatchObject({ screen: 'connections', permission: 'roles.manage' });
    expect(resolveTarget('connections.brain')).toMatchObject({ screen: 'connections' });
    for (const id of ['mcp.hub_link', 'mcp.hub_link.token', 'mcp.hub_link.test']) expect(resolveTarget(id)?.screen, id).toBe('connections');
    for (const id of ['system.users.list', 'system.users.invite', 'system.users.temp_password']) {
      expect(resolveTarget(id)?.screen, id).toBe('team');
      expect(resolveTarget(id)?.params, id).toBeUndefined();
    }
    expect(resolveTarget('system.tab.channels')).toBeNull();
    expect(resolveTarget('system.tab.users')).toBeNull();
    expect(resolveTarget('system.tab.storage')?.label).toBe('Tab "Sao lưu & cập nhật"');
    expect(GEN_SCREENS.map((s) => s.key)).not.toContain('plugins');
    expect(GEN_SCREENS.map((s) => s.key)).toEqual(expect.arrayContaining(['connections', 'team', 'profile']));
  });

  it('user-management targets require roles.manage', () => {
    const users = GEN_TARGETS.filter((t) => t.id.startsWith('system.users.'));
    expect(users.length).toBe(3);
    for (const t of users) expect(t.permission, t.id).toBe('roles.manage');
  });

  it('v0.1.54: 16 mục tiêu của Gen hướng dẫn có trong registry VÀ có chỗ gắn trong mã nguồn', () => {
    const rows = ['hub', 'facebook', 'agy', 'claude', 'jev', 'telegram', 'remote', 'facebook_reply', 'kho_write'];
    const ids = [
      ...rows.map((k) => `boss_checks.row.${k}`),
      'system.channels.telegram',
      'system.channels.telegram.test',
      'system.remote_access',
      'system.ai_cost',
      'system.brain.coach',
      'gen.coach.card',
      'help.curriculum',
    ];
    expect(ids).toHaveLength(16);
    const found = scan();
    for (const id of ids) {
      expect(resolveTarget(id), `${id} trong registry`).not.toBeNull();
      expect(found.has(id), `${id} có chỗ gắn data-gen-target`).toBe(true);
    }
    // 9 dòng "Việc Sếp cần làm": màn boss_checks, mục tiêu tĩnh (không dòng động).
    for (const k of rows) {
      const t = resolveTarget(`boss_checks.row.${k}`);
      expect(t?.screen).toBe('boss_checks');
      expect(t?.dynamic).toBeUndefined();
    }
    expect(resolveTarget('system.channels.telegram')?.screen).toBe('connections');
    expect(resolveTarget('system.channels.telegram.test')?.screen).toBe('connections');
    expect(resolveTarget('system.remote_access')).toMatchObject({ screen: 'system', params: { tab: 'storage' } });
    expect(resolveTarget('system.ai_cost')).toMatchObject({ screen: 'system', params: { tab: 'brain' } });
    expect(resolveTarget('system.brain.coach')).toMatchObject({ screen: 'system', params: { tab: 'brain' } });
    expect(resolveTarget('gen.coach.card')?.screen).toBe('overview');
    expect(resolveTarget('help.curriculum')?.screen).toBe('help');
  });

  it('v0.1.54: ngoại lệ quét src/gen/ chỉ dành cho CoachTodayCard.tsx — tệp khác của khung Gen không được gắn mục tiêu', () => {
    const attach = /(?:data-gen-target|genTarget)\s*[=:]\s*["']([\w.-]+)["']/;
    const attachedInGen = files(join(SRC, 'gen')).filter((f) => attach.test(readFileSync(f, 'utf8')));
    expect(attachedInGen.map((f) => f.slice(SRC.length + 1))).toEqual(['gen/CoachTodayCard.tsx']);
  });

  it('apps/api/gh/gen/registry.json matches the TS export', () => {
    const want = `${JSON.stringify(exportRegistry(), null, 2)}\n`;
    if (process.env.GEN_WRITE === '1') writeFileSync(REGISTRY, want);
    expect(readFileSync(REGISTRY, 'utf8')).toBe(want);
  });
});
