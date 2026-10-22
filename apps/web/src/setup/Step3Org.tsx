import { useState } from 'react';
import { ApiError } from '@gen-harness/contracts';
import { SelectField, TextField } from '@gen-harness/ui';
import { api } from '../lib/api';
import { qk } from '../lib/queries';
import { queryClient } from '../lib/queryClient';
import { CURRENCIES, TIMEZONES } from './steps';
import { StepFrame } from './StepFrame';
import { describeError, type StepProps } from './types';
import { useFieldErrors } from './useFieldErrors';
import { DEFAULT_ADDRESSING, addressingPreview, firstHiddenError, isComplete, step3Errors, type Step3Values } from './validation';

type K = keyof Step3Values;

export function Step3Org({ meta, description, onBack, onSaved, formRef }: StepProps) {
  const [v, setV] = useState<Step3Values>({
    org_name: '',
    timezone: 'Asia/Ho_Chi_Minh',
    currency: 'VND',
    // v0.1.55 (Thiết lập gọn): hai ô xưng hô điền sẵn "Sếp" — sửa được; chỉ tên tổ chức là ô bắt buộc phải gõ.
    self_name: DEFAULT_ADDRESSING,
    bot_calls_me: DEFAULT_ADDRESSING,
  });
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const clientErrors = step3Errors(v);
  const f = useFieldErrors<K>(clientErrors);

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
      const state = await api.setup.step3({
        ...v,
        org_name: v.org_name.trim(),
        self_name: v.self_name.trim(),
        bot_calls_me: v.bot_calls_me.trim(),
      });
      void queryClient.invalidateQueries({ queryKey: qk.me });
      onSaved(state);
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) f.setServer(e.fieldErrors as Partial<Record<K, string>>);
      setFormError(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  const text = (k: K, label: string, extra: Partial<Parameters<typeof TextField>[0]> = {}) => (
    <TextField
      label={label}
      value={v[k]}
      onChange={(e) => set(k, e.target.value)}
      onBlur={() => f.blur(k)}
      error={f.errorOf(k)}
      {...extra}
    />
  );

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
      blockedHint={firstHiddenError(clientErrors, f.errorOf)}
    >
      <div className="setup-fields">
        {text('org_name', 'Tên tổ chức', { autoFocus: true, autoComplete: 'organization' })}
        <div className="setup-grid">
          <SelectField
            label="Múi giờ"
            value={v.timezone}
            options={TIMEZONES}
            onChange={(e) => set('timezone', e.target.value)}
            onBlur={() => f.blur('timezone')}
            error={f.errorOf('timezone')}
          />
          <SelectField
            label="Tiền tệ"
            value={v.currency}
            options={CURRENCIES}
            onChange={(e) => set('currency', e.target.value)}
            onBlur={() => f.blur('currency')}
            error={f.errorOf('currency')}
          />
        </div>
        <div className="setup-grid">
          {text('self_name', 'Sếp tự xưng là', { placeholder: 'Anh, Chị, Tôi…', hint: 'Điền sẵn “Sếp” — đổi nếu Sếp muốn xưng khác.' })}
          {text('bot_calls_me', 'Agent gọi Sếp là', { placeholder: 'Sếp, Anh, Chị…' })}
        </div>
        <div className="setup-preview">
          <div className="setup-preview__label">Xem trước</div>
          <div className="setup-preview__line" aria-live="polite">
            {addressingPreview(v.self_name, v.bot_calls_me)}
          </div>
        </div>
      </div>
    </StepFrame>
  );
}
