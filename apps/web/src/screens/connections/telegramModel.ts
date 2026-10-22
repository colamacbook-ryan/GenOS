import type { TelegramConfig, TelegramHostStatus, TelegramInbound } from '@gen-harness/contracts';
import { friendlyError } from '../../lib/friendlyError';
import { REQUEST_UNDELETABLE_CODE, REQUEST_UNDELETABLE_TEXT } from '../../lib/genhCodes';

/**
 * v0.1.44 (F-8c): Kết nối › Telegram ("Báo động & bản tin") — chữ, câu lỗi theo mã, kiểm định dạng token. Hàm thuần,
 * không gọi API. Câu lỗi dùng lại ở "Việc Sếp cần làm" (bossChecksModel.BOSS_ERROR_TEXT).
 */

/** Khoá cache của `GET /notify/telegram`. */
export const TELEGRAM_KEY = ['notify', 'telegram'] as const;

/** Neo trong trang Kết nối (`/connections#telegram`). */
export const TELEGRAM_GUIDE_PATH = '/connections#telegram';

/** Token BotFather: `<số bot>:<chuỗi 30–64 ký tự>` — kiểm phía web trước khi gửi (máy chủ kiểm lại). */
export const TELEGRAM_TOKEN_RE = /^\d{5,12}:[A-Za-z0-9_-]{30,64}$/;

export const TOKEN_FORMAT_ERROR =
  'Token chưa đúng dạng — token BotFather gửi có dạng 123456789:AAH… (dãy số, dấu hai chấm rồi một chuỗi dài). Chép lại nguyên dòng, không thêm khoảng trắng.';

/** Câu lỗi cho ô token (null = hợp lệ hoặc còn trống). */
export function tokenFormatError(token: string): string | null {
  const t = token.trim();
  if (!t) return null;
  return TELEGRAM_TOKEN_RE.test(t) ? null : TOKEN_FORMAT_ERROR;
}

/** Câu cho Sếp theo mã lỗi Telegram của máy chủ. */
export const TELEGRAM_ERROR_TEXT: Record<string, string> = {
  TELEGRAM_NOT_CONFIGURED: 'Chưa nối Telegram — làm theo hướng dẫn ở Kết nối › Telegram rồi bấm Lưu.',
  TELEGRAM_TOKEN_REJECTED: 'Token sai hoặc bot đã bị xoá — tạo lại bằng BotFather rồi dán token mới.',
  TELEGRAM_CHAT_NOT_FOUND: 'Không tìm thấy chat — mở bot, bấm Bắt đầu (Start) rồi bấm Tìm chat_id lại.',
  TELEGRAM_BOT_BLOCKED: 'Sếp đã chặn bot hoặc chưa bấm Bắt đầu (Start) — mở bot trên Telegram, bấm Bắt đầu (Start).',
  TELEGRAM_RATE_LIMITED: 'Telegram đang giới hạn số tin — đợi một phút rồi bấm Gửi thử lại.',
  TELEGRAM_UNREACHABLE: 'Máy chủ không ra được Internet tới Telegram — kiểm tra mạng.',
  TELEGRAM_KEY_MISMATCH: 'Máy chủ không đọc được cấu hình Telegram — bấm Lưu lại một lần.',
  // v0.1.53 (F-97): Gửi thử từ máy chủ — genh không xoá được tệp yêu cầu nên không gửi; thử lại vẫn lỗi tới khi sửa quyền.
  [REQUEST_UNDELETABLE_CODE]: REQUEST_UNDELETABLE_TEXT,
};

/** v0.1.64 — câu lỗi của phần "Nhận tin từ Sếp" (không đưa vào "Việc Sếp cần làm"). */
export const INBOUND_ERROR_TEXT: Record<string, string> = {
  TELEGRAM_NOT_PAIRED: 'Chưa ghép tài khoản Telegram của Sếp — bấm Ghép Telegram trước rồi mới bật nhận tin.',
  TELEGRAM_POLL_CONFLICT:
    'Bot đang bị nơi khác giữ kênh nhận tin (đã đặt webhook, hoặc một máy khác đang chạy cùng bot) nên Gen chưa nhận được tin của Sếp.',
  TELEGRAM_INBOUND_ACTIVE: 'Đang bật nhận tin từ Sếp nên không tìm chat_id kiểu cũ — dùng nút Ghép Telegram (mã 6 số).',
};

