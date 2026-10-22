import { useEffect, useId, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { CoachItemAction, CoachLesson, CoachTip, CoachToday, CoachTodo } from '@gen-harness/contracts';
import { Button, Dialog, Icon, IconButton } from '@gen-harness/ui';
import { errorDetail, errorText } from '../lib/errorText';
import { fmtDM, fmtHM } from '../lib/format';
import { navigateTo } from '../lib/navigation';
import { useOrgTimezone } from '../lib/permissions';
import { useMe } from '../lib/queries';
import { toast } from '../lib/toast';
import { useIsMobile } from '../lib/useMediaQuery';
import { InlineError } from '../screens/common';
import {
  COACH_DISMISS_FALLBACK,
  COACH_RESTORE_HINT,
  COACH_SNOOZE_DAYS,
  COACH_STABLE_LABEL,
  COACH_TIP_HEADING,
  COACH_TITLE,
  COACH_TODO_HEADING,
  askMorePrompt,
  cardTodos,
  coachCardVisible,
  coachIsEmpty,
  coachUnavailable,
  lessonActions,
  lessonHeading,
  lessonItemKey,
  levelMeta,
  requiredText,
  showMe,
  snoozeActive,
  tipActions,
  tipItemKey,
  todoActions,
  todoItemKey,
  tryMe,
  type CoachStep,
} from './coachModel';
import { markCoachShown, useCoachAudience, useCoachItemAction, useCoachToday, usePatchCoachPrefs } from './coachQueries';
import { executeUiAction } from './director';
import { useGenStore } from './genStore';

/** Câu thân thiện khi không tải được thẻ (lý do kỹ thuật nằm trong "Chi tiết kỹ thuật"). */
const LOAD_ERROR = 'Em chưa tải được việc hôm nay của Sếp — thử lại sau ít phút.';
const NOTHING_TO_SHOW = 'Em chưa có chỗ để chỉ cho việc này — Sếp mở trang liên quan trong thanh bên nhé.';

const COLLAPSE_KEY = 'gh-coach-collapsed';

function readCollapsed(userId: string): boolean {
  try {
    const raw = window.localStorage.getItem(COLLAPSE_KEY);
    const map = raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
    return map[userId] === true;
  } catch {
    return false;
  }
}

function writeCollapsed(userId: string, value: boolean): void {
  try {
    const raw = window.localStorage.getItem(COLLAPSE_KEY);
    const map = (raw ? JSON.parse(raw) : {}) as Record<string, unknown>;
    window.localStorage.setItem(COLLAPSE_KEY, JSON.stringify({ ...map, [userId]: value }));
  } catch {
    /* localStorage bị chặn: thẻ vẫn chạy, chỉ không nhớ lựa chọn thu gọn */
  }
}

/** Chạy các bước chỉ đường tuần tự: hành động UI của director, hoặc đi thẳng tới đường dẫn trong app. */
async function runSteps(steps: CoachStep[]): Promise<void> {
  for (const step of steps) {
    if (step.type === 'go') navigateTo(step.to);
    else await executeUiAction(step);
  }
}

/**
 * v0.1.54 — thẻ "Hôm nay của Sếp" ở ĐẦU khung Gen (Gen hướng dẫn): ≤ 3 "Việc cần làm ngay" (màu theo mức), một mẹo "Sếp
 * biết chưa?", một "Bài học hôm nay · k/19", nhãn "Hệ thống đã ổn định" và chân thẻ "Hoãn tất cả 1/3/7 ngày · Tắt hướng dẫn".
 * Chỉ vẽ khi người xem là Owner và Gen bật. Thẻ KHÔNG tạo hội thoại và KHÔNG gửi câu hỏi: nút chỉ chỉ đường (director), ghi
 * một hành động lên `/gen/coach/items/*` hoặc điền sẵn ô nhập ("Hỏi Gen thêm"). Mỗi lần khung mở và thẻ hiện thật (không
 * thu gọn) gọi `today?mark_shown=1` MỘT lần để tắt chấm đỏ.
 */
export function CoachTodayCard() {
  const audience = useCoachAudience();
  if (!audience) return null;
  return <CoachCard />;
}

function CoachCard() {
  const qc = useQueryClient();
  const me = useMe();
  const userId = me.data?.id ?? '';
  const q = useCoachToday(true);
  const [collapsed, setCollapsedState] = useState(() => readCollapsed(userId));
  const rootRef = useRef<HTMLElement>(null);
  const marked = useRef(false);
  const headId = useId();

  const setCollapsed = (v: boolean) => {
    setCollapsedState(v);
    if (userId) writeCollapsed(userId, v);
  };

  const today = q.data;
  const visible = coachCardVisible(today);
  const showing = visible && !collapsed;

  // Thẻ thật sự hiện (khung mở + không thu gọn): đánh dấu đã thấy MỘT lần mỗi lần mở khung.
  useEffect(() => {
    if (!showing || marked.current) return;
    marked.current = true;
    void markCoachShown(qc);
  }, [showing, qc]);

  // `/overview?gen=coach`: cuộn tới thẻ và mở rộng (một lần rồi hạ cờ — kể cả khi thẻ không vẽ được).
  const focusReq = useGenStore((s) => s.coachFocus);
  useEffect(() => {
    if (!focusReq || q.isPending) return;
    useGenStore.getState().setCoachFocus(false);
    const el = rootRef.current;
    if (!el) return;
    setCollapsedState(false);
    el.scrollIntoView?.({ block: 'start' });
    el.focus?.({ preventScroll: true });
  }, [focusReq, q.isPending]);

  // Đã có dữ liệu thì dùng tiếp dù một lần tải lại bị lỗi; chưa có gì mà lỗi ⇒ báo.
  if (!today && q.isError) {
    // 403 (không phải Owner) / 404 (máy chủ chưa có Gen hướng dẫn) ⇒ ẩn lặng lẽ.
    if (coachUnavailable(q.error)) return null;
    return (
      <section className="coach-card coach-card--error" ref={rootRef} tabIndex={-1} data-gen-target="gen.coach.card" aria-label={COACH_TITLE}>
        <InlineError detail={errorDetail(q.error)}>{LOAD_ERROR}</InlineError>
        <Button variant="secondary" size="sm" icon="ph ph-arrow-clockwise" loading={q.isFetching} onClick={() => void q.refetch()}>
          Thử lại
        </Button>
      </section>
    );
  }
  if (!visible || !today) return null;

  return (
    <section
      className="coach-card"
      ref={rootRef}
      tabIndex={-1}
      data-gen-target="gen.coach.card"
      data-collapsed={collapsed || undefined}
      aria-labelledby={headId}
      aria-live="off"
    >
      <div className="coach-card__head">
        <span className="coach-card__icon" aria-hidden>
          <Icon name="ph ph-chalkboard-teacher" size={14} />
        </span>
        <div className="coach-card__titles">
          <h2 className="coach-card__title" id={headId}>
            {COACH_TITLE}
          </h2>
          <span className="coach-card__meta" data-testid="coach-required">
            {requiredText(today.progress.required_done, today.progress.required_total)}
          </span>
        </div>
        <IconButton
          icon={collapsed ? 'ph ph-caret-down' : 'ph ph-caret-up'}
          label={collapsed ? 'Mở rộng thẻ Hôm nay của Sếp' : 'Thu gọn thẻ Hôm nay của Sếp'}
          variant="ghost"
          className="coach-card__toggle"
          aria-expanded={!collapsed}
          onClick={() => setCollapsed(!collapsed)}
        />
      </div>
      {collapsed ? null : <CoachBody today={today} userId={userId} />}
    </section>
  );
}

function CoachBody({ today, userId }: { today: CoachToday; userId: string }) {
  const tz = useOrgTimezone();
  const mobile = useIsMobile();
  const act = useCoachItemAction();
  const patch = usePatchCoachPrefs();
  const [notice, setNotice] = useState<string | null>(null);
  const [dismissing, setDismissing] = useState<CoachTodo | null>(null);

  const todos = cardTodos(today);
  const snoozed = snoozeActive(today.snoozed_until);
  const busy = act.isPending || patch.isPending;

  const guide = (steps: CoachStep[]) => {
    setNotice(null);
    if (steps.length === 0) {
      setNotice(NOTHING_TO_SHOW);
      return;
    }
    // Điện thoại: khung Gen phủ cả màn — đi tới trang thì thu khung lại để Sếp thấy trang (làm sáng thì khung tự ẩn).
    if (mobile && steps.every((s) => s.type === 'go') && userId) useGenStore.getState().setOpen(userId, false);
    void runSteps(steps);
  };
  const post = (itemKey: string, body: CoachItemAction) => {
    setNotice(null);
    act.reset();
    act.mutate({ itemKey, body });
  };

  return (
    <div className="coach-card__body">
      {act.isError ? <InlineError detail={errorDetail(act.error)}>{errorText(act.error)}</InlineError> : null}
      {patch.isError ? <InlineError detail={errorDetail(patch.error)}>{errorText(patch.error)}</InlineError> : null}
      {notice ? (
        <p className="coach-card__note" role="status">
          {notice}
        </p>
      ) : null}

      {today.progress.stable ? (
        <p className="coach-card__stable" data-testid="coach-stable">
          <Icon name="ph ph-check-circle" size={13} /> {COACH_STABLE_LABEL}
        </p>
      ) : null}

      {snoozed ? (
        <div className="coach-card__snoozed" data-testid="coach-snoozed">
          <span>
            Đang hoãn tới {fmtHM(today.snoozed_until, tz)} {fmtDM(today.snoozed_until, tz)} — em sẽ nhắc lại sau.
          </span>
          <Button variant="secondary" size="sm" disabled={busy} onClick={() => patch.mutate({ snooze_all_days: 0 })}>
            Bỏ hoãn
          </Button>
        </div>
      ) : (
        <>
          {todos.length > 0 ? (
            <Block heading={COACH_TODO_HEADING} testId="coach-todos">
              <ul className="coach-card__list">
                {todos.map((t) => (
                  <TodoItem
                    key={t.key}
                    todo={t}
                    busy={busy}
                    onShow={() => guide(showMe(t))}
                    onTomorrow={() => post(todoItemKey(t), { action: 'snooze', days: 1 })}
                    onDismiss={() => setDismissing(t)}
                  />
                ))}
              </ul>
            </Block>
          ) : null}

          {today.tip ? (
            <Block heading={COACH_TIP_HEADING} testId="coach-tip">
              <TipItem
                tip={today.tip}
                busy={busy}
                onTry={() => guide(tryMe(today.tip?.try))}
                onUnderstood={() => today.tip && post(tipItemKey(today.tip), { action: 'understood' })}
              />
            </Block>
          ) : null}

          {today.lesson ? (
            <Block heading={lessonHeading(today.lesson)} testId="coach-lesson">
              <LessonItem
                lesson={today.lesson}
                busy={busy}
                onTry={() => guide(tryMe(today.lesson?.try))}
                onUnderstood={() => today.lesson && post(lessonItemKey(today.lesson), { action: 'understood' })}
                onAskMore={() => today.lesson && useGenStore.getState().setComposerDraft(askMorePrompt(today.lesson))}
                onSnooze={() => today.lesson && post(lessonItemKey(today.lesson), { action: 'snooze', days: 1 })}
              />
            </Block>
          ) : null}

          {coachIsEmpty(today) && !today.progress.stable ? (
            <p className="coach-card__note">Hôm nay chưa có việc nào cần Sếp làm.</p>
          ) : null}
        </>
      )}

      <div className="coach-card__foot">
        {snoozed ? null : (
          <div className="coach-card__foot-group" role="group" aria-label="Hoãn tất cả">
            <span className="coach-card__foot-label">Hoãn tất cả</span>
            {COACH_SNOOZE_DAYS.map((d) => (
              <Button
                key={d}
                variant="ghost"
                size="sm"
                aria-label={`Hoãn tất cả ${d} ngày`}
                disabled={busy}
                onClick={() => patch.mutate({ snooze_all_days: d })}
              >
                {d} ngày
              </Button>
            ))}
          </div>
        )}
        <Button
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={() =>
            patch.mutate(
              { enabled: false },
              { onSuccess: () => toast(`Đã tắt Gen hướng dẫn — ${COACH_RESTORE_HINT}`, 'ok') },
            )
          }
        >
          Tắt hướng dẫn
        </Button>
      </div>

      <DismissDialog todo={dismissing} onClose={() => setDismissing(null)} />
    </div>
  );
}

function Block({ heading, testId, children }: { heading: string; testId: string; children: React.ReactNode }) {
  const id = useId();
  return (
    <div className="coach-card__block" role="group" aria-labelledby={id} data-testid={testId}>
      <h3 className="coach-card__heading" id={id}>
        {heading}
      </h3>
      {children}
    </div>
  );
}

function TodoItem({
  todo,
  busy,
  onShow,
  onTomorrow,
  onDismiss,
}: {
  todo: CoachTodo;
  busy: boolean;
  onShow: () => void;
  onTomorrow: () => void;
  onDismiss: () => void;
}) {
  const titleId = useId();
  const meta = levelMeta(todo.level);
  const handlers = { show: onShow, tomorrow: onTomorrow, dismiss: onDismiss } as const;
  return (
    <li className="coach-card__item" data-level={todo.level} data-testid="coach-todo" aria-labelledby={titleId}>
      <div className="coach-card__item-head">
        <span className="coach-card__chip" data-tone={meta.tone}>
          {todo.level} · {meta.label}
        </span>
        <span className="coach-card__item-title" id={titleId}>
          {todo.title}
        </span>
      </div>
      <p className="coach-card__why">{todo.why}</p>
      <div className="coach-card__actions">
        {todoActions(todo).map((b) => (
          <Button key={b.id} variant={b.id === 'show' ? 'primary' : b.id === 'dismiss' ? 'ghost' : 'secondary'} size="sm" disabled={busy} onClick={handlers[b.id]}>
            {b.label}
          </Button>
        ))}
      </div>
    </li>
  );
}

function TipItem({ tip, busy, onTry, onUnderstood }: { tip: CoachTip; busy: boolean; onTry: () => void; onUnderstood: () => void }) {
  const handlers = { try: onTry, understood: onUnderstood } as const;
  return (
    <div className="coach-card__item coach-card__item--plain">
      <div className="coach-card__item-head">
        <Icon name="ph ph-lightbulb" size={13} />
        <span className="coach-card__item-title">{tip.title}</span>
      </div>
      <p className="coach-card__why">{tip.body}</p>
      <div className="coach-card__actions">
        {tipActions(tip).map((b) => (
          <Button key={b.id} variant={b.id === 'try' ? 'primary' : 'secondary'} size="sm" disabled={busy} onClick={handlers[b.id]}>
            {b.label}
          </Button>
        ))}
      </div>
    </div>
  );
}

function LessonItem({
  lesson,
  busy,
  onTry,
  onUnderstood,
  onAskMore,
  onSnooze,
}: {
  lesson: CoachLesson;
  busy: boolean;
  onTry: () => void;
  onUnderstood: () => void;
  onAskMore: () => void;
  onSnooze: () => void;
}) {
  const handlers = { try: onTry, understood: onUnderstood, ask_more: onAskMore, snooze: onSnooze } as const;
  return (
    <div className="coach-card__item coach-card__item--plain">
      <div className="coach-card__item-head">
        <Icon name="ph ph-book-open" size={13} />
        <span className="coach-card__item-title">{lesson.title}</span>
      </div>
      <p className="coach-card__why">{lesson.body}</p>
      <div className="coach-card__actions">
        {lessonActions(lesson).map((b) => (
          <Button key={b.id} variant={b.id === 'try' ? 'primary' : 'secondary'} size="sm" disabled={busy && b.id !== 'ask_more'} onClick={handlers[b.id]}>
            {b.label}
          </Button>
        ))}
      </div>
    </div>
  );
}

/**
 * "Không dùng việc này" → hộp xác nhận (Dialog của dự án): câu cảnh báo của máy chủ (`dismiss_warning`) + câu cách bật
 * lại. Chỉ khi Sếp xác nhận mới gửi `{action:'dismiss', confirm:true}`; việc khẩn P0 máy chủ từ chối (422) — web hiện
 * câu thân thiện + "Chi tiết kỹ thuật", không bao giờ đối tượng thô.
 */
function DismissDialog({ todo, onClose }: { todo: CoachTodo | null; onClose: () => void }) {
  const dismiss = useCoachItemAction();
  const close = () => {
    if (dismiss.isPending) return;
    dismiss.reset();
    onClose();
  };
  const warning = todo?.dismiss_warning && todo.dismiss_warning.trim() ? todo.dismiss_warning : COACH_DISMISS_FALLBACK;
  return (
    <Dialog
      open={!!todo}
      onClose={close}
      title="Không dùng việc này?"
      kicker={todo?.title}
      actions={
        <>
          <Button variant="secondary" disabled={dismiss.isPending} onClick={close}>
            Giữ lại
          </Button>
          <Button
            variant="primary"
            icon="ph ph-bell-slash"
            loading={dismiss.isPending}
            onClick={() => {
              if (!todo) return;
              dismiss.mutate({ itemKey: todoItemKey(todo), body: { action: 'dismiss', confirm: true } }, { onSuccess: () => onClose() });
            }}
          >
            Xác nhận tắt việc này
          </Button>
        </>
      }
    >
      <p className="coach-card__warning" data-testid="coach-dismiss-warning">
        {warning}
      </p>
      <p className="coach-card__restore">{COACH_RESTORE_HINT}</p>
      {dismiss.isError ? <InlineError detail={errorDetail(dismiss.error)}>{errorText(dismiss.error)}</InlineError> : null}
    </Dialog>
  );
}
