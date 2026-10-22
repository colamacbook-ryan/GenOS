/** Presentation helpers dùng chung cho 4 màn của cụm Cơ hội & Thị trường. */
import type { CaseStatus, DealStatus, OppConfidence, OppStage, SearchBulkAction } from '@gen-harness/contracts';
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

// ── Bảng cơ hội ──
export const STAGE_LABEL: Record<OppStage, string> = {
  raw_signal: 'Tín hiệu thô',
  validated: 'Đã xác thực',
  matched: 'Đã ráp khớp',
  approaching: 'Đang tiếp cận',
  negotiating: 'Đang đàm phán',
  handed_off: 'Đã chuyển nội bộ',
  won: 'Thắng',
  lost: 'Trượt',
  dormant: 'Ngủ đông',
};
export const STAGE_TONE: Record<OppStage, string> = {
  raw_signal: N4,
  validated: ACC3,
  matched: ACC4,
  approaching: WARN,
  negotiating: WARN,
  handed_off: ACC3,
  won: OK,
  lost: BAD,
  dormant: N5,
};

export function heatTone(h: number | null): string {
  if (h === null) return N5;
  return h >= 80 ? OK : h >= 50 ? WARN : N4;
}

export const CONFIDENCE_LABEL: Record<OppConfidence, string> = { high: 'cao', medium: 'trung bình', low: 'thấp' };
export function confidenceTone(c: OppConfidence): string {
  return c === 'high' ? OK : c === 'medium' ? WARN : N4;
}

// ── Cung ↔ Cầu ──
export function scoreTone(score: number): string {
  return score >= 80 ? OK : score >= 60 ? WARN : N4;
}

// ── Kho hội thoại ──
// v0.1.69 (F062): nhãn loại sự kiện dùng MỘT bảng chung của contracts (`eventTypes.ts`), không giữ bảng riêng ở đây.
export { eventTypeLabel } from '@gen-harness/contracts';
export const CHANNEL_LABEL: Record<string, string> = { zalo: 'Zalo', whatsapp: 'WhatsApp', telegram: 'Telegram', linkedin: 'LinkedIn' };
export const BULK_ACTION_LABEL: Record<SearchBulkAction, string> = { tag: 'Gắn nhãn vào sổ tay', task: 'Giao việc theo dõi' };

// ── Deal & Vụ việc ──
export const DEAL_STATUS_LABEL: Record<DealStatus, string> = { open: 'Đang mở', won: 'Đã chốt', lost: 'Đã mất' };
export function dealStatusTone(s: DealStatus): string {
  return s === 'won' ? OK : s === 'lost' ? BAD : ACC3;
}
export const CASE_STATUS_LABEL: Record<CaseStatus, string> = {
  open: 'Mới mở',
  in_progress: 'Đang xử lý',
  resolved: 'Đã giải quyết',
  closed: 'Đã đóng',
};
export function caseStatusTone(s: CaseStatus): string {
  return s === 'resolved' || s === 'closed' ? OK : s === 'in_progress' ? WARN : N4;
}
export const CASE_PRIORITY_LABEL: Record<string, string> = { P1: 'P1', P2: 'P2', P3: 'P3' };
export function priorityTone(p: string): string {
  return p === 'P1' ? BAD : p === 'P2' ? WARN : N4;
}

