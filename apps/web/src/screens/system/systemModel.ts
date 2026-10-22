/**
 * Pure presentation logic for Điều khiển hệ thống › Kênh & đăng nhập and
 * the setup channel/brain steps — ported from the design's `channels`,
 * `creds` and CLI card. Colours are token variables.
 */
import type {
  Boundary,
  Channel,
  ChannelState,
  CliKind,
  CliLoginStatus,
  CliProfile,
  Credential,
  DirectMode,
  GroupKind,
  ListenMode,
  NewGroupMode,
  PermScope,
  RetentionPolicy,
  SettableListenMode,
  ViewScope,
} from '@gen-harness/contracts';
import { ALL_ONLY_PERMS, ApiError, PinCancelledError } from '@gen-harness/contracts';
import { DEFAULT_TZ, fmtDM, fmtHM, fmtInt, fmtLatency, fmtPct, fmtRemaining, fmtSessionAge } from '../../lib/format';
import { AGY_LAYERS_TEXT } from '../../lib/agyLayers';
import { ACC3, BAD, N3, N4, N5, N8, OK, TXT, WARN, channelIcon, channelTone } from '../data/dataModel';

export const CHANNEL_STATE: Record<ChannelState, { label: string; tone: string }> = {
  active: { label: 'Đang kết nối', tone: OK },
  pending_qr: { label: 'Chờ quét mã', tone: WARN },
  expired: { label: 'Phiên hết hạn', tone: WARN },
  logged_out: { label: 'Chưa đăng nhập', tone: N4 },
  error: { label: 'Lỗi kết nối', tone: BAD },
  not_installed: { label: 'Chưa cài', tone: N4 },
  identity_only: { label: 'Chỉ đọc danh tính', tone: N4 },
};

export function channelState(s: ChannelState | string): { label: string; tone: string; border: string } {
  const v = CHANNEL_STATE[s as ChannelState] ?? { label: s, tone: N4 };
  return { ...v, border: v.tone === N4 ? N8 : v.tone };
}

/** `resumable` / `bridge_online` (v0.1.62) nay nằm ở kiểu `Channel` của hợp đồng; máy chủ cũ không gửi ⇒ undefined ⇒ cách hiện như trước. */

/**
 * v0.1.62 (F051, tích hợp B5↔B4): `GET /system/health` báo bộ nhận tin im (`bridge.state === 'silent'`: nhịp mất > 2 phút mà
 * kênh vẫn ghi "Đang kết nối") ⇒ coi thẻ kênh QR đang chạy là mất liên lạc dù `bridge_online` của danh sách kênh chưa kịp
 * đổi. Chỉ áp cho kênh QR có phiên (active/error); không đụng kênh đã đăng xuất. `bridgeSilent` falsy ⇒ trả nguyên thẻ.
 */
export function withBridgeHealth<T extends Pick<Channel, 'type' | 'state' | 'bridge_online'>>(c: T, bridgeSilent: boolean | undefined): T {
  if (!bridgeSilent || c.bridge_online === false) return c;
  if (!(c.type === 'zalo' || c.type === 'whatsapp') || !(c.state === 'active' || c.state === 'error')) return c;
  return { ...c, bridge_online: false };
}

/** Mã lỗi bridge báo khi mất mạng nhưng đang tự nối lại (phiên còn nguyên). */
export const NETWORK_UNAVAILABLE = 'NETWORK_UNAVAILABLE';

/** Thẻ nói còn phiên chạy mà bộ nhận tin (bridge) đã ngừng báo nhịp tim ⇒ tin có thể đang không về. */
export function isBridgeLost(c: Pick<Channel, 'type' | 'state' | 'resumable' | 'bridge_online'>): boolean {
  return (c.type === 'zalo' || c.type === 'whatsapp') && c.bridge_online === false && (c.state === 'active' || c.state === 'error');
}

/** Nhãn + màu trạng thái của thẻ: thêm "Mất liên lạc" và "Mất mạng — đang nối lại" mà `channelState` (theo state thuần) không có. */
export function channelStatus(c: Pick<Channel, 'type' | 'state' | 'error' | 'resumable' | 'bridge_online'>): { label: string; tone: string; border: string } {
  if (isBridgeLost(c)) return { label: 'Mất liên lạc', tone: WARN, border: WARN };
  if (c.state === 'error' && c.error === NETWORK_UNAVAILABLE) return { label: 'Mất mạng — đang nối lại', tone: WARN, border: WARN };
  return channelState(c.state);
}

/** Icon tile colour: the design colours the tile by channel health, not brand. */
export function channelTileTone(c: Pick<Channel, 'type' | 'state'>): string {
  if (c.state === 'active') return OK;
  if (c.state === 'expired' || c.state === 'pending_qr') return WARN;
  if (c.state === 'error') return BAD;
  if (c.state === 'not_installed' || c.state === 'identity_only' || c.state === 'logged_out') return N4;
  return channelTone(c.type);
}

export { channelIcon };

/** Channels that log in with a QR (Zalo, WhatsApp); Telegram is not in this build, LinkedIn is identity-only. */
export const isQrChannel = (c: Pick<Channel, 'type' | 'state'>) =>
  c.state !== 'not_installed' && c.state !== 'identity_only' && (c.type === 'zalo' || c.type === 'whatsapp');

