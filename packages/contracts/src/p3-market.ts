/** Hợp đồng API giai đoạn 3 · Cơ hội & Thị trường (docs/api/phase-3-market.md). */
import type { ApiClient } from './client';
import type { EvidenceRef, GroupRef, PersonRef, UserRef } from './p3-core';

// ─── Bảng cơ hội (opportunity) ───────────────────────────────────────────────
/** 9 giai đoạn (`gh.biz.market.service.STAGES`); `won|lost|dormant` là đóng, không tính vào pipeline đang mở. */
export type OppStage = 'raw_signal' | 'validated' | 'matched' | 'approaching' | 'negotiating' | 'handed_off' | 'won' | 'lost' | 'dormant';
export const OPP_STAGES: OppStage[] = ['raw_signal', 'validated', 'matched', 'approaching', 'negotiating', 'handed_off', 'won', 'lost', 'dormant'];
export const OPP_CLOSED_STAGES: OppStage[] = ['won', 'lost', 'dormant'];
export type OppConfidence = 'high' | 'medium' | 'low';

export interface OppSuggestedMatch {
  item: string;
  score: number;
  person: PersonRef | null;
  group: GroupRef | null;
}
export interface Opportunity {
  id: string;
  code: string;
  need: string;
  stage: OppStage;
  value_vnd: number | null;
  confidence: OppConfidence;
  heat: number | null;
  person: PersonRef | null;
  group: GroupRef | null;
  owner: UserRef | null;
  first_signal_at: string;
  first_contact_at: string | null;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
  suggested_match: OppSuggestedMatch | null;
  risk_note: string | null;
}
export interface OpportunityStageHistoryItem {
  from_stage: OppStage | null;
  to_stage: OppStage;
  actor: string | null;
  at: string;
}
export interface OpportunityDetail extends Opportunity {
  stage_history: OpportunityStageHistoryItem[];
}
export interface OpportunityPage {
  items: Opportunity[];
  next_cursor: string | null;
  total: number;
}
export interface OpportunityQuery {
  stage?: OppStage;
  owner_user_id?: string;
  confidence?: OppConfidence;
  cursor?: string;
  limit?: number;
}
export interface OpportunityCreateBody {
  person_id: string;
  need: string;
  value_vnd?: number | null;
  confidence?: OppConfidence;
}
export interface OpportunityPipelineStage {
  stage: OppStage;
  count: number;
  value_vnd: number;
}
export interface OpportunityPipeline {
  stages: OpportunityPipelineStage[];
  open_pipeline_value_vnd: number;
  open_pipeline_count: number;
}

// ─── Cung ↔ Cầu (supply) ──────────────────────────────────────────────────────
export type SignalSide = 'demand' | 'supply';
export type SignalStatus = 'open' | 'matched' | 'closed' | 'ignored';
export interface MarketSignal {
  id: string;
  side: SignalSide;
  item: string;
  category: string | null;
  quantity: number | null;
  unit: string | null;
  value_vnd: number | null;
  location: string | null;
  needed_by: string | null;
  heat: number | null;
  status: SignalStatus;
  created_at: string;
  person: PersonRef | null;
  group: GroupRef | null;
}
export type MatchStatus = 'suggested' | 'introduced' | 'accepted' | 'rejected';
export interface SignalMatchRef {
  id: string;
  score: number;
  reasons: string[];
  status: MatchStatus;
  item: string;
  person: PersonRef | null;
  group: GroupRef | null;
}
export interface MarketSignalDetail extends MarketSignal {
  matches: SignalMatchRef[];
}
export interface MarketSignalPage {
  items: MarketSignal[];
  next_cursor: string | null;
  total: number;
}
export interface MarketSignalQuery {
  side?: SignalSide;
  status?: SignalStatus;
  category?: string;
  cursor?: string;
  limit?: number;
}

export interface MatchSideRef {
  id: string;
  item: string;
  person: PersonRef | null;
  group: GroupRef | null;
}
export interface Match {
  id: string;
  score: number;
  reasons: string[];
  status: MatchStatus;
  opportunity_id: string | null;
  created_at: string;
  demand: MatchSideRef;
  supply: MatchSideRef;
}
export interface MatchPage {
  items: Match[];
  next_cursor: string | null;
  total: number;
}
export interface MatchQuery {
  status?: MatchStatus;
  min_score?: number;
  cursor?: string;
  limit?: number;
}
export interface MatchIntroduceResult {
  ok: true;
  opportunity_id: string;
  draft: { id: string | null; code: string | null; status: string | null; outcome: string; hold_reason: string | null };
}

// ─── Kho hội thoại (search) ───────────────────────────────────────────────────
export interface SearchPersonItem {
  person: PersonRef;
  match_count: number;
  last_at: string;
  last_snippet: string | null;
  last_event_type: string;
  evidence: EvidenceRef;
}
export interface SearchFacetValue {
  value: string;
  count: number;
}
export interface SearchPage {
  items: SearchPersonItem[];
  next_cursor: string | null;
  total: number;
  facets: { event_type: SearchFacetValue[]; channel: SearchFacetValue[] };
}
export interface SearchQuery {
  q?: string;
  event_type?: string;
  channel?: string;
  date_from?: string;
  date_to?: string;
  cursor?: string;
  limit?: number;
}
export type SearchBulkAction = 'tag' | 'task';
export interface SearchBulkBody {
  person_ids: string[];
  action: SearchBulkAction;
  text: string;
  due_at?: string | null;
  priority?: string;
}
export interface SearchBulkResult {
  ok: true;
  count: number;
}

