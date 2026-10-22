import { expect, test, type Page } from '@playwright/test';
import { apiCall, loginAsOwner, p3Hook, resetMock } from './support';

/**
 * v0.1.59 (G4) — màn QUAN HỆ (/owner/quan-he) trên máy chủ giả (tất định; MCP giả). Bám docs/design/mat-tien-owner-v2.md mục 3.3:
 * Cần chăm (mặc định, gồm người loại "Chưa phân loại" = unknown) · Khách nóng · Cầu nối (danh sách + liên kết /graph, KHÔNG bản đồ) · Cung–cầu
 * (Giới thiệu chỉ tạo NHÁP chờ duyệt; Bỏ qua) · chọn dòng ⇒ hồ sơ sống · "Tạo nhắc" · "Nhờ Gen soạn tin" mở ngăn Hỏi Gen với câu điền sẵn ·
 * điện thoại 390px không cuộn ngang · ngày đầu (nochannel) ⇒ đúng MỘT thẻ "Nối Zalo".
 * Dữ liệu mẫu của mock-owner: care = p-thang (Chưa phân loại, sắp rời) · p-duyen (sắp rời) · p-tri · p-bich (lâu chưa liên lạc); hot = p-hau, p-bao, p-duoc;
 * bridges = p-khoa, p-thang; matches = match-1, match-2 (mock market).
 */

const REL = '/owner/quan-he';
const chip = (page: Page, list: string) => page.getByTestId(`rel-chip-${list}`);
const panel = (page: Page) => page.getByTestId('profile-panel');
const tableRow = (page: Page, name: string) => page.getByRole('row').filter({ hasText: name });

async function noObjectText(page: Page): Promise<void> {
  expect(await page.locator('body').innerText()).not.toContain('[object Object]');
}

async function noHorizontalScroll(page: Page, where = ''): Promise<void> {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over, `${where} không được cuộn ngang`).toBeLessThanOrEqual(0);
}

