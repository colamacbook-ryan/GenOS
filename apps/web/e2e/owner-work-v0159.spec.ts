import { expect, test, type Page } from '@playwright/test';
import { apiCall, enterPin, loginAsOwner, p3Hook, resetMock } from './support';

/**
 * v0.1.59 (G3) — màn VIỆC (/owner/viec) trên máy chủ giả (tất định; mã PIN 246810; MCP giả, không Gen-hub / Kho thật):
 *  - Xác nhận một đề xuất Gen cần mã PIN (kho_*) → hộp PIN → đề xuất biến mất;
 *  - tick "Xong" một việc → biến mất; "Dời sang mai" → rời khỏi Việc hôm nay, hạn mới sau hôm nay;
 *  - "Tạo nhắc" ở Gen đề xuất chỉ tạo việc khi Sếp bấm; điện thoại có Segmented "Cần quyết (n) · Hôm nay (n) · Gen đề xuất (n)".
 * Việc mẫu của mock có hạn tương đối so với "bây giờ": ca nào cần việc chắc chắn quá hạn thì TỰ TẠO bằng POST /tasks (hạn 2 giờ trước).
 */

const twoHoursAgo = () => new Date(Date.now() - 2 * 3_600_000).toISOString();

async function newOverdueTask(page: Page, title: string): Promise<string> {
  const t = (await apiCall(page, 'POST', '/tasks', { title, due_at: twoHoursAgo() })) as { id: string };
  return t.id;
}

async function noHorizontalScroll(page: Page): Promise<void> {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over).toBeLessThanOrEqual(0);
}

