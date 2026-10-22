/**
 * Mock API giai đoạn 3 · Bản đồ quan hệ (docs/api/phase-3-graph.md). `handle` trả true khi đã trả lời request.
 *
 * Cùng quy ước `clean.relationships` mà `gh.biz.graph.jobs.recompute_org` là "nguồn dữ liệu duy nhất của toàn
 * màn" (docs/api/phase-3-graph.md): mock này cũng có đúng MỘT mảng `EDGES` — bốn `kind` (`interacts`,
 * `shares_members`, `owns`, `bridges`) — và mọi chế độ (danh sách, Người↔Người, Nhóm↔Nhóm, Luồng chủ đề) đều
 * chỉ lọc/gộp từ đó, không có số liệu rời rạc nào khác.
 *
 * Con người/nhóm dùng lại đúng `id`/`code`/`name` của `mock-p3-relations.ts` (cùng vai, cùng roster) để bấm một
 * dòng ở đây mở đúng Hồ sơ sống của cụm `relations`.
 */
import type { GraphGroupEdge, GraphGroupNode, GraphListItem, GraphPersonEdge, GraphPersonNode, GraphState, PersonType } from '@gen-harness/contracts';
import { USER_IDS, rejectNonUuid } from './mock-ids';
import type { P2Ctx } from './mock-phase2';

export interface P3Options {
  fresh: boolean;
  emit: (type: string, data: unknown) => void;
}

const MAX_NODES = 200;
const DAY = 24 * 60 * 60 * 1000;
const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();
const has = (ctx: P2Ctx, perm: string) => !!ctx.perms[perm] && ctx.perms[perm] !== 'none';
const edgeState = (lastAt: string | null): GraphState => (!lastAt || Date.now() - new Date(lastAt).getTime() > 30 * DAY ? 'cold' : 'active');

// ─── Roster (cùng id/code/tên `mock-p3-relations.ts` để cùng mở được Hồ sơ sống) ─────────────────────────────
interface PersonSeed {
  id: string; code: string; name: string; org: string; type: PersonType; channels: string[]; relation: string;
  heat: number; potential: number | null; risk: number | null; ownerUserId: string | null; lastAt: string;
}
const PEOPLE: PersonSeed[] = [
  { id: 'p-hau', code: 'PER-0311', name: 'Trần Văn Hậu', org: 'Xưởng gỗ Bình Dương', type: 'customer', channels: ['zalo'], relation: 'direct', heat: 91, potential: 88, risk: 22, ownerUserId: null, lastAt: ago(18) },
  { id: 'p-bao', code: 'PER-0042', name: 'Nguyễn Văn Bảo', org: 'Công ty in Thành Phát', type: 'customer', channels: ['zalo', 'whatsapp'], relation: 'direct', heat: 87, potential: 58, risk: 84, ownerUserId: USER_IDS.lan, lastAt: ago(120) },
  { id: 'p-duoc', code: 'PER-0402', name: 'Lâm Văn Được', org: 'Kho ván Bình Dương', type: 'supplier', channels: ['zalo'], relation: 'stranger', heat: 84, potential: null, risk: null, ownerUserId: null, lastAt: ago(60 * 24 * 5) },
  { id: 'p-lan', code: 'PER-0119', name: 'Hoàng Thị Lan', org: 'An Khang Logistics', type: 'partner', channels: ['zalo', 'whatsapp'], relation: 'via_staff', heat: 78, potential: 61, risk: 52, ownerUserId: USER_IDS.minh, lastAt: ago(60 * 4) },
  { id: 'p-son', code: 'PER-0619', name: 'Võ Thanh Sơn', org: 'Ứng viên · Key Account ngành lạnh', type: 'candidate', channels: ['zalo'], relation: 'stranger', heat: 74, potential: null, risk: null, ownerUserId: null, lastAt: ago(60 * 6) },
  { id: 'p-minh', code: 'PER-0512', name: 'Phạm Quốc Minh', org: 'Nội thất Minh Long', type: 'customer', channels: ['zalo'], relation: 'via_staff', heat: 64, potential: 55, risk: 48, ownerUserId: USER_IDS.minh, lastAt: ago(60 * 24) },
  { id: 'p-bich', code: 'PER-0844', name: 'Lê Thị Bích', org: 'Kho lạnh Tân Cảng', type: 'customer', channels: ['whatsapp'], relation: 'stranger', heat: 58, potential: null, risk: 18, ownerUserId: null, lastAt: ago(60 * 24 * 2) },
  { id: 'p-ha', code: 'PER-0007', name: 'Nguyễn Thu Hà', org: 'Nội bộ · Trưởng ban Tài chính', type: 'staff', channels: ['zalo', 'whatsapp'], relation: 'staff', heat: 62, potential: null, risk: null, ownerUserId: null, lastAt: ago(35) },
  { id: 'p-khoa', code: 'PER-0003', name: 'Trần Minh Khoa', org: 'Nội bộ · Giám đốc vận hành', type: 'staff', channels: ['zalo', 'whatsapp'], relation: 'staff', heat: 71, potential: null, risk: null, ownerUserId: null, lastAt: ago(12) },
  { id: 'p-thang', code: 'PER-0733', name: 'Bùi Đức Thắng', org: 'Gỗ Trường Thành Mới', type: 'customer', channels: ['zalo'], relation: 'stranger', heat: 72, potential: 70, risk: 45, ownerUserId: null, lastAt: ago(60 * 24 * 3) },
  { id: 'p-duyen', code: 'PER-0951', name: 'Trịnh Mỹ Duyên', org: 'Bao bì Sài Gòn Mới', type: 'customer', channels: ['zalo', 'whatsapp'], relation: 'via_staff', heat: 18, potential: 30, risk: 78, ownerUserId: USER_IDS.minh, lastAt: ago(60 * 24 * 41) },
  { id: 'p-tri', code: 'PER-0688', name: 'Đặng Hữu Trí', org: 'Gỗ Đông Phương', type: 'customer', channels: ['zalo'], relation: 'via_staff', heat: 22, potential: 35, risk: 74, ownerUserId: USER_IDS.minh, lastAt: ago(60 * 24 * 74) },
];
const P = Object.fromEntries(PEOPLE.map((p) => [p.id, p]));

