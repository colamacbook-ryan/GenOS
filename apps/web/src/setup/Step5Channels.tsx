import { useState } from 'react';
import { EmptyState } from '@gen-harness/ui';
import { api } from '../lib/api';
import { useChannels } from '../lib/dataQueries';
import { CardError, SkeletonLines } from '../screens/common';
import { ChannelCard } from '../screens/system/ChannelCard';
import { StepFrame } from './StepFrame';
import { describeError, type StepProps } from './types';

/** Bước 5 — same channel cards as Điều khiển hệ thống, QR at 240px, risk warning first. */
export function Step5Channels({ meta, description, onBack, onSaved, formRef, onSkip, skipping, skipError }: StepProps) {
  const channels = useChannels();
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const active = channels.data?.filter((c) => c.state === 'active').length ?? 0;

  const save = async () => {
    setBusy(true);
    setFormError(null);
    try {
      onSaved(await api.setup.step5());
    } catch (e) {
      setFormError(describeError(e));
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
      canContinue={active > 0}
      busy={busy}
      onContinue={() => void save()}
      onBack={onBack}
      onSkip={onSkip}
      skipping={skipping}
      formError={formError ?? skipError}
      bare
    >
      {channels.isPending ? (
        <div className="gh-card">
          <SkeletonLines rows={4} />
        </div>
      ) : channels.isError ? (
        <div className="gh-card">
          <CardError error={channels.error} onRetry={() => void channels.refetch()} retrying={channels.isFetching} />
        </div>
      ) : channels.data.length === 0 ? (
        <div className="gh-card">
          <EmptyState icon="ph ph-plugs" title="Chưa có kênh nào" description="Bản đang chạy chưa có kênh nhắn tin nào để kết nối." />
        </div>
      ) : (
        channels.data.map((c) => <ChannelCard key={c.type} channel={c} canManage large />)
      )}
    </StepFrame>
  );
}
