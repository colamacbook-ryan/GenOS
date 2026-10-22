/**
 * v0.1.55 (G1) — mô hình thuần của "Chế độ tiêu chuẩn" / "Về mặc định" (không React, test được).

 */
export type {
  DefaultItem,
  DefaultSuggestion,
  DefaultsResetResult,
  DefaultsResponse,
} from '@gen-harness/contracts';

import { AUTONOMY_KEY, CORE_BINDING_KEYS } from '@gen-harness/contracts';
import type { DefaultItem, DefaultSuggestion, DefaultsResponse } from '@gen-harness/contracts';

export const DEFAULT_CHIP = 'Mặc định';
export const CHANGED_CHIP = 'Đã đổi';
export const RESET_LABEL = 'Về mặc định';
export const RESET_ALL_LABEL = 'Về mặc định tất cả';
export const APPLY_STANDARD_LABEL = 'Áp model chuẩn theo vai';
export const STANDARD_MODE_TITLE = 'Chế độ tiêu chuẩn';
/** Neo của dải "Chế độ tiêu chuẩn" trong Bộ não AI — gợi ý `apply_standard` dẫn tới `/system?tab=brain#chuan`. */
export const STANDARD_ANCHOR = 'chuan';

/** Chuỗi an toàn cho JSX: máy chủ cũ/lạ trả object thì bỏ (không bao giờ render object vào JSX). */
export function asText(v: unknown): string {
  return typeof v === 'string' ? v : typeof v === 'number' || typeof v === 'boolean' ? String(v) : '';
}

/** Mục sổ theo khoá; thiếu thì undefined. */
export function findDefault(data: DefaultsResponse | undefined, key: string): DefaultItem | undefined {
  return Array.isArray(data?.items) ? data.items.find((i) => i.key === key) : undefined;
}

/** Số mục "Đã đổi" mà "Về mặc định tất cả" sẽ đưa về mặc định — tin số máy chủ tính, thiếu thì đếm lại từ danh sách. */
export function changedCount(data: DefaultsResponse | undefined): number {
  if (!data) return 0;
  if (typeof data.customized_count === 'number' && Number.isFinite(data.customized_count)) return Math.max(0, data.customized_count);
  return Array.isArray(data.items) ? data.items.filter((i) => i.customized && i.resettable).length : 0;
}

/** "Chế độ tiêu chuẩn: đang dùng" / "Chế độ tiêu chuẩn: đã đổi 3 mục". */
export function standardModeText(n: number): string {
  return n > 0 ? `${STANDARD_MODE_TITLE}: đã đổi ${n} mục` : `${STANDARD_MODE_TITLE}: đang dùng`;
}

/** Gợi ý của máy chủ theo khoá (chỉ nhận các gợi ý có đủ chữ). */
export function suggestionOf(data: DefaultsResponse | undefined, key: DefaultSuggestion['key']): DefaultSuggestion | undefined {
  const list = Array.isArray(data?.suggestions) ? data.suggestions : [];
  return list.find((s) => s.key === key && typeof s.title === 'string' && s.title.trim() !== '');
}

/** Các khoá `binding:*` (dòng gán model) — dùng cho dải ở đầu Bộ não AI. */
export function bindingItems(data: DefaultsResponse | undefined): DefaultItem[] {
  return Array.isArray(data?.items) ? data.items.filter((i) => i.key.startsWith('binding:')) : [];
}

/**
 * Số dòng gán model LÕI (Gen, Bản tin Gen, lọc tin, soạn nháp) Owner đã đổi — đúng số dòng "Áp model chuẩn theo vai" sẽ bỏ
 * (máy chủ chỉ xoá bốn khoá lõi; dòng gán riêng của agent giữ nguyên nên KHÔNG tính vào hộp Xác nhận).
 */
export function customCoreBindingCount(data: DefaultsResponse | undefined): number {
  const core = new Set(CORE_BINDING_KEYS.map((k) => `binding:${k}`));
  return bindingItems(data).filter((i) => i.customized && core.has(i.key)).length;
}

/**
 * Mục "Mức tự trị của tổ chức" khi Sếp đã đổi khỏi mặc định — "Về mặc định tất cả" sẽ đưa nó về mặc định nên hộp Xác nhận
 * phải nói thẳng (đổi mức tự trị là việc liên quan an toàn). Không đổi ⇒ undefined.
 */
export function changedAutonomy(data: DefaultsResponse | undefined): DefaultItem | undefined {
  const it = findDefault(data, AUTONOMY_KEY);
  return it && it.customized && it.resettable ? it : undefined;
}

/** Nội dung hộp Xác nhận của một mục: "Đang dùng: …" và "Mặc định: …" (luôn là chuỗi). */
export function confirmLines(item: Pick<DefaultItem, 'current_text' | 'default_text'> | undefined): { current: string; standard: string } {
  return { current: asText(item?.current_text), standard: asText(item?.default_text) };
}