/** Câu thân thiện theo mã (mã lạ → câu máy chủ đã lọc qua friendlyError). Luôn là chuỗi. */
export function telegramErrorText(code: string | null | undefined, message?: string | null): string {
  if (code && TELEGRAM_ERROR_TEXT[code]) return TELEGRAM_ERROR_TEXT[code];
  if (code && INBOUND_ERROR_TEXT[code]) return INBOUND_ERROR_TEXT[code];
  return friendlyError(message ?? null, 'Chưa gửi được — thử lại sau ít phút.').message;
}

/** Dòng "Chi tiết kỹ thuật" của một lượt Gửi thử / Tìm chat_id — chỉ chuỗi. */
export function telegramTechDetail(x: { error_code?: string | null; message?: string | null; checked_at?: string | null }): string {
  const parts: string[] = [];
  if (x.error_code) parts.push(`Mã lỗi ${x.error_code}`);
  if (typeof x.message === 'string' && x.message.trim()) parts.push(x.message.trim());
  if (x.checked_at) parts.push(x.checked_at);
  return parts.join(' · ');
}

/** Sáu bước tạo bot bằng BotFather (đánh số trên giao diện). */
export const BOTFATHER_STEPS: readonly string[] = [
  'Mở Telegram trên điện thoại, tìm @BotFather (có dấu tích xanh) và bấm Bắt đầu (Start).',
  'Gửi lệnh /newbot cho BotFather.',
  'Đặt tên hiển thị cho bot, vd "Gen-Harness của Sếp".',
  'Đặt tên đăng nhập cho bot, phải kết thúc bằng "bot", vd genharness_sep_bot.',
  'BotFather gửi lại token dạng 123456789:AAH… — chép nguyên dòng, dán vào ô Token bên dưới.',
  'Mở bot vừa tạo, bấm Bắt đầu (Start) và gửi một tin bất kỳ, rồi bấm "Tìm chat_id".',
];

/** Dòng phụ dưới tiêu đề thẻ — chỉ kể mục ĐANG bật (không hứa bản tin/nhắc việc Sếp đã tắt). */
export function telegramKicker(t: Pick<TelegramConfig, 'configured' | 'briefing' | 'reminders'> | null | undefined): string {
  if (!t?.configured) return 'Nhận báo động & bản tin qua bot Telegram của Sếp';
  const extra = [t.briefing ? 'bản tin 07:30/17:30' : null, t.reminders ? 'nhắc việc' : null].filter((x): x is string => !!x);
  if (extra.length === 0) return 'Báo động sự cố qua bot Telegram của Sếp';
  if (extra.length === 1) return `Báo động sự cố và ${extra[0]}`;
  return `Báo động sự cố, ${extra[0]} và ${extra[1]}`;
}

/** Câu cảnh báo cố định của thẻ. */
export const TELEGRAM_WARNING =
  'Token là chìa khoá của bot — không gửi cho ai. Gen-Harness lưu mã hoá. Qua Telegram Gen chỉ đọc và đề xuất; mọi thao tác ghi Sếp vẫn xác nhận (kèm mã PIN) trong Console — không bao giờ gõ mã PIN vào Telegram.';

// ─── v0.1.64 — Nhận tin từ Sếp (Telegram 2 chiều) ───────────────────────────────────────────────────────────────

export const INBOUND_TITLE = 'Nhận tin từ Sếp';
export const INBOUND_NOTE =
  'Sếp nhắn bot được các lệnh /hom_nay · /viec · /suc_khoe · /nhap <ý> hoặc hỏi tự do. Gen chỉ đọc và đề xuất — bot chỉ trả lời tài khoản Telegram đã ghép, tin của người khác bị bỏ im lặng. Muốn ghi hay gửi đi, Sếp mở Console bấm Xác nhận và nhập mã PIN.';