// ─── Deal & Vụ việc (deals) ───────────────────────────────────────────────────
export type DealStatus = 'open' | 'won' | 'lost';
export interface Deal {
  id: string;
  code: string;
  opportunity_id: string | null;
  person: PersonRef | null;
  amount_vnd: number;
  status: DealStatus;
  won_at: string | null;
  erp_ref: string | null;
  created_at: string;
  updated_at: string;
}
export interface DealPage {
  items: Deal[];
  next_cursor: string | null;
  total: number;
}
export interface DealQuery {
  status?: DealStatus;
  person_id?: string;
  cursor?: string;
  limit?: number;
}
export interface DealCreateBody {
  person_id: string;
  amount_vnd: number;
  opportunity_id?: string;
  erp_ref?: string | null;
}
export interface DealPatchBody {
  status?: DealStatus;
  amount_vnd?: number;
  erp_ref?: string | null;
}

export type CaseKind = 'complaint';
export type CaseStatus = 'open' | 'in_progress' | 'resolved' | 'closed';
export type CasePriority = 'P1' | 'P2' | 'P3';
export interface CaseItem {
  id: string;
  code: string;
  kind: CaseKind;
  priority: CasePriority;
  title: string;
  status: CaseStatus;
  assignee: UserRef | null;
  subject: (PersonRef | GroupRef) | null;
  opened_at: string;
  resolved_at: string | null;
  updated_at: string;
}
export interface CasePage {
  items: CaseItem[];
  next_cursor: string | null;
  total: number;
}
export interface CaseQuery {
  status?: CaseStatus;
  assignee_user_id?: string;
  priority?: CasePriority;
  cursor?: string;
  limit?: number;
}
export interface CaseCreateBody {
  title: string;
  priority?: CasePriority;
  subject?: { type: 'person' | 'group'; id: string };
  assignee_user_id?: string;
}
export interface CasePatchBody {
  status?: CaseStatus;
  assignee_user_id?: string | null;
  priority?: CasePriority;
}

type Q = Record<string, string | number | boolean | null | undefined>;
const enc = encodeURIComponent;

/** Endpoint của cụm — gộp vào `createEndpoints` dưới khoá `market` (vd `api.market.opportunities.list(...)`). */
export function marketEndpoints(r: ApiClient['request']) {
  return {
    opportunities: {
      list: (q: OpportunityQuery = {}, signal?: AbortSignal) => r<OpportunityPage>('/opportunities', { query: q as Q, signal }),
      pipeline: (signal?: AbortSignal) => r<OpportunityPipeline>('/opportunities/pipeline', { signal }),
      get: (id: string, signal?: AbortSignal) => r<OpportunityDetail>(`/opportunities/${enc(id)}`, { signal }),
      create: (body: OpportunityCreateBody) => r<OpportunityDetail>('/opportunities', { method: 'POST', body }),
      setStage: (id: string, to_stage: OppStage) => r<OpportunityDetail>(`/opportunities/${enc(id)}/stage`, { method: 'PATCH', body: { to_stage } }),
    },
    supply: {
      list: (q: MarketSignalQuery = {}, signal?: AbortSignal) => r<MarketSignalPage>('/supply', { query: q as Q, signal }),
      get: (id: string, signal?: AbortSignal) => r<MarketSignalDetail>(`/supply/${enc(id)}`, { signal }),
    },
    matches: {
      list: (q: MatchQuery = {}, signal?: AbortSignal) => r<MatchPage>('/matches', { query: q as Q, signal }),
      recompute: () => r<{ ok: true; matches: number }>('/matches/recompute', { method: 'POST' }),
      introduce: (id: string) => r<MatchIntroduceResult>(`/matches/${enc(id)}/introduce`, { method: 'POST' }),
      reject: (id: string) => r<{ ok: true }>(`/matches/${enc(id)}/reject`, { method: 'POST' }),
    },
    search: {
      query: (q: SearchQuery = {}, signal?: AbortSignal) => r<SearchPage>('/search', { query: q as Q, signal }),
      bulk: (body: SearchBulkBody) => r<SearchBulkResult>('/search/bulk', { method: 'POST', body }),
    },
    deals: {
      list: (q: DealQuery = {}, signal?: AbortSignal) => r<DealPage>('/deals', { query: q as Q, signal }),
      get: (id: string, signal?: AbortSignal) => r<Deal>(`/deals/${enc(id)}`, { signal }),
      create: (body: DealCreateBody) => r<Deal>('/deals', { method: 'POST', body }),
      update: (id: string, body: DealPatchBody) => r<Deal>(`/deals/${enc(id)}`, { method: 'PATCH', body }),
    },
    cases: {
      list: (q: CaseQuery = {}, signal?: AbortSignal) => r<CasePage>('/cases', { query: q as Q, signal }),
      get: (id: string, signal?: AbortSignal) => r<CaseItem>(`/cases/${enc(id)}`, { signal }),
      create: (body: CaseCreateBody) => r<CaseItem>('/cases', { method: 'POST', body }),
      update: (id: string, body: CasePatchBody) => r<CaseItem>(`/cases/${enc(id)}`, { method: 'PATCH', body }),
    },
  };
}
