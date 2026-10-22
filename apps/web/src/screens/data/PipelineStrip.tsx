import { Link } from 'react-router-dom';
import { Icon, Skeleton } from '@gen-harness/ui';
import { usePipeline } from '../../lib/dataQueries';
import { CardError } from '../common';
import { pipelineCards, type DataScreen } from './dataModel';

const TARGET: Record<number, string> = { 1: '/connections', 2: '/raw', 3: '/rules', 4: '/clean' };

/**
 * Dải pipeline dùng chung cho Kho thô · Quy tắc · Kho sạch (design `pipeline`):
 * Bridge lắng nghe → Kho thô → Core agent sàng lọc → Kho sạch. The
 * current screen's step(s) get the accent border.
 */
export function PipelineStrip({ screen }: { screen: DataScreen }) {
  const q = usePipeline();
  if (q.isError) {
    return (
      <div className="gh-card" aria-label="Dải pipeline">
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      </div>
    );
  }
  return (
    <nav className="pipe" aria-label="Dải pipeline dữ liệu">
      {q.isPending
        ? [1, 2, 3, 4].map((i) => (
            <div className="pipe-card" key={i} aria-hidden>
              <div className="pipe-card__top">
                <Skeleton width={28} height={28} radius={8} />
                <div className="pipe-card__head">
                  <Skeleton width={40} height={8} />
                  <Skeleton width="70%" height={11} style={{ marginTop: 5 }} />
                </div>
              </div>
              <Skeleton width="45%" height={20} style={{ marginTop: 9 }} />
              <Skeleton width="85%" height={9} style={{ marginTop: 6 }} />
            </div>
          ))
        : pipelineCards(q.data, screen).map((c) => (
            <Link
              key={c.step}
              to={TARGET[c.step]}
              className="pipe-card"
              data-on={c.on || undefined}
              aria-current={c.on ? 'step' : undefined}
              aria-label={`Bước ${c.step} — ${c.name}: ${c.value} ${c.unit}`}
            >
              <div className="pipe-card__top">
                <div className="pipe-card__icon" style={{ color: c.tone }}>
                  <Icon name={c.icon} size={15} />
                </div>
                <div className="pipe-card__head">
                  <div className="pipe-card__step">Bước {c.step}</div>
                  <div className="pipe-card__name" title={c.name}>{c.name}</div>
                </div>
                {!c.last ? <Icon name="ph ph-caret-double-right" size={14} className="pipe-card__arrow" /> : null}
              </div>
              <div className="pipe-card__nums">
                <span className="pipe-card__value" style={{ color: c.tone }}>
                  {c.value}
                </span>
                <span className="pipe-card__unit">{c.unit}</span>
              </div>
              <div className="pipe-card__note">{c.note}</div>
            </Link>
          ))}
    </nav>
  );
}
