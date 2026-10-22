import type { ReactNode } from 'react';
import { cx } from './cx';
import { Icon } from './Icon';

export interface PillProps {
  /** Phosphor class for the leading icon. */
  icon?: string;
  iconColor?: string;
  /** Leading LIVE dot instead of an icon; `false` shows it static/neutral. */
  live?: boolean;
  mono?: boolean;
  className?: string;
  children: ReactNode;
}

/** Header status pill (design: "4 kênh · 42 nhóm", "tự trị 4", "78%"). */
export function Pill({ icon, iconColor, live, mono, className, children }: PillProps) {
  return (
    <div className={cx('gh-pill', live !== undefined && 'gh-pill--live', className)}>
      {live !== undefined ? (
        <span className={cx('gh-live-dot', !live && 'gh-live-dot--off')} aria-hidden />
      ) : icon ? (
        <Icon name={icon} size={13} color={iconColor} />
      ) : null}
      <span className={cx('gh-pill__text', mono && 'gh-pill__text--mono')}>{children}</span>
    </div>
  );
}
