/**
 * Hợp đồng v0.1.55 (G5) + v0.1.59 (G1) — Mặt tiền Owner (`apps/api/gh/owner/routes.py`, CHỈ ĐỌC, CHỈ Owner; vai trò
 * khác 403 FORBIDDEN). Không GET nào gọi model; mọi thao tác ghi đi qua luồng sẵn có (nháp / đề xuất Gen + Xác nhận +
 * mã PIN — client gặp 423 thì tự hỏi PIN).
 *
 * - `GET /owner/today` → {@link OwnerToday}: Cần Sếp duyệt (≤ 10), 4 số, Bản tin mới nhất, giá trị bộ lọc, gợi ý, tiến độ,
 *   và `nav` ({@link OwnerNav}: số đếm trên thanh điều hướng).
 * - `GET /owner/brief?id=` → {@link OwnerBrief}: bản tin đã lưu (`latest`) + số liệu đọc lúc này (`live`), cảnh báo, lịch sử.
 * - `GET /owner/feed?view=&offset=&limit=` → {@link OwnerFeed}: dòng tin "Thông tin" theo mức Khẩn / Quan trọng / Thường;
 *   `GET /owner/feed/{id}/context` → {@link FeedContext}: hội thoại quanh một tin.
 * - `GET /owner/relations?list=hot|cooling|bridges|matches|care&limit=20` → {@link OwnerRelations} (limit chặn ≤ 50).
 * - `GET /owner/insights?range=week|month&only=k1,k2` → {@link OwnerInsights}: 9 chỉ số tính thẳng từ dữ liệu đang có.
 * - `GET /owner/proposals` → {@link OwnerProposals}: đề xuất của Gen còn hiệu lực của chính Owner.
 * - `GET /owner/tasks` → {@link OwnerTasks}: Hộp thư đã lọc, Bàn làm việc, Việc & Nhắc hẹn (đếm + vài dòng + link sâu).
 *
 * Mọi `to` là đường dẫn nội bộ (bản tin đã đổi về đường Mặt tiền `/owner/…`; các link còn lại là đường Console). Số đếm
 * chạm trần 999 hiện "999+". Kiểu gợi ý khai riêng ở đây (không import từ defaults.ts) để hai gói độc lập khi tích hợp.
 */
import type { ApiClient } from './client';

export type OwnerReviewKind = 'draft' | 'proposal' | 'overdue_task';

/** Một việc chờ Sếp duyệt: bản nháp (Bàn làm việc), đề xuất của Gen (mã PIN), việc quá hạn. */
export interface OwnerReviewItem {
  kind: OwnerReviewKind;
  title: string;
  to: string;
  at: string | null;
}

/** 4 số của Hôm nay (+ giá trị cơ hội): khách nóng, quan hệ nguội, cơ hội đang mở, lời hứa quá hạn. */
export interface OwnerKpis {
  hot: number;
  cooling: number;
  open_opps: number;
  open_value_vnd: number;
  overdue_promises: number;
}

export interface OwnerBriefing {
  title: string;
  at: string | null;
  summary_text: string;
  to: string;
}

/** Giá trị của bộ lọc 7 ngày (hợp đồng G4 `value_summary`). */
export interface OwnerFilterValue {
  filtered: number;
  spam_blocked: number;
  calls_saved: number;
  jev_on: boolean;
}

export type OwnerSuggestionKey = 'apply_standard' | 'background_key_missing';

export interface OwnerSuggestion {
  key: OwnerSuggestionKey;
  title: string;
  body: string;
  /** Đường dẫn trong Console, vd `/system?tab=brain#chuan`. */
  to: string;
}

/** Tiến độ "Việc Sếp cần làm" (lấy từ máy chủ, không ghi cứng). */
export interface OwnerProgress {
  required_done: number;
  required_total: number;
}

/** v0.1.59: số đếm trên thanh điều hướng — Thông tin = `urgent_open`, Việc = `decide`. */
export interface OwnerNav {
  /** Số tin Khẩn đang mở (bằng `feed.counts.urgent_open`; không theo dõi "đã xem"). */
  urgent_open: number;
  /** Nháp chờ duyệt (mọi tuổi, trừ nháp đã tắt tiếng) + đề xuất Gen còn hiệu lực. */
  decide: number;
  /** `true` khi Redis không đọc được đề xuất của Gen: `decide` chỉ đếm nháp (khoá vắng mặt khi bình thường). */
  proposals_unavailable?: boolean;
}

