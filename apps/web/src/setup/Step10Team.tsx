import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button, EmptyState, Icon, SelectField, TextField } from '@gen-harness/ui';
import { PinCancelledError } from '@gen-harness/contracts';
import { api } from '../lib/api';
import { errorText } from '../lib/errorText';
import { qk } from '../lib/queries';
import { PinHint } from '../screens/common';
import { StepFrame } from './StepFrame';
import { describeError, type StepProps } from './types';

type InviteRole = 'manager' | 'operator' | 'agent_staff' | 'auditor';
const ROLE_OPTIONS: Array<{ value: InviteRole; label: string }> = [
  { value: 'manager', label: 'Quản lý — thấy việc được giao' },
  { value: 'operator', label: 'Vận hành — xử lý hàng đợi việc' },
  { value: 'agent_staff', label: 'Nhân viên phụ trách — chỉ khách được phân' },
  { value: 'auditor', label: 'Kiểm soát — chỉ xem' },
];

interface Row {
  id: number;
  display_name: string;
  email: string;
  role: InviteRole;
}
let rowSeq = 0;
const emptyRow = (): Row => ({ id: ++rowSeq, display_name: '', email: '', role: 'operator' });

interface InvitedRow {
  id: string;
  display_name: string;
  email: string;
  role: string;
  temp_password: string;
}

const PIN_TEXT = 'Sau Hoàn tất, mời thêm người (tạo tài khoản) cần mã PIN';

/**
 * Bước 10 — Mời đội ngũ (tuỳ chọn, PLAN 4.6). v0.1.55 (Thiết lập gọn): mở ra là THẺ GỢI Ý có nút "Để sau" (nút chính) và "Mời
 * ngay" (hiện form mời); mở lại sau Hoàn tất (từ Hướng dẫn) thì hiện thẳng form. v0.1.68 (F038): danh sách rỗng KHÔNG lưu
 * được (không ghi "Đã xong: Mời người trong đội" khi chưa mời ai) — "Tiếp tục" chỉ bật khi có người đủ tên + email.
 */
