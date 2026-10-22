/**
 * v0.1.54 — Gen hướng dẫn (thẻ "Hôm nay của Sếp"): phần THUẦN (không React, không gọi mạng) để kiểm bằng vitest.
 * Nhãn nút, hành động chỉ đường qua GenDirector, lời điền sẵn cho "Hỏi Gen thêm", mức khẩn → màu, chữ trạng thái.
 * Thẻ KHÔNG tạo hội thoại và KHÔNG gửi câu hỏi: mọi nút chỉ (a) chỉ đường bằng director, (b) ghi một hành động lên
 * `/gen/coach/items/*`, hoặc (c) điền sẵn ô nhập của Gen.
 */
import {
  ApiError,
  resolveTarget,
  type CoachDismissedItem,
  type CoachLesson,
  type CoachLessonStatus,
  type CoachLevel,
  type CoachTip,
  type CoachToday,
  type CoachTodo,
  type CoachTry,
  type CurriculumLesson,
  type UiAction,
} from '@gen-harness/contracts';

/** Khoá cache: việc hôm nay (chấm đỏ + thẻ), cài đặt và lộ trình. */
export const COACH_TODAY_KEY = ['gen', 'coach', 'today'] as const;
export const COACH_PREFS_KEY = ['gen', 'coach', 'prefs'] as const;
export const COACH_CURRICULUM_KEY = ['gen', 'coach', 'curriculum'] as const;

/** Chấm đỏ ở nút Gen: tải khi mở app rồi hỏi lại mỗi 30 phút (chuông `gen.coach` làm mới ngay). */
export const COACH_POLL_MS = 30 * 60_000;

/** Số việc tối đa trên thẻ (máy chủ đã cắt; web cắt thêm một lần phòng máy chủ lệch). */
export const COACH_MAX_TODOS = 3;

/** Tên khối + nhãn thẻ (thuật ngữ cố định của Gen hướng dẫn). */
export const COACH_TITLE = 'Hôm nay của Sếp';
export const COACH_TODO_HEADING = 'Việc cần làm ngay';
export const COACH_TIP_HEADING = 'Sếp biết chưa?';
export const COACH_STABLE_LABEL = 'Hệ thống đã ổn định';
export const COACH_CURRICULUM_TITLE = 'Lộ trình học cùng Gen';
export const COACH_PREFS_TITLE = 'Gen hướng dẫn';
export const COACH_DISMISSED_TITLE = 'Việc Sếp đã chọn không dùng';
/** Câu cuối hộp xác nhận "Không dùng việc này". */
export const COACH_RESTORE_HINT = 'Sếp bật lại được ở Cài đặt › Bộ não AI › Gen hướng dẫn';
/** Mặc định khi máy chủ không gửi `dismiss_warning`. */
export const COACH_DISMISS_FALLBACK = 'Em sẽ không nhắc việc này nữa.';

/** Hoãn tất cả: 1 / 3 / 7 ngày. */
export const COACH_SNOOZE_DAYS = [1, 3, 7] as const;
export type CoachSnoozeDays = (typeof COACH_SNOOZE_DAYS)[number];

/** Câu mẫu tĩnh ở khung Gen khi có việc khẩn (P0/P1) — Sếp bấm thì mới gửi (đây là câu hỏi thường cho Gen). */
export const COACH_URGENT_PROMPT = 'Hôm nay em cần làm gì?';

// ── khoá hành động trên máy chủ ────────────────────────────────────────────────────────────────────────

/** `item_key` lưu CSDL: `todo:<khoá>` | `tip:<key>` | `lesson:<id>` | `card:setup_followup`. */
export const todoItemKey = (todo: Pick<CoachTodo, 'key'>): string => `todo:${todo.key}`;
export const tipItemKey = (tip: Pick<CoachTip, 'key'>): string => `tip:${tip.key}`;
export const lessonItemKey = (lesson: Pick<CoachLesson, 'id'> | Pick<CurriculumLesson, 'id'>): string => `lesson:${lesson.id}`;
export const SETUP_FOLLOWUP_ITEM = 'card:setup_followup';

/**
 * Khoá để "Bật lại" một việc đã tắt: `prefs.dismissed[].key` có thể là khoá việc trần (`boss.hub`) hoặc đã có tiền tố
 * (`todo:boss.hub`) — luôn trả `item_key` đầy đủ.
 */
export function dismissedItemKey(item: Pick<CoachDismissedItem, 'key'>): string {
  return /^(todo|tip|lesson|card):/.test(item.key) ? item.key : `todo:${item.key}`;
}

// ── nút ────────────────────────────────────────────────────────────────────────────────────────────────

export type TodoButtonId = 'show' | 'tomorrow' | 'dismiss';
export type TipButtonId = 'try' | 'understood';
export type LessonButtonId = 'try' | 'understood' | 'ask_more' | 'snooze';

