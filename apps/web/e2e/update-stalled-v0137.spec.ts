import { expect, test } from '@playwright/test';
import { AUDITOR, loginAs, loginAsOwner, mockHook, resetMock } from './support';

/** Khối `autostart` của `/system/health` khi Docker chưa bật tự chạy và thiếu linger (genh ghi run/autostart-status.json). */
const AUTOSTART_WARN = {
  state: 'warn', linger: 'no', linger_required: true, docker_enabled: 'no', docker_mode: 'system',
  checked_at: new Date(Date.now() - 30 * 60_000).toISOString(),
};

/**
 * v0.1.37 (F-34) — 'running' mà tiến trình genh trên máy chủ đã chết (máy tắt/khởi động lại giữa chừng): api trả
 * `state: 'stalled'` + `stalled_reason: 'process_gone'`. Thẻ cập nhật nói "bị dừng giữa chừng" (không phải "máy chủ chưa
 * nhận yêu cầu") và "Thử lại" gửi lại yêu cầu cập nhật.
 */
function updateState(over: Record<string, unknown> = {}) {
  return {
    current: 'v0.1.36',
    latest: 'v0.1.37',
    update_available: true,
    updater: 'systemd',
    linked: true,
    can_request: true,
    state: 'stalled',
    stalled_reason: 'process_gone',
    message: null,
    from: 'v0.1.36',
    to: 'v0.1.37',
    started_at: new Date(Date.now() - 75 * 60_000).toISOString(),
    finished_at: null,
    requested_at: null,
    release_url: null,
    release_notes: null,
    published_at: null,
    auto_update_enabled: true,
    ...over,
  };
}

