/** Hợp đồng API giai đoạn 3 · nền chung (chứng cứ, góc nhìn, bản nháp) (docs/api/phase-3.md). */
import type { ApiClient } from './client';
import type { ChannelType as Channel, RawDetail } from './phase2';

export type PersonType = 'customer' | 'partner' | 'staff' | 'candidate' | 'learner' | 'supplier' | 'unknown';
export type Trend = 'up' | 'down' | 'flat';

export interface PersonRef {
  id: string;
  code: string;
  name: string;
  type: PersonType | null;
  org_name: string | null;
}
export interface GroupRef {
  id: string;
  code: string;
  name: string;
  channel: Channel;
}
export interface UserRef {
  id: string;
  name: string;
  role?: string;
}
export interface AgentRef {
  id: string;
  name: string;
}
export interface Score {
  value: number;
  trend: Trend | null;
  confidence: number | null;
}
export type EvidenceKind = 'meaning_unit' | 'raw' | 'score' | 'alert' | 'opportunity' | 'draft' | 'review' | 'task';
export interface EvidenceRef {
  type: EvidenceKind | string;
  id: string;
  code?: string | null;
  label?: string | null;
}

/** Chip tin cậy: cao ≥ 0,8 · trung bình ≥ 0,6 · thấp. */
export type ConfidenceBand = 'high' | 'medium' | 'low';
export function confidenceBand(c: number): ConfidenceBand {
  return c >= 0.8 ? 'high' : c >= 0.6 ? 'medium' : 'low';
}

// ─── chứng cứ ───────────────────────────────────────────────────────────────
export interface ExplainQuote {
  raw_id: string;
  raw_code: string | null;
  quote: string;
  occurred_at: string;
  channel: Channel;
  sender: PersonRef | null;
}
export interface ExplainUnit {
  id: string;
  event_type: string;
  conclusion: string;
  confidence: number;
  observed_at: string;
  group: GroupRef | null;
  person: PersonRef | null;
  quotes: ExplainQuote[];
}
export interface ExplainFactor {
  label: string;
  value: number | null;
  evidence: EvidenceRef[];
}
export interface Explain {
  kind: string;
  id: string;
  title: string;
  statement: string;
  method: string | null;
  factors: ExplainFactor[];
  units: ExplainUnit[];
  history: {
    value: number;
    computed_at: string;
    method: string | null;
    by: UserRef | null;
  }[];
}

// ─── góc nhìn đã lưu ────────────────────────────────────────────────────────
export interface SavedView {
  id: string;
  screen: string;
  name: string;
  filters: Record<string, string>;
  created_at: string;
}

// ─── bản nháp ───────────────────────────────────────────────────────────────
export type DraftKind = 'message' | 'quotation' | 'contract' | 'reminder' | 'report' | 'mcp_write';
export type DraftStatus = 'pending' | 'approved' | 'edited' | 'sent' | 'failed' | 'rejected';
export interface DraftItem {
  id: string;
  code: string;
  kind: DraftKind;
  kind_label: string;
  title: string;
  agent: AgentRef | null;
  created_by: UserRef | null;
  created_at: string;
  status: DraftStatus;
  hold_reason: string | null;
  subject: (PersonRef | GroupRef) | null;
  /** Bản gửi lỗi đã được gửi lại bằng bản MỚI này (nối bằng `resend_of`); null = chưa gửi lại (hoặc bản mới đã bị huỷ). */
  resent_by?: { id: string; code: string } | null;
}
export interface DraftPage {
  items: DraftItem[];
  next_cursor: string | null;
  total: number;
}
export interface SendResult {
  ok: boolean;
  error: string | null;
  at: string;
  external_msg_id?: string | null;
}
export interface DraftDetail extends DraftItem {
  paragraphs: string[];
  text: string;
  lang: string;
  target: {
    channel: Channel;
    thread_type: 'group' | 'user';
    group: GroupRef | null;
    person: PersonRef | null;
    /** Tin 1-1: tài khoản kênh sẽ nhận (tên hiển thị) — Sếp thấy trước khi duyệt; null = nhóm hoặc chưa tìm thấy. */
    account?: { label: string } | null;
    /** Tài khoản kênh cụ thể đã chốt cho tin 1-1 (gửi lại giữ nguyên). */
    identity_id?: string | null;
  } | null;
  amount_vnd: number | null;
  autonomy_level: number;
  flags: {
    writes_external: boolean;
    personnel_related: boolean;
    over_threshold: boolean;
  };
  approve_label: string;
  sources: { label: string; ref: EvidenceRef | null }[];
  context: { key: string; value: string; ref: EvidenceRef | null }[];
  side_actions: { key: string; label: string; on: boolean }[];
  decision: { by: UserRef; at: string; reason: string | null } | null;
  send_result: SendResult | null;
  versions: { at: string; by: 'agent' | 'user'; text: string }[];
}
export interface DraftCreate {
  kind: Exclude<DraftKind, 'mcp_write'>;
  title: string;
  text: string;
  target?: {
    channel: Channel;
    thread_type: 'group' | 'user';
    group_id?: string;
    person_id?: string;
    identity_id?: string;
  } | null;
  amount_vnd?: number | null;
  subject?: { type: 'person' | 'group'; id: string } | null;
  sources?: { label: string; ref: EvidenceRef | null }[];
  /** "Gửi lại": id bản nháp GỬI LỖI mà bản này thay thế — bản cũ ra khỏi thẻ "Gửi lỗi", bấm lần hai bị chặn (409 ALREADY_RESENT). */
  resend_of?: string | null;
}
export interface DraftDecide {
  side_actions?: Record<string, boolean>;
  text?: string;
  reason?: string | null;
}
export type DraftNewEvent = DraftItem;
export interface DraftUpdatedEvent {
  id: string;
  status: DraftStatus;
  send_result: SendResult | null;
}

