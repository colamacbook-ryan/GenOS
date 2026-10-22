import { expect, test } from '@playwright/test';
import { loginAsOwner, p3Hook, resetMock } from './support';

/**
 * v0.1.55 (G1) — "Chế độ tiêu chuẩn" + "Về mặc định" ở Cài đặt › Bộ não AI (mock-defaults: thẻ Lọc tin mặc định "Đã đổi").
 */
test.describe('v0.1.55 — Chế độ tiêu chuẩn / Về mặc định', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
  });

  test('Bộ não AI: thẻ Lọc tin "Đã đổi" → Về mặc định → Xác nhận → chip "Mặc định"', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/system?tab=brain');
    const strip = page.getByTestId('standard-strip');
    await expect(strip).toBeVisible();
    await expect(strip.getByTestId('standard-strip-text')).toHaveText('Chế độ tiêu chuẩn: đã đổi 1 mục');

    const card = page.getByRole('region', { name: 'Lọc tin' });
    const chip = card.getByTestId('default-badge-triage');
    await expect(chip).toHaveText('Đã đổi');
    await card.getByRole('button', { name: 'Về mặc định' }).click();

    const dlg = page.getByRole('dialog', { name: /Về mặc định: Lọc tin/ });
    await expect(dlg.getByTestId('reset-current')).toContainText('ngưỡng điểm 55');
    await expect(dlg.getByTestId('reset-default')).toContainText('ngưỡng điểm 30');
    const post = page.waitForResponse((r) => r.url().includes('/defaults/triage/reset') && r.request().method() === 'POST');
    await dlg.getByRole('button', { name: 'Xác nhận' }).click();
    expect((await post).status()).toBe(200);

    await expect(dlg).toBeHidden();
    await expect(card.getByTestId('default-badge-triage')).toHaveText('Mặc định');
    await expect(card.getByRole('button', { name: 'Về mặc định' })).toHaveCount(0);
    await expect(strip.getByTestId('standard-strip-text')).toHaveText('Chế độ tiêu chuẩn: đang dùng');
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('Về mặc định tất cả cần mã PIN: Xác nhận → hộp PIN → đưa mọi mục "Đã đổi" về mặc định', async ({ page }) => {
    await loginAsOwner(page);
    await p3Hook(page.request, 'defaults', 'customize', { keys: ['backup', 'ai_cost'] });
    await page.goto('/system?tab=brain');
    const strip = page.getByTestId('standard-strip');
    await expect(strip.getByTestId('standard-strip-text')).toHaveText('Chế độ tiêu chuẩn: đã đổi 3 mục');
    await strip.getByRole('button', { name: 'Về mặc định tất cả' }).click();
    const dlg = page.getByRole('dialog', { name: /Về mặc định tất cả/ });
    await expect(dlg).toContainText('Khoá API');
    await dlg.getByRole('button', { name: 'Xác nhận' }).click();
    const pin = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await expect(pin).toBeVisible();
    await page.keyboard.type('246810');
    await expect(strip.getByTestId('standard-strip-text')).toHaveText('Chế độ tiêu chuẩn: đang dùng');
    await expect(strip.getByTestId('standard-strip-badge')).toHaveText('Mặc định');
  });

  test('gợi ý "Áp model chuẩn theo vai" chỉ hiện khi ≥ 2 dòng gán lõi đã đổi; neo #chuan', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/system?tab=brain');
    await expect(page.getByTestId('standard-strip')).toBeVisible();
    await expect(page.getByTestId('apply-standard')).toHaveCount(0);
    await p3Hook(page.request, 'defaults', 'customize', { keys: ['binding:core.gen', 'binding:core.reply'] });
    await page.goto('/overview');                        // tải lại trang thật (chỉ đổi #hash thì không tải lại)
    await page.goto('/system?tab=brain#chuan');
    const apply = page.getByTestId('apply-standard');
    await expect(apply).toBeVisible();
    await expect(page.locator('#chuan')).toBeInViewport();
    await apply.click();
    const dlg = page.getByRole('dialog', { name: /Áp model chuẩn theo vai/ });
    const post = page.waitForResponse((r) => r.url().includes('/defaults/apply-standard') && r.request().method() === 'POST');
    await dlg.getByRole('button', { name: 'Xác nhận' }).click();
    expect((await post).status()).toBe(200);
    await expect(page.getByTestId('apply-standard')).toHaveCount(0);
  });
});
