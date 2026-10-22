import type { HTMLAttributes, ReactNode } from 'react';
import { cx } from './cx';

/**
 * v0.1.59 (G2) — Huy hiệu của Mặt tiền, đúng 3 loại (CSS: owner-kit.css, lớp `kit-badge`):
 *  - `level`  — Mức: Khẩn → `bad`, Quan trọng → `warn`, Thường → `neutral`. Truyền `level="Khẩn"`, hoặc `tone="Khẩn"`,
 *               hoặc chỉ ghi chữ làm nội dung (`<Badge kind="level">Khẩn</Badge>`) — tự suy ra màu theo chữ.
 *  - `status` — Trạng thái: `tone` = bad | warn | ok | info | accent | neutral (mặc định neutral).
 *  - `count`  — Đếm: số trong viên tròn (mặc định accent); `count` > 999 hiện "999+".
 * Màu LUÔN đi kèm chữ (mức/ trạng thái/ số) nên không chỉ dựa vào màu. Tối đa 2 huy hiệu mỗi dòng (quy ước thiết kế).
 */
export type BadgeKind = 'level' | 'status' | 'count';
export type BadgeTone = 'bad' | 'warn' | 'ok' | 'info' | 'accent' | 'neutral';
export type OwnerLevel = 'Khẩn' | 'Quan trọng' | 'Thường';

const TONES: readonly BadgeTone[] = ['bad', 'warn', 'ok', 'info', 'accent', 'neutral'];

/** Mức quan trọng → màu. Chữ lạ → neutral (không bao giờ đỏ nhầm). */
const LEVEL_TONE: Record<OwnerLevel, BadgeTone> = { Khẩn: 'bad', 'Quan trọng': 'warn', Thường: 'neutral' };

function isLevel(v: unknown): v is OwnerLevel {
  return typeof v === 'string' && Object.prototype.hasOwnProperty.call(LEVEL_TONE, v);
}

function levelTone(level: unknown): BadgeTone {
  return isLevel(level) ? LEVEL_TONE[level] : 'neutral';
}

function isTone(v: unknown): v is BadgeTone {
  return typeof v === 'string' && (TONES as readonly string[]).includes(v);
}

export interface BadgeProps extends Omit<HTMLAttributes<HTMLSpanElement>, 'children'> {
  kind?: BadgeKind;
  /** Màu: bad | warn | ok | info | accent | neutral — hoặc (loại `level`) chính tên mức "Khẩn" | "Quan trọng" | "Thường". */
  tone?: BadgeTone | OwnerLevel | (string & {});
  /** Loại `level`: tên mức, vừa là chữ hiện ra (nếu không có nội dung) vừa quyết định màu. */
  level?: OwnerLevel | (string & {});
  /** Loại `count`: số đếm. */
  count?: number;
  children?: ReactNode;
}

export function Badge({ kind = 'status', tone, level, count, children, className, ...rest }: BadgeProps) {
  const text = typeof children === 'string' ? children : undefined;
  const resolved: BadgeTone = isTone(tone)
    ? tone
    : kind === 'level' || typeof tone === 'string'
      ? levelTone(isLevel(tone) ? tone : (level ?? text))
      : kind === 'count'
        ? 'accent'
        : 'neutral';
  let body: ReactNode = children;
  if (kind === 'count' && typeof count === 'number' && Number.isFinite(count)) body = count > 999 ? '999+' : String(Math.max(0, Math.floor(count)));
  else if (kind === 'level' && (body === undefined || body === null || body === '') && typeof level === 'string') body = level;
  return (
    <span className={cx('kit-badge', `kit-badge--${kind}`, `kit-badge--${resolved}`, className)} data-kind={kind} data-tone={resolved} {...rest}>
      {body}
    </span>
  );
}
