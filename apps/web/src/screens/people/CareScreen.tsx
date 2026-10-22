import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import type { CareIssueRow, CareResponseRow, CareScenarioItem, CareScenarioStatus } from '@gen-harness/contracts';
import { EmptyState, Icon, Segmented } from '@gen-harness/ui';
import { fmtDMClock, fmtDec, fmtInt } from '../../lib/format';
import { useUrlState } from '../../lib/uiStore';
import { CardError, Panel, ScreenHead, SkeletonLines, StateChip } from '../common';
import { PROMISES_TRACKED } from '../../lib/features';
import { BAD, ISSUE_KIND_LABEL, OK, SCENARIO_LABEL, STAFF_HOWTO, WARN, fmtVnd, minuteBandTone } from './peopleModel';
import { useCareIssues, useCareResponseTimes, useCareScenarios } from './queries';

const CARE_DESC = 'Hệ thống không chỉ biết đã nhắn hay chưa, mà biết cách nhắn: follow quá sớm hay quá muộn, quên khách sau báo giá, và kịch bản nào đang chuyển thành deal.';
const CARE_DESC_WITH_PROMISES =
  'Hệ thống không chỉ biết đã nhắn hay chưa, mà biết cách nhắn: follow quá sớm hay quá muộn, hứa rồi quên, quên khách sau báo giá, và kịch bản nào đang chuyển thành deal.';

