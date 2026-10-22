import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { loginAsOwner, mockHook, resetMock, resultsDir, settle } from './support';

/**
 * v0.1.68 — Nhật ký lắng nghe, trên bản mock chạy thật ở 390px và 1440px: không lỗi console, không tràn ngang, dòng tin +
 * sự kiện + chuỗi "hệ thống đã làm gì", "Xem thêm", lọc, cập nhật trực tiếp (WebSocket), link nháp sang Bàn làm việc.
 * Ảnh: `SHOTS_DIR` (nếu đặt) hoặc test-results/visual.
 */
const shots = process.env.SHOTS_DIR ?? join(resultsDir, 'visual');

async function noHorizontalScroll(page: Page) {
  const d = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(d).toBeLessThanOrEqual(0);
}

for (const [label, size] of [['390', { width: 390, height: 844 }], ['1440', { width: 1440, height: 900 }]] as const) {
  test(`Nhật ký lắng nghe — ${label}px`, async ({ page }) => {
    mkdirSync(shots, { recursive: true });
    await page.setViewportSize(size);
    await resetMock(page.request as APIRequestContext, 'finished');
    await loginAsOwner(page);
    const errors: string[] = [];
    page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
    page.on('pageerror', (e) => errors.push(String(e)));

    await page.goto('/listenlog');
    await expect(page.getByRole('heading', { name: 'Nhật ký lắng nghe', level: 2 })).toBeVisible();
    await expect(page.getByTestId('ll-not-listening-note')).toContainText('Không nghe');
    const messages = page.getByTestId('ll-message');
    const events = page.getByTestId('ll-event');
    await expect(messages.first()).toBeVisible();
    await expect(events.first()).toBeVisible();
    await expect(page.getByRole('group', { name: /Số liệu 24 giờ/ })).toBeVisible();
    // chuỗi "hệ thống đã làm gì" + link nháp
    await expect(page.getByRole('list', { name: 'Hệ thống đã làm gì' }).first()).toBeVisible();
    await settle(page);
    await page.screenshot({ path: join(shots, `nhat-ky-${label}-dau-trang.png`) });

    await page.getByRole('region', { name: 'Dòng thời gian' }).scrollIntoViewIfNeeded();
    await settle(page);
    await page.screenshot({ path: join(shots, `nhat-ky-${label}-danh-sach.png`) });
    await page.evaluate(() => window.scrollTo(0, 0));

    // sự kiện hiện cùng dòng thời gian, có câu tiếng Việt
    await expect(events.filter({ hasText: 'vừa được thêm vào nhóm' }).first()).toContainText('Lắng nghe im lặng');

    // "Xem thêm" nối thêm dòng
    const before = await messages.count();
    await page.getByRole('button', { name: 'Xem thêm' }).click();
    await expect.poll(async () => messages.count()).toBeGreaterThan(before);

    // cập nhật trực tiếp: sự kiện mới rồi tin mới tới qua WebSocket → xuất hiện ở ĐẦU dòng thời gian (không cần tải lại trang)
    const topOf = async () => (await page.getByTestId(/^ll-(message|event)$/).first().innerText()).trim();
    const t0 = await topOf();
    await mockHook(page.request, 'listenevent');
    await expect.poll(topOf, { timeout: 8000 }).not.toBe(t0);
    await expect(events.first()).toContainText('thành viên mới');
    const t1 = await topOf();
    await mockHook(page.request, 'raw');
    await expect.poll(topOf, { timeout: 8000 }).not.toBe(t1);
    await expect(page.getByTestId('ll-message').first()).toBeVisible();

    // link nháp → Bàn làm việc
    const draft = page.locator('a.ll-draft').first();
    await expect(draft).toHaveAttribute('href', /\/workbench\?id=draft-/);

    // lọc: chỉ sự kiện
    await page.getByRole('radio', { name: 'Chỉ sự kiện' }).click();
    await expect(messages).toHaveCount(0);
    await expect(events.first()).toBeVisible();
    await settle(page);
    await page.screenshot({ path: join(shots, `nhat-ky-${label}-chi-su-kien.png`) });
    // lọc: chỉ tin được tag (sự kiện biến mất, có ghi chú)
    await page.getByRole('radio', { name: 'Tin và sự kiện' }).click();
    await page.getByRole('button', { name: 'Chỉ tin được tag' }).click();
    await expect(events).toHaveCount(0);
    await expect(page.getByText(/các sự kiện của kênh được ẩn/)).toBeVisible();
    await page.screenshot({ path: join(shots, `nhat-ky-${label}-chi-tin-tag.png`) });
    // tìm chữ phía máy chủ
    await page.getByRole('button', { name: /Xoá \d+ bộ lọc/ }).click();
    await page.getByRole('searchbox', { name: 'Tìm chữ' }).fill('zzzkhongco');
    await expect(page.getByText('Không có dòng nào khớp bộ lọc')).toBeVisible();

    await noHorizontalScroll(page);
    expect(await page.locator('body').innerText()).not.toContain('[object Object]');
    expect(errors.filter((e) => !/favicon|Failed to load resource/.test(e))).toEqual([]);
  });
}

test('vai Kiểm soát (Auditor): tin 1-1 và sự kiện người lạ nhắn riêng bị ẩn, số dài bị che', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await resetMock(page.request as APIRequestContext, 'finished');
  const { loginAs, AUDITOR } = await import('./support');
  await loginAs(page, AUDITOR.email);
  await page.goto('/listenlog');
  await expect(page.getByTestId('ll-message').first()).toBeVisible();
  await expect(page.getByText('Tin riêng', { exact: true })).toHaveCount(0);
  await expect(page.getByText(/Người mới nhắn riêng lần đầu/)).toHaveCount(0);
  expect(await page.locator('body').innerText()).not.toContain('0912345678');
});

test('vai Quản lý (không có quyền xem tầng dữ liệu): không vào được màn, không tràn ngang', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await resetMock(page.request as APIRequestContext, 'finished');
  const { loginAs, MANAGER } = await import('./support');
  await loginAs(page, MANAGER.email);
  await page.goto('/listenlog');
  // Quản lý không có `data.read`: danh mục không có màn này ⇒ thẻ "không có quyền xem màn này" (API cũng trả 403).
  await expect(page.getByText('Vai trò của bạn không có quyền xem màn này')).toBeVisible();
  expect(await page.locator('body').innerText()).not.toContain('[object Object]');
  await noHorizontalScroll(page);
});
