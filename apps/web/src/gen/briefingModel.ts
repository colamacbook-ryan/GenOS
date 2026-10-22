import type { GenStep } from '@gen-harness/contracts';

/**
 * v0.1.49 (F-8, QD-16): vị trí chèn thẻ mục Gen-hub (Lịch hôm nay / Mail cần trả lời / Việc Google đang mở) giữa các bước
 * của Bản tin — ngay sau "Sự cố cần Sếp", TRƯỚC Facebook/Kho và các lời nhắc ("tick thêm quyền", "Dán khoá…") cùng nút.
 *
 * - Máy chủ gửi `content.hub_at` (chỉ số trong `steps`) ⇒ dùng đúng chỉ số đó (kẹp trong [0, số bước]).
 * - Bản tin cũ / mock không có `hub_at` ⇒ chèn trước cặp "lời nhắc + nút" đầu tiên (bước `suggest` đầu tiên và câu
 *   `say` ngay trước nó); không có nút nào ⇒ cuối danh sách (như trước).
 */
export function hubCardsAt(steps: readonly GenStep[], hubAt: unknown): number {
  if (typeof hubAt === 'number' && Number.isInteger(hubAt)) return Math.min(Math.max(hubAt, 0), steps.length);
  const firstSuggest = steps.findIndex((s) => s.kind === 'suggest');
  if (firstSuggest < 0) return steps.length;
  return firstSuggest > 0 && steps[firstSuggest - 1]?.kind === 'say' ? firstSuggest - 1 : firstSuggest;
}
