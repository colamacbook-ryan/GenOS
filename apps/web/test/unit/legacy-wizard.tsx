/**
 * v0.1.59 (g5-thiet-lap) — khung thử cho các bước 5–12 của máy chủ. `SetupPage` nay là Thiết lập MỘT màn 3 bước nên không còn
 * vẽ các bước 5–12; các form đó chỉ còn ở /guide/5…/guide/11 (GuideStepPage). Khung này đi dây giống `SetupPage`
 * 12 bước cũ (trạng thái máy chủ → bước hiện tại, "Để sau", lưu thì sang bước kế) để các test hành vi của từng bước vẫn chạy
 * nguyên — không còn là mã sản phẩm. Dùng: `<LegacyWizard />` trong `QueryClientProvider` + `MemoryRouter`.
 */
import { createRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { SetupState } from '@gen-harness/contracts';
import { api } from '../../src/lib/api';
import { qk } from '../../src/lib/queries';
import { queryClient } from '../../src/lib/queryClient';
import { Step10Team } from '../../src/setup/Step10Team';
import { Step11Backup } from '../../src/setup/Step11Backup';
import { Step4Brain } from '../../src/setup/Step4Brain';
import { Step5Channels } from '../../src/setup/Step5Channels';
import { Step6Groups } from '../../src/setup/Step6Groups';
import { Step7Refinery } from '../../src/setup/Step7Refinery';
import { Step8Agent } from '../../src/setup/Step8Agent';
import { Step9Autonomy } from '../../src/setup/Step9Autonomy';
import { STEP_DESCRIPTIONS } from '../../src/setup/steps';
import { mergeSteps } from '../../src/setup/stepState';
import { describeError, type StepProps } from '../../src/setup/types';

/** Bước 12 (Hoàn tất) không còn mã sản phẩm (v0.1.68, Nợ #36: Step12Finish đã gỡ — Thiết lập một màn có `StepDone`); chỉ giữ tiêu đề để các test đi tới bước cuối. */
function Step12Stub({ meta }: StepProps) {
  return <h1 className="screen-title">{meta.title}</h1>;
}

const BUILT: Record<number, (p: StepProps) => JSX.Element> = {
  4: Step4Brain,
  5: Step5Channels,
  6: Step6Groups,
  7: Step7Refinery,
  8: Step8Agent,
  9: Step9Autonomy,
  10: Step10Team,
  11: Step11Backup,
  12: Step12Stub,
};

const formRef = createRef<HTMLFormElement>();

export function LegacyWizard() {
  const [skipping, setSkipping] = useState(false);
  const [skipError, setSkipError] = useState<string | null>(null);
  const state = useQuery({ queryKey: qk.setupState, queryFn: ({ signal }) => api.setup.state(signal) });
  if (!state.data) return <div aria-busy="true">Đang tải…</div>;
  const data = state.data;
  const steps = mergeSteps(data);
  const view = data.current_step;
  const current = steps[view - 1];
  const onSaved = (s: SetupState) => {
    queryClient.setQueryData(qk.setupState, s);
    setSkipError(null);
  };
  const onSkip = async () => {
    setSkipping(true);
    setSkipError(null);
    try {
      onSaved(await api.setup.skip(view));
    } catch (e) {
      setSkipError(describeError(e));
    } finally {
      setSkipping(false);
    }
  };
  const Step = BUILT[view];
  if (!Step) return null;
  return (
    <div className="setup-content" key={view}>
      <Step
        meta={current}
        description={STEP_DESCRIPTIONS[view] ?? current.content}
        status={current.status}
        token=""
        setToken={() => undefined}
        onBack={undefined}
        onSaved={onSaved}
        onNext={() => undefined}
        formRef={formRef}
        onSkip={!current.required && current.status !== 'done' && current.status !== 'skipped' ? () => void onSkip() : undefined}
        skipping={skipping}
        skipError={skipError}
      />
    </div>
  );
}
