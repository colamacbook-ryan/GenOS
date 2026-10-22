import { expect, test, type Page } from '@playwright/test';
import { OWNER, SETUP_TOKEN, apiCall, loginAs, resetMock } from './support';

const PIN_DIALOG = 'Mã PIN xác nhận thao tác';

/** v0.1.35 (F-20): thêm nhà cung cấp AI cần PIN `ai.route_change` — hộp PIN BẮT BUỘC hiện ở lần đầu. */
async function enterOwnerPin(page: Page): Promise<void> {
  const dlg = page.getByRole('dialog', { name: PIN_DIALOG });
  await expect(dlg).toBeVisible();
  await page.keyboard.type(OWNER.pin);
  await expect(dlg).toBeHidden();
}

/**
 * v0.1.28 — hành trình đã đổi sau rà soát UX (mock, tất định):
 * - Bước 4: nguồn gọi thử lỗi báo câu dễ hiểu + chi tiết kỹ thuật thu gọn, xếp CUỐI chuỗi, xoá được; nguồn OK có
 *   "Dùng model này" còn nguyên sau khi tải lại; Tiếp tục chỉ bật khi có model.
 * - "Để sau" ở bước 7/11 dùng mặc định (v0.1.59: ghi khi Hoàn tất); bước Xong nói thật nguồn AI "Đã chạy" / "Làm sau".
 * - Vai trò Vận hành: Trợ giúp không có lệnh máy chủ/Gen, Hướng dẫn thiết lập giải thích thay vì báo lỗi.
 */
