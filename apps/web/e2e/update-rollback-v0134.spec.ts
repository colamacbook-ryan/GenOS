import { expect, test } from '@playwright/test';
import { loginAsOwner, resetMock } from './support';

/**
 * v0.1.34 (F-10, F-11, F-33) — genh cập nhật an toàn: bản mới lỗi ⇒ genh tự quay về bản cũ, ghi run/update-blocked.json
 * và báo "failed" vào hộp thư; lịch đêm sau đó KHÔNG tự thử lại bản đó (và để nguyên hộp thư) nhưng "Thử lại"/"Cập nhật
 * ngay" vẫn chạy. Mock trả đúng khuôn `GET /system/update` mà api đọc từ hộp thư genh — thông điệp đúng dạng genh ghi
 * ("<việc> — <cách xử lý> (GH-E9xx)", cmd/genh consoleUpdateMessage): lời dẫn thân thiện theo mã, nguyên văn trong
 * "Chi tiết kỹ thuật".
 */
const ROLLBACK_MSG =
  '/api/v1/ready không trả 200 sau khi cập nhật — đã tự quay về bản cũ (khôi phục bản sao lưu) — Rollback đã hoàn tất tự động (khôi phục backups/20260930T030000Z-ab12cd34.pgcustom.enc + khởi động lại bằng bản cũ); lịch đêm sẽ không tự thử lại bản này. Xem docker compose logs sau khi rollback xong. (GH-E945)';
const DISK_MSG =
  'Ổ đĩa không đủ chỗ để tải bản mới — DỪNG LẠI, chưa đụng gì (còn 1.2 GB trống tại /var/lib/docker, cần tối thiểu 5 GB) — Giải phóng ổ đĩa (xem docker system df), rồi chạy lại genh update — lịch đêm cũng sẽ tự thử lại. (GH-E948)';

function updateState(over: Record<string, unknown> = {}) {
  return {
    current: 'v0.1.33',
    latest: 'v0.1.34',
    update_available: true,
    updater: 'systemd',
    linked: true,
    can_request: true,
    state: 'failed',
    message: ROLLBACK_MSG,
    from: 'v0.1.33',
    to: 'v0.1.34',
    started_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    finished_at: new Date(Date.now() - 60_000).toISOString(),
    requested_at: null,
    release_url: null,
    release_notes: null,
    published_at: null,
    auto_update_enabled: true,
    ...over,
  };
}

