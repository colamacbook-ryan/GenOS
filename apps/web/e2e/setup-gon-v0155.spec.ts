import { expect, test } from '@playwright/test';
import { apiCall, expectOwnerFront, finishSetupOne, loginAsOwner, resetMock } from './support';

/**
 * v0.1.55 (G2 — Thiết lập gọn, mock, tất định), cập nhật v0.1.59 (g5-thiet-lap: Thiết lập MỘT màn, 3 bước).
 * Từ máy mới tới Mặt tiền chỉ qua 3 màn (Tài khoản · Nguồn AI · Xong); mọi thứ khác điền sẵn mặc định: xưng hô "Sếp", múi giờ / tiền
 * tệ VN, và khi Hoàn tất bước 5–11 chưa làm được ghi "để sau" = dùng mặc định của máy chủ (bộ quy tắc sàng lọc khởi đầu, sao lưu hằng
 * ngày 02:00). Bước 8–11 làm sau ở Hướng dẫn thiết lập (/guide/n): mẫu agent không tin thử, mức tự trị 4 + một dòng ghi chú ranh giới,
 * thẻ gợi ý "Mời đội ngũ" có "Để sau", sao lưu 02:00 giữ 7 bản.
 */

interface Sent {
  method: string;
  path: string;
  body: Record<string, unknown>;
}

