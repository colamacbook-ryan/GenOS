/**
 * v0.1.55 (G3) — chọn model / mức suy nghĩ trong khung chat Gen (mock, tất định):
 * mở Gen → "Tự động (chuẩn) · Nhanh · Kỹ hơn" → chọn "Kỹ hơn" (+ "Cao") → gửi câu ⇒ `POST /gen/turns` mang
 * `model_choice.tier = 'deep'`; mở hội thoại mới ⇒ về Tự động (BỎ `model_choice`); mở lại hội thoại cũ ⇒ thấy lại lựa chọn;
 * tầng bị khoá có tooltip chữ; 422 hiện chuỗi thân thiện + "Chi tiết kỹ thuật"; màn 390px không tràn.
 */
import { expect, test, type Page } from '@playwright/test';
import { loginAsOwner, openGen, p3Hook, resetMock } from './support';

const GROUP = 'Chế độ trả lời của Gen';

const panel = (page: Page) => page.getByRole('complementary', { name: /Gen — trợ lý quản trị/ });
const tier = (page: Page, name: string) => panel(page).getByRole('group', { name: GROUP }).getByRole('button', { name, exact: true });
const effort = (page: Page, name: string) => panel(page).getByRole('group', { name: 'Mức suy nghĩ' }).getByRole('button', { name, exact: true });
const isTurnPost = (r: { url(): string; method(): string }) => r.url().endsWith('/api/v1/gen/turns') && r.method() === 'POST';

/** Gửi một câu và chờ câu trả lời xong (hiện nút "Hữu ích"). Trả về thân request đã gửi. */
async function ask(page: Page, q: string): Promise<Record<string, unknown>> {
  await panel(page).getByLabel('Câu hỏi cho Gen').fill(q);
  const sent = page.waitForRequest(isTurnPost);
  await panel(page).getByRole('button', { name: 'Gửi', exact: true }).click();
  const body = (await sent).postDataJSON() as Record<string, unknown>;
  await expect(panel(page).getByRole('button', { name: 'Hữu ích', exact: true }).last()).toBeVisible({ timeout: 15_000 });
  return body;
}

async function noObjectText(page: Page) {
  await expect(page.locator('body')).not.toContainText('[object Object]');
}

