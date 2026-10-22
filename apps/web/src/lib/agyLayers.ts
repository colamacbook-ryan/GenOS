/**
 * Antigravity CLI (agy) — MỖI VAI MỘT CHỖ CHẠY (Sếp chốt 16/10/2026; gói G v0.1.62, gói H2 v0.1.63), nói đúng với người dùng:
 * - chạy TRONG hệ thống: Gen của Sếp, agent nội bộ (không gắn kênh / nhóm / người);
 * - chạy QUA HỘP RIÊNG (không có khoá hệ thống): Bản tin (review v0.1.63 P2-3 — có tên hiển thị khách, tiêu đề việc),
 *   soạn lại / dịch nháp tin gửi khách, sàng lọc tin người lạ, agent gắn kênh /
 *   nhóm / người, Gen của nhân viên, lượt thử agent. Hộp chưa chạy / chưa cài ⇒ các việc này dùng nguồn AI kế.
 * Câu lý do của từng dòng do máy chủ trả (`agy_blocked_reason`, một điểm quyết định `gh.providers.agy_policy.decide`);
 * file này chỉ giữ câu phạm vi chung cho thẻ Antigravity CLI và hàm nhận biết model agy.
 */

export const AGY_LAYERS_TEXT =
  'Dùng được cho: Gen của Sếp và agent nội bộ (chạy trong hệ thống); Bản tin, sàng lọc tin người lạ, soạn lại / dịch nháp, agent gắn kênh hoặc nhóm chat và Gen của nhân viên (chạy qua hộp riêng, không có khoá hệ thống — hộp chưa chạy thì dùng nguồn AI kế). Lưu ý: phần chạy trong hệ thống dùng chung máy chủ với khoá hệ thống.';

export const AGY_BACKGROUND_NOTE =
  'Antigravity CLI chạy được Bản tin và sàng lọc tin người lạ (qua hộp riêng), không cần Sếp cho phép. Hộp riêng chưa chạy thì Bản tin, sàng lọc tin và agent trực kênh trả lời người ngoài dùng nguồn khác — cần khoá API (Bản tin không có nguồn vẫn gửi đủ mục, chỉ thiếu đoạn tóm tắt).';

export const AGY_KIND = 'antigravity_cli';

/** Model này thuộc nguồn Antigravity CLI? (`kind` do máy chủ gửi trong `models` của /agents/bindings; máy chủ cũ: thiếu ⇒ false). */
export function isAgyModel(m: { kind?: string | null }): boolean {
  return m.kind === AGY_KIND;
}

/** Lý do (chuỗi) Antigravity không dùng được cho dòng này, hoặc null — không bao giờ object. */
export function agyBlockedReason(slot: { agy_blocked_reason?: string | null }): string | null {
  const r = typeof slot.agy_blocked_reason === 'string' ? slot.agy_blocked_reason.trim() : '';
  return r || null;
}