export const INBOUND_UNPAIRED_HINT = 'Chưa ghép tài khoản Telegram của Sếp — bấm Ghép Telegram, rồi gửi mã 6 số cho bot.';
export const PAIR_CODE_HINT = 'Mã sống 10 phút và chỉ dùng được một lần. Bot không trả lời nếu mã sai hoặc đã hết hạn — bấm Ghép Telegram để lấy mã mới.';
/** Hỏi lại mỗi 3 giây trong lúc chờ Sếp gửi mã cho bot — thấy "Đã ghép" ngay mà không phải tải lại trang. */
export const PAIR_POLL_MS = 3_000;

/** Câu hướng dẫn gửi mã ghép cho bot. */
export function pairInstruction(bot: string | null | undefined, code: string): string {
  return `Mở bot ${bot ? `@${bot}` : 'trên Telegram'} bằng tài khoản Telegram của Sếp và gửi đúng dòng: /start ${code}`;
}

/** Mã ghép 6 số hiển thị cách quãng "123 456" cho dễ đọc. */
export function spacedCode(code: string): string {
  return /^\d{6}$/.test(code) ? `${code.slice(0, 3)} ${code.slice(3)}` : code;
}

/** `refetchInterval` khi đang chờ ghép (có mã sống): PAIR_POLL_MS; ngược lại false. */
export function pairPollMs(t: Pick<TelegramConfig, 'inbound'> | null | undefined): number | false {
  return t?.inbound?.pair_pending === true ? PAIR_POLL_MS : false;
}

/** Dòng trạng thái ghép dưới công tắc. */
export function inboundStatusText(i: TelegramInbound, fmtTime: (iso: string) => string): string {
  if (!i.paired) return INBOUND_UNPAIRED_HINT;
  const who = `Đã ghép với tài khoản Telegram ${i.owner_masked ?? '•••'}${i.paired_at ? ` · từ ${fmtTime(i.paired_at)}` : ''}`;
  return i.enabled ? `${who} · đang nhận tin` : `${who} · đang TẮT nhận tin`;
}

/** Khối `inbound` của GET /notify/telegram có đúng khuôn (máy chủ cũ không có ⇒ null ⇒ ẩn phần Nhận tin). */
export function asInbound(t: TelegramConfig | null | undefined): TelegramInbound | null {
  const i = t?.inbound;
  if (!i || typeof i !== 'object' || Array.isArray(i)) return null;
  if (typeof i.paired !== 'boolean' || typeof i.enabled !== 'boolean') return null;
  return i;
}

export const TEST_OK_TEXT = 'Đã gửi — kiểm tra Telegram trên điện thoại (sẽ có thêm 1 tin từ trực canh máy chủ trong ~1 phút)';
export const TEST_OK_NO_HOST_TEXT = 'Đã gửi — kiểm tra Telegram trên điện thoại';

/**
 * Câu kết quả Gửi thử ĐẠT: có nhờ Trực canh máy chủ gửi thêm VÀ khối trực canh không đang báo lỗi (key_mismatch,
 * gửi lỗi, genh cũ) ⇒ mới hứa tin thứ hai.
 */
export function testOkText(
  r: { host_requested?: boolean } | null | undefined,
  host?: Pick<TelegramHostStatus, 'supported' | 'telegram' | 'telegram_error_code'> | null,
): string {
  if (r?.host_requested === false || hostWarning(host)) return TEST_OK_NO_HOST_TEXT;
  return TEST_OK_TEXT;
}

/** Chờ tin thử thứ hai của Trực canh máy chủ: hỏi lại mỗi 5 giây, tối đa 2 phút. */
export const HOST_TEST_POLL_MS = 5_000;
export const HOST_TEST_WAIT_MS = 120_000;

/** Mốc bấm Gửi thử (giờ trình duyệt) + `host.test.at` lúc đó (null = chưa có tin thử nào từ máy chủ). */
export interface HostWait {
  since: number;
  prevAt: string | null;
}

/**
 * `refetchInterval` của `GET /notify/telegram` khi đang chờ tin thử từ máy chủ: số ms, hoặc false khi không chờ / kết
 * quả đã đổi so với lúc bấm (so mốc của máy chủ với nhau — không so với giờ trình duyệt) / quá 2 phút.
 */
