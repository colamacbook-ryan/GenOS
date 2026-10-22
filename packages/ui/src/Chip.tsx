import type { CSSProperties, ReactNode } from 'react';
import { cx } from './cx';
import { toneColor, type Tone } from './tone';

export interface ChipProps {
  tone?: Tone;
  /** Leading 5px dot in the tone colour. */
  dot?: boolean;
  className?: string;
  children: ReactNode;
}

/** Status chip — 1px border in the tone colour, pill radius (design `stateStyle`). */
export function Chip({ tone = 'neutral', dot, className, children }: ChipProps) {
  const style = {
    '--chip-tone': toneColor(tone),
    '--chip-border': tone === 'neutral' ? 'var(--color-neutral-800)' : toneColor(tone),
  } as CSSProperties;
  return (
    <span className={cx('gh-chip', className)} style={style}>
      {dot ? <span className="gh-chip__dot" aria-hidden /> : null}
      {children}
    </span>
  );
}

export interface TagProps {
  tone?: Tone;
  className?: string;
  children: ReactNode;
}

/** Kind tag — 9.5px/600 uppercase, 4px radius (design `chip()`). */
export function Tag({ tone = 'neutral', className, children }: TagProps) {
  const style = {
    '--tag-tone': tone === 'accent' ? 'var(--color-accent-300)' : toneColor(tone),
    '--tag-border': tone === 'accent' ? 'var(--color-accent-800)' : 'var(--color-neutral-800)',
  } as CSSProperties;
  return (
    <span className={cx('gh-tag', className)} style={style}>
      {children}
    </span>
  );
}
