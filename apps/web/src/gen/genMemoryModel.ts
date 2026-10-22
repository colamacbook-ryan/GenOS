/**
 * v0.1.50 (F-81, QD-18) — "Gen nhớ" (Cài đặt › Bộ não AI): hàm thuần cho thẻ GenMemoryCard — chữ cố định, nguồn ghi chú,
 * ngày dd/mm/yyyy, bộ đếm ký tự, câu lỗi theo mã. Không chứa bí mật; giá trị từ máy chủ luôn được ép về chuỗi.
 */
import { ApiError, type GenMemoryList, type GenMemoryNote } from '@gen-harness/contracts';
import { DEFAULT_TZ } from '../lib/format';
import { errorText } from '../lib/errorText';

/** Khoá cache của `GET /gen/memory`. */
export const GEN_MEMORY_KEY = ['gen', 'memory'] as const;

export const MEMORY_TITLE = 'Gen nhớ';
export const MEMORY_DESC = 'Quy ước, sở thích Sếp đã xác nhận — Gen đọc khi trả lời và khi soạn Bản tin';
export const MEMORY_EMPTY = 'Chưa có ghi chú — dặn Gen “nhớ giúp em …” để Gen đề xuất';
/** Mốc dự phòng khi máy chủ cũ chưa gửi `limit` / `max_len` / `reason_max`. */
export const MEMORY_LIMIT = 30;
export const MEMORY_MAX_LEN = 280;
export const MEMORY_REASON_MAX = 200;

export interface MemoryLimits {
  limit: number;
  maxLen: number;
  reasonMax: number;
}

const posInt = (v: unknown, fallback: number): number => (typeof v === 'number' && Number.isFinite(v) && v > 0 ? Math.floor(v) : fallback);

export function memoryLimits(data: Pick<GenMemoryList, 'limit' | 'max_len' | 'reason_max'> | undefined): MemoryLimits {
  return { limit: posInt(data?.limit, MEMORY_LIMIT), maxLen: posInt(data?.max_len, MEMORY_MAX_LEN), reasonMax: posInt(data?.reason_max, MEMORY_REASON_MAX) };
}

/** "n/30". */
export const countText = (n: number, limit: number): string => `${n}/${limit}`;

/** Bộ đếm ký tự của ô nhập: "12/280". */
export const charCount = (value: string, max: number): string => `${[...value].length}/${max}`;

/** `source` 'gen' ⇒ "Gen đề xuất"; 'owner' ⇒ "Sếp sửa"; giá trị lạ ⇒ "Gen đề xuất" (máy chủ cũ hơn web). */
export function sourceLabel(source: unknown): 'Gen đề xuất' | 'Sếp sửa' {
  return source === 'owner' ? 'Sếp sửa' : 'Gen đề xuất';
}

/** ISO → "dd/mm/yyyy" theo múi giờ tổ chức; sai định dạng ⇒ "—". */
export function fmtNoteDate(iso: unknown, tz: string = DEFAULT_TZ): string {
  if (typeof iso !== 'string' || !iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  let fmt: Intl.DateTimeFormat;
  try {
    fmt = new Intl.DateTimeFormat('en-GB', { timeZone: tz, day: '2-digit', month: '2-digit', year: 'numeric' });
  } catch {
    fmt = new Intl.DateTimeFormat('en-GB', { timeZone: DEFAULT_TZ, day: '2-digit', month: '2-digit', year: 'numeric' });
  }
  const p: Record<string, string> = {};
  for (const part of fmt.formatToParts(d)) p[part.type] = part.value;
  return `${p.day}/${p.month}/${p.year}`;
}

/** Ghi chú an toàn để vẽ: mọi trường ép về chuỗi (lý do rỗng ⇒ null). */
export interface MemoryItemView {
  id: string;
  text: string;
  reason: string | null;
  source: 'Gen đề xuất' | 'Sếp sửa';
  date: string;
}

export function memoryItems(data: GenMemoryList | undefined, tz: string = DEFAULT_TZ): MemoryItemView[] {
  const items: unknown = data?.items;
  if (!Array.isArray(items)) return [];
  return (items as GenMemoryNote[])
    .filter((n) => n && typeof n.id === 'string')
    .map((n) => ({
      id: n.id,
      text: typeof n.text === 'string' ? n.text : '',
      reason: typeof n.reason === 'string' && n.reason.trim() ? n.reason : null,
      source: sourceLabel(n.source),
      date: fmtNoteDate(n.updated_at ?? n.created_at, tz),
    }));
}

const MEMORY_ERROR: Record<string, string> = {
  GEN_MEMORY_FULL: 'Gen nhớ đã đủ 30 ghi chú — xoá bớt một ghi chú rồi thử lại.',
  GEN_MEMORY_DUPLICATE: 'Ghi chú này trùng với một ghi chú đã có trong Gen nhớ.',
};

/** Câu thân thiện cho lỗi sửa / xoá ghi chú (luôn là chuỗi). */
export function memoryErrorText(e: unknown, tz: string = DEFAULT_TZ): string {
  if (e instanceof ApiError) {
    if (MEMORY_ERROR[e.code]) return MEMORY_ERROR[e.code];
    if (e.status === 404) return 'Ghi chú này không còn nữa (có thể đã bị xoá) — đã tải lại danh sách.';
    if (e.status === 403) return 'Chỉ Sếp (Owner) dùng được Gen nhớ.';
  }
  return errorText(e, tz);
}