export function hostPollMs(wait: HostWait | null, at: string | null | undefined, now: number = Date.now()): number | false {
  if (!wait) return false;
  if ((typeof at === 'string' && at ? at : null) !== wait.prevAt) return false;
  if (now - wait.since > HOST_TEST_WAIT_MS) return false;
  return HOST_TEST_POLL_MS;
}

export const FIND_CHAT_EMPTY = 'Chưa thấy tin nào — mở bot, bấm Bắt đầu (Start), gửi một tin rồi bấm Tìm chat_id lần nữa.';

export const HOST_KEY_MISMATCH_TEXT = 'Máy chủ không đọc được cấu hình — bấm Lưu lại một lần.';
export const HOST_UNSUPPORTED_TEXT = 'Cập nhật genh để bật trực canh.';
export const HOST_FAILED_PREFIX = 'Trực canh máy chủ chưa gửi được tin Telegram:';

/** Cảnh báo của khối "Trực canh máy chủ" (null = không có gì để cảnh báo). */
export function hostWarning(host: Pick<TelegramHostStatus, 'supported' | 'telegram' | 'telegram_error_code'> | null | undefined): string | null {
  if (!host) return null;
  if (host.supported === false) return HOST_UNSUPPORTED_TEXT;
  if (host.telegram === 'key_mismatch') return HOST_KEY_MISMATCH_TEXT;
  if (host.telegram === 'failed') return `${HOST_FAILED_PREFIX} ${telegramErrorText(str(host.telegram_error_code))}`;
  return null;
}

/** "Chi tiết kỹ thuật" của cảnh báo trực canh gửi lỗi (mã lỗi genh) — chỉ chuỗi, rỗng khi không có. */
export function hostWarningDetail(host: Pick<TelegramHostStatus, 'telegram' | 'telegram_error_code'> | null | undefined): string {
  if (!host || host.telegram !== 'failed') return '';
  const code = str(host.telegram_error_code);
  return code ? `Mã lỗi ${code} · trực canh máy chủ (genh)` : 'trực canh máy chủ (genh) báo gửi lỗi, không kèm mã';
}

/** Dòng kết quả tin thử thứ hai (do trực canh máy chủ gửi): "Tin thử từ máy chủ: Đạt · 03/10 08:05". null = chưa có. */
export function hostTestText(test: TelegramHostStatus['test'] | null | undefined, fmtTime: (iso: string) => string): string | null {
  if (!test || typeof test !== 'object' || typeof test.ok !== 'boolean') return null;
  const at = str(test.at);
  const when = at ? ` · ${fmtTime(at)}` : '';
  if (test.ok) return `Tin thử từ máy chủ: Đạt${when}`;
  return `Tin thử từ máy chủ: Lỗi · ${telegramErrorText(str(test.error_code))}${when}`;
}

export const SCHEDULE_LABEL: Record<string, string> = {
  systemd: 'systemd (Linux)',
  cron: 'cron',
  launchd: 'launchd (macOS)',
  schtasks: 'Task Scheduler (Windows)',
};

export function scheduleText(s: string | null | undefined): string {
  if (!s) return 'Chưa đặt lịch — chạy "genh update" một lần trên máy chủ';
  return SCHEDULE_LABEL[s] ?? s;
}

export const HOST_STATE_LABEL: Record<string, string> = {
  ok: 'ổn',
  issues: 'có sự cố',
  paused: 'đang tạm dừng (genh stop)',
  skipped_busy: 'bỏ qua lượt vì máy chủ đang bận',
  error: 'lỗi khi chạy',
};

/** Dữ liệu `GET /notify/telegram` có đúng khuôn (phòng phản hồi lạ — không vẽ object). */
export function asTelegramConfig(x: unknown): TelegramConfig | null {
  if (!x || typeof x !== 'object' || Array.isArray(x)) return null;
  const t = x as Partial<TelegramConfig>;
  if (typeof t.configured !== 'boolean') return null;
  return t as TelegramConfig;
}

/** Chỉ chuỗi — tránh vẽ object từ phản hồi lạ. */
export const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);
