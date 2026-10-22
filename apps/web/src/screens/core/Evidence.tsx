import { useState, type ReactNode } from 'react';
import { confidenceBand, eventTypeLabel, type ExplainQuote, type ExplainUnit } from '@gen-harness/contracts';
import { Button, Dialog, EmptyState, Icon } from '@gen-harness/ui';
import { fmtDMClock, fmtDec } from '../../lib/format';
import { CardError, SkeletonLines, StateChip } from '../common';
import { useExplain, useRawQuote } from './queries';

const BAND = {
  high: { label: 'cao', color: 'var(--color-ok)' },
  medium: { label: 'trung bình', color: 'var(--color-warn)' },
  low: { label: 'thấp', color: 'var(--color-bad)' },
} as const;

/** Chip tin cậy cao / trung bình / thấp (docs/api/phase-3.md). */
export function ConfidenceChip({ value }: { value: number }) {
  const b = BAND[confidenceBand(value)];
  return <StateChip color={b.color}>tin cậy {b.label}</StateChip>;
}

const METHOD: Record<string, string> = {
  'rules+model': 'quy tắc + model',
  manual: 'sửa tay',
  rule: 'quy tắc',
  model: 'model',
  agent: 'agent',
};

/**
 * Nút "Vì sao hệ thống nghĩ vậy" / "Xem chứng cứ gốc" / "Vì sao →": mở hộp chứng cứ của một đối tượng
 * (`GET /explain/{kind}/{id}`). Chữ và kiểu nút lấy theo chỗ đặt trong thiết kế.
 */
export function WhyButton({
  kind,
  id,
  children = 'Vì sao hệ thống nghĩ vậy',
  icon = 'ph ph-question',
  iconRight,
  size = 'sm',
  className,
}: {
  kind: string;
  id: string;
  children?: ReactNode;
  icon?: string | null;
  iconRight?: string;
  size?: 'sm' | 'md';
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        variant="ghost"
        size={size}
        icon={icon ?? undefined}
        iconRight={iconRight}
        className={className}
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
      >
        {children}
      </Button>
      {open ? <EvidenceDialog kind={kind} id={id} onClose={() => setOpen(false)} /> : null}
    </>
  );
}

/** Chuỗi chứng cứ: điểm → ý chính → trích dẫn → bản ghi thô. */
export function EvidenceDialog({ kind, id, onClose }: { kind: string; id: string; onClose: () => void }) {
  const q = useExplain(kind, id);
  const d = q.data;
  return (
    <Dialog
      open
      onClose={onClose}
      width={640}
      title={d?.title ?? 'Vì sao hệ thống nghĩ vậy'}
      kicker={d?.statement ?? 'Đang tải chứng cứ'}
      aside={
        d?.method ? (
          <StateChip color="var(--color-neutral-400)" border="var(--color-neutral-800)">
            {METHOD[d.method] ?? d.method}
          </StateChip>
        ) : null
      }
      actions={
        <Button variant="secondary" onClick={onClose}>
          Đóng
        </Button>
      }
    >
      {q.isPending ? (
        <SkeletonLines rows={4} padding="0" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : d ? (
        <div className="ev-body">
          {d.factors.length > 0 ? (
            <section className="ev-section" aria-label="Yếu tố">
              <div className="dlg-section-title">Yếu tố</div>
              <div className="ev-factors">
                {d.factors.map((f, i) => (
                  <div key={i} className="ev-factor">
                    <span className="ev-factor__label">{f.label}</span>
                    {f.value != null ? <span className="ev-factor__value">{fmtDec(f.value, 1)}</span> : null}
                  </div>
                ))}
              </div>
            </section>
          ) : null}
          <section className="ev-section" aria-label="Chứng cứ">
            <div className="dlg-section-title">Chứng cứ · {d.units.length} ý chính</div>
            {d.units.length === 0 ? (
              <EmptyState
                icon="ph ph-quotes"
                title="Chưa có chứng cứ"
                description="Hệ thống chưa có tin nào làm căn cứ cho kết luận này."
              />
            ) : (
              <div className="dlg-list">
                {d.units.map((u) => (
                  <UnitBlock key={u.id} u={u} />
                ))}
              </div>
            )}
          </section>
          {d.history.length > 1 ? (
            <section className="ev-section" aria-label="Lịch sử điểm">
              <div className="dlg-section-title">Lịch sử điểm</div>
              <div className="ev-history">
                {d.history.map((h, i) => (
                  <div key={i} className="ev-history__row">
                    <span className="ev-history__value">{fmtDec(h.value, 0)}</span>
                    <span>{fmtDMClock(h.computed_at)}</span>
                    <span>{h.by ? `sửa tay · ${h.by.name}` : (METHOD[h.method ?? ''] ?? h.method ?? '')}</span>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
        </div>
      ) : null}
    </Dialog>
  );
}

function UnitBlock({ u }: { u: ExplainUnit }) {
  return (
    <div className="dlg-item">
      <div className="dlg-item__meta">
        <span>{eventTypeLabel(u.event_type)}</span>
        <span>·</span>
        <span>{fmtDMClock(u.observed_at)}</span>
        {u.group ? (
          <>
            <span>·</span>
            <span>{u.group.name}</span>
          </>
        ) : null}
        {u.person ? (
          <>
            <span>·</span>
            <span>{u.person.name}</span>
          </>
        ) : null}
        <span style={{ flex: 1 }} />
        <ConfidenceChip value={u.confidence} />
      </div>
      <div className="dlg-item__text">{u.conclusion}</div>
      {u.quotes.map((q) => (
        <QuoteRow key={q.raw_id} q={q} />
      ))}
    </div>
  );
}

function QuoteRow({ q }: { q: ExplainQuote }) {
  const [open, setOpen] = useState(false);
  const raw = useRawQuote(open ? q.raw_id : null);
  return (
    <div className="ev-quote">
      <div className="ev-quote__row">
        <Icon name="ph ph-quotes" size={14} className="ev-quote__icon" />
        <span className="ev-quote__text">{q.quote}</span>
        <Button
          variant="ghost"
          size="sm"
          iconRight={open ? 'ph ph-caret-up' : 'ph ph-arrow-square-out'}
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
        >
          {open ? 'Thu gọn' : 'Xem nguyên văn'}
        </Button>
      </div>
      <div className="ev-quote__meta">
        {q.raw_code ? <span>{q.raw_code}</span> : null}
        <span>{q.channel}</span>
        <span>{fmtDMClock(q.occurred_at)}</span>
        {q.sender ? <span>{q.sender.name}</span> : null}
      </div>
      {open ? (
        <div className="ev-raw">
          {raw.isPending ? (
            <SkeletonLines rows={1} padding="0" />
          ) : raw.isError ? (
            <CardError error={raw.error} onRetry={() => void raw.refetch()} retrying={raw.isFetching} />
          ) : (
            <pre className="ev-raw__text">{raw.data?.text ?? ''}</pre>
          )}
        </div>
      ) : null}
    </div>
  );
}
