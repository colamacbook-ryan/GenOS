import { expect, test, type Page } from '@playwright/test';
import { OWNER, apiCall, expectPhoneNav, loginAs, loginAsOwner, p3Hook, resetMock } from './support';

/**
 * v0.1.59 (G2) viết lại ở v0.1.78 (G0) — KHUNG của Owner trên máy chủ giả (tất định). Trước v0.1.78 Owner có Mặt tiền riêng
 * `/owner/*` (OwnerShell: 5 trụ cột + Cài đặt, thanh dưới 5 nút trên điện thoại, nút "Hỏi Gen" ngăn kéo). Nay chỉ còn MỘT shell
 * (AppShell): thanh bên 6 mục hằng ngày + Quản trị, ngăn kéo danh mục trên điện thoại, dock Hỏi Gen bên phải; `/owner/*` là link cũ
 * chuyển hướng (shell/legacyRedirects.ts). Spec này kiểm KHUNG + link cũ; nội dung từng trang có spec riêng (owner-intel, owner-feed,
 * owner-work, owner-analytics, owner-relations, menu-v0142).
 *
 * Tương ứng với spec cũ:
 *   thanh trái 5 trụ cột + Cài đặt  → thanh bên 7 mục cấp 1 (Tình báo · Cần quyết · Việc · Toàn cảnh · Tôi · Đội ngũ · Quản trị)
 *   thanh dưới 5 nút (390)         → ngăn kéo danh mục mở bằng ☰ (support.expectPhoneNav)
 *   nút "Hỏi Gen" mở ngăn 480px    → nút Hỏi Gen ở header mở dock phải (toàn màn ở 390)
 *   /owner/* mở thẳng              → chuyển hướng sang trang mới, giữ ?tin= ?nguoi= ?gen=
 *   "← Về Mặt tiền"                → bỏ (không còn Mặt tiền thứ hai)
 */

const GEN_PANEL = /Gen — trợ lý quản trị/;
const LEVEL1 = ['Tình báo', 'Cần quyết', 'Việc', 'Toàn cảnh', 'Tôi', 'Đội ngũ', 'Quản trị'];
const nav = (page: Page) => page.getByRole('navigation', { name: 'Danh mục màn hình' });
const genPanel = (page: Page) => page.getByRole('complementary', { name: GEN_PANEL });
const genButton = (page: Page) => page.getByRole('button', { name: /Hỏi Gen/ });

/** Mục thanh bên (trang) → đường dẫn + tiêu đề h1. */
const PAGES = [
  { label: 'Cần quyết', url: /\/decide$/ },
  { label: 'Việc', url: /\/work$/ },
  { label: 'Toàn cảnh', url: /\/panorama$/ },
  { label: 'Tôi', url: /\/me$/ },
  { label: 'Đội ngũ', url: /\/team$/ },
  { label: 'Tình báo', url: /\/intel$/ },
] as const;

/** Link cũ của Mặt tiền → đích mới (đúng bảng ở shell/legacyRedirects.ts). */
const OLD_LINKS = [
  { from: '/owner', to: /\/intel$/ },
  { from: '/owner/thong-tin', to: /\/intel\?tab=signals$/ },
  { from: '/owner/quan-he', to: /\/panorama\?tab=relations$/ },
  { from: '/owner/phan-tich', to: /\/panorama\?tab=trends$/ },
  { from: '/owner/viec', to: /\/decide$/ },
  { from: '/owner/them', to: /\/admin\/start\?tab=more$/ },
  { from: '/owner/gen', to: /\/intel$/ },
  { from: '/owner/khong-co-trang-nay', to: /\/intel$/ },
] as const;

async function noObjectText(page: Page): Promise<void> {
  expect(await page.locator('body').innerText()).not.toContain('[object Object]');
}

async function noHorizontalScroll(page: Page): Promise<void> {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over).toBeLessThanOrEqual(0);
}

