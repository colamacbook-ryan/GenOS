import { expect, test, type Page } from '@playwright/test';
import { apiCall, enterPin, loginAsOwner, p3Hook, resetMock } from './support';

/**
 * v0.1.59 (G4) — màn THÔNG TIN (/owner/thong-tin) trên máy chủ giả (tất định; MCP giả; mã PIN 246810). Bám docs/design/mat-tien-owner-v2.md mục 3.2:
 * dòng tin xếp theo mức (Khẩn / Quan trọng / Thường) · bấm một tin thấy hội thoại 10 bong bóng (tin chính tô nền) · Trả lời chỉ TẠO NHÁP → "Duyệt và gửi"
 * có hộp PIN → nháp rời "Chờ duyệt" · Tắt tiếng / Ghi nhận · `?tin=<id>` mở sẵn · điện thoại 390px: bấm dòng mở ngăn toàn màn, "Quay lại", không cuộn
 * ngang · ngày đầu (nochannel) ⇒ đúng MỘT thẻ "Nối Zalo".
 * Dữ liệu mẫu của mock-owner: fd-1 (Khẩn, có hội thoại 10 tin) · fd-2 (cảnh báo Khẩn) · fd-3 … fd-9 · nháp chờ duyệt lấy từ mock core.
 */

const FEED = '/owner/thong-tin';
const rows = (page: Page) => page.getByTestId('feed-row');
const chip = (page: Page, view: string) => page.getByTestId(`feed-chip-${view}`);
const detail = (page: Page) => page.getByTestId('feed-detail');

async function noObjectText(page: Page): Promise<void> {
  expect(await page.locator('body').innerText()).not.toContain('[object Object]');
}

async function noHorizontalScroll(page: Page, where = ''): Promise<void> {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over, `${where} không được cuộn ngang`).toBeLessThanOrEqual(0);
}

/** Số trên nút lọc ("Chờ duyệt 2" → 2). */
async function chipCount(page: Page, view: string): Promise<number> {
  const text = (await chip(page, view).innerText()).replace(/\s+/g, ' ');
  return Number(/(\d+)\s*$/.exec(text)?.[1] ?? NaN);
}