export interface CoachButton<Id extends string> {
  id: Id;
  label: string;
}

export const TODO_LABELS: Record<TodoButtonId, string> = { show: 'Chỉ cho em', tomorrow: 'Để mai', dismiss: 'Không dùng việc này' };

/** Nút của một việc: "Chỉ cho em" · "Để mai" · "Không dùng việc này" (KHÔNG có với việc khẩn P0 — `can_dismiss` false). */
export function todoActions(todo: Pick<CoachTodo, 'can_dismiss'>): CoachButton<TodoButtonId>[] {
  const out: CoachButton<TodoButtonId>[] = [
    { id: 'show', label: TODO_LABELS.show },
    { id: 'tomorrow', label: TODO_LABELS.tomorrow },
  ];
  if (todo.can_dismiss) out.push({ id: 'dismiss', label: TODO_LABELS.dismiss });
  return out;
}

/** Nút của mẹo: "Thử ngay" (khi có `try`) · "Đã hiểu". */
export function tipActions(tip: Pick<CoachTip, 'try'>): CoachButton<TipButtonId>[] {
  const out: CoachButton<TipButtonId>[] = [];
  if (tip.try?.target) out.push({ id: 'try', label: 'Thử ngay' });
  out.push({ id: 'understood', label: 'Đã hiểu' });
  return out;
}

/** Nút của bài học: "Làm thử" (khi có `try`) · "Đã hiểu" · "Hỏi Gen thêm" · "Hoãn". */
export function lessonActions(lesson: Pick<CoachLesson, 'try'>): CoachButton<LessonButtonId>[] {
  const out: CoachButton<LessonButtonId>[] = [];
  if (lesson.try?.target) out.push({ id: 'try', label: 'Làm thử' });
  out.push({ id: 'understood', label: 'Đã hiểu' }, { id: 'ask_more', label: 'Hỏi Gen thêm' }, { id: 'snooze', label: 'Hoãn' });
  return out;
}

/** Câu điền sẵn vào ô nhập của Gen (KHÔNG gửi): "Giải thích thêm cho em bài «<tên bài>»". */
export function askMorePrompt(lesson: Pick<CoachLesson, 'title'> | Pick<CurriculumLesson, 'title'>): string {
  return `Giải thích thêm cho em bài «${lesson.title}»`;
}

// ── chỉ đường (GenDirector) ────────────────────────────────────────────────────────────────────────────

/** Bước chỉ đường: hành động UI của director, hoặc đi thẳng tới một đường dẫn trong app (`link` của sự cố). */
export type CoachStep = UiAction | { type: 'go'; to: string };

/** `/system?tab=storage#x` hay `/guide/viec-sep` — chỉ nhận đường dẫn trong app (bắt đầu bằng một dấu `/`). */
export function isAppLink(link: string | null | undefined): link is string {
  return typeof link === 'string' && /^\/(?!\/)/.test(link);
}

/**
 * Các bước để chỉ tới một mục tiêu registry: mở màn + tab của mục tiêu rồi làm sáng. Mục tiêu dạng dòng
 * (`guide.item.do:7`) giữ nguyên id có dòng. Mục tiêu lạ (máy chủ mới hơn web) ⇒ rỗng.
 */
export function targetSteps(target: string | null | undefined, message: string): CoachStep[] {
  if (!target) return [];
  const reg = resolveTarget(target);
  if (!reg) return [];
  const nav: UiAction = { type: 'navigate', screen: reg.screen, ...(reg.params ? { params: { ...reg.params } } : {}) };
  return [nav, { type: 'highlight', target, message }];
}

/**
 * "Chỉ cho em": có `target` hợp lệ ⇒ mở màn rồi làm sáng đúng phần tử; không thì có `link` ⇒ đi tới link; không có gì ⇒ rỗng
 * (nút vẫn bấm được nhưng không làm gì — web báo "Em chưa có chỗ để chỉ").
 */
export function showMe(todo: Pick<CoachTodo, 'target' | 'link' | 'title'>): CoachStep[] {
  const viaTarget = targetSteps(todo.target, todo.title);
  if (viaTarget.length) return viaTarget;
  return isAppLink(todo.link) ? [{ type: 'go', to: todo.link }] : [];
}

/** "Thử ngay" / "Làm thử": làm sáng `try.target` với lời nhắn `try.label`. */
export function tryMe(t: Pick<CoachTry, 'target' | 'label'> | null | undefined): CoachStep[] {
  return t ? targetSteps(t.target, t.label) : [];
}

// ── trạng thái ─────────────────────────────────────────────────────────────────────────────────────────

/** Có việc khẩn (P0/P1) ⇒ khung Gen thêm câu mẫu "Hôm nay em cần làm gì?". */
export function hasUrgent(today: Pick<CoachToday, 'todos'> | null | undefined): boolean {
  return !!today?.todos?.some((t) => t.level === 'P0' || t.level === 'P1');
}

