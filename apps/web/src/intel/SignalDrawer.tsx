import { Badge, Button } from '@gen-harness/ui';
import { errorDetail, errorText } from '../lib/errorText';
import { toast } from '../lib/toast';
import { CardError, InlineError, SkeletonLines } from '../screens/common';
import { reasonText } from './intelModel';
import {
  confidenceLabel,
  levelMeta,
  signalKindLabel,
  statusLabel,
  txt,
  when,
  type SignalDetail,
  type SignalTransition,
} from './intelHubModel';
import { useSignalDetail, useSignalTransition } from './intelHubQueries';
import { SignalVerdict } from './SignalVerdict';

const TRANSITIONS: Array<{ to: SignalTransition; label: string; onlyOpen?: boolean; done: string }> = [
  { to: 'acknowledged', label: 'Tôi đã thấy', onlyOpen: true, done: 'Đã ghi nhận' },
  { to: 'resolved', label: 'Đã xong', done: 'Đã đánh dấu xong' },
  { to: 'dismissed', label: 'Bỏ qua', done: 'Đã bỏ qua' },
];

/**
 * v0.1.79 (G1) — ngăn chi tiết một tín hiệu (400px ở máy tính, cả màn ở điện thoại): chuyện gì · vì sao · độ chắc · việc Sếp cần làm,
 * nút Đúng / Sai (3 lý do) / Không cần báo, chuyển trạng thái, bằng chứng (đã che). Dữ liệu từ `GET /signals/{id}`; tín hiệu riêng tư cần
 * PIN ⇒ hộp PIN chung tự mở (423). Không có chữ tin thô: chỉ `quote_display` máy chủ đã che.
 */
export function SignalDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useSignalDetail(id);
  return (
    <aside className="ih-drawer" aria-label="Chi tiết tín hiệu" data-gen-target="intel.signal.drawer" data-testid="intel-signal-drawer">
      <div className="ih-drawer__bar">
        <Button variant="ghost" size="sm" onClick={onClose} aria-label="Đóng chi tiết tín hiệu">
          ← Quay lại
        </Button>
      </div>
      {q.isPending ? (
        <SkeletonLines rows={6} padding="12px 16px" />
      ) : q.isError ? (
        <div className="ih-drawer__pad">
          <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        </div>
      ) : (
        <DrawerBody s={q.data} />
      )}
    </aside>
  );
}

function DrawerBody({ s }: { s: SignalDetail }) {
  const tr = useSignalTransition();
  const lv = levelMeta(s.level);
  const why = reasonText(Array.isArray(s.reasons) ? s.reasons.filter((r) => typeof r === 'string') : []);
  const conf = confidenceLabel(s.confidence_level);
  const active = s.status === 'open' || s.status === 'acknowledged';
  const evidence = Array.isArray(s.evidence) ? s.evidence : [];
  return (
    <div className="ih-drawer__pad">
      <p className="ih-drawer__kicker">
        <span aria-hidden>{lv.dot}</span> {lv.label} · {signalKindLabel(s.kind)}
        {txt(s.code) ? <span className="mono muted-note"> · {txt(s.code)}</span> : null}
      </p>
      <h2 className="ih-drawer__title">{txt(s.title)}</h2>
      <p className="muted-note">
        {statusLabel(s.status)}
        {when(s.created_at) ? ` · ${when(s.created_at)}` : ''}
      </p>
      {txt(s.summary) ? <p className="ih-drawer__text">{txt(s.summary)}</p> : null}
      {why.length ? <p className="ih-drawer__text"><strong>Vì sao:</strong> {why.join(' · ')}{conf ? ` (độ chắc: ${conf})` : ''}</p> : conf ? <p className="ih-drawer__text"><strong>Độ chắc:</strong> {conf}</p> : null}
      {txt(s.suggested_action) ? (
        <p className="ih-action" data-testid="intel-drawer-action">
          <strong>Việc Sếp cần làm:</strong> {txt(s.suggested_action)}
        </p>
      ) : null}

      <div className="ih-drawer__block" data-gen-target="intel.signal.verdict_block">
        <p className="ih-drawer__label">Gen báo vậy có đúng không?</p>
        <SignalVerdict id={s.id} title={txt(s.title)} verdict={s.feedback_verdict ?? null} />
      </div>

      {active ? (
        <div className="ih-drawer__block">
          <p className="ih-drawer__label">Xử lý</p>
          <div className="ih-drawer__row">
            {TRANSITIONS.filter((t) => !t.onlyOpen || s.status === 'open').map((t) => (
              <Button
                key={t.to}
                variant="secondary"
                size="sm"
                disabled={tr.isPending}
                onClick={() => tr.mutate({ id: s.id, to: t.to }, { onSuccess: () => toast(t.done) })}
              >
                {t.label}
              </Button>
            ))}
          </div>
          {tr.error ? <InlineError detail={errorDetail(tr.error)}>{errorText(tr.error)}</InlineError> : null}
        </div>
      ) : null}

      <div className="ih-drawer__block">
        <p className="ih-drawer__label">
          Bằng chứng <Badge kind="count" count={evidence.length} />
        </p>
        {evidence.length === 0 ? (
          <p className="muted-note">Tín hiệu này chưa có tin gốc kèm theo.</p>
        ) : (
          <ul className="ih-evidence">
            {evidence.map((e, i) => (
              <li key={txt(e?.id) || i}>
                <span className="muted-note">{when(e?.received_at) || '—'}</span>
                <span className="ih-evidence__quote">{txt(e?.quote_display) || 'Tin gốc đã được che'}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