export function CareScreen() {
  const response = useCareResponseTimes({});
  const issues = useCareIssues({ limit: 20 });
  const [status, setStatus] = useUrlState<CareScenarioStatus | 'all'>('status', 'all');
  const scenarios = useCareScenarios(status === 'all' ? {} : { status });

  // Không có endpoint KPI riêng (docs/api/phase-3-people.md chỉ có 3 endpoint care) — 5 ô này tính từ dữ liệu
  // thật của response-times + repeated-issues, không hardcode; quyết định tự đưa ra thay vì bịa thêm endpoint.
  const kpis = useMemo(() => {
    const rows: Array<{ label: string; value: string; unit: string; sub: string; tone: string }> = [];
    if (response.data) {
      const t = response.data.totals;
      rows.push({
        label: 'Phản hồi nhanh (<15 phút)', value: fmtDec(t.fast_pct, 1), unit: '%',
        sub: `${fmtInt(t.fast)} / ${fmtInt(t.total_answered)} lượt trả lời`, tone: t.fast_pct >= 60 ? OK : t.fast_pct >= 35 ? WARN : BAD,
      });
      rows.push({ label: 'Phản hồi chậm (>60 phút)', value: fmtInt(t.slow), unit: 'lượt', sub: `${response.data.items.length} nhân viên trong lưới`, tone: t.slow === 0 ? OK : WARN });
      rows.push({ label: 'Khách chưa ai trả lời', value: fmtInt(response.data.unattended), unit: 'khách', sub: 'Trong cửa sổ theo dõi hiện tại', tone: response.data.unattended === 0 ? OK : BAD });
    }
    if (issues.data) {
      const brokenCount = issues.data.items.filter((i) => i.kind === 'broken_promise').reduce((s, i) => s + i.count, 0);
      const abandonedCount = issues.data.items.filter((i) => i.kind === 'abandoned_customer').length;
      if (PROMISES_TRACKED) rows.push({ label: 'Hứa rồi quên', value: fmtInt(brokenCount), unit: 'lần', sub: 'Cộng dồn theo người, 30 ngày', tone: brokenCount === 0 ? OK : BAD });
      rows.push({ label: 'Khách bị bỏ rơi', value: fmtInt(abandonedCount), unit: 'khách', sub: 'Không ai chạm liên tục', tone: abandonedCount === 0 ? OK : WARN });
    }
    return rows;
  }, [response.data, issues.data]);
  const kpisLoading = response.isPending || issues.isPending;

  return (
    <div className="screen">
      <ScreenHead
        title="Chất lượng chăm sóc"
        description={PROMISES_TRACKED ? CARE_DESC_WITH_PROMISES : CARE_DESC}
        maxWidth={700}
      />

      <div className="care-kpis">
        {kpisLoading
          ? Array.from({ length: PROMISES_TRACKED ? 5 : 4 }, (_, i) => <div key={i} className="gh-card care-kpi"><SkeletonLines rows={1} padding="0" /></div>)
          : kpis.map((k) => (
              <div key={k.label} className="gh-card care-kpi">
                <span className="care-kpi__label">{k.label}</span>
                <div className="care-kpi__value">
                  <span style={{ color: k.tone }}>{k.value}</span>
                  <span className="care-kpi__unit">{k.unit}</span>
                </div>
                <div className="care-kpi__sub">{k.sub}</div>
              </div>
            ))}
      </div>

      <div className="care-cols">
        <Panel
          title="Lưới phản hồi theo khung giờ"
          kicker="<15 · 15–60 · >60 phút — theo nhân viên"
          bodyClass="care-grid"
          aside={
            <div className="care-legend">
              <span><span className="care-legend__dot" style={{ background: OK }} />dưới 15 phút</span>
              <span><span className="care-legend__dot" style={{ background: WARN }} />15–60</span>
              <span><span className="care-legend__dot" style={{ background: BAD }} />trên 60</span>
            </div>
          }
        >
          {response.isPending ? (
            <SkeletonLines rows={5} padding="0" />
          ) : response.isError ? (
            <CardError error={response.error} onRetry={() => void response.refetch()} retrying={response.isFetching} />
          ) : response.data.items.length === 0 ? (
            <EmptyState icon="ph ph-chat-circle-dots" title="Chưa có dữ liệu phản hồi" description={`Lưới tính theo nhân viên trả lời khách. ${STAFF_HOWTO}`} />
          ) : (
            <ResponseGrid items={response.data.items} />
          )}
        </Panel>

        <div className="care-side">
          <Panel title="Lỗi chăm sóc lặp lại" kicker="30 ngày · toàn tổ chức" bodyClass="care-issues">
            {issues.isPending ? (
              <SkeletonLines rows={4} padding="0" />
            ) : issues.isError ? (
              <CardError error={issues.error} onRetry={() => void issues.refetch()} retrying={issues.isFetching} />
            ) : issues.data.items.length === 0 ? (
              <EmptyState icon="ph ph-check-circle" title="Chưa phát hiện lỗi lặp lại nào" />
            ) : (
              issues.data.items.map((i, idx) => <IssueRow key={idx} i={i} />)
            )}
          </Panel>

          <Panel
            title="Kịch bản thắng và mất khách"
            kicker={scenarios.data ? `${scenarios.data.total} ca đã đóng` : 'Rút từ deal đã đóng'}
            bodyClass="care-scenarios"
            aside={
              <Segmented
                label="Lọc kịch bản"
                value={status}
                onChange={setStatus}
                options={[
                  { value: 'all', label: 'Tất cả' },
                  { value: 'won', label: 'Thắng' },
                  { value: 'lost', label: 'Mất' },
                ]}
              />
            }
          >
            {scenarios.isPending ? (
              <SkeletonLines rows={4} padding="0" />
            ) : scenarios.isError ? (
              <CardError error={scenarios.error} onRetry={() => void scenarios.refetch()} retrying={scenarios.isFetching} />
            ) : scenarios.data.items.length === 0 ? (
              <EmptyState icon="ph ph-flag" title="Chưa có kịch bản nào khớp bộ lọc" />
            ) : (
              <>
                <div className="care-scn-summary">
                  <span>
                    Thắng: {scenarios.data.summary.won.count} ca · phản hồi nhanh TB {fmtDec(scenarios.data.summary.won.avg_fast_pct, 1)}%
                    {PROMISES_TRACKED ? ` · hứa vỡ TB ${fmtDec(scenarios.data.summary.won.avg_broken_promises, 1)}` : ''}
                  </span>
                  <span>
                    Mất: {scenarios.data.summary.lost.count} ca · phản hồi nhanh TB {fmtDec(scenarios.data.summary.lost.avg_fast_pct, 1)}%
                    {PROMISES_TRACKED ? ` · hứa vỡ TB ${fmtDec(scenarios.data.summary.lost.avg_broken_promises, 1)}` : ''}
                  </span>
                </div>
                {scenarios.data.items.map((s) => (
                  <ScenarioRow key={s.deal.id} s={s} />
                ))}
              </>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}

function ResponseGrid({ items }: { items: CareResponseRow[] }) {
  return (
    <div className="care-grid-table" role="table" aria-label="Lưới phản hồi theo khung giờ">
      <div className="care-grid-row care-grid-row--head" role="row">
        <span role="columnheader">Nhân viên</span>
        <span role="columnheader">&lt;15 phút</span>
        <span role="columnheader">15–60</span>
        <span role="columnheader">&gt;60</span>
        <span role="columnheader">% nhanh</span>
        <span role="columnheader">TB phút</span>
      </div>
      {items.map((r) => (
        <div key={r.staff.id} className="care-grid-row" role="row">
          <span role="cell" className="care-grid-row__name" title={r.staff.name}>{r.staff.name}</span>
          <span role="cell" className="care-grid-cell" style={{ color: OK }} title={`${r.fast} lượt dưới 15 phút`}>{fmtInt(r.fast)}</span>
          <span role="cell" className="care-grid-cell" style={{ color: WARN }} title={`${r.normal} lượt 15–60 phút`}>{fmtInt(r.normal)}</span>
          <span role="cell" className="care-grid-cell" style={{ color: BAD }} title={`${r.slow} lượt trên 60 phút`}>{fmtInt(r.slow)}</span>
          <span role="cell" style={{ color: minuteBandTone(r.avg_minutes) }}>{fmtDec(r.fast_pct, 1)}%</span>
          <span role="cell" className="care-grid-row__avg">{fmtDec(r.avg_minutes, 1)}</span>
        </div>
      ))}
    </div>
  );
}

function IssueRow({ i }: { i: CareIssueRow }) {
  const tone = i.repeated ? BAD : WARN;
  return (
    <div className="care-issue-row">
      <div className="care-issue-row__main">
        <span className="care-issue-row__label">{ISSUE_KIND_LABEL[i.kind] ?? i.kind}</span>
        <span className="care-issue-row__who">{i.subject.name}</span>
      </div>
      <div className="care-issue-row__meta">
        {i.repeated ? <StateChip color={tone}>lặp lại</StateChip> : null}
        <span style={{ color: tone, fontFamily: 'var(--font-mono)' }}>{i.count}</span>
        <span className="care-issue-row__at">{fmtDMClock(i.last_at)}</span>
      </div>
    </div>
  );
}

function ScenarioRow({ s }: { s: CareScenarioItem }) {
  const tone = s.deal.status === 'won' ? OK : BAD;
  const icon = s.deal.status === 'won' ? 'ph ph-trophy' : 'ph ph-warning-octagon';
  return (
    <div className="care-scn-row">
      <Icon name={icon} size={15} color={tone} />
      <div className="care-scn-row__body">
        <div className="care-scn-row__head">
          <span className="care-scn-row__code">{s.deal.code}</span>
          <span style={{ color: tone }}>{SCENARIO_LABEL[s.deal.status]}</span>
          <span className="care-scn-row__amount">{fmtVnd(s.deal.amount_vnd)}</span>
          {s.person ? (
            <Link className="care-scn-row__link" to="/deals" title={`Mở Deal & Vụ việc — ${s.deal.code}`}>
              {s.person.name} · xem deal
            </Link>
          ) : null}
        </div>
        <div className="care-scn-row__note">{s.note}</div>
        {s.response ? (
          <div className="care-scn-row__stats">
            phản hồi nhanh {fmtDec(s.response.fast_pct, 1)}% · TB {fmtDec(s.response.avg_minutes, 1)} phút{PROMISES_TRACKED ? ` · ${s.broken_promises} lời hứa bị vỡ` : ''}
          </div>
        ) : null}
      </div>
    </div>
  );
}
