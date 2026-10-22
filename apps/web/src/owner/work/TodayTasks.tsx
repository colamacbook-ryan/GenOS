/**
 * v0.1.59 (G3) — "Việc hôm nay" và "Gen đề xuất" của màn Việc (`/owner/viec`).
 *
 * - Việc hôm nay = việc đang mở (`GET /tasks`, trạng thái todo + doing) có hạn đến hết hôm nay hoặc đã quá hạn — LỌC HẠN Ở MÁY CHỦ
 *   (`due_before` = đầu ngày mai theo giờ tổ chức) nên việc quá hạn tạo từ lâu không rơi khỏi 50 việc mới nhất
 *   + lời hứa quá hạn (`GET /tasks/promises?status=overdue`). Tick "Xong" (`useTaskDone`; lời hứa thì ghi "đã giữ lời"),
 *   "Dời sang mai" (`useTaskPostpone`, 09:00 sáng mai), "Mở hồ sơ" (`/owner/quan-he?nguoi=<mã người>`).
 * - Gen đề xuất = gợi ý nhắc việc cho khách cần chăm (`GET /owner/relations?list=care`) — Sếp bấm "Tạo nhắc" (`useCreateReminder`)
 *   thì mới có việc; Gen không tự tạo. Người ĐÃ có việc/nhắc đang mở (`has_open_task`) không được gợi ý nữa (tải lại không tạo trùng).
 * Mọi chuỗi từ máy chủ qua `asText`; lỗi = câu thân thiện + "Chi tiết kỹ thuật".
 */
/* eslint-disable react-refresh/only-export-components */
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import type { OwnerRelationRow, OwnerRelations, Promise as PromiseItem, Task } from '@gen-harness/contracts';
import { Badge, Card, EmptyState, cx } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { errorDetail, errorText } from '../../lib/errorText';
import { DEFAULT_TZ, fmtDM, fmtHM } from '../../lib/format';
import { InlineError } from '../../screens/common';
import { PROMISES_TRACKED } from '../../lib/features';
import { usePromises, useTasks } from '../../screens/queue/queries';
import { invalidateAfterWrite, useCreateReminder, useTaskDone, useTaskPostpone, tomorrowMorning } from '../actions/useOwnerActions';
import { ASK_GEN_PROMPTS, OWNER_PATHS, asText, moneyText, safeLink } from '../ownerModel';
import { openAskGen } from '../AskGenScreen';
import { OwnerError, PartialLoadNote } from '../parts';
import './work.css';

// ── mô hình thuần ─────────────────────────────────────────────────────────────────────────────────────────────────────

/** Ngày `YYYY-MM-DD` theo giờ tổ chức (để so "hạn ≤ hôm nay"). Giờ lạ ⇒ rỗng. */
export function dayKey(iso: string | number | Date, tz: string = DEFAULT_TZ): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const fmt = (zone: string) => new Intl.DateTimeFormat('en-CA', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
  try {
    return fmt(tz);
  } catch {
    return fmt(DEFAULT_TZ);
  }
}

/** Số phút lệch của múi giờ `tz` so với UTC tại thời điểm `at` (UTC+7 ⇒ 420). Múi lạ ⇒ dùng DEFAULT_TZ. */
function tzOffsetMin(at: number, tz: string): number {
  const read = (zone: string) => {
    const parts = new Intl.DateTimeFormat('en-US', { timeZone: zone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }).formatToParts(new Date(at));
    const n = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
    return (Date.UTC(n('year'), n('month') - 1, n('day'), n('hour'), n('minute'), n('second')) - Math.floor(at / 1000) * 1000) / 60_000;
  };
  try {
    return read(tz);
  } catch {
    return read(DEFAULT_TZ);
  }
}

/** Đầu ngày MAI (00:00) theo giờ tổ chức, dạng ISO — mốc `due_before` của "Việc hôm nay". Cùng một giá trị cả ngày (khoá truy vấn ổn định). */
export function tomorrowStartIso(now: number = Date.now(), tz: string = DEFAULT_TZ): string {
  const [y, m, d] = dayKey(now, tz).split('-').map(Number);
  const wall = Date.UTC(y, m - 1, d + 1, 0, 0, 0);
  let at = wall - tzOffsetMin(wall, tz) * 60_000;
  at = wall - tzOffsetMin(at, tz) * 60_000; // lần 2: múi giờ có giờ mùa hè lệch ngay quanh nửa đêm
  return new Date(at).toISOString();
}