/** Meta line under the channel name — segments so "N nhóm lắng nghe" can be a button. */
export function channelMeta(c: Channel, now = Date.now(), tz?: string): { before: string; groups: string | null; after: string } {
  const groups = `${fmtInt(c.groups_listening)} nhóm lắng nghe`;
  const device = c.account_label ? `thiết bị ${c.account_label}` : '';
  switch (c.state) {
    case 'active':
      if (isBridgeLost(c)) {
        const lost = c.last_heartbeat_at ? `Chưa nhận được tin từ ${fmtHM(c.last_heartbeat_at, tz)}` : 'Chưa nhận được tin';
        return { before: `${lost} · `, groups, after: '' };
      }
      return { before: `Phiên mở ${fmtSessionAge(c.started_at, now)} · `, groups, after: device ? ` · ${device}` : '' };
    case 'expired':
    case 'error': {
      // Bỏ "N tin trong hàng đợi gửi": bridge không có hàng đợi bền (F069) — tin gửi khi phiên không chạy bị từ chối ngay.
      const since = c.last_heartbeat_at ? `Ngừng nhận tin từ ${fmtHM(c.last_heartbeat_at, tz)}` : 'Ngừng nhận tin';
      return { before: `${since} · `, groups, after: '' };
    }
    case 'pending_qr':
      return { before: 'Đang chờ quét mã trên điện thoại · ', groups, after: '' };
    case 'logged_out':
      return c.groups_listening > 0
        ? { before: 'Đã đăng xuất · ', groups, after: '' }
        : { before: 'Chưa đăng nhập — tạo mã QR để bắt đầu nhận tin', groups: null, after: '' };
    case 'not_installed':
      // v0.1.42 (F-41): Plugin đóng băng — không còn trỏ tới chợ tiện ích.
      return { before: 'Kênh này chưa có trong bản đang chạy', groups: null, after: '' };
    case 'identity_only':
      return { before: 'Dùng để hợp nhất danh tính, không nhận tin nhắn', groups: null, after: '' };
    default:
      return { before: '', groups, after: '' };
  }
}

export type ChannelAction = 'logout' | 'rescan' | 'login' | 'resume' | 'unavailable' | 'configure' | null;

export function channelAction(c: Pick<Channel, 'type' | 'state' | 'resumable' | 'bridge_online'>): { action: ChannelAction; label: string; icon: string; accent: boolean } {
  switch (c.state) {
    case 'active':
      return { action: 'logout', label: 'Đăng xuất', icon: 'ph ph-sign-out', accent: false };
    case 'error':
      // v0.1.62 (F046): phiên còn credential ⇒ nối lại bằng phiên đã lưu trước; "Quét lại QR" là đường phụ (channelSecondaryAction).
      if (c.resumable) return { action: 'resume', label: 'Nối lại bằng phiên đã lưu', icon: 'ph ph-plugs-connected', accent: true };
      return { action: 'rescan', label: 'Quét lại QR', icon: 'ph ph-qr-code', accent: true };
    case 'expired':
    case 'pending_qr':
      return { action: 'rescan', label: 'Quét lại QR', icon: 'ph ph-qr-code', accent: true };
    case 'logged_out':
      return { action: 'login', label: 'Tạo mã QR', icon: 'ph ph-qr-code', accent: true };
    case 'not_installed':
      return { action: 'unavailable', label: 'Chưa có trong bản này', icon: 'ph ph-info', accent: false };
    case 'identity_only':
      return { action: 'configure', label: 'Cấu hình', icon: 'ph ph-gear', accent: false };
    default:
      return { action: null, label: '', icon: '', accent: false };
  }
}

/** Nút phụ: phiên 'Lỗi kết nối' nối lại được thì vẫn cho quét lại QR khi nối lại không xong. */
export function channelSecondaryAction(c: Pick<Channel, 'type' | 'state' | 'resumable' | 'bridge_online'>): { action: 'rescan'; label: string; icon: string } | null {
  return c.state === 'error' && c.resumable ? { action: 'rescan', label: 'Quét lại QR', icon: 'ph ph-qr-code' } : null;
}

/**
 * Gói K (P1-1): phiên "Lỗi kết nối" còn giữ phiên đã lưu — Sếp phải có đường ĐĂNG XUẤT (xoá phiên đã lưu, bridge khởi động
 * lại không tự nối), không chỉ Nối lại/Quét lại. Hiện như nút phụ; trạng thái khác đã có nút chính hoặc không có phiên để xoá.
 */
export function channelLogoutAction(c: Pick<Channel, 'state'>): { label: string; icon: string } | null {
  return c.state === 'error' ? { label: 'Đăng xuất', icon: 'ph ph-sign-out' } : null;
}

/** Phiên bị ngắt vì tài khoản mở ở nơi khác: nối lại sẽ đá nơi đó ra ⇒ phải hỏi lại Sếp trước khi gửi lệnh nối. */
export function channelResumeNeedsConfirm(c: Pick<Channel, 'state' | 'error'>): boolean {
  return c.state === 'error' && c.error === 'CONNECTION_REPLACED';
}

/** Câu hỏi xác nhận nối lại khi phiên bị mở nơi khác (Zalo Web / WhatsApp Web trên máy tính…). */
export function channelResumeConfirmText(c: Pick<Channel, 'type' | 'name'>): string {
  const web = c.type === 'whatsapp' ? 'WhatsApp Web' : c.type === 'zalo' ? 'Zalo Web' : `${c.name} ở nơi khác`;
  return `${c.name} đang được mở ở một nơi khác (ví dụ ${web} trên máy tính). Nối lại bây giờ sẽ ngắt nơi đó — Sếp đang dùng thì sẽ bị đăng xuất ở đó. Sếp vẫn muốn nối lại?`;
}

