import { useState } from 'react';
import type { SetupState, Step8Agent as Step8Result } from '@gen-harness/contracts';
import { Icon, SelectField, TextField } from '@gen-harness/ui';
import { api } from '../lib/api';
import { reasonsOf } from '../lib/friendlyError';
import { FriendlyErrorText, ModelUnavailableNotice } from '../screens/common';
import { StepFrame } from './StepFrame';
import { describeError, type StepProps } from './types';

/**
 * Mẫu agent (docs/handoff/06 bước 8) — khoá mẫu PHẢI trùng `TEMPLATES` của máy chủ (agents_api: commercial, key_account, admin, cs,
 * recruiter, secretary); lệch khoá thì agent tạo ở bước 8 không sửa được (PATCH báo "Mẫu không tồn tại"). — điền sẵn tên/vai trò, Sếp sửa lại tuỳ ý. v0.1.55 (Thiết lập gọn): chọn mẫu là xong;
 * "Tạo trống" cũng có tên + vai trò mặc định (cùng chữ với `STEP8_DEFAULT_*` của api) nên luôn bấm Tiếp tục được.
 */
const TEMPLATES: Array<{ value: string; label: string; name: string; role: string }> = [
  { value: '', label: 'Tạo trống', name: 'Trợ lý', role: 'Theo dõi tin nhắn, báo việc quan trọng và soạn sẵn trả lời chờ Sếp duyệt.' },
  { value: 'commercial', label: 'Trợ lý thương mại', name: 'Trợ lý Kinh doanh', role: 'Theo dõi nhu cầu mua bán trong nhóm, báo cơ hội và soạn sẵn báo giá chờ duyệt.' },
  { value: 'key_account', label: 'Khách hàng lớn', name: 'Trợ lý Khách hàng lớn', role: 'Chăm sóc khách hàng quan trọng, nhắc lịch hẹn và theo dõi đơn đang mở.' },
  { value: 'admin', label: 'Admin hậu cần', name: 'Trợ lý Hậu cần', role: 'Theo dõi giao nhận, tồn kho và việc đến hạn của đội vận hành.' },
  { value: 'cs', label: 'CSKH', name: 'Trợ lý CSKH', role: 'Trả lời câu hỏi thường gặp, ghi nhận khiếu nại và chuyển việc khó cho người phụ trách.' },
  { value: 'recruiter', label: 'Tuyển dụng', name: 'Trợ lý Tuyển dụng', role: 'Ghi nhận ứng viên, lịch phỏng vấn và nhắc việc tuyển dụng.' },
  { value: 'secretary', label: 'Thư ký cá nhân', name: 'Thư ký', role: 'Tóm tắt tin nhắn quan trọng, nhắc lịch và soạn sẵn trả lời cho Sếp duyệt.' },
];

/** Mẫu chọn sẵn khi mở bước 8 (Trợ lý thương mại). */
const DEFAULT_TEMPLATE = 'commercial';

/**
 * v0.1.30: thử trò chuyện lỗi. `try_error` là chuỗi từ v0.1.30; máy chủ cũ trả đối tượng `{reasons}` — trước đây vẽ
 * thẳng làm React child → màn /guide/8 sập (React error #31). Không có model chạy được → trạng thái "Chọn model".
 */
function TryFailed({ agent }: { agent: Step8Result }) {
  const raw: unknown = agent.try_error;
  const legacyReasons = reasonsOf(raw);
  if (agent.try_error_code === 'MODEL_UNAVAILABLE' || legacyReasons) {
    return (
      <>
        <p className="muted-note">Agent đã lưu, nhưng chưa trò chuyện thử được.</p>
        <ModelUnavailableNotice reasons={agent.try_reasons?.length ? agent.try_reasons : legacyReasons} />
      </>
    );
  }
  return (
    <div className="muted-note">
      Agent đã lưu, nhưng chưa trò chuyện thử được — thử lại sau ở màn Danh tính Agent.
      <FriendlyErrorText raw={raw} fallback="bộ não AI chưa trả lời." />
    </div>
  );
}

/**
 * Bước 8 — Agent đầu tiên: chọn mẫu là xong (PUT /setup/steps/8). v0.1.55: bỏ tin thử trò chuyện (không gọi model, không tốn
 * lượt) — máy chủ chỉ trả lời thử khi payload có `try_message`. Gán kênh/nhóm làm sau ở màn Agent.
 */
export function Step8Agent({ meta, description, onBack, onSaved, formRef, onSkip, skipping, skipError }: StepProps) {
  const initial = TEMPLATES.find((t) => t.value === DEFAULT_TEMPLATE) ?? TEMPLATES[0];
  const [template, setTemplate] = useState(initial.value);
  const [name, setName] = useState(initial.name);
  const [role, setRole] = useState(initial.role);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [result, setResult] = useState<{ agent: Step8Result; state: SetupState } | null>(null);

  const pickTemplate = (v: string) => {
    setTemplate(v);
    const t = TEMPLATES.find((x) => x.value === v);
    if (t) {
      setName(t.name);
      setRole(t.role);
    }
  };
  const canContinue = name.trim() !== '' && role.trim() !== '';

  const save = async () => {
    setBusy(true);
    setFormError(null);
    try {
      const res = await api.setup.step8({ name: name.trim(), role_desc: role.trim(), template: template || null });
      setResult({ agent: res.agent, state: res });
    } catch (e) {
      setFormError(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  if (result) {
    const { agent, state } = result;
    return (
      <StepFrame n={meta.n} title={meta.title} description={description} canContinue onContinue={() => onSaved(state)} onBack={onBack} continueLabel="Tiếp tục">
        <div className="setup-section">
          <div className="setup-section__title">Đã tạo agent “{agent.name}”</div>
          {agent.try_reply ? (
            <div className="setup-try">
              <div className="setup-try__label">
                <Icon name="ph ph-chat-circle-text" size={14} /> {agent.name} trả lời thử
              </div>
              <p className="setup-try__reply">{agent.try_reply}</p>
            </div>
          ) : agent.try_error || agent.try_error_code || agent.try_reasons?.length ? (
            <TryFailed agent={agent} />
          ) : null}
          {typeof agent.listening_channels === 'number' ? (
            <p className="muted-note" data-testid="step8-listening">
              {agent.listening_channels > 0
                ? `Agent nghe sẵn ${agent.listening_channels} kênh đã nối — thu hẹp theo nhóm hoặc bỏ bớt ở màn Danh tính Agent.`
                : 'Chưa có kênh nào được nối nên agent chưa nghe kênh nào — nối Zalo/WhatsApp rồi gán kênh cho agent ở màn Danh tính Agent.'}
            </p>
          ) : (
            <p className="muted-note">Gán kênh và nhóm cho agent làm sau ở màn Danh tính Agent.</p>
          )}
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
      busy={busy}
      onContinue={() => void save()}
      onBack={onBack}
      onSkip={onSkip}
      skipping={skipping}
      formError={formError ?? skipError}
    >
      <div className="setup-section">
        <SelectField label="Mẫu" value={template} onChange={(e) => pickTemplate(e.target.value)} options={TEMPLATES.map((t) => ({ value: t.value, label: t.label }))} />
        <TextField label="Tên agent" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required />
        <TextField label="Vai trò — agent làm gì cho Sếp" value={role} onChange={(e) => setRole(e.target.value)} maxLength={500} required />
        <p className="muted-note" data-testid="step8-note">
          Chọn mẫu là xong — tên và vai trò điền sẵn theo mẫu, sửa lại ở Danh tính Agent bất cứ lúc nào.
        </p>
      </div>
    </StepFrame>
  );
}