interface GroupSeed { id: string; code: string; name: string; kind: string; memberCount: number }
const GROUPS: GroupSeed[] = [
  { id: 'g-zl-0114', code: 'GRP-ZL-0114', name: 'Vận hành Genesis — Quý 4', kind: 'internal', memberCount: 24 },
  { id: 'g-zl-0231', code: 'GRP-ZL-0231', name: 'Group Ngành gỗ Miền Nam', kind: 'market', memberCount: 412 },
  { id: 'g-zl-0174', code: 'GRP-ZL-0174', name: 'Ban Tài chính', kind: 'internal', memberCount: 11 },
  { id: 'g-zl-0356', code: 'GRP-ZL-0356', name: 'Group Nhân sự Logistics', kind: 'market', memberCount: 286 },
  { id: 'g-zl-0489', code: 'GRP-ZL-0489', name: 'Đối tác in ấn Thành Phát', kind: 'partner', memberCount: 4 },
  { id: 'g-zl-0502', code: 'GRP-ZL-0502', name: 'Truyền thông & Sự kiện', kind: 'internal', memberCount: 13 },
  { id: 'g-wa-0007', code: 'GRP-WA-0007', name: 'Điều hành mở rộng', kind: 'internal', memberCount: 6 },
  { id: 'g-wa-0011', code: 'GRP-WA-0011', name: 'Kho lạnh Tân Cảng', kind: 'customer', memberCount: 3 },
  { id: 'g-li-0001', code: 'GRP-LI-0001', name: 'Mạng đối tác ngành gỗ', kind: 'market', memberCount: 128 },
];
const G = Object.fromEntries(GROUPS.map((g) => [g.id, g]));

