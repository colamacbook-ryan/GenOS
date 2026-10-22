import { expect, test, type Page } from '@playwright/test';
import { OWNER, loginAsOwner, resetMock } from './support';

/**
 * v0.1.45 (F-20) — PIN đúng chỗ hạ rào: sửa agent chỉ hỏi PIN khi đổi mức tự trị / điều cấm / giới hạn / phạm vi kênh;
 * thêm tài khoản CLI hỏi PIN (`cli.switch_account`); Hướng dẫn bước 9 sau Hoàn tất hỏi PIN (`policy.change`).
 * Mock (test/mock-p4-agents.ts, mock-phase2.ts, mock-api.ts) trả 423 như API thật; client tự mở hộp PIN rồi gửi lại.
 */

const PIN_DIALOG = 'Mã PIN xác nhận thao tác';
const AGENT = 'Trợ lý thương mại';

async function enterOwnerPin(page: Page): Promise<void> {
  const dlg = page.getByRole('dialog', { name: PIN_DIALOG });
  await expect(dlg).toBeVisible();
  await page.getByLabel('Mã PIN — chữ số 1/6').click();
  await page.keyboard.type(OWNER.pin);
  await expect(dlg).toBeHidden();
}

async function openEdit(page: Page) {
  await page.goto('/agents');
  const card = page.getByRole('listitem', { name: AGENT, exact: true });
  await expect(card).toBeVisible();
  await card.getByRole('button', { name: `Sửa agent ${AGENT}` }).click();
  const dlg = page.getByRole('dialog', { name: `Sửa ${AGENT}` });
  await expect(dlg).toBeVisible();
  return dlg;
}

test.describe('v0.1.45 — PIN đúng chỗ hạ rào', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  test('sửa agent chỉ đổi tên: không hỏi PIN, lưu xong', async ({ page }) => {
    const dlg = await openEdit(page);
    await expect(dlg.getByText('Đổi mức tự trị, điều cấm hay phạm vi kênh cần mã PIN')).toBeVisible();
    await dlg.getByLabel('Tên hiển thị').fill('Trợ lý thương mại 2');
    const req = page.waitForResponse((r) => r.url().includes('/agents/') && r.request().method() === 'PATCH');
    await dlg.getByRole('button', { name: 'Lưu thay đổi' }).click();
    const res = await req;
    expect(res.status()).toBe(200);
    const sent = res.request().postDataJSON() as Record<string, unknown>;
    expect(sent).not.toHaveProperty('forbidden');
    expect(sent).not.toHaveProperty('channel_scopes');
    expect(sent).not.toHaveProperty('autonomy_level');
    await expect(dlg).toBeHidden();
    await expect(page.getByRole('dialog', { name: PIN_DIALOG })).toHaveCount(0);
    await expect(page.getByRole('listitem', { name: 'Trợ lý thương mại 2', exact: true })).toBeVisible();
  });

  test('đổi mức tự trị: hỏi PIN → nhập → lưu, thẻ hiện mức mới', async ({ page }) => {
    const dlg = await openEdit(page);
    await dlg.getByRole('group', { name: 'Mức tự trị' }).getByRole('button', { name: 'Gợi ý' }).click();
    await dlg.getByRole('button', { name: 'Lưu thay đổi' }).click();
    await enterOwnerPin(page);
    await expect(dlg).toBeHidden();
    const card = page.getByRole('listitem', { name: AGENT, exact: true });
    await expect(card.locator('.ag-autonomy')).toHaveText('Gợi ý');
  });

  test('huỷ PIN khi đổi mức tự trị: báo đã huỷ, mức giữ nguyên', async ({ page }) => {
    const dlg = await openEdit(page);
    await dlg.getByRole('group', { name: 'Mức tự trị' }).getByRole('button', { name: 'Gợi ý' }).click();
    await dlg.getByRole('button', { name: 'Lưu thay đổi' }).click();
    const pin = page.getByRole('dialog', { name: PIN_DIALOG });
    await expect(pin).toBeVisible();
    await pin.getByRole('button', { name: 'Huỷ' }).click();
    await expect(pin).toBeHidden();
    await expect(dlg.getByText('Đã huỷ — thao tác cần mã PIN.')).toBeVisible();
    await dlg.getByRole('button', { name: 'Huỷ' }).click();
    const card = page.getByRole('listitem', { name: AGENT, exact: true });
    await expect(card.locator('.ag-autonomy')).toHaveText('Soạn sẵn chờ duyệt');
  });

  test('Thêm tài khoản CLI: hỏi PIN rồi hiện link đăng nhập', async ({ page }) => {
    await page.goto('/connections#brain');
    const card = page.getByTestId('cli-card-claude_code_cli');
    await expect(card).toBeVisible();
    await expect(card.getByText('Đăng nhập / thêm tài khoản cần mã PIN')).toBeVisible();
    await card.getByRole('button', { name: 'Đăng nhập Claude' }).click();
    await enterOwnerPin(page);
    await expect(card.getByRole('link', { name: /Mở trang đăng nhập Claude/ })).toHaveAttribute('href', /^https:\/\/claude\.com\//);
  });

  test('/guide/9 sau Hoàn tất: không đổi mức thì không hỏi PIN', async ({ page }) => {
    await page.goto('/guide/9');
    await expect(page.getByRole('heading', { name: 'Tự trị & ranh giới' })).toBeVisible();
    await expect(page.getByTestId('step9-agent')).toContainText('Trợ lý thương mại');
    await expect(page.getByRole('radio', { name: 'Soạn sẵn chờ duyệt' })).toHaveAttribute('aria-checked', 'true');
    await expect(page.getByText('Sau Hoàn tất, đổi mức tự trị cần mã PIN')).toHaveCount(0);
    const req = page.waitForResponse((r) => r.url().includes('/setup/steps/9') && r.request().method() === 'PUT' && r.ok());
    await page.getByRole('button', { name: 'Tiếp tục', exact: true }).click();
    await req;
    await expect(page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' })).toHaveCount(0);
    await expect(page).toHaveURL(/\/guide$/);
  });

  test('/guide/9 sau Hoàn tất: đổi mức thì hỏi PIN rồi lưu', async ({ page }) => {
    await page.goto('/guide/9');
    await expect(page.getByRole('heading', { name: 'Tự trị & ranh giới' })).toBeVisible();
    await expect(page.getByTestId('step9-agent')).toBeVisible();
    await page.getByRole('radio', { name: 'Gợi ý' }).click();
    await expect(page.getByText('Sau Hoàn tất, đổi mức tự trị cần mã PIN')).toBeVisible();
    const req = page.waitForResponse((r) => r.url().includes('/setup/steps/9') && r.request().method() === 'PUT' && r.ok());
    await page.getByRole('button', { name: 'Tiếp tục', exact: true }).click();
    await enterOwnerPin(page);
    await req;
    await expect(page).toHaveURL(/\/guide$/);
  });
});
