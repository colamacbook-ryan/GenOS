import { useState } from 'react';
import { ApiError } from '@gen-harness/contracts';
import { Icon, TextField } from '@gen-harness/ui';
import { api } from '../lib/api';
import { StepFrame } from './StepFrame';
import { describeError, TOKEN_INVALID, type StepProps } from './types';
import { useFieldErrors } from './useFieldErrors';
import { isComplete, step1Errors, type Step1Values } from './validation';

export function Step1Welcome({ meta, description, status, token, setToken, onSaved, onNext, formRef }: StepProps) {
  // v0.1.43 (F-23): bỏ lựa chọn 'Dùng dữ liệu mẫu' — tin mẫu ghi vào raw.events sẽ ở lại mãi. Luôn bắt đầu trống,
  // luôn gửi language 'vi' (chưa có bản dịch giao diện tiếng Anh).
  const [v, setV] = useState<Step1Values>({ token, language: 'vi', mode: 'empty' });
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const clientErrors = step1Errors(v);
  const f = useFieldErrors<keyof Step1Values>(clientErrors);
  const done = status === 'done';

  const save = async () => {
    f.setSubmitted(true);
    if (!isComplete(clientErrors)) return;
    setBusy(true);
    setFormError(null);
    try {
      const body = { ...v, token: v.token.trim() };
      const state = await api.setup.step1(body);
      setToken(body.token);
      onSaved(state);
    } catch (e) {
      if (e instanceof ApiError && (e.code === 'SETUP_TOKEN_INVALID' || e.status === 403)) f.setServer({ token: TOKEN_INVALID });
      else if (e instanceof ApiError && e.status === 422) f.setServer(e.fieldErrors as Partial<Record<keyof Step1Values, string>>);
      else setFormError(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <StepFrame
      n={meta.n}
      title={meta.title}
      description={description}
      formRef={formRef}
      canContinue={done || (isComplete(clientErrors) && !f.hasServerErrors)}
      busy={busy}
      onContinue={done ? onNext : () => void save()}
      formError={formError}
    >
      {done ? (
        <div className="setup-done">
          <Icon name="ph ph-check-circle" size={16} color="var(--color-ok)" />
          Mã thiết lập đã được xác nhận. Tiếp tục để tạo tài khoản Owner.
        </div>
      ) : (
        <div className="setup-fields">
          <TextField
            label="Mã thiết lập"
            className="gh-input setup-mono"
            value={v.token}
            autoFocus={!v.token}
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => {
              setV({ ...v, token: e.target.value });
              f.changed('token');
            }}
            onBlur={() => f.blur('token')}
            error={f.errorOf('token')}
            hint="Hiện trong màn hình dòng lệnh của trình cài — tự điền khi mở từ đường dẫn của trình cài."
          />
        </div>
      )}
    </StepFrame>
  );
}