/** Mỗi nguồn việc (todo / doing) tải tối đa chừng này việc hạn ≤ hôm nay; nhiều hơn thì báo "còn N việc nữa". */
export const TODAY_TASKS_LIMIT = 200;

export interface TodayItem {
  kind: 'task' | 'promise';
  id: string;
  title: string;
  /** Hạn (ISO) — luôn có: việc không có hạn không phải "việc hôm nay". */
  dueAt: string;
  overdue: boolean;
  /** Mã người liên quan (mở hồ sơ ở Quan hệ); null = không có / là nhóm. */
  personId: string | null;
  /** Dòng phụ 12px. */
  meta: string;
}

const SOURCE_TEXT: Record<string, string> = { promise: 'Từ lời hứa', draft: 'Từ bản nháp', manual: 'Sếp tự tạo' };

/** Việc đang mở (todo/doing) hạn ≤ hết hôm nay hoặc quá hạn + lời hứa quá hạn chưa giữ; quá hạn trước, rồi theo hạn tăng dần. */
export function buildTodayItems(tasks: readonly Task[], promises: readonly PromiseItem[], now: number = Date.now(), tz: string = DEFAULT_TZ): TodayItem[] {
  const today = dayKey(now, tz);
  const out: TodayItem[] = [];
  for (const t of tasks) {
    const due = asText(t?.due_at);
    if (!asText(t?.id) || !due || !Number.isFinite(Date.parse(due))) continue;
    if (t.status !== 'todo' && t.status !== 'doing') continue;
    if (dayKey(due, tz) > today) continue;
    const s = t.subject as { id?: unknown; channel?: unknown } | null;
    out.push({
      kind: 'task',
      id: asText(t.id),
      title: asText(t.title) || 'Việc chưa đặt tên',
      dueAt: due,
      overdue: Date.parse(due) < now,
      personId: s && typeof s === 'object' && !('channel' in s) && asText(s.id) ? asText(s.id) : null,
      meta: SOURCE_TEXT[asText(t.source)] ?? 'Sếp tự tạo',
    });
  }
  for (const p of promises) {
    const due = asText(p?.due_at);
    if (!asText(p?.id) || !due || !Number.isFinite(Date.parse(due)) || p.kept_at) continue;
    if (Date.parse(due) >= now) continue;
    const from = p.from as { id?: unknown } | null;
    out.push({
      kind: 'promise',
      id: asText(p.id),
      title: asText(p.text) || 'Lời hứa chưa ghi nội dung',
      dueAt: due,
      overdue: true,
      personId: from && typeof from === 'object' && asText(from.id) ? asText(from.id) : null,
      meta: `Lời hứa hạn ${fmtDM(due, tz)}`,
    });
  }
  return out.sort((a, b) => Number(b.overdue) - Number(a.overdue) || Date.parse(a.dueAt) - Date.parse(b.dueAt));
}

/** "Quá hạn 2 ngày" / "Quá hạn 3 giờ" / "Hạn 17:00". */
export function dueText(it: Pick<TodayItem, 'dueAt' | 'overdue'>, now: number = Date.now(), tz: string = DEFAULT_TZ): string {
  if (!it.overdue) return `Hạn ${fmtHM(it.dueAt, tz)}`;
  const ms = Math.max(0, now - Date.parse(it.dueAt));
  const days = Math.floor(ms / 86_400_000);
  if (days >= 1) return `Quá hạn ${days} ngày`;
  const hours = Math.floor(ms / 3_600_000);
  return hours >= 1 ? `Quá hạn ${hours} giờ` : 'Vừa quá hạn';
}

/** Đường tới hồ sơ người trong Quan hệ. */
export function profileLink(personId: string): string {
  return safeLink(`${OWNER_PATHS.relations}?nguoi=${encodeURIComponent(personId)}`, OWNER_PATHS.relations);
}

// ── dữ liệu ───────────────────────────────────────────────────────────────────────────────────────────────────────────