test.describe('v0.1.34 — cập nhật lỗi tự quay về bản cũ', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  test('thẻ "chưa thành công" báo đã quay về bản cũ, hiện thông điệp genh, "Thử lại" gửi yêu cầu cập nhật', async ({ page }) => {
    let posts = 0;
    let state = updateState();
    await page.route('**/api/v1/system/update', async (route) => {
      const method = route.request().method();
      if (method === 'POST') {
        posts += 1;
        state = updateState({ state: 'requested', message: null, requested_at: new Date().toISOString(), finished_at: null });
        return route.fulfill({ json: state });
      }
      if (method === 'GET') return route.fulfill({ json: state });
      return route.fallback();
    });
    await page.goto('/system?tab=storage'); // v0.1.42 (F-61): thẻ cập nhật chỉ ở Cài đặt › Sao lưu & cập nhật
    await expect(page.getByText('Cập nhật lên v0.1.34 chưa thành công')).toBeVisible();
    await expect(page.getByText('Hệ thống đã tự quay về bản đang dùng — dữ liệu giữ nguyên')).toBeVisible();
    await expect(page.getByText(/lịch đêm sẽ không tự cài lại bản này/)).toBeVisible();
    // Nguyên văn genh (mã lỗi, bản sao lưu) nằm trong "Chi tiết kỹ thuật".
    await page.getByRole('region', { name: 'Cập nhật phần mềm' }).getByText('Chi tiết kỹ thuật').click();
    await expect(page.getByText(/GH-E945/)).toBeVisible();
    await expect(page.getByText(/backups\/20260930T030000Z-ab12cd34/)).toBeVisible();
    // Không màn hình lỗi chung, không render đối tượng thô.
    await expect(page.getByText('Đã có lỗi xảy ra')).toHaveCount(0);
    await expect(page.getByText('[object Object]')).toHaveCount(0);
    await page.getByRole('button', { name: /Thử lại/ }).click();
    await expect.poll(() => posts).toBe(1);
    await expect(page.getByText(/Đang cập nhật lên v0.1.34/)).toBeVisible();
  });

  test('GH-E948 ổ đĩa đầy: báo chưa đụng gì + Owner phải giải phóng ổ đĩa, có số GB trong chi tiết', async ({ page }) => {
    const disk = updateState({ message: DISK_MSG, to: 'v0.1.34' });
    await page.route('**/api/v1/system/update', (route) =>
      route.request().method() === 'GET' ? route.fulfill({ json: disk }) : route.fallback(),
    );
    await page.goto('/system?tab=storage'); // v0.1.42 (F-61): thẻ cập nhật chỉ ở Cài đặt › Sao lưu & cập nhật
    await expect(page.getByText('Cập nhật lên v0.1.34 chưa thành công')).toBeVisible();
    await expect(page.getByText('Ổ đĩa máy chủ sắp đầy — chưa đụng gì, bản đang dùng vẫn chạy bình thường')).toBeVisible();
    await expect(page.getByText(/Cần giải phóng ổ đĩa trên máy chủ/)).toBeVisible();
    await expect(page.getByText(/quay về bản đang dùng/)).toHaveCount(0);
    await page.getByRole('region', { name: 'Cập nhật phần mềm' }).getByText('Chi tiết kỹ thuật').click();
    await expect(page.getByText(/còn 1\.2 GB trống/)).toBeVisible();
    await expect(page.getByText(/GH-E948/)).toBeVisible();
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('máy chủ chưa nhận yêu cầu từ nút bấm: không nhắc nút Thử lại không tồn tại, hiện lệnh chạy tay', async ({ page }) => {
    const noWatcher = updateState({ message: DISK_MSG, can_request: false, updater: null });
    await page.route('**/api/v1/system/update', (route) =>
      route.request().method() === 'GET' ? route.fulfill({ json: noWatcher }) : route.fallback(),
    );
    await page.goto('/system?tab=storage'); // v0.1.42 (F-61): thẻ cập nhật chỉ ở Cài đặt › Sao lưu & cập nhật
    await expect(page.getByText('Cập nhật lên v0.1.34 chưa thành công')).toBeVisible();
    await expect(page.getByText(/chạy lệnh bên dưới trên máy chủ/)).toBeVisible();
    await expect(page.getByText(/bấm Thử lại/)).toHaveCount(0);
    await expect(page.getByRole('button', { name: /Thử lại/ })).toHaveCount(0);
    await expect(page.getByText('~/.gen-harness/bin/genh update')).toBeVisible();
  });

  test('quay về bản cũ CŨNG thất bại (rollback_failed từ api): thẻ "Cần xử lý tay", không nhắc khôi phục bản sao lưu', async ({ page }) => {
    const failed = updateState({
      message: '/api/v1/ready không trả 200 sau khi cập nhật — CSDL chưa bị đụng, NHƯNG khởi động lại bằng bản cũ chưa trọn — chạy tay docker compose up -d --remove-orphans. (GH-E945)',
      blocked_version: 'v0.1.34', blocked_rollback_failed: true,
    });
    await page.route('**/api/v1/system/update', (route) =>
      route.request().method() === 'GET' ? route.fulfill({ json: failed }) : route.fallback(),
    );
    await page.goto('/system?tab=storage'); // v0.1.42 (F-61): thẻ cập nhật chỉ ở Cài đặt › Sao lưu & cập nhật
    await expect(page.getByText('Cần xử lý tay — tự quay về bản cũ chưa trọn')).toBeVisible();
    await expect(page.getByText(/bản sao lưu cần khôi phục/)).toHaveCount(0);
    await expect(page.getByText(/dữ liệu giữ nguyên/)).toHaveCount(0);
  });

  test('bản mới nhất đang bị chặn: không hứa "Tự cài đêm", bảo bấm Cập nhật ngay để thử lại', async ({ page }) => {
    const old = updateState({
      finished_at: '2020-01-01T00:00:00Z', started_at: '2020-01-01T00:00:00Z',
      published_at: '2020-01-01T00:00:00Z', blocked_version: 'v0.1.34',
    });
    await page.route('**/api/v1/system/update', (route) =>
      route.request().method() === 'GET' ? route.fulfill({ json: old }) : route.fallback(),
    );
    await page.goto('/system?tab=storage'); // v0.1.42 (F-61): thẻ cập nhật chỉ ở Cài đặt › Sao lưu & cập nhật
    await expect(page.getByText('Có bản mới v0.1.34')).toBeVisible();
    await expect(page.getByText(/lịch đêm không tự cài lại — bấm Cập nhật ngay để thử lại/)).toBeVisible();
    await expect(page.getByText(/Tự cài đêm/)).toHaveCount(0);
  });

  test('lỗi đã cũ không treo thẻ đỏ mãi — quay về "Có bản mới" với "Cập nhật ngay"', async ({ page }) => {
    const old = updateState({ finished_at: '2020-01-01T00:00:00Z', started_at: '2020-01-01T00:00:00Z' });
    await page.route('**/api/v1/system/update', (route) =>
      route.request().method() === 'GET' ? route.fulfill({ json: old }) : route.fallback(),
    );
    await page.goto('/system?tab=storage'); // v0.1.42 (F-61): thẻ cập nhật chỉ ở Cài đặt › Sao lưu & cập nhật
    await expect(page.getByText('Có bản mới v0.1.34')).toBeVisible();
    await expect(page.getByText(/chưa thành công/)).toHaveCount(0);
    await expect(page.getByRole('button', { name: /Cập nhật ngay/ })).toBeVisible();
  });
});
