import { useEffect, useRef, useState } from 'react';
import { cx } from './cx';

/**
 * v0.1.59 (G2) — TrendChart: biểu đồ xu hướng SVG tự vẽ (không thư viện biểu đồ). CSS: `kit-chart` trong owner-kit.css; màu đọc
 * qua CSS var nên tự đổi sáng/tối.
 *
 *  - `kind: 'bar'`  — cột cho chuỗi đầu ("kỳ này", accent); `kind: 'line'` — đường.
 *  - `series`       — tối đa 2 chuỗi: chuỗi 1 = kỳ này (accent), chuỗi 2 = kỳ trước (đường xám). Mỗi chuỗi là mảng số hoặc
 *                     `{ name, values }`. `prev` là cách viết khác của chuỗi 2 (dùng khi `series` chỉ có một chuỗi).
 *  - `labels`       — nhãn cho từng điểm; trục X chỉ hiện 3–4 nhãn (đầu, giữa, cuối). Không truyền ⇒ 1, 2, 3…
 *  - Trục Y đúng 3 vạch (0, nửa, đỉnh làm tròn đẹp) định dạng bằng `yFormat`; `threshold` = vạch trần (màu warn) có trong legend.
 *  - Tooltip: rê chuột / bàn phím (Tab) / chạm vào cột hoặc điểm; mỗi điểm có `aria-label` "nhãn: giá trị".
 *  - `ariaLabel` (bắt buộc): câu tóm tắt cả biểu đồ cho trình đọc màn hình ("Tin nhận theo ngày, tuần này 1.240 tin…").
 *  - Chuỗi rỗng, toàn 0, có NaN/Infinity/chuỗi lạ: vẫn vẽ được khung + dòng "Chưa có số liệu", KHÔNG bao giờ ra "NaN".
 */
export interface TrendSeries {
  name?: string;
  values: readonly number[];
}

export interface TrendChartProps {
  kind: 'bar' | 'line';
  series: ReadonlyArray<readonly number[] | TrendSeries>;
  prev?: readonly number[];
  labels?: readonly string[];
  yFormat?: (v: number) => string;
  /** Định dạng giá trị trong tooltip/aria (mặc định = yFormat). */
  valueFormat?: (v: number) => string;
  threshold?: number;
  thresholdLabel?: string;
  ariaLabel: string;
  height?: number;
  className?: string;
  'data-testid'?: string;
}

const NUM = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 1 });
const defaultFmt = (v: number) => NUM.format(v);
const safeFmt = (f: (v: number) => string, v: number): string => {
  try {
    return f(Number.isFinite(v) ? v : 0);
  } catch {
    return '';
  }
};

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
const r1 = (x: number): number => (Number.isFinite(x) ? Math.round(x * 10) / 10 : 0);

interface NormSeries {
  name?: string;
  values: Array<number | null>;
}

function toSeries(s: unknown): NormSeries | null {
  if (Array.isArray(s)) return { values: s.map((v) => (typeof v === 'number' && Number.isFinite(v) ? v : null)) };
  if (s && typeof s === 'object') {
    const o = s as Record<string, unknown>;
    const arr = o.values ?? o.data ?? o.points;
    if (Array.isArray(arr)) {
      return {
        name: typeof o.name === 'string' ? o.name : typeof o.label === 'string' ? o.label : undefined,
        values: arr.map((v) => (typeof v === 'number' && Number.isFinite(v) ? v : null)),
      };
    }
  }
  return null;
}

function normalize(series: unknown, prev: unknown): NormSeries[] {
  let out: NormSeries[] = [];
  if (Array.isArray(series)) {
    out = series.length > 0 && typeof series[0] === 'number' ? [toSeries(series)!] : series.map(toSeries).filter((s): s is NormSeries => !!s);
  }
  out = out.slice(0, 2);
  if (out.length === 1 && Array.isArray(prev)) {
    const p = toSeries(prev);
    if (p) out.push(p);
  }
  return out;
}

/** Đỉnh trục Y làm tròn đẹp: 1, 2, 4, 6, 8, 10 × 10^k (nửa đỉnh luôn tròn); chuỗi toàn số nguyên nhỏ thì tối thiểu 2. */
function niceMax(peak: number): number {
  if (!(peak > 0)) return 4;
  const p = 10 ** Math.floor(Math.log10(peak));
  const hit = [1, 2, 4, 6, 8, 10].map((s) => s * p).find((m) => m >= peak) ?? 10 * p;
  return Number.isInteger(peak) && hit < 2 ? 2 : hit;
}

