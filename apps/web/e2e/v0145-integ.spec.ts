import { expect, test, type Page } from '@playwright/test';
import { MANAGER, OWNER, loginAs, loginAsOwner, mockHook, resetMock } from './support';

/**
 * v0.1.45 — nghiệm thu sau khi gộp 4 gói (pin-rbac-cli, mcp-ssrf-log, run-pg-secrets, ws-people-help), phần mock:
 * (d) MCP đổi tool ghi → đọc hỏi PIN; (e) dòng đánh giá nhân sự bị gắn cờ có chip 'Đáng ngờ' + lý do khi rê chuột;
 * (f) Trợ giúp có đoạn 'Mã PIN bảo vệ được gì'. Không chỗ nào hiện lỗi 423 thô hay '[object Object]'.
 * (a)–(c) nằm ở pin-barriers-v0145.spec.ts.
 */

const PIN_DIALOG = 'Mã PIN xác nhận thao tác';

async function enterOwnerPin(page: Page): Promise<void> {
  const dlg = page.getByRole('dialog', { name: PIN_DIALOG });
  await expect(dlg).toBeVisible();
  await page.getByLabel('Mã PIN — chữ số 1/6').click();
  await page.keyboard.type(OWNER.pin);
  await expect(dlg).toBeHidden();
}

async function expectNoRawError(page: Page): Promise<void> {
  await expect(page.getByText('[object Object]')).toHaveCount(0);
  await expect(page.getByText(/\b423\b/)).toHaveCount(0);
  await expect(page.getByText(/PIN_REQUIRED/)).toHaveCount(0);
}

