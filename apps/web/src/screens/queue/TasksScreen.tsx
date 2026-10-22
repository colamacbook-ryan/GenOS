import { useState } from 'react';
import type { PromiseStatus, Task, TaskPriority, TaskStatus } from '@gen-harness/contracts';
import { Button, cx, Dialog, EmptyState, FilterSelect, Icon, Tabs, TextField, type FilterOption } from '@gen-harness/ui';
import { errorText } from '../../lib/errorText';
import { fmtDMClock, fmtInt } from '../../lib/format';
import { useUrlState } from '../../lib/uiStore';
import { CardError, InlineError, Panel, ScreenHead, SkeletonLines } from '../common';
import { WhyButton } from '../core/Evidence';
import { DataEmptyState } from '../DataEmptyState';
import { TASK_STATUS_LABEL, taskPriorityTone, taskStatusTone } from './queueModel';
import { LoadMore } from './LoadMore';
import { PROMISES_TRACKED } from '../../lib/features';
import { useCreateTask, useKeepPromise, usePromises, useTaskList, useUpdateTask } from './queries';

const STATUS_OPTIONS: FilterOption[] = [
  { value: '', label: 'Tất cả' },
  { value: 'todo', label: 'Chưa làm' },
  { value: 'doing', label: 'Đang làm' },
  { value: 'done', label: 'Đã xong' },
  { value: 'cancelled', label: 'Đã huỷ' },
];
const PRIORITY_OPTIONS: FilterOption[] = [
  { value: '', label: 'Tất cả' },
  { value: 'P1', label: 'P1' },
  { value: 'P2', label: 'P2' },
  { value: 'P3', label: 'P3' },
];
const NEXT_STATUS: Record<TaskStatus, TaskStatus> = { todo: 'doing', doing: 'done', done: 'todo', cancelled: 'todo' };

const TASKS_DESC = 'Việc sinh ra từ bản nháp đã duyệt hoặc do Sếp tạo tay. Việc quá hạn tô đỏ, việc có hẹn nhắc được nhắc trước để không ai bị bỏ quên.';
const TASKS_DESC_WITH_PROMISES =
  'Việc sinh ra từ lời hứa trong hội thoại, từ bản nháp đã duyệt hoặc do Sếp tạo tay. Việc quá hạn tô đỏ, lời hứa sắp đến hạn được nhắc trước để không ai bị bỏ quên.';

export function TasksScreen() {
  const [status, setStatus] = useUrlState<string>('status', '');
  const [priority, setPriority] = useUrlState<string>('priority', '');
  const [overdue, setOverdue] = useUrlState<string>('overdue', '');
  const tasks = useTaskList({
    status: (status || undefined) as TaskStatus | undefined,
    priority: (priority || undefined) as TaskPriority | undefined,
    overdue: overdue === 'true' ? true : undefined,
  });
  const update = useUpdateTask();
  const [adding, setAdding] = useState(false);

  return (
    <div className="screen">
      <ScreenHead
        title="Việc & Nhắc hẹn"
        description={PROMISES_TRACKED ? TASKS_DESC_WITH_PROMISES : TASKS_DESC}
        maxWidth={700}
        actions={
          <Button variant="primary" icon="ph ph-plus" data-gen-target="tasks.new" onClick={() => setAdding(true)}>
            Tạo việc mới
          </Button>
        }
      />

      <div className="tk-filters" role="group" aria-label="Bộ lọc việc">
        <FilterSelect label="Trạng thái" value={status} onChange={setStatus} options={STATUS_OPTIONS} />
        <FilterSelect label="Ưu tiên" value={priority} onChange={setPriority} options={PRIORITY_OPTIONS} />
        <FilterSelect
          label="Hạn"
          value={overdue}
          onChange={setOverdue}
          options={[
            { value: '', label: 'Tất cả' },
            { value: 'true', label: 'Đã quá hạn' },
          ]}
        />
      </div>

      <div className="tk-grid">
        <Panel title="Danh sách việc" kicker={tasks.data ? `${fmtInt(tasks.data.total)} việc` : 'Đang tải…'} bodyClass="tk-list">
          {tasks.isPending ? (
            <SkeletonLines rows={5} padding="12px 16px" />
          ) : tasks.isError && !tasks.isFetchNextPageError ? (
            <CardError error={tasks.error} onRetry={() => void tasks.refetch()} retrying={tasks.isFetching} />
          ) : tasks.data.items.length === 0 ? (
            <DataEmptyState
              filtered={status !== '' || priority !== '' || overdue !== ''}
              fallback={<EmptyState icon="ph ph-check-square" title="Không có việc nào khớp bộ lọc" />} />
          ) : (
            <>
              {tasks.data.items.map((t) => (
                <TaskRow
                  key={t.id}
                  t={t}
                  onToggleStatus={() => update.mutate({ id: t.id, body: { status: NEXT_STATUS[t.status] } })}
                  busy={update.isPending && update.variables?.id === t.id}
                />
              ))}
              <LoadMore
                shown={tasks.data.items.length}
                total={tasks.data.total}
                hasMore={tasks.hasNextPage}
                loading={tasks.isFetchingNextPage}
                onMore={() => void tasks.fetchNextPage()}
                error={tasks.isFetchNextPageError ? tasks.error : null}
                unit="việc"
                testId="tasks-xem-them"
              />
            </>
          )}
        </Panel>

        {PROMISES_TRACKED ? <PromisesPanel /> : null}
      </div>

      {adding ? <NewTaskDialog onClose={() => setAdding(false)} /> : null}
    </div>
  );
}