export interface OwnerToday {
  needs_review: OwnerReviewItem[];
  kpis: OwnerKpis;
  briefing_latest: OwnerBriefing | null;
  filter_value: OwnerFilterValue;
  suggestions: OwnerSuggestion[];
  progress: OwnerProgress;
  nav: OwnerNav;
}

// ─── GET /owner/brief (v0.1.59) ──────────────────────────────────────────────────────────────────────────────────

export type BriefGroup = 'decide' | 'alert' | 'key';
export type BriefSlot = 'sáng' | 'chiều';

/** Một mục bản tin. `group`: decide = nháp / việc đến hạn / mail / việc Google · alert = sự cố · key = khách nóng, lịch, Facebook, Kho. */
export interface BriefSection {
  key: string;
  title: string;
  count: number;
  /** ≤ 5 dòng. */
  lines: string[];
  /** Đã đổi về đường Mặt tiền (/tasks, /workbench → /owner/viec; /inbox → /owner/thong-tin; /people, /profile → /owner/quan-he). */
  to: string;
  group: BriefGroup;
  state?: 'error' | 'empty';
  /** Mục Gen-hub chạm trần ⇒ hiện "10+". */
  more?: boolean;
}

/** Bản tin Gen đã lưu. `summary` null khi chưa có nguồn AI / tóm tắt lỗi. */
export interface BriefStored {
  id: string;
  slot: BriefSlot;
  at: string;
  title: string;
  summary: string | null;
  needs_api_key: boolean;
  sections: BriefSection[];
}

/** Số liệu đọc LÚC NÀY (chỉ đọc DB — không có mục Gen-hub). */
export interface BriefLive {
  at: string;
  sections: BriefSection[];
}

export interface BriefAlert {
  key: string;
  title: string;
  severity: 'bad' | 'warn';
  raised_at: string | null;
  to: string;
}

export interface BriefHistoryItem {
  id: string;
  slot: BriefSlot;
  at: string;
  title: string;
}

export interface OwnerBrief {
  latest: BriefStored | null;
  /** Số liệu đọc lúc này; `null` khi xem một bản cũ (`?id=`) — máy chủ không chạy lại phần đọc. */
  live: BriefLive | null;
  /** ≤ 5 sự cố đang mở (nặng trước). */
  alerts: BriefAlert[];
  /** ≤ 14 bản gần nhất, mới trước. */
  history: BriefHistoryItem[];
  /** Mốc bản tin kế tiếp (07:30 / 17:30 giờ VN). */
  next_at: string;
  /** Có ít nhất một nguồn AI đạt (false ⇒ dải "Thiết lập 2 phút" → /setup?buoc=nguon-ai). */
  ai_ready: boolean;
  /** 0 ⇒ "ngày đầu": hiện MỘT thẻ "Nối Zalo". */
  channels_connected: number;
}

// ─── GET /owner/feed (v0.1.59) ───────────────────────────────────────────────────────────────────────────────────

export type FeedView = 'important' | 'all' | 'opportunity' | 'alert' | 'approval';
export type FeedItemType = 'unit' | 'alert' | 'draft';
export type FeedImportance = 'Khẩn' | 'Quan trọng' | 'Thường';

export interface FeedItem {
  id: string;
  item_type: FeedItemType;
  /** Nhãn việc tiếng Việt ("Hỏi giá", "Than phiền"…) hoặc tiêu đề cảnh báo / nháp. */
  title: string;
  /** ≤ 240 ký tự. */
  summary: string;
  importance: FeedImportance;
  /** Chỉ có khi `item_type = 'unit'`. */
  event_type: string | null;
  created_at: string | null;
  person: { id: string; name: string } | null;
  group: { id: string; name: string; channel: string | null } | null;
  /** Chỉ có ở cảnh báo. */
  suggested_action: string | null;
}

export interface FeedCounts {
  important: number;
  all: number;
  opportunity: number;
  alert: number;
  approval: number;
  /** Bằng `OwnerNav.urgent_open`. */
  urgent_open: number;
}

