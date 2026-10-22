import { expect, test } from '@playwright/test';
import { MANAGER, askGen, enterPin, enterPinIfAsked, hubSim, khoCalls, loginAs, loginAsOwner, mockHook, p3Hook, resetMock } from './support';

/**
 * v0.1.50 (F-81, QD-18) — mock, tất định (KHÔNG có Kho / Gen-hub thật): Gen đề xuất ghi Kho dữ liệu → thẻ "Ghi vào Kho dữ liệu" hiện đúng bảng +
 * trường → Xác nhận và ghi Kho → 423 → nhập mã PIN → "Đã ghi vào Kho: PHIEN-12". Bấm Huỷ thì mock ghi nhận 0 lời gọi /hub/kho/write.
 * Thiếu quyền ghi Kho → khoá nút + nút mở thẻ Gen-hub; lỗi "Chưa chắc đã ghi" → câu đúng, bấm lại được; dòng 9 "Gen ghi Kho" đạt sau lần ghi đầu.
 */

const CARD = 'Ghi vào Kho dữ liệu';
const WARNING = 'Ghi thẳng vào Kho dữ liệu qua Gen-hub khi Sếp bấm Xác nhận và nhập mã PIN — không tự hoàn tác.';

test.beforeEach(async ({ page }) => {
  await resetMock(page.request, 'finished');
  await page.setViewportSize({ width: 1440, height: 900 });
  await hubSim(page.request, { link: 'on', write: 'ok', kho: 'ok' }); // Gen-hub đã nối + Kiểm tra xanh, token có quyền ghi Kho
  await loginAsOwner(page);
});

test('Gen đề xuất ghi Phiên → thẻ đúng bảng + trường → Xác nhận → 423 → nhập PIN → "Đã ghi vào Kho: PHIEN-12" → dòng 9 đạt', async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto('/overview');
  const card = await askGen(page, 'ghi phiên hôm nay vào Kho', CARD);

  await expect(card).toContainText('Đề xuất · Ghi vào Kho dữ liệu');
  await expect(card).toContainText('Bảng');
  await expect(card).toContainText('Phiên');
  await expect(card).toContainText('Bản ghi mới');
  const table = card.getByTestId('gen-kho-table');
  await expect(table.locator('thead th')).toHaveText(['Trường', 'Sẽ ghi']);
  await expect(table.locator('tbody th')).toHaveText(['Chủ đề', 'Ngày', 'Đã chốt', 'Việc tiếp']);
  await expect(table.locator('tbody tr').first()).toContainText('Gen-Harness v0.1.50 — Gen nhớ và ghi Kho có mã PIN');
  await expect(card).toContainText('Cần mã PIN');
  await expect(card.getByTestId('gen-kho-warning')).toHaveText(WARNING);
  expect((await khoCalls(page.request)).calls).toBe(0); // chưa Xác nhận ⇒ chưa có lời gọi ghi nào

  await card.getByRole('button', { name: 'Xác nhận và ghi Kho' }).click();
  await enterPin(page); // 423 PIN_REQUIRED → hộp PIN → gửi lại
  await expect(card).toContainText('Đã ghi vào Kho: PHIEN-12');
  await expect(card.getByRole('button', { name: 'Xác nhận và ghi Kho' })).toHaveCount(0);

  const calls = await khoCalls(page.request);
  expect(calls.calls).toBe(1);
  expect(calls.writes.map((w) => [w.tool, w.code])).toEqual([['kho_create', 'PHIEN-12']]);

  // Việc Sếp cần làm: dòng 9 "Gen ghi Kho" (không bắt buộc) đã đạt sau lần ghi Kho thật đầu tiên.
  await page.goto('/guide/viec-sep');
  const row = page.getByRole('region', { name: 'Gen ghi Kho' });
  await expect(row).toBeVisible();
  await expect(row.getByTestId('boss-result')).toContainText('Đạt');
  await expect(row).toContainText('Không bắt buộc');
  await expect(row.getByRole('button')).toHaveCount(0);
  await expect(page.getByText('Đã đạt 0/1 dòng bắt buộc')).toBeVisible(); // không tính vào bắt buộc
});

