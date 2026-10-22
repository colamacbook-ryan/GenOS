import type { CSSProperties } from 'react';
import type { IconWeight } from '@phosphor-icons/react';
import { ICONS, parseIconClass } from './iconRegistry';

export interface IconProps {
  /** Phosphor web class ("ph ph-gauge") or bare name ("gauge"). */
  name: string;
  size?: number;
  color?: string;
  weight?: IconWeight;
  className?: string;
  style?: CSSProperties;
  /** Accessible label; icons are decorative (aria-hidden) without one. */
  label?: string;
}

/**
 * Renders a Phosphor icon in a `size`×`size` box. An unknown name renders
 * an empty box of the same size — what the web font does for a class it does
 * not have (e.g. the design's `ph-radar`, which Phosphor 2.1 does not ship).
 */
export function Icon({ name, size = 16, color, weight, className, style, label }: IconProps) {
  const parsed = name.includes('ph') ? parseIconClass(name) : { name, weight: 'regular' as IconWeight };
  const Cmp = ICONS[parsed.name];
  // Sau v0.1.49: inline-block (không còn block) — Icon trong cha KHÔNG flex/grid (vd `<span class="muted-note"><Icon/> chữ</span>`,
  // `<a class="upd-notice">`) trước đây rớt một dòng riêng phía trên chữ. Trong cha flex/grid (đa số chỗ dùng) không đổi gì.
  // Hệ quả: khung CHỈ chứa icon mà là khối thường (block/inline-block) có hộp dòng — strut theo line-height đội khung cao
  // hơn icon và đẩy icon lệch xuống. Khung chỉ-icon phải là flex/inline-flex/grid (vd `.td-ch`, `.tk-row__status`,
  // `.roles-matrix__mark`); lính gác e2e/layout-guard.spec.ts bắt kiểu lỗi này (`icon-lech`).
  const boxStyle: CSSProperties = { width: size, height: size, flex: 'none', display: 'inline-block', verticalAlign: 'middle', color, ...style };
  if (!Cmp) {
    return <span className={className} style={boxStyle} aria-hidden={label ? undefined : true} aria-label={label} data-icon={parsed.name} />;
  }
  return (
    <Cmp
      size={size}
      weight={weight ?? parsed.weight}
      className={className}
      style={boxStyle}
      aria-hidden={label ? undefined : true}
      aria-label={label}
      data-icon={parsed.name}
    />
  );
}
