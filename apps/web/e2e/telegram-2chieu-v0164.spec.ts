import { expect, test, type Page } from '@playwright/test';
import { OWNER, loginAsOwner, p3Hook, resetMock } from './support';

/**
 * v0.1.64 (T3) — Kết nối › Telegram › "Nhận tin từ Sếp" (Telegram 2 chiều), mock tất định: Ghép Telegram (PIN) → mã 6 số →
 * (giả lập Sếp gửi /start <mã> cho bot) → "Đã ghép" → tắt/bật nhận tin (PIN) → Gỡ ghép. Telegram chỉ đọc/đề xuất: không nút
 * Duyệt, không ô nhập mã PIN trên thẻ. Không lỗi console. Ảnh 390/1440 lưu ở E2E_SHOTS (nếu đặt).
 */

const SHOTS = process.env.E2E_SHOTS;

async function enterPin(page: Page) {
  const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
  await expect(dlg).toBeVisible();
  await page.getByLabel('Mã PIN — chữ số 1/6').click();
  await page.keyboard.type(OWNER.pin);
  await expect(dlg).toBeHidden();
}

const card = (page: Page) => page.getByRole('region', { name: 'Telegram — báo động & bản tin' });

test.describe('Kết nối › Telegram › Nhận tin từ Sếp (v0.1.64)', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
  });

  for (const [name, width, height] of [['1440', 1440, 900], ['390', 390, 844]] as const) {
    test(`ghép bằng mã 6 số → bật/tắt nhận tin → gỡ ghép (${name}px), không lỗi console`, async ({ page }) => {
      const errors: string[] = [];
      // 423 PIN_REQUIRED là phản hồi bình thường (hộp PIN mở rồi gửi lại) — trình duyệt vẫn ghi một dòng console.
      page.on('console', (m) => m.type() === 'error' && !m.text().includes('status of 423') && errors.push(m.text()));
      page.on('pageerror', (e) => errors.push(String(e)));
      await page.setViewportSize({ width, height });
      await p3Hook(page.request, 'telegram', 'seed', { configured: true });
      await loginAsOwner(page);
      await page.goto('/connections#telegram');
      const tg = card(page);
      const inbound = tg.getByTestId('telegram-inbound');
      await expect(inbound).toBeVisible();
      await expect(inbound.getByRole('switch', { name: 'Nhận tin từ Sếp' })).toHaveAttribute('aria-checked', 'false');
      await expect(inbound.getByTestId('telegram-inbound-status')).toContainText('Chưa ghép tài khoản Telegram của Sếp');
      await expect(tg.getByRole('button', { name: /duyệt/i })).toHaveCount(0);
      if (SHOTS) await tg.screenshot({ path: `${SHOTS}/telegram-chua-ghep-${name}.png` });

      await inbound.getByRole('button', { name: 'Ghép Telegram' }).click();
      await enterPin(page);
      await expect(inbound.getByTestId('telegram-pair-code')).toHaveText('123 456');
      await expect(inbound.getByTestId('telegram-pair')).toContainText('/start 123456');
      if (SHOTS) await tg.screenshot({ path: `${SHOTS}/telegram-ma-ghep-${name}.png` });

      await p3Hook(page.request, 'telegram', 'seed', { pairDone: true }); // Sếp gửi /start 123456 cho bot
      await expect(inbound.getByTestId('telegram-inbound-status')).toContainText('Đã ghép với tài khoản Telegram •••4321', { timeout: 15_000 });
      await expect(inbound.getByTestId('telegram-pair-code')).toHaveCount(0);
      const sw = inbound.getByRole('switch', { name: 'Nhận tin từ Sếp' });
      await expect(sw).toHaveAttribute('aria-checked', 'true');
      if (SHOTS) await tg.screenshot({ path: `${SHOTS}/telegram-da-ghep-${name}.png` });

      await sw.click();
      await enterPin(page).catch(() => undefined); // phiên PIN còn hiệu lực thì không hỏi lại
      await expect(sw).toHaveAttribute('aria-checked', 'false');
      await sw.click();
      await expect(sw).toHaveAttribute('aria-checked', 'true');

      await inbound.getByRole('button', { name: 'Gỡ ghép' }).click();
      const dlg = page.getByRole('dialog', { name: 'Gỡ ghép Telegram?' });
      await dlg.getByRole('button', { name: 'Gỡ ghép' }).click();
      await expect(inbound.getByTestId('telegram-inbound-status')).toContainText('Chưa ghép tài khoản Telegram của Sếp');
      await expect(page.locator('body')).not.toContainText('[object Object]');
      expect(errors, errors.join('\n')).toEqual([]);
    });
  }

  test('lỗi vòng nhận tin → câu thân thiện + Chi tiết kỹ thuật', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await p3Hook(page.request, 'telegram', 'seed', { configured: true, paired: true, inboundError: 'TELEGRAM_POLL_CONFLICT' });
    await loginAsOwner(page);
    await page.goto('/connections#telegram');
    const err = card(page).getByTestId('telegram-inbound-error');
    await expect(err).toContainText('Bot đang bị nơi khác giữ kênh nhận tin');
    await err.getByText('Chi tiết kỹ thuật').click();
    await expect(err).toContainText('TELEGRAM_POLL_CONFLICT');
    await expect(page.locator('body')).not.toContainText('[object Object]');
  });
});
