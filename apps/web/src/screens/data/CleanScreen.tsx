import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  ApiError,
  SCREEN_BY_KEY,
  eventTypeLabel,
  type CleanItem,
  type CleanQuery,
  type NotebookSection,
  type NotebookSubjectType,
} from '@gen-harness/contracts';
import { Button, Dialog, EmptyState, Icon, SelectField, Skeleton } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { qk2, useCleanList } from '../../lib/dataQueries';
import { fmtDMClock, fmtHM, fmtInt } from '../../lib/format';
import { useCan, useOrgTimezone } from '../../lib/permissions';
import { queryClient } from '../../lib/queryClient';
import { toast } from '../../lib/toast';
import { useUrlState } from '../../lib/uiStore';
import { errorText } from '../../lib/errorText';
import { CardError, InlineError, Panel, ScreenHead, SkeletonLines } from '../common';
import { ACC3, N4, N5, N8, channelIcon, channelTone, cleanTone, paramTone, sectionAge, sectionTone, subjectOf } from './dataModel';
import { PipelineStrip } from './PipelineStrip';

export function CleanScreen() {
  const meta = SCREEN_BY_KEY.clean;
  const tz = useOrgTimezone();
  const [sel, setSel] = useUrlState<string>('sel', '');
  const [byGroup, setByGroup] = useUrlState<string>('fg', '');
  const [byPerson, setByPerson] = useUrlState<string>('fp', '');
  const [week, setWeek] = useUrlState<string>('t', '');

  const q: CleanQuery = useMemo(
    () => ({ group_id: byGroup || undefined, person_id: byPerson || undefined, since: week ? '7d' : undefined }),
    [byGroup, byPerson, week],
  );
  const list = useCleanList(q);
  const rows = useMemo(() => list.data?.pages.flatMap((p) => p.items) ?? [], [list.data]);
  const total = list.data?.pages[0]?.total ?? 0;

  // Keep the last selected row around even when a filter hides it.
  const lastSel = useRef<CleanItem | null>(null);
  const selected = rows.find((r) => r.id === sel) ?? (sel && lastSel.current?.id === sel ? lastSel.current : null) ?? rows[0] ?? null;
  if (selected) lastSel.current = selected;

  const [evidenceFor, setEvidenceFor] = useState<CleanItem | null>(null);

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

  return (
    <>
      <PipelineStrip screen="clean" />
      <div className="screen">
        <ScreenHead title={meta.title} description={meta.description} maxWidth={760} />
        <div className="split-380">
          <section className="table-card" aria-label="Bản ghi sạch theo ID">
            <div className="gh-card__header">
              <div style={{ minWidth: 0 }}>
                <div className="gh-card__title">Bản ghi sạch theo ID</div>
                <div className="gh-card__kicker">
                  {list.data ? `${fmtInt(total)} bản ghi` : '… bản ghi'} · bấm một dòng để xem trí nhớ tạm
                </div>
              </div>
              <div className="card-chips" role="group" aria-label="Lọc bản ghi sạch">
                <button
                  type="button"
                  className="toggle-chip"
                  aria-pressed={!!byGroup}
                  disabled={!byGroup && !selected?.group}
                  title={selected?.group ? `Chỉ nhóm ${selected.group.code}` : undefined}
                  onClick={() => setByGroup(byGroup ? '' : (selected?.group?.id ?? ''))}
                >
                  Theo nhóm
                </button>
                <button
                  type="button"
                  className="toggle-chip"
                  aria-pressed={!!byPerson}
                  disabled={!byPerson && !selected?.person}
                  title={selected?.person ? `Chỉ người ${selected.person.code}` : undefined}
                  onClick={() => setByPerson(byPerson ? '' : (selected?.person?.id ?? ''))}
                >
                  Theo người
                </button>
                <button type="button" className="toggle-chip" aria-pressed={!!week} onClick={() => setWeek(week ? '' : '1')}>
                  7 ngày
                </button>
              </div>
            </div>
            <div className="gh-table-scroll">
              <table className="gh-table w920" aria-label="Bản ghi sạch" aria-busy={list.isFetching || undefined}>
                <thead>
                  <tr>
                    <th style={{ width: 106 }}>ID nhóm</th>
                    <th style={{ width: 100 }}>ID người</th>
                    <th style={{ width: 136 }}>Sự kiện</th>
                    <th style={{ minWidth: 280 }}>Kết luận đã chốt</th>
                    <th style={{ width: 70 }}>Điểm</th>
                    <th style={{ width: 84 }}>Chu kỳ</th>
                    <th style={{ width: 80 }}>Thô</th>
                  </tr>
                </thead>
                <tbody>
                  {list.isPending
                    ? Array.from({ length: 8 }, (_, i) => <SkeletonRow key={i} />)
                    : rows.map((r) => {
                        const tone = cleanTone(r.event_type, r.score);
                        const active = selected?.id === r.id;
                        return (
                          <tr
                            key={r.id}
                            className={active ? 'row-click row-active' : 'row-click'}
                            aria-selected={active}
                            tabIndex={0}
                            onClick={() => setSel(r.id)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' || e.key === ' ') {
                                e.preventDefault();
                                setSel(r.id);
                              }
                            }}
                          >
                            <td className="td-id">{r.group?.code ?? '—'}</td>
                            <td className="td-id">{r.person?.code ?? '—'}</td>
                            <td>
                              <span className="ev-chip" style={{ color: tone, borderColor: tone === N4 ? N8 : tone }}>
                                {eventTypeLabel(r.event_type)}
                              </span>
                            </td>
                            <td className="td-text min280">
                              <div className="clamp2">{r.conclusion}</div>
                            </td>
                            <td className="td-conf" style={{ color: tone }}>
                              {fmtInt(r.score)}
                            </td>
                            <td className="td-id">{fmtHM(r.cycle_at, tz)}</td>
                            <td>
                              <Button
                                variant="ghost"
                                className="btn-22"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setEvidenceFor(r);
                                }}
                                aria-label={`Xem bản ghi thô của kết luận ${eventTypeLabel(r.event_type)} ${r.person?.code ?? ''}`}
                              >
                                Xem thô
                              </Button>
                            </td>
                          </tr>
                        );
                      })}
                  {list.isFetchingNextPage ? <SkeletonRow /> : null}
                </tbody>
              </table>
            </div>
            {list.isError ? (
              <CardError error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />
            ) : !list.isPending && rows.length === 0 ? (
              <EmptyState
                icon="ph ph-check-circle"
                title="Chưa có bản ghi sạch nào"
                description="Hệ thống ghi vào đây sau mỗi chu kỳ sàng lọc. Kiểm tra quy tắc hoặc chạy sàng lọc ngay ở Kho dữ liệu thô."
              />
            ) : null}
            <div ref={sentinel} aria-hidden style={{ height: 1 }} />
          </section>

          <div className="side-col">
            <MemoryCard row={selected} loading={list.isPending} tz={tz} />
            <ParamsCard row={selected} loading={list.isPending} />
          </div>
        </div>
      </div>
      <EvidenceDialog item={evidenceFor} onClose={() => setEvidenceFor(null)} tz={tz} />
    </>
  );
}