test.describe('v0.1.59 · Thông tin (máy tính 1440)', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 1440, height: 900 });
    await loginAsOwner(page);
    await p3Hook(page.request, 'owner', 'data', { mode: 'full' });
  });

  test('danh sách theo mức: Khẩn đứng đầu, nút lọc có số đếm, chân "Gen đã lọc N tin rác/trùng", không chữ kỹ thuật', async ({ page }) => {
    await page.goto(FEED);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Thông tin');
    await expect(rows(page).first()).toBeVisible();
    // Dòng đầu là tin Khẩn: huy hiệu mức + nhãn việc + người · nhóm · kênh
    const first = rows(page).first();
    await expect(first).toContainText('Khẩn');
    await expect(first).toContainText('Than phiền');
    await expect(first).toContainText('Nguyễn Văn Bảo');
    await expect(first).toContainText('Nhóm Thành Phát');
    await expect(first).toContainText('Zalo');
    // Mỗi dòng tối đa 2 huy hiệu
    for (const row of await rows(page).all()) expect(await row.locator('.kit-badge').count()).toBeLessThanOrEqual(2);
    // Đầu danh sách: số lớn 28px + câu; năm nút lọc đúng thứ tự, "Quan trọng" mặc định
    await expect(page.getByTestId('feed-headline')).toContainText('tin cần Sếp để ý');
    await expect(page.getByTestId('feed-headline')).toContainText('2 Khẩn');
    const big = await page.locator('.feed-head__n').evaluate((el) => {
      const cs = getComputedStyle(el);
      return { size: cs.fontSize, weight: cs.fontWeight };
    });
    expect(big).toEqual({ size: '28px', weight: '600' });
    await expect(page.getByRole('group', { name: 'Lọc tin' }).getByRole('button')).toHaveText([/^Quan trọng\s*\d+$/, /^Tất cả\s*\d+$/, /^Cơ hội\s*\d+$/, /^Cảnh báo\s*\d+$/, /^Chờ duyệt\s*\d+$/]);
    await expect(chip(page, 'important')).toHaveAttribute('aria-pressed', 'true');
    expect(await chipCount(page, 'all')).toBeGreaterThanOrEqual(await chipCount(page, 'important'));
    await expect(page.getByTestId('feed-footer')).toHaveText('Gen đã lọc 124 tin rác/trùng trong 7 ngày');
    // Mức Thường chỉ có ở "Tất cả"
    await expect(page.getByTestId('feed-list')).not.toContainText('Chào buổi sáng anh');
    await chip(page, 'all').click();
    await expect(page.getByTestId('feed-list')).toContainText('Chào buổi sáng anh');
    await expect(rows(page).filter({ hasText: 'Thường' }).first()).toBeVisible();
    // Không chữ kỹ thuật trên màn
    const text = await page.locator('main').innerText();
    for (const banned of ['model', 'token', 'failover', 'P1', 'P2', 'score', 'pipeline']) expect(text, banned).not.toContain(banned);
    await noObjectText(page);
    await noHorizontalScroll(page, '1440');
  });

  test('mở tin Khẩn đầu tiên ⇒ hội thoại 10 bong bóng (trái đến / phải đi), đúng MỘT tin chính tô nền', async ({ page }) => {
    await page.goto(FEED);
    await rows(page).first().click();
    await expect(page).toHaveURL(/[?&]tin=fd-1/);
    const msgs = page.getByTestId('conv-msg');
    await expect(msgs).toHaveCount(10);
    await expect(page.getByTestId('feed-conv')).toContainText('10 tin quanh tin này');
    await expect(page.locator('[data-testid="conv-msg"][data-center="true"]')).toHaveCount(1);
    await expect(page.locator('[data-testid="conv-msg"][data-center="true"]')).toContainText('Tin này');
    await expect(page.locator('[data-testid="conv-msg"][data-center="true"]')).toContainText('Giao ván chậm hai ngày');
    // Tên người gửi hiện trên bong bóng
    await expect(msgs.first()).toContainText('Nguyễn Văn Bảo');
    await expect(page.locator('[data-testid="conv-msg"][data-dir="out"]').first()).toContainText('Bên mình');
    // Tin đến bên trái, tin đi bên phải
    const inbound = (await page.locator('[data-testid="conv-msg"][data-dir="in"]').first().boundingBox())!;
    const outbound = (await page.locator('[data-testid="conv-msg"][data-dir="out"]').first().boundingBox())!;
    expect(outbound.x + outbound.width).toBeGreaterThan(inbound.x + inbound.width);
    expect(inbound.x).toBeLessThan(outbound.x);
    // Tin chính có nền riêng (khác bong bóng đến thường)
    const bg = (sel: string) => page.locator(sel).first().evaluate((el) => getComputedStyle(el).backgroundColor);
    const centerBg = await bg('[data-testid="conv-msg"][data-center="true"]');
    const plainBg = await bg('[data-testid="conv-msg"][data-dir="in"]:not([data-center])');
    expect(centerBg).not.toBe(plainBg);
    expect(centerBg).not.toBe('rgba(0, 0, 0, 0)');
    // Đầu khung: người, nhóm, kênh, thời điểm + "Mở hồ sơ" → Quan hệ
    await expect(page.getByTestId('feed-detail-name')).toHaveText('Nguyễn Văn Bảo');
    await expect(detail(page)).toContainText('Nhóm Thành Phát · Zalo');
    await expect(detail(page)).toContainText('hôm nay');
    await expect(page.getByTestId('feed-open-profile')).toHaveAttribute('href', '/owner/quan-he?nguoi=p-bao');
    await noObjectText(page);
  });

  test('"Về khách này" (3 điểm kèm lời + tóm tắt 5 dòng) và "Chứng cứ Gen trích" từ chi tiết Hộp thư', async ({ page }) => {
    await page.route('**/api/v1/inbox/fd-1', async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      body.units = [
        { id: 'u-1', event_type: 'Complained', conclusion: 'Giao trễ 2 ngày so với hẹn', confidence: 0.9, observed_at: new Date().toISOString(), group: null, person: null, quotes: [] },
        { id: 'u-2', event_type: 'AskedStatus', conclusion: 'Đòi hoàn tiền cọc nếu hôm nay chưa giao', confidence: 0.8, observed_at: new Date().toISOString(), group: null, person: null, quotes: [] },
      ];
      await route.fulfill({ response: res, json: body });
    });
    await page.goto(`${FEED}?tin=fd-1`);
    const evidence = page.getByTestId('feed-evidence');
    await expect(evidence).toContainText('Gen trích từ tin');
    await expect(evidence.getByRole('listitem')).toHaveText(['Giao trễ 2 ngày so với hẹn', 'Đòi hoàn tiền cọc nếu hôm nay chưa giao']);
    const about = page.getByTestId('feed-about');
    await expect(about).toContainText('Về khách này');
    const scores = about.getByTestId('profile-scores').getByRole('listitem');
    await expect(scores).toHaveCount(3);
    await expect(scores.nth(0)).toHaveAttribute('aria-label', 'Độ nóng 87 · đang tăng');
    await expect(scores.nth(1)).toHaveAttribute('aria-label', 'Rủi ro rời bỏ 81 · đang tăng');
    await expect(scores.nth(2)).toHaveAttribute('aria-label', 'Tiềm năng 74 · giữ nguyên');
    await expect(about).not.toContainText('churn');
    expect(await page.getByTestId('about-summary').getByRole('listitem').count()).toBeGreaterThan(0);
    expect(await page.getByTestId('about-summary').getByRole('listitem').count()).toBeLessThanOrEqual(5);
    await noObjectText(page);
  });

  test('"Trả lời" chỉ TẠO NHÁP → "Duyệt và gửi" có hộp PIN → nháp rời "Chờ duyệt"; không có đường gửi thẳng', async ({ page }) => {
    await page.goto(FEED);
    await expect(rows(page).first()).toBeVisible(); // số đếm trên nút lọc chỉ có sau khi danh sách tải xong
    const approvalBefore = await chipCount(page, 'approval');
    await rows(page).first().click();
    await expect(page.getByTestId('conv-msg')).toHaveCount(10);
    // Chưa gõ gì thì "Trả lời" khoá; không có nút "Gửi" thẳng nào trên thanh hành động
    await expect(page.getByTestId('feed-reply')).toBeDisabled();
    await expect(page.getByTestId('feed-bar').getByRole('button', { name: /^Gửi/ })).toHaveCount(0);
    await page.getByTestId('feed-reply-input').fill('Dạ em xin lỗi anh Bảo, xe sẽ tới trước 15 giờ chiều nay ạ.');
    await page.getByTestId('feed-reply').click();
    // Nháp vừa tạo hiện ngay trong khung, chờ duyệt — CHƯA gửi
    const draft = page.getByTestId('feed-draft');
    await expect(draft).toBeVisible();
    await expect(draft.getByTestId('owner-draft-text')).toContainText('xe sẽ tới trước 15 giờ chiều nay');
    await expect(page.getByTestId('feed-reply-input')).toHaveValue('');
    await expect.poll(() => chipCount(page, 'approval')).toBe(approvalBefore + 1);
    // Duyệt và gửi ⇒ hộp PIN ⇒ xong
    await draft.getByTestId('owner-draft-approve').click();
    await enterPin(page);
    await expect(draft).toBeHidden();
    await expect(page.getByTestId('feed-note')).toHaveText('Đã duyệt và gửi bản nháp trả lời.');
    // Nháp biến khỏi "Chờ duyệt" (số về như cũ, dòng nháp không còn)
    await expect.poll(() => chipCount(page, 'approval')).toBe(approvalBefore);
    await chip(page, 'approval').click();
    await expect(page.getByTestId('feed-list')).not.toContainText('Trả lời Nguyễn Văn Bảo');
    await noObjectText(page);
  });

  test('"Tắt tiếng" một tin ⇒ tin rời danh sách; cảnh báo kế tiếp có "Ghi nhận" và "Gen gợi ý"; "Ghi nhận" ⇒ cảnh báo rời danh sách', async ({ page }) => {
    await page.goto(FEED);
    await expect(page.getByTestId('feed-headline')).toContainText('2 Khẩn');
    const total = await rows(page).count();
    await expect(page.getByTestId('feed-list')).toContainText('Giao ván chậm hai ngày');
    await rows(page).first().click();
    await page.getByTestId('feed-silence').click();
    await expect(page.getByTestId('feed-list')).not.toContainText('Giao ván chậm hai ngày');
    await expect(rows(page)).toHaveCount(total - 1);
    await expect(page.getByTestId('feed-list-note')).toHaveText('Đã tắt tiếng tin này — Gen sẽ không nhắc lại.');
    await expect(page.getByTestId('feed-headline')).toContainText('1 Khẩn');
    // Khung chuyển sang tin kế tiếp: cảnh báo Khẩn "Khách có thể sắp rời"
    await expect(page.getByTestId('feed-detail-name')).toHaveText('Trần Văn Hậu');
    await expect(detail(page)).toHaveAttribute('data-type', 'alert');
    await expect(page.getByTestId('feed-alert')).toContainText('Khẩn');
    await expect(page.getByTestId('feed-alert')).toContainText('Khách có thể sắp rời');
    await expect(page.getByTestId('feed-suggest')).toContainText('Gen gợi ý');
    await expect(page.getByTestId('feed-suggest')).toContainText('Gọi hỏi thăm trong hôm nay');
    await expect(page.getByTestId('feed-reply')).toHaveCount(0); // cảnh báo không có ô trả lời
    await page.getByTestId('feed-ack').click();
    await expect(page.getByTestId('feed-list')).not.toContainText('Khách có thể sắp rời');
    await expect(page.getByTestId('feed-list-note')).toHaveText('Đã ghi nhận cảnh báo.');
    await expect(page.getByTestId('feed-headline')).not.toContainText('Khẩn');
    await noObjectText(page);
  });

  test('"Tạo nhắc" gắn với người, báo thành công; nhắc có trong danh sách việc', async ({ page }) => {
    await page.goto(FEED);
    await rows(page).first().click();
    await page.getByTestId('feed-remind').click();
    await expect(page.getByTestId('feed-remind')).toHaveText('Đã tạo nhắc');
    await expect(page.getByTestId('feed-remind')).toBeDisabled();
    await expect(page.getByTestId('feed-remind-note')).toHaveText('Nhắc lúc 09:00 sáng mai — xem ở Việc');
    const tasks = (await apiCall(page, 'GET', '/tasks')) as { items: Array<{ title: string; remind_at: string | null }> };
    const mine = tasks.items.find((t) => t.title === 'Liên hệ lại Nguyễn Văn Bảo');
    expect(mine).toBeTruthy();
    expect(mine?.remind_at).toBeTruthy();
  });

  test('mở thẳng bằng /owner/thong-tin?tin=<id>: dòng được chọn, khung ngữ cảnh đúng tin; tin không có hội thoại gốc thì nói rõ; "Mở hồ sơ" sang Quan hệ', async ({ page }) => {
    await page.goto(`${FEED}?tin=fd-3`);
    await expect(page.getByTestId('feed-detail-name')).toHaveText('Trần Văn Hậu');
    await expect(detail(page)).toHaveAttribute('data-item', 'fd-3');
    await expect(rows(page).filter({ hasText: 'Hỏi giá' }).getByRole('button')).toHaveAttribute('aria-current', 'true');
    await expect(page.getByTestId('conv-empty')).toContainText('Chưa có hội thoại gốc cho tin này');
    await expect(page.getByTestId('conv-msg')).toHaveCount(0);
    // Tin Thường (không nằm trong "Quan trọng") vẫn mở thẳng được
    await page.goto(`${FEED}?tin=fd-7`);
    await expect(page.getByTestId('feed-detail-name')).toHaveText('Phạm Quốc Minh');
    // "Mở hồ sơ" → Quan hệ chọn sẵn người đó
    await page.getByTestId('feed-open-profile').click();
    await expect(page).toHaveURL(/\/owner\/quan-he\?nguoi=p-minh$/);
    await expect(page.getByTestId('profile-panel')).toHaveAttribute('data-person', 'p-minh');
    await expect(page.getByTestId('profile-panel')).toContainText('Phạm Quốc Minh');
  });

  test('nháp chờ duyệt (mục "Chờ duyệt"): khung hiện DraftDecide — duyệt có PIN, nháp rời danh sách', async ({ page }) => {
    await page.goto(FEED);
    await chip(page, 'approval').click();
    await expect(chip(page, 'approval')).toHaveAttribute('aria-pressed', 'true');
    await expect(rows(page).first()).toBeVisible();
    await expect(rows(page).first()).toContainText('Quan trọng'); // nháp chờ duyệt = mức Quan trọng
    const before = await rows(page).count();
    expect(before).toBeGreaterThan(0);
    await rows(page).first().click();
    await expect(detail(page)).toHaveAttribute('data-type', 'draft');
    await expect(page.getByTestId('feed-bar')).toHaveCount(0); // nháp: không có "Trả lời" (máy chủ trả 409 USE_WORKBENCH) — chỉ DraftDecide
    await page.getByTestId('owner-draft-approve').click();
    await enterPin(page);
    await expect(rows(page)).toHaveCount(before - 1);
    await expect(page.getByTestId('feed-list-note')).toHaveText('Đã xử lý bản nháp.');
  });

  test('mục "Quan trọng" rỗng ⇒ "Không có tin cần Sếp để ý. Gen đã lọc N tin." + nút "Xem tất cả" chuyển sang mục Tất cả', async ({ page }) => {
    await page.route('**/api/v1/owner/feed?*', async (route) => {
      const url = new URL(route.request().url());
      if (url.searchParams.get('view') !== 'important') return route.continue();
      return route.fulfill({
        json: {
          items: [], next_offset: null, channels_connected: 1, filtered_7d: { filtered: 124, spam_blocked: 31 },
          counts: { important: 0, all: 9, opportunity: 0, alert: 0, approval: 0, urgent_open: 0 },
        },
      });
    });
    await page.goto(FEED);
    await expect(page.getByTestId('feed-empty')).toContainText('Không có tin cần Sếp để ý. Gen đã lọc 124 tin.');
    await expect(page.getByTestId('owner-empty')).toHaveCount(1); // một câu rỗng duy nhất
    await page.getByTestId('feed-show-all').click();
    await expect(chip(page, 'all')).toHaveAttribute('aria-pressed', 'true');
    await expect(rows(page).first()).toBeVisible();
  });

  test('"Xem thêm" theo next_offset: trang sau nối vào danh sách, hết trang thì nút biến mất', async ({ page }) => {
    const item = (id: string, summary: string) => ({
      id, item_type: 'unit', title: 'Hỏi giá', summary, importance: 'Quan trọng', event_type: 'AskedPrice', created_at: new Date().toISOString(),
      person: { id: 'p-hau', name: 'Trần Văn Hậu' }, group: null, suggested_action: null,
    });
    const counts = { important: 2, all: 2, opportunity: 2, alert: 0, approval: 0, urgent_open: 0 };
    await page.route('**/api/v1/owner/feed?*', async (route) => {
      const url = new URL(route.request().url());
      const offset = Number(url.searchParams.get('offset') ?? 0);
      return route.fulfill({
        json: {
          items: [offset === 0 ? item('fd-a', 'Trang một') : item('fd-b', 'Trang hai')], next_offset: offset === 0 ? 1 : null,
          counts, filtered_7d: { filtered: 5, spam_blocked: 1 }, channels_connected: 1,
        },
      });
    });
    await page.goto(FEED);
    await expect(rows(page)).toHaveCount(1);
    await page.getByTestId('feed-more').click();
    await expect(rows(page)).toHaveCount(2);
    await expect(page.getByTestId('feed-list')).toContainText('Trang hai');
    await expect(page.getByTestId('feed-more')).toHaveCount(0);
  });

  test('lỗi tải danh sách = câu thân thiện + "Chi tiết kỹ thuật"; khối khác vẫn chạy', async ({ page }) => {
    await p3Hook(page.request, 'owner', 'scenario', { mode: 'error', only: 'feed' });
    await page.goto(FEED);
    const err = page.getByTestId('owner-error');
    await expect(err).toBeVisible();
    await expect(err).toContainText('Chi tiết kỹ thuật');
    await expect(page.getByRole('navigation', { name: 'Mặt tiền' })).toBeVisible();
    await noObjectText(page);
    await p3Hook(page.request, 'owner', 'scenario', { mode: 'data' });
  });

  test('ngày đầu (nochannel) ⇒ đúng MỘT thẻ "Nối Zalo", không danh sách, không câu rỗng chồng', async ({ page }) => {
    await p3Hook(page.request, 'owner', 'data', { mode: 'nochannel' });
    await page.goto(FEED);
    await expect(page.locator('[data-testid=first-run-card]')).toHaveCount(1);
    await expect(page.locator('[data-testid=first-run-card]')).toContainText('Nối Zalo để Gen bắt đầu đọc tin');
    await expect(page.getByTestId('feed-list')).toHaveCount(0);
    await expect(page.getByTestId('owner-empty')).toHaveCount(0);
    await expect(page.getByTestId('feed-chip-important')).toHaveCount(0);
    await noObjectText(page);
    await p3Hook(page.request, 'owner', 'data', { mode: 'full' });
  });
});