// ─── nguồn dữ liệu duy nhất: `clean.relationships` ────────────────────────────────────────────────────────
type EdgeKind = 'interacts' | 'shares_members' | 'owns' | 'bridges';
interface EdgeRow {
  fromType: 'person' | 'group'; fromId: string; toType: 'person' | 'group'; toId: string; kind: EdgeKind;
  weight: number; interactions: number; lastAt: string | null; topic: string | null;
}
function edges(fresh: boolean): EdgeRow[] {
  if (fresh) return [];
  return [
    // interacts (person↔person)
    { fromType: 'person', fromId: 'p-ha', toType: 'person', toId: 'p-bao', kind: 'interacts', weight: 42.5, interactions: 14, lastAt: ago(120), topic: 'hợp đồng in ấn quý 4' },
    { fromType: 'person', fromId: 'p-khoa', toType: 'person', toId: 'p-lan', kind: 'interacts', weight: 35.0, interactions: 11, lastAt: ago(240), topic: 'tuyến vận chuyển lạnh' },
    { fromType: 'person', fromId: 'p-khoa', toType: 'person', toId: 'p-hau', kind: 'interacts', weight: 51.0, interactions: 20, lastAt: ago(18), topic: 'ván MDF E1' },
    { fromType: 'person', fromId: 'p-khoa', toType: 'person', toId: 'p-duoc', kind: 'interacts', weight: 18.5, interactions: 6, lastAt: ago(60 * 24 * 5), topic: 'ván MDF E1' },
    { fromType: 'person', fromId: 'p-khoa', toType: 'person', toId: 'p-minh', kind: 'interacts', weight: 24.0, interactions: 8, lastAt: ago(60 * 24), topic: 'báo giá nội thất' },
    { fromType: 'person', fromId: 'p-ha', toType: 'person', toId: 'p-khoa', kind: 'interacts', weight: 30.0, interactions: 40, lastAt: ago(12), topic: null },
    { fromType: 'person', fromId: 'p-ha', toType: 'person', toId: 'p-bich', kind: 'interacts', weight: 12.0, interactions: 5, lastAt: ago(60 * 24 * 2), topic: null },
    { fromType: 'person', fromId: 'p-khoa', toType: 'person', toId: 'p-son', kind: 'interacts', weight: 9.0, interactions: 3, lastAt: ago(60 * 6), topic: null },
    { fromType: 'person', fromId: 'p-khoa', toType: 'person', toId: 'p-thang', kind: 'interacts', weight: 22.0, interactions: 9, lastAt: ago(60 * 24 * 3), topic: 'báo giá gỗ trường thành' },
    { fromType: 'person', fromId: 'p-ha', toType: 'person', toId: 'p-duyen', kind: 'interacts', weight: 6.0, interactions: 2, lastAt: ago(60 * 24 * 41), topic: 'bao bì tuỳ chỉnh' },
    { fromType: 'person', fromId: 'p-khoa', toType: 'person', toId: 'p-tri', kind: 'interacts', weight: 5.0, interactions: 2, lastAt: ago(60 * 24 * 74), topic: 'ván ép công nghiệp' },
    // shares_members (group↔group)
    { fromType: 'group', fromId: 'g-zl-0114', toType: 'group', toId: 'g-zl-0231', kind: 'shares_members', weight: 0.35, interactions: 9, lastAt: ago(30), topic: null },
    { fromType: 'group', fromId: 'g-zl-0114', toType: 'group', toId: 'g-zl-0174', kind: 'shares_members', weight: 0.45, interactions: 6, lastAt: ago(60), topic: null },
    { fromType: 'group', fromId: 'g-zl-0114', toType: 'group', toId: 'g-zl-0356', kind: 'shares_members', weight: 0.12, interactions: 3, lastAt: ago(200), topic: null },
    { fromType: 'group', fromId: 'g-zl-0114', toType: 'group', toId: 'g-zl-0489', kind: 'shares_members', weight: 0.5, interactions: 3, lastAt: ago(120), topic: null },
    { fromType: 'group', fromId: 'g-zl-0114', toType: 'group', toId: 'g-wa-0007', kind: 'shares_members', weight: 0.08, interactions: 2, lastAt: ago(60 * 24 * 40), topic: null },
    { fromType: 'group', fromId: 'g-zl-0231', toType: 'group', toId: 'g-zl-0356', kind: 'shares_members', weight: 0.1, interactions: 2, lastAt: ago(300), topic: null },
    { fromType: 'group', fromId: 'g-zl-0174', toType: 'group', toId: 'g-zl-0489', kind: 'shares_members', weight: 0.15, interactions: 2, lastAt: ago(180), topic: null },
    { fromType: 'group', fromId: 'g-zl-0114', toType: 'group', toId: 'g-zl-0502', kind: 'shares_members', weight: 0.2, interactions: 4, lastAt: ago(500), topic: null },
    { fromType: 'group', fromId: 'g-zl-0114', toType: 'group', toId: 'g-wa-0011', kind: 'shares_members', weight: 0.05, interactions: 1, lastAt: ago(60 * 24 * 35), topic: null },
    // owns (person→group, admin)
    { fromType: 'person', fromId: 'p-khoa', toType: 'group', toId: 'g-zl-0114', kind: 'owns', weight: 45, interactions: 45, lastAt: ago(12), topic: null },
    { fromType: 'person', fromId: 'p-ha', toType: 'group', toId: 'g-zl-0174', kind: 'owns', weight: 30, interactions: 30, lastAt: ago(35), topic: null },
    // bridges (person→group; cùng người → cùng trọng số = số cặp nhóm người đó bắc cầu)
    { fromType: 'person', fromId: 'p-khoa', toType: 'group', toId: 'g-zl-0114', kind: 'bridges', weight: 1, interactions: 1, lastAt: ago(12), topic: null },
    { fromType: 'person', fromId: 'p-khoa', toType: 'group', toId: 'g-zl-0231', kind: 'bridges', weight: 1, interactions: 1, lastAt: ago(18), topic: null },
    { fromType: 'person', fromId: 'p-ha', toType: 'group', toId: 'g-zl-0114', kind: 'bridges', weight: 3, interactions: 3, lastAt: ago(35), topic: null },
    { fromType: 'person', fromId: 'p-ha', toType: 'group', toId: 'g-zl-0174', kind: 'bridges', weight: 3, interactions: 3, lastAt: ago(35), topic: null },
    { fromType: 'person', fromId: 'p-ha', toType: 'group', toId: 'g-zl-0489', kind: 'bridges', weight: 3, interactions: 3, lastAt: ago(120), topic: null },
  ];
}

