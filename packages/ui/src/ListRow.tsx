import type { AnchorHTMLAttributes, MouseEvent, ReactNode } from 'react';
import { cx } from './cx';
import { Icon } from './Icon';
import { kitNavigate } from './useMediaQuery';

/**
 * v0.1.59 (G2) — ListRow: một dòng danh sách của Mặt tiền, GỘP `RowLink` + `Avatar` cũ (apps/web/src/owner/parts.tsx).
 *
 *   [lead | avatar] [tiêu đề 14px/600 + dòng phụ 12px] [right] [›]            [actions]
 *   └──────────── vùng bấm được (liên kết hoặc nút) ────────────┘   └ nút riêng, ngoài vùng bấm ┘
 *
 * - `to` ⇒ cả vùng bấm là liên kết (chuyển trang trong ứng dụng, bấm giữa/Ctrl mở tab mới như thẻ <a>); `onClick` ⇒ nút;
 *   không có cả hai ⇒ dòng tĩnh. `actions` nằm NGOÀI vùng bấm nên đặt được nút (Duyệt, Trả lời…) mà không lồng nút trong nút.
 * - Vùng chạm ≥ 44px. CSS: owner-kit.css (`kit-row`), chỉ dùng token.
 * - Đây cũng là dạng "xếp chồng" mà DataTable chuyển sang dưới 760px.
 */

/** Chỉ nhận đường dẫn trong ứng dụng (`/x`, `#x`, `?x`); lạ (javascript:, //evil, https:…) ⇒ không có href. */
function safeHref(to: unknown): string | undefined {
  if (typeof to !== 'string') return undefined;
  const s = to.trim();
  if (/^\/(?![/\\])/.test(s) || /^[#?]/.test(s)) return [...s].some((c) => c.charCodeAt(0) < 32) ? undefined : s;
  return undefined;
}

export interface KitLinkProps extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> {
  to: string;
}

/** Thẻ <a> chuyển trang trong ứng dụng khi đã `setKitNavigator`; còn lại hành xử như <a> thường. */
export function KitLink({ to, onClick, children, ...rest }: KitLinkProps) {
  const href = safeHref(to);
  const handle = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e);
    if (e.defaultPrevented || !href || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || rest.target === '_blank') return;
    if (/^\//.test(href) && kitNavigate(href)) e.preventDefault();
  };
  return (
    <a {...rest} href={href} onClick={handle}>
      {children}
    </a>
  );
}

/** "Nguyễn Văn An" → "NA"; một từ → 2 chữ đầu; rỗng → "·". */
function initialsOf(name: unknown): string {
  const parts = (typeof name === 'string' ? name : '').trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '·';
  if (parts.length === 1) return [...parts[0]].slice(0, 2).join('').toLocaleUpperCase('vi');
  return ((parts[0][0] ?? '') + (parts.at(-1)?.[0] ?? '')).toLocaleUpperCase('vi');
}

export interface ListRowProps {
  title: ReactNode;
  /** Dòng phụ 12px (người, thời gian, lý do…). */
  meta?: ReactNode;
  /** Bên phải, TRONG vùng bấm: huy hiệu, con số, chữ trạng thái (không đặt nút ở đây). */
  right?: ReactNode;
  /** Ô bên trái (icon/tile). */
  lead?: ReactNode;
  /** Tên người ⇒ viên tròn chữ cái đầu (thay cho `lead`). */
  avatar?: string;
  to?: string;
  onClick?: () => void;
  /** Nút hành động riêng bên phải, nằm ngoài vùng bấm. */
  actions?: ReactNode;
  selected?: boolean;
  /** Mũi tên ›: mặc định hiện khi dòng bấm được. */
  chevron?: boolean;
  as?: 'div' | 'li';
  className?: string;
  'data-testid'?: string;
}

export function ListRow({
  title,
  meta,
  right,
  lead,
  avatar,
  to,
  onClick,
  actions,
  selected,
  chevron,
  as: Tag = 'div',
  className,
  'data-testid': testId,
}: ListRowProps) {
  const inner = (
    <>
      {avatar !== undefined ? (
        <span className="kit-avatar" aria-hidden>
          {initialsOf(avatar)}
        </span>
      ) : (
        (lead ?? null)
      )}
      <span className="kit-row__body">
        <span className="kit-row__title">{title}</span>
        {meta ? <span className="kit-row__meta">{meta}</span> : null}
      </span>
      {right ? <span className="kit-row__right">{right}</span> : null}
      {(chevron ?? !!(to || onClick)) ? <Icon className="kit-row__go" name="ph ph-caret-right" size={14} /> : null}
    </>
  );
  return (
    <Tag className={cx('kit-row', selected && 'kit-row--selected', className)} data-selected={selected || undefined} data-testid={testId}>
      {to ? (
        <KitLink to={to} className="kit-row__main" aria-current={selected ? 'true' : undefined}>
          {inner}
        </KitLink>
      ) : onClick ? (
        <button type="button" className="kit-row__main" onClick={onClick} aria-current={selected ? 'true' : undefined}>
          {inner}
        </button>
      ) : (
        <div className="kit-row__main">{inner}</div>
      )}
      {actions ? <div className="kit-row__actions">{actions}</div> : null}
    </Tag>
  );
}
