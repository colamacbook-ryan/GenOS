import { expect, test, type Page } from '@playwright/test';
import { MANAGER, OWNER, SETUP_TOKEN, apiCall, expectOwnerFront, loginAs, loginAsOwner, resetMock } from './support';

/**
 * v0.1.29 — mock, tất định (không có Facebook thật, không có trình duyệt từ xa thật):
 * - Tài khoản mạng xã hội: chỉ Owner; thêm tài khoản phải tích đủ 2 ô chấp nhận rủi ro + PIN; cửa sổ đăng nhập từ xa
 *   nhận khung hình, gửi phím, "Tôi đã đăng nhập xong" → Đang kết nối; Đọc ngay → mục đọc được (có mục đáng ngờ);
 *   Dừng tất cả → dải đỏ, Bật lại (PIN); Gỡ tài khoản.
 * - Nguồn AI "Làm sau" (v0.1.59: link nhỏ ở màn Nguồn AI) → Hoàn tất được → Tổng quan hiện "Chưa có model" → Chọn model mở /setup?buoc=nguon-ai.
 */

async function enterPinIfAsked(page: Page) {
  const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
  await expect(dlg).toBeVisible();
  await page.getByLabel('Mã PIN — chữ số 1/6').click();
  await page.keyboard.type(OWNER.pin);
  await expect(dlg).toBeHidden();
}

test.describe('Tài khoản mạng xã hội (v0.1.29)', () => {
  test('Owner: thêm (rủi ro + PIN) → đăng nhập từ xa → đọc → dừng tất cả → bật lại → gỡ', async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
    await page.goto('/overview');
    // Lối vào: menu tài khoản ở chân thanh bên.
    await page.getByRole('button', { name: /Anh Nguyễn Văn A|Tài khoản/ }).last().click();
    await page.getByRole('menuitem', { name: 'Tài khoản mạng xã hội' }).click();
    await expect(page).toHaveURL(/\/social$/);
    await expect(page.getByRole('heading', { level: 2, name: 'Tài khoản mạng xã hội' })).toBeVisible();
    await expect(page.getByTestId('social-hard-rules')).toContainText('Không tạo tài khoản giả');
    await expect(page.getByTestId('social-hard-rules')).toContainText('không giải CAPTCHA');

    // Thêm tài khoản: hộp rủi ro, phải tích cả 2 ô.
    await page.getByRole('button', { name: 'Thêm tài khoản' }).click();
    await page.getByLabel('Tên để nhận ra').fill('Facebook của Sếp');
    await page.getByRole('button', { name: /^Tiếp/ }).click();
    const risk = page.getByTestId('social-risk-dialog');
    await expect(risk).toContainText('Điều khoản của Meta');
    const submit = page.getByRole('button', { name: /Tôi chấp nhận, thêm tài khoản/ });
    await expect(submit).toBeDisabled();
    await page.getByLabel(/Tôi hiểu và chấp nhận rủi ro/).check();
    await expect(submit).toBeDisabled();
    await page.getByLabel(/tài khoản thật của chính tôi/).check();
    await submit.click();
    await enterPinIfAsked(page);
    const row = page.locator('[data-testid^="social-account-"]');
    await expect(row).toContainText('Facebook của Sếp');
    await expect(row).toContainText('Chưa đăng nhập');

    // Đăng nhập trong cửa sổ trình duyệt từ xa (PIN còn hiệu lực).
    await row.getByRole('button', { name: 'Đăng nhập' }).click();
    const viewer = page.getByRole('dialog', { name: /Đăng nhập Facebook của Sếp/ });
    await expect(viewer).toBeVisible();
    await expect(viewer.getByRole('status')).toContainText('Sếp tự đăng nhập');
    await expect(page.getByTestId('social-viewer-canvas')).toHaveAttribute('data-frames', /[1-9]/);
    await page.getByTestId('social-viewer-canvas').click();
    await page.keyboard.type('owner@example.vn');
    await viewer.getByRole('button', { name: 'Tôi đã đăng nhập xong' }).click();
    await expect(viewer).toBeHidden();
    await expect(row).toContainText('Đang kết nối');

    // Đọc ngay → mục đọc được, mục đáng ngờ được đánh dấu.
    await row.getByRole('button', { name: 'Đọc ngay' }).click();
    const items = page.getByTestId('social-items');
    await expect(items).toContainText('Shop Mai');
    await expect(items).toContainText('đáng ngờ');

    // Dừng tất cả (không cần PIN) → dải đỏ; Bật lại cần PIN (đã có phiên PIN).
    await page.getByRole('button', { name: 'Dừng tất cả' }).click();
    await page.getByRole('button', { name: 'Dừng ngay' }).click();
    await expect(page.getByTestId('social-halted')).toBeVisible();
    await expect(row.getByRole('button', { name: 'Đọc ngay' })).toBeDisabled();
    await page.getByRole('button', { name: /Bật lại/ }).click();
    await expect(page.getByTestId('social-halted')).toHaveCount(0);

    // Gỡ tài khoản.
    await row.getByRole('button', { name: 'Gỡ tài khoản' }).click();
    await page.getByRole('dialog', { name: /Gỡ Facebook của Sếp/ }).getByRole('button', { name: 'Gỡ tài khoản' }).click();
    await expect(page.getByText('Chưa có tài khoản nào')).toBeVisible();
  });

  test('vai trò khác Owner: không có lối vào, mở thẳng thì báo chỉ Owner', async ({ page }) => {
    await resetMock(page.request, 'finished');
    await loginAs(page, MANAGER.email);
    await page.goto('/social');
    await expect(page.getByText('Chỉ Owner dùng được')).toBeVisible();
  });
});

