import { join } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { AUDITOR, OWNER, loginAs, loginAsOwner, p3Hook, resetMock, resultsDir, settle } from './support';

/**
 * v0.1.27 — phủ e2e cho các tính năng v0.1.24–v0.1.26 (mock, tất định):
 * thẻ đề xuất của Gen (Xác nhận / Sửa / Huỷ + đường hỏi PIN), chuông sau khi nhắc việc tới giờ,
 * huy hiệu lọc đầu Hộp thư + công tắc "Ẩn rác & trùng", thẻ Gen-hub ở MCP Hub (token chỉ ghi, Lưu / Kiểm tra / Tắt).
 */
const shots = join(resultsDir, 'visual');
const HUB_TOKEN = 'ghtok_E2E_SieuBiMat_0123456789';

async function enterPin(page: Page) {
  const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
  await expect(dlg).toBeVisible();
  await page.getByLabel('Mã PIN — chữ số 1/6').click();
  await page.keyboard.type(OWNER.pin);
  await expect(dlg).toBeHidden();
}

async function askGen(page: Page, question: string) {
  const panel = page.getByRole('complementary', { name: /Gen — trợ lý quản trị/ });
  if (!(await panel.isVisible())) await page.getByRole('button', { name: /Hỏi Gen/ }).click();
  await expect(panel).toBeVisible();
  await panel.getByLabel('Câu hỏi cho Gen').fill(question);
  await panel.getByRole('button', { name: 'Gửi' }).click();
  return panel;
}

test.describe('Gen — thẻ đề xuất có xác nhận', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 1440, height: 900 });
    await loginAsOwner(page);
    await page.goto('/overview');
  });

  test('nhắc việc: Sửa → Xác nhận → mã việc; tới giờ nhắc → chuông báo đúng việc', async ({ page }) => {
    const bell = page.locator('header .hd-bell');
    await expect(bell).toHaveAccessibleName('Thông báo — 1 chưa đọc');
    const panel = await askGen(page, 'nhắc tôi gọi lại khách sau 1 giờ');
    const card = panel.getByRole('group', { name: 'Đề xuất: Tạo nhắc việc' });
    await expect(card).toBeVisible();
    await expect(card).toContainText('Gọi lại khách');
    await expect(card.getByText('Cần mã PIN')).toHaveCount(0);

    await card.getByRole('button', { name: 'Sửa' }).click();
    const title = card.getByLabel('Việc');
    await expect(title).toHaveValue('Gọi lại khách');
    await title.fill('Gọi lại anh Bảo');
    await expect(card.getByLabel('Giao cho')).toBeVisible();
    await card.getByRole('button', { name: 'Xác nhận' }).click();
    await expect(card).toContainText('Đã xác nhận · TSK-0999');
    await expect(card.getByRole('button', { name: 'Xác nhận' })).toHaveCount(0);
    await settle(page);
    await page.screenshot({ path: join(shots, 'gen-proposal-confirmed.png') });

    // Worker task_reminder_scan tới giờ (hook mock) → chuông cập nhật qua WS, không cần tải lại.
    expect(await p3Hook(page.request, 'gen', 'fireReminders')).toEqual({ fired: 1 });
    await expect(bell).toHaveAccessibleName('Thông báo — 2 chưa đọc');
    expect(await p3Hook(page.request, 'gen', 'fireReminders')).toEqual({ fired: 0 }); // mỗi mốc một lần
    await panel.getByRole('button', { name: 'Đóng khung Gen' }).click();
    await bell.click();
    const dlg = page.getByRole('dialog', { name: 'Thông báo' });
    const item = dlg.locator('.nt-item').first();
    await expect(item).toContainText('Nhắc việc: Gọi lại anh Bảo');
    await expect(item).toContainText('TSK-0999 · P2');
    await item.click();
    await expect(page).toHaveURL(/\/tasks$/);
    await expect(bell).toHaveAccessibleName('Thông báo — 1 chưa đọc');
  });

  test('Huỷ: đề xuất đóng lại, không làm gì', async ({ page }) => {
    const panel = await askGen(page, 'nhắc tôi việc này');
    const card = panel.getByRole('group', { name: 'Đề xuất: Tạo nhắc việc' });
    await card.getByRole('button', { name: 'Huỷ' }).click();
    await expect(card).toContainText('Đã huỷ — không làm gì');
    await expect(card.getByRole('button', { name: 'Xác nhận' })).toHaveCount(0);
    expect(await p3Hook(page.request, 'gen', 'fireReminders')).toEqual({ fired: 0 });
  });

  test('nháp tin cần PIN: Xác nhận → hỏi PIN → tự gửi lại → đã vào hàng chờ duyệt', async ({ page }) => {
    const panel = await askGen(page, 'soạn nháp tin báo giá cho anh Bảo');
    const card = panel.getByRole('group', { name: 'Đề xuất: Soạn nháp tin gửi đi' });
    await expect(card.getByText('Cần mã PIN')).toBeVisible();
    await expect(card).toContainText('Anh Bảo');
    await card.getByRole('button', { name: 'Xác nhận' }).click();
    await enterPin(page);
    await expect(card).toContainText('Đã lưu nháp — chưa gửi · ACT-0999');
    await expect(card.getByText('Cần mã PIN')).toHaveCount(0);
  });

  test('nháp tin: bỏ qua hộp PIN → đề xuất vẫn chờ, bấm lại được', async ({ page }) => {
    const panel = await askGen(page, 'soạn nháp tin');
    const card = panel.getByRole('group', { name: 'Đề xuất: Soạn nháp tin gửi đi' });
    await card.getByRole('button', { name: 'Xác nhận' }).click();
    const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await expect(dlg).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dlg).toBeHidden();
    await expect(card.getByRole('button', { name: 'Xác nhận' })).toBeEnabled();
    await expect(card).not.toContainText('Đã xác nhận');
  });
});

