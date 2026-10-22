import { autonomyChoice } from '@gen-harness/contracts';

/** v0.1.43 (F-30): viên header hiện nhãn 3 mức ("Soạn sẵn chờ duyệt"…); mức lạ ngoài thang hiện "mức n". */
export function autonomyPillText(level: number): string {
  return autonomyChoice(level)?.label ?? `mức ${level}`;
}

export function autonomyTooltip(level: number): string {
  const choice = autonomyChoice(level);
  if (!choice) return `Mức tự trị chung: mức ${level} (ngoài thang 0–6)`;
  return `Mức tự trị chung: ${choice.label} (mức ${level}/6)`;
}

/** data_confidence may arrive as a 0–1 fraction or a 0–100 percent. */
export function confidencePercent(v: number | null | undefined): number | null {
  if (v === null || v === undefined || Number.isNaN(v)) return null;
  return Math.round(v <= 1 ? v * 100 : v);
}

/** "9+" khi quá 9 — huy hiệu giữ gọn trong nút 32px. */
export function badgeText(n: number): string {
  return n > 9 ? '9+' : String(n);
}

/**
 * Màn nghiệp vụ có bộ lọc trên URL (gọi `useUrlState`) — giữ nút "Góc nhìn đã lưu" ở header. Màn Nâng cao luôn có
 * nút. Màn khác (vd Bảng cơ hội, Cài đặt) vẫn hiện nút khi Sếp đã lưu góc nhìn ở đó từ trước (`savedCount > 0`) để
 * mở/xoá được.
 */
export const SAVED_VIEW_SCREENS: ReadonlySet<string> = new Set([
  'inbox',
  'workbench',
  'tasks',
  'directory',
  'deals',
  'documents',
  'search',
  'people',
  'care',
]);

export function showSavedViews(key: string | null | undefined, advanced: boolean, savedCount = 0): boolean {
  return advanced || (!!key && (SAVED_VIEW_SCREENS.has(key) || savedCount > 0));
}