export interface TodayData {
  items: TodayItem[];
  isPending: boolean;
  /** Không đọc được việc nào (cả todo và doing đều lỗi). */
  isError: boolean;
  /** Lời hứa lỗi riêng — vẫn hiện việc, kèm một dòng báo. */
  promisesFailed: boolean;
  /** Chỉ MỘT trong hai nguồn việc (todo / doing) lỗi — danh sách có thể thiếu. */
  tasksPartialFailed: boolean;
  /** Số việc hạn ≤ hôm nay mà máy chủ có nhưng chưa tải hết (vượt `TODAY_TASKS_LIMIT`); 0 = đã đủ. */
  tasksNotShown: number;
  /** Lỗi của phần không tải được (lời hứa, hoặc nguồn việc lỗi) — cho "Chi tiết kỹ thuật" của dòng báo một phần. */
  partialError: unknown;
  error: unknown;
  isFetching: boolean;
  refetch: () => void;
}

export function useTodayData(tz: string = DEFAULT_TZ): TodayData {
  // Lọc hạn Ở MÁY CHỦ (`due_before` = đầu ngày mai): trước đây lấy 50 việc mới nhất rồi lọc ở web nên việc quá hạn tạo sớm bị rơi.
  const dueBefore = tomorrowStartIso(Date.now(), tz); // cùng một giá trị cả ngày ⇒ khoá truy vấn ổn định; qua nửa đêm thì tự tải lại
  const todo = useTasks({ status: 'todo', due_before: dueBefore, limit: TODAY_TASKS_LIMIT });
  const doing = useTasks({ status: 'doing', due_before: dueBefore, limit: TODAY_TASKS_LIMIT });
  const promises = usePromises('overdue', PROMISES_TRACKED);
  const taskRows = [...(Array.isArray(todo.data?.items) ? todo.data.items : []), ...(Array.isArray(doing.data?.items) ? doing.data.items : [])];
  const promiseRows = Array.isArray(promises.data?.items) ? promises.data.items : [];
  const shown = (q: typeof todo) => (Array.isArray(q.data?.items) ? q.data.items.length : 0);
  const total = (q: typeof todo) => (typeof q.data?.total === 'number' ? q.data.total : shown(q));
  return {
    items: buildTodayItems(taskRows, promiseRows, Date.now(), tz),
    isPending: todo.isPending && doing.isPending,
    isError: todo.isError && doing.isError,
    promisesFailed: promises.isError,
    tasksPartialFailed: todo.isError !== doing.isError,
    tasksNotShown: Math.max(0, total(todo) - shown(todo)) + Math.max(0, total(doing) - shown(doing)),
    partialError: promises.error ?? todo.error ?? doing.error,
    error: todo.error ?? doing.error,
    isFetching: todo.isFetching || doing.isFetching || promises.isFetching,
    refetch: () => {
      void todo.refetch();
      void doing.refetch();
      if (PROMISES_TRACKED) void promises.refetch();
    },
  };
}

/** Giữ lời hứa: `PATCH /tasks/promises/{id} {kept:true}`; sau đó tải lại `['owner']`, `['queue']`… như mọi lần ghi. */
function useKeepPromise() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.queue.tasks.promises.keep(id, true),
    onSuccess: () => invalidateAfterWrite(qc),
  });
}

// ── Việc hôm nay ──────────────────────────────────────────────────────────────────────────────────────────────────────

