/**
 * v0.1.49 (F-8, QD-16) — Bản tin Gen có Lịch hôm nay / Mail cần trả lời / Việc Google đang mở từ Gen-hub (chỉ đọc); Kết nối › Gen-hub
 * ghi quyền đọc cần tick + Kiểm tra báo thiếu; Trợ giúp "Gen đọc được gì từ Gen-hub" (mock: `npm run dev:mock`).
 */
import { expect, test, type Page } from '@playwright/test';
import { OWNER, apiCall, loginAsOwner, p3Hook, resetMock } from './support';

type HubMode = 'ok' | 'missing' | 'breaker' | 'error' | 'off';

function panel(page: Page) {
  return page.getByRole('complementary', { name: /Gen — trợ lý quản trị/ });
}

async function enterPin(page: Page) {
  const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
  await expect(dlg).toBeVisible();
  await page.getByLabel('Mã PIN — chữ số 1/6').click();
  await page.keyboard.type(OWNER.pin);
  await expect(dlg).toBeHidden();
}

async function noObjectText(page: Page) {
  expect(await page.locator('body').innerText()).not.toContain('[object Object]');
}

/** Worker Bản tin tới giờ (mock) rồi mở đúng hội thoại như khi bấm chuông (`/overview?gen=<id>`). */
async function openBriefing(page: Page, hub: HubMode) {
  const seeded = (await apiCall(page, 'POST', '/gen/__mock/briefing', { slot: 'sang', needs_api_key: false, hub })) as { conversation_id: string };
  expect(seeded.conversation_id).toMatch(/^[0-9a-f-]{36}$/);
  await page.goto(`/overview?gen=${seeded.conversation_id}`);
  await expect(panel(page)).toBeVisible();
  const msg = panel(page).locator('.gen-msg--briefing');
  await expect(msg).toContainText('Bản tin');
  return msg;
}

/** Không cuộn ngang: cả trang lẫn khung Gen (tấm phủ trên điện thoại). */
async function expectNoHorizontalScroll(page: Page) {
  const over = await page.evaluate(() => {
    const doc = document.documentElement;
    const list = document.querySelector('.gen-panel__list');
    return { page: doc.scrollWidth - doc.clientWidth, list: list ? list.scrollWidth - list.clientWidth : 0 };
  });
  expect(over.page).toBeLessThanOrEqual(1);
  expect(over.list).toBeLessThanOrEqual(1);
}

