import { join } from 'node:path';
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { loginAsOwner, mockHook, resetMock, resultsDir, settle } from './support';

/** v0.1.65 (F1c) + QD 17/10 — nhóm mới MẶC ĐỊNH vào Lắng nghe im lặng (khối «N nhóm mới đã vào Lắng nghe im lặng», hiện ngay khi
 *  bridge báo nhóm mới, «Đã biết»); hộp "Nhóm mới" chỉ hiện khi Sếp đặt «Hỏi Sếp» (Lắng nghe im lặng / Không nghe); nút Cập nhật
 *  danh sách nhóm. Chụp 390px và 1440px; không tràn ngang trang. */
const shots = join(resultsDir, 'visual');

async function noHorizontalScroll(page: Page) {
  const d = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(d).toBeLessThanOrEqual(0);
}

for (const [label, size] of [['390px', { width: 390, height: 844 }], ['1440px', { width: 1440, height: 900 }]] as const) {
  test(`hộp "Nhóm mới" trên Kết nối — ${label}`, async ({ page }) => {
    await page.setViewportSize(size);
    await resetMock(page.request as APIRequestContext, 'finished');
    await loginAsOwner(page);
    const errors: string[] = [];
    page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
    await page.goto('/connections');
    const zalo = page.getByRole('article', { name: 'Kênh Zalo' });
    await expect(zalo).toBeVisible();
    await expect(zalo.getByTestId('newgroups-zalo')).toHaveCount(0);          // chưa có nhóm chờ ⇒ không hộp
    await expect(zalo.getByRole('button', { name: 'Cập nhật danh sách nhóm' })).toBeVisible();

    await expect(zalo.getByTestId('newsilent-zalo')).toHaveCount(0);

    await mockHook(page.request, 'discover', { type: 'zalo' });                // bridge thấy nhóm mới ⇒ mặc định Lắng nghe im lặng
    const notice = zalo.getByTestId('newsilent-zalo');
    await expect(notice).toContainText('3 nhóm mới đã vào Lắng nghe im lặng');
    await expect(notice).toContainText('không bao giờ trả lời (kể cả khi được tag)');
    await expect(zalo.getByTestId('newgroups-zalo')).toHaveCount(0);           // không hỏi
    await settle(page);
    await zalo.scrollIntoViewIfNeeded();
    await zalo.screenshot({ path: join(shots, `danh-ba-v0165-silent-${label}.png`) });
    await noHorizontalScroll(page);
    await notice.getByRole('button', { name: 'Đã biết' }).click();
    await expect(zalo.getByTestId('newsilent-zalo')).toHaveCount(0);

    await mockHook(page.request, 'discover', { type: 'zalo', ask: true });     // Sếp đặt «Nhóm mới: Hỏi Sếp» ⇒ hộp hỏi
    const box = zalo.getByTestId('newgroups-zalo');
    await expect(box).toBeVisible();
    await expect(box).toContainText('Nhóm mới — chọn Lắng nghe im lặng hay Không nghe (3)');
    await expect(box).toContainText('Khách sỉ Hà Nội — nhóm mới');
    await settle(page);
    await zalo.scrollIntoViewIfNeeded();
    await zalo.screenshot({ path: join(shots, `danh-ba-v0165-${label}.png`) });
    await noHorizontalScroll(page);

    await box.getByRole('button', { name: 'Lắng nghe im lặng — nhóm Khách sỉ Hà Nội — nhóm mới' }).click();
    await expect(box).toContainText('(2)');
    await box.getByRole('button', { name: /Tất cả 2 nhóm: Không nghe/ }).click();
    await expect(zalo.getByTestId('newgroups-zalo')).toHaveCount(0);           // chọn xong hết ⇒ hộp biến mất

    await zalo.getByRole('button', { name: 'Cập nhật danh sách nhóm' }).click();
    await expect(page.getByText(/Đang cập nhật danh sách nhóm Zalo/)).toBeVisible();
    expect(errors.filter((e) => !/favicon|Failed to load resource/.test(e))).toEqual([]);
  });
}

test('bridge tắt: Cập nhật danh sách nhóm báo lỗi thân thiện + Chi tiết kỹ thuật', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await resetMock(page.request as APIRequestContext, 'finished');
  await loginAsOwner(page);
  await mockHook(page.request, 'bridge', { online: false });
  await page.goto('/connections');
  const zalo = page.getByRole('article', { name: 'Kênh Zalo' });
  await zalo.getByRole('button', { name: 'Cập nhật danh sách nhóm' }).click();
  const alert = zalo.getByRole('alert').filter({ hasText: 'Bộ nhận tin' });
  await expect(alert).toBeVisible();
  await expect(alert).toContainText('Chi tiết kỹ thuật');
  await mockHook(page.request, 'bridge', { online: true });
});
