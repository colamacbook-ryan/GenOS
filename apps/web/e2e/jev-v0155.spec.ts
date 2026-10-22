import { expect, test, type Page } from '@playwright/test';
import { MANAGER, OWNER, apiCall, enterPin, loginAs, loginAsOwner, mockHook, p3Hook, resetMock } from './support';

/**
 * v0.1.55 (G4, mock, tất định) — thẻ Jev ở Cài đặt › Bộ não AI, đúng đường Sếp đi (việc Sếp phải làm duy nhất của bản này):
 *   "Dùng khoá OpenRouter đang có" → mã PIN → thẻ chuyển sang trạng thái + tự kiểm tra → "Thử 12 câu mẫu" (bảng 12 dòng + dòng
 *   tóm tắt) → "Tin đã bỏ qua" → công tắc "Lọc trước khi trích xuất". Lỗi 409 JEV_KEY_MISSING = câu thân thiện + "Chi tiết kỹ thuật"
 *   (không bao giờ object). Nhân viên không thấy "Thử 12 câu mẫu" / dòng số đo, công tắc bị khoá.
 */

const CARD = 'Jev — quyết định nhanh cho Gen';
const card = (page: Page) => page.getByRole('region', { name: CARD });

/** Nguồn OpenRouter có sẵn (khoá đã lưu) + hết phiên PIN để bước "Bật Jev" phải hỏi mã PIN thật. */
async function seedOpenRouter(page: Page) {
  await apiCall(page, 'POST', '/auth/pin/verify', { pin: OWNER.pin });
  await apiCall(page, 'POST', '/providers', {
    kind: 'openai_compat', name: 'OpenRouter', endpoint: 'https://openrouter.ai/api/v1', keys: ['sk-or-e2e-0123456789'],
  });
  await mockHook(page.request, 'pin_expire');
}

async function noObjectText(page: Page) {
  await expect(page.getByText('[object Object]')).toHaveCount(0);
}

