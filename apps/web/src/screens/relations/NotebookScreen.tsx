import { useEffect, useState } from 'react';
import type { NbEntry, NotebookSubjectType } from '@gen-harness/contracts';
import { Button, Dialog, EmptyState, Icon, Switch, Tabs, TextField, type TabItem } from '@gen-harness/ui';
import { errorText } from '../../lib/errorText';
import { fmtDMClock, fmtHM, fmtInt } from '../../lib/format';
import { useUrlState } from '../../lib/uiStore';
import { CardError, InlineError, Panel, SkeletonLines } from '../common';
import { initialsOf, sectionIcon, sectionTone } from './relationsModel';
import {
  useAddNbEntry,
  useCompactNb,
  useDeleteNbEntry,
  useNbDropped,
  useNbHistory,
  useNbSubjects,
  useNotebook,
  useResetNb,
  useUpdateNbEntry,
} from './queries';

export function NotebookScreen() {
  const [tab, setTab] = useUrlState<NotebookSubjectType>('nbt', 'person');
  const [subjectId, setSubjectId] = useUrlState<string>('id', '');
  const subjects = useNbSubjects(tab);

  useEffect(() => {
    if (!subjectId && subjects.data?.items.length) setSubjectId(subjects.data.items[0].id);
    // chỉ tự chọn khi chưa có gì được chọn — không ghi đè lựa chọn của người dùng
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subjects.data]);

  const items: TabItem<NotebookSubjectType>[] = [
    { key: 'person', label: 'Con người' },
    { key: 'group', label: 'Nhóm' },
  ];

  return (
    <div className="screen">
      <div className="screen-head-solo" style={{ maxWidth: 760 }}>
        <h2 className="screen-title">Sổ tay nhận thức</h2>
        <p className="screen-desc">
          Mỗi ID người và ID nhóm có một sổ tay riêng do AI-trợ lý tự ghi trong quá trình tương tác: nhận thức hiện tại, các mốc lũy
          tiến, và danh sách ID dữ liệu cần gọi lại khi cần. Đây là cửa sổ ngữ cảnh ngắn — không phải toàn bộ kho sạch.
        </p>
      </div>

      <div className="nb-grid">
        <div className="gh-card nb-sidebar">
          <div className="nb-sidebar__head">
            <Tabs
              items={items}
              value={tab}
              onChange={(v) => {
                setTab(v);
                setSubjectId('');
              }}
              label="Tab sổ tay"
              idPrefix="nb-tab"
            />
          </div>
          <div className="nb-sidebar__list">
            {subjects.isPending ? (
              <SkeletonLines rows={6} padding="10px 13px" />
            ) : subjects.isError ? (
              <CardError error={subjects.error} onRetry={() => void subjects.refetch()} retrying={subjects.isFetching} />
            ) : subjects.data.items.length === 0 ? (
              <EmptyState icon="ph ph-notebook" title="Chưa có sổ tay nào" />
            ) : (
              subjects.data.items.map((s) => (
                <button key={s.id} type="button" className="nb-subject" aria-pressed={s.id === subjectId} onClick={() => setSubjectId(s.id)}>
                  <div className="nb-subject__row">
                    <span className="nb-subject__name">{s.name}</span>
                  </div>
                  <div className="nb-subject__id">{s.code}</div>
                  <div className="nb-subject__foot">
                    <span className="nb-subject__bar">
                      <span style={{ width: `${Math.min(100, (s.token_used / Math.max(1, s.token_budget)) * 100)}%` }} />
                    </span>
                    <span className="nb-subject__tokens">
                      {fmtInt(s.token_used)}/{fmtInt(s.token_budget)}
                    </span>
                    <span className="nb-subject__entries">{s.entries} mục</span>
                  </div>
                </button>
              ))
            )}
          </div>
        </div>

        {subjectId ? <NotebookDetail type={tab} id={subjectId} /> : (
          <div className="gh-card nb-empty">
            <EmptyState icon="ph ph-notebook" title="Chọn một chủ thể để xem sổ tay" description="Danh sách bên trái liệt kê mọi người và nhóm đang có sổ tay." />
          </div>
        )}
      </div>
    </div>
  );
}

