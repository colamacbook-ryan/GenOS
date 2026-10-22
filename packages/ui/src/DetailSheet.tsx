import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { cx } from './cx';
import { Icon } from './Icon';

/**
 * v0.1.59 (G2) — DetailSheet: ngăn chi tiết của Mặt tiền (CSS: `kit-sheet` trong owner-kit.css).
 *
 *   ≥ 760px  ngăn bên PHẢI rộng 480px (nền mờ phía sau, bấm nền để đóng), tiêu đề + nút đóng ×
 *   < 760px  TOÀN MÀN HÌNH, có nút "← Quay lại" ở đầu
 *
 * Esc đóng (nghe ở cấp cửa sổ để Esc đóng được cả khi tiêu điểm đang ở <body>); khi có hộp thoại khác đang mở ở trên (hộp
 * PIN) thì Esc chỉ đóng hộp đó, không đóng ngăn. Tiêu điểm vào ngăn khi mở, bẫy Tab trong ngăn, trả về chỗ cũ khi đóng.
 * `bare`: bỏ thanh tiêu đề — dành cho nội dung tự có thanh riêng (khung Gen); tên ngăn vẫn dành cho trình đọc màn hình.
 */
export interface DetailSheetProps {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  children?: ReactNode;
  backLabel?: string;
  closeLabel?: string;
  bare?: boolean;
  /** Trả `false` ⇒ Esc KHÔNG đóng lúc này (vd Gen đang làm sáng một phần tử thì Esc chỉ tắt lớp phủ). */
  escapeEnabled?: () => boolean;
  className?: string;
  'data-testid'?: string;
  'data-spot'?: 'on';
}

const FOCUSABLE =
  'a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Các ngăn đang mở theo thứ tự mở — chỉ ngăn trên cùng nhận Esc. */
const openSheets: symbol[] = [];

export function DetailSheet({
  open,
  title,
  onClose,
  children,
  backLabel = 'Quay lại',
  closeLabel = 'Đóng',
  bare,
  escapeEnabled,
  className,
  'data-testid': testId,
  'data-spot': spot,
}: DetailSheetProps) {
  const id = useId();
  const panel = useRef<HTMLDivElement>(null);
  const latest = useRef({ onClose, escapeEnabled });
  latest.current = { onClose, escapeEnabled };

  useEffect(() => {
    if (!open) return;
    const me = Symbol('detail-sheet');
    openSheets.push(me);
    const returnFocus = (document.activeElement as HTMLElement | null) ?? null;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== 'Escape' || openSheets.at(-1) !== me) return;
      // Esc đang ở trong một hộp thoại khác (hộp PIN…) ⇒ hộp đó lo, không đóng ngăn.
      const dlg = e.target instanceof Element ? e.target.closest('[role="dialog"]') : null;
      if (dlg && dlg !== panel.current) return;
      if (latest.current.escapeEnabled && !latest.current.escapeEnabled()) return;
      latest.current.onClose();
    };
    window.addEventListener('keydown', onKey, true);
    const t = setTimeout(() => {
      const el = panel.current;
      if (!el || el.contains(document.activeElement)) return;
      (el.querySelector<HTMLElement>(FOCUSABLE) ?? el).focus();
    }, 0);
    return () => {
      clearTimeout(t);
      window.removeEventListener('keydown', onKey, true);
      const i = openSheets.lastIndexOf(me);
      if (i >= 0) openSheets.splice(i, 1);
      if (returnFocus && document.contains(returnFocus)) returnFocus.focus?.();
    };
  }, [open]);

  if (!open || typeof document === 'undefined') return null;

  const onTab = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Tab' || !panel.current) return;
    const f = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (f.length === 0) return;
    const first = f[0];
    const last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  const name = typeof title === 'string' ? title : 'Chi tiết';
  return createPortal(
    <div className={cx('kit-sheet', className)} data-testid={testId} data-spot={spot}>
      <div className="kit-sheet__scrim" aria-hidden onMouseDown={onClose} />
      <div
        ref={panel}
        className={cx('kit-sheet__panel', bare && 'kit-sheet__panel--bare')}
        role="dialog"
        aria-modal="true"
        aria-label={bare ? name : undefined}
        aria-labelledby={bare ? undefined : `${id}-title`}
        tabIndex={-1}
        onKeyDown={onTab}
      >
        {bare ? null : (
          <header className="kit-sheet__head">
            <button type="button" className="kit-sheet__back" onClick={onClose}>
              <Icon name="ph ph-arrow-left" size={16} />
              {backLabel}
            </button>
            <h2 className="kit-sheet__title" id={`${id}-title`}>
              {title}
            </h2>
            <button type="button" className="kit-sheet__close" aria-label={closeLabel} onClick={onClose}>
              <Icon name="ph ph-x" size={20} />
            </button>
          </header>
        )}
        <div className={cx('kit-sheet__body', bare && 'kit-sheet__body--bare')}>{children}</div>
      </div>
    </div>,
    document.body,
  );
}
