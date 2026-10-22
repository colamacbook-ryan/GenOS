import { expect, test } from '@playwright/test';
import { loginAsOwner, resetMock } from './support';

/**
 * v0.1.32 — Boss 01/10: "high" là MỨC SUY NGHĨ, không phải tên model. Bước 4: ô model chỉ có model gốc, ô "Mức suy nghĩ"
 * riêng (Thấp / Vừa / Cao), "Gọi thử OK" kèm giờ, nút "Chẩn đoán" (chỉ Owner) hiện đầu ra thô + "Chép".
 */
test.describe('v0.1.32 — model + mức suy nghĩ tách riêng, Chẩn đoán CLI', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  test('Antigravity: chọn model gốc + mức suy nghĩ, mức model không có bị loại khỏi danh sách', async ({ page }) => {
    await page.goto('/guide/4');
    const row = page.locator('.prov-row', { hasText: 'Antigravity Brain' });
    await row.getByRole('button', { name: 'Kiểm tra' }).click();
    await expect(row.locator('.prov-test')).toContainText(/Gọi thử OK · .* · gemini-3\.8-flash · Cao · lúc \d{2}\/\d{2} \d{2}:\d{2}/);
    const model = row.getByRole('combobox', { name: /Model cho Antigravity Brain/ });
    await expect(model.locator('option', { hasText: /high/i })).toHaveCount(0);
    // Model đã lưu (gemini-2.5-pro) vẫn có trong danh sách — không bao giờ thu gọn.
    await expect(model.locator('option[value="gemini-2.5-pro"]')).toHaveCount(1);
    await model.selectOption('gemini-3.8-flash');
    const effort = row.getByRole('combobox', { name: /Mức suy nghĩ \(effort\) cho Antigravity Brain/ });
    await expect(effort.locator('option')).toHaveText(['Thấp · nhanh, rẻ', 'Vừa · cân bằng', 'Cao · kỹ, chậm hơn']);
    await expect(effort).toHaveValue('high');
    await model.selectOption('gemini-3.1-pro');
    await expect(effort.locator('option')).toHaveCount(2);
    await effort.selectOption('low');
    await row.getByRole('button', { name: 'Dùng model này' }).click();
    await expect(row.getByRole('button', { name: 'Đang dùng' })).toBeVisible();
    await expect(row).toContainText('Đang dùng gemini-3.1-pro · Thấp');
    // Model không chỉnh mức (Claude qua Antigravity) → không có ô mức suy nghĩ.
    await model.selectOption('claude-sonnet-4-6-thinking');
    await expect(effort).toHaveCount(0);
    await expect(row).toContainText('không chỉnh mức suy nghĩ');
  });

  test('Chẩn đoán (Owner): phiên bản, agy models, /model, /effort, 1 lượt gọi — mã thoát + đầu ra thô, có nút Chép', async ({ page }) => {
    await page.goto('/guide/4');
    const row = page.locator('.prov-row', { hasText: 'Antigravity Brain' });
    await row.getByRole('button', { name: 'Chẩn đoán' }).click();
    await expect(row.locator('.cli-diag__step')).toHaveCount(5);
    await expect(row.locator('.cli-diag__step').nth(0)).toContainText('1.2.9');
    await expect(row.locator('.cli-diag__step').nth(1)).toContainText('gemini-3.8-flash-high');
    await expect(row.locator('.cli-diag__step').nth(2)).toContainText('agy -p /model');
    await expect(row.locator('.cli-diag__step').nth(3)).toContainText('agy -p /effort');
    await expect(row.locator('.cli-diag__step').nth(4)).toContainText('mã thoát 0');
    await expect(row.getByRole('button', { name: 'Chép' })).toBeVisible();
  });
});
