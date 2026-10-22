import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import type { GraphGroupEdge, GraphGroupNode, GraphLayoutMode, GraphPersonEdge, GraphPersonNode } from '@gen-harness/contracts';
import { Button, EmptyState, FilterSelect, Icon, type FilterOption } from '@gen-harness/ui';
import { CardError, SkeletonLines } from '../common';
import { fmtInt } from '../../lib/format';
import { useUrlState } from '../../lib/uiStore';
import { GraphCanvas, type GraphCanvasEdge, type GraphCanvasNode, type GraphPos } from './GraphCanvas';
import { ACC3, ACC4, N4, N7, OK, WARN } from './graphModel';
import { useGraphGroups, useGraphLayout, useGraphList, useGraphPeople, useGraphTopic, useSaveGraphLayout } from './queries';

const MIN_WEIGHT_OPTIONS: FilterOption<string>[] = [
  { value: '', label: 'Tất cả' },
  { value: '20', label: '≥ 20' },
  { value: '50', label: '≥ 50' },
  { value: '80', label: '≥ 80' },
];

/** Tô cạnh theo phân vị trọng số trong CHÍNH tập cạnh đang hiển thị — không có thang cố định vì `interacts`
 * (tổng phút hoạt động chung) và `shares_members` (hệ số chồng lấp 0–1) là hai đơn vị khác hẳn nhau. */
function edgeTone(weight: number, maxWeight: number, cold: boolean, bridge: boolean): string {
  if (bridge) return ACC3;
  if (cold) return N7;
  const r = maxWeight > 0 ? weight / maxWeight : 0;
  return r >= 0.66 ? OK : r >= 0.33 ? WARN : N4;
}
function edgeWidth(weight: number, maxWeight: number, bridge: boolean): number {
  const r = maxWeight > 0 ? Math.min(1, weight / maxWeight) : 0;
  return (bridge ? 2 : 1) + 3 * r;
}

interface StatRow {
  key: string;
  name: string;
  value: string;
  tone: string;
}
interface Derived {
  nodes: GraphCanvasNode[];
  edges: GraphCanvasEdge[];
  statTitle: string;
  stats: StatRow[];
  insights: string[];
}

/** Nhận xét rút ra thẳng từ dữ liệu đang hiển thị (không hardcode câu chuyện — mục 2 "Định nghĩa xong" của
 * PLAN cấm hardcode dữ liệu), khác thiết kế tĩnh vốn có sẵn vài câu nhận định cố định không tính lại được. */
function insightsFor(nodeCount: number, edgeCount: number, isolated: number, truncated: boolean, hint: string | undefined, coldEdges: number): string[] {
  const out: string[] = [];
  if (truncated && hint) out.push(hint);
  if (edgeCount === 0) return [...out, 'Chưa có cạnh nào khớp bộ lọc hiện tại.'];
  if (coldEdges > 0) out.push(`${fmtInt(coldEdges)} trên ${fmtInt(edgeCount)} cạnh đang lạnh (không chạm trong hơn 30 ngày).`);
  if (isolated > 0) out.push(`${fmtInt(isolated)} node hiển thị không còn cạnh nào đang hoạt động.`);
  out.push(`${fmtInt(nodeCount)} node · ${fmtInt(edgeCount)} cạnh đang hiển thị.`);
  return out;
}

