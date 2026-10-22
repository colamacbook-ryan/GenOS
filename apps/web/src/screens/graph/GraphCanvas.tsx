import { useEffect, useMemo, useRef, useState } from 'react';
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation } from 'd3-force';
import { Icon } from '@gen-harness/ui';

/**
 * Đồ thị lực node-cạnh dùng chung cho Người↔Người, Nhóm↔Nhóm và chi tiết một Luồng chủ đề.
 *
 * Chọn **d3-force** thay vì elkjs (PLAN §3.5 gợi ý cả hai): đây là quan hệ vô hướng nhiều-nhiều không có cấu
 * trúc phân lớp/thứ bậc rõ ràng (khác một sơ đồ luồng hay cây phụ thuộc mà elkjs mạnh) — mô phỏng lực (đẩy giữa
 * mọi node, hút theo cạnh) cho một bố cục toả tròn tự nhiên hơn cho "ai gần ai", và cho khả năng kéo thả trực
 * tiếp từng node mà không phải tính lại toàn bộ phân lớp.
 *
 * Bố cục **không chạy hoạt hình liên tục**: mô phỏng chạy đồng bộ (một số bước `tick()` cố định) chỉ để đặt vị
 * trí ban đầu cho node CHƯA có vị trí đã lưu, rồi dừng hẳn — quyết định có chủ đích để (a) tôn trọng
 * `prefers-reduced-motion` mặc định thay vì luôn hoạt hình, (b) cho Playwright một trạng thái ổn định để chụp
 * ảnh/thao tác, và (c) không phải chạy lại toàn bộ vật lý mỗi khi API trả cùng một đồ thị (refetch). Vị trí đã
 * lưu (`positions`) đứng yên tuyệt đối (dùng làm neo `fx`/`fy`); chỉ node mới được mô phỏng đặt chỗ.
 * Kéo thả sau đó chỉ là thao tác UI thuần (không chạy lại mô phỏng) — mượt và có thể đoán trước.
 */

export interface GraphCanvasNode {
  id: string;
  name: string;
  sub: string;
  icon: string;
  tone: string;
  radius: number;
  muted?: boolean;
}
export interface GraphCanvasEdge {
  id: string;
  from: string;
  to: string;
  width: number;
  color: string;
  opacity: number;
  title: string;
}
export interface GraphPos {
  x: number;
  y: number;
}

const MARGIN = 5;
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

