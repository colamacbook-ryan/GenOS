import { expect, test } from '@playwright/test';
import { loginAsOwner, resetMock } from './support';

/**
 * v0.1.30 (hotfix) — "Minified React error #31 … object with keys {reasons}" khi không model nào chạy được.
 * Mock trả đúng khuôn máy chủ ≤ v0.1.29 (`try_error` / `detail` là đối tượng `{reasons}`) để tái hiện đường sập.
 */
const REASONS = ['gemini: 429', 'deepseek: đang ngắt mạch'];

test.describe('Hotfix v0.1.30 — không có model AI', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  test('/guide/8 thử trò chuyện: try_error = {reasons} không sập, có "Chọn model" → /setup?buoc=nguon-ai (v0.1.59)', async ({ page }) => {
    await page.route('**/api/v1/setup/steps/8', async (route) => {
      if (route.request().method() !== 'PUT') return route.fallback();
      const body = route.request().postDataJSON() as { name: string };
      const state = await (await route.fetch({ url: route.request().url().replace('/steps/8', '/state'), method: 'GET' })).json();
      return route.fulfill({ json: { ...state, agent: { id: 'a1', name: body.name, try_reply: null, try_error: { reasons: REASONS } } } });
    });
    await page.goto('/guide/8');
    await page.getByLabel('Tên agent').fill('Thư ký');
    await page.getByLabel(/Vai trò/).fill('Nhắc lịch');
    await page.getByRole('button', { name: /Tiếp tục/ }).click();
    await expect(page.getByText(/Đã tạo agent/)).toBeVisible();
    await expect(page.getByText('Đã có lỗi xảy ra')).toHaveCount(0);
    const notice = page.getByTestId('model-unavailable');
    await expect(notice).toContainText('Chưa có model AI hoạt động');
    await notice.getByText('Chi tiết kỹ thuật').click();
    await expect(notice).toContainText('gemini: 429; deepseek: đang ngắt mạch');
    await notice.getByRole('link', { name: /Chọn model/ }).click();
    // v0.1.59: mọi nút "Chọn model" mở màn Thiết lập nguồn AI (Owner đã xong thiết lập vẫn mở lại được để đổi nguồn AI).
    await expect(page).toHaveURL(/\/setup\?buoc=nguon-ai$/);
    await expect(page.getByRole('heading', { name: 'Đổi nguồn AI' })).toBeVisible();
  });

  test('Đánh giá con người: 503 MODEL_UNAVAILABLE (detail đối tượng) → trạng thái "Chọn model", không sập', async ({ page }) => {
    await page.route('**/api/v1/people/reviews**', (route) =>
      route.fulfill({
        status: 503,
        contentType: 'application/problem+json',
        json: { status: 503, code: 'MODEL_UNAVAILABLE', title: 'Chưa có model nào chạy được', detail: { reasons: REASONS } },
      }),
    );
    await page.goto('/people');
    await expect(page.getByRole('heading', { name: 'Đánh giá con người', level: 2 })).toBeVisible();
    await expect(page.getByText('Đã có lỗi xảy ra')).toHaveCount(0);
    await expect(page.getByTestId('model-unavailable')).toContainText('Chưa có model AI hoạt động');
    await expect(page.getByRole('link', { name: /Chọn model/ })).toHaveAttribute('href', '/setup?buoc=nguon-ai');
  });

  // v0.1.42 (F-61): thẻ "Cập nhật phần mềm" chỉ ở Cài đặt › Sao lưu & cập nhật; Trợ giúp có liên kết tới đó.
  test('mục "Cập nhật phần mềm" cố định ở Cài đặt (Trợ giúp dẫn tới), "Kiểm tra bản mới" chạy được', async ({ page }) => {
    await page.goto('/help');
    await page.getByTestId('help-update-link').click();
    await expect(page).toHaveURL(/\/system\?tab=storage$/);
    const section = page.getByTestId('update-section');
    await expect(section).toContainText('v0.1.16');
    await expect(page.getByText('Đang dùng bản mới nhất')).toBeVisible();
    await page.getByRole('button', { name: /Kiểm tra bản mới/ }).click();
    await expect(page.getByText(/Đang dùng bản mới nhất \(v0.1.16\)/)).toBeVisible();
  });

  // v0.1.42 (F-7): lối vào ở Cài đặt (liên kết đầu màn) + menu tài khoản — không còn mục riêng trên thanh bên.
  test('lối vào cố định "Hướng dẫn thiết lập": Cài đặt + menu tài khoản', async ({ page }) => {
    await page.goto('/system');
    const nav = page.getByRole('navigation', { name: 'Danh mục màn hình' });
    await expect(nav.getByRole('link', { name: /Hướng dẫn thiết lập/ })).toHaveCount(0);
    await page.locator('.screen').getByRole('link', { name: /Hướng dẫn thiết lập/ }).click();
    await expect(page).toHaveURL(/\/guide$/);
    await page.locator('.sb-account').click();
    await expect(page.getByRole('menuitem', { name: /Hướng dẫn thiết lập/ })).toBeVisible();
  });
});
