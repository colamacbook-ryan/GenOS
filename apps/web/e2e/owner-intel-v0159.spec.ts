import { expect, test, type Page } from '@playwright/test';
import { expectPhoneNav, loginAsOwner, resetMock } from './support';

/**
 * v0.1.79 (G1) — TRANG TÌNH BÁO (`/intel`, trang chủ của Sếp) trên máy chủ giả (tất định; `test/mock-intel.ts`):
 *  - Hôm nay: tín hiệu mở xếp 🔴🟠⚪, mỗi dòng có "Việc Sếp cần làm", 3 dòng đầu Cần quyết + link /decide;
 *  - bấm một dòng → ngăn chi tiết (bằng chứng đã che) → Đúng / Sai ▾ (3 lý do) / Không cần báo gọi đúng API; chuyển trạng thái;
 *  - tab Tín hiệu: bảng đủ cột, lọc theo mức/trạng thái, mở thẳng bằng ?tin=; tab Chạy thử + Thiết lập có mặt;
 *  - 390px: không cuộn ngang, ngăn chi tiết thành cả màn, Quay lại đóng.
 * (Bản v0.1.59 của tệp này kiểm Mặt tiền cũ — IntelScreen — nay không còn được gắn vào trang; xem docs/releases v0.1.79.)
 */

async function noHorizontalScroll(page: Page): Promise<void> {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over).toBeLessThanOrEqual(0);
}