test.describe('Hộp thư — lọc đầu (trùng, rác, điểm)', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 1440, height: 900 });
    await loginAsOwner(page);
  });

  test('huy hiệu Trùng / Rác / Điểm + "Ẩn rác & trùng" nhớ theo đường dẫn', async ({ page }) => {
    await page.goto('/inbox');
    const junk = page.getByRole('article', { name: 'Chào bán' });
    await expect(junk).toBeVisible();
    for (const label of ['Nghi rác', 'Trùng', 'Rác', 'Điểm lọc 8']) await expect(junk.getByText(label, { exact: true })).toBeVisible();
    await expect(junk.locator('span[title="Gần trùng với một mục trước đó"]')).toHaveCount(1);
    await expect(junk.locator('span[title^="có đường link"]')).toHaveCount(1);
    const good = page.getByRole('article', { name: 'Hỏi giá' });
    await expect(good.getByText('Điểm lọc 88', { exact: true })).toBeVisible();
    await expect(good.getByText('Rác', { exact: true })).toHaveCount(0);

    const toggle = page.getByRole('switch', { name: 'Ẩn rác & trùng' });
    await expect(toggle).toHaveAttribute('aria-checked', 'false');
    await toggle.click();
    await expect(page).toHaveURL(/[?&]hide=1/);
    await expect(toggle).toHaveAttribute('aria-checked', 'true');
    await expect(junk).toHaveCount(0);
    await expect(good).toBeVisible();
    await expect(page.getByText(/Đã ẩn 1 mục trùng, rác hoặc điểm dưới 30/)).toBeVisible();
    await settle(page);
    await page.screenshot({ path: join(shots, 'inbox-hide-junk.png') });

    await page.reload();
    await expect(page.getByRole('switch', { name: 'Ẩn rác & trùng' })).toHaveAttribute('aria-checked', 'true');
    await expect(page.getByRole('article', { name: 'Chào bán' })).toHaveCount(0);
    await page.getByRole('switch', { name: 'Ẩn rác & trùng' }).click();
    await expect(page).not.toHaveURL(/hide=1/);
    await expect(page.getByRole('article', { name: 'Chào bán' })).toBeVisible();
  });
});

