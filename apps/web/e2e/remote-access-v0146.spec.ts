import { expect, test, type Page } from '@playwright/test';
import { MANAGER, OWNER, loginAs, loginAsOwner, mockHook, p3Hook, resetMock } from './support';

/**
 * v0.1.46 (F-21) — Truy cập từ xa (mock, tất định): hộp mời cảnh báo đỏ khi địa chỉ đăng nhập chỉ mở được trên máy chủ,
 * lời nhắn chép theo `login_url` (không theo địa chỉ trình duyệt); chuông "Cổng đang mở cho cả mạng" → thẻ "Truy cập từ
 * xa"; dòng 7 "Việc Sếp cần làm" kiểm theo Origin.
 */

async function enterPin(page: Page) {
  const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
  await expect(dlg).toBeVisible();
  await page.getByLabel('Mã PIN — chữ số 1/6').click();
  await page.keyboard.type(OWNER.pin);
  await expect(dlg).toBeHidden();
}

async function setAccess(page: Page, data: Record<string, unknown>) {
  const res = await page.request.post('/api/v1/__mock/access', { data });
  expect(res.status()).toBeLessThan(400);
}

/** Mời một người ở Đội ngũ › Người dùng (cần PIN) → hộp "mật khẩu tạm". */
async function invite(page: Page, email: string) {
  await page.goto('/team');
  await page.getByRole('button', { name: /Mời người dùng/ }).first().click();
  const form = page.getByRole('dialog', { name: 'Mời người dùng' });
  await form.getByLabel('Tên hiển thị').fill('Lan Vận Hành');
  await form.getByLabel('Email đăng nhập').fill(email);
  await form.getByRole('button', { name: 'Mời', exact: true }).click();
  // PIN chỉ hỏi khi chưa có phiên PIN (lần mời thứ hai trong cùng phiên thì không hỏi lại).
  const box = page.getByRole('dialog', { name: /Đã mời Lan Vận Hành/ });
  await expect(page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' }).or(box)).toBeVisible();
  if (await page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' }).isVisible()) await enterPin(page);
  await expect(box).toBeVisible();
  return box;
}

test.describe('Truy cập từ xa (v0.1.46)', () => {
  test.beforeEach(async ({ page, context }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 1440, height: 900 });
    await context.grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => undefined);
    await loginAsOwner(page);
  });

  test('mời người: localhost → cảnh báo đỏ; đổi sang Tailscale → mời lại không cảnh báo, lời nhắn chứa địa chỉ Tailscale', async ({ page }) => {
    test.setTimeout(90_000);
    const box = await invite(page, 'lan.van@genesis.local');
    const warn = box.getByTestId('invite-local-warning');
    await expect(warn).toBeVisible();
    await expect(warn).toHaveAttribute('role', 'alert');
    await expect(warn).toContainText('Địa chỉ này chỉ mở được trên chính máy chủ');
    await expect(warn).toContainText('genh remote tailscale');
    await expect(box.getByLabel('Mật khẩu tạm')).toBeVisible();
    await expect(page.getByText('[object Object]')).toHaveCount(0);
    // Lời nhắn chép theo GH_PUBLIC_URL (mock mặc định https://localhost:8443), KHÔNG theo địa chỉ trình duyệt đang mở.
    await box.getByRole('button', { name: /Chép lời nhắn gửi nhân viên/ }).click();
    await expect(box.getByRole('button', { name: 'Đã chép' })).toBeVisible();
    const clip1 = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip1).toContain('https://localhost:8443/login');
    expect(clip1).not.toContain(new URL(page.url()).origin);
    await box.getByRole('button', { name: 'Đã gửi, đóng' }).click();

    await setAccess(page, { public_url: 'https://gen-harness.tail1234.ts.net', mode: 'tailscale' });
    const box2 = await invite(page, 'lan.van2@genesis.local');
    await expect(box2.getByTestId('invite-local-warning')).toHaveCount(0);
    await box2.getByRole('button', { name: /Chép lời nhắn gửi nhân viên/ }).click();
    await expect(box2.getByRole('button', { name: 'Đã chép' })).toBeVisible();
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip).toContain('Địa chỉ: https://gen-harness.tail1234.ts.net/login');
    expect(clip).not.toContain('localhost');
    expect(clip).toContain('lan.van2@genesis.local');
  });

  test('chuông "Cổng đang mở cho cả mạng" → nút "Chọn cách truy cập" → thẻ "Truy cập từ xa"', async ({ page }) => {
    await setAccess(page, { mode: 'lan_legacy', bind_addr: '0.0.0.0', public_url: 'https://192.168.1.20:8443' });
    await mockHook(page.request, 'health', { issues: [{ kind: 'network.open_lan' }] });
    await page.goto('/overview');
    const strip = page.getByRole('region', { name: 'Cần Sếp xử lý' });
    const rows = strip.getByTestId('needs-boss-row');
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText('Cổng đang mở cho cả mạng');
    // Thẻ đích chỉ có lệnh chạy trên máy chủ ⇒ không hứa "Bấm để chọn".
    await expect(rows.first()).toContainText('Bấm để xem lệnh chọn cách truy cập (chạy trên máy chủ)');
    await expect(rows.first()).not.toContainText('Bấm để chọn');
    await rows.first().getByRole('link', { name: 'Chọn cách truy cập' }).click();
    await expect(page).toHaveURL(/\/system\?tab=storage&focus=access$/);
    const card = page.getByRole('region', { name: 'Truy cập từ xa' });
    await expect(card).toBeVisible();
    await expect(card).toBeInViewport();
    await expect(card).toContainText('Đang mở cho cả mạng (bản cài cũ)');
    const cmds = card.getByTestId('access-commands');
    for (const cmd of ['genh remote tailscale', 'genh remote --local', 'genh remote --lan']) await expect(cmds).toContainText(cmd);
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('Manager (system.read + system.manage): dải "Nhờ Owner xử lý"; thẻ "Truy cập từ xa" không có lệnh genh remote, chỉ câu nhờ Owner', async ({ page, context }) => {
    // Manager mặc định không thấy Cài đặt/dải sức khoẻ — mở quyền như offsite-v0140 (ma trận mock sống qua reset ⇒ trả lại).
    const grants: Array<{ permission: string; prev: string }> = [];
    try {
      for (const permission of ['system.read', 'system.manage']) {
        const r = (await p3Hook(page.request, 'system', 'grant', { role: 'manager', permission, scope: 'all' })) as { prev: string };
        grants.push({ permission, prev: r.prev });
      }
      await setAccess(page, { mode: 'lan_legacy', bind_addr: '0.0.0.0', public_url: 'https://192.168.1.20:8443' });
      await mockHook(page.request, 'health', { issues: [{ kind: 'network.open_lan' }] });
      await context.clearCookies();
      await loginAs(page, MANAGER.email);
      await page.goto('/overview');
      const row = page.getByRole('region', { name: 'Cần Sếp xử lý' }).getByTestId('needs-boss-row').filter({ hasText: 'Cổng đang mở cho cả mạng' });
      await expect(row).toHaveCount(1);
      await expect(row).toContainText('Cổng Console đang mở cho cả mạng — nhờ Owner chọn cách truy cập từ xa.');
      await expect(row).not.toContainText('Bấm để');
      await row.getByRole('link', { name: 'Nhờ Owner xử lý' }).click();
      await expect(page).toHaveURL(/\/system\?tab=storage&focus=access$/);
      const card = page.getByRole('region', { name: 'Truy cập từ xa' });
      await expect(card).toBeVisible();
      await expect(card).toContainText('https://192.168.1.20:8443/login');
      await expect(card).toContainText('Nhờ Owner chọn cách truy cập từ xa.');
      await expect(card.getByTestId('access-commands')).toHaveCount(0);
      await expect(card).not.toContainText('genh remote');
      await expect(page.getByText('[object Object]')).toHaveCount(0);
    } finally {
      await loginAs(page, OWNER.email);
      for (const g of grants) await p3Hook(page.request, 'system', 'grant', { role: 'manager', permission: g.permission, scope: g.prev });
    }
  });

  test('Việc Sếp cần làm dòng 7: Kiểm tra trên localhost → Lỗi + câu hướng dẫn; Origin từ xa → Đạt, không đổi số bắt buộc (0/1)', async ({ page }) => {
    await page.goto('/guide/viec-sep');
    const row = page.getByRole('region', { name: 'Truy cập từ xa', exact: true });
    await expect(row).toContainText('Bấm nút này TRÊN ĐIỆN THOẠI sau khi mở Console bằng địa chỉ từ xa');
    // Chưa chọn cách truy cập từ xa (GH_PUBLIC_URL local) ⇒ REMOTE_NOT_CONFIGURED.
    await row.getByRole('button', { name: 'Kiểm tra' }).click();
    await expect(row).toContainText('Lỗi');
    await expect(row).toContainText('Chưa chọn cách truy cập từ xa — trên máy chủ chạy genh remote tailscale');
    await expect(row).toContainText('REMOTE_NOT_CONFIGURED');
    // Đã có địa chỉ từ xa nhưng trình duyệt đang mở bằng localhost ⇒ REMOTE_OPENED_ON_SERVER.
    await setAccess(page, { public_url: 'https://gen.tail1234.ts.net', mode: 'tailscale' });
    await row.getByRole('button', { name: 'Kiểm tra' }).click();
    await expect(row).toContainText('Đang mở trên chính máy chủ — mở Console trên điện thoại bằng địa chỉ ở Cài đặt › Sao lưu & cập nhật › Truy cập từ xa');
    await expect(row).toContainText('REMOTE_OPENED_ON_SERVER');
    await expect(page.getByText('Đã đạt 0/1 dòng bắt buộc')).toBeVisible();
    // Bấm từ điện thoại: Origin là địa chỉ Tailscale ⇒ Đạt (dòng không bắt buộc ⇒ vẫn 0/1).
    await page.route('**/api/v1/boss-checks/remote_access/run', (route) =>
      route.continue({ headers: { ...route.request().headers(), origin: 'https://gen-harness.tail1234.ts.net' } }),
    );
    await row.getByRole('button', { name: 'Kiểm tra' }).click();
    await expect(row.getByTestId('boss-result')).toContainText('Đạt · đã mở Console từ gen-harness.tail1234.ts.net');
    await expect(row).toContainText('Xong');
    await expect(page.getByText('Đã đạt 0/1 dòng bắt buộc')).toBeVisible();
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('đăng nhập bị giới hạn (429 LOGIN_RATE_LIMITED): câu thân thiện theo scope + Chi tiết kỹ thuật', async ({ page, context }) => {
    await context.clearCookies();
    let scope = 'email';
    await page.route('**/api/v1/auth/login', (route) =>
      route.fulfill({
        status: 429,
        contentType: 'application/problem+json',
        body: JSON.stringify({ status: 429, code: 'LOGIN_RATE_LIMITED', title: 'Đăng nhập sai quá nhiều lần', retry_after_s: 540, scope }),
      }),
    );
    await page.goto('/login');
    await page.getByLabel('Email').fill(OWNER.email);
    await page.getByLabel('Mật khẩu', { exact: true }).fill('mat-khau-sai-123');
    await page.getByRole('button', { name: /Đăng nhập/ }).click();
    const alert = page.getByRole('alert').filter({ hasText: 'Đăng nhập sai quá nhiều lần' });
    await expect(alert).toContainText('Đợi khoảng 9 phút');
    await expect(alert).toContainText('Owner: chạy ~/.gen-harness/bin/genh reset-password trên máy chủ');
    await expect(alert).toContainText('cấp mật khẩu tạm MỚI cho Owner');
    const tech = page.locator('details.tech-detail', { hasText: 'Chi tiết kỹ thuật' });
    await expect(tech).toBeVisible();
    await tech.locator('summary').click();
    await expect(tech).toContainText('LOGIN_RATE_LIMITED');
    // Bộ đếm chung cả mạng: không hứa "Owner đặt lại mật khẩu" gỡ được.
    scope = 'ip';
    await page.getByLabel('Mật khẩu', { exact: true }).fill('mat-khau-sai-123');
    await page.getByRole('button', { name: /Đăng nhập/ }).click();
    const ipAlert = page.getByRole('alert').filter({ hasText: 'từ cùng mạng' });
    await expect(ipAlert).toBeVisible();
    await expect(ipAlert).not.toContainText('Đặt lại mật khẩu');
    // Lệnh gỡ ngay ghi đủ đường dẫn và báo trước là cấp mật khẩu tạm mới cho Owner.
    await expect(ipAlert).toContainText('~/.gen-harness/bin/genh reset-password');
    await expect(ipAlert).toContainText('cấp mật khẩu tạm MỚI cho Owner');
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });
});