test.describe('v0.1.59 · Quan hệ (máy tính 1440)', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 1440, height: 900 });
    await loginAsOwner(page);
    await p3Hook(page.request, 'owner', 'data', { mode: 'full' });
  });

  test('Cần chăm (mặc định): số lớn + lý do, bảng đủ cột, có người "Chưa phân loại"; nút lọc có số đếm', async ({ page }) => {
    await page.goto(REL);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Quan hệ');
    await expect(page.getByRole('group', { name: 'Chọn danh sách quan hệ' }).getByRole('button')).toHaveText([
      /^Cần chăm\s*4$/, /^Khách nóng\s*3$/, /^Cầu nối\s*2$/, /^Cung–cầu\s*\d+$/,
    ]);
    await expect(chip(page, 'care')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('rel-headline')).toContainText('4');
    await expect(page.getByTestId('rel-headline')).toContainText('khách cần chăm');
    await expect(page.getByTestId('rel-headline')).toContainText('2 sắp rời, 2 lâu chưa liên lạc');
    const big = await page.locator('.rel-head__n').evaluate((el) => {
      const cs = getComputedStyle(el);
      return { size: cs.fontSize, weight: cs.fontWeight };
    });
    expect(big).toEqual({ size: '28px', weight: '600' });
    await expect(page.getByRole('columnheader')).toHaveText(['Khách', 'Lý do', 'Độ nóng', 'Lần cuối liên lạc', 'Cơ hội mở', '']);
    // người loại unknown vẫn là khách cần chăm
    const thang = tableRow(page, 'Bùi Đức Thắng');
    await expect(thang).toContainText('Chưa phân loại');
    await expect(thang).toContainText('Sắp rời');
    await expect(thang).toContainText('6 ngày trước');
    await expect(thang).toContainText('880 triệu ₫');
    await expect(thang).toContainText('72');
    await expect(tableRow(page, 'Đặng Hữu Trí')).toContainText('Lâu chưa liên lạc');
    await expect(tableRow(page, 'Lê Thị Bích')).toContainText('Chưa có'); // không có cơ hội mở ⇒ "Chưa có", không "0 ₫"
    expect(await page.locator('.kit-table .kit-badge').count()).toBe(4); // mỗi dòng đúng 1 huy hiệu
    await expect(page.getByTestId('rel-body')).not.toContainText('churn');
    const text = await page.locator('main').innerText();
    for (const banned of ['model', 'token', 'score', 'pipeline']) expect(text, banned).not.toContain(banned);
    await noObjectText(page);
    await noHorizontalScroll(page, 'Cần chăm 1440');
  });

  test('chọn dòng ⇒ hồ sơ sống bên phải (4 cột); mặc định dòng đầu; địa chỉ có ?nguoi=', async ({ page }) => {
    await page.goto(REL);
    // máy tính: tự chọn dòng đầu
    await expect(panel(page)).toHaveAttribute('data-person', 'p-thang');
    await expect(panel(page)).toContainText('Bùi Đức Thắng');
    const side = (await page.getByTestId('rel-side-card').boundingBox())!;
    const main = (await page.getByTestId('rel-list-card').boundingBox())!;
    expect(side.x).toBeGreaterThan(main.x + main.width - 1);
    expect(side.width).toBeLessThan(main.width / 1.5);
    await tableRow(page, 'Trịnh Mỹ Duyên').click();
    await expect(page).toHaveURL(/[?&]nguoi=p-duyen/);
    await expect(panel(page)).toHaveAttribute('data-person', 'p-duyen');
    await expect(panel(page)).toContainText('Trịnh Mỹ Duyên');
    await expect(tableRow(page, 'Trịnh Mỹ Duyên')).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByTestId('profile-open-full')).toHaveAttribute('href', '/profile?id=p-duyen');
    await expect(page.getByTestId('profile-open-full')).toHaveText('Mở hồ sơ đầy đủ');
    // đổi danh sách ⇒ bỏ chọn cũ, chọn lại dòng đầu của danh sách mới
    await chip(page, 'hot').click();
    await expect(panel(page)).toHaveAttribute('data-person', 'p-hau');
    await expect(page).not.toHaveURL(/nguoi=/);
    await expect(page).toHaveURL(/[?&]list=hot/);
  });

  test('?nguoi=p-bao: hồ sơ sống đủ khối — 3 điểm kèm lời, tóm tắt ≤ 5 dòng, 5 sự việc, kênh (số đã che), ghi chú chỉ đọc, mở hồ sơ đầy đủ', async ({ page }) => {
    await page.goto(`${REL}?nguoi=p-bao`);
    await expect(panel(page)).toHaveAttribute('data-person', 'p-bao');
    await expect(panel(page).getByRole('heading', { level: 2 })).toHaveText('Nguyễn Văn Bảo');
    await expect(panel(page)).toContainText('Khách hàng · quen');
    await expect(page.getByTestId('profile-relation')).toHaveText('Quan hệ với SếpQuen trực tiếp với Sếp');
    const scores = page.getByTestId('profile-scores').getByRole('listitem');
    await expect(scores).toHaveCount(3);
    await expect(scores.nth(0)).toHaveAttribute('aria-label', 'Độ nóng 87 · đang tăng');
    await expect(scores.nth(1)).toHaveAttribute('aria-label', 'Rủi ro rời bỏ 81 · đang tăng');
    await expect(scores.nth(2)).toHaveAttribute('aria-label', 'Tiềm năng 74 · giữ nguyên');
    const summary = await page.getByTestId('profile-summary').getByRole('listitem').count();
    expect(summary).toBeGreaterThan(0);
    expect(summary).toBeLessThanOrEqual(5);
    await expect(page.getByTestId('profile-timeline').getByRole('listitem')).toHaveCount(5);
    await expect(page.getByTestId('profile-timeline').getByRole('listitem').first()).toContainText('Nhắc lần thứ ba, giọng gay gắt');
    const channels = page.getByTestId('profile-channels');
    await expect(channels).toContainText('Zalo');
    await expect(channels).toContainText('WhatsApp · 09•• ••• •18'); // số điện thoại đã che
    await expect(channels).not.toContainText('+84903');
    const note = page.getByTestId('profile-note');
    await expect(note).toContainText('Ghi chú của Sếp');
    await expect(note).toContainText('Anh Bảo thích nói chuyện thẳng');
    await expect(note.locator('textarea, input')).toHaveCount(0); // chỉ đọc
    await expect(panel(page)).not.toContainText('churn');
    await expect(panel(page)).not.toContainText('Điểm chăm sóc'); // chỉ 3 điểm
    await noObjectText(page);
  });

  test('"Tạo nhắc" từ hồ sơ ⇒ báo thành công, nhắc gắn với người có trong danh sách việc', async ({ page }) => {
    await page.goto(REL);
    await tableRow(page, 'Bùi Đức Thắng').click();
    await expect(panel(page)).toHaveAttribute('data-person', 'p-thang');
    await page.getByTestId('profile-remind').click();
    await expect(page.getByTestId('profile-remind')).toHaveText('Đã tạo nhắc');
    await expect(page.getByTestId('profile-remind-note')).toHaveText('Nhắc lúc 09:00 sáng mai — xem ở Việc');
    await expect(page.getByTestId('profile-remind')).toBeDisabled();
    const tasks = (await apiCall(page, 'GET', '/tasks')) as { items: Array<{ title: string; remind_at: string | null }> };
    const mine = tasks.items.find((t) => t.title === 'Liên hệ lại Bùi Đức Thắng');
    expect(mine).toBeTruthy();
    expect(mine?.remind_at).toBeTruthy();
    // "Tạo nhắc" ngay trên dòng bảng cũng chạy
    await page.getByTestId('rel-remind-p-duyen').click();
    await expect(page.getByTestId('rel-remind-p-duyen')).toHaveText('Đã tạo nhắc');
    await expect(page.getByTestId('rel-remind-p-duyen-note')).toContainText('Nhắc lúc 09:00 sáng mai');
  });

  test('"Nhờ Gen soạn tin" mở ngăn Hỏi Gen với câu điền sẵn (không tự gửi) — từ hồ sơ và từ dòng bảng', async ({ page }) => {
    const sent: string[] = [];
    page.on('request', (r) => {
      if (r.method() === 'POST' && r.url().includes('/api/v1/gen/turns')) sent.push(r.url());
    });
    await page.goto(REL);
    await tableRow(page, 'Bùi Đức Thắng').click();
    await page.getByTestId('profile-ask').click();
    const dlg = page.getByRole('dialog', { name: 'Hỏi Gen' });
    await expect(dlg).toBeVisible();
    await expect(dlg.getByLabel('Câu hỏi cho Gen')).toHaveValue('Soạn tin hỏi thăm Bùi Đức Thắng');
    expect(sent, 'chỉ điền sẵn ô nhập — không tự gửi cho Gen').toHaveLength(0);
    await page.keyboard.press('Escape');
    await expect(dlg).toBeHidden();
    await page.getByTestId('rel-ask-p-tri').click();
    await expect(dlg).toBeVisible();
    await expect(dlg.getByLabel('Câu hỏi cho Gen')).toHaveValue('Soạn tin hỏi thăm Đặng Hữu Trí');
    expect(sent).toHaveLength(0);
  });

  test('Khách nóng: cùng kiểu bảng, thêm giá trị cơ hội và xu hướng độ nóng', async ({ page }) => {
    await page.goto(`${REL}?list=hot`);
    await expect(chip(page, 'hot')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByRole('columnheader')).toHaveText(['Khách', 'Độ nóng', 'Lần cuối liên lạc', 'Giá trị cơ hội', '']);
    const hau = tableRow(page, 'Trần Văn Hậu');
    await expect(hau).toContainText('91');
    await expect(hau).toContainText('đang tăng');
    await expect(hau).toContainText('1,2 tỷ ₫');
    await expect(hau).toContainText('Hôm qua');
    await expect(tableRow(page, 'Lâm Văn Được')).toContainText('Chưa có'); // không có cơ hội
    // link cũ ?list=cooling (từ Hôm nay) về Cần chăm
    await page.goto(`${REL}?list=cooling`);
    await expect(chip(page, 'care')).toHaveAttribute('aria-pressed', 'true');
  });

  test('Cầu nối: danh sách người + "Nối với" + liên kết "Xem bản đồ trong Console" (/graph), KHÔNG vẽ bản đồ', async ({ page }) => {
    await page.goto(REL);
    await chip(page, 'bridges').click();
    const list = page.getByTestId('bridge-list');
    await expect(list).toBeVisible();
    await expect(page.getByRole('columnheader')).toHaveText(['Người', 'Nối với', 'Ví dụ']);
    await expect(tableRow(page, 'Trần Minh Khoa')).toContainText('5 cặp nhóm');
    await expect(tableRow(page, 'Bùi Đức Thắng')).toContainText('2 cặp nhóm');
    await expect(tableRow(page, 'Trần Minh Khoa')).toContainText('Có mặt ở 3 nhóm');
    const link = page.getByTestId('bridge-graph-link');
    await expect(link).toHaveText('Xem bản đồ trong Console');
    await expect(link).toHaveAttribute('href', '/graph');
    await expect(page.locator('main canvas, main svg.graph, main [data-testid*="graph-canvas"]')).toHaveCount(0);
    // bấm dòng ⇒ hồ sơ của người đó
    await tableRow(page, 'Trần Minh Khoa').click();
    await expect(panel(page)).toHaveAttribute('data-person', 'p-khoa');
    await noObjectText(page);
  });

  test('Cung–cầu: Ai cần · Cần gì · Ai có · Khớp; "Giới thiệu" chỉ tạo NHÁP chờ duyệt (báo "Đã tạo nháp, duyệt ở Việc"); "Bỏ qua" bỏ cặp', async ({ page }) => {
    const approvalBefore = ((await apiCall(page, 'GET', '/owner/feed?view=approval')) as { counts: { approval: number } }).counts.approval;
    await page.goto(REL);
    await chip(page, 'matches').click();
    await expect(page.getByRole('columnheader')).toHaveText(['Ai cần', 'Cần gì', 'Ai có', 'Khớp', '']);
    const first = tableRow(page, 'Trần Văn Hậu');
    await expect(first).toContainText('3 cont ván MDF E1 17mm');
    await expect(first).toContainText('Lâm Văn Được');
    await expect(first).toContainText('94%');
    await page.getByTestId('match-introduce-match-1').click();
    const done = page.getByTestId('match-done-match-1');
    await expect(done).toHaveText('Đã tạo nháp, duyệt ở Việc');
    await expect(done.getByRole('link', { name: 'Việc' })).toHaveAttribute('href', '/owner/viec');
    await expect(page.getByTestId('match-introduce-match-1')).toBeDisabled();
    // chỉ tạo nháp chờ duyệt — số "Chờ duyệt" tăng 1, chưa gửi gì
    const approvalAfter = ((await apiCall(page, 'GET', '/owner/feed?view=approval')) as { counts: { approval: number } }).counts.approval;
    expect(approvalAfter).toBe(approvalBefore + 1);
    // Bỏ qua cặp thứ hai ⇒ biến khỏi danh sách
    await expect(tableRow(page, 'Lê Thị Bích')).toBeVisible();
    await page.getByTestId('match-reject-match-2').click();
    await expect(tableRow(page, 'Lê Thị Bích')).toHaveCount(0);
    await noObjectText(page);
  });

  test('trạng thái rỗng: Cần chăm rỗng ⇒ "Không khách nào cần chăm gấp."; chưa có khách nào ⇒ "Gen chưa nhận diện khách nào…"; chỉ MỘT câu rỗng', async ({ page }) => {
    await page.route('**/api/v1/owner/relations?*', async (route) => {
      const url = new URL(route.request().url());
      if (url.searchParams.get('list') !== 'care') return route.continue();
      return route.fulfill({ json: { list: 'care', items: [] } });
    });
    await page.goto(REL);
    await expect(page.getByTestId('rel-empty')).toHaveText('Không khách nào cần chăm gấp.');
    await expect(page.getByTestId('owner-empty')).toHaveCount(1);
    await expect(page.getByTestId('rel-headline')).toHaveCount(0);
    await page.unroute('**/api/v1/owner/relations?*');
    // có kênh nhưng chưa có khách nào (mọi danh sách rỗng)
    await p3Hook(page.request, 'owner', 'data', { mode: 'empty' });
    await page.goto(REL);
    await expect(page.getByTestId('rel-empty')).toHaveText('Gen chưa nhận diện khách nào. Khi có tin từ Zalo, khách sẽ tự hiện ở đây.');
    await expect(page.locator('[data-testid=first-run-card]')).toHaveCount(0);
    await expect(page.getByTestId('owner-empty')).toHaveCount(1);
    await p3Hook(page.request, 'owner', 'data', { mode: 'full' });
  });

  test('lỗi tải danh sách = câu thân thiện + "Chi tiết kỹ thuật"; các nút lọc vẫn dùng được', async ({ page }) => {
    await p3Hook(page.request, 'owner', 'scenario', { mode: 'error', only: 'relations' });
    await page.goto(REL);
    await expect(page.getByTestId('rel-body').getByTestId('owner-error')).toContainText('Chi tiết kỹ thuật');
    await expect(chip(page, 'hot')).toBeVisible();
    await noObjectText(page);
    await p3Hook(page.request, 'owner', 'scenario', { mode: 'data' });
  });

  test('ngày đầu (nochannel) ⇒ đúng MỘT thẻ "Nối Zalo", không bảng', async ({ page }) => {
    await p3Hook(page.request, 'owner', 'data', { mode: 'nochannel' });
    await page.goto(REL);
    await expect(page.locator('[data-testid=first-run-card]')).toHaveCount(1);
    await expect(page.locator('[data-testid=first-run-card]')).toContainText('Nối Zalo để Gen bắt đầu đọc tin');
    await expect(page.locator('table')).toHaveCount(0);
    await expect(page.getByTestId('owner-empty')).toHaveCount(0);
    await expect(chip(page, 'care')).toHaveCount(0);
    await noObjectText(page);
    await p3Hook(page.request, 'owner', 'data', { mode: 'full' });
  });
});

