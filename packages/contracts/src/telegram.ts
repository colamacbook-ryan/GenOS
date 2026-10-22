/**
 * Hợp đồng v0.1.44 (F-8c) — Kết nối › Telegram ("Báo động & bản tin"): bot Telegram của Sếp nhận cảnh báo sự cố từ
 * "Trực canh máy chủ" (genh watchdog) và bản tin 07:30/17:30 + nhắc việc từ api. CHỈ Owner. Tin đi MỘT CHIỀU — không
 * có nút/hành động trong Telegram; mọi thao tác Sếp vẫn xác nhận trong Console.
 *
 * Token bot KHÔNG BAO GIỜ có trong phản hồi (chỉ `bot_username` + `chat_id_masked`).
 */
import type { ApiClient } from './client';
import type { BossCheck } from './bossChecks';

/** Kết quả "Gửi thử" gần nhất (api lưu). */
export interface TelegramLastTest {
  status: 'pass' | 'fail';
  error_code: string | null;
  message: string | null;
  checked_at: string;
}

/** Sự cố đang mở do "Trực canh máy chủ" ghi (`run/watchdog-status.json`). */
export interface TelegramIncident {
  key: string;
  severity: string;
  title: string;
  since: string;
}

/** Trạng thái Trực canh máy chủ (genh) — api đọc từ `run/watchdog-status.json` + `run/genh.json`. */
export interface TelegramHostStatus {
  /** genh trên máy chủ có hỗ trợ trực canh (`run/genh.json` "requests" có "watchdog"). */
  supported: boolean;
  /** systemd | cron | launchd | schtasks | null (chưa đặt lịch). */
  schedule: string | null;
  last_run_at: string | null;
  /** ok | issues | paused | skipped_busy | error | null. */
  state: string | null;
  /** ok | not_configured | disabled | failed | key_mismatch | null. */
  telegram: string | null;
  telegram_error_code: string | null;
  incidents: TelegramIncident[];
  test: { at: string; ok: boolean; error_code: string | null } | null;
}

/**
 * v0.1.64 (Telegram 2 chiều): Sếp nhắn Gen qua bot. Chỉ tài khoản đã GHÉP bằng mã 6 số (sống 10 phút) được nhận; qua
 * Telegram Gen chỉ đọc/đề xuất — KHÔNG có nút Duyệt, KHÔNG nhận mã PIN (ghi ra ngoài vẫn Xác nhận + PIN trong Console).
 * Mã ghép không bao giờ có ở đây (chỉ trong phản hồi `pair/start`); `owner_masked` dạng "•••4321".
 */
export interface TelegramInbound {
  /** Công tắc "Nhận tin từ Sếp" (mặc định TẮT với bản cài cũ; chỉ bật được khi đã ghép). */
  enabled: boolean;
  paired: boolean;
  owner_masked: string | null;
  paired_at: string | null;
  /** Có mã ghép còn sống (đã bấm Ghép Telegram, chưa gửi /start <mã>, chưa hết 10 phút). */
  pair_pending: boolean;
  pair_expires_at: string | null;
  /** Lỗi vòng nhận tin lần gần nhất (vd TELEGRAM_POLL_CONFLICT) — null = ổn / chưa chạy. */
  error_code: string | null;
  message: string | null;
}

/** `POST /notify/telegram/pair/start` — cấu hình + mã ghép (mã chỉ hiện MỘT lần, không lưu vào cache). */
export interface TelegramPairStart extends TelegramConfig {
  pair: { code: string; expires_at: string; bot_username: string | null };
}

export interface TelegramConfig {
  configured: boolean;
  enabled: boolean;
  bot_username: string | null;
  /** vd "•••4321" — không bao giờ là chat_id đầy đủ. */
  chat_id_masked: string | null;
  briefing: boolean;
  reminders: boolean;
  updated_at: string | null;
  last_test: TelegramLastTest | null;
  host: TelegramHostStatus;
  /** v0.1.64 — thiếu ở máy chủ cũ hơn. */
  inbound?: TelegramInbound;
}

/**
 * `PUT /notify/telegram` (PIN `notify.change`). Đã cấu hình: bỏ `token`/`chat_id` (hoặc để trống) = giữ giá trị đã lưu;
 * `{}` = "Lưu lại" (máy chủ ghi lại run/telegram.json bằng khoá hiện tại). Chưa cấu hình: cần cả hai.
 */
export interface TelegramSaveBody {
  token?: string;
  chat_id?: string;
  enabled?: boolean;
  briefing?: boolean;
  reminders?: boolean;
}

export interface TelegramChat {
  chat_id: string;
  name: string;
  username: string | null;
}

export interface TelegramFindChatResult {
  chats: TelegramChat[];
  error_code: string | null;
  message: string | null;
}

/** "Gửi thử" — khuôn BossCheck (key "telegram") + đã nhờ Trực canh máy chủ gửi thêm một tin hay chưa. */
export type TelegramTestResult = BossCheck & { host_requested: boolean };

/** Mã lỗi Telegram (máy chủ). TELEGRAM_RATE_LIMITED là lỗi tạm — không ghi vào boss_checks. */
export const TELEGRAM_ERROR_CODES = [
  'TELEGRAM_NOT_CONFIGURED',
  'TELEGRAM_TOKEN_REJECTED',
  'TELEGRAM_CHAT_NOT_FOUND',
  'TELEGRAM_BOT_BLOCKED',
  'TELEGRAM_RATE_LIMITED',
  'TELEGRAM_UNREACHABLE',
  // v0.1.64 (2 chiều)
  'TELEGRAM_NOT_PAIRED',
  'TELEGRAM_POLL_CONFLICT',
  'TELEGRAM_INBOUND_ACTIVE',
] as const;
export type TelegramErrorCode = (typeof TELEGRAM_ERROR_CODES)[number];

/**
 * `GET/PUT/DELETE /notify/telegram`, `POST /notify/telegram/find-chat`, `POST /notify/telegram/test`.
 * v0.1.64: `POST …/pair/start` (PIN) · `PUT …/inbound` {enabled} (PIN; bật cần đã ghép) · `DELETE …/pair` (PIN).
 */
export function telegramEndpoints(r: ApiClient['request']) {
  return {
    getTelegram: (signal?: AbortSignal) => r<TelegramConfig>('/notify/telegram', { signal }),
    saveTelegram: (body: TelegramSaveBody) => r<TelegramConfig>('/notify/telegram', { method: 'PUT', body }),
    deleteTelegram: () => r<TelegramConfig>('/notify/telegram', { method: 'DELETE' }),
    findTelegramChat: (token?: string) =>
      r<TelegramFindChatResult>('/notify/telegram/find-chat', { method: 'POST', body: token ? { token } : {} }),
    testTelegram: () => r<TelegramTestResult>('/notify/telegram/test', { method: 'POST', body: {} }),
    /** `telegram.pair_start` — cấp mã ghép 6 số (sống 10 phút). */
    pairStartTelegram: () => r<TelegramPairStart>('/notify/telegram/pair/start', { method: 'POST', body: {} }),
    /** `telegram.inbound_toggle` — công tắc "Nhận tin từ Sếp". */
    setTelegramInbound: (enabled: boolean) => r<TelegramConfig>('/notify/telegram/inbound', { method: 'PUT', body: { enabled } }),
    /** Gỡ ghép tài khoản Telegram của Sếp (tắt nhận tin, huỷ mã đang chờ). */
    unpairTelegram: () => r<TelegramConfig>('/notify/telegram/pair', { method: 'DELETE' }),
  };
}
