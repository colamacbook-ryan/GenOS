import { cx } from './cx';

export interface SwitchProps {
  checked: boolean;
  onChange?: (next: boolean) => void;
  label: string;
  /** Locked boundary: rendered at .55 opacity and not toggleable. */
  locked?: boolean;
  disabled?: boolean;
  id?: string;
}

/** 30×16 pill switch; knob 10px at left 3px / 17px. */
export function Switch({ checked, onChange, label, locked, disabled, id }: SwitchProps) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      aria-disabled={locked || disabled || undefined}
      className={cx('gh-switch', locked && 'gh-switch--locked')}
      onClick={() => {
        if (!locked && !disabled) onChange?.(!checked);
      }}
    >
      <span className="gh-switch__knob" />
    </button>
  );
}
