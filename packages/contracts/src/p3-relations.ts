/** Hợp đồng API giai đoạn 3 · Quan hệ & Đối tượng (docs/api/phase-3-relations.md). */
import type { ApiClient } from './client';
import type { AgentRef, EvidenceRef, GroupRef, PersonRef, PersonType, Trend, UserRef } from './p3-core';
import type { ChannelState, ChannelType as Channel, GroupKind, ListenMode, NotebookSubjectType } from './phase2';

// ─── Nhóm & Con người (directory) ───────────────────────────────────────────
export interface DirChannel {
  id: string;
  type: Channel;
  name: string;
  /** Chỉ 4 giá trị mà cụm này dùng (`active|pending_qr|expired|logged_out`), hoặc `null` khi kênh chưa cài. */
  state: ChannelState | null;
  group_count: number;
  events_24h: number;
}
export interface DirGroup {
  id: string;
  code: string;
  name: string;
  kind: GroupKind | string;
  listen_mode: ListenMode;
  member_count: number;
  events_24h: number;
  heat: number | null;
  channel: { type: Channel; name: string };
  bot: AgentRef | null;
  created_at: string;
  /** v0.1.72: ai thêm bot vào nhóm — `owner` · `other` · `unknown`; null/vắng = máy chủ chưa gửi (hiện «—»). */
  added_by_kind?: 'owner' | 'other' | 'unknown' | string | null;
}
export type DirRelation = 'direct' | 'via_staff' | 'stranger' | 'staff';
export type DirHeatBand = 'high' | 'mid' | 'cold';
export type DirValueBand = 'high' | 'mid' | 'unknown';
export type DirPriority = 'P1' | 'P2' | 'P3';
export interface DirPerson {
  id: string;
  code: string;
  name: string;
  type: PersonType | null;
  org_name: string | null;
  relation: DirRelation;
  channels: Channel[];
  heat: number | null;
  heat_trend: Trend | null;
  value_vnd: number | null;
  priority: DirPriority;
  bot: AgentRef | null;
  autonomy_level: number | null;
  owner_user_id: string | null;
}
export interface DirGroupQuery {
  channel_id?: string;
  kind?: string;
  listen_mode?: string;
  /** Tìm theo tên hoặc mã nhóm — máy chủ lọc (không lọc trong phần đã tải). */
  q?: string;
  /** Con trỏ `<created_at>|<id>` do máy chủ trả ở `next_cursor`. */
  cursor?: string;
  limit?: number;
}
export interface DirPeopleQuery {
  relation?: DirRelation | '';
  heat?: DirHeatBand | '';
  value?: DirValueBand | '';
  priority?: DirPriority | '';
  bot?: 'assigned' | 'unassigned' | '';
  /** Loại kênh (zalo, whatsapp, …): người có danh tính trên kênh đó. */
  channel?: string;
  /** Tìm theo tên hoặc mã người — máy chủ lọc. */
  q?: string;
  /** Con trỏ `<created_at>|<id>` do máy chủ trả ở `next_cursor`. */
  cursor?: string;
  limit?: number;
}
/** Bộ lọc của thao tác hàng loạt — cùng nghĩa với `DirPeopleQuery` (không có con trỏ/giới hạn). */
export type DirPeopleFilter = Omit<DirPeopleQuery, 'cursor' | 'limit'>;
export interface DirCursorPage<T> {
  items: T[];
  next_cursor: string | null;
  total: number;
}
export interface DirBotBody {
  agent_id?: string | null;
  autonomy_level?: number | null;
}
/**
 * Gán BOT / tự trị cho MỌI người khớp bộ lọc (máy chủ tự tìm, không giới hạn ở trang đã tải). Khoá vắng ⇒ giữ nguyên;
 * `agent_id: null` ⇒ gỡ BOT. `expected_total` = số đã hiện cho người dùng: lệch ⇒ 409 `BULK_FILTER_CHANGED`.
 */
export interface DirBulkBotBody extends DirBotBody {
  filter: DirPeopleFilter;
  expected_total?: number;
}
export interface DirBulkBotResult {
  ok: true;
  updated: number;
}

