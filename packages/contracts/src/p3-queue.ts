/** Hợp đồng API giai đoạn 3 · Hàng đợi & Hành động (docs/api/phase-3-queue.md). */
import type { ApiClient } from './client';
import type { AgentRef, EvidenceRef, ExplainUnit, GroupRef, PersonRef, UserRef } from './p3-core';

// ─── Hôm nay (Tổng quan) ────────────────────────────────────────────────────
export interface KpiItem {
  key: string;
  label: string;
  value: number | string | null;
  unit: string | null;
  row: 1 | 2;
  status: 'ok' | 'warn' | 'bad';
  sublabel: string | null;
  pct: number | null;
  filter: { screen: string; filters: Record<string, string> } | null;
}
export type QueueWidgetKind = 'opportunity' | 'alert' | 'draft' | 'due';
export interface QueueWidgetItem {
  kind: QueueWidgetKind;
  id: string;
  code: string | null;
  title: string;
  priority: 'P1' | 'P2' | 'P3';
  at: string | null;
  due_at: string | null;
}
export type SpotlightDimension = 'heat' | 'churn_risk';
export interface SpotlightItem {
  person: PersonRef;
  dimension: SpotlightDimension;
  value: number;
  at: string;
}
export interface SignalItem {
  topic: string;
  count: number;
  delta_pct: number | null;
}
export interface OverviewHealth {
  channels: { type: string; active: number }[];
  /** Trước v0.1.42 — Console không còn đếm plugin (F-41, F-64); API có thể vẫn gửi. */
  plugins?: { healthy: number; degraded: number; isolated: number };
  backlog_pending: number;
  /**
   * v0.1.42 (F-64): 4 số kỹ thuật chuyển từ hàng KPI xuống thẻ "Sức khoẻ hệ thống". Thiếu (API cũ) thì bỏ qua.
   * `processing_latency_s` null khi chưa đủ dữ liệu.
   */
  tech?: OverviewHealthTech;
}
export interface OverviewHealthTech {
  channels_live: number;
  groups_listening: number;
  events_today: number;
  processing_latency_s: number | null;
}
export interface OverviewDataQuality {
  missing_identity_pct: number;
  low_confidence_score_pct: number;
  unassigned_event_pct: number;
}
export interface HourlyPoint {
  hour: string;
  count: number;
}
export interface Overview {
  kpis: KpiItem[];
  queue: QueueWidgetItem[];
  spotlight: SpotlightItem[];
  signals: SignalItem[];
  health: OverviewHealth;
  dataQuality: OverviewDataQuality;
  hourly: HourlyPoint[];
}

// ─── Hộp thư ──────────────────────────────────────────────────────────────────
export type InboxTab = 'all' | 'opportunity' | 'alert' | 'approval' | 'reply' | 'candidate';
export type InboxItemType = 'unit' | 'alert' | 'draft';
export type ConfidenceBandLabel = 'cao' | 'trung bình' | 'thấp';

export interface InboxItem {
  id: string;
  code: string | null;
  item_type: InboxItemType;
  tab: InboxTab;
  title: string;
  summary: string | null;
  priority: 'P1' | 'P2' | 'P3';
  created_at: string;
  score: number | null;
  confidence_band: ConfidenceBandLabel | null;
  subject: (PersonRef | GroupRef) | null;
  group: GroupRef | null;
  agent: AgentRef | null;
  alert_type: string | null;
  alert_type_label: string | null;
  suggested_action: string | null;
  /** Dấu lọc đầu (v0.1.25, Đợt C1) — null/thiếu khi mục chưa được lọc hoặc tổ chức tắt lọc đầu. */
  triage?: InboxTriage | null;
}
/** Kết quả lọc đầu một mục: trùng (exact|near, trỏ mục gốc), rác, điểm chất lượng 0–100 + lý do ngắn. */
export interface InboxTriage {
  duplicate_of: string | null;
  duplicate_kind: 'exact' | 'near' | null;
  spam: boolean;
  spam_reason: string | null;
  score: number;
  /** `score` dưới ngưỡng `min_score` của tổ chức. */
  low_score: boolean;
  reason: string;
  source: 'heuristic' | 'jev';
}
export interface InboxPage {
  items: InboxItem[];
  next_cursor: string | null;
  total: number;
  counts: Record<InboxTab, number>;
  /** v0.1.25 — trạng thái lọc đầu; `hidden` = số mục bị ẩn khi `hide_junk=true`. */
  triage?: { enabled: boolean; min_score: number | null; hidden: number };
}

