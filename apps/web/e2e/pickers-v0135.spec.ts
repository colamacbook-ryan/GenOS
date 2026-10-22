/**
 * v0.1.35 (F-1): giao/gán dùng người và trợ lý THẬT — id lấy từ `/pickers/users` / `/pickers/agents` (UUID), không
 * còn danh sách cứng 'u-lan' / 'agent-ka'. Mock kiểm như API thật: id không phải UUID → 422 VALIDATION.
 */
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { AUDITOR, loginAs, loginAsOwner, resetMock } from './support';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

async function pickerUsers(page: Page): Promise<Array<{ id: string; name: string; me: boolean }>> {
  const r = await page.request.get('/api/v1/pickers/users');
  expect(r.status()).toBe(200);
  return ((await r.json()) as { items: Array<{ id: string; name: string; me: boolean }> }).items;
}

async function csrfHeaders(page: Page): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((c) => c.name === 'gh_csrf')?.value ?? '';
  return { 'X-CSRF-Token': csrf };
}

test.describe('v0.1.35 · chọn người / trợ lý thật', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request as APIRequestContext, 'finished');
    await loginAsOwner(page);
  });

  test('Hộp thư › Giao cho người khác gửi user_id UUID của Chị Lan Phạm', async ({ page }) => {
    const users = await pickerUsers(page);
    const lan = users.find((u) => u.name === 'Chị Lan Phạm');
    expect(lan?.id).toMatch(UUID_RE);
    expect(users.find((u) => u.me)?.name).toBe('Anh Nguyễn Văn A (Chủ)');
    expect(users.every((u) => !('email' in u))).toBe(true);

    await page.goto('/inbox');
    const card = page.locator('.ib-card').first();
    await card.getByRole('button', { name: 'Giao cho người khác' }).click();
    const dlg = page.getByRole('dialog', { name: 'Giao cho người khác' });
    await expect(dlg.getByRole('list').getByRole('button').first()).toHaveText('Tôi');
    const req = page.waitForRequest((r) => /\/inbox\/[^/]+\/assign$/.test(r.url()) && r.method() === 'POST');
    await dlg.getByText('Chị Lan Phạm').click();
    const body = (await req).postDataJSON() as { user_id: string };
    expect(body.user_id).toMatch(UUID_RE);
    expect(body.user_id).toBe(lan!.id);
    await expect(dlg).toBeHidden();
  });

  test('Deal & Vụ việc › Gán người xử lý gửi assignee_user_id UUID, dòng hiện tên', async ({ page }) => {
    await page.goto('/deals?dtab=cases');
    const caseRow = page.locator('tr', { hasText: 'CAS-0017' });
    await expect(caseRow).toBeVisible();
    await caseRow.getByRole('button', { name: 'Đổi' }).click();
    const dlg = page.getByRole('dialog', { name: 'Gán người xử lý' });
    await expect(dlg.getByText('Chưa gán')).toBeVisible();
    const req = page.waitForRequest((r) => r.url().includes('/cases/') && r.method() === 'PATCH');
    await dlg.getByText('Anh Minh Kiểm').click();
    const body = (await req).postDataJSON() as { assignee_user_id: string };
    expect(body.assignee_user_id).toMatch(UUID_RE);
    await expect(dlg).toBeHidden();
    await expect(caseRow).toContainText('Anh Minh Kiểm');
  });

  test('Nhóm & Con người › Gán BOT trực nhóm gửi agent_id UUID từ /pickers/agents', async ({ page }) => {
    const agents = ((await (await page.request.get('/api/v1/pickers/agents')).json()) as { items: Array<{ id: string; name: string }> }).items;
    const hc = agents.find((a) => a.name === 'Admin hậu cần');
    expect(hc?.id).toMatch(UUID_RE);
    expect(agents.some((a) => a.name === 'Bé Heo')).toBe(false); // agent đang tắt không có trong danh sách

    await page.goto('/directory');
    const groupRow = page.locator('tr', { hasText: 'Vận hành Genesis — Quý 4' });
    await expect(groupRow).toBeVisible();
    await groupRow.getByRole('button', { name: 'Đổi' }).click();
    const dlg = page.getByRole('dialog', { name: 'Gán BOT trực nhóm' });
    await dlg.getByText('Admin hậu cần').click();
    const req = page.waitForRequest((r) => /\/directory\/groups\/[^/]+\/bot$/.test(r.url()) && r.method() === 'POST');
    await dlg.getByRole('button', { name: 'Lưu' }).click();
    const body = (await req).postDataJSON() as { agent_id: string };
    expect(body.agent_id).toBe(hc!.id);
    await expect(dlg).toBeHidden();
    await expect(groupRow).toContainText('Admin hậu cần');
  });

  test('Bản đồ quan hệ › Danh sách › Phụ trách liệt kê người thật, chọn → owner_user_id=<uuid>', async ({ page }) => {
    const lan = (await pickerUsers(page)).find((u) => u.name === 'Chị Lan Phạm')!;
    await page.goto('/graph');
    await expect(page.getByText('Nguyễn Văn Bảo')).toBeVisible();
    await page.getByRole('button', { name: /Phụ trách/ }).click();
    await expect(page.getByRole('option', { name: 'Tôi' })).toBeVisible();
    await expect(page.getByRole('option', { name: 'Nguyễn Thu Hà' })).toHaveCount(0);
    const req = page.waitForRequest((r) => r.url().includes('/graph/list') && r.url().includes('owner_user_id='));
    await page.getByRole('option', { name: 'Chị Lan Phạm' }).click();
    const url = new URL((await req).url());
    expect(url.searchParams.get('owner_user_id')).toBe(lan.id);
    await expect(page.getByText('Nguyễn Văn Bảo')).toBeVisible();
  });

  test("id giả 'u-lan' → 422 VALIDATION; UI hiện chuỗi thân thiện, không object", async ({ page }) => {
    await page.goto('/inbox');
    const card = page.locator('.ib-card').first();
    await expect(card).toBeVisible();
    const itemId = await card.getAttribute('data-gen-target').then((v) => v!.split(':')[1]);

    // Gọi thẳng mock như API thật.
    const direct = await page.request.post(`/api/v1/inbox/${itemId}/assign`, {
      data: { user_id: 'u-lan' },
      headers: await csrfHeaders(page),
    });
    expect(direct.status()).toBe(422);
    const prob = (await direct.json()) as { code: string; detail: unknown; errors: Record<string, string> };
    expect(prob.code).toBe('VALIDATION');
    expect(prob.detail).toBeNull();
    expect(typeof prob.errors.user_id).toBe('string');
    const unknown = await page.request.post(`/api/v1/inbox/${itemId}/assign`, {
      data: { user_id: '0190f1a0-0000-7000-8000-00000000dead' },
      headers: await csrfHeaders(page),
    });
    expect(unknown.status()).toBe(404);

    // UI: ép request gửi id giả → hộp thoại hiện lỗi dạng chuỗi.
    await page.route('**/api/v1/inbox/*/assign', (route) => route.continue({ postData: JSON.stringify({ user_id: 'u-lan' }) }));
    await card.getByRole('button', { name: 'Giao cho người khác' }).click();
    const dlg = page.getByRole('dialog', { name: 'Giao cho người khác' });
    await dlg.getByText('Chị Lan Phạm').click();
    const alert = dlg.getByRole('alert');
    await expect(alert).toBeVisible();
    await expect(alert).not.toHaveText('');
    await expect(alert).not.toContainText('[object Object]');
    await expect(dlg).toBeVisible();
  });
});