function SkeletonRow() {
  return (
    <tr aria-hidden>
      <td><Skeleton width={78} height={10} /></td>
      <td><Skeleton width={62} height={10} /></td>
      <td><Skeleton width={96} height={18} /></td>
      <td><Skeleton width="90%" height={10} /><Skeleton width="55%" height={10} style={{ marginTop: 6 }} /></td>
      <td><Skeleton width={22} height={10} /></td>
      <td><Skeleton width={36} height={10} /></td>
      <td><Skeleton width={56} height={22} /></td>
    </tr>
  );
}

// ── Trí nhớ tạm ───────────────────────────────────────────────────────────
export function MemoryCard({ row, loading, tz }: { row: CleanItem | null; loading: boolean; tz: string }) {
  const subject = subjectOf(row);
  // v0.1.35: khớp backend NB_WRITE = data.manage (apps/api/gh/data_api/routes.py) — không hiện nút bấm vào là 403.
  const canWrite = useCan('data.manage');
  const nb = useQuery({
    queryKey: qk2.notebook(subject?.type ?? 'person', subject?.id ?? '-'),
    queryFn: ({ signal }) => api.notebooks.get(subject!.type, subject!.id, signal),
    enabled: !!subject,
    retry: (n, e) => !(e instanceof ApiError && e.status === 404) && n < 2,
  });
  const compactions = useQuery({
    queryKey: qk2.compactions(subject?.type ?? 'person', subject?.id ?? '-'),
    queryFn: ({ signal }) => api.notebooks.compactions(subject!.type, subject!.id, signal),
    enabled: !!subject && nb.isSuccess && nb.data.compaction_no > 0,
  });
  const [historyOpen, setHistoryOpen] = useState(false);
  const [pinOpen, setPinOpen] = useState(false);
  const latest = compactions.data?.[0];
  const ids = [row?.group?.code, row?.person?.code, row?.person?.name ?? row?.group?.name].filter(Boolean).join(' · ');

  return (
    <section className="gh-card mem-card" aria-label="Trí nhớ tạm">
      <div className="mem-head">
        <div className="mem-head__top">
          <Icon name="ph ph-brain" size={15} color={ACC3} />
          <span className="mem-head__title">Trí nhớ tạm</span>
          {nb.data ? (
            <span className="mem-head__tokens">
              {fmtInt(nb.data.token_used)} / {fmtInt(nb.data.token_budget)} token
            </span>
          ) : null}
        </div>
        <div className="mem-head__sub">{ids || (loading ? '…' : 'Chưa chọn bản ghi')}</div>
      </div>
      {loading || (subject && nb.isPending) ? (
        <SkeletonLines rows={4} padding="13px 16px" gap={13} />
      ) : !subject ? (
        <EmptyState icon="ph ph-brain" title="Chọn một dòng để xem trí nhớ tạm" />
      ) : nb.isError ? (
        nb.error instanceof ApiError && nb.error.status === 404 ? (
          <EmptyState icon="ph ph-notebook" title="Chưa có sổ tay cho đối tượng này" description="Agent mở sổ tay khi có kết luận đầu tiên về người hoặc nhóm này." />
        ) : (
          <CardError error={nb.error} onRetry={() => void nb.refetch()} retrying={nb.isFetching} />
        )
      ) : !nb.data ? null : (
        <div className="mem-body">
          {nb.data.sections
            .filter((s) => s.entries.length > 0)
            .map((s) => {
              const tone = sectionTone(s.key);
              return (
                <div className="mem-sec" key={s.key}>
                  <div className="mem-sec__head">
                    <span className="mem-sec__title" style={{ color: tone }}>
                      {s.title}
                    </span>
                    <span className="mem-sec__rule" />
                    <span className="mem-sec__age">{sectionAge(s, nb.data, tz)}</span>
                  </div>
                  {s.entries.map((e) => (
                    <div className="mem-line" key={e.id}>
                      <span className="mem-line__dot" style={{ background: tone }} />
                      <span className="mem-line__text">{e.body}</span>
                    </div>
                  ))}
                </div>
              );
            })}
          {latest ? (
            <div className="mem-sec">
              <div className="mem-sec__head">
                <span className="mem-sec__title" style={{ color: N5 }}>
                  Đã nén bỏ khỏi ngữ cảnh
                </span>
                <span className="mem-sec__rule" />
                <span className="mem-sec__age">còn trong kho sạch</span>
              </div>
              <div className="mem-line">
                <span className="mem-line__dot" style={{ background: N5 }} />
                <span className="mem-line__text">{latest.summary}</span>
              </div>
            </div>
          ) : null}
          {nb.data.sections.every((s) => s.entries.length === 0) && !latest ? (
            <p className="muted-note">Sổ tay còn trống — agent sẽ ghi dần sau các chu kỳ tới.</p>
          ) : null}
          <div className="mem-actions">
            <Button variant="secondary" icon="ph ph-clock-counter-clockwise" className="btn-27" onClick={() => setHistoryOpen(true)}>
              Lịch sử nén
            </Button>
            {canWrite ? (
              <Button variant="ghost" icon="ph ph-pencil-simple" className="btn-27" onClick={() => setPinOpen(true)}>
                Sếp ghim thêm
              </Button>
            ) : null}
          </div>
        </div>
      )}
      {subject ? (
        <>
          <CompactionsDialog open={historyOpen} onClose={() => setHistoryOpen(false)} subject={subject} tz={tz} />
          <PinNoteDialog open={pinOpen} onClose={() => setPinOpen(false)} subject={subject} sections={nb.data?.sections ?? []} />
        </>
      ) : null}
    </section>
  );
}