test.describe('v0.1.45 — nghiệm thu sau gộp (mock)', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  test('(d) MCP: đổi tool ghi → đọc hỏi PIN rồi lưu; đọc → ghi không hỏi', async ({ page }) => {
    await page.goto('/mcp');
    await expect(page.getByRole('heading', { name: 'MCP Hub', level: 2 })).toBeVisible();
    const crm = page.locator('.mcp-server', { hasText: 'CRM Genesis' });
    const sel = crm.getByLabel('Loại tool deal.upsert');
    await expect(sel).toHaveValue('write');
    // Lời nhắc PIN một lần cho cả bảng, không lặp dưới từng tool ghi.
    await expect(crm.getByText('Chuyển tool ghi sang đọc cần mã PIN')).toHaveCount(1);

    // Ghi → đọc hỏi xác nhận trước; "Giữ nguyên" không gửi gì.
    await sel.selectOption('read');
    const confirm = page.getByRole('dialog', { name: 'Chuyển deal.upsert sang Chỉ đọc?' });
    await expect(confirm).toBeVisible();
    await confirm.getByRole('button', { name: 'Giữ nguyên' }).click();
    await expect(confirm).toBeHidden();
    await expect(sel).toHaveValue('write');

    const ok = page.waitForResponse((r) => r.url().includes('/mcp/tools/') && r.request().method() === 'PATCH' && r.status() === 200);
    await sel.selectOption('read');
    await confirm.getByRole('button', { name: 'Chuyển sang Chỉ đọc' }).click();
    await enterOwnerPin(page);
    await ok;
    await expect(sel).toHaveValue('read');
    await expect(page.getByText('Đã chuyển deal.upsert sang Chỉ đọc — tool chạy không qua duyệt.')).toBeVisible();
    await expectNoRawError(page);

    // Đọc → ghi (chặt hơn): không hỏi PIN.
    const erp = page.locator('.mcp-server', { hasText: 'ERP Genesis' });
    const sel2 = erp.getByLabel('Loại tool order.lookup');
    const ok2 = page.waitForResponse((r) => r.url().includes('/mcp/tools/') && r.request().method() === 'PATCH');
    await sel2.selectOption('write');
    expect((await ok2).status()).toBe(200);
    await expect(sel2).toHaveValue('write');
    await expect(page.getByRole('dialog', { name: PIN_DIALOG })).toHaveCount(0);
  });

  test("(e) Đánh giá nhân sự: dòng bị gắn cờ có chip 'Đáng ngờ' với lý do khi rê chuột", async ({ page }) => {
    await page.goto('/people');
    await enterOwnerPin(page);
    await expect(page.getByRole('heading', { name: 'Đánh giá con người', level: 2 })).toBeVisible();
    const tu = page.locator('.ppl-row', { hasText: 'Phạm Anh Tú' });
    const chip = tu.getByTestId('ppl-suspicious');
    await expect(chip).toBeVisible();
    await expect(chip).toContainText('Đáng ngờ');
    await expect(chip).toHaveAttribute('title', /xin điểm/);
    await chip.hover();
    // Chỉ dòng bị gắn cờ mới có chip.
    await expect(page.getByTestId('ppl-suspicious')).toHaveCount(1);
    await expectNoRawError(page);
  });

  test('(d2) MCP: huỷ hộp PIN khi đổi ghi → đọc — tool vẫn Có ghi, báo đã huỷ', async ({ page }) => {
    await page.goto('/mcp');
    const crm = page.locator('.mcp-server', { hasText: 'CRM Genesis' });
    const sel = crm.getByLabel('Loại tool deal.upsert');
    await sel.selectOption('read');
    const confirm = page.getByRole('dialog', { name: 'Chuyển deal.upsert sang Chỉ đọc?' });
    await confirm.getByRole('button', { name: 'Chuyển sang Chỉ đọc' }).click();
    const pin = page.getByRole('dialog', { name: PIN_DIALOG });
    await expect(pin).toBeVisible();
    await pin.getByRole('button', { name: 'Huỷ' }).click();
    await expect(pin).toBeHidden();
    await expect(confirm.getByText('Đã huỷ — thao tác cần mã PIN.')).toBeVisible();
    await confirm.getByRole('button', { name: 'Giữ nguyên' }).click();
    await expect(sel).toHaveValue('write');
    // Giữ nguyên là lựa chọn có chủ ý — lỗi 'Đã huỷ' không ở lại trong ô.
    await expect(crm.getByText('Đã huỷ — thao tác cần mã PIN.')).toHaveCount(0);
    await expectNoRawError(page);
  });

  test("(e2) Đánh giá nhân sự: chi tiết có ghi chú 'Cảnh báo đáng ngờ', bỏ cờ có lý do", async ({ page }) => {
    await page.goto('/people');
    await enterOwnerPin(page);
    const tu = page.locator('.ppl-row', { hasText: 'Phạm Anh Tú' });
    await tu.getByRole('button', { name: 'Sửa điểm tay' }).click();
    const dlg = page.getByRole('dialog');
    const note = dlg.getByRole('note', { name: 'Cảnh báo đáng ngờ' });
    await expect(note).toContainText('xin điểm');
    await expect(note).toContainText('dùng điểm này. Hệ thống chỉ gắn cờ');
    await note.getByRole('button', { name: 'Bỏ cờ (đã xem chứng cứ)' }).click();
    await note.getByLabel('Lý do bỏ cờ').fill('Đã đọc tin gốc — nhân viên trích lời khách');
    await note.getByRole('button', { name: 'Bỏ cờ', exact: true }).click();
    await expect(dlg.getByTestId('ppl-suspicious-cleared')).toContainText('Đã đọc tin gốc');
    await expect(dlg.getByRole('note', { name: 'Cảnh báo đáng ngờ' })).toHaveCount(0);
    await dlg.getByRole('button', { name: 'Đóng', exact: true }).last().click();
    await expect(page.getByTestId('ppl-suspicious')).toHaveCount(0);
    await expectNoRawError(page);
  });

  test('(i) /guide/10 sau Hoàn tất: mời một người → hỏi PIN → đã tạo tài khoản', async ({ page }) => {
    await page.goto('/guide/10');
    await expect(page.getByText('Sau Hoàn tất, mời thêm người (tạo tài khoản) cần mã PIN')).toBeVisible();
    await page.getByRole('button', { name: 'Thêm người' }).click();
    await page.getByLabel('Tên hiển thị').fill('Chị Hoa');
    await page.getByLabel('Email').fill('hoa@genesis.local');
    const req = page.waitForResponse((r) => r.url().includes('/setup/steps/10') && r.request().method() === 'PUT' && r.ok());
    await page.getByRole('button', { name: 'Tiếp tục', exact: true }).click();
    await enterOwnerPin(page);
    await req;
    await expect(page.getByText('Đã tạo 1 tài khoản — chưa gửi thư mời thật')).toBeVisible();
    await expect(page.getByText('hoa@genesis.local')).toBeVisible();
    await expectNoRawError(page);
  });

  test("(f) Trợ giúp có đoạn 'Mã PIN bảo vệ được gì'", async ({ page }) => {
    await page.goto('/help');
    const card = page.getByTestId('help-pin-limits');
    await expect(card).toBeVisible();
    await expect(card.getByRole('heading', { name: 'Mã PIN bảo vệ được gì' })).toBeVisible();
    await expect(card.getByText(/KHÔNG phải lớp bảo vệ thứ hai/)).toBeVisible();
    await expectNoRawError(page);
  });
});

