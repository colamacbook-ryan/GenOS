import type { CSSProperties } from 'react';
import { cx } from './cx';

export interface SkeletonProps {
  width?: number | string;
  height?: number | string;
  radius?: number | string;
  className?: string;
  style?: CSSProperties;
}

/** Placeholder block drawn in the same frame as the content it stands for. */
export function Skeleton({ width = '100%', height = 12, radius, className, style }: SkeletonProps) {
  return (
    <span
      aria-hidden
      className={cx('gh-skeleton', className)}
      style={{ width, height, borderRadius: radius, ...style }}
    />
  );
}