/** Chọn top cạnh theo trọng số cho tới khi vừa khít `nodeLimit` node — cùng thuật toán `build_graph`
 * (`gh.biz.graph.service`): không bao giờ cắt ngẫu nhiên, luôn ưu tiên trọng số cao nhất. */
function buildGraph<E>(rows: E[], endpoints: (e: E) => [string, string], weightOf: (e: E) => number, nodeLimit: number) {
  const sorted = [...rows].sort((a, b) => weightOf(b) - weightOf(a));
  const allIds = new Set(rows.flatMap((e) => endpoints(e)));
  const included = new Set<string>();
  const kept: E[] = [];
  for (const e of sorted) {
    const [a, b] = endpoints(e);
    const addCount = (included.has(a) ? 0 : 1) + (included.has(b) ? 0 : 1);
    if (included.size + addCount > nodeLimit) continue;
    included.add(a);
    included.add(b);
    kept.push(e);
  }
  return { kept, nodeIds: included, truncated: allIds.size > nodeLimit };
}

function scopeQuery(url: URL) {
  return {
    nodeId: url.searchParams.get('node_id') || null,
    minWeight: Number(url.searchParams.get('min_weight') ?? 0) || 0,
    nodeLimit: Math.min(MAX_NODES, Number(url.searchParams.get('node_limit') ?? MAX_NODES) || MAX_NODES),
  };
}