function buildPeople(nodes: GraphPersonNode[], edges: GraphPersonEdge[], bridgeScores: Map<string, number>, truncated: boolean, hint?: string): Derived {
  const maxW = edges.reduce((m, e) => Math.max(m, e.weight), 0);
  const degree = new Map<string, { n: number; w: number }>();
  for (const e of edges) {
    for (const id of [e.from, e.to]) {
      const cur = degree.get(id) ?? { n: 0, w: 0 };
      cur.n += 1;
      cur.w += e.weight;
      degree.set(id, cur);
    }
  }
  const canvasNodes: GraphCanvasNode[] = nodes.map((n) => {
    const d = degree.get(n.id) ?? { n: 0, w: 0 };
    const bridging = (bridgeScores.get(n.id) ?? 0) > 0;
    return {
      id: n.id,
      name: n.name,
      sub: `${n.code} · ${fmtInt(d.n)} quan hệ`,
      icon: 'ph ph-user',
      tone: bridging ? ACC3 : d.n === 0 ? N4 : ACC4,
      radius: Math.max(12, Math.min(26, 12 + d.n * 2.4)),
      muted: d.n === 0,
    };
  });
  const canvasEdges: GraphCanvasEdge[] = edges.map((e, i) => {
    const cold = e.state === 'cold';
    return {
      id: `${e.from}-${e.to}-${i}`,
      from: e.from,
      to: e.to,
      width: edgeWidth(e.weight, maxW, false),
      color: edgeTone(e.weight, maxW, cold, false),
      opacity: cold ? 0.5 : 0.85,
      title: `${e.topic ? e.topic + ' · ' : ''}trọng số ${e.weight.toFixed(1)} · ${fmtInt(e.interactions)} lượt${cold ? ' · đang lạnh' : ''}`,
    };
  });
  const topBridges = [...bridgeScores.entries()]
    .filter(([id, v]) => v > 0 && nodes.some((n) => n.id === id))
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);
  const stats: StatRow[] =
    topBridges.length > 0
      ? topBridges.map(([id, v]) => ({ key: id, name: nodes.find((n) => n.id === id)?.name ?? id, value: `cầu nối · trọng số ${v.toFixed(1)}`, tone: ACC3 }))
      : [...degree.entries()]
          .sort((a, b) => b[1].w - a[1].w)
          .slice(0, 6)
          .map(([id, d]) => ({ key: id, name: nodes.find((n) => n.id === id)?.name ?? id, value: `${fmtInt(d.n)} quan hệ · trọng số ${d.w.toFixed(1)}`, tone: N4 }));
  const isolated = nodes.filter((n) => !degree.has(n.id)).length;
  return {
    nodes: canvasNodes,
    edges: canvasEdges,
    statTitle: 'Người là cầu nối',
    stats,
    insights: insightsFor(nodes.length, edges.length, isolated, truncated, hint, edges.filter((e) => e.state === 'cold').length),
  };
}

function buildGroups(nodes: GraphGroupNode[], edges: GraphGroupEdge[], truncated: boolean, hint?: string): Derived {
  const maxW = edges.reduce((m, e) => Math.max(m, e.weight), 0);
  const degree = new Map<string, { n: number; w: number }>();
  const bridgingIds = new Set<string>();
  for (const e of edges) {
    for (const id of [e.from, e.to]) {
      const cur = degree.get(id) ?? { n: 0, w: 0 };
      cur.n += 1;
      cur.w += e.weight;
      degree.set(id, cur);
    }
    if (e.bridge_person_codes.length) {
      bridgingIds.add(e.from);
      bridgingIds.add(e.to);
    }
  }
  const canvasNodes: GraphCanvasNode[] = nodes.map((n) => {
    const d = degree.get(n.id) ?? { n: 0, w: 0 };
    return {
      id: n.id,
      name: n.name,
      sub: `${fmtInt(n.member_count)} thành viên`,
      icon: 'ph ph-users-three',
      tone: bridgingIds.has(n.id) ? ACC3 : d.n === 0 ? N4 : ACC4,
      radius: Math.max(14, Math.min(28, 14 + Math.sqrt(n.member_count))),
      muted: d.n === 0,
    };
  });
  const bridgeCodeSet = new Set(edges.flatMap((e) => e.bridge_person_codes));
  const canvasEdges: GraphCanvasEdge[] = edges.map((e, i) => {
    const cold = e.state === 'cold';
    const bridge = e.bridge_person_codes.length > 0;
    return {
      id: `${e.from}-${e.to}-${i}`,
      from: e.from,
      to: e.to,
      width: edgeWidth(e.weight, maxW, bridge),
      color: edgeTone(e.weight, maxW, cold, bridge),
      opacity: cold ? 0.5 : 0.85,
      title: `trọng số ${e.weight.toFixed(2)} · ${fmtInt(e.interactions)} thành viên chung${bridge ? ` · cầu nối: ${e.bridge_person_codes.join(', ')}` : ''}${cold ? ' · đang lạnh' : ''}`,
    };
  });
  const stats: StatRow[] = [...degree.entries()]
    .sort((a, b) => b[1].w - a[1].w)
    .slice(0, 6)
    .map(([id, d]) => ({ key: id, name: nodes.find((n) => n.id === id)?.name ?? id, value: `${fmtInt(d.n)} liên kết · trọng số ${d.w.toFixed(2)}`, tone: bridgingIds.has(id) ? ACC3 : N4 }));
  const isolated = nodes.filter((n) => !degree.has(n.id)).length;
  return {
    nodes: canvasNodes,
    edges: canvasEdges,
    statTitle: 'Nhóm có trọng số cao nhất',
    stats,
    insights: [
      ...insightsFor(nodes.length, edges.length, isolated, truncated, hint, edges.filter((e) => e.state === 'cold').length),
      ...(bridgeCodeSet.size ? [`${bridgeCodeSet.size} người đang bắc cầu giữa các nhóm hiển thị: ${[...bridgeCodeSet].slice(0, 6).join(', ')}.`] : []),
    ],
  };
}

