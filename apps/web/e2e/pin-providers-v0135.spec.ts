import { expect, test, type Page } from '@playwright/test';
import { OWNER, SETUP_TOKEN, apiCall, loginAsOwner, resetMock } from './support';

/**
 * v0.1.35 (F-20) — thêm / sửa nhà cung cấp AI, khoá API, chuỗi ưu tiên cần mã PIN (`ai.route_change`).
 * Mock đặt PIN SAU kiểm quyền như API thật; client tự mở hộp PIN khi gặp 423 rồi gửi lại.
 */

const PIN_DIALOG = 'Mã PIN xác nhận thao tác';

async function enterOwnerPin(page: Page): Promise<void> {
  const dlg = page.getByRole('dialog', { name: PIN_DIALOG });
  await expect(dlg).toBeVisible();
  await page.getByLabel('Mã PIN — chữ số 1/6').click();
  await page.keyboard.type(OWNER.pin);
  await expect(dlg).toBeHidden();
}

async function freshAtStep4(page: Page): Promise<void> {
  await page.setViewportSize({ width: 1440, height: 900 });
  await resetMock(page.request, 'fresh');
  await apiCall(page, 'PUT', '/setup/steps/1', { token: SETUP_TOKEN, language: 'vi', mode: 'empty' });
  await apiCall(page, 'PUT', '/setup/steps/2', {
    token: SETUP_TOKEN, display_name: 'Anh Cơ', email: 'owner@example.test', password: 'mot-cau-rat-dai-de-nho-2026', pin: OWNER.pin, pin_confirm: OWNER.pin,
  });
  await apiCall(page, 'PUT', '/setup/steps/3', { org_name: 'Genesis Trading', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND', self_name: 'Anh', bot_calls_me: 'Sếp' });
  await page.goto('/setup');
  // v0.1.59: bước 4 của máy chủ = màn "Nguồn AI" của Thiết lập một màn (dán khoá Gemini/DeepSeek ở lựa chọn thứ hai).
  await expect(page.getByRole('heading', { name: 'Nối nguồn AI cho Gen' })).toBeVisible();
}

const keyForm = (page: Page) => page.getByTestId('setup-ai-keyform-basic');

// v0.1.59: hai ca đầu chạy trên màn Nguồn AI của Thiết lập một màn (bản cũ: form Bộ não AI của bước 4); form đầy đủ vẫn ở /guide/4.
test.describe('PIN nhà cung cấp AI v0.1.35', () => {
  test('bước Nguồn AI: dán khoá → Kiểm tra hỏi mã PIN rồi gọi thử OK ("Đã chạy được")', async ({ page }) => {
    await freshAtStep4(page);
    await expect(keyForm(page).getByText('Cần mã PIN 6 số')).toBeVisible();
    await keyForm(page).getByLabel('Khoá API').fill('AIza-good-key-1234');
    await keyForm(page).getByRole('button', { name: 'Kiểm tra' }).click();
    await enterOwnerPin(page);
    const row = page.getByTestId('setup-ai-provider').filter({ hasText: 'Gemini API' });
    await expect(row).toContainText('Gọi thử OK');
    await expect(page.getByTestId('setup-ai-ready')).toContainText('Đã chạy được');
  });

  test('bước Nguồn AI: huỷ mã PIN → không tạo nhà cung cấp, báo cần mã PIN', async ({ page }) => {
    await freshAtStep4(page);
    await keyForm(page).getByLabel('Khoá API').fill('AIza-good-key-1234');
    await keyForm(page).getByRole('button', { name: 'Kiểm tra' }).click();
    const dlg = page.getByRole('dialog', { name: PIN_DIALOG });
    await expect(dlg).toBeVisible();
    await dlg.getByRole('button', { name: 'Huỷ' }).click();
    await expect(dlg).toBeHidden();
    await expect(page.getByText('Đã huỷ — thao tác cần mã PIN.')).toBeVisible();
    await expect(page.getByTestId('setup-ai-provider')).toHaveCount(0);
    expect(await apiCall(page, 'GET', '/providers')).toEqual([]);
  });

  test('API & Model: bật/tắt hỏi PIN; thêm khoá trong cùng phiên PIN không hỏi lại', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
    await page.goto('/api');
    const card = page.locator('.apm-provider', { hasText: 'DeepSeek API' });
    await expect(card).toBeVisible();

    // Công tắc báo trước cần mã PIN (tooltip), như PinHint ở các hộp thêm/sửa.
    await expect(card.locator('span[title*="cần mã PIN 6 số"]')).toHaveCount(1);
    await card.getByRole('switch', { name: 'Tắt DeepSeek API' }).click();
    await enterOwnerPin(page);
    await expect(card.getByRole('switch', { name: 'Bật DeepSeek API' })).toHaveAttribute('aria-checked', 'false');

    await card.getByRole('button', { name: 'Thêm khoá' }).click();
    const keyDlg = page.getByRole('dialog', { name: /Thêm khoá cho DeepSeek API/ });
    await expect(keyDlg.getByText('Cần mã PIN 6 số')).toBeVisible();
    await keyDlg.getByLabel('Khoá API mới').fill('sk-test-khoa-moi-55102');
    const keyReq = page.waitForResponse((r) => r.url().includes('/keys') && r.request().method() === 'POST');
    await keyDlg.getByRole('button', { name: 'Thêm khoá' }).click();
    expect((await keyReq).status()).toBe(201);
    await expect(keyDlg).toBeHidden();
    await expect(page.getByRole('dialog', { name: PIN_DIALOG })).toHaveCount(0);
    await expect(card).toContainText('DS-KEY-02');
  });

  test('API & Model: đổi thứ tự chuỗi ưu tiên hỏi PIN (phiên mới)', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
    await page.goto('/api');
    const chain = page.locator('.apm-chain-list');
    await expect(chain.locator('.apm-chain-row').nth(2)).toContainText('DeepSeek API');
    await expect(page.getByRole('region', { name: 'Chuỗi chuyển hướng nhà cung cấp' }).getByText('Cần mã PIN 6 số')).toBeVisible();

    await page.getByRole('button', { name: 'Đưa DeepSeek API lên trước' }).click();
    await enterOwnerPin(page);
    await expect(chain.locator('.apm-chain-row').nth(1)).toContainText('DeepSeek API');
    const order = (await apiCall(page, 'GET', '/providers')) as Array<{ name: string; failover_rank: number }>;
    expect(order.sort((a, b) => a.failover_rank - b.failover_rank).map((p) => p.name)).toEqual(['Antigravity Brain', 'DeepSeek API', 'Gemini API']);
  });
});
