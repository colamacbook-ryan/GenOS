import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { SCREEN_BY_KEY, autonomyChoice, autonomyPatch } from '@gen-harness/contracts';
import { ApiError } from '@gen-harness/contracts';
import type { AgentRef, DirChannel, DirGroup, DirHeatBand, DirPeopleFilter, DirPerson, DirPriority, DirRelation, DirValueBand } from '@gen-harness/contracts';
import { Bar, CardError, InlineError, ScreenHead, SkeletonLines } from '../common';
import { Button, Dialog, EmptyState, Icon, Tabs, TextField, type FilterOption, type TabItem } from '@gen-harness/ui';
import { errorDetail, errorText } from '../../lib/errorText';
import { fmtInt } from '../../lib/format';
import { useCan } from '../../lib/permissions';
import { useIsOwnerRole } from '../../owner/queries';
import { useAgentOptions, useEmptyAgentsText } from '../../lib/pickers';
import { toast } from '../../lib/toast';
import { useDebouncedValue } from '../../lib/useDebouncedValue';
import { useUrlState } from '../../lib/uiStore';
import { AutonomySelect } from '../AutonomySelect';
import { addedByLabel, isOwnerChannel } from '../connections/ownerChannelModel';
import { GroupSupportDialog } from './GroupSupportDialog';
import { DataEmptyState } from '../DataEmptyState';
import {
  CHANNEL_LABEL,
  CHANNEL_STATE_LABEL,
  GROUP_KIND_LABEL,
  HEAT_FILTER_LABEL,
  LISTEN_MODE_LABEL,
  PRIORITY_LABEL,
  RELATION_LABEL,
  VALUE_FILTER_LABEL,
  channelIcon,
  channelTone,
  fmtVnd,
  heatTone,
  initialsOf,
  listenModeTone,
  priorityTone,
} from './relationsModel';
import { useBulkSetPersonBot, useDirChannels, useDirGroupsPaged, useDirPeoplePaged, useSetGroupBot, useSetPersonBot } from './queries';

/** Giá trị "giữ nguyên BOT hiện tại" của hộp gán hàng loạt — không gửi `agent_id` (API giữ nguyên). */
const KEEP_BOT = '__keep__';

/** Danh sách chọn trợ lý (agent THẬT từ `/pickers/agents`, v0.1.35) dùng chung cho 3 hộp gán BOT. BOT hiện tại đã
 * bị tắt (không còn trong danh sách) vẫn hiện một dòng "<tên> (đã tắt)" được đánh dấu chọn. `keepLabel` thêm lựa
 * chọn "giữ nguyên" (giá trị KEEP_BOT) đứng đầu — dùng cho hộp hàng loạt để mặc định không gỡ BOT của ai. */
function AgentChoices({
  value,
  onChange,
  current,
  noneLabel = 'Chưa gán',
  keepLabel,
}: {
  value: string;
  onChange: (id: string) => void;
  current?: AgentRef | null;
  noneLabel?: string;
  keepLabel?: string;
}) {
  const { options, query } = useAgentOptions();
  const emptyText = useEmptyAgentsText();
  const missing = current && query.isSuccess && !options.some((a) => a.id === current.id) ? current : null;
  return (
    <div className="dlg-list" role="list">
      {keepLabel ? (
        <button type="button" className="sv-open" aria-pressed={value === KEEP_BOT} onClick={() => onChange(KEEP_BOT)}>
          <span className="sv-open__name">{keepLabel}</span>
          {value === KEEP_BOT ? <Icon name="ph ph-check" size={14} /> : null}
        </button>
      ) : null}
      <button type="button" className="sv-open" aria-pressed={value === ''} onClick={() => onChange('')}>
        <span className="sv-open__name">{noneLabel}</span>
        {value === '' ? <Icon name="ph ph-check" size={14} /> : null}
      </button>
      {query.isPending ? (
        <SkeletonLines rows={3} />
      ) : query.isError ? (
        <InlineError detail={errorDetail(query.error)}>{errorText(query.error)}</InlineError>
      ) : (
        <>
          {missing ? (
            <button type="button" className="sv-open" aria-pressed={value === missing.id} onClick={() => onChange(missing.id)}>
              <span className="sv-open__name">{`${missing.name || 'Trợ lý'} (đã tắt)`}</span>
              {value === missing.id ? <Icon name="ph ph-check" size={14} /> : null}
            </button>
          ) : null}
          {options.map((a) => (
            <button key={a.id} type="button" className="sv-open" aria-pressed={value === a.id} onClick={() => onChange(a.id)}>
              <span className="sv-open__name">{a.label}</span>
              {value === a.id ? <Icon name="ph ph-check" size={14} /> : null}
            </button>
          ))}
          {options.length === 0 ? <p className="muted-note">{emptyText}</p> : null}
        </>
      )}
    </div>
  );
}

