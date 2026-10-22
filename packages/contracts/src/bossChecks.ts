/**
 * Hợp đồng v0.1.39 (F-74) — "Việc Sếp cần làm" (`/guide/viec-sep`): các dòng kết nối chạy thật, mỗi lần bấm là một lần
 * kiểm và KẾT QUẢ ĐƯỢC LƯU LẠI ở máy chủ (`ops.boss_checks`) — Claude tự đọc qua `GET /boss-checks`, Sếp không phải
 * chụp màn hình. CHỈ Owner (vai trò khác 403). Lỗi nghiệp vụ vẫn trả 200 với `status: 'fail'` + `error_code`.
 * v0.1.55 (Thiết lập gọn): CHỈ dòng 0 `ai` ("Có ít nhất 1 nguồn AI chạy được") là bắt buộc — mọi dòng kết nối khác tuỳ chọn.
 */
import type { ApiClient } from './client';

/**
 * `agy_login` / `claude_login` do luồng đăng nhập CLI tự ghi — không chạy được bằng `run`. v0.1.47 (F-79):
 * `facebook_reply` (dòng 8, KHÔNG bắt buộc) do máy chủ tự ghi 'pass' khi một lần Gửi trả lời Facebook thật được
 * xác nhận — cũng không có nút chạy. v0.1.50 (F-81): `kho_write` (dòng 9 "Gen ghi Kho", KHÔNG bắt buộc, không có nút chạy) do
 * máy chủ tự ghi 'pass' sau lần ghi Kho THẬT đầu tiên. v0.1.55: `ai_source` (dòng 0 "ai", BẮT BUỘC duy nhất) — Đạt khi có lượt
 * gọi model thật 30 ngày gần đây, hoặc Claude / Google gọi thử đạt, hoặc bấm Kiểm tra (`run('ai_source')`, Owner, không cần PIN).
 */
export type BossCheckKey =
  | 'hub'
  | 'facebook'
  | 'agy_login'
  | 'agy_call'
  | 'agy_switch'
  | 'claude_login'
  | 'claude_call'
  | 'jev'
  | 'telegram'
  | 'remote_access'
  | 'facebook_reply'
  | 'kho_write'
  | 'ai_source';

export type BossCheckStatus = 'pass' | 'fail' | 'pending';

export interface BossCheck {
  key: BossCheckKey;
  status: BossCheckStatus;
  /** Mã lỗi thống nhất (HUB_TOKEN_REJECTED, SOCIAL_NO_SESSION, AGY_ACCOUNT_MISMATCH…) — null khi đạt/đang chạy. */
  error_code: string | null;
  /** Câu tiếng Việt cho Sếp (đã lọc bí mật). */
  message: string | null;
  detail: Record<string, string | number | boolean | null | string[] | Record<string, unknown>>;
  checked_at: string;
  /** Số bản ghi đang giữ của mục này — TÍNH CẢ lượt lỗi. */
  runs: number;
  /** Tài khoản đang dùng (agy/claude) — Owner thấy email đầy đủ, CHỈ trong phản hồi `run` (tải lại: `detail.account_masked`). */
  account?: string | null;
  /**
   * Lỗi TẠM (bận/hạn mức: SOCIAL_BUSY, SOCIAL_RATE_LIMIT, PROBE_RATE_LIMITED, HUB_RATE_LIMITED, CLI_LOGIN_IN_PROGRESS)
   * — máy chủ KHÔNG ghi, kết quả đã lưu giữ nguyên. Web báo cạnh nút, không thay ô kết quả.
   */
  transient?: boolean;
}

export interface BossRow {
  row: number;
  key: string;
  title: string;
  /** Dòng tuỳ chọn — không tính vào `required_total` (v0.1.55: mọi dòng trừ `ai`). */
  optional: boolean;
  checks: BossCheckKey[];
  done: boolean;
}

export interface BossOverview {
  rows: BossRow[];
  /**
   * `facebook_reply` (v0.1.47), `kho_write` (v0.1.50) và `ai_source` (v0.1.55) có thể vắng ở máy chủ cũ — web đọc bằng `resultOf`
   * (null = chưa kiểm). `ai_source` Đạt dựa trên lượt gọi thật thì `runs` = 0 và `detail.via` = 'model_calls' | 'claude_call' | 'agy_call'.
   */
  results: Record<Exclude<BossCheckKey, 'facebook_reply' | 'kho_write' | 'ai_source'>, BossCheck | null> & {
    facebook_reply?: BossCheck | null;
    kho_write?: BossCheck | null;
    ai_source?: BossCheck | null;
  };
  required_done: number;
  /** Số dòng BẮT BUỘC, lấy từ máy chủ (v0.1.55: 1 — chỉ dòng `ai`); web KHÔNG được ghi cứng số này. */
  required_total: number;
}

export interface BossCheckRunBody {
  profile_id?: string;
  account_id?: string;
}

const enc = encodeURIComponent;

/**
 * `GET /boss-checks`, `POST /boss-checks/{key}/run` (423 PIN_REQUIRED cho hub/agy_switch). v0.1.44 (F-8c): dòng 6
 * "telegram"; v0.1.46 (F-21): dòng 7 "remote_access" (quyết theo header Origin); `run('telegram')` trả thêm `host_requested`.
 * v0.1.47 (F-79): dòng 8 "facebook_reply" (không có nút chạy). v0.1.50 (F-81): dòng 9 "kho_write" (không có nút chạy).
 * v0.1.55: dòng 0 "ai" (`ai_source`) là dòng BẮT BUỘC duy nhất (`required_total` = 1); các dòng còn lại tuỳ chọn.
 */
export function bossChecksEndpoints(r: ApiClient['request']) {
  return {
    list: (signal?: AbortSignal) => r<BossOverview>('/boss-checks', { signal }),
    run: (key: BossCheckKey, body?: BossCheckRunBody) => r<BossCheck>(`/boss-checks/${enc(key)}/run`, { method: 'POST', body: body ?? {} }),
  };
}