export interface StatCell {
  label: string;
  value: string;
  tone: string;
}

/** The four stat cells (design `ch.stats`); LinkedIn's identity counts are not in the contract → "—". */
export function channelStats(c: Channel): StatCell[] {
  const s = c.stats;
  const dash = '—';
  if (c.type === 'linkedin' || c.state === 'identity_only') {
    return [
      { label: 'Tin 24h', value: dash, tone: TXT },
      { label: 'Danh tính', value: s?.identities != null ? fmtInt(s.identities) : dash, tone: TXT },
      { label: 'Đã gộp', value: s?.merged != null ? fmtInt(s.merged) : dash, tone: TXT },
      { label: 'Thời gian trực', value: s?.uptime_pct != null ? fmtPct(s.uptime_pct) : dash, tone: TXT },
    ];
  }
  const degraded = c.state === 'expired' || c.state === 'error';
  const up = s?.uptime_pct;
  return [
    { label: 'Tin 24h', value: s?.msgs_24h != null ? fmtInt(s.msgs_24h) : dash, tone: degraded && s?.msgs_24h != null ? WARN : TXT },
    { label: 'Được tag', value: s?.tagged_24h != null ? fmtInt(s.tagged_24h) : dash, tone: TXT },
    { label: 'Độ trễ', value: s?.latency_ms != null ? fmtLatency(s.latency_ms) : dash, tone: TXT },
    { label: 'Thời gian trực', value: up != null ? fmtPct(up) : dash, tone: up == null ? TXT : up >= 99 ? OK : up >= 95 ? TXT : WARN },
  ];
}

// ── QR ────────────────────────────────────────────────────────────────────
/**
 * Thời gian sống tối đa của mã (để vẽ thanh đếm ngược) theo kênh: Zalo 100 giây (bridge `ZALO_QR_TTL_MS`), WhatsApp mã đầu
 * 60 giây. Con số ĐẾM NGƯỢC luôn tính từ `expires_at` của sự kiện, không từ hằng số nào (F063).
 */
export const QR_LIFETIME_MS = { zalo: 100_000, whatsapp: 60_000 } as const;
export const qrLifetimeMs = (type: string): number => QR_LIFETIME_MS[type as keyof typeof QR_LIFETIME_MS] ?? QR_LIFETIME_MS.zalo;

export function qrRemaining(expiresAt: string, now = Date.now(), lifetimeMs: number = QR_LIFETIME_MS.zalo): { ms: number; pct: number; label: string } {
  const ms = Math.max(0, new Date(expiresAt).getTime() - now);
  const s = Math.ceil(ms / 1000);
  return { ms, pct: Math.min(100, (ms / lifetimeMs) * 100), label: `${s} giây` };
}

export function qrHint(c: Pick<Channel, 'type'> & Partial<Pick<Channel, 'outbound_queued'>>): string {
  // WhatsApp đổi mã sau 60 giây đầu rồi cứ 20 giây một lần; mã dày hơn Zalo nên cần đưa điện thoại gần, đủ sáng.
  if (c.type === 'whatsapp') {
    return 'Mã tự làm mới (mã đầu 60 giây, các mã sau 20 giây). Nếu điện thoại báo chưa liên kết được, đợi mã mới hiện rồi quét lại.';
  }
  // Không hứa số giây cố định: thời gian còn lại hiện ở thanh đếm ngược (tính từ hạn thật của mã).
  return 'Mã tự làm mới khi hết hạn — thời gian còn lại của mã này hiện ở thanh đếm ngược bên dưới.';
}

/** WhatsApp có mã QR dày (≈ 60 × 60 ô): ảnh phải đủ lớn mới quét được, khác Zalo (≈ 30 × 30 ô). */
export const isDenseQr = (c: Pick<Channel, 'type'>) => c.type === 'whatsapp';

/** Câu cho Sếp khi bộ nhận tin Zalo/WhatsApp đang tắt (503 BRIDGE_OFFLINE); mã lỗi nằm ở "Chi tiết kỹ thuật" (F069). */
export const BRIDGE_OFFLINE_TITLE = 'Bộ nhận tin Zalo/WhatsApp đang tắt — chưa tạo được mã QR';
export const BRIDGE_OFFLINE_TEXT = 'Thử lại sau ít phút; nếu vẫn lỗi, gửi Gói chẩn đoán cho người hỗ trợ.';

// ── lỗi phiên (mã lỗi bridge → câu thân thiện; mã gốc nằm ở "Chi tiết kỹ thuật") ───────────────────────────────
const RESCAN = 'Bấm "Quét lại QR" để thử lại.';
const SAFE_WAIT = 'Đợi vài phút rồi bấm "Quét lại QR"; đừng bấm liên tục vì WhatsApp dễ tạm khoá ghép thiết bị.';

