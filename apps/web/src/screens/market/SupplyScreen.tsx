import { useNavigate } from 'react-router-dom';
import type { Match, MarketSignal } from '@gen-harness/contracts';
import { Button, EmptyState, Icon } from '@gen-harness/ui';
import { CardError, InlineError, Panel, ScreenHead, SkeletonLines } from '../common';
import { errorText } from '../../lib/errorText';
import { fmtInt } from '../../lib/format';
import { fmtVnd, heatTone, scoreTone } from './marketModel';
import { useIntroduceMatch, useMatches, useRecomputeMatches, useRejectMatch, useSupply } from './queries';

export function SupplyScreen() {
  const demand = useSupply({ side: 'demand', limit: 100 });
  const supply = useSupply({ side: 'supply', limit: 100 });
  const matches = useMatches({ status: 'suggested', limit: 100 });
  const recompute = useRecomputeMatches();
  const introduce = useIntroduceMatch();
  const reject = useRejectMatch();
  const navigate = useNavigate();

  const introducedCount = matches.data?.items.length ?? 0;

  return (
    <div className="screen">
      <ScreenHead
        title="Cung ↔ Cầu"
        description="Hai danh sách rút từ kho sạch: người đang cần nguồn hàng và người đang cần bán. Hệ thống đề xuất cặp ghép, Sếp quyết định có bắt tay hay không."
        maxWidth={760}
        actions={
          <div className="sup-head-actions">
            <span className="sup-head-actions__count">
              {fmtInt((demand.data?.total ?? 0) + (supply.data?.total ?? 0))} tín hiệu · {fmtInt(introducedCount)} cặp ghép được
            </span>
            <Button variant="primary" icon="ph ph-arrows-left-right" loading={recompute.isPending} onClick={() => recompute.mutate()}>
              Chạy ráp khớp
            </Button>
          </div>
        }
      />
      {recompute.isSuccess ? (
        <div className="gp-recompute-ok" role="status">
          Đã chấm lại {recompute.data.matches} cặp ghép.
        </div>
      ) : recompute.isError ? (
        <InlineError>{errorText(recompute.error)}</InlineError>
      ) : null}

      <div className="sup-sides">
        <SideList title="Đang cần nguồn hàng" subtitle="Demand · CẦU" tone="var(--color-ok)" icon="ph ph-magnifying-glass" q={demand} />
        <SideList title="Đang cần bán" subtitle="Supply · CUNG" tone="var(--color-accent-400)" icon="ph ph-storefront" q={supply} />
      </div>

      <Panel title="Cặp ghép hệ thống đề xuất" kicker="Cầu ↔ Cung · kèm lý do và điểm khớp" bodyClass="sup-matches" aside={<span className="sup-matches__note">Không tự liên hệ hai bên khi chưa được Sếp duyệt</span>}>
        {matches.isPending ? (
          <SkeletonLines rows={5} />
        ) : matches.isError ? (
          <CardError error={matches.error} onRetry={() => void matches.refetch()} retrying={matches.isFetching} />
        ) : matches.data.items.length === 0 ? (
          <EmptyState icon="ph ph-handshake" title="Chưa có gợi ý ghép nào đạt ngưỡng" description="Bấm “Chạy ráp khớp” để chấm lại điểm ngay, hoặc chờ việc nền chạy mỗi 15 phút." />
        ) : (
          matches.data.items.map((mt) => (
            <MatchRow
              key={mt.id}
              mt={mt}
              busy={introduce.isPending || reject.isPending}
              onIntroduce={() =>
                introduce.mutate(mt.id, {
                  onSuccess: (r) => {
                    if (r.draft.id) navigate(`/workbench?id=${encodeURIComponent(r.draft.id)}`);
                  },
                })
              }
              onReject={() => reject.mutate(mt.id)}
            />
          ))
        )}
        {introduce.isError ? <InlineError>{errorText(introduce.error)}</InlineError> : null}
        {reject.isError ? <InlineError>{errorText(reject.error)}</InlineError> : null}
      </Panel>
    </div>
  );
}

function SideList({
  title,
  subtitle,
  tone,
  icon,
  q,
}: {
  title: string;
  subtitle: string;
  tone: string;
  icon: string;
  q: ReturnType<typeof useSupply>;
}) {
  return (
    <section className="gh-card sup-side" style={{ borderTopColor: tone }} aria-label={title}>
      <div className="gh-card__header">
        <div className="sup-side__head">
          <Icon name={icon} size={17} color={tone} />
          <div>
            <div className="gh-card__title">{title}</div>
            <div className="gh-card__kicker">{subtitle}</div>
          </div>
        </div>
        {q.data ? <span className="sup-side__count">{fmtInt(q.data.total)}</span> : null}
      </div>
      {q.isPending ? (
        <SkeletonLines rows={4} />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : q.data.items.length === 0 ? (
        <EmptyState icon="ph ph-tray" title="Chưa có tín hiệu nào" />
      ) : (
        <div className="sup-side__list">
          {q.data.items.map((s) => (
            <SignalRow key={s.id} s={s} />
          ))}
        </div>
      )}
    </section>
  );
}

function SignalRow({ s }: { s: MarketSignal }) {
  return (
    <div className="sup-row">
      <div className="sup-row__line">
        {s.heat !== null ? (
          <span className="sup-row__heat" style={{ color: heatTone(s.heat) }}>
            {fmtInt(s.heat)}
          </span>
        ) : null}
        <span className="sup-row__item" title={s.item}>{s.item}</span>
        <span className="sup-row__value">{fmtVnd(s.value_vnd)}</span>
      </div>
      <div className="sup-row__line">
        <span className="sup-row__pid">{s.person?.code ?? s.group?.code ?? '—'}</span>
        <span className="sup-row__who">{s.person?.name ?? s.group?.name ?? 'Chưa rõ'}</span>
        {s.location ? <span className="sup-row__loc">{s.location}</span> : null}
      </div>
    </div>
  );
}

function MatchRow({ mt, busy, onIntroduce, onReject }: { mt: Match; busy: boolean; onIntroduce: () => void; onReject: () => void }) {
  return (
    <div className="sup-match">
      <div className="sup-match__side">
        <div className="sup-match__item">{mt.demand.item}</div>
        <div className="sup-match__who">{mt.demand.person?.name ?? mt.demand.group?.name ?? '—'}</div>
      </div>
      <div className="sup-match__mid">
        <span className="sup-match__score" style={{ color: scoreTone(mt.score) }}>
          khớp {fmtInt(mt.score)}
        </span>
        <ul className="sup-match__reasons">
          {mt.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      </div>
      <div className="sup-match__side">
        <div className="sup-match__item">{mt.supply.item}</div>
        <div className="sup-match__who">{mt.supply.person?.name ?? mt.supply.group?.name ?? '—'}</div>
      </div>
      <div className="sup-match__actions">
        <Button variant="secondary" size="sm" icon="ph ph-pen-nib" disabled={busy} onClick={onIntroduce}>
          Giới thiệu hai bên
        </Button>
        <Button variant="ghost" size="sm" disabled={busy} onClick={onReject}>
          Từ chối
        </Button>
      </div>
    </div>
  );
}