// ─── Lọc đầu Hộp thư (v0.1.25, Đợt C1 — `/refinery/triage/*`) ─────────────────
export interface TriageSettings {
  enabled: boolean;
  /** Ngưỡng điểm 0–100: dưới ngưỡng = "điểm thấp", bị ẩn khi bật "Ẩn rác & trùng". */
  min_score: number;
  use_jev: boolean;
  /** v0.1.55 (J2): lọc trước khi trích xuất — bỏ qua tin trùng hẳn / rác chắc chắn (mặc định bật; máy chủ cũ không có). */
  prefilter?: boolean;
}
export interface TriageSummary {
  days: number;
  /** v0.1.27: phạm vi `queue.read` của người gọi — `all` = cả tổ chức; `team`/`assigned` chỉ đếm mục mình thấy. */
  scope?: 'all' | 'team' | 'assigned';
  enabled: boolean;
  min_score: number;
  use_jev: boolean;
  total: number;
  kept: number;
  duplicates: number;
  exact_duplicates: number;
  near_duplicates: number;
  spam: number;
  low_score: number;
  pending: number;
  avg_quality: number | null;
  jev: { count: number; heuristic_count: number; avg_latency_ms: number | null; spam_agreement: number | null };
}

// ─── Jev (v0.1.55, G4 — `/jev/*` + `/refinery/triage/skipped`) ─────────────────
/** Một câu trong bộ 12 câu mẫu: `expected`/`got` là NHÃN tiếng Việt (không phải khoá nội bộ). */
export interface JevBenchmarkItem {
  question: string;
  expected: string;
  got: string | null;
  ok: boolean;
  latency_ms: number;
  /** Có khi Jev lỗi / chậm / độ tin thấp với câu này (chuỗi, đã che). */
  error_text?: string;
}
export interface JevBenchmark {
  total: number;
  correct: number;
  /** Trung bình các câu Jev trả lời được; null nếu không câu nào. */
  avg_latency_ms: number | null;
  items: JevBenchmarkItem[];
}
/** Số đo giá trị lọc (chỉ đếm số lần, KHÔNG quy ra tiền). */
export interface ValueSummary {
  filtered: number;
  spam_blocked: number;
  calls_saved: number;
  jev_on: boolean;
}
/** `POST /jev/enable` — cần mã PIN (ai.route_change); chỉ Owner. Khoá không bao giờ quay về trình duyệt. */
export interface JevEnableBody {
  use_existing_openrouter: boolean;
  key?: string;
}
export interface JevEnableResult {
  provider_id: string;
  created: boolean;
  key_source: 'existing_openrouter' | 'pasted' | 'kept';
  endpoint: string;
  model: string;
}
export type SkippedReason = 'exact_dup' | 'spam_rule_jev' | 'spam_rule_nojev';
/** "Tin đã bỏ qua" — tin lọc trước khi trích xuất, chỉ đọc (vai không phải Owner: số dài đã che). */
export interface SkippedItem {
  id: string;
  code: string;
  at: string;
  kind: string;
  reason: SkippedReason;
  reason_text: string;
  text: string;
  group: string | null;
  person: string | null;
}
export interface SkippedPage {
  items: SkippedItem[];
  total: number;
  days: number;
}
export interface InboxDetail extends InboxItem {
  units: ExplainUnit[];
  /** Có khi `item_type` là `alert` (`open|acknowledged`) hoặc `draft` (trạng thái bản nháp). */
  status?: string;
  /** Chỉ có khi `item_type = 'draft'`. */
  kind?: string;
}
export interface InboxActResult {
  ok: true;
  status?: string;
  draft?: { id: string; code: string; status: string };
}
export interface InboxAssignResult {
  ok: true;
  assigned_to: { id: string; name: string };
}

// ─── Việc & Nhắc hẹn ────────────────────────────────────────────────────────
export type TaskStatus = 'todo' | 'doing' | 'done' | 'cancelled';
export type TaskPriority = 'P1' | 'P2' | 'P3';
export type TaskSource = 'promise' | 'draft' | 'manual';
export interface Task {
  id: string;
  code: string;
  title: string;
  priority: TaskPriority;
  status: TaskStatus;
  assignee: UserRef | null;
  subject: (PersonRef | GroupRef) | null;
  due_at: string | null;
  remind_at: string | null;
  overdue: boolean;
  source: TaskSource;
  created_at: string;
  completed_at: string | null;
}
export interface TaskPage {
  items: Task[];
  next_cursor: string | null;
  total: number;
}
export interface TaskCreateBody {
  title: string;
  priority?: TaskPriority;
  assignee_user_id?: string;
  subject?: { type: 'person' | 'group'; id: string };
  due_at?: string | null;
  remind_at?: string | null;
}
export interface TaskPatchBody {
  status?: TaskStatus;
  priority?: TaskPriority;
  assignee_user_id?: string | null;
  due_at?: string | null;
  remind_at?: string | null;
}

