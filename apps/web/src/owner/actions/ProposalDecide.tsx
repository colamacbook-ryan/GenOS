/**
 * v0.1.59 (G1) — `<ProposalDecide proposal onDone/>`: Xác nhận / Huỷ MỘT đề xuất của Gen ngay trên Mặt tiền.
 *
 * Gen chỉ ĐỀ XUẤT; chỉ khi Sếp bấm Xác nhận máy chủ mới làm (qua endpoint sẵn có, Action Log ghi Sếp là người bấm). Đề
 * xuất cần mã PIN (kho_*, social_*) thì API trả 423 và client tự hỏi PIN — ở đây KHÔNG dựng hộp PIN. Đề xuất sống 24
 * giờ ở Redis: 404/410 ⇒ "Đề xuất đã hết hạn — hỏi Gen lại" (kèm "Chi tiết kỹ thuật"), và danh sách tự tải lại để thẻ biến mất.
 */
import { useState } from 'react';
import type { OwnerProposal } from '@gen-harness/contracts';
import { Button } from '@gen-harness/ui';
import { errorDetail, errorText } from '../../lib/errorText';
import { InlineError } from '../../screens/common';
import { asText } from '../ownerModel';
import { PROPOSAL_EXPIRED_TEXT, expiresText, isProposalExpired, useProposalDecision } from './useOwnerActions';
import './actions.css';

export type ProposalOutcome = 'confirmed' | 'cancelled' | 'expired';

export function ProposalDecide({ proposal, onDone }: { proposal: OwnerProposal; onDone?: (outcome: ProposalOutcome) => void }) {
  const { confirm, cancel } = useProposalDecision();
  const [gone, setGone] = useState(false);
  const busy = confirm.isPending || cancel.isPending;
  const failed = confirm.error ?? cancel.error;
  const expired = gone || isProposalExpired(failed);
  const expiry = expiresText(proposal.expires_at);

  const run = (kind: 'confirm' | 'cancel') => {
    const m = kind === 'confirm' ? confirm : cancel;
    m.mutate(proposal.id, {
      onSuccess: () => onDone?.(kind === 'confirm' ? 'confirmed' : 'cancelled'),
      onError: (e) => {
        if (isProposalExpired(e)) {
          setGone(true);
          onDone?.('expired');
        }
      },
    });
  };

  return (
    <section className="owner-act" data-testid="owner-proposal-decide" aria-label="Đề xuất của Gen">
      <header className="owner-act__head">
        <h3 className="owner-act__title">{asText(proposal.type_label) || 'Gen đề xuất'}</h3>
        <p className="owner-act__meta">
          Gen đề xuất{expiry ? <> · <span className="owner-act__expire">{expiry}</span></> : null}
        </p>
      </header>
      <p className="owner-act__text" data-testid="owner-proposal-summary">
        {asText(proposal.summary)}
      </p>
      {failed ? (
        <div className="owner-act__error" data-testid="owner-action-error">
          <InlineError detail={errorDetail(failed)}>{expired ? PROPOSAL_EXPIRED_TEXT : errorText(failed)}</InlineError>
        </div>
      ) : null}
      {expired ? null : (
        <>
          <div className="owner-act__buttons">
            <Button
              variant="primary"
              icon="ph ph-check"
              loading={confirm.isPending}
              disabled={busy}
              data-testid="owner-proposal-confirm"
              onClick={() => run('confirm')}
            >
              Xác nhận
            </Button>
            <Button icon="ph ph-x" loading={cancel.isPending} disabled={busy} data-testid="owner-proposal-cancel" onClick={() => run('cancel')}>
              Huỷ
            </Button>
          </div>
          {proposal.needs_pin ? <p className="owner-act__pin">Cần mã PIN của Sếp — hộp nhập mã PIN sẽ tự hiện khi Sếp bấm Xác nhận.</p> : null}
        </>
      )}
    </section>
  );
}
