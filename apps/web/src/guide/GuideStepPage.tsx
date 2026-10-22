import { useEffect, useRef } from 'react';
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { EmptyState, Icon } from '@gen-harness/ui';
import { api } from '../lib/api';
import { qk, useMe } from '../lib/queries';
import { queryClient } from '../lib/queryClient';
import { toast } from '../lib/toast';
import { OWNER_HOME } from '../owner/ownerModel';
import { CardError, SkeletonLines } from '../screens/common';
import { Step4Brain } from '../setup/Step4Brain';
import { Step5Channels } from '../setup/Step5Channels';
import { Step6Groups } from '../setup/Step6Groups';
import { Step7Refinery } from '../setup/Step7Refinery';
import { Step8Agent } from '../setup/Step8Agent';
import { Step9Autonomy } from '../setup/Step9Autonomy';
import { Step10Team } from '../setup/Step10Team';
import { Step11Backup } from '../setup/Step11Backup';
import { AI_SOURCE_PATH, STEP_DESCRIPTIONS } from '../setup/steps';
import { mergeSteps } from '../setup/stepState';
import type { StepProps } from '../setup/types';
import { FOLLOW_UP_KEY, GUIDE_BY_N } from './guideContent';

const STEPS: Record<number, (p: StepProps) => JSX.Element> = {
  4: Step4Brain,
  5: Step5Channels,
  6: Step6Groups,
  7: Step7Refinery,
  8: Step8Agent,
  9: Step9Autonomy,
  10: Step10Team,
  11: Step11Backup,
};

/**
 * `/guide/:n` — mở đúng form của trình thiết lập cho việc n (5–11) ngay trong Console, kể cả sau Hoàn tất (API cho
 * phép lưu lại các bước tuỳ chọn). Lưu xong quay về trang Hướng dẫn thiết lập, việc đó tự hiện "Đã xong".
 */
export function GuideStepPage() {
  const n = Number(useParams().n);
  const navigate = useNavigate();
  const formRef = useRef<HTMLFormElement>(null);
  // v0.1.68 (F145): việc thiết lập chỉ Owner làm — vai khác thấy khung "Việc này do Owner làm" (như trang /guide), không phải
  // "Không tải được dữ liệu — Vai trò của bạn không có quyền" + Thử lại, và không gọi các API chỉ Owner dùng (403 ở console).
  // Review v0.1.68 (P3): vai lấy từ truy vấn /auth/me THẬT (dùng chung khoá `qk.me` với khung ứng dụng nên thường đã có sẵn trong
  // bộ nhớ đệm, không gọi thêm). Trước đây `enabled: false` chỉ đọc đệm — tải thẳng /guide/n khi đệm trống thì vai coi như
  // Owner và gọi API chỉ Owner dùng (403). Chưa biết vai thì CHỜ, chưa tải form.
  const me = useMe();
  const role = me.data?.role?.code;
  const nonOwner = !!role && role !== 'owner';
  const state = useQuery({ queryKey: qk.setupState, queryFn: ({ signal }) => api.setup.state(signal), enabled: !!role && !nonOwner });
  const guide = GUIDE_BY_N[n];

  useEffect(() => {
    if (guide) document.title = `${guide.title} · Hướng dẫn thiết lập · Gen-Harness`;
  }, [guide]);

  const Step = STEPS[n];
  // v0.1.39: việc không có form trình thiết lập (13 Facebook, 14 Gen-hub) → mở thẳng màn làm việc đó.
  if (guide?.doTo) return <Navigate to={guide.doTo} replace />;
  if (!Step || !guide) return <Navigate to="/guide" replace />;
  if (nonOwner) {
    return (
      <div className="screen guide-step">
        <Link to="/guide" className="guide-back">
          <Icon name="ph ph-arrow-left" size={13} /> Hướng dẫn thiết lập
        </Link>
        <div className="gh-card">
          <EmptyState
            icon="ph ph-lock-simple"
            title="Việc này do Owner làm"
            description="Kết nối kênh, chọn nhóm, sao lưu… chỉ Owner cài đặt được. Cần thêm gì, hãy nhắn Owner."
          />
        </div>
      </div>
    );
  }

  if (!role) {
    return (
      <div className="screen guide-step">
        <div className="gh-card">
          {me.isError ? <CardError error={me.error} onRetry={() => void me.refetch()} retrying={me.isFetching} /> : <SkeletonLines rows={6} />}
        </div>
      </div>
    );
  }

  // Bước 4 (chọn model): v0.1.59 — nút "Chọn model" mới đi thẳng /setup?buoc=nguon-ai; trang này (form đầy đủ, cho người muốn
  // chỉnh kỹ) quay về Mặt tiền, không còn về /overview.
  const home = n === 4 ? { to: OWNER_HOME, label: 'Mặt tiền' } : { to: '/guide', label: 'Hướng dẫn thiết lập' };
  const back = () => navigate(home.to);
  const meta = mergeSteps(state.data).find((s) => s.n === n)!;

  return (
    <div className="screen guide-step">
      <Link to={home.to} className="guide-back">
        <Icon name="ph ph-arrow-left" size={13} /> {home.label}
      </Link>
      {n === 4 ? (
        <p className="muted-note" data-testid="guide-quick-ai">
          <Link to={AI_SOURCE_PATH}>Cách nhanh: Thiết lập nguồn AI 2 phút</Link>
        </p>
      ) : null}
      {state.isPending ? (
        <div className="gh-card">
          <SkeletonLines rows={6} />
        </div>
      ) : state.isError ? (
        <div className="gh-card">
          <CardError error={state.error} onRetry={() => void state.refetch()} retrying={state.isFetching} />
        </div>
      ) : (
        <div className="setup-content">
          <Step
            meta={meta}
            description={STEP_DESCRIPTIONS[n] ?? guide.why}
            status={meta.status}
            token=""
            setToken={() => undefined}
            onBack={back}
            onNext={back}
            onSaved={(s) => {
              queryClient.setQueryData(qk.setupState, s);
              void queryClient.invalidateQueries({ queryKey: FOLLOW_UP_KEY });
              toast(`Đã xong: ${guide.title}`, 'ok');
              back();
            }}
            formRef={formRef}
          />
        </div>
      )}
    </div>
  );
}