export interface OwnerFeed {
  items: FeedItem[];
  /** null = hết trang. */
  next_offset: number | null;
  counts: FeedCounts;
  /** Gen đã lọc bấy nhiêu tin rác/trùng trong 7 ngày. */
  filtered_7d: { filtered: number; spam_blocked: number };
  channels_connected: number;
}

export interface FeedQuery {
  view?: FeedView;
  offset?: number;
  /** Chặn trong [1, 50], mặc định 30. */
  limit?: number;
}

export interface FeedContextMessage {
  id: string;
  at: string | null;
  direction: 'inbound' | 'outbound';
  sender: string;
  /** Cắt ≤ 280 ký tự. */
  text: string;
  /** Đúng một tin là tin chính. */
  is_center: boolean;
}

/** Hội thoại quanh một tin (≤ 10 tin: 5 trước + tin chính + 4 sau). Không phải tin có gốc ⇒ `messages` rỗng. */
export interface FeedContext {
  center_event_id: string | null;
  messages: FeedContextMessage[];
}

// ─── GET /owner/relations ────────────────────────────────────────────────────────────────────────────────────────

export type OwnerRelationList = 'hot' | 'cooling' | 'bridges' | 'matches' | 'care';

export interface OwnerRelationRow {
  id: string;
  name: string;
  subtitle: string;
  metric_text: string;
  /** Link Hồ sơ sống (`/profile?id=…`); cặp Cung ↔ Cầu không có người thì `/supply`. */
  to: string;
  /** v0.1.59 — trường tuỳ chọn (chỉ có khi có giá trị). */
  person_id?: string;
  heat?: number;
  churn_risk?: number;
  last_contact_days?: number;
  /** Chỉ ở `care`: `churn` = sắp rời · `silent` = lâu chưa liên lạc. */
  reason?: 'churn' | 'silent';
  open_value_vnd?: number;
  /** Chỉ ở `care`, chỉ khi `true`: người này đã có việc / nhắc hẹn ĐANG MỞ — không gợi ý tạo nhắc trùng. */
  has_open_task?: boolean;
  /** Chỉ ở `matches`: mã gợi ý ghép (POST /matches/{id}/introduce|reject). */
  match_id?: string;
}

export interface OwnerRelations {
  list: OwnerRelationList;
  items: OwnerRelationRow[];
  /** `error` khi máy chủ không tính kịp (quá giờ / lỗi) — `items` rỗng KHÔNG có nghĩa "không có khách nào"; vắng mặt khi bình thường. */
  state?: 'error';
}

export type OwnerTaskGroupKey = 'inbox' | 'desk' | 'tasks';

export interface OwnerTaskItem {
  title: string;
  at: string | null;
  to: string;
}

export interface OwnerTaskGroup {
  key: OwnerTaskGroupKey;
  title: string;
  count: number;
  to: string;
  items: OwnerTaskItem[];
}

export interface OwnerTasks {
  groups: OwnerTaskGroup[];
}

// ─── GET /owner/insights (v0.1.59) ───────────────────────────────────────────────────────────────────────────────

export type InsightRange = 'week' | 'month';
export type InsightKey =
  | 'messages_in'
  | 'signals'
  | 'new_contacts'
  | 'opps_new'
  | 'opps_won'
  | 'reply_fast_pct'
  | 'unanswered'
  | 'ai_cost_vnd'
  | 'filtered';
export const INSIGHT_KEYS: readonly InsightKey[] = [
  'messages_in',
  'signals',
  'new_contacts',
  'opps_new',
  'opps_won',
  'reply_fast_pct',
  'unanswered',
  'ai_cost_vnd',
  'filtered',
];

export interface InsightPoint {
  /** Ngày giờ VN, `YYYY-MM-DD`. */
  d: string;
  v: number;
}

/**
 * Một chỉ số. `series` = kỳ này, `prev_series` = kỳ liền trước (cùng độ dài, tô 0 ngày trống; `filtered` không có chuỗi).
 * `delta_pct` null khi kỳ trước = 0 ("Mới"). `good`: chiều tăng là tốt ('up') hay xấu ('down': chi phí, tin chưa trả lời).
 * `state: 'error'` = riêng chỉ số này tính lỗi / quá giờ. `value_vnd`: chỉ ở `opps_won` (tổng giá trị cơ hội thắng kỳ này).
 */
