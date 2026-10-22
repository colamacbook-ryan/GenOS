import { join } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { loginAsOwner, mockHook, resetMock, resultsDir, settle } from './support';

/** v0.1.23 — Đợt B4–B7: điện thoại 375px, trang lỗi/404, chuông thông báo, sáng/tối. */
const shots = join(resultsDir, 'visual');
const PHONE = { width: 375, height: 812 };

/** Các màn chính phải vừa 375px: không cuộn ngang cả trang, và vùng nội dung không tràn ngang. */
const MAIN_SCREENS: Array<[string, string]> = [
  ['/overview', 'overview'],
  ['/inbox', 'inbox'],
  ['/workbench', 'workbench'],
  ['/tasks', 'tasks'],
  ['/directory', 'directory'],
  ['/opportunity', 'opportunity'],
  ['/raw', 'raw'],
  ['/system', 'system'],
  ['/account', 'account'],
  ['/help', 'help'],
];

async function overflow(page: Page) {
  return page.evaluate(() => {
    const doc = document.documentElement;
    const content = document.querySelector('.content') as HTMLElement | null;
    return {
      page: doc.scrollWidth - doc.clientWidth,
      content: content ? content.scrollWidth - content.clientWidth : 0,
    };
  });
}

test.describe('B4 — điện thoại 375px', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize(PHONE);
    await loginAsOwner(page);
  });

  for (const [path, name] of MAIN_SCREENS) {
    test(`không cuộn ngang · ${name}`, async ({ page }) => {
      await page.goto(path);
      await expect(page.locator('header.hd')).toBeVisible();
      await expect(page.locator('.content')).not.toBeEmpty();
      await page.waitForLoadState('networkidle');
      await settle(page);
      await page.screenshot({ path: join(shots, `phone-${name}.png`), fullPage: false });
      const o = await overflow(page);
      expect(o.page, `${name}: trang cuộn ngang ${o.page}px`).toBeLessThanOrEqual(0);
      expect(o.content, `${name}: nội dung tràn ngang ${o.content}px`).toBeLessThanOrEqual(0);
      // Header một dòng, nút danh mục + tiêu đề + các nút bên phải đều trong màn.
      const hd = (await page.locator('header.hd').boundingBox())!;
      expect(hd.width).toBeLessThanOrEqual(PHONE.width);
      for (const sel of ['.hd-menu', '.hd-bell', '.hd-theme']) {
        const b = (await page.locator(`header.hd ${sel}`).boundingBox())!;
        expect(b.x + b.width, `${name}: ${sel} trong màn`).toBeLessThanOrEqual(PHONE.width);
      }
    });
  }

  test('ngăn kéo danh mục: mở bằng nút ☰, chọn màn thì tự đóng, Esc cũng đóng', async ({ page }) => {
    await page.goto('/overview');
    const sidebar = page.locator('aside.sb');
    await expect(sidebar).toBeHidden();
    const menu = page.getByRole('button', { name: 'Mở danh mục' });
    await menu.click();
    await expect(sidebar).toBeVisible();
    await expect(page.getByRole('button', { name: 'Đóng danh mục' })).toHaveAttribute('aria-expanded', 'true');
    await settle(page);
    await page.screenshot({ path: join(shots, 'phone-drawer.png') });
    // Ngăn kéo luôn hiện đủ tên mục (không phải rail icon).
    await expect(sidebar).toHaveAttribute('data-mode', 'full');
    await sidebar.getByRole('link', { name: /Cài đặt/ }).click();
    await expect(page).toHaveURL(/\/system$/);
    await expect(sidebar).toBeHidden();

    await page.getByRole('button', { name: 'Mở danh mục' }).click();
    await expect(sidebar).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(sidebar).toBeHidden();
    // Chạm nền tối cũng đóng.
    await page.getByRole('button', { name: 'Mở danh mục' }).click();
    await page.mouse.click(PHONE.width - 10, PHONE.height / 2);
    await expect(sidebar).toBeHidden();
  });

  test('khung Gen phủ toàn màn hình, đóng bằng nút X', async ({ page }) => {
    await page.goto('/overview');
    await page.getByRole('button', { name: /Hỏi Gen/ }).click();
    const panel = page.getByRole('complementary', { name: /Gen — trợ lý quản trị/ });
    await expect(panel).toBeVisible();
    const b = (await panel.boundingBox())!;
    expect(Math.round(b.x)).toBe(0);
    expect(Math.round(b.width)).toBe(PHONE.width);
    expect(Math.round(b.height)).toBe(PHONE.height);
    await settle(page);
    await page.screenshot({ path: join(shots, 'phone-gen.png') });
    expect((await overflow(page)).page).toBeLessThanOrEqual(0);
    await panel.getByRole('button', { name: 'Đóng khung Gen' }).click();
    await expect(panel).toBeHidden();
  });

  test('bảng thông báo vừa màn điện thoại', async ({ page }) => {
    await page.goto('/overview');
    await page.getByRole('button', { name: /^Thông báo/ }).click();
    const dlg = page.getByRole('dialog', { name: 'Thông báo' });
    await expect(dlg).toBeVisible();
    const b = (await dlg.boundingBox())!;
    expect(b.x).toBeGreaterThanOrEqual(0);
    expect(b.x + b.width).toBeLessThanOrEqual(PHONE.width);
    await settle(page);
    await page.screenshot({ path: join(shots, 'phone-bell.png') });
  });
});