test.describe('v0.1.78 · một shell cho Owner (máy tính 1440)', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  test('Owner mở "/" ⇒ /intel: thanh bên 7 mục cấp 1, số đếm chỉ ở Tình báo/Cần quyết/Việc, không còn thanh dưới hay thanh Mặt tiền', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/');
    await expect(page).toHaveURL(/\/intel$/);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Tình báo');
    await expect(nav(page).locator('[data-level1] .sb-item__name')).toHaveText(LEVEL1);
    await expect(nav(page).getByRole('link', { name: /^Tình báo/ })).toHaveAttribute('aria-current', 'page');
    await expect(nav(page).locator('[aria-current="page"]')).toHaveCount(1);
    // số đếm: chỉ ba mục có (Tình báo, Cần quyết, Việc)
    for (const label of ['Tình báo', 'Cần quyết', 'Việc']) await expect(nav(page).getByRole('link', { name: new RegExp(`^${label}`) }).locator('.gh-badge')).toHaveCount(1);
    for (const label of ['Toàn cảnh', 'Tôi', 'Đội ngũ']) await expect(nav(page).getByRole('link', { name: new RegExp(`^${label}`) }).locator('.gh-badge')).toHaveCount(0);
    // không còn khung Mặt tiền
    await expect(page.getByTestId('owner-tabbar')).toHaveCount(0);
    await expect(page.locator('aside.owner-rail')).toHaveCount(0);
    await expect(page.locator('[data-testid^="owner-nav-"]')).toHaveCount(0);
    await expect(page.getByRole('navigation', { name: 'Mặt tiền' })).toHaveCount(0);
    await expect(page.getByTestId('back-to-front')).toHaveCount(0);
    for (const item of await nav(page).locator('[data-level1]').all()) expect((await item.boundingBox())!.height).toBeGreaterThanOrEqual(28);
    await expect(genButton(page)).toBeVisible();
    await expect(page.locator('.sb-account')).toBeVisible();
    await noObjectText(page);
    await noHorizontalScroll(page);
  });

  test('bấm từng mục: đổi trang, đổi tiêu đề, mục đang sáng đổi theo; đường dẫn đúng', async ({ page }) => {
    await loginAsOwner(page);
    // Đội ngũ › Đánh giá nhân viên là màn nhạy cảm (cần phiên PIN) — mở sẵn để hộp PIN không chặn lượt bấm.
    await apiCall(page, 'POST', '/auth/pin/verify', { pin: OWNER.pin });
    await page.goto('/intel');
    for (const it of PAGES) {
      await nav(page).getByRole('link', { name: new RegExp(`^${it.label}`) }).click();
      await expect(page).toHaveURL(it.url);
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(it.label);
      await expect(nav(page).getByRole('link', { name: new RegExp(`^${it.label}`) })).toHaveAttribute('aria-current', 'page');
      await expect(nav(page).locator('a[aria-current="page"]')).toHaveCount(1);
      await noHorizontalScroll(page);
    }
  });

  test('mọi link cũ của Mặt tiền (/owner/*) chuyển hướng sang trang mới, không văng ra khung khác; "/" của Owner là Tình báo', async ({ page }) => {
    await loginAsOwner(page);
    for (const it of OLD_LINKS) {
      await page.goto(it.from);
      await expect(page, it.from).toHaveURL(it.to);
      await expect(page.locator('aside.sb'), it.from).toBeVisible(); // luôn là khung chung
      await expect(page.locator('aside.owner-rail'), it.from).toHaveCount(0);
    }
    // giữ tham số của link cũ (?tin= ?nguoi=)
    await page.goto('/owner/thong-tin?tin=fd-1');
    await expect(page).toHaveURL(/\/intel\?tab=signals&tin=fd-1$/);
    await page.goto('/owner/quan-he?nguoi=p-bao');
    await expect(page).toHaveURL(/\/panorama\?tab=relations&nguoi=p-bao$/);
    await page.goto('/overview');
    await expect(page).toHaveURL(/\/intel\?tab=overview$/); // Console cũ cũng chuyển
    await page.goto('/');
    await expect(page).toHaveURL(/\/intel$/);
  });

  test('Hỏi Gen: nút ở header mở dock bên phải (khung chat Gen nguyên vẹn), nút × của khung Gen đóng', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/intel');
    await expect(genPanel(page)).toHaveCount(0);
    await genButton(page).click();
    const dlg = genPanel(page);
    await expect(dlg).toBeVisible();
    const box = (await dlg.boundingBox())!;
    expect(Math.round(box.x + box.width)).toBe(1440); // dính mép phải
    expect(box.width).toBeGreaterThan(300);
    expect(box.width).toBeLessThan(560);
    await expect(dlg.getByLabel('Câu hỏi cho Gen')).toBeVisible();
    await dlg.getByRole('button', { name: 'Đóng khung Gen' }).click();
    await expect(genPanel(page)).toHaveCount(0);
    await noObjectText(page);
  });

  test('Hỏi Gen mở từ trang nào cũng được và giữ nguyên trang đó; hỏi một câu thì Gen trả lời trong dock', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/panorama?tab=relations');
    await genButton(page).click();
    const dlg = genPanel(page);
    await dlg.getByLabel('Câu hỏi cho Gen').fill('Hôm nay có gì cần tôi xử lý?');
    await dlg.getByRole('button', { name: 'Gửi', exact: true }).click();
    await expect(dlg.locator('.gen-msg').first()).toBeVisible();
    await expect(page).toHaveURL(/\/panorama\?tab=relations$/);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Toàn cảnh');
  });

  test('link cũ /owner/gen?gen=<mã> mở Bản tin trong dock Gen; "Nhờ Gen…" trong trang nhúng điền sẵn ô nhập, không tự gửi', async ({ page }) => {
    await loginAsOwner(page);
    const seeded = (await apiCall(page, 'POST', '/gen/__mock/briefing', { slot: 'sang' })) as { conversation_id: string };
    await page.goto(`/owner/gen?gen=${seeded.conversation_id}`);
    await expect(page).toHaveURL(/\/intel$/);
    await expect(genPanel(page)).toBeVisible();
    await expect(genPanel(page).locator('.gen-msg--briefing')).toContainText('Bản tin');
    await page.goto(`/?gen=${seeded.conversation_id}`);
    await expect(page).toHaveURL(/\/intel$/);
    await expect(genPanel(page).locator('.gen-msg--briefing')).toContainText('Bản tin');
  });

  test('?gen=coach: "/" giữ tham số tới Tình báo, dock Hỏi Gen mở ra với thẻ Hôm nay của Sếp, tham số bị bỏ khỏi địa chỉ', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/?gen=coach');
    await expect(page).toHaveURL(/\/intel$/);
    await expect(genPanel(page)).toBeVisible();
    const card = genPanel(page).getByRole('region', { name: 'Hôm nay của Sếp' });
    await expect(card).toBeVisible();
    await expect(card.getByTestId('coach-todo').first()).toBeVisible();
  });

  test('Gen tắt: ?gen=coach ⇒ /guide/viec-sep; không có nút Hỏi Gen ở header; "Nhờ Gen soạn…" ở trang nhúng giải thích + link Bộ não AI', async ({ page }) => {
    await loginAsOwner(page);
    await apiCall(page, 'PATCH', '/gen/settings', { enabled: false });
    await page.goto('/?gen=coach');
    await expect(page).toHaveURL(/\/guide\/viec-sep$/);
    await page.goto('/panorama?tab=relations&nguoi=p-bao');
    await expect(genButton(page)).toHaveCount(0);
    await page.getByTestId('profile-ask').click();
    const sheet = page.getByRole('dialog', { name: 'Hỏi Gen' });
    await expect(sheet.getByText('Gen đang tắt')).toBeVisible();
    await expect(sheet.getByRole('link', { name: 'Mở Bộ não AI' })).toHaveAttribute('href', '/system?tab=brain');
    await expect(genPanel(page)).toHaveCount(0);
  });

  test('Quản trị (Mặt tiền cũ: Cài đặt): /owner/them → Quản trị › Bắt đầu; "Việc nên làm tiếp" có tài khoản, nguồn AI, việc làm tiếp, lối vào Nâng cao', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/owner/them');
    await expect(page).toHaveURL(/\/admin\/start\?tab=more$/);
    await expect(nav(page).getByRole('link', { name: /^Bắt đầu/ })).toHaveAttribute('aria-current', 'page');
    await expect(page.getByTestId('owner-more-account')).toBeVisible();
    await expect(page.getByTestId('owner-more-ai-status')).toBeVisible(); // Sếp thấy ngay nguồn AI đã chạy chưa
    await expect(page.getByRole('link', { name: /Đổi nguồn AI/ })).toHaveAttribute('href', '/setup?buoc=nguon-ai');
    await expect(page.getByTestId('owner-more-todo')).toBeVisible();
    await expect(page.getByTestId('owner-conn-row')).toHaveCount(0);
    await expect(page.getByTestId('owner-connections')).toHaveCount(0);
    await expect(page.getByTestId('owner-more-advanced').getByRole('link')).toHaveText([/API & Model/, /Bộ não AI/, /Mở Console đầy đủ/]);
    // "Mở Console đầy đủ" nay chỉ là link tới Tổng quan trong cùng shell (không còn khung Console riêng, không có nút về Mặt tiền).
    await page.getByRole('link', { name: /Mở Console đầy đủ/ }).click();
    await expect(page).toHaveURL(/\/intel\?tab=overview$/);
    await expect(page.locator('aside.sb')).toBeVisible();
    await expect(page.getByTestId('back-to-front')).toHaveCount(0);
  });

  test('menu tài khoản (chân thanh bên): Tài khoản của tôi · … · Đăng xuất; Esc đóng; đổi giao diện sáng/tối ở header', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/intel');
    await page.locator('.sb-account').click();
    const menu = page.getByRole('menu', { name: 'Tài khoản' });
    await expect(menu).toBeVisible();
    const names = await menu.getByRole('menuitem').allInnerTexts();
    expect(names.map((n) => n.trim()).filter(Boolean).length).toBeGreaterThanOrEqual(4);
    await expect(menu.getByRole('menuitem', { name: /Tài khoản của tôi/ })).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: /Hướng dẫn thiết lập/ })).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: /Đăng xuất/ })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(menu).toHaveCount(0);
    // đổi giao diện: html[data-theme] đảo giữa sáng/tối (nút ở header)
    const theme = () => page.evaluate(() => document.documentElement.dataset.theme);
    const before = await theme();
    await page.locator('header.hd').getByRole('button', { name: /Giao diện/ }).click();
    await expect.poll(theme).not.toBe(before);
    expect(['light', 'dark']).toContain(await theme());
    // Tài khoản của tôi → /account; Trợ giúp có đường vào từ trang Trợ giúp
    await page.locator('.sb-account').click();
    await page.getByRole('menuitem', { name: /Tài khoản của tôi/ }).click();
    await expect(page).toHaveURL(/\/account$/);
  });

  test('menu tài khoản: Đăng xuất ⇒ trang đăng nhập; mở /intel và link cũ /owner lại phải đăng nhập ', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/intel');
    await page.locator('.sb-account').click();
    await page.getByRole('menuitem', { name: 'Đăng xuất' }).click();
    await expect(page).toHaveURL(/\/login/);
    await page.goto('/intel');
    await expect(page).toHaveURL(/\/login\?next=%2Fintel$/);
    await page.goto('/owner');
    await expect(page).toHaveURL(/\/login\?next=%2Fowner$/);
  });

  test('chuyển giao diện sáng/tối: nền, chữ và thanh bên đổi theo token, không còn chữ hoa toàn bộ ở tên mục', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/intel');
    const paint = () =>
      page.evaluate(() => {
        const cs = (sel: string) => getComputedStyle(document.querySelector(sel)!);
        return { app: cs('.app').backgroundColor, title: cs('.hd-title').color, rail: cs('aside.sb').backgroundColor, upper: cs('.sb-item__name').textTransform };
      });
    await page.evaluate(() => (document.documentElement.dataset.theme = 'dark'));
    const dark = await paint();
    await page.evaluate(() => (document.documentElement.dataset.theme = 'light'));
    const light = await paint();
    expect(dark.app).not.toBe(light.app);
    expect(dark.title).not.toBe(light.title);
    expect(dark.rail).not.toBe(light.rail);
    expect([dark.upper, light.upper]).toEqual(['none', 'none']);
  });
});

