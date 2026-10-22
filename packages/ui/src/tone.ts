export type Tone = 'ok' | 'warn' | 'bad' | 'accent' | 'neutral';

/** Foreground colour for a status tone (docs/handoff/02 "Màu trạng thái"). */
export function toneColor(tone: Tone | string | null | undefined): string {
  switch (tone) {
    case 'ok':
      return 'var(--color-ok)';
    case 'warn':
      return 'var(--color-warn)';
    case 'bad':
      return 'var(--color-bad)';
    case 'accent':
      return 'var(--color-accent-400)';
    default:
      return 'var(--color-neutral-400)';
  }
}

/** Tinted background for badges (design `railLeaf.badgeStyle`). */
export function toneTint(tone: Tone | string | null | undefined): string {
  switch (tone) {
    case 'ok':
      return 'var(--color-ok-tint)';
    case 'bad':
      return 'var(--color-bad-tint)';
    case 'accent':
      return 'var(--color-accent-900)';
    default:
      return 'var(--color-warn-tint)';
  }
}