/** Câu thân thiện cho mã lỗi phiên của bridge. `type` quyết định lời khuyên riêng của kênh (mẹo "không liên kết được thiết bị" chỉ có ở WhatsApp). */
export function channelErrorText(code: string, name: string, type?: Channel['type'], resumable = false): string {
  // Phiên còn credential ⇒ chỉ đường nối lại KHÔNG cần quét QR trước (F046).
  const redo = resumable ? 'Bấm "Nối lại bằng phiên đã lưu" (không cần quét QR); không được thì bấm "Quét lại QR".' : RESCAN;
  const no = /^WA_NO_QR_(\d+)$/.exec(code);
  if (no) {
    return `${name} từ chối mở phiên ghép thiết bị mới (mã ${no[1]}). Thường do địa chỉ mạng bị hạn chế hoặc thư viện WhatsApp trong bản này đã cũ. ${SAFE_WAIT}`;
  }
  switch (code) {
    case NETWORK_UNAVAILABLE:
      return `Mất kết nối mạng với ${name}. Gen đang tự thử nối lại bằng phiên đã lưu — không cần quét lại QR; có mạng là tự nhận tin trở lại.`;
    case 'WA_NETWORK':
      return `Bộ nhận tin không nối được tới máy chủ ${name}. Kiểm tra mạng của máy chạy Gen-Harness (DNS, tường lửa, Docker ra Internet tới web.whatsapp.com) rồi bấm "Quét lại QR".`;
    case 'PAIRING_REJECTED':
      return `${name} từ chối lần ghép này (điện thoại có thể đã báo "không liên kết được thiết bị"). ${SAFE_WAIT}`;
    case 'QR_EXPIRED':
      return type === 'whatsapp'
        ? `Mã QR đã hết hạn mà chưa ghép được. ${RESCAN} Nếu điện thoại báo "không liên kết được thiết bị", đợi mã mới hiện rồi quét lại ngay; vẫn không được thì thử lại sau ít phút.`
        : `Mã QR đã hết hạn mà chưa quét. ${RESCAN} Mở ${name} trên điện thoại trước, rồi quét mã ngay khi nó hiện.`;
    case 'FORBIDDEN':
      return `${name} từ chối kết nối từ địa chỉ mạng này (403). Thử lại sau, hoặc đổi mạng cho máy chạy Gen-Harness.`;
    case 'LOGGED_OUT_REMOTELY':
      return `Thiết bị đã bị gỡ khỏi ${name} trên điện thoại. ${RESCAN}`;
    case 'CREDENTIAL_REJECTED':
    case 'CREDENTIAL_INVALID':
    case 'CREDENTIAL_UNREADABLE':
      return `Phiên đã lưu không còn dùng được nữa. ${RESCAN}`;
    case 'CONNECTION_REPLACED':
      return `${name} đang được mở ở một nơi khác nên phiên này bị ngắt. ${redo}`;
    case 'MULTIDEVICE_MISMATCH':
      return `Tài khoản ${name} chưa bật chế độ nhiều thiết bị. Cập nhật ${name} trên điện thoại rồi bấm "Quét lại QR".`;
    case 'RECONNECT_FAILED':
      return `Mất kết nối với ${name} và nối lại nhiều lần không được. Kiểm tra mạng của máy chạy Gen-Harness rồi ${resumable ? 'bấm "Nối lại bằng phiên đã lưu"' : 'bấm "Quét lại QR"'}.`;
    case 'CHANNEL_UNAVAILABLE':
      return `Bộ nhận tin chưa mở được kênh ${name} (thư viện ${name} trong bản này không khởi tạo được). Nhờ người quản trị kiểm tra bộ nhận tin rồi bấm "Quét lại QR".`;
    case 'NO_LOGIN_INFO':
      return `${name} báo quét xong nhưng không trả về thông tin đăng nhập. ${RESCAN}`;
    case 'BRIDGE_KEY_MISSING':
      return 'Bộ nhận tin chưa có khoá bảo mật nên chưa đăng nhập được. Nhờ người quản trị máy chủ kiểm tra khoá bảo mật của bộ nhận tin.';
    default:
      return `Phiên ${name} gặp lỗi khi ghép thiết bị. ${redo} Còn lỗi thì gửi "Chi tiết kỹ thuật" cho người hỗ trợ.`;
  }
}

// ── groups ────────────────────────────────────────────────────────────────
/** QD 17/10: nhóm vào đây là mặc định khi được add — lưu hết tin, KHÔNG BAO GIỜ trả lời (kể cả khi được tag). */
export const SILENT_LISTEN_LABEL = 'Lắng nghe im lặng — lưu hết, không bao giờ trả lời (kể cả khi được tag)';
/** Chế độ Sếp được CHỌN cho một nhóm (khớp `SETTABLE_LISTEN_MODES` ở api/gh/data/common.py). */
export const LISTEN_MODES: Array<{ value: SettableListenMode; label: string }> = [
  { value: 'off', label: 'Không nghe' },
  // v0.1.65 (Q6=1, F065): chế độ này VẪN lưu mọi tin của nhóm — chỉ việc TRẢ LỜI mới cần được tag.
  { value: 'tagged_only', label: 'Nghe hết, chỉ trả lời khi được tag' },
  { value: 'silent', label: SILENT_LISTEN_LABEL },
  { value: 'proactive', label: 'Chủ động bắt tín hiệu' },
  { value: 'paused', label: 'Tạm dừng' },
];
/** Nhãn của nhóm mới đang chờ Sếp chọn (`pending`) — không phải lựa chọn, chỉ để hiển thị. */
export const PENDING_MODE_LABEL = 'Chờ Sếp chọn';

/**
 * Các chế độ ĐANG nghe (lưu tin vào Kho thô) — khớp `LISTENING_MODES` ở api/gh/data/common.py. Một nguồn cho web: `off`,
 * `paused` và `pending` KHÔNG nghe (F066: trước đây mỗi nơi đếm một kiểu).
 */
export const LISTENING_MODE_VALUES: readonly ListenMode[] = ['tagged_only', 'silent', 'proactive'];
export const isListeningMode = (m: ListenMode): boolean => LISTENING_MODE_VALUES.includes(m);