function NotebookDetail({ type, id }: { type: NotebookSubjectType; id: string }) {
  const nb = useNotebook(type, id);
  const history = useNbHistory(type, id);
  const dropped = useNbDropped(type, id);
  const compact = useCompactNb(type, id);
  const reset = useResetNb(type, id);
  const [addOpen, setAddOpen] = useState<{ section: string; pinned: boolean } | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);

  if (nb.isPending) return <div className="gh-card nb-detail"><SkeletonLines rows={6} /></div>;
  if (nb.isError) return <div className="gh-card nb-detail"><CardError error={nb.error} onRetry={() => void nb.refetch()} retrying={nb.isFetching} /></div>;

  const d = nb.data;
  const pct = Math.min(100, (d.token_used / Math.max(1, d.token_budget)) * 100);

  return (
    <div className="nb-main">
      <section className="gh-card nb-detail">
        <div className="nb-detail__head">
          <span className="nb-detail__av" aria-hidden>
            {initialsOf(d.subject.name)}
          </span>
          <div className="nb-detail__meta">
            <div className="nb-detail__row">
              <span className="nb-detail__name">{d.subject.name}</span>
              <span className="mono">{d.subject.code}</span>
            </div>
            <div className="nb-detail__ctx">
              Nén lần thứ {d.compaction_no}{d.last_compacted_at ? ` lúc ${fmtHM(d.last_compacted_at)}` : ''}
            </div>
          </div>
          <div className="nb-detail__tokens">
            <div className="nb-detail__tokens-label">Cửa sổ ngữ cảnh</div>
            <div className="nb-detail__tokens-value">{fmtInt(d.token_used)} / {fmtInt(d.token_budget)}</div>
          </div>
          <Button variant="secondary" icon="ph ph-arrows-in-line-horizontal" loading={compact.isPending} onClick={() => compact.mutate()}>
            Nén ngay
          </Button>
        </div>
        {compact.isError ? <InlineError>{errorText(compact.error)}</InlineError> : null}

        <div className="nb-detail__body">
          <div className="nb-sections">
            {d.sections.map((s) => (
              <SectionBlock key={s.key} type={type} id={id} section={s} />
            ))}
          </div>

          <div className="nb-side">
            <div className="nb-refs">
              <div className="nb-refs__title">
                <Icon name="ph ph-hash" size={13} />
                Gợi nhớ ID dữ liệu
              </div>
              <div className="nb-refs__note">Agent chỉ giữ ID, cần chi tiết thì gọi thẳng vào kho sạch.</div>
              <div className="nb-refs__chips">
                {d.refs.length === 0 ? <span className="nb-refs__empty">Chưa có ID nào được ghi</span> : d.refs.map((r, i) => (
                  <span className="mono-tag" key={i} title={r.label ?? undefined}>{r.code ?? r.id}</span>
                ))}
              </div>
            </div>

            <div className="nb-owner">
              <div className="nb-owner__title">Sếp can thiệp</div>
              <Button variant="ghost" size="sm" icon="ph ph-push-pin" onClick={() => setAddOpen({ section: 'guardrails', pinned: true })}>
                Ghim thêm một điều agent phải nhớ
              </Button>
              <Button variant="ghost" size="sm" icon="ph ph-pencil-simple" onClick={() => setAddOpen({ section: 'attention_now', pinned: false })}>
                Sửa lại nhận thức agent đang hiểu sai
              </Button>
              <Button variant="ghost" size="sm" icon="ph ph-eraser" loading={reset.isPending} onClick={() => setConfirmReset(true)}>
                Xoá sổ tay và học lại từ kho sạch
              </Button>
              {reset.isError ? <InlineError>{errorText(reset.error)}</InlineError> : null}
            </div>
          </div>
        </div>
      </section>

      <div className="nb-bottom">
        <Panel flush title="Lịch sử nén" kicker={`${fmtInt(d.compaction_no)} lần nén`}>
          {history.isPending ? (
            <SkeletonLines rows={3} padding="10px 16px" />
          ) : history.isError ? (
            <CardError error={history.error} onRetry={() => void history.refetch()} retrying={history.isFetching} />
          ) : history.data.length === 0 ? (
            <EmptyState icon="ph ph-arrows-in-line-horizontal" title="Chưa nén lần nào" />
          ) : (
            <div className="nb-history">
              {history.data.map((h) => (
                <div className="nb-history__row" key={h.compaction_no}>
                  <span className="nb-history__no">Lần {h.compaction_no}</span>
                  <span className="nb-history__time">{fmtDMClock(h.at)}</span>
                  <span className="nb-history__text">{h.summary} — gộp {fmtInt(h.archived)} mục, {fmtInt(h.tokens_before)} → {fmtInt(h.tokens_after)} token</span>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel flush title="Mục đã lưu trữ" kicker="Đã nén khỏi cửa sổ — vẫn truy được">
          {dropped.isPending ? (
            <SkeletonLines rows={3} padding="10px 16px" />
          ) : dropped.isError ? (
            <CardError error={dropped.error} onRetry={() => void dropped.refetch()} retrying={dropped.isFetching} />
          ) : dropped.data.items.length === 0 ? (
            <EmptyState icon="ph ph-archive" title="Chưa có mục nào đã lưu trữ" />
          ) : (
            <div className="nb-dropped">
              {dropped.data.items.map((it) => (
                <div className="nb-dropped__row" key={it.id}>
                  <Icon name="ph ph-archive" size={13} />
                  <span className="nb-dropped__text">{it.body}</span>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      {addOpen ? (
        <AddEntryDialog type={type} id={id} defaultSection={addOpen.section} defaultPinned={addOpen.pinned} onClose={() => setAddOpen(null)} />
      ) : null}
      {confirmReset ? (
        <ConfirmResetDialog
          onClose={() => setConfirmReset(false)}
          onConfirm={() => reset.mutate(undefined, { onSuccess: () => setConfirmReset(false) })}
          busy={reset.isPending}
        />
      ) : null}
      {pct >= 90 ? <div className="nb-warn">Cửa sổ ngữ cảnh đã dùng {Math.round(pct)}% ngân sách — sẽ tự nén ở lần ghi tiếp theo.</div> : null}
    </div>
  );
}

function SectionBlock({ type, id, section }: { type: NotebookSubjectType; id: string; section: { key: string; title: string; entries: NbEntry[] } }) {
  const update = useUpdateNbEntry(type, id);
  const del = useDeleteNbEntry(type, id);
  const tone = sectionTone(section.key);
  return (
    <div className="nb-section">
      <div className="nb-section__head">
        <Icon name={sectionIcon(section.key)} size={14} color={tone} />
        <span className="nb-section__title" style={{ color: tone }}>{section.title}</span>
        <span className="nb-section__rule" />
        <span className="nb-section__count">{section.entries.length} mục</span>
      </div>
      {section.entries.length === 0 ? (
        <div className="nb-section__empty">Chưa có mục nào</div>
      ) : (
        section.entries.map((e) => (
          <div className="nb-line" key={e.id}>
            <span className="nb-line__dot" style={{ background: tone }} />
            <span className="nb-line__text">{e.body}</span>
            <span className="nb-line__actions">
              <button
                type="button"
                className="nb-line__icon"
                aria-pressed={e.pinned}
                aria-label={e.pinned ? 'Bỏ ghim mục này' : 'Ghim mục này'}
                title={e.pinned ? 'Bỏ ghim' : 'Ghim — không bị nén'}
                onClick={() => update.mutate({ eid: e.id, body: { pinned: !e.pinned } })}
              >
                <Icon name={e.pinned ? 'ph-fill ph-push-pin' : 'ph ph-push-pin'} size={13} />
              </button>
              {e.editable ? (
                <button
                  type="button"
                  className="nb-line__icon"
                  aria-label="Xoá mục này"
                  title="Xoá (lưu trữ)"
                  onClick={() => del.mutate(e.id)}
                >
                  <Icon name="ph ph-trash" size={13} />
                </button>
              ) : null}
            </span>
          </div>
        ))
      )}
    </div>
  );
}

function AddEntryDialog({
  type, id, defaultSection, defaultPinned, onClose,
}: {
  type: NotebookSubjectType; id: string; defaultSection: string; defaultPinned: boolean; onClose: () => void;
}) {
  const add = useAddNbEntry(type, id);
  const [section, setSection] = useState(defaultSection);
  const [body, setBody] = useState('');
  const [pinned, setPinned] = useState(defaultPinned);
  const SECTIONS = [
    { key: 'attention_now', title: 'Điều cần chú ý ngay' },
    { key: 'rolling_context', title: 'Ngữ cảnh ngắn lũy tiến' },
    { key: 'guardrails', title: 'Giới hạn cho agent' },
    { key: 'preferences', title: 'Sở thích' },
    { key: 'open_threads', title: 'Việc dở' },
  ];
  return (
    <Dialog
      open
      onClose={onClose}
      width={440}
      title="Ghi mục tay vào sổ tay"
      kicker="Ghi chú của Sếp — không bị agent nén nếu ghim"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button
            variant="primary"
            icon="ph ph-push-pin"
            loading={add.isPending}
            disabled={!body.trim()}
            onClick={() => add.mutate({ section, body: body.trim(), pinned }, { onSuccess: onClose })}
          >
            Ghi vào sổ tay
          </Button>
        </>
      }
    >
      <div className="dlg-fields">
        <div className="dir-filter-row">
          <span className="dir-filter-row__label">Mục</span>
          <div className="dir-filter-row__opts" role="group" aria-label="Chọn mục sổ tay">
            {SECTIONS.map((s) => (
              <button key={s.key} type="button" className="dir-filter-row__opt" aria-pressed={section === s.key} onClick={() => setSection(s.key)}>
                {s.title}
              </button>
            ))}
          </div>
        </div>
        <TextField label="Nội dung" value={body} onChange={(e) => setBody(e.target.value)} maxLength={600} />
        <label className="nb-pin-row">
          <span>Ghim — không bị nén tự động</span>
          <Switch checked={pinned} onChange={setPinned} label="Ghim mục này" />
        </label>
        {add.isError ? <InlineError>{errorText(add.error)}</InlineError> : null}
      </div>
    </Dialog>
  );
}

function ConfirmResetDialog({ onClose, onConfirm, busy }: { onClose: () => void; onConfirm: () => void; busy: boolean }) {
  return (
    <Dialog
      open
      onClose={onClose}
      width={400}
      title="Xoá sổ tay và học lại từ kho sạch"
      kicker="Lưu trữ toàn bộ mục chưa ghim — không sinh mục tóm tắt"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" icon="ph ph-eraser" loading={busy} onClick={onConfirm}>
            Đặt lại sổ tay
          </Button>
        </>
      }
    >
      <p className="nb-confirm-text">
        Mọi mục chưa ghim sẽ được lưu trữ ngay (vẫn truy được ở "Mục đã lưu trữ"), khác nén — không sinh mục tóm tắt. Mục ở "Giới hạn cho
        agent" không bao giờ bị đụng tới.
      </p>
    </Dialog>
  );
}
