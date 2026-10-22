import type { ReactNode } from 'react';
import { cx } from './cx';

/**
 * v0.1.59 (G2) — thanh ngang cho "top N" và phễu (CSS: `kit-hbar`): mỗi dòng nhãn (trái) + con số (phải) ở trên, thanh dài tỉ
 * lệ với `max` (mặc định = giá trị lớn nhất) ở dưới. Số luôn hiện bằng chữ cạnh thanh nên không chỉ dựa vào độ dài/màu.
 * Giá trị không phải số / âm ⇒ coi là 0; mọi giá trị 0 ⇒ thanh trống (không chia cho 0, không "NaN").
 */
export interface HBarItem {
  label: string;
  value: number;
  /** Chữ hiện bên phải thay cho số thô (vd "12 · 3,5 triệu ₫"). Mặc định: số đã nhóm hàng nghìn. */
  display?: ReactNode;
}

export interface HBarProps {
  items: readonly HBarItem[];
  /** Mẫu số của thanh; mặc định giá trị lớn nhất trong `items`. */
  max?: number;
  /** Tên danh sách cho trình đọc màn hình. */
  ariaLabel?: string;
  className?: string;
}

const vi = new Intl.NumberFormat('vi-VN');

function pos(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : 0;
}

export function HBar({ items, max, ariaLabel, className }: HBarProps) {
  const rows = Array.isArray(items) ? items : [];
  const top = pos(max) || Math.max(0, ...rows.map((r) => pos(r?.value)));
  return (
    <ul className={cx('kit-hbar', className)} aria-label={ariaLabel}>
      {rows.map((r, i) => {
        const v = pos(r?.value);
        const pct = top > 0 ? Math.min(100, (v / top) * 100) : 0;
        return (
          <li key={`${typeof r?.label === 'string' ? r.label : ''}-${i}`} className="kit-hbar__row">
            <div className="kit-hbar__head">
              <span className="kit-hbar__label">{typeof r?.label === 'string' ? r.label : ''}</span>
              <span className="kit-hbar__value">{r?.display ?? vi.format(v)}</span>
            </div>
            <div className="kit-hbar__track" aria-hidden>
              <div className="kit-hbar__fill" style={{ width: `${pct.toFixed(1)}%` }} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
