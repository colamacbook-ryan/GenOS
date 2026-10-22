import { expect, test, type Page } from '@playwright/test';
import { OWNER, loginAsOwner, p3Hook, resetMock } from './support';

/**
 * v0.1.39 (F-74) — "Việc Sếp cần làm" (mock, tất định): thẻ ở đầu Hướng dẫn thiết lập → trang các dòng; Gen-hub nhập
 * token → Kiểm tra (PIN một lần) → "Đạt" ngay cạnh dòng; Facebook "Đọc ngay" → "Đang chạy…" → "Đạt"; Google gọi thử;
 * tải lại trang kết quả vẫn còn (đọc từ API, không phải bộ nhớ trình duyệt).
 *
 * v0.1.55 (G2): CHỈ dòng 0 "Có ít nhất 1 nguồn AI chạy được" bắt buộc (x/1); mọi dòng kết nối khác "Không bắt buộc" và không
 * đổi số đếm; Google bỏ yêu cầu đổi qua lại hai tài khoản (chỉ Đăng nhập + Gọi thử).
 */

const HUB_TOKEN = 'ghtok_E2E_dung_0123456789';

async function enterPin(page: Page) {
  const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
  await expect(dlg).toBeVisible();
  await page.getByLabel('Mã PIN — chữ số 1/6').click();
  await page.keyboard.type(OWNER.pin);
  await expect(dlg).toBeHidden();
}

const row = (page: Page, name: string) => page.getByRole('region', { name, exact: true });

