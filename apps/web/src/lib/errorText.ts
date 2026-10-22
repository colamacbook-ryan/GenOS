import { ApiError, PinCancelledError } from '@gen-harness/contracts';
import { MODEL_DOWN_TEXT, MODEL_UNAVAILABLE_TEXT, detailToText } from './friendlyError';
import { DEFAULT_TZ, fmtDMClock } from './format';

const INTERNAL_FALLBACK_TITLE = 'Hệ thống gặp lỗi khi xử lý yêu cầu — đã ghi nhật ký';
const PIN_LOCKED_FALLBACK_TITLE = 'Mã PIN đang bị khoá do nhập sai nhiều lần';

/** v0.1.30: lỗi "không model nào chạy được" (503 MODEL_UNAVAILABLE, kể cả máy chủ cũ trả `detail={reasons}`). */
export function isModelUnavailable(e: unknown): boolean {
  return e instanceof ApiError && e.code === 'MODEL_UNAVAILABLE';
}

/**
 * v0.1.67 (F011): 503 MODEL_UNAVAILABLE có `model_down: true` = đã có model nhưng nguồn đang lỗi / hết hạn mức — khác với
 * "chưa có model nào". Máy chủ cũ không gửi cờ ⇒ coi như chưa có model (câu cũ).
 */
export function isModelDown(e: unknown): boolean {
  return e instanceof ApiError && e.code === 'MODEL_UNAVAILABLE' && (e.problem as { model_down?: unknown }).model_down === true;
}

function titleOf(e: ApiError, fallback: string): string {
  const t = e.problem.title;
  return typeof t === 'string' && t.trim() ? t.trim() : fallback;
}

const FORBIDDEN_TEXT = 'Vai trò của bạn không có quyền làm thao tác này.';
const CSRF_TEXT = 'Phiên đã cũ — tải lại trang rồi thử lại.';

/**
 * v0.1.62 (F110): 403 phân theo mã — CSRF_INVALID là phiên lệch (tải lại trang), không phải thiếu quyền; chỉ FORBIDDEN
 * (hoặc 403 không mã) mới dùng câu về vai trò. Mã khác (GEN_DISABLED, PASSWORD_CHANGE_REQUIRED…) đã kèm câu tiếng Việt
 * riêng của máy chủ ⇒ hiện câu đó; không có câu thì quay về câu quyền.
 */
function forbiddenText(e: ApiError): string {
  if (e.code === 'CSRF_INVALID') return CSRF_TEXT;
  if (e.code === 'FORBIDDEN') return FORBIDDEN_TEXT;
  return detailToText(e.problem.detail).trim() || titleOf(e, FORBIDDEN_TEXT);
}

/**
 * User-facing message for an API error — luôn là chuỗi.
 * `tz`: múi giờ tổ chức (useOrgTimezone) để hiện giờ mở khoá PIN theo giờ địa phương.
 */
export function errorText(e: unknown, tz: string = DEFAULT_TZ): string {
  if (e instanceof PinCancelledError) return 'Đã huỷ — thao tác cần mã PIN.';
  if (e instanceof ApiError) {
    if (e.status === 0) return 'Không kết nối được máy chủ. Kiểm tra máy chủ rồi thử lại.';
    if (e.status === 403) return forbiddenText(e);
    if (isModelUnavailable(e)) return isModelDown(e) ? MODEL_DOWN_TEXT : MODEL_UNAVAILABLE_TEXT;
    // v0.1.35: 500 INTERNAL — câu dễ hiểu (title) KÈM mã lỗi; `detail` chỉ có mã nên không được thay chỗ title.
    // v0.1.44 (F-4b): kèm "Mã yêu cầu" (X-Request-ID) cạnh mã lỗi để người hỗ trợ đối chiếu nhật ký.
    if (e.code === 'INTERNAL') {
      const id = (e.problem as { error_id?: unknown }).error_id;
      const title = titleOf(e, INTERNAL_FALLBACK_TITLE);
      const codes = [typeof id === 'string' && id ? `Mã lỗi ${id}` : '', e.requestId ? `Mã yêu cầu ${e.requestId}` : ''].filter(Boolean);
      if (!codes.length) return title;
      return `${title}. ${codes.join(' · ')} — gửi ${codes.length > 1 ? 'các mã' : 'mã'} này cho người hỗ trợ.`;
    }
    // v0.1.35: PIN_LOCKED — nói rõ PIN bị khoá, giờ mở khoá theo múi giờ tổ chức (không phải ISO UTC thô).
    if (e.code === 'PIN_LOCKED') {
      const until = e.lockedUntil;
      const when = until && !Number.isNaN(Date.parse(until)) ? fmtDMClock(until, tz) : null;
      return `${titleOf(e, PIN_LOCKED_FALLBACK_TITLE)}. ${when ? `Thử lại sau ${when}.` : 'Thử lại sau ít phút.'}`;
    }
    if (e.status === 422) {
      const errs = Object.values(e.fieldErrors).map(detailToText).filter(Boolean);
      if (errs.length) return errs.join(' ');
    }
    return detailToText(e.message) || 'Có lỗi không xác định.';
  }
  if (e instanceof Error) return detailToText(e.message) || 'Có lỗi không xác định.';
  return detailToText(e) || 'Có lỗi không xác định.';
}

/** Lý do kỹ thuật kèm theo (hiện trong "Chi tiết kỹ thuật"), null nếu không có. */
export function errorReasons(e: unknown): string | null {
  if (e instanceof ApiError && e.reasons.length) return e.reasons.join('; ');
  return null;
}

/**
 * v0.1.41: dòng kỹ thuật cho "Chi tiết kỹ thuật" dưới câu lỗi thân thiện — mã HTTP, mã lỗi, mã nhật ký, mã yêu cầu
 * (v0.1.44, X-Request-ID), lý do. Luôn là
 * chuỗi (hoặc null khi không có gì để thêm, vd Sếp tự huỷ PIN); không bao giờ mang đối tượng thô.
 */
export function errorDetail(e: unknown): string | null {
  if (e instanceof PinCancelledError) return null;
  if (e instanceof ApiError) {
    const parts = [e.status === 0 ? 'Không có phản hồi HTTP' : `HTTP ${e.status}`];
    if (typeof e.code === 'string' && e.code) parts.push(e.code);
    const id = (e.problem as { error_id?: unknown }).error_id;
    if (typeof id === 'string' && id) parts.push(`error_id ${id}`);
    if (e.requestId) parts.push(`request_id ${e.requestId}`);
    if (e.reasons.length) parts.push(e.reasons.join('; '));
    return parts.join(' · ');
  }
  if (e instanceof Error) {
    const m = detailToText(e.message);
    return m ? `${e.name}: ${m}` : e.name || null;
  }
  return null;
}
