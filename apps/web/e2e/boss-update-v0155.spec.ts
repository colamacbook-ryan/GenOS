import { expect, test, type Page } from '@playwright/test';
import { loginAsOwner, p3Hook, resetMock } from './support';

/**
 * v0.1.55 (G2, mock, tất định):
 *  - Thẻ Cập nhật khi nút "Cập nhật ngay" bị ẩn nói RÕ lý do + việc cần làm; lệnh (nếu có) luôn đi SAU câu lý do. Lý do do máy chủ
 *    trả ở `GET /system/update` → `request_block_reason`; mock đặt qua hook `__mock/nightly` (`update` trộn vào phản hồi).
 *  - Việc Sếp cần làm: chỉ dòng "Có ít nhất 1 nguồn AI chạy được" bắt buộc (x/1), chín dòng kết nối khác "Không bắt buộc".
 *  - Gen hướng dẫn: Gen-hub chỉ là gợi ý, "Thử mở Console từ điện thoại" chỉ hiện khi đã mời nhân viên.
 */

async function nightlyHook(page: Page, data: unknown) {
  const res = await page.request.post('/api/v1/__mock/nightly', { data });
  if (res.status() >= 400) throw new Error(`mock hook nightly failed: ${res.status()}`);
}

test.describe('v0.1.55 — thẻ Cập nhật nói rõ lý do', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  test('watcher_stalled: ẩn nút Cập nhật ngay, hiện lý do + việc cần làm + lệnh genh auto-update enable (sau câu lý do)', async ({ page }) => {
    await nightlyHook(page, { update: { latest: 'v0.1.99', request_block_reason: 'watcher_stalled', can_request: false } });
    await page.goto('/system?tab=storage');
    const card = page.getByRole('region', { name: 'Cập nhật phần mềm' });
    const why = card.getByTestId('update-block-reason');
    await expect(why).toBeVisible();
    await expect(why).toHaveAttribute('data-reason', 'watcher_stalled');
    await expect(why).toContainText('Người gác cập nhật đang lỗi');
    await expect(why).toContainText('Chạy lệnh dưới đây trên máy chủ để bật lại người gác, rồi bấm Cập nhật ngay.');
    await expect(why.locator('code')).toHaveText('genh auto-update enable');
    // Không có nút bấm; lệnh chép được và đứng SAU câu lý do (không bao giờ hiện lệnh trần).
    await expect(card.getByRole('button', { name: /Cập nhật ngay/ })).toHaveCount(0);
    const reasonFirst = await why.evaluate((el) => {
      const text = el.textContent ?? '';
      return text.indexOf('Người gác cập nhật đang lỗi') >= 0 && text.indexOf('Người gác cập nhật đang lỗi') < text.indexOf('genh auto-update enable');
    });
    expect(reasonFirst).toBe(true);
    expect(await why.locator('code').evaluate((el) => getComputedStyle(el).userSelect)).not.toBe('none');
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('genh_unlinked và not_owner: câu lý do riêng; chỉ genh_unlinked có lệnh chạy một lần', async ({ page }) => {
    await nightlyHook(page, { update: { latest: 'v0.1.99', request_block_reason: 'genh_unlinked', can_request: false, linked: true } });
    await page.goto('/system?tab=storage');
    const card = page.getByRole('region', { name: 'Cập nhật phần mềm' });
    const why = card.getByTestId('update-block-reason');
    await expect(why).toContainText('Máy chủ chưa bật cập nhật bằng nút bấm');
    await expect(why).toContainText('Chạy lệnh dưới đây một lần trên máy chủ — lần sau chỉ cần bấm nút ở đây.');
    await expect(why.locator('code')).toHaveText('~/.gen-harness/bin/genh update');
    await expect(card.getByRole('button', { name: /Cập nhật ngay/ })).toHaveCount(0);

    await nightlyHook(page, { update: { request_block_reason: 'not_owner', can_request: false } });
    await page.reload();
    const again = page.getByRole('region', { name: 'Cập nhật phần mềm' }).getByTestId('update-block-reason');
    await expect(again).toContainText('Chỉ Owner cập nhật được — nhờ Owner bấm');
    await expect(again).toContainText('Nhờ Owner mở mục này và bấm Cập nhật ngay.');
    await expect(again.locator('code')).toHaveCount(0);
  });

  test('không bị chặn (request_block_reason = null): có nút Cập nhật ngay, không có khối lý do', async ({ page }) => {
    await nightlyHook(page, { update: { latest: 'v0.1.99', request_block_reason: null, can_request: true } });
    await page.goto('/system?tab=storage');
    const card = page.getByRole('region', { name: 'Cập nhật phần mềm' });
    await expect(card.getByRole('button', { name: /Cập nhật ngay/ })).toBeVisible();
    await expect(card.getByTestId('update-block-reason')).toHaveCount(0);
  });
});

test.describe('v0.1.55 — Việc Sếp cần làm chỉ 1 dòng bắt buộc', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  test('chỉ dòng "nguồn AI" bắt buộc (x/1); Kiểm tra → Đạt → Tổng quan 1/1; chín dòng kia "Không bắt buộc"', async ({ page }) => {
    await page.goto('/guide/viec-sep');
    await expect(page.locator('.boss-row')).toHaveCount(10);
    await expect(page.locator('.boss-row', { hasText: 'Không bắt buộc' })).toHaveCount(9);
    const ai = page.getByRole('region', { name: 'Có ít nhất 1 nguồn AI chạy được', exact: true });
    await expect(ai).not.toContainText('Không bắt buộc');
    await expect(page.getByText('Đã đạt 0/1 dòng bắt buộc')).toBeVisible();
    // Google không còn đòi đổi qua lại hai tài khoản.
    await expect(page.getByRole('region', { name: 'Google (Antigravity)', exact: true }).getByRole('button', { name: /Đổi sang/ })).toHaveCount(0);

    await ai.getByRole('button', { name: 'Kiểm tra', exact: true }).click();
    await expect(ai.getByTestId('boss-result')).toContainText('Đạt');
    await expect(page.getByText('Đã đạt đủ 1 dòng bắt buộc')).toBeVisible();

    // Đủ 1/1 ⇒ dòng "Đã đạt x/N việc bắt buộc" ở Tổng quan tự ẩn.
    await page.goto('/overview');
    await page.waitForLoadState('networkidle');
    await expect(page.getByTestId('boss-progress')).toHaveCount(0);
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('Gen hướng dẫn: Gen-hub chỉ là gợi ý; "Thử mở Console từ điện thoại" chỉ hiện khi đã mời nhân viên', async ({ page }) => {
    const card = () => page.getByRole('region', { name: 'Hôm nay của Sếp' });
    const titles = async () => (await card().getByTestId('coach-todo').locator('.coach-card__item-title').allTextContents()).map((t) => t.trim());
    await page.goto('/overview');
    await page.getByRole('button', { name: /Hỏi Gen|Đóng Gen/ }).click();
    await expect(card()).toBeVisible();
    expect(await titles()).toEqual(['Kiểm tra nguồn AI chạy được', 'Nối Gen-hub']);

    await p3Hook(page.request, 'genCoach', 'scenario', { staff: true });
    await page.reload();
    await expect(card()).toBeVisible();
    await expect.poll(titles).toEqual(['Kiểm tra nguồn AI chạy được', 'Nối Gen-hub', 'Thử mở Console từ điện thoại']);
  });
});