test.describe('Việc Sếp cần làm (v0.1.39)', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  test('Owner: thẻ ở Hướng dẫn thiết lập → 10 dòng; nguồn AI Kiểm tra → 1/1; Gen-hub PIN một lần → Đạt; Facebook Đọc ngay → Đang chạy… → Đạt (không đổi số bắt buộc)', async ({ page }) => {
    test.setTimeout(90_000);
    await p3Hook(page.request, 'social', 'seedActive', { label: 'Facebook của Sếp' });
    let pinDialogs = 0;
    page.on('response', (res) => {
      if (res.url().includes('/api/v1/') && res.status() === 423) pinDialogs += 1;
    });
    await loginAsOwner(page);
    await page.goto('/guide');
    await expect(page.getByRole('heading', { level: 2, name: 'Hướng dẫn thiết lập' })).toBeVisible();
    const card = page.getByRole('link', { name: /Việc Sếp cần làm — nguồn AI chạy thật/ });
    await expect(card).toContainText('Đã đạt 0/1 dòng bắt buộc');
    await card.click();
    await expect(page).toHaveURL(/\/guide\/viec-sep$/);
    await expect(page.locator('.boss-row')).toHaveCount(10);
    await expect(row(page, 'Jev')).toContainText('Không bắt buộc');
    await expect(row(page, 'Có ít nhất 1 nguồn AI chạy được')).not.toContainText('Không bắt buộc');
    await expect(page.getByText('Kết quả được lưu lại — Claude tự đọc, Sếp không cần chụp màn hình.')).toBeVisible();

    // 0. Nguồn AI (dòng bắt buộc duy nhất): Kiểm tra — không hỏi PIN — → Đạt, thẻ đếm 1/1.
    const ai = row(page, 'Có ít nhất 1 nguồn AI chạy được');
    await expect(ai.getByTestId('boss-result')).toContainText('Chưa kiểm');
    await ai.getByRole('button', { name: 'Kiểm tra', exact: true }).click();
    await expect(ai.getByTestId('boss-result')).toContainText(/Đạt/);
    await expect(page.getByText('Đã đạt đủ 1 dòng bắt buộc')).toBeVisible();
    expect(pinDialogs).toBe(0);

    // 1. Gen-hub: địa chỉ https công khai → công tắc bật sẵn; Kiểm tra = lưu (PIN) rồi kiểm, không hỏi PIN lần hai.
    const hub = row(page, 'Nối Gen-hub');
    await expect(hub).toContainText('Chưa kiểm');
    await hub.getByLabel('Địa chỉ Gen-hub').fill('https://hub.example.test/mcp');
    await hub.getByLabel('Token', { exact: true }).fill(HUB_TOKEN);
    await expect(hub.getByRole('switch', { name: 'Cho phép Gen-hub ở mạng công cộng' })).toHaveAttribute('aria-checked', 'true');
    await expect(hub.getByRole('note')).toContainText('Đã bật sẵn vì địa chỉ là https công khai');
    await hub.getByRole('button', { name: 'Kiểm tra', exact: true }).click();
    await enterPin(page);
    await expect(hub.getByTestId('boss-result')).toContainText(/Đạt · \d\d:\d\d \d\d\/\d\d/);
    expect(pinDialogs).toBe(1);
    await expect(hub.getByLabel('Token mới (bỏ trống để giữ)')).toHaveValue('');
    expect(await page.content()).not.toContain(HUB_TOKEN);

    // 2. Facebook: Đọc ngay → Đang chạy… → Đạt (thăm lại 3 giây).
    const fb = row(page, 'Kết nối Facebook');
    await fb.getByRole('button', { name: 'Đọc ngay' }).click();
    await expect(fb.getByTestId('boss-result')).toContainText('Đang chạy…');
    // Đang chạy → nút tắt (bấm lại sẽ chỉ ra SOCIAL_BUSY và che mất lượt đang chạy).
    await expect(fb.getByRole('button', { name: 'Đọc ngay' })).toBeDisabled();
    await expect(fb.getByTestId('boss-result')).toContainText('Đạt ·', { timeout: 15_000 });
    await expect(fb.getByRole('button', { name: 'Đọc lại' })).toBeEnabled();
    // Gen-hub và Facebook không bắt buộc ⇒ số đếm vẫn đủ 1/1 (chỉ dòng nguồn AI tính).
    await expect(page.getByText('Đã đạt đủ 1 dòng bắt buộc')).toBeVisible();

    // Hướng dẫn thiết lập: việc "Nối Gen-hub" và "Kết nối Facebook" tự hiện Đã xong.
    await page.goto('/guide');
    await expect(page.locator('[data-gen-target="guide.item:14"]')).toContainText('Đã xong');
    await expect(page.locator('[data-gen-target="guide.item:13"]')).toContainText('Đã xong');
    await expect(page.getByRole('link', { name: /Việc Sếp cần làm/ })).toContainText('Đã đạt 1/1 dòng bắt buộc');
  });

  test('Google: Gọi thử báo binh@ (không còn đòi đổi qua lại hai tài khoản) → Xong + nguồn AI tự đạt; tải lại vẫn còn kết quả', async ({ page }) => {
    test.setTimeout(90_000);
    await p3Hook(page.request, 'bossChecks', 'seedAgy');
    await loginAsOwner(page);
    await page.goto('/guide/viec-sep');
    const agy = row(page, 'Google (Antigravity)');
    const results = agy.getByTestId('boss-result');
    await expect(results).toHaveCount(2);
    await expect(agy.getByRole('button', { name: /Đổi sang/ })).toHaveCount(0);
    await expect(agy).toContainText('Không bắt buộc');
    await expect(agy.getByRole('link', { name: /Thêm \/ đổi tài khoản ở Thiết lập nguồn AI/ })).toHaveAttribute('href', '/setup?buoc=nguon-ai');

    await agy.getByRole('button', { name: 'Gọi thử' }).click();
    await expect(results.nth(1)).toContainText('Đạt · đang dùng binh@genesis.vn');
    await expect(agy).toContainText('Xong');
    // Google gọi thử đạt ⇒ dòng nguồn AI tự đạt (máy chủ suy ra), đủ 1/1.
    await expect(page.getByText('Đã đạt đủ 1 dòng bắt buộc')).toBeVisible();

    // Tải lại: kết quả đọc lại từ API — máy chủ chỉ lưu email ĐÃ CHE (email đầy đủ chỉ có trong phản hồi lúc bấm).
    await page.reload();
    const again = row(page, 'Google (Antigravity)');
    await expect(again.getByTestId('boss-result').nth(1)).toContainText('Đạt · đang dùng b***@genesis.vn');
    await expect(again).toContainText('Xong');
    await expect(page.getByText('Đã đạt đủ 1 dòng bắt buộc')).toBeVisible();
  });

  test('Claude: đã đăng nhập từ trước (chưa có bản đăng nhập) → Gọi thử đạt là dòng 4 Xong; tải lại vẫn còn', async ({ page }) => {
    await p3Hook(page.request, 'bossChecks', 'seedClaude');
    await loginAsOwner(page);
    await page.goto('/guide/viec-sep');
    const cl = row(page, 'Claude Code CLI');
    const results = cl.getByTestId('boss-result');
    await expect(results.nth(0)).toContainText('Đã có phiên (đăng nhập trước đây) — bấm Gọi thử để xác nhận');
    await expect(cl.getByRole('button', { name: /Đăng nhập/ })).toHaveCount(0);
    await cl.getByRole('button', { name: 'Gọi thử' }).click();
    await expect(results.nth(1)).toContainText('Đạt · đang dùng a@example.test');
    await expect(results.nth(0)).toContainText('Đạt · phiên có sẵn, đã xác nhận bằng Gọi thử');
    await expect(cl).toContainText('Xong');
    await page.reload();
    const again = row(page, 'Claude Code CLI');
    await expect(again).toContainText('Xong');
    await expect(again.getByTestId('boss-result').nth(0)).toContainText('phiên có sẵn');
  });
  test('Telegram (dòng 6, v0.1.44): chưa nối → Gửi thử báo cách làm + Mở hướng dẫn tới Kết nối › Telegram; đã nối → Đạt, không đổi số bắt buộc (0/1)', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/guide/viec-sep');
    const tg = row(page, 'Telegram (báo động & bản tin)');
    await expect(tg.getByTestId('boss-result')).toContainText('Chưa kiểm');
    await tg.getByRole('button', { name: 'Gửi thử' }).click();
    await expect(tg.getByTestId('boss-result')).toContainText('Lỗi · Chưa nối Telegram');
    await tg.getByTestId('boss-result').getByText('Chi tiết kỹ thuật').click();
    await expect(tg.getByTestId('boss-result')).toContainText('Mã lỗi TELEGRAM_NOT_CONFIGURED');
    await expect(tg.getByRole('link', { name: /Mở hướng dẫn/ })).toHaveAttribute('href', '/connections#telegram');

    await p3Hook(page.request, 'telegram', 'seed', { configured: true });
    await tg.getByRole('button', { name: 'Gửi thử' }).click();
    await expect(tg.getByTestId('boss-result')).toContainText('Đạt · đã gửi tới @gen_harness_sep_bot → chat •••4321');
    await expect(tg).toContainText('Xong');
    await expect(page.getByText('Đã đạt 0/1 dòng bắt buộc')).toBeVisible();   // Telegram không bắt buộc ⇒ không tính

    await tg.getByRole('link', { name: /Mở hướng dẫn/ }).click();
    await expect(page).toHaveURL(/\/connections#telegram$/);
    await expect(page.getByRole('region', { name: 'Telegram — báo động & bản tin' })).toBeVisible();
  });

  test('Facebook trả lời (dòng 8, v0.1.47): không bắt buộc, 4 bước, nút mở Tài khoản mạng xã hội; đạt → Đạt, vẫn 0/1 bắt buộc', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/guide/viec-sep');
    const fr = row(page, 'Facebook trả lời');
    await expect(fr.getByTestId('boss-result')).toContainText('Chưa kiểm');
    await expect(fr).toContainText('Không bắt buộc');
    await expect(fr.getByRole('listitem')).toHaveCount(4);
    await expect(fr.getByRole('button')).toHaveCount(0);
    await expect(page.getByText('Đã đạt 0/1 dòng bắt buộc')).toBeVisible();
    await fr.getByRole('link', { name: /Mở Tài khoản mạng xã hội/ }).click();
    await expect(page).toHaveURL(/\/social$/);

    await p3Hook(page.request, 'bossChecks', 'seedFacebookReply');
    await page.goto('/guide/viec-sep');
    const again = row(page, 'Facebook trả lời');
    await expect(again.getByTestId('boss-result')).toContainText(/Đạt · \d\d:\d\d \d\d\/\d\d/);
    await expect(again).toContainText('Xong');
    await expect(page.getByText('Đã đạt 0/1 dòng bắt buộc')).toBeVisible();   // không bắt buộc ⇒ không tính
  });
});
