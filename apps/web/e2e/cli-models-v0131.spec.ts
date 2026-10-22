import { expect, test } from '@playwright/test';
import { OWNER, loginAsOwner, resetMock } from './support';

/**
 * v0.1.31 — Boss 01/10: bước 4 "Bộ não AI" không thấy model / nhóm model nào để chọn; chưa có Claude Code CLI.
 * Mock: test/mock-phase2.ts (danh sách theo nhóm như gh.providers.catalog; Claude Code đăng nhập qua link claude.com).
 */
test.describe('v0.1.31 — model CLI theo nhóm + Claude Code CLI', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  test('Antigravity: Kiểm tra → ô chọn có nhóm Gemini + Claude (qua Antigravity) → Dùng model này', async ({ page }) => {
    await page.goto('/guide/4');
    const row = page.locator('.prov-row', { hasText: 'Antigravity Brain' });
    await row.getByRole('button', { name: 'Kiểm tra' }).click();
    await expect(row).toContainText('Gọi thử OK');
    const select = row.getByRole('combobox', { name: /Model cho Antigravity Brain/ });
    await expect(select.locator('optgroup')).toHaveCount(2);
    await expect(select.locator('optgroup').nth(0)).toHaveAttribute('label', 'Gemini');
    await expect(select.locator('optgroup').nth(1)).toHaveAttribute('label', 'Claude (qua Antigravity)');
    await expect(select.locator('option')).toHaveCount(4); // v0.1.32: 3 model gốc (mức suy nghĩ là ô riêng) + model đã lưu
    await select.selectOption('claude-sonnet-4-6-thinking');
    await row.getByRole('button', { name: 'Dùng model này' }).click();
    await expect(row.getByRole('button', { name: 'Đang dùng' })).toBeVisible();
    await expect(row).toContainText('Đang dùng claude-sonnet-4-6-thinking');
  });

  test('Claude Code CLI: cảnh báo điều khoản, đăng nhập bằng link + mã, chọn model', async ({ page }) => {
    await page.goto('/guide/4');
    const section = page.getByTestId('setup-cli-claude_code_cli');
    await expect(section).toContainText('Claude Code CLI · gói Claude (tuỳ chọn)');
    await expect(section.getByTestId('claude-risk')).toContainText('Sếp tự quyết rủi ro');
    await section.getByRole('button', { name: 'Đăng nhập Claude' }).click();
    // v0.1.45 (F-20): thêm tài khoản CLI cần mã PIN (`cli.switch_account`).
    const pin = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await expect(pin).toBeVisible();
    await page.getByLabel('Mã PIN — chữ số 1/6').click();
    await page.keyboard.type(OWNER.pin);
    await expect(pin).toBeHidden();
    await expect(section.getByRole('link', { name: /Mở trang đăng nhập Claude/ })).toHaveAttribute('href', /^https:\/\/claude\.com\/cai\/oauth\/authorize/);
    await section.getByLabel('Mã xác thực').fill('abcd#efgh');
    await section.getByRole('button', { name: 'Xác nhận' }).click();
    await expect(section).toContainText('c@example.test');
    await expect(section).toContainText('Đang hoạt động');
    await expect(section).toContainText('tự gia hạn');

    const row = page.locator('.prov-row', { hasText: 'Claude Code CLI' });
    await row.getByRole('button', { name: 'Kiểm tra' }).click();
    const select = row.getByRole('combobox', { name: /Model cho Claude Code CLI/ });
    await expect(select.locator('optgroup')).toHaveAttribute('label', 'Claude');
    await select.selectOption('opus');
    await row.getByRole('button', { name: 'Dùng model này' }).click();
    await expect(row).toContainText('Đang dùng opus');
  });

  // v0.1.42 (F-61): thẻ tài khoản CLI chỉ ở Kết nối (mục Bộ não AI); API & Model chỉ còn liên kết tới đó.
  test('Kết nối: có thẻ Claude Code CLI (tuỳ chọn) bên cạnh Antigravity', async ({ page }) => {
    await page.goto('/api');
    await page.getByRole('link', { name: /Tài khoản Google \/ Claude CLI ở Kết nối/ }).click();
    await expect(page).toHaveURL(/\/connections#brain$/);
    await expect(page.getByTestId('cli-card-antigravity_cli')).toBeVisible();
    const card = page.getByTestId('cli-card-claude_code_cli');
    await expect(card).toContainText('Tài khoản Claude Code CLI');
    await expect(card.getByTestId('claude-risk')).toBeVisible();
    await expect(card.getByRole('button', { name: 'Đăng nhập Claude' })).toBeVisible();
  });
});