/** Tin riêng 1-1 theo kênh (v0.1.65): mặc định lưu im lặng. */
export const DIRECT_MODES: Array<{ value: DirectMode; label: string; hint: string }> = [
  { value: 'silent', label: 'Lưu im lặng', hint: 'Lưu tin riêng vào Kho thô, không tự trả lời, không đưa cho trợ lý trực. Mặc định.' },
  { value: 'reply', label: 'Lưu và cho trợ lý trả lời', hint: 'Trợ lý trực được phép soạn trả lời tin riêng (vẫn qua duyệt theo mức tự trị).' },
  { value: 'off', label: 'Không lưu', hint: 'Bỏ hẳn tin riêng gửi tới tài khoản.' },
];
export const directModeOf = (c: Pick<Channel, 'direct_mode' | 'listen_direct'>): DirectMode =>
  c.direct_mode ?? (c.listen_direct === false ? 'off' : 'silent');

/** Nhóm MỚI phát hiện sau này (v0.1.65, F1d; Sếp chốt 17/10 thay «hỏi khi kết nối»): vào Lắng nghe im lặng ngay (mặc định) · hỏi từng lần · vào Không nghe. */
export const NEW_GROUP_MODES: Array<{ value: NewGroupMode; label: string; hint: string }> = [
  {
    value: 'silent',
    label: SILENT_LISTEN_LABEL,
    hint: 'Nhóm mới tự vào Lắng nghe im lặng: lưu hết tin, không bao giờ trả lời, kể cả khi được tag. Mặc định.',
  },
  { value: 'ask', label: 'Hỏi Sếp', hint: 'Nhóm mới chờ Sếp chọn Lắng nghe im lặng hay Không nghe — chưa lưu tin nào cho tới khi Sếp chọn.' },
  { value: 'off', label: 'Không nghe', hint: 'Nhóm mới tự vào Không nghe: không lưu tin. Danh sách thành viên vẫn được ghi nhận.' },
];
/** Máy chủ cũ không gửi `new_group_mode` ⇒ `undefined` (không hiện ô chỉnh); có gửi thì giá trị lạ coi như `silent` (mặc định). */
export const newGroupModeOf = (c: Pick<Channel, 'new_group_mode'>): NewGroupMode | undefined =>
  c.new_group_mode === undefined ? undefined : NEW_GROUP_MODES.some((m) => m.value === c.new_group_mode) ? c.new_group_mode : 'silent';

export const VIEW_SCOPES: Array<{ value: ViewScope; label: string }> = [
  { value: 'owner', label: 'Chỉ Sếp' },
  { value: 'manager', label: 'Sếp + quản lý' },
  { value: 'all_members', label: 'Mọi thành viên' },
];

export const GROUP_KIND_LABEL: Record<GroupKind, string> = {
  internal: 'Nội bộ',
  market: 'Thị trường',
  partner: 'Đối tác',
  customer: 'Khách hàng',
  private: 'Riêng tư',
};

export const groupKindLabel = (k: string) => GROUP_KIND_LABEL[k as GroupKind] ?? k;

export function listenTone(m: ListenMode | string): string {
  return m === 'off' ? N4 : m === 'paused' ? WARN : m === 'proactive' ? ACC3 : OK;
}

// ── credentials & CLI ─────────────────────────────────────────────────────
export function credTone(s: Credential['state'] | string): string {
  return s === 'ok' ? OK : s === 'warn' ? WARN : s === 'bad' ? BAD : N4;
}

/**
 * v0.1.31 (Boss 01/10): MỘT sự thật với dòng nguồn — máy chủ tính `state` từ hạn token + refresh token + lượt gọi thật
 * gần nhất. "ok" = AI gọi được (kể cả khi token ngắn hạn đã quá giờ nhưng CLI tự gia hạn) → "Đang hoạt động".
 */
export function cliChip(active: CliProfile | undefined): { label: string; tone: string } {
  if (!active) return { label: 'Chưa đăng nhập', tone: N4 };
  if (active.state === 'expired') return { label: 'Hết hạn', tone: BAD };
  if (active.state === 'expiring') return { label: 'Sắp hết hạn', tone: WARN };
  return { label: 'Đang hoạt động', tone: OK };
}

/** v0.1.31: chữ theo loại CLI — Antigravity (tài khoản Google) hay Claude Code (gói Claude Pro/Max). */
export const CLI_TEXT: Record<CliKind, { title: string; kicker: string; account: string; login: string; add: string; empty: string; openLink: string; hint: string; scope: string | null }> = {
  antigravity_cli: {
    title: 'Tài khoản Antigravity CLI',
    kicker: 'Tài khoản Google dùng cho AI',
    account: 'Google',
    login: 'Đăng nhập',
    add: 'Thêm tài khoản Google',
    empty: 'Đăng nhập Google để hệ thống dùng AI qua Antigravity CLI',
    openLink: 'Mở trang đăng nhập Google',
    hint: 'Đăng nhập đúng tài khoản Google muốn dùng; trang Google sẽ hiện một mã — chép mã đó dán vào ô bên dưới.',
    scope: AGY_LAYERS_TEXT, // gói G: phạm vi theo lớp (lib/agyLayers.ts), thay câu "mọi việc" cũ
  },
  claude_code_cli: {
    title: 'Tài khoản Claude Code CLI',
    kicker: 'Gói Claude Pro/Max của Sếp · tuỳ chọn',
    account: 'Claude',
    login: 'Đăng nhập Claude',
    add: 'Thêm tài khoản Claude',
    empty: 'Chưa bật — đăng nhập gói Claude (Pro/Max) nếu Sếp muốn AI dùng thêm model Claude',
    openLink: 'Mở trang đăng nhập Claude',
    hint: 'Đăng nhập đúng tài khoản Claude muốn dùng, bấm cho phép; trang sẽ hiện một mã — chép mã đó dán vào ô bên dưới.',
    scope: null,
  },
};

