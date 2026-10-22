import { expect, test, type Page } from '@playwright/test';
import { apiCall, loginAs, loginAsOwner, p3Hook, resetMock } from './support';

/**
 * v0.1.59 (G2) — khung Mặt tiền Owner 5 trụ cột (/owner/*) trên máy chủ giả (tất định). Spec này CHỈ kiểm KHUNG (thanh điều
 * hướng, tiêu đề, nút Hỏi Gen, menu ảnh đại diện, vai trò, đường lạ) — nội dung từng màn có spec riêng của G3/G4 (owner-intel,
 * owner-work, owner-analytics, owner-feed, owner-relations). Bảng testid là hợp đồng, ghi ở đầu apps/web/src/owner/OwnerShell.tsx:
 *   owner-nav-{intel|feed|relations|analytics|work|settings} · owner-tabbar · owner-tabbar-{intel|feed|relations|analytics|work}
 *   owner-ask-gen · owner-avatar-menu · first-run-card
 * Máy tính 1440: thanh trái 232px (5 trụ cột + vạch ngăn + Cài đặt). Điện thoại 390: thanh dưới đúng 5 nút. "Hỏi Gen" là ngăn kéo
 * (480px bên phải / toàn màn), Esc đóng; /owner/gen?gen=<mã> (link cũ) vẫn mở được.
 */

const PILLARS = [
  { key: 'intel', label: 'Tình báo', path: '/owner', url: /\/owner$/ },
  { key: 'feed', label: 'Thông tin', path: '/owner/thong-tin', url: /\/owner\/thong-tin$/ },
  { key: 'relations', label: 'Quan hệ', path: '/owner/quan-he', url: /\/owner\/quan-he$/ },
  { key: 'analytics', label: 'Phân tích', path: '/owner/phan-tich', url: /\/owner\/phan-tich$/ },
  { key: 'work', label: 'Việc', path: '/owner/viec', url: /\/owner\/viec$/ },
] as const;
const SETTINGS = { key: 'settings', label: 'Cài đặt', path: '/owner/them', url: /\/owner\/them$/ } as const;

const COACH_CARD = 'Hôm nay của Sếp';
const rail = (page: Page) => page.getByRole('navigation', { name: 'Mặt tiền' });
const tabbar = (page: Page) => page.getByTestId('owner-tabbar');
const back = (page: Page) => page.getByTestId('back-to-front');
const askDialog = (page: Page) => page.getByRole('dialog', { name: 'Hỏi Gen' });

/** Số đếm trên thanh điều hướng = `today.nav` của GET /owner/today: Thông tin = urgent_open (tin Khẩn chưa xử lý), Việc = decide. */
async function expectNavCounts(page: Page, prefix: 'owner-nav' | 'owner-tabbar'): Promise<void> {
  const r = await page.request.get('/api/v1/owner/today');
  expect(r.ok()).toBe(true);
  const nav = ((await r.json()) as { nav: { urgent_open: number; decide: number } }).nav;
  expect(nav.urgent_open).toBeGreaterThan(0);
  expect(nav.decide).toBeGreaterThan(0);
  const badge = (key: string) => page.getByTestId(`${prefix}-${key}`).locator('.kit-badge');
  await expect(badge('feed')).toHaveText(String(nav.urgent_open));
  await expect(badge('work')).toHaveText(String(nav.decide));
  for (const key of ['intel', 'relations', 'analytics']) await expect(badge(key)).toHaveCount(0);
}

async function noObjectText(page: Page): Promise<void> {
  expect(await page.locator('body').innerText()).not.toContain('[object Object]');
}

async function noHorizontalScroll(page: Page): Promise<void> {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over).toBeLessThanOrEqual(0);
}