// ─── Lời hứa ────────────────────────────────────────────────────────────────
export type PromiseStatus = 'upcoming' | 'overdue' | 'kept' | 'all';
export interface Promise {
  id: string;
  text: string;
  due_at: string;
  kept_at: string | null;
  broken: boolean;
  from: PersonRef;
  to: PersonRef | null;
  evidence: EvidenceRef | null;
}
export interface PromisePage {
  items: Promise[];
  next_cursor: string | null;
  total: number;
}

type Q = Record<string, string | number | boolean | null | undefined>;
const enc = encodeURIComponent;

/** Endpoint của cụm — gộp vào `createEndpoints` dưới khoá `queue` (vd `api.queue.overview()`). */
export function queueEndpoints(r: ApiClient['request']) {
  return {
    overview: (signal?: AbortSignal) => r<Overview>('/overview', { signal }),
    triage: {
      settings: (signal?: AbortSignal) => r<TriageSettings>('/refinery/triage/settings', { signal }),
      setSettings: (body: Partial<TriageSettings>) =>
        r<TriageSettings>('/refinery/triage/settings', { method: 'PATCH', body }),
      summary: (days = 7, signal?: AbortSignal) =>
        r<TriageSummary>('/refinery/triage/summary', { query: { days }, signal }),
      skipped: (limit = 50, signal?: AbortSignal) =>
        r<SkippedPage>('/refinery/triage/skipped', { query: { limit }, signal }),
    },
    jev: {
      benchmark: () => r<JevBenchmark>('/jev/benchmark', { method: 'POST', body: {} }),
      enable: (body: JevEnableBody) => r<JevEnableResult>('/jev/enable', { method: 'POST', body }),
      valueSummary: (days = 7, signal?: AbortSignal) =>
        r<ValueSummary>('/jev/value-summary', { query: { days }, signal }),
    },
    inbox: {
      list: (
        q: { tab?: InboxTab; intent?: string; cursor?: string; limit?: number; hide_junk?: boolean } = {},
        signal?: AbortSignal,
      ) => r<InboxPage>('/inbox', { query: q as Q, signal }),
      get: (id: string, signal?: AbortSignal) => r<InboxDetail>(`/inbox/${enc(id)}`, { signal }),
      act: (id: string, body: { text?: string; create_task?: boolean } = {}) =>
        r<InboxActResult>(`/inbox/${enc(id)}/act`, { method: 'POST', body }),
      assign: (id: string, userId: string) =>
        r<InboxAssignResult>(`/inbox/${enc(id)}/assign`, { method: 'POST', body: { user_id: userId } }),
      silence: (id: string, body: { reason?: string | null; until?: string | null } = {}) =>
        r<{ ok: true }>(`/inbox/${enc(id)}/silence`, { method: 'POST', body }),
    },
    tasks: {
      list: (
        q: {
          status?: TaskStatus;
          priority?: TaskPriority;
          overdue?: boolean;
          /** ISO — chỉ việc CÓ hạn và hạn < mốc này (lọc ở máy chủ, không phụ thuộc trang đầu). */
          due_before?: string;
          assignee_user_id?: string;
          cursor?: string;
          limit?: number;
        } = {},
        signal?: AbortSignal,
      ) => r<TaskPage>('/tasks', { query: q as Q, signal }),
      get: (id: string, signal?: AbortSignal) => r<Task>(`/tasks/${enc(id)}`, { signal }),
      create: (body: TaskCreateBody) => r<Task>('/tasks', { method: 'POST', body }),
      update: (id: string, body: TaskPatchBody) => r<Task>(`/tasks/${enc(id)}`, { method: 'PATCH', body }),
      promises: {
        list: (
          q: { status?: PromiseStatus; cursor?: string; limit?: number } = {},
          signal?: AbortSignal,
        ) => r<PromisePage>('/tasks/promises', { query: q as Q, signal }),
        keep: (id: string, kept: boolean) =>
          r<{ ok: true }>(`/tasks/promises/${enc(id)}`, { method: 'PATCH', body: { kept } }),
      },
    },
  };
}
