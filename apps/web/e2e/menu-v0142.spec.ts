/**
 * v0.1.42 (viết lại ở v0.1.78 theo MỘT shell) — nghiệm thu menu trên máy chủ giả (tất định): 6 mục hằng ngày + Quản trị thu gọn,
 * trang chủ theo vai trò, Kết nối một trang (viên trạng thái chung), Đội ngũ, Quản trị lọc theo quyền, link cũ chuyển đúng chỗ,
 * header gọn ngoài Quản trị › Nâng cao, logo hiện phiên bản thật, Tổng quan một hàng 4 số, dải tab không tràn ở 1440px.
 * Menu v0.1.42 (Hôm nay · Hộp thư & Việc · Khách & Cơ hội · Kết nối · Đội ngũ · Cài đặt · Nâng cao) đã đổi thành menu v0.1.78
 * (Tình báo · Cần quyết · Việc · Toàn cảnh · Tôi · Đội ngũ · Quản trị) — xem docs/design/UI-IA-REVIEW-v0.1.78 §4.
 */
import { expect, test, type Page } from '@playwright/test';
import { AUDITOR, MANAGER, OWNER, apiCall, loginAs, loginAsOwner, mockHook, resetMock } from './support';

const nav = (page: Page) => page.getByRole('navigation', { name: 'Danh mục màn hình' });
const LEVEL1 = ['Tình báo', 'Cần quyết', 'Việc', 'Toàn cảnh', 'Tôi', 'Đội ngũ', 'Quản trị'];
const ADMIN_LINKS = ['Bắt đầu', 'Kết nối', 'Người & quyền', 'Sao lưu & cập nhật', 'Tổ chức', 'Nhật ký', 'Nâng cao'];

