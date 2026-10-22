/**
 * v0.1.41: Tổng quan › hàng dưới có thêm "Chi phí AI hôm nay" (vai trò system.read) ⇒ 4 panel phải xếp lưới 2×2 trên
 * màn rộng (không để "Nhiệt kế hoạt động" lẻ một hàng 1/3), màn hẹp vẫn 1 cột.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(resolve(__dirname, '../../src/styles/p3-queue.css'), 'utf8');

describe('ov-bottom-grid', () => {
  it('có AiCostPanel ⇒ 2 cột chỉ ở màn rộng (≥1025px); không có ⇒ 3 cột; màn hẹp 1 cột', () => {
    expect(css).toMatch(
      /@media \(min-width: 1025px\) \{\s*\.ov-bottom-grid:has\(> \.ov-ai-cost\) \{ grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); \}/,
    );
    expect(css).toMatch(/\.ov-bottom-grid \{ display: grid; grid-template-columns: repeat\(3, minmax\(0, 1fr\)\);/);
    expect(css).toMatch(/@media \(max-width: 1024px\) \{\s*\.ov-main-grid, \.ov-bottom-grid,[^}]*grid-template-columns: minmax\(0, 1fr\);/);
  });
});
