/**
 * Hợp đồng v0.1.72 — Bot tài khoản riêng (`apps/api/gh/owner_channel/routes.py`). CHỈ Owner (+ `system.manage`); mọi thao
 * tác đổi trạng thái cần mã PIN (client gặp 423 thì tự mở hộp PIN rồi gửi lại).
 *
 * - `GET/PUT /owner-channel/{ch}` → {@link OwnerChannelView}: kênh quản trị (Zalo/WhatsApp) + cài đặt báo cáo chủ động.
 *   Bot CHỈ nhắn Sếp khi có sự kiện đặc biệt (QĐ Sếp 18/10: không báo cáo định kỳ, không gộp).
 * - `POST /owner-channel/{ch}/pair/start` (PIN) → {@link OwnerPairStart}: mã ghép 6 số, trả MỘT lần, sống 10 phút.
 * - `DELETE /owner-channel/{ch}/pair` (PIN): huỷ ghép — tắt kênh quản trị và mọi chế độ hỗ trợ của kênh.
 * - `PUT /channels/{ch}/sessions/{sid}/account-kind` (PIN) → {@link OwnerChannelView}: «tài khoản riêng của bot» ⇄
 *   «tài khoản cá nhân của Sếp». Đổi khỏi `bot` ⇒ huỷ ghép + tắt hỗ trợ.
 * - `GET/PUT /groups/{gid}/support` (PIN khi đổi) → {@link GroupSupportView}: Chế độ hỗ trợ của một nhóm.
 * - `GET /auto-sends?since=&limit=` → {@link AutoSendPage}: sổ tin bot tự gửi (KHÔNG có nội dung tin).
 *
 * Mã lỗi: 404 CHANNEL_NOT_FOUND / SESSION_NOT_FOUND / GROUP_NOT_FOUND · 409 OWNER_NOT_PAIRED / NOT_BOT_ACCOUNT /
 * SUPPORT_NOT_ALLOWED (detail = lý do nối bằng "; ") / SUPPORT_ALREADY_ON · 422 field errors.
 */
import type { ApiClient } from './client';
import type { Since } from './phase2';

/** Kênh có kênh quản trị (khớp `identity.CHANNELS`). */
export type OwnerChannelType = 'zalo' | 'whatsapp';
export const OWNER_CHANNEL_TYPES: readonly OwnerChannelType[] = ['zalo', 'whatsapp'] as const;

/** `bot` = tài khoản riêng của bot · `owner_self` = tài khoản cá nhân của Sếp (mặc định, bản cài cũ NULL ⇒ owner_self). */
export type AccountKind = 'bot' | 'owner_self';

export type ReportSeverity = 'info' | 'important' | 'urgent';

/** Cài đặt báo cáo chủ động — CHỈ 4 khoá (không có tóm tắt/gộp). Giờ theo giờ Việt Nam, 0–23. */
export interface OwnerReportPrefs {
  quiet_start: number;
  quiet_end: number;
  /** 1–60 tin/giờ. */
  max_per_hour: number;
  min_severity: ReportSeverity;
}

export interface OwnerChannelView {
  channel: OwnerChannelType | string;
  /** Phiên kênh đang chạy (null = chưa đăng nhập). */
  session: { id: string; account_kind: AccountKind | string } | null;
  bot_account: boolean;
  paired: boolean;
  enabled: boolean;
  /** "•••1234" — ID tài khoản Sếp đã che; null khi chưa ghép. */
  owner_masked: string | null;
  owner_display: string | null;
  paired_at: string | null;
  pair_pending: boolean;
  pair_expires_at: string | null;
  report_prefs: OwnerReportPrefs;
  /** Số nhóm đang bật Chế độ hỗ trợ trên kênh. */
  support_groups: number;
}

/** Phản hồi `pair/start`: trạng thái kênh + mã ghép (chỉ ở phản hồi này; web không đưa vào cache). */
export type OwnerPairStart = OwnerChannelView & { pair: { code: string; expires_at: string } };

