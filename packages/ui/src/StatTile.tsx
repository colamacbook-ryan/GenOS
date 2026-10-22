import { isValidElement, type ReactNode } from 'react';
import { cx } from './cx';
import { Icon } from './Icon';
import { KitLink } from './ListRow';
import { Sparkline } from './Sparkline';

/**
 * v0.1.59 (G2) — StatTile: ô một con số của Mặt tiền (CSS: `kit-stat`).
 *
 *   nhãn 14px
 *   GIÁ TRỊ 28px (+ đơn vị)         ← điểm nhìn của ô; mỗi số đi kèm một câu so sánh bằng chữ
 *   [↑ 12%] so với tuần trước        ← `delta`: màu theo NGHĨA tốt/xấu, không theo chiều lên/xuống
 *   ghi chú                [~~~~•]   ← `note` + `spark` (80×24)
 *
 * `delta.pct`: số phần trăm đã tính sẵn (12 = +12%, -8 = −8%); `null` = chưa có kỳ trước ⇒ huy hiệu "Mới".
 * `delta.good`: chiều nào là TỐT — 'up' (tin nhiều hơn là tốt) hoặc 'down' (chi phí, tin chưa trả lời: ít đi là tốt).
 * Tốt ⇒ xanh (ok), xấu ⇒ vàng (warn), không đổi ⇒ trung tính. Màu luôn kèm mũi tên + chữ "Tăng/Giảm" cho trình đọc màn hình.
 * `to` ⇒ cả ô là liên kết (vùng chạm ≥ 44px).
 */
export interface StatDelta {
  pct: number | null;
  good: 'up' | 'down';
  /** Câu so sánh sau huy hiệu ("so với tuần trước"). Mặc định "so với kỳ trước". */
  compare?: string;
}

export interface StatTileProps {
  label: ReactNode;
  value: ReactNode;
  unit?: string;
  delta?: StatDelta;
  spark?: readonly number[];
  note?: ReactNode;
  to?: string;
  className?: string;
  'data-testid'?: string;
}

const NUM = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 1 });

/** Giá trị an toàn cho JSX: số hữu hạn → nhóm hàng nghìn; chuỗi/phần tử React giữ nguyên; còn lại (null, NaN, object) → "—". */
function show(v: unknown): ReactNode {
  if (typeof v === 'number') return Number.isFinite(v) ? NUM.format(v) : '—';
  if (typeof v === 'string') return v;
  if (isValidElement(v)) return v;
  return '—';
}

function DeltaPill({ delta }: { delta: StatDelta }) {
  const pct = delta.pct;
  if (typeof pct !== 'number' || !Number.isFinite(pct)) {
    return (
      <>
        <span className="kit-delta kit-delta--new">Mới</span>
        <span className="kit-stat__vs">chưa có kỳ trước để so sánh</span>
      </>
    );
  }
  const compare = <span className="kit-stat__vs">{delta.compare ?? 'so với kỳ trước'}</span>;
  if (Math.abs(pct) < 0.05) {
    return (
      <>
        <span className="kit-delta kit-delta--flat">Không đổi</span>
        {compare}
      </>
    );
  }
  const up = pct > 0;
  const good = delta.good === 'up' ? up : !up;
  return (
    <>
      <span className={cx('kit-delta', good ? 'kit-delta--ok' : 'kit-delta--warn')} data-direction={up ? 'up' : 'down'}>
        <Icon name={up ? 'ph ph-arrow-up' : 'ph ph-arrow-down'} size={12} />
        <span className="kit-sr">{up ? 'Tăng ' : 'Giảm '}</span>
        {NUM.format(Math.abs(pct))}%
      </span>
      {compare}
    </>
  );
}

export function StatTile({ label, value, unit, delta, spark, note, to, className, 'data-testid': testId }: StatTileProps) {
  const body = (
    <>
      <span className="kit-stat__label">{label}</span>
      <span className="kit-stat__value">
        {show(value)}
        {unit ? <span className="kit-stat__unit"> {unit}</span> : null}
      </span>
      {delta ? (
        <span className="kit-stat__delta">
          <DeltaPill delta={delta} />
        </span>
      ) : null}
      {note || (spark && spark.length > 0) ? (
        <span className="kit-stat__foot">
          <span className="kit-stat__note">{note}</span>
          {spark && spark.length > 0 ? <Sparkline values={spark} /> : null}
        </span>
      ) : null}
    </>
  );
  return to ? (
    <KitLink to={to} className={cx('kit-stat', 'kit-stat--link', className)} data-testid={testId}>
      {body}
    </KitLink>
  ) : (
    <article className={cx('kit-stat', className)} data-testid={testId}>
      {body}
    </article>
  );
}