test.describe('v0.1.42 · menu', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
  });

  test('1. Owner: "/" → /intel (Tình báo); thanh bên có đúng 7 mục cấp 1 (6 hằng ngày + Quản trị)', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/');
    await expect(page).toHaveURL(/\/intel$/);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Tình báo');
    // v0.1.78: một shell — không còn thanh trái riêng của Mặt tiền (owner-nav-*), không còn đường /owner.
    await expect(page.locator('[data-testid^="owner-nav-"]')).toHaveCount(0);
    const items = nav(page).locator('[data-level1] .sb-item__name');
    await expect(items).toHaveText(LEVEL1);
    await expect(nav(page).getByText('Việc hằng ngày')).toBeVisible();
    await expect(nav(page).getByRole('link', { name: /Tình báo/ })).toHaveAttribute('aria-current', 'page');
    // /owner (link cũ) chuyển hướng về cùng chỗ.
    await page.goto('/owner');
    await expect(page).toHaveURL(/\/intel$/);
  });

  test('2. Quản trị thu gọn mặc định, bấm thì mở ra 7 trang con; mở lại trang là thu gọn (không lưu)', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/intel');
    const adm = nav(page).getByRole('button', { name: /Quản trị/ });
    await expect(adm).toHaveAttribute('aria-expanded', 'false');
    await expect(nav(page).getByRole('link', { name: /^Bắt đầu/ })).toHaveCount(0);
    await adm.click();
    await expect(adm).toHaveAttribute('aria-expanded', 'true');
    const links = nav(page).locator('#sb-domain-admin .sb-item__name');
    await expect(links).toHaveText(ADMIN_LINKS);
    // Nâng cao là một trang có tab (Kho thô, API & Model…), không còn nhóm con trên thanh bên; Plugin không có lối riêng.
    await expect(nav(page).getByRole('link', { name: /Kho dữ liệu thô/ })).toHaveCount(0);
    await expect(nav(page).getByRole('link', { name: /Plugin & Tiện ích/ })).toHaveCount(0);
    await page.reload();
    await expect(nav(page).getByRole('button', { name: /Quản trị/ })).toHaveAttribute('aria-expanded', 'false');
  });

  test('3. Quản trị › Nâng cao: tự mở, header có tự trị + khiên + Góc nhìn đã lưu; trang có bộ lọc giữ Góc nhìn đã lưu', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/raw');
    await expect(page).toHaveURL(/\/admin\/advanced\?tab=raw$/);
    await expect(nav(page).getByRole('button', { name: /Quản trị/ })).toHaveAttribute('aria-expanded', 'true');
    await expect(nav(page).getByRole('link', { name: /^Nâng cao/ })).toHaveAttribute('aria-current', 'page');
    const hd = page.locator('header.hd');
    await expect(hd.getByText('Soạn sẵn chờ duyệt', { exact: true })).toBeVisible();
    await expect(hd.getByRole('button', { name: 'Góc nhìn đã lưu' })).toBeVisible();
    await expect(page.locator('.hd-chip')).toHaveText('QUẢN TRỊ');
    await page.goto('/intel?tab=overview');
    await expect(hd.getByText(/\d+ kênh · \d+ nhóm/)).toBeVisible();
    await expect(hd.getByText('Soạn sẵn chờ duyệt', { exact: true })).toHaveCount(0);
    await expect(page.locator('.hd-chip')).toHaveText('HẰNG NGÀY');
    // Trang có bộ lọc trên URL (Toàn cảnh, Đội ngũ, Cần quyết, Việc): mở/lưu được góc nhìn, nhưng không có viên tự trị.
    // (v0.1.78: Tình báo — kể cả tab Hộp thư — không nằm trong SAVED_VIEW_SCREENS, đúng theo test/unit/menu-review-v0142.test.ts.)
    for (const path of ['/directory', '/search', '/people', '/care', '/workbench', '/tasks']) {
      await page.goto(path);
      await expect(hd.getByRole('button', { name: 'Góc nhìn đã lưu' }), path).toBeVisible();
      await expect(hd.getByText('Soạn sẵn chờ duyệt', { exact: true }), path).toHaveCount(0);
    }
    // Trang không có bộ lọc (Tôi): không có nút — trừ khi Sếp đã lưu góc nhìn ở đó từ trước (vẫn mở/xoá được).
    await page.goto('/me');
    await expect(page.locator('.hd-chip')).toBeVisible();
    await expect(hd.getByRole('button', { name: 'Góc nhìn đã lưu' })).toHaveCount(0);
    await apiCall(page, 'POST', '/views', { screen: 'me', name: 'Góc nhìn cũ', filters: {} });
    await page.reload();
    await hd.getByRole('button', { name: 'Góc nhìn đã lưu' }).click();
    await expect(page.getByRole('dialog').getByText('Góc nhìn cũ')).toBeVisible();
  });

  test('4. Logo hiện phiên bản thật (GET /system/about), không còn "v2.2"', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/overview');
    await expect(page.locator('.sb-logo__sub')).toHaveText(/^v\d+\.\d+\.\d+/);
    await expect(page.locator('.sb-logo')).not.toContainText('v2.2');
    // v0.1.78: tên hiển thị là GenOS (không còn Gen-Harness).
    await expect(page.locator('.sb-logo')).toContainText('GenOS');
    await expect(page.locator('.sb-logo')).not.toContainText('Gen-Harness');
  });

  test('5. Hôm nay: một hàng 4 số; Sức khoẻ hệ thống có 4 số kỹ thuật; nút "Mở hộp thư"', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/overview');
    await expect(page.locator('.ov-kpi-row')).toHaveCount(1);
    await expect(page.locator('.ov-kpi-row .ov-kpi')).toHaveCount(4);
    const health = page.locator('[data-gen-target="overview.health"]');
    await expect(health.getByTestId('ov-tech-row')).toHaveCount(4);
    await expect(health).not.toContainText('cách ly');
    await expect(page.getByRole('link', { name: 'Mở hộp thư', exact: true })).toHaveAttribute('href', '/inbox');
  });

  test('6. Kết nối: mỗi thẻ một viên trạng thái + một nút chính; Bộ não AI, kênh, Facebook, Gen-hub, MCP', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/connections');
    await expect(page.getByRole('heading', { level: 2, name: 'Kết nối' })).toBeVisible();
    const cards = [
      page.getByRole('region', { name: 'Bộ não AI', exact: true }),
      page.getByRole('article', { name: 'Kênh Zalo' }),
      page.getByRole('article', { name: 'Kênh WhatsApp' }),
      page.locator('[data-gen-target="system.channels.facebook"]'),
      page.locator('[data-gen-target="mcp.hub_link"]'),
      page.getByRole('region', { name: 'MCP', exact: true }),
      page.getByTestId('cli-card-antigravity_cli'),
      page.getByTestId('cli-card-claude_code_cli'),
    ];
    for (const c of cards) {
      await expect(c).toBeVisible();
      await expect(c.locator('[data-status]')).toHaveCount(1);
      await expect(c.locator('[data-main-action]')).toHaveCount(1);
    }
    await expect(page.locator('a[href="/plugins"]')).toHaveCount(0);
    // Thẻ tài khoản CLI chỉ ở đây (mục #brain).
    await expect(page.locator('#brain [data-testid="cli-card-antigravity_cli"]')).toBeVisible();
    await page.getByRole('region', { name: 'Bộ não AI', exact: true }).getByRole('link', { name: /Mở Bộ não AI/ }).click();
    // v0.1.78: Bộ não AI (hạn mức, chi phí) là tab của Quản trị › Nâng cao.
    await expect(page).toHaveURL(/\/admin\/advanced\?tab=brain$/);
    await expect(page.getByRole('tab', { name: /Bộ não AI/ })).toHaveAttribute('aria-selected', 'true');
  });

  test('7. Link cũ: ?tab=channels → /admin/connections, ?tab=users → /admin/access, ?tab=storage&focus=… giữ nguyên focus', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/system?tab=channels');
    await expect(page).toHaveURL(/\/admin\/connections$/);
    await page.goto('/system?tab=users');
    await expect(page).toHaveURL(/\/admin\/access$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Người & quyền' })).toBeVisible();
    await expect(page.getByRole('button', { name: /Mời người dùng/ })).toBeVisible();
    // Link «Đội ngũ › Người dùng» của v0.1.42–v0.1.77 cũng về Người & quyền.
    await page.goto('/team?tab=users');
    await expect(page).toHaveURL(/\/admin\/access$/);
    await page.goto('/system?tab=storage&focus=health');
    await expect(page).toHaveURL(/\/admin\/backup\?focus=health$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Sao lưu & cập nhật' })).toBeVisible();
  });

  test('8. Quản trị (Owner): Người & quyền 2 tab; dải tab của Nâng cao/Toàn cảnh không cắt chữ, trang không tràn ngang ở 1440px', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/admin/access');
    const access = page.getByRole('tablist', { name: 'Người & quyền' }).getByRole('tab');
    await expect(access).toHaveText(['Người dùng', 'Quyền hạn']);
    for (const [path, label] of [['/admin/advanced', 'Nâng cao'], ['/panorama', 'Toàn cảnh'], ['/admin/access', 'Người & quyền']] as const) {
      await page.goto(path);
      const list = page.getByRole('tablist', { name: label });
      await expect(list).toBeVisible();
      const m = await list.evaluate((el) => ({
        cut: Array.from(el.querySelectorAll<HTMLElement>('[role="tab"]')).some((t) => t.scrollWidth > t.clientWidth + 1),
      }));
      expect(m.cut, `${path}: có tab bị cắt chữ`).toBe(false);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow, `${path}: trang tràn ngang`).toBeLessThanOrEqual(0);
    }
    // Lối vào cố định (không còn nằm trong trang Cài đặt): menu tài khoản.
    await page.locator('.sb-account').click();
    const menu = page.getByRole('menu', { name: 'Tài khoản' });
    await expect(menu.getByRole('menuitem', { name: /Hướng dẫn thiết lập/ })).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: /Tài khoản/ }).first()).toBeVisible();
  });

  test('9. Quản lý: "/" về màn đầu tiên của vai trò; Quản trị chỉ có Nhật ký; không có Kết nối/Đội ngũ', async ({ page }) => {
    await loginAs(page, MANAGER.email);
    await page.goto('/');
    await expect(page).toHaveURL(/\/intel$/);
    await expect(nav(page).getByRole('link', { name: /Kết nối/ })).toHaveCount(0);
    await expect(nav(page).getByRole('link', { name: /Đội ngũ/ })).toHaveCount(0);
    await nav(page).getByRole('button', { name: /Quản trị/ }).click();
    await expect(nav(page).locator('#sb-domain-admin .sb-item__name')).toHaveText(['Nhật ký']);
    // /system (Cài đặt cũ) → trang đầu mà vai trò được phép: Nhật ký.
    await page.goto('/system');
    await expect(page).toHaveURL(/\/admin\/log$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Nhật ký' })).toBeVisible();
    await expect(page.getByRole('tab')).toHaveCount(0);
    await expect(page.getByRole('link', { name: /Hướng dẫn thiết lập/ })).toHaveCount(0);
  });

  test('10. Chưa có nhân viên: thanh bên ẩn mục Đội ngũ (5 mục hằng ngày); trang /team vẫn mở được bằng đường dẫn', async ({ page }) => {
    await resetMock(page.request, 'finished', { staff: false });
    await loginAsOwner(page);
    await page.goto('/team');
    // v0.1.78: Đội ngũ là mục `needsStaff` — chưa có nhân viên thì không hiện ở thanh bên (số mục hằng ngày 6 → 5), route vẫn có.
    await expect(nav(page).locator('[data-level1] .sb-item__name')).toHaveText(['Tình báo', 'Cần quyết', 'Việc', 'Toàn cảnh', 'Tôi', 'Quản trị']);
    await expect(nav(page).getByRole('link', { name: /Đội ngũ/ })).toHaveCount(0);
    await expect(page.getByText('Vai trò của bạn không có quyền xem màn này')).toHaveCount(0);
    await expect(page.locator('.hd-title')).toHaveText('Đội ngũ');
    await expect(page.getByRole('tab', { name: 'Đánh giá nhân viên' })).toBeVisible();
    // Link cũ vẫn có route.
    await page.goto('/people');
    await expect(page).toHaveURL(/\/team\?tab=reviews$/);
    await expect(page.locator('.content')).not.toBeEmpty();
  });

  test('11. Màn ẩn vẫn mở được (/profile, /plugins); /social, /help, /guide tô sáng mục cha', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/plugins');
    await expect(page).toHaveURL(/\/admin\/advanced\/plugins$/);
    await expect(page.getByRole('heading', { level: 2, name: 'Plugin & Tiện ích' })).toBeVisible();
    await expect(nav(page).getByRole('link', { name: /Plugin & Tiện ích/ })).toHaveCount(0);
    await page.goto('/profile?id=p-bao');
    await expect(page).toHaveURL(/\/panorama\?tab=profile&id=p-bao$/);
    await expect(page.locator('.content')).not.toBeEmpty();
    await expect(nav(page).getByRole('link', { name: /Hồ sơ sống/ })).toHaveCount(0);
    await page.goto('/social');
    await expect(nav(page).getByRole('link', { name: /^Kết nối/ })).toHaveAttribute('aria-current', 'page');
    // v0.1.78: Trợ giúp không thuộc mục nào của thanh bên; Hướng dẫn thiết lập thuộc Quản trị › Bắt đầu.
    await page.goto('/help');
    await expect(page.locator('.hd-title')).toHaveText('Trợ giúp');
    await expect(nav(page).locator('[data-on]')).toHaveCount(0);
    await page.goto('/guide');
    await expect(page).toHaveURL(/\/admin\/start$/);
    await expect(page.locator('.hd-title')).toHaveText('Bắt đầu');
    await expect(nav(page).getByRole('link', { name: /^Bắt đầu/ })).toHaveAttribute('aria-current', 'page');
    await page.goto('/guide/4');
    await expect(page.locator('.hd-title')).toHaveText('Hướng dẫn thiết lập');
    await expect(nav(page).getByRole('link', { name: /^Bắt đầu/ })).toHaveAttribute('data-on', 'true');
  });
});