test('Bấm Huỷ ⇒ không ghi gì: mock ghi nhận 0 lời gọi /hub/kho/write', async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto('/overview');
  const card = await askGen(page, 'ghi phiên hôm nay vào Kho', CARD);
  await card.getByRole('button', { name: 'Huỷ' }).click();
  await expect(card).toContainText('Đã huỷ — không ghi gì vào Kho');
  await expect(card.getByRole('button', { name: 'Xác nhận và ghi Kho' })).toHaveCount(0);
  expect((await khoCalls(page.request)).calls).toBe(0);
  // Chưa ghi gì thật ⇒ dòng 9 "Gen ghi Kho" ở Việc Sếp cần làm vẫn chưa đạt.
  const res = await page.request.get('/api/v1/boss-checks');
  expect(((await res.json()) as { results: { kho_write?: unknown } }).results.kho_write ?? null).toBeNull();
});

test('Gen đề xuất sửa Việc: cột "Hiện tại" → "Sẽ ghi"; Sửa đổi trường rồi ghi; ghi sai ⇒ "Chưa chắc đã ghi" rồi bấm lại được', async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto('/overview');
  const card = await askGen(page, 'cập nhật việc xong vào Kho', CARD);
  const table = card.getByTestId('gen-kho-table');
  await expect(table.locator('thead th')).toHaveText(['Trường', 'Hiện tại', 'Sẽ ghi']);
  await expect(table.locator('tbody tr').first()).toContainText('Đang làm');
  await expect(table.locator('tbody tr').first()).toContainText('Xong');
  await expect(card).toContainText('VIEC-12 · Soạn báo giá ván MDF E1');

  // Sửa: bảng / mã bản ghi khoá cứng (không có ô), Trạng thái là ô chọn, Ngày xong là ô ngày.
  await card.getByRole('button', { name: 'Sửa' }).click();
  await expect(card.getByLabel('Trạng thái')).toHaveValue('Xong');
  await expect(card.getByLabel('Ngày xong')).toHaveAttribute('type', 'date');
  await expect(card.getByLabel('Bảng')).toHaveCount(0);
  await card.getByLabel('Trạng thái').selectOption('Chờ duyệt');

  await hubSim(page.request, { kho: 'uncertain' });
  await card.getByRole('button', { name: 'Xác nhận và ghi Kho' }).click();
  await enterPinIfAsked(page);
  const alert = card.getByRole('alert');
  await expect(alert).toContainText('Chưa chắc đã ghi — Sếp mở Kho kiểm trước khi bấm lại');
  await expect(alert.getByText('Chi tiết kỹ thuật')).toBeVisible();
  await expect(card).not.toContainText('Đã ghi vào Kho');
  expect((await khoCalls(page.request)).writes).toHaveLength(0);

  await hubSim(page.request, { kho: 'ok' });
  await card.getByRole('button', { name: 'Xác nhận và ghi Kho' }).click();
  await enterPinIfAsked(page);
  await expect(card).toContainText('Đã ghi vào Kho: VIEC-12');
  const calls = await khoCalls(page.request);
  expect(calls.calls).toBe(2); // một lần không chắc + một lần ghi thật
  expect(calls.writes.map((w) => [w.tool, w.code])).toEqual([['kho_update', 'VIEC-12']]);
});

test('Ghi "chưa chắc" ⇒ câu nói rõ Huỷ / Xác nhận lại; Sếp mở Kho thấy đã có ⇒ Huỷ: thẻ KHÔNG nói "không ghi gì vào Kho"', async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto('/overview');
  const card = await askGen(page, 'ghi phiên hôm nay vào Kho', CARD);
  await hubSim(page.request, { kho: 'uncertain' });
  await card.getByRole('button', { name: 'Xác nhận và ghi Kho' }).click();
  await enterPinIfAsked(page);
  const alert = card.getByRole('alert');
  await expect(alert).toContainText('Kho đã có bản ghi thì bấm Huỷ; chưa có thì bấm Xác nhận lại.');
  await card.getByRole('button', { name: 'Huỷ' }).click();
  await expect(card.getByTestId('gen-prop-cancelled')).toContainText('Đã đóng — Gen không ghi thêm vào Kho (lần ghi trước chưa chắc: Kho có thể đã có bản ghi)');
  await expect(card).not.toContainText('không ghi gì vào Kho');
  await expect(card.getByRole('alert')).toHaveCount(0);
  expect((await khoCalls(page.request)).calls).toBe(1); // chỉ lần chưa chắc — Huỷ không gọi ghi
});

