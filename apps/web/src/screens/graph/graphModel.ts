/** Presentation helpers dùng chung cho 4 chế độ của cụm Bản đồ quan hệ (`graph`). */
import type { GraphHeatBand, GraphState, GraphValueBand } from '@gen-harness/contracts';
// F-38 (v0.1.43): định nghĩa duy nhất ở lib/format.ts — re-export để các màn không phải đổi import.
export { initialsOf, fmtVnd } from '../../lib/format';

export const OK = 'var(--color-ok)';
export const WARN = 'var(--color-warn)';
export const BAD = 'var(--color-bad)';
export const ACC3 = 'var(--color-accent-300)';
export const ACC4 = 'var(--color-accent-400)';
export const N3 = 'var(--color-neutral-300)';
export const N4 = 'var(--color-neutral-400)';
export const N5 = 'var(--color-neutral-500)';
export const N7 = 'var(--color-neutral-700)';

/** Cùng ngưỡng 80/50 dùng ở cả 3 bộ lọc (độ nóng/tiềm năng/rủi ro) và cả 3 chiều điểm. */
export function bandOf(v: number | null): 'high' | 'mid' | 'low' {
  if (v === null) return 'low';
  return v >= 80 ? 'high' : v >= 50 ? 'mid' : 'low';
}
export function heatTone(v: number | null): string {
  const b = bandOf(v);
  return b === 'high' ? OK : b === 'mid' ? WARN : N4;
}
export function riskTone(v: number | null): string {
  const b = bandOf(v);
  return b === 'high' ? BAD : b === 'mid' ? WARN : N4;
}
export function potentialTone(v: number | null): string {
  const b = bandOf(v);
  return b === 'high' ? OK : b === 'mid' ? WARN : N4;
}

export const HEAT_OPTIONS: { value: GraphHeatBand | ''; label: string }[] = [
  { value: '', label: 'Tất cả' },
  { value: 'high', label: '≥ 80' },
  { value: 'mid', label: '50–79' },
  { value: 'cold', label: 'Lạnh' },
];
export const VALUE_OPTIONS: { value: GraphValueBand | ''; label: string }[] = [
  { value: '', label: 'Tất cả' },
  { value: 'high', label: '≥ 80' },
  { value: 'mid', label: '50–79' },
  { value: 'low', label: '< 50' },
];

export const PERSON_TYPE_LABEL: Record<string, string> = {
  customer: 'Khách', partner: 'Đối tác', staff: 'Nhân viên', candidate: 'Ứng viên',
  learner: 'Học viên', supplier: 'Nhà cung cấp', unknown: 'Chưa rõ',
};
export const GROUP_KIND_LABEL: Record<string, string> = {
  internal: 'Nội bộ', market: 'Thị trường', partner: 'Đối tác', customer: 'Khách', private: 'Riêng tư',
};
export const RELATION_LABEL: Record<string, string> = {
  direct: 'Trực tiếp với Sếp', via_staff: 'Qua nhân viên', stranger: 'Người lạ có tín hiệu', staff: 'Nhân sự của Sếp',
};
export const STATE_LABEL: Record<GraphState, string> = { active: 'Đang hoạt động', cold: 'Đang lạnh' };
export function stateTone(s: GraphState): string {
  return s === 'cold' ? N5 : OK;
}

export const CHANNEL_LABEL: Record<string, string> = { zalo: 'Zalo', whatsapp: 'WhatsApp', telegram: 'Telegram', linkedin: 'LinkedIn' };
export function channelIcon(type: string): string {
  return type === 'zalo' ? 'ph ph-chat-circle-dots' : type === 'whatsapp' ? 'ph ph-device-mobile' : type === 'linkedin' ? 'ph ph-linkedin-logo' : 'ph ph-chats-circle';
}
export function channelTone(type: string): string {
  return type === 'zalo' ? OK : type === 'whatsapp' ? WARN : N4;
}

export const TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'Tất cả' },
  ...Object.entries(PERSON_TYPE_LABEL).map(([value, label]) => ({ value, label })),
];
export const CHANNEL_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'Tất cả' },
  ...Object.entries(CHANNEL_LABEL).map(([value, label]) => ({ value, label })),
];
export const RELATION_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'Tất cả' },
  ...Object.entries(RELATION_LABEL).map(([value, label]) => ({ value, label })),
];
export const STATE_OPTIONS: { value: GraphState | ''; label: string }[] = [
  { value: '', label: 'Tất cả' },
  { value: 'active', label: 'Đang hoạt động' },
  { value: 'cold', label: 'Đang lạnh (> 30 ngày)' },
];
export type GraphModeKey = 'list' | 'people' | 'groups' | 'topics';
export const MODE_OPTIONS: { value: GraphModeKey; label: string; icon: string }[] = [
  { value: 'list', label: 'Danh sách', icon: 'ph ph-rows' },
  { value: 'people', label: 'Người ↔ Người', icon: 'ph ph-users-three' },
  { value: 'groups', label: 'Nhóm ↔ Nhóm', icon: 'ph ph-polygon' },
  { value: 'topics', label: 'Luồng chủ đề', icon: 'ph ph-flow-arrow' },
];

/** Trọng số → độ dày cạnh (1.2–4.5px), theo phân vị trong tập cạnh hiện có (không có thang cố định — `interacts`
 * là tổng phút hoạt động chung, `shares_members` là hệ số chồng lấp 0–1, hai đơn vị khác hẳn nhau). */
export function edgeWidth(weight: number, maxWeight: number): number {
  if (maxWeight <= 0) return 1.2;
  return 1.2 + 3.3 * Math.min(1, weight / maxWeight);
}
