import { existsSync, mkdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { OWNER, SETUP_TOKEN, apiCall, loginAsOwner, p3Hook, resetMock, resultsDir } from './support';

/**
 * v0.1.59 (G2) — SPEC CHỤP ẢNH Mặt tiền v2 để người duyệt (Opus, rồi Boss) XEM bằng mắt theo checklist "Owner 3 giây hiểu"
 * (docs/design/mat-tien-owner-v2.md mục 9) và hệ hình ảnh (mục 4). Không so ảnh mẫu (pixel) — nhiệm vụ của spec là:
 *   - 6 màn × 390 / 1440 × sáng / tối = 24 ảnh:  /owner (tinh-bao), /owner/thong-tin, /owner/quan-he, /owner/phan-tich,
 *     /owner/viec, /setup?buoc=nguon-ai (thiet-lap);
 *   - 5 ảnh trạng thái ở 390: nochannel cho Tình báo / Thông tin / Quan hệ, empty và collecting cho Phân tích.
 * Lưu: apps/web/test-results/owner-v2/<man>-<rong>-<sang|toi>.png  và  <man>-390-<trangthai>.png  (test-results đã nằm trong
 * .gitignore). Mỗi ảnh chụp CẢ TRANG: viewport được kéo cao bằng chiều cao nội dung nên thanh dưới/thanh trái nằm đúng chỗ.
 * Trên đường đi vẫn kiểm vài điều rẻ mà ảnh hay giấu: không tràn ngang, đủ thanh điều hướng đúng cỡ, không "[object Object]".
 *
 * Chế độ dữ liệu: p3Hook(request, 'owner', 'data', { mode }) — mode = 'full' | 'nochannel' | 'empty' | 'collecting' (mock-owner, G1).
 * Hook lỗi ⇒ spec đỏ (không nuốt lỗi) để ảnh trạng thái chắc chắn là trạng thái cần xem.
 */

const OUT = join(resultsDir, 'owner-v2');
const THEMES = [
  { key: 'sang', scheme: 'light' },
  { key: 'toi', scheme: 'dark' },
] as const;
const SCREENS = [
  { key: 'tinh-bao', path: '/owner' },
  { key: 'thong-tin', path: '/owner/thong-tin' },
  { key: 'quan-he', path: '/owner/quan-he' },
  { key: 'phan-tich', path: '/owner/phan-tich' },
  { key: 'viec', path: '/owner/viec' },
] as const;
const WIDTHS = [390, 1440] as const;
const BASE_H: Record<number, number> = { 390: 844, 1440: 900 };

/** Chuyển chế độ dữ liệu của mock Mặt tiền (chặt: hook lỗi thì ném). */
async function setDataMode(page: Page, mode: 'full' | 'nochannel' | 'empty' | 'collecting'): Promise<void> {
  await p3Hook(page.request, 'owner', 'data', { mode });
}

async function setTheme(page: Page, scheme: 'light' | 'dark'): Promise<void> {
  await page.emulateMedia({ colorScheme: scheme });
  await expect(page.locator('html')).toHaveAttribute('data-theme', scheme === 'light' ? 'light' : 'dark');
}

async function settle(page: Page): Promise<void> {
  await page
    .waitForFunction(() => !document.querySelector('main .gh-skeleton, main [aria-busy="true"], [data-testid="owner-boot"]'), undefined, { timeout: 8_000 })
    .catch(() => undefined);
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  await page.waitForTimeout(250);
}

/** Chụp cả trang: kéo viewport cao bằng nội dung (thanh dưới dính đáy ảnh, thanh trái đủ chiều cao), chụp, trả cỡ cũ. */
async function shoot(page: Page, name: string, width: number): Promise<string> {
  mkdirSync(OUT, { recursive: true });
  const file = join(OUT, `${name}.png`);
  await page.setViewportSize({ width, height: BASE_H[width] });
  await settle(page);
  const h = await page.evaluate(() => document.documentElement.scrollHeight);
  await page.setViewportSize({ width, height: Math.min(Math.max(h, BASE_H[width]), 5000) });
  await page.waitForTimeout(150);
  await page.screenshot({ path: file });
  await page.setViewportSize({ width, height: BASE_H[width] });
  expect(existsSync(file), `${file} phải tồn tại`).toBe(true);
  expect(statSync(file).size, `${file} không được rỗng`).toBeGreaterThan(2_000);
  return file;
}

/** Những điều rẻ mà ảnh hay giấu. */
async function sanity(page: Page, width: number, where: string): Promise<void> {
  expect(await page.locator('body').innerText(), where).not.toContain('[object Object]');
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over, `${where}: không được cuộn ngang`).toBeLessThanOrEqual(0);
  if (width === 390) {
    await expect(page.getByTestId('owner-tabbar'), where).toBeVisible();
    await expect(page.getByTestId('owner-tabbar').getByRole('link'), where).toHaveCount(5);
  } else {
    await expect(page.getByRole('navigation', { name: 'Mặt tiền' }), where).toBeVisible();
    await expect(page.locator('[data-testid^="owner-nav-"]'), where).toHaveCount(6);
  }
}

