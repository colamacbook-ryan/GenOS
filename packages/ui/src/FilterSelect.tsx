import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { cx } from './cx';
import { Icon } from './Icon';

export interface FilterOption<V extends string = string> {
  value: V;
  label: ReactNode;
}

export interface FilterSelectProps<V extends string = string> {
  /** Muted prefix ("Kênh"). */
  label: string;
  value: V;
  options: FilterOption<V>[];
  onChange: (v: V) => void;
  /** Text shown for the current value (defaults to the option's label). */
  display?: ReactNode;
  disabled?: boolean;
  className?: string;
}

/**
 * Filter chip from the design's `rawFilters`: 26px pill, muted label, value,
 * caret; opens a listbox. Arrow keys move, Enter/Space pick, Esc closes.
 */
export function FilterSelect<V extends string>({ label, value, options, onChange, display, disabled, className }: FilterSelectProps<V>) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const wrap = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const id = useId();
  const current = options.find((o) => o.value === value);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  useEffect(() => {
    if (open) list.current?.focus();
  }, [open]);

  const openList = () => {
    setActive(Math.max(0, options.findIndex((o) => o.value === value)));
    setOpen(true);
  };
  const pick = (i: number) => {
    const o = options[i];
    if (o) onChange(o.value);
    setOpen(false);
    btn.current?.focus();
  };
  const onListKey = (e: KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((a) => Math.min(options.length - 1, a + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === 'Home') {
      e.preventDefault();
      setActive(0);
    } else if (e.key === 'End') {
      e.preventDefault();
      setActive(options.length - 1);
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      pick(active);
    } else if (e.key === 'Escape' || e.key === 'Tab') {
      e.preventDefault();
      setOpen(false);
      btn.current?.focus();
    }
  };

  return (
    <div className={cx('gh-filter', className)} ref={wrap}>
      <button
        ref={btn}
        type="button"
        className="gh-filter__chip"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? `${id}-list` : undefined}
        aria-label={`${label}: ${typeof (display ?? current?.label) === 'string' ? (display ?? current?.label) : ''}`.trim()}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : openList())}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' && !open) {
            e.preventDefault();
            openList();
          }
        }}
      >
        <span className="gh-filter__label">{label}</span>
        {display ?? current?.label}
        <Icon name="ph ph-caret-down" size={11} className="gh-filter__caret" />
      </button>
      {open ? (
        <ul
          ref={list}
          id={`${id}-list`}
          role="listbox"
          aria-label={label}
          tabIndex={-1}
          className="gh-filter__list"
          aria-activedescendant={`${id}-opt-${active}`}
          onKeyDown={onListKey}
        >
          {options.map((o, i) => (
            <li
              key={o.value}
              id={`${id}-opt-${i}`}
              role="option"
              aria-selected={o.value === value}
              data-active={i === active || undefined}
              className="gh-filter__opt"
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick(i)}
            >
              <span className="gh-filter__opt-text">{o.label}</span>
              {o.value === value ? <Icon name="ph ph-check" size={12} className="gh-filter__check" /> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
