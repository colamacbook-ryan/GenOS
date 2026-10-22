import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { cx } from './cx';
import { Icon } from './Icon';
import { Tooltip } from './Tooltip';

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  /** Phosphor class. */
  icon: string;
  /** Accessible name, also shown as tooltip. */
  label: string;
  variant?: 'primary' | 'secondary' | 'ghost';
  iconSize?: number;
  /** Show the label as a tooltip (default true). */
  tooltip?: boolean;
}

/** 32×30 icon button (header "Góc nhìn đã lưu" / "Tìm theo ý định"). */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { icon, label, variant = 'secondary', iconSize = 15, tooltip = true, className, type = 'button', ...rest },
  ref,
) {
  const btn = (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      className={cx('gh-btn', `gh-btn--${variant}`, 'gh-btn--icon', className)}
      {...rest}
    >
      <Icon name={icon} size={iconSize} />
    </button>
  );
  return tooltip ? <Tooltip content={label}>{btn}</Tooltip> : btn;
});
