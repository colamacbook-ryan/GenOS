/** Hợp đồng API giai đoạn 3 · Bản đồ quan hệ (docs/api/phase-3-graph.md). */
import type { ApiClient } from './client';
import type { PersonType } from './p3-core';
import type { DirRelation } from './p3-relations';
import type { ChannelType as Channel, GroupKind } from './phase2';

export type GraphHeatBand = 'high' | 'mid' | 'cold';
export type GraphValueBand = 'high' | 'mid' | 'low';
export type GraphState = 'active' | 'cold';

// ─── Chế độ 1 — Danh sách ────────────────────────────────────────────────────
export interface GraphListItem {
  id: string;
  code: string;
  name: string;
  type: PersonType | null;
  org_name: string | null;
  relation: DirRelation | string;
  channels: Channel[];
  heat: number | null;
  potential: number | null;
  risk: number | null;
  owner_user_id: string | null;
  last_interaction_at: string | null;
  state: GraphState;
  /** Tải quan hệ (PLAN §3.5): số cạnh (mọi `kind`) người này là một đầu, và tổng trọng số các cạnh đó. */
  degree: number;
  total_weight: number;
  /** Trọng số cạnh `bridges` của người đó — 0 nếu không phải cầu nối. */
  bridge_score: number;
}
export interface GraphListQuery {
  type?: PersonType | '';
  channel?: Channel | '';
  heat?: GraphHeatBand | '';
  potential?: GraphValueBand | '';
  risk?: GraphValueBand | '';
  owner_user_id?: string;
  state?: GraphState | '';
  relation?: DirRelation | string | '';
  cursor?: string;
  limit?: number;
}
export interface GraphListPage {
  items: GraphListItem[];
  next_cursor: string | null;
  total: number;
}

// ─── Chế độ 2/3 — đồ thị node-cạnh (Người↔Người, Nhóm↔Nhóm, và Luồng chủ đề chi tiết) ─
export interface GraphPersonNode {
  id: string;
  code: string;
  name: string;
  type: PersonType | null;
}
export interface GraphPersonEdge {
  from: string;
  to: string;
  weight: number;
  interactions: number;
  last_at: string | null;
  state: GraphState;
  topic: string | null;
}
export interface GraphGroupNode {
  id: string;
  code: string;
  name: string;
  kind: GroupKind | string;
  member_count: number;
}
export interface GraphGroupEdge {
  from: string;
  to: string;
  weight: number;
  interactions: number;
  last_at: string | null;
  state: GraphState;
  /** Mã người có cạnh `bridges` tới cả hai nhóm của cạnh này — cầu nối hiện ngay trong Nhóm↔Nhóm. */
  bridge_person_codes: string[];
}
export interface GraphResult<TNode, TEdge> {
  nodes: TNode[];
  edges: TEdge[];
  node_limit: number;
  total_edges: number;
  truncated: boolean;
  /** Chỉ có khi `truncated`. */
  hint?: string;
}
export type GraphPeopleResult = GraphResult<GraphPersonNode, GraphPersonEdge>;
export type GraphGroupsResult = GraphResult<GraphGroupNode, GraphGroupEdge>;
export interface GraphEdgeQuery {
  node_id?: string;
  min_weight?: number;
  node_limit?: number;
}

// ─── Chế độ 4 — Luồng chủ đề ──────────────────────────────────────────────────
export interface GraphTopicItem {
  topic: string;
  edges: number;
  people: number;
  total_weight: number;
  last_at: string | null;
  state: GraphState;
}
export interface GraphTopicsPage {
  items: GraphTopicItem[];
}

// ─── Vị trí node đã lưu ───────────────────────────────────────────────────────
export type GraphLayoutMode = 'people' | 'groups' | 'topics';
export interface GraphPosition {
  x: number;
  y: number;
}
export interface GraphLayout {
  positions: Record<string, GraphPosition>;
}
export interface GraphLayoutBody {
  positions: Record<string, GraphPosition>;
}

// ─── Dựng lại đồ thị ──────────────────────────────────────────────────────────
export interface GraphRecomputeCounts {
  interacts: number;
  shares_members: number;
  owns: number;
  bridges: number;
}
export interface GraphRecomputeResult {
  ok: boolean;
  counts: GraphRecomputeCounts;
}

type Q = Record<string, string | number | boolean | null | undefined>;
const enc = encodeURIComponent;

/** Endpoint của cụm — gộp vào `createEndpoints` dưới khoá `graph` (vd `api.graph.people(...)`). */
export function graphEndpoints(r: ApiClient['request']) {
  return {
    list: (q: GraphListQuery = {}, signal?: AbortSignal) => r<GraphListPage>('/graph/list', { query: q as Q, signal }),
    people: (q: GraphEdgeQuery = {}, signal?: AbortSignal) => r<GraphPeopleResult>('/graph/people', { query: q as Q, signal }),
    groups: (q: GraphEdgeQuery = {}, signal?: AbortSignal) => r<GraphGroupsResult>('/graph/groups', { query: q as Q, signal }),
    topics: {
      list: (q: { limit?: number } = {}, signal?: AbortSignal) => r<GraphTopicsPage>('/graph/topics', { query: q as Q, signal }),
      get: (topic: string, q: { node_limit?: number } = {}, signal?: AbortSignal) =>
        r<GraphPeopleResult>(`/graph/topics/${enc(topic)}`, { query: q as Q, signal }),
    },
    layout: {
      get: (mode: GraphLayoutMode, signal?: AbortSignal) => r<GraphLayout>(`/graph/layout/${enc(mode)}`, { signal }),
      put: (mode: GraphLayoutMode, body: GraphLayoutBody) => r<{ ok: boolean }>(`/graph/layout/${enc(mode)}`, { method: 'PUT', body }),
    },
    recompute: () => r<GraphRecomputeResult>('/graph/recompute', { method: 'POST' }),
  };
}