function CompactionsDialog({
  open,
  onClose,
  subject,
  tz,
}: {
  open: boolean;
  onClose: () => void;
  subject: { type: NotebookSubjectType; id: string };
  tz: string;
}) {
  const q = useQuery({
    queryKey: qk2.compactions(subject.type, subject.id),
    queryFn: ({ signal }) => api.notebooks.compactions(subject.type, subject.id, signal),
    enabled: open,
  });
  return (
    <Dialog open={open} onClose={onClose} width={480} title="Lịch sử nén" kicker="Mỗi lần nén giữ lại ý chính, phần bỏ đi vẫn còn trong kho sạch">
      {q.isPending ? (
        <SkeletonLines rows={3} padding="0" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : q.data.length === 0 ? (
        <EmptyState icon="ph ph-clock-counter-clockwise" title="Sổ tay này chưa được nén lần nào" />
      ) : (
        <div className="dlg-list">
          {q.data.map((c) => (
            <div className="dlg-item" key={c.compaction_no}>
              <div className="dlg-item__meta">
                <span>Lần {c.compaction_no}</span>
                <span>·</span>
                <span>{fmtDMClock(c.at, tz)}</span>
                <span>·</span>
                <span>
                  {fmtInt(c.tokens_before)} → {fmtInt(c.tokens_after)} token
                </span>
                <span>·</span>
                <span>{fmtInt(c.archived)} mục lưu trữ</span>
              </div>
              <div className="dlg-item__text">{c.summary}</div>
            </div>
          ))}
        </div>
      )}
    </Dialog>
  );
}

const PIN_SECTIONS = [
  { value: 'guardrails', label: 'Giới hạn cho agent' },
  { value: 'attention_now', label: 'Điều cần chú ý ngay' },
  { value: 'preferences', label: 'Sở thích' },
  { value: 'open_threads', label: 'Việc dở' },
];

function PinNoteDialog({
  open,
  onClose,
  subject,
  sections,
}: {
  open: boolean;
  onClose: () => void;
  subject: { type: NotebookSubjectType; id: string };
  sections: NotebookSection[];
}) {
  const [section, setSection] = useState('guardrails');
  const [body, setBody] = useState('');
  useEffect(() => {
    if (open) {
      setSection('guardrails');
      setBody('');
    }
  }, [open]);
  const options = PIN_SECTIONS.map((o) => ({ ...o, label: sections.find((s) => s.key === o.value)?.title ?? o.label }));
  const save = useMutation({
    mutationFn: () => api.notebooks.addEntry(subject.type, subject.id, { section, body: body.trim(), pinned: true }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: qk2.notebook(subject.type, subject.id) });
      toast('Đã ghim vào trí nhớ tạm');
      onClose();
    },
  });
  const valid = body.trim().length > 0;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      width={440}
      title="Sếp ghim thêm"
      kicker="Mục ghim luôn nằm trong ngữ cảnh, không bị nén"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" icon="ph ph-push-pin" disabled={!valid} loading={save.isPending} onClick={() => save.mutate()}>
            Ghim
          </Button>
        </>
      }
    >
      <form
        className="dlg-fields"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) save.mutate();
        }}
      >
        <SelectField label="Mục" value={section} onChange={(e) => setSection(e.target.value)} options={options} />
        <label className="dlg-fields" style={{ gap: 6 }}>
          <span className="dlg-section-title">Nội dung</span>
          <textarea
            className="gh-textarea"
            value={body}
            maxLength={500}
            data-autofocus
            placeholder="Ví dụ: Không tự cam kết mốc giao dưới 12 ngày."
            onChange={(e) => setBody(e.target.value)}
          />
        </label>
        <InlineError>{save.isError ? errorText(save.error) : null}</InlineError>
      </form>
    </Dialog>
  );
}