export function Step10Team({ meta, description, onBack, onSaved, formRef, onSkip, skipping, skipError }: StepProps) {
  // v0.1.45 (F-20): mở lại từ trang Hướng dẫn SAU Hoàn tất → mời người cần phiên PIN `user.manage` (hộp PIN tự mở).
  const finished = useQuery({ queryKey: qk.setupState, queryFn: ({ signal }) => api.setup.state(signal) }).data?.finished === true;
  const [formOpen, setFormOpen] = useState(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [invited, setInvited] = useState<InvitedRow[] | null>(null);
  const [nextStateReady, setNextStateReady] = useState<(() => void) | null>(null);

  const addRow = () => setRows((r) => [...r, emptyRow()]);
  const removeRow = (id: number) => setRows((r) => r.filter((x) => x.id !== id));
  const updateRow = (id: number, patch: Partial<Row>) => setRows((r) => r.map((x) => (x.id === id ? { ...x, ...patch } : x)));

  const isFilled = (r: Row) => r.display_name.trim() !== '' && r.email.trim() !== '';
  const isPartial = (r: Row) => (r.display_name.trim() !== '') !== (r.email.trim() !== '');
  const hasPartial = rows.some(isPartial);
  // v0.1.68 (F038): danh sách rỗng KHÔNG phải "đã mời" — phải có ít nhất một dòng đủ tên + email; chưa muốn mời thì bấm "Để sau".
  const canContinue = !hasPartial && rows.some(isFilled);
  // Trình thiết lập có nút "Để sau"; mở từ Hướng dẫn thì không có (onSkip rỗng) — lối ra là "Quay lại".
  const exitLabel = onSkip ? 'Để sau' : 'Quay lại';

  const save = async () => {
    setBusy(true);
    setFormError(null);
    try {
      const state = await api.setup.step10({
        invites: rows.filter(isFilled).map((r) => ({ display_name: r.display_name.trim(), email: r.email.trim().toLowerCase(), role: r.role })),
      });
      if (state.invited.length > 0) {
        setInvited(state.invited);
        setNextStateReady(() => () => onSaved(state));
      } else {
        onSaved(state);
      }
    } catch (e) {
      // v0.1.45: huỷ hộp PIN → câu chung 'Đã huỷ — thao tác cần mã PIN.' (errorText), lỗi khác như cũ.
      setFormError(e instanceof PinCancelledError ? errorText(e) : describeError(e));
    } finally {
      setBusy(false);
    }
  };

  if (invited) {
    return (
      <StepFrame
        n={meta.n}
        title={meta.title}
        description={description}
        canContinue
        onContinue={() => nextStateReady?.()}
        onBack={onBack}
        continueLabel="Đã lưu, sang bước sau"
      >
        <div className="setup-section">
          <div className="setup-section__title">Đã tạo {invited.length} tài khoản — chưa gửi thư mời thật</div>
          <p className="muted-note">Hệ thống chưa tự gửi email mời: mật khẩu tạm hiện ngay dưới đây, Sếp tự gửi qua kênh riêng (Zalo, email cá nhân…) rồi đổi lại ở lần đăng nhập đầu.</p>
          <div className="invite-result-list">
            {invited.map((inv) => (
              <div className="invite-result-row" key={inv.id}>
                <Icon name="ph ph-user-plus" size={15} color="var(--color-accent-300)" />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="invite-result-row__name">
                    {inv.display_name} · {ROLE_OPTIONS.find((r) => r.value === inv.role)?.label ?? inv.role}
                  </div>
                  <div className="invite-result-row__email">{inv.email}</div>
                </div>
                <span className="mono invite-result-row__pw">{inv.temp_password}</span>
              </div>
            ))}
          </div>
        </div>
      </StepFrame>
    );
  }

  if (!formOpen && !finished && onSkip) {
    return (
      <StepFrame
        n={meta.n}
        title={meta.title}
        description={description}
        formRef={formRef}
        canContinue={false}
        hideContinue
        skipPrimary
        onContinue={() => undefined}
        onBack={onBack}
        onSkip={onSkip}
        skipping={skipping}
        formError={skipError}
      >
        <div className="setup-section" data-testid="step10-suggestion">
          <div className="setup-section__title">
            <Icon name="ph ph-users-three" size={15} /> Gợi ý: mời đội ngũ cùng dùng
          </div>
          <p className="muted-note">
            Sếp một mình dùng được ngay. Khi có quản lý, vận hành hay nhân viên cùng làm, Sếp mời họ — mỗi người chỉ thấy phần việc được giao. Không bắt buộc:
            mời lúc nào cũng được ở Đội ngũ › Người dùng.
          </p>
          <Button variant="secondary" icon="ph ph-user-plus" className="btn-27" onClick={() => setFormOpen(true)}>
            Mời ngay
          </Button>
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
      canContinue={canContinue}
      blockedHint={!hasPartial && !canContinue ? `Thêm ít nhất một người để mời — chưa muốn mời thì bấm "${exitLabel}".` : null}
      busy={busy}
      onContinue={() => void save()}
      onBack={onBack}
      onSkip={onSkip}
      skipping={skipping}
      formError={formError ?? skipError}
    >
      <div className="setup-section">
        <div className="setup-section__title">Thành viên được mời</div>
        {finished ? <PinHint text={PIN_TEXT} title={PIN_TEXT} /> : null}
        {rows.length === 0 ? (
          <EmptyState icon="ph ph-users-three" title="Chưa mời ai" description={`Bấm Thêm người để mời. Chưa muốn mời ai thì bấm ${exitLabel} — việc này chỉ tính xong khi có người thật.`} />
        ) : (
          <div className="invite-rows">
            {rows.map((r) => (
              <div className="invite-row" key={r.id}>
                <TextField label="Tên hiển thị" value={r.display_name} onChange={(e) => updateRow(r.id, { display_name: e.target.value })} maxLength={120} />
                <TextField label="Email" type="email" value={r.email} onChange={(e) => updateRow(r.id, { email: e.target.value })} maxLength={320} />
                <SelectField label="Vai trò" value={r.role} onChange={(e) => updateRow(r.id, { role: e.target.value as InviteRole })} options={ROLE_OPTIONS} />
                <Button variant="ghost" className="btn-27 invite-row__remove" icon="ph ph-trash" aria-label="Bỏ dòng này" onClick={() => removeRow(r.id)} />
              </div>
            ))}
          </div>
        )}
        {hasPartial ? <p className="inline-error">Điền đủ tên và email cho từng dòng, hoặc bấm bỏ dòng.</p> : null}
        <Button variant="secondary" icon="ph ph-plus" onClick={addRow} className="btn-27">
          Thêm người
        </Button>
      </div>
    </StepFrame>
  );
}
