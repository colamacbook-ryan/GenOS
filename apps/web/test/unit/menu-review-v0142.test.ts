/**
 * v0.1.42 — sửa theo review: Góc nhìn đã lưu ở màn có bộ lọc, link cũ /system?tab=… theo quyền và giữ tham số,
 * viên trạng thái thẻ CLI, dòng báo cập nhật tự hỏi lại, mô tả thanh bên tiếng Việt.
 */
import { describe, expect, it } from 'vitest';
import { SCREENS } from '@gen-harness/contracts';
import { SAVED_VIEW_SCREENS, showSavedViews } from '../../src/shell/headerModel';
import { movedTabTarget } from '../../src/screens/system/settingsModel';
import { cliConnStatus } from '../../src/screens/connections/connectionsModel';
import { updatePollMs } from '../../src/update/updateModel';
import { itemTitle } from '../../src/shell/navModel';

describe('Góc nhìn đã lưu', () => {
  it('hiện ở Nâng cao và ở các màn nghiệp vụ có bộ lọc; không ở Hôm nay/Kết nối/Cài đặt', () => {
    for (const k of ['inbox', 'workbench', 'tasks', 'directory', 'deals', 'documents', 'search', 'people', 'care'])
      expect(showSavedViews(k, false), k).toBe(true);
    for (const k of ['overview', 'connections', 'team', 'system', 'opportunity', null]) expect(showSavedViews(k, false)).toBe(false);
    expect(showSavedViews('raw', true)).toBe(true);
  });

  it('màn ngoài danh sách vẫn hiện nút khi đã có góc nhìn lưu từ trước', () => {
    expect(showSavedViews('system', false, 2)).toBe(true);
    expect(showSavedViews('opportunity', false, 1)).toBe(true);
    expect(showSavedViews(null, false, 3)).toBe(false);
  });

  it('mọi khoá trong danh sách là màn có thật', () => {
    const keys = new Set(SCREENS.map((s) => s.key));
    for (const k of SAVED_VIEW_SCREENS) expect(keys.has(k)).toBe(true);
  });
});

describe('link cũ /system?tab=channels|users', () => {
  const all = () => true;
  it('chuyển khi vai trò mở được trang đích, giữ tham số khác', () => {
    expect(movedTabTarget('?tab=channels', all)).toBe('/connections');
    expect(movedTabTarget('?tab=users', all)).toBe('/team');
    expect(movedTabTarget('?tab=channels&gen=c-1', all)).toBe('/connections?gen=c-1');
    expect(movedTabTarget('?gen=c-1&tab=users&x=2', all)).toBe('/team?gen=c-1&x=2');
  });

  it('không chuyển khi thiếu quyền trang đích hoặc tab không bị dời', () => {
    expect(movedTabTarget('?tab=channels', (p) => p !== 'system.read')).toBeNull();
    expect(movedTabTarget('?tab=users', (p) => p !== 'roles.manage')).toBeNull();
    expect(movedTabTarget('?tab=storage&focus=health', all)).toBeNull();
    expect(movedTabTarget('', all)).toBeNull();
  });
});

describe('thẻ CLI ở Kết nối', () => {
  it('cliConnStatus: chưa có tài khoản / còn hạn / sắp hết hạn / hết hạn', () => {
    expect(cliConnStatus(undefined)).toBe('not_connected');
    expect(cliConnStatus({ state: 'ok' })).toBe('running');
    expect(cliConnStatus({ state: 'expiring' })).toBe('running');
    expect(cliConnStatus({ state: 'expired' })).toBe('needs_boss');
  });
});

describe('dòng báo cập nhật ở Hôm nay', () => {
  it('hỏi lại mỗi 4 giây khi đang cập nhật, dừng khi xong/lỗi', () => {
    expect(updatePollMs('requested')).toBe(4000);
    expect(updatePollMs('running')).toBe(4000);
    for (const s of ['idle', 'done', 'failed', 'stalled', undefined, null]) expect(updatePollMs(s)).toBe(false);
  });
});

describe('thanh bên không còn chữ tiếng Anh (F-63)', () => {
  it('mô tả `en` của mọi màn là tiếng Việt', () => {
    const english = /\b(Board|Review|Quality|Deals|Cases|Documents|Search|Rules|Resolution|Identity|Map|Demand|Workbench|Reminders|filters|settings|servers|notebook|store|plugins)\b/;
    for (const s of SCREENS) expect(s.en, s.key).not.toMatch(english);
    expect(itemTitle({ name: 'Bảng cơ hội', en: SCREENS.find((s) => s.key === 'opportunity')!.en } as never)).toBe(
      'Bảng cơ hội — Cơ hội bán hàng đang theo',
    );
  });
});
