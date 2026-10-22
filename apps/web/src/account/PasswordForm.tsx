import { useState, type FormEvent, type ReactNode } from 'react';
import type { SessionsRevoked } from '@gen-harness/contracts';
import { Button, TextField } from '@gen-harness/ui';
import { api } from '../lib/api';
import { errorText } from '../lib/errorText';
import { useFieldErrors } from '../setup/useFieldErrors';
import { passwordStrength } from '../setup/validation';
import { hasErrors, passwordErrors, serverFieldErrors, type PasswordValues } from './accountModel';

type K = keyof PasswordValues;
const EMPTY: PasswordValues = { current_password: '', new_password: '', new_password_confirm: '' };

/**
 * Đổi mật khẩu (POST /account/password) — dùng chung cho mục "Đổi mật khẩu" ở Tài khoản của tôi và màn buộc
 * "Đặt mật khẩu mới" sau `genh reset-password`. Mật khẩu hiện tại sai → lỗi ngay dưới ô đó (422 từ API).
 */
export function PasswordForm({
  currentLabel = 'Mật khẩu hiện tại',
  submitLabel = 'Đổi mật khẩu',
  autoFocus,
  block,
  onDone,
  footer,
}: {
  currentLabel?: string;
  submitLabel?: string;
  autoFocus?: boolean;
  block?: boolean;
  onDone: (r: SessionsRevoked) => void | Promise<void>;
  footer?: ReactNode;
}) {
  const [v, setV] = useState<PasswordValues>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const clientErrors = passwordErrors(v);
  const f = useFieldErrors<K>(clientErrors);
  const strength = passwordStrength(v.new_password);

  const set = (k: K, val: string) => {
    setV((s) => ({ ...s, [k]: val }));
    f.changed(k);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    f.setSubmitted(true);
    if (hasErrors(clientErrors)) return;
    setBusy(true);
    setFormError(null);
    try {
      const r = await api.account.changePassword({ current_password: v.current_password, new_password: v.new_password });
      setV(EMPTY);
      f.setSubmitted(false);
      await onDone(r);
    } catch (err) {
      const fields = serverFieldErrors(err);
      if (fields) f.setServer(fields as Partial<Record<K, string>>);
      else setFormError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="acct-form" onSubmit={submit} noValidate aria-label={submitLabel}>
      <TextField
        label={currentLabel}
        revealable
        autoComplete="current-password"
        autoFocus={autoFocus}
        value={v.current_password}
        onChange={(e) => set('current_password', e.target.value)}
        onBlur={() => f.blur('current_password')}
        error={f.errorOf('current_password')}
      />
      <TextField
        label="Mật khẩu mới"
        revealable
        autoComplete="new-password"
        value={v.new_password}
        onChange={(e) => set('new_password', e.target.value)}
        onBlur={() => f.blur('new_password')}
        error={f.errorOf('new_password')}
        hint="Ít nhất 12 ký tự. Cụm từ dài dễ nhớ an toàn hơn mật khẩu ngắn phức tạp."
        after={
          v.new_password ? (
            <div className="setup-strength" data-tone={strength.tone} aria-live="polite">
              <span className="setup-strength__bar">
                <span className="setup-strength__fill" style={{ width: `${(strength.score / 4) * 100}%` }} />
              </span>
              <span className="setup-strength__label">Độ mạnh: {strength.label}</span>
            </div>
          ) : null
        }
      />
      <TextField
        label="Nhập lại mật khẩu mới"
        revealable
        autoComplete="new-password"
        value={v.new_password_confirm}
        onChange={(e) => set('new_password_confirm', e.target.value)}
        onBlur={() => f.blur('new_password_confirm')}
        error={f.errorOf('new_password_confirm')}
      />
      <div className="acct-form__error" role="alert">
        {formError}
      </div>
      <div className="acct-form__actions">
        <Button variant="primary" type="submit" loading={busy} block={block} icon="ph ph-lock-key">
          {submitLabel}
        </Button>
        {footer}
      </div>
    </form>
  );
}
