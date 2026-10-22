/**
 * v0.1.41 (F-8a, F-8, F-86) — khung Gen giữ hội thoại qua tải lại trang, "Hội thoại cũ", Bản tin Gen mở từ chuông,
 * nút Hữu ích nhớ trạng thái (mock: `npm run dev:mock`).
 */
import { expect, test, type Page } from '@playwright/test';
import { apiCall, loginAsOwner, resetMock } from './support';

function panel(page: Page) {
  return page.getByRole('complementary', { name: /Gen — trợ lý quản trị/ });
}

async function openGen(page: Page) {
  if (!(await panel(page).isVisible())) await page.getByRole('button', { name: /Hỏi Gen/ }).click();
  await expect(panel(page)).toBeVisible();
}

async function ask(page: Page, q: string) {
  await panel(page).getByLabel('Câu hỏi cho Gen').fill(q);
  await panel(page).getByRole('button', { name: 'Gửi' }).click();
  // Câu trả lời xong ⇒ hiện nút đánh giá.
  await expect(panel(page).getByRole('button', { name: 'Hữu ích', exact: true }).last()).toBeVisible({ timeout: 15_000 });
}

async function noObjectText(page: Page) {
  await expect(page.locator('body')).not.toContainText('[object Object]');
}

test.describe('v0.1.41 — Gen giữ hội thoại, Hội thoại cũ, Bản tin Gen', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  test('hỏi ⇒ tải lại trang vẫn thấy hội thoại; Hội thoại cũ mở đúng hội thoại khác', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/overview');
    await openGen(page);
    await ask(page, 'Chào Gen, giới thiệu đi');
    await expect(panel(page)).toContainText('Chào Gen, giới thiệu đi');
    await expect(panel(page)).toContainText('Dạ, em là Gen');

    // F-84/F-86: "Hữu ích" trên câu trả lời thường — lưu máy chủ (PUT), tải lại vẫn chọn, bấm lại ⇒ bỏ (DELETE).
    const good = () => panel(page).locator('.gen-msg--gen').last().getByRole('button', { name: 'Hữu ích', exact: true });
    await expect(good()).toHaveAttribute('aria-pressed', 'false');
    const put = page.waitForResponse((r) => r.url().endsWith('/api/v1/gen/feedback') && r.request().method() === 'PUT');
    await good().click();
    expect((await put).status()).toBe(200);
    await expect(good()).toHaveAttribute('aria-pressed', 'true');

    await page.reload();
    await expect(panel(page)).toBeVisible();
    await expect(panel(page)).toContainText('Chào Gen, giới thiệu đi');
    await expect(panel(page)).toContainText('Dạ, em là Gen');
    await expect(good()).toHaveAttribute('aria-pressed', 'true');
    const del = page.waitForResponse((r) => /\/api\/v1\/gen\/feedback\/[^/]+$/.test(r.url()) && r.request().method() === 'DELETE');
    await good().click();
    expect((await del).status()).toBe(204);
    await expect(good()).toHaveAttribute('aria-pressed', 'false');
    await page.reload();
    await expect(panel(page)).toContainText('Dạ, em là Gen');
    await expect(good()).toHaveAttribute('aria-pressed', 'false');

    // Hội thoại thứ hai.
    await panel(page).getByRole('button', { name: 'Hội thoại mới' }).click();
    await ask(page, 'Gen ơi cho hỏi thử');
    await expect(panel(page)).not.toContainText('Chào Gen, giới thiệu đi');

    await panel(page).getByRole('button', { name: 'Hội thoại cũ' }).click();
    const list = page.getByRole('dialog', { name: 'Hội thoại cũ' });
    await expect(list.getByRole('button')).toHaveCount(2);
    await list.getByRole('button', { name: /Chào Gen, giới thiệu đi/ }).click();
    await expect(list).toHaveCount(0);
    await expect(panel(page)).toContainText('Chào Gen, giới thiệu đi');
    await expect(panel(page)).not.toContainText('Gen ơi cho hỏi thử');
    await noObjectText(page);
  });

  test('bản tin: chuông "Bản tin Gen sáng …" ⇒ Gen mở bản tin ⇒ Hữu ích ⇒ tải lại vẫn giữ', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/overview');
    await expect(page.getByRole('button', { name: /^Thông báo/ })).toBeVisible();
    const seeded = (await apiCall(page, 'POST', '/gen/__mock/briefing', { slot: 'sang', needs_api_key: true })) as { conversation_id: string };
    expect(seeded.conversation_id).toMatch(/^[0-9a-f-]{36}$/);

    await page.getByRole('button', { name: /^Thông báo/ }).click();
    const dlg = page.getByRole('dialog', { name: 'Thông báo' });
    const item = dlg.locator('.nt-item', { hasText: /Bản tin Gen sáng \d{2}\/\d{2}/ });
    await expect(item).toBeVisible();
    await item.click();

    await expect(page).toHaveURL(/\/overview$/);
    await expect(panel(page)).toBeVisible();
    const msg = panel(page).locator('.gen-msg--briefing');
    await expect(msg).toContainText('Bản tin');
    await expect(msg).toContainText('Đã tra việc, khách, nháp, sự cố…');
    await expect(msg).toContainText('Việc tới hạn hôm nay (2)');
    await expect(msg).toContainText('Khách đang nóng (1)');
    await expect(msg).toContainText('Nháp chờ duyệt (1)');
    await expect(msg.getByText('Dán khoá OpenRouter/Gemini để Gen tóm tắt')).toHaveCount(1);
    await expect(msg.getByRole('button', { name: 'Mở nơi dán khoá' })).toBeVisible();

    const good = msg.getByRole('button', { name: 'Hữu ích', exact: true });
    await expect(good).toHaveAttribute('aria-pressed', 'false');
    const saved = page.waitForResponse((r) => r.url().endsWith('/api/v1/gen/feedback') && r.request().method() === 'PUT');
    await good.click();
    expect((await saved).status()).toBe(200);
    await expect(good).toHaveAttribute('aria-pressed', 'true');

    await page.reload();
    const again = panel(page).locator('.gen-msg--briefing');
    await expect(again).toContainText('Đã tra việc, khách, nháp, sự cố…');
    await expect(again.getByRole('button', { name: 'Hữu ích', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(again.getByRole('button', { name: 'Không hữu ích' })).toHaveAttribute('aria-pressed', 'false');

    // Hội thoại cũ có nhãn Bản tin.
    await panel(page).getByRole('button', { name: 'Hội thoại cũ' }).click();
    await expect(page.getByRole('dialog', { name: 'Hội thoại cũ' }).locator('.gen-badge')).toHaveText('Bản tin');
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: 'Hội thoại cũ' })).toHaveCount(0);

    // Bản tin không có khoá API: nút gợi ý mở thẳng API & Model (nơi "Thêm nhà cung cấp").
    await panel(page).locator('.gen-msg--briefing').getByRole('button', { name: 'Mở nơi dán khoá' }).click();
    await expect(page).toHaveURL(/\/api$/);
    await noObjectText(page);
  });

  test('hội thoại đã bị xoá (404) ⇒ khung Gen trống, không báo lỗi đỏ', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/overview');
    await openGen(page);
    await ask(page, 'Câu sẽ bị xoá');
    const first = (await apiCall(page, 'GET', '/gen/conversations')) as Array<{ id: string; title: string }>;
    const gone = first.find((c) => c.title.startsWith('Câu sẽ bị xoá'));
    expect(gone).toBeTruthy();
    // Hội thoại bị xoá (quá hạn lưu / máy khác xoá) ⇒ máy chủ trả 404 cho tin nhắn và không còn trong danh sách.
    await page.route(`**/api/v1/gen/conversations/${gone!.id}/messages`, (route) =>
      route.fulfill({ status: 404, contentType: 'application/problem+json', body: JSON.stringify({ status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' }) }),
    );
    await page.reload();
    await expect(panel(page)).toBeVisible();
    await expect(panel(page).locator('.gen-empty')).toBeVisible();
    await expect(panel(page)).not.toContainText('Câu sẽ bị xoá');
    await expect(panel(page)).not.toContainText('Chưa tải lại được');
    await expect(panel(page).getByRole('alert')).toHaveCount(0);
    await expect(page.locator('.toast[data-tone="bad"]')).toHaveCount(0);

    // Chọn hội thoại đó từ "Hội thoại cũ" (danh sách cũ còn ghi) ⇒ chỉ nhắc nhẹ, khung vẫn trống, không lỗi đỏ.
    await panel(page).getByRole('button', { name: 'Hội thoại cũ' }).click();
    const list = page.getByRole('dialog', { name: 'Hội thoại cũ' });
    await list.getByRole('button', { name: /Câu sẽ bị xoá/ }).click();
    await expect(page.locator('.toast[data-tone="warn"]')).toContainText('Hội thoại này không còn');
    await expect(page.locator('.toast[data-tone="bad"]')).toHaveCount(0);
    await expect(panel(page).locator('.gen-empty')).toBeVisible();
    await expect(panel(page).getByRole('alert')).toHaveCount(0);
    await noObjectText(page);
  });
});
