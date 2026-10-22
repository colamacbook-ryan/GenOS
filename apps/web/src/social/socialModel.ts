import type { BrowserJob, SocialAccount, SocialLiveInput, SocialWriteAction, SocialWriteItem } from '@gen-harness/contracts';
import type { Tone } from '@gen-harness/ui';

/** Khoá bộ đệm dùng chung — sự kiện WS `social.update` làm mới cả nhóm. */
export const SOCIAL_KEY = ['social'] as const;
export const qkSocial = {
  status: ['social', 'status'] as const,
  platforms: ['social', 'platforms'] as const,
  accounts: ['social', 'accounts'] as const,
  latest: (id: string) => ['social', 'latest', id] as const,
  writeGate: ['social', 'write-gate'] as const,
  writes: ['social', 'writes'] as const,
  job: (id: string) => ['social', 'job', id] as const,
};

/** Route trang cảnh báo rủi ro gửi Facebook (v0.1.47). */
export const WRITE_RISK_PATH = '/social/ghi-facebook';
export const WRITE_LIMIT_MIN = 1;
export const WRITE_LIMIT_MAX = 20;

export interface WriteStatusView {
  label: string;
  /** Nhãn ngắn cho chip ở danh sách "Lần gửi gần đây" (cùng nguồn với `label`). */
  chip: string;
  tone: Tone;
  /** Chữ phụ (vd cảnh báo "chưa thấy hiện trên trang"). */
  notes: string[];
  terminal: boolean;
}

type WriteLike = Pick<BrowserJob, 'status'> & {
  confirmed?: boolean | null;
  after_halt?: boolean | null;
  after_cancel?: boolean | null;
  send_error?: boolean | null;
  has_proof?: boolean | null;
  /** Mã lỗi (vd WORKER_TIMEOUT) + câu giải thích của API. */
  error?: string | null;
  error_text?: string | null;
  /** Có = việc đã chạy trên trình duyệt: huỷ/dừng/quá giờ lúc đó thì tin CÓ THỂ đã đi. */
  started_at?: string | null;
  /** 'PROOF_MISSING' = gửi xong mà không chụp được ảnh (KHÁC ảnh đã xoá theo hạn lưu — khi đó has_proof cũng false). */
  proof_error?: string | null;
  result?: BrowserJob['result'];
};

export const PROOF_MISSING_TEXT = 'Đã gửi nhưng không chụp được ảnh bằng chứng — mở Facebook để kiểm tra.';
export const SEND_ERROR_TEXT = 'Có lỗi ngay sau khi bấm gửi — tin có thể đã đi. Mở Facebook kiểm tra trước khi gửi lại.';
export const RETRY_HINT = 'Hỏi Gen soạn lại nếu muốn gửi lần nữa.';
/** Huỷ/dừng khi việc ĐANG chạy: API vẫn nhận 'done' đến muộn (tin kịp đi) — không khẳng định "chưa gửi gì". */
export const MAYBE_SENT_NOTE = 'Nếu tin kịp đi trước khi dừng, mục này sẽ tự chuyển sang "Đã gửi". Mở Facebook kiểm tra trước khi gửi lại.';

/** Việc gửi đã có kết luận PROOF_MISSING (không suy từ has_proof: ảnh quá hạn lưu bị xoá cũng làm has_proof=false). */
export function proofMissing(j: Pick<WriteLike, 'proof_error' | 'result'>): boolean {
  return (j.proof_error ?? j.result?.proof_error) === 'PROOF_MISSING';
}

/**
 * Lỗi mà gửi lại là AN TOÀN (chắc chắn chưa gửi gì). WORKER_TIMEOUT sau khi đã chạy: trình duyệt có thể chết SAU khi bấm
 * Enter — bảo "soạn lại / thử lại" là khiến Sếp gửi hai lần.
 */
export function writeOutcomeUnknown(j: Pick<WriteLike, 'status' | 'error' | 'started_at'>): boolean {
  return j.status === 'failed' && j.error === 'WORKER_TIMEOUT' && !!j.started_at;
}