test('Token thiếu quyền ghi Kho: khoá Xác nhận + nút "Mở thẻ Gen-hub"; tick quyền + Kiểm tra ⇒ thẻ tự mở khoá', async ({ page }) => {
  test.setTimeout(90_000);
  await hubSim(page.request, { write: 'missing', link: 'on' });
  await page.goto('/overview');
  const card = await askGen(page, 'ghi phiên hôm nay vào Kho (thiếu quyền)', CARD);
  await expect(card.getByRole('button', { name: 'Xác nhận và ghi Kho' })).toBeDisabled();
  await expect(card.getByTestId('gen-kho-missing')).toContainText('Gen-hub chưa cấp quyền ghi Kho');

  await card.getByRole('button', { name: 'Mở thẻ Gen-hub' }).click();
  await expect(page).toHaveURL(/\/connections#genhub$/);
  const write = page.getByTestId('hub-write-scopes');
  await expect(write).toBeVisible();
  await expect(write).toContainText('Quyền ghi Kho (tuỳ chọn)');
  await expect(write.getByText('Chưa', { exact: true })).toHaveCount(2);
  await expect(write).toContainText('Vào Gen-hub tick quyền ghi Kho (tạo và sửa bản ghi — tên trong Gen-hub: kho_create, kho_update) cho token của Gen-Harness rồi bấm Kiểm tra.');
  await expect(write).toContainText('Gen chỉ ghi khi Sếp bấm Xác nhận + nhập mã PIN trên thẻ đề xuất.');

  // Sếp tick quyền ghi ở Gen-hub (mock: hook) rồi bấm Kiểm tra (cần PIN) ⇒ "Có" ×2; thẻ đề xuất mở khoá.
  await hubSim(page.request, { write: 'ok' });
  await page.locator('[data-gen-target="mcp.hub_link.test"]').click();
  await enterPinIfAsked(page);
  await expect(write.getByText('Có', { exact: true })).toHaveCount(2);
  await expect(write).toContainText('Đủ quyền ghi Kho (Phiên, Việc).');
  await expect(card.getByRole('button', { name: 'Xác nhận và ghi Kho' })).toBeEnabled();
  expect((await khoCalls(page.request)).calls).toBe(0);
});

test('mock ghi nhận mã lỗi theo hook: HUB_LINK_OFF khi Gen-hub chưa nối ⇒ câu thân thiện + nút mở Kết nối › Gen-hub; không ghi gì', async ({ page }) => {
  test.setTimeout(90_000);
  await resetMock(page.request, 'finished'); // Gen-hub CHƯA nối
  await loginAsOwner(page);
  await page.goto('/overview');
  const card = await askGen(page, 'ghi phiên hôm nay vào Kho', CARD);
  await card.getByRole('button', { name: 'Xác nhận và ghi Kho' }).click();
  await enterPinIfAsked(page);
  const alert = card.getByRole('alert');
  await expect(alert).toContainText('Gen-hub đang tắt — vào Kết nối › Gen-hub bấm Kiểm tra để bật lại.');
  await expect(alert.getByText('Chi tiết kỹ thuật')).toBeVisible();
  await card.getByRole('button', { name: 'Mở Kết nối › Gen-hub' }).click();
  await expect(page).toHaveURL(/\/connections#genhub$/);
  expect((await khoCalls(page.request)).writes).toHaveLength(0);
  // Hook p3 chung vẫn dùng được (đảm bảo `p3Hook` import có tác dụng trong spec này).
  expect(await p3Hook(page.request, 'mcp', 'khoCalls')).toMatchObject({ calls: 1 });
});

// ── Bước 2 của Boss (F-87): chuông → hội thoại → thẻ ghi Phiên của bản mới ─────────────────────────────────────────────

const GEN_PANEL = /Gen — trợ lý quản trị/;

test('Boss bước 2: chuông "Gen đề xuất ghi Kho · Phiên v0.1.50" → /overview?gen=… → bảng Trường | Sẽ ghi + "Huỷ là huỷ cho mọi Owner" → Xác nhận + PIN → PHIEN-12', async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto('/overview');
  const bell = page.getByRole('button', { name: /^Thông báo/ });
  await expect(bell).toBeVisible();
  // Job gen_kho_release (mock): một hội thoại có thẻ đề xuất + một chuông gen.kho_proposal — KHÔNG ghi gì.
  const seeded = (await p3Hook(page.request, 'gen', 'khoRelease', { version: 'v0.1.50' })) as { conversation_id: string };
  expect(seeded.conversation_id).toMatch(/^[0-9a-f-]{36}$/);
  expect((await khoCalls(page.request)).calls).toBe(0);

  await bell.click();
  const item = page.getByRole('dialog', { name: 'Thông báo' }).locator('.nt-item', { hasText: 'Gen đề xuất ghi Kho · Phiên v0.1.50' });
  await expect(item).toBeVisible();
  await item.click();
  // v0.1.55: chuông Gen dẫn vào Console /overview?gen=… (không về Mặt tiền); bỏ tham số khỏi địa chỉ.
  await expect(page).toHaveURL(/\/overview$/);
  const panel = page.getByRole('complementary', { name: GEN_PANEL });
  await expect(panel).toBeVisible();
  await expect(panel).toContainText('Máy chủ Gen-Harness vừa lên v0.1.50.');
  const card = panel.getByRole('group', { name: `Đề xuất: ${CARD}` }).last();
  await expect(card).toBeVisible();
  // Tạo bản ghi ⇒ bảng chỉ có "Trường | Sẽ ghi" (cột "Hiện tại" chỉ có khi SỬA bản ghi).
  const table = card.getByTestId('gen-kho-table');
  await expect(table.locator('thead th')).toHaveText(['Trường', 'Sẽ ghi']);
  await expect(table.locator('tbody th')).toHaveText(['Chủ đề', 'Ngày', 'Đã chốt']);
  await expect(table.locator('tbody tr').first()).toContainText('Gen-Harness lên bản v0.1.50');
  await expect(card.getByTestId('gen-kho-warning')).toHaveText(WARNING);
  await expect(card.getByTestId('gen-kho-release-note')).toHaveText(
    'Phiên của bản v0.1.50: mỗi bản chỉ ghi vào Kho một lần cho cả tổ chức — Owner khác đã ghi thì thẻ này tự đóng; bấm Huỷ là huỷ cho mọi Owner.',
  );

  await card.getByRole('button', { name: 'Xác nhận và ghi Kho' }).click();
  await enterPin(page);
  await expect(card).toContainText('Đã ghi vào Kho: PHIEN-12');
  const calls = await khoCalls(page.request);
  expect(calls.calls).toBe(1);
  expect(calls.writes.map((w) => [w.tool, w.code])).toEqual([['kho_create', 'PHIEN-12']]);
});

test('Thẻ ghi Phiên mà Owner khác đã ghi: mở từ chuông thấy "Thẻ đã đóng — Owner khác đã ghi …", không còn nút Xác nhận', async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto('/overview');
  await p3Hook(page.request, 'gen', 'khoRelease', { version: 'v0.1.50', closed: true });
  await page.getByRole('button', { name: /^Thông báo/ }).click();
  await page.getByRole('dialog', { name: 'Thông báo' }).locator('.nt-item', { hasText: 'Gen đề xuất ghi Kho · Phiên v0.1.50' }).click();
  const card = page.getByRole('complementary', { name: GEN_PANEL }).getByRole('group', { name: `Đề xuất: ${CARD}` }).last();
  await expect(card.getByTestId('gen-prop-cancelled')).toHaveText('Thẻ đã đóng — Owner khác đã ghi bản này vào Kho (PHIEN-12)');
  await expect(card.getByRole('button', { name: 'Xác nhận và ghi Kho' })).toHaveCount(0);
  expect((await khoCalls(page.request)).calls).toBe(0);
});

