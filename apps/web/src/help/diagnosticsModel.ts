import { ApiError, type DiagnosticsState } from '@gen-harness/contracts';
import { REQUEST_UNDELETABLE_CODE, REQUEST_UNDELETABLE_TEXT } from '../lib/genhCodes';

/**
 * v0.1.44 (F-4b) — "Gói chẩn đoán cho người hỗ trợ" (Trợ giúp, chỉ Owner): chữ, trạng thái, kích thước tệp. Hàm thuần.
 */

/** Khoá cache của `GET /system/diagnostics`. */
export const DIAGNOSTICS_KEY = ['system', 'diagnostics'] as const;

/** Đang tạo gói → thăm lại mỗi 3 giây. */
export const DIAG_POLL_MS = 3000;

export const DIAG_FILTERED_TEXT = 'Đã lọc mật khẩu/khoá/token trước khi đóng gói';
export const DIAG_WORKING_TEXT = 'Đang tạo gói chẩn đoán trên máy chủ… thường xong trong 1–2 phút.';
export const DIAG_UNSUPPORTED_TEXT =
  'Công cụ genh trên máy chủ chưa tạo được gói từ Console. Chạy lệnh dưới đây trên máy chủ, rồi gửi tệp .zip nó tạo cho người hỗ trợ:';
export const DIAG_STALE_TEXT =
  'Máy chủ chưa nhận yêu cầu tạo gói (đã hơn 15 phút) — có thể công cụ genh trên máy chủ đang tạm dừng hoặc chưa chạy nền. Bấm Tạo gói chẩn đoán để thử lại, hoặc chạy lệnh dưới đây trên máy chủ rồi gửi tệp .zip nó tạo cho người hỗ trợ:';
/** Đang tạo quá chừng này (tính từ `requested_at`) ⇒ hiện thêm lệnh chạy tay, chưa đợi tới mốc `stale` 15 phút. */
export const DIAG_SLOW_AFTER_MS = 3 * 60_000;
export const DIAG_SLOW_TEXT =
  'Lâu hơn thường lệ — có thể công cụ genh trên máy chủ đang tạm dừng hoặc chưa chạy nền. Không muốn đợi thì chạy lệnh dưới đây trên máy chủ rồi gửi tệp .zip nó tạo cho người hỗ trợ:';

/** Đang tạo (`working`) đã quá `DIAG_SLOW_AFTER_MS` kể từ lúc yêu cầu. */
export function diagSlow(d: Pick<DiagnosticsState, 'requested_at'> | null | undefined, phase: DiagPhase, now: number = Date.now()): boolean {
  if (phase !== 'working' || !d || typeof d.requested_at !== 'string') return false;
  const at = Date.parse(d.requested_at);
  return Number.isFinite(at) && now - at > DIAG_SLOW_AFTER_MS;
}

export const DIAG_FAILED_TEXT = 'Chưa tạo được gói chẩn đoán — bấm Tạo gói chẩn đoán lần nữa; vẫn lỗi thì chạy "genh doctor" trên máy chủ.';

export const DIAG_ERROR_TEXT: Record<string, string> = {
  DIAG_UNSUPPORTED: 'Công cụ genh trên máy chủ chưa hỗ trợ tạo gói từ Console — chạy "genh doctor" trên máy chủ.',
  DIAG_BUSY: 'Máy chủ đang tạo một gói chẩn đoán — đợi gói đó xong rồi tải.',
  DIAG_NOT_READY: 'Gói chẩn đoán chưa có hoặc đã bị dọn — bấm Tạo gói chẩn đoán lần nữa.',
  DIAG_FILE_UNSAFE: 'Tệp gói chẩn đoán trên máy chủ không an toàn để tải — bấm Tạo gói chẩn đoán lần nữa.',
  // v0.1.53 (F-97): genh không xoá được tệp yêu cầu nên không tạo gói — bấm lại vẫn lỗi tới khi sửa quyền thư mục.
  [REQUEST_UNDELETABLE_CODE]: REQUEST_UNDELETABLE_TEXT,
};