test.describe('v0.1.59 · Thông tin (điện thoại 390px)', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 390, height: 844 });
    await loginAsOwner(page);
    await p3Hook(page.request, 'owner', 'data', { mode: 'full' });
  });

  test('chỉ có danh sách; bấm dòng mở ngăn chi tiết TOÀN MÀN, "Quay lại" đóng; không cuộn ngang ở mọi bước', async ({ page }) => {
    await page.goto(FEED);
    await expect(rows(page).first()).toBeVisible();
    await expect(page.getByTestId('feed-pane')).toHaveCount(0); // không có cột phải
    await expect(page.getByTestId('owner-tabbar').getByRole('link')).toHaveCount(5);
    await noHorizontalScroll(page, 'danh sách 390');
    // vùng chạm của dòng ≥ 44px
    for (const row of (await rows(page).all()).slice(0, 4)) expect((await row.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await rows(page).first().click();
    const sheet = page.getByRole('dialog', { name: 'Than phiền' });
    await expect(sheet).toBeVisible();
    await expect(page).toHaveURL(/[?&]tin=fd-1/);
    const box = (await sheet.boundingBox())!;
    expect(Math.round(box.width)).toBe(390); // toàn màn hình
    expect(Math.round(box.height)).toBe(844);
    await expect(page.getByTestId('conv-msg')).toHaveCount(10);
    await expect(page.getByTestId('feed-detail-name')).toHaveText('Nguyễn Văn Bảo');
    await noHorizontalScroll(page, 'chi tiết 390');
    const over = await sheet.evaluate((el) => {
      const body = el.querySelector('.kit-sheet__body') as HTMLElement;
      return body.scrollWidth - body.clientWidth;
    });
    expect(over).toBeLessThanOrEqual(0);
    // thanh hành động dính đáy: nằm trong khung nhìn, nút cao ≥ 44px
    const bar = (await page.getByTestId('feed-bar').boundingBox())!;
    expect(bar.y + bar.height).toBeLessThanOrEqual(844 + 1);
    for (const id of ['feed-reply-input', 'feed-remind', 'feed-silence']) expect((await page.getByTestId(id).boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await page.getByRole('button', { name: 'Quay lại' }).click();
    await expect(sheet).toBeHidden();
    await expect(page).not.toHaveURL(/tin=/);
    await expect(rows(page).first()).toBeVisible();
  });

  test('/owner/thong-tin?tin=<id> trên điện thoại mở sẵn ngăn chi tiết; Trả lời → nháp → Duyệt và gửi (PIN) ngay trong ngăn', async ({ page }) => {
    await page.goto(`${FEED}?tin=fd-1`);
    const sheet = page.getByRole('dialog', { name: 'Than phiền' });
    await expect(sheet).toBeVisible();
    await page.getByTestId('feed-reply-input').fill('Dạ em xin lỗi anh, xe sẽ tới trước 15 giờ ạ.');
    await page.getByTestId('feed-reply').click();
    await expect(page.getByTestId('feed-draft')).toBeVisible();
    await expect(page.getByTestId('owner-draft-approve')).toBeVisible();
    await noHorizontalScroll(page, 'nháp 390');
    await page.getByTestId('owner-draft-approve').click();
    await enterPin(page);
    await expect(page.getByTestId('feed-draft')).toBeHidden();
    await expect(page.getByTestId('feed-note')).toHaveText('Đã duyệt và gửi bản nháp trả lời.');
  });

  test('"Tắt tiếng" trong ngăn ⇒ ngăn đóng, danh sách hiện câu báo, tin rời danh sách', async ({ page }) => {
    await page.goto(FEED);
    await rows(page).first().click();
    await page.getByTestId('feed-silence').click();
    await expect(page.getByRole('dialog')).toBeHidden();
    await expect(page.getByTestId('feed-list-note')).toContainText('Đã tắt tiếng');
    await expect(page.getByTestId('feed-list')).not.toContainText('Giao ván chậm hai ngày');
    await noHorizontalScroll(page, 'sau tắt tiếng 390');
  });

  test('ngày đầu (nochannel) ⇒ đúng MỘT thẻ "Nối Zalo"; không cuộn ngang', async ({ page }) => {
    await p3Hook(page.request, 'owner', 'data', { mode: 'nochannel' });
    await page.goto(FEED);
    await expect(page.locator('[data-testid=first-run-card]')).toHaveCount(1);
    await expect(page.getByTestId('feed-list')).toHaveCount(0);
    await noHorizontalScroll(page, 'nochannel 390');
    await p3Hook(page.request, 'owner', 'data', { mode: 'full' });
  });
});