/**
 * Đồ thị Người↔Người / Nhóm↔Nhóm (chế độ 2/3), tái dùng nguyên cho chi tiết một Luồng chủ đề (chế độ 4, khi
 * `topic` có giá trị — khi đó gọi `GET /graph/topics/{topic}` thay vì `/graph/people`, ẩn bộ lọc trọng
 * số/tâm điểm vì hợp đồng chế độ 4 không có hai tham số đó).
 */
export function NodeGraphPane({ kind, topic }: { kind: 'people' | 'groups'; topic?: string | null }) {
  const [nodeId, setNodeId] = useUrlState<string>(kind === 'people' ? 'focus' : 'gfocus', '');
  const [minWeight, setMinWeight] = useUrlState<string>(kind === 'people' ? 'mw' : 'gmw', '');
  const [selected, setSelected] = useState<string | null>(null);
  const [positions, setPositions] = useState<Record<string, GraphPos>>({});
  const [pendingSave, setPendingSave] = useState(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const positionsRef = useRef(positions);
  positionsRef.current = positions;

  const inTopic = kind === 'people' && !!topic;
  const q = { node_id: nodeId || undefined, min_weight: minWeight ? Number(minWeight) : undefined };
  const people = useGraphPeople(q, kind === 'people' && !inTopic);
  const topicGraph = useGraphTopic(inTopic ? (topic as string) : null);
  const groups = useGraphGroups(q, kind === 'groups');
  const bridgeList = useGraphList({ limit: 200 }, kind === 'people');

  const layoutMode: GraphLayoutMode = inTopic ? 'topics' : kind === 'people' ? 'people' : 'groups';
  const layout = useGraphLayout(layoutMode);
  const saveLayout = useSaveGraphLayout(layoutMode);

  useEffect(() => {
    setPositions({});
    setSelected(null);
    // Đổi chế độ/luồng chủ đề → vị trí đã lưu (nếu có) sẽ được nạp lại từ `layout.data` khi nó tới.
  }, [layoutMode]);
  useEffect(() => {
    if (layout.data && Object.keys(layout.data.positions).length > 0) setPositions((cur) => (Object.keys(cur).length === 0 ? layout.data.positions : cur));
  }, [layout.data]);

  const activeResult = inTopic ? topicGraph : kind === 'people' ? people : groups;

  const bridgeScores = useMemo(() => {
    const m = new Map<string, number>();
    for (const it of bridgeList.data?.items ?? []) if (it.bridge_score > 0) m.set(it.id, it.bridge_score);
    return m;
  }, [bridgeList.data]);

  const derived = useMemo(() => {
    if (kind === 'people' && (inTopic ? topicGraph.data : people.data)) {
      const d = (inTopic ? topicGraph.data : people.data)!;
      return buildPeople(d.nodes, d.edges, bridgeScores, d.truncated, d.hint);
    }
    if (kind === 'groups' && groups.data) return buildGroups(groups.data.nodes, groups.data.edges, groups.data.truncated, groups.data.hint);
    return null;
  }, [kind, inTopic, people.data, topicGraph.data, groups.data, bridgeScores]);

  const scheduleSave = (next: Record<string, GraphPos>) => {
    setPendingSave(true);
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      saveLayout.mutate({ positions: next }, { onSettled: () => setPendingSave(false) });
    }, 700);
  };
  useEffect(() => () => void (saveTimer.current && clearTimeout(saveTimer.current)), []);

  const commit = (id: string, pos: GraphPos) => {
    const next = { ...positionsRef.current, [id]: pos };
    setPositions(next);
    scheduleSave(next);
  };
  const autoPlace = (next: Record<string, GraphPos>) => setPositions((cur) => ({ ...next, ...cur }));

  const nodesForFocusLookup = inTopic ? topicGraph.data?.nodes : kind === 'people' ? people.data?.nodes : groups.data?.nodes;
  const focusName = nodeId ? nodesForFocusLookup?.find((n) => n.id === nodeId)?.name : null;
  const selectedInfo = selected ? derived?.nodes.find((n) => n.id === selected) : null;

  return (
    <div className="gp-wrap">
      {!inTopic ? (
        <div className="gp-tools">
          <FilterSelect label="Trọng số tối thiểu" value={minWeight} onChange={setMinWeight} options={MIN_WEIGHT_OPTIONS} />
          {nodeId ? (
            <Button variant="secondary" size="sm" icon="ph ph-arrows-in" onClick={() => setNodeId('')}>
              {focusName ? `Đang xem quanh ${focusName}` : 'Đang lọc theo tâm điểm'} · bỏ lọc
            </Button>
          ) : null}
          {pendingSave || saveLayout.isPending ? (
            <span className="gp-tools__saving">
              <Icon name="ph ph-arrows-clockwise" size={12} /> đang lưu vị trí…
            </span>
          ) : null}
        </div>
      ) : null}

      <div className="gp-grid">
        <div className="gh-card gp-card">
          <div className="gh-card__header">
            <div>
              <div className="gh-card__title">{kind === 'groups' ? 'Đồ thị quan hệ giữa các nhóm' : 'Đồ thị quan hệ giữa con người'}</div>
              <div className="gh-card__kicker">
                {kind === 'groups' ? 'Cạnh là số thành viên chung (hệ số chồng lấp)' : 'Cạnh là tần suất và chiều tương tác đã phân tích'}
              </div>
            </div>
            {activeResult.data ? (
              <span className="raw-count">
                {fmtInt(activeResult.data.nodes.length)} node · {fmtInt(activeResult.data.total_edges)} cạnh
              </span>
            ) : null}
          </div>
          {activeResult.isPending ? (
            <SkeletonLines rows={6} />
          ) : activeResult.isError ? (
            <CardError error={activeResult.error} onRetry={() => void activeResult.refetch()} retrying={activeResult.isFetching} />
          ) : !derived || derived.nodes.length === 0 ? (
            <EmptyState icon="ph ph-graph" title="Chưa có quan hệ nào khớp bộ lọc" description="Thử hạ trọng số tối thiểu, hoặc bấm Dựng lại đồ thị nếu dữ liệu vừa mới nạp." />
          ) : (
            <div style={{ padding: 20, background: 'var(--color-bg)' }}>
              <GraphCanvas
                nodes={derived.nodes}
                edges={derived.edges}
                positions={positions}
                onAutoPlaced={autoPlace}
                onNodeCommit={commit}
                onNodeSelect={setSelected}
                selectedId={selected}
              />
            </div>
          )}
        </div>

        <div className="gp-side">
          {selectedInfo ? (
            <div className="gh-card">
              <div className="gh-card__header">
                <div>
                  <div className="gh-card__title">{selectedInfo.name}</div>
                  <div className="gh-card__kicker">{selectedInfo.sub}</div>
                </div>
                <Button variant="ghost" size="sm" onClick={() => setSelected(null)} aria-label="Đóng chi tiết node">
                  <Icon name="ph ph-x" size={13} />
                </Button>
              </div>
              <div style={{ padding: '12px 16px 14px', display: 'flex', flexDirection: 'column', gap: 8 }}>
                {!inTopic ? (
                  <Button variant="secondary" size="sm" icon="ph ph-crosshair" onClick={() => setNodeId(selectedInfo.id)}>
                    Xem quanh node này
                  </Button>
                ) : null}
                {kind === 'people' ? (
                  <Link to={`/profile?id=${encodeURIComponent(selectedInfo.id)}`} className="gh-btn gh-btn--secondary gh-btn--sm">
                    <Icon name="ph ph-user-circle" size={12} />
                    Mở hồ sơ sống
                  </Link>
                ) : null}
              </div>
            </div>
          ) : null}
          <div className="gh-card">
            <div className="gh-card__header">
              <div>
                <div className="gh-card__title">{derived?.statTitle ?? '—'}</div>
                <div className="gh-card__kicker">Rút từ dữ liệu đã phân tích</div>
              </div>
            </div>
            <div style={{ padding: '4px 0 8px' }}>
              {(derived?.stats ?? []).length === 0 ? (
                <div className="muted-note" style={{ padding: '10px 16px' }}>
                  Chưa có dữ liệu.
                </div>
              ) : (
                derived!.stats.map((s) => (
                  <div key={s.key} className="gp-stat-row">
                    <span className="gp-stat-row__name">{s.name}</span>
                    <span className="gp-stat-row__value" style={{ color: s.tone }}>
                      {s.value}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
          <div className="gh-card">
            <div className="gh-card__header">
              <div>
                <div className="gh-card__title">Hệ thống đọc được gì</div>
                <div className="gh-card__kicker">Kết luận từ đồ thị đang hiển thị</div>
              </div>
            </div>
            <div style={{ padding: '12px 16px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
              {(derived?.insights ?? []).map((t, i) => (
                <div key={i} className="gp-insight">
                  <span className="gp-insight__dot" style={{ background: i === 0 && activeResult.data && 'truncated' in activeResult.data && activeResult.data.truncated ? WARN : ACC4 }} />
                  <span className="gp-insight__text">{t}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