/** Vai trò khác Owner — backend đã có ma trận vai trò (tests/test_pickers_v0135.py), đây là phía giao diện. */
test.describe('v0.1.35 · chọn người theo vai trò', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request as APIRequestContext, 'finished');
  });

  test('Operator: Hộp thư › Giao cho người khác thấy "Tôi" + người khác, gửi UUID', async ({ page }) => {
    await loginAs(page, 'operator@genesis.local');
    const users = await pickerUsers(page);
    const me = users.find((u) => u.me);
    expect(me?.id).toMatch(UUID_RE);
    expect(me?.name).not.toBe('Anh Nguyễn Văn A (Chủ)');
    const owner = users.find((u) => u.name === 'Anh Nguyễn Văn A (Chủ)' && !u.me);

    await page.goto('/inbox');
    const card = page.locator('.ib-card').first();
    await card.getByRole('button', { name: 'Giao cho người khác' }).click();
    const dlg = page.getByRole('dialog', { name: 'Giao cho người khác' });
    await expect(dlg.getByRole('list').getByRole('button').first()).toHaveText('Tôi');
    await expect(dlg.getByText('Anh Nguyễn Văn A (Chủ)')).toBeVisible();
    // Operator không có roles.manage → không bị chỉ tới màn mời người dùng.
    await expect(dlg.getByText(/mời thêm ở Điều khiển hệ thống/)).toHaveCount(0);
    expect(owner?.id).toMatch(UUID_RE);
    const req = page.waitForRequest((r) => /\/inbox\/[^/]+\/assign$/.test(r.url()) && r.method() === 'POST');
    await dlg.getByText('Anh Nguyễn Văn A (Chủ)').click();
    expect(((await req).postDataJSON() as { user_id: string }).user_id).toBe(owner!.id);
    await expect(dlg).toBeHidden();
  });

  test('Auditor: Bản đồ quan hệ › bộ lọc Phụ trách vẫn tải người thật', async ({ page }) => {
    await loginAs(page, AUDITOR.email);
    const users = await pickerUsers(page);
    const owner = users.find((u) => u.name === 'Anh Nguyễn Văn A (Chủ)')!;
    expect(owner.id).toMatch(UUID_RE);
    await page.goto('/graph');
    await expect(page.getByText('Nguyễn Văn Bảo')).toBeVisible();
    await page.getByRole('button', { name: /Phụ trách/ }).click();
    await expect(page.getByRole('option', { name: 'Tôi' })).toBeVisible();
    const req = page.waitForRequest((r) => r.url().includes('/graph/list') && r.url().includes('owner_user_id='));
    await page.getByRole('option', { name: 'Anh Nguyễn Văn A (Chủ)' }).click();
    expect(new URL((await req).url()).searchParams.get('owner_user_id')).toBe(owner.id);
  });
});
