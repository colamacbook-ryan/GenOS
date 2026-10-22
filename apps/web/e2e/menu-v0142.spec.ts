/**
 * v0.1.42 — nghiệm thu menu mới trên máy chủ giả (tất định): một menu 6 mục + "Nâng cao" thu gọn, trang chủ theo vai
 * trò, Kết nối một trang (viên trạng thái chung), Đội ngũ, Cài đặt lọc tab theo quyền, link cũ chuyển đúng chỗ,
 * header gọn ngoài Nâng cao, logo hiện phiên bản thật, Hôm nay một hàng 4 số, dải tab không tràn ở 1440px.
 */
import { expect, test, type Page } from '@playwright/test';
import { AUDITOR, MANAGER, OWNER, apiCall, loginAs, loginAsOwner, mockHook, resetMock } from './support';

const nav = (page: Page) => page.getByRole('navigation', { name: 'Danh mục màn hình' });
const LEVEL1 = ['Hôm nay', 'Hộp thư & Việc', 'Khách & Cơ hội', 'Kết nối', 'Đội ngũ', 'Cài đặt', 'Nâng cao'];

test.describe('v0.1.42 · menu', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
  });

  test('1. Owner: "/" → /owner (Mặt tiền); Console /overview có đúng 7 mục cấp 1 (6 + Nâng cao)', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/');
    await expect(page).toHaveURL(/\/owner$/);
    // v0.1.59: Mặt tiền 5 trụ cột — thanh trái có Tình báo (đang mở) … Việc và Cài đặt (testid owner-nav-*).
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Tình báo');
    await expect(page.getByTestId('owner-nav-intel')).toHaveAttribute('aria-current', 'page');
    await expect(page.locator('[data-testid^="owner-nav-"]')).toHaveCount(6);
    await page.goto('/overview');
    const items = nav(page).locator('[data-level1] .sb-item__name');
    await expect(items).toHaveText(LEVEL1);
    await expect(nav(page).getByText('Việc hằng ngày')).toBeVisible();
    await expect(nav(page).getByRole('link', { name: /Hôm nay/ })).toHaveAttribute('aria-current', 'page');
  });

  test('2. Nâng cao thu gọn mặc định, bấm thì mở; mở lại trang là thu gọn (không lưu)', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/overview');
    const adv = nav(page).getByRole('button', { name: /Nâng cao/ });
    await expect(adv).toHaveAttribute('aria-expanded', 'false');
    await expect(nav(page).getByText('Tầng dữ liệu')).toHaveCount(0);
    await adv.click();
    await expect(adv).toHaveAttribute('aria-expanded', 'true');
    await nav(page).getByRole('button', { name: /Tầng dữ liệu/ }).click();
    await expect(nav(page).getByRole('link', { name: /Kho dữ liệu thô/ })).toBeVisible();
    await expect(nav(page).getByRole('link', { name: /Plugin & Tiện ích/ })).toHaveCount(0);
    await page.reload();
    await expect(nav(page).getByRole('button', { name: /Nâng cao/ })).toHaveAttribute('aria-expanded', 'false');
  });

  test('3. Màn Nâng cao: tự mở, header có tự trị + khiên + Góc nhìn đã lưu; màn có bộ lọc giữ Góc nhìn đã lưu', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/raw');
    await expect(nav(page).getByRole('button', { name: /Nâng cao/ })).toHaveAttribute('aria-expanded', 'true');
    await expect(nav(page).getByRole('link', { name: /Kho dữ liệu thô/ })).toHaveAttribute('aria-current', 'page');
    const hd = page.locator('header.hd');
    await expect(hd.getByText('Soạn sẵn chờ duyệt', { exact: true })).toBeVisible();
    await expect(hd.getByRole('button', { name: 'Góc nhìn đã lưu' })).toBeVisible();
    await expect(page.locator('.hd-chip')).toHaveText('NÂNG CAO');
    await page.goto('/overview');
    await expect(hd.getByText(/\d+ kênh · \d+ nhóm/)).toBeVisible();
    await expect(hd.getByText('Soạn sẵn chờ duyệt', { exact: true })).toHaveCount(0);
    await expect(hd.getByRole('button', { name: 'Góc nhìn đã lưu' })).toHaveCount(0);
    await expect(page.locator('.hd-chip')).toHaveText('HẰNG NGÀY');
    // Màn nghiệp vụ có bộ lọc: vẫn mở/lưu được góc nhìn, nhưng không có viên tự trị.
    for (const path of ['/inbox', '/directory', '/search', '/people', '/care']) {
      await page.goto(path);
      await expect(hd.getByRole('button', { name: 'Góc nhìn đã lưu' }), path).toBeVisible();
      await expect(hd.getByText('Soạn sẵn chờ duyệt', { exact: true }), path).toHaveCount(0);
    }
    // Màn không có bộ lọc: không có nút — trừ khi Sếp đã lưu góc nhìn ở đó từ trước (vẫn mở/xoá được).
    await page.goto('/opportunity');
    await expect(page.locator('.hd-chip')).toBeVisible();
    await expect(hd.getByRole('button', { name: 'Góc nhìn đã lưu' })).toHaveCount(0);
    await apiCall(page, 'POST', '/views', { screen: 'opportunity', name: 'Góc nhìn cũ', filters: {} });
    await page.reload();
    await hd.getByRole('button', { name: 'Góc nhìn đã lưu' }).click();
    await expect(page.getByRole('dialog').getByText('Góc nhìn cũ')).toBeVisible();
  });

  test('4. Logo hiện phiên bản thật (GET /system/about), không còn "v2.2"', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/overview');
    await expect(page.locator('.sb-logo__sub')).toHaveText(/^Gen-Harness · v\d+\.\d+\.\d+/);
    await expect(page.locator('.sb-logo')).not.toContainText('v2.2');
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
    await expect(page).toHaveURL(/\/system\?tab=brain$/);
    await expect(page.getByRole('tab', { name: /Bộ não AI/ })).toHaveAttribute('aria-selected', 'true');
  });

  test('7. Link cũ: ?tab=channels → /connections, ?tab=users → /team, ?tab=storage&focus=… giữ nguyên', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/system?tab=channels');
    await expect(page).toHaveURL(/\/connections$/);
    await page.goto('/system?tab=users');
    await expect(page).toHaveURL(/\/team$/);
    await expect(page.getByRole('heading', { level: 2, name: 'Đội ngũ' })).toBeVisible();
    await expect(page.getByRole('button', { name: /Mời người dùng/ })).toBeVisible();
    await page.goto('/system?tab=storage&focus=health');
    await expect(page).toHaveURL(/\/system\?tab=storage&focus=health$/);
    await expect(page.getByRole('tab', { name: /Sao lưu & cập nhật/ })).toHaveAttribute('aria-selected', 'true');
  });

  test('8. Cài đặt (Owner): 5 tab theo thứ tự, dải tab không tràn và không cắt chữ ở 1440px', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/system');
    const tabs = page.getByRole('tablist', { name: 'Cài đặt' }).getByRole('tab');
    await expect(tabs).toHaveCount(5);
    await expect(tabs.first()).toContainText('Sao lưu & cập nhật');
    await expect(tabs.last()).toContainText('Nhật ký');
    const m = await page.getByRole('tablist', { name: 'Cài đặt' }).evaluate((el) => ({
      scroll: el.scrollWidth,
      client: el.clientWidth,
      cut: Array.from(el.querySelectorAll<HTMLElement>('[role="tab"]')).some((t) => t.scrollWidth > t.clientWidth + 1),
    }));
    expect(m.scroll).toBeLessThanOrEqual(m.client);
    expect(m.cut).toBe(false);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await expect(page.getByRole('link', { name: /Tài khoản & PIN của tôi/ })).toHaveAttribute('href', '/account');
    await expect(page.getByRole('link', { name: /Hướng dẫn thiết lập/ })).toHaveAttribute('href', '/guide');
  });

  test('9. Quản lý: "/" về màn đầu tiên của vai trò; Cài đặt chỉ có tab Nhật ký; không có Kết nối/Đội ngũ', async ({ page }) => {
    await loginAs(page, MANAGER.email);
    await page.goto('/');
    await expect(page).toHaveURL(/\/overview$/);
    await expect(nav(page).getByRole('link', { name: /Kết nối/ })).toHaveCount(0);
    await expect(nav(page).getByRole('link', { name: /Đội ngũ/ })).toHaveCount(0);
    await page.goto('/system');
    const tabs = page.getByRole('tab');
    await expect(tabs).toHaveCount(1);
    await expect(tabs.first()).toContainText('Nhật ký');
    await expect(page.getByRole('link', { name: /Hướng dẫn thiết lập/ })).toHaveCount(0);
  });

  test('10. Chưa có nhân viên: thanh bên ẩn Đánh giá/Chăm sóc; Đội ngũ vẫn có link + cách đánh dấu nhân viên', async ({ page }) => {
    await resetMock(page.request, 'finished', { staff: false });
    await loginAsOwner(page);
    await page.goto('/team');
    await expect(nav(page).getByRole('link', { name: /Đội ngũ/ })).toHaveAttribute('aria-current', 'page');
    await expect(nav(page).getByText('Đánh giá con người')).toHaveCount(0);
    await expect(nav(page).getByText('Chất lượng chăm sóc')).toHaveCount(0);
    const note = page.getByRole('note').filter({ hasText: 'Chưa có nhân viên nào' });
    await expect(note).toContainText('Đánh dấu là nhân viên');
    await expect(note.getByRole('link', { name: 'Mở Khách & Nhóm' })).toHaveAttribute('href', '/directory');
    await expect(page.locator('.team-links').getByRole('link', { name: /Đánh giá con người/ })).toHaveAttribute('href', '/people');
    await expect(page.locator('.team-links').getByRole('link', { name: /Chất lượng chăm sóc/ })).toHaveAttribute('href', '/care');
    // Màn vẫn có route.
    await page.goto('/people');
    await expect(page.locator('.content')).not.toBeEmpty();
  });

  test('11. Màn ẩn vẫn mở được (/profile, /plugins); /social, /help, /guide tô sáng mục cha', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/plugins');
    await expect(page.getByRole('heading', { level: 2, name: 'Plugin & Tiện ích' })).toBeVisible();
    await expect(nav(page).getByRole('link', { name: /Plugin & Tiện ích/ })).toHaveCount(0);
    await page.goto('/profile?id=p-bao');
    await expect(page.locator('.content')).not.toBeEmpty();
    await expect(nav(page).getByRole('link', { name: /Hồ sơ sống/ })).toHaveCount(0);
    await page.goto('/social');
    await expect(nav(page).getByRole('link', { name: /Kết nối/ })).toHaveAttribute('aria-current', 'page');
    await page.goto('/help');
    await expect(nav(page).getByRole('link', { name: /Cài đặt/ })).toHaveAttribute('aria-current', 'page');
    await page.goto('/guide');
    await expect(page.locator('.hd-title')).toHaveText('Hướng dẫn thiết lập');
    await expect(nav(page).getByRole('link', { name: /Cài đặt/ })).toHaveAttribute('aria-current', 'page');
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

  test('12. Owner mở Console: tiêu đề Hôm nay, dải "Cần Sếp xử lý" ở đầu; ≤ 7 mục cấp 1; Nâng cao mở/thu, /raw tự mở', async ({ page }) => {
    await mockHook(page.request, 'health', { issues: [{ kind: 'channel.down', title: 'Kênh Zalo đã ngắt kết nối' }] });
    await loginAsOwner(page);
    // v0.1.55: "/" của Owner là Mặt tiền (/owner); Console đầy đủ mở bằng /overview (đúng đường "Cài đặt nâng cao").
    await page.goto('/');
    await expect(page).toHaveURL(/\/owner$/);
    await expect(page.getByTestId('owner-nav-intel')).toHaveAttribute('aria-current', 'page');
    await page.goto('/overview');
    await expect(page.locator('.hd-title')).toHaveText('Hôm nay');
    const strip = page.getByRole('region', { name: 'Cần Sếp xử lý' });
    await expect(strip).toBeVisible();
    // Dải nằm TRƯỚC hàng số và mọi thẻ khác của màn.
    const first = await page.locator('.content .screen').first().evaluate((el) => el.firstElementChild?.getAttribute('data-testid'));
    expect(first).toBe('needs-boss');
    const level1 = nav(page).locator('[data-level1]');
    expect(await level1.count()).toBeLessThanOrEqual(7);
    await expect(level1.locator('.sb-item__name')).toHaveText(LEVEL1);
    const adv = nav(page).getByRole('button', { name: /Nâng cao/ });
    await expect(adv).toHaveAttribute('aria-expanded', 'false');
    await expect(nav(page).getByText('Kho dữ liệu thô')).toHaveCount(0);
    await adv.click();
    await nav(page).getByRole('button', { name: /Tầng dữ liệu/ }).click();
    await expect(nav(page).getByRole('link', { name: /Kho dữ liệu thô/ })).toBeVisible();
    await page.reload();
    await expect(nav(page).getByRole('button', { name: /Nâng cao/ })).toHaveAttribute('aria-expanded', 'false');
    await expect(nav(page).getByText('Kho dữ liệu thô')).toHaveCount(0);
    await page.goto('/raw');
    await expect(nav(page).getByRole('button', { name: /Nâng cao/ })).toHaveAttribute('aria-expanded', 'true');
    await expect(nav(page).getByRole('link', { name: /Kho dữ liệu thô/ })).toBeVisible();
  });

  test('13. Năm việc chính ≤ 2 cú bấm: Hộp thư, Bản tin Gen, Sao lưu & cập nhật, Kết nối', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/overview');
    // (b) Hộp thư & Việc → Hộp thư.
    await nav(page).getByRole('button', { name: /Hộp thư & Việc/ }).click();
    await nav(page).getByRole('link', { name: /^Hộp thư/ }).first().click();
    await expect(page).toHaveURL(/\/inbox$/);

    // (c) Chuông → mục bản tin → khung Gen mở đúng hội thoại.
    const seeded = (await apiCall(page, 'POST', '/gen/__mock/briefing', { slot: 'sang' })) as { conversation_id: string };
    await page.goto('/overview');
    await page.getByRole('button', { name: /^Thông báo/ }).click();
    const conv = page.waitForResponse((r) => r.url().includes(`/gen/conversations/${seeded.conversation_id}`) && r.request().method() === 'GET');
    await page.getByRole('dialog', { name: 'Thông báo' }).locator('.nt-item', { hasText: /Bản tin Gen sáng/ }).click();
    expect((await conv).status()).toBe(200);
    await expect(panel(page)).toBeVisible();
    await expect(panel(page).locator('.gen-msg--briefing')).toContainText('Bản tin');

    // (d) Cài đặt → tab mặc định Sao lưu & cập nhật: thẻ Cập nhật + Sao lưu.
    await page.goto('/overview');
    await nav(page).getByRole('link', { name: /Cài đặt/ }).click();
    await expect(page).toHaveURL(/\/system$/);
    await expect(page.getByRole('tab', { name: /Sao lưu & cập nhật/ })).toHaveAttribute('aria-selected', 'true');
    await expect(page.locator('.upd')).toHaveCount(1);
    await expect(page.locator('[data-gen-target="system.backup.panel"]')).toBeVisible();

    // (e) Kết nối → đủ thẻ, mỗi thẻ 1 viên trạng thái hợp lệ + 1 nút chính.
    await nav(page).getByRole('link', { name: /Kết nối/ }).click();
    await expect(page).toHaveURL(/\/connections$/);
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

  test('14. staff=true: Đội ngũ có Đánh giá con người + Chất lượng chăm sóc, mở được', async ({ page }) => {
    await resetMock(page.request, 'finished', { staff: true });
    await loginAsOwner(page);
    // Đánh giá con người là màn nhạy cảm (cần phiên PIN) — mở sẵn để không vướng hộp PIN.
    await apiCall(page, 'POST', '/auth/pin/verify', { pin: OWNER.pin });
    await page.goto('/team');
    const people = nav(page).getByRole('link', { name: /Đánh giá con người/ });
    const care = nav(page).getByRole('link', { name: /Chất lượng chăm sóc/ });
    await expect(people).toBeVisible();
    await expect(care).toBeVisible();
    await expect(page.getByRole('note').filter({ hasText: 'Chưa có nhân viên nào' })).toHaveCount(0);
    await people.click();
    await expect(page).toHaveURL(/\/people$/);
    await expect(page.locator('.content')).not.toBeEmpty();
    await expect(page.getByText(LOCKED)).toHaveCount(0);
    await nav(page).getByRole('link', { name: /Chất lượng chăm sóc/ }).click();
    await expect(page).toHaveURL(/\/care$/);
    await expect(page.locator('.content')).not.toBeEmpty();
    await expect(page.getByText(LOCKED)).toHaveCount(0);
  });

  test('15. Agent NV đăng nhập (và vào "/") → /inbox, không gặp ổ khoá', async ({ page }) => {
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
    await expect(page).toHaveURL(/\/inbox$/);
    await expect(page.locator('.content')).not.toBeEmpty();
    await expect(page.getByText(LOCKED)).toHaveCount(0);
    await page.goto('/');
    await expect(page).toHaveURL(/\/inbox$/);
    await expect(page.locator('.content')).not.toBeEmpty();
    await expect(page.getByText(LOCKED)).toHaveCount(0);
  });

  test('16. Quản lý mở /system: 1 tab Nhật ký, không thẻ lỗi đỏ, không gọi /providers', async ({ page }) => {
    const providerCalls: string[] = [];
    page.on('request', (r) => {
      if (/\/api\/v1\/providers(\/|\?|$)/.test(r.url())) providerCalls.push(r.url());
    });
    await loginAs(page, MANAGER.email);
    await page.goto('/system');
    const tabs = page.getByRole('tablist').getByRole('tab');
    await expect(tabs).toHaveCount(1);
    await expect(tabs.first()).toContainText('Nhật ký');
    await page.waitForLoadState('networkidle');
    await expect(page.locator('.content .gh-state--error')).toHaveCount(0);
    expect(providerCalls).toEqual([]);
  });

  test('17. Route cũ: focus=backup cuộn tới Sao lưu; ?gen= mở bản tin; /social, /mcp, /profile, /plugins mở; /guide/:n có tiêu đề', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/system?tab=storage&focus=backup');
    await expect(page.getByRole('tab', { name: /Sao lưu & cập nhật/ })).toHaveAttribute('aria-selected', 'true');
    const backup = page.locator('[data-gen-target="system.backup.panel"]');
    await expect(backup).toBeInViewport();
    await expect(backup.locator('[data-gen-target="system.backup.now"]')).toBeFocused();

    const seeded = (await apiCall(page, 'POST', '/gen/__mock/briefing', { slot: 'chieu' })) as { conversation_id: string };
    await page.goto(`/overview?gen=${seeded.conversation_id}`);
    await expect(panel(page)).toBeVisible();
    await expect(panel(page).locator('.gen-msg--briefing')).toContainText('Bản tin');
    // v0.1.55: "/?gen=<mã>" của Owner ⇒ Mặt tiền, mở Bản tin ở màn Hỏi Gen (/owner/gen) và bỏ tham số khỏi địa chỉ.
    // v0.1.59: /owner/gen giữ cho link cũ — khung chat nằm ngay trên trang (không mở ngăn thứ hai), nút Hỏi Gen vẫn ở đầu trang.
    await page.goto(`/?gen=${seeded.conversation_id}`);
    await expect(page).toHaveURL(/\/owner\/gen$/);
    await expect(page.getByTestId('owner-ask-gen')).toBeVisible();
    await expect(page.getByTestId('owner-gen-panel')).toBeVisible();
    await expect(panel(page).locator('.gen-msg--briefing')).toContainText('Bản tin');

    for (const path of ['/social', '/mcp', '/profile?id=p-bao', '/plugins']) {
      await page.goto(path);
      await expect(page.locator('.content')).not.toBeEmpty();
      await expect(page.getByText(LOCKED)).toHaveCount(0);
    }
    await expect(nav(page).getByRole('link', { name: /Plugin & Tiện ích/ })).toHaveCount(0);
    await expect(nav(page).getByRole('link', { name: /Hồ sơ sống/ })).toHaveCount(0);
    await page.goto('/guide');
    await expect(page.locator('.hd-title')).toHaveText('Hướng dẫn thiết lập');
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
    const zalo = page.getByRole('article', { name: 'Kênh Zalo' });
    await expect(zalo.locator('[data-status]')).toHaveAttribute('data-status', 'needs_boss');
    await expect(zalo.locator('[data-status]')).toHaveText('Cần Sếp xử lý');
    // v0.1.68 (F060/F135): không còn thẻ kênh "Telegram — Chưa có trong bản này" cạnh thẻ Telegram thật.
    await expect(page.getByRole('article', { name: 'Kênh Telegram' })).toHaveCount(0);
    await expect(page.getByText('Chưa có trong bản này')).toHaveCount(0);
    await expect(page.locator('a[href^="/plugins"]')).toHaveCount(0);
  });

  test('19. Header gọn ngoài Nâng cao; logo phiên bản thật; menu tài khoản không còn Phụ đề tiếng Anh', async ({ page }) => {
    await loginAsOwner(page);
    // Đọc phiên bản bằng gọi API thẳng: body của response trang đã điều hướng có thể bị trình duyệt bỏ (flake CI).
    const ver = ((await apiCall(page, 'GET', '/system/about')) as { version: string }).version;
    await page.goto('/overview');
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
    // Tiêu đề màn Khách & Nhóm trùng tên mục thanh bên.
    await page.goto('/directory');
    await expect(page.getByRole('heading', { level: 2, name: 'Khách & Nhóm' })).toBeVisible();
    await expect(page.locator('.hd-title')).toHaveText('Khách & Nhóm');
    // Bảng lệnh genh ở Trợ giúp chỉ đúng menu mới.
    await page.goto('/help');
    await expect(page.locator('.content')).toContainText('Cài đặt › Sao lưu & cập nhật › Sao lưu ngay');
    // Trợ giúp chỉ có liên kết tới Cài đặt › Sao lưu & cập nhật.
    await page.goto('/help');
    await expect(page.getByTestId('help-update-link')).toHaveAttribute('href', '/system?tab=storage');
  });

  test('22. Auditor (system.read, không system.manage/roles.manage): "/" → Hôm nay; Kết nối chỉ xem; Cài đặt 5 tab; ?tab=users ở lại', async ({ page }) => {
    await loginAs(page, AUDITOR.email);
    await page.goto('/');
    await expect(page).toHaveURL(/\/overview$/);
    await page.goto('/connections');
    await expect(page.getByRole('article', { name: 'Kênh Zalo' })).toBeVisible();
    await expect(page.locator('.content [aria-busy="true"]')).toHaveCount(0);
    await expect(page.locator('[aria-label="Facebook"]')).toHaveCount(0);
    await expect(page.locator('.gh-state--error')).toHaveCount(0);
    // Không có nút thao tác kênh (Quét lại QR / Tạo mã QR / Đăng xuất / Cấu hình).
    await expect(page.getByRole('button', { name: /Quét lại QR|Tạo mã QR|Đăng xuất|Cấu hình/ })).toHaveCount(0);
    // Bộ não AI: Auditor không đọc được "chưa chọn model" ⇒ không có viên trạng thái.
    await expect(page.getByRole('region', { name: 'Bộ não AI' }).locator('[data-status]')).toHaveCount(0);
    await page.goto('/system');
    const tabs = page.getByRole('tablist', { name: 'Cài đặt' }).getByRole('tab');
    await expect(tabs).toHaveCount(5);
    await expect(page.getByRole('link', { name: /Hướng dẫn thiết lập/ })).toHaveCount(0);
    await page.goto('/system?tab=users');
    await expect(page).toHaveURL(/\/system/);
    await expect(page.getByRole('tablist', { name: 'Cài đặt' })).toBeVisible();
    await page.goto('/system?tab=channels');
    await expect(page).toHaveURL(/\/connections$/);
  });
});
