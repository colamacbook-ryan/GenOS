import type { FormEvent, ReactNode, RefObject } from 'react';
import { Button } from '@gen-harness/ui';

export interface StepFrameProps {
  n: number;
  title: string;
  description: string;
  children: ReactNode;
  canContinue: boolean;
  busy?: boolean;
  onContinue: () => void;
  onBack?: () => void;
  /** Present only for optional steps. */
  onSkip?: () => void;
  skipping?: boolean;
  /** Form-level error (network, conflict…). */
  formError?: string | null;
  formRef?: RefObject<HTMLFormElement>;
  continueLabel?: string;
  continueIcon?: string;
  /** Render children without the surface card (steps made of their own cards). */
  bare?: boolean;
  /** v0.1.28 (UX V1): why "Tiếp tục" is disabled — shown beside the buttons while `canContinue` is false. */
  blockedHint?: string | null;
  /** v0.1.55: thẻ gợi ý (bước 10) — không có nút "Tiếp tục", chỉ "Để sau" (nút chính) / hành động trong thẻ. */
  hideContinue?: boolean;
  /** v0.1.55: "Để sau" là nút chính (thẻ gợi ý) thay vì nút phụ. */
  skipPrimary?: boolean;
}

/** Right pane of the wizard: title, description, surface card, action bar. */
export function StepFrame({
  n,
  title,
  description,
  children,
  canContinue,
  busy,
  onContinue,
  onBack,
  onSkip,
  skipping,
  formError,
  formRef,
  continueLabel = 'Tiếp tục',
  continueIcon = 'ph ph-arrow-right',
  bare,
  blockedHint,
  hideContinue,
  skipPrimary,
}: StepFrameProps) {
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (canContinue && !busy) onContinue();
  };
  return (
    <form ref={formRef} className="setup-step" onSubmit={submit} noValidate aria-labelledby={`setup-step-${n}-title`}>
      <div className="setup-step__head">
        <h1 className="screen-title" id={`setup-step-${n}-title`}>
          {title}
        </h1>
        <p className="screen-desc">{description}</p>
      </div>
      {bare ? <div className="setup-bare">{children}</div> : <div className="gh-card setup-card">{children}</div>}
      <div className="setup-error" role="alert" aria-live="assertive">
        {formError}
      </div>
      {!canContinue && blockedHint ? (
        <p className="setup-blocked" role="status">
          {blockedHint}
        </p>
      ) : null}
      <div className="setup-actions">
        <Button variant="ghost" icon="ph ph-arrow-left" onClick={onBack} disabled={!onBack}>
          Quay lại
        </Button>
        <span className="setup-actions__spacer" />
        {onSkip ? (
          <Button variant={skipPrimary ? 'primary' : 'secondary'} onClick={onSkip} loading={skipping}>
            Để sau
          </Button>
        ) : null}
        {hideContinue ? null : (
          <Button variant="primary" type="submit" disabled={!canContinue} loading={busy} iconRight={continueIcon}>
            {continueLabel}
          </Button>
        )}
      </div>
    </form>
  );
}