/** `stale` — đang chờ/chạy quá 15 phút (máy chủ báo `stale`): thôi thăm lại, cho tạo lại + hiện lệnh chạy tay. */
export type DiagPhase = 'unsupported' | 'idle' | 'working' | 'stale' | 'done' | 'failed';

export function diagPhase(d: (Pick<DiagnosticsState, 'supported' | 'state'> & Partial<Pick<DiagnosticsState, 'stale'>>) | null | undefined): DiagPhase {
  if (!d) return 'idle';
  if (d.supported === false) return 'unsupported';
  switch (d.state) {
    case 'pending':
    case 'running':
      return d.stale === true ? 'stale' : 'working';
    case 'done':
      return 'done';
    case 'failed':
      return 'failed';
    default:
      return 'idle';
  }
}

/** "48 KB", "1,2 MB" — kích thước tệp dễ đọc (null/âm ⇒ chuỗi rỗng). */
export function fmtBytes(n: number | null | undefined): string {
  if (typeof n !== 'number' || !Number.isFinite(n) || n < 0) return '';
  if (n < 1024) return `${n} B`;
  const units = ['KB', 'MB', 'GB'];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  const s = v >= 10 ? String(Math.round(v)) : v.toFixed(1).replace('.', ',').replace(/,0$/, '');
  return `${s} ${units[i]}`;
}

/** Nhãn nút tải: "Tải gói chẩn đoán (48 KB)". */
export function downloadLabel(d: Pick<DiagnosticsState, 'size_bytes'>): string {
  const size = fmtBytes(d.size_bytes);
  return size ? `Tải gói chẩn đoán (${size})` : 'Tải gói chẩn đoán';
}

/** Câu thân thiện khi genh báo tạo gói lỗi (mã lạ ⇒ câu máy chủ đã lọc, thiếu ⇒ câu mặc định). Luôn là chuỗi. */
export function diagFailedText(d: Pick<DiagnosticsState, 'error_code' | 'message'>): string {
  if (d.error_code && DIAG_ERROR_TEXT[d.error_code]) return DIAG_ERROR_TEXT[d.error_code];
  return DIAG_FAILED_TEXT;
}

/** Dòng "Chi tiết kỹ thuật" của một gói: mã lỗi · request_id · lời genh · thời điểm. Chỉ chuỗi. */
export function diagTechDetail(d: Partial<Pick<DiagnosticsState, 'error_code' | 'request_id' | 'message' | 'finished_at' | 'sha256'>>): string {
  const parts: string[] = [];
  if (typeof d.error_code === 'string' && d.error_code) parts.push(`Mã lỗi ${d.error_code}`);
  if (typeof d.request_id === 'string' && d.request_id) parts.push(`request_id ${d.request_id}`);
  if (typeof d.message === 'string' && d.message.trim()) parts.push(d.message.trim());
  if (typeof d.sha256 === 'string' && d.sha256) parts.push(`sha256 ${d.sha256}`);
  if (typeof d.finished_at === 'string' && d.finished_at) parts.push(d.finished_at);
  return parts.join(' · ');
}

/** Câu cho lỗi gọi API của thẻ (409/404 theo mã gói chẩn đoán), null ⇒ dùng errorText chung. */
export function diagApiErrorText(e: unknown): string | null {
  return e instanceof ApiError && DIAG_ERROR_TEXT[e.code] ? DIAG_ERROR_TEXT[e.code] : null;
}

/** Tên tệp lưu về máy (máy chủ đặt tên genh-doctor-YYYYMMDDTHHMMSSZ.zip; chỉ giữ ký tự an toàn). */
export function diagFileName(d: Pick<DiagnosticsState, 'file_name'>): string {
  const n = typeof d.file_name === 'string' ? d.file_name.replace(/[^A-Za-z0-9._-]/g, '') : '';
  return n && n.endsWith('.zip') ? n : 'genh-doctor.zip';
}
