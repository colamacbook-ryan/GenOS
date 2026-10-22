import { expect, test, type Page } from '@playwright/test';
import { OWNER, loginAs, loginAsOwner, p3Hook, resetMock } from './support';

/**
 * v0.1.44 (F-8c) — Kết nối › Telegram ("Báo động & bản tin"), mock tất định (không gọi Telegram thật): dán token →
 * Tìm chat_id → chọn → Lưu (PIN) → Gửi thử → "Đã gửi"; nhánh TELEGRAM_BOT_BLOCKED chỉ cách bấm Bắt đầu (Start). Token (giả,
 * chỉ cho test) không bao giờ nằm lại trên trang sau khi lưu.
 */

const TOKEN = '123456789:AAFakeTokenForTestOnly_abcdefghijkl';

async function enterPin(page: Page) {
  const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
  await expect(dlg).toBeVisible();
  await page.getByLabel('Mã PIN — chữ số 1/6').click();
  await page.keyboard.type(OWNER.pin);
  await expect(dlg).toBeHidden();
}

const card = (page: Page) => page.getByRole('region', { name: 'Telegram — báo động & bản tin' });

test.describe('Kết nối › Telegram (v0.1.44)', () => {
  test.beforeEach(async ({ page }) => {
    await resetMock(page.request, 'finished');
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  test('dán token → Tìm chat_id → chọn → Lưu (PIN) → Gửi thử → "Đã gửi"; token không còn trên trang', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/connections#telegram');
    const tg = card(page);
    await expect(tg).toBeVisible();
    await expect(tg.locator('.conn-pill')).toHaveText('Chưa nối');
    await expect(tg.getByTestId('botfather-steps').getByRole('listitem')).toHaveCount(6);
    await expect(tg.getByTestId('telegram-warning')).toContainText('Token là chìa khoá của bot — không gửi cho ai.');

    // Token sai dạng → câu lỗi, không gọi API.
    const token = tg.getByLabel('Token', { exact: true });
    await token.fill('12345:ngan');
    let findCalls = 0;
    page.on('request', (r) => {
      if (r.url().includes('/notify/telegram/find-chat')) findCalls += 1;
    });
    await tg.getByRole('button', { name: 'Tìm chat_id' }).click();
    await expect(tg.getByText(/Token chưa đúng dạng/)).toBeVisible();
    expect(findCalls).toBe(0);

    await token.fill(TOKEN);
    await tg.getByRole('button', { name: 'Tìm chat_id' }).click();
    await tg.getByRole('button', { name: 'Nguyễn Văn A (@nva_test)' }).click();
    await expect(tg.getByLabel('chat_id')).toHaveValue('987654321');
    await tg.getByRole('button', { name: 'Lưu', exact: true }).click();
    await enterPin(page);

    await expect(tg.getByTestId('telegram-target')).toContainText('@gen_harness_sep_bot → chat •••4321');
    await expect(tg.locator('.conn-pill')).toHaveText('Cần Sếp xử lý'); // chưa Gửi thử
    expect(await page.content()).not.toContain(TOKEN);

    await tg.getByRole('button', { name: 'Gửi thử' }).click();
    await expect(tg.getByTestId('telegram-test-result')).toContainText('Đã gửi — kiểm tra Telegram trên điện thoại');
    await expect(tg.locator('.conn-pill')).toHaveText('Đang chạy');
    await expect(tg.getByTestId('telegram-host')).toContainText('systemd');

    // Kết quả Gửi thử cũng là dòng 6 của "Việc Sếp cần làm".
    await page.goto('/guide/viec-sep');
    await expect(page.getByRole('region', { name: 'Telegram (báo động & bản tin)', exact: true })).toContainText('Xong');
  });

  test('TELEGRAM_BOT_BLOCKED → hướng dẫn mở bot, bấm Bắt đầu (Start) (+ Chi tiết kỹ thuật); Tắt Telegram cần xác nhận', async ({ page }) => {
    await p3Hook(page.request, 'telegram', 'seed', { configured: true, testError: 'TELEGRAM_BOT_BLOCKED' });
    await loginAsOwner(page);
    await page.goto('/connections#telegram');
    const tg = card(page);
    await tg.getByRole('button', { name: 'Gửi thử' }).click();
    const res = tg.getByTestId('telegram-test-result');
    await expect(res).toContainText('Sếp đã chặn bot hoặc chưa bấm Bắt đầu (Start) — mở bot trên Telegram, bấm Bắt đầu (Start)');
    await res.getByText('Chi tiết kỹ thuật').click();
    await expect(res).toContainText('Mã lỗi TELEGRAM_BOT_BLOCKED');
    await expect(tg.locator('.conn-pill')).toHaveText('Cần Sếp xử lý');
    await expect(page.locator('body')).not.toContainText('[object Object]');

    await tg.getByRole('button', { name: 'Tắt Telegram' }).click();
    const dlg = page.getByRole('dialog', { name: 'Tắt Telegram?' });
    await expect(dlg).toBeVisible();
    await dlg.getByRole('button', { name: 'Tắt Telegram' }).click();
    await enterPin(page);
    await expect(tg.getByTestId('botfather-steps')).toBeVisible();
    await expect(tg.locator('.conn-pill')).toHaveText('Chưa nối');
  });

  test('Trực canh máy chủ: key_mismatch → "Lưu lại" một lần bấm (PIN) → cảnh báo biến mất, giữ chat cũ', async ({ page }) => {
    await p3Hook(page.request, 'telegram', 'seed', { configured: true, host: 'key_mismatch' });
    await loginAsOwner(page);
    await page.goto('/connections#telegram');
    const tg = card(page);
    await expect(tg.getByTestId('telegram-host-warning')).toContainText('Máy chủ không đọc được cấu hình — bấm Lưu lại một lần');
    await expect(tg.locator('.conn-pill')).toHaveText('Cần Sếp xử lý');
    await tg.getByRole('button', { name: 'Lưu lại' }).click();
    await enterPin(page);
    await expect(tg.getByTestId('telegram-host-warning')).toHaveCount(0);
    await expect(tg.getByTestId('telegram-target')).toContainText('chat •••4321');
    await expect(tg.getByLabel(/Token mới/)).toHaveCount(0);
  });

  test('Đổi token/chat_id: Tìm chat_id không có tin → hướng dẫn; bỏ trống chat_id, chỉ tắt bản tin → Lưu giữ chat cũ', async ({ page }) => {
    await p3Hook(page.request, 'telegram', 'seed', { configured: true, findChats: 'none' });
    await loginAsOwner(page);
    await page.goto('/connections#telegram');
    const tg = card(page);
    await tg.getByRole('button', { name: 'Đổi token/chat_id' }).click();
    await tg.getByRole('button', { name: 'Tìm chat_id' }).click();
    await expect(tg.getByTestId('telegram-find-empty')).toContainText('Chưa thấy tin nào — mở bot, bấm Bắt đầu (Start), gửi một tin rồi bấm Tìm chat_id lần nữa.');
    await expect(tg.getByLabel('chat_id mới (bỏ trống để giữ •••4321)')).toHaveValue('');
    await tg.getByRole('switch', { name: 'Gửi bản tin 07:30/17:30' }).click();
    await tg.getByRole('button', { name: 'Lưu', exact: true }).click();
    await enterPin(page);
    await expect(tg.getByTestId('telegram-target')).toContainText('chat •••4321');
    await expect(tg.getByTestId('telegram-target')).not.toContainText('bản tin');
  });

  test('Trực canh máy chủ gửi lỗi (failed) → câu theo mã + Chi tiết kỹ thuật; Gửi thử không hứa tin thứ hai', async ({ page }) => {
    await p3Hook(page.request, 'telegram', 'seed', { configured: true, host: 'failed' });
    await loginAsOwner(page);
    await page.goto('/connections#telegram');
    const tg = card(page);
    const warn = tg.getByTestId('telegram-host-warning');
    await expect(warn).toContainText('Trực canh máy chủ chưa gửi được tin Telegram: Sếp đã chặn bot hoặc chưa bấm Bắt đầu (Start)');
    await warn.getByText('Chi tiết kỹ thuật').click();
    await expect(warn).toContainText('Mã lỗi TELEGRAM_BOT_BLOCKED');
    await tg.getByRole('button', { name: 'Gửi thử' }).click();
    await expect(tg.getByTestId('telegram-test-result')).toHaveText(/Đã gửi — kiểm tra Telegram trên điện thoại$/);
    await expect(tg.getByTestId('telegram-host-test')).toContainText('Tin thử từ máy chủ: Lỗi · Sếp đã chặn bot');
    await expect(page.locator('body')).not.toContainText('[object Object]');
  });

  test('Gửi thử Đạt rồi Lưu token mới → "Cần Sếp xử lý", không còn "Gửi thử gần nhất", dòng 6 về Chưa kiểm; Tắt Telegram cũng vậy', async ({ page }) => {
    await p3Hook(page.request, 'telegram', 'seed', { configured: true });
    await loginAsOwner(page);
    await page.goto('/connections#telegram');
    const tg = card(page);
    await tg.getByRole('button', { name: 'Gửi thử' }).click();
    await expect(tg.locator('.conn-pill')).toHaveText('Đang chạy');
    const row6 = () => page.getByRole('region', { name: 'Telegram (báo động & bản tin)', exact: true });

    await tg.getByRole('button', { name: 'Đổi token/chat_id' }).click();
    await tg.getByLabel(/Token mới/).fill('987654321:BBOtherFakeTokenOnly_zyxwvutsrqponm');
    await tg.getByRole('button', { name: 'Lưu', exact: true }).click();
    await enterPin(page);
    await expect(tg.locator('.conn-pill')).toHaveText('Cần Sếp xử lý');
    await expect(tg.getByTestId('telegram-last-test')).toHaveCount(0);
    await expect(tg).toContainText('Chưa Gửi thử lần nào');
    await page.goto('/guide/viec-sep');
    await expect(row6()).toContainText('Chưa kiểm');
    await expect(row6()).not.toContainText('Xong');

    await page.goto('/connections#telegram');
    await tg.getByRole('button', { name: 'Gửi thử' }).click();
    await expect(tg.locator('.conn-pill')).toHaveText('Đang chạy');
    await tg.getByRole('button', { name: 'Tắt Telegram' }).click();
    await page.getByRole('dialog', { name: 'Tắt Telegram?' }).getByRole('button', { name: 'Tắt Telegram' }).click();
    await expect(tg.locator('.conn-pill')).toHaveText('Chưa nối');
    await page.goto('/guide/viec-sep');
    await expect(row6()).toContainText('Chưa kiểm');
  });

  test('vai trò khác Owner không thấy thẻ Telegram', async ({ page }) => {
    await loginAs(page, 'auditor@genesis.local');
    await page.goto('/connections');
    await expect(page.getByRole('region', { name: 'Bộ não AI' })).toBeVisible();
    await expect(card(page)).toHaveCount(0);
  });
});