// ─── Hồ sơ sống (profile) ────────────────────────────────────────────────────
export interface ProfileIdentity {
  id: string;
  channel: { type: Channel; name: string };
  external_id: string;
  handle: string | null;
  phone_e164: string | null;
  first_seen_at: string;
}
export type ScoreTone = 'ok' | 'bad' | 'neutral';
export interface ProfileScore {
  dimension: string;
  label: string;
  value: number;
  trend: Trend | null;
  updated_at: string;
}
export interface ProfileSummaryLine {
  text: string;
  tone: ScoreTone;
  evidence: EvidenceRef;
}
export interface ProfileTimelineItem {
  id: string;
  event_type: string;
  conclusion: string;
  confidence: number;
  observed_at: string;
  group: GroupRef | null;
  evidence: EvidenceRef;
}
export interface ProfileDocument {
  id: string;
  title: string;
  mime: string;
  bytes: number;
  created_at: string;
}
export interface ProfileMergeHistory {
  id: string;
  op: 'merge' | 'split';
  from_person: PersonRef;
  to_person: PersonRef;
  identities: string[];
  at: string;
  reverted_at: string | null;
}
export interface Profile {
  person: PersonRef & { title: string | null; relation_to_owner: DirRelation | string; owner: UserRef | null };
  autonomy_level: number | null;
  bot: AgentRef | null;
  owner_note: string | null;
  identities: ProfileIdentity[];
  scores: ProfileScore[];
  summary: ProfileSummaryLine[];
  timeline: ProfileTimelineItem[];
  documents: ProfileDocument[];
  touchpoints: UserRef[];
  merge_history: ProfileMergeHistory[];
}
export interface ProfilePatchBody {
  owner_user_id?: string | null;
  autonomy_level?: number | null;
  note?: string | null;
  /** v0.1.64 (F045, F141): Sếp tự đặt loại người ("Đánh dấu là nhân viên"); không gửi = giữ nguyên. */
  person_type?: PersonType;
  /** v0.1.64: mức "Liên quan Sếp". */
  relation_to_owner?: DirRelation;
}

// ─── Sổ tay nhận thức (notebook) ─────────────────────────────────────────────
export interface NbSubject {
  id: string;
  code: string;
  name: string;
  entries: number;
  token_used: number;
  token_budget: number;
  updated_at: string;
}
export interface NbEntry {
  id: string;
  body: string;
  refs: EvidenceRef[];
  pinned: boolean;
  editable: boolean;
  author: { type: 'user' | 'agent'; label: string };
  created_at: string;
}
export interface NbSection {
  key: string;
  title: string;
  entries: NbEntry[];
}
export interface NbNotebook {
  subject: { type: NotebookSubjectType; id: string; code: string; name: string };
  token_used: number;
  token_budget: number;
  compaction_no: number;
  last_compacted_at: string | null;
  sections: NbSection[];
  refs: EvidenceRef[];
}
export interface NbHistoryItem {
  compaction_no: number;
  at: string;
  tokens_before: number;
  tokens_after: number;
  archived: number;
  summary: string;
}
export interface NbDroppedItem {
  id: string;
  section: string;
  body: string;
  refs: EvidenceRef[];
  author: { type: 'user' | 'agent' };
  archived_at: string;
}
export interface NbEntryBody {
  section: string;
  body: string;
  pinned?: boolean;
  refs?: EvidenceRef[];
}
export interface NbEntryPatchBody {
  body?: string;
  pinned?: boolean;
}

// ─── Tài liệu (documents, spec G1) ────────────────────────────────────────────
export type DocSource = 'channel' | 'agent' | 'tay';
export interface DocumentItem {
  id: string;
  title: string;
  description: string | null;
  mime: string;
  bytes: number;
  owner: (PersonRef | GroupRef) | null;
  source: DocSource;
  /** Nhãn người/agent tạo (vd "Nguyễn Văn A (Chủ)", "Trợ lý thương mại") — `source` đã tách theo loại. */
  created_by: string | null;
  created_at: string;
  updated_at: string;
}
export interface DocAclRow {
  principal: string;
  can_read: boolean;
  can_write: boolean;
}
export interface DocumentDetail extends DocumentItem {
  acl: DocAclRow[];
}
export interface DocumentCreateBody {
  title: string;
  description?: string | null;
  filename: string;
  mime: string;
  content_base64: string;
  owner_person_id?: string;
  owner_group_id?: string;
  acl?: DocAclRow[];
}
export interface DocumentPatchBody {
  title?: string;
  description?: string | null;
  owner_person_id?: string | null;
  owner_group_id?: string | null;
}
export interface DocumentQuery {
  owner_person_id?: string;
  owner_group_id?: string;
  source?: DocSource;
  cursor?: string;
  limit?: number;
}

type Q = Record<string, string | number | boolean | null | undefined>;
const enc = encodeURIComponent;

