/**
 * v0.1.62: Antigravity CLI (agy) mở cho MỌI vai (Owner chọn 16/10/2026, thay luật v0.1.38 F-22 "chỉ dùng cho Gen").
 * Màn API & Model: dữ liệu mẫu (bản cài cũ) gán agy cho "Sàng lọc & suy luận chính" → KHÔNG còn nhãn "Chỉ cho Gen"; ô chọn
 * model của MỌI dòng liệt kê model agy không kèm chú thích hạn chế; gán agy cho Sàng lọc → lưu được (200), gán cho Gen
 * → lưu được. Thẻ Antigravity CLI ở Kết nối nói đúng phạm vi mới + rủi ro còn lại.
 *
 * Mock dùng chung (test/mock-p4-api.ts) không còn luật owner-only / `blocked_reason` / 409 AGY_OWNER_GEN_ONLY — đúng
 * API máy chủ v0.1.62.
 *
 * F-17: tài khoản mạng xã hội chuyển máy mà phiên không mở được (needs_login + key_changed, has_session=false) → gợi ý
 * và nút đều là "Đăng nhập lại".
 */
import { expect, test, type APIRequestContext } from '@playwright/test';
import { loginAsOwner, resetMock } from './support';

const GEN_LABEL = 'Gen — trợ lý quản trị';

test.describe('v0.1.62 · Antigravity CLI dùng được cho mọi vai', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request as APIRequestContext, 'finished');
    await loginAsOwner(page);
  });

  test('gán model agy cho Sàng lọc và cho Gen → đều lưu được, không còn nhãn "Chỉ cho Gen"', async ({ page }) => {
    const providers = (await (await page.request.get('/api/v1/providers')).json()) as Array<{ kind: string; models: Array<{ id: string; model_name: string }> }>;
    const agyModels = providers.filter((p) => p.kind === 'antigravity_cli').flatMap((p) => p.models);
    expect(agyModels.length).toBeGreaterThan(0);

    // Thẻ Antigravity CLI nói đúng phạm vi mới (cùng một câu với API) — v0.1.42 (F-61): thẻ chỉ ở Kết nối.
    await page.goto('/connections');
    await expect(page.getByTestId('cli-card-antigravity_cli')).toContainText('Dùng được cho: Gen của Sếp và agent nội bộ (chạy trong hệ thống); Bản tin, sàng lọc tin người lạ');
    await expect(page.getByTestId('cli-card-antigravity_cli')).not.toContainText('Chỉ dùng cho Gen');

    await page.goto('/api');
    const table = page.locator('.apm-table');
    await expect(table).toContainText('Sàng lọc & suy luận chính');
    await expect(table).toContainText(GEN_LABEL);

    // Slot sàng lọc đang gán model agy (bản cài cũ): không còn nhãn / câu "chỉ cho Gen".
    const refineryRow = table.locator('tr', { hasText: 'Sàng lọc & suy luận chính' });
    await expect(refineryRow.getByTestId('binding-blocked-core.refinery')).toHaveCount(0);
    await expect(refineryRow.getByTestId('binding-blocked-hint-core.refinery')).toHaveCount(0);
    await expect(table).not.toContainText('Chỉ cho Gen');

    // Soạn lại / dịch nháp là lớp ngoại giao ⇒ chạy agy qua hộp riêng; hộp chưa chạy nên chưa dùng agy. Dòng chưa gán agy thì
    // KHÔNG hiện cảnh báo trong bảng (cảnh báo ở dòng chỉ dành cho dòng gán agy cũ — xem ApiScreen, test vitest
    // agy-layers-goi-g); lý do (câu của máy chủ) nằm ở hộp chọn model, model agy bị làm mờ, KHÔNG phải câu "chỉ cho Gen".
    const replyRow = table.locator('tr', { hasText: 'Soạn lại / dịch nháp' });
    await expect(replyRow.getByTestId('binding-agy-blocked-core.reply')).toHaveCount(0);
    await replyRow.locator('.apm-model-pill').click();
    let dlg = page.getByRole('dialog', { name: /Soạn lại \/ dịch nháp/ });
    await expect(dlg.locator('option', { hasText: 'chỉ cho Gen' })).toHaveCount(0);
    await expect(dlg.getByTestId('binding-agy-blocked-note')).toContainText('Hộp riêng Antigravity chưa chạy');
    await expect(dlg.locator(`option[value="${agyModels[0].id}"]`)).toBeDisabled();
    await dlg.getByRole('button', { name: 'Huỷ' }).click();
    await expect(dlg).toBeHidden();

    // 1) Gán model agy cho Sàng lọc → 200.
    await refineryRow.locator('.apm-model-pill').click();
    dlg = page.getByRole('dialog', { name: /Sàng lọc & suy luận chính/ });
    await dlg.getByLabel('Model').selectOption(agyModels[0].id);
    const putRefinery = page.waitForResponse((r) => r.url().includes('/agents/bindings/core.refinery') && r.request().method() === 'PUT');
    await dlg.getByRole('button', { name: 'Lưu' }).click();
    expect((await putRefinery).status()).toBe(200);
    await expect(dlg).toBeHidden();
    await expect(refineryRow.locator('.apm-model-pill')).toContainText(agyModels[0].model_name);
    await expect(refineryRow.getByTestId('binding-blocked-core.refinery')).toHaveCount(0);

    // 2) Gán model agy cho Gen → thành công như trước.
    const genRow = table.locator('tr', { hasText: GEN_LABEL });
    await genRow.locator('.apm-model-pill').click();
    dlg = page.getByRole('dialog', { name: new RegExp(GEN_LABEL) });
    await dlg.getByLabel('Model').selectOption(agyModels[0].id);
    const putGen = page.waitForResponse((r) => r.url().includes('/agents/bindings/core.gen') && r.request().method() === 'PUT');
    await dlg.getByRole('button', { name: 'Lưu' }).click();
    expect((await putGen).status()).toBe(200);
    await expect(dlg).toBeHidden();
    await expect(genRow.locator('.apm-model-pill')).toContainText(agyModels[0].model_name);
  });

  test('F-17: tài khoản chuyển máy (key_changed, không còn phiên) → gợi ý và nút đều là "Đăng nhập lại"', async ({ page }) => {
    const res = await page.request.post('/api/v1/__mock/p3/social/importKeyChanged', { data: { label: 'Facebook chuyển máy' } });
    expect(res.ok()).toBe(true);
    await page.goto('/social');
    const row = page.locator('[data-testid^="social-account-"]', { hasText: 'Facebook chuyển máy' });
    await expect(row).toContainText('Cần đăng nhập lại');
    await expect(row).toContainText('Phiên đã lưu không mở được trên máy này (chuyển máy hoặc đổi khoá) — bấm Đăng nhập lại.');
    await expect(row.getByRole('button', { name: 'Đăng nhập lại' })).toBeVisible();
    await expect(row.getByRole('button', { name: 'Đăng nhập', exact: true })).toHaveCount(0);
  });
});