export function createMock(opts: P3Options) {
  let EDGES = edges(opts.fresh);

  function listRow(p: PersonSeed): GraphListItem {
    const my = EDGES.filter((e) => (e.fromType === 'person' && e.fromId === p.id) || (e.toType === 'person' && e.toId === p.id));
    const degree = my.length;
    const totalWeight = my.reduce((s, e) => s + e.weight, 0);
    const bridgeScore = Math.max(0, ...my.filter((e) => e.kind === 'bridges' && e.fromId === p.id).map((e) => e.weight), 0);
    return {
      id: p.id, code: p.code, name: p.name, type: p.type, org_name: p.org, relation: p.relation as GraphListItem['relation'],
      channels: p.channels as GraphListItem['channels'], heat: p.heat, potential: p.potential, risk: p.risk,
      owner_user_id: p.ownerUserId, last_interaction_at: p.lastAt, state: edgeState(p.lastAt),
      degree, total_weight: Number(totalWeight.toFixed(2)), bridge_score: bridgeScore,
    };
  }

  function peopleGraph(url: URL, extraTopic?: string | null) {
    const { nodeId, minWeight, nodeLimit } = scopeQuery(url);
    let rows = EDGES.filter((e) => e.kind === 'interacts');
    if (nodeId) rows = rows.filter((e) => e.fromId === nodeId || e.toId === nodeId);
    if (minWeight) rows = rows.filter((e) => e.weight >= minWeight);
    if (extraTopic) rows = rows.filter((e) => e.topic === extraTopic);
    const totalEdges = rows.length;
    const { kept, nodeIds, truncated } = buildGraph(rows, (e) => [e.fromId, e.toId], (e) => e.weight, nodeLimit);
    const nodes: GraphPersonNode[] = [...nodeIds].map((id) => {
      const p = P[id];
      return { id: p.id, code: p.code, name: p.name, type: p.type };
    });
    const outEdges: GraphPersonEdge[] = kept.map((e) => ({
      from: e.fromId, to: e.toId, weight: e.weight, interactions: e.interactions, last_at: e.lastAt, state: edgeState(e.lastAt), topic: e.topic,
    }));
    const out: { nodes: GraphPersonNode[]; edges: GraphPersonEdge[]; node_limit: number; total_edges: number; truncated: boolean; hint?: string } = {
      nodes, edges: outEdges, node_limit: nodeLimit, total_edges: totalEdges, truncated,
    };
    if (truncated) out.hint = 'Quá nhiều node cho một lượt — đang hiển thị theo trọng số cạnh cao nhất; thu hẹp bằng node_id hoặc min_weight để thấy hết';
    return out;
  }

  function groupsGraph(url: URL) {
    const { nodeId, minWeight, nodeLimit } = scopeQuery(url);
    let rows = EDGES.filter((e) => e.kind === 'shares_members');
    if (nodeId) rows = rows.filter((e) => e.fromId === nodeId || e.toId === nodeId);
    if (minWeight) rows = rows.filter((e) => e.weight >= minWeight);
    const totalEdges = rows.length;
    const { kept, nodeIds, truncated } = buildGraph(rows, (e) => [e.fromId, e.toId], (e) => e.weight, nodeLimit);
    const nodes: GraphGroupNode[] = [...nodeIds].map((id) => {
      const g = G[id];
      return { id: g.id, code: g.code, name: g.name, kind: g.kind, member_count: g.memberCount };
    });
    const bridgesOf = (personId: string) => new Set(EDGES.filter((e) => e.kind === 'bridges' && e.fromId === personId).map((e) => e.toId));
    const bridgePeople = [...new Set(EDGES.filter((e) => e.kind === 'bridges').map((e) => e.fromId))];
    const outEdges: GraphGroupEdge[] = kept.map((e) => {
      const codes = bridgePeople.filter((pid) => bridgesOf(pid).has(e.fromId) && bridgesOf(pid).has(e.toId)).map((pid) => P[pid].code);
      return { from: e.fromId, to: e.toId, weight: e.weight, interactions: e.interactions, last_at: e.lastAt, state: edgeState(e.lastAt), bridge_person_codes: codes };
    });
    const out: { nodes: GraphGroupNode[]; edges: GraphGroupEdge[]; node_limit: number; total_edges: number; truncated: boolean; hint?: string } = {
      nodes, edges: outEdges, node_limit: nodeLimit, total_edges: totalEdges, truncated,
    };
    if (truncated) out.hint = 'Quá nhiều node cho một lượt — đang hiển thị theo trọng số cạnh cao nhất; thu hẹp bằng node_id hoặc min_weight để thấy hết';
    return out;
  }

  function topicsList() {
    const byTopic = new Map<string, EdgeRow[]>();
    for (const e of EDGES) {
      if (e.kind !== 'interacts' || !e.topic) continue;
      (byTopic.get(e.topic) ?? byTopic.set(e.topic, []).get(e.topic)!).push(e);
    }
    return [...byTopic.entries()]
      .map(([topic, rows]) => {
        const people = new Set(rows.flatMap((e) => [e.fromId, e.toId]));
        const lastAt = rows.reduce<string | null>((m, e) => (!m || (e.lastAt && e.lastAt > m) ? e.lastAt : m), null);
        const totalWeight = rows.reduce((s, e) => s + e.weight, 0);
        return { topic, edges: rows.length, people: people.size, total_weight: Number(totalWeight.toFixed(2)), last_at: lastAt, state: edgeState(lastAt) };
      })
      .sort((a, b) => b.total_weight - a.total_weight);
  }

  // Vị trí node đã lưu — `ops.saved_views` giả lập bằng Map trong tiến trình mock (tương đương autosave).
  const layouts = new Map<string, Record<string, { x: number; y: number }>>();

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p, url, body, reply, problem } = ctx;
    const seg = p.split('/').filter(Boolean);
    if (seg[0] !== 'graph') return false;
    if (!has(ctx, 'profile.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');

    if (seg[1] === 'list' && seg.length === 2 && m === 'GET') {
      const type = url.searchParams.get('type');
      const channel = url.searchParams.get('channel');
      const heat = url.searchParams.get('heat');
      const potential = url.searchParams.get('potential');
      const risk = url.searchParams.get('risk');
      const ownerUserId = url.searchParams.get('owner_user_id');
      // Như API (`owner_user_id: uuid.UUID | None`): không phải UUID → 422 VALIDATION (v0.1.35).
      if (ownerUserId && rejectNonUuid(problem, 'owner_user_id', ownerUserId)) return true;
      const state = url.searchParams.get('state');
      const relation = url.searchParams.get('relation');
      const limit = Number(url.searchParams.get('limit') ?? 50);
      let rows = PEOPLE.map(listRow);
      if (type) rows = rows.filter((r) => r.type === type);
      if (channel) rows = rows.filter((r) => r.channels.includes(channel as never));
      if (relation) rows = rows.filter((r) => r.relation === relation);
      if (heat === 'high') rows = rows.filter((r) => (r.heat ?? 0) >= 80);
      else if (heat === 'mid') rows = rows.filter((r) => (r.heat ?? 0) >= 50 && (r.heat ?? 0) < 80);
      else if (heat === 'cold') rows = rows.filter((r) => r.heat === null || r.heat < 50);
      if (potential === 'high') rows = rows.filter((r) => (r.potential ?? -1) >= 80);
      else if (potential === 'mid') rows = rows.filter((r) => (r.potential ?? -1) >= 50 && (r.potential ?? -1) < 80);
      else if (potential === 'low') rows = rows.filter((r) => r.potential === null || r.potential < 50);
      if (risk === 'high') rows = rows.filter((r) => (r.risk ?? -1) >= 80);
      else if (risk === 'mid') rows = rows.filter((r) => (r.risk ?? -1) >= 50 && (r.risk ?? -1) < 80);
      else if (risk === 'low') rows = rows.filter((r) => r.risk === null || r.risk < 50);
      if (ownerUserId) rows = rows.filter((r) => r.owner_user_id === ownerUserId);
      if (state) rows = rows.filter((r) => r.state === state);
      return reply(200, { items: rows.slice(0, limit), next_cursor: null, total: rows.length });
    }
    if (seg[1] === 'people' && seg.length === 2 && m === 'GET') return reply(200, peopleGraph(url));
    if (seg[1] === 'groups' && seg.length === 2 && m === 'GET') return reply(200, groupsGraph(url));
    if (seg[1] === 'topics' && seg.length === 2 && m === 'GET') {
      const limit = Number(url.searchParams.get('limit') ?? 50);
      return reply(200, { items: topicsList().slice(0, limit) });
    }
    if (seg[1] === 'topics' && seg.length === 3 && m === 'GET') return reply(200, peopleGraph(url, decodeURIComponent(seg[2])));
    if (seg[1] === 'layout' && seg.length === 3) {
      const mode = seg[2];
      if (!['people', 'groups', 'topics'].includes(mode)) return problem(404, 'NOT_FOUND', 'Không tồn tại');
      if (m === 'GET') return reply(200, { positions: layouts.get(mode) ?? {} });
      if (m === 'PUT') {
        if (!has(ctx, 'profile.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        const b = body as { positions?: Record<string, { x: number; y: number }> };
        layouts.set(mode, b.positions ?? {});
        return reply(200, { ok: true });
      }
    }
    if (seg[1] === 'recompute' && seg.length === 2 && m === 'POST') {
      if (!has(ctx, 'profile.write')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      EDGES = edges(false);
      const counts = {
        interacts: EDGES.filter((e) => e.kind === 'interacts').length,
        shares_members: EDGES.filter((e) => e.kind === 'shares_members').length,
        owns: EDGES.filter((e) => e.kind === 'owns').length,
        bridges: EDGES.filter((e) => e.kind === 'bridges').length,
      };
      return reply(200, { ok: true, counts });
    }
    return false;
  }

  return {
    handle,
    hooks: {
      edges: () => EDGES,
      layouts: () => layouts,
    } as Record<string, (...args: never[]) => unknown>,
    dispose: () => {},
  };
}