test.describe('v0.1.55 — Gen: chọn model / mức suy nghĩ', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 1440, height: 900 });
    await loginAsOwner(page);
    await page.goto('/overview');
  });

  test('chọn "Kỹ hơn" + "Cao" rồi gửi ⇒ POST /gen/turns có model_choice.tier = deep; hội thoại mới ⇒ về Tự động', async ({ page }) => {
    await openGen(page);
    // Ba lựa chọn, mặc định Tự động (chuẩn); Cân bằng ẩn; chưa có hàng Mức suy nghĩ.
    const group = panel(page).getByRole('group', { name: GROUP });
    await expect(group.getByRole('button')).toHaveText(['Tự động (chuẩn)', 'Nhanh', 'Kỹ hơn']);
    await expect(tier(page, 'Tự động (chuẩn)')).toHaveAttribute('aria-pressed', 'true');
    await expect(panel(page).getByRole('group', { name: 'Mức suy nghĩ' })).toHaveCount(0);

    await tier(page, 'Kỹ hơn').click();
    await expect(tier(page, 'Kỹ hơn')).toHaveAttribute('aria-pressed', 'true');
    await expect(panel(page).getByRole('group', { name: 'Mức suy nghĩ' })).toContainText('Mức suy nghĩ:');
    await expect(panel(page).getByRole('group', { name: 'Mức suy nghĩ' }).getByRole('button')).toHaveText(['Thấp', 'Vừa', 'Cao']);
    await effort(page, 'Cao').click();
    await expect(effort(page, 'Cao')).toHaveAttribute('aria-pressed', 'true');

    const first = await ask(page, 'Chào Gen, giới thiệu đi');
    expect(first.model_choice).toEqual({ tier: 'deep', effort: 'high' });
    expect(first.conversation_id).toBeNull();
    // Máy chủ giả cũng ghi lại đúng thân đã nhận (hook lastTurnBody).
    const rec = (await p3Hook(page.request, 'gen', 'lastTurnBody')) as { model_choice: unknown };
    expect(rec.model_choice).toEqual({ tier: 'deep', effort: 'high' });

    // Hội thoại mới ⇒ Tự động; câu hỏi tiếp theo BỎ model_choice.
    await panel(page).getByRole('button', { name: 'Hội thoại mới' }).click();
    await expect(tier(page, 'Tự động (chuẩn)')).toHaveAttribute('aria-pressed', 'true');
    await expect(tier(page, 'Kỹ hơn')).toHaveAttribute('aria-pressed', 'false');
    await expect(panel(page).getByRole('group', { name: 'Mức suy nghĩ' })).toHaveCount(0);
    const second = await ask(page, 'Gen ơi cho hỏi thử');
    expect(second).not.toHaveProperty('model_choice');
    expect(((await p3Hook(page.request, 'gen', 'lastTurnBody')) as { model_choice: unknown }).model_choice).toBeNull();
    await noObjectText(page);
  });

  test('nhớ theo hội thoại: mở lại hội thoại cũ thấy lại lựa chọn; tải lại trang vẫn nhớ; hội thoại khác không bị rò', async ({ page }) => {
    await openGen(page);
    await tier(page, 'Kỹ hơn').click();
    await effort(page, 'Vừa').click();
    await ask(page, 'Chào Gen, giới thiệu đi');
    await panel(page).getByRole('button', { name: 'Hội thoại mới' }).click();
    await tier(page, 'Nhanh').click();
    await ask(page, 'Gen ơi cho hỏi thử');
    await panel(page).getByRole('button', { name: 'Hội thoại mới' }).click();
    await expect(tier(page, 'Tự động (chuẩn)')).toHaveAttribute('aria-pressed', 'true');

    await panel(page).getByRole('button', { name: 'Hội thoại cũ' }).click();
    const list = page.getByRole('dialog', { name: 'Hội thoại cũ' });
    await list.getByRole('button', { name: /Chào Gen, giới thiệu đi/ }).click();
    await expect(panel(page)).toContainText('Chào Gen, giới thiệu đi');
    await expect(tier(page, 'Kỹ hơn')).toHaveAttribute('aria-pressed', 'true');
    await expect(effort(page, 'Vừa')).toHaveAttribute('aria-pressed', 'true');

    // Tải lại trang: lựa chọn nằm ở localStorage `gh-gen-model-choice` theo mã hội thoại.
    const saved = await page.evaluate(() => window.localStorage.getItem('gh-gen-model-choice'));
    expect(Object.values(JSON.parse(saved ?? '{}'))).toEqual(expect.arrayContaining([{ tier: 'deep', effort: 'medium' }, { tier: 'fast' }]));
    await page.reload();
    await expect(panel(page)).toContainText('Chào Gen, giới thiệu đi');
    await expect(tier(page, 'Kỹ hơn')).toHaveAttribute('aria-pressed', 'true');
    await expect(effort(page, 'Vừa')).toHaveAttribute('aria-pressed', 'true');

    // Hội thoại thứ hai nhớ riêng: "Nhanh", không có hàng Mức suy nghĩ.
    await panel(page).getByRole('button', { name: 'Hội thoại cũ' }).click();
    await page.getByRole('dialog', { name: 'Hội thoại cũ' }).getByRole('button', { name: /Gen ơi cho hỏi thử/ }).click();
    await expect(panel(page)).toContainText('Gen ơi cho hỏi thử');
    await expect(tier(page, 'Nhanh')).toHaveAttribute('aria-pressed', 'true');
    await expect(tier(page, 'Kỹ hơn')).toHaveAttribute('aria-pressed', 'false');
    await expect(panel(page).getByRole('group', { name: 'Mức suy nghĩ' })).toHaveCount(0);
    await noObjectText(page);
  });

  test('tầng không dùng được ⇒ nút khoá kèm tooltip chữ; máy chủ hạ về Tự động ⇒ hiện một dòng giải thích', async ({ page }) => {
    await p3Hook(page.request, 'gen', 'modelOptions', { deep: false });
    await page.reload();
    await openGen(page);
    await expect(tier(page, 'Kỹ hơn')).toHaveAttribute('aria-disabled', 'true');
    await expect(tier(page, 'Kỹ hơn')).toHaveAttribute('title', /Mức “Kỹ hơn” chưa dùng được/);
    await expect(tier(page, 'Nhanh')).toBeEnabled();
    await p3Hook(page.request, 'gen', 'modelOptions', {});
  });

  test('điện thoại 390px: bấm tầng không dùng được hiện câu giải thích, không tràn ngang', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 780 });
    await p3Hook(page.request, 'gen', 'modelOptions', { deep: false });
    await page.reload();
    await openGen(page);
    await expect(page.getByTestId('gen-model-reason')).toHaveCount(0);
    await tier(page, 'Kỹ hơn').click({ force: true }); // nút mờ (aria-disabled) vẫn bấm được trên màn cảm ứng
    const reason = page.getByTestId('gen-model-reason');
    await expect(reason).toBeVisible();
    await expect(reason).toContainText('Mức “Kỹ hơn” chưa dùng được');
    await expect(tier(page, 'Kỹ hơn')).toHaveAttribute('aria-pressed', 'false');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await noObjectText(page);
    await p3Hook(page.request, 'gen', 'modelOptions', {});
  });

  test('máy chủ hạ lựa chọn về Tự động ⇒ bước notice hiện thành một dòng chữ', async ({ page }) => {
    await openGen(page);
    await tier(page, 'Kỹ hơn').click();
    // Giữa lúc Sếp đã chọn, nguồn của tầng này biến mất (vd đổi nguồn model) — máy chủ giả báo "Kỹ hơn" không dùng được.
    await p3Hook(page.request, 'gen', 'modelOptions', { deep: false });
    const body = await ask(page, 'Chào Gen, giới thiệu đi');
    expect(body.model_choice).toEqual({ tier: 'deep' });
    await expect(panel(page).getByRole('note')).toContainText('Em dùng chế độ Tự động vì mức “Kỹ hơn” chưa có nguồn AI nào phục vụ.');
    await expect(panel(page)).toContainText('Dạ, em là Gen');
    await noObjectText(page);
  });

  test('422 MODEL_CHOICE_INVALID ⇒ câu thân thiện + "Chi tiết kỹ thuật", lựa chọn về Tự động', async ({ page }) => {
    // Máy chủ giả kiểm tập giá trị như máy chủ thật: tier lạ ⇒ 422 problem+json.
    const bad = await page.request.post('/api/v1/gen/turns', {
      data: { text: 'x', model_choice: { tier: 'strong' } },
      headers: { 'X-CSRF-Token': (await page.request.storageState()).cookies.find((c) => c.name === 'gh_csrf')?.value ?? '' },
    });
    expect(bad.status()).toBe(422);
    expect(bad.headers()['content-type']).toContain('application/problem+json');
    const problem = (await bad.json()) as { code: string; title: string; detail: unknown };
    expect(problem.code).toBe('MODEL_CHOICE_INVALID');
    expect(problem.title).toBe('Lựa chọn model không hợp lệ — em dùng chế độ Tự động nhé');
    expect(typeof problem.detail).toBe('string');

    // Trên màn: ép máy chủ trả 422 cho lượt gửi kế tiếp.
    await openGen(page);
    await tier(page, 'Kỹ hơn').click();
    await page.route('**/api/v1/gen/turns', (route) =>
      route.request().method() === 'POST'
        ? route.fulfill({
            status: 422,
            contentType: 'application/problem+json',
            body: JSON.stringify({ ...problem, status: 422, type: 'https://gen-harness.local/errors/model_choice_invalid' }),
          })
        : route.fallback(),
    );
    await panel(page).getByLabel('Câu hỏi cho Gen').fill('Gửi lựa chọn hỏng');
    await panel(page).getByRole('button', { name: 'Gửi', exact: true }).click();
    await expect(panel(page)).toContainText('Lựa chọn model không hợp lệ — em dùng chế độ Tự động nhé');
    await panel(page).locator('details.tech-detail summary').click();
    await expect(panel(page).locator('details.tech-detail code')).toContainText('MODEL_CHOICE_INVALID');
    await expect(tier(page, 'Tự động (chuẩn)')).toHaveAttribute('aria-pressed', 'true');
    await noObjectText(page);
  });

  test('màn điện thoại 390px: thẻ chọn model không tràn ngang', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/overview');
    await openGen(page);
    await tier(page, 'Kỹ hơn').click();
    await expect(panel(page).getByRole('group', { name: 'Mức suy nghĩ' })).toBeVisible();
    const box = await panel(page).locator('.gen-model').boundingBox();
    expect(box).not.toBeNull();
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(390);
    for (const loc of [tier(page, 'Tự động (chuẩn)'), tier(page, 'Nhanh'), tier(page, 'Kỹ hơn'), effort(page, 'Thấp'), effort(page, 'Vừa'), effort(page, 'Cao')]) {
      const b = await loc.boundingBox();
      expect(b).not.toBeNull();
      expect((b?.x ?? 0) + (b?.width ?? 0)).toBeLessThanOrEqual(390);
      expect(b?.x ?? -1).toBeGreaterThanOrEqual(0);
    }
    const scrollW = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(scrollW).toBeLessThanOrEqual(390);
  });
});