test.describe('v0.1.59 · Mặt tiền 5 trụ cột (máy tính 1440)', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  test('Owner mở "/" ⇒ /owner (Tình báo): thanh trái 5 trụ cột + Cài đặt đúng testid, rộng 232px, vạch ngăn trước Cài đặt, không có thanh dưới', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/');
    await expect(page).toHaveURL(/\/owner$/);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Tình báo');
    await expect(rail(page).getByRole('link')).toHaveCount(6);
    await expect(rail(page).locator('.owner-nav__name')).toHaveText(['Tình báo', 'Thông tin', 'Quan hệ', 'Phân tích', 'Việc', 'Cài đặt']);
    await expectNavCounts(page, 'owner-nav');
    for (const it of [...PILLARS, SETTINGS]) await expect(page.getByTestId(`owner-nav-${it.key}`)).toBeVisible();
    await expect(page.getByTestId('owner-nav-intel')).toHaveAttribute('aria-current', 'page');
    await expect(page.getByTestId('owner-nav-feed')).not.toHaveAttribute('aria-current', 'page');
    await expect(rail(page).getByRole('separator')).toHaveCount(1);
    // vạch ngăn nằm ngay trước Cài đặt
    const sepBottom = (await rail(page).getByRole('separator').boundingBox())!;
    const setTop = (await page.getByTestId('owner-nav-settings').boundingBox())!;
    const workBottom = (await page.getByTestId('owner-nav-work').boundingBox())!;
    expect(sepBottom.y).toBeGreaterThan(workBottom.y + workBottom.height - 1);
    expect(setTop.y).toBeGreaterThanOrEqual(sepBottom.y);
    const aside = (await page.locator('aside.owner-rail').boundingBox())!;
    expect(Math.round(aside.width)).toBe(232);
    await expect(tabbar(page)).toHaveCount(0);
    // tiêu đề màn 18px / 600; mục điều hướng cao ≥ 44px
    const title = await page.getByRole('heading', { level: 1 }).evaluate((el) => {
      const cs = getComputedStyle(el);
      return { size: cs.fontSize, weight: cs.fontWeight, upper: cs.textTransform };
    });
    expect(title).toEqual({ size: '18px', weight: '600', upper: 'none' });
    for (const it of [...PILLARS, SETTINGS]) expect((await page.getByTestId(`owner-nav-${it.key}`).boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await expect(page.getByTestId('owner-ask-gen')).toBeVisible();
    await expect(page.getByTestId('owner-avatar-menu')).toBeVisible();
    await noObjectText(page);
    await noHorizontalScroll(page);
  });

  test('bấm từng mục: đổi màn, đổi tiêu đề, mục đang sáng đổi theo; đường dẫn đúng', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/owner');
    for (const it of [...PILLARS.slice(1), SETTINGS, PILLARS[0]]) {
      await page.getByTestId(`owner-nav-${it.key}`).click();
      await expect(page).toHaveURL(it.url);
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(it.label);
      await expect(page.getByTestId(`owner-nav-${it.key}`)).toHaveAttribute('aria-current', 'page');
      await expect(page.locator('[data-testid^="owner-nav-"][aria-current="page"]')).toHaveCount(1);
      await noHorizontalScroll(page);
    }
  });

  test('mọi đường dẫn trụ cột mở thẳng được, không văng ra Console; đường lạ dưới /owner về /owner; "/" của Owner không còn về /overview', async ({ page }) => {
    await loginAsOwner(page);
    for (const it of [...PILLARS, SETTINGS]) {
      await page.goto(it.path);
      await expect(page).toHaveURL(it.url);
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(it.label);
      await expect(page.locator('aside.sb')).toHaveCount(0);        // không phải khung Console
    }
    await page.goto('/owner/khong-co-trang-nay');
    await expect(page).toHaveURL(/\/owner$/);
    await page.goto('/overview');
    await expect(page).toHaveURL(/\/overview$/);                    // vào thẳng Console vẫn được
    await page.goto('/');
    await expect(page).toHaveURL(/\/owner$/);
  });

  test('Hỏi Gen: nút ở đầu trang mở ngăn bên phải 480px (khung chat Gen nguyên vẹn), Esc đóng; nút × của khung Gen cũng đóng', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/owner');
    await expect(askDialog(page)).toHaveCount(0);
    await page.getByTestId('owner-ask-gen').click();
    const dlg = askDialog(page);
    await expect(dlg).toBeVisible();
    const box = (await dlg.boundingBox())!;
    expect(Math.round(box.width)).toBe(480);
    expect(Math.round(box.x + box.width)).toBe(1440);              // dính mép phải
    await expect(dlg.getByLabel('Câu hỏi cho Gen')).toBeVisible();
    await expect(dlg.getByRole('complementary', { name: /Gen — trợ lý quản trị/ })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dlg).toHaveCount(0);
    // mở lại, đóng bằng × của khung Gen
    await page.getByTestId('owner-ask-gen').click();
    await expect(askDialog(page)).toBeVisible();
    await askDialog(page).getByRole('button', { name: 'Đóng khung Gen' }).click();
    await expect(askDialog(page)).toHaveCount(0);
    // bấm nền mờ phía sau cũng đóng
    await page.getByTestId('owner-ask-gen').click();
    await expect(askDialog(page)).toBeVisible();
    await page.mouse.click(200, 400);
    await expect(askDialog(page)).toHaveCount(0);
    await noObjectText(page);
  });

  test('Hỏi Gen mở từ màn nào cũng được và giữ nguyên màn đó phía sau; hỏi một câu thì Gen trả lời trong ngăn', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/owner/quan-he');
    await page.getByTestId('owner-ask-gen').click();
    const dlg = askDialog(page);
    await dlg.getByLabel('Câu hỏi cho Gen').fill('Hôm nay có gì cần tôi xử lý?');
    await dlg.getByRole('button', { name: 'Gửi', exact: true }).click();
    await expect(dlg.locator('.gen-msg').first()).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page).toHaveURL(/\/owner\/quan-he$/);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Quan hệ');
  });

  test('/owner/gen (link cũ) vẫn mở: khung chat bên phải; câu gợi ý chỉ điền sẵn ô nhập', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/owner/gen');
    const gen = page.getByTestId('owner-gen-panel');
    await expect(gen.getByRole('complementary', { name: /Gen — trợ lý quản trị/ })).toBeVisible();
    const box = await gen.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThan(700);                           // máy tính: khung chat bên phải
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Hỏi Gen');
    await page.getByRole('complementary', { name: 'Gợi ý câu hỏi' }).getByRole('button', { name: /Nhắc tôi gọi lại khách/ }).click();
    await expect(gen.getByLabel('Câu hỏi cho Gen')).toHaveValue('Nhắc tôi gọi lại khách lúc 3 giờ chiều');
    await expect(gen.locator('.gen-msg')).toHaveCount(0);          // chỉ điền sẵn, không gửi
    // nút Hỏi Gen ở trang này chỉ đưa con trỏ vào ô nhập, không mở ngăn thứ hai
    await page.getByTestId('owner-ask-gen').click();
    await expect(askDialog(page)).toHaveCount(0);
    await expect(gen.getByLabel('Câu hỏi cho Gen')).toBeFocused();
    await noObjectText(page);
  });

  // v0.1.59 (G5): Cài đặt đúng 4 khối (Tài khoản · Nguồn AI · Việc nên làm tiếp · Cài đặt nâng cao); bỏ khối "Kết nối" từ boss-checks.
  test('Cài đặt: 4 khối, MỘT danh sách Việc nên làm tiếp; nâng cao ⇒ Console, "← Về Mặt tiền" quay lại', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/owner/them');
    await expect(page.getByTestId('owner-more-account')).toBeVisible();
    await expect(page.getByTestId('owner-more-ai-status')).toBeVisible();        // Sếp thấy ngay nguồn AI đã chạy chưa
    await expect(page.getByRole('link', { name: /Đổi nguồn AI/ })).toHaveAttribute('href', '/setup?buoc=nguon-ai');
    await expect(page.getByTestId('owner-more-todo')).toBeVisible();
    await expect(page.getByTestId('owner-conn-row')).toHaveCount(0);
    await expect(page.getByTestId('owner-connections')).toHaveCount(0);
    await expect(page.getByTestId('owner-more-advanced').getByRole('link')).toHaveText([/API & Model/, /Bộ não AI/, /Mở Console đầy đủ/]);

    const adv = page.getByRole('link', { name: /Mở Console đầy đủ/ });
    await expect(adv).toHaveAttribute('href', '/overview');
    await adv.click();
    await expect(page).toHaveURL(/\/overview$/);
    await expect(page.locator('aside.sb')).toBeVisible();           // khung Console đầy đủ
    await back(page).click();
    await expect(page).toHaveURL(/\/owner$/);
    await expect(page.getByTestId('owner-nav-intel')).toHaveAttribute('aria-current', 'page');
  });

  test('Bản tin từ chuông: "/?gen=<mã>" của Owner ⇒ /owner/gen, mở Bản tin trong khung chat', async ({ page }) => {
    await loginAsOwner(page);
    const seeded = (await apiCall(page, 'POST', '/gen/__mock/briefing', { slot: 'sang' })) as { conversation_id: string };
    await page.goto(`/?gen=${seeded.conversation_id}`);
    await expect(page).toHaveURL(/\/owner\/gen$/);
    await expect(page.getByTestId('owner-gen-panel').locator('.gen-msg--briefing')).toContainText('Bản tin');
  });

  test('?gen=coach: "/" giữ tham số tới Tình báo, ngăn Hỏi Gen mở ra với thẻ Hôm nay của Sếp, tham số bị bỏ khỏi địa chỉ', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/?gen=coach');
    await expect(page).toHaveURL(/\/owner$/);
    await expect(askDialog(page)).toBeVisible();
    await expect(askDialog(page).getByRole('region', { name: COACH_CARD })).toBeVisible();
    await expect(askDialog(page).getByRole('region', { name: COACH_CARD }).getByTestId('coach-todo').first()).toBeVisible();
  });

  test('Gen tắt: ?gen=coach ⇒ /guide/viec-sep; ngăn và trang Hỏi Gen giải thích + link Bộ não AI', async ({ page }) => {
    await loginAsOwner(page);
    await apiCall(page, 'PATCH', '/gen/settings', { enabled: false });
    await page.goto('/?gen=coach');
    await expect(page).toHaveURL(/\/guide\/viec-sep$/);
    await page.goto('/owner');
    await page.getByTestId('owner-ask-gen').click();
    await expect(askDialog(page).getByText('Gen đang tắt')).toBeVisible();
    await expect(askDialog(page).getByRole('link', { name: 'Mở Bộ não AI' })).toHaveAttribute('href', '/system?tab=brain');
    await page.keyboard.press('Escape');
    await page.goto('/owner/gen');
    await expect(page.getByText('Gen đang tắt')).toBeVisible();
    await expect(page.getByTestId('owner-gen-panel')).toHaveCount(0);
  });

  test('menu ảnh đại diện: Cài đặt · Trợ giúp · Đổi giao diện sáng/tối · Đăng xuất', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/owner');
    await page.getByTestId('owner-avatar-menu').click();
    const menu = page.getByRole('menu', { name: 'Tài khoản' });
    await expect(menu.getByRole('menuitem')).toHaveText([/^Cài đặt$/, /^Trợ giúp$/, /Đổi giao diện sáng\/tối/, /^Đăng xuất$/]);
    for (const item of await menu.getByRole('menuitem').all()) expect((await item.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    // Esc đóng
    await page.keyboard.press('Escape');
    await expect(menu).toHaveCount(0);
    // đổi giao diện: html[data-theme] đảo giữa sáng/tối (cơ chế theme sẵn có)
    const theme = () => page.evaluate(() => document.documentElement.dataset.theme);
    const before = await theme();
    await page.getByTestId('owner-avatar-menu').click();
    await page.getByRole('menuitem', { name: /Đổi giao diện sáng\/tối/ }).click();
    await expect.poll(theme).not.toBe(before);
    expect(['light', 'dark']).toContain(await theme());
    // Cài đặt → /owner/them
    await page.getByTestId('owner-avatar-menu').click();
    await page.getByRole('menuitem', { name: 'Cài đặt', exact: true }).click();
    await expect(page).toHaveURL(/\/owner\/them$/);
    // Trợ giúp → /help (Console)
    await page.getByTestId('owner-avatar-menu').click();
    await page.getByRole('menuitem', { name: 'Trợ giúp' }).click();
    await expect(page).toHaveURL(/\/help$/);
  });

  test('menu ảnh đại diện: Đăng xuất ⇒ trang đăng nhập, mở /owner lại phải đăng nhập', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/owner');
    await page.getByTestId('owner-avatar-menu').click();
    await page.getByRole('menuitem', { name: 'Đăng xuất' }).click();
    await expect(page).toHaveURL(/\/login/);
    await page.goto('/owner');
    await expect(page).toHaveURL(/\/login\?next=%2Fowner$/);
  });

  test('Console (vào thẳng /overview): nút "← Về Mặt tiền" đưa về /owner', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/overview');
    await expect(page.locator('aside.sb')).toBeVisible();          // khung Console đầy đủ
    await expect(back(page)).toHaveAccessibleName('Về Mặt tiền');
    await expect(back(page)).toContainText('← Về Mặt tiền');
    await back(page).click();
    await expect(page).toHaveURL(/\/owner$/);
    await expect(page.getByTestId('owner-nav-intel')).toHaveAttribute('aria-current', 'page');
  });

  test('chuyển giao diện sáng/tối: nền, chữ và thanh trái đổi theo token, không còn chữ hoa toàn bộ', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/owner');
    const paint = () =>
      page.evaluate(() => {
        const cs = (sel: string) => getComputedStyle(document.querySelector(sel)!);
        return { app: cs('.owner-app').backgroundColor, title: cs('.owner-top__title').color, rail: cs('.owner-rail').backgroundColor, upper: cs('.owner-nav__name').textTransform };
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

test.describe('v0.1.59 · Mặt tiền 5 trụ cột (điện thoại 390px)', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 390, height: 844 });
  });

  test('thanh dưới đúng 5 nút đúng testid (icon 24 + nhãn 12), dính đáy, không có thanh trái; bấm chuyển màn; không tràn ngang', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/');
    await expect(page).toHaveURL(/\/owner$/);
    await expect(tabbar(page).getByRole('link')).toHaveCount(5);
    await expect(tabbar(page).locator('.owner-tabbar__name')).toHaveText(['Tình báo', 'Thông tin', 'Quan hệ', 'Phân tích', 'Việc']);
    await expectNavCounts(page, 'owner-tabbar');
    for (const it of PILLARS) await expect(page.getByTestId(`owner-tabbar-${it.key}`)).toBeVisible();
    await expect(page.getByTestId('owner-tabbar-settings')).toHaveCount(0);
    await expect(rail(page)).toHaveCount(0);
    await expect(page.locator('[data-testid^="owner-nav-"]')).toHaveCount(0);
    const bar = (await tabbar(page).boundingBox())!;
    expect(Math.round(bar.y + bar.height)).toBe(844);              // dính đáy màn
    const first = await page.getByTestId('owner-tabbar-intel').evaluate((el) => {
      const label = el.querySelector('.owner-tabbar__name')!;
      const icon = el.querySelector('svg')!;
      const ir = icon.getBoundingClientRect();
      return { size: getComputedStyle(label).fontSize, iw: Math.round(ir.width), ih: Math.round(ir.height) };
    });
    expect(first).toEqual({ size: '12px', iw: 24, ih: 24 });
    for (const it of PILLARS) expect((await page.getByTestId(`owner-tabbar-${it.key}`).boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await noHorizontalScroll(page);
    for (const it of [PILLARS[4], PILLARS[2], PILLARS[1], PILLARS[3], PILLARS[0]]) {
      await page.getByTestId(`owner-tabbar-${it.key}`).click();
      await expect(page).toHaveURL(it.url);
      await expect(page.getByRole('heading', { level: 1 })).toHaveText(it.label);
      await expect(page.getByTestId(`owner-tabbar-${it.key}`)).toHaveAttribute('aria-current', 'page');
      await noHorizontalScroll(page);
    }
  });

  test('đầu trang: nút Hỏi Gen, chuông, ảnh đại diện đều có vùng chạm ≥ 44px; menu ảnh đại diện không tràn màn', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/owner');
    for (const loc of [page.getByTestId('owner-ask-gen'), page.getByRole('button', { name: /Thông báo/ }), page.getByTestId('owner-avatar-menu')]) {
      const b = (await loc.boundingBox())!;
      expect(b.width).toBeGreaterThanOrEqual(44);
      expect(b.height).toBeGreaterThanOrEqual(44);
    }
    await page.getByTestId('owner-avatar-menu').click();
    const m = (await page.getByRole('menu', { name: 'Tài khoản' }).boundingBox())!;
    expect(m.x).toBeGreaterThanOrEqual(0);
    expect(m.x + m.width).toBeLessThanOrEqual(390);
    await noHorizontalScroll(page);
  });

  test('Hỏi Gen: ngăn TOÀN MÀN HÌNH (đè cả thanh dưới), Esc đóng', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/owner');
    await page.getByTestId('owner-ask-gen').click();
    const dlg = askDialog(page);
    await expect(dlg).toBeVisible();
    const box = (await dlg.boundingBox())!;
    expect([Math.round(box.x), Math.round(box.y), Math.round(box.width), Math.round(box.height)]).toEqual([0, 0, 390, 844]);
    await expect(dlg.getByLabel('Câu hỏi cho Gen')).toBeVisible();
    await noHorizontalScroll(page);
    await page.keyboard.press('Escape');
    await expect(dlg).toHaveCount(0);
  });

  test('/owner/gen (link cũ): Gen chiếm cả màn giữa đầu trang và thanh dưới', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/owner/gen');
    const gen = page.getByTestId('owner-gen-panel');
    await expect(gen.getByLabel('Câu hỏi cho Gen')).toBeVisible();
    const g = (await gen.boundingBox())!;
    const bar = (await tabbar(page).boundingBox())!;
    expect(g.width).toBeGreaterThan(380);
    expect(g.height).toBeGreaterThan(500);
    expect(g.y + g.height).toBeLessThanOrEqual(bar.y + 1);         // không bị thanh dưới che
    await expect(page.getByRole('complementary', { name: 'Gợi ý câu hỏi' })).toBeHidden();
    await noHorizontalScroll(page);
  });

  test('trong Console trên điện thoại, nút về Mặt tiền vẫn có (chỉ mũi tên) và không làm tràn header', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/overview');
    await expect(back(page)).toBeVisible();
    await expect(back(page)).toHaveAccessibleName('Về Mặt tiền');
    await noHorizontalScroll(page);
    await back(page).click();
    await expect(page).toHaveURL(/\/owner$/);
  });
});