/** Tải xuống/xem trước một tài liệu — GET trực tiếp (bytes nguyên văn), không qua ApiClient JSON. */
export function documentContentUrl(id: string, baseUrl = '/api/v1'): string {
  return `${baseUrl}/documents/${enc(id)}/content`;
}

/** Endpoint của cụm — gộp vào `createEndpoints` dưới khoá `relations` (vd `api.relations.directory.groups.list(...)`). */
export function relationsEndpoints(r: ApiClient['request']) {
  return {
    directory: {
      channels: (signal?: AbortSignal) => r<DirChannel[]>('/directory/channels', { signal }),
      groups: {
        list: (q: DirGroupQuery = {}, signal?: AbortSignal) => r<DirCursorPage<DirGroup>>('/directory/groups', { query: q as Q, signal }),
        setBot: (id: string, body: DirBotBody) => r<DirGroup>(`/directory/groups/${enc(id)}/bot`, { method: 'POST', body }),
      },
      people: {
        list: (q: DirPeopleQuery = {}, signal?: AbortSignal) => r<DirCursorPage<DirPerson>>('/directory/people', { query: q as Q, signal }),
        setBot: (id: string, body: DirBotBody) => r<DirPerson>(`/directory/people/${enc(id)}/bot`, { method: 'POST', body }),
        bulkBot: (body: DirBulkBotBody) => r<DirBulkBotResult>('/directory/people/bulk-bot', { method: 'POST', body }),
      },
    },
    profile: {
      get: (personId: string, signal?: AbortSignal) => r<Profile>(`/profile/${enc(personId)}`, { signal }),
      update: (personId: string, body: ProfilePatchBody) => r<Profile>(`/profile/${enc(personId)}`, { method: 'PATCH', body }),
    },
    notebook: {
      subjects: (q: { type: NotebookSubjectType; cursor?: string; limit?: number }, signal?: AbortSignal) =>
        r<{ items: NbSubject[]; next_cursor: string | null }>('/notebook/subjects', { query: q as Q, signal }),
      get: (type: NotebookSubjectType, id: string, signal?: AbortSignal) => r<NbNotebook>(`/notebook/${type}/${enc(id)}`, { signal }),
      addEntry: (type: NotebookSubjectType, id: string, body: NbEntryBody) =>
        r<NbEntry>(`/notebook/${type}/${enc(id)}/entries`, { method: 'POST', body }),
      updateEntry: (type: NotebookSubjectType, id: string, eid: string, body: NbEntryPatchBody) =>
        r<{ id: string }>(`/notebook/${type}/${enc(id)}/entries/${enc(eid)}`, { method: 'PATCH', body }),
      deleteEntry: (type: NotebookSubjectType, id: string, eid: string) =>
        r<void>(`/notebook/${type}/${enc(id)}/entries/${enc(eid)}`, { method: 'DELETE' }),
      compact: (type: NotebookSubjectType, id: string) => r<NbNotebook>(`/notebook/${type}/${enc(id)}/compact`, { method: 'POST' }),
      reset: (type: NotebookSubjectType, id: string) => r<NbNotebook>(`/notebook/${type}/${enc(id)}/reset`, { method: 'POST' }),
      history: (type: NotebookSubjectType, id: string, signal?: AbortSignal) => r<NbHistoryItem[]>(`/notebook/${type}/${enc(id)}/history`, { signal }),
      dropped: (type: NotebookSubjectType, id: string, q: { cursor?: string; limit?: number } = {}, signal?: AbortSignal) =>
        r<{ items: NbDroppedItem[]; next_cursor: string | null }>(`/notebook/${type}/${enc(id)}/dropped`, { query: q as Q, signal }),
    },
    documents: {
      list: (q: DocumentQuery = {}, signal?: AbortSignal) => r<DirCursorPage<DocumentItem>>('/documents', { query: q as Q, signal }),
      get: (id: string, signal?: AbortSignal) => r<DocumentDetail>(`/documents/${enc(id)}`, { signal }),
      create: (body: DocumentCreateBody) => r<DocumentDetail>('/documents', { method: 'POST', body }),
      update: (id: string, body: DocumentPatchBody) => r<DocumentDetail>(`/documents/${enc(id)}`, { method: 'PATCH', body }),
      setAcl: (id: string, acl: DocAclRow[]) => r<DocAclRow[]>(`/documents/${enc(id)}/acl`, { method: 'PUT', body: acl }),
      remove: (id: string) => r<void>(`/documents/${enc(id)}`, { method: 'DELETE' }),
      contentUrl: (id: string) => documentContentUrl(id),
    },
  };
}