test.describe('B5–B7 (máy tính)', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 1280, height: 800 });
    await loginAsOwner(page);
  });

  test('404: đường dẫn lạ → trang Không tìm thấy trong khung Console, về trang chủ', async ({ page }) => {
    await page.goto('/khong-co-trang-nay');
    await expect(page.getByRole('heading', { name: 'Không tìm thấy trang' })).toBeVisible();
    await expect(page.locator('aside.sb')).toBeVisible();
    await settle(page);
    await page.screenshot({ path: join(shots, 'notfound-1280.png') });
    await page.getByRole('button', { name: 'Về trang chủ' }).click();
    // v0.1.55: "/" của Owner là Mặt tiền (/owner), không còn /overview.
    await expect(page).toHaveURL(/\/owner$/);
  });

  test('chuông: số chưa đọc, cập nhật trực tiếp qua WebSocket, bấm mở trang liên quan', async ({ page }) => {
    await page.goto('/overview');
    const bell = page.locator('header .hd-bell');
    await expect(bell).toHaveAccessibleName('Thông báo — 1 chưa đọc');
    // Thông báo mới từ máy chủ (sao lưu lỗi) → huy hiệu tăng ngay, không cần tải lại.
    await mockHook(page.request, 'notify', { kind: 'backup.failed', title: 'Sao lưu thất bại', body: 'Hết dung lượng.', link: '/system?tab=storage' });
    await expect(bell).toHaveAccessibleName('Thông báo — 2 chưa đọc');
    await expect(bell.locator('.hd-bell__badge')).toHaveText('2');
    await bell.click();
    const dlg = page.getByRole('dialog', { name: 'Thông báo' });
    await expect(dlg.locator('.nt-item').first()).toContainText('Sao lưu thất bại');
    await settle(page);
    await page.screenshot({ path: join(shots, 'bell-1280.png') });
    await dlg.getByRole('button', { name: /Sao lưu thất bại/ }).click();
    await expect(page).toHaveURL(/\/system\?tab=storage$/);
    await expect(bell).toHaveAccessibleName('Thông báo — 1 chưa đọc');
    await bell.click();
    await page.getByRole('button', { name: 'Đánh dấu đã đọc hết' }).click();
    await expect(bell).toHaveAccessibleName('Thông báo');
    // Đã lưu ở máy chủ: tải lại vẫn 0.
    await page.reload();
    await expect(page.locator('header .hd-bell')).toHaveAccessibleName('Thông báo');
  });

  test('sáng/tối: mặc định theo hệ thống, nút header đổi và nhớ sau khi tải lại', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'light' });
    await page.goto('/overview');
    const html = page.locator('html');
    await expect(html).toHaveAttribute('data-theme', 'light');
    await page.emulateMedia({ colorScheme: 'dark' });
    await expect(html).toHaveAttribute('data-theme', 'dark');
    const bg = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    const darkBg = await bg();

    await page.getByRole('button', { name: /Giao diện: Theo hệ thống/ }).click();
    await expect(html).toHaveAttribute('data-theme', 'light');
    expect(await bg()).not.toBe(darkBg);
    await settle(page);
    await page.screenshot({ path: join(shots, 'light-overview-1280.png') });
    await page.goto('/system');
    await settle(page);
    await page.screenshot({ path: join(shots, 'light-system-1280.png') });
    await page.reload();
    await expect(html).toHaveAttribute('data-theme', 'light');
    await page.getByRole('button', { name: /Giao diện: Sáng/ }).click();
    await expect(html).toHaveAttribute('data-theme', 'dark');
    expect(await bg()).toBe(darkBg);
  });
});