test('Chuông không mất thông báo phát lúc ổ cắm realtime còn đang kết nối: ổ cắm mở muộn ⇒ chuông tự tải lại và có "Gen đề xuất ghi Kho · Phiên"', async ({ page }) => {
  test.setTimeout(90_000);
  await page.routeWebSocket(/\/api\/v1\/ws/, async (ws) => {
    await new Promise((r) => setTimeout(r, 1500)); // máy chậm: ổ cắm mở sau khi thông báo đã phát
    ws.connectToServer();
  });
  await page.goto('/overview');
  await p3Hook(page.request, 'gen', 'khoRelease', { version: 'v0.1.50' });
  await page.getByRole('button', { name: /^Thông báo/ }).click();
  await expect(page.getByRole('dialog', { name: 'Thông báo' }).locator('.nt-item', { hasText: 'Gen đề xuất ghi Kho · Phiên v0.1.50' })).toBeVisible();
});

test('Kho từ chối (HUB_WRITE_REJECTED) ⇒ câu nêu ĐÚNG lý do Kho + "Bấm Sửa"; "Chi tiết kỹ thuật" có mã + lý do; thẻ còn chờ, không ghi', async ({ page }) => {
  test.setTimeout(90_000);
  await hubSim(page.request, { kho: 'rejected' });
  await page.goto('/overview');
  const card = await askGen(page, 'ghi việc mới vào Kho', CARD);
  await card.getByRole('button', { name: 'Xác nhận và ghi Kho' }).click();
  await enterPinIfAsked(page);
  const alert = card.getByRole('alert');
  const why = "Giá trị 'Trạng thái' không có trong danh sách lựa chọn của Kho";
  await expect(alert.locator('.write-error__text')).toHaveText(`Kho từ chối lần ghi này: ${why} — chưa ghi gì. Bấm Sửa để chỉnh các trường rồi Xác nhận lại.`);
  await alert.getByText('Chi tiết kỹ thuật').click();
  await expect(alert.locator('details code')).toContainText('HUB_WRITE_REJECTED');
  await expect(alert.locator('details code')).toContainText(why);
  await expect(card.getByRole('button', { name: 'Xác nhận và ghi Kho' })).toBeEnabled();
  await expect(card).not.toContainText('Đã ghi vào Kho');
  const calls = await khoCalls(page.request);
  expect(calls.calls).toBe(1);
  expect(calls.writes).toHaveLength(0);
});