function barPath(x: number, y: number, w: number, h: number, r: number): string {
  const rr = Math.min(r, w / 2, h);
  return `M${r1(x)},${r1(y + h)}V${r1(y + rr)}A${r1(rr)},${r1(rr)} 0 0 1 ${r1(x + rr)},${r1(y)}H${r1(x + w - rr)}A${r1(rr)},${r1(rr)} 0 0 1 ${r1(x + w)},${r1(y + rr)}V${r1(y + h)}Z`;
}

/** Đường qua các điểm; điểm thiếu (null) ngắt đường. */
function linePath(pts: Array<[number, number] | null>): string {
  let d = '';
  let pen = false;
  for (const p of pts) {
    if (!p) {
      pen = false;
      continue;
    }
    d += `${pen ? 'L' : 'M'}${r1(p[0])},${r1(p[1])}`;
    pen = true;
  }
  return d;
}

/** Đo bề rộng khung bằng ResizeObserver; không có (jsdom) ⇒ giữ bề rộng mặc định. */
function useWidth(initial: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(initial);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const apply = (px: number) => {
      const next = Math.floor(px);
      if (next >= 120) setW((cur) => (cur === next ? cur : next));
    };
    apply(el.clientWidth);
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) apply(e.contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

export function TrendChart({
  kind,
  series,
  prev,
  labels,
  yFormat = defaultFmt,
  valueFormat,
  threshold,
  thresholdLabel,
  ariaLabel,
  height,
  className,
  'data-testid': testId,
}: TrendChartProps) {
  const [wrapRef, W] = useWidth(560);
  const [active, setActive] = useState<number | null>(null);
  const lastPointer = useRef('mouse');
  const norm = normalize(series, prev);
  const cur = norm[0]?.values ?? [];
  const old = norm[1]?.values ?? [];
  const n = Math.max(cur.length, old.length);
  const fmtV = valueFormat ?? yFormat;
  const thr = typeof threshold === 'number' && Number.isFinite(threshold) && threshold > 0 ? threshold : null;
  const peak = Math.max(0, ...cur.map(num), ...old.map(num), thr ?? 0);
  const max = niceMax(peak);
  const ticks = [0, max / 2, max];
  const empty = n === 0 || peak === 0;

  const H = height ?? (W < 480 ? 180 : 220);
  const m = { l: Math.max(...ticks.map((t) => safeFmt(yFormat, t).length), 1) * 7 + 12, r: 4, t: 8, b: 28 };
  const pw = Math.max(W - m.l - m.r, 1);
  const ph = Math.max(H - m.t - m.b, 1);
  const band = pw / Math.max(n, 1);
  const bw = Math.max(2, Math.min(24, band * 0.6));
  const cx0 = (i: number) => m.l + band * (i + 0.5);
  const y = (v: number) => m.t + ph - (Math.min(Math.max(v, 0), max) / max) * ph;
  const lab = (i: number): string => {
    const l = labels?.[i];
    return typeof l === 'string' ? l : String(i + 1);
  };

  // Trục X: 3–4 nhãn (đầu, giữa, cuối), bỏ trùng.
  const idx = n === 0 ? [] : [...new Set(n <= 7 ? [0, Math.floor((n - 1) / 2), n - 1] : [0, Math.round((n - 1) / 3), Math.round(((n - 1) * 2) / 3), n - 1])];

  // Chạm/rê hiện tooltip; chạm ra ngoài thì tắt.
  useEffect(() => {
    if (active === null) return;
    const off = (e: PointerEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setActive(null);
    };
    document.addEventListener('pointerdown', off);
    return () => document.removeEventListener('pointerdown', off);
  }, [active, wrapRef]);

  const curName = norm[0]?.name ?? 'Kỳ này';
  const prevName = norm[1]?.name ?? 'Kỳ trước';
  const a = active !== null && active < n ? active : null;
  const curPts = cur.map((v, i): [number, number] | null => (v === null ? null : [cx0(i), y(v)]));
  const oldPts = old.map((v, i): [number, number] | null => (v === null ? null : [cx0(i), y(v)]));
  const lastOf = (p: Array<[number, number] | null>) => [...p].reverse().find((q) => q !== null) ?? null;

  const tipLeft = a === null ? 0 : Math.min(Math.max(0, cx0(a) - 70), Math.max(0, W - 140));
  const tipTop = a === null ? 0 : Math.max(0, y(num(cur[a] ?? old[a])) - 64);

  return (
    <div className={cx('kit-chart', className)} ref={wrapRef} data-testid={testId} data-kind={kind} data-active={a !== null || undefined}>
      <svg className="kit-chart__svg" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={ariaLabel} onPointerLeave={(e) => e.pointerType === 'mouse' && setActive(null)}>
        {ticks.map((t, k) => (
          <g key={k}>
            <line className={t ? 'kit-chart__grid' : 'kit-chart__base'} x1={r1(m.l)} x2={r1(W - m.r)} y1={r1(y(t))} y2={r1(y(t))} />
            <text className="kit-chart__t" x={r1(m.l - 8)} y={r1(y(t) + 4)} textAnchor="end">
              {safeFmt(yFormat, t)}
            </text>
          </g>
        ))}
        {kind === 'bar'
          ? cur.map((v, i) => {
              const h = v === null ? 0 : (Math.min(Math.max(v, 0), max) / max) * ph;
              return h > 0 ? <path key={i} className={cx('kit-chart__bar', a === i && 'is-on')} d={barPath(cx0(i) - bw / 2, y(v ?? 0), bw, h, 4)} /> : null;
            })
          : curPts.some(Boolean) && <path className="kit-chart__line" d={linePath(curPts)} />}
        {thr !== null ? <line className="kit-chart__thr" x1={r1(m.l)} x2={r1(W - m.r)} y1={r1(y(thr))} y2={r1(y(thr))} /> : null}
        {old.length > 0 && oldPts.some(Boolean) ? <path className="kit-chart__prev" d={linePath(oldPts)} /> : null}
        {(() => {
          const p = lastOf(oldPts);
          return p ? <circle className="kit-chart__dot" cx={r1(p[0])} cy={r1(p[1])} r={4} /> : null;
        })()}
        {kind === 'line' && !empty
          ? (() => {
              const p = a !== null ? curPts[a] : lastOf(curPts);
              return p ? <circle className="kit-chart__pt" cx={r1(p[0])} cy={r1(p[1])} r={4} /> : null;
            })()
          : null}
        {idx.map((i, k) => {
          const anchor = k === 0 ? 'start' : k === idx.length - 1 ? 'end' : 'middle';
          const x = anchor === 'start' ? cx0(i) - bw / 2 : anchor === 'end' ? cx0(i) + bw / 2 : cx0(i);
          return (
            <text key={i} className="kit-chart__t" x={r1(x)} y={r1(H - 8)} textAnchor={anchor}>
              {lab(i)}
            </text>
          );
        })}
        {empty ? (
          <text className="kit-chart__empty" x={r1(W / 2)} y={r1(m.t + ph / 2)} textAnchor="middle">
            Chưa có số liệu trong kỳ này
          </text>
        ) : null}
        {Array.from({ length: n }, (_, i) => {
          const v = cur[i] ?? old[i];
          return (
            <rect
              key={i}
              className="kit-chart__hit"
              x={r1(m.l + band * i)}
              y={r1(m.t)}
              width={r1(band)}
              height={r1(ph)}
              tabIndex={0}
              role="img"
              aria-label={`${lab(i)}: ${safeFmt(fmtV, num(v))}`}
              onPointerEnter={(e) => e.pointerType === 'mouse' && setActive(i)}
              onPointerDown={(e) => {
                lastPointer.current = e.pointerType;
              }}
              // Chuột: rê đã hiện, bấm giữ nguyên; chạm: bấm lần nữa vào cùng điểm thì tắt.
              onClick={() => setActive((cur0) => (cur0 === i && lastPointer.current !== 'mouse' ? null : i))}
              onFocus={() => setActive(i)}
              onBlur={() => setActive(null)}
            />
          );
        })}
      </svg>
      {a !== null ? (
        <div className="kit-chart__tip" style={{ left: tipLeft, top: tipTop }} aria-hidden>
          <strong>{safeFmt(fmtV, num(cur[a] ?? old[a]))}</strong>
          <span>{`${lab(a)} · ${curName}`}</span>
          {old.length > 0 && old[a] !== null && old[a] !== undefined ? (
            <span className="kit-chart__tiprow">
              <i className="kit-chart__key kit-chart__key--prev" />
              {`${prevName}: ${safeFmt(fmtV, num(old[a]))}`}
            </span>
          ) : null}
        </div>
      ) : null}
      {!empty && (old.length > 0 || thr !== null) ? (
        <div className="kit-chart__legend">
          <span>
            <i className={cx('kit-chart__key', kind === 'bar' ? 'kit-chart__key--bar' : 'kit-chart__key--cur')} />
            {curName}
          </span>
          {old.length > 0 ? (
            <span>
              <i className="kit-chart__key kit-chart__key--prev" />
              {prevName}
            </span>
          ) : null}
          {thr !== null ? (
            <span>
              <i className="kit-chart__key kit-chart__key--thr" />
              {thresholdLabel ?? 'Mức trần'}
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
