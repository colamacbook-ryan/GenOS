import { ApiError, CLIENT_ERROR_LIMITS, CLIENT_ERRORS_URL, CSRF_COOKIE, CSRF_HEADER, readCookie, type ClientErrorBody } from '@gen-harness/contracts';

/**
 * v0.1.44 (F-4b) — báo lỗi giao diện về máy chủ (`POST /api/v1/client-errors`, không cần đăng nhập) để Claude/người
 * hỗ trợ tra theo "Mã lỗi" (ERR-…) Sếp gửi mà không cần Sếp chép console.
 *
 * - Gọi `fetch` trực tiếp, KHÔNG qua apiClient: 401/428 không được kéo Sếp sang /login hay /setup, không mở hộp PIN.
 * - Khử trùng theo `errorId`, tối đa `CLIENT_ERRORS_PER_MINUTE` lần/phút; thân cắt độ dài.
 * - Không bao giờ ném, không báo lại lỗi của chính nó (không vòng lặp lỗi → báo lỗi → lỗi).
 */

export const CLIENT_ERRORS_PER_MINUTE = 5;
// Theo đúng giới hạn server (`ClientErrorIn`) — vượt là 422, mất cả báo lỗi.
export const MAX_MESSAGE = CLIENT_ERROR_LIMITS.message;
export const MAX_STACK = CLIENT_ERROR_LIMITS.stack;
export const MAX_COMPONENT_STACK = CLIENT_ERROR_LIMITS.component_stack;
export const MAX_PATH = CLIENT_ERROR_LIMITS.path;
const MAX_REMEMBERED = 200;

export interface ClientErrorReport {
  /** Mã hiện cho người dùng (`newErrorId()`), cũng là khoá khử trùng. */
  errorId: string;
  error: unknown;
  componentStack?: string | null;
  /** Mặc định: đường dẫn đang mở (không kèm query — có thể chứa mã/khoá). */
  path?: string;
}

const seen = new Set<string>();
/** Cùng một đối tượng lỗi chỉ báo một lần (React bản dev phát thêm sự kiện window "error" cho lỗi ErrorBoundary đã bắt). */
let seenErrors = new WeakSet<object>();
let sentAt: number[] = [];

/** Chỉ cho test: xoá bộ khử trùng và bộ đếm hạn mức. */
export function resetClientErrorsForTest(): void {
  seen.clear();
  seenErrors = new WeakSet<object>();
  sentAt = [];
}

const cut = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

function textOf(error: unknown): { name?: string; message: string; stack?: string } {
  if (error instanceof Error) {
    return {
      name: typeof error.name === 'string' ? error.name : undefined,
      message: typeof error.message === 'string' ? error.message : '',
      stack: typeof error.stack === 'string' ? error.stack : undefined,
    };
  }
  if (typeof error === 'string') return { message: error };
  try {
    const s = JSON.stringify(error);
    return { message: typeof s === 'string' ? s : String(error) };
  } catch {
    return { message: 'Lỗi không xác định' };
  }
}

/** Phiên bản web lúc build (`VITE_APP_VERSION`), không có thì bỏ trống. */
function appVersion(): string | undefined {
  try {
    const v = (import.meta.env as Record<string, unknown> | undefined)?.VITE_APP_VERSION;
    return typeof v === 'string' && v ? cut(v, CLIENT_ERROR_LIMITS.app_version) : undefined;
  } catch {
    return undefined;
  }
}

function currentPath(): string {
  try {
    return typeof window !== 'undefined' ? window.location.pathname : '/';
  } catch {
    return '/';
  }
}

/** Thân gửi đi — đúng hợp đồng `ClientErrorBody`, đã cắt độ dài. */
export function clientErrorBody(r: ClientErrorReport): ClientErrorBody {
  const t = textOf(r.error);
  const body: ClientErrorBody = {
    error_id: r.errorId,
    message: cut(t.message || t.name || 'Lỗi không xác định', MAX_MESSAGE),
    path: cut(r.path ?? currentPath(), MAX_PATH),
  };
  if (t.name) body.name = cut(t.name, CLIENT_ERROR_LIMITS.name);
  if (t.stack) body.stack = cut(t.stack, MAX_STACK);
  if (typeof r.componentStack === 'string' && r.componentStack) body.component_stack = cut(r.componentStack, MAX_COMPONENT_STACK);
  if (r.error instanceof ApiError && r.error.requestId) body.request_id = r.error.requestId;
  const v = appVersion();
  if (v) body.app_version = v;
  return body;
}

/** Gửi một báo lỗi giao diện (bắn rồi quên). Trả `true` nếu đã gửi, `false` nếu bỏ qua (trùng/hạn mức/lỗi). */
export function reportClientError(r: ClientErrorReport): boolean {
  try {
    if (!r || typeof r.errorId !== 'string' || !r.errorId || seen.has(r.errorId)) return false;
    const obj = r.error !== null && typeof r.error === 'object' ? (r.error as object) : null;
    if (obj && seenErrors.has(obj)) return false;
    const now = Date.now();
    sentAt = sentAt.filter((t) => now - t < 60_000);
    if (sentAt.length >= CLIENT_ERRORS_PER_MINUTE) return false;
    if (seen.size >= MAX_REMEMBERED) seen.clear();
    seen.add(r.errorId);
    if (obj) seenErrors.add(obj);
    sentAt.push(now);
    const headers: Record<string, string> = { 'Content-Type': 'application/json', Accept: 'application/json' };
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) headers[CSRF_HEADER] = csrf;
    const p = globalThis.fetch?.(CLIENT_ERRORS_URL, {
      method: 'POST',
      headers,
      body: JSON.stringify(clientErrorBody(r)),
      credentials: 'include',
      keepalive: true,
    });
    if (p && typeof p.then === 'function') p.then(undefined, () => undefined);
    return true;
  } catch {
    return false;
  }
}
