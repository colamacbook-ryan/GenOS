import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react';
import { cx } from './cx';

export interface TabItem<K extends string = string> {
  key: K;
  label: ReactNode;
  /** Mono count label after the text (design: "QR · PIN", "6 model"). */
  count?: ReactNode;
  /** Gen v1: `data-gen-target` cho nút tab. */
  genTarget?: string;
}

export interface TabsProps<K extends string = string> {
  items: TabItem<K>[];
  value: K;
  onChange: (key: K) => void;
  label: string;
  className?: string;
  /** id prefix for aria-controls wiring. */
  idPrefix?: string;
}

/** 34px tabs with a 2px accent underline; arrow keys move between tabs. */
export function Tabs<K extends string>({ items, value, onChange, label, className, idPrefix = 'tab' }: TabsProps<K>) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const listRef = useRef<HTMLDivElement>(null);
  const index = items.findIndex((t) => t.key === value);
  // v0.1.28 (UX V5): dải tab cuộn ngang (điện thoại) → đưa tab đang mở vào giữa dải; không cuộn trang.
  useEffect(() => {
    const list = listRef.current;
    const btn = refs.current[index];
    if (!list || !btn || list.scrollWidth <= list.clientWidth) return;
    const b = btn.getBoundingClientRect();
    const l = list.getBoundingClientRect();
    list.scrollLeft = Math.max(0, list.scrollLeft + (b.left - l.left) - (list.clientWidth - b.width) / 2);
  }, [index]);
  const onKey = (e: KeyboardEvent, i: number) => {
    let next = -1;
    if (e.key === 'ArrowRight') next = (i + 1) % items.length;
    else if (e.key === 'ArrowLeft') next = (i - 1 + items.length) % items.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = items.length - 1;
    if (next >= 0) {
      e.preventDefault();
      refs.current[next]?.focus();
      onChange(items[next].key);
    }
  };
  return (
    <div ref={listRef} role="tablist" aria-label={label} className={cx('gh-tabs', className)}>
      {items.map((t, i) => {
        const selected = t.key === value;
        return (
          <button
            key={t.key}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="tab"
            id={`${idPrefix}-${t.key}`}
            aria-selected={selected}
            aria-controls={`${idPrefix}-panel-${t.key}`}
            tabIndex={selected ? 0 : -1}
            className="gh-tab"
            data-gen-target={t.genTarget}
            onClick={() => onChange(t.key)}
            onKeyDown={(e) => onKey(e, i)}
          >
            {t.label}
            {t.count !== undefined ? <span className="gh-tab__count">{t.count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}
