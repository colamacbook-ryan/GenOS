import { expect, test, type Page } from '@playwright/test';
import { AI_SOURCE_PATH, OWNER, SETUP_TOKEN, apiCall, expectOwnerFront, fillSetupAccount, loginAsOwner, loginGoogleInSetup, resetMock, setupOneToDone } from './support';

/**
 * v0.1.59 (g5-thiet-lap, mock, tất định) — Thiết lập MỘT màn, 3 bước: 1 Tài khoản · 2 Nguồn AI · 3 Xong.
 * Máy chủ vẫn 12 bước (chỉ 1, 2, 3 và 12 bắt buộc); giao diện gọi PUT 1 → 2 → 3, PUT 4 (hoặc "Làm sau"), rồi PUT 12 — bước 5–11
 * làm sau ở /guide/n. URL `?buoc=tai-khoan|nguon-ai|xong`; `?step=` cũ được ánh xạ.
 */

interface Sent {
  method: string;
  path: string;
  body: Record<string, unknown>;
}
/** Ghi lại mọi PUT/POST /setup/steps/* do giao diện gửi (chỉ để kiểm hình dạng — không in mật khẩu / PIN). */
function recordSetupCalls(page: Page): Sent[] {
  const sent: Sent[] = [];
  page.on('request', (r) => {
    const path = new URL(r.url()).pathname.replace('/api/v1', '');
    if (/^\/setup\/steps\//.test(path) && r.method() !== 'GET') sent.push({ method: r.method(), path, body: (r.postDataJSON() ?? {}) as Record<string, unknown> });
  });
  return sent;
}

/** Không cuộn ngang: không phần tử nào làm trang rộng hơn khung nhìn. */
async function expectNoHorizontalScroll(page: Page): Promise<void> {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth }));
  expect(scrollWidth).toBeLessThanOrEqual(innerWidth);
}

const stepBar = (page: Page) => page.getByRole('list', { name: 'Các bước thiết lập' });

