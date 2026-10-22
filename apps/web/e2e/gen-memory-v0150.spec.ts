import { expect, test } from '@playwright/test';
import { askGen, loginAsOwner, resetMock } from './support';

/**
 * v0.1.50 (F-81, QD-18) — mock, tất định (không có model thật): Gen trả đề xuất "Ghi nhớ" → Xác nhận ghi nhớ (không PIN) →
 * "Xem ở Cài đặt" → Cài đặt › Bộ não AI thấy ghi chú trong thẻ "Gen nhớ" → Sửa tại chỗ → Xoá (có hộp xác nhận) → trạng thái rỗng.
 */

const CARD = 'Ghi nhớ';

test.beforeEach(async ({ page }) => {
  await resetMock(page.request, 'finished');
  await page.setViewportSize({ width: 1440, height: 900 });
  await loginAsOwner(page);
});

test('Gen đề xuất nhớ → Xác nhận → Cài đặt › Bộ não AI thấy ghi chú → Sửa → Xoá', async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto('/overview');
  const card = await askGen(page, 'nhớ giúp em: báo giá luôn ghi rõ VAT 8%', CARD);

  // Thẻ Ghi nhớ: nội dung + lý do, KHÔNG có nhãn PIN, chưa ghi gì.
  await expect(card).toContainText('Báo giá luôn ghi rõ VAT 8% và thời hạn hiệu lực 7 ngày.');
  await expect(card).toContainText('Lý do');
  await expect(card).toContainText('Sếp dặn khi soạn báo giá ván MDF.');
  await expect(card).not.toContainText('Cần mã PIN');
  const before = await page.request.get('/api/v1/gen/memory');
  expect((await before.json()).items).toHaveLength(0);

  // Xác nhận ghi nhớ (không PIN) → "Đã ghi nhớ" + nút dẫn tới Cài đặt.
  await card.getByRole('button', { name: 'Xác nhận ghi nhớ' }).click();
  await expect(card).toContainText('Đã ghi nhớ');
  await expect(page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' })).toHaveCount(0);
  await card.getByRole('button', { name: 'Xem ở Cài đặt' }).click();
  await expect(page).toHaveURL(/\/system\?tab=brain#gen-memory$/);

  // Cài đặt › Bộ não AI: thẻ "Gen nhớ" có đúng ghi chú vừa xác nhận.
  const mem = page.getByRole('region', { name: 'Gen nhớ' });
  await expect(mem).toBeVisible();
  await expect(mem).toContainText('Quy ước, sở thích Sếp đã xác nhận — Gen đọc khi trả lời và khi soạn Bản tin');
  await expect(mem.getByTestId('gen-memory-count')).toHaveText('1/30');
  const rows = mem.getByTestId('gen-memory-row');
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText('Báo giá luôn ghi rõ VAT 8% và thời hạn hiệu lực 7 ngày.');
  await expect(rows.first()).toContainText('Sếp dặn khi soạn báo giá ván MDF.');
  await expect(rows.first()).toContainText('Gen đề xuất');
  await expect(rows.first()).toContainText(/\d{2}\/\d{2}\/\d{4}/);

  // Sửa tại chỗ → nguồn thành "Sếp sửa".
  await rows.first().getByRole('button', { name: 'Sửa' }).click();
  const box = rows.first().getByLabel('Ghi nhớ');
  await box.fill('Báo giá luôn ghi rõ VAT 10%.');
  await expect(rows.first().getByTestId('gen-memory-chars-text')).toHaveText('28/280');
  await rows.first().getByRole('button', { name: 'Lưu' }).click();
  await expect(rows.first()).toContainText('Báo giá luôn ghi rõ VAT 10%.');
  await expect(rows.first()).toContainText('Sếp sửa');
  const saved = (await (await page.request.get('/api/v1/gen/memory')).json()) as { items: Array<{ text: string; source: string }> };
  expect(saved.items.map((i) => [i.text, i.source])).toEqual([['Báo giá luôn ghi rõ VAT 10%.', 'owner']]);

  // Xoá: hộp xác nhận (Huỷ không xoá) → Xoá ghi chú → trạng thái rỗng.
  await rows.first().getByRole('button', { name: 'Xoá' }).click();
  const dlg = page.getByRole('dialog', { name: /Xoá ghi chú này\?/ });
  await dlg.getByRole('button', { name: 'Huỷ' }).click();
  await expect(rows).toHaveCount(1);
  await rows.first().getByRole('button', { name: 'Xoá' }).click();
  await page.getByRole('dialog', { name: /Xoá ghi chú này\?/ }).getByRole('button', { name: 'Xoá ghi chú' }).click();
  await expect(rows).toHaveCount(0);
  await expect(mem).toContainText('Chưa có ghi chú — dặn Gen “nhớ giúp em …” để Gen đề xuất');
  await expect(mem.getByTestId('gen-memory-count')).toHaveText('0/30');
});

test('Gen nhớ: ghi chú trùng bị từ chối bằng câu thân thiện; Huỷ đề xuất thì không ghi gì', async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto('/overview');
  const first = await askGen(page, 'nhớ giúp em: báo giá ghi rõ VAT', CARD);
  await first.getByRole('button', { name: 'Huỷ' }).click();
  await expect(first).toContainText('Đã huỷ — không ghi nhớ');
  expect(((await (await page.request.get('/api/v1/gen/memory')).json()) as { items: unknown[] }).items).toHaveLength(0);

  // Xác nhận một lần, rồi đề xuất y hệt lần nữa → 409 GEN_MEMORY_DUPLICATE ⇒ câu thân thiện + "Chi tiết kỹ thuật".
  const second = await askGen(page, 'nhớ giúp em: báo giá ghi rõ VAT', CARD);
  await second.getByRole('button', { name: 'Xác nhận ghi nhớ' }).click();
  await expect(second).toContainText('Đã ghi nhớ');
  const third = await askGen(page, 'nhớ giúp em: báo giá ghi rõ VAT lần nữa', CARD);
  await third.getByRole('button', { name: 'Xác nhận ghi nhớ' }).click();
  const alert = third.getByRole('alert');
  await expect(alert).toContainText('Ghi chú này đã có trong Gen nhớ — không cần ghi lại.');
  await expect(alert.getByText('Chi tiết kỹ thuật')).toBeVisible();
  expect(((await (await page.request.get('/api/v1/gen/memory')).json()) as { items: unknown[] }).items).toHaveLength(1);
});