test.describe('v0.1.59 · nhân viên không có Mặt tiền', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  for (const email of ['operator@genesis.local', 'manager@genesis.local', 'auditor@genesis.local']) {
    test(`${email.split('@')[0]}: mở "/" như cũ; mở mọi đường /owner/* bị chuyển; không có nút Về Mặt tiền; API Owner 403`, async ({ page }) => {
      const ownerApiCalls: string[] = [];
      page.on('request', (r) => {
        if (r.url().includes('/api/v1/owner/')) ownerApiCalls.push(r.url());
      });
      await loginAs(page, email);
      await page.goto('/');
      await expect(page).toHaveURL(/\/(overview|inbox)$/);         // màn đầu tiên của vai, như cũ
      await expect(page.locator('aside.sb')).toBeVisible();
      await expect(back(page)).toHaveCount(0);

      for (const path of ['/owner', '/owner/thong-tin', '/owner/quan-he', '/owner/phan-tich', '/owner/viec', '/owner/them']) {
        await page.goto(path);
        await expect(page, path).not.toHaveURL(/\/owner/);
        await expect(page, path).toHaveURL(/\/(overview|inbox)$/);
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

  test('chưa đăng nhập mở /owner (và các trụ cột) ⇒ về trang đăng nhập kèm next', async ({ page }) => {
    await page.goto('/owner');
    await expect(page).toHaveURL(/\/login\?next=%2Fowner$/);
    await page.goto('/owner/thong-tin');
    await expect(page).toHaveURL(/\/login\?next=%2Fowner%2Fthong-tin$/);
  });
});