// ─── agent trực kênh ────────────────────────────────────────────────────────
export interface AgentDecision {
  id: string;
  at: string;
  agent: AgentRef;
  decision: 'silent' | 'note' | 'suggest' | 'draft' | 'send';
  rationale: string | null;
  trigger: EvidenceRef;
  context_refs: EvidenceRef[];
  /** `status` = trạng thái nháp thật (v0.1.67, F076): chỉ khi `sent` mới là "Đã gửi". Máy chủ cũ không gửi thì thiếu. */
  draft: { id: string; code: string; status?: DraftStatus } | null;
  /**
   * v0.1.67 (F076): kết cục sau chính sách tự trị — `held` (nháp chờ Sếp duyệt), `suggested`, `noted`, `blocked`,
   * `rejected`, `none`. Máy chủ cũ không gửi thì thiếu.
   */
  outcome?: 'none' | 'noted' | 'suggested' | 'held' | 'blocked' | 'rejected' | null;
  /** v0.1.67 (F075): nội dung agent gợi ý / soạn (đã che số nhạy cảm cho vai dưới Owner); null khi không có. */
  proposal_text?: string | null;
}

type Q = Record<string, string | number | boolean | null | undefined>;
const enc = encodeURIComponent;

/** Endpoint nền chung — gộp vào `createEndpoints` dưới các khoá `explain`, `views`, `drafts`, `agentDecisions`. */
export function coreEndpoints(r: ApiClient['request']) {
  return {
    explain: {
      get: (kind: string, id: string, signal?: AbortSignal) => r<Explain>(`/explain/${enc(kind)}/${enc(id)}`, { signal }),
      raw: (rawId: string, signal?: AbortSignal) => r<RawDetail>(`/explain/raw/${enc(rawId)}`, { signal }),
    },
    views: {
      list: (screen?: string, signal?: AbortSignal) => r<SavedView[]>('/views', { query: { screen }, signal }),
      create: (body: { screen: string; name: string; filters: Record<string, string> }) => r<SavedView>('/views', { method: 'POST', body }),
      remove: (id: string) => r<void>(`/views/${enc(id)}`, { method: 'DELETE' }),
    },
    drafts: {
      list: (
        q: {
          /** `failed`: chỉ bản gửi lỗi CHƯA được gửi lại (lọc ở máy chủ). */
          status?: 'pending' | 'decided' | 'failed' | 'all';
          kind?: string;
          /** Bỏ nháp đã "Tắt tiếng" (Mặt tiền Owner: số khớp `today.nav.decide`). */
          hide_silenced?: boolean;
          cursor?: string;
          limit?: number;
        } = {},
        signal?: AbortSignal,
      ) => r<DraftPage>('/drafts', { query: q as Q, signal }),
      get: (id: string, signal?: AbortSignal) => r<DraftDetail>(`/drafts/${enc(id)}`, { signal }),
      create: (body: DraftCreate) => r<DraftDetail>('/drafts', { method: 'POST', body }),
      approve: (id: string, body: DraftDecide = {}) => r<DraftDetail>(`/drafts/${enc(id)}/approve`, { method: 'POST', body }),
      editSend: (id: string, body: DraftDecide & { text: string }) =>
        r<DraftDetail>(`/drafts/${enc(id)}/edit-send`, {
          method: 'POST',
          body,
        }),
      reject: (id: string, body: DraftDecide = {}) => r<DraftDetail>(`/drafts/${enc(id)}/reject`, { method: 'POST', body }),
      translate: (id: string, lang: 'vi' | 'en' | 'zh' | 'ja' | 'ko') =>
        r<{ lang: string; text: string }>(`/drafts/${enc(id)}/translate`, {
          method: 'POST',
          body: { lang },
        }),
      regenerate: (id: string, instruction?: string | null) =>
        r<DraftDetail>(`/drafts/${enc(id)}/regenerate`, {
          method: 'POST',
          body: { instruction: instruction ?? null },
        }),
    },
    agentDecisions: (
      q: {
        agent_id?: string;
        decision?: string;
        cursor?: string;
        limit?: number;
      } = {},
      signal?: AbortSignal,
    ) => r<{ items: AgentDecision[]; next_cursor: string | null; total: number }>('/agents/decisions', { query: q as Q, signal }),
  };
}
