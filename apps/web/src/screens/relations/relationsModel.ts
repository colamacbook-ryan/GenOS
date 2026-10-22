/** Presentation helpers dùng chung cho 4 màn của cụm Quan hệ & Đối tượng. */
import type { DirHeatBand, DirPriority, DirRelation, DirValueBand, DocSource, ListenMode } from '@gen-harness/contracts';
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

export function heatTone(h: number | null): string {
  if (h === null) return N5;
  return h >= 80 ? OK : h >= 50 ? WARN : N4;
}

export const RELATION_LABEL: Record<DirRelation, string> = {
  direct: 'Trực tiếp với Sếp',
  via_staff: 'Qua nhân viên',
  stranger: 'Người lạ có tín hiệu',
  staff: 'Nhân sự của Sếp',
};
export const HEAT_FILTER_LABEL: Record<DirHeatBand, string> = { high: '≥ 80', mid: '50–79', cold: 'Lạnh' };
export const VALUE_FILTER_LABEL: Record<DirValueBand, string> = { high: '≥ 500tr', mid: '100–500tr', unknown: 'Chưa rõ' };
export const PRIORITY_LABEL: Record<DirPriority, string> = { P1: 'P1', P2: 'P2', P3: 'P3' };
export function priorityTone(p: DirPriority): string {
  return p === 'P1' ? BAD : p === 'P2' ? WARN : N4;
}

export const GROUP_KIND_LABEL: Record<string, string> = {
  internal: 'Nội bộ', market: 'Thị trường', partner: 'Đối tác', customer: 'Khách', private: 'Riêng tư',
};
export const LISTEN_MODE_LABEL: Record<ListenMode, string> = {
  pending: 'Chờ Sếp chọn',
  off: 'Không lắng nghe',
  // Q6=1 (v0.1.65): chế độ này vẫn lưu mọi tin của nhóm; chỉ việc trả lời mới cần được tag.
  tagged_only: 'Nghe hết, chỉ trả lời khi được tag',
  silent: 'Lắng nghe im lặng',
  proactive: 'Chủ động bắt tín hiệu',
  paused: 'Tạm dừng', // do Sếp tự chọn — không phải "chờ QR" (F066)
};
export function listenModeTone(m: ListenMode): string {
  return m === 'proactive' ? ACC3 : m === 'paused' || m === 'pending' ? WARN : m === 'off' ? N5 : N3;
}

export const CHANNEL_LABEL: Record<string, string> = { zalo: 'Zalo', whatsapp: 'WhatsApp', telegram: 'Telegram', linkedin: 'LinkedIn' };
export function channelIcon(type: string): string {
  return type === 'zalo' ? 'ph ph-chat-circle-dots' : type === 'whatsapp' ? 'ph ph-device-mobile' : type === 'linkedin' ? 'ph ph-linkedin-logo' : 'ph ph-chats-circle';
}
export function channelTone(type: string): string {
  return type === 'zalo' ? OK : type === 'whatsapp' ? WARN : N4;
}
export const CHANNEL_STATE_LABEL: Record<string, string> = {
  active: 'Đang kết nối', pending_qr: 'Chờ quét QR', expired: 'Phiên hết hạn', logged_out: 'Đã đăng xuất',
};

export const SUMMARY_TONE: Record<string, string> = { ok: OK, bad: BAD, neutral: N4 };

const EVENT_TONE: Record<string, string> = {
  Complained: BAD, MentionsCompetitor: BAD, WentSilent: BAD,
  AskedPrice: OK, OfferedSupply: OK, SentQuotation: OK, DealWon: OK,
};
export function eventTone(eventType: string): string {
  return EVENT_TONE[eventType] ?? N4;
}

// ── Sổ tay nhận thức ──
export const SECTION_TONE: Record<string, string> = {
  attention_now: BAD, rolling_context: ACC4, guardrails: WARN, preferences: ACC3, open_threads: N4,
};
export function sectionTone(key: string): string {
  return SECTION_TONE[key] ?? N4;
}
export function sectionIcon(key: string): string {
  switch (key) {
    case 'attention_now':
      return 'ph ph-warning';
    case 'rolling_context':
      return 'ph ph-arrows-clockwise';
    case 'guardrails':
      return 'ph ph-shield-warning';
    case 'preferences':
      return 'ph ph-sliders';
    default:
      return 'ph ph-list-checks';
  }
}

// ── Tài liệu ──
export const DOC_SOURCE_LABEL: Record<DocSource, string> = { channel: 'Từ kênh', agent: 'Agent soạn', tay: 'Tải tay' };
export function docSourceTone(s: DocSource): string {
  return s === 'tay' ? ACC3 : s === 'agent' ? OK : N4;
}
export function docIcon(mime: string): string {
  if (mime.includes('spreadsheet') || mime.includes('csv')) return 'ph ph-file-xls';
  if (mime.includes('pdf')) return 'ph ph-file-pdf';
  if (mime.includes('image')) return 'ph ph-file-image';
  if (mime.includes('word') || mime.includes('document')) return 'ph ph-file-doc';
  return 'ph ph-file-text';
}
export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
