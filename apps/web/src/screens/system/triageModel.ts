/**
 * v0.1.43 (F-30): mức lọc tin dễ hiểu cho Owner — ba mức ứng với ngưỡng điểm `min_score` của lọc đầu Hộp thư.
 * Ngưỡng khác ba giá trị này (đặt ở "Nâng cao") là "tuỳ chỉnh".
 */
export type TriageLevelKey = 'low' | 'medium' | 'high';

export interface TriageLevel {
  key: TriageLevelKey;
  label: string;
  min_score: number;
  hint: string;
}

export const TRIAGE_LEVELS: readonly TriageLevel[] = [
  { key: 'low', label: 'Thấp', min_score: 15, hint: 'Lọc ít — giữ gần như mọi tin' },
  { key: 'medium', label: 'Vừa', min_score: 30, hint: 'Mặc định' },
  { key: 'high', label: 'Cao', min_score: 50, hint: 'Chỉ giữ tin điểm cao' },
];

export function triageLevelOf(minScore: number): TriageLevelKey | 'custom' {
  return TRIAGE_LEVELS.find((l) => l.min_score === minScore)?.key ?? 'custom';
}

/**
 * v0.1.55 (J2): lọc trước khi trích xuất (`triage.prefilter`) — bỏ qua tin trùng hẳn / rác chắc chắn trước khi gửi
 * model. Máy chủ cũ không trả `prefilter` ⇒ coi như bật (mặc định của máy chủ mới).
 */
export function prefilterOn(settings: { prefilter?: boolean } | null | undefined): boolean {
  return settings?.prefilter !== false;
}
