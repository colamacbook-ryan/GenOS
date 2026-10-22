import { expect, test, type Page } from '@playwright/test';
import { OWNER, loginAs, loginAsOwner, p3Hook, resetMock } from './support';

/**
 * v0.1.44 (F-4b) — Trợ giúp › Gói chẩn đoán cho người hỗ trợ (mock tất định): Tạo gói (PIN) → đang tạo → xong → nút
 * Tải đúng href cùng gốc và tải được tệp .zip; genh cũ → lệnh chạy tay. Kèm: lỗi JS chưa bắt được báo về
 * `POST /client-errors`, mọi phản hồi lỗi của API có X-Request-ID.
 */

async function enterPin(page: Page) {
  const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
  await expect(dlg).toBeVisible();
  await page.getByLabel('Mã PIN — chữ số 1/6').click();
  await page.keyboard.type(OWNER.pin);
  await expect(dlg).toBeHidden();
}

test.describe('Gói chẩn đoán (v0.1.44)', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  test('Trợ giúp → Tạo gói chẩn đoán (PIN) → chờ xong → nút Tải có đúng href và tải được .zip', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/help');
    const card = page.getByTestId('diagnostics-card');
    await expect(card).toBeVisible();
    await card.getByRole('button', { name: 'Tạo gói chẩn đoán' }).click();
    await enterPin(page);
    await expect(card.getByTestId('diagnostics-working')).toBeVisible();
    await expect(card.getByTestId('diagnostics-meta')).toContainText(/Mã yêu cầu [0-9a-f]{16}/);

    const link = card.getByRole('link', { name: /^Tải gói chẩn đoán \(47 KB\)$/ });
    await expect(link).toBeVisible({ timeout: 15_000 });
    await expect(link).toHaveAttribute('href', '/api/v1/system/diagnostics/download');
    await expect(card).toContainText('Đã lọc mật khẩu/khoá/token trước khi đóng gói');

    const download = page.waitForEvent('download');
    await link.click();
    const file = await download;
    expect(file.suggestedFilename()).toBe('genh-doctor-20261003T010203Z.zip');
    await expect(page.locator('body')).not.toContainText('[object Object]');
  });

  test('genh không nhận yêu cầu quá 15 phút (stale) → câu "Máy chủ chưa nhận" + lệnh tay + Tạo gói lại được', async ({ page }) => {
    await p3Hook(page.request, 'diagnostics', 'seed', { diag: 'stale' });
    await loginAsOwner(page);
    await page.goto('/help');
    const card = page.getByTestId('diagnostics-card');
    const stale = card.getByTestId('diagnostics-stale');
    await expect(stale).toContainText('Máy chủ chưa nhận yêu cầu tạo gói');
    await expect(stale).toContainText('genh doctor');
    await expect(card.getByTestId('diagnostics-working')).toHaveCount(0);
    await card.getByRole('button', { name: 'Tạo gói chẩn đoán' }).click();
    await enterPin(page);
    await expect(card.getByRole('link', { name: /^Tải gói chẩn đoán/ })).toBeVisible({ timeout: 15_000 });
    await expect(card.getByTestId('diagnostics-stale')).toHaveCount(0);
  });

  test('genh cũ → hiện lệnh "genh doctor" để chạy trên máy chủ; vai trò khác Owner không thấy thẻ', async ({ page }) => {
    await p3Hook(page.request, 'diagnostics', 'seed', { diag: 'unsupported' });
    await loginAsOwner(page);
    await page.goto('/help');
    const cmd = page.getByTestId('diagnostics-command');
    await expect(cmd).toContainText('genh doctor');
    await expect(page.getByRole('button', { name: 'Tạo gói chẩn đoán' })).toHaveCount(0);

    await page.context().clearCookies();
    await loginAs(page, 'auditor@genesis.local');
    await page.goto('/help');
    await expect(page.getByRole('heading', { level: 2, name: 'Trợ giúp' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Báo lỗi — chép thông tin' })).toBeVisible();
    await expect(page.getByTestId('diagnostics-card')).toHaveCount(0);
  });

  test('lỗi JS chưa bắt → POST /client-errors (mã ERR-…); phản hồi lỗi API có X-Request-ID + request_id', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/help');
    await expect(page.getByTestId('diagnostics-card')).toBeVisible();
    const sent = page.waitForRequest((r) => r.url().endsWith('/api/v1/client-errors') && r.method() === 'POST');
    await page.evaluate(() => {
      setTimeout(() => {
        throw new Error('lỗi thử e2e');
      }, 0);
    });
    const req = await sent;
    const body = req.postDataJSON() as Record<string, unknown>;
    expect(body.error_id).toMatch(/^ERR-[A-Z0-9]+-[A-Z0-9]{4}$/);
    expect(body.message).toContain('lỗi thử e2e');
    expect(body.path).toBe('/help');
    await expect.poll(async () => ((await p3Hook(page.request, 'diagnostics', 'clientErrors')) as unknown[]).length).toBeGreaterThan(0);

    const res = await page.request.get('/api/v1/khong-ton-tai');
    expect(res.status()).toBe(404);
    const rid = res.headers()['x-request-id'];
    expect(rid).toMatch(/^[0-9a-f]{16}$/);
    expect(((await res.json()) as { request_id?: string }).request_id).toBe(rid);
  });
});
