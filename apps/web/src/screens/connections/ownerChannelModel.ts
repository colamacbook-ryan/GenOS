/**
 * v0.1.72 — Kết nối › «Kênh quản trị» (Zalo/WhatsApp chạy bằng tài khoản riêng của bot): phần logic thuần (không React).
 * Bot CHỈ nhắn Sếp khi có việc đặc biệt (QĐ Sếp 18/10): không có báo cáo định kỳ, không có bản tóm tắt/gộp — cài đặt chỉ
 * gồm mức tối thiểu, giờ yên lặng, số tin/giờ. Mọi câu lỗi là CHUỖI (không bao giờ đưa object vào JSX).
 */
import {
  ApiError,
  type AccountKind,
  type AutoSendToday,
  type OwnerChannelView,
  type OwnerReportPrefs,
  type ReportSeverity,
} from '@gen-harness/contracts';
import { errorText } from '../../lib/errorText';

export const OWNER_CHANNEL_TITLE = 'Kênh quản trị';
export const ownerChannelKey = (ch: string) => ['owner-channel', ch] as const;
export const AUTO_SENDS_KEY = ['auto-sends'] as const;
export const GROUP_SUPPORT_KEY = ['group-support'] as const;
export const groupSupportKey = (gid: string) => ['group-support', gid] as const;
export const autoSendsKey = (since: string) => ['auto-sends', since] as const;

/** Mã ghép sống 10 phút (khớp `pairing.PAIR_TTL`). */
export const PAIR_TTL_MS = 10 * 60_000;
/** Đang chờ Sếp nhắn mã ⇒ hỏi lại trạng thái mỗi 3 giây để thấy «Đã ghép» ngay. */
export const PAIR_POLL_MS = 3000;

export const ONLY_SPECIAL_NOTE =
  'Bot chỉ nhắn Sếp khi có việc đặc biệt (phàn nàn gay gắt, cơ hội lớn, người lạ thêm bot vào nhóm, kênh mất kết nối, câu khó cần Sếp). Không có báo cáo định kỳ — còn lại bot chỉ trả lời khi Sếp hỏi.';

export const CHANNEL_NAME: Record<string, string> = { zalo: 'Zalo', whatsapp: 'WhatsApp' };
export const channelName = (ch: string): string => CHANNEL_NAME[ch] ?? ch;

/** Kênh có kênh quản trị (khớp máy chủ). */
export const isOwnerChannel = (ch: string): boolean => ch === 'zalo' || ch === 'whatsapp';

export function pairInstruction(ch: string, code: string): string {
  return `Từ ${channelName(ch)} của Sếp nhắn cho bot: ${code}`;
}

export function spacedCode(code: string): string {
  return /^\d{6}$/.test(code) ? `${code.slice(0, 3)} ${code.slice(3)}` : code;
}