export function TodayTasks({ data, kicker, tz = DEFAULT_TZ, className }: { data: TodayData; kicker?: string; tz?: string; className?: string }) {
  const done = useTaskDone();
  const postpone = useTaskPostpone();
  const keep = useKeepPromise();
  // Ô tick giữ trạng thái "đã tick" NGAY khi bấm (đồng bộ) — không chờ cờ isPending của mutation, nếu không React trả ô về chưa tick
  // trong lúc chờ. Lỗi ⇒ bỏ tick để Sếp bấm lại; thành công ⇒ dòng biến mất khi danh sách tải lại.
  const [ticked, setTicked] = useState<ReadonlySet<string>>(new Set());
  const mark = (key: string, on: boolean) =>
    setTicked((cur) => {
      const next = new Set(cur);
      if (on) next.add(key);
      else next.delete(key);
      return next;
    });
  const title = 'Việc hôm nay';
  if (data.isPending) return <Card title={title} state="loading" className={className} data-testid="work-today-loading" />;
  if (data.isError) {
    return (
      <Card title={title} state="error" className={className} data-testid="work-today-error">
        <OwnerError error={data.error} onRetry={data.refetch} retrying={data.isFetching} />
      </Card>
    );
  }
  const failed = done.error ?? postpone.error ?? keep.error;
  // Một phần không tải được (lời hứa, hoặc chỉ một nguồn việc) KHÔNG được che bằng "Hôm nay không có việc" hay chỉ một dòng chữ.
  const partial = data.promisesFailed || data.tasksPartialFailed ? (
    <PartialLoadNote error={data.partialError} onRetry={data.refetch} retrying={data.isFetching} testId="work-today-partial">
      {data.tasksPartialFailed ? 'Chưa tải được một phần danh sách việc — danh sách dưới đây có thể thiếu.' : 'Chưa tải được danh sách lời hứa — danh sách dưới đây có thể thiếu.'}
    </PartialLoadNote>
  ) : null;
  const truncated =
    data.tasksNotShown > 0 ? (
      <p className="owner-muted" data-testid="work-today-truncated">
        {`Còn ${data.tasksNotShown} việc đến hạn nữa chưa hiện hết — Sếp mở Việc & Nhắc hẹn để xem đủ.`}
      </p>
    ) : null;
  if (data.items.length === 0) {
    return (
      <Card title={title} kicker={kicker} className={className} data-testid="work-today">
        <EmptyState
          icon="ph ph-check-square"
          title="Hôm nay không có việc đến hạn."
          action={
            <button type="button" className="kit-btn kit-btn--secondary" data-testid="work-today-ask" onClick={() => openAskGen(ASK_GEN_PROMPTS[2])}>
              Nhờ Gen tạo nhắc
            </button>
          }
        />
        {partial}
        {truncated}
        {failed ? <InlineError detail={errorDetail(failed)}>{errorText(failed)}</InlineError> : null}
      </Card>
    );
  }
  const now = Date.now();
  return (
    <Card title={title} kicker={kicker} padded={false} className={cx('work-today', className)} data-testid="work-today">
      {failed ? (
        <div className="work-error" data-testid="work-today-action-error">
          <InlineError detail={errorDetail(failed)}>{errorText(failed)}</InlineError>
        </div>
      ) : null}
      {partial ? <div className="work-note">{partial}</div> : null}
      {truncated ? <div className="work-note">{truncated}</div> : null}
      <ul className="work-list" aria-label="Việc hôm nay">
        {data.items.map((it) => {
          const key = `${it.kind}-${it.id}`;
          const isTicked = ticked.has(key);
          const pending = isTicked || (postpone.isPending && postpone.variables?.id === it.id);
          return (
            <li key={key} className="work-task" aria-busy={pending || undefined} data-testid="work-task-row" data-kind={it.kind}>
              <input
                type="checkbox"
                className="work-check"
                aria-label={`Xong: ${it.title}`}
                checked={isTicked}
                disabled={pending}
                data-testid="work-task-done"
                onChange={() => {
                  mark(key, true);
                  const opts = { onError: () => mark(key, false) };
                  if (it.kind === 'task') done.mutate(it.id, opts);
                  else keep.mutate(it.id, opts);
                }}
              />
              <div className="work-task__body">
                <span className="work-task__title">{it.title}</span>
                <span className="work-task__meta">
                  <Badge kind="status" tone={it.overdue ? 'bad' : 'warn'}>
                    {dueText(it, now, tz)}
                  </Badge>
                  <span>{it.meta}</span>
                </span>
                <span className="work-task__actions">
                  {it.kind === 'task' ? (
                    <button type="button" className="kit-link work-link" disabled={pending} data-testid="work-task-postpone" onClick={() => postpone.mutate({ id: it.id })}>
                      Dời sang mai
                    </button>
                  ) : null}
                  {it.personId ? (
                    <Link to={profileLink(it.personId)} className="kit-link work-link">
                      Mở hồ sơ
                    </Link>
                  ) : null}
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

// ── Gen đề xuất (gợi ý nhắc việc) ─────────────────────────────────────────────────────────────────────────────────────

export interface Suggestion {
  personId: string;
  name: string;
  /** Tiêu đề việc sẽ tạo. */
  task: string;
  meta: string;
}

/**
 * Khách cần chăm (có mã người) → gợi ý "Nhắc gọi hỏi thăm …". Hàng không có mã người bị bỏ; người ĐÃ có việc/nhắc đang mở
 * (`has_open_task`) cũng bị bỏ — nếu không, tải lại trang là gợi ý lại và Sếp tạo nhắc trùng. `keep`: người vừa được tạo nhắc
 * trong phiên này vẫn giữ dòng ("Đã tạo nhắc") dù máy chủ đã báo họ có việc mở.
 */
export function buildSuggestions(rows: readonly OwnerRelationRow[], keep: ReadonlySet<string> = new Set()): Suggestion[] {
  const out: Suggestion[] = [];
  for (const r of rows) {
    const personId = asText(r?.person_id).trim();
    const name = asText(r?.name).trim();
    if (!personId || !name) continue;
    if (r.has_open_task === true && !keep.has(personId)) continue;
    const value = typeof r.open_value_vnd === 'number' && r.open_value_vnd > 0 ? `còn cơ hội ${moneyText(r.open_value_vnd)}` : '';
    out.push({ personId, name, task: `Gọi hỏi thăm ${name}`, meta: [asText(r.metric_text) || asText(r.subtitle), value].filter(Boolean).join(' · ') });
  }
  return out;
}

export function suggestionRows(q: Pick<UseQueryResult<OwnerRelations>, 'data'>, keep?: ReadonlySet<string>): Suggestion[] {
  return buildSuggestions(Array.isArray(q.data?.items) ? q.data.items : [], keep);
}

export function GenSuggest({ q, className }: { q: UseQueryResult<OwnerRelations>; className?: string }) {
  const create = useCreateReminder();
  const [made, setMade] = useState<ReadonlySet<string>>(new Set());
  const title = 'Gen đề xuất';
  const rows = suggestionRows(q, made);
  if (q.isPending) return <Card title={title} state="loading" className={className} data-testid="work-suggest-loading" />;
  if (q.isError) {
    return (
      <Card title={title} state="error" className={className} data-testid="work-suggest-error">
        <OwnerError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      </Card>
    );
  }
  if (rows.length === 0) {
    return (
      <Card title={title} className={className} data-testid="work-suggest">
        <EmptyState
          icon="ph ph-sparkle"
          title="Gen chưa có gợi ý nhắc việc nào."
          action={
            <Link to={OWNER_PATHS.relations} className="kit-btn kit-btn--secondary">
              Xem Quan hệ
            </Link>
          }
        />
      </Card>
    );
  }
  return (
    <Card title={title} kicker="Sếp bấm mới tạo" padded={false} className={cx('work-suggest', className)} data-testid="work-suggest">
      {create.error ? (
        <div className="work-error" data-testid="work-suggest-error-inline">
          <InlineError detail={errorDetail(create.error)}>{errorText(create.error)}</InlineError>
        </div>
      ) : null}
      <ul className="work-list" aria-label="Gen đề xuất nhắc việc">
        {rows.map((s) => {
          const isMade = made.has(s.personId);
          const pending = create.isPending && create.variables?.subject?.id === s.personId;
          return (
            <li key={s.personId} className="work-suggest__row" data-testid="work-suggest-row">
              <div className="work-suggest__text">
                <span className="work-task__title">{`Nhắc ${s.task.charAt(0).toLocaleLowerCase('vi')}${s.task.slice(1)}`}</span>
                {s.meta ? <span className="work-task__meta">{s.meta}</span> : null}
              </div>
              {isMade ? (
                <span className="work-suggest__done" data-testid="work-suggest-made">
                  Đã tạo nhắc
                </span>
              ) : (
                <button
                  type="button"
                  className="kit-btn kit-btn--secondary work-btn"
                  disabled={pending}
                  aria-busy={pending || undefined}
                  data-testid="work-suggest-create"
                  aria-label={`Tạo nhắc: ${s.task}`}
                  onClick={() =>
                    create.mutate(
                      { title: s.task, subject: { type: 'person', id: s.personId }, due_at: tomorrowMorning() },
                      { onSuccess: () => setMade((cur) => new Set(cur).add(s.personId)) },
                    )
                  }
                >
                  Tạo nhắc
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