/** Trang điều khoản chính thức (QD-12: Owner tự quyết) — Anthropic: đăng nhập gói Free/Pro/Max chỉ cho dùng cá nhân thông thường. */
export const CLAUDE_TERMS_URL = 'https://code.claude.com/docs/en/legal-and-compliance';
export const CLAUDE_CONSUMER_TERMS_URL = 'https://www.anthropic.com/legal/consumer-terms';

/** Email of a CLI profile, or a readable stand-in when the session file did not reveal it (email null). */
export function cliAccountLabel(p: Pick<CliProfile, 'email'> & { kind?: CliKind } | null | undefined): string {
  return p?.email || (p?.kind === 'claude_code_cli' ? 'Tài khoản Claude (chưa rõ email)' : 'Tài khoản Google (chưa rõ email)');
}

/** Friendly Vietnamese text for a failed switch / delete of a CLI account (v0.1.30). */
export function cliSwitchError(e: unknown, fallback: (e: unknown) => string): string {
  if (e instanceof PinCancelledError) return 'Chưa đổi — cần nhập mã PIN để đổi hoặc xoá tài khoản Google.';
  if (e instanceof ApiError) {
    if (e.code === 'CLI_LOGIN_IN_PROGRESS') return 'Đang đăng nhập thêm một tài khoản Google — hoàn tất hoặc bấm “Huỷ đăng nhập” trước.';
    if (e.code === 'CLI_PROFILE_NO_SESSION') return 'Tài khoản này chưa có phiên đăng nhập đã lưu — bấm “Thêm tài khoản Google” để đăng nhập lại.';
    if (e.status === 404) return 'Tài khoản này không còn nữa — danh sách vừa được làm mới.';
  }
  return fallback(e);
}

/**
 * "Google AI Pro · token 0 ₫ · còn hiệu lực 23 giờ" — plan_label carries the plan/token part.
 * v0.1.31: token ngắn hạn quá giờ mà còn refresh token → "tự gia hạn" (không còn "đã hết hiệu lực" gây hiểu nhầm);
 * hết hạn thật → nói việc cần làm.
 */
export function cliMeta(p: CliProfile, now = Date.now()): string {
  if (p.state === 'expired') return [p.plan_label, 'phiên đã hết hiệu lực — bấm “Đăng nhập lại”'].filter(Boolean).join(' · ');
  const passed = p.expires_at ? fmtRemaining(p.expires_at, now) === 'đã hết' : false;
  const left = p.expires_at ? (passed ? (p.refreshable ? 'tự gia hạn' : 'đã hết hiệu lực') : `còn hiệu lực ${fmtRemaining(p.expires_at, now)}`) : p.refreshable ? 'tự gia hạn' : null;
  return [p.plan_label, left].filter(Boolean).join(' · ') || '—';
}

export const CLI_LOGIN_TEXT: Record<CliLoginStatus, string> = {
  starting: 'Đang mở phiên đăng nhập của CLI…',
  waiting_code: 'Mở trang đăng nhập, đăng nhập Google rồi dán mã xác thực vào đây.',
  verifying: 'Đang xác thực mã…',
  done: 'Đã đăng nhập.',
  failed: 'Đăng nhập không thành công.',
};

export { N3 };

// ── Quyền hạn: ma trận (PLAN 4.5, thiết kế `permCols`/`permRows`) ───────────
// v0.1.28 (UX N7): ô ma trận nói PHẠM VI dữ liệu (tất cả / việc được giao / khách được phân / không), không nói "toàn
// quyền" — Auditor chỉ xem, ô ✓ của Auditor nghĩa là xem được tất cả, không phải được làm mọi thứ. Nhãn ngắn để ô
// chọn không bị cắt chữ.
export const SCOPE_CELL: Record<PermScope, { icon: string; tone: string; title: string }> = {
  all: { icon: 'ph-fill ph-check-circle', tone: OK, title: 'Tất cả dữ liệu' },
  team: { icon: 'ph ph-minus-circle', tone: WARN, title: 'Chỉ việc được giao cho họ' },
  assigned: { icon: 'ph ph-minus-circle', tone: WARN, title: 'Chỉ khách được phân' },
  none: { icon: 'ph ph-x', tone: N5, title: 'Không được' },
};

/** F096 (Q4 = 2, 16/10): hệ thống chưa có tính năng Nhóm (tạo nhóm, xếp người — Nợ ROADMAP) ⇒ false. */
export const TEAMS_AVAILABLE = false;

export const SCOPE_OPTIONS: Array<{ value: PermScope; label: string }> = [
  { value: 'all', label: 'Tất cả' },
  { value: 'team', label: 'Việc được giao' },
  { value: 'assigned', label: 'Khách được phân' },
  { value: 'none', label: 'Không' },
];

/** v0.1.45 (F-20): "Đăng nhập", "Đăng nhập lại" và thêm tài khoản CLI đều gọi POST /cli/login — cùng cần phiên PIN. */
export const CLI_ADD_PIN_TEXT = 'Đăng nhập / thêm tài khoản cần mã PIN';

/** F-58: quyền chỉ-toàn-tổ-chức (system.manage) chỉ có "Tất cả" / "Không"; giá trị cũ team/assigned (không có tác dụng)
 *  vẫn hiện để ô chọn không trống, kèm ghi chú, và không chọn lại được. */