/** Đếm ngược tới `expiresAt`: nhãn "mm:ss", phần trăm còn lại (0–100), `ms` ≤ 0 = hết hạn. */
export function pairCountdown(expiresAt: string, now: number): { ms: number; label: string; pct: number } {
  const end = Date.parse(expiresAt);
  const ms = Number.isFinite(end) ? end - now : 0;
  const left = Math.max(0, ms);
  const s = Math.ceil(left / 1000);
  const label = `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  return { ms, label, pct: Math.max(0, Math.min(100, (left / PAIR_TTL_MS) * 100)) };
}

export const SEVERITY_OPTIONS: Array<{ value: ReportSeverity; label: string }> = [
  { value: 'urgent', label: 'Chỉ việc khẩn' },
  { value: 'important', label: 'Quan trọng trở lên (khuyên dùng)' },
  { value: 'info', label: 'Mọi việc đặc biệt, kể cả thông tin' },
];

export const HOUR_OPTIONS: Array<{ value: number; label: string }> = Array.from({ length: 24 }, (_, h) => ({
  value: h,
  label: `${String(h).padStart(2, '0')}:00`,
}));

export const hh = (h: number): string => `${String(h).padStart(2, '0')}:00`;

/** Câu giải thích giờ yên lặng (khớp `reports.release_time`). */
export function quietText(p: Pick<OwnerReportPrefs, 'quiet_start' | 'quiet_end'>): string {
  if (p.quiet_start === p.quiet_end) return 'Không đặt giờ yên lặng — bot nhắn ngay khi có việc.';
  return `Từ ${hh(p.quiet_start)} đến ${hh(p.quiet_end)} bot chỉ nhắn việc khẩn; việc khác đợi tới ${hh(p.quiet_end)}.`;
}

/** Số tin/giờ hợp lệ 1–60; ô trống / chữ ⇒ null (báo lỗi tại ô). */
export function parseMaxPerHour(raw: string): number | null {
  if (!/^\d{1,2}$/.test(raw.trim())) return null;
  const n = Number(raw.trim());
  return n >= 1 && n <= 60 ? n : null;
}

export function asOwnerChannelView(d: unknown): OwnerChannelView | null {
  if (!d || typeof d !== 'object') return null;
  const v = d as Partial<OwnerChannelView>;
  return typeof v.paired === 'boolean' && typeof v.enabled === 'boolean' ? (v as OwnerChannelView) : null;
}

/** Đang chờ Sếp nhắn mã ⇒ hỏi lại; còn lại không hỏi. */
export function ownerPairPollMs(v: OwnerChannelView | null | undefined): number | false {
  return v && v.pair_pending && !v.paired ? PAIR_POLL_MS : false;
}

export const OWNER_ERROR_TEXT: Record<string, string> = {
  OWNER_NOT_PAIRED: 'Chưa ghép kênh quản trị — bấm «Lấy mã ghép» rồi nhắn mã cho bot trước.',
  NOT_BOT_ACCOUNT: 'Kênh đang chạy bằng tài khoản cá nhân của Sếp — đổi «Loại tài khoản» sang «Tài khoản riêng của bot» ở thẻ kênh trước.',
  CHANNEL_NOT_FOUND: 'Kênh này chưa có kênh quản trị (chỉ Zalo và WhatsApp).',
  SESSION_NOT_ACTIVE: 'Kênh chưa đăng nhập — nối tài khoản của bot ở thẻ kênh trước rồi mới ghép.',
  SESSION_NOT_FOUND: 'Không tìm thấy phiên kênh — đăng nhập kênh lại rồi thử.',
  GROUP_NOT_FOUND: 'Không tìm thấy nhóm này.',
  SUPPORT_NOT_ALLOWED: 'Chưa bật được chế độ hỗ trợ cho nhóm này.',
  SUPPORT_ALREADY_ON: 'Nhóm này đã bật chế độ hỗ trợ.',
};

/** Lỗi API của kênh quản trị / hỗ trợ / loại tài khoản → chuỗi tiếng Việt. Mã lạ ⇒ câu chung của errorText. */
export function ownerErrorText(e: unknown): string {
  if (e instanceof ApiError && OWNER_ERROR_TEXT[e.code]) return OWNER_ERROR_TEXT[e.code]!;
  return errorText(e);
}

export function ownerStatusText(v: OwnerChannelView, fmt: (iso: string) => string): string {
  if (!v.bot_account) return 'Kênh đang chạy bằng tài khoản cá nhân của Sếp — kênh quản trị chỉ dùng được khi kênh chạy bằng tài khoản riêng của bot.';
  if (!v.paired) return 'Chưa ghép. Lấy mã ghép rồi nhắn mã đó cho bot từ tài khoản của Sếp — bot nhận ra Sếp theo ID tài khoản, không theo tên.';
  const who = [v.owner_display, v.owner_masked].filter(Boolean).join(' · ') || 'tài khoản của Sếp';
  const at = v.paired_at ? ` · từ ${fmt(v.paired_at)}` : '';
  return `Đã ghép với ${who}${at} · ${v.enabled ? 'đang bật' : 'đang TẮT'}`;
}

// ── Loại tài khoản (thẻ kênh) ────────────────────────────────────────────────────────────────────────────────────

export const ACCOUNT_KIND_OPTIONS: Array<{ value: AccountKind; label: string }> = [
  { value: 'bot', label: 'Tài khoản riêng của bot' },
  { value: 'owner_self', label: 'Tài khoản cá nhân của Sếp' },
];

export const accountKindOf = (v: OwnerChannelView | null | undefined): AccountKind =>
  v?.session?.account_kind === 'bot' ? 'bot' : 'owner_self';

export const ACCOUNT_KIND_WARNING =
  'Tài khoản cá nhân của Sếp: bot không tự gửi gì — không bật được Kênh quản trị và Chế độ hỗ trợ. Tài khoản riêng của bot: bot được tự trả lời đúng ba trường hợp (Kênh quản trị, Sếp gọi, Hỗ trợ).';

/** Câu cảnh báo trong hộp xác nhận đổi loại tài khoản. */
export function accountKindChangeText(from: AccountKind, to: AccountKind, v: OwnerChannelView | null | undefined): string {
  if (to === 'bot') {
    return 'Chỉ chọn khi kênh này đăng nhập bằng một tài khoản RIÊNG cho bot (SIM riêng), không phải tài khoản cá nhân của Sếp. Sau đó Sếp ghép Kênh quản trị và bật Chế độ hỗ trợ cho từng nhóm.';
  }
  const parts = ['Bot sẽ không tự gửi gì trên kênh này nữa.'];
  if (from === 'bot') {
    parts.push(v?.paired ? 'Kênh quản trị sẽ bị huỷ ghép' : 'Kênh quản trị sẽ không dùng được');
    parts[parts.length - 1] += (v?.support_groups ?? 0) > 0 ? ` và ${v!.support_groups} nhóm đang hỗ trợ sẽ tắt.` : ' và mọi Chế độ hỗ trợ sẽ tắt.';
  }
  return parts.join(' ');
}

// ── Chế độ hỗ trợ (Danh bạ nhóm) ─────────────────────────────────────────────────────────────────────────────────

export const ADDED_BY_LABEL: Record<string, string> = { owner: 'Sếp', other: 'Người khác', unknown: 'Không rõ' };
/** «Ai thêm bot»: null/vắng (máy chủ chưa gửi) ⇒ «—». */
export const addedByLabel = (k: string | null | undefined): string => (k ? (ADDED_BY_LABEL[k] ?? 'Không rõ') : '—');

export const SUPPORT_HOURS_OPTIONS: Array<{ value: number; label: string }> = [
  { value: 0, label: 'Không hết hạn' },
  { value: 2, label: '2 giờ' },
  { value: 8, label: '8 giờ' },
  { value: 24, label: '1 ngày' },
  { value: 72, label: '3 ngày' },
  { value: 168, label: '7 ngày' },
  { value: 720, label: '30 ngày' },
];

export const SUPPORT_LIMIT_FIELDS: Array<{ key: 'per_group_hour' | 'per_member_hour' | 'per_channel_day'; label: string; max: number }> = [
  { key: 'per_group_hour', label: 'Tin/giờ trong nhóm', max: 200 },
  { key: 'per_member_hour', label: 'Tin/giờ cho mỗi người', max: 50 },
  { key: 'per_channel_day', label: 'Tin/ngày trên cả kênh', max: 2000 },
];

export const SUPPORT_NOTE =
  'Bot trả lời thành viên trong nhóm sau bộ lọc an toàn: không nói giá, tiền, hợp đồng, nhân sự hay thông tin người khác; câu khó thì chuyển Sếp.';

/** Số nguyên ≥ 0 ≤ max; sai ⇒ null. */
export function parseLimit(raw: string, max: number): number | null {
  if (!/^\d{1,5}$/.test(raw.trim())) return null;
  const n = Number(raw.trim());
  return n <= max ? n : null;
}

// ── Tin bot tự gửi ────────────────────────────────────────────────────────────────────────────────────────────────

export const BASIS_LABEL: Record<string, string> = { owner_dm: 'Kênh quản trị', owner_tag: 'Sếp gọi', support: 'Hỗ trợ' };
export const basisLabel = (b: string, serverLabel?: string | null): string => serverLabel || BASIS_LABEL[b] || 'Bot tự gửi';

export const AUTO_STATUS_LABEL: Record<string, string> = {
  sent: 'Đã gửi',
  issued: 'Đang gửi',
  failed: 'Gửi lỗi',
  expired: 'Quá hạn, không gửi',
};
export const autoStatusLabel = (s: string): string => AUTO_STATUS_LABEL[s] ?? s;

/** «Tin bot tự gửi hôm nay: N (Sếp gọi a · Hỗ trợ b · Chuyển Sếp c)». */
export function autoSendTodayText(t: AutoSendToday | null | undefined): string {
  const n = (x: unknown) => (typeof x === 'number' && Number.isFinite(x) ? x : 0);
  if (!t) return 'Tin bot tự gửi hôm nay: 0';
  return `Tin bot tự gửi hôm nay: ${n(t.total)} (Sếp gọi ${n(t.owner_tag)} · Hỗ trợ ${n(t.support)} · Chuyển Sếp ${n(t.escalated)})`;
}