function TaskRow({ t, onToggleStatus, busy }: { t: Task; onToggleStatus: () => void; busy: boolean }) {
  return (
    <div className={cx('tk-row', t.overdue && 'tk-row--overdue')} data-gen-target={`tasks.row:${t.id}`}>
      <button type="button" className="tk-row__status" style={{ color: taskStatusTone(t.status) }} onClick={onToggleStatus} disabled={busy} aria-label={`Đổi trạng thái ${t.code}`}>
        <Icon name={t.status === 'done' ? 'ph-fill ph-check-circle' : t.status === 'cancelled' ? 'ph ph-x-circle' : 'ph ph-circle'} size={16} />
      </button>
      <div className="tk-row__body">
        <div className="tk-row__title">{t.title}</div>
        <div className="tk-row__meta">
          <span className="mono">{t.code}</span>
          <span style={{ color: taskPriorityTone(t.priority) }}>{t.priority}</span>
          <span>{TASK_STATUS_LABEL[t.status]}</span>
          {t.assignee ? <span>{t.assignee.name}</span> : <span>chưa phân công</span>}
          {t.due_at ? <span className={t.overdue ? 'tk-row__due--overdue' : undefined}>hạn {fmtDMClock(t.due_at)}</span> : null}
        </div>
      </div>
      <WhyButton kind="task" id={t.id} icon={null} size="sm">
        Vì sao →
      </WhyButton>
    </div>
  );
}

function PromisesPanel() {
  const [tab, setTab] = useUrlState<PromiseStatus>('ptab', 'upcoming');
  const promises = usePromises(tab);
  const keep = useKeepPromise();
  return (
    <Panel title="Lời hứa" kicker="Theo hạn — quá hạn tô đỏ" bodyClass="tk-promises">
      <Tabs
        items={[
          { key: 'upcoming', label: 'Sắp đến hạn' },
          { key: 'overdue', label: 'Đã quá hạn' },
          { key: 'kept', label: 'Đã giữ' },
          { key: 'all', label: 'Tất cả' },
        ]}
        value={tab}
        onChange={setTab}
        label="Tab lời hứa"
        idPrefix="promise-tab"
        className="tk-promises__tabs"
      />
      {promises.isPending ? (
        <SkeletonLines rows={3} padding="10px 16px" />
      ) : promises.isError ? (
        <CardError error={promises.error} onRetry={() => void promises.refetch()} retrying={promises.isFetching} />
      ) : promises.data.items.length === 0 ? (
        <DataEmptyState filtered={tab !== 'upcoming' && tab !== 'all'} fallback={<EmptyState icon="ph ph-handshake" title="Không có lời hứa nào ở mục này" />} />
      ) : (
        promises.data.items.map((p) => (
          <div key={p.id} className={cx('tk-promise', p.broken && 'tk-promise--broken')}>
            <div className="tk-promise__text">{p.text}</div>
            <div className="tk-promise__meta">
              <span>{p.from.name}</span>
              <span>hạn {fmtDMClock(p.due_at)}</span>
              {p.kept_at ? <span>đã giữ {fmtDMClock(p.kept_at)}</span> : p.broken ? <span className="tk-row__due--overdue">đã vỡ</span> : null}
            </div>
            {!p.kept_at ? (
              <div className="tk-promise__actions">
                <Button variant="ghost" size="sm" loading={keep.isPending} onClick={() => keep.mutate({ id: p.id, kept: true })}>
                  Đã giữ lời hứa
                </Button>
              </div>
            ) : null}
          </div>
        ))
      )}
      {keep.isError ? <InlineError>{errorText(keep.error)}</InlineError> : null}
    </Panel>
  );
}

function NewTaskDialog({ onClose }: { onClose: () => void }) {
  const create = useCreateTask();
  const [title, setTitle] = useState('');
  const [dueAt, setDueAt] = useState('');
  const [priority, setPriority] = useState<TaskPriority>('P3');
  const submit = () => {
    if (!title.trim()) return;
    create.mutate({ title, priority, due_at: dueAt ? new Date(dueAt).toISOString() : null }, { onSuccess: onClose });
  };
  return (
    <Dialog
      open
      onClose={onClose}
      width={420}
      title="Tạo việc mới"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" icon="ph ph-plus" loading={create.isPending} disabled={!title.trim()} onClick={submit}>
            Tạo việc
          </Button>
        </>
      }
    >
      <form
        className="dlg-fields"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <TextField label="Tiêu đề" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
        <FilterSelect label="Ưu tiên" value={priority} onChange={(v) => setPriority(v as TaskPriority)} options={PRIORITY_OPTIONS.filter((o) => o.value)} />
        <TextField label="Hạn (không bắt buộc)" type="datetime-local" value={dueAt} onChange={(e) => setDueAt(e.target.value)} />
        {create.isError ? <InlineError>{errorText(create.error)}</InlineError> : null}
      </form>
    </Dialog>
  );
}
