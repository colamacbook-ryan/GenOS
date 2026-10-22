import { expect, test, type Page } from '@playwright/test';
import { loginAsOwner, p3Hook, resetMock } from './support';

/**
 * v0.1.59 (G3) — màn PHÂN TÍCH (/owner/phan-tich) trên máy chủ giả (tất định):
 *  - chuyển 7 ↔ 30 ngày đổi số và câu so sánh;
 *  - mode `nochannel` → đúng một first-run-card; `empty` (đã nối kênh, chưa có tin) → một câu bật nhóm lắng nghe; `collecting` → câu "Đang gom dữ liệu", không huy hiệu chênh lệch; `full` → bảng đủ 9 chỉ số;
 *  - chi phí: "N lượt chưa đặt giá" + link /api, KHÔNG "0 ₫"; điện thoại: tile 2×2 → biểu đồ cao 180 → bảng xếp chồng, không cuộn ngang.
 * Chế độ dữ liệu: p3Hook(request, 'owner', 'data', { mode }).
 */

const NINE = ['Tin nhận', 'Người mới liên hệ', 'Cơ hội mới', 'Tín hiệu đáng chú ý', 'Cơ hội thắng', 'Trả lời nhanh (trong 15 phút)', 'Tin khách chờ trả lời', 'Chi phí trợ lý Gen', 'Gen lọc giúp'];

async function noHorizontalScroll(page: Page): Promise<void> {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over).toBeLessThanOrEqual(0);
}

