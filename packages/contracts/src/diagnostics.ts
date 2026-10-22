/**
 * Hợp đồng v0.1.44 (F-4b) — "Gói chẩn đoán cho người hỗ trợ" (Trợ giúp, CHỈ Owner): Console nhờ genh trên máy chủ
 * chạy `genh doctor` (qua `run/request/doctor.json`), genh đóng gói zip ĐÃ LỌC mật khẩu/khoá/token; Sếp tải về gửi
 * người hỗ trợ. genh cũ (không hỗ trợ) ⇒ `supported=false`, Console hiện lệnh để Sếp chạy tay trên máy chủ.
 *
 * Kèm hợp đồng `POST /client-errors` (web báo lỗi giao diện về máy chủ, không cần đăng nhập).
 */
import type { ApiClient } from './client';

export type DiagnosticsStateName = 'idle' | 'pending' | 'running' | 'done' | 'failed';

export interface DiagnosticsState {
  supported: boolean;
  state: DiagnosticsStateName;
  /** 16 hex — để đối chiếu nhật ký genh/api. */
  request_id: string | null;
  requested_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  file_name: string | null;
  size_bytes: number | null;
  sha256: string | null;
  error_code: string | null;
  message: string | null;
  /** Lệnh chạy tay trên máy chủ ("genh doctor"). */
  command: string;
  /**
   * Đang chờ/chạy nhưng đã quá 15 phút (genh trên máy chủ không nhận yêu cầu: watcher không chạy, thiếu linger,
   * `genh stop`…) ⇒ Console thôi chờ, cho tạo lại và hiện lệnh chạy tay. Máy chủ cũ không có trường này.
   */
  stale?: boolean;
}

/** `GET /system/diagnostics/download` (PIN `diagnostics.download`) — tải bằng điều hướng trình duyệt (cùng gốc). */
export const DIAGNOSTICS_DOWNLOAD_URL = '/api/v1/system/diagnostics/download';

/** `GET /system/diagnostics`, `POST /system/diagnostics` (PIN; 202; 409 DIAG_UNSUPPORTED|DIAG_BUSY). */
export function diagnosticsEndpoints(r: ApiClient['request']) {
  return {
    getDiagnostics: (signal?: AbortSignal) => r<DiagnosticsState>('/system/diagnostics', { signal }),
    requestDiagnostics: () => r<DiagnosticsState>('/system/diagnostics', { method: 'POST', body: {} }),
    diagnosticsDownloadUrl: DIAGNOSTICS_DOWNLOAD_URL,
    /** Tải tệp zip (vài MB) qua apiClient ⇒ 423 PIN tự mở hộp PIN; 404 DIAG_NOT_READY, 409 DIAG_FILE_UNSAFE. */
    downloadDiagnostics: () => r<Blob>('/system/diagnostics/download', { responseType: 'blob' }),
  };
}

/** `POST /client-errors` (không cần đăng nhập, miễn SetupGate) → 202 `{ok, request_id}`; 429 CLIENT_ERRORS_RATE_LIMITED. */
export interface ClientErrorBody {
  /** `ERR-XXXXX-YYYY` — mã hiện cho người dùng. */
  error_id: string;
  message: string;
  name?: string;
  stack?: string;
  component_stack?: string;
  path: string;
  request_id?: string;
  app_version?: string;
}

/**
 * Độ dài tối đa từng trường của `ClientErrorBody` — PHẢI trùng `ClientErrorIn` (apps/api/gh/system_api/client_errors.py,
 * `max_length`); test api `test_client_error_limits_match_contracts` đọc chính khối này để so. Vượt ⇒ server 422 và mất
 * cả báo lỗi.
 */
export const CLIENT_ERROR_LIMITS = {
  message: 1000,
  name: 100,
  stack: 4000,
  component_stack: 4000,
  path: 300,
  app_version: 40,
} as const;

export const CLIENT_ERRORS_URL = '/api/v1/client-errors';
