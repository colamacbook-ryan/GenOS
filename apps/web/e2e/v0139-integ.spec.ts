import { expect, test, type Page } from '@playwright/test';
import { MANAGER, OWNER, apiCall, loginAs, loginAsOwner, p3Hook, resetMock } from './support';

/**
 * v0.1.39 — nghiệm thu sau khi gộp 3 gói (mock, tất định): lối vào Mạng xã hội (thanh bên, thẻ Facebook, Gen mở
 * /social), Hướng dẫn thiết lập (9 việc, không còn tên cũ), "Việc Sếp cần làm" (Gen-hub lỗi token, Claude Code, Jev
 * một lần + tải lại vẫn còn), thẻ Gen-hub (v0.1.42: ở /connections) chỉ đúng công tắc mạng công cộng, Tổng quan hết việc khi đã xong.
 * Phần Gen-hub đạt / Facebook / Google đổi qua lại nằm ở `boss-checks-v0139.spec.ts`.
 */

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

const row = (page: Page, name: string) => page.getByRole('region', { name, exact: true });
const nav = (page: Page) => page.getByRole('navigation', { name: 'Danh mục màn hình' });

test.describe('v0.1.39 · nghiệm thu sau gộp', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  // v0.1.42 (F-7): Hướng dẫn thiết lập vào Cài đặt + menu tài khoản, Facebook vào Kết nối — không còn mục riêng.
  test('Owner: lối vào Hướng dẫn thiết lập ở Cài đặt, Facebook ở Kết nối; Hỏi Gen "mở trang mạng xã hội" → /social', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/system');
    await expect(page.getByRole('link', { name: /Hướng dẫn thiết lập/ })).toHaveAttribute('href', '/guide');
    await page.goto('/connections');
    await expect(page.locator('[data-gen-target="system.channels.facebook"]').getByRole('link', { name: 'Mở Facebook' })).toHaveAttribute('href', '/social');
    await page.goto('/overview');
    await expect(nav(page).getByRole('link', { name: /Hướng dẫn thiết lập/ })).toHaveCount(0);
    await expect(nav(page).getByRole('link', { name: /Mạng xã hội/ })).toHaveCount(0);
    await noObjectText(page);

    const panel = page.getByRole('complementary', { name: /Gen — trợ lý quản trị/ });
    if (!(await panel.isVisible())) await page.getByRole('button', { name: /Hỏi Gen/ }).click();
    await panel.getByLabel('Câu hỏi cho Gen').fill('mở trang mạng xã hội');
    await panel.getByRole('button', { name: 'Gửi' }).click();
    await expect(page).toHaveURL(/\/social$/, { timeout: 15_000 });
    await expect(page.getByRole('heading', { level: 2, name: 'Tài khoản mạng xã hội' })).toBeVisible();
    await expect(panel).toContainText('Đã mở Tài khoản mạng xã hội');
    await noObjectText(page);
  });

  test('Quản lý: không thấy Hướng dẫn thiết lập và Mạng xã hội trên thanh bên', async ({ page }) => {
    await loginAs(page, MANAGER.email);
    await page.goto('/overview');
    await expect(nav(page).locator('.sb-group').first()).toBeVisible();
    await expect(nav(page).getByRole('link', { name: /Hướng dẫn thiết lập/ })).toHaveCount(0);
    await expect(nav(page).getByRole('link', { name: /Mạng xã hội/ })).toHaveCount(0);
  });

  test('/guide: 9 việc 01–09, link đúng; không còn "Hướng dẫn kết nối" ở Trợ giúp, Tổng quan, breadcrumb', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/guide');
    await expect(page.getByRole('heading', { level: 2, name: 'Hướng dẫn thiết lập' })).toBeVisible();
    const cards = page.locator('.guide-card');
    await expect(cards).toHaveCount(9);
    await expect(page.locator('.guide-card .guide-card__num')).toHaveText(['01', '02', '03', '04', '05', '06', '07', '08', '09']);
    const invite = page.locator('[data-gen-target="guide.item:10"]');
    await expect(invite).toContainText('Mời người trong đội');
    await expect(invite.locator('a[href="/team"]')).toHaveCount(1);
    await expect(page.locator('[data-gen-target="guide.item:13"]')).toContainText('Kết nối Facebook');
    await expect(page.locator('[data-gen-target="guide.item.do:13"]')).toHaveAttribute('href', '/social');
    await expect(page.locator('[data-gen-target="guide.item:14"]')).toContainText('Nối Gen-hub');
    await expect(page.locator('[data-gen-target="guide.item.do:14"]')).toHaveAttribute('href', '/connections#genhub');
    for (const path of ['/guide', '/help', '/overview', '/guide/viec-sep']) {
      await page.goto(path);
      await expect(page.locator('main, .screen').first()).toBeVisible();
      await page.waitForLoadState('networkidle');
      const text = await page.locator('body').innerText();
      expect(text, path).not.toContain('Hướng dẫn kết nối');
      expect(text, path).not.toContain('[object Object]');
    }
  });

  test('Gen-hub + Facebook xong → Đã xong ở /guide và biến khỏi "Việc thiết lập tiếp" ở Tổng quan', async ({ page }) => {
    test.setTimeout(90_000);
    await loginAsOwner(page);
    await page.goto('/overview');
    const follow = page.getByRole('region', { name: 'Việc thiết lập tiếp' });
    await expect(follow).toContainText('Kết nối Facebook');
    await expect(follow).toContainText('Nối Gen-hub');

    await p3Hook(page.request, 'social', 'importKeyChanged', { label: 'Facebook của Sếp' });
    await page.goto('/guide/viec-sep');
    const hub = row(page, 'Nối Gen-hub');
    await hub.getByLabel('Địa chỉ Gen-hub').fill('https://hub.example.test/mcp');
    await hub.getByLabel('Token', { exact: true }).fill('ghtok_E2E_dung_0123456789');
    await hub.getByRole('button', { name: 'Kiểm tra', exact: true }).click();
    await enterPin(page);
    await expect(hub.getByTestId('boss-result')).toContainText('Đạt ·');

    await page.goto('/guide');
    await expect(page.locator('[data-gen-target="guide.item:13"]')).toContainText('Đã xong');
    await expect(page.locator('[data-gen-target="guide.item:14"]')).toContainText('Đã xong');
    const followUp = page.waitForResponse((r) => r.url().includes('/api/v1/setup/follow-up') && r.ok());
    await page.goto('/overview');
    await followUp;
    await page.waitForLoadState('networkidle');
    // Hai việc đã xong biến khỏi thẻ (thẻ tự ẩn hẳn khi không còn việc nào để sau).
    await expect(page.locator('.ov-followup__title', { hasText: 'Kết nối Facebook' })).toHaveCount(0);
    await expect(page.locator('.ov-followup__title', { hasText: 'Nối Gen-hub' })).toHaveCount(0);
    await noObjectText(page);
  });

  test('Việc Sếp cần làm: token sai → Lỗi + câu thân thiện + Chi tiết kỹ thuật HUB_TOKEN_REJECTED; tiến độ vẫn 0/1', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/guide/viec-sep');
    await expect(page.locator('.boss-row')).toHaveCount(10); // v0.1.55: + dòng 0 "nguồn AI" (bắt buộc duy nhất); v0.1.47: + dòng 8 "Facebook trả lời"; v0.1.50: + dòng 9 "Gen ghi Kho" (đều không bắt buộc)
    await expect(page.getByText('Đã đạt 0/1 dòng bắt buộc')).toBeVisible();
    for (const name of ['Nối Gen-hub', 'Kết nối Facebook', 'Google (Antigravity)', 'Claude Code CLI', 'Jev']) {
      await expect(row(page, name)).toBeVisible();
    }
    await expect(row(page, 'Jev')).toContainText('Không bắt buộc');

    const hub = row(page, 'Nối Gen-hub');
    await hub.getByLabel('Địa chỉ Gen-hub').fill('https://hub.example.test/mcp');
    await expect(hub.getByRole('switch', { name: 'Cho phép Gen-hub ở mạng công cộng' })).toHaveAttribute('aria-checked', 'true');
    await expect(hub.getByRole('note')).toContainText('Đã bật sẵn vì địa chỉ là https công khai');
    await hub.getByLabel('Token', { exact: true }).fill('ghtok_token_sai_0123456789');
    await hub.getByRole('button', { name: 'Kiểm tra', exact: true }).click();
    await enterPin(page);
    const res = hub.getByTestId('boss-result');
    await expect(res).toContainText('Lỗi · Gen-hub từ chối token');
    await res.getByText('Chi tiết kỹ thuật').click();
    await expect(res).toContainText('Mã lỗi HUB_TOKEN_REJECTED');
    await expect(page.getByText('Đã đạt 0/1 dòng bắt buộc')).toBeVisible();
    await noObjectText(page);
  });

  test('Thẻ Gen-hub ở /connections (v0.1.42): bỏ tích mạng công cộng với địa chỉ công khai → Lưu & kiểm tra → chỉ đúng công tắc', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/connections');
    const card = page.getByRole('region', { name: 'Gen-hub' });
    await card.getByLabel('Địa chỉ Gen-hub').fill('https://hub.example.test/mcp');
    await card.getByLabel('Token Gen-hub').fill('ghtok_E2E_dung_0123456789');
    const sw = card.getByRole('switch', { name: 'Cho phép Gen-hub ở mạng công cộng' });
    await expect(sw).toHaveAttribute('aria-checked', 'true');
    await sw.click();
    await expect(sw).toHaveAttribute('aria-checked', 'false');
    await card.getByRole('button', { name: 'Lưu & kiểm tra' }).click();
    await enterPin(page);
    await expect(card.getByRole('status')).toContainText("Bật 'Cho phép Gen-hub ở mạng công cộng' ngay trong thẻ này.");
    await noObjectText(page);
  });

  test('Claude Code CLI: đăng nhập mock tới xong → Gọi thử → Đạt', async ({ page }) => {
    test.setTimeout(60_000);
    await loginAsOwner(page);
    await page.goto('/guide/viec-sep');
    const cl = row(page, 'Claude Code CLI');
    await cl.getByRole('button', { name: 'Đăng nhập Claude Code' }).click();
    await enterPin(page); // v0.1.45 (F-20): thêm tài khoản CLI cần mã PIN
    const code = cl.getByLabel('Mã xác thực');
    await expect(code).toBeVisible({ timeout: 10_000 });
    await code.fill('abcd-EFGH-1234');
    await cl.getByRole('button', { name: 'Xác nhận' }).click();
    await expect(cl.getByRole('button', { name: 'Đăng nhập Claude Code' })).toHaveCount(0, { timeout: 10_000 });
    const results = cl.getByTestId('boss-result');
    // Ô "Đăng nhập" tự cập nhật ngay khi đăng nhập xong — TRƯỚC khi bấm Gọi thử (máy chủ ghi trong luồng đăng nhập).
    await expect(results.nth(0)).toContainText('Đạt');
    await expect(results.nth(1)).toContainText('Chưa kiểm');
    await cl.getByRole('button', { name: 'Gọi thử' }).click();
    await expect(results.nth(1)).toContainText('Đạt');
    await expect(results.nth(0)).toContainText('Đạt');
    await expect(cl).toContainText('Xong');
    await expect(page.getByText('Đã đạt đủ 1 dòng bắt buộc')).toBeVisible(); // Claude gọi thử đạt ⇒ nguồn AI tự đạt
    await noObjectText(page);
  });

  test('Jev: Kiểm tra 1 lần → kết quả, nút biến mất; tải lại vẫn còn (đọc từ API, không từ localStorage)', async ({ page }) => {
    await loginAsOwner(page);
    await apiCall(page, 'POST', '/auth/pin/verify', { pin: OWNER.pin });
    await apiCall(page, 'POST', '/providers', { kind: 'system_one', name: 'Jev', keys: ['jev-e2e-key-0123456789'] });
    await page.goto('/guide/viec-sep');
    const jev = row(page, 'Jev');
    await jev.getByRole('button', { name: 'Kiểm tra 1 lần' }).click();
    await expect(jev.getByTestId('boss-result')).toContainText('Đạt ·');
    await expect(jev.getByRole('button', { name: 'Kiểm tra 1 lần' })).toHaveCount(0);

    await page.evaluate(() => {
      try {
        localStorage.clear();
        sessionStorage.clear();
      } catch {
        /* bộ nhớ trình duyệt có thể bị chặn */
      }
    });
    await page.reload();
    const again = row(page, 'Jev');
    await expect(again.getByTestId('boss-result')).toContainText('Đạt ·');
    await expect(again.getByRole('button', { name: 'Kiểm tra 1 lần' })).toHaveCount(0);
    await noObjectText(page);
  });

  test('Vận hành mở /guide/viec-sep → lời giải thích, không có lỗi đỏ', async ({ page }) => {
    await loginAs(page, 'operator@genesis.local');
    await page.goto('/guide/viec-sep');
    await expect(page.getByText('Việc kết nối do Owner làm')).toBeVisible();
    await expect(page.locator('.boss-row')).toHaveCount(0);
    await expect(page.locator('.friendly-error, .inline-error, [role="alert"]')).toHaveCount(0);
    await noObjectText(page);
  });
});
