import type { Problem } from './schema';

/** Error thrown for every non-2xx response, carrying the RFC 7807 body. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly problem: Problem;

  constructor(status: number, problem: Partial<Problem> | null, fallbackMessage?: string) {
    const p: Problem = { status, ...(problem ?? {}) } as Problem;
    // v0.1.30: `detail` có thể là đối tượng/mảng (vd `{reasons: […]}`, mảng lỗi kiểm tra của FastAPI) — message
    // LUÔN là chuỗi, không bao giờ mang đối tượng thô (web vẽ message làm React child).
    super(problemText(p.detail) || (typeof p.title === 'string' ? p.title : '') || fallbackMessage || `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.code = p.code ?? codeFromStatus(status);
    this.problem = p;
  }

  /** 422 field errors, `{field: message}`. */
  get fieldErrors(): Record<string, string> {
    return this.problem.errors ?? {};
  }

  /**
   * v0.1.30: lý do kỹ thuật (MODEL_UNAVAILABLE…) — `reasons` cấp ngoài cùng (khuôn mới) hoặc `detail.reasons`
   * (máy chủ ≤ v0.1.29). Chỉ giữ chuỗi.
   */
  get reasons(): string[] {
    const p = this.problem as Problem & { reasons?: unknown };
    const d = p.detail as unknown;
    const raw = Array.isArray(p.reasons) ? p.reasons : d && typeof d === 'object' && !Array.isArray(d) ? (d as { reasons?: unknown }).reasons : null;
    return Array.isArray(raw) ? raw.filter((r): r is string => typeof r === 'string' && r.trim() !== '') : [];
  }

  /**
   * v0.1.44 (F-4b): "Mã yêu cầu" (X-Request-ID) để đối chiếu nhật ký máy chủ — `request_id` trong thân problem+json,
   * hoặc (client gắn vào problem) header `X-Request-ID` khi thân không có/không phải JSON (vd 502 từ proxy).
   */
  get requestId(): string | null {
    const v = (this.problem as { request_id?: unknown }).request_id;
    return typeof v === 'string' && v.trim() ? v.trim() : null;
  }

  /** 401 PIN_INVALID → attempts left before lock. */
  get attemptsLeft(): number | null {
    const v = this.problem.attempts_left;
    return typeof v === 'number' ? v : null;
  }

  /**
   * 423 PIN_LOCKED → ISO time until which PIN entry is locked. v0.1.35: ưu tiên khoá NGOÀI `locked_until`
   * (detail giờ là chuỗi "Thử lại sau …"); detail đối tượng/chuỗi chỉ để tương thích máy chủ cũ.
   */
  get lockedUntil(): string | null {
    const p = this.problem;
    if (typeof p.locked_until === 'string' && p.locked_until) return p.locked_until;
    if (p.detail && typeof p.detail === 'object' && typeof p.detail.locked_until === 'string') {
      return p.detail.locked_until;
    }
    if (typeof p.detail === 'string') {
      const m = /\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:?\d{2})?/.exec(p.detail);
      if (m) return m[0];
    }
    return null;
  }
}

/** Raised when the user closes the PIN dialog instead of entering a PIN. */
export class PinCancelledError extends ApiError {
  constructor() {
    super(423, { code: 'PIN_REQUIRED', title: 'Cần nhập mã PIN để tiếp tục' });
    this.name = 'PinCancelledError';
  }
}

/** Chuỗi đọc được từ `detail` bất kỳ kiểu: chuỗi giữ nguyên; `{message|msg|detail}` chuỗi; mảng lỗi `{msg}` nối lại. */
function problemText(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((x) => (typeof x === 'string' ? x : x && typeof x === 'object' && typeof (x as { msg?: unknown }).msg === 'string' ? (x as { msg: string }).msg : ''))
      .filter(Boolean)
      .join('; ');
  }
  if (detail && typeof detail === 'object') {
    for (const k of ['message', 'msg', 'detail'] as const) {
      const v = (detail as Record<string, unknown>)[k];
      if (typeof v === 'string' && v) return v;
    }
  }
  return '';
}

function codeFromStatus(status: number): string {
  switch (status) {
    case 400:
      return 'BAD_REQUEST';
    case 401:
      return 'UNAUTHENTICATED';
    case 403:
      return 'FORBIDDEN';
    case 404:
      return 'NOT_FOUND';
    case 409:
      return 'CONFLICT';
    case 422:
      return 'VALIDATION_ERROR';
    case 423:
      return 'PIN_REQUIRED';
    case 428:
      return 'SETUP_REQUIRED';
    default:
      return status >= 500 ? 'SERVER_ERROR' : 'HTTP_ERROR';
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}