/** Trạng thái việc gửi → chữ tiếng Việt (một nguồn cho thẻ đề xuất và danh sách "Lần gửi gần đây"). */
export function writeStatusView(j: WriteLike): WriteStatusView {
  const confirmed = j.confirmed ?? j.result?.confirmed;
  const afterHalt = j.after_halt || j.result?.after_halt;
  const afterCancel = j.after_cancel || j.result?.after_cancel;
  const sendError = j.send_error || j.result?.send_error;
  const wasRunning = !!j.started_at;
  switch (j.status) {
    case 'queued':
      return { label: 'Đang chờ trình duyệt…', chip: 'Đang chờ', tone: 'neutral', notes: [], terminal: false };
    case 'running':
      return { label: 'Đang gửi trên Facebook…', chip: 'Đang gửi', tone: 'accent', notes: [], terminal: false };
    case 'done': {
      const notes: string[] = [];
      if (afterHalt) notes.push('Đã gửi trước khi kịp dừng');
      if (afterCancel) notes.push('Đã gửi trước khi kịp huỷ / tạm dừng');
      if (sendError) notes.push(SEND_ERROR_TEXT);
      else if (confirmed === false) notes.push(j.has_proof !== false ? 'Đã bấm gửi nhưng chưa thấy hiện trên trang — xem ảnh chụp' : 'Đã bấm gửi nhưng chưa thấy hiện trên trang');
      if (proofMissing(j)) notes.push(PROOF_MISSING_TEXT);
      return { label: 'Đã gửi', chip: 'Đã gửi', tone: confirmed === false || sendError ? 'warn' : 'ok', notes, terminal: true };
    }
    case 'halted':
      return wasRunning
        ? { label: 'Đã dừng bằng Dừng tất cả', chip: 'Đã dừng', tone: 'warn', notes: [MAYBE_SENT_NOTE], terminal: true }
        : { label: 'Đã dừng bằng Dừng tất cả — chưa gửi gì', chip: 'Đã dừng', tone: 'warn', notes: [], terminal: true };
    case 'cancelled':
      return wasRunning
        ? { label: 'Đã huỷ', chip: 'Đã huỷ', tone: 'warn', notes: [MAYBE_SENT_NOTE], terminal: true }
        : { label: 'Đã huỷ — chưa gửi gì', chip: 'Đã huỷ', tone: 'neutral', notes: [], terminal: true };
    default:
      if (writeOutcomeUnknown(j)) {
        // error_text của API đã nói rõ "mở Facebook kiểm tra trước khi gửi lại" — KHÔNG thêm gợi ý soạn lại.
        return { label: 'Không rõ tin đã đi hay chưa', chip: 'Không rõ', tone: 'warn', notes: [], terminal: true };
      }
      // Câu lỗi của API đã có "Hỏi Gen …" (PERMIT_INVALID / TARGET_NOT_FOUND) → không lặp lại.
      return { label: 'Gửi không thành công', chip: 'Lỗi', tone: 'bad', notes: j.error_text?.includes('Hỏi Gen') ? [] : [RETRY_HINT], terminal: true };
  }
}

export function writeActionLabel(a: SocialWriteAction | null | undefined): string {
  return a === 'send_message' ? 'Nhắn tin' : 'Trả lời bình luận';
}

/** Đích gửi hiển thị gọn: bỏ giao thức, cắt dài. */
export function shortTarget(url: string | null | undefined, max = 48): string {
  if (!url) return '(đã xoá theo hạn lưu)';
  const t = url.replace(/^https?:\/\/(www\.)?/, '');
  return t.length > max ? `${t.slice(0, max - 1)}…` : t;
}

export function sortWrites(items: SocialWriteItem[]): SocialWriteItem[] {
  return [...items].sort((a, b) => b.created_at.localeCompare(a.created_at));
}

