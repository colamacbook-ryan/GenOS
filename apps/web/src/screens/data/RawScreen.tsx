import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ApiError, EVENT_TYPES, NOISE_CODE, SCREEN_BY_KEY, eventTypeLabel, type RawItem, type RawQuery, type RawState, type RefineryProgress, type Since } from '@gen-harness/contracts';
import { Button, EmptyState, FilterSelect, Icon, Skeleton, type FilterOption } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { qk2, usePipeline, useRawByGroup, useRawList, useRuns, useSchedule } from '../../lib/dataQueries';
import { downloadText } from '../../lib/download';
import { fmtClock, fmtInt } from '../../lib/format';
import { useCan, useOrgTimezone } from '../../lib/permissions';
import { toast } from '../../lib/toast';
import { useUrlState } from '../../lib/uiStore';
import { useNow } from '../../lib/useNow';
import { errorText } from '../../lib/errorText';
import { Bar, CardError, Panel, ScreenHead, SkeletonLines } from '../common';
import {
  RAW_STATE_LABEL,
  channelIcon,
  channelTone,
  confidenceTone,
  fmtConfidence,
  N5,
  rawStateLabel,
  rawStateStyle,
  runLine,
  triggerRows,
} from './dataModel';
import { PipelineStrip } from './PipelineStrip';
import { ScheduleDialog } from './ScheduleDialog';

const SINCE_OPTIONS: FilterOption<Since>[] = [
  { value: '24h', label: '24 giờ' },
  { value: '7d', label: '7 ngày' },
  { value: '30d', label: '30 ngày' },
  { value: 'all', label: 'Tất cả' },
];
const CONF_OPTIONS: FilterOption[] = [
  { value: '', label: '≥ 0' },
  { value: '0.6', label: '≥ 0,6' },
  { value: '0.8', label: '≥ 0,8' },
];
// v0.1.69 (F062): bộ lọc nhãn liệt kê đủ 15 loại + tin rác, hiện tên tiếng Việt (giá trị gửi lên vẫn là mã).
const KNOWN_LABELS = [...EVENT_TYPES.map((e) => e.code), NOISE_CODE];