test.describe('v0.1.59 · Quan hệ (điện thoại 390px)', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 390, height: 844 });
    await loginAsOwner(page);
    await p3Hook(page.request, 'owner', 'data', { mode: 'full' });
  });

  test('bảng xếp chồng thành danh sách; bấm dòng mở hồ sơ trong ngăn toàn màn, "Quay lại" đóng; vùng chạm ≥ 44px; không cuộn ngang ở mọi danh sách', async ({ page }) => {
    await page.goto(REL);
    await expect(page.locator('table')).toHaveCount(0);
    await expect(page.getByTestId('kit-list-row')).toHaveCount(4);
    await expect(page.getByTestId('rel-side-card')).toHaveCount(0);
    await expect(page.getByTestId('owner-tabbar').getByRole('link')).toHaveCount(5);
    await noHorizontalScroll(page, 'Cần chăm 390');
    const row = page.getByTestId('kit-list-row').first();
    await expect(row).toContainText('Bùi Đức Thắng');
    await expect(row).toContainText('Lý do');
    await expect(row).toContainText('Sắp rời');
    await expect(row).toContainText('Độ nóng');
    await expect(row).toContainText('Cơ hội mở');
    // nút trên dòng cao ≥ 44px và nằm trong khung nhìn
    for (const id of ['rel-ask-p-thang', 'rel-remind-p-thang']) {
      const b = (await page.getByTestId(id).boundingBox())!;
      expect(b.height, id).toBeGreaterThanOrEqual(44);
      expect(b.x + b.width, id).toBeLessThanOrEqual(390);
    }
    await row.locator('.kit-row__main').click();
    const sheet = page.getByRole('dialog', { name: 'Hồ sơ khách' });
    await expect(sheet).toBeVisible();
    await expect(page).toHaveURL(/[?&]nguoi=p-thang/);
    expect(Math.round((await sheet.boundingBox())!.width)).toBe(390);
    await expect(panel(page)).toHaveAttribute('data-person', 'p-thang');
    await noHorizontalScroll(page, 'hồ sơ 390');
    await page.getByTestId('profile-remind').click();
    await expect(page.getByTestId('profile-remind')).toHaveText('Đã tạo nhắc');
    await page.getByRole('button', { name: 'Quay lại' }).click();
    await expect(sheet).toBeHidden();
    await expect(page).not.toHaveURL(/nguoi=/);
    for (const list of ['hot', 'bridges', 'matches']) {
      await chip(page, list).click();
      await expect(chip(page, list)).toHaveAttribute('aria-pressed', 'true');
      await expect(page.getByTestId('rel-body')).toBeVisible();
      await noHorizontalScroll(page, `${list} 390`);
    }
    await noObjectText(page);
  });

  test('/owner/quan-he?nguoi=<id> trên điện thoại mở sẵn hồ sơ; "Nhờ Gen soạn tin" mở ngăn Hỏi Gen điền sẵn', async ({ page }) => {
    await page.goto(`${REL}?nguoi=p-bao`);
    const sheet = page.getByRole('dialog', { name: 'Hồ sơ khách' });
    await expect(sheet).toBeVisible();
    await expect(panel(page).getByTestId('profile-scores')).toBeVisible();
    await page.getByTestId('profile-ask').click();
    const dlg = page.getByRole('dialog', { name: 'Hỏi Gen' });
    await expect(dlg).toBeVisible();
    await expect(dlg.getByLabel('Câu hỏi cho Gen')).toHaveValue('Soạn tin hỏi thăm Nguyễn Văn Bảo');
  });

  test('Cung–cầu và Cầu nối trên điện thoại: Giới thiệu báo đã tạo nháp; link /graph; không cuộn ngang', async ({ page }) => {
    await page.goto(`${REL}?list=matches`);
    await expect(page.getByTestId('kit-list-row').first()).toBeVisible();
    await noHorizontalScroll(page, 'Cung–cầu 390');
    await page.getByTestId('match-introduce-match-1').click();
    await expect(page.getByTestId('match-done-match-1')).toHaveText('Đã tạo nháp, duyệt ở Việc');
    await noHorizontalScroll(page, 'sau Giới thiệu 390');
    await chip(page, 'bridges').click();
    await expect(page.getByTestId('bridge-graph-link')).toHaveAttribute('href', '/graph');
    await noHorizontalScroll(page, 'Cầu nối 390');
  });

  test('ngày đầu (nochannel) ⇒ đúng MỘT thẻ "Nối Zalo"; không cuộn ngang', async ({ page }) => {
    await p3Hook(page.request, 'owner', 'data', { mode: 'nochannel' });
    await page.goto(REL);
    await expect(page.locator('[data-testid=first-run-card]')).toHaveCount(1);
    await expect(page.getByTestId('kit-list-row')).toHaveCount(0);
    await noHorizontalScroll(page, 'nochannel 390');
    await p3Hook(page.request, 'owner', 'data', { mode: 'full' });
  });
});
