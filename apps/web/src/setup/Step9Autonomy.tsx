import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { AUTONOMY_CHOICES, PinCancelledError, autonomyChoice } from '@gen-harness/contracts';
import { Icon, Segmented } from '@gen-harness/ui';
import { api } from '../lib/api';
import { errorText } from '../lib/errorText';
import { qk } from '../lib/queries';
import { CardError, PinHint, SkeletonLines } from '../screens/common';
import { StepFrame } from './StepFrame';
import { describeError, type StepProps } from './types';

// F-30: nhãn 3 mức (không tiền tố số); giá trị gửi vẫn là 3/4 trên thang 0–6.
const LEVEL_OPTIONS = AUTONOMY_CHOICES.filter((c) => c.level === 3 || c.level === 4).map((c) => ({ value: String(c.level), label: c.label }));

const PIN_TEXT = 'Sau Hoàn tất, đổi mức tự trị cần mã PIN';
/** Ngưỡng tiền mặc định phải duyệt (chữ hiển thị; chỉ nêu khi đang thiết lập lần đầu — sau Hoàn tất Owner có thể đã đổi ngưỡng). */
const AMOUNT_APPROVAL_VND = '50.000.000 ₫';
const levelLabel = (n: number) => autonomyChoice(n)?.label ?? `mức ${n}`;

/** Bước 9 — Tự trị & ranh giới: mức 3 hoặc 4 cho agent tạo ở bước 8 (mặc định 4), kèm danh sách ranh giới khoá cứng (không tắt
 *  được). v0.1.55: không còn ô tích "Tôi đã đọc…" — thay bằng MỘT dòng ghi chú (bấm Tiếp tục = đã đọc; web gửi
 *  `ack_boundaries: true`). Mở lại sau Hoàn tất: điền sẵn mức hiện tại của agent; bấm Tiếp tục mà không đổi mức thì giữ nguyên
 *  (không hỏi PIN). */
export function Step9Autonomy({ meta, description, onBack, onSaved, formRef, onSkip, skipping, skipError }: StepProps) {
  const boundaries = useQuery({ queryKey: ['setup', 'hard-boundaries'], queryFn: ({ signal }) => api.setup.hardBoundaries(signal) });
  const qc = useQueryClient();
  // refetchOnMount 'always': mở lại bước 9 luôn đọc mức hiện tại (không tin bộ nhớ đệm 30 s — có thể vừa đổi ở nơi khác).
  const target = useQuery({ queryKey: qk.setupStep9, queryFn: ({ signal }) => api.setup.step9State(signal), refetchOnMount: 'always' });
  const agent = target.data?.agent ?? null;
  const [level, setLevel] = useState('4');
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (agent && !touched) setLevel(String(agent.autonomy_level));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- chỉ điền sẵn khi dữ liệu agent về, không đè lựa chọn của Sếp.
  }, [agent?.id, agent?.autonomy_level]);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  // v0.1.45 (F-20): mở lại từ trang Hướng dẫn SAU Hoàn tất → máy chủ đòi phiên PIN (hộp PIN tự mở khi gặp 423).
  const finished = useQuery({ queryKey: qk.setupState, queryFn: ({ signal }) => api.setup.state(signal) }).data?.finished === true;
  // Mức sẽ áp khi bấm Tiếp tục: mức Sếp đang chọn (3/4), hoặc mức hiện tại của agent khi không chọn lại (giữ nguyên).
  const shownLevel = level === '3' || level === '4' ? Number(level) : (agent?.autonomy_level ?? 4);

  const save = async () => {
    setBusy(true);
    setFormError(null);
    try {
      // Mức hiện tại ngoài 3/4 (đã đổi ở màn Danh tính Agent) và Sếp không chọn lại → null = giữ nguyên.
      const chosen = level === '3' ? 3 : level === '4' ? 4 : null;
      const state = await api.setup.step9({ autonomy_level: chosen, ack_boundaries: true });
      // Ghi mức vừa lưu vào bộ nhớ đệm — Quay lại / mở lại bước 9 không điền mức cũ rồi gửi ngược về.
      if (state.agent) qc.setQueryData(qk.setupStep9, { agent: state.agent });
      else void qc.invalidateQueries({ queryKey: qk.setupStep9 });
      onSaved(state);
    } catch (e) {
      // v0.1.45: huỷ hộp PIN → câu chung 'Đã huỷ — thao tác cần mã PIN.' (errorText), lỗi khác như cũ.
      setFormError(e instanceof PinCancelledError ? errorText(e) : describeError(e));
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
      canContinue={target.isSuccess}
      busy={busy}
      onContinue={() => void save()}
      onBack={onBack}
      onSkip={onSkip}
      skipping={skipping}
      formError={formError ?? skipError}
    >
      <div className="setup-section">
        <div className="setup-section__title">Mức tự trị của agent</div>
        {target.isPending ? (
          <SkeletonLines rows={1} padding="0" />
        ) : target.isError ? (
          <CardError error={target.error} onRetry={() => void target.refetch()} retrying={target.isFetching} />
        ) : (
          <>
            {agent ? (
              <p className="muted-note" data-testid="step9-agent">
                Agent: <strong>{agent.name}</strong> · mức hiện tại: {levelLabel(agent.autonomy_level)}
              </p>
            ) : null}
            <Segmented
              label="Mức tự trị"
              value={level}
              onChange={(v) => {
                setTouched(true);
                setLevel(v);
              }}
              options={LEVEL_OPTIONS}
            />
            {agent && !touched && agent.autonomy_level !== 3 && agent.autonomy_level !== 4 ? (
              <p className="muted-note" data-testid="step9-keep">
                Không chọn = giữ nguyên mức hiện tại.
              </p>
            ) : null}
          </>
        )}
        {finished && agent && level !== String(agent.autonomy_level) ? <PinHint text={PIN_TEXT} title={PIN_TEXT} /> : null}
        <p className="muted-note">Soạn sẵn chờ duyệt: agent soạn sẵn, Sếp duyệt rồi mới gửi. Gợi ý: agent chỉ gợi ý việc nên làm. Đổi lại bất cứ lúc nào ở màn Danh tính Agent.</p>
      </div>
      <div className="setup-section">
        <div className="setup-section__title">Ranh giới khoá cứng — không tắt được</div>
        <ul className="setup-boundaries">
          {(boundaries.data ?? []).map((b) => (
            <li key={b}>
              <Icon name="ph ph-lock-simple" size={13} /> {b}
            </li>
          ))}
        </ul>
        <p className="muted-note" data-testid="step9-ack-note">
          Bấm Tiếp tục nghĩa là Sếp đã đọc các ranh giới trên. Agent sẽ ở mức {levelLabel(shownLevel)}
          {finished ? '.' : `; chi vượt ${AMOUNT_APPROVAL_VND} luôn chờ Sếp duyệt.`}
        </p>
      </div>
    </StepFrame>
  );
}