export interface OwnerChannelBody {
  enabled?: boolean;
  report_prefs?: Partial<OwnerReportPrefs>;
}

export interface AccountKindBody {
  account_kind: AccountKind;
}

/** Giới hạn của Chế độ hỗ trợ (mặc định máy chủ: 20 · 5 · 20 · 150 · 600 · 300). */
export interface SupportLimits {
  per_group_hour: number;
  per_member_hour: number;
  min_gap_s: number;
  per_channel_day: number;
  max_len: number;
  max_age_s: number;
}

/** Ai thêm bot vào nhóm: Sếp · người khác · không rõ (NULL ở bản cài cũ / nhóm chưa có dữ liệu). */
export type AddedByKind = 'owner' | 'other' | 'unknown';

export interface GroupSupportView {
  group: {
    id: string;
    code: string;
    name: string;
    channel: string;
    listen_mode: string;
    added_by_kind: AddedByKind | string | null;
  };
  enabled: boolean;
  since: string | null;
  /** `console` | `owner_cmd` … */
  via: string | null;
  until_at: string | null;
  limits: SupportLimits;
  can_enable: boolean;
  /** Câu tiếng Việt vì sao chưa bật được. */
  blocked_reasons: string[];
  history: Array<{ enabled_at: string | null; via: string | null; disabled_at: string | null; reason: string | null }>;
}

export interface GroupSupportBody {
  enabled: boolean;
  /** 1 giờ – 30 ngày; bỏ trống = không hết hạn. */
  until_hours?: number | null;
  limits?: Partial<SupportLimits> | null;
}

/** `owner_dm` = Kênh quản trị · `owner_tag` = Sếp gọi · `support` = Hỗ trợ. */
export type AutoSendBasis = 'owner_dm' | 'owner_tag' | 'support';
export type AutoSendStatus = 'issued' | 'sent' | 'failed' | 'expired';

export interface AutoSendItem {
  id: string;
  at: string;
  basis: AutoSendBasis | string;
  basis_label: string;
  channel: string;
  thread_type: string | null;
  status: AutoSendStatus | string;
  error: string | null;
  body_len: number | null;
  rules: string[];
  group: { id: string; code: string; name: string } | null;
}

export interface AutoSendToday {
  owner_dm: number;
  owner_tag: number;
  support: number;
  /** Câu khó chuyển Sếp hôm nay (sự kiện `support_escalated`). */
  escalated: number;
  total: number;
}

export interface AutoSendPage {
  items: AutoSendItem[];
  today: AutoSendToday;
}

const enc = encodeURIComponent;

export function ownerChannelEndpoints(r: ApiClient['request']) {
  return {
    get: (ch: string, signal?: AbortSignal) => r<OwnerChannelView>(`/owner-channel/${enc(ch)}`, { signal }),
    update: (ch: string, body: OwnerChannelBody) => r<OwnerChannelView>(`/owner-channel/${enc(ch)}`, { method: 'PUT', body }),
    pairStart: (ch: string) => r<OwnerPairStart>(`/owner-channel/${enc(ch)}/pair/start`, { method: 'POST', body: {} }),
    unpair: (ch: string) => r<OwnerChannelView>(`/owner-channel/${enc(ch)}/pair`, { method: 'DELETE' }),
    setAccountKind: (ch: string, sessionId: string, body: AccountKindBody) =>
      r<OwnerChannelView>(`/channels/${enc(ch)}/sessions/${enc(sessionId)}/account-kind`, { method: 'PUT', body }),
    getSupport: (groupId: string, signal?: AbortSignal) => r<GroupSupportView>(`/groups/${enc(groupId)}/support`, { signal }),
    setSupport: (groupId: string, body: GroupSupportBody) =>
      r<GroupSupportView>(`/groups/${enc(groupId)}/support`, { method: 'PUT', body }),
    autoSends: (q: { since?: Since; limit?: number } = {}, signal?: AbortSignal) =>
      r<AutoSendPage>('/auto-sends', { query: q, signal }),
  };
}
