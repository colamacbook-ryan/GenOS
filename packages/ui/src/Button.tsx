import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { cx } from './cx';
import { Icon } from './Icon';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: 'md' | 'sm';
  /** Phosphor class for a leading icon. */
  icon?: string;
  /** Phosphor class for a trailing icon (design: "… →"). */
  iconRight?: string;
  loading?: boolean;
  block?: boolean;
  children?: ReactNode;
}

/** `.btn` — primary is an accent outline, never a fill (Nocturne). */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', icon, iconRight, loading, block, className, children, disabled, type = 'button', ...rest },
  ref,
) {
  const iconSize = size === 'sm' ? 12 : 14;
  return (
    <button
      ref={ref}
      type={type}
      className={cx('gh-btn', `gh-btn--${variant}`, size === 'sm' && 'gh-btn--sm', block && 'gh-btn--block', className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <span className="gh-btn__spinner" aria-hidden /> : icon ? <Icon name={icon} size={iconSize} /> : null}
      {children}
      {iconRight ? <Icon name={iconRight} size={12} /> : null}
    </button>
  );
});