test.describe('v0.1.78 · một shell cho Owner (điện thoại 390px)', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 390, height: 844 });
  });

  test('không còn thanh dưới 5 nút: ☰ mở ngăn kéo danh mục đủ 7 mục; chọn một mục thì chuyển trang và ngăn tự đóng; không tràn ngang', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/');
    await expect(page).toHaveURL(/\/intel$/);
    await expect(nav(page)).toBeHidden();
    await expectPhoneNav(page);
    await noHorizontalScroll(page);
    for (const it of [PAGES[1], PAGES[3], PAGES[0], PAGES[5]]) {
      await page.getByRole('button', { name: 'Mở danh mục' }).click();
      await nav(page).getByRole('link', { name: new RegExp(`^${it.label}`) }).click();
      await expect(page).toHaveURL(it.url);
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(it.label);
      await expect(nav(page)).toBeHidden();
      await noHorizontalScroll(page);
    }
  });

  test('đầu trang: nút ☰, Hỏi Gen, chuông đều trong màn và đủ lớn để chạm (≥ 28px); menu tài khoản trong ngăn kéo không tràn màn', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/intel');
    for (const loc of [page.getByRole('button', { name: 'Mở danh mục' }), genButton(page), page.getByRole('button', { name: /^Thông báo/ })]) {
      const b = (await loc.boundingBox())!;
      expect(b.width).toBeGreaterThanOrEqual(28);
      expect(b.height).toBeGreaterThanOrEqual(28);
      expect(b.x + b.width).toBeLessThanOrEqual(390);
    }
    await page.getByRole('button', { name: 'Mở danh mục' }).click();
    await page.locator('.sb-account').click();
    const m = (await page.getByRole('menu', { name: 'Tài khoản' }).boundingBox())!;
    expect(m.x).toBeGreaterThanOrEqual(0);
    expect(m.x + m.width).toBeLessThanOrEqual(390);
    await noHorizontalScroll(page);
  });

  test('Hỏi Gen: khung TOÀN MÀN HÌNH, nút × đóng', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/intel');
    await genButton(page).click();
    const dlg = genPanel(page);
    await expect(dlg).toBeVisible();
    const box = (await dlg.boundingBox())!;
    expect([Math.round(box.x), Math.round(box.y), Math.round(box.width), Math.round(box.height)]).toEqual([0, 0, 390, 844]);
    await expect(dlg.getByLabel('Câu hỏi cho Gen')).toBeVisible();
    await noHorizontalScroll(page);
    await dlg.getByRole('button', { name: 'Đóng khung Gen' }).click();
    await expect(dlg).toHaveCount(0);
  });

  test('link cũ /owner/gen trên điện thoại về Tình báo, không còn trang Hỏi Gen riêng; Tổng quan (Console cũ) không có nút về Mặt tiền', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/owner/gen');
    await expect(page).toHaveURL(/\/intel$/);
    await page.goto('/overview');
    await expect(page.getByTestId('back-to-front')).toHaveCount(0);
    await noHorizontalScroll(page);
  });
});

