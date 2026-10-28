import { useSearchParams } from 'react-router-dom';
import { useState } from 'react';
import { Badge, EmptyState, SelectField } from '@gen-harness/ui';
import { CardError, Panel, SkeletonLines } from '../screens/common';
import {
  ALL_KIND_LABEL,
  DEFAULT_FILTERS,
  LEVELS,
  STATUS_FILTERS,
  confidenceLabel,
  levelMeta,
  signalKindLabel,
  statusLabel,
  txt,
  when,
  type SignalFilters,
  type SignalItem,
} from './intelHubModel';
import { useSignals } from './intelHubQueries';
import { SignalDrawer } from './SignalDrawer';
import { SignalVerdict } from './SignalVerdict';

/**
 * v0.1.79 (G1) — tab "Tín hiệu": bảng MỌI tín hiệu (lọc mức / trạng thái / loại) + ngăn chi tiết bên phải. Dòng được chọn nằm trên
 * URL (`?tin=<id>`) để chuông / bản tin / Gen mở thẳng đúng tín hiệu. Bảng cuộn ngang ở màn hẹp; ở điện thoại ngăn chi tiết thành cả màn.
 */
export function SignalGrid() {
  const [params, setParams] = useSearchParams();
  const selected = params.get('tin');
  const [f, setF] = useState<SignalFilters>(DEFAULT_FILTERS);
  const q = useSignals(f);
  const select = (id: string | null) => {
    const next = new URLSearchParams(params);
    if (id) next.set('tin', id);
    else next.delete('tin');
    setParams(next, { replace: false });
  };
  const items = q.data?.items ?? [];
  return (
    <div className={`ih-split${selected ? ' ih-split--open' : ''}`}>
      <Panel
        title="Mọi tín hiệu"
        kicker={q.data ? `${items.length} tín hiệu${f.status === 'open' ? ' đang mở' : ''}` : 'Gen nghe được từ nhóm chat'}
        label="Tín hiệu"
        genTarget="intel.signals.table"
        flush
        aside={
          <div className="ih-filters" role="group" aria-label="Lọc tín hiệu" data-gen-target="intel.signals.filters">
            <SelectField
              label="Mức"
              value={f.level}
              onChange={(e) => setF({ ...f, level: e.target.value })}
              options={[{ value: '', label: 'Mọi mức' }, ...LEVELS.map((l) => ({ value: l.level, label: `${l.dot} ${l.label}` }))]}
            />
            <SelectField
              label="Trạng thái"
              value={f.status}
              onChange={(e) => setF({ ...f, status: e.target.value })}
              options={STATUS_FILTERS.map((s) => ({ value: s, label: statusLabel(s) }))}
            />
            <SelectField
              label="Loại"
              value={f.kind}
              onChange={(e) => setF({ ...f, kind: e.target.value })}
              options={[{ value: '', label: 'Mọi loại' }, ...Object.entries(ALL_KIND_LABEL).map(([value, label]) => ({ value, label }))]}
            />
          </div>
        }
      >
        {q.isPending ? (
          <SkeletonLines rows={6} />
        ) : q.isError ? (
          <div className="ih-pad">
            <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            icon="ph ph-radar"
            title="Không có tín hiệu nào khớp bộ lọc."
            description={q.data.hidden_pin_n > 0 ? `Còn ${q.data.hidden_pin_n} tín hiệu riêng tư — nhập mã PIN để xem.` : 'Khi Gen nghe thấy điều đáng để ý, nó hiện ở đây.'}
          />
        ) : (
          <div className="ih-tablewrap">
            <table className="ih-table">
              <thead>
                <tr>
                  <th scope="col">Mã</th>
                  <th scope="col">Mức</th>
                  <th scope="col">Loại</th>
                  <th scope="col">Tiêu đề</th>
                  <th scope="col">Việc Sếp cần làm</th>
                  <th scope="col">Độ chắc</th>
                  <th scope="col">Trạng thái</th>
                  <th scope="col">Lúc</th>
                  <th scope="col">Chấm</th>
                </tr>
              </thead>
              <tbody>
                {items.map((s) => (
                  <Row key={s.id} s={s} active={s.id === selected} onOpen={() => select(s.id)} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {q.isSuccess && items.length > 0 && q.data.hidden_pin_n > 0 ? (
          <p className="muted-note ih-pad">Còn {q.data.hidden_pin_n} tín hiệu riêng tư — nhập mã PIN để xem.</p>
        ) : null}
      </Panel>
      {selected ? <SignalDrawer key={selected} id={selected} onClose={() => select(null)} /> : null}
    </div>
  );
}

function Row({ s, active, onOpen }: { s: SignalItem; active: boolean; onOpen: () => void }) {
  const lv = levelMeta(s.level);
  const verdict = s.feedback_verdict;
  return (
    <tr className={`ih-row${active ? ' ih-row--active' : ''}`} data-testid="intel-signal-row" aria-selected={active}>
      <td className="mono">{txt(s.code) || '—'}</td>
      <td>
        <span aria-hidden>{lv.dot}</span> {lv.label}
      </td>
      <td>{signalKindLabel(s.kind)}</td>
      <td className="ih-cell-title">
        <button type="button" className="ih-linkbtn" onClick={onOpen}>
          {txt(s.title)}
        </button>
      </td>
      <td className="ih-cell-action">{txt(s.suggested_action) || '—'}</td>
      <td>{confidenceLabel(s.confidence_level) || '—'}</td>
      <td>
        <Badge kind="status" tone={s.status === 'open' ? 'warn' : 'neutral'}>{statusLabel(s.status)}</Badge>
      </td>
      <td className="muted-note ih-nowrap">{when(s.created_at) || '—'}</td>
      <td>{verdict ? <SignalVerdict id={s.id} title={txt(s.title)} verdict={verdict} compact /> : <span className="muted-note">Chưa chấm</span>}</td>
    </tr>
  );
}
