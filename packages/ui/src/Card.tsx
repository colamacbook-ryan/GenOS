import type { HTMLAttributes, ReactNode } from 'react';
import { cx } from './cx';
import { Skeleton } from './Skeleton';

export interface CardProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  title?: ReactNode;
  /** 10px uppercase sublabel under the title. */
  kicker?: ReactNode;
  /** Right side of the header (buttons, chips). */
  actions?: ReactNode;
  /** v0.1.59: cách viết số ít của `actions` (cùng chỗ: bên phải tiêu đề). Có cả hai thì hiện cả hai. */
  action?: ReactNode;
  /** v0.1.59: chân thẻ (liên kết "Xem tất cả", ghi chú) — có vạch ngăn phía trên. */
  footer?: ReactNode;
  /**
   * v0.1.59: trạng thái chung của mọi khối Mặt tiền — 'loading' vẽ khung chờ đúng hình khối; 'error' / 'empty' hiện `children`
   * (truyền OwnerError / EmptyState làm nội dung) hoặc một câu mặc định. Không đặt ⇒ thẻ vẽ như trước.
   */
  state?: 'loading' | 'error' | 'empty';
  /** Wrap children in the default 15px 16px body padding (default true). */
  padded?: boolean;
  as?: 'section' | 'div' | 'article';
}

const STATE_TEXT = {
  error: 'Chưa tải được khối này. Sếp thử lại sau ít phút.',
  empty: 'Chưa có gì để hiện.',
} as const;

/**
 * Surface card: header 12px 16px (title 13px/600 + kicker), divider under it.
 * Mặt tiền (v0.1.59): trong vùng `.kit-scope` (owner-kit.css) thẻ đổi sang số đo của hệ mới (đệm 16/24, bán kính 8, tiêu đề
 * 14px/600); ngoài vùng đó — cả Console — vẽ y như cũ.
 */
export function Card({ title, kicker, actions, action, footer, state, padded = true, as: Tag = 'section', className, children, ...rest }: CardProps) {
  const aside = actions || action ? (
    <>
      {actions}
      {action}
    </>
  ) : null;
  const body =
    state === 'loading' ? (
      <div className="kit-card__state kit-card__state--loading" role="status" aria-busy="true" aria-label="Đang tải">
        <Skeleton width="62%" height={14} />
        <Skeleton width="100%" height={12} />
        <Skeleton width="84%" height={12} />
      </div>
    ) : state ? (
      <div className={cx('kit-card__state', `kit-card__state--${state}`)} role={state === 'error' ? 'alert' : undefined}>
        {children ?? STATE_TEXT[state]}
      </div>
    ) : (
      children
    );
  return (
    <Tag className={cx('gh-card', state && 'kit-card', className)} data-state={state} {...rest}>
      {title !== undefined || aside ? (
        <div className="gh-card__header">
          <div style={{ minWidth: 0 }}>
            {title !== undefined ? <div className="gh-card__title">{title}</div> : null}
            {kicker !== undefined ? <div className="gh-card__kicker">{kicker}</div> : null}
          </div>
          {aside}
        </div>
      ) : null}
      {padded || state ? <div className="gh-card__body">{body}</div> : body}
      {footer ? <div className="kit-card__footer">{footer}</div> : null}
    </Tag>
  );
}
