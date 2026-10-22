import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { loginAsOwner, resetMock, resultsDir, settle } from './support';

/**
 * v0.1.68 — bốn điểm nhỏ (P3) sau nghiệm thu, trên bản mock ở 390px và 1440px:
 *  - API & Model: bảng "Gán model cho từng agent" ở 390px đổi sang dạng thẻ (không cắt cột MODEL, không tràn trang); 1440px vẫn là bảng.
 *  - Agent: thẻ ở 390px — nút "Sửa" nằm gọn một dòng.
 *  - Nhật ký lắng nghe: nút "Xoá n bộ lọc" ở lại cùng hàng thanh lọc khi đủ chỗ (1440px).
 * Ảnh: `SHOTS_DIR` (nếu đặt) hoặc test-results/visual; `SHOT_TAG` (vd "truoc"/"sau") đặt đầu tên ảnh.
 */
const shots = process.env.SHOTS_DIR ?? join(resultsDir, 'visual');
const tag = process.env.SHOT_TAG ? `${process.env.SHOT_TAG}-` : '';

async function noPageOverflow(page: Page) {
  const d = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(d).toBeLessThanOrEqual(0);
}

for (const [label, size] of [['390', { width: 390, height: 844 }], ['1440', { width: 1440, height: 900 }]] as const) {
  test.describe(`P3 v0.1.68 — ${label}px`, () => {
    test.beforeEach(async ({ page }) => {
      mkdirSync(shots, { recursive: true });
      await page.setViewportSize(size);
      await resetMock(page.request as APIRequestContext, 'finished');
      await loginAsOwner(page);
    });

    test('API & Model: bảng gán model không cắt cột MODEL, không tràn trang', async ({ page }) => {
      const errors: string[] = [];
      page.on('pageerror', (e) => errors.push(String(e)));
      await page.goto('/api');
      const table = page.locator('.apm-table');
      await expect(table).toContainText('Sàng lọc & suy luận chính');
      const panel = page.getByRole('region', { name: 'Gán model cho từng agent' });
      await panel.scrollIntoViewIfNeeded();
      await settle(page);
      await panel.screenshot({ path: join(shots, `${tag}api-gan-model-${label}.png`) });
      await noPageOverflow(page);
      const wrap = page.locator('.apm-table-wrap').first();
      const m = await wrap.evaluate((el) => ({ sw: el.scrollWidth, cw: el.clientWidth }));
      const wbox = (await wrap.boundingBox())!;
      const rows = table.locator('tbody tr');
      const first = rows.first();
      if (label === '390') {
        // màn hẹp ⇒ dạng thẻ: không còn cuộn ngang, ô MODEL của MỌI dòng nằm trọn trong khung, mức suy nghĩ có nhãn
        expect(m.sw).toBeLessThanOrEqual(m.cw + 1);
        for (let i = 0; i < (await rows.count()); i++) {
          const cell = rows.nth(i).locator('.apm-td-model');
          const cb = (await cell.boundingBox())!;
          expect(cb.x).toBeGreaterThanOrEqual(wbox.x - 1);
          expect(cb.x + cb.width).toBeLessThanOrEqual(wbox.x + wbox.width + 1);
          expect(await cell.evaluate((el) => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(1);
        }
        await expect(first.locator('.apm-td-effort')).toBeVisible();
      } else {
        // màn rộng: vẫn là bảng, đủ bốn cột
        expect(await first.locator('td').count()).toBe(4);
        expect(m.sw).toBeLessThanOrEqual(m.cw + 1);
      }
      expect(errors).toEqual([]);
    });

    test('Agent: nút Sửa ở thẻ agent nằm gọn một dòng', async ({ page }) => {
      await page.goto('/agents');
      const edit = page.getByRole('button', { name: /^Sửa agent / }).first();
      await expect(edit).toBeVisible();
      await edit.scrollIntoViewIfNeeded();
      await settle(page);
      const box = (await edit.boundingBox())!;
      expect(box.height).toBeLessThanOrEqual(30);
      const lines = await edit.evaluate((el) => {
        const r = document.createRange();
        r.selectNodeContents(el);
        return new Set(Array.from(r.getClientRects()).map((x) => Math.round(x.top))).size;
      });
      expect(lines).toBe(1);
      await page.screenshot({ path: join(shots, `${tag}agents-${label}.png`) });
      await noPageOverflow(page);
    });

    test('Nhật ký lắng nghe: «Xoá n bộ lọc» ở cùng hàng thanh lọc khi đủ chỗ', async ({ page }) => {
      await page.goto('/listenlog?k=message&tag=1');
      const bar = page.getByRole('group', { name: 'Bộ lọc nhật ký' });
      const clear = bar.getByRole('button', { name: /^Xoá \d+ bộ lọc$/ });
      await expect(clear).toBeVisible();
      await settle(page);
      await bar.screenshot({ path: join(shots, `${tag}nhat-ky-bo-loc-${label}.png`) });
      await noPageOverflow(page);
      if (label === '1440') {
        // mọi phần tử trong thanh lọc chung một hàng ở 1440px: nút Xoá không bị đẩy xuống hàng riêng
        const rows = await bar.evaluate((el) => {
          const tops = new Set<number>();
          for (const c of Array.from(el.children)) {
            const r = (c as HTMLElement).getBoundingClientRect();
            if (r.width > 0) tops.add(Math.round(r.top + r.height / 2 - 6));
          }
          return tops.size;
        });
        const cb = (await clear.boundingBox())!;
        const sb = (await bar.locator('.ll-search').boundingBox())!;
        expect(Math.abs(cb.y + cb.height / 2 - (sb.y + sb.height / 2))).toBeLessThan(cb.height);
        expect(rows).toBeGreaterThan(0);
      }
    });
  });
}
