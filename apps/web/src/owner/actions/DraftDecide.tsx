/**
 * v0.1.59 (G1) — `<DraftDecide draftId onDone/>`: duyệt MỘT bản nháp ngay trên Mặt tiền (Tình báo, Thông tin, Việc).
 *
 * Tải `GET /drafts/{id}`, hiện toàn văn + ngữ cảnh + lý do giữ lại, ba nút: **Duyệt và gửi** (chính) · **Sửa rồi gửi** ·
 * **Từ chối** (lý do tuỳ chọn) qua `api.drafts.approve|editSend|reject`. Mã PIN do client tự hỏi khi API trả 423 — ở đây
 * KHÔNG dựng hộp PIN. Xong thì tải lại `['owner']`, `['drafts']`, `['inbox']`… (useOwnerActions) rồi gọi `onDone`.
 * Lỗi = câu thân thiện + "Chi tiết kỹ thuật"; mọi chuỗi từ API đi qua `asText()` (không bao giờ render object).
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { DraftDetail } from '@gen-harness/contracts';
import { Button, Skeleton } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { errorDetail, errorText } from '../../lib/errorText';
import { CardError, InlineError } from '../../screens/common';
import { qk3 } from '../../screens/core/queries';
import { asText } from '../ownerModel';
import { draftTargetText, useDraftDecision } from './useOwnerActions';
import './actions.css';

export function DraftDecide({ draftId, onDone }: { draftId: string; onDone?: (draft: DraftDetail) => void }) {
  const q = useQuery({
    queryKey: qk3.draft(draftId),
    queryFn: ({ signal }) => api.drafts.get(draftId, signal),
  });
  if (q.isPending) {
    return (
      <div className="owner-act-skel" aria-busy="true" aria-label="Đang tải bản nháp" data-testid="owner-draft-loading">
        <Skeleton width="60%" height={18} />
        <Skeleton width="100%" height={72} />
        <Skeleton width="100%" height={44} />
      </div>
    );
  }
  if (q.isError) return <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />;
  return <DraftBody key={q.data.id} d={q.data} onDone={onDone} />;
}

function DraftBody({ d, onDone }: { d: DraftDetail; onDone?: (draft: DraftDetail) => void }) {
  const { approve, editSend, reject } = useDraftDecision(d.id);
  const [mode, setMode] = useState<'view' | 'edit' | 'reject'>('view');
  const [text, setText] = useState(asText(d.text));
  const [reason, setReason] = useState('');
  const pending = d.status === 'pending';
  const busy = approve.isPending || editSend.isPending || reject.isPending;
  const failed = approve.error ?? editSend.error ?? reject.error;
  const finish = (r: DraftDetail) => onDone?.(r);

  const target = draftTargetText(d);
  const meta = [asText(d.code), asText(d.kind_label), target ? `Gửi cho ${target}` : ''].filter(Boolean).join(' · ');
  const hold = asText(d.hold_reason);
  const ctx = (Array.isArray(d.context) ? d.context : []).filter((c) => asText(c?.key) && asText(c?.value));

  return (
    <section className={pending ? 'owner-act' : 'owner-act owner-act--decided'} data-testid="owner-draft-decide" aria-label="Duyệt bản nháp">
      <header className="owner-act__head">
        <h3 className="owner-act__title">{asText(d.title) || 'Bản nháp chờ duyệt'}</h3>
        {meta ? <p className="owner-act__meta">{meta}</p> : null}
      </header>
      {hold ? (
        <p className="owner-act__hold" data-testid="owner-draft-hold">
          Giữ lại để Sếp duyệt: {hold}
        </p>
      ) : null}

      <div>
        <p className="owner-act__label">{mode === 'edit' ? 'Nội dung gửi (Sếp sửa được)' : 'Nội dung sẽ gửi'}</p>
        {mode === 'edit' ? (
          <textarea
            className="owner-act__input"
            data-testid="owner-draft-edit-text"
            aria-label="Nội dung gửi"
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={busy}
          />
        ) : (
          <pre className="owner-act__text" data-testid="owner-draft-text">
            {asText(d.text)}
          </pre>
        )}
      </div>

      {ctx.length > 0 ? (
        <div>
          <p className="owner-act__label">Ngữ cảnh</p>
          <ul className="owner-act__ctx" data-testid="owner-draft-context">
            {ctx.map((c, i) => (
              <li key={`${asText(c.key)}-${i}`}>
                <span className="owner-act__ctx-key">{asText(c.key)}</span>
                <span>{asText(c.value)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {mode === 'reject' ? (
        <div>
          <label className="owner-act__label" htmlFor={`reject-reason-${d.id}`}>
            Lý do từ chối (không bắt buộc)
          </label>
          <input
            id={`reject-reason-${d.id}`}
            className="owner-act__input"
            data-testid="owner-draft-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            disabled={busy}
            maxLength={500}
          />
        </div>
      ) : null}

      {failed ? (
        <div className="owner-act__error" data-testid="owner-action-error">
          <InlineError detail={errorDetail(failed)}>{errorText(failed)}</InlineError>
        </div>
      ) : null}

      {pending ? (
        <>
          <div className="owner-act__buttons">
            {mode === 'view' ? (
              <>
                <Button
                  variant="primary"
                  icon="ph ph-paper-plane-tilt"
                  loading={approve.isPending}
                  disabled={busy}
                  data-testid="owner-draft-approve"
                  onClick={() => approve.mutate({}, { onSuccess: finish })}
                >
                  Duyệt và gửi
                </Button>
                <Button icon="ph ph-pencil-simple" disabled={busy} data-testid="owner-draft-edit" onClick={() => setMode('edit')}>
                  Sửa rồi gửi
                </Button>
                <Button icon="ph ph-x" disabled={busy} data-testid="owner-draft-reject" onClick={() => setMode('reject')}>
                  Từ chối
                </Button>
              </>
            ) : null}
            {mode === 'edit' ? (
              <>
                <Button
                  variant="primary"
                  icon="ph ph-paper-plane-tilt"
                  loading={editSend.isPending}
                  disabled={busy || !text.trim()}
                  data-testid="owner-draft-edit-send"
                  onClick={() => editSend.mutate({ text }, { onSuccess: finish })}
                >
                  Gửi bản đã sửa
                </Button>
                <Button disabled={busy} onClick={() => setMode('view')}>
                  Quay lại
                </Button>
              </>
            ) : null}
            {mode === 'reject' ? (
              <>
                <Button
                  variant="primary"
                  loading={reject.isPending}
                  disabled={busy}
                  data-testid="owner-draft-reject-confirm"
                  onClick={() => reject.mutate(reason.trim() || null, { onSuccess: finish })}
                >
                  Xác nhận từ chối
                </Button>
                <Button disabled={busy} onClick={() => setMode('view')}>
                  Quay lại
                </Button>
              </>
            ) : null}
          </div>
          {d.flags?.writes_external ? (
            <p className="owner-act__pin">Gửi tin ra ngoài cần mã PIN của Sếp — hộp nhập mã PIN sẽ tự hiện khi Sếp bấm.</p>
          ) : null}
        </>
      ) : (
        <p className="owner-act__done" data-testid="owner-draft-decided">
          Bản nháp này đã được xử lý.
        </p>
      )}
    </section>
  );
}
