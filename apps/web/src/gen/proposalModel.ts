/** Gen v2 (A4) — hàm thuần cho thẻ đề xuất (ProposalCard): nhãn, chuyển giờ `datetime-local`, so trường đã sửa. */
import { GEN_PROPOSAL_EDITABLE, KHO_LABEL, type GenProposal } from '@gen-harness/contracts';
import { khoBangOf, khoDraftRecord, khoInitialDraft, sameRecord } from './khoWriteModel';

export const PROPOSAL_TITLE: Record<GenProposal['type'], string> = {
  draft_message: 'Soạn nháp tin gửi đi',
  reminder: 'Tạo nhắc việc',
  assign: 'Giao người phụ trách',
  social_reply: 'Trả lời bình luận Facebook',
  social_dm: 'Nhắn tin Facebook',
  // v0.1.50 (F-81, QD-18): thẻ Gen nhớ / thẻ ghi Kho dữ liệu (tạo và sửa dùng chung một tiêu đề).
  memory_note: 'Ghi nhớ',
  kho_create: `Ghi vào ${KHO_LABEL}`,
  kho_update: `Ghi vào ${KHO_LABEL}`,
};

/** v0.1.47 (F-79): đề xuất gửi lên Facebook (trả lời bình luận / nhắn tin) — gửi NGAY khi xác nhận, cần PIN. */
export function isSocialWrite(p: GenProposal): p is Extract<GenProposal, { type: 'social_reply' | 'social_dm' }> {
  return p.type === 'social_reply' || p.type === 'social_dm';
}

/** Dòng cảnh báo cố định trên mọi thẻ gửi Facebook. */
export const SOCIAL_WRITE_WARNING = 'Bấm Xác nhận là GỬI NGAY lên Facebook của Sếp (cần mã PIN). Hệ thống không tự thu hồi được.';
export const SOCIAL_SUSPICIOUS_WARNING = 'Mục này có dấu hiệu lừa đảo — đọc kỹ trước khi trả lời.';
/** Thời gian tối đa theo dõi một việc gửi (ms) và nhịp hỏi. */
export const WRITE_POLL_MS = 2000;
export const WRITE_POLL_MAX_MS = 4 * 60 * 1000;
export const WRITE_MAX_TEXT = 2000;

const TERMINAL = new Set(['done', 'failed', 'halted', 'cancelled']);
export const isTerminalJob = (status: string | undefined): boolean => !!status && TERMINAL.has(status);

/** Câu thân thiện cho lỗi khi xác nhận gửi (title lấy từ API nếu có). */
export function writeErrorKind(code: string | undefined): 'halted' | 'locked' | 'limit' | null {
  if (code === 'SOCIAL_HALTED') return 'halted';
  if (code === 'SOCIAL_WRITE_LOCKED') return 'locked';
  if (code === 'SOCIAL_WRITE_LIMIT') return 'limit';
  return null;
}

/** ISO → giá trị ô `datetime-local` theo giờ máy. */
export function toLocalInput(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function fromLocalInput(v: string): string | null {
  if (!v) return null;
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

export type Draft = Record<string, string>;

export function initialDraft(p: GenProposal): Draft {
  if (p.type === 'draft_message') return { title: p.fields.title, text: p.fields.text };
  if (p.type === 'reminder')
    return {
      title: p.fields.title,
      remind_at: toLocalInput(p.fields.remind_at),
      due_at: toLocalInput(p.fields.due_at),
      priority: p.fields.priority,
      assignee_user_id: p.fields.assignee_user_id ?? '',
    };
  if (p.type === 'social_reply' || p.type === 'social_dm') return { text: p.fields.text };
  if (p.type === 'memory_note') return { text: p.fields.text ?? '', reason: p.fields.reason ?? '' };
  if (p.type === 'kho_create' || p.type === 'kho_update') return khoInitialDraft(p);
  return { user_id: p.fields.user_id };
}

/** Chỉ gửi trường được sửa và thật sự đổi (server giữ nguyên phần còn lại). */
export function changedFields(p: GenProposal, d: Draft): Record<string, unknown> {
  // v0.1.50: ghi Kho — bảng / mã bản ghi khoá cứng; chỉ gửi `record` (các trường không rỗng) khi nó khác đề xuất.
  if (p.type === 'kho_create' || p.type === 'kho_update') {
    const bang = khoBangOf(p);
    if (!bang) return {};
    const record = khoDraftRecord(bang, d);
    return sameRecord(record, p.fields.record ?? {}) ? {} : { record };
  }
  const init = initialDraft(p);
  const out: Record<string, unknown> = {};
  for (const k of GEN_PROPOSAL_EDITABLE[p.type]) {
    if (d[k] === undefined || d[k] === init[k]) continue;
    out[k] = k === 'remind_at' || k === 'due_at' ? fromLocalInput(d[k]) : k === 'assignee_user_id' ? d[k] || null : d[k];
  }
  return out;
}