// ── Tham số agent đang dùng ───────────────────────────────────────────────
function ParamsCard({ row, loading }: { row: CleanItem | null; loading: boolean }) {
  const g = row?.group?.id;
  const p = row?.person?.id;
  const params = useQuery({
    queryKey: qk2.agentParams(g, p),
    queryFn: ({ signal }) => api.clean.agentParams({ group_id: g, person_id: p }, signal),
    enabled: !!row,
  });
  return (
    <Panel flush title="Tham số agent đang dùng" kicker="Đầu vào quyết định nội dung phản hồi" label="Tham số agent đang dùng">
      {loading || (row && params.isPending) ? (
        <SkeletonLines rows={6} padding="12px 16px" gap={12} />
      ) : !row ? (
        <EmptyState icon="ph ph-sliders-horizontal" title="Chọn một dòng để xem tham số" />
      ) : params.isError ? (
        <CardError error={params.error} onRetry={() => void params.refetch()} retrying={params.isFetching} />
      ) : !params.data ? null : params.data.length === 0 ? (
        <EmptyState icon="ph ph-sliders-horizontal" title="Chưa có tham số nào" />
      ) : (
        <div className="params">
          {params.data.map((a) => (
            <div className="params__row" key={a.key}>
              <Icon name={a.icon} size={14} color={paramTone(a.icon)} />
              <span className="params__label">{a.label}</span>
              <span className="params__value">{a.value}</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

// ── Xem thô ───────────────────────────────────────────────────────────────
function EvidenceDialog({ item, onClose, tz }: { item: CleanItem | null; onClose: () => void; tz: string }) {
  const q = useQuery({
    queryKey: qk2.cleanEvidence(item?.id ?? '-'),
    queryFn: ({ signal }) => api.clean.evidence(item!.id, signal),
    enabled: !!item,
  });
  return (
    <Dialog
      open={!!item}
      onClose={onClose}
      width={520}
      title="Bản ghi thô làm chứng cứ"
      kicker={item ? `${eventTypeLabel(item.event_type)} · ${[item.group?.code, item.person?.code].filter(Boolean).join(' · ')}` : undefined}
    >
      {item ? <p className="dlg-item__text" style={{ marginTop: 0, marginBottom: 12 }}>{item.conclusion}</p> : null}
      {q.isPending ? (
        <SkeletonLines rows={3} padding="0" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : q.data.length === 0 ? (
        <EmptyState icon="ph ph-database" title="Không còn bản ghi thô nào gắn với kết luận này" />
      ) : (
        <div className="dlg-list">
          {q.data.map((ev) => (
            <div className="dlg-item" key={ev.raw.id}>
              <div className="dlg-item__meta">
                <Icon name={channelIcon(ev.raw.channel.type)} size={13} color={channelTone(ev.raw.channel.type)} label={ev.raw.channel.name} />
                <span>{ev.raw.code}</span>
                <span>·</span>
                <span>{fmtDMClock(ev.raw.received_at, tz)}</span>
                {ev.raw.person ? (
                  <>
                    <span>·</span>
                    <span>{ev.raw.person.code}</span>
                  </>
                ) : null}
              </div>
              <div className="dlg-item__text">{ev.raw.text}</div>
              {ev.quote ? <div className="dlg-item__quote">“{ev.quote}”</div> : null}
            </div>
          ))}
        </div>
      )}
    </Dialog>
  );
}
