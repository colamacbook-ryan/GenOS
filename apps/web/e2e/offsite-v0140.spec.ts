import { expect, test } from '@playwright/test';
import { MANAGER, loginAs, loginAsOwner, OWNER, p3Hook, resetMock } from './support';

/**
 * v0.1.40 (F-12) — Bản sao ngoài máy (ổ USB/NAS cắm vào máy chủ): sự cố `offsite.stale` ở dải "Cần Sếp xử lý" dẫn tới
 * đúng thẻ (focus=offsite); Owner chọn nơi lưu (PIN) ⇒ chờ máy chủ nhận; Bộ khôi phục hiện khoá + mã QR vẽ tại chỗ.
 */
test.describe('v0.1.40 — Bản sao ngoài máy', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
  });

  test('dải "Cần Sếp xử lý" có dòng offsite.stale ⇒ bấm hành động tới thẻ Bản sao ngoài máy (được focus, cảnh báo vàng)', async ({ page }) => {
    await loginAsOwner(page);
    // Lần thành công gần nhất 9 ngày trước ⇒ API (mock) mở sự cố offsite.stale (warn) + khối offsite trong /system/health.
    await p3Hook(page.request, 'system', 'offsite', { days_ago: 9 });
    await page.goto('/overview');
    const strip = page.getByRole('region', { name: 'Cần Sếp xử lý' });
    // Chữ của API thật (gh/health._eval_offsite / ACTIONS) — mock chép đúng.
    const row = strip.getByTestId('needs-boss-row').filter({ hasText: 'Bản sao ngoài máy đã cũ 9 ngày' });
    await expect(row).toHaveCount(1);
    await expect(row).toHaveAttribute('data-severity', 'warn');
    await row.getByRole('link', { name: 'Chọn nơi lưu / sao lưu ngay' }).click();
    await expect(page).toHaveURL(/\/system\?tab=storage&focus=offsite$/);

    const card = page.getByRole('region', { name: 'Bản sao ngoài máy' });
    await expect(card).toBeVisible();
    await expect(card).toBeFocused();
    await expect(card).toBeInViewport();
    await expect(card.getByTestId('offsite-latest')).toContainText('(9 ngày trước)');
    await expect(card.getByTestId('offsite-warning')).toHaveAttribute('data-tone', 'warn');
    await expect(card.getByTestId('offsite-warning')).toContainText('Hỏng ổ đĩa là mất hết dữ liệu');
    // Thẻ Bản sao ngoài máy đứng ngay sau Sao lưu & khôi phục.
    const order = await page.locator('section[aria-label]').evaluateAll((els) => els.map((e) => e.getAttribute('aria-label')));
    expect(order.indexOf('Bản sao ngoài máy')).toBe(order.indexOf('Sao lưu & khôi phục') + 1);
    // Thẻ Sức khoẻ có dòng Bản sao ngoài máy (vàng).
    await expect(page.getByRole('region', { name: 'Sức khoẻ hệ thống' }).getByTestId('health-offsite')).toHaveAttribute('data-tone', 'warn');
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('Owner chọn nơi lưu (PIN) ⇒ "Đang chờ máy chủ nhận…"; Bộ khôi phục hiện khoá + mã QR', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/system?tab=storage');
    const card = page.getByRole('region', { name: 'Bản sao ngoài máy' });
    await expect(card.getByTestId('offsite-latest')).toContainText('Bản sao ngoài máy gần nhất:');
    await expect(card.getByText('Mỗi Chủ nhật ~05:30')).toBeVisible();

    await card.getByRole('button', { name: 'Chọn nơi lưu bản sao ngoài máy' }).click();
    const dlg = page.getByRole('dialog', { name: 'Chọn nơi lưu bản sao ngoài máy' });
    await expect(dlg).toContainText('/media/<tên>/<ổ>');
    await dlg.getByLabel('Đường dẫn ổ USB/NAS trên máy chủ').fill('/media/sep/USB-MOI');
    await dlg.getByRole('button', { name: 'Lưu' }).click();
    const pin = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await expect(pin).toBeVisible();
    await page.getByLabel('Mã PIN — chữ số 1/6').click();
    await page.keyboard.type('246810');
    await expect(pin).toBeHidden();
    await expect(dlg).toBeHidden();
    await expect(card.getByTestId('offsite-request')).toContainText('Đang chờ máy chủ nhận yêu cầu đổi nơi lưu…');

    // Phiên PIN còn hiệu lực ⇒ Bộ khôi phục mở thẳng.
    await card.getByRole('button', { name: 'Bộ khôi phục' }).click();
    const kit = page.getByRole('dialog', { name: 'Bộ khôi phục' });
    await expect(kit.getByTestId('recovery-key')).toHaveText(/^[A-Z2-7]{5}(-[A-Z2-7]{5}){5}$/);
    await expect(kit.getByRole('img', { name: 'Mã QR của Khoá khôi phục' })).toBeVisible();
    await expect(kit).toContainText('Cất TÁCH khỏi ổ USB');
    await expect(kit.getByTestId('recovery-key-created')).toHaveText('· Khoá tạo ngày 01/10/2026');
    await expect(kit).toContainText('genh import --yes <tệp .ghbundle>');
    await kit.getByRole('button', { name: 'Đã cất xong' }).click();
    await expect(kit).toBeHidden();
    await expect(page.getByTestId('recovery-key')).toHaveCount(0);
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('Manager (system.read + system.manage): dải nhờ Owner, thẻ có "Sao lưu ra ổ ngoài ngay" nhưng không có nút chỉ Owner', async ({ page }) => {
    await loginAsOwner(page);
    const grants: Array<{ permission: string; prev: string }> = [];
    try {
      for (const permission of ['system.read', 'system.manage']) {
        const r = (await p3Hook(page.request, 'system', 'grant', { role: 'manager', permission, scope: 'all' })) as { prev: string };
        grants.push({ permission, prev: r.prev });
      }
      // Chưa chọn nơi lưu ⇒ offsite.stale "Chưa có bản sao ngoài máy" — thân cho Manager nhờ Owner (gh/health.NON_OWNER_BODIES).
      await p3Hook(page.request, 'system', 'offsite', { configured: false, dest: '', days_ago: null });
      await loginAs(page, MANAGER.email);
      await page.goto('/overview');
      const strip = page.getByRole('region', { name: 'Cần Sếp xử lý' });
      const row = strip.getByTestId('needs-boss-row').filter({ hasText: 'Chưa có bản sao ngoài máy' });
      await expect(row).toHaveCount(1);
      await expect(row).toContainText('Nhờ Owner cắm ổ USB/NAS và chọn nơi lưu bản sao ngoài máy');
      await expect(row).not.toContainText("bấm 'Chọn nơi lưu");
      await expect(row.getByRole('link', { name: 'Xem bản sao ngoài máy' })).toBeVisible();

      // Đã chọn nơi lưu, bản cũ 9 ngày ⇒ Manager bấm được "Sao lưu ra ổ ngoài ngay"; không có nút chỉ Owner.
      await p3Hook(page.request, 'system', 'offsite', { configured: true, dest: '/media/usb/gen-harness', days_ago: 9 });
      await page.goto('/system?tab=storage&focus=offsite');
      const card = page.getByRole('region', { name: 'Bản sao ngoài máy' });
      await expect(card.getByRole('button', { name: 'Sao lưu ra ổ ngoài ngay' })).toBeEnabled();
      for (const name of ['Chọn nơi lưu bản sao ngoài máy', 'Tải gói mang đi', 'Bộ khôi phục']) {
        await expect(card.getByRole('button', { name })).toHaveCount(0);
      }
      await expect(page.getByText('[object Object]')).toHaveCount(0);
    } finally {
      await loginAs(page, OWNER.email);
      for (const g of grants) await p3Hook(page.request, 'system', 'grant', { role: 'manager', permission: g.permission, scope: g.prev });
    }
  });

  test('Tải gói mang đi lỗi (409) qua khung ẩn — header khung như proxy Caddy — hiện lỗi thân thiện + Chi tiết kỹ thuật', async ({ page }) => {
    await loginAsOwner(page);
    // Mock đặt X-Frame-Options/CSP như Caddy (DENY cho mọi /api, riêng gói mang đi SAMEORIGIN do api tự đặt).
    await p3Hook(page.request, 'system', 'offsite', { portable_busy: true });
    await page.goto('/system?tab=storage');
    const card = page.getByRole('region', { name: 'Bản sao ngoài máy' });
    await card.getByRole('button', { name: 'Tải gói mang đi' }).click();
    const dlg = page.getByRole('dialog', { name: 'Tải gói mang đi?' });
    const frameRes = page.waitForResponse((r) => r.url().endsWith('/api/v1/system/offsite/portable') && r.status() === 409);
    await dlg.getByRole('button', { name: 'Tải về' }).click();
    const pin = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await expect(pin).toBeVisible();
    await page.getByLabel('Mã PIN — chữ số 1/6').click();
    await page.keyboard.type('246810');
    const res = await frameRes;
    expect(res.headers()['x-frame-options']).toBe('SAMEORIGIN');
    const err = card.getByTestId('offsite-portable-error');
    await expect(err).toContainText('Đang chuẩn bị một gói mang đi khác — chờ tải xong rồi thử lại.');
    await err.getByText('Chi tiết kỹ thuật').click();
    await expect(err).toContainText('PORTABLE_IN_PROGRESS');
    await expect(card.getByTestId('offsite-portable-preparing')).toHaveCount(0);
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });
});
