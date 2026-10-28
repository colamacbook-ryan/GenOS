import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { buildScreenTree, screenPath, SCREENS } from '@gen-harness/contracts';
import { autonomyTooltip, confidencePercent } from '../../src/shell/headerModel';
import { initials, roleLine } from '../../src/shell/people';
import { safeNext } from '../../src/lib/safeNext';
import { routes } from '../../src/router';

interface DesignScreen {
  key: string;
  domain: string;
  parent: string | null;
  name: string;
  en: string;
  title: string;
  subtitle: string;
  hidden?: boolean;
}
const design: DesignScreen[] = JSON.parse(
  readFileSync(resolve(__dirname, '../../../../docs/design/screens.json'), 'utf8'),
);

describe('screen registry', () => {
  it('matches docs/design/screens.json key for key', () => {
    expect(SCREENS.filter((s) => !s.extra).map((s) => s.key)).toEqual(design.map((d) => d.key));
    for (const d of design) {
      const s = SCREENS.find((x) => x.key === d.key)!;
      expect(s.name).toBe(d.name);
      expect(s.en).toBe(d.en);
      expect(s.title).toBe(d.title);
      expect(s.subtitle).toBe(d.subtitle);
      expect(s.parent).toBe(d.parent);
      expect(s.domain).toBe(({ 'Việc hằng ngày': 'daily', 'Quản trị': 'admin' } as Record<string, string>)[d.domain]);
      expect(!!s.navHidden).toBe(!!d.hidden);
    }
  });

  it('builds one page route per sidebar entry (v0.1.78: one shell), plus sub-routes for embedded screens that own ?tab=', () => {
    const paths: string[] = [];
    const walk = (rs: typeof routes, depth: string[]) =>
      rs.forEach((r) => {
        if (r.path && !r.path.startsWith('/') && r.path !== '*') paths.push([...depth, r.path].join(' > '));
        if (r.children) walk(r.children, r.id ? [...depth, r.id] : depth);
      });
    walk(routes, []);
    for (const p of ['intel', 'intel/inbox', 'decide', 'work', 'panorama', 'me', 'team']) expect(paths).toContain(`domain:daily > ${p}`);
    for (const p of ['start', 'connections', 'access', 'backup', 'org', 'log', 'advanced', 'advanced/plugins']) {
      expect(paths).toContain(`domain:admin > admin/${p}`);
    }
    // Mọi mục thanh bên có đúng một route ở đường dẫn chuẩn SCREEN_PATH.
    for (const d of buildScreenTree()) {
      for (const e of d.entries) {
        const key = e.kind === 'screen' ? e.screen.key : e.group.key!;
        expect(paths, key).toContain(`domain:${d.id} > ${screenPath(key).slice(1)}`);
      }
    }
    expect(paths).toContain('account');
    expect(paths).toContain('help');
    expect(paths).toContain('social');
    expect(paths).toContain('social/ghi-facebook');
    expect(paths).toContain('guide/viec-sep');
    expect(paths).toContain('guide/:n');
    // `guide/viec-sep` phải đứng TRƯỚC `guide/:n` (không bị `:n` nuốt).
    expect(paths.indexOf('guide/viec-sep')).toBeLessThan(paths.indexOf('guide/:n'));
    // Đường cũ không còn route riêng cho màn — chỉ là chuyển hướng (bảng ở legacy-redirects-v0178.test.ts).
    expect(paths).not.toContain('domain:business > connections');
    // v0.1.78: 6 mục Việc hằng ngày + 7 trang Quản trị.
    expect(buildScreenTree().map((d) => d.entries.length)).toEqual([6, 7]);
  });

  it('/owner và /owner/* là bộ chuyển hướng ngoài AppShell, đặt trước "/"', () => {
    const root = routes[0].children ?? [];
    const idxOwner = root.findIndex((r) => r.path === '/owner');
    const idxStar = root.findIndex((r) => r.path === '/owner/*');
    const idxHome = root.findIndex((r) => r.path === '/');
    expect(idxOwner).toBeGreaterThanOrEqual(0);
    expect(idxStar).toBeGreaterThan(idxOwner);
    expect(idxStar).toBeLessThan(idxHome);
    expect(root[idxOwner].children).toBeUndefined();
  });
});

describe('header + footer helpers', () => {
  // v0.1.43 (F-30): viên header hiện nhãn 3 mức; tooltip nói rõ đây là mức CHUNG và mức số (thay câu thiết kế cũ).
  it('autonomy tooltip shows the 3-level label and the 0–6 level', () => {
    expect(autonomyTooltip(4)).toBe('Mức tự trị chung: Soạn sẵn chờ duyệt (mức 4/6)');
    // Không chỉ tới chỗ không có thật: Cài đặt không có chỗ đổi mức tự trị chung của tổ chức.
    expect(autonomyTooltip(4)).not.toContain('Cài đặt');
  });
  it('data confidence accepts a fraction or a percent', () => {
    expect(confidencePercent(0.78)).toBe(78);
    expect(confidencePercent(78)).toBe(78);
    expect(confidencePercent(null)).toBeNull();
  });
  it('avatar initials skip the honorific and nickname', () => {
    expect(initials('Anh Nguyễn Văn A (Chủ)')).toBe('VA');
    expect(initials('Chị Lan Phạm')).toBe('LP');
    expect(initials('Nam')).toBe('N');
  });
  it('owner role line matches the design', () => {
    expect(roleLine({ role: { code: 'owner', name: 'Owner — Sếp' } })).toBe('Owner · thấy toàn cảnh');
    expect(roleLine({ role: { code: 'auditor', name: 'Auditor' } })).toBe('Kiểm soát');
  });
  it('login ?next= only accepts in-app paths', () => {
    expect(safeNext('/inbox?tab=all')).toBe('/inbox?tab=all');
    expect(safeNext('//evil.example')).toBe('/');
    expect(safeNext('https://evil.example')).toBe('/');
    expect(safeNext(null)).toBe('/');
  });
});