export function GraphCanvas({
  nodes,
  edges,
  positions,
  onAutoPlaced,
  onNodeCommit,
  onNodeSelect,
  selectedId,
  height = 470,
}: {
  nodes: GraphCanvasNode[];
  edges: GraphCanvasEdge[];
  /** Vị trí đã biết (đã lưu ở server, hoặc đã tự đặt chỗ ở lượt render trước) theo domain 0–100. */
  positions: Record<string, GraphPos>;
  /** Node chưa có vị trí vừa được mô phỏng đặt chỗ — cha gộp vào state của nó (không tự lưu server). */
  onAutoPlaced: (next: Record<string, GraphPos>) => void;
  /** Kéo thả (hoặc bàn phím) xong một node — cha cập nhật vị trí và tự lưu (debounce). */
  onNodeCommit: (id: string, pos: GraphPos) => void;
  onNodeSelect?: (id: string) => void;
  selectedId?: string | null;
  height?: number;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState<{ id: string; pos: GraphPos; moved: boolean } | null>(null);
  const kbdTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const nodeKey = useMemo(() => nodes.map((n) => n.id).sort().join(','), [nodes]);
  const edgeKey = useMemo(() => edges.map((e) => e.id).sort().join(','), [edges]);
  const positionsRef = useRef(positions);
  positionsRef.current = positions;

  // Chỉ chạy mô phỏng khi TẬP node/cạnh thật sự đổi (không phải mỗi lần refetch trả cùng dữ liệu) — đọc vị trí
  // đã biết qua ref để không phải liệt `positions`/`onAutoPlaced` vào deps (chúng đổi tham chiếu mỗi render).
  useEffect(() => {
    const known = positionsRef.current;
    const missing = nodes.filter((n) => !known[n.id]);
    if (missing.length === 0) return;
    interface SimNode { id: string; x: number; y: number; fx?: number; fy?: number }
    const simNodes: SimNode[] = nodes.map((n) => {
      const p = known[n.id];
      return p ? { id: n.id, x: p.x, y: p.y, fx: p.x, fy: p.y } : { id: n.id, x: 50 + (Math.random() - 0.5) * 14, y: 50 + (Math.random() - 0.5) * 14 };
    });
    const simEdges = edges.map((e) => ({ source: e.from, target: e.to }));
    const sim = forceSimulation(simNodes)
      .force('link', forceLink(simEdges).id((d) => (d as SimNode).id).distance(15).strength(0.3))
      .force('charge', forceManyBody().strength(-22))
      .force('center', forceCenter(50, 50))
      .force('collide', forceCollide(7))
      .stop();
    for (let i = 0; i < 240; i++) sim.tick();
    const next: Record<string, GraphPos> = {};
    for (const n of simNodes) if (!known[n.id]) next[n.id] = { x: clamp(n.x, MARGIN, 100 - MARGIN), y: clamp(n.y, MARGIN, 100 - MARGIN) };
    if (Object.keys(next).length) onAutoPlaced(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- xem ghi chú ở trên: chỉ chạy lại theo shape, không theo tham chiếu.
  }, [nodeKey, edgeKey]);

  const posOf = (id: string): GraphPos => (dragging?.id === id ? dragging.pos : positions[id] ?? { x: 50, y: 50 });

  const toPct = (clientX: number, clientY: number): GraphPos => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return { x: 50, y: 50 };
    return {
      x: clamp(((clientX - rect.left) / rect.width) * 100, MARGIN, 100 - MARGIN),
      y: clamp(((clientY - rect.top) / rect.height) * 100, MARGIN, 100 - MARGIN),
    };
  };

  const startDrag = (id: string) => (e: React.PointerEvent) => {
    e.currentTarget.setPointerCapture?.(e.pointerId);
    setDragging({ id, pos: positions[id] ?? toPct(e.clientX, e.clientY), moved: false });
  };
  const onMove = (e: React.PointerEvent) => {
    if (!dragging) return;
    const p = toPct(e.clientX, e.clientY);
    setDragging({ id: dragging.id, pos: p, moved: true });
  };
  const endDrag = (e: React.PointerEvent) => {
    if (!dragging) return;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    if (dragging.moved) onNodeCommit(dragging.id, dragging.pos);
    else onNodeSelect?.(dragging.id);
    setDragging(null);
  };

  const onKeyDown = (id: string) => (e: React.KeyboardEvent) => {
    const step = e.shiftKey ? 8 : 3;
    let d: GraphPos | null = null;
    if (e.key === 'ArrowLeft') d = { x: -step, y: 0 };
    else if (e.key === 'ArrowRight') d = { x: step, y: 0 };
    else if (e.key === 'ArrowUp') d = { x: 0, y: -step };
    else if (e.key === 'ArrowDown') d = { x: 0, y: step };
    else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onNodeSelect?.(id);
      return;
    } else return;
    e.preventDefault();
    const cur = posOf(id);
    const next = { x: clamp(cur.x + d.x, MARGIN, 100 - MARGIN), y: clamp(cur.y + d.y, MARGIN, 100 - MARGIN) };
    setDragging({ id, pos: next, moved: true });
    if (kbdTimer.current) clearTimeout(kbdTimer.current);
    kbdTimer.current = setTimeout(() => {
      onNodeCommit(id, next);
      setDragging(null);
    }, 500);
  };

  useEffect(() => () => {
    if (kbdTimer.current) clearTimeout(kbdTimer.current);
  }, []);

  return (
    <div className="gp-canvas" ref={wrapRef} style={{ height }} onPointerMove={onMove}>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="gp-canvas__svg">
        {edges.map((e) => {
          const a = posOf(e.from);
          const b = posOf(e.to);
          return (
            <line
              key={e.id}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke={e.color}
              strokeWidth={e.width}
              strokeOpacity={e.opacity}
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
            >
              <title>{e.title}</title>
            </line>
          );
        })}
      </svg>
      {nodes.map((n) => {
        const p = posOf(n.id);
        const selected = selectedId === n.id;
        return (
          <button
            type="button"
            key={n.id}
            className="gp-node"
            style={{
              left: `${p.x}%`,
              top: `${p.y}%`,
              width: n.radius * 2,
              height: n.radius * 2,
              opacity: n.muted ? 0.55 : 1,
              zIndex: dragging?.id === n.id ? 5 : selected ? 4 : 2,
            }}
            aria-label={`${n.name} — ${n.sub}. Kéo hoặc dùng mũi tên để đổi vị trí, Enter để xem chi tiết.`}
            aria-pressed={selected}
            onPointerDown={startDrag(n.id)}
            onPointerUp={endDrag}
            onKeyDown={onKeyDown(n.id)}
          >
            <span className="gp-node__dot" style={{ borderColor: n.tone, color: n.tone, boxShadow: `0 0 18px -8px ${n.tone}` }}>
              <Icon name={n.icon} size={Math.round(n.radius * 0.5)} />
            </span>
            <span className="gp-node__label">
              <span className="gp-node__name">{n.name}</span>
              <span className="gp-node__sub">{n.sub}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
