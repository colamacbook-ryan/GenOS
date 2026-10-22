import { useEffect, useState, type FormEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { OrgSettings } from '@gen-harness/contracts';
import { Button, EmptyState, SelectField, TextField } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { errorText } from '../../lib/errorText';
import { useCan } from '../../lib/permissions';
import { qk } from '../../lib/queries';
import { queryClient } from '../../lib/queryClient';
import { toast } from '../../lib/toast';
import { serverFieldErrors } from '../../account/accountModel';
import { CURRENCIES, TIMEZONES } from '../../setup/steps';
import { useFieldErrors } from '../../setup/useFieldErrors';
import { addressingPreview, isComplete, step3Errors, type Step3Values } from '../../setup/validation';
import { CardError, Panel, SkeletonLines } from '../common';
import { ORG_KEY } from './usersModel';

type K = keyof Step3Values;

const valuesOf = (o: OrgSettings): Step3Values => ({
  org_name: o.org_name,
  timezone: o.timezone,
  currency: o.currency,
  self_name: o.self_name,
  bot_calls_me: o.bot_calls_me,
});

/** Múi giờ đang dùng có thể nằm ngoài danh sách gợi ý của bước 3 — vẫn giữ nó trong ô chọn. */
function withCurrent(options: Array<{ value: string; label: string }>, current: string) {
  return options.some((o) => o.value === current) ? options : [{ value: current, label: current }, ...options];
}

/** Điều khiển hệ thống › Tổ chức (v0.1.22, Đợt B2): sửa thông tin công ty + xưng hô sau khi thiết lập (như bước 3). */
export function OrgTab() {
  const canRead = useCan('system.read');
  const q = useQuery({ queryKey: ORG_KEY, queryFn: ({ signal }) => api.org.get(signal), enabled: canRead });
  if (!canRead) {
    return (
      <div className="gh-card">
        <EmptyState icon="ph ph-lock-simple" title="Vai trò của bạn không xem được thông tin tổ chức" description="Chỉ vai trò có quyền xem hệ thống mới thấy mục này." />
      </div>
    );
  }
  return (
    <Panel flush
      title="Tổ chức & xưng hô"
      kicker="Như bước 3 của trình thiết lập · chỉ Owner sửa · ghi Nhật ký hành động"
      label="Tổ chức & xưng hô"
      genTarget="system.org.form"
      style={{ maxWidth: 640 }}
    >
      {q.isPending ? (
        <SkeletonLines rows={5} padding="10px 16px" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : (
        <OrgForm org={q.data} />
      )}
    </Panel>
  );
}

function OrgForm({ org }: { org: OrgSettings }) {
  const [v, setV] = useState<Step3Values>(valuesOf(org));
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const clientErrors = step3Errors(v);
  const f = useFieldErrors<K>(clientErrors);
  const saved = valuesOf(org);
  const dirty = (Object.keys(saved) as K[]).some((k) => v[k].trim() !== saved[k]);
  const readOnly = !org.can_edit;

  useEffect(() => setV(valuesOf(org)), [org]);

  const set = (k: K, val: string) => {
    setV((s) => ({ ...s, [k]: val }));
    f.changed(k);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    f.setSubmitted(true);
    if (!isComplete(clientErrors) || !dirty) return;
    setBusy(true);
    setFormError(null);
    try {
      const next = await api.org.update({
        org_name: v.org_name.trim(),
        timezone: v.timezone,
        currency: v.currency,
        self_name: v.self_name.trim(),
        bot_calls_me: v.bot_calls_me.trim(),
      });
      queryClient.setQueryData(ORG_KEY, next);
      await queryClient.invalidateQueries({ queryKey: qk.me });
      f.setSubmitted(false);
      toast('Đã lưu thông tin tổ chức.');
    } catch (err) {
      const fields = serverFieldErrors(err);
      if (fields) f.setServer(fields as Partial<Record<K, string>>);
      else setFormError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const text = (k: K, label: string, extra: Partial<Parameters<typeof TextField>[0]> = {}) => (
    <TextField label={label} value={v[k]} onChange={(e) => set(k, e.target.value)} onBlur={() => f.blur(k)} error={f.errorOf(k)} disabled={readOnly} {...extra} />
  );

  const currencies = org.currencies.map((c) => CURRENCIES.find((x) => x.value === c) ?? { value: c, label: c });

  return (
    <form className="acct-form org-form" onSubmit={submit} noValidate aria-label="Tổ chức & xưng hô">
      {text('org_name', 'Tên tổ chức', { autoComplete: 'organization' })}
      <div className="setup-grid">
        <SelectField label="Múi giờ" value={v.timezone} options={withCurrent(TIMEZONES, v.timezone)} onChange={(e) => set('timezone', e.target.value)} error={f.errorOf('timezone')} disabled={readOnly} />
        <SelectField label="Tiền tệ" value={v.currency} options={withCurrent(currencies, v.currency)} onChange={(e) => set('currency', e.target.value)} error={f.errorOf('currency')} disabled={readOnly} />
      </div>
      <div className="setup-grid">
        {text('self_name', 'Sếp tự xưng là', { placeholder: 'Anh, Chị, Tôi…' })}
        {text('bot_calls_me', 'Agent gọi Sếp là', { placeholder: 'Sếp, Anh, Chị…' })}
      </div>
      <div className="setup-preview">
        <div className="setup-preview__label">Xem trước</div>
        <div className="setup-preview__line" aria-live="polite">
          {addressingPreview(v.self_name, v.bot_calls_me)}
        </div>
      </div>
      <p className="muted-note">Múi giờ dùng cho lịch sao lưu, nhắc hẹn và giờ hiển thị trên Console. Bản tin Gen, chi phí AI trong ngày và các việc dọn dữ liệu ban đêm luôn tính theo giờ Việt Nam, không đổi theo múi giờ này. Đổi xưng hô áp dụng cho các câu agent soạn từ giờ trở đi.</p>
      <div className="acct-form__error" role="alert">
        {formError}
      </div>
      {readOnly ? (
        <p className="muted-note">Chỉ Owner sửa được thông tin tổ chức.</p>
      ) : (
        <div className="acct-form__actions">
          <Button variant="primary" type="submit" loading={busy} disabled={!dirty && !busy} icon="ph ph-floppy-disk" data-gen-target="system.org.save">
            Lưu thông tin tổ chức
          </Button>
        </div>
      )}
    </form>
  );
}
