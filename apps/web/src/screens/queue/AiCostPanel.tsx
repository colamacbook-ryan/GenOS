import { useEffect, useRef } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import type { AiCost } from '@gen-harness/contracts';
import { fmtInt, fmtVnd } from '../../lib/format';
import { useCan } from '../../lib/permissions';
import { CardError, Panel, SkeletonLines } from '../common';
import { useAiCost } from '../system/queries';

/** Kicker: "<tổng> ₫ / trần <trần> ₫" hoặc "<tổng> ₫ · chưa đặt trần". Số tiền thiếu hiện "0 ₫" (không "—"). */
function aiCostKicker(d: Pick<AiCost, 'total_vnd' | 'budget_vnd'>): string {
  return d.budget_vnd == null ? `${fmtVnd(d.total_vnd ?? 0)} · chưa đặt trần` : `${fmtVnd(d.total_vnd ?? 0)} / trần ${fmtVnd(d.budget_vnd)}`;
}

/**
 * v0.1.41 (F-84): "Chi phí AI hôm nay" ở Tổng quan › Sức khoẻ (ngay sau "Sức khoẻ hệ thống") — tổng ₫ trong ngày (giờ VN)
 * so với trần, chi phí theo agent, số lượt chưa có giá (dẫn tới Bộ não AI để nhập giá), đánh giá "Hữu ích" 7 ngày.
 * Chỉ vai trò có `system.read`. `?focus=ai-cost` (sự cố `ai.budget_exceeded`) cuộn tới thẻ.
 */
export function AiCostPanel() {
  const canRead = useCan('system.read');
  const q = useAiCost(canRead);
  const [params] = useSearchParams();
  const focus = params.get('focus') === 'ai-cost';
  const box = useRef<HTMLDivElement>(null);
  const focused = useRef(false);
  useEffect(() => {
    if (!focus || focused.current || !box.current || q.isPending) return;
    focused.current = true;
    box.current.scrollIntoView?.({ block: 'start' });
    box.current.focus({ preventScroll: true });
  }, [focus, q.isPending]);
  if (!canRead) return null;
  return (
    <div id="ai-cost" ref={box} tabIndex={-1} className="ov-ai-cost">
      <Panel
        title="Chi phí AI hôm nay"
        kicker={q.data ? aiCostKicker(q.data) : 'Theo giờ Việt Nam'}
        label="Chi phí AI hôm nay"
        className="ov-ai-cost__card"
      >
        {q.isPending ? (
          <SkeletonLines rows={4} padding="4px 0" />
        ) : q.isError ? (
          <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        ) : (
          <AiCostBody d={q.data} />
        )}
      </Panel>
    </div>
  );
}

function AiCostBody({ d }: { d: AiCost }) {
  const canManage = useCan('system.manage');
  const over = !!d.over_budget;
  const pct = d.budget_vnd ? Math.min(100, (d.total_vnd * 100) / d.budget_vnd) : 0;
  const agents = [...(Array.isArray(d.agents) ? d.agents : [])].sort((a, b) => b.cost_vnd - a.cost_vnd || b.calls - a.calls);
  const subs = (Array.isArray(d.models) ? d.models : []).filter((m) => m.price_source === 'subscription' && m.calls_today > 0);
  const fb = d.feedback_7d;
  const rated = fb ? fb.helpful + fb.not_helpful : 0;
  const ratedBriefing = fb ? fb.briefing_helpful + fb.briefing_not_helpful : 0;
  return (
    <div className="ov-ai-cost__body" data-over={over ? 'yes' : 'no'}>
      <div className="ov-ai-cost__total">
        <b data-testid="ai-cost-total">{fmtVnd(d.total_vnd ?? 0)}</b>
        {over ? (
          <span className="ov-ai-cost__over" role="status">
            Vượt trần
          </span>
        ) : null}
      </div>
      {d.budget_vnd != null ? (
        <div
          className="ov-ai-cost__bar"
          role="progressbar"
          aria-label="Chi phí so với trần"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(pct)}
        >
          <span style={{ width: `${over ? 100 : pct}%` }} />
        </div>
      ) : null}
      {agents.length === 0 ? (
        <p className="ov-ai-cost__note">Hôm nay chưa có lượt gọi AI nào.</p>
      ) : (
        <table className="ov-ai-cost__table">
          <thead>
            <tr>
              <th>Agent</th>
              <th>Lượt</th>
              <th>Chi phí</th>
            </tr>
          </thead>
          <tbody>
            {agents.map((a) => (
              <tr key={a.agent_key} data-testid={`ai-cost-agent-${a.agent_key}`}>
                <td>{a.label}</td>
                <td className="mono">{fmtInt(a.calls)}</td>
                <td className="mono">{fmtVnd(a.cost_vnd ?? 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {subs.map((m) => (
        <p className="ov-ai-cost__note" key={m.model_id}>
          {m.provider_name}: {fmtInt(m.calls_today)} lượt — trả theo gói (0 ₫)
        </p>
      ))}
      {d.unpriced_calls > 0 ? (
        <p className="ov-ai-cost__note ov-ai-cost__note--warn">
          {fmtInt(d.unpriced_calls)} lượt gọi chưa có giá —{' '}
          {canManage ? <Link to="/system?tab=brain">nhập giá ở Bộ não AI</Link> : 'báo Owner nhập giá ở Bộ não AI'}
        </p>
      ) : null}
      {fb ? (
        <p className="ov-ai-cost__note">
          {rated > 0 ? `Hữu ích 7 ngày: ${fmtInt(fb.helpful)}/${fmtInt(rated)}` : 'Hữu ích 7 ngày: chưa có đánh giá'}
          {ratedBriefing > 0 ? ` · Bản tin Gen ${fmtInt(fb.briefing_helpful)}/${fmtInt(ratedBriefing)}` : ''}
        </p>
      ) : null}
    </div>
  );
}
