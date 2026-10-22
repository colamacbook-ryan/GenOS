import { expect, test, type Locator, type Page } from '@playwright/test';
import { OWNER, apiCall, loginAsOwner, resetMock } from './support';

/**
 * v0.1.47 (F-79/F-85) — mock, tất định (không có Facebook thật): Gen đề xuất trả lời bình luận → Xác nhận và gửi (PIN) → theo dõi việc
 * gửi (chờ → đang gửi → đã gửi) → Xem ảnh chụp; Dừng tất cả chặn cả gửi; cổng khoá → trang cảnh báo → đồng ý → thẻ dùng được;
 * vượt giới hạn gửi/ngày → câu báo giới hạn. Việc gửi do hook mock `advanceWrite` chuyển bước (không hẹn giờ ngẫu nhiên).
 */

const SOCIAL_HOOK = '/api/v1/__mock/p3/social';
const CARD = 'Đề xuất: Trả lời bình luận Facebook';

async function hook(page: Page, name: string, data: unknown = {}) {
  const res = await page.request.post(`${SOCIAL_HOOK}/${name}`, { data });
  if (!res.ok()) throw new Error(`hook ${name} failed: ${res.status()}`);
  return res.json();
}

async function enterPin(page: Page) {
  const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
  await expect(dlg).toBeVisible();
  await page.getByLabel('Mã PIN — chữ số 1/6').click();
  await page.keyboard.type(OWNER.pin);
  await expect(dlg).toBeHidden();
}

/** PIN có thể còn hiệu lực từ thao tác trước — chỉ nhập khi hộp PIN hiện. */
async function enterPinIfAsked(page: Page) {
  const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
  // `isVisible` KHÔNG chờ (bỏ qua timeout) → hộp PIN hiện sau vòng 423 bị bỏ sót (test chập chờn). Chờ thật tối đa 2,5 giây.
  const shown = await dlg
    .waitFor({ state: 'visible', timeout: 2500 })
    .then(() => true)
    .catch(() => false);
  if (shown) await enterPin(page);
}

async function openGen(page: Page): Promise<Locator> {
  const panel = page.getByRole('complementary', { name: /Gen — trợ lý quản trị/ });
  if (!(await panel.isVisible())) await page.getByRole('button', { name: /Hỏi Gen/ }).click();
  await expect(panel).toBeVisible();
  return panel;
}

async function ask(page: Page, question: string): Promise<Locator> {
  const panel = await openGen(page);
  await panel.getByLabel('Câu hỏi cho Gen').fill(question);
  await panel.getByRole('button', { name: 'Gửi', exact: true }).click();
  const card = panel.getByRole('group', { name: CARD }).last();
  await expect(card).toBeVisible();
  return card;
}

async function accountId(page: Page): Promise<string> {
  const list = (await apiCall(page, 'GET', '/social/accounts')) as { items: Array<{ id: string }> };
  return list.items[0].id;
}

test.beforeEach(async ({ page }) => {
  await resetMock(page.request, 'finished');
  await page.setViewportSize({ width: 1440, height: 900 });
  await hook(page, 'seedActive', { label: 'Facebook của Sếp' });
  await loginAsOwner(page);
});