test.describe('v0.1.59 · chụp ảnh Mặt tiền v2 (xem bằng mắt ở bước tích hợp)', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
  });

  for (const width of WIDTHS) {
    test(`5 trụ cột @${width}px × sáng/tối (10 ảnh)`, async ({ page }) => {
      test.setTimeout(150_000);
      await loginAsOwner(page);
      await setDataMode(page, 'full');
      await page.setViewportSize({ width, height: BASE_H[width] });
      for (const s of SCREENS) {
        await page.goto(s.path);
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
        for (const t of THEMES) {
          await setTheme(page, t.scheme);
          await settle(page);
          await sanity(page, width, `${s.path} ${width}px ${t.key}`);
          await shoot(page, `${s.key}-${width}-${t.key}`, width);
        }
      }
    });
  }

  test('5 ảnh trạng thái @390px: ngày đầu (nochannel) cho Tình báo / Thông tin / Quan hệ; rỗng và đang gom cho Phân tích', async ({ page }) => {
    test.setTimeout(150_000);
    await loginAsOwner(page);
    await page.setViewportSize({ width: 390, height: BASE_H[390] });
    await page.emulateMedia({ colorScheme: 'light' });
    const states = [
      { mode: 'nochannel', shots: [['tinh-bao', '/owner'], ['thong-tin', '/owner/thong-tin'], ['quan-he', '/owner/quan-he']] },
      { mode: 'empty', shots: [['phan-tich', '/owner/phan-tich']] },
      { mode: 'collecting', shots: [['phan-tich', '/owner/phan-tich']] },
    ] as const;
    for (const st of states) {
      await setDataMode(page, st.mode);
      for (const [key, path] of st.shots) {
        await page.goto(path);
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
        await setTheme(page, 'light');
        await settle(page);
        await sanity(page, 390, `${path} ${st.mode}`);
        await shoot(page, `${key}-390-${st.mode}`, 390);
      }
    }
    await setDataMode(page, 'full');
  });

  for (const width of WIDTHS) {
    test(`Thiết lập (/setup?buoc=nguon-ai) @${width}px × sáng/tối (2 ảnh)`, async ({ page }) => {
      test.setTimeout(90_000);
      await resetMock(page.request, 'fresh');
      await apiCall(page, 'PUT', '/setup/steps/1', { token: SETUP_TOKEN, language: 'vi', mode: 'empty' });
      await apiCall(page, 'PUT', '/setup/steps/2', {
        token: SETUP_TOKEN, display_name: 'Anh Cơ', email: 'owner@example.test', password: OWNER.password, pin: OWNER.pin, pin_confirm: OWNER.pin,
      });
      await apiCall(page, 'PUT', '/setup/steps/3', { org_name: 'Công ty Mẫu', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND', self_name: 'Anh', bot_calls_me: 'Sếp' });
      await page.setViewportSize({ width, height: BASE_H[width] });
      await page.goto('/setup?buoc=nguon-ai');
      await expect(page.getByRole('heading').first()).toBeVisible();
      for (const t of THEMES) {
        await setTheme(page, t.scheme);
        await settle(page);
        expect(await page.locator('body').innerText()).not.toContain('[object Object]');
        const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        expect(over, `/setup ${width}px ${t.key}: không được cuộn ngang`).toBeLessThanOrEqual(0);
        await shoot(page, `thiet-lap-${width}-${t.key}`, width);
      }
    });
  }
});
