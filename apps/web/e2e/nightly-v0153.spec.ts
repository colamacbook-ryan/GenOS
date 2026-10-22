import { expect, test, type Page } from '@playwright/test';
import { AUDITOR, loginAs, loginAsOwner, mockHook, resetMock } from './support';

/**
 * v0.1.53 (F-99, F-96) — Console nói thẳng NGUYÊN NHÂN "máy chủ chưa nhận yêu cầu" (linger tắt / trình nhận yêu cầu lỗi /
 * không xoá được tệp yêu cầu), thẻ Sức khoẻ có dòng "Tự cập nhật đêm" + hướng dẫn bật lại, và gợi ý "Tự cài…" theo cách
 * lịch đêm chọn bản đủ 24 giờ.
 */

/** Khối `nightly` của `/system/health` khi lịch đêm im 3 ngày và linger tắt (genh ghi run/nightly-status.json). */
const NIGHTLY_WARN = {
  state: 'warn', last_run_at: new Date(Date.now() - 3 * 24 * 3600_000).toISOString(), next_run_at: new Date(Date.now() + 14 * 3600_000).toISOString(),
  days_since: 3, opted_out: false, linger: 'no', checked_at: new Date(Date.now() - 4 * 60_000).toISOString(),
};

function updateState(over: Record<string, unknown> = {}) {
  return {
    current: 'v0.1.52', latest: 'v0.1.53', update_available: true, updater: 'systemd', linked: true, can_request: true,
    state: 'stalled', stalled_reason: 'linger_off', message: null, from: 'v0.1.52', to: 'v0.1.53', started_at: null,
    finished_at: null, requested_at: new Date(Date.now() - 20 * 60_000).toISOString(), release_url: null, release_notes: null,
    published_at: null, auto_update_enabled: true, nightly_candidates: [], nightly: null, ...over,
  };
}

/** `POST /api/v1/__mock/nightly` — hook mới của mock (test/mock-api.ts); `mockHook` của support.ts chưa liệt kê tên này. */
async function nightlyHook(page: Page, data: unknown) {
  const res = await page.request.post('/api/v1/__mock/nightly', { data });
  if (res.status() >= 400) throw new Error(`mock hook nightly failed: ${res.status()}`);
}