export function RawScreen() {
  const meta = SCREEN_BY_KEY.raw;
  const tz = useOrgTimezone();
  const canManage = useCan('data.manage');
  const [channel, setChannel] = useUrlState<string>('ch', '');
  const [groupId, setGroupId] = useUrlState<string>('g', '');
  const [state, setState] = useUrlState<string>('st', '');
  const [since, setSince] = useUrlState<Since>('t', '24h');
  const [label, setLabel] = useUrlState<string>('lb', '');
  const [conf, setConf] = useUrlState<string>('conf', '');

  const filters: RawQuery = useMemo(
    () => ({
      channel: channel || undefined,
      group_id: groupId || undefined,
      state: (state || undefined) as RawState | undefined,
      since,
      label: label || undefined,
      min_confidence: conf ? Number(conf) : undefined,
    }),
    [channel, groupId, state, since, label, conf],
  );

  const pipeline = usePipeline();
  const list = useRawList(filters);
  const byGroup = useRawByGroup('24h');
  const rows = useMemo(() => list.data?.pages.flatMap((p) => p.items) ?? [], [list.data]);
  const total = list.data?.pages[0]?.total ?? 0;

  // Remember which ids were on screen so live rows can flash in.
  const seen = useRef<Set<string> | null>(null);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (!list.data) return;
    const ids = rows.map((r) => r.id);
    if (seen.current) {
      const added = ids.filter((id) => !seen.current!.has(id));
      if (added.length && added.length < 10) setFresh(new Set(added));
    }
    seen.current = new Set(ids);
  }, [rows, list.data]);

  const groupOptions: FilterOption[] = useMemo(() => {
    const opts: FilterOption[] = [{ value: '', label: 'Tất cả' }];
    const seenIds = new Set<string>();
    for (const g of byGroup.data ?? []) {
      if (g.group && !seenIds.has(g.group.id)) {
        seenIds.add(g.group.id);
        opts.push({ value: g.group.id, label: g.group.name });
      }
    }
    for (const r of rows) {
      if (r.group && !seenIds.has(r.group.id)) {
        seenIds.add(r.group.id);
        opts.push({ value: r.group.id, label: r.group.name });
      }
    }
    return opts;
  }, [byGroup.data, rows]);
  const groupName = groupOptions.find((o) => o.value === groupId)?.label;
  const groupsListening = pipeline.data?.groups_listening;

  const labelOptions: FilterOption[] = useMemo(() => {
    const set = new Set(KNOWN_LABELS);
    rows.forEach((r) => r.label && set.add(r.label));
    if (label) set.add(label);
    return [{ value: '', label: 'Tất cả' }, ...[...set].map((l) => ({ value: l, label: eventTypeLabel(l) }))];
  }, [rows, label]);

  const exportCsv = useMutation({
    mutationFn: () => api.raw.exportCsv({ ...filters }),
    onSuccess: (csv) => {
      const d = new Date().toISOString().slice(0, 10).replace(/-/g, '');
      downloadText(csv, `kho-tho-${d}.csv`);
      toast('Đã xuất tập thô theo bộ lọc hiện tại');
    },
    onError: (e) => toast(errorText(e), 'bad'),
  });

  const [runId, setRunId] = useState<string | null>(null);
  const progress = useQuery<RefineryProgress | null>({
    queryKey: qk2.progress(runId ?? '-'),
    queryFn: () => null,
    enabled: false,
    initialData: null,
  });
  const running = !!runId && (!progress.data || progress.data.status === 'running' || progress.data.status === 'queued');
  const run = useMutation({
    mutationFn: () => api.refinery.run(),
    onSuccess: (r) => {
      setRunId(r.run_id);
      toast('Đã bắt đầu một lượt sàng lọc');
    },
    onError: (e) =>
      toast(e instanceof ApiError && e.code === 'REFINERY_BUSY' ? 'Đang có một lượt chạy tay — chờ lượt này xong rồi thử lại.' : errorText(e), 'warn'),
  });
  useEffect(() => {
    if (progress.data && (progress.data.status === 'done' || progress.data.status === 'failed')) {
      toast(progress.data.status === 'done' ? `Lượt sàng lọc xong: ${fmtInt(progress.data.clean)} vào kho sạch` : 'Lượt sàng lọc lỗi', progress.data.status === 'done' ? 'ok' : 'bad');
      setRunId(null);
    }
  }, [progress.data]);

  // Infinite scroll: fetch the next page when the sentinel scrolls into view.
  const sentinel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = sentinel.current;
    if (!el || typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && list.hasNextPage && !list.isFetchingNextPage) void list.fetchNextPage();
    });
    io.observe(el);
    return () => io.disconnect();
  }, [list]);

  const actions = canManage ? (
    <>
      <Button variant="secondary" icon="ph ph-download-simple" loading={exportCsv.isPending} onClick={() => exportCsv.mutate()}>
        Xuất tập thô
      </Button>
      <Button variant="primary" icon="ph ph-play" loading={run.isPending || running} onClick={() => run.mutate()}>
        Chạy sàng lọc ngay
      </Button>
    </>
  ) : undefined;

  return (
    <>
      <PipelineStrip screen="raw" />
      <div className="screen">
        <ScreenHead title={meta.title} description={meta.description} maxWidth={760} actions={actions} />

        <div className="raw-filters" role="group" aria-label="Bộ lọc kho thô">
          <FilterSelect
            label="Kênh"
            value={channel}
            onChange={setChannel}
            options={[
              { value: '', label: 'Tất cả' },
              { value: 'zalo', label: 'Zalo' },
              { value: 'whatsapp', label: 'WhatsApp' },
            ]}
          />
          <FilterSelect
            label="Nhóm"
            value={groupId}
            onChange={setGroupId}
            options={groupOptions}
            display={groupId ? (groupName ?? 'Nhóm đã chọn') : groupsListening !== undefined ? `Tất cả ${fmtInt(groupsListening)} nhóm` : 'Tất cả'}
          />
          <FilterSelect
            label="Trạng thái"
            value={state}
            onChange={setState}
            options={[{ value: '', label: 'Tất cả' }, ...Object.entries(RAW_STATE_LABEL).map(([value, l]) => ({ value, label: l }))]}
          />
          <FilterSelect label="Thời gian" value={since} onChange={setSince} options={SINCE_OPTIONS} />
          <FilterSelect label="Nhãn" value={label} onChange={setLabel} options={labelOptions} />
          <FilterSelect label="Tin cậy" value={conf} onChange={setConf} options={CONF_OPTIONS} />
          <span className="raw-filters__spacer" />
          {list.data ? (
            <span className="raw-count" aria-live="polite">
              {fmtInt(total)} bản ghi thô · hiển thị {fmtInt(rows.length)} mới nhất
            </span>
          ) : null}
        </div>

        <div className="split-316">
          <div className="table-card">
            <div className="gh-table-scroll">
              <table className="gh-table w920" aria-label="Bản ghi thô" aria-busy={list.isFetching || undefined}>
                <thead>
                  <tr>
                    <th style={{ width: 88 }}>Lúc</th>
                    <th style={{ width: 46 }}>Kênh</th>
                    <th style={{ width: 106 }}>ID nhóm</th>
                    <th style={{ width: 100 }}>ID người</th>
                    <th style={{ minWidth: 300 }}>Nội dung thô</th>
                    <th style={{ width: 124 }}>Phân loại</th>
                    <th style={{ width: 70 }}>Tin cậy</th>
                    <th style={{ width: 112 }}>Trạng thái</th>
                  </tr>
                </thead>
                <tbody>
                  {list.isPending
                    ? Array.from({ length: 8 }, (_, i) => <SkeletonRow key={i} />)
                    : rows.map((r) => <RawRow key={r.id} row={r} tz={tz} fresh={fresh.has(r.id)} />)}
                  {list.isFetchingNextPage ? <SkeletonRow /> : null}
                </tbody>
              </table>
            </div>
            {list.isError ? (
              <CardError error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />
            ) : !list.isPending && rows.length === 0 ? (
              <EmptyState
                icon="ph ph-database"
                title="Chưa có bản ghi thô nào khớp bộ lọc"
                description={
                  <>
                    Bridge gom tin từ các nhóm đang lắng nghe về đây. Nới bộ lọc hoặc bật thêm nhóm ở{' '}
                    <Link to="/connections">Kết nối</Link>.
                  </>
                }
              />
            ) : null}
            <div ref={sentinel} aria-hidden style={{ height: 1 }} />
          </div>

          <div className="side-col">
            <TriggerCard canManage={canManage} />
            <Panel flush title="Thô theo nhóm nguồn" kicker="24 giờ · bấm để lọc bảng">
              {byGroup.isPending ? (
                <SkeletonLines rows={5} padding="12px 16px 14px" gap={9} />
              ) : byGroup.isError ? (
                <CardError error={byGroup.error} onRetry={() => void byGroup.refetch()} retrying={byGroup.isFetching} />
              ) : byGroup.data.length === 0 ? (
                <EmptyState icon="ph ph-users-three" title="Chưa có bản ghi nào trong 24 giờ" />
              ) : (
                <div className="bygroup">
                  {(() => {
                    const max = Math.max(1, ...byGroup.data.map((g) => g.n));
                    const named = byGroup.data.filter((g) => g.group).length;
                    return byGroup.data.map((g, i) => {
                      const others =
                        groupsListening !== undefined && groupsListening > named ? ` (${fmtInt(groupsListening - named)} nhóm)` : '';
                      const name = g.group ? g.group.name : `Nhóm khác${others}`;
                      const pressed = !!g.group && g.group.id === groupId;
                      return (
                        <button
                          key={g.group?.id ?? `other-${i}`}
                          type="button"
                          className="bygroup__row"
                          aria-pressed={pressed}
                          disabled={!g.group}
                          style={!g.group ? { cursor: 'default' } : undefined}
                          onClick={() => g.group && setGroupId(pressed ? '' : g.group.id)}
                        >
                          <span className="bygroup__name">{name}</span>
                          <Bar pct={(g.n / max) * 100} tone="var(--color-accent)" height={4} className="bygroup__bar" />
                          <span className="bygroup__n">{fmtInt(g.n)}</span>
                        </button>
                      );
                    });
                  })()}
                </div>
              )}
            </Panel>
            <RunsCard tz={tz} />
          </div>
        </div>
      </div>
    </>
  );
}

