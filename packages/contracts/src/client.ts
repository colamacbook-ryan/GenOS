import { ApiError, PinCancelledError } from './errors';
import type { Problem } from './schema';

export type HttpMethod = 'GET' | 'HEAD' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface RequestOptions {
  method?: HttpMethod;
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  signal?: AbortSignal;
  /** Sent on writes; generated automatically when omitted so a PIN retry reuses it. */
  idempotencyKey?: string;
  /** Do not open the PIN dialog on 423 (used by the PIN verify call itself). */
  skipPinFlow?: boolean;
  /** Do not redirect to /login on 401 (login form, PIN verify). */
  skipAuthRedirect?: boolean;
  /** Do not redirect to /setup on 428 (the setup wizard itself). */
  skipSetupRedirect?: boolean;
  /** `text` returns the body as a string (CSV export), `blob` as a Blob (file download). Default `json`. */
  responseType?: 'json' | 'text' | 'blob';
}

export interface ApiClientConfig {
  /** Default `/api/v1`. */
  baseUrl?: string;
  fetch?: typeof fetch;
  /** Reads the CSRF cookie. Default: `gh_csrf` from `document.cookie`. */
  getCsrfToken?: () => string | null;
  /** 401 (except INVALID_CREDENTIALS / PIN_INVALID): go to /login. */
  onUnauthenticated?: (error: ApiError) => void;
  /** 428 SETUP_REQUIRED: go to /setup. */
  onSetupRequired?: (error: ApiError) => void;
  /** 403 PASSWORD_CHANGE_REQUIRED (temporary password): go to /change-password. */
  onPasswordChangeRequired?: (error: ApiError) => void;
  /**
   * 423 PIN_REQUIRED: open the PIN dialog. Resolve once a PIN session is
   * established (the request is then retried); reject/`false` to cancel.
   */
  requestPin?: (error: ApiError) => Promise<boolean | void>;
  /** Max PIN prompts per request before giving up. Default 2. */
  maxPinPrompts?: number;
  newIdempotencyKey?: () => string;
}

export const CSRF_COOKIE = 'gh_csrf';
export const CSRF_HEADER = 'X-CSRF-Token';

/** Codes for which a 401 is an expected answer, not a lost session. */
const NON_SESSION_401 = new Set(['INVALID_CREDENTIALS', 'PIN_INVALID']);

export function readCookie(name: string, cookieString?: string): string | null {
  const source = cookieString ?? (typeof document !== 'undefined' ? document.cookie : '');
  for (const part of source.split(';')) {
    const [k, ...rest] = part.trim().split('=');
    if (k === name) return decodeURIComponent(rest.join('='));
  }
  return null;
}

/** v0.1.44 (F-4b): header "Mã yêu cầu" mọi phản hồi của api (nhận cả khuôn client gửi `^[A-Za-z0-9_-]{8,64}$`). */
export const REQUEST_ID_HEADER = 'X-Request-ID';
const REQUEST_ID_RE = /^[A-Za-z0-9_-]{8,64}$/;

/**
 * Gắn header `X-Request-ID` vào problem khi thân không có `request_id` — kể cả thân rỗng/không phải JSON (502 từ
 * proxy). Header sai khuôn thì bỏ qua (không đưa chuỗi lạ lên giao diện).
 */
function withRequestId(problem: Partial<Problem> | null, res: Response): Partial<Problem> | null {
  const current = problem ? (problem as { request_id?: unknown }).request_id : undefined;
  if (typeof current === 'string' && current) return problem;
  let header: string | null = null;
  try {
    header = res.headers?.get?.(REQUEST_ID_HEADER) ?? null;
  } catch {
    header = null;
  }
  const rid = header?.trim();
  if (!rid || !REQUEST_ID_RE.test(rid)) return problem;
  return { ...(problem ?? {}), request_id: rid } as Partial<Problem>;
}

