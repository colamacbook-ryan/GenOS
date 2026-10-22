import { expect, test } from '@playwright/test';
import { apiCall, loginAsOwner, p3Hook, resetMock } from './support';

/**
 * v0.1.41 (F-84, F-86): Chi phí AI hôm nay ở Tổng quan › Sức khoẻ (focus=ai-cost), Bộ não AI › "Nguồn AI cho việc nền"
 * (bật Claude Code CLI: cảnh báo + tích + PIN) và mẫu nhà cung cấp OpenRouter. Mock tính sẵn 12.500 ₫ hôm nay.
 */
test.describe('v0.1.41 — Chi phí AI & nguồn AI cho việc nền', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
  });

  test('Tổng quan có "Chi phí AI hôm nay" với số ₫; ?focus=ai-cost cuộn tới thẻ', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/overview');
    const panel = page.getByRole('region', { name: 'Chi phí AI hôm nay' });
    await expect(panel).toBeVisible();
    await expect(panel.getByTestId('ai-cost-total')).toHaveText('12.500 ₫');
    await expect(panel).toContainText('12.500 ₫ / trần 20.000 ₫');
    await expect(panel).toContainText('3 lượt gọi chưa có giá — nhập giá ở Bộ não AI');
    await expect(panel).toContainText('Hữu ích 7 ngày: 14/17');
    // Bảng theo agent (xếp theo chi phí giảm dần) — CLI trả theo gói 0 ₫, model chưa có giá không cộng tiền.
    const rows = panel.locator('table.ov-ai-cost__table tbody tr');
    await expect(rows).toHaveCount(3);
    await expect(panel.getByTestId('ai-cost-agent-core.gen')).toContainText('Gen — trợ lý quản trị');
    await expect(panel.getByTestId('ai-cost-agent-core.gen')).toContainText('17');
    await expect(panel.getByTestId('ai-cost-agent-core.refinery')).toContainText('Sàng lọc & suy luận chính');
    await expect(panel.getByTestId('ai-cost-agent-duty.decide')).toContainText('0 ₫');
    await expect(panel.getByText('Vượt trần')).toHaveCount(0);
    // Ngay sau "Sức khoẻ hệ thống" trong lưới dưới.
    const order = await page.locator('.ov-bottom-grid section[aria-label], .ov-bottom-grid > section').evaluateAll((els) =>
      els.map((e) => e.getAttribute('aria-label') ?? e.querySelector('.gh-card__title')?.textContent ?? ''),
    );
    expect(order.indexOf('Chi phí AI hôm nay')).toBe(order.indexOf('Sức khoẻ hệ thống') + 1);

    await page.setViewportSize({ width: 1280, height: 600 });
    await page.goto('/overview?focus=ai-cost');
    const box = page.locator('#ai-cost');
    await expect(box).toBeFocused();
    await expect(box).toBeInViewport();
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('Bộ não AI: bật Claude Code CLI cho việc nền ⇒ cảnh báo + tích + PIN ⇒ lưu', async ({ page }) => {
    await loginAsOwner(page);
    await p3Hook(page.request, 'api', 'background', { claude: true });
    const bgState = (await apiCall(page, 'GET', '/providers/background')) as {
      risk_text: string;
      sources: Array<{ provider_id: string; kind: string; reason: string | null }>;
    };
    const claude = bgState.sources.find((x) => x.kind === 'claude_code_cli')!;
    expect(claude.reason).toBeTruthy();
    await page.goto('/system?tab=brain');
    const card = page.getByRole('region', { name: 'Nguồn AI cho việc nền' });
    await expect(card).toContainText('Dùng cho sàng lọc tin, trực việc, Bản tin Gen');
    await expect(card.getByText('Dùng cho việc nền').first()).toBeVisible();
    // Claude Code CLI: "Không dùng" kèm lý do của máy chủ.
    const claudeRow = card.getByTestId(`bg-src-${claude.provider_id}`);
    await expect(claudeRow).toContainText('Không dùng');
    await expect(claudeRow).toContainText(claude.reason!);
    const sw = card.getByRole('switch', { name: 'Cho Claude Code CLI chạy việc nền' });
    await expect(sw).toHaveAttribute('aria-checked', 'false');
    await sw.click();
    const dlg = page.getByRole('dialog', { name: 'Cho Claude Code CLI chạy việc nền' });
    // Cảnh báo NGUYÊN VĂN risk_text của máy chủ.
    await expect(dlg.getByTestId('bg-cli-risk')).toHaveText(bgState.risk_text);
    const allow = dlg.getByRole('button', { name: 'Cho phép' });
    await expect(allow).toBeDisabled();
    await dlg.getByRole('checkbox', { name: 'Tôi đã đọc cảnh báo và tự chịu rủi ro' }).check();
    await expect(allow).toBeEnabled();
    const locked = page.waitForResponse((r) => r.url().endsWith('/api/v1/providers/background') && r.request().method() === 'PUT');
    await allow.click();
    expect((await locked).status()).toBe(423);
    const pin = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await expect(pin).toBeVisible();
    const saved = page.waitForResponse((r) => r.url().endsWith('/api/v1/providers/background') && r.request().method() === 'PUT');
    await page.getByLabel('Mã PIN — chữ số 1/6').click();
    await page.keyboard.type('246810');
    const ok = await saved;
    expect(ok.status()).toBe(200);
    expect(ok.request().postDataJSON()).toEqual({ allow_cli: ['claude_code_cli'], accept_risk: true });
    await expect(pin).toBeHidden();
    await expect(dlg).toBeHidden();
    await expect(sw).toHaveAttribute('aria-checked', 'true');
    await expect(claudeRow).toContainText('Dùng cho việc nền');
    await expect(claudeRow).not.toContainText('Không dùng');
    // Tắt lại không cần cảnh báo, không cần PIN.
    const off = page.waitForResponse((r) => r.url().endsWith('/api/v1/providers/background') && r.request().method() === 'PUT');
    await sw.click();
    const offRes = await off;
    expect(offRes.status()).toBe(200);
    expect(offRes.request().postDataJSON()).toEqual({ allow_cli: [], accept_risk: false });
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(sw).toHaveAttribute('aria-checked', 'false');
    await expect(claudeRow).toContainText('Không dùng');

    // Thẻ chi phí cùng tab: CLI trả theo gói, không sửa.
    const budget = page.getByRole('region', { name: 'Chi phí & trần ngân sách' });
    await expect(budget.getByTestId('ai-price-mc-claude-cli')).toContainText('Trả theo gói — 0 ₫');
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('API & Model: thêm nhà cung cấp từ mẫu OpenRouter', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/api');
    await page.getByRole('button', { name: /Thêm nhà cung cấp/ }).first().click();
    const dlg = page.getByRole('dialog', { name: 'Thêm nhà cung cấp' });
    await dlg.getByLabel('Loại').selectOption('openrouter');
    await expect(dlg.getByLabel('Tên hiển thị')).toHaveValue('OpenRouter');
    await expect(dlg.getByLabel('Địa chỉ gọi (Endpoint)')).toHaveValue('https://openrouter.ai/api/v1');
    await expect(dlg).toContainText('Tạo khoá ở openrouter.ai › Keys rồi dán vào đây');
    await dlg.getByLabel('Khoá API (mỗi dòng một khoá)').fill('sk-mock-or-0001');
    const created = page.waitForRequest((r) => r.url().endsWith('/api/v1/providers') && r.method() === 'POST');
    await dlg.getByRole('button', { name: 'Thêm' }).click();
    const sent = (await created).postDataJSON() as { kind: string; endpoint: string; name: string };
    expect(sent.kind).toBe('openai_compat');
    expect(sent.endpoint).toBe('https://openrouter.ai/api/v1');
    expect(sent.name).toBe('OpenRouter');
    const pin = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await Promise.race([pin.waitFor({ state: 'visible' }), dlg.waitFor({ state: 'hidden' })]);
    if (await pin.isVisible()) {
      await page.getByLabel('Mã PIN — chữ số 1/6').click();
      await page.keyboard.type('246810');
    }
    await expect(dlg).toBeHidden();
    await expect(page.getByText('OpenRouter', { exact: true }).first()).toBeVisible();
  });

  test('Bộ não AI › Chi phí & trần: lưu trần + giá model gọi đúng endpoint; vượt trần ⇒ Tổng quan báo "Vượt trần"', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/system?tab=brain');
    const budget = page.getByRole('region', { name: 'Chi phí & trần ngân sách' });
    await expect(budget.getByLabel('Trần chi phí mỗi ngày (₫)')).toHaveValue(/20[.,]?000/);
    await budget.getByLabel('Trần chi phí mỗi ngày (₫)').fill('10000');
    const putBudget = page.waitForRequest((r) => r.url().endsWith('/api/v1/system/ai-cost/budget') && r.method() === 'PUT');
    await budget.getByRole('button', { name: 'Lưu trần' }).click();
    expect((await putBudget).postDataJSON()).toEqual({ daily_budget_vnd: 10000 });
    await expect(budget).toContainText('Trần hiện tại 10.000 ₫ mỗi ngày.');

    // Giá model chưa có giá (DeepSeek) ⇒ PUT /system/ai-cost/prices/{model_id}.
    const row = budget.getByTestId('ai-price-mc-deepseek');
    await row.getByLabel('Giá token vào của deepseek-reasoner (₫/1M token)').fill('14000');
    await row.getByLabel('Giá token ra của deepseek-reasoner (₫/1M token)').fill('56000');
    const putPrice = page.waitForRequest((r) => r.url().endsWith('/api/v1/system/ai-cost/prices/mc-deepseek') && r.method() === 'PUT');
    await row.getByRole('button', { name: 'Lưu giá deepseek-reasoner' }).click();
    expect((await putPrice).postDataJSON()).toEqual({ in_vnd_per_mtok: 14000, out_vnd_per_mtok: 56000 });
    await expect(row.getByRole('button', { name: 'Lưu giá deepseek-reasoner' })).toBeDisabled();

    await page.goto('/overview');
    const panel = page.getByRole('region', { name: 'Chi phí AI hôm nay' });
    await expect(panel.getByText('Vượt trần')).toBeVisible();
    await expect(panel).toContainText('/ trần 10.000 ₫');
    await expect(panel).not.toContainText('chưa có giá');
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });

  test('lỗi máy chủ ⇒ câu tiếng Việt + "Chi tiết kỹ thuật", không "[object Object]"', async ({ page }) => {
    await loginAsOwner(page);
    const boom = (route: import('@playwright/test').Route) =>
      route.fulfill({
        status: 500,
        contentType: 'application/problem+json',
        body: JSON.stringify({ status: 500, code: 'INTERNAL', title: 'Hệ thống gặp lỗi khi xử lý yêu cầu', error_id: 'e2e-err-1', detail: { trace: ['x'] } }),
      });
    await page.route('**/api/v1/system/ai-cost', boom);
    await page.route('**/api/v1/providers/background', boom);
    await page.goto('/overview');
    const panel = page.getByRole('region', { name: 'Chi phí AI hôm nay' });
    const alert = panel.getByRole('alert');
    await expect(alert).toContainText('Hệ thống gặp lỗi khi xử lý yêu cầu');
    await expect(alert.locator('details.tech-detail summary')).toHaveText('Chi tiết kỹ thuật');
    await alert.getByText('Chi tiết kỹ thuật').click();
    await expect(alert.locator('details.tech-detail code')).toContainText('HTTP 500 · INTERNAL · error_id e2e-err-1');

    await page.goto('/system?tab=brain');
    const card = page.getByRole('region', { name: 'Nguồn AI cho việc nền' });
    await expect(card.getByRole('alert')).toContainText('Hệ thống gặp lỗi khi xử lý yêu cầu');
    await expect(card.getByRole('alert').locator('details.tech-detail summary')).toHaveText('Chi tiết kỹ thuật');
    const budget = page.getByRole('region', { name: 'Chi phí & trần ngân sách' });
    await expect(budget.getByRole('alert').locator('details.tech-detail summary')).toHaveText('Chi tiết kỹ thuật');
    await expect(page.getByText('[object Object]')).toHaveCount(0);
  });
});
