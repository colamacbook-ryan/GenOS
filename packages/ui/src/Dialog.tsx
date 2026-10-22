import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { cx } from './cx';
import { Icon } from './Icon';

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  /** 13px/600 card title. */
  title: ReactNode;
  /** 10px uppercase kicker under the title. */
  kicker?: ReactNode;
  /** Right side of the header (a chip, a code). */
  aside?: ReactNode;
  /** Footer buttons (right-aligned, divider above). */
  actions?: ReactNode;
  /** Dialog width in px (default 400, the PIN dialog's). */
  width?: number;
  /** Wrap children in the 15px 16px body padding (default true). */
  padded?: boolean;
  className?: string;
  children?: ReactNode;
  /** Close on backdrop click, Esc and the × button (default true). `false`: only the dialog's own actions close it
   * (v0.1.28, UX V4 — e.g. a one-time temp password must not vanish on a stray Esc/click). */
  dismissable?: boolean;
}

const FOCUSABLE =
  'a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Modal surface in the card language: header 12px 16px (title + kicker),
 * body, footer actions. Esc and the backdrop close it; focus is trapped and
 * returned to the opener.
 */
export function Dialog({
  open,
  onClose,
  title,
  kicker,
  aside,
  actions,
  width = 400,
  padded = true,
  className,
  children,
  dismissable = true,
}: DialogProps) {
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    returnFocus.current = (document.activeElement as HTMLElement) ?? null;
    const t = setTimeout(() => {
      const el = ref.current;
      if (!el || el.contains(document.activeElement)) return;
      const auto = el.querySelector<HTMLElement>('[data-autofocus], [autofocus]');
      (auto ?? el.querySelector<HTMLElement>(FOCUSABLE) ?? el).focus();
    }, 0);
    return () => {
      clearTimeout(t);
      const back = returnFocus.current;
      returnFocus.current = null;
      if (back && document.contains(back)) back.focus?.();
    };
  }, [open]);

  if (!open || typeof document === 'undefined') return null;

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      if (dismissable) onClose();
    } else if (e.key === 'Tab' && ref.current) {
      const f = Array.from(ref.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!f.length) return;
      const first = f[0];
      const last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  };

  return createPortal(
    <div
      className="gh-dialog-backdrop"
      onMouseDown={(e) => {
        if (dismissable && e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        className={cx('gh-dialog', 'gh-dialog--generic', className)}
        style={{ width: `min(${width}px, 100%)` }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        tabIndex={-1}
        onKeyDown={onKeyDown}
      >
        <div className="gh-card__header">
          <div style={{ minWidth: 0 }}>
            <div className="gh-card__title" id={`${id}-title`}>
              {title}
            </div>
            {kicker !== undefined ? <div className="gh-card__kicker">{kicker}</div> : null}
          </div>
          <div className="gh-dialog__head-aside">
            {aside}
            {dismissable ? (
              <button type="button" className="gh-dialog__close" aria-label="Đóng" onClick={onClose}>
                <Icon name="ph ph-x" size={14} />
              </button>
            ) : null}
          </div>
        </div>
        <div className={cx('gh-dialog__scroll', padded && 'gh-dialog__body')}>{children}</div>
        {actions ? <div className="gh-dialog__actions">{actions}</div> : null}
      </div>
    </div>,
    document.body,
  );
}