type DirTab = 'groups' | 'people';

export function DirectoryScreen() {
  const [tab, setTab] = useUrlState<DirTab>('dt', 'groups');
  const items: TabItem<DirTab>[] = [
    { key: 'groups', label: 'Nhóm theo kênh' },
    { key: 'people', label: 'Con người' },
  ];
  return (
    <div className="screen">
      <ScreenHead
        title={SCREEN_BY_KEY.directory.title}
        description="Danh sách nhóm tách theo từng kênh, và danh sách con người lọc được theo mức liên quan với Sếp, độ nhiệt, giá trị và mức ưu tiên — từ đó gán agent trực tương ứng."
        maxWidth={760}
        actions={<Tabs items={items} value={tab} onChange={setTab} label="Tab nhóm & con người" idPrefix="dir-tab" />}
      />
      {tab === 'groups' ? <GroupsPane /> : <PeoplePane />}
    </div>
  );
}

function GroupsPane() {
  const channels = useDirChannels();
  const [qRaw, setQRaw] = useUrlState<string>('gq', '');
  const q = useDebouncedValue(qRaw.trim(), 300);
  const [botFor, setBotFor] = useState<DirGroup | null>(null);
  const [supportFor, setSupportFor] = useState<DirGroup | null>(null);
  // v0.1.72: Chế độ hỗ trợ chỉ Owner (API require_owner), chỉ nhóm Zalo/WhatsApp.
  const isOwner = useIsOwnerRole();
  // Gán BOT cần profile.write — vai trò chỉ đọc (Auditor) không thấy nút "Đổi" (mở ra chỉ gặp 403).
  const canWrite = useCan('profile.write');

  if (channels.isPending) return <SkeletonLines rows={6} />;
  if (channels.isError) return <CardError error={channels.error} onRetry={() => void channels.refetch()} retrying={channels.isFetching} />;
  if (channels.data.length === 0) return <DataEmptyState fallback={<EmptyState icon="ph ph-broadcast" title="Chưa có kênh nào kết nối" />} />;

  return (
    <div className="dir-groups">
      <div className="dir-search">
        <TextField
          label="Tìm nhóm (tên hoặc mã)"
          type="search"
          value={qRaw}
          onChange={(e) => setQRaw(e.target.value)}
          placeholder="Đại lý miền Trung, GRP-ZL-0114…"
        />
      </div>
      {channels.data.map((ch) => (
        <ChannelGroupsCard
          key={ch.id}
          ch={ch}
          q={q}
          canWrite={canWrite}
          onPickBot={setBotFor}
          onSupport={isOwner && isOwnerChannel(ch.type) ? setSupportFor : undefined}
        />
      ))}
      {botFor ? <GroupBotDialog group={botFor} onClose={() => setBotFor(null)} /> : null}
      {supportFor ? <GroupSupportDialog groupId={supportFor.id} groupName={supportFor.name} onClose={() => setSupportFor(null)} /> : null}
    </div>
  );
}

