import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, SCREEN_BY_KEY, type AutoSendItem, type ListenLogDecision, type ListenLogEvent, type ListenLogItem, type ListenLogMessage, type Since } from '@gen-harness/contracts';
import { Button, EmptyState, ErrorState, FilterSelect, Icon, Segmented, Skeleton, Switch, TextField, type FilterOption } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { fmtDMClock, fmtHM, fmtInt } from '../../lib/format';
import { errorDetail, errorText } from '../../lib/errorText';
import { setListenLogLive, useListenLog, useListenLogStats } from '../../lib/listenLogQueries';
import { useOrgTimezone } from '../../lib/permissions';
import { useUrlPatch, useUrlState } from '../../lib/uiStore';
import { useDebouncedValue } from '../../lib/useDebouncedValue';
import { useIsOwnerRole } from '../../owner/queries';
import { CardError, InlineError, ScreenHead } from '../common';
import { autoSendsKey, autoStatusLabel, basisLabel, channelName } from '../connections/ownerChannelModel';
import { channelIcon, channelTone } from './dataModel';
import {
  KIND_OPTIONS,
  NOT_LISTENING_NOTE,
  SINCE_OPTIONS,
  activeFilterCount,
  autoSendsForChannel,
  decisionTone,
  draftHref,
  eventKindLabel,
  eventTone,
  eventsHiddenByFilters,
  groupByDay,
  screeningText,
  screeningTone,
  senderName,
  toQuery,
  type LogFilters,
} from './listenLogModel';

const KIND_LABEL: Record<string, string> = {
  image: 'Ảnh',
  file: 'Tệp',
  sticker: 'Nhãn dán',
  reaction: 'Biểu tượng cảm xúc',
  system: 'Tin hệ thống',
  other: 'Tin không có chữ',
};