export function scopeOptionsFor(permission: string, current: PermScope): Array<{ value: PermScope; label: string; disabled?: boolean }> {
  // F096 (Q4 = 2): chưa có cách tạo team ⇒ mức "team" chỉ còn là việc được giao cho chính người đó; không mời chọn mới,
  // nhưng giá trị đã lưu vẫn hiện để ô chọn không trống. Có tính năng Nhóm thì bật `TEAMS_AVAILABLE`.
  if (!ALL_ONLY_PERMS.includes(permission)) {
    return TEAMS_AVAILABLE || current === 'team' ? SCOPE_OPTIONS : SCOPE_OPTIONS.filter((o) => o.value !== 'team');
  }
  const opts: Array<{ value: PermScope; label: string; disabled?: boolean }> = SCOPE_OPTIONS.filter((o) => o.value === 'all' || o.value === 'none');
  if (current !== 'all' && current !== 'none') {
    const old = SCOPE_OPTIONS.find((o) => o.value === current);
    opts.push({ value: current, label: `${old?.label ?? current} (không có tác dụng)`, disabled: true });
  }
  return opts;
}

/** v0.1.28 (UX N7): tên vai trò tiếng Việt trên giao diện (mã vai trò và tên lưu ở máy chủ giữ nguyên). */
export const ROLE_LABEL: Record<string, { name: string; meta: string }> = {
  owner: { name: 'Owner — Sếp', meta: 'thấy và làm mọi thứ' },
  manager: { name: 'Quản lý', meta: 'thấy việc được giao' },
  operator: { name: 'Vận hành', meta: 'xử lý hàng đợi việc' },
  agent_staff: { name: 'Nhân viên phụ trách', meta: 'chỉ khách được phân' },
  auditor: { name: 'Kiểm soát', meta: 'chỉ xem, không làm thao tác nào' },
};

export function roleLabel(code: string, fallback: string): string {
  return ROLE_LABEL[code]?.name ?? fallback;
}

/** Owner luôn `all` mọi cột; Auditor không bao giờ có quyền ghi — khoá cứng ARCHITECTURE §7.4/§8.3, ô ma
 * trận tương ứng bị khoá (disabled), không phải cho bấm rồi báo lỗi. */
const WRITE_PERMISSIONS = new Set(['queue.act', 'profile.write', 'people_review.write', 'opportunity.write', 'action.draft', 'action.approve']);
export function cellLocked(role: string, permission: string): boolean {
  if (role === 'owner') return true;
  if (role === 'auditor' && WRITE_PERMISSIONS.has(permission)) return true;
  return false;
}

// ── Quyền hạn: ranh giới có trách nhiệm ──────────────────────────────────────
export const BOUNDARY_ICON: Record<string, string> = {
  listen_authorized_only: 'ph ph-shield-check',
  disclose_staff_observation: 'ph ph-eye',
  hide_sensitive_below_owner: 'ph ph-eye-slash',
  personnel_alert_requires_evidence: 'ph ph-quotes',
  observe_external_market: 'ph ph-globe-hemisphere-east',
  auto_personnel_decisions: 'ph ph-prohibit',
  approval_gate: 'ph ph-gavel',
  mcp_write_requires_approval: 'ph ph-plugs-connected',
};

export function boundaryTone(b: Pick<Boundary, 'enabled' | 'locked'>): string {
  if (!b.enabled) return b.locked ? BAD : N5;
  return OK;
}

// ── Nhật ký hệ thống ──────────────────────────────────────────────────────────
export const AUDIT_RESULT: Record<string, { label: string; tone: string }> = {
  ok: { label: 'Thành công', tone: OK },
  held: { label: 'Chờ duyệt', tone: WARN },
  blocked: { label: 'Đã chặn', tone: BAD },
  failed: { label: 'Thất bại', tone: BAD },
};
export function auditResultView(result: string): { label: string; tone: string } {
  return AUDIT_RESULT[result] ?? { label: result, tone: N4 };
}
export function auditActorTone(actorType: string): string {
  return actorType === 'agent' ? ACC3 : actorType === 'system' ? N4 : N3;
}

// ── Dữ liệu & lưu trữ (spec I) ───────────────────────────────────────────────
export const RETENTION_LABEL: Record<string, string> = {
  'raw.events': 'Tin gốc — tin nhắn nguyên bản',
  'clean.meaning_units': 'Tin đã lọc — ý chính từ tin nhắn',
  'ops.action_log': 'Nhật ký hành động',
  'memory.entries': 'Sổ tay nhận thức',
  'agent.model_calls': 'Lượt gọi model',
  'agent.browser_jobs.result': 'Kết quả việc trình duyệt nền',
};

/** Câu mặc định theo cách dọn khi API không gửi `note` (v0.1.40, F-2). */
const RETENTION_MODE_NOTE: Record<string, string> = {
  partition: 'Xoá theo cả tháng khi cả tháng đã quá hạn',
  batch: 'Xoá dần các dòng quá hạn mỗi đêm',
  not_applicable: 'Nhật ký hành động chỉ ghi thêm — không xoá theo hạn',
};