test.describe('Bước Nguồn AI "Làm sau" (v0.1.29, v0.1.59)', () => {
  test('Làm sau → Hoàn tất → Tổng quan "Chưa có model" → Chọn model mở /setup?buoc=nguon-ai', async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'fresh');
    await apiCall(page, 'PUT', '/setup/steps/1', { token: SETUP_TOKEN, language: 'vi', mode: 'empty' });
    await apiCall(page, 'PUT', '/setup/steps/2', {
      token: SETUP_TOKEN, display_name: 'Anh Cơ', email: 'owner@example.test', password: 'mot-cau-rat-dai-de-nho-2026', pin: OWNER.pin, pin_confirm: OWNER.pin,
    });
    await apiCall(page, 'PUT', '/setup/steps/3', { org_name: 'Genesis Trading', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND', self_name: 'Anh', bot_calls_me: 'Sếp' });
    await page.goto('/setup');
    await expect(page.getByRole('heading', { name: 'Nối nguồn AI cho Gen' })).toBeVisible();
    // v0.1.59: "Làm sau" là link nhỏ kèm câu nói rõ hậu quả (không còn hộp cảnh báo).
    await expect(page.getByText('Gen sẽ chưa tóm tắt được bản tin.')).toBeVisible();
    await page.getByRole('button', { name: 'Làm sau' }).click();
    await expect(page.getByRole('heading', { name: 'Xong rồi, thưa Sếp' })).toBeVisible();
    await expect(page.getByTestId('setup-done-ai')).toContainText('Làm sau — Gen sẽ chưa tóm tắt được bản tin');
    await page.getByRole('button', { name: /Vào Mặt tiền/ }).click();
    // Owner "/" ⇒ Mặt tiền /owner (Tình báo là trang đầu); thẻ "Chưa có model" nằm ở Tổng quan (Console).
    await expectOwnerFront(page);
    await page.goto('/overview');
    const banner = page.getByTestId('no-model');
    await expect(banner).toContainText('Chưa có model');
    await banner.getByRole('link', { name: /Chọn model/ }).click();
    await expect(page).toHaveURL(/\/setup\?buoc=nguon-ai$/);
    await expect(page.getByRole('heading', { name: 'Đổi nguồn AI' })).toBeVisible();
    // Form Bộ não AI đầy đủ vẫn ở /guide/4 (nhiều màn dựa vào).
    await page.goto('/guide/4');
    await expect(page.getByRole('heading', { name: 'Bộ não AI' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Cách nhanh: Thiết lập nguồn AI 2 phút' })).toHaveAttribute('href', '/setup?buoc=nguon-ai');
  });
});