export function ListenLogScreen() {
  const meta = SCREEN_BY_KEY.listenlog;
  const tz = useOrgTimezone();
  const [channel] = useUrlState<string>('ch', '');
  const [groupId, setGroupId] = useUrlState<string>('g', '');
  const [kind, setKind] = useUrlState<LogFilters['kind']>('k', 'all');
  const [since, setSince] = useUrlState<LogFilters['since']>('t', '7d');
  const [tagged, setTagged] = useUrlState<string>('tag', '');
  const [acted, setActed] = useUrlState<string>('act', '');
  const [qUrl, setQUrl] = useUrlState<string>('q', '');
  const [qRaw, setQRaw] = useState(qUrl);
  const qDebounced = useDebouncedValue(qRaw, 300);
  useEffect(() => {
    if (qDebounced !== qUrl) setQUrl(qDebounced);
    // chỉ đẩy lên URL khi người dùng gõ xong (nút Xoá bộ lọc tự dọn cả ô gõ)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qDebounced]);

  const filters: LogFilters = useMemo(
    () => ({ channel, groupId, kind, since, tagged: tagged === '1', acted: acted === '1', q: qUrl }),
    [channel, groupId, kind, since, tagged, acted, qUrl],
  );
  const query = useMemo(() => toQuery(filters), [filters]);

  const [live, setLive] = useState(true);
  const qc = useQueryClient();
  // Rời màn thì trả công tắc về bật (lần mở sau không bị kẹt tạm dừng).
  useEffect(() => () => setListenLogLive(true), []);
  // Bật lại sau lúc tạm dừng ⇒ tải bù ngay một lần để tin đến lúc tạm dừng hiện ra.
  const toggleLive = (on: boolean) => {
    setLive(on);
    setListenLogLive(on, qc);
  };

  const list = useListenLog(query);
  const stats = useListenLogStats();
  const items = useMemo(() => list.data?.pages.flatMap((p) => p.items) ?? [], [list.data]);
  const decisionsVisible = list.data?.pages[0]?.decisions_visible ?? true;
  const days = useMemo(() => groupByDay(items, tz), [items, tz]);

  // Dòng mới đến lúc đang xem thì nháy nền một nhịp (như Kho thô).
  const seen = useRef<Set<string> | null>(null);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (!list.data) return;
    const ids = items.map((i) => i.id);
    if (seen.current) {
      const added = ids.filter((id) => !seen.current!.has(id));
      if (added.length && added.length < 10) setFresh(new Set(added));
    }
    seen.current = new Set(ids);
  }, [items, list.data]);
  useEffect(() => {
    seen.current = null;
  }, [query]);

  const channelOptions: FilterOption[] = useMemo(
    () => [{ value: '', label: 'Tất cả' }, ...(stats.data?.by_channel ?? []).map((c) => ({ value: c.type, label: c.name }))],
    [stats.data],
  );
  const groupOptions: FilterOption[] = useMemo(() => {
    const opts: FilterOption[] = [{ value: '', label: 'Tất cả' }];
    for (const g of stats.data?.groups ?? []) {
      if (!channel || g.channel === channel) opts.push({ value: g.id, label: g.name });
    }
    if (groupId && !opts.some((o) => o.value === groupId)) opts.push({ value: groupId, label: 'Nhóm đã chọn' });
    return opts;
  }, [stats.data, channel, groupId]);
  const groupName = groupOptions.find((o) => o.value === groupId)?.label;

  const nFilters = activeFilterCount(filters);
  const patchUrl = useUrlPatch();
  const clearAll = () => {
    patchUrl({ ch: null, g: null, k: null, t: null, tag: null, act: null, q: null });
    setQRaw('');
  };

  const forbidden = list.error instanceof ApiError && list.error.status === 403;

  return (
      <div className="screen ll">
        <ScreenHead
          title={meta.title}
          description={meta.description}
          maxWidth={760}
          actions={
            <>
              <span className="ll-live">
                <Switch checked={live} onChange={toggleLive} label="Cập nhật trực tiếp" />
                <span>{live ? 'Đang cập nhật trực tiếp' : 'Tạm dừng cập nhật'}</span>
              </span>
              <Button
                variant="secondary"
                icon="ph ph-arrow-clockwise"
                loading={list.isRefetching}
                onClick={() => {
                  void list.refetch();
                  void stats.refetch();
                }}
              >
                Làm mới
              </Button>
            </>
          }
        />

        {forbidden ? <NeedPermissionCard error={list.error} /> : null}

        {!forbidden ? (
          <>
            <p className="ll-note" data-testid="ll-not-listening-note">
              <Icon name="ph ph-info" size={14} />
              <span>{NOT_LISTENING_NOTE}</span>
            </p>

            <StatsStrip stats={stats} channel={channel} onPick={(c) => patchUrl({ ch: c === channel ? null : c, g: null })} />

            <div className="ll-filters" role="group" aria-label="Bộ lọc nhật ký">
              <FilterSelect label="Kênh" value={channel} onChange={(v) => patchUrl({ ch: v, g: null })} options={channelOptions} />
              <FilterSelect
                label="Nhóm"
                value={groupId}
                onChange={setGroupId}
                options={groupOptions}
                display={groupId ? (groupName ?? 'Nhóm đã chọn') : stats.data ? `Tất cả ${fmtInt(stats.data.groups_listening)} nhóm` : 'Tất cả'}
              />
              <FilterSelect label="Thời gian" value={since} onChange={setSince} options={SINCE_OPTIONS} />
              <Segmented label="Hiển thị" options={KIND_OPTIONS} value={kind} onChange={setKind} />
              <button type="button" className="toggle-chip" aria-pressed={filters.tagged} onClick={() => setTagged(filters.tagged ? '' : '1')}>
                Chỉ tin được tag
              </button>
              <button type="button" className="toggle-chip" aria-pressed={filters.acted} onClick={() => setActed(filters.acted ? '' : '1')}>
                Chỉ tin agent đã xử lý
              </button>
              <div className="ll-search">
                <TextField label="Tìm chữ" type="search" value={qRaw} onChange={(e) => setQRaw(e.target.value)} placeholder="Nội dung, người gửi, tên nhóm…" />
              </div>
              {nFilters > 0 ? (
                <Button variant="ghost" onClick={clearAll}>
                  Xoá {nFilters} bộ lọc
                </Button>
              ) : null}
            </div>
            {eventsHiddenByFilters(filters) ? (
              <p className="muted-note" role="status">
                Bộ lọc tag / agent đã xử lý chỉ áp cho tin nên các sự kiện của kênh được ẩn.
              </p>
            ) : null}

            {kind !== 'event' ? <AutoSendsBlock since={since} channel={channel} tz={tz} /> : null}

            <section className="ll-card" aria-label="Dòng thời gian" aria-busy={list.isFetching || undefined}>
              {list.isPending ? (
                <SkeletonList />
              ) : list.isError ? (
                <CardError error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />
              ) : items.length === 0 ? (
                <EmptyState
                  icon="ph ph-ear"
                  title={nFilters > 0 ? 'Không có dòng nào khớp bộ lọc' : 'Chưa có gì để hiện'}
                  description={
                    nFilters > 0 ? (
                      'Nới bộ lọc hoặc đổi khoảng thời gian.'
                    ) : (
                      <>
                        Tin của nhóm đang nghe và sự kiện trên kênh sẽ hiện ở đây ngay khi có. Bật nghe cho nhóm ở <Link to="/connections">Kết nối</Link>.
                      </>
                    )
                  }
                />
              ) : (
                <>
                  {days.map((d) => (
                    <div className="ll-day" key={d.key}>
                      <h3 className="ll-day__title">{d.label}</h3>
                      <ol className="ll-items">
                        {d.items.map((it) =>
                          it.kind === 'message' ? (
                            <MessageRow key={it.id} item={it} tz={tz} fresh={fresh.has(it.id)} decisionsVisible={decisionsVisible} />
                          ) : (
                            <EventRow key={it.id} item={it} tz={tz} fresh={fresh.has(it.id)} />
                          ),
                        )}
                      </ol>
                    </div>
                  ))}
                  <div className="ll-more">
                    <span className="raw-count" aria-live="polite">
                      Hiển thị {fmtInt(items.length)} dòng mới nhất
                    </span>
                    {list.hasNextPage ? (
                      <Button variant="secondary" icon="ph ph-arrow-down" loading={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
                        Xem thêm
                      </Button>
                    ) : (
                      <span className="muted-note">Đã hết dòng trong khoảng thời gian này</span>
                    )}
                  </div>
                </>
              )}
            </section>
          </>
        ) : null}
      </div>
  );
}

/** 403 — vai trò chưa có quyền xem tầng dữ liệu: câu thân thiện + "Chi tiết kỹ thuật" (không render object). */
export function NeedPermissionCard({ error }: { error: unknown }) {
  return (
    <section className="ll-card" aria-label="Thiếu quyền">
      <ErrorState
        title="Việc này cần quyền Xem tầng dữ liệu"
        message="Vai trò của bạn chưa được mở quyền xem Nhật ký lắng nghe. Nhờ Owner mở quyền «Xem tầng dữ liệu» ở Cài đặt › Quyền."
        detail={errorDetail(error) ?? errorText(error)}
      />
    </section>
  );
}

function StatsStrip({
  stats,
  channel,
  onPick,
}: {
  stats: ReturnType<typeof useListenLogStats>;
  channel: string;
  onPick: (type: string) => void;
}) {
  if (stats.isPending) {
    return (
      <div className="ll-stats" aria-hidden>
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} width={170} height={58} radius={8} />
        ))}
      </div>
    );
  }
  if (stats.isError) return <CardError error={stats.error} onRetry={() => void stats.refetch()} retrying={stats.isFetching} />;
  const s = stats.data;
  return (
    <div className="ll-stats" role="group" aria-label="Số liệu 24 giờ qua">
      {s.by_channel.map((c) => (
        <button key={c.type} type="button" className="ll-stat" aria-pressed={channel === c.type} onClick={() => onPick(c.type)} title="Bấm để lọc theo kênh này">
          <span className="ll-stat__head">
            <Icon name={channelIcon(c.type)} size={14} color={channelTone(c.type)} />
            {c.name}
          </span>
          <span className="ll-stat__num">{fmtInt(c.messages_24h)}</span>
          <span className="ll-stat__sub">
            tin trong 24 giờ{c.events_24h ? ` · ${fmtInt(c.events_24h)} sự kiện` : ''} · {fmtInt(c.groups_listening)} nhóm nghe
          </span>
        </button>
      ))}
      <div className="ll-stat ll-stat--total" role="group" aria-label="Tổng">
        <span className="ll-stat__head">
          <Icon name="ph ph-users-three" size={14} />
          Đang nghe
        </span>
        <span className="ll-stat__num">{fmtInt(s.groups_listening)}</span>
        <span className="ll-stat__sub">nhóm · {fmtInt(s.messages_24h)} tin · {fmtInt(s.events_24h)} sự kiện / 24 giờ</span>
      </div>
    </div>
  );
}

