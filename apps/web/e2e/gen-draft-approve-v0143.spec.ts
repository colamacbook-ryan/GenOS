import { expect, test, type Locator, type Page } from '@playwright/test';
import { OWNER, apiCall, loginAsOwner, resetMock } from './support';

/**
 * v0.1.43 (F-24): nháp tin của Gen chỉ được LƯU — thẻ nói rõ "Đã lưu nháp — chưa gửi". Chỉ khi nháp có NƠI GỬI thật
 * (API chỉ gắn khi đối tượng là NHÓM) thì nút chính mới là "Mở để duyệt và gửi"; nháp cho một người (chưa có nơi gửi) chỉ có
 * "Mở nháp ở Bàn làm việc" + "Nháp chưa có nơi gửi" — Bàn làm việc hiện "Duyệt và thực hiện", không hứa gửi.
 */

async function enterPin(page: Page) {
  const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
  await expect(dlg).toBeVisible();
  await page.getByLabel('Mã PIN — chữ số 1/6').click();
  await page.keyboard.type(OWNER.pin);
  await expect(dlg).toBeHidden();
}

async function askAndConfirm(page: Page, question: string, title: string): Promise<Locator> {
  const panel = page.getByRole('complementary', { name: /Gen — trợ lý quản trị/ });
  await page.getByRole('button', { name: /Hỏi Gen/ }).click();
  await expect(panel).toBeVisible();
  await panel.getByLabel('Câu hỏi cho Gen').fill(question);
  await panel.getByRole('button', { name: 'Gửi' }).click();

  const card = panel.getByRole('group', { name: 'Đề xuất: Soạn nháp tin gửi đi' });
  await expect(card).toBeVisible();
  await expect(card).toContainText(title);
  await card.getByRole('button', { name: 'Xác nhận' }).click();
  await enterPin(page);

  await expect(card).toContainText('Đã lưu nháp — chưa gửi · ACT-0999');
  await expect(card).not.toContainText('Đã xác nhận');
  await expect(card.locator('[data-icon="paper-plane-tilt"]')).toHaveCount(0);
  return card;
}

async function expectWorkbenchDraft(page: Page, title: string, text: string): Promise<string> {
  await expect(page).toHaveURL(/\/workbench\?id=draft-gen-[0-9a-f-]+$/);
  const id = decodeURIComponent(new URL(page.url()).searchParams.get('id') ?? '');
  expect(id).toMatch(/^draft-gen-/);
  // Dòng nháp được chọn đúng tiêu đề; khung chi tiết hiện đúng nháp đó.
  const row = page.locator('.wb-list__row[aria-pressed="true"]');
  await expect(row).toHaveCount(1);
  await expect(row).toContainText(title);
  await expect(page.locator('.wb-grid').getByText(text)).toBeVisible();
  return id;
}

test.beforeEach(async ({ page }) => {
  await resetMock(page.request, 'finished');
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginAsOwner(page);
  await page.goto('/overview');
});

test('nháp cho NHÓM (có nơi gửi) → "Mở để duyệt và gửi" mở đúng nháp, Bàn làm việc có "Duyệt và gửi qua Zalo"', async ({ page }) => {
  const card = await askAndConfirm(page, 'soạn nháp tin báo giá cho nhóm Thành Phát', 'Báo giá ván MDF cho nhóm');
  const go = card.getByRole('button', { name: 'Mở để duyệt và gửi' });
  await expect(go).toBeVisible();
  await go.click();

  const id = await expectWorkbenchDraft(page, 'Báo giá ván MDF cho nhóm', 'Chào cả nhà, bên em gửi báo giá ván MDF E1 17mm ạ.');
  await expect(page.locator('.wb-grid').getByRole('button', { name: /Duyệt và gửi qua Zalo/ })).toBeVisible();
  // Chỉ điều hướng — nháp vẫn chờ duyệt.
  const saved = (await apiCall(page, 'GET', `/drafts/${encodeURIComponent(id)}`)) as { status: string; title: string };
  expect(saved).toMatchObject({ status: 'pending', title: 'Báo giá ván MDF cho nhóm' });
});

test('nháp cho một NGƯỜI (chưa có nơi gửi) → không hứa "Mở để duyệt và gửi", chỉ "Mở nháp ở Bàn làm việc"', async ({ page }) => {
  const card = await askAndConfirm(page, 'soạn nháp tin báo giá cho anh Bảo', 'Báo giá ván MDF');
  await expect(card.getByRole('button', { name: 'Mở để duyệt và gửi' })).toHaveCount(0);
  await expect(card).toContainText('Nháp chưa có nơi gửi');
  await card.getByRole('button', { name: /Mở nháp ở Bàn làm việc/ }).click();

  const id = await expectWorkbenchDraft(page, 'Báo giá ván MDF', 'Chào anh Bảo, bên em gửi báo giá ván MDF E1 17mm như anh hỏi ạ.');
  const detail = page.locator('.wb-grid');
  await expect(detail.getByRole('button', { name: /Duyệt và thực hiện/ })).toBeVisible();
  await expect(detail.getByRole('button', { name: /Duyệt và gửi qua/ })).toHaveCount(0);
  const saved = (await apiCall(page, 'GET', `/drafts/${encodeURIComponent(id)}`)) as { status: string; target: unknown };
  expect(saved).toMatchObject({ status: 'pending', target: null });
});