/** "HH:mm dd/MM/yyyy" theo múi giờ tổ chức. */
export function fmtConsentTime(iso: string, tz = 'Asia/Ho_Chi_Minh'): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const out: Record<string, string> = {};
  for (const p of new Intl.DateTimeFormat('en-GB', { timeZone: tz, hourCycle: 'h23', hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit', year: 'numeric' }).formatToParts(d)) out[p.type] = p.value;
  return `${out.hour}:${out.minute} ${out.day}/${out.month}/${out.year}`;
}

export interface StatusView {
  label: string;
  tone: Tone;
  hint: string;
}

/**
 * Nhãn nút đăng nhập (review F-17): tài khoản `needs_login` (phiên không mở được sau chuyển máy/đổi khoá ⇒ máy chủ xoá
 * phiên, `has_session=false`) vẫn là "Đăng nhập lại" — khớp gợi ý ở dòng trạng thái, chuông và hướng dẫn.
 */
export function loginLabel(a: Pick<SocialAccount, 'status' | 'has_session'>): 'Đăng nhập lại' | 'Đăng nhập' {
  return a.has_session || a.status === 'needs_login' ? 'Đăng nhập lại' : 'Đăng nhập';
}

/** Trạng thái tài khoản → nhãn tiếng Việt + việc Owner cần làm (một nguồn cho thẻ, test, e2e). */
export function accountStatus(a: Pick<SocialAccount, 'status' | 'pause_reason' | 'active_job'>): StatusView {
  if (a.active_job) {
    const what = a.active_job.kind === 'login' ? 'Đang mở cửa sổ đăng nhập' : a.active_job.kind === 'read' ? 'Đang đọc' : a.active_job.kind === 'write' ? 'Đang gửi' : 'Đang kiểm phiên';
    return { label: what, tone: 'accent', hint: 'Mỗi tài khoản chỉ chạy một việc một lúc.' };
  }
  switch (a.status) {
    case 'active':
      return { label: 'Đang kết nối', tone: 'ok', hint: 'Gen đọc được khi Sếp hỏi hoặc theo lịch.' };
    case 'pending_login':
      return { label: 'Chưa đăng nhập', tone: 'neutral', hint: 'Bấm Đăng nhập — Sếp tự đăng nhập trong cửa sổ trình duyệt.' };
    case 'needs_login':
      // F-17 (v0.1.38): phiên đã lưu không mở được sau khi chuyển máy/đổi khoá.
      if (a.pause_reason === 'key_changed') {
        return {
          label: 'Cần đăng nhập lại',
          tone: 'warn',
          hint: 'Phiên đã lưu không mở được trên máy này (chuyển máy hoặc đổi khoá) — bấm Đăng nhập lại.',
        };
      }
      return { label: 'Cần đăng nhập lại', tone: 'warn', hint: 'Phiên đã hết hoặc bị đăng xuất — bấm Đăng nhập lại.' };
    case 'paused':
      return a.pause_reason === 'checkpoint' || a.pause_reason === 'captcha'
        ? {
            label: a.pause_reason === 'captcha' ? 'Dừng: CAPTCHA' : 'Dừng: cần xác minh',
            tone: 'bad',
            hint: 'Nền tảng đòi xác minh. Hệ thống không tự giải — bấm Đăng nhập lại để Sếp tự xử lý, rồi Tiếp tục.',
          }
        : a.pause_reason === 'errors'
          ? { label: 'Tạm dừng (lỗi)', tone: 'warn', hint: '3 lần lỗi liên tiếp — kiểm tra rồi bấm Tiếp tục.' }
          : { label: 'Tạm dừng', tone: 'neutral', hint: 'Sếp đã tạm dừng — bấm Tiếp tục khi muốn dùng lại.' };
    default:
      return { label: 'Đã gỡ', tone: 'neutral', hint: '' };
  }
}

const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;

/** "08:00, 17:00" → ['08:00','17:00']; lỗi → thông báo tiếng Việt (khớp kiểm của API). */
export function parseTimes(text: string): { times: string[]; error: string | null } {
  const parts = text
    .split(/[,;\s]+/)
    .map((t) => t.trim())
    .filter(Boolean)
    .map((t) => (/^\d:\d\d$/.test(t) ? `0${t}` : t));
  if (parts.length > 4) return { times: [], error: 'Tối đa 4 mốc giờ mỗi ngày' };
  for (const t of parts) {
    if (!TIME_RE.test(t)) return { times: [], error: `"${t}" chưa đúng dạng HH:MM (vd 08:00)` };
    const h = Number(t.slice(0, 2));
    if (h >= 23 || h < 6) return { times: [], error: 'Không đặt lịch trong giờ nghỉ 23:00–06:00' };
  }
  return { times: [...new Set(parts)].sort(), error: null };
}

/** Phím đặc biệt gửi tới trình duyệt từ xa (máy chủ lọc lại theo đúng danh sách này). */
const SPECIAL_KEYS = new Set([
  'Enter', 'Tab', 'Backspace', 'Delete', 'Escape', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End',
  'PageUp', 'PageDown',
]);

/** Sự kiện bàn phím của trình duyệt → sự kiện gửi đi (null = bỏ qua, vd phím tắt của chính trình duyệt Sếp). */
export function keyToInput(e: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'altKey'>): SocialLiveInput | null {
  if (e.ctrlKey || e.metaKey || e.altKey) return null;
  if (e.key === ' ') return { type: 'text', text: ' ' };
  if (SPECIAL_KEYS.has(e.key)) return { type: 'key', action: 'press', key: e.key };
  if ([...e.key].length === 1) return { type: 'text', text: e.key };
  return null;
}

/** Toạ độ con trỏ trên khung hiển thị → toạ độ trang từ xa (khung hình có thể bị thu nhỏ). */
export function mapPoint(
  clientX: number,
  clientY: number,
  rect: Pick<DOMRect, 'left' | 'top' | 'width' | 'height'>,
  remote: { w: number; h: number },
): { x: number; y: number } {
  const x = Math.round(((clientX - rect.left) / Math.max(1, rect.width)) * remote.w);
  const y = Math.round(((clientY - rect.top) / Math.max(1, rect.height)) * remote.h);
  return { x: Math.max(0, Math.min(remote.w, x)), y: Math.max(0, Math.min(remote.h, y)) };
}

export function liveUrl(ticket: string, loc: Pick<Location, 'protocol' | 'host'> = window.location): string {
  return `${loc.protocol === 'https:' ? 'wss' : 'ws'}://${loc.host}/api/v1/social/login/${encodeURIComponent(ticket)}`;
}
