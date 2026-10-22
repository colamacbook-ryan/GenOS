import { expect, test } from '@playwright/test';
import { AUDITOR, MANAGER, loginAs, loginAsOwner, mockHook, resetMock } from './support';

/**
 * v0.1.36 (F-6, F-2, F-46) — Sếp thấy ngay việc cần tự tay làm: dải "Cần Sếp xử lý" đầu Tổng quan (sự cố từ
 * `GET /system/health`), thẻ "Sức khoẻ hệ thống" ở Điều khiển hệ thống › Dữ liệu & lưu trữ, chuông có kind sự cố mới,
 * "Hạn lưu dữ liệu" (v0.1.40: sửa được — việc nền dọn thật), Trợ giúp hiện phiên bản máy chủ + phiên bản công cụ cài đặt (genh).
 */
test.describe('v0.1.36 — Cần Sếp xử lý & Sức khoẻ hệ thống', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  test('sự cố kênh rớt + cập nhật lỗi ⇒ dải 2 dòng; "Đăng nhập lại" mở Kết nối (link cũ tab Kênh tự chuyển)', async ({ page }) => {
    await mockHook(page.request, 'health', { issues: [{ kind: 'channel.down', title: 'Kênh Zalo đã ngắt kết nối' }, { kind: 'update.failed' }] });
    await page.goto('/overview');
    const strip = page.getByRole('region', { name: 'Cần Sếp xử lý' });
    await expect(strip).toBeVisible();
    await expect(strip.getByRole('heading', { name: 'Cần Sếp xử lý' })).toBeVisible();
    const rows = strip.getByTestId('needs-boss-row');
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText('Kênh Zalo đã ngắt kết nối');
    await expect(rows.nth(1)).toContainText('Cập nhật lên bản mới chưa thành công');
    for (const r of await rows.all()) await expect(r).toHaveAttribute('data-severity', 'bad');
    // Dải đứng ĐẦU trang: phần tử đầu tiên của màn Tổng quan.
    await expect(page.locator('.screen > :first-child')).toHaveAttribute('data-testid', 'needs-boss');
    await expect(rows.nth(0).getByRole('link', { name: 'Đăng nhập lại' })).toBeVisible();
    await expect(rows.nth(1).getByRole('link', { name: 'Xem & thử lại' })).toBeVisible();
    await expect(page.getByText('[object Object]')).toHaveCount(0);
    await strip.getByRole('link', { name: 'Đăng nhập lại' }).click();
    // v0.1.42 (F-7): /system?tab=channels → /connections.
    await expect(page).toHaveURL(/\/connections$/);
    await expect(page.getByRole('article', { name: 'Kênh Zalo' })).toBeVisible();
    await expect(page.getByText('[object Object]')).toHaveCount(0);

    await page.goto('/overview');
    await page.getByRole('region', { name: 'Cần Sếp xử lý' }).getByRole('link', { name: 'Xem & thử lại' }).click();
    await expect(page).toHaveURL(/\/system\?tab=storage$/);
    await expect(page.getByRole('region', { name: 'Sức khoẻ hệ thống' })).toBeVisible();
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('sự cố "warn" đưa vào trước vẫn xếp SAU sự cố "bad"', async ({ page }) => {
    await mockHook(page.request, 'health', { issues: [{ kind: 'model.auth_expired' }, { kind: 'channel.down' }] });
    await page.goto('/overview');
    const rows = page.getByRole('region', { name: 'Cần Sếp xử lý' }).getByTestId('needs-boss-row');
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toHaveAttribute('data-severity', 'bad');
    await expect(rows.nth(0)).toContainText('Kênh Zalo đã ngắt kết nối');
    await expect(rows.nth(1)).toHaveAttribute('data-severity', 'warn');
    await expect(rows.nth(1).getByRole('link', { name: 'Đăng nhập lại model' })).toBeVisible();
  });

  test('chưa có model (bước 4 chưa xong) ⇒ dòng "Chưa có model" nằm trong dải', async ({ page }) => {
    await page.route('**/api/v1/setup/follow-up', async (route) => {
      const res = await route.fetch();
      const items = (await res.json()) as Array<{ n: number; done: boolean; status?: string }>;
      const patched = items.map((s) => (s.n === 4 ? { ...s, done: false, status: 'skipped' } : s));
      if (!patched.some((s) => s.n === 4)) patched.push({ n: 4, done: false, status: 'skipped' });
      return route.fulfill({ response: res, json: patched });
    });
    await page.goto('/overview');
    const strip = page.getByRole('region', { name: 'Cần Sếp xử lý' });
    await expect(strip).toBeVisible();
    const noModel = strip.getByTestId('no-model');
    await expect(noModel).toContainText('Chưa có model');
    await expect(strip.getByTestId('needs-boss-row')).toHaveCount(0);
    await noModel.getByRole('link', { name: /Chọn model/ }).click();
    // v0.1.59: mọi nút "Chọn model" mở màn Thiết lập nguồn AI.
    await expect(page).toHaveURL(/\/setup\?buoc=nguon-ai$/);
  });

  test('cập nhật lỗi chỉ hiện MỘT lần ở Tổng quan (trong dải); thẻ đầy đủ có "Thử lại" chỉ ở Cài đặt › Sao lưu & cập nhật', async ({ page }) => {
    const failed = {
      current: 'v0.1.35', latest: 'v0.1.36', update_available: true, updater: 'systemd', linked: true, can_request: true,
      state: 'failed', message: 'Bản mới không khởi động được — đã tự quay về bản cũ (GH-E945)', from: 'v0.1.35', to: 'v0.1.36',
      started_at: new Date(Date.now() - 5 * 60_000).toISOString(), finished_at: new Date(Date.now() - 60_000).toISOString(),
      requested_at: null, release_url: null, release_notes: null, published_at: null, auto_update_enabled: true,
    };
    await page.route('**/api/v1/system/update', (route) => (route.request().method() === 'GET' ? route.fulfill({ json: failed }) : route.fallback()));
    await mockHook(page.request, 'health', { update: { state: 'failed', failed: true, finished_at: failed.finished_at } });
    await page.goto('/overview');
    const strip = page.getByRole('region', { name: 'Cần Sếp xử lý' });
    await expect(strip.getByTestId('needs-boss-row')).toHaveCount(1);
    await expect(strip).toContainText('Cập nhật lên bản mới chưa thành công');
    await expect(page.locator('[data-gen-target="overview.kpis"]')).toBeVisible();
    await expect(page.getByText('Cập nhật lên v0.1.36 chưa thành công')).toHaveCount(0);
    await expect(page.getByRole('button', { name: /Thử lại/ })).toHaveCount(0);

    await page.goto('/system?tab=storage');
    await expect(page.getByText('Cập nhật lên v0.1.36 chưa thành công')).toBeVisible();
    await expect(page.getByRole('button', { name: /Thử lại/ }).first()).toBeVisible();
    // v0.1.42 (F-61): Trợ giúp chỉ còn liên kết tới Cài đặt › Sao lưu & cập nhật.
    await page.goto('/help');
    await expect(page.getByTestId('help-update-link')).toHaveAttribute('href', '/system?tab=storage');
    await expect(page.getByText('Cập nhật lên v0.1.36 chưa thành công')).toHaveCount(0);
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('mặc định (khoẻ, đã có model) ⇒ Tổng quan không có dải', async ({ page }) => {
    await page.goto('/overview');
    await expect(page.locator('[data-gen-target="overview.kpis"]')).toBeVisible();
    await expect(page.getByTestId('needs-boss')).toHaveCount(0);
  });

  test('thẻ "Sức khoẻ hệ thống": đang chạy; Bộ xử lý nền ngừng 14 phút ⇒ "Đã ngừng 14 phút" + cách khởi động lại + dòng trong dải Tổng quan', async ({ page }) => {
    await page.goto('/system?tab=storage');
    const card = page.getByRole('region', { name: 'Sức khoẻ hệ thống' });
    await expect(card).toBeVisible();
    await expect(card.getByTestId('health-worker')).toContainText('Đang chạy');
    await expect(card.getByTestId('health-backup')).toContainText('Bản mới nhất');
    await card.getByText('Chi tiết kỹ thuật').click();
    await expect(card.getByText('backup_scheduled')).toBeVisible();
    await expect(card.getByText('gh:raw.dlq')).toBeVisible();

    await mockHook(page.request, 'health', { worker: { state: 'silent', alive: false, silent_minutes: 14, last_seen_at: new Date(Date.now() - 14 * 60_000).toISOString() } });
    await page.reload();
    const card2 = page.getByRole('region', { name: 'Sức khoẻ hệ thống' });
    await expect(card2.getByTestId('health-worker')).toHaveText(/Đã ngừng 14 phút/);
    await expect(card2.getByTestId('health-tip-worker')).toContainText('genh start');
    await page.goto('/overview');
    const strip = page.getByRole('region', { name: 'Cần Sếp xử lý' });
    await expect(strip.getByTestId('needs-boss-row')).toHaveCount(1);
    await expect(strip).toContainText('Bộ xử lý nền đã ngừng 14 phút');
    await expect(strip.getByRole('link', { name: 'Xem sức khoẻ' })).toHaveAttribute('href', '/system?tab=storage');
  });

  test('ổ đĩa sắp đầy: "Xem cách giải phóng" mở thẻ có hướng dẫn giải phóng kèm lệnh', async ({ page }) => {
    await mockHook(page.request, 'health', { disk: { state: 'low', free_bytes: 3 * 1024 ** 3, min_bytes: 5 * 1024 ** 3 } });
    await page.goto('/overview');
    const strip = page.getByRole('region', { name: 'Cần Sếp xử lý' });
    await expect(strip).toContainText('Ổ đĩa sắp hết chỗ');
    await expect(strip).toContainText('Còn 3,0 GB trống, cần tối thiểu 5,0 GB');
    await strip.getByRole('link', { name: 'Xem cách giải phóng' }).click();
    await expect(page).toHaveURL(/\/system\?tab=storage$/);
    const tip = page.getByRole('region', { name: 'Sức khoẻ hệ thống' }).getByTestId('health-tip-disk');
    await expect(tip).toContainText('Cách giải phóng chỗ trống');
    await expect(tip.getByText('docker system prune')).toBeVisible();
    await expect(tip).toContainText('Không xoá thư mục cài Gen-Harness');
    // Ổ đĩa chỉ được đo khi genh cập nhật (không phải mỗi phút) ⇒ hiện giờ đo và chỉ cách đo lại.
    await expect(tip).toContainText('genh update');
    await expect(tip).not.toContainText('mỗi phút');
    await expect(page.getByRole('region', { name: 'Sức khoẻ hệ thống' }).getByTestId('health-disk')).toContainText(/Sắp hết chỗ — còn 3,0 GB · đo lúc \d{2}\/\d{2} \d{2}:\d{2}/);
  });

  test('quá hạn sao lưu: "Mở mục Sao lưu" đưa tới đúng nút "Sao lưu ngay" (thấy ngay, không phải cuộn)', async ({ page }) => {
    await mockHook(page.request, 'health', { issues: [{ kind: 'backup.stale' }] });
    await page.goto('/overview');
    const strip = page.getByRole('region', { name: 'Cần Sếp xử lý' });
    await expect(strip).toContainText('Mở mục Sao lưu và bấm Sao lưu ngay');
    await expect(strip.getByRole('link', { name: 'Sao lưu ngay' })).toHaveCount(0);
    await strip.getByRole('link', { name: 'Mở mục Sao lưu' }).click();
    await expect(page).toHaveURL(/\/system\?tab=storage&focus=backup$/);
    const now = page.getByRole('region', { name: 'Sao lưu & khôi phục' }).getByRole('button', { name: 'Sao lưu ngay' });
    await expect(now).toBeVisible();
    await expect(now).toBeInViewport();
    await expect(now).toBeFocused();
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  // v0.1.40 (F-2): việc nền dọn theo hạn lưu đã chạy thật — bỏ nhãn tạm "Chưa tự xoá", nút Sửa hoạt động (PIN như cũ).
  test('Hạn lưu dữ liệu: nút Sửa hoạt động (PIN), Nhật ký hành động "Không áp dụng", không còn nhãn "Chưa tự xoá"', async ({ page }) => {
    const patches: unknown[] = [];
    page.on('request', (r) => {
      if (r.method() === 'PATCH' && r.url().includes('/retention-policies')) patches.push(r.postDataJSON());
    });
    await page.goto('/system?tab=storage');
    const panel = page.getByRole('region', { name: 'Hạn lưu dữ liệu' });
    await expect(panel.getByText('Mỗi tập dữ liệu một hạn — đổi cần mã PIN')).toBeVisible();
    await expect(page.getByText(/Chưa tự xoá/)).toHaveCount(0);
    await expect(panel.getByRole('columnheader', { name: 'Ẩn danh sau' })).toHaveCount(0);
    const logRow = panel.locator('tr', { has: page.getByText('Nhật ký hành động', { exact: true }) });
    await expect(logRow.getByText('Không áp dụng')).toBeVisible();
    await expect(logRow.getByRole('button', { name: /Sửa/ })).toHaveCount(0);
    await expect(panel.locator('tr', { has: page.getByText('agent.browser_jobs.result') }).getByText('14 ngày (cố định)')).toBeVisible();

    const rawRow = panel.locator('tr', { has: page.getByText('Tin gốc') });
    await expect(rawRow.getByText(/Lần dọn gần nhất: .* · đã xoá 0/)).toBeVisible();
    const edit = rawRow.getByRole('button', { name: /Sửa/ });
    await expect(edit).toBeEnabled();
    await edit.click();
    await rawRow.getByLabel(/Giữ trong \(ngày\)/).fill('400');
    await rawRow.getByRole('button', { name: 'Lưu', exact: true }).click();
    // Hỏi lại trước khi lưu: dữ liệu quá hạn bị XOÁ VĨNH VIỄN ở lượt dọn kế tiếp.
    const confirm = panel.getByTestId('retention-confirm');
    await expect(confirm).toContainText('Mọi tháng dữ liệu đã cũ hơn 400 ngày sẽ bị XOÁ VĨNH VIỄN');
    expect(patches).toHaveLength(0);
    await confirm.getByRole('button', { name: 'Đồng ý xoá dữ liệu quá hạn' }).click();
    const pin = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await expect(pin).toBeVisible();
    await page.getByLabel('Mã PIN — chữ số 1/6').click();
    await page.keyboard.type('246810');
    await expect(pin).toBeHidden();
    await expect(rawRow.getByText('400 ngày')).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Lưu', exact: true })).toHaveCount(0);
    expect(patches.at(-1)).toEqual({ dataset: 'raw.events', keep_days: 400, anonymize_after_days: null, confirm_delete: true });
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('chuông: kind channel.down mới ⇒ tăng 1, dải tự hiện không cần tải lại, bấm mở Kết nối', async ({ page }) => {
    await page.goto('/overview');
    const bell = page.locator('header .hd-bell');
    await expect(bell).toHaveAccessibleName('Thông báo — 1 chưa đọc');
    await expect(page.locator('[data-gen-target="overview.kpis"]')).toBeVisible();
    await expect(page.getByTestId('needs-boss')).toHaveCount(0);
    // API thật: health.raise_once mở dòng ops.health_alerts rồi gửi chuông — mock: ghi đè sức khoẻ trước, rồi chuông.
    await mockHook(page.request, 'health', { issues: [{ kind: 'channel.down' }] });
    await mockHook(page.request, 'notify', { kind: 'channel.down', title: 'Kênh Zalo đã ngắt kết nối', body: 'Đăng nhập lại để nhận tin.', link: '/system?tab=channels' });
    await expect(bell).toHaveAccessibleName('Thông báo — 2 chưa đọc');
    // notification.new kind sự cố ⇒ làm mới /system/health ngay (không chờ 60 giây).
    await expect(page.getByRole('region', { name: 'Cần Sếp xử lý' })).toContainText('Kênh Zalo đã ngắt kết nối');
    await bell.click();
    const dlg = page.getByRole('dialog', { name: 'Thông báo' });
    await expect(dlg.locator('.nt-item').first()).toContainText('Kênh Zalo đã ngắt kết nối');
    await expect(dlg.locator('.nt-item').first().locator('[data-icon]').first()).toHaveAttribute('data-icon', /plugs/);
    await dlg.getByRole('button', { name: /Kênh Zalo đã ngắt kết nối/ }).click();
    await expect(page).toHaveURL(/\/connections$/);
  });

  test('Trợ giúp hiện phiên bản máy chủ và phiên bản công cụ cài đặt (genh), không lặp dòng "phiên bản"', async ({ page }) => {
    await page.goto('/help');
    const about = page.locator('[data-gen-target="help.version"]');
    await expect(about.getByText('phiên bản máy chủ', { exact: true })).toBeVisible();
    await expect(about.getByTestId('about-image-version')).toHaveText(/^v\d+\.\d+\.\d+/);
    await expect(about.getByText('phiên bản công cụ cài đặt (genh)', { exact: true })).toBeVisible();
    await expect(about.getByTestId('about-genh-version')).toBeVisible();
    await expect(about.getByText('phiên bản', { exact: true })).toHaveCount(0);
  });

  test('vai trò không có system.read: không gọi /system/health, không thấy dải sự cố hệ thống', async ({ page }) => {
    await mockHook(page.request, 'health', { issues: [{ kind: 'channel.down' }, { kind: 'update.failed' }] });
    const healthCalls: string[] = [];
    page.on('request', (r) => {
      if (r.url().includes('/api/v1/system/health')) healthCalls.push(r.url());
    });
    await page.context().clearCookies();
    await loginAs(page, MANAGER.email);
    await page.goto('/overview');
    await expect(page.locator('[data-gen-target="overview.kpis"]')).toBeVisible();
    await page.waitForTimeout(500);
    await expect(page.getByTestId('needs-boss-row')).toHaveCount(0);
    await expect(page.getByText('Kênh Zalo đã ngắt kết nối')).toHaveCount(0);
    await page.goto('/help');
    await expect(page.locator('[data-gen-target="help.version"]')).toBeVisible();
    await page.waitForTimeout(300);
    expect(healthCalls).toEqual([]);
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('Auditor (chỉ đọc hệ thống): không có dải "Cần Sếp xử lý", không có nút hành động; thẻ Sức khoẻ vẫn xem được', async ({ page }) => {
    await mockHook(page.request, 'health', { issues: [{ kind: 'channel.down' }, { kind: 'backup.stale' }] });
    await page.context().clearCookies();
    await loginAs(page, AUDITOR.email);
    await page.goto('/overview');
    await expect(page.locator('[data-gen-target="overview.kpis"]')).toBeVisible();
    await page.waitForTimeout(500);
    await expect(page.getByTestId('needs-boss')).toHaveCount(0);
    await expect(page.getByText('Cần Sếp', { exact: false })).toHaveCount(0);
    for (const name of ['Mở mục Sao lưu', 'Đăng nhập lại', 'Xem & thử lại']) await expect(page.getByRole('link', { name })).toHaveCount(0);
    await page.goto('/system?tab=storage');
    await expect(page.getByRole('region', { name: 'Sức khoẻ hệ thống' }).getByTestId('health-backup')).toBeVisible();
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('/system/health lỗi 500 không làm sập Tổng quan; thẻ sức khoẻ báo lỗi thân thiện kèm mã lỗi', async ({ page }) => {
    await page.route('**/api/v1/system/health', (route) =>
      route.fulfill({
        status: 500,
        contentType: 'application/problem+json',
        body: JSON.stringify({ type: 'about:blank', title: 'Lỗi máy chủ', status: 500, code: 'INTERNAL', error_id: 'err-v0136', detail: { boom: { nested: true } } }),
      }),
    );
    await page.goto('/overview');
    await expect(page.locator('[data-gen-target="overview.kpis"]')).toBeVisible();
    await expect(page.getByTestId('needs-boss')).toHaveCount(0);
    await expect(page.getByText('Đã có lỗi xảy ra')).toHaveCount(0);
    await expect(page.getByText('[object Object]')).toHaveCount(0);

    await page.goto('/system?tab=storage');
    const card = page.getByRole('region', { name: 'Sức khoẻ hệ thống' });
    await expect(card).toBeVisible();
    await expect(card.getByRole('alert')).toContainText('Lỗi máy chủ. Mã lỗi err-v0136');
    await expect(card.getByRole('button', { name: 'Thử lại' })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Hạn lưu dữ liệu' })).toBeVisible();
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });
});