test('Gen đề xuất trả lời bình luận → Xác nhận và gửi (PIN) → đang chờ → đang gửi → Đã gửi → Xem ảnh chụp', async ({ page }) => {
  test.setTimeout(90_000);
  await hook(page, 'setGate', { consent: true });
  await page.goto('/overview');
  const card = await ask(page, 'trả lời bình luận của chị Lan');
  await expect(card).toContainText('Facebook của Sếp');
  await expect(card).toContainText('Bình luận của chị Lan');
  await expect(card).toContainText('Cảm ơn bạn!');
  await expect(card).toContainText('Cần mã PIN');
  await expect(card).toContainText('Bấm Xác nhận là GỬI NGAY lên Facebook của Sếp (cần mã PIN). Hệ thống không tự thu hồi được.');

  await card.getByRole('button', { name: 'Xác nhận và gửi' }).click();
  await enterPin(page);
  const status = card.getByTestId('gen-write-status');
  await expect(status).toContainText('Đang chờ trình duyệt…');
  await expect(card.getByRole('button', { name: 'Xác nhận và gửi' })).toHaveCount(0);

  await hook(page, 'advanceWrite'); // queued → running
  await expect(status).toContainText('Đang gửi trên Facebook…', { timeout: 10_000 });
  await hook(page, 'advanceWrite'); // running → done (+ ảnh chụp)
  await expect(status).toContainText('Đã gửi', { timeout: 10_000 });

  await status.getByRole('button', { name: 'Xem ảnh chụp' }).click();
  const img = page.getByRole('img', { name: 'Ảnh chụp bằng chứng lần gửi' });
  await expect(img).toBeVisible();
  await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth > 0)).toBe(true);
  await page.getByRole('dialog', { name: 'Ảnh chụp bằng chứng lần gửi' }).getByRole('button', { name: 'Đóng', exact: true }).last().click();

  // Trang Tài khoản mạng xã hội: "Đã dùng 1/10 lượt gửi (24 giờ qua)" + dòng trong "Lần gửi gần đây" có "Xem ảnh chụp".
  await page.goto('/social');
  await expect(page.getByTestId('social-write-gate-state')).toContainText('Mở');
  await expect(page.getByText('Đã dùng 1/10 lượt gửi (24 giờ qua)')).toBeVisible();
  const recent = page.getByRole('list', { name: 'Lần gửi gần đây' });
  await expect(recent.getByRole('listitem')).toHaveCount(1);
  await expect(recent).toContainText('Trả lời bình luận');
  await expect(recent.getByRole('button', { name: 'Xem ảnh chụp' })).toBeVisible();
});

test('Dừng tất cả rồi xác nhận đề xuất khác → báo đang dừng, không có việc gửi nào', async ({ page }) => {
  test.setTimeout(90_000);
  await hook(page, 'setGate', { consent: true });
  await page.goto('/social');
  await page.getByRole('button', { name: 'Dừng tất cả' }).click();
  await page.getByRole('button', { name: 'Dừng ngay' }).click();
  await expect(page.getByTestId('social-halted')).toBeVisible();

  const card = await ask(page, 'trả lời bình luận của chị Lan');
  await card.getByRole('button', { name: 'Xác nhận và gửi' }).click();
  await enterPinIfAsked(page);
  const alert = card.getByRole('alert');
  await expect(alert).toContainText('Đang dừng tất cả việc trình duyệt');
  await expect(alert.getByText('Chi tiết kỹ thuật')).toBeVisible();
  await expect(card.getByTestId('gen-write-status')).toHaveCount(0);
  const writes = (await apiCall(page, 'GET', '/social/writes')) as { items: unknown[] };
  expect(writes.items).toHaveLength(0);
});

test('cổng khoá → trang cảnh báo → "Tôi hiểu rủi ro và đồng ý" (PIN) → quay lại thẻ, Xác nhận dùng được', async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto('/overview');
  const card = await ask(page, 'trả lời bình luận của chị Lan');
  await expect(card.getByRole('button', { name: 'Xác nhận và gửi' })).toBeDisabled();
  await card.getByRole('button', { name: 'Đọc cảnh báo & đồng ý' }).click();

  await expect(page).toHaveURL(/\/social\/ghi-facebook$/);
  await expect(page.getByRole('heading', { level: 2, name: 'Gửi trả lời & tin nhắn Facebook — cảnh báo rủi ro' })).toBeVisible();
  await expect(page.getByTestId('write-sandbox')).toContainText('Chưa bật');
  await expect(page.getByRole('list', { name: 'Rủi ro khi gửi lên Facebook' }).getByRole('listitem').first()).toBeVisible();
  await page.getByRole('button', { name: 'Tôi hiểu rủi ro và đồng ý' }).click();
  await enterPinIfAsked(page);
  await expect(page.getByTestId('write-consent-info')).toContainText('Sếp đã đồng ý lúc');
  await expect(page.getByTestId('write-consent-info')).toContainText('phiên bản cảnh báo 2026-10-03');
  await expect(page.getByRole('button', { name: 'Rút lại đồng ý' })).toBeVisible();

  await page.getByRole('button', { name: 'Quay lại' }).click();
  await expect(page).toHaveURL(/\/overview$/);
  const back = (await openGen(page)).getByRole('group', { name: CARD }).last();
  const confirm = back.getByRole('button', { name: 'Xác nhận và gửi' });
  await expect(confirm).toBeEnabled();
  await expect(back.getByRole('button', { name: 'Đọc cảnh báo & đồng ý' })).toHaveCount(0);
  await confirm.click();
  await enterPinIfAsked(page);
  await expect(back.getByTestId('gen-write-status')).toContainText('Đang chờ trình duyệt…');
});