export interface RetentionRowView {
  /** Ô "Giữ trong". */
  keep: string;
  /** false ⇒ "Không áp dụng" (chữ thường, không mono). */
  applicable: boolean;
  /** Có nút Sửa. */
  editable: boolean;
  note: string | null;
  /** "Lần dọn gần nhất: … · đã xoá N tháng/dòng"; null = chưa dọn. */
  lastRun: string | null;
  /** Lượt dọn gần nhất của tập này lỗi ⇒ hiện tông cảnh báo (không coi "đã xoá 0" là thành công). */
  lastFailed: boolean;
  /** Hạn đặt trước v0.1.40 chưa xác nhận ⇒ chưa thi hành (câu nhắc dưới tên tập dữ liệu); null = không. */
  pending: string | null;
  /** Lý do không sửa được (vd Manager trên bảng xoá theo tháng) — hiện thành chữ, không chỉ tooltip; null = không. */
  lockedReason: string | null;
}

/** Người xem dòng hạn lưu: Owner / có quyền sửa (system.manage). Mặc định = Owner (giữ hành vi cũ). */
export interface RetentionViewer {
  isOwner: boolean;
  canManage: boolean;
}

export const RETENTION_OWNER_ONLY_REASON = 'Chỉ Owner đổi được hạn lưu của dữ liệu xoá theo tháng';

/** Tập dữ liệu mà lưu số ngày là đồng ý XOÁ VĨNH VIỄN dữ liệu quá hạn (cần hỏi lại trước khi gửi). */
export function retentionDeletes(r: RetentionPolicy): boolean {
  return r.mode !== 'not_applicable' && r.dataset !== 'ops.action_log' && r.dataset !== 'agent.browser_jobs.result';
}

/** Câu cảnh báo trước khi lưu hạn lưu (xoá vĩnh viễn ở lượt dọn kế tiếp). */
export function retentionConfirmText(r: RetentionPolicy, days: number): string {
  const what = r.mode === 'partition' ? `Mọi tháng dữ liệu đã cũ hơn ${days} ngày` : `Dữ liệu cũ hơn ${days} ngày`;
  return `${what} sẽ bị XOÁ VĨNH VIỄN ở lượt dọn kế tiếp (05:00 hằng ngày) — chỉ lấy lại được từ bản sao lưu.`;
}

/** Bảng phân vùng (xoá cả tháng cho mọi tổ chức trên máy) — chỉ Owner đổi được hạn. */
export function retentionOwnerOnly(r: RetentionPolicy): boolean {
  return r.mode === 'partition';
}

/** v0.1.40 (F-2): một dòng bảng "Hạn lưu dữ liệu" — mọi giá trị là chuỗi/bool. */
export function retentionRowView(r: RetentionPolicy, tz = DEFAULT_TZ, viewer: RetentionViewer = { isOwner: true, canManage: true }): RetentionRowView {
  const notApplicable = r.mode === 'not_applicable';
  const fixed = r.dataset === 'agent.browser_jobs.result';
  const note = typeof r.note === 'string' && r.note.trim() ? r.note.trim() : r.mode ? (RETENTION_MODE_NOTE[r.mode] ?? null) : null;
  const keep = notApplicable
    ? 'Không áp dụng'
    : fixed
      ? `${r.keep_days ?? 14} ngày (cố định)`
      : r.keep_days != null
        ? `${r.keep_days} ngày`
        : 'mãi mãi';
  const lastFailed = !!r.last_run_at && !notApplicable && r.last_ok === false;
  const unit = r.mode === 'partition' ? 'tháng' : 'dòng';
  const lastRun =
    r.last_run_at && !notApplicable
      ? lastFailed
        ? `Lần dọn gần nhất lỗi (${fmtDM(r.last_run_at, tz)} ${fmtHM(r.last_run_at, tz)}) — hệ thống sẽ thử lại lúc 05:00`
        : `Lần dọn gần nhất: ${fmtDM(r.last_run_at, tz)} ${fmtHM(r.last_run_at, tz)}${typeof r.last_deleted === 'number' ? ` · đã xoá ${fmtInt(r.last_deleted)} ${unit}` : ''}`
      : null;
  const editable = !notApplicable && !fixed && r.editable !== false;
  const canEdit = editable && viewer.canManage && (viewer.isOwner || !retentionOwnerOnly(r));
  const pending =
    r.needs_confirm === true && r.keep_days != null && !notApplicable && !fixed
      ? canEdit
        ? 'Chưa áp dụng — hạn này đặt trước bản v0.1.40. Bấm Sửa → Lưu để xác nhận (dữ liệu quá hạn sẽ bị xoá vĩnh viễn).'
        : 'Chưa áp dụng — hạn này đặt trước bản v0.1.40. Nhờ Owner xác nhận lại hạn này.'
      : null;
  return {
    keep: pending ? `${keep} (chưa áp dụng)` : keep,
    applicable: !notApplicable,
    editable,
    note,
    lastRun,
    lastFailed,
    pending,
    lockedReason: editable && viewer.canManage && !viewer.isOwner && retentionOwnerOnly(r) ? RETENTION_OWNER_ONLY_REASON : null,
  };
}

export const DATA_REQUEST_KIND: Record<string, { label: string; icon: string; tone: string }> = {
  export: { label: 'Xuất dữ liệu', icon: 'ph ph-download-simple', tone: N3 },
  erase: { label: 'Ẩn danh hoá', icon: 'ph ph-eye-slash', tone: BAD },
  restrict: { label: 'Giới hạn dùng', icon: 'ph ph-lock-simple', tone: WARN },
};

/** Tên tệp tải về khi xuất dữ liệu một người: `du-lieu-<mã người>-<ngày>.json` (mã làm sạch, không ký tự lạ trong tên tệp). */
export function personExportFileName(code: string, now = new Date()): string {
  return `du-lieu-${code.replace(/[^A-Za-z0-9_-]/g, '') || 'nguoi'}-${now.toISOString().slice(0, 10)}.json`;
}