test.describe('UX v0.1.28', () => {
  test('bước 4 → 12: nguồn lỗi, model sau tải lại, xoá nguồn, việc còn thiếu', async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'fresh');
    await apiCall(page, 'PUT', '/setup/steps/1', { token: SETUP_TOKEN, language: 'vi', mode: 'empty' });
    await apiCall(page, 'PUT', '/setup/steps/2', {
      token: SETUP_TOKEN, display_name: 'Anh Cơ', email: 'owner@example.test', password: 'mot-cau-rat-dai-de-nho-2026', pin: OWNER.pin, pin_confirm: OWNER.pin,
    });
    await apiCall(page, 'PUT', '/setup/steps/3', { org_name: 'Genesis Trading', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND', self_name: 'Anh', bot_calls_me: 'Sếp' });

    // v0.1.59: form Bộ não AI đầy đủ (xếp thứ tự, "Dùng model này", xoá nguồn) vẫn ở /guide/4; /setup chỉ còn màn Nguồn AI gọn.
    await page.goto('/guide/4');
    await expect(page.getByRole('heading', { name: 'Bộ não AI' })).toBeVisible();
    const next = page.getByRole('button', { name: /Tiếp tục/ });
    await expect(next).toBeDisabled();
    await expect(page.getByText('Cần ít nhất một nguồn gọi thử thành công để tiếp tục.')).toBeVisible();

    // Khoá quá ngắn → nói lý do ngay dưới ô.
    await page.getByLabel('Khoá API').fill('abc');
    await expect(page.getByText(/Khoá API có vẻ quá ngắn/)).toBeVisible();

    // Nguồn lỗi (địa chỉ nội bộ không gọi được).
    await page.getByLabel('Loại').selectOption('openai_compat');
    await page.getByLabel('Tên hiển thị').fill('Model sai');
    await page.getByLabel('Địa chỉ gọi (Endpoint)').fill('http://127.0.0.1:9999/v1');
    await page.getByLabel('Khoá API').fill('sk-sai-12345678');
    await page.getByRole('button', { name: 'Thêm & kiểm tra' }).click();
    await enterOwnerPin(page);
    const bad = page.locator('.prov-row', { hasText: 'Model sai' });
    await expect(bad.getByText(/Không gọi được địa chỉ này/)).toBeVisible();
    await expect(bad.getByText('All connection attempts failed')).toBeHidden(); // chi tiết thô thu gọn
    await expect(bad.getByText('Lỗi kết nối')).toBeVisible();
    await expect(next).toBeDisabled();

    // Nguồn tốt → thêm sau nhưng đứng TRƯỚC nguồn lỗi; có model tự chọn nên Tiếp tục bật.
    await page.getByLabel('Loại').selectOption('gemini');
    await page.getByLabel('Khoá API').fill('AIza-good-key-1234');
    await page.getByRole('button', { name: 'Thêm & kiểm tra' }).click();
    // Phiên PIN còn hạn từ lần thêm trên → máy chủ không hỏi lại.
    const good = page.locator('.prov-row', { hasText: 'Gemini API' });
    await expect(good.getByRole('button', { name: 'Dùng model này' })).toBeVisible();
    await expect(page.locator('.prov-row').first()).toContainText('Gemini API');
    await expect(page.locator('.prov-row').nth(1)).toContainText('Model sai');
    await expect(next).toBeEnabled();

    // Tải lại: kết quả gọi thử + nút chọn model vẫn còn (lưu ở máy chủ).
    await page.reload();
    await expect(page.locator('.prov-row', { hasText: 'Gemini API' }).getByRole('button', { name: 'Dùng model này' })).toBeVisible();
    // v0.1.66 (model hiển thị = model chạy): nguồn mới thêm chưa lưu model nào ⇒ nói thẳng, không hứa "hệ thống dùng model X".
    await expect(page.getByText(/Chưa lưu model nào — chọn rồi bấm "Dùng model này"/).first()).toBeVisible();

    // Xoá nguồn lỗi.
    page.once('dialog', (d) => void d.accept());
    await page.getByRole('button', { name: 'Xoá Model sai' }).click();
    await expect(page.locator('.prov-row', { hasText: 'Model sai' })).toHaveCount(0);
    await next.click();
    // Lưu xong quay về Mặt tiền (bước 4 đã xong ở máy chủ).
    await expect(page).toHaveURL(/\/owner$/);

    // Thiết lập một màn: bước 4 xong ⇒ /setup tới thẳng bước Xong; "Vào Mặt tiền" ghi Hoàn tất, bước 5–11 chưa làm ⇒ "để sau" (mặc định).
    await page.goto('/setup');
    await expect(page.getByRole('heading', { name: 'Xong rồi, thưa Sếp' })).toBeVisible();
    await expect(page.getByTestId('setup-done-ai')).toContainText('Đã chạy');
    await expect(page.getByText('Mọi thứ đã sẵn sàng')).toHaveCount(0);
    await page.getByRole('button', { name: /Vào Mặt tiền/ }).click();
    await expect(page).toHaveURL(/\/owner$/);
    const state = (await apiCall(page, 'GET', '/setup/state')) as { finished: boolean; steps: Array<{ n: number; status: string }> };
    expect(state.finished).toBe(true);
    expect(state.steps.filter((x) => x.status === 'skipped').map((x) => x.n)).toEqual([5, 6, 7, 8, 9, 10, 11]);
  });

  test('Vận hành: Trợ giúp theo vai trò, Hướng dẫn thiết lập không báo lỗi', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAs(page, 'operator@genesis.local');
    await page.goto('/help');
    await expect(page.getByText('Cần giúp về tài khoản')).toBeVisible();
    await expect(page.getByText('genh update')).toHaveCount(0);
    await expect(page.getByRole('link', { name: /Mở Hướng dẫn thiết lập/ })).toHaveCount(0);
    await page.goto('/guide');
    await expect(page.getByRole('heading', { level: 2, name: 'Hướng dẫn thiết lập' })).toBeVisible();
    await expect(page.getByText('Việc kết nối do Owner làm')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Thử lại' })).toHaveCount(0);
    // v0.1.39: trang "Việc Sếp cần làm" cũng chỉ giải thích, không báo lỗi.
    await page.goto('/guide/viec-sep');
    await expect(page.getByText('Việc kết nối do Owner làm')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Thử lại' })).toHaveCount(0);
  });
});