test.describe('v0.1.79 · Trang Tình báo', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  test('Hôm nay: tín hiệu theo mức 🔴🟠⚪, "Việc Sếp cần làm", 3 dòng Cần quyết + link /decide; bốn tab của Sếp', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto('/intel');
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Tình báo');
    for (const name of ['Hôm nay', 'Tín hiệu', 'Chạy thử 14 ngày', 'Thiết lập', 'Tổng quan', 'Hộp thư']) await expect(page.getByRole('tab', { name })).toBeVisible();
    await expect(page.getByTestId('intel-level-red').getByTestId('intel-signal-line')).toHaveCount(2);
    await expect(page.getByTestId('intel-level-amber').getByTestId('intel-signal-line')).toHaveCount(2);
    await expect(page.getByTestId('intel-level-grey').getByTestId('intel-signal-line')).toHaveCount(1);
    const order = await page.evaluate(() => ['red', 'amber', 'grey'].map((l) => (document.querySelector(`[data-testid="intel-level-${l}"]`) as HTMLElement).getBoundingClientRect().top));
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    await expect(page.getByTestId('intel-level-red')).toContainText('Việc Sếp cần làm: Gọi số cũ xác minh, chưa chuyển tiền');
    const decide = page.getByRole('region', { name: 'Cần Sếp quyết' });
    await expect(decide.getByTestId('intel-ask-row')).toHaveCount(2);
    await expect(decide).toContainText('Cần Sếp quyết (2)');
    await decide.getByTestId('intel-decide-all').click();
    await expect(page).toHaveURL(/\/decide$/);
    expect(errors).toEqual([]);
    expect(await page.locator('body').innerText()).not.toContain('[object Object]');
  });

  test('bấm một dòng → ngăn chi tiết có bằng chứng; Sai ▾ hỏi 3 lý do và gửi đúng; Không cần báo; Đã xong', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/intel');
    await page.getByRole('button', { name: 'Người mới gửi STK lạ, yêu cầu chuyển khoản', exact: true }).click();
    const drawer = page.getByRole('complementary', { name: 'Chi tiết tín hiệu' });
    await expect(drawer).toBeVisible();
    await expect(page).toHaveURL(/\/intel\?tin=sg-1$/);
    await expect(drawer).toContainText('Em chuyển khoản vào •••456 giúp anh nhé');
    await expect(drawer.getByTestId('intel-drawer-action')).toContainText('Gọi số cũ xác minh');

    await drawer.getByRole('button', { name: 'Sai ▾' }).click();
    const grp = drawer.getByRole('group', { name: 'Vì sao báo sai?' });
    await expect(grp.getByRole('button')).toHaveText(['Khách vẫn mua', 'Không gấp', 'NV lo rồi', 'Huỷ']);
    const req = page.waitForRequest((r) => r.url().endsWith('/signals/sg-1/feedback') && r.method() === 'POST');
    await grp.getByRole('button', { name: 'Sai: Không gấp' }).click();
    expect((await req).postDataJSON()).toEqual({ verdict: 'wrong', reason: 'not_urgent' });
    await expect(drawer.getByText('Đã chấm Sai')).toBeVisible();

    await drawer.getByRole('button', { name: 'Đóng chi tiết tín hiệu' }).click();
    await expect(drawer).toBeHidden();
    await page.getByRole('button', { name: 'Báo đúng: Khách Anh Tuấn doạ huỷ đơn' }).click();
    await expect(page.getByTestId('intel-level-red').getByText('Đã chấm Đúng')).toBeVisible();

    await page.getByRole('button', { name: 'Lời hứa giao 20 bao xi măng trước 15h', exact: true }).click();
    const d2 = page.getByRole('complementary', { name: 'Chi tiết tín hiệu' });
    const noNeed = page.waitForRequest((r) => r.url().endsWith('/signals/sg-3/feedback') && r.method() === 'POST');
    await d2.getByRole('button', { name: 'Không cần báo' }).click();
    expect((await noNeed).postDataJSON()).toEqual({ verdict: 'unimportant' });
    const done = page.waitForRequest((r) => r.url().endsWith('/signals/sg-3/transition') && r.method() === 'POST');
    await d2.getByRole('button', { name: 'Đã xong' }).click();
    expect((await done).postDataJSON()).toEqual({ to: 'resolved' });
    await expect(page.getByTestId('intel-level-amber').getByTestId('intel-signal-line')).toHaveCount(1);
  });

  test('tab Tín hiệu: bảng đủ cột, lọc theo mức và trạng thái, mở thẳng bằng ?tin=', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/intel?tab=signals');
    await expect(page.getByRole('tab', { name: 'Tín hiệu' })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByTestId('intel-signal-row')).toHaveCount(5);
    await expect(page.getByRole('columnheader')).toHaveText(['Mã', 'Mức', 'Loại', 'Tiêu đề', 'Việc Sếp cần làm', 'Độ chắc', 'Trạng thái', 'Lúc', 'Chấm']);
    await page.getByLabel('Mức').selectOption('red');
    await expect(page.getByTestId('intel-signal-row')).toHaveCount(2);
    await page.getByLabel('Mức').selectOption('');
    await page.getByLabel('Trạng thái').selectOption('resolved');
    await expect(page.getByTestId('intel-signal-row')).toHaveCount(1);
    await expect(page.getByTestId('intel-signal-row')).toContainText('Gửi báo giá cho cô Hà');
    await page.goto('/intel?tab=signals&tin=sg-2');
    await expect(page.getByRole('complementary', { name: 'Chi tiết tín hiệu' })).toContainText('Khách Anh Tuấn doạ huỷ đơn');
    expect(await page.locator('body').innerText()).not.toContain('[object Object]');
  });

  test('tab Chạy thử 14 ngày và Thiết lập có nội dung; Cài đặt › Bộ não AI chỉ còn link sang Tình báo', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/intel?tab=trial');
    await expect(page.getByRole('region', { name: 'Chạy thử 14 ngày', exact: true })).toContainText('Đã chấm 4/10 lượt');
    await page.getByRole('tab', { name: 'Thiết lập' }).click();
    await expect(page.getByRole('region', { name: 'Thiết lập Tình báo' })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Khoá, xác minh và cảnh báo' })).toBeVisible();
    await page.goto('/system?tab=brain');
    const moved = page.getByTestId('brain-intel-moved');
    await expect(moved).toContainText('Tình báo đã chuyển sang trang Tình báo');
    await moved.getByRole('link').click();
    await expect(page).toHaveURL(/\/intel\?tab=setup$/);
  });

  test('390px: không cuộn ngang; mở tín hiệu = cả màn có nút Đúng/Sai; Quay lại đóng', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/intel');
    await expect(page.getByTestId('intel-level-red')).toBeVisible();
    await noHorizontalScroll(page);
    await expectPhoneNav(page);
    await page.getByRole('button', { name: 'Người mới gửi STK lạ, yêu cầu chuyển khoản', exact: true }).click();
    const drawer = page.getByRole('complementary', { name: 'Chi tiết tín hiệu' });
    await expect(drawer).toBeVisible();
    const box = await drawer.boundingBox();
    expect(box!.width).toBeGreaterThanOrEqual(388);
    await expect(drawer.getByRole('button', { name: 'Đúng' })).toBeVisible();
    await noHorizontalScroll(page);
    await drawer.getByRole('button', { name: 'Đóng chi tiết tín hiệu' }).click();
    await expect(drawer).toBeHidden();
    await page.goto('/intel?tab=signals');
    await expect(page.getByTestId('intel-signal-row').first()).toBeVisible();
    await noHorizontalScroll(page);
  });
});