test.describe('v0.1.45 — vai trò khác Owner (mock)', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
  });

  test('Nhân viên (operator) ở /help: thẻ PIN gọi "bạn", không có cách lách điểm', async ({ page }) => {
    await loginAs(page, 'operator@genesis.local');
    await page.goto('/help');
    const card = page.getByTestId('help-pin-limits');
    await expect(card).toBeVisible();
    await expect(card).toContainText('phiên đăng nhập đang mở của bạn');
    await expect(card).not.toContainText('Đáng ngờ');
    await expect(card).not.toContainText('xin điểm');
    await expect(card).not.toContainText('Sếp');
  });

  test('Manager có system.manage = team ở /mcp: không có nút sửa (không nút chết 403)', async ({ page }) => {
    await mockHook(page.request, 'perm', { role: 'manager', permission: 'system.read', scope: 'all' });
    await mockHook(page.request, 'perm', { role: 'manager', permission: 'system.manage', scope: 'team' });
    await loginAs(page, 'manager@genesis.local');
    await page.goto('/mcp');
    await expect(page.getByRole('heading', { name: 'MCP Hub', level: 2 })).toBeVisible();
    await expect(page.locator('.mcp-server', { hasText: 'CRM Genesis' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Thêm máy chủ' })).toHaveCount(0);
    await expect(page.getByLabel('Loại tool deal.upsert')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Xoá' })).toHaveCount(0);
  });

  test("Manager có people_review.read = team, write = none: thấy cờ 'Đáng ngờ' nhưng không có nút Bỏ cờ / Sửa điểm (không nút chết 403)", async ({ page }) => {
    await mockHook(page.request, 'perm', { role: 'manager', permission: 'people_review.read', scope: 'team' });
    await loginAs(page, MANAGER.email);
    await page.goto('/people');
    const pin = page.getByRole('dialog', { name: PIN_DIALOG });
    await expect(pin).toBeVisible();
    await page.getByLabel('Mã PIN — chữ số 1/6').click();
    await page.keyboard.type(MANAGER.pin);
    await expect(pin).toBeHidden();
    const row = page.locator('.ppl-row', { has: page.getByTestId('ppl-suspicious') }).first();
    await expect(row).toBeVisible();
    await expect(page.getByRole('button', { name: 'Sửa điểm tay' })).toHaveCount(0);
    await row.getByRole('button', { name: 'Xem chi tiết' }).click();
    const dlg = page.getByRole('dialog');
    await expect(dlg.getByRole('note', { name: 'Cảnh báo đáng ngờ' })).toBeVisible();
    await expect(dlg.getByTestId('ppl-read-only')).toHaveText('Chỉ người có quyền sửa đánh giá mới sửa điểm hoặc bỏ cờ được — nhờ Owner.');
    await expect(dlg.getByRole('button', { name: /Bỏ cờ/ })).toHaveCount(0);
    await expect(dlg.getByRole('button', { name: 'Lưu điểm mới' })).toHaveCount(0);
    await expectNoRawError(page);
  });
});