test.describe('Thiết lập một màn (v0.1.59)', () => {
  test.beforeEach(async ({ request }) => resetMock(request, 'fresh'));

  test('máy mới: /setup?token=… đi đủ 3 bước (Tài khoản → Nguồn AI → Xong) tới /owner; chỉ PUT 1,2,3,4,12 + "để sau" 5–11', async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    const sent = recordSetupCalls(page);
    await page.goto('/setup?token=' + SETUP_TOKEN);

    // Bước 1 — Tài khoản: một form, mã thiết lập có sẵn từ đường dẫn nên không hỏi lại; "Tuỳ chỉnh" thu gọn.
    await expect(page.getByRole('heading', { name: 'Tạo tài khoản Sếp' })).toBeVisible();
    await expect(page).toHaveURL(/buoc=tai-khoan/);
    // logo cùng kiểu Mặt tiền: "Gen-Harness", không viết hoa toàn bộ (checklist "Owner 3 giây hiểu", v0.1.60)
    await expect(page.getByTestId('setup-brand')).toHaveText('Gen-Harness');
    expect(await page.getByTestId('setup-brand').evaluate((el) => getComputedStyle(el.lastElementChild!).textTransform)).toBe('none');
    await expect(stepBar(page).getByRole('listitem')).toHaveText(['1Bước 1: Tài khoản', '2Bước 2: Nguồn AI', '3Bước 3: Xong']);
    await expect(stepBar(page).getByRole('listitem').first()).toHaveAttribute('aria-current', 'step');
    await expect(page.getByLabel('Mã thiết lập')).toHaveCount(0);
    await expect(page.getByText('Tuỳ chỉnh').locator('xpath=ancestor::details')).not.toHaveAttribute('open', /.*/);
    const next = page.getByRole('button', { name: /Tiếp tục/ });
    await expect(next).toBeDisabled();
    await fillSetupAccount(page);
    await expect(next).toBeEnabled();
    await next.click();

    // Bước 2 — Nguồn AI: hai lựa chọn lớn; "Nâng cao" thu gọn; Tiếp tục khoá tới khi có nguồn chạy được.
    await expect(page.getByRole('heading', { name: 'Nối nguồn AI cho Gen' })).toBeVisible();
    await expect(page).toHaveURL(/buoc=nguon-ai/);
    await expect(page.getByRole('heading', { name: 'Đăng nhập Google (Antigravity)' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Dán khoá API' })).toBeVisible();
    await expect(page.getByTestId('setup-ai-advanced')).not.toHaveAttribute('open', /.*/);
    await expect(next).toBeDisabled();
    await loginGoogleInSetup(page);
    // Như máy chủ thật: nguồn Antigravity vừa đăng nhập có 0 dòng model ⇒ màn tự "Kiểm tra" một lần, rồi mới "Đã chạy được".
    await expect(page.getByTestId('setup-ai-cli-provider')).toContainText('Gọi thử OK');
    await expect(page.getByTestId('setup-ai-ready')).toContainText('Đã chạy được');
    await expect(next).toBeEnabled();
    await next.click();
    await page.request.post('/api/v1/__mock/pin_expire', { data: {} });

    // Bước 3 — Xong: ba dòng tóm tắt, nút chính + nút phụ nổi bật.
    await expect(page.getByRole('heading', { name: 'Xong rồi, thưa Sếp' })).toBeVisible();
    await expect(page).toHaveURL(/buoc=xong/);
    await expect(page.getByTestId('setup-done-account')).toContainText('Anh Nguyễn Văn A (Chủ)');
    await expect(page.getByTestId('setup-done-ai')).toContainText('Đã chạy');
    await expect(page.getByTestId('setup-done-first-run')).toContainText('Gen');
    await expect(page.getByText('Zalo là nguồn tin chính — chưa nối thì Mặt tiền còn trống.')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Nhận bản tin qua Telegram' })).toHaveAttribute('href', '/connections#telegram');
    await expect(page.getByRole('link', { name: 'Mời đội' })).toHaveAttribute('href', '/guide/10');
    await page.getByRole('button', { name: /Vào Mặt tiền/ }).click();
    await expectOwnerFront(page);

    // Payload: 3 bước tài khoản giữ nguyên hình dạng cũ; xưng hô / múi giờ / tiền tệ dùng mặc định; bước 4 chỉ gửi danh sách nguồn.
    const body = (path: string) => sent.filter((s) => s.method === 'PUT' && s.path === path).map((s) => s.body);
    expect(body('/setup/steps/1')).toEqual([{ token: SETUP_TOKEN, language: 'vi', mode: 'empty' }]);
    expect(body('/setup/steps/2')).toEqual([expect.objectContaining({ token: SETUP_TOKEN, display_name: 'Anh Nguyễn Văn A (Chủ)', email: 'owner@example.test', pin: OWNER.pin, pin_confirm: OWNER.pin })]);
    expect(body('/setup/steps/3')).toEqual([{ org_name: 'Genesis Trading', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND', self_name: 'Sếp', bot_calls_me: 'Sếp' }]);
    expect(body('/setup/steps/4')).toEqual([{ provider_ids: expect.any(Array) }]);
    expect(body('/setup/steps/12')).toHaveLength(1);
    // Bước 5–11 chưa làm ⇒ ghi "để sau" (mặc định máy chủ) trước PUT 12; làm sau ở /guide/n.
    expect(sent.filter((s) => s.method === 'POST').map((s) => s.path)).toEqual([5, 6, 7, 8, 9, 10, 11].map((n) => `/setup/steps/${n}/skip`));

    const state = (await apiCall(page, 'GET', '/setup/state')) as { finished: boolean; steps: Array<{ n: number; status: string }> };
    expect(state.finished).toBe(true);
    expect(state.steps.filter((s) => s.status === 'done').map((s) => s.n)).toEqual([1, 2, 3, 4, 12]);
    expect(state.steps.filter((s) => s.status === 'skipped').map((s) => s.n)).toEqual([5, 6, 7, 8, 9, 10, 11]);
  });

  test('"Làm sau" ở Nguồn AI: tới bước Xong với câu "Gen sẽ chưa tóm tắt được bản tin"; Cài đặt có "Đổi nguồn AI" mở lại màn Nguồn AI', async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await setupOneToDone(page, { skipAi: true });
    await expect(page).toHaveURL(/buoc=xong/);
    await expect(page.getByTestId('setup-done-ai')).toContainText('Làm sau — Gen sẽ chưa tóm tắt được bản tin');
    await page.getByRole('button', { name: /Vào Mặt tiền/ }).click();
    await expectOwnerFront(page);

    // Mặt tiền → Cài đặt: khối Nguồn AI nói chưa nối, nút "Đổi nguồn AI" → /setup?buoc=nguon-ai (đã xong thiết lập vẫn mở được).
    await page.goto('/owner/them');
    await expect(page.getByTestId('owner-more-ai-status')).toContainText('Chưa nối nguồn AI');
    await page.getByRole('link', { name: /Đổi nguồn AI/ }).click();
    await expect(page).toHaveURL(new RegExp(AI_SOURCE_PATH.replace('?', '\\?') + '$'));
    await expect(page.getByRole('heading', { name: 'Đổi nguồn AI' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Làm sau' })).toHaveCount(0);
  });

  test('"Nối Zalo ngay" ghi PUT 12 rồi mở /guide/5 (Kết nối kênh)', async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    const sent = recordSetupCalls(page);
    await setupOneToDone(page, { skipAi: true });
    await page.getByRole('button', { name: /Nối Zalo ngay/ }).click();
    await expect(page).toHaveURL(/\/guide\/5$/);
    await expect(page.getByRole('heading', { name: 'Kết nối kênh' })).toBeVisible();
    expect(sent.filter((s) => s.method === 'PUT' && s.path === '/setup/steps/12')).toHaveLength(1);
    const state = (await apiCall(page, 'GET', '/setup/state')) as { finished: boolean };
    expect(state.finished).toBe(true);
  });

  test('tải lại giữa bước 1 (sau PUT 2): vẫn đi tiếp — không hỏi lại tài khoản, chỉ còn tên doanh nghiệp', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const sent = recordSetupCalls(page);
    await page.goto('/setup?token=' + SETUP_TOKEN);
    await fillSetupAccount(page);
    // PUT 3 rớt mạng một lần: bước 1 và 2 đã lưu ở máy chủ (đã có phiên), bước 3 chưa.
    await page.route('**/api/v1/setup/steps/3', (route) => route.abort());
    await page.getByRole('button', { name: /Tiếp tục/ }).click();
    await expect(page.getByRole('alert')).toContainText('Không kết nối được máy chủ');
    await expect(page.getByRole('alert')).toContainText('Chi tiết kỹ thuật');
    await expect(page.getByRole('heading', { name: 'Tạo tài khoản Sếp' })).toBeVisible();
    await page.unroute('**/api/v1/setup/steps/3');

    await page.reload();
    await expect(page.getByRole('heading', { name: 'Tạo tài khoản Sếp' })).toBeVisible();
    await expect(page.getByTestId('setup-account-done')).toBeVisible();
    await expect(page.getByLabel('Mật khẩu', { exact: true })).toHaveCount(0);
    await expect(page.getByLabel('Mã thiết lập')).toHaveCount(0);
    await page.getByLabel('Tên doanh nghiệp').fill('Genesis Trading');
    await page.getByRole('button', { name: /Tiếp tục/ }).click();
    await expect(page.getByRole('heading', { name: 'Nối nguồn AI cho Gen' })).toBeVisible();
    // PUT 1 và PUT 2 chỉ gửi MỘT lần (sau khi tải lại bước đã xong bị bỏ qua).
    const puts = (n: number) => sent.filter((s) => s.method === 'PUT' && s.path === `/setup/steps/${n}`).length;
    expect([puts(1), puts(2)]).toEqual([1, 1]);
  });

  test('lỗi máy chủ gắn đúng ô: email trùng báo ngay dưới ô Email, bước 1 đã lưu không bị gửi lại', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/setup?token=' + SETUP_TOKEN);
    await fillSetupAccount(page);
    await page.route('**/api/v1/setup/steps/2', (route) => route.fulfill({ status: 422, contentType: 'application/problem+json', json: { status: 422, code: 'VALIDATION_ERROR', title: 'Dữ liệu chưa hợp lệ', errors: { email: 'Email đã được dùng.' } } }));
    await page.getByRole('button', { name: /Tiếp tục/ }).click();
    const err = page.getByText('Email đã được dùng.');
    await expect(err).toBeVisible();
    await expect(page.getByLabel('Email')).toHaveAttribute('aria-invalid', 'true');
    await expect(page.getByRole('alert')).toContainText('Kiểm tra lại các trường được đánh dấu.');
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('/setup?step=4 → ?buoc=nguon-ai; ?step=12 chưa đủ điều kiện ⇒ ở lại bước hiện tại', async ({ page, request }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(request, 'fresh', { startAtStep: 4 });
    await loginAsOwner(page);
    await page.goto('/setup?step=4');
    await expect(page.getByRole('heading', { name: 'Nối nguồn AI cho Gen' })).toBeVisible();
    await expect(page).toHaveURL(/\/setup\?buoc=nguon-ai$/);
    await page.goto('/setup?step=12');
    await expect(page.getByRole('heading', { name: 'Nối nguồn AI cho Gen' })).toBeVisible();
    await expect(page).toHaveURL(/\/setup\?buoc=nguon-ai$/);
    await page.goto('/setup?step=2');
    await expect(page).toHaveURL(/\/setup\?buoc=nguon-ai$/);
  });
});

test.describe('Thiết lập một màn — đã xong thiết lập (v0.1.59)', () => {
  test.beforeEach(async ({ page, request }) => {
    await resetMock(request, 'finished');
    await loginAsOwner(page);
  });

  test('/setup?step=7 → /guide/7; /setup → /owner', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/setup?step=7');
    await expect(page).toHaveURL(/\/guide\/7$/);
    await page.goto('/setup');
    await expectOwnerFront(page);
    await page.goto('/setup?buoc=tai-khoan');
    await expectOwnerFront(page);
  });

  test('mở /setup?buoc=nguon-ai (Owner) và lưu được bước 4 rồi về Mặt tiền', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const sent = recordSetupCalls(page);
    await page.goto(AI_SOURCE_PATH);
    await expect(page.getByRole('heading', { name: 'Đổi nguồn AI' })).toBeVisible();
    await expect(page.getByTestId('setup-ai-ready')).toContainText('Đã chạy được');
    const next = page.getByRole('button', { name: /Tiếp tục/ });
    await expect(next).toBeEnabled();
    await next.click();
    await expectOwnerFront(page);
    const put4 = sent.filter((s) => s.method === 'PUT' && s.path === '/setup/steps/4');
    expect(put4).toHaveLength(1);
    expect(put4[0].body).toEqual({ provider_ids: expect.any(Array) });
    expect((put4[0].body.provider_ids as string[]).length).toBeGreaterThan(0);
  });

  test('nút "Chọn model" ở Tổng quan/Kết nối dẫn tới /setup?buoc=nguon-ai', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/connections');
    const brain = page.getByRole('region', { name: 'Bộ não AI' });
    await expect(brain.getByRole('link', { name: /Thêm \/ kiểm tra nguồn AI/ })).toHaveAttribute('href', AI_SOURCE_PATH);
  });
});

test.describe('Thiết lập một màn — điện thoại 390px (v0.1.59)', () => {
  test('cả 3 bước không cuộn ngang; thẻ chiếm toàn bề ngang với lề 16px', async ({ page, request }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 390, height: 844 });
    await resetMock(request, 'fresh');
    await page.goto('/setup?token=' + SETUP_TOKEN);
    await expect(page.getByRole('heading', { name: 'Tạo tài khoản Sếp' })).toBeVisible();
    await expectNoHorizontalScroll(page);
    // thẻ giữa: toàn bề ngang trừ lề 16px mỗi bên
    const card = await page.locator('.setup-one__card').boundingBox();
    expect(card).not.toBeNull();
    expect(Math.round(card!.x)).toBe(16);
    expect(Math.round(card!.width)).toBe(390 - 32);
    await page.getByText('Tuỳ chỉnh').click();
    await expect(page.getByLabel('Sếp tự xưng là')).toBeVisible();
    await expectNoHorizontalScroll(page);
    await fillSetupAccount(page);
    await page.getByRole('button', { name: /Tiếp tục/ }).click();

    await expect(page.getByRole('heading', { name: 'Nối nguồn AI cho Gen' })).toBeVisible();
    await expectNoHorizontalScroll(page);
    await page.getByText('Nâng cao', { exact: true }).click();
    await expect(page.getByRole('link', { name: /Thứ tự dự phòng/ })).toBeVisible();
    await expectNoHorizontalScroll(page);
    await loginGoogleInSetupMobile(page);
    await page.getByRole('button', { name: /Tiếp tục/ }).click();

    await expect(page.getByRole('heading', { name: 'Xong rồi, thưa Sếp' })).toBeVisible();
    await expectNoHorizontalScroll(page);
    await expect(page.getByRole('button', { name: /Vào Mặt tiền/ })).toBeVisible();
    // vùng chạm >= 44px cho nút chính và nút phụ nổi bật
    for (const name of [/Vào Mặt tiền/, /Nối Zalo ngay/]) {
      const box = await page.getByRole('button', { name }).boundingBox();
      expect(box!.height).toBeGreaterThanOrEqual(44);
    }
  });
});

/** Như `loginGoogleInSetup` nhưng trên điện thoại (khung "Nâng cao" đang mở — đăng nhập vẫn nằm ở lựa chọn Google). */
async function loginGoogleInSetupMobile(page: Page): Promise<void> {
  await loginGoogleInSetup(page);
  await expectNoHorizontalScroll(page);
}