function RawRow({ row, tz, fresh }: { row: RawItem; tz: string; fresh: boolean }) {
  const st = rawStateStyle(row.state);
  return (
    <tr className={fresh ? 'raw-row--new' : undefined}>
      <td className="td-time">{fmtClock(row.received_at, tz)}</td>
      <td>
        <span title={row.channel.name} className="td-ch">
          <Icon name={channelIcon(row.channel.type)} size={14} color={channelTone(row.channel.type)} label={row.channel.name} />
        </span>
      </td>
      <td className="td-id">{row.group?.code ?? '—'}</td>
      <td className="td-id">{row.person?.code ?? '—'}</td>
      <td className="td-text min300">
        <div className="clamp2">{row.text}</div>
      </td>
      <td>{row.label ? <span className="mono-tag" style={row.label === NOISE_CODE ? { color: N5 } : undefined}>{eventTypeLabel(row.label)}</span> : <span className="td-id">—</span>}</td>
      <td className="td-conf" style={{ color: confidenceTone(row.confidence) }}>
        {fmtConfidence(row.confidence)}
      </td>
      <td>
        <span className="state-chip" style={{ color: st.color, borderColor: st.border }}>
          {rawStateLabel(row.state)}
        </span>
      </td>
    </tr>
  );
}

function SkeletonRow() {
  return (
    <tr aria-hidden>
      <td><Skeleton width={56} height={10} /></td>
      <td><Skeleton width={14} height={14} radius={7} /></td>
      <td><Skeleton width={78} height={10} /></td>
      <td><Skeleton width={62} height={10} /></td>
      <td><Skeleton width="90%" height={10} /><Skeleton width="60%" height={10} style={{ marginTop: 6 }} /></td>
      <td><Skeleton width={80} height={16} /></td>
      <td><Skeleton width={30} height={10} /></td>
      <td><Skeleton width={90} height={18} radius={999} /></td>
    </tr>
  );
}