test.describe('v0.1.78 · vai khác Owner dùng cùng một shell', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  for (const email of ['operator@genesis.local', 'manager@genesis.local', 'auditor@genesis.local']) {
    test(`${email.split('@')[0]}: "/" về Tình báo; mở mọi link cũ /owner/* chuyển hướng như Owner nhưng KHÔNG gọi API Owner; API Owner 403`, async ({ page }) => {
      const ownerApiCalls: string[] = [];
      page.on('request', (r) => {
        if (r.url().includes('/api/v1/owner/')) ownerApiCalls.push(r.url());
      });
      await loginAs(page, email);
      await page.goto('/');
      await expect(page).toHaveURL(/\/intel$/);
      await expect(page.locator('aside.sb')).toBeVisible();
      await expect(page.getByTestId('back-to-front')).toHaveCount(0);

      for (const path of ['/owner', '/owner/thong-tin', '/owner/quan-he', '/owner/phan-tich', '/owner/viec', '/owner/them']) {
        await page.goto(path);
        await expect(page, path).not.toHaveURL(/\/owner/);
        await expect(page.locator('aside.sb'), path).toBeVisible();
        await expect(page.locator('body'), path).not.toContainText('[object Object]');
        await page.waitForLoadState('networkidle');
      }
      expect(ownerApiCalls).toEqual([]);

      for (const path of ['today', 'relations?list=hot', 'tasks']) {
        const r = await page.request.get(`/api/v1/owner/${path}`);
        expect(r.status(), path).toBe(403);
      }
      const state = (await p3Hook(page.request, 'owner', 'state')) as { nonOwnerCalls: string[] };
      expect(state.nonOwnerCalls).toHaveLength(3);
    });
  }

  test('chưa đăng nhập mở /owner (và các trụ cột cũ) ⇒ về trang đăng nhập kèm next là link cũ (sau khi đăng nhập vẫn chuyển hướng đúng)', async ({ page }) => {
    await page.goto('/owner');
    await expect(page).toHaveURL(/\/login\?next=%2Fowner$/);
    await page.goto('/owner/thong-tin');
    await expect(page).toHaveURL(/\/login\?next=%2Fowner%2Fthong-tin$/);
  });
});