function defaultIdempotencyKey(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (c?.randomUUID) return c.randomUUID();
  return `idem-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export interface ApiClient {
  request<T>(path: string, options?: RequestOptions): Promise<T>;
  readonly config: Readonly<ApiClientConfig>;
}

export function createApiClient(config: ApiClientConfig = {}): ApiClient {
  const baseUrl = (config.baseUrl ?? '/api/v1').replace(/\/$/, '');
  const getCsrf = config.getCsrfToken ?? (() => readCookie(CSRF_COOKIE));
  const maxPinPrompts = config.maxPinPrompts ?? 2;
  const newKey = config.newIdempotencyKey ?? defaultIdempotencyKey;
  // Several requests can hit 423 together: share one dialog.
  let pendingPin: Promise<boolean | void> | null = null;

  const doFetch = (...args: Parameters<typeof fetch>) => (config.fetch ?? globalThis.fetch)(...args);

  function buildUrl(path: string, query?: RequestOptions['query']): string {
    let url = baseUrl + (path.startsWith('/') ? path : '/' + path);
    if (query) {
      const qs = new URLSearchParams();
      for (const [k, v] of Object.entries(query)) {
        if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
      }
      const s = qs.toString();
      if (s) url += (url.includes('?') ? '&' : '?') + s;
    }
    return url;
  }

  async function parseProblem(res: Response): Promise<Partial<Problem> | null> {
    const text = await res.text().catch(() => '');
    if (!text) return null;
    try {
      const body = JSON.parse(text) as unknown;
      return body && typeof body === 'object' ? (body as Partial<Problem>) : null;
    } catch {
      return { detail: text.slice(0, 300) };
    }
  }

  function askPin(err: ApiError): Promise<boolean | void> {
    if (!config.requestPin) return Promise.reject(err);
    if (!pendingPin) {
      pendingPin = config.requestPin(err).finally(() => {
        pendingPin = null;
      });
    }
    return pendingPin;
  }

  async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const method = options.method ?? 'GET';
    const isWrite = method !== 'GET' && method !== 'HEAD';
    const idempotencyKey = isWrite ? (options.idempotencyKey ?? newKey()) : undefined;
    let pinPrompts = 0;

    for (;;) {
      const headers: Record<string, string> = {
        Accept:
          options.responseType === 'text'
            ? 'text/csv, text/plain, */*'
            : options.responseType === 'blob'
              ? 'application/octet-stream, */*'
              : 'application/json',
      };
      if (options.body !== undefined) headers['Content-Type'] = 'application/json';
      if (isWrite) {
        const csrf = getCsrf();
        if (csrf) headers[CSRF_HEADER] = csrf;
        if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;
      }

      let res: Response;
      try {
        res = await doFetch(buildUrl(path, options.query), {
          method,
          headers,
          body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
          credentials: 'include',
          signal: options.signal,
        });
      } catch (e) {
        if ((e as { name?: string })?.name === 'AbortError') throw e;
        throw new ApiError(0, { code: 'NETWORK_ERROR', title: 'Không kết nối được máy chủ' });
      }

      if (res.ok) {
        if (res.status === 204 || method === 'HEAD') return undefined as T;
        if (options.responseType === 'blob') return (await res.blob()) as T;
        const text = await res.text();
        if (options.responseType === 'text') return text as T;
        return (text ? JSON.parse(text) : undefined) as T;
      }

      const err = new ApiError(res.status, withRequestId(await parseProblem(res), res), res.statusText);

      if (res.status === 423 && err.code === 'PIN_REQUIRED' && !options.skipPinFlow && pinPrompts < maxPinPrompts) {
        pinPrompts += 1;
        let ok: boolean | void;
        try {
          ok = await askPin(err);
        } catch (cause) {
          if (cause instanceof ApiError) throw cause;
          throw new PinCancelledError();
        }
        if (ok === false) throw new PinCancelledError();
        continue; // retry the original request with the same Idempotency-Key
      }
      if (res.status === 401 && !options.skipAuthRedirect && !NON_SESSION_401.has(err.code)) {
        config.onUnauthenticated?.(err);
      }
      if (res.status === 403 && err.code === 'PASSWORD_CHANGE_REQUIRED') {
        config.onPasswordChangeRequired?.(err);
      }
      if (res.status === 428 && !options.skipSetupRedirect) {
        config.onSetupRequired?.(err);
      }
      throw err;
    }
  }

  return { request, config };
}
