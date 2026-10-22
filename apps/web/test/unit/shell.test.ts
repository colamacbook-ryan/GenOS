import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { buildScreenTree, SCREENS } from '@gen-harness/contracts';
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
      expect(s.domain).toBe(({ 'Việc hằng ngày': 'business', 'Nâng cao': 'tech' } as Record<string, string>)[d.domain]);
      expect(!!s.navHidden).toBe(!!d.hidden);
    }
  });

  it('builds one route per screen, nested domain › group › screen', () => {
    const paths: string[] = [];
    const walk = (rs: typeof routes, depth: string[]) =>
      rs.forEach((r) => {
        if (r.path && !r.path.startsWith('/') && r.path !== '*') paths.push([...depth, r.path].join(' > '));
        if (r.children) walk(r.children, r.id ? [...depth, r.id] : depth);
      });
    walk(routes, []);
    // 21 màn thiết kế + 3 màn spec bổ sung (tasks, documents, deals) + Hướng dẫn thiết lập (guide, guide/:n)
    // + Tài khoản của tôi (account, v0.1.19) + Trợ giúp (help, v0.1.22) + Tài khoản mạng xã hội (social, v0.1.29)
    // + Việc Sếp cần làm (guide/viec-sep, v0.1.39) + Kết nối, Đội ngũ (v0.1.42)
    // + Trang cảnh báo gửi Facebook (social/ghi-facebook, v0.1.47)
    // + Mặt tiền Owner (v0.1.55): 4 màn con tương đối của /owner (quan-he, viec, gen, them)
    // + Mặt tiền v2 (v0.1.59): thêm 2 trụ cột thong-tin, phan-tich ⇒ 6 màn con của /owner.
    // + Nhật ký lắng nghe (listenlog, v0.1.68).
    expect(paths).toHaveLength(40);
    for (const p of ['thong-tin', 'quan-he', 'phan-tich', 'viec', 'gen', 'them']) expect(paths).toContain(p);
    expect(paths).toContain('social/ghi-facebook');
    expect(paths).toContain('domain:business > connections');
    expect(paths).toContain('domain:business > team');
    expect(paths).toContain('domain:business > group:Đội ngũ > people');
    expect(paths).toContain('account');
    expect(paths).toContain('help');
    expect(paths).toContain('guide');
    expect(paths).toContain('guide/:n');
    expect(paths).toContain('guide/viec-sep');
    // `guide/viec-sep` phải đứng TRƯỚC `guide/:n` (không bị `:n` nuốt).
    expect(paths.indexOf('guide/viec-sep')).toBeLessThan(paths.indexOf('guide/:n'));
    expect(paths).toContain('domain:business > group:Hộp thư & Việc > inbox');
    expect(paths).toContain('domain:tech > graph');
    expect(paths).toContain('domain:tech > group:Bản đồ quan hệ > notebook');
    // Màn ẩn (Hồ sơ sống, Plugin) vẫn có route.
    expect(paths).toContain('domain:business > group:Khách & Cơ hội > profile');
    expect(paths).toContain('domain:tech > plugins');
    expect(paths).toContain('domain:business > system');
    // v0.1.42: 6 mục Việc hằng ngày + 5 mục Nâng cao.
    expect(buildScreenTree().map((d) => d.entries.length)).toEqual([6, 5]);
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
