import { expect, test, type Page } from '@playwright/test';
import { enterPin, loginAsOwner, p3Hook, resetMock } from './support';

/**
 * v0.1.59 (G3) — màn TÌNH BÁO (/owner) trên máy chủ giả (tất định; mã PIN 246810; KHÔNG gọi Gen-hub / Kho thật):
 *  - "Việc phải quyết": bấm "Duyệt" một nháp → ngăn DraftDecide → "Duyệt và gửi" → hộp mã PIN → nháp biến mất và số giảm;
 *  - ngày đầu (`mode: 'nochannel'`) → đúng MỘT [data-testid=first-run-card], không chồng EmptyState;
 *  - bản tin chưa có (brief rỗng) vẫn hiện số (kpis + số liệu lúc này);
 *  - 390px không cuộn ngang, thứ tự đọc đúng, số 28px ở khối đầu.
 * Chế độ dữ liệu: p3Hook(request, 'owner', 'data', { mode }) — trả về 'full' sau mỗi ca.
 */

async function noHorizontalScroll(page: Page): Promise<void> {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over).toBeLessThanOrEqual(0);
}

const count = async (page: Page): Promise<number> => Number.parseInt((await page.getByTestId('intel-decide-count').innerText()).replace(/\D/g, ''), 10);

test.describe('v0.1.59 · Tình báo', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await p3Hook(page.request, 'owner', 'data', { mode: 'full' });
    await p3Hook(page.request, 'owner', 'reset');
    await loginAsOwner(page);
  });
  test.afterEach(async ({ page }) => {
    await p3Hook(page.request, 'owner', 'data', { mode: 'full' });
    await p3Hook(page.request, 'owner', 'reset');
  });

  test('bấm "Duyệt" nháp ở Việc phải quyết → ngăn → "Duyệt và gửi" → hộp PIN → nháp biến mất và số giảm', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/owner');
    await expect(page.getByTestId('intel-decide-count')).toBeVisible();
    const before = await count(page);
    expect(before).toBeGreaterThan(0);
    const decide = page.getByTestId('intel-decide');
    const rows = decide.getByTestId('intel-decide-row-draft');
    expect(await rows.count()).toBeGreaterThan(0);
    const title = (await rows.first().locator('.kit-row__title').innerText()).trim();

    await rows.first().getByRole('button', { name: 'Duyệt', exact: true }).click();
    const sheet = page.getByRole('dialog', { name: 'Duyệt bản nháp' });
    await expect(sheet).toBeVisible();
    await expect(sheet.getByTestId('owner-draft-text')).toBeVisible();
    await sheet.getByRole('button', { name: 'Duyệt và gửi' }).click();
    await enterPin(page);

    await expect(sheet).toBeHidden();
    await expect(page.getByTestId('intel-decide-count')).toHaveText(String(before - 1));
    await expect(decide.getByText(title, { exact: true })).toHaveCount(0);
    // thanh điều hướng "Việc" cũng khớp (cùng nguồn today.nav.decide)
    await expect(page.getByTestId('owner-nav-work')).toContainText(String(before - 1));
    expect(await page.locator('body').innerText()).not.toContain('[object Object]');
  });

  test('đề xuất của Gen ở Việc phải quyết: "Xem" mở ngăn, chưa ghi gì cho tới khi Sếp bấm Xác nhận', async ({ page }) => {
    await p3Hook(page.request, 'core', 'setDrafts', []); // chỉ còn đề xuất ⇒ hiện đủ trong ba dòng đầu
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/owner');
    const rows = page.getByTestId('intel-decide-row-proposal');
    await expect(rows).toHaveCount(2);
    await expect(page.getByTestId('intel-decide-count')).toHaveText('2');
    await rows.first().getByRole('button', { name: 'Xem' }).click();
    const sheet = page.getByRole('dialog', { name: 'Đề xuất của Gen' });
    await expect(sheet.getByTestId('owner-proposal-summary')).toBeVisible();
    await expect(sheet.getByRole('button', { name: 'Xác nhận' })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(sheet).toBeHidden();
    await expect(page.getByTestId('intel-decide-count')).toHaveText('2'); // chỉ xem, chưa xác nhận ⇒ số không đổi
  });

  test('ngày đầu (nochannel) → đúng MỘT first-run-card, không tile / tin / khách, giữ hàng 1', async ({ page }) => {
    await p3Hook(page.request, 'owner', 'data', { mode: 'nochannel' });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/owner');
    await expect(page.getByTestId('intel-hero')).toBeVisible();
    await expect(page.locator('[data-testid=first-run-card]')).toHaveCount(1);
    await expect(page.getByTestId('first-run-connect')).toHaveText('Nối Zalo');
    for (const id of ['intel-tile-hot', 'intel-tile-messages', 'intel-feed', 'intel-care', 'intel-key']) await expect(page.getByTestId(id)).toHaveCount(0);
    await expect(page.locator('.gh-state--empty')).toHaveCount(0); // không chồng EmptyState
    await expect(page.getByTestId('intel-summary')).toContainText('Bản tin đầu tiên lúc 07:30');
    await noHorizontalScroll(page);
  });

  test('bản tin chưa có vẫn hiện số: câu "Bản tin đầu tiên lúc 07:30…" + tile lấy từ số liệu lúc này', async ({ page }) => {
    await p3Hook(page.request, 'owner', 'scenario', { mode: 'empty', only: 'brief' });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/owner');
    await expect(page.getByTestId('intel-summary')).toHaveText('Bản tin đầu tiên lúc 07:30. Dưới đây là số liệu lúc này.');
    await expect(page.getByTestId('intel-brief-history')).toHaveCount(0);
    for (const id of ['intel-tile-hot', 'intel-tile-opps']) {
      await expect(page.getByTestId(id)).toBeVisible();
      await expect(page.getByTestId(id)).toContainText(/\d/);
    }
    await expect(page.getByTestId('intel-tile-promises')).toHaveCount(0); // v0.1.71: chưa có bộ ghi lời hứa ⇒ ẩn ô
    await expect(page.getByTestId('intel-tile-messages')).toBeVisible();
    await expect(page.locator('[data-testid=first-run-card]')).toHaveCount(0);
  });

  test('390px: không cuộn ngang; thứ tự đọc tóm tắt → Việc phải quyết → tile → Điểm chính → Tin quan trọng → Khách cần chăm; số 28px', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/owner');
    await expect(page.getByTestId('intel-decide-count')).toBeVisible();
    await expect(page.getByTestId('intel-care-row').first()).toBeVisible();
    await noHorizontalScroll(page);
    await expect(page.getByTestId('owner-tabbar').getByRole('link')).toHaveCount(5);
    const order = await page.evaluate(() => {
      const y = (id: string) => (document.querySelector(`[data-testid="${id}"]`) as HTMLElement | null)?.getBoundingClientRect().top ?? -1;
      return ['intel-summary', 'intel-decide', 'intel-tile-messages', 'intel-key', 'intel-feed', 'intel-care'].map(y);
    });
    expect(order.every((v) => v >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    const sizes = await page.evaluate(() => {
      const fs = (id: string) => getComputedStyle(document.querySelector(`[data-testid="${id}"]`) as Element).fontSize;
      const up = (id: string) => getComputedStyle(document.querySelector(`[data-testid="${id}"]`) as Element).textTransform;
      return { big: fs('intel-decide-count'), summary: fs('intel-summary'), hello: fs('intel-hello'), sub: fs('intel-hero-sub'), upper: up('intel-summary') };
    });
    expect(sizes).toEqual({ big: '28px', summary: '18px', hello: '14px', sub: '12px', upper: 'none' });
    // vùng chạm ≥ 44px: nút "Duyệt" và dòng Điểm chính
    const hit = await page.getByTestId('intel-decide-approve').first().boundingBox();
    expect(hit!.height).toBeGreaterThanOrEqual(44);
    const keyRow = await page.locator('.intel-keyrow__main').first().boundingBox();
    expect(keyRow!.height).toBeGreaterThanOrEqual(44);
  });

  test('dải nguồn AI không hiện khi nguồn AI đã chạy; tin quan trọng → ?tin=; không có ô lời hứa (chưa theo dõi); không có Chi phí trợ lý Gen', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/owner');
    await expect(page.getByTestId('intel-decide-count')).toBeVisible();
    await expect(page.getByTestId('intel-ai-strip')).toHaveCount(0);
    expect(await page.locator('main').innerText()).not.toMatch(/Chi phí trợ lý Gen|Chi phí AI/);
    const first = page.getByTestId('intel-feed-row').first();
    await expect(first).toBeVisible();
    const href = await first.getByRole('link').getAttribute('href');
    expect(href).toMatch(/^\/owner\/thong-tin\?tin=/);
    await expect(page.getByTestId('intel-tile-promises')).toHaveCount(0);
  });
});