test.describe('v0.1.37 — cập nhật bị dừng giữa chừng', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  test('process_gone: thẻ "bị dừng giữa chừng", bấm Thử lại gửi POST /system/update', async ({ page }) => {
    let posts = 0;
    let state = updateState();
    await page.route('**/api/v1/system/update', async (route) => {
      const method = route.request().method();
      if (method === 'POST') {
        posts += 1;
        state = updateState({ state: 'requested', stalled_reason: null, requested_at: new Date().toISOString() });
        return route.fulfill({ status: 202, json: state });
      }
      if (method === 'GET') return route.fulfill({ json: state });
      return route.fallback();
    });
    await page.goto('/system?tab=storage'); // v0.1.42 (F-61): thẻ cập nhật chỉ ở Cài đặt › Sao lưu & cập nhật
    await expect(page.getByText('Cập nhật lên v0.1.37 bị dừng giữa chừng')).toBeVisible();
    await expect(page.getByText(/Tiến trình cập nhật trên máy chủ không còn chạy/)).toBeVisible();
    await expect(page.getByText('Máy chủ chưa nhận yêu cầu cập nhật')).toHaveCount(0);
    await expect(page.getByText('[object Object]')).toHaveCount(0);
    await page.getByRole('button', { name: /Thử lại/ }).click();
    await expect.poll(() => posts).toBe(1);
    await expect(page.getByText(/Đang cập nhật lên v0.1.37/)).toBeVisible();
  });

  test('not_picked_up: giữ chữ cũ "Máy chủ chưa nhận yêu cầu cập nhật"', async ({ page }) => {
    const state = updateState({ stalled_reason: 'not_picked_up', started_at: null, requested_at: new Date(Date.now() - 20 * 60_000).toISOString() });
    await page.route('**/api/v1/system/update', (route) => (route.request().method() === 'GET' ? route.fulfill({ json: state }) : route.fallback()));
    await page.goto('/system?tab=storage'); // v0.1.42 (F-61): thẻ cập nhật chỉ ở Cài đặt › Sao lưu & cập nhật
    await expect(page.getByText('Máy chủ chưa nhận yêu cầu cập nhật')).toBeVisible();
    await expect(page.getByText(/bị dừng giữa chừng/)).toHaveCount(0);
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  for (const c of [
    {
      code: 'GH-E94B',
      message: 'Cập nhật bị dừng giữa chừng do tín hiệu dừng — đã tự quay về v0.1.36 (GH-E94B)',
      kicker: 'Cập nhật bị dừng giữa chừng',
    },
    // GH-E94A không có ở đây: genh ≥ v0.1.37 không ghi mã này vào run/update-status.json (chỉ unit test phòng hờ).
    {
      code: 'GH-E94B (cần chạy tiếp)',
      message: 'Cập nhật bị dừng giữa chừng (máy tắt, khởi động lại hoặc bị dừng tay) — CSDL đã sang bản mới, cần chạy tiếp — Sau khi máy bật lại: chạy genh update để đi tiếp lên bản mới (GH-E94B)',
      kicker: 'Cập nhật bị dừng giữa chừng — dữ liệu đã chuyển sang bản mới, cần chạy lại để hoàn tất',
    },
  ]) {
    test(`${c.code}: lời dẫn riêng, nguyên văn ở "Chi tiết kỹ thuật"`, async ({ page }) => {
      const state = updateState({ state: 'failed', stalled_reason: null, message: c.message, finished_at: new Date(Date.now() - 10 * 60_000).toISOString() });
      await page.route('**/api/v1/system/update', (route) => (route.request().method() === 'GET' ? route.fulfill({ json: state }) : route.fallback()));
      await page.goto('/system?tab=storage'); // v0.1.42 (F-61): thẻ cập nhật chỉ ở Cài đặt › Sao lưu & cập nhật
      await expect(page.getByText(c.kicker, { exact: true }).first()).toBeVisible();
      await page.getByRole('region', { name: 'Cập nhật phần mềm' }).getByText('Chi tiết kỹ thuật').click();
      await expect(page.getByRole('region', { name: 'Cập nhật phần mềm' }).getByText(c.message)).toBeVisible();
      await expect(page.getByText('[object Object]')).toHaveCount(0);
    });
  }

  test('thẻ Sức khoẻ: cập nhật stalled/process_gone ⇒ dòng "Cập nhật bị dừng giữa chừng"', async ({ page }) => {
    await mockHook(page.request, 'health', { update: { state: 'stalled', stalled_reason: 'process_gone', failed: false } });
    await page.goto('/system?tab=storage');
    const card = page.getByRole('region', { name: 'Sức khoẻ hệ thống' });
    await expect(card.getByText('Cập nhật bị dừng giữa chừng')).toBeVisible();
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('GH-E94B dừng gọn: thẻ Sức khoẻ vàng "bị dừng giữa chừng", dải "Cần Sếp xử lý" dòng vàng (không đỏ)', async ({ page }) => {
    await mockHook(page.request, 'health', { update: { state: 'failed', failed: true, interrupted: 'rolled_back' } });
    await page.goto('/overview');
    const strip = page.getByRole('region', { name: 'Cần Sếp xử lý' });
    const row = strip.getByTestId('needs-boss-row').filter({ hasText: 'bị dừng giữa chừng' });
    await expect(row).toHaveCount(1);
    await expect(row).toHaveAttribute('data-severity', 'warn');
    await expect(strip.getByText(/chưa thành công/)).toHaveCount(0);
    await page.goto('/system?tab=storage');
    const card = page.getByRole('region', { name: 'Sức khoẻ hệ thống' });
    await expect(card.getByText('Cập nhật bị dừng giữa chừng')).toBeVisible();
    await expect(card.getByText(/Lần cập nhật gần nhất lỗi/)).toHaveCount(0);
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('host.autostart: dải "Cần Sếp xử lý" có dòng kèm lệnh, nút "Xem cách bật" tới hướng dẫn có lệnh', async ({ page }) => {
    await mockHook(page.request, 'health', { autostart: AUTOSTART_WARN });
    await page.goto('/overview');
    const strip = page.getByRole('region', { name: 'Cần Sếp xử lý' });
    const rows = strip.getByTestId('needs-boss-row');
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toHaveAttribute('data-severity', 'warn');
    await expect(rows.first()).toContainText('Máy chủ có thể không tự chạy lại Gen-Harness khi bật lại máy');
    await expect(rows.first()).toContainText('sudo systemctl enable docker');
    await expect(rows.first()).toContainText('sudo loginctl enable-linger $USER');
    await expect(rows.first()).toContainText('genh status');
    // Nút dẫn tới thẻ Sức khoẻ — hướng dẫn từng bước, lệnh dạng mã chép được (thân chuông bị cắt 2 dòng).
    await rows.first().getByRole('link', { name: /Xem cách bật/ }).click();
    await expect(page).toHaveURL(/\/system\?tab=storage/);
    await expect(page.getByTestId('health-tip-autostart')).toBeVisible();
    await expect(page.getByTestId('health-tip-autostart').locator('code', { hasText: 'sudo systemctl enable docker' })).toBeVisible();
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('host.autostart: bấm chuông tới thẻ Sức khoẻ có lệnh (không phải mục chết)', async ({ page }) => {
    await page.goto('/overview');
    const bell = page.locator('header .hd-bell');
    await expect(bell).toHaveAccessibleName('Thông báo — 1 chưa đọc');
    // API thật: health.raise_once mở dòng ops.health_alerts rồi gửi chuông với link /system?tab=storage.
    await mockHook(page.request, 'health', { autostart: AUTOSTART_WARN });
    await mockHook(page.request, 'notify', {
      kind: 'host.autostart', title: 'Máy chủ có thể không tự chạy lại Gen-Harness khi bật lại máy',
      body: 'Docker chưa bật tự chạy khi mở máy — chạy một lần trên máy chủ: sudo systemctl enable docker · Chạy xong thì chạy genh status để cảnh báo tự hết',
      link: '/system?tab=storage',
    });
    await expect(bell).toHaveAccessibleName('Thông báo — 2 chưa đọc');
    await bell.click();
    const dlg = page.getByRole('dialog', { name: 'Thông báo' });
    await dlg.getByRole('button', { name: /có thể không tự chạy lại Gen-Harness/ }).click();
    await expect(page).toHaveURL(/\/system\?tab=storage$/);
    const tip = page.getByTestId('health-tip-autostart');
    await expect(tip.locator('code', { hasText: 'sudo systemctl enable docker' })).toBeVisible();
    await expect(tip.locator('code', { hasText: 'sudo loginctl enable-linger $USER' })).toBeVisible();
    await expect(tip.locator('code', { hasText: 'genh status' })).toBeVisible();
  });

  test('Auditor (chỉ system.read): thẻ Sức khoẻ có dòng "Tự chạy lại khi bật máy" + hướng dẫn có lệnh', async ({ page }) => {
    await mockHook(page.request, 'health', { autostart: AUTOSTART_WARN });
    await page.context().clearCookies();
    await loginAs(page, AUDITOR.email);
    await page.goto('/system?tab=storage');
    const card = page.getByRole('region', { name: 'Sức khoẻ hệ thống' });
    const row = card.getByTestId('health-autostart');
    await expect(row).toContainText('Tự chạy lại khi bật máy');
    await expect(row).toContainText('Chưa bật');
    await expect(row).toHaveAttribute('data-tone', 'warn');
    const tip = card.getByTestId('health-tip-autostart');
    await expect(tip.locator('code', { hasText: 'sudo systemctl enable docker' })).toBeVisible();
    await expect(tip.locator('code', { hasText: 'genh status' })).toBeVisible();
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });
});