test.describe('MCP Hub — thẻ Gen-hub', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  test('Owner: token chỉ ghi, Lưu (PIN) → Kiểm tra → Tắt; token không bao giờ quay lại trình duyệt', async ({ page }) => {
    const hubBodies: string[] = [];
    page.on('response', async (res) => {
      if (res.url().includes('/api/v1/hub/')) hubBodies.push(await res.text().catch(() => ''));
    });
    await loginAsOwner(page);
    await page.goto('/connections'); // v0.1.42 (F-61): thẻ Gen-hub chỉ ở Kết nối
    const card = page.getByRole('region', { name: 'Gen-hub' });
    await expect(card).toContainText('Chưa nối');
    await expect(card).toContainText('Đang tắt');
    const save = card.getByRole('button', { name: 'Lưu', exact: true });
    const check = card.getByRole('button', { name: 'Kiểm tra', exact: true });
    await expect(save).toBeDisabled();
    await expect(check).toBeDisabled();
    await expect(card.getByRole('button', { name: 'Tắt' })).toHaveCount(0);

    await card.getByLabel('Địa chỉ Gen-hub').fill('https://hub.example.test/mcp');
    const token = card.getByLabel('Token Gen-hub');
    await expect(token).toHaveAttribute('type', 'password');
    await token.fill('ngan');
    await expect(save).toBeDisabled(); // token < 8 ký tự
    await token.fill(HUB_TOKEN);
    await card.getByLabel('Ngày hết hạn token').fill('2026-12-28');
    // v0.1.39 (F-31): địa chỉ https công khai → công tắc "mạng công cộng" tự bật sẵn.
    await expect(card.getByRole('switch', { name: 'Cho phép Gen-hub ở mạng công cộng' })).toHaveAttribute('aria-checked', 'true');
    await save.click();
    await enterPin(page);
    await expect(card).toContainText('Đã lưu (mã hoá, không hiện lại)');
    await expect(card.getByLabel('Token mới (bỏ trống để giữ token đã lưu)')).toHaveValue('');
    await expect(card).toContainText('https://hub.example.test/mcp');
    await expect(card).toContainText('2026-12-28');
    await expect(card).toContainText('Đang tắt'); // chưa Kiểm tra thì chưa bật

    await expect(check).toBeEnabled();
    await check.click();
    // v0.1.50: tool đọc và tool ghi Kho (kho_create, kho_update) đếm riêng — không gộp tool ghi vào "tool đọc".
    await expect(card.getByRole('status')).toContainText('Đã nối Kho · 240 ms · mở 3 tool đọc + 2 tool ghi Kho cho Gen');
    await expect(card).toContainText('Đang nối');
    await settle(page);
    await page.screenshot({ path: join(shots, 'mcp-hub-link.png') });

    await card.getByRole('button', { name: 'Tắt' }).click();
    await expect(card).toContainText('Đang tắt');
    await expect(card.getByRole('button', { name: 'Tắt' })).toHaveCount(0);

    // Token chỉ ghi: không nằm trong DOM, không nằm trong bất kỳ phản hồi /hub nào.
    expect(await page.content()).not.toContain(HUB_TOKEN);
    expect(hubBodies.length).toBeGreaterThan(0);
    for (const b of hubBodies) expect(b).not.toContain(HUB_TOKEN);
  });

  test('vai trò khác chỉ xem trạng thái, không có ô token / nút', async ({ page }) => {
    await loginAs(page, AUDITOR.email);
    await page.goto('/connections');
    const card = page.getByRole('region', { name: 'Gen-hub' });
    await expect(card).toContainText('Chỉ Sếp (Owner) cấu hình và dùng Gen-hub.');
    await expect(card.getByLabel('Token Gen-hub')).toHaveCount(0);
    await expect(card.getByRole('button', { name: 'Lưu' })).toHaveCount(0);
    await expect(card.getByRole('button', { name: 'Kiểm tra' })).toHaveCount(0);
  });
});