test('vượt giới hạn gửi/ngày → câu báo giới hạn (không tạo việc thứ hai)', async ({ page }) => {
  test.setTimeout(90_000);
  await hook(page, 'setGate', { consent: true });
  const id = await accountId(page);
  await apiCall(page, 'PATCH', `/social/accounts/${id}`, { daily_write_limit: 1 });
  await page.goto('/overview');

  const first = await ask(page, 'trả lời bình luận của chị Lan');
  await first.getByRole('button', { name: 'Xác nhận và gửi' }).click();
  await enterPinIfAsked(page);
  await expect(first.getByTestId('gen-write-status')).toContainText('Đang chờ trình duyệt…');
  await hook(page, 'advanceWrite', { to: 'running' });
  await hook(page, 'advanceWrite', { to: 'done' });
  await expect(first.getByTestId('gen-write-status')).toContainText('Đã gửi', { timeout: 10_000 });

  const second = await ask(page, 'trả lời bình luận của chị Lan lần nữa');
  await second.getByRole('button', { name: 'Xác nhận và gửi' }).click();
  await enterPinIfAsked(page);
  const alert = second.getByRole('alert');
  // Câu NGUYÊN VĂN của API (mock chép đúng title thật).
  await expect(alert).toContainText('Đã gửi 1 lượt trong 24 giờ (giới hạn để giảm rủi ro khoá tài khoản) — thử lại sau hoặc nâng Giới hạn gửi/ngày');
  await expect(alert).toContainText('SOCIAL_WRITE_LIMIT');
  const writes = (await apiCall(page, 'GET', '/social/writes')) as { items: unknown[] };
  expect(writes.items).toHaveLength(1);
});

test('gửi xong mà không chụp được ảnh → câu thân thiện + "Chi tiết kỹ thuật"; không còn chip "Cần mã PIN"', async ({ page }) => {
  test.setTimeout(90_000);
  await hook(page, 'setGate', { consent: true });
  await page.goto('/overview');
  const card = await ask(page, 'trả lời bình luận của chị Lan');
  await card.getByRole('button', { name: 'Xác nhận và gửi' }).click();
  await enterPinIfAsked(page);
  const status = card.getByTestId('gen-write-status');
  await expect(status).toContainText('Đang chờ trình duyệt…');
  await expect(card).not.toContainText('Cần mã PIN');
  await hook(page, 'advanceWrite', { to: 'running' });
  await hook(page, 'advanceWrite', { to: 'done', proof: false });
  await expect(status).toContainText('Đã gửi nhưng không chụp được ảnh bằng chứng — mở Facebook để kiểm tra.', { timeout: 10_000 });
  await expect(status.getByRole('button', { name: 'Xem ảnh chụp' })).toHaveCount(0);
  await expect(status.getByText('Mã lỗi PROOF_MISSING')).toBeHidden();
  await status.getByText('Chi tiết kỹ thuật').click();
  await expect(status.getByText('Mã lỗi PROOF_MISSING')).toBeVisible();
  await page.goto('/social');
  const recent = page.getByRole('list', { name: 'Lần gửi gần đây' });
  await expect(recent).toContainText('không chụp được ảnh bằng chứng');
});