test.describe('v0.1.49 — Bản tin Gen đọc lịch / mail / việc qua Gen-hub', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  test('hub=ok: khung Gen hiện 3 mục với dòng lịch / mail / việc', async ({ page }) => {
    await loginAsOwner(page);
    const msg = await openBriefing(page, 'ok');
    const box = msg.getByTestId('briefing-hub-sections');
    await expect(box).toBeVisible();
    await expect(box.getByRole('heading', { name: 'Lịch hôm nay (2)' })).toBeVisible();
    await expect(box.getByRole('heading', { name: 'Mail cần trả lời (3)' })).toBeVisible();
    await expect(box.getByRole('heading', { name: 'Việc Google đang mở (1)' })).toBeVisible();
    await expect(box).toContainText('09:00 · Họp với nhà cung cấp ván MDF');
    await expect(box).toContainText('Anh Bảo — Báo giá ván MDF E1 17mm');
    await expect(box).toContainText('Gọi nhà cung cấp keo dán');
    // Mục nội bộ vẫn như cũ (bước say), không bị lặp trong khối Gen-hub.
    await expect(msg).toContainText('Việc tới hạn hôm nay (2)');
    await expect(box).not.toContainText('Việc tới hạn hôm nay');
    await expect(msg).not.toContainText('tick thêm quyền');
    await noObjectText(page);
  });

  test('hub=missing: không có thẻ "Mail cần trả lời"; có dòng "tick thêm quyền" và nút "Mở thẻ Gen-hub" làm sáng thẻ Gen-hub ở /connections', async ({ page }) => {
    await loginAsOwner(page);
    const msg = await openBriefing(page, 'missing');
    await expect(msg).toContainText('Bản tin chưa có mail cần trả lời: vào Gen-hub tick thêm quyền đọc mail');
    await expect(msg.getByRole('heading', { name: /Mail cần trả lời/ })).toHaveCount(0);
    await expect(msg.getByRole('heading', { name: 'Lịch hôm nay (2)' })).toBeVisible();
    await expect(msg.getByRole('heading', { name: 'Việc Google đang mở (1)' })).toBeVisible();
    // Thẻ Gen-hub đứng TRƯỚC lời nhắc + nút (không nằm dưới nút).
    const box = msg.getByTestId('briefing-hub-sections');
    const btn = msg.getByRole('button', { name: 'Mở thẻ Gen-hub' });
    const [boxY, btnY] = await Promise.all([box.boundingBox(), btn.boundingBox()]);
    expect(boxY && btnY ? boxY.y < btnY.y : false).toBe(true);
    await btn.click();
    await expect(page).toHaveURL(/\/connections/);
    await expect(page.getByRole('region', { name: 'Gen-hub' })).toBeVisible();
    await expect(page.getByText('Thẻ Gen-hub: sau khi tick thêm quyền đọc trong Gen-hub, bấm Kiểm tra ở đây.')).toBeVisible();
    await noObjectText(page);
  });

  test('hub=breaker: "Gen-hub tạm không trả lời" + mở "Chi tiết kỹ thuật" thấy mã; hub=error: lịch lỗi, mail còn đọc được', async ({ page }) => {
    await loginAsOwner(page);
    const msg = await openBriefing(page, 'breaker');
    const box = msg.getByTestId('briefing-hub-sections');
    await expect(box.getByText('Gen-hub tạm không trả lời — bản tin sau Gen thử lại.').first()).toBeVisible();
    const detail = box.locator('details.tech-detail').first();
    await expect(detail.locator('code')).toBeHidden();
    await detail.getByText('Chi tiết kỹ thuật').click();
    await expect(detail.locator('code')).toContainText('HUB_BREAKER_OPEN');
    await noObjectText(page);

    // Hội thoại mới: lỗi một mục (lịch) không làm mất các mục còn lại.
    await panel(page).getByRole('button', { name: 'Đóng khung Gen' }).click();
    const msg2 = await openBriefing(page, 'error');
    const box2 = msg2.getByTestId('briefing-hub-sections');
    await expect(box2.getByText('Chưa đọc được mục này lần này')).toHaveCount(1);
    await box2.getByText('Chi tiết kỹ thuật').click();
    await expect(box2.locator('code')).toContainText('HUB_UNAVAILABLE');
    await expect(box2.getByRole('heading', { name: 'Mail cần trả lời (3)' })).toBeVisible();
    await expect(box2.getByText('Không có việc Google đang mở.')).toBeVisible();
    await noObjectText(page);
  });

  test('Kết nối › Gen-hub: Kiểm tra ở chế độ thiếu quyền ⇒ xanh nhưng "Còn thiếu quyền: đọc lịch, đọc mail"; bộ ngắt mở ⇒ dải báo; Việc Sếp cần làm có dòng phụ', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/connections');
    const card = page.getByRole('region', { name: 'Gen-hub' });
    const scopes = card.getByTestId('hub-scopes');
    await expect(scopes).toContainText('Quyền đọc thêm (tuỳ chọn)');
    await expect(scopes.getByText('Chưa kiểm')).toHaveCount(4);
    await expect(scopes).toContainText('Gen chỉ đọc — không gửi mail, không tạo lịch hay tệp.');
    await expect(card).toContainText('KHÔNG bật quyền ghi');

    // Token chứa "thieu" ⇒ mock giả lập token chưa tick quyền đọc lịch + mail (Kiểm tra vẫn xanh).
    await card.getByLabel('Địa chỉ Gen-hub').fill('https://hub.example.test/mcp');
    await card.getByLabel('Token Gen-hub').fill('ghtok_thieu_quyen_123456');
    await card.getByRole('button', { name: 'Lưu & kiểm tra' }).click();
    await enterPin(page);
    await expect(card).toContainText('Đã nối Kho');
    await expect(card).toContainText('Còn thiếu quyền: đọc lịch, đọc mail — vào Gen-hub tick thêm cho token của Gen-Harness rồi bấm Kiểm tra lại.');
    await expect(scopes.getByText('Chưa', { exact: true })).toHaveCount(2);
    await expect(scopes.getByText('Có', { exact: true })).toHaveCount(2);
    await expect(card).toContainText('Đang nối'); // thiếu quyền đọc KHÔNG làm Kiểm tra đỏ
    await noObjectText(page);

    // Việc Sếp cần làm: hàng Gen-hub đạt, kèm dòng phụ quyền đọc thêm (không bắt buộc).
    await page.goto('/guide/viec-sep');
    const hub = page.getByRole('region', { name: 'Nối Gen-hub', exact: true });
    await hub.getByRole('button', { name: 'Kiểm tra' }).click();
    await expect(hub.getByText(/^Đạt · /)).toBeVisible();
    await expect(hub.getByTestId('boss-hub-scopes')).toContainText('Quyền đọc thêm (không bắt buộc): Lịch ✗ · Mail ✗ · Việc ✓ · Drive ✓');
    await expect(hub).toContainText('tick thêm quyền đọc');
    await expect(page.getByText(/Đã đạt \d\/\d+ dòng bắt buộc/)).toBeVisible();

    // Tick đủ quyền rồi Kiểm tra lại ⇒ "Đủ quyền"; bộ ngắt mở ⇒ dải "Gen-hub tạm không trả lời".
    await p3Hook(page.request, 'mcp', 'hubSim', { scopes: 'full', breaker: true });
    await page.goto('/connections');
    await expect(card.getByTestId('hub-breaker')).toContainText('Gen-hub tạm không trả lời — Gen tự thử lại sau 1 phút.');
    await card.getByRole('button', { name: 'Kiểm tra', exact: true }).click();
    await expect(card).toContainText('Đủ quyền đọc lịch, mail, việc và Drive.');
    await expect(scopes.getByText('Có', { exact: true })).toHaveCount(4);
    await expect(card.getByTestId('hub-breaker')).toHaveCount(0); // Kiểm tra xanh đóng bộ ngắt
    await noObjectText(page);
  });

  test('/help có thẻ "Gen đọc được gì từ Gen-hub" (chỉ đọc, che dữ liệu, chỉ Owner)', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/help');
    const card = page.getByTestId('help-genhub-reads');
    await expect(card.getByText('Gen đọc được gì từ Gen-hub')).toBeVisible();
    await expect(card).toContainText('Gen đọc: Kho dữ liệu, lịch, mail'); // v0.1.50: hết "chỉ ĐỌC" — Gen ghi Kho khi Sếp xác nhận + PIN
    await expect(card).toContainText('Gen KHÔNG gửi mail');
    await expect(card).toContainText('che trước khi gửi cho AI');
    await expect(card).toContainText('tạm dừng gọi 1 phút');
    await expect(card).toContainText('Chỉ Sếp (Owner) dùng được');
    await noObjectText(page);
  });

  test('viewport 390px: bản tin Gen-hub, thẻ Gen-hub, Trợ giúp không cuộn ngang', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await loginAsOwner(page);
    const msg = await openBriefing(page, 'breaker');
    await expect(msg.getByTestId('briefing-hub-sections')).toBeVisible();
    await msg.getByText('Chi tiết kỹ thuật').first().click();
    await expectNoHorizontalScroll(page);

    await panel(page).getByRole('button', { name: 'Đóng khung Gen' }).click();
    const ok = await openBriefing(page, 'ok');
    await expect(ok.getByTestId('briefing-hub-sections')).toBeVisible();
    await expectNoHorizontalScroll(page);
    await panel(page).getByRole('button', { name: 'Đóng khung Gen' }).click();

    await p3Hook(page.request, 'mcp', 'hubSim', { scopes: 'missing', breaker: true });
    await page.goto('/connections');
    await expect(page.getByRole('region', { name: 'Gen-hub' }).getByTestId('hub-scopes')).toBeVisible();
    await expectNoHorizontalScroll(page);

    await page.goto('/help');
    await expect(page.getByTestId('help-genhub-reads')).toBeVisible();
    await expectNoHorizontalScroll(page);
  });
});
