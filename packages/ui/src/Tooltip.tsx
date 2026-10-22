import {
  cloneElement,
  isValidElement,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
  type SyntheticEvent,
} from 'react';
import { createPortal } from 'react-dom';

export interface TooltipProps {
  content: ReactNode;
  /** Secondary line (e.g. the English name in rail mode). */
  sub?: ReactNode;
  placement?: 'bottom' | 'right' | 'top';
  /** ms before showing on hover (focus shows immediately). */
  delay?: number;
  disabled?: boolean;
  children: ReactElement;
}

type Handler = (e: SyntheticEvent) => void;

/**
 * Hover/focus tooltip. The child keeps its own accessible name; the tooltip is
 * linked with aria-describedby. Esc hides it.
 */
export function Tooltip({ content, sub, placement = 'bottom', delay = 350, disabled, children }: TooltipProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const anchor = useRef<HTMLElement | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const place = useCallback(() => {
    const el = anchor.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    if (placement === 'right') setPos({ left: r.right + 8, top: r.top + r.height / 2 });
    else if (placement === 'top') setPos({ left: r.left + r.width / 2, top: r.top - 8 });
    else setPos({ left: r.left + r.width / 2, top: r.bottom + 8 });
  }, [placement]);

  const show = useCallback(
    (immediate: boolean) => {
      if (disabled) return;
      if (timer.current) clearTimeout(timer.current);
      const go = () => {
        place();
        setOpen(true);
      };
      if (immediate) go();
      else timer.current = setTimeout(go, delay);
    },
    [delay, disabled, place],
  );
  const hide = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    setOpen(false);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') hide();
    };
    window.addEventListener('keydown', onKey);
    window.addEventListener('scroll', hide, true);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', hide, true);
    };
  }, [open, hide]);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  if (!isValidElement(children)) return children;
  const props = children.props as Record<string, unknown>;
  const chain = (name: string, fn: Handler): Handler => (e) => {
    (props[name] as Handler | undefined)?.(e);
    fn(e);
  };

  const child = cloneElement(children as ReactElement<Record<string, unknown>>, {
    ref: (el: HTMLElement | null) => {
      anchor.current = el;
      const r = (children as unknown as { ref?: unknown }).ref;
      if (typeof r === 'function') r(el);
      else if (r && typeof r === 'object') (r as { current: unknown }).current = el;
    },
    'aria-describedby': open && !disabled ? id : (props['aria-describedby'] as string | undefined),
    onMouseEnter: chain('onMouseEnter', () => show(false)),
    onMouseLeave: chain('onMouseLeave', hide),
    onFocus: chain('onFocus', () => show(true)),
    onBlur: chain('onBlur', hide),
  });

  const transform =
    placement === 'right' ? 'translateY(-50%)' : placement === 'top' ? 'translate(-50%, -100%)' : 'translateX(-50%)';

  return (
    <>
      {child}
      {open && pos && !disabled && typeof document !== 'undefined'
        ? createPortal(
            <div role="tooltip" id={id} className="gh-tooltip" style={{ left: pos.left, top: pos.top, transform }}>
              {content}
              {sub ? <span className="gh-tooltip__sub">{sub}</span> : null}
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