/** Chấm đỏ ở nút Gen: còn nội dung mới Sếp chưa thấy và hướng dẫn đang bật. */
export function showCoachDot(today: Pick<CoachToday, 'enabled' | 'unseen'> | null | undefined): boolean {
  return !!today && today.enabled !== false && today.unseen === true;
}

/** Vẽ thẻ khi hướng dẫn đang bật (Sếp chưa Tắt hướng dẫn). Đang "Hoãn tất cả" thì máy chủ trả danh sách rỗng, thẻ gọn lại. */
export function coachCardVisible(today: CoachToday | null | undefined): today is CoachToday {
  return !!today && today.enabled !== false;
}

/** Việc trên thẻ: tối đa 3, giữ thứ tự của máy chủ (mức khẩn trước). */
export function cardTodos(today: Pick<CoachToday, 'todos'>): CoachTodo[] {
  return (Array.isArray(today.todos) ? today.todos : []).slice(0, COACH_MAX_TODOS);
}

/** Thẻ không còn gì để Sếp làm: không việc, không mẹo, không bài học. */
export function coachIsEmpty(today: CoachToday): boolean {
  return cardTodos(today).length === 0 && !today.tip && !today.lesson;
}

export interface LevelMeta {
  label: string;
  /** Màu theo token sẵn có (`--color-bad`, `--color-warn`…) — vẽ bằng thuộc tính `data-level`. */
  tone: 'bad' | 'warn' | 'info' | 'neutral';
}

export const LEVEL_META: Record<CoachLevel, LevelMeta> = {
  P0: { label: 'Khẩn', tone: 'bad' },
  P1: { label: 'Nên làm sớm', tone: 'warn' },
  P2: { label: 'Gợi ý', tone: 'info' },
  P3: { label: 'Khi rảnh', tone: 'neutral' },
};

export function levelMeta(level: string): LevelMeta {
  return LEVEL_META[level as CoachLevel] ?? LEVEL_META.P3;
}

/** "Bài học hôm nay · 3/19". */
export function lessonHeading(lesson: Pick<CoachLesson, 'k' | 'total'>): string {
  return `Bài học hôm nay · ${lesson.k}/${lesson.total}`;
}

/** "Đã đạt x/N việc bắt buộc" — N lấy từ máy chủ (`progress.required_total`), không ghi cứng. */
export function requiredText(done: number, total: number): string {
  return `Đã đạt ${done}/${total} việc bắt buộc`;
}

const LESSON_STATUS_TEXT: Record<CoachLessonStatus, string> = {
  new: 'Chưa học',
  shown: 'Đã gặp',
  understood: 'Đã hiểu',
  snoozed: 'Đang hoãn',
  done: 'Đã làm',
};

export function lessonStatusText(status: string): string {
  return LESSON_STATUS_TEXT[status as CoachLessonStatus] ?? LESSON_STATUS_TEXT.new;
}

/** Bài đã xong (Đã hiểu / Đã làm) — Lộ trình đánh dấu tick. */
export function lessonFinished(status: string): boolean {
  return status === 'understood' || status === 'done';
}

/** "Học lại" chỉ có với bài đã xong hoặc đang hoãn (bài mới chưa cần). */
export function canRelearn(status: string): boolean {
  return lessonFinished(status) || status === 'snoozed';
}

/** Số bài đã xong trong lộ trình (để hiện "6/19 bài"). */
export function curriculumDone(lessons: ReadonlyArray<Pick<CurriculumLesson, 'status'>> | undefined): number {
  return (lessons ?? []).filter((l) => lessonFinished(l.status)).length;
}

/** Giờ 0..23 → "07:00" (giờ yên lặng theo múi giờ tổ chức). */
export function hourLabel(h: number): string {
  const n = Number.isFinite(h) ? Math.min(23, Math.max(0, Math.trunc(h))) : 0;
  return `${String(n).padStart(2, '0')}:00`;
}

export const HOURS: readonly number[] = Array.from({ length: 24 }, (_, i) => i);

/** Hoãn tất cả còn hiệu lực: "Đang hoãn tới 12:00 12/10". */
export function snoozeActive(iso: string | null | undefined, now: number = Date.now()): boolean {
  if (!iso) return false;
  const t = Date.parse(iso);
  return Number.isFinite(t) && t > now;
}

// ── lỗi ────────────────────────────────────────────────────────────────────────────────────────────────

/**
 * Máy chủ chưa có Gen hướng dẫn (404, bản api cũ hơn web) hoặc người xem không phải Owner (403) ⇒ ẩn thẻ lặng lẽ, không
 * vẽ lỗi. Mọi lỗi khác (mạng, 5xx) ⇒ thẻ báo câu thân thiện + "Chi tiết kỹ thuật".
 */
export function coachUnavailable(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 403 || error.status === 404);
}
