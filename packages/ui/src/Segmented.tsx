import { useRef, type KeyboardEvent, type ReactNode } from 'react';
import { cx } from './cx';
import { Icon } from './Icon';

export interface SegmentedOption<V extends string = string> {
  value: V;
  label: ReactNode;
  icon?: string;
  disabled?: boolean;
}

export interface SegmentedProps<V extends string = string> {
  options: SegmentedOption<V>[];
  value: V;
  onChange: (v: V) => void;
  label: string;
  block?: boolean;
  className?: string;
}

/** Segmented control: neutral-800 border, 2px padding, selected item on nav-active. */
export function Segmented<V extends string>({ options, value, onChange, label, block, className }: SegmentedProps<V>) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const enabled = options.map((o, i) => (o.disabled ? -1 : i)).filter((i) => i >= 0);
  const onKey = (e: KeyboardEvent, i: number) => {
    const pos = enabled.indexOf(i);
    let next = -1;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = enabled[(pos + 1) % enabled.length];
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = enabled[(pos - 1 + enabled.length) % enabled.length];
    if (next >= 0) {
      e.preventDefault();
      refs.current[next]?.focus();
      onChange(options[next].value);
    }
  };
  return (
    <div role="radiogroup" aria-label={label} className={cx('gh-seg', block && 'gh-seg--block', className)}>
      {options.map((o, i) => {
        const checked = o.value === value;
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={checked}
            tabIndex={checked ? 0 : -1}
            disabled={o.disabled}
            className="gh-seg__opt"
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => onKey(e, i)}
          >
            {o.icon ? <Icon name={o.icon} size={13} /> : null}
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