/** Nhóm của MỘT kênh: máy chủ lọc theo kênh + tên, trả từng trang; "Xem thêm" lấy trang kế (không giới hạn ở 50 nhóm đầu). */
function ChannelGroupsCard({
  ch,
  q,
  canWrite,
  onPickBot,
  onSupport,
}: {
  ch: DirChannel;
  q: string;
  canWrite: boolean;
  onPickBot: (g: DirGroup) => void;
  /** v0.1.72: có ⇒ thêm cột «Chế độ hỗ trợ» (Owner, Zalo/WhatsApp). */
  onSupport?: (g: DirGroup) => void;
}) {
  const groups = useDirGroupsPaged({ channel_id: ch.id, q: q || undefined });
  const rows = groups.data?.pages.flatMap((pg) => pg.items) ?? [];
  const total = groups.data?.pages[0]?.total ?? 0;
  const label = CHANNEL_LABEL[ch.type] ?? ch.type;
  return (
    <section className="gh-card" aria-label={label}>
      <div className="gh-card__header">
        <div className="dir-ch-head">
          <span className="dir-ch-head__icon" style={{ color: channelTone(ch.type) }}>
            <Icon name={channelIcon(ch.type)} size={15} />
          </span>
          <div>
            <div className="gh-card__title">{label}</div>
            <div className="gh-card__kicker">
              {q && groups.data ? `${fmtInt(total)} nhóm khớp tìm kiếm` : `${fmtInt(ch.group_count)} nhóm`} · {fmtInt(ch.events_24h)} tin trong 24 giờ
            </div>
          </div>
          {ch.state ? (
            <span className="state-chip" style={{ color: channelTone(ch.type), borderColor: channelTone(ch.type) }}>
              <span className="state-chip__dot" style={{ background: channelTone(ch.type) }} />
              {CHANNEL_STATE_LABEL[ch.state] ?? ch.state}
            </span>
          ) : null}
        </div>
      </div>
      {groups.isPending ? (
        <SkeletonLines rows={3} />
      ) : groups.isError && !groups.data ? (
        <CardError error={groups.error} onRetry={() => void groups.refetch()} retrying={groups.isFetching} />
      ) : rows.length === 0 ? (
        <EmptyState icon="ph ph-users-three" title={q ? 'Không có nhóm nào khớp tìm kiếm' : 'Chưa có nhóm nào trên kênh này'} />
      ) : (
        <>
          <div className="gh-table-scroll">
            <table className="gh-table w920" aria-label={`Nhóm kênh ${label}`}>
              <thead>
                <tr>
                  <th style={{ width: 118 }}>ID nhóm</th>
                  <th>Tên nhóm</th>
                  <th style={{ width: 96 }}>Loại</th>
                  <th style={{ width: 90 }}>Thành viên</th>
                  <th style={{ width: 80 }}>Tin 24h</th>
                  <th style={{ width: 110 }}>Độ nhiệt</th>
                  <th style={{ width: 150 }}>Chế độ nghe</th>
                  <th style={{ width: 170 }}>Agent trực</th>
                  <th style={{ width: 110 }}>Thêm bởi</th>
                  {onSupport ? <th style={{ width: 120 }}>Chế độ hỗ trợ</th> : null}
                </tr>
              </thead>
              <tbody>
                {rows.map((g) => (
                  <tr key={g.id}>
                    <td className="td-id">{g.code}</td>
                    <td className="dir-grp-name">{g.name}</td>
                    <td>
                      <span className="mono-tag">{GROUP_KIND_LABEL[g.kind] ?? g.kind}</span>
                    </td>
                    <td className="td-id">{fmtInt(g.member_count)}</td>
                    <td className="td-id">{fmtInt(g.events_24h)}</td>
                    <td>
                      {g.heat !== null ? <Bar pct={g.heat} tone={heatTone(g.heat)} width={64} /> : <span className="td-id">—</span>}
                      {g.heat !== null ? <span style={{ marginLeft: 8, color: heatTone(g.heat), fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>{fmtInt(g.heat)}</span> : null}
                    </td>
                    <td>
                      <span className="mono-tag" style={{ color: listenModeTone(g.listen_mode) }}>
                        {LISTEN_MODE_LABEL[g.listen_mode]}
                      </span>
                    </td>
                    <td>
                      <div className="dir-bot-cell">
                        <span className="dir-bot-cell__name">{g.bot ? g.bot.name : 'Chưa gán'}</span>
                        {canWrite ? (
                          <Button variant="ghost" size="sm" onClick={() => onPickBot(g)}>
                            Đổi
                          </Button>
                        ) : null}
                      </div>
                    </td>
                    <td data-testid="dir-added-by" title={g.added_by_kind ? undefined : 'Máy chủ chưa gửi thông tin ai thêm bot'}>
                      {addedByLabel(g.added_by_kind)}
                    </td>
                    {onSupport ? (
                      <td>
                        <Button variant="ghost" size="sm" icon="ph ph-lifebuoy" aria-label={`Chế độ hỗ trợ của ${g.name}`} onClick={() => onSupport(g)}>
                          Hỗ trợ
                        </Button>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <MoreRow shown={rows.length} total={total} unit="nhóm" hasMore={Boolean(groups.hasNextPage)} loading={groups.isFetchingNextPage} error={groups.isFetchNextPageError ? groups.error : null} onMore={() => void groups.fetchNextPage()} />
        </>
      )}
    </section>
  );
}

/** Chân bảng: "Đang hiện X / Y" + nút Xem thêm (lỗi tải trang kế hiện ngay dưới, bấm lại để thử lại). */
function MoreRow({ shown, total, unit, hasMore, loading, error, onMore }: { shown: number; total: number; unit: string; hasMore: boolean; loading: boolean; error: unknown; onMore: () => void }) {
  return (
    <div className="dir-more">
      <span className="raw-count">
        Đang hiện {fmtInt(shown)} / {fmtInt(total)} {unit}
      </span>
      {hasMore ? (
        <Button variant="secondary" className="btn-27" loading={loading} onClick={onMore}>
          Xem thêm
        </Button>
      ) : null}
      {error ? <InlineError detail={errorDetail(error)}>{errorText(error)}</InlineError> : null}
    </div>
  );
}

function GroupBotDialog({ group, onClose }: { group: DirGroup; onClose: () => void }) {
  const setBot = useSetGroupBot();
  const [agentId, setAgentId] = useState(group.bot?.id ?? '');
  return (
    <Dialog
      open
      onClose={onClose}
      width={400}
      title="Gán BOT trực nhóm"
      kicker={group.name}
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button
            variant="primary"
            icon="ph ph-robot"
            loading={setBot.isPending}
            onClick={() => setBot.mutate({ id: group.id, body: { agent_id: agentId || null } }, { onSuccess: onClose })}
          >
            Lưu
          </Button>
        </>
      }
    >
      <AgentChoices value={agentId} onChange={setAgentId} current={group.bot} />
      {group.listen_mode === 'silent' && agentId ? (
        <p className="risk-box__text" role="note" data-testid="group-bot-silent-warning">
          <b>BOT sẽ không trả lời trong nhóm Lắng nghe im lặng</b> — nhóm này chỉ được lưu và sàng lọc tin, không đưa cho agent trực, kể cả khi
          được tag. Muốn BOT trả lời khi được tag, đặt nhóm sang «Nghe hết, chỉ trả lời khi được tag».
        </p>
      ) : null}
      {setBot.isError ? <InlineError detail={errorDetail(setBot.error)}>{errorText(setBot.error)}</InlineError> : null}
    </Dialog>
  );
}

const REL_OPTIONS: FilterOption<string>[] = [
  { value: '', label: 'Tất cả' },
  { value: 'direct', label: RELATION_LABEL.direct },
  { value: 'via_staff', label: RELATION_LABEL.via_staff },
  { value: 'stranger', label: RELATION_LABEL.stranger },
];
const HEAT_OPTIONS: FilterOption<string>[] = [{ value: '', label: 'Tất cả' }, ...(['high', 'mid', 'cold'] as DirHeatBand[]).map((v) => ({ value: v, label: HEAT_FILTER_LABEL[v] }))];
const VALUE_OPTIONS: FilterOption<string>[] = [{ value: '', label: 'Tất cả' }, ...(['high', 'mid', 'unknown'] as DirValueBand[]).map((v) => ({ value: v, label: VALUE_FILTER_LABEL[v] }))];
const PRIORITY_OPTIONS: FilterOption<string>[] = [{ value: '', label: 'Tất cả' }, ...(['P1', 'P2', 'P3'] as DirPriority[]).map((v) => ({ value: v, label: PRIORITY_LABEL[v] }))];
const BOT_OPTIONS: FilterOption<string>[] = [
  { value: '', label: 'Tất cả' },
  { value: 'assigned', label: 'Đã gán' },
  { value: 'unassigned', label: 'Chưa gán' },
];

function PeoplePane() {
  const channels = useDirChannels();
  const [qRaw, setQRaw] = useUrlState<string>('pq', '');
  const q = useDebouncedValue(qRaw.trim(), 300);
  const [channel, setChannel] = useUrlState<string>('chn', '');
  const [relation, setRelation] = useUrlState<string>('rel', '');
  const [heat, setHeat] = useUrlState<string>('heat', '');
  const [value, setValue] = useUrlState<string>('val', '');
  const [priority, setPriority] = useUrlState<string>('pri', '');
  const [bot, setBot] = useUrlState<string>('bot', '');
  const query = useMemo(
    () => ({
      relation: (relation || undefined) as DirRelation | undefined,
      heat: (heat || undefined) as DirHeatBand | undefined,
      value: (value || undefined) as DirValueBand | undefined,
      priority: (priority || undefined) as DirPriority | undefined,
      bot: (bot || undefined) as 'assigned' | 'unassigned' | undefined,
      channel: channel || undefined,
      q: q || undefined,
    }),
    [relation, heat, value, priority, bot, channel, q],
  );
  const people = useDirPeoplePaged(query);
  const rows = useMemo(() => people.data?.pages.flatMap((pg) => pg.items) ?? [], [people.data]);
  // Tổng do MÁY CHỦ đếm theo bộ lọc (trang đầu) — không phải số dòng đã tải.
  const total = people.data?.pages[0]?.total ?? 0;
  const channelOptions = useMemo<FilterOption<string>[]>(
    () => [{ value: '', label: 'Tất cả' }, ...(channels.data ?? []).map((c) => ({ value: c.type, label: CHANNEL_LABEL[c.type] ?? c.type }))],
    [channels.data],
  );
  const [botFor, setBotFor] = useState<DirPerson | null>(null);
  const [bulk, setBulk] = useState(false);
  // Gán/đổi BOT cho NGƯỜI chỉ Owner (+ mã PIN): máy chủ từ chối vai khác (review v0.1.65 P2-3), nên không hiện nút.
  const canProfileWrite = useCan('profile.write');
  const isOwner = useIsOwnerRole();
  const canBot = canProfileWrite && isOwner;

  return (
    <div className="dir-people">
      <div className="dir-filters">
        <FilterRow label="Liên quan Sếp" value={relation} onChange={setRelation} options={REL_OPTIONS} />
        <FilterRow label="Độ nhiệt" value={heat} onChange={setHeat} options={HEAT_OPTIONS} />
        <FilterRow label="Giá trị" value={value} onChange={setValue} options={VALUE_OPTIONS} />
        <FilterRow label="Ưu tiên" value={priority} onChange={setPriority} options={PRIORITY_OPTIONS} />
        <FilterRow label="BOT" value={bot} onChange={setBot} options={BOT_OPTIONS} />
        {channelOptions.length > 1 ? <FilterRow label="Kênh" value={channel} onChange={setChannel} options={channelOptions} /> : null}
        <div className="dir-filter-row dir-filter-row--search">
          <TextField label="Tìm theo tên hoặc mã" type="search" value={qRaw} onChange={(e) => setQRaw(e.target.value)} placeholder="Nguyễn Văn Bảo, PER-0042…" />
        </div>
        <span className="dir-filters__spacer" />
        <div className="dir-filters__actions">
          {people.data ? <span className="raw-count">{fmtInt(total)} người khớp</span> : null}
          {canBot ? (
            // Đang hiện dữ liệu cũ của bộ lọc trước (placeholder) ⇒ số "khớp" chưa phải của bộ lọc này ⇒ chưa cho áp dụng.
            <Button variant="primary" icon="ph ph-robot" disabled={!total || people.isPlaceholderData} onClick={() => setBulk(true)}>
              Thiết lập BOT cho nhóm đã lọc
            </Button>
          ) : null}
        </div>
      </div>

      <div className="table-card">
        {people.isPending ? (
          <SkeletonLines rows={6} />
        ) : people.isError && !people.data ? (
          <CardError error={people.error} onRetry={() => void people.refetch()} retrying={people.isFetching} />
        ) : rows.length === 0 ? (
          <DataEmptyState
            filtered={Boolean(relation || heat || value || priority || bot || channel || q)}
            fallback={<EmptyState icon="ph ph-address-book" title="Không có ai khớp bộ lọc" />}
          />
        ) : (
          <>
          <div className="gh-table-scroll">
            <table className="gh-table w920" aria-label="Con người">
              <thead>
                <tr>
                  <th style={{ width: 100 }}>ID người</th>
                  <th>Con người</th>
                  <th style={{ width: 64 }}>Kênh</th>
                  <th style={{ width: 150 }}>Liên quan Sếp</th>
                  <th style={{ width: 120 }}>Độ nhiệt</th>
                  <th style={{ width: 120 }}>Giá trị</th>
                  <th style={{ width: 80 }}>Ưu tiên</th>
                  <th style={{ width: 190 }}>BOT phụ trách</th>
                  <th style={{ width: 90 }}>Tự trị</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.id}>
                    <td className="td-id">{p.code}</td>
                    <td>
                      <div className="dir-person-cell">
                        <span className="dir-person-cell__av">{initialsOf(p.name)}</span>
                        <div style={{ minWidth: 0 }}>
                          <Link to={`/profile?id=${encodeURIComponent(p.id)}`} className="dir-person-cell__name">
                            {p.name}
                          </Link>
                          {p.org_name ? <div className="dir-person-cell__org">{p.org_name}</div> : null}
                        </div>
                      </div>
                    </td>
                    <td>
                      <span className="dir-ch-icons">
                        {p.channels.map((c) => (
                          <Icon key={c} name={channelIcon(c)} size={13} color={channelTone(c)} label={CHANNEL_LABEL[c] ?? c} />
                        ))}
                      </span>
                    </td>
                    <td>
                      <span className="mono-tag">{RELATION_LABEL[p.relation]}</span>
                    </td>
                    <td>
                      {p.heat !== null ? (
                        <>
                          <Bar pct={p.heat} tone={heatTone(p.heat)} width={56} />
                          <span style={{ marginLeft: 8, color: heatTone(p.heat), fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>{fmtInt(p.heat)}</span>
                        </>
                      ) : (
                        <span className="td-id">—</span>
                      )}
                    </td>
                    <td className="td-id">{fmtVnd(p.value_vnd)}</td>
                    <td>
                      <span style={{ color: priorityTone(p.priority), fontFamily: 'var(--font-mono)', fontSize: 11 }}>{p.priority}</span>
                    </td>
                    <td>
                      <div className="dir-bot-cell">
                        <span className="dir-bot-cell__name">{p.bot ? p.bot.name : 'Chưa gán'}</span>
                        {canBot ? (
                          <Button variant="ghost" size="sm" onClick={() => setBotFor(p)}>
                            Đổi
                          </Button>
                        ) : null}
                      </div>
                    </td>
                    <td className="td-id">{autonomyChoice(p.autonomy_level)?.label ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <MoreRow shown={rows.length} total={total} unit="người" hasMore={Boolean(people.hasNextPage)} loading={people.isFetchingNextPage} error={people.isFetchNextPageError ? people.error : null} onMore={() => void people.fetchNextPage()} />
          </>
        )}
      </div>

      {botFor ? <PersonBotDialog person={botFor} onClose={() => setBotFor(null)} /> : null}
      {bulk ? <BulkBotDialog filter={query} total={total} onClose={() => setBulk(false)} onStale={() => void people.refetch()} /> : null}
    </div>
  );
}

function FilterRow({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: FilterOption<string>[] }) {
  return (
    <div className="dir-filter-row">
      <span className="dir-filter-row__label">{label}</span>
      <div className="dir-filter-row__opts" role="group" aria-label={label}>
        {options.map((o) => (
          <button key={o.value} type="button" className="dir-filter-row__opt" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function PersonBotDialog({ person, onClose }: { person: DirPerson; onClose: () => void }) {
  const setBot = useSetPersonBot();
  const [agentId, setAgentId] = useState(person.bot?.id ?? '');
  // F-30: `null` = giữ nguyên mức đang lưu — Lưu không gửi lại autonomy_level nếu Sếp không chọn mức khác.
  const [picked, setPicked] = useState<number | null>(null);
  return (
    <Dialog
      open
      onClose={onClose}
      width={400}
      title="Thiết lập BOT + tự trị"
      kicker={person.name}
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button
            variant="primary"
            icon="ph ph-robot"
            loading={setBot.isPending}
            onClick={() =>
              setBot.mutate(
                { id: person.id, body: { agent_id: agentId || null, ...autonomyPatch(person.autonomy_level, picked) } },
                { onSuccess: onClose },
              )
            }
          >
            Lưu
          </Button>
        </>
      }
    >
      <div className="dlg-fields">
        <p className="risk-box__text" data-testid="person-bot-hint">
          BOT được gán sẽ phụ trách tin nhắn riêng (1-1) của người này: trợ lý được kích hoạt để soạn trả lời (vẫn qua duyệt theo mức tự trị),
          kể cả khi kênh đang ở chế độ “Lưu im lặng”. Muốn trợ lý thôi phụ trách, chọn “Chưa gán”.
        </p>
        <AgentChoices value={agentId} onChange={setAgentId} current={person.bot} />
        <AutonomySelect current={person.autonomy_level} value={picked} onChange={setPicked} />
        {setBot.isError ? <InlineError detail={errorDetail(setBot.error)}>{errorText(setBot.error)}</InlineError> : null}
      </div>
    </Dialog>
  );
}

function BulkBotDialog({ filter, total, onClose, onStale }: { filter: DirPeopleFilter; total: number; onClose: () => void; onStale: () => void }) {
  const bulk = useBulkSetPersonBot();
  // Mặc định GIỮ NGUYÊN BOT: người chỉ muốn đổi mức tự trị không vô tình gỡ BOT của cả nhóm đã lọc.
  const [agentId, setAgentId] = useState(KEEP_BOT);
  const [picked, setPicked] = useState<number | null>(null);
  const keepBot = agentId === KEEP_BOT;
  const nothingToApply = keepBot && picked === null;
  // Một lần gọi: máy chủ tự tìm MỌI người khớp bộ lọc (không chỉ trang đã tải) và chỉ áp khi số vẫn đúng `total`.
  const apply = () =>
    bulk.mutate(
      { filter, expected_total: total, ...(keepBot ? {} : { agent_id: agentId || null }), ...autonomyPatch(null, picked) },
      {
        onSuccess: (r) => {
          toast(`Đã cập nhật ${fmtInt(r.updated)} người`, 'ok');
          onClose();
        },
        // Số người khớp đã đổi ⇒ tải lại để Sếp thấy số mới trước khi quyết lại.
        onError: (e) => {
          if (e instanceof ApiError && e.status === 409) onStale();
        },
      },
    );
  return (
    <Dialog
      open
      onClose={onClose}
      width={400}
      title="Thiết lập BOT cho nhóm đã lọc"
      kicker={`${fmtInt(total)} người đang khớp bộ lọc hiện tại`}
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" icon="ph ph-robot" loading={bulk.isPending} disabled={!total || nothingToApply} onClick={apply}>
            Áp dụng cho {fmtInt(total)} người
          </Button>
        </>
      }
    >
      <div className="dlg-fields">
        <AgentChoices value={agentId} onChange={setAgentId} noneLabel="Chưa gán (gỡ BOT)" keepLabel="Giữ nguyên BOT hiện tại" />
        <AutonomySelect current={null} value={picked} onChange={setPicked} keepLabel="Giữ nguyên" />
        {bulk.isError ? <InlineError detail={errorDetail(bulk.error)}>{errorText(bulk.error)}</InlineError> : null}
      </div>
    </Dialog>
  );
}