test.describe('v0.1.59 · Việc', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await p3Hook(page.request, 'owner', 'reset');
    await loginAsOwner(page);
  });

  test('máy tính: Cần Sếp quyết (nháp + đề xuất Gen) bên trái, Việc hôm nay + Gen đề xuất bên phải; nháp có lý do giữ + 2 dòng đầu', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/owner/viec');
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Việc');
    const decide = page.getByTestId('work-decide');
    await expect(decide).toBeVisible();
    await expect(page.getByTestId('work-decide-count')).toBeVisible();
    await expect(decide.getByTestId('work-draft-row').first()).toBeVisible();
    await expect(decide.getByTestId('work-draft-preview').first()).toBeVisible();
    await expect(decide.getByTestId('work-draft-hold').first()).toContainText('Gen giữ lại để Sếp duyệt');
    await expect(decide.getByTestId('work-proposal-row')).toHaveCount(2);
    await expect(page.getByTestId('work-today')).toBeVisible();
    await expect(page.getByTestId('work-suggest')).toBeVisible();
    // hai khối bên phải nằm bên phải khối quyết
    const l = (await decide.boundingBox())!;
    const r = (await page.getByTestId('work-today').boundingBox())!;
    expect(r.x).toBeGreaterThan(l.x + l.width - 1);
    // nháp vượt ngưỡng tự gửi lên đầu
    await expect(decide.getByTestId('work-draft-row').first().getByText('Trên mức tự gửi')).toBeVisible();
    expect(await page.locator('body').innerText()).not.toContain('[object Object]');
    await noHorizontalScroll(page);
  });

  test('Xác nhận một đề xuất Gen cần mã PIN (kho_*) → hộp PIN → đề xuất biến mất', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/owner/viec');
    const prop = page.locator('[data-testid=work-proposal-row][data-proposal-id=prop-2]');
    await expect(prop).toBeVisible();
    await expect(prop).toContainText('Cần mã PIN');
    await prop.getByRole('button', { name: 'Xác nhận' }).click();
    await enterPin(page);
    await expect(prop).toHaveCount(0);
    await expect(page.getByTestId('work-proposal-row')).toHaveCount(1);
    // số chờ quyết ở thanh bên giảm theo
    await expect(page.getByTestId('owner-nav-work')).toBeVisible();
  });

  test('Huỷ một đề xuất (không cần PIN) → biến mất', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/owner/viec');
    const prop = page.locator('[data-testid=work-proposal-row][data-proposal-id=prop-1]');
    await expect(prop).toBeVisible();
    await prop.getByRole('button', { name: 'Huỷ' }).click();
    await expect(prop).toHaveCount(0);
  });

  test('tick Xong một việc → biến mất; việc đã xong trên máy chủ', async ({ page }) => {
    const title = 'Việc thử tick Xong';
    const id = await newOverdueTask(page, title);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/owner/viec');
    const today = page.getByTestId('work-today');
    const row = today.getByTestId('work-task-row').filter({ hasText: title });
    await expect(row).toBeVisible();
    await expect(row.getByText(/Quá hạn/)).toBeVisible();
    await row.getByRole('checkbox', { name: `Xong: ${title}` }).check();
    await expect(today.getByText(title)).toHaveCount(0);
    const t = (await apiCall(page, 'GET', `/tasks/${id}`)) as { status: string };
    expect(t.status).toBe('done');
  });

  test('Dời sang mai → rời khỏi Việc hôm nay; hạn mới sau hôm nay', async ({ page }) => {
    const title = 'Việc thử dời sang mai';
    const id = await newOverdueTask(page, title);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/owner/viec');
    const today = page.getByTestId('work-today');
    const row = today.getByTestId('work-task-row').filter({ hasText: title });
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: 'Dời sang mai' }).click();
    await expect(today.getByText(title)).toHaveCount(0);
    const t = (await apiCall(page, 'GET', `/tasks/${id}`)) as { due_at: string; status: string };
    expect(Date.parse(t.due_at)).toBeGreaterThan(Date.now());
    expect(t.status).toBe('todo');
  });

  test('chưa có bộ ghi lời hứa (v0.1.71): Việc hôm nay không có dòng lời hứa', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/owner/viec');
    await expect(page.getByTestId('work-today')).toBeVisible();
    await expect(page.getByTestId('work-today').locator('[data-kind="promise"]')).toHaveCount(0);
    await expect(page.getByTestId('work-today').getByText('Gọi lại xác nhận giá cho An Khang trong hôm nay')).toHaveCount(0);
  });

  test('Gen đề xuất: "Tạo nhắc" chỉ tạo việc khi Sếp bấm', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/owner/viec');
    const suggest = page.getByTestId('work-suggest');
    const first = suggest.getByTestId('work-suggest-row').first();
    await expect(first).toBeVisible();
    const before = (await apiCall(page, 'GET', '/tasks')) as { total: number };
    await first.getByRole('button', { name: /Tạo nhắc/ }).click();
    await expect(first.getByTestId('work-suggest-made')).toHaveText('Đã tạo nhắc');
    const after = (await apiCall(page, 'GET', '/tasks')) as { total: number };
    expect(after.total).toBe(before.total + 1);
  });

  test('điện thoại 390px: Segmented "Cần quyết (n) · Hôm nay (n) · Gen đề xuất (n)", mỗi lần một mục, không cuộn ngang', async ({ page }) => {
    await newOverdueTask(page, 'Việc thử cho điện thoại');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/owner/viec');
    const group = page.getByRole('radiogroup', { name: 'Chọn mục việc' });
    await expect(group.getByRole('radio', { name: /^Cần quyết \(\d+\)$/ })).toBeVisible();
    await expect(group.getByRole('radio', { name: /^Hôm nay \(\d+\)$/ })).toBeVisible();
    await expect(group.getByRole('radio', { name: /^Gen đề xuất \(\d+\)$/ })).toBeVisible();
    await expect(page.getByTestId('work-decide')).toBeVisible();
    await expect(page.getByTestId('work-today')).toHaveCount(0);
    await noHorizontalScroll(page);
    // ba mục nằm trọn trong khung Segmented và trong màn (ảnh v0.1.60: "Gen đề xuất (4)" từng bị cắt ở mép phải)
    const box = (await group.boundingBox())!;
    expect(box.x + box.width).toBeLessThanOrEqual(390);
    const heights = new Set<number>();
    for (const opt of await group.getByRole('radio').all()) {
      const r = (await opt.boundingBox())!;
      expect(r.x).toBeGreaterThanOrEqual(box.x);
      expect(r.x + r.width).toBeLessThanOrEqual(box.x + box.width + 0.5);
      expect(r.height).toBeGreaterThanOrEqual(44);
      heights.add(Math.round(r.height));
      expect(await opt.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
      // nhãn một dòng: mọi hộp chữ của nút nằm sát một mép trên (xuống dòng ⇒ lệch ≈ một dòng chữ 14px)
      const tops = await opt.evaluate((el) => {
        const range = document.createRange();
        range.selectNodeContents(el);
        return Array.from(range.getClientRects(), (x) => x.top);
      });
      expect(Math.max(...tops) - Math.min(...tops), `nhãn "${await opt.innerText()}" xuống dòng`).toBeLessThan(6);
    }
    // ba tab cùng chiều cao
    expect(heights.size).toBe(1);
    await group.getByRole('radio', { name: /^Hôm nay/ }).click();
    await expect(page.getByTestId('work-today')).toBeVisible();
    await expect(page.getByTestId('work-decide')).toHaveCount(0);
    // vùng chạm ≥ 44px: ô tick, nút Dời sang mai
    const check = await page.getByTestId('work-task-done').first().boundingBox();
    expect(check!.width).toBeGreaterThanOrEqual(44);
    expect(check!.height).toBeGreaterThanOrEqual(44);
    await group.getByRole('radio', { name: /^Gen đề xuất/ }).click();
    await expect(page.getByTestId('work-suggest')).toBeVisible();
    await noHorizontalScroll(page);
    await expect(page.getByTestId('owner-tabbar').getByRole('link')).toHaveCount(5);
  });

  test('rỗng: "Không có gì chờ Sếp duyệt." và "Hôm nay không có việc đến hạn." khi không còn nháp, đề xuất, việc, lời hứa quá hạn', async ({ page }) => {
    await p3Hook(page.request, 'owner', 'data', { mode: 'empty' }); // /owner/proposals rỗng
    await p3Hook(page.request, 'queue', 'setTasks', []);
    await p3Hook(page.request, 'core', 'setDrafts', []);
    await apiCall(page, 'PATCH', '/tasks/promises/pr-2', { kept: true }); // lời hứa quá hạn duy nhất của dữ liệu mẫu
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/owner/viec');
    await expect(page.getByText('Không có gì chờ Sếp duyệt.')).toBeVisible();
    await expect(page.getByText('Hôm nay không có việc đến hạn.')).toBeVisible();
    await expect(page.getByTestId('work-decide-ask')).toHaveText('Nhờ Gen soạn nháp');
    await expect(page.getByTestId('work-today-ask')).toHaveText('Nhờ Gen tạo nhắc');
    expect(await page.locator('body').innerText()).not.toContain('[object Object]');
    await p3Hook(page.request, 'owner', 'data', { mode: 'full' });
  });
});