export interface InsightMetric {
  key: InsightKey;
  label: string;
  unit: 'count' | 'vnd' | 'pct';
  total: number;
  prev_total: number;
  delta_pct: number | null;
  good: 'up' | 'down';
  series: InsightPoint[];
  prev_series: InsightPoint[];
  state?: 'error';
  value_vnd?: number;
}

export interface InsightEventType {
  event_type: string;
  /** Nhãn tiếng Việt ("Hỏi giá"…). */
  label: string;
  units: number;
}

export interface InsightFunnelRow {
  stage: string;
  label: string;
  n: number;
  value_vnd: number;
}

/** `null` ở một khối = khối đó tính lỗi; cả `breakdown` null khi gọi với `only`. */
export interface InsightBreakdown {
  event_types: InsightEventType[] | null;
  funnel: InsightFunnelRow[] | null;
}

export interface OwnerInsights {
  range: InsightRange;
  from: string;
  to: string;
  prev_from: string;
  prev_to: string;
  generated_at: string;
  /** empty = chưa từng có tin · collecting = chưa đủ 2 kỳ để so sánh · ok. */
  state: 'ok' | 'collecting' | 'empty';
  /** Chỉ khi `state = 'collecting'`: còn bấy nhiêu ngày nữa có so sánh. */
  days_until_compare: number | null;
  channels_connected: number;
  metrics: InsightMetric[];
  breakdown: InsightBreakdown | null;
  /** Chỉ tính model đã có giá; `unpriced_calls` = số lượt chưa đặt giá (không bao giờ hiện "0 ₫" khi còn lượt này). */
  ai_cost: { calls: number; unpriced_calls: number; budget_vnd: number | null };
  /** Đánh giá Hữu ích / Không hữu ích — chỉ ở chế độ 7 ngày. */
  feedback: { helpful: number; not_helpful: number } | null;
}

// ─── GET /owner/proposals (v0.1.59) ──────────────────────────────────────────────────────────────────────────────

/** Đề xuất của Gen còn hiệu lực. Xác nhận / Huỷ qua `api.gen.confirmProposal|cancelProposal` (404 ⇒ đã hết hạn). */
export interface OwnerProposal {
  id: string;
  type: string;
  /** Nhãn tiếng Việt ("Tạo nhắc việc"…). */
  type_label: string;
  summary: string;
  /** kho_*, social_* (và mục tiêu nhạy cảm) cần mã PIN — client tự hỏi khi gặp 423. */
  needs_pin: boolean;
  created_at: string | null;
  expires_at: string | null;
  conversation_id: string;
}

export interface OwnerProposals {
  items: OwnerProposal[];
  /** `unavailable` khi Redis không đọc được (khi đó `items` rỗng chứ KHÔNG phải "không có đề xuất"); vắng mặt khi bình thường. */
  state?: 'unavailable';
}

/** `/owner/*` — gắn vào `endpoints.ts` (`owner: ownerEndpoints(r)`). Chỉ có GET. */
export function ownerEndpoints(r: ApiClient['request']) {
  return {
    today: (signal?: AbortSignal) => r<OwnerToday>('/owner/today', { signal }),
    brief: (id?: string, signal?: AbortSignal) => r<OwnerBrief>('/owner/brief', { signal, query: { id } }),
    feed: (q: FeedQuery = {}, signal?: AbortSignal) =>
      r<OwnerFeed>('/owner/feed', { signal, query: { view: q.view, offset: q.offset, limit: q.limit } }),
    feedContext: (id: string, signal?: AbortSignal) =>
      r<FeedContext>(`/owner/feed/${encodeURIComponent(id)}/context`, { signal }),
    relations: (list: OwnerRelationList, limit = 20, signal?: AbortSignal) =>
      r<OwnerRelations>('/owner/relations', { signal, query: { list, limit } }),
    insights: (range: InsightRange, only?: readonly InsightKey[] | string, signal?: AbortSignal) =>
      r<OwnerInsights>('/owner/insights', {
        signal,
        query: { range, only: typeof only === 'string' ? only : only?.join(',') },
      }),
    proposals: (signal?: AbortSignal) => r<OwnerProposals>('/owner/proposals', { signal }),
    tasks: (signal?: AbortSignal) => r<OwnerTasks>('/owner/tasks', { signal }),
  };
}
