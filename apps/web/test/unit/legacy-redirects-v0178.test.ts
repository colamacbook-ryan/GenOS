/**
 * v0.1.78 (G0): bảng chuyển hướng đường cũ → route mới. Không link cũ nào chết: mọi đích là route thật, không đích nào lại
 * chuyển hướng tiếp, query/băm của link cũ được giữ.
 */
import { describe, expect, it } from 'vitest';
import { matchRoutes } from 'react-router-dom';
import { SCREENS, screenPath } from '@gen-harness/contracts';
import { routes } from '../../src/router';
import { LEGACY_PATHS, LEGACY_REDIRECTS, SYSTEM_TAB_TARGETS, legacyTarget, resolveLegacy } from '../../src/shell/legacyRedirects';

const pathOf = (to: string) => to.split(/[?#]/)[0];
/** Route hiện hành (không phải LegacyRedirect) khớp pathname. */
function liveRoute(pathname: string) {
  const m = matchRoutes(routes, pathname) ?? [];
  const last = m.at(-1)?.route;
  return last && last.path !== '*' && !last.path?.endsWith('/*') ? last : null;
}

describe('bảng chuyển hướng', () => {
  it('bảng chính: Mặt tiền + route Console cũ → route mới', () => {
    const t = (p: string, search = '') => resolveLegacy(p, search, '');
    expect(t('/owner')).toBe('/intel');
    expect(t('/owner/thong-tin')).toBe('/intel?tab=signals');
    expect(t('/owner/quan-he')).toBe('/panorama?tab=relations');
    expect(t('/owner/phan-tich')).toBe('/panorama?tab=trends');
    expect(t('/owner/viec')).toBe('/decide');
    expect(t('/owner/gen')).toBe('/intel');
    expect(t('/owner/them')).toBe('/admin/start?tab=more');
    expect(t('/owner/khong-co')).toBe('/intel');
    expect(t('/overview')).toBe('/intel?tab=overview');
    expect(t('/inbox')).toBe('/intel/inbox');
    expect(t('/workbench')).toBe('/decide?tab=drafts');
    expect(t('/tasks')).toBe('/work');
    expect(t('/directory')).toBe('/panorama?tab=people');
    expect(t('/graph')).toBe('/panorama?tab=graph');
    expect(t('/profile', '?id=P1')).toBe('/panorama?tab=profile&id=P1');
    expect(t('/people')).toBe('/team?tab=reviews');
    expect(t('/care')).toBe('/team?tab=care');
    expect(t('/connections')).toBe('/admin/connections');
    expect(t('/guide')).toBe('/admin/start');
    expect(t('/raw')).toBe('/admin/advanced?tab=raw');
    expect(t('/plugins')).toBe('/admin/advanced/plugins');
  });

  it('giữ query và băm của link cũ; `tab` cũ bị bỏ trừ màn tự dùng ?tab= (Hộp thư, Plugin)', () => {
    expect(resolveLegacy('/owner/thong-tin', '?tin=A1', '')).toBe('/intel?tab=signals&tin=A1');
    expect(resolveLegacy('/owner/quan-he', '?list=hot&nguoi=P9', '')).toBe('/panorama?tab=relations&list=hot&nguoi=P9');
    expect(resolveLegacy('/overview', '?gen=coach', '')).toBe('/intel?tab=overview&gen=coach');
    // Review v0.1.78: tham số lặp giữ đủ (append, không ghi đè).
    expect(resolveLegacy('/directory', '?k=a&k=b', '')).toBe('/panorama?tab=people&k=a&k=b');
    expect(resolveLegacy('/inbox', '?tab=alert&intent=x', '')).toBe('/intel/inbox?tab=alert&intent=x');
    expect(resolveLegacy('/plugins', '?tab=core', '')).toBe('/admin/advanced/plugins?tab=core');
    expect(resolveLegacy('/directory', '?tab=zzz&dt=people', '')).toBe('/panorama?tab=people&dt=people');
    expect(resolveLegacy('/connections', '', '#telegram')).toBe('/admin/connections#telegram');
    expect(resolveLegacy('/owner/', '', '')).toBe('/intel');
  });

  it('/system?tab=… chuyển theo tab; không tab ⇒ Sao lưu (hoặc Nhật ký khi chỉ có audit.read)', () => {
    const t = (search: string, can = true) => resolveLegacy('/system', search, '', can);
    expect(t('?tab=storage')).toBe('/admin/backup');
    expect(t('?tab=storage&focus=health')).toBe('/admin/backup?focus=health');
    expect(t('?tab=org')).toBe('/admin/org');
    expect(t('?tab=brain', true)).toBe('/admin/advanced?tab=brain');
    expect(resolveLegacy('/system', '?tab=brain', '#gen-memory')).toBe('/admin/advanced?tab=brain#gen-memory');
    expect(t('?tab=roles')).toBe('/admin/access?tab=roles');
    expect(t('?tab=log')).toBe('/admin/log');
    expect(t('?tab=channels')).toBe('/admin/connections');
    expect(t('?tab=users')).toBe('/admin/access');
    expect(t('')).toBe('/admin/backup');
    expect(t('', false)).toBe('/admin/log');
    expect(t('?tab=la')).toBe('/admin/backup');
  });

  it('trang hiện hành không bị coi là đường cũ', () => {
    for (const p of ['/', '/intel', '/team', '/admin/start', '/account', '/help', '/guide/viec-sep', '/social']) {
      expect(legacyTarget(p), p).toBeNull();
    }
  });

  it('mọi đường cũ trong bảng có route chuyển hướng; mọi đích là route thật và không chuyển hướng tiếp', () => {
    const targets = [
      ...Object.entries(LEGACY_REDIRECTS).map(([from, t]) => [from, t.to] as const),
      ...Object.entries(SYSTEM_TAB_TARGETS).map(([tab, t]) => [`/system?tab=${tab}`, t.to] as const),
      ['/system', '/admin/log'],
      ['/owner/*', '/intel'],
    ];
    for (const [from, to] of targets) {
      const dest = pathOf(to);
      expect(liveRoute(dest), `${from} → ${to}: đích không phải route thật`).not.toBeNull();
      expect(legacyTarget(dest), `${from} → ${to}: đích lại chuyển hướng`).toBeNull();
    }
    for (const p of LEGACY_PATHS) {
      const m = matchRoutes(routes, p);
      expect(m?.at(-1)?.route.path, `/${p}: thiếu route chuyển hướng`).toBe(p);
    }
    expect(matchRoutes(routes, '/owner')?.at(-1)?.route.path).toBe('/owner');
    expect(matchRoutes(routes, '/owner/khong-co')?.at(-1)?.route.path).toBe('/owner/*');
  });

  it('mọi khoá màn cũ (con ẩn) có đường `/<khoá>` chuyển hướng; mọi mục thanh bên có đường chuẩn là route thật', () => {
    for (const s of SCREENS) {
      if (s.navHidden && s.key !== 'system') {
        // Hồ sơ sống, Plugin… đều là khoá màn cũ ⇒ link cũ `/<khoá>` phải sống.
        expect(legacyTarget(`/${s.key}`), s.key).not.toBeNull();
      } else if (!s.navHidden) {
        expect(liveRoute(screenPath(s.key)), s.key).not.toBeNull();
      }
    }
    expect(legacyTarget('/system')).not.toBeNull();
  });
});
