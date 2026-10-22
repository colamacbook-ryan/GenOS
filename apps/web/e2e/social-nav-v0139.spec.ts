/**
 * v0.1.39 (F-32): trang Tài khoản mạng xã hội có lối vào cố định.
 * v0.1.42 (F-7): lối vào là thẻ Facebook ở Kết nối (/connections, chỉ Owner) + menu tài khoản — không còn mục
 * "Mạng xã hội" riêng trên thanh bên; đang ở /social thì thanh bên tô sáng "Kết nối". Vai trò khác không thấy thẻ.
 */
import { expect, test } from '@playwright/test';
import { loginAs, loginAsOwner, resetMock } from './support';

test.describe('v0.1.39 · lối vào trang Tài khoản mạng xã hội', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
  });

  test('Owner: không còn mục "Mạng xã hội" trên thanh bên; /social tô sáng "Kết nối"', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/overview');
    const nav = page.getByRole('navigation', { name: 'Danh mục màn hình' });
    await expect(nav.getByRole('link', { name: /Kết nối/ })).toBeVisible();
    await expect(nav.getByRole('link', { name: /Mạng xã hội/ })).toHaveCount(0);
    await page.goto('/social');
    await expect(page.getByRole('heading', { level: 2, name: 'Tài khoản mạng xã hội' })).toBeVisible();
    await expect(nav.getByRole('link', { name: /Kết nối/ })).toHaveAttribute('aria-current', 'page');
  });

  test('Owner: Kết nối có thẻ Facebook dẫn tới /social', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/connections');
    const card = page.locator('[data-gen-target="system.channels.facebook"]');
    await expect(card).toBeVisible();
    await expect(card.locator('[data-status]')).toHaveCount(1);
    await card.getByRole('link', { name: 'Mở Facebook' }).click();
    await expect(page).toHaveURL(/\/social$/);
    await expect(page.getByRole('heading', { level: 2, name: 'Tài khoản mạng xã hội' })).toBeVisible();
  });

  test('Owner: link cũ /system?tab=channels chuyển sang Kết nối', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/system?tab=channels');
    await expect(page).toHaveURL(/\/connections$/);
    await expect(page.locator('[data-gen-target="system.channels.facebook"]')).toBeVisible();
  });

  test('Vận hành: không có mục "Mạng xã hội" trên thanh bên', async ({ page }) => {
    await loginAs(page, 'operator@genesis.local');
    await page.goto('/overview');
    const nav = page.getByRole('navigation', { name: 'Danh mục màn hình' });
    await expect(nav.locator('.sb-group').first()).toBeVisible();
    await expect(nav.getByRole('link', { name: /Mạng xã hội/ })).toHaveCount(0);
    await expect(page.locator('[data-screen="social"]')).toHaveCount(0);
  });
});