function TriggerCard({ canManage }: { canManage: boolean }) {
  const s = useSchedule();
  const now = useNow(1000, s.isSuccess);
  const [editing, setEditing] = useState(false);
  return (
    <Panel flush
      title="Kích hoạt sàng lọc"
      kicker="Theo chu kỳ hoặc theo số lượng — cái nào đến trước"
      aside={
        canManage && s.data ? (
          <Button variant="ghost" className="btn-22" onClick={() => setEditing(true)} aria-label="Sửa lịch kích hoạt sàng lọc">
            Sửa
          </Button>
        ) : undefined
      }
    >
      {s.isPending ? (
        <SkeletonLines rows={4} />
      ) : s.isError ? (
        <CardError error={s.error} onRetry={() => void s.refetch()} retrying={s.isFetching} />
      ) : (
        <div className="trig">
          {triggerRows(s.data, now).map((t) => (
            <div className="trig-row" key={t.label}>
              <div className="trig-head">
                <span className="trig-label">{t.label}</span>
                <span className="trig-value">{t.value}</span>
              </div>
              <Bar pct={t.bar} tone={t.tone} />
              <span className="trig-note" aria-live={t.label === 'Chu kỳ thời gian' ? 'off' : undefined}>
                {t.note}
              </span>
            </div>
          ))}
          <Link to="/rules" className="gh-btn gh-btn--secondary btn-28">
            <Icon name="ph ph-funnel" size={13} />
            Mở quy tắc sàng lọc
          </Link>
        </div>
      )}
      {s.data ? <ScheduleDialog open={editing} onClose={() => setEditing(false)} schedule={s.data} /> : null}
    </Panel>
  );
}

function RunsCard({ tz }: { tz: string }) {
  const runs = useRuns();
  return (
    <Panel flush title="Chu kỳ gần nhất" kicker="Các lượt sàng lọc">
      {runs.isPending ? (
        <SkeletonLines rows={5} padding="12px 16px" gap={14} />
      ) : runs.isError ? (
        <CardError error={runs.error} onRetry={() => void runs.refetch()} retrying={runs.isFetching} />
      ) : runs.data.length === 0 ? (
        <EmptyState icon="ph ph-funnel" title="Chưa có lượt sàng lọc nào" description="Lượt đầu chạy khi hết chu kỳ hoặc khi kho thô vượt ngưỡng." />
      ) : (
        <div className="runs">
          {runs.data.slice(0, 5).map((r) => {
            const l = runLine(r);
            return (
              <div className="runs__row" key={r.id}>
                <span className="runs__dot" style={{ background: l.tone }} aria-hidden />
                <span className="runs__time">{fmtClock(r.started_at, tz)}</span>
                <span className="runs__meta">{l.meta}</span>
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
}