test('Thiết lập gọn: từ máy mới tới Mặt tiền chỉ qua 3 màn; xưng hô / múi giờ / tiền tệ dùng mặc định; bước 5–11 dùng mặc định máy chủ', async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await resetMock(page.request, 'fresh');

  // Ghi lại payload từng PUT/POST /setup/steps/* do giao diện gửi (chỉ kiểm hình dạng — không in mật khẩu / PIN).
  const sent: Sent[] = [];
  page.on('request', (r) => {
    const m = /\/api\/v1(\/setup\/steps\/\d+(?:\/skip)?)$/.exec(new URL(r.url()).pathname);
    if (m && r.method() !== 'GET') sent.push({ method: r.method(), path: m[1], body: (r.postDataJSON() ?? {}) as Record<string, unknown> });
  });

  await finishSetupOne(page);
  await expectOwnerFront(page);

  const body = (path: string) => sent.filter((s) => s.method === 'PUT' && s.path === path).map((s) => s.body);
  // Bước 3: xưng hô điền sẵn "Sếp", múi giờ / tiền tệ mặc định — chỉ tên doanh nghiệp là ô phải gõ.
  expect(body('/setup/steps/3')).toEqual([expect.objectContaining({ org_name: 'Genesis Trading', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND', self_name: 'Sếp', bot_calls_me: 'Sếp' })]);
  // Giao diện KHÔNG gửi bước 7–11 (không còn form ở trình thiết lập): máy chủ tự dùng mặc định khi "để sau".
  for (const n of [5, 6, 7, 8, 9, 10, 11]) expect(body(`/setup/steps/${n}`)).toEqual([]);
  expect(sent.filter((s) => s.method === 'POST').map((s) => s.path)).toEqual([5, 6, 7, 8, 9, 10, 11].map((n) => `/setup/steps/${n}/skip`));

  // Trạng thái cuối: 1–4 và 12 xong; 5–11 "để sau".
  const state = (await apiCall(page, 'GET', '/setup/state')) as { finished: boolean; steps: Array<{ n: number; status: string }> };
  expect(state.finished).toBe(true);
  expect(state.steps.filter((s) => s.status === 'skipped').map((s) => s.n)).toEqual([5, 6, 7, 8, 9, 10, 11]);

  // "Để sau" = dùng mặc định: bộ quy tắc sàng lọc khởi đầu đã nạp, lịch sao lưu hằng ngày đã bật.
  const rules = (await apiCall(page, 'GET', '/rules')) as Array<{ code: string }>;
  expect(rules.map((r) => r.code)).toEqual(expect.arrayContaining(['R-01', 'R-06']));
  const backups = (await apiCall(page, 'GET', '/system/backups')) as { schedule: { time_of_day?: string } | null };
  expect(backups.schedule).not.toBeNull();
});

test('Thiết lập gọn: bước 10 mở từ Hướng dẫn hiện thẳng form mời (danh sách rỗng ⇒ Tiếp tục tắt, lối ra là Quay lại); bước 11 "Đổi lịch" gập sẵn nhưng đổi được', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await resetMock(page.request, 'fresh', { startAtStep: 10 });
  await loginAsOwner(page);
  await page.goto('/guide/10');
  await expect(page.getByRole('heading', { name: 'Mời đội ngũ' })).toBeVisible();
  await expect(page.getByTestId('step10-suggestion')).toHaveCount(0); // thẻ gợi ý "Để sau" chỉ có trong trình thiết lập (bản test đơn vị giữ)
  await expect(page.getByRole('button', { name: 'Thêm người' })).toBeVisible();
  // v0.1.68 (F038): danh sách rỗng KHÔNG phải "đã mời" ⇒ Tiếp tục tắt, lối ra là "Quay lại" (Hướng dẫn không có nút Để sau; không bắt buộc mời ai).
  await expect(page.getByRole('button', { name: /Tiếp tục/ })).toBeDisabled();
  await expect(page.getByText('chưa muốn mời thì bấm "Quay lại"')).toBeVisible();
  await page.getByRole('button', { name: 'Quay lại' }).click();
  await expect(page).toHaveURL(/\/guide$/);

  await page.goto('/guide/11');
  await expect(page.getByRole('heading', { name: 'Sao lưu' })).toBeVisible();
  await expect(page.getByTestId('step11-default')).toContainText('Hằng ngày lúc 02:00, giữ 7 bản theo ngày, 4 theo tuần, 12 theo tháng');
  const advanced = page.locator('details', { has: page.getByText('Đổi lịch (không bắt buộc)') });
  await expect(advanced).not.toHaveAttribute('open', /.*/);
  await page.getByText('Đổi lịch (không bắt buộc)').click();
  await expect(page.getByLabel('Giờ chạy (HH:MM)')).toHaveValue('02:00');
  await expect(page.getByText('[object Object]')).toHaveCount(0);
});

test('Thiết lập gọn: bước 8–9 mở ở Hướng dẫn — mẫu có sẵn, không tin thử; một dòng ghi chú thay ô tích (mức 4)', async ({ page }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await resetMock(page.request, 'fresh', { startAtStep: 8 });
  await loginAsOwner(page);
  const sent: Sent[] = [];
  page.on('request', (r) => {
    const m = /\/api\/v1(\/setup\/steps\/\d+)$/.exec(new URL(r.url()).pathname);
    if (m && r.method() === 'PUT') sent.push({ method: r.method(), path: m[1], body: (r.postDataJSON() ?? {}) as Record<string, unknown> });
  });

  await page.goto('/guide/8');
  await expect(page.getByRole('heading', { name: 'Agent đầu tiên' })).toBeVisible();
  await expect(page.getByTestId('step8-note')).toBeVisible();
  await expect(page.getByLabel('Tên agent')).not.toHaveValue('');
  const next = page.getByRole('button', { name: /Tiếp tục/ });
  await expect(next).toBeEnabled();
  await next.click();
  await expect(page.getByText(/Đã tạo agent/)).toBeVisible();
  await next.click();
  await expect(page).toHaveURL(/\/guide$/);

  await page.goto('/guide/9');
  await expect(page.getByRole('heading', { name: 'Tự trị & ranh giới' })).toBeVisible();
  await expect(page.getByTestId('step9-ack-note')).toContainText('Bấm Tiếp tục nghĩa là Sếp đã đọc các ranh giới trên');
  await expect(page.getByTestId('step9-ack-note')).toContainText('50.000.000');
  await expect(page.getByRole('checkbox')).toHaveCount(0);
  await expect(next).toBeEnabled();
  await next.click();
  await expect(page).toHaveURL(/\/guide$/);

  const s8 = sent.filter((s) => s.path === '/setup/steps/8');
  expect(s8).toHaveLength(1);
  expect(s8[0].body).toEqual(expect.objectContaining({ name: expect.any(String), role_desc: expect.any(String) }));
  expect(s8[0].body).not.toHaveProperty('try_message');
  expect(sent.filter((s) => s.path === '/setup/steps/9').map((s) => s.body)).toEqual([expect.objectContaining({ autonomy_level: 4, ack_boundaries: true })]);
});

test('Thiết lập gọn: bước 7 mở ở Hướng dẫn chỉ hỏi "Sếp làm ngành nào?"; lịch / quy tắc / trọng số nằm trong "Nâng cao" (đóng sẵn)', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await resetMock(page.request, 'fresh', { startAtStep: 7 });
  await loginAsOwner(page);
  const sent: Sent[] = [];
  page.on('request', (r) => {
    if (/\/api\/v1\/setup\/steps\/7$/.test(new URL(r.url()).pathname) && r.method() === 'PUT') sent.push({ method: 'PUT', path: '/setup/steps/7', body: (r.postDataJSON() ?? {}) as Record<string, unknown> });
  });
  await page.goto('/guide/7');
  await expect(page.getByRole('heading', { name: 'Sàng lọc dữ liệu' })).toBeVisible();
  await expect(page.getByTestId('step7-industry')).toBeVisible();
  await expect(page.getByText('Sếp làm ngành nào?')).toBeVisible();
  await expect(page.locator('details', { has: page.getByText('Nâng cao — lịch sàng lọc và từng quy tắc') })).not.toHaveAttribute('open', /.*/);
  await expect(page.locator('details', { has: page.getByText('Nâng cao — trọng số chấm điểm') })).not.toHaveAttribute('open', /.*/);
  const next = page.getByRole('button', { name: /Tiếp tục/ });
  await expect(next).toBeEnabled();
  await next.click();
  await expect(page).toHaveURL(/\/guide$/);
  expect(sent.map((s) => s.body)).toEqual([expect.objectContaining({ interval_seconds: 900, count_threshold: 500, min_confidence: 0.6 })]);
});