test.describe('v0.1.55 — thẻ Jev: Bật 1 chạm, 12 câu mẫu, Tin đã bỏ qua, bộ lọc trước', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
  });

  test('Owner: Dùng khoá OpenRouter đang có → PIN → trạng thái → Thử 12 câu mẫu → Tin đã bỏ qua → công tắc lọc trước', async ({ page }) => {
    await loginAsOwner(page);
    await seedOpenRouter(page);
    await page.goto('/system?tab=brain#jev'); // thẻ Jev nằm trong «Nâng cao» — #jev mở sẵn

    // Chưa có Jev: nút một chạm + cảnh báo QD-12 một dòng; chưa có nút "Thử 12 câu mẫu".
    await expect(card(page)).toBeVisible();
    await expect(card(page).getByTestId('jev-privacy-warning')).toContainText('Tin đã che số điện thoại/email được gửi sang OpenRouter');
    await expect(card(page).getByRole('button', { name: 'Thử 12 câu mẫu' })).toHaveCount(0);
    const enable = card(page).getByRole('button', { name: 'Dùng khoá OpenRouter đang có' });
    await expect(enable).toBeVisible();

    // Bật: cần mã PIN → nhập → thẻ chuyển sang trạng thái (khoá, trạng thái) và tự kiểm tra 1 lần.
    const post = page.waitForResponse((r) => r.url().includes('/jev/enable') && r.request().method() === 'POST' && r.ok());
    await enable.click();
    await enterPin(page);
    await post;
    await expect(card(page).getByText('Trạng thái')).toBeVisible();
    await expect(card(page).getByRole('button', { name: 'Dùng khoá OpenRouter đang có' })).toHaveCount(0);
    await expect(card(page).getByText(/Jev trả lời được/)).toBeVisible();

    // Thử 12 câu mẫu: bảng 12 dòng + dòng tóm tắt "Đúng N/12 · trung bình … ms".
    await card(page).getByRole('button', { name: 'Thử 12 câu mẫu' }).click();
    const bench = card(page).getByTestId('jev-bench');
    await expect(bench.getByText(/^Đúng \d+\/12 · trung bình \d+ ms$/)).toBeVisible();
    await expect(bench.locator('tbody tr')).toHaveCount(12);
    await expect(bench.getByRole('columnheader', { name: 'Kỳ vọng' })).toBeVisible();
    await expect(bench.getByText('Sai').first()).toBeVisible();          // mock cố ý cho 1 câu sai: bảng có cả Đúng/Sai

    // Số đo giá trị (chỉ đếm lần, không ra tiền).
    await expect(card(page).getByTestId('jev-value')).toContainText('tiết kiệm');
    await expect(card(page).getByTestId('jev-value')).not.toContainText('₫');

    // Tin đã bỏ qua: mở danh sách, thấy lý do bằng chữ.
    const skipped = card(page).getByTestId('jev-skipped');
    await skipped.getByRole('button', { name: /Tin đã bỏ qua/ }).click();
    await expect(skipped.getByText('Trùng hẳn một tin đã có')).toBeVisible();
    await expect(skipped.getByText('Rác — quy tắc và Jev cùng chấm rác')).toBeVisible();

    // Công tắc "Lọc trước khi trích xuất": tắt rồi bật lại, lưu qua PATCH triage settings.
    const sw = card(page).getByRole('switch', { name: 'Lọc trước khi trích xuất' });
    await expect(sw).toHaveAttribute('aria-checked', 'true');
    const patch = page.waitForResponse((r) => r.url().includes('/refinery/triage/settings') && r.request().method() === 'PATCH' && r.ok());
    await sw.click();
    await patch;
    await expect(sw).toHaveAttribute('aria-checked', 'false');
    await sw.click();
    await expect(sw).toHaveAttribute('aria-checked', 'true');
    await noObjectText(page);
  });

  test('thiếu khoá OpenRouter (409 JEV_KEY_MISSING) ⇒ câu thân thiện + Chi tiết kỹ thuật, không object', async ({ page }) => {
    await loginAsOwner(page);
    await seedOpenRouter(page);
    await page.goto('/system?tab=brain#jev'); // thẻ Jev nằm trong «Nâng cao» — #jev mở sẵn
    const post = page.waitForResponse((r) => r.url().includes('/jev/enable') && r.request().method() === 'POST' && r.ok());
    await card(page).getByRole('button', { name: 'Dùng khoá OpenRouter đang có' }).click();
    await enterPin(page);
    await post;
    await expect(card(page).getByRole('button', { name: 'Thử 12 câu mẫu' })).toBeVisible();

    // Giữa chừng khoá mất (vd Sếp xoá nguồn OpenRouter): máy chủ trả 409 — Sếp thấy câu dễ hiểu, kỹ thuật nằm sau "Chi tiết kỹ thuật".
    await p3Hook(page.request, 'queue', 'jevKey', { on: false });
    await card(page).getByRole('button', { name: 'Thử 12 câu mẫu' }).click();
    const alert = card(page).getByRole('alert');
    await expect(alert).toBeVisible();
    await expect(alert).toContainText('Chưa có khoá OpenRouter cho Jev');
    await expect(alert.getByText('Chi tiết kỹ thuật')).toBeVisible();
    await expect(card(page).getByTestId('jev-bench').locator('tbody tr')).toHaveCount(0);
    await noObjectText(page);
    await p3Hook(page.request, 'queue', 'jevKey', { on: true });
  });

  test('nhân viên (không phải Owner): không thấy "Thử 12 câu mẫu" / dòng số đo, công tắc lọc trước bị khoá', async ({ page }) => {
    // Owner bật Jev trước (trạng thái mock dùng chung), rồi vào bằng tài khoản Quản lý.
    await loginAsOwner(page);
    await seedOpenRouter(page);
    await page.goto('/system?tab=brain#jev'); // thẻ Jev nằm trong «Nâng cao» — #jev mở sẵn
    const post = page.waitForResponse((r) => r.url().includes('/jev/enable') && r.request().method() === 'POST' && r.ok());
    await card(page).getByRole('button', { name: 'Dùng khoá OpenRouter đang có' }).click();
    await enterPin(page);
    await post;
    await expect(card(page).getByRole('button', { name: 'Thử 12 câu mẫu' })).toBeVisible();

    // Quản lý mặc định chỉ thấy tab Nhật ký — cho xem tab Bộ não AI + sửa cài đặt (team) để kiểm đúng thẻ Jev với vai không phải Owner.
    await mockHook(page.request, 'perm', { role: 'manager', permission: 'system.read', scope: 'all' });
    await mockHook(page.request, 'perm', { role: 'manager', permission: 'system.manage', scope: 'team' });
    await mockHook(page.request, 'perm', { role: 'manager', permission: 'data.read', scope: 'all' });   // đọc cấu hình lọc tin / Tin đã bỏ qua
    await page.context().clearCookies();
    await loginAs(page, MANAGER.email);
    await page.goto('/overview'); // tải lại trang thật (chỉ đổi #hash thì SPA giữ phiên Owner cũ)
    await page.goto('/system?tab=brain#jev'); // thẻ Jev nằm trong «Nâng cao» — #jev mở sẵn
    await expect(card(page)).toBeVisible();
    await expect(card(page).getByTestId('jev-privacy-warning')).toBeVisible();
    await expect(card(page).getByRole('button', { name: 'Thử 12 câu mẫu' })).toHaveCount(0);
    await expect(card(page).getByTestId('jev-value')).toHaveCount(0);
    await expect(card(page).getByRole('switch', { name: 'Lọc trước khi trích xuất' })).toHaveAttribute('aria-disabled', 'true');
    // Tin đã bỏ qua: vai không phải Owner thấy bản đã che số điện thoại dài.
    const skipped = card(page).getByTestId('jev-skipped');
    await skipped.getByRole('button', { name: /Tin đã bỏ qua/ }).click();
    await expect(skipped.getByText('Rác — quy tắc và Jev cùng chấm rác')).toBeVisible();
    await expect(skipped).not.toContainText('0912345678');
    await noObjectText(page);
  });
});
