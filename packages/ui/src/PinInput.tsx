import { forwardRef, useImperativeHandle, useRef, type ClipboardEvent, type KeyboardEvent } from 'react';

export interface PinInputProps {
  value: string;
  onChange: (value: string) => void;
  /** Called once all 6 digits are filled. */
  onComplete?: (value: string) => void;
  length?: number;
  /** Accessible name of the group, e.g. "Mã PIN". */
  label: string;
  invalid?: boolean;
  disabled?: boolean;
  autoFocus?: boolean;
  /** Mask digits (default true). */
  masked?: boolean;
  onBlur?: () => void;
  describedBy?: string;
  idPrefix?: string;
}

export interface PinInputHandle {
  focus: () => void;
}

/**
 * Six separate digit boxes, 30×36 (design `pinDigits`). Typing advances,
 * Backspace goes back, arrows move, pasting fills every box.
 */
export const PinInput = forwardRef<PinInputHandle, PinInputProps>(function PinInput(
  { value, onChange, onComplete, length = 6, label, invalid, disabled, autoFocus, masked = true, onBlur, describedBy, idPrefix },
  ref,
) {
  const boxes = useRef<Array<HTMLInputElement | null>>([]);
  const group = useRef<HTMLDivElement | null>(null);
  // Latest value, updated synchronously: focus moves happen before the parent
  // re-renders with the new `value`, so handlers must not read the stale prop.
  const latest = useRef(value);
  latest.current = value;
  const emit = (next: string) => {
    latest.current = next;
    onChange(next);
    if (next.length === length) onComplete?.(next);
  };
  useImperativeHandle(ref, () => ({
    focus: () => boxes.current[Math.min(latest.current.length, length - 1)]?.focus(),
  }));

  const digits = Array.from({ length }, (_, i) => value[i] ?? '');

  const setAt = (i: number, d: string) => {
    const arr = Array.from({ length }, (_, k) => latest.current[k] ?? '');
    arr[i] = d;
    // keep the value contiguous: cut at the first empty box
    const firstEmpty = arr.indexOf('');
    const next = (firstEmpty === -1 ? arr : arr.slice(0, firstEmpty)).join('');
    emit(next);
    return next;
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>, i: number) => {
    const cur = latest.current;
    if (e.key === 'Backspace') {
      e.preventDefault();
      if (cur[i]) {
        emit(cur.slice(0, i));
      } else if (i > 0) {
        emit(cur.slice(0, i - 1));
        boxes.current[i - 1]?.focus();
      }
    } else if (e.key === 'ArrowLeft' && i > 0) {
      e.preventDefault();
      boxes.current[i - 1]?.focus();
    } else if (e.key === 'ArrowRight' && i < length - 1) {
      e.preventDefault();
      boxes.current[Math.min(i + 1, cur.length)]?.focus();
    } else if (/^\d$/.test(e.key)) {
      e.preventDefault();
      const pos = Math.min(i, cur.length);
      const next = setAt(pos, e.key);
      if (pos < length - 1) boxes.current[Math.min(pos + 1, next.length)]?.focus();
    }
  };

  const onPaste = (e: ClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, length);
    if (!text) return;
    e.preventDefault();
    emit(text);
    boxes.current[Math.min(text.length, length - 1)]?.focus();
  };

  return (
    <div
      ref={group}
      className="gh-pin"
      role="group"
      aria-label={label}
      aria-invalid={invalid || undefined}
      aria-describedby={describedBy}
      onBlur={(e) => {
        if (onBlur && !group.current?.contains(e.relatedTarget as Node | null)) onBlur();
      }}
    >
      {digits.map((d, i) => (
        <input
          key={i}
          id={idPrefix ? `${idPrefix}-${i}` : undefined}
          ref={(el) => {
            boxes.current[i] = el;
          }}
          className="gh-pin__box"
          type={masked ? 'password' : 'text'}
          inputMode="numeric"
          pattern="[0-9]*"
          autoComplete={i === 0 ? 'one-time-code' : 'off'}
          maxLength={1}
          value={d}
          disabled={disabled}
          autoFocus={autoFocus && i === 0}
          aria-label={`${label} — chữ số ${i + 1}/${length}`}
          onChange={(e) => {
            // mobile keyboards / autofill that bypass keydown
            const v = e.target.value.replace(/\D/g, '');
            if (v.length > 1) {
              const text = (latest.current.slice(0, i) + v).slice(0, length);
              emit(text);
              boxes.current[Math.min(text.length, length - 1)]?.focus();
            } else if (v) {
              const next = setAt(Math.min(i, latest.current.length), v);
              boxes.current[Math.min(next.length, length - 1)]?.focus();
            }
          }}
          onKeyDown={(e) => onKeyDown(e, i)}
          onPaste={onPaste}
          onFocus={(e) => {
            // never leave a hole: jump to the first empty box
            if (i > latest.current.length) boxes.current[latest.current.length]?.focus();
            else e.target.select();
          }}
        />
      ))}
    </div>
  );
});