test('Vai trò Manager: không có thẻ Gen nhớ ở Cài đặt › Bộ não AI, không có khối "Quyền ghi Kho" ở Kết nối (chỉ Sếp)', async ({ page }) => {
  test.setTimeout(90_000);
  await mockHook(page.request, 'perm', { role: 'manager', permission: 'system.read', scope: 'all' });
  await loginAs(page, MANAGER.email);
  const memoryCalls: string[] = [];
  page.on('request', (r) => {
    if (r.url().includes('/api/v1/gen/memory')) memoryCalls.push(r.url());
  });
  await page.goto('/system?tab=brain');
  await expect(page.getByRole('region', { name: 'Nguồn AI cho việc nền' })).toBeVisible(); // đã vào tab Bộ não AI
  await expect(page.locator('#gen-memory')).toHaveCount(0);
  await expect(page.getByText('Gen nhớ', { exact: true })).toHaveCount(0);

  await page.goto('/connections#genhub');
  const hub = page.locator('#genhub');
  await expect(hub).toContainText('Chỉ Sếp (Owner) cấu hình và dùng Gen-hub.');
  await expect(page.getByTestId('hub-write-scopes')).toHaveCount(0);
  expect(memoryCalls).toEqual([]); // thẻ ẩn hẳn với vai trò khác — không gọi /gen/memory
});