test.describe('v0.1.59 · Phân tích', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await p3Hook(page.request, 'owner', 'data', { mode: 'full' });
    await p3Hook(page.request, 'owner', 'reset');
    await loginAsOwner(page);
  });
  test.afterEach(async ({ page }) => {
    await p3Hook(page.request, 'owner', 'data', { mode: 'full' });
  });

  test('mode full → bảng "Bảng số liệu" đủ 9 chỉ số, 4 cột; 4 tile; câu "So với 7 ngày trước đó · Cập nhật HH:MM"', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/owner/phan-tich');
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Phân tích');
    const table = page.getByRole('table', { name: 'Bảng số liệu' });
    await expect(table).toBeVisible();
    await expect(table.getByRole('columnheader')).toHaveText(['Chỉ số', 'Kỳ này', 'Kỳ trước', 'Thay đổi']);
    await expect(table.getByRole('row')).toHaveCount(10);
    for (const label of NINE) await expect(table.getByRole('cell', { name: label, exact: true })).toBeVisible();
    for (const key of ['messages_in', 'new_contacts', 'opps_new', 'reply_fast_pct']) await expect(page.getByTestId(`an-tile-${key}`)).toBeVisible();
    await expect(page.getByTestId('an-status-line')).toHaveText(/^So với 7 ngày trước đó · Cập nhật \d{2}:\d{2}$/);
    await expect(page.getByTestId('an-chart-messages')).toBeVisible();
    await expect(page.getByTestId('an-chart-asked')).toContainText('Hỏi giá');
    await expect(page.getByTestId('an-chart-funnel')).toBeVisible();
    await expect(page.getByTestId('an-chart-cost')).toBeVisible();
    // 8 cột / 4 cột và 6 / 6 như bố cục thiết kế
    const msg = (await page.getByTestId('an-chart-messages').boundingBox())!;
    const asked = (await page.getByTestId('an-chart-asked').boundingBox())!;
    expect(msg.width).toBeGreaterThan(asked.width * 1.7);
    expect(asked.x).toBeGreaterThan(msg.x + msg.width - 1);
    expect(await page.locator('body').innerText()).not.toContain('[object Object]');
    expect(await page.locator('body').innerText()).not.toMatch(/NaN|undefined/);
    await noHorizontalScroll(page);
  });

  test('chuyển 7 ngày ↔ 30 ngày đổi số, câu so sánh và chữ chú giải', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/owner/phan-tich');
    const tile = page.getByTestId('an-tile-messages_in');
    await expect(tile).toBeVisible();
    const week = await tile.innerText();
    await expect(page.getByRole('radio', { name: '7 ngày' })).toHaveAttribute('aria-checked', 'true');
    await page.getByRole('radio', { name: '30 ngày' }).click();
    await expect(page.getByRole('radio', { name: '30 ngày' })).toHaveAttribute('aria-checked', 'true');
    await expect(page.getByTestId('an-status-line')).toContainText('So với 30 ngày trước đó');
    await expect(tile).toContainText('so với 30 ngày trước');
    await expect.poll(async () => tile.innerText()).not.toBe(week);
    await expect(page.getByTestId('an-chart-messages')).toContainText('30 ngày này');
    // đánh giá chỉ ở 7 ngày; quay lại 7 ngày thì có
    await expect(page.getByTestId('an-feedback')).toHaveCount(0);
    await page.getByRole('radio', { name: '7 ngày' }).click();
    await expect(page.getByTestId('an-feedback')).toBeVisible();
    await expect(tile).toContainText('so với 7 ngày trước');
  });

  test('mode nochannel → đúng một first-run-card, không tile / biểu đồ / bảng', async ({ page }) => {
    await p3Hook(page.request, 'owner', 'data', { mode: 'nochannel' });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/owner/phan-tich');
    await expect(page.locator('[data-testid=first-run-card]')).toHaveCount(1);
    for (const id of ['an-tile-messages_in', 'an-chart-messages', 'an-table-card', 'an-tile-cost']) await expect(page.getByTestId(id)).toHaveCount(0);
    await expect(page.locator('.gh-state--empty')).toHaveCount(0);
    await noHorizontalScroll(page);
  });

  test('mode empty (đã nối Zalo, chưa có tin) → KHÔNG bảo nối Zalo lại: một câu "bật nhóm lắng nghe" + nút → /guide/6', async ({ page }) => {
    await p3Hook(page.request, 'owner', 'data', { mode: 'empty' });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/owner/phan-tich');
    await expect(page.getByTestId('first-run-card')).toHaveCount(0);
    await expect(page.getByTestId('an-empty-listen')).toContainText('Chưa có tin nào — bật nhóm lắng nghe để Gen bắt đầu đọc');
    await expect(page.getByTestId('an-empty-listen-cta')).toHaveAttribute('href', '/guide/6');
    for (const id of ['an-tile-messages_in', 'an-chart-messages', 'an-table-card']) await expect(page.getByTestId(id)).toHaveCount(0);
    await noHorizontalScroll(page);
  });

  test('mode collecting → "Đang gom dữ liệu — còn N ngày nữa có so sánh", không huy hiệu chênh lệch, không đường kỳ trước', async ({ page }) => {
    await p3Hook(page.request, 'owner', 'data', { mode: 'collecting' });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/owner/phan-tich');
    await expect(page.getByTestId('an-status-line')).toContainText('Đang gom dữ liệu — còn 5 ngày nữa có so sánh');
    await expect(page.getByTestId('an-tile-messages_in')).toBeVisible();
    await expect(page.locator('.kit-delta')).toHaveCount(0);
    await expect(page.locator('.kit-badge--ok, .kit-badge--warn')).toHaveCount(0);
    await expect(page.locator('[data-testid=an-trend-messages] .kit-chart__prev')).toHaveCount(0);
    await expect(page.getByRole('table', { name: 'Bảng số liệu' }).getByRole('row')).toHaveCount(10);
    expect(await page.locator('body').innerText()).not.toMatch(/Tăng \d|Giảm \d/);
  });

  test('chi phí: "N lượt chưa đặt giá — chi phí thật có thể cao hơn" + link /api; trần ngân sách trong chú giải; không "0 ₫"', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/owner/phan-tich');
    const card = page.getByTestId('an-chart-cost');
    await expect(card).toBeVisible();
    await expect(card.getByTestId('an-unpriced')).toContainText('12 lượt chưa đặt giá — chi phí thật có thể cao hơn');
    await expect(card.getByRole('link', { name: 'Đặt giá trong Cài đặt nâng cao' })).toHaveAttribute('href', '/api');
    await expect(card).toContainText('Trần ngân sách');
    await expect(card.getByTestId('an-cost-total')).toContainText(/^Tổng /);
    expect(await card.innerText()).not.toMatch(/(^|\s)0 ₫/);
  });

  test('điện thoại 390px: chọn kỳ → tile 2×2 → biểu đồ toàn bề ngang cao 180 → bảng xếp chồng; không cuộn ngang', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/owner/phan-tich');
    await expect(page.getByTestId('an-tile-messages_in')).toBeVisible();
    await noHorizontalScroll(page);
    const tiles = await Promise.all(['messages_in', 'new_contacts', 'opps_new', 'reply_fast_pct'].map(async (k) => (await page.getByTestId(`an-tile-${k}`).boundingBox())!));
    expect(Math.abs(tiles[0].y - tiles[1].y)).toBeLessThan(2); // hàng 1: hai tile cạnh nhau
    expect(Math.abs(tiles[2].y - tiles[3].y)).toBeLessThan(2); // hàng 2
    expect(tiles[2].y).toBeGreaterThan(tiles[0].y + tiles[0].height - 1);
    expect(tiles[1].x).toBeGreaterThan(tiles[0].x);
    // biểu đồ: toàn bề ngang khung và cao 180
    const svg = page.locator('[data-testid=an-trend-messages] svg.kit-chart__svg');
    await expect(svg).toBeVisible();
    expect(Math.round((await svg.boundingBox())!.height)).toBe(180);
    const card = (await page.getByTestId('an-chart-messages').boundingBox())!;
    expect(card.width).toBeGreaterThan(340);
    // bảng xếp chồng: 9 dòng, không <table>
    await expect(page.getByTestId('an-table').locator('[data-testid=kit-list-row]')).toHaveCount(9);
    await expect(page.getByRole('table')).toHaveCount(0);
    await expect(page.getByTestId('owner-tabbar').getByRole('link')).toHaveCount(5);
    // vùng chạm: nút chọn kỳ ≥ 44px
    expect((await page.getByRole('radio', { name: '30 ngày' }).boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await noHorizontalScroll(page);
  });
});
