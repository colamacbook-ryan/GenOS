import { useState, type DragEvent } from 'react';
import type { Opportunity, OppStage } from '@gen-harness/contracts';
import { OPP_STAGES } from '@gen-harness/contracts';
import { Dialog, EmptyState, Icon } from '@gen-harness/ui';
import { CardError, InlineError, ScreenHead, SkeletonLines } from '../common';
import { DataEmptyState } from '../DataEmptyState';
import { errorText } from '../../lib/errorText';
import { fmtInt } from '../../lib/format';
import { CONFIDENCE_LABEL, STAGE_LABEL, STAGE_TONE, confidenceTone, fmtVnd, heatTone } from './marketModel';
import { useChangeStage, useOpportunities, useOpportunityPipeline } from './queries';

export function OpportunityScreen() {
  const list = useOpportunities();
  const pipeline = useOpportunityPipeline();
  const changeStage = useChangeStage();
  const [moveFor, setMoveFor] = useState<Opportunity | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [dragOverStage, setDragOverStage] = useState<OppStage | null>(null);

  const move = (id: string, toStage: OppStage) => {
    if (changeStage.isPending) return;
    changeStage.mutate({ id, toStage });
  };

  return (
    <div className="screen">
      <ScreenHead
        title="Bảng cơ hội"
        description="Pipeline sống từ hội thoại, không phải form nhập tay. Mỗi thẻ trả lời: ai cần gì, nóng đến đâu, tin được đến đâu, nên ghép với ai, và mất gì nếu không làm gì."
        maxWidth={700}
        actions={
          pipeline.data ? (
            <div className="opp-pipeline-total">
              <span className="opp-pipeline-total__label">Pipeline đang mở</span>
              <span className="opp-pipeline-total__value">{fmtVnd(pipeline.data.open_pipeline_value_vnd)}</span>
              <span className="opp-pipeline-total__count">{fmtInt(pipeline.data.open_pipeline_count)} cơ hội</span>
            </div>
          ) : null
        }
      />

      {list.isPending ? (
        <SkeletonLines rows={6} />
      ) : list.isError ? (
        <CardError error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />
      ) : list.data.items.length === 0 ? (
        <DataEmptyState
          fallback={<EmptyState icon="ph ph-kanban" title="Chưa có cơ hội nào" description="Cơ hội mở tự động từ tín hiệu cầu trong hội thoại, hoặc mở tay khi biết tin ngoài luồng chat." />}
        />
      ) : (
        <div className="opp-board" role="group" aria-label="Bảng cơ hội theo giai đoạn">
          {pipeline.isError ? <InlineError>{errorText(pipeline.error)}</InlineError> : null}
          {OPP_STAGES.map((stage) => {
            const cards = list.data.items.filter((o) => o.stage === stage);
            const stat = pipeline.data?.stages.find((s) => s.stage === stage);
            return (
              <div
                key={stage}
                className="opp-col"
                data-stage={stage}
                aria-label={STAGE_LABEL[stage]}
                onDragOver={(e: DragEvent) => {
                  e.preventDefault();
                  setDragOverStage(stage);
                }}
                onDragLeave={() => setDragOverStage((s) => (s === stage ? null : s))}
                onDrop={(e: DragEvent) => {
                  e.preventDefault();
                  setDragOverStage(null);
                  const id = e.dataTransfer.getData('text/plain') || dragId;
                  if (id) move(id, stage);
                  setDragId(null);
                }}
              >
                <div className="opp-col__head">
                  <span className="opp-col__dot" style={{ background: STAGE_TONE[stage] }} aria-hidden />
                  <span className="opp-col__name">{STAGE_LABEL[stage]}</span>
                  <span className="opp-col__count">{fmtInt(stat?.count ?? 0)}</span>
                </div>
                <div className="opp-col__value">{fmtVnd(stat?.value_vnd ?? 0)}</div>
                <div className={dragOverStage === stage ? 'opp-col__drop opp-col__drop--over' : 'opp-col__drop'}>
                  {cards.map((o) => (
                    <OppCard key={o.id} o={o} onDragStart={setDragId} onMove={() => setMoveFor(o)} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {moveFor ? <MoveStageDialog o={moveFor} onMove={move} onClose={() => setMoveFor(null)} /> : null}
      {changeStage.isError ? <InlineError>{errorText(changeStage.error)}</InlineError> : null}
    </div>
  );
}

function OppCard({ o, onDragStart, onMove }: { o: Opportunity; onDragStart: (id: string) => void; onMove: () => void }) {
  return (
    <div
      className="opp-card"
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData('text/plain', o.id);
        e.dataTransfer.effectAllowed = 'move';
        onDragStart(o.id);
      }}
    >
      <div className="opp-card__head">
        {o.heat !== null ? (
          <span className="opp-card__heat" style={{ color: heatTone(o.heat) }}>
            {fmtInt(o.heat)}
          </span>
        ) : null}
        <span className="opp-card__code">{o.code}</span>
        <button type="button" className="opp-card__menu" aria-label={`Chuyển giai đoạn cho ${o.code}`} onClick={onMove}>
          <Icon name="ph ph-dots-three-vertical" size={13} />
        </button>
      </div>
      <div className="opp-card__need">{o.need}</div>
      <div className="opp-card__who">{o.person?.name ?? o.group?.name ?? 'Chưa rõ đối tượng'}</div>
      <div className="opp-card__divider" />
      <div className="opp-card__row">
        <span className="opp-card__value">{fmtVnd(o.value_vnd)}</span>
        <span className="opp-card__conf" style={{ color: confidenceTone(o.confidence) }}>
          tin cậy {CONFIDENCE_LABEL[o.confidence]}
        </span>
      </div>
      {o.suggested_match ? (
        <div className="opp-card__match">
          <Icon name="ph ph-arrows-left-right" size={12} />
          <span>
            Gợi ý ghép: {o.suggested_match.item} · {o.suggested_match.person?.name ?? o.suggested_match.group?.name ?? '—'} (khớp {fmtInt(o.suggested_match.score)})
          </span>
        </div>
      ) : null}
      {o.risk_note ? (
        <div className="opp-card__risk">
          <Icon name="ph ph-warning" size={12} />
          <span>{o.risk_note}</span>
        </div>
      ) : null}
    </div>
  );
}

/** Thay thế bàn phím cho kéo thả — "Chuyển sang giai đoạn…" (PLAN §3.8, docs/handoff/07 §6 điều hướng bàn phím đủ). */
function MoveStageDialog({ o, onMove, onClose }: { o: Opportunity; onMove: (id: string, s: OppStage) => void; onClose: () => void }) {
  return (
    <Dialog open onClose={onClose} width={360} title="Chuyển sang giai đoạn…" kicker={`${o.code} · ${o.need}`}>
      <div className="dlg-list" role="list">
        {OPP_STAGES.map((s) => (
          <button
            key={s}
            type="button"
            className="sv-open"
            aria-pressed={s === o.stage}
            onClick={() => {
              onMove(o.id, s);
              onClose();
            }}
          >
            <span className="sv-open__name">{STAGE_LABEL[s]}</span>
            {s === o.stage ? <Icon name="ph ph-check" size={14} /> : null}
          </button>
        ))}
      </div>
    </Dialog>
  );
}
