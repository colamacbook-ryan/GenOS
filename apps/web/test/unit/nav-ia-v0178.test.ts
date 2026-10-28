/** v0.1.78 (G0): cây điều hướng một shell — danh mục khoá màn, đường dẫn, icon, trang chủ theo vai, chip sức khoẻ. */
import { describe, expect, it } from 'vitest';
import { NAV_ORDER, SCREENS, SCREEN_BY_KEY, SCREEN_PATH, buildScreenTree, screenPath } from '@gen-harness/contracts';
import { hasIcon } from '@gen-harness/ui';
import { buildNavigation } from '../mock-api';
import { firstScreenKey, visibleChildren, visibleGroups } from '../../src/shell/navModel';
import { HEALTH_FALLBACK_PATH, healthChipView, healthIssueLink } from '../../src/shell/headerModel';

describe('danh mục một shell', () => {
  it('Sếp thấy đúng cây §4.2: Tình báo · Cần quyết · Việc · Toàn cảnh · Tôi · (Đội ngũ khi có nhân viên) · Quản trị', () => {
    const nav = buildNavigation(new Set(), true, { hasStaff: true });
    expect(nav.map((d) => d.label)).toEqual(['Việc hằng ngày', 'Quản trị']);
    expect(visibleGroups(nav[0]).map((g) => g.name)).toEqual(['Tình báo', 'Cần quyết', 'Việc', 'Toàn cảnh', 'Tôi', 'Đội ngũ']);
    expect(visibleGroups(nav[1]).map((g) => g.name)).toEqual([
      'Bắt đầu', 'Kết nối', 'Người & quyền', 'Sao lưu & cập nhật', 'Tổ chức', 'Nhật ký', 'Nâng cao',
    ]);
    expect(nav[1].collapsed).toBe(true);
    // Mọi màn cũ là con ẩn — không thanh bên nào hiện con.
    for (const d of nav) for (const g of d.groups) expect(visibleChildren(g)).toEqual([]);
  });

  it('chưa có nhân viên ⇒ không có Đội ngũ trên thanh bên', () => {
    const nav = buildNavigation(new Set(), true, { hasStaff: false });
    expect(visibleGroups(nav[0]).map((g) => g.name)).not.toContain('Đội ngũ');
  });

  it('trang chủ: mục đầu tiên không ẩn là Tình báo (/intel)', () => {
    const key = firstScreenKey(buildNavigation());
    expect(key).toBe('intel');
    expect(screenPath(key!)).toBe('/intel');
  });

  it('các màn ẩn trước đây (Hồ sơ sống, Đánh giá, Chăm sóc) có lối vào từ menu: là tab của trang nhúng chúng', () => {
    expect(SCREEN_BY_KEY.profile.parent).toBe('Toàn cảnh');
    expect(SCREEN_BY_KEY.people.parent).toBe('Đội ngũ');
    expect(SCREEN_BY_KEY.care.parent).toBe('Đội ngũ');
    expect(SCREEN_PATH.panorama && SCREEN_PATH.team).toBeTruthy();
  });

  it('mỗi mục thanh bên có đường chuẩn; icon của mục mới có trong bảng icon (tên lạ vẽ ra ô trống)', () => {
    for (const d of buildScreenTree()) {
      for (const e of d.entries) {
        const s = e.kind === 'screen' ? e.screen : SCREEN_BY_KEY[e.group.key!];
        expect(SCREEN_PATH[s.key], s.key).toBeDefined();
        expect(hasIcon(s.icon), `${s.key}: ${s.icon}`).toBe(true);
      }
    }
    expect(NAV_ORDER.daily).toHaveLength(6);
    expect(NAV_ORDER.admin).toHaveLength(7);
    expect(SCREENS.every((s) => s.domain === 'daily' || s.domain === 'admin')).toBe(true);
  });
});

describe('chip sức khoẻ ở header', () => {
  it('không sự cố ⇒ "Hệ thống ổn"; có sự cố ⇒ đếm, đỏ nếu có mức bad', () => {
    expect(healthChipView([])).toEqual({ tone: 'ok', text: 'Hệ thống ổn', count: 0 });
    expect(healthChipView(undefined).tone).toBe('ok');
    expect(healthChipView([{ severity: 'warn' }])).toEqual({ tone: 'warn', text: '1 sự cố', count: 1 });
    expect(healthChipView([{ severity: 'warn' }, { severity: 'bad' }])).toEqual({ tone: 'bad', text: '2 sự cố', count: 2 });
  });

  it('link sự cố chỉ nhận đường trong app', () => {
    expect(healthIssueLink('/system?tab=channels')).toBe('/system?tab=channels');
    expect(healthIssueLink(null)).toBe(HEALTH_FALLBACK_PATH);
    expect(healthIssueLink('//evil.test')).toBe(HEALTH_FALLBACK_PATH);
    expect(healthIssueLink('https://evil.test')).toBe(HEALTH_FALLBACK_PATH);
  });
});
