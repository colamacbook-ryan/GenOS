import { cx } from './cx';

/**
 * v0.1.59 (G2) — đường xu hướng nhỏ 80×24, không trục (CSS: `kit-spark` trong owner-kit.css). Chấm cuối là điểm mới nhất.
 * Chuỗi rỗng / 1 điểm / toàn 0 / có giá trị không phải số: vẫn vẽ được (đường phẳng ở giữa), KHÔNG bao giờ ra "NaN".
 * Mặc định trang trí (`aria-hidden`); truyền `ariaLabel` khi nó là thông tin duy nhất của số liệu.
 */
export interface SparklineProps {
  values: readonly number[];
  width?: number;
  height?: number;
  className?: string;
  ariaLabel?: string;
}

const PAD = 4;

function num(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0;
}

/** Toạ độ các điểm trong khung `width`×`height` (chừa lề PAD). Thuần — dùng chung cho test. */
function sparkPoints(values: readonly number[], width = 80, height = 24): Array<[number, number]> {
  const v = (Array.isArray(values) ? values : []).map(num);
  if (v.length === 0) return [];
  const min = Math.min(...v);
  const span = Math.max(...v) - min;
  const w = width - 2 * PAD;
  const h = height - 2 * PAD;
  return v.map((d, i): [number, number] => [
    v.length === 1 ? width / 2 : PAD + (w * i) / (v.length - 1),
    span === 0 ? height / 2 : height - PAD - (h * (d - min)) / span,
  ]);
}

const f = (x: number) => (Number.isFinite(x) ? x.toFixed(1) : '0');

export function Sparkline({ values, width = 80, height = 24, className, ariaLabel }: SparklineProps) {
  const pts = sparkPoints(values, width, height);
  const last = pts.at(-1);
  const d = pts.length >= 2 ? pts.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${f(x)},${f(y)}`).join(' ') : '';
  return (
    <svg
      className={cx('kit-spark', className)}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role={ariaLabel ? 'img' : undefined}
      aria-label={ariaLabel}
      aria-hidden={ariaLabel ? undefined : true}
      focusable="false"
    >
      {d ? <path className="kit-spark__line" d={d} /> : null}
      {last ? <circle className="kit-spark__dot" cx={f(last[0])} cy={f(last[1])} r={3} /> : null}
    </svg>
  );
}
