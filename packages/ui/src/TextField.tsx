import { forwardRef, useId, useState, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from 'react';
import { Icon } from './Icon';

interface FieldFrameProps {
  label: ReactNode;
  error?: string | null;
  hint?: ReactNode;
  id: string;
  children: ReactNode;
  after?: ReactNode;
}

function FieldFrame({ label, error, hint, id, children, after }: FieldFrameProps) {
  return (
    <div className="gh-field">
      <label className="gh-field__label" htmlFor={id}>
        {label}
      </label>
      {children}
      {after}
      {error ? (
        <div className="gh-field__error" id={`${id}-error`}>
          {error}
        </div>
      ) : hint ? (
        <div className="gh-field__hint" id={`${id}-hint`}>
          {hint}
        </div>
      ) : null}
    </div>
  );
}

export interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  label: ReactNode;
  /** Inline error — 11px BAD under the field (docs/handoff/06 "Hành vi"). */
  error?: string | null;
  hint?: ReactNode;
  id?: string;
  /** Adds a show/hide toggle for password fields. */
  revealable?: boolean;
  /** Extra content rendered between the input and the error line. */
  after?: ReactNode;
}

export const TextField = forwardRef<HTMLInputElement, TextFieldProps>(function TextField(
  { label, error, hint, id: idProp, revealable, type = 'text', after, ...rest },
  ref,
) {
  const auto = useId();
  const id = idProp ?? auto;
  const [shown, setShown] = useState(false);
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined;
  const input = (
    <input
      ref={ref}
      id={id}
      type={revealable ? (shown ? 'text' : 'password') : type}
      className="gh-input"
      aria-invalid={error ? true : undefined}
      aria-describedby={describedBy}
      {...rest}
    />
  );
  return (
    <FieldFrame label={label} error={error} hint={hint} id={id} after={after}>
      {revealable ? (
        <div className="gh-input-wrap">
          {input}
          <button
            type="button"
            className="gh-input-wrap__btn"
            aria-label={shown ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}
            aria-pressed={shown}
            onClick={() => setShown((s) => !s)}
          >
            <Icon name={shown ? 'ph ph-eye-slash' : 'ph ph-eye'} size={15} />
          </button>
        </div>
      ) : (
        input
      )}
    </FieldFrame>
  );
});

export interface SelectFieldProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'id'> {
  label: ReactNode;
  error?: string | null;
  hint?: ReactNode;
  id?: string;
  options: Array<{ value: string; label: string }>;
}

export const SelectField = forwardRef<HTMLSelectElement, SelectFieldProps>(function SelectField(
  { label, error, hint, id: idProp, options, ...rest },
  ref,
) {
  const auto = useId();
  const id = idProp ?? auto;
  return (
    <FieldFrame label={label} error={error} hint={hint} id={id}>
      <select
        ref={ref}
        id={id}
        className="gh-input"
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        {...rest}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </FieldFrame>
  );
});