test.describe('v0.1.53 — lịch tự cập nhật đêm: nguyên nhân + cảnh báo', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  test('stalled linger_off: thẻ nói tiến trình nền chỉ chạy khi có người đăng nhập + lệnh enable-linger chép được', async ({ page }) => {
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
    await page.goto('/system?tab=storage');
    const card = page.getByRole('region', { name: 'Cập nhật phần mềm' });
    await expect(card.getByText('Máy chủ chưa nhận yêu cầu cập nhật')).toBeVisible();
    await expect(card.getByText('Tiến trình nền trên máy chủ chỉ chạy khi có người đăng nhập — cần bật linger')).toBeVisible();
    await expect(card.getByText('Chạy một lần lệnh dưới đây trên máy chủ (máy hỏi mật khẩu đăng nhập máy), rồi bấm Thử lại.')).toBeVisible();
    const cmd = card.locator('code', { hasText: 'sudo loginctl enable-linger $USER' });
    await expect(cmd).toBeVisible();
    // Chép được: chữ chọn được (không bị tắt user-select) và là một dòng lệnh nguyên vẹn, không dấu chấm dính sau lệnh.
    await expect(cmd).toHaveText('sudo loginctl enable-linger $USER');
    expect(await cmd.evaluate((el) => getComputedStyle(el).userSelect)).not.toBe('none');
    // Không còn lệnh cập nhật chung: lệnh ở đây là lệnh bật linger.
    await expect(card.locator('code', { hasText: 'genh update' })).toHaveCount(0);
    await expect(page.getByText('[object Object]')).toHaveCount(0);
    await card.getByRole('button', { name: /Thử lại/ }).click();
    await expect.poll(() => posts).toBe(1);
  });

  test('stalled watcher_failed: thẻ nói trình nhận yêu cầu đang lỗi + lệnh cập nhật để bật lại', async ({ page }) => {
    const state = updateState({ stalled_reason: 'watcher_failed' });
    await page.route('**/api/v1/system/update', (route) => (route.request().method() === 'GET' ? route.fulfill({ json: state }) : route.fallback()));
    await page.goto('/system?tab=storage');
    const card = page.getByRole('region', { name: 'Cập nhật phần mềm' });
    await expect(card.getByText('Trình nhận yêu cầu trên máy chủ đang lỗi')).toBeVisible();
    await expect(card.getByText('Chạy lệnh dưới đây trên máy chủ một lần để bật lại, rồi bấm Thử lại.')).toBeVisible();
    await expect(card.locator('code', { hasText: 'genh update' })).toBeVisible();
    await expect(card.locator('code', { hasText: 'enable-linger' })).toHaveCount(0);
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('GH-E94C: không xoá được tệp yêu cầu — lời dẫn riêng, thông điệp genh ở Chi tiết kỹ thuật', async ({ page }) => {
    const message = 'Không xoá được tệp yêu cầu trong run/request nên chưa làm gì — kiểm quyền thư mục run/request rồi thử lại (GH-E94C)';
    const state = updateState({ state: 'failed', stalled_reason: null, message, finished_at: new Date(Date.now() - 5 * 60_000).toISOString() });
    await page.route('**/api/v1/system/update', (route) => (route.request().method() === 'GET' ? route.fulfill({ json: state }) : route.fallback()));
    await page.goto('/system?tab=storage');
    const card = page.getByRole('region', { name: 'Cập nhật phần mềm' });
    await expect(card.getByText('Máy chủ không xoá được tệp yêu cầu — chưa đụng gì')).toBeVisible();
    await expect(card.getByText(/kiểm quyền thư mục run\/request/).first()).toBeVisible();
    await card.getByText('Chi tiết kỹ thuật').click();
    await expect(card.getByText(message)).toBeVisible();
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('thẻ Sức khoẻ: lịch đêm im 3 ngày ⇒ dòng "Tự cập nhật đêm: Chưa chạy 3 ngày" + hướng dẫn bật lại có lệnh', async ({ page }) => {
    await mockHook(page.request, 'health', { nightly: NIGHTLY_WARN });
    await page.goto('/system?tab=storage');
    const card = page.getByRole('region', { name: 'Sức khoẻ hệ thống' });
    const row = card.getByTestId('health-nightly');
    await expect(row).toContainText('Tự cập nhật đêm');
    await expect(row).toContainText('Chưa chạy 3 ngày');
    await expect(row).toHaveAttribute('data-tone', 'warn');
    const tip = card.getByTestId('health-tip-nightly');
    await expect(tip).toContainText('Cách bật lại lịch tự cập nhật đêm');
    await expect(tip.locator('code', { hasText: 'genh auto-update status' })).toBeVisible();
    await expect(tip.locator('code', { hasText: 'sudo loginctl enable-linger $USER' })).toBeVisible();
    await expect(tip.locator('code', { hasText: 'genh auto-update enable' })).toBeVisible();
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('dải "Cần Sếp xử lý": sự cố host.nightly kèm lệnh, nút "Xem cách bật lại" tới hướng dẫn trong thẻ Sức khoẻ', async ({ page }) => {
    await mockHook(page.request, 'health', { nightly: NIGHTLY_WARN });
    await page.goto('/overview');
    const strip = page.getByRole('region', { name: 'Cần Sếp xử lý' });
    const row = strip.getByTestId('needs-boss-row').filter({ hasText: 'Lịch tự cập nhật đêm chưa chạy 3 ngày' });
    await expect(row).toHaveCount(1);
    await expect(row).toHaveAttribute('data-severity', 'warn');
    await expect(row).toContainText('genh auto-update status');
    await expect(row).toContainText('sudo loginctl enable-linger $USER');
    await expect(row).toContainText('genh auto-update enable');
    await row.getByRole('link', { name: /Xem cách bật lại/ }).click();
    await expect(page).toHaveURL(/\/system\?tab=storage&focus=health/);
    await expect(page.getByTestId('health-tip-nightly')).toBeVisible();
    await expect(page.getByTestId('health-tip-nightly').locator('code', { hasText: 'genh auto-update enable' })).toBeVisible();
  });

  test('thẻ Sức khoẻ: ok / Sếp đã tắt / chưa rõ; không có khối thì không có dòng', async ({ page }) => {
    await page.goto('/system?tab=storage');
    const card = page.getByRole('region', { name: 'Sức khoẻ hệ thống' });
    await expect(card.getByTestId('health-worker')).toBeVisible();
    await expect(card.getByTestId('health-nightly')).toHaveCount(0);
    const states: Array<[Record<string, unknown>, RegExp]> = [
      [{ state: 'ok', days_since: 0, linger: 'yes', last_run_at: new Date(Date.now() - 2 * 3600_000).toISOString() }, /Bình thường · chạy lần cuối \d{2}\/\d{2} \d{2}:\d{2}/],
      [{ state: 'off', days_since: null, opted_out: true, linger: 'yes' }, /Tắt \(Sếp đã tắt\)/],
      // Bản cài phụ: lịch dùng chung thuộc bản cài khác còn sống — không cảnh báo, không có lệnh bật (bị từ chối).
      [{ state: 'other', reason: null, days_since: null, linger: 'yes' }, /Lịch đêm do bản cài khác trên máy này quản lý/],
      [{ state: 'unknown', days_since: null, last_run_at: null, next_run_at: null, opted_out: null, linger: 'unknown', checked_at: null }, /Chưa rõ/],
    ];
    for (const [over, text] of states) {
      await mockHook(page.request, 'health', { nightly: { ...NIGHTLY_WARN, ...over } });
      await page.reload();
      await expect(page.getByRole('region', { name: 'Sức khoẻ hệ thống' }).getByTestId('health-nightly')).toContainText(text);
      await expect(page.getByTestId('health-tip-nightly')).toHaveCount(0);
    }
  });

  test('Sếp đã tắt mà lịch vẫn bật: thẻ nói thật (không "Tắt"), hướng dẫn hai lựa chọn; chuông cùng cách nói', async ({ page }) => {
    await mockHook(page.request, 'health', { nightly: { ...NIGHTLY_WARN, reason: 'opted_out_running', opted_out: true, days_since: 0, linger: 'yes' } });
    await page.goto('/system?tab=storage');
    const card = page.getByRole('region', { name: 'Sức khoẻ hệ thống' });
    const row = card.getByTestId('health-nightly');
    await expect(row).toContainText('Sếp đã tắt nhưng lịch vẫn bật');
    await expect(row).not.toContainText('Tắt (Sếp đã tắt)');
    await expect(row).toHaveAttribute('data-tone', 'warn');
    const tip = card.getByTestId('health-tip-nightly');
    await expect(tip.locator('code', { hasText: 'genh auto-update disable' })).toBeVisible();
    await expect(tip.locator('code', { hasText: 'genh auto-update enable' })).toBeVisible();
    await page.goto('/overview');
    const strip = page.getByRole('region', { name: 'Cần Sếp xử lý' });
    await expect(strip.getByTestId('needs-boss-row').filter({ hasText: 'Sếp đã tắt tự cập nhật đêm nhưng lịch vẫn bật' })).toHaveCount(1);
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('Auditor (chỉ system.read): thẻ Sức khoẻ có dòng "Tự cập nhật đêm" + hướng dẫn', async ({ page }) => {
    await mockHook(page.request, 'health', { nightly: NIGHTLY_WARN });
    await page.context().clearCookies();
    await loginAs(page, AUDITOR.email);
    await page.goto('/system?tab=storage');
    const card = page.getByRole('region', { name: 'Sức khoẻ hệ thống' });
    await expect(card.getByTestId('health-nightly')).toContainText('Chưa chạy 3 ngày');
    await expect(card.getByTestId('health-tip-nightly').locator('code', { hasText: 'genh auto-update enable' })).toBeVisible();
  });

  test('gợi ý "Tự cài…": ứng viên 25 giờ cài đêm nay, bản mới nhất đợi đủ 24 giờ', async ({ page }) => {
    const now = Date.now();
    // Lần ~03:00 giờ Việt Nam (UTC+7, không có giờ mùa hè; playwright.config đặt timezoneId) đầu tiên sau `now`. Mốc "đủ 24 giờ" của
    // bản mới nhất đặt SAU lần chạy đó 1 giờ (chứ không phải now + 23 giờ) để test không phụ thuộc giờ chạy CI: now + 23 giờ rơi
    // vào trước 03:00 khi CI chạy 03:00–04:00 giờ VN ⇒ bản mới nhất đã đủ hạn kịp đêm nay và gợi ý đổi thành "Tự cài đêm dd/mm".
    const VN = 7 * 3600_000;
    const day = 24 * 3600_000;
    const local = now + VN; // đồng hồ treo tường giờ VN, tính như UTC
    let run03 = Math.floor(local / day) * day + 3 * 3600_000;
    if (run03 < local) run03 += day;
    const firstRun = run03 - VN;
    await nightlyHook(page, {
      update: {
        current: 'v0.1.52', latest: 'v0.1.54', auto_update_enabled: true, published_at: new Date(now - 3600_000).toISOString(),
        nightly_candidates: [
          { tag: 'v0.1.53', eligible_at: new Date(now - 3600_000).toISOString() },
          { tag: 'v0.1.54', eligible_at: new Date(firstRun + 3600_000).toISOString() },
        ],
      },
    });
    await page.goto('/system?tab=storage');
    const card = page.getByRole('region', { name: 'Cập nhật phần mềm' });
    await expect(card.getByText('Có bản mới v0.1.54')).toBeVisible();
    await expect(card.getByText(/Tự cài v0\.1\.53 đêm \d{2}\/\d{2} \(~03:00\) — v0\.1\.54 tự cài sau khi đủ 24 giờ \(đêm \d{2}\/\d{2}\)/)).toBeVisible();
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('api cũ (không có ứng viên): gợi ý "Tự cài đêm dd/mm" như trước', async ({ page }) => {
    await nightlyHook(page, {
      update: { current: 'v0.1.52', latest: 'v0.1.53', auto_update_enabled: true, published_at: new Date(Date.now() - 30 * 3600_000).toISOString(), nightly_candidates: [] },
    });
    await page.goto('/system?tab=storage');
    const card = page.getByRole('region', { name: 'Cập nhật phần mềm' });
    await expect(card.getByText(/Tự cài đêm \d{2}\/\d{2} \(~03:00\) — hoặc bấm Cập nhật ngay/)).toBeVisible();
    await expect(card.getByText(/tự cài sau khi đủ 24 giờ/)).toHaveCount(0);
  });
});