function SkeletonList() {
  return (
    <div className="ll-items" aria-hidden>
      {Array.from({ length: 6 }, (_, i) => (
        <div className="ll-row" key={i}>
          <Skeleton width={28} height={28} radius={14} />
          <div style={{ flex: 1 }}>
            <Skeleton width="40%" height={10} />
            <Skeleton width="90%" height={10} style={{ marginTop: 8 }} />
            <Skeleton width="55%" height={10} style={{ marginTop: 6 }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function MessageRow({ item, tz, fresh, decisionsVisible }: { item: ListenLogMessage; tz: string; fresh: boolean; decisionsVisible: boolean }) {
  const body = item.text?.trim() ? item.text : `[${KIND_LABEL[item.msg_kind] ?? 'Tin không có chữ'}]`;
  const scr = item.screening;
  return (
    <li className={`ll-row ll-row--msg${fresh ? ' ll-row--new' : ''}`} data-testid="ll-message">
      <span className="ll-rail" title={item.channel.name}>
        <Icon name={channelIcon(item.channel.type)} size={15} color={channelTone(item.channel.type)} label={item.channel.name} />
      </span>
      <div className="ll-main">
        <div className="ll-head">
          <time className="ll-time" dateTime={item.at}>
            {fmtHM(item.at, tz)}
          </time>
          <span className={`ll-where${item.direct ? ' ll-where--dm' : ''}`}>{item.group ? item.group.name : 'Tin riêng'}</span>
          <span className="ll-who">{item.direction === 'outbound' ? 'Phía mình gửi' : senderName(item.sender)}</span>
          {item.tagged ? <span className="ll-tagged">Được tag</span> : null}
          {item.listen_mode_label ? (
            <span
              className="ll-mode"
              title={
                item.listen_mode_known
                  ? `Chế độ nghe lúc tin này vào: ${item.listen_mode_label}`
                  : `Chế độ hiện tại: ${item.listen_mode_label} (tin vào trước khi hệ thống ghi lại chế độ nghe)`
              }
            >
              {item.listen_mode_label}
              {item.listen_mode_known ? '' : ' (hiện tại)'}
            </span>
          ) : null}
        </div>
        <p className={`ll-text clamp2${item.text?.trim() ? '' : ' ll-text--kind'}`}>{body}</p>
        <ul className="ll-trail" aria-label="Hệ thống đã làm gì">
          <li className="ll-step" style={{ ['--step-tone' as string]: screeningTone(scr.state) }} title={scr.note}>
            <span className="ll-step__dot" aria-hidden />
            <span className="ll-step__what">Sàng lọc</span>
            <span>{scr.state === 'discarded' ? scr.note : screeningText(scr.state, scr.label, scr.confidence)}</span>
          </li>
          {decisionsVisible
            ? item.decisions.map((d) => <DecisionStep key={d.id} d={d} />)
            : null}
          {item.note ? (
            <li className="ll-step ll-step--quiet">
              <span className="ll-step__dot" aria-hidden />
              <span>{item.note}</span>
            </li>
          ) : null}
        </ul>
      </div>
    </li>
  );
}

function DecisionStep({ d }: { d: ListenLogDecision }) {
  return (
    <li className="ll-step" style={{ ['--step-tone' as string]: decisionTone(d) }}>
      <span className="ll-step__dot" aria-hidden />
      <span className="ll-step__what">{d.agent.name}</span>
      <span>{d.label}</span>
      {d.draft ? (
        <Link className="ll-draft" to={draftHref(d.draft.id)} title="Mở bản nháp ở Bàn làm việc">
          {d.draft.code}
          <Icon name="ph ph-arrow-right" size={11} />
        </Link>
      ) : null}
    </li>
  );
}

function EventRow({ item, tz, fresh }: { item: ListenLogEvent; tz: string; fresh: boolean }) {
  const tone = eventTone(item.tone);
  return (
    <li className={`ll-row ll-row--event${fresh ? ' ll-row--new' : ''}`} data-testid="ll-event" data-event-type={item.event_type}>
      <span className="ll-rail ll-rail--event" style={{ ['--event-tone' as string]: tone }}>
        <Icon name={item.icon} size={15} color={tone} />
      </span>
      <div className="ll-main">
        <div className="ll-head">
          <time className="ll-time" dateTime={item.at}>
            {fmtHM(item.at, tz)}
          </time>
          <span className="ll-event-kind">{eventKindLabel(String(item.event_type))}</span>
          {item.channel ? <span className="ll-who">{item.channel.name}</span> : null}
        </div>
        <p className="ll-event-text">{item.text}</p>
      </div>
    </li>
  );
}

/**
 * v0.1.72: «Bot đã tự trả lời» — sổ tin bot tự gửi (`GET /auto-sends`, CHỈ Owner; không có nội dung tin) kèm nhãn trường
 * hợp: Kênh quản trị / Sếp gọi / Hỗ trợ. Ẩn khi không có dòng nào, khi máy chủ cũ chưa có API (404) hoặc không phải Owner.
 */
function AutoSendsBlock({ since, channel, tz }: { since: Since; channel: string; tz: string }) {
  const isOwner = useIsOwnerRole();
  const q = useQuery({
    queryKey: autoSendsKey(since),
    queryFn: ({ signal }) => api.ownerChannel.autoSends({ since, limit: 50 }, signal),
    enabled: isOwner,
  });
  if (!isOwner || q.isPending) return null;
  if (q.isError) {
    if (q.error instanceof ApiError && (q.error.status === 404 || q.error.status === 403)) return null;
    return <InlineError detail={errorDetail(q.error)}>{`Chưa tải được danh sách tin bot tự gửi. ${errorText(q.error)}`}</InlineError>;
  }
  const items = autoSendsForChannel(Array.isArray(q.data?.items) ? q.data.items : [], channel);
  if (items.length === 0) return null;
  return (
    <section className="ll-card ll-auto" aria-label="Bot đã tự trả lời" data-testid="ll-autosends">
      <h3 className="ll-day__title">
        <Icon name="ph ph-robot" size={13} /> Bot đã tự trả lời · {fmtInt(items.length)} tin
      </h3>
      <p className="muted-note">Chỉ ghi lúc gửi, trường hợp và độ dài — không lưu nội dung tin ở đây. Bot chỉ tự gửi khi kênh chạy bằng tài khoản riêng của bot.</p>
      <ol className="ll-items">
        {items.map((a) => (
          <AutoSendRow key={a.id} a={a} tz={tz} />
        ))}
      </ol>
    </section>
  );
}

function AutoSendRow({ a, tz }: { a: AutoSendItem; tz: string }) {
  const bad = a.status === 'failed' || a.status === 'expired';
  return (
    <li className="ll-row ll-row--event" data-testid="ll-autosend" data-basis={a.basis}>
      <span className="ll-rail ll-rail--event" style={{ ['--event-tone' as string]: bad ? 'var(--color-bad)' : 'var(--color-accent-300)' }}>
        <Icon name="ph ph-robot" size={15} color={bad ? 'var(--color-bad)' : 'var(--color-accent-300)'} />
      </span>
      <div className="ll-main">
        <div className="ll-head">
          <time className="ll-time" dateTime={a.at} title={fmtDMClock(a.at, tz)}>
            {fmtHM(a.at, tz)}
          </time>
          <span className="ll-event-kind" data-testid="ll-autosend-basis">
            {basisLabel(String(a.basis), typeof a.basis_label === 'string' ? a.basis_label : null)}
          </span>
          <span className="ll-who">{channelName(String(a.channel))}</span>
        </div>
        <p className="ll-event-text">
          {`Bot đã tự trả lời ${a.group?.name ? `trong nhóm ${a.group.name}` : 'Sếp (tin riêng)'} · ${autoStatusLabel(String(a.status))}${
            typeof a.body_len === 'number' ? ` · ${fmtInt(a.body_len)} ký tự` : ''
          }`}
        </p>
      </div>
    </li>
  );
}

export type { ListenLogItem };
