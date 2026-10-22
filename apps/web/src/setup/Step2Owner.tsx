import { useState } from 'react';
import { ApiError } from '@gen-harness/contracts';
import { Icon, PinInput, TextField } from '@gen-harness/ui';
import { api } from '../lib/api';
import { qk } from '../lib/queries';
import { queryClient } from '../lib/queryClient';
import { PIN_RULES } from './steps';
import { StepFrame } from './StepFrame';
import { describeError, TOKEN_INVALID, type StepProps } from './types';
import { useFieldErrors } from './useFieldErrors';
import { firstHiddenError, isComplete, passwordStrength, step2Errors, type Step2Values } from './validation';

type K = keyof Step2Values;

export function Step2Owner({ meta, description, status, token, onBack, onSaved, onNext, formRef }: StepProps) {
  const [v, setV] = useState<Step2Values>({ token, display_name: '', email: '', password: '', pin: '', pin_confirm: '' });
  const [showToken, setShowToken] = useState(!token);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const clientErrors = step2Errors(v);
  const f = useFieldErrors<K>(clientErrors);
  const done = status === 'done';
  const strength = passwordStrength(v.password);

  const set = (k: K, val: string) => {
    setV((s) => ({ ...s, [k]: val }));
    f.changed(k);
  };

  const save = async () => {
    f.setSubmitted(true);
    if (!isComplete(clientErrors)) return;
    setBusy(true);
    setFormError(null);
    try {
      const state = await api.setup.step2({ ...v, display_name: v.display_name.trim(), email: v.email.trim(), token: v.token.trim() });
      await queryClient.invalidateQueries({ queryKey: qk.me });
      onSaved(state);
    } catch (e) {
      if (e instanceof ApiError && (e.code === 'SETUP_TOKEN_INVALID' || e.status === 403)) {
        setShowToken(true);
        f.setServer({ token: TOKEN_INVALID });
      } else if (e instanceof ApiError && e.status === 422) {
        f.setServer(e.fieldErrors as Partial<Record<K, string>>);
        setFormError(describeError(e));
      } else setFormError(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <StepFrame n={meta.n} title={meta.title} description={description} formRef={formRef} canContinue onContinue={onNext} onBack={onBack}>
        <div className="setup-done">
          <Icon name="ph ph-check-circle" size={16} color="var(--color-ok)" />
          Tài khoản Owner đã được tạo và đang đăng nhập.
        </div>
      </StepFrame>
    );
  }

  return (
    <StepFrame
      n={meta.n}
      title={meta.title}
      description={description}
      formRef={formRef}
      canContinue={isComplete(clientErrors) && !f.hasServerErrors}
      busy={busy}
      onContinue={() => void save()}
      onBack={onBack}
      formError={formError}
      blockedHint={firstHiddenError(clientErrors, (k) => (k === 'pin_confirm' && v.pin_confirm.length === 6 ? 'shown' : f.errorOf(k)))}
    >
      <div className="setup-fields">
        {showToken ? (
          <TextField
            label="Mã thiết lập"
            className="gh-input setup-mono"
            value={v.token}
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => set('token', e.target.value)}
            onBlur={() => f.blur('token')}
            error={f.errorOf('token')}
            hint="Cần lại mã từ trình cài vì trang vừa được tải lại."
          />
        ) : null}
        <div className="setup-grid">
          <TextField
            label="Tên hiển thị"
            autoComplete="name"
            autoFocus
            value={v.display_name}
            onChange={(e) => set('display_name', e.target.value)}
            onBlur={() => f.blur('display_name')}
            error={f.errorOf('display_name')}
          />
          <TextField
            label="Email"
            type="email"
            autoComplete="username"
            value={v.email}
            onChange={(e) => set('email', e.target.value)}
            onBlur={() => f.blur('email')}
            error={f.errorOf('email')}
          />
        </div>
        <TextField
          label="Mật khẩu"
          revealable
          autoComplete="new-password"
          value={v.password}
          onChange={(e) => set('password', e.target.value)}
          onBlur={() => f.blur('password')}
          error={f.errorOf('password')}
          hint="Ít nhất 12 ký tự. Cụm từ dài dễ nhớ an toàn hơn mật khẩu ngắn phức tạp."
          after={
            <div className="setup-strength" data-tone={strength.tone} aria-live="polite">
              <span className="setup-strength__bar">
                <span className="setup-strength__fill" style={{ width: `${(strength.score / 4) * 100}%` }} />
              </span>
              <span className="setup-strength__label">
                Độ mạnh: {strength.label}
              </span>
            </div>
          }
        />
        <div className="setup-grid">
          <PinField
            label="Mã PIN (6 số)"
            id="pin"
            value={v.pin}
            onChange={(val) => set('pin', val)}
            onBlur={() => f.blur('pin')}
            error={f.errorOf('pin')}
          />
          <PinField
            label="Nhập lại PIN"
            id="pin_confirm"
            value={v.pin_confirm}
            onChange={(val) => set('pin_confirm', val)}
            onBlur={() => f.blur('pin_confirm')}
            error={f.errorOf('pin_confirm') ?? (v.pin_confirm.length === 6 ? (clientErrors.pin_confirm ?? null) : null)}
          />
        </div>
        <div className="setup-pin-rules">
          <div className="setup-pin-rules__title">PIN dùng khi nào</div>
          {PIN_RULES.map(([k, val]) => (
            <div className="setup-pin-rules__row" key={k}>
              <span className="setup-pin-rules__key">{k}</span>
              <span className="setup-pin-rules__val">{val}</span>
            </div>
          ))}
        </div>
      </div>
    </StepFrame>
  );
}

function PinField({
  label,
  id,
  value,
  onChange,
  onBlur,
  error,
}: {
  label: string;
  id: string;
  value: string;
  onChange: (v: string) => void;
  onBlur: () => void;
  error: string | null;
}) {
  return (
    <div className="gh-field">
      <label className="gh-field__label" htmlFor={`${id}-0`}>
        {label}
      </label>
      <PinInput
        idPrefix={id}
        label={label}
        value={value}
        onChange={onChange}
        onBlur={onBlur}
        invalid={!!error}
        describedBy={error ? `${id}-error` : undefined}
      />
      {error ? (
        <div className="gh-field__error" id={`${id}-error`}>
          {error}
        </div>
      ) : null}
    </div>
  );
}