/**
 * v0.1.42 — nghiệm thu sau khi gộp 3 gói (menu-api, menu-web, tu-tri): năm việc chính ≤ 2 cú bấm, Agent NV về Hộp
 * thư, Quản lý mở Cài đặt không gọi /providers, link cũ, Zalo hết phiên, header/menu tài khoản, Tổng quan 4 số, chữ cũ
 * không còn và mỗi thẻ một chỗ.
 */
test.describe('v0.1.42 · nghiệm thu sau tích hợp', () => {
  const panel = (page: Page) => page.getByRole('complementary', { name: /Gen — trợ lý quản trị/ });
  // Nhãn có thể kèm chú thích kỳ tính (vd "(trung vị)", "(30 ngày)").
  const KPI_LABELS = [/^Tỉ lệ cơ hội được nhận/, /^Tín hiệu → tiếp cận/, /^Báo giá đã gửi/, /^Tỉ lệ chờ duyệt/];
  const LOCKED = 'Vai trò của bạn không có quyền xem màn này';
  /** Khiên "độ tin cậy dữ liệu" trên header: viên chỉ có "NN%" hoặc "—". */
  const shield = (page: Page) => page.locator('header.hd .gh-pill', { hasText: /^(\d+%|—)$/ });

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
  });

  test('12. Owner mở Tổng quan: dải "Cần Sếp xử lý" ở đầu; ≤ 7 mục cấp 1; Quản trị mở/thu, /raw tự mở', async ({ page }) => {
    await mockHook(page.request, 'health', { issues: [{ kind: 'channel.down', title: 'Kênh Zalo đã ngắt kết nối' }] });
    await loginAsOwner(page);
    // v0.1.78: "/" của Owner là Tình báo; Tổng quan (Hôm nay của Console cũ) là tab của Tình báo, /overview chuyển tới đó.
    await page.goto('/');
    await expect(page).toHaveURL(/\/intel$/);
    await page.goto('/overview');
    await expect(page).toHaveURL(/\/intel\?tab=overview$/);
    await expect(page.locator('.hd-title')).toHaveText('Tình báo');
    await expect(page.getByRole('tab', { name: 'Tổng quan' })).toHaveAttribute('aria-selected', 'true');
    const strip = page.getByRole('region', { name: 'Cần Sếp xử lý' });
    await expect(strip).toBeVisible();
    // Dải nằm TRƯỚC hàng số và mọi thẻ khác của màn.
    const first = await page.locator('.hub__panel .screen').first().evaluate((el) => el.firstElementChild?.getAttribute('data-testid'));
    expect(first).toBe('needs-boss');
    const level1 = nav(page).locator('[data-level1]');
    expect(await level1.count()).toBeLessThanOrEqual(7);
    await expect(level1.locator('.sb-item__name')).toHaveText(LEVEL1);
    const adm = nav(page).getByRole('button', { name: /Quản trị/ });
    await expect(adm).toHaveAttribute('aria-expanded', 'false');
    await expect(nav(page).getByRole('link', { name: /^Nâng cao/ })).toHaveCount(0);
    await adm.click();
    await expect(nav(page).getByRole('link', { name: /^Nâng cao/ })).toBeVisible();
    await page.reload();
    await expect(nav(page).getByRole('button', { name: /Quản trị/ })).toHaveAttribute('aria-expanded', 'false');
    await expect(nav(page).getByRole('link', { name: /^Nâng cao/ })).toHaveCount(0);
    await page.goto('/raw');
    await expect(nav(page).getByRole('button', { name: /Quản trị/ })).toHaveAttribute('aria-expanded', 'true');
    await expect(nav(page).getByRole('link', { name: /^Nâng cao/ })).toBeVisible();
  });

  test('13. Năm việc chính ≤ 2 cú bấm: Hộp thư, Bản tin Gen, Sao lưu & cập nhật, Kết nối', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/me');
    // (b) Tình báo → tab Hộp thư (2 cú bấm).
    await nav(page).getByRole('link', { name: /^Tình báo/ }).click();
    await page.getByRole('tab', { name: 'Hộp thư', exact: true }).click();
    await expect(page).toHaveURL(/\/intel\/inbox$/);

    // (c) Chuông → mục bản tin → khung Gen mở đúng hội thoại.
    const seeded = (await apiCall(page, 'POST', '/gen/__mock/briefing', { slot: 'sang' })) as { conversation_id: string };
    await page.goto('/overview');
    await page.getByRole('button', { name: /^Thông báo/ }).click();
    const conv = page.waitForResponse((r) => r.url().includes(`/gen/conversations/${seeded.conversation_id}`) && r.request().method() === 'GET');
    await page.getByRole('dialog', { name: 'Thông báo' }).locator('.nt-item', { hasText: /Bản tin Gen sáng/ }).click();
    expect((await conv).status()).toBe(200);
    await expect(panel(page)).toBeVisible();
    await expect(panel(page).locator('.gen-msg--briefing')).toContainText('Bản tin');

    // (d) Quản trị → Sao lưu & cập nhật (2 cú bấm): thẻ Cập nhật + Sao lưu.
    await page.goto('/me');
    await nav(page).getByRole('button', { name: /Quản trị/ }).click();
    await nav(page).getByRole('link', { name: /^Sao lưu & cập nhật/ }).click();
    await expect(page).toHaveURL(/\/admin\/backup$/);
    await expect(page.locator('.upd')).toHaveCount(1);
    await expect(page.locator('[data-gen-target="system.backup.panel"]')).toBeVisible();

    // (e) Kết nối → đủ thẻ, mỗi thẻ 1 viên trạng thái hợp lệ + 1 nút chính.
    await nav(page).getByRole('link', { name: /^Kết nối/ }).click();
    await expect(page).toHaveURL(/\/admin\/connections$/);
    const cards = [
      page.getByRole('region', { name: 'Bộ não AI', exact: true }),
      page.getByRole('article', { name: 'Kênh Zalo' }),
      page.getByRole('article', { name: 'Kênh WhatsApp' }),
      page.locator('[data-gen-target="system.channels.facebook"]'),
      page.locator('[data-gen-target="mcp.hub_link"]'),
      page.getByRole('region', { name: 'MCP', exact: true }),
      page.getByTestId('cli-card-antigravity_cli'),
      page.getByTestId('cli-card-claude_code_cli'),
    ];
    for (const c of cards) {
      await expect(c).toBeVisible();
      const pill = c.locator('[data-status]');
      await expect(pill).toHaveCount(1);
      expect(['running', 'needs_boss', 'not_connected']).toContain(await pill.getAttribute('data-status'));
      await expect(c.locator('[data-main-action]')).toHaveCount(1);
    }
  });

  test('14. staff=true: Đội ngũ có Đánh giá nhân viên + Chất lượng chăm sóc, mở được', async ({ page }) => {
    await resetMock(page.request, 'finished', { staff: true });
    await loginAsOwner(page);
    // Đánh giá con người là màn nhạy cảm (cần phiên PIN) — mở sẵn để không vướng hộp PIN.
    await apiCall(page, 'POST', '/auth/pin/verify', { pin: OWNER.pin });
    await page.goto('/intel');
    await nav(page).getByRole('link', { name: /^Đội ngũ/ }).click();
    await expect(page).toHaveURL(/\/team$/);
    const people = page.getByRole('tab', { name: 'Đánh giá nhân viên' });
    const care = page.getByRole('tab', { name: 'Chất lượng chăm sóc' });
    await expect(people).toBeVisible();
    await expect(care).toBeVisible();
    await expect(page.getByRole('note').filter({ hasText: 'Chưa có nhân viên nào' })).toHaveCount(0);
    await expect(people).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator('.content')).not.toBeEmpty();
    await expect(page.getByText(LOCKED)).toHaveCount(0);
    await care.click();
    await expect(page).toHaveURL(/\/team\?tab=care$/);
    await expect(page.locator('.content')).not.toBeEmpty();
    await expect(page.getByText(LOCKED)).toHaveCount(0);
  });

  test('15. Agent NV đăng nhập (và vào "/") → /intel, không gặp ổ khoá', async ({ page }) => {
    await loginAsOwner(page);
    await apiCall(page, 'POST', '/auth/pin/verify', { pin: OWNER.pin });
    const inv = (await apiCall(page, 'POST', '/users', { display_name: 'Nhân viên Thử', email: 'nv-thu@genesis.local', role: 'agent_staff' })) as {
      temp_password: string;
    };
    await page.context().clearCookies();
    await loginAs(page, 'nv-thu@genesis.local', inv.temp_password);
    const pw = 'matkhau-nhan-vien-2026';
    await apiCall(page, 'POST', '/account/password', { current_password: inv.temp_password, new_password: pw });
    await page.context().clearCookies();
    // Đăng nhập qua màn đăng nhập: xong là về màn đầu tiên của vai trò.
    await page.goto('/login');
    await page.getByLabel('Email').fill('nv-thu@genesis.local');
    await page.getByLabel('Mật khẩu', { exact: true }).fill(pw);
    await page.getByRole('button', { name: /Đăng nhập/ }).click();
    // v0.1.78: màn đầu của mọi vai là Tình báo (Agent NV chỉ có tab Hộp thư — không có Tổng quan).
    await expect(page).toHaveURL(/\/intel$/);
    await expect(page.locator('.content')).not.toBeEmpty();
    await expect(page.getByText(LOCKED)).toHaveCount(0);
    await page.goto('/');
    await expect(page).toHaveURL(/\/intel$/);
    await expect(page.locator('.content')).not.toBeEmpty();
    await expect(page.getByText(LOCKED)).toHaveCount(0);
  });

  test('16. Quản lý mở /system: chuyển tới Nhật ký (1 trang, không tab), không thẻ lỗi đỏ, không gọi /providers', async ({ page }) => {
    const providerCalls: string[] = [];
    page.on('request', (r) => {
      if (/\/api\/v1\/providers(\/|\?|$)/.test(r.url())) providerCalls.push(r.url());
    });
    await loginAs(page, MANAGER.email);
    await page.goto('/system');
    await expect(page).toHaveURL(/\/admin\/log$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Nhật ký' })).toBeVisible();
    await expect(page.getByRole('tablist')).toHaveCount(0);
    await page.waitForLoadState('networkidle');
    await expect(page.locator('.content .gh-state--error')).toHaveCount(0);
    expect(providerCalls).toEqual([]);
  });

  test('17. Route cũ: focus=backup cuộn tới Sao lưu; ?gen= mở bản tin; /social, /mcp, /profile, /plugins mở; /guide/:n có tiêu đề', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/system?tab=storage&focus=backup');
    await expect(page).toHaveURL(/\/admin\/backup\?focus=backup$/);
    const backup = page.locator('[data-gen-target="system.backup.panel"]');
    await expect(backup).toBeInViewport();
    await expect(backup.locator('[data-gen-target="system.backup.now"]')).toBeFocused();

    const seeded = (await apiCall(page, 'POST', '/gen/__mock/briefing', { slot: 'chieu' })) as { conversation_id: string };
    await page.goto(`/overview?gen=${seeded.conversation_id}`);
    await expect(panel(page)).toBeVisible();
    await expect(panel(page).locator('.gen-msg--briefing')).toContainText('Bản tin');
    // v0.1.78: "/?gen=<mã>" của Owner ⇒ Tình báo (một shell), khung Gen dock phải mở Bản tin và tham số bị bỏ khỏi địa chỉ.
    // Link cũ /owner/gen (Hỏi Gen của Mặt tiền) cũng về Tình báo.
    await page.goto(`/?gen=${seeded.conversation_id}`);
    await expect(page).toHaveURL(/\/intel$/);
    await expect(panel(page)).toBeVisible();
    await expect(panel(page).locator('.gen-msg--briefing')).toContainText('Bản tin');
    await page.goto('/owner/gen');
    await expect(page).toHaveURL(/\/intel$/);

    for (const path of ['/social', '/mcp', '/profile?id=p-bao', '/plugins']) {
      await page.goto(path);
      await expect(page.locator('.content')).not.toBeEmpty();
      await expect(page.getByText(LOCKED)).toHaveCount(0);
    }
    await expect(nav(page).getByRole('link', { name: /Plugin & Tiện ích/ })).toHaveCount(0);
    await expect(nav(page).getByRole('link', { name: /Hồ sơ sống/ })).toHaveCount(0);
    await page.goto('/guide');
    await expect(page).toHaveURL(/\/admin\/start$/);
    await expect(page.locator('.hd-title')).toHaveText('Bắt đầu');
    await page.goto('/guide/4');
    await expect(page.locator('.hd-title')).toHaveText('Hướng dẫn thiết lập');
  });

  test('18. Zalo hết phiên → thẻ Zalo "Cần Sếp xử lý"; Telegram chưa cài không dẫn tới /plugins', async ({ page }) => {
    await page.route(/\/api\/v1\/channels$/, async (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      const res = await route.fetch();
      const list = (await res.json()) as Array<{ type: string; state: string }>;
      await route.fulfill({ response: res, json: list.map((c) => (c.type === 'zalo' ? { ...c, state: 'expired' } : c)) });
    });
    await loginAsOwner(page);
    await page.goto('/connections');
    await expect(page).toHaveURL(/\/admin\/connections$/);
    const zalo = page.getByRole('article', { name: 'Kênh Zalo' });
    await expect(zalo.locator('[data-status]')).toHaveAttribute('data-status', 'needs_boss');
    await expect(zalo.locator('[data-status]')).toHaveText('Cần Sếp xử lý');
    // v0.1.68 (F060/F135): không còn thẻ kênh "Telegram — Chưa có trong bản này" cạnh thẻ Telegram thật.
    await expect(page.getByRole('article', { name: 'Kênh Telegram' })).toHaveCount(0);
    await expect(page.getByText('Chưa có trong bản này')).toHaveCount(0);
    await expect(page.locator('a[href^="/plugins"]')).toHaveCount(0);
  });

  test('19. Header gọn ngoài Quản trị › Nâng cao; logo phiên bản thật; menu tài khoản không còn Phụ đề tiếng Anh', async ({ page }) => {
    await loginAsOwner(page);
    // Đọc phiên bản bằng gọi API thẳng: body của response trang đã điều hướng có thể bị trình duyệt bỏ (flake CI).
    const ver = ((await apiCall(page, 'GET', '/system/about')) as { version: string }).version;
    await page.goto('/me');
    const hd = page.locator('header.hd');
    await expect(hd.getByText(/\d+ kênh · \d+ nhóm/)).toBeVisible();
    await expect(hd.getByText('Soạn sẵn chờ duyệt', { exact: true })).toHaveCount(0);
    await expect(shield(page)).toHaveCount(0);
    await expect(hd.getByRole('button', { name: 'Góc nhìn đã lưu' })).toHaveCount(0);
    await expect(page.locator('.sb-logo__sub')).toContainText(ver);
    await expect(page.locator('.sb-logo')).not.toContainText('v2.2');
    await page.locator('.sb-account').click();
    const menu = page.getByRole('menu', { name: 'Tài khoản' });
    await expect(menu).toBeVisible();
    await expect(menu).not.toContainText('Phụ đề tiếng Anh');
    await page.keyboard.press('Escape');
    await page.goto('/raw');
    await expect(hd.getByText('Soạn sẵn chờ duyệt', { exact: true })).toBeVisible();
    await expect(shield(page)).toHaveCount(1);
    await expect(hd.getByRole('button', { name: 'Góc nhìn đã lưu' })).toBeVisible();
  });

  test('20. Tổng quan đúng 4 ô số; Sức khoẻ hệ thống 4 số kỹ thuật; không còn "Độ trễ xử lý của hệ thống"', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/overview');
    await expect(page.locator('.ov-kpi-row .ov-kpi')).toHaveCount(4);
    await expect(page.locator('.ov-kpi-row .ov-kpi__label')).toHaveText(KPI_LABELS);
    await expect(page.locator('[data-gen-target="overview.health"]').getByTestId('ov-tech-row')).toHaveCount(4);
    await expect(page.locator('.content')).not.toContainText('Độ trễ xử lý của hệ thống');
  });

  test('21. Chữ cũ không còn; PIN chỉ ở Tài khoản; tài khoản CLI chỉ ở Kết nối; thẻ Cập nhật chỉ ở Cài đặt', async ({ page }) => {
    await loginAsOwner(page);
    const OLD = [
      'Hộp thư ý nghĩa',
      'Kỹ thuật · Backend',
      'Chuỗi ưu tiên',
      'Điều khiển hệ thống',
      'Dữ liệu & lưu trữ',
      'Tổng quan điều hành',
      'Nhóm & Con người',
    ];
    const screens = [
      '/overview',
      '/inbox',
      '/directory',
      '/connections',
      '/team',
      '/system',
      '/system?tab=brain',
      '/system?tab=org',
      '/account',
      '/help',
      '/guide',
      '/guide/4',
      '/raw',
    ];
    for (const path of screens) {
      await page.goto(path);
      await expect(page.locator('.content')).not.toBeEmpty();
      // Đợi màn tải xong (không còn khung chờ) rồi mới kiểm chữ.
      await expect(page.locator('.content [aria-busy="true"]')).toHaveCount(0);
      for (const t of OLD) await expect(page.locator('body'), `${path}: còn chữ "${t}"`).not.toContainText(t);
      const pin = page.getByRole('form', { name: 'Đổi mã PIN' });
      if (path === '/account') await expect(pin).toHaveCount(1);
      else await expect(pin, `${path}: có form đổi PIN`).toHaveCount(0);
      const cli = page.locator('[data-testid^="cli-card-"]');
      if (path === '/connections') await expect(cli).toHaveCount(2);
      else await expect(cli, `${path}: có thẻ tài khoản CLI`).toHaveCount(0);
      const upd = page.locator('.upd');
      if (path === '/system') await expect(upd).toHaveCount(1);
      else await expect(upd, `${path}: có thẻ Cập nhật`).toHaveCount(0);
    }
    // v0.1.78: Khách & Nhóm là tab "Người & nhóm" của Toàn cảnh — header ghi tên mục thanh bên, tiêu đề màn vẫn là Khách & Nhóm.
    await page.goto('/directory');
    await expect(page.getByRole('heading', { level: 2, name: 'Khách & Nhóm' })).toBeVisible();
    await expect(page.locator('.hd-title')).toHaveText('Toàn cảnh');
    // Bảng lệnh genh ở Trợ giúp chỉ đúng menu mới.
    await page.goto('/help');
    await expect(page.locator('.content')).toContainText('Cài đặt › Sao lưu & cập nhật › Sao lưu ngay');
    // Trợ giúp chỉ có liên kết tới Cài đặt › Sao lưu & cập nhật.
    await page.goto('/help');
    await expect(page.getByTestId('help-update-link')).toHaveAttribute('href', '/system?tab=storage');
  });

  test('22. Auditor (system.read, không system.manage/roles.manage): "/" → Tình báo; Kết nối chỉ xem; Quản trị không có Người dùng/Mời', async ({ page }) => {
    await loginAs(page, AUDITOR.email);
    await page.goto('/');
    await expect(page).toHaveURL(/\/intel$/);
    await page.goto('/connections');
    await expect(page).toHaveURL(/\/admin\/connections$/);
    await expect(page.getByRole('article', { name: 'Kênh Zalo' })).toBeVisible();
    await expect(page.locator('.content [aria-busy="true"]')).toHaveCount(0);
    await expect(page.locator('[aria-label="Facebook"]')).toHaveCount(0);
    await expect(page.locator('.gh-state--error')).toHaveCount(0);
    // Không có nút thao tác kênh (Quét lại QR / Tạo mã QR / Đăng xuất / Cấu hình).
    await expect(page.getByRole('button', { name: /Quét lại QR|Tạo mã QR|Đăng xuất|Cấu hình/ })).toHaveCount(0);
    // Bộ não AI: Auditor không đọc được "chưa chọn model" ⇒ không có viên trạng thái.
    await expect(page.getByRole('region', { name: 'Bộ não AI' }).locator('[data-status]')).toHaveCount(0);
    // v0.1.78: Cài đặt cũ tách thành các trang Quản trị. Auditor (system.read): Sao lưu & cập nhật, Người & quyền (chỉ tab Quyền hạn).
    await page.goto('/system');
    await expect(page).toHaveURL(/\/admin\/backup$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Sao lưu & cập nhật' })).toBeVisible();
    await expect(page.getByRole('link', { name: /Hướng dẫn thiết lập/ })).toHaveCount(0);
    await page.goto('/system?tab=users');
    await expect(page).toHaveURL(/\/admin\/access$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Người & quyền' })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Người dùng' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /Mời người dùng/ })).toHaveCount(0);
    await page.goto('/system?tab=channels');
    await expect(page).toHaveURL(/\/admin\/connections$/);
  });
});
