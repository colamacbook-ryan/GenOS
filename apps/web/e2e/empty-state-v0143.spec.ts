/**
 * v0.1.43 (F-29) — danh sách trống vì chưa có nguồn dữ liệu thì dẫn đường. Máy chủ giả giữ nguyên (không sửa
 * mock-api.ts): header và các API danh sách bị chặn bằng page.route để mô phỏng "chưa nối kênh" / "chưa nghe nhóm".
 */
import { expect, test, type Page } from '@playwright/test';
import { MANAGER, loginAs, loginAsOwner, resetMock } from './support';

const EMPTY_PAGE = { items: [], next_cursor: null, total: 0 };
const EMPTY_INBOX = { ...EMPTY_PAGE, counts: { all: 0, opportunity: 0, alert: 0, approval: 0, reply: 0, candidate: 0 } };

async function fakeHeader(page: Page, channels_live: number, groups_listening: number, channels_connected = channels_live) {
  await page.route(/\/api\/v1\/header(\?|$)/, (route) =>
    route.fulfill({ json: { channels_live, channels_connected, groups_listening, autonomy_level: 4, data_confidence: null } }),
  );
}

/** Mọi danh sách của 5 màn chính trả rỗng. */
async function emptyLists(page: Page) {
  await page.route(/\/api\/v1\/inbox(\?|$)/, (route) => route.fulfill({ json: EMPTY_INBOX }));
  await page.route(/\/api\/v1\/tasks\/promises(\?|$)/, (route) => route.fulfill({ json: EMPTY_PAGE }));
  await page.route(/\/api\/v1\/tasks(\?|$)/, (route) => (route.request().method() === 'GET' ? route.fulfill({ json: EMPTY_PAGE }) : route.fallback()));
  await page.route(/\/api\/v1\/drafts(\?|$)/, (route) => (route.request().method() === 'GET' ? route.fulfill({ json: EMPTY_PAGE }) : route.fallback()));
  await page.route(/\/api\/v1\/directory\/channels(\?|$)/, (route) => route.fulfill({ json: [] }));
  await page.route(/\/api\/v1\/directory\/groups(\?|$)/, (route) => route.fulfill({ json: EMPTY_PAGE }));
  await page.route(/\/api\/v1\/directory\/people(\?|$)/, (route) => route.fulfill({ json: EMPTY_PAGE }));
  await page.route(/\/api\/v1\/opportunities(\?|$)/, (route) => (route.request().method() === 'GET' ? route.fulfill({ json: EMPTY_PAGE }) : route.fallback()));
}

test.describe('v0.1.43 · DataEmptyState (F-29)', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
  });

  test('Hộp thư trống + chưa nối kênh → "Nối kênh" dẫn /guide/5', async ({ page }) => {
    await loginAsOwner(page);
    await fakeHeader(page, 0, 0);
    await emptyLists(page);
    await page.goto('/inbox');
    const empty = page.getByTestId('data-empty-state');
    await expect(empty).toHaveAttribute('data-reason', 'no-channel');
    await expect(empty.getByText('Chưa có dữ liệu vì chưa nối kênh')).toBeVisible();
    await empty.getByRole('link', { name: 'Nối kênh' }).click();
    await expect(page).toHaveURL(/\/guide\/5$/);
  });

  test('đã nối kênh nhưng chưa nghe nhóm → "Chọn nhóm để nghe" dẫn /guide/6', async ({ page }) => {
    await loginAsOwner(page);
    await fakeHeader(page, 1, 0);
    await emptyLists(page);
    await page.goto('/inbox');
    const empty = page.getByTestId('data-empty-state');
    await expect(empty).toHaveAttribute('data-reason', 'no-group');
    await expect(empty.getByText('Chưa chọn nhóm nào để nghe')).toBeVisible();
    await empty.getByRole('link', { name: 'Chọn nhóm để nghe' }).click();
    await expect(page).toHaveURL(/\/guide\/6$/);
  });

  test('kiểm khói /tasks, /workbench, /directory, /opportunity với danh sách rỗng', async ({ page }) => {
    await loginAsOwner(page);
    await fakeHeader(page, 0, 0);
    await emptyLists(page);
    for (const path of ['/tasks', '/workbench', '/directory', '/directory?dt=people', '/opportunity']) {
      await page.goto(path);
      const empty = page.getByTestId('data-empty-state').first();
      await expect(empty, path).toBeVisible();
      await expect(empty, path).toHaveAttribute('data-reason', 'no-channel');
      await expect(empty.getByRole('link', { name: 'Nối kênh' }), path).toHaveAttribute('href', '/guide/5');
    }
  });

  test('/tasks: chưa có bộ ghi lời hứa (v0.1.71) ⇒ không có khối «Lời hứa» để trống mãi', async ({ page }) => {
    await loginAsOwner(page);
    await fakeHeader(page, 0, 0);
    await emptyLists(page);
    await page.goto('/tasks');
    await expect(page.getByRole('heading', { name: 'Việc & Nhắc hẹn', level: 2 })).toBeVisible();
    await expect(page.locator('.tk-promises')).toHaveCount(0);
  });

  test('đã có kênh + nhóm: giữ trạng thái trống cũ của màn', async ({ page }) => {
    await loginAsOwner(page);
    await fakeHeader(page, 4, 42);
    await emptyLists(page);
    await page.goto('/inbox');
    await expect(page.getByText('Hộp thư đang trống')).toBeVisible();
    await expect(page.getByText('Không có tin, cảnh báo hay bản nháp nào khớp bộ lọc hiện tại.')).toBeVisible();
    await expect(page.getByTestId('data-empty-state')).toHaveCount(0);
  });

  test('kênh đã nối nhưng mất phiên → "Quét lại QR" dẫn /connections', async ({ page }) => {
    await loginAsOwner(page);
    await fakeHeader(page, 0, 3, 1);
    await emptyLists(page);
    await page.goto('/inbox');
    const empty = page.getByTestId('data-empty-state');
    await expect(empty).toHaveAttribute('data-reason', 'channel-down');
    await expect(empty.getByText('Kênh mất kết nối — quét lại QR')).toBeVisible();
    await expect(empty.getByRole('link', { name: 'Quét lại QR' })).toHaveAttribute('href', '/connections');
  });

  test('đang lọc (tab/bộ lọc) + chưa có kênh sống: vẫn báo "không khớp bộ lọc", không dẫn nối kênh', async ({ page }) => {
    await loginAsOwner(page);
    await fakeHeader(page, 0, 0);
    await emptyLists(page);
    await page.goto('/inbox?tab=alert');
    await expect(page.getByText('Hộp thư đang trống')).toBeVisible();
    await expect(page.getByTestId('data-empty-state')).toHaveCount(0);
    await page.goto('/tasks?status=done');
    await expect(page.getByText('Không có việc nào khớp bộ lọc')).toBeVisible();
    await page.goto('/directory?dt=people&heat=high');
    await expect(page.getByText('Không có ai khớp bộ lọc')).toBeVisible();
  });

  test('vai trò khác Owner: "Nhờ Owner …", không có nút dẫn đường', async ({ page }) => {
    await loginAs(page, MANAGER.email);
    await fakeHeader(page, 0, 0);
    await emptyLists(page);
    await page.goto('/inbox');
    const empty = page.getByTestId('data-empty-state');
    await expect(empty.getByText(/Nhờ Owner nối kênh/)).toBeVisible();
    await expect(empty.getByRole('link')).toHaveCount(0);
  });
});
