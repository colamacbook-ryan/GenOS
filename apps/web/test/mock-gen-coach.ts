/**
 * Mock v0.1.54 — Gen hướng dẫn (`/api/v1/gen/coach/*`), theo HỢP ĐỒNG API (gói api-coach làm thật):
 *
 * - `GET /gen/coach/today[?mark_shown=1]` → CoachToday. CHỈ Owner (vai trò khác 403 FORBIDDEN 'Chỉ Sếp (Owner) dùng được Gen hướng dẫn').
 * - `POST /gen/coach/items/{item_key}` {action: understood|snooze|done|dismiss|restore, days?: 1|3|7, confirm?: true} → 204.
 *   `dismiss` BẮT BUỘC `confirm: true` (thiếu ⇒ 422 COACH_CONFIRM_REQUIRED); việc P0 ⇒ 422 COACH_DISMISS_NOT_ALLOWED; khoá lạ ⇒ 404
 *   COACH_ITEM_UNKNOWN; `days` ngoài 1|3|7 ⇒ 422 VALIDATION_ERROR.
 * - `GET|PATCH /gen/coach/prefs`, `GET /gen/coach/curriculum` (19 bài). 0 lời gọi model, 0 ghi `gen_messages`; không trả detail/message/email/token.
 *
 * TRẠNG THÁI Ở BỘ NHỚ MODULE (không nằm trong từng lần `createMock`): mọi context trình duyệt của MỘT lần chạy dùng chung một máy chủ mock nên
 * bài đã "Đã hiểu" ở context 1 thì context 2 cũng không thấy. `resetCoachMock()` (gọi mỗi lần `createMock`, tức mỗi `POST /__mock/reset`)
 * đưa về "máy mới 0/1" (v0.1.55, Thiết lập gọn): việc `boss.ai` (P1, dòng bắt buộc duy nhất — "Kiểm tra nguồn AI chạy được") rồi gợi ý
 * `boss.hub` (P3, không chuông), mẹo `telegram_briefing`, bài học N01 (1/19), `unseen`. `boss.remote` (P3) chỉ hiện khi đã mời nhân viên
 * (hook `scenario {staff: true}`); Gen-hub/Facebook/Telegram/Google/Claude KHÔNG còn là việc P1.
 *
 * Việc `boss.*` suy từ bảng "Việc Sếp cần làm" của mock-boss-checks (`opts.boss`) nên làm xong một dòng là việc biến mất.
 * Hook e2e `POST /api/v1/__mock/p3/genCoach/{hook}`:
 *   scenario {extras?: string[], stable?: boolean, unseen?: boolean, staff?: boolean} — thêm việc mẫu (health.channel.down P0, model.missing P0
 *                                  [ẩn boss.ai như máy chủ], backup.unset, hub.token_expiring, drafts.pending, followup [việc gộp followup.5–10, v0.1.70]), ép nhãn "ổn định", ép chấm
 *                                  đỏ, `staff` = đã mời nhân viên (bật việc boss.remote);
 *   notify {} — chuông `gen.coach` cho Owner (link theo Gen bật/tắt) + bật `unseen`;
 *   state {} — đọc trạng thái nội bộ (kiểm tra; `nonOwnerCalls` = lời gọi bị 403 của vai trò khác Owner); reset {} — về máy mới.
 */
import type {
  CoachItemAction,
  CoachLesson,
  CoachLessonStatus,
  CoachPrefs,
  CoachProgress,
  CoachTip,
  CoachToday,
  CoachTodo,
  Curriculum,
  CurriculumLesson,
} from '../../../packages/contracts/src/gen';
import type { BossOverview } from '../../../packages/contracts/src/bossChecks';
import type { P2Ctx } from './mock-phase2';

export interface GenCoachOptions {
  /** Bảng "Việc Sếp cần làm" hiện tại (mock-boss-checks `overview`) — `boss.<key>` biến mất khi dòng đạt. */
  boss?: () => BossOverview;
  /** Một thông báo (chuông) cho mọi Owner. */
  notifyOwners?: (kind: string, title: string, body: string, link: string | null) => void;
  /** Gen đang bật? — quyết định link của chuông (`/overview?gen=coach` hay `/guide/viec-sep`). */
  genEnabled?: () => boolean;
}

const TOTAL_LESSONS = 19;
const DAY_MS = 86_400_000;

interface Def {
  title: string;
  why: string;
  level: 'P0' | 'P1' | 'P2' | 'P3';
  target?: string;
  link?: string;
  warning?: string;
}

/**
 * Việc theo dòng "Việc Sếp cần làm" (đúng thứ tự bảng quy tắc của máy chủ). v0.1.55: `ai` là P1 duy nhất (dòng bắt buộc); `hub` chỉ là
 * gợi ý P3; `remote` P3 chỉ khi đã mời nhân viên.
 */
const BOSS_DEFS: Record<string, Def> = {
  ai: {
    title: 'Kiểm tra nguồn AI chạy được',
    why: 'Em đã có nguồn AI nhưng chưa gọi thật được lần nào — Sếp bấm Kiểm tra để em chắc là trả lời và lọc tin được.',
    level: 'P1',
    // v0.1.68 (F029): như máy chủ (`TODO_LINKS`) — dẫn thẳng màn Thiết lập nguồn AI, không còn làm sáng thẻ Kết nối.
    link: '/setup?buoc=nguon-ai',
    warning: 'Nếu tắt, Gen sẽ không nhắc kiểm tra nguồn AI nữa — tới lúc cần trả lời mới biết lỗi.',
  },
  hub: {
    title: 'Nối Gen-hub',
    why: 'Gen-hub là cầu để em đọc lịch, mail và Kho dữ liệu của Sếp — không nối thì em vẫn làm việc bình thường.',
    level: 'P3',
    target: 'boss_checks.row.hub',
    warning: 'Nếu tắt, Gen sẽ không nhắc nối Gen-hub nữa — Gen không đọc được lịch, mail và Kho dữ liệu của Sếp.',
  },
  remote: {
    title: 'Thử mở Console từ điện thoại',
    why: 'Đã có người trong đội dùng Console — mở được từ máy khác thì mọi người xử lý việc khi đi ngoài.',
    level: 'P3',
    target: 'boss_checks.row.remote',
    warning: 'Nếu tắt, Gen sẽ không nhắc kiểm tra truy cập từ xa nữa.',
  },
};
const BOSS_ORDER = ['ai', 'hub', 'remote'];

/** Việc mẫu thêm qua hook `scenario` (khoá việc thật của máy chủ). */
const EXTRA_DEFS: Record<string, Def> = {
  'health.channel.down': {
    title: 'Kênh Zalo đã ngắt kết nối',
    why: 'Tin nhắn Zalo mới sẽ không vào hệ thống tới khi Sếp quét lại mã QR.',
    level: 'P0',
    link: '/connections',
  },
  'model.missing': {
    title: 'Chưa có model cho Gen',
    why: 'Không có model thì Gen và việc sàng lọc không chạy được.',
    level: 'P0',
    // v0.1.59: như máy chủ (`TODO_LINKS`) — đích là MỘT TRANG (Thiết lập nguồn AI), không phải phần tử cần làm sáng ⇒ `link`, không `target`.
    link: '/setup?buoc=nguon-ai',
  },
  'backup.unset': {
    title: 'Chưa đặt lịch sao lưu tự động',
    why: 'Để dữ liệu của Sếp luôn có bản sao khi máy chủ gặp sự cố.',
    level: 'P1',
    target: 'system.backup.schedule',
    warning: 'Nếu tắt, Gen sẽ không nhắc đặt lịch sao lưu nữa — dữ liệu có thể mất khi máy chủ hỏng.',
  },
  'hub.token_expiring': {
    title: 'Token Gen-hub sắp hết hạn',
    why: 'Hết hạn thì Gen không đọc được Kho dữ liệu, lịch và mail.',
    level: 'P1',
    target: 'mcp.hub_link.token',
    warning: 'Nếu tắt, Gen sẽ không nhắc thay token Gen-hub nữa.',
  },
  'drafts.pending': {
    title: '3 bản nháp đang chờ Sếp duyệt',
    why: 'Nháp chưa duyệt thì tin chưa được gửi đi.',
    level: 'P2',
    target: 'workbench.drafts',
    warning: 'Nếu tắt, Gen sẽ không nhắc duyệt nháp nữa.',
  },
  // v0.1.70 (F031/F138): như máy chủ — các việc thiết lập 5–10 gộp thành MỘT việc trỏ thẻ "Việc thiết lập tiếp".
  followup: {
    title: 'Việc thiết lập tiếp',
    why: 'Còn 3 việc tuỳ chọn, không bắt buộc — làm khi sẵn sàng, xong sẽ tự biến mất khỏi thẻ “Việc thiết lập tiếp” ở Tổng quan.',
    level: 'P3',
    target: 'overview.setup_followup',
    warning: 'Tắt thì em không nhắc các việc thiết lập tuỳ chọn nữa — thẻ “Việc thiết lập tiếp” ở Tổng quan vẫn còn đó khi Sếp cần.',
  },
};

const TIP: CoachTip = {
  key: 'telegram_briefing',
  title: 'Bản tin Telegram 07:30 và 17:30',
  body: 'Gen gửi bản tin việc, khách nóng và sự cố vào Telegram mỗi sáng và chiều — Sếp không cần mở Console.',
  try: { label: 'Bật bản tin ở thẻ Telegram', target: 'system.channels.telegram' },
};

/** 19 bài: N01..N10 + G05..G11, G13, G14 (bài sinh từ Hướng dẫn thiết lập). */
const LESSON_DEFS: Array<{ id: string; title: string; body: string; target?: string }> = [
  { id: 'N01', title: 'Hỏi Gen thay vì tự dò menu', body: 'Sếp gõ câu hỏi bằng tiếng Việt, Gen tra số liệu và mở đúng màn. Gen chỉ đề xuất, Sếp xác nhận thì Gen mới làm.', target: 'help.ask_gen' },
  { id: 'N02', title: 'Bản tin Gen sáng và chiều', body: 'Mỗi ngày hai lần Gen tóm tắt việc tới hạn, khách nóng, nháp chờ duyệt và sự cố vào một hội thoại riêng.' },
  { id: 'N03', title: 'Gen nhớ quy ước của Sếp', body: 'Dặn Gen “nhớ giúp em …” để Gen đề xuất ghi nhớ một quy ước; Sếp xác nhận mới lưu.', target: 'system.brain.memory' },
  { id: 'N04', title: 'Duyệt nháp ở Bàn làm việc', body: 'Tin gửi ra ngoài luôn chờ Sếp duyệt ở Bàn làm việc. Gen chỉ soạn nháp, không tự gửi.', target: 'workbench.drafts' },
  { id: 'N05', title: 'Chuông thông báo', body: 'Chuông ở góc trên báo sự cố và bản tin. Bấm một thông báo để mở đúng trang liên quan.' },
  { id: 'N06', title: 'Mã PIN bảo vệ thao tác nhạy cảm', body: 'Sao lưu, đổi tài khoản CLI, mời người dùng… cần mã PIN. PIN không thay thế mật khẩu đăng nhập.', target: 'account.pin' },
  { id: 'N07', title: 'Sức khoẻ hệ thống', body: 'Thẻ Sức khoẻ hệ thống cho biết kênh, bộ xử lý nền, sao lưu và ổ đĩa có ổn không.', target: 'overview.health' },
  { id: 'N08', title: 'Chi phí AI và trần ngân sách', body: 'Đặt trần chi phí AI mỗi ngày để không bị vượt ngoài ý muốn; vượt trần Gen báo ngay.', target: 'system.ai_cost' },
  { id: 'N09', title: 'Bản sao ngoài máy', body: 'Cắm ổ USB/NAS vào máy chủ để có thêm một bản sao dữ liệu ngoài máy.', target: 'system.storage.offsite' },
  { id: 'N10', title: 'Truy cập từ xa', body: 'Mở Console từ điện thoại bằng địa chỉ truy cập từ xa; đổi chế độ bằng lệnh genh remote trên máy chủ.', target: 'system.remote_access' },
  { id: 'G05', title: 'Kết nối Zalo / WhatsApp', body: 'Kênh nhắn tin là nguồn dữ liệu đầu tiên của hệ thống; quét mã QR để nối.', target: 'guide.item.do:5' },
  { id: 'G06', title: 'Chọn nhóm cho agent lắng nghe', body: 'Nhóm mới chờ Sếp chọn (chưa lưu tin); Sếp chọn nhóm nào agent được nghe.', target: 'guide.item.do:6' },
  { id: 'G07', title: 'Bật sàng lọc dữ liệu', body: 'Tin thô được lọc và chấm điểm trước khi vào kho sạch.', target: 'guide.item.do:7' },
  { id: 'G08', title: 'Tạo agent đầu tiên', body: 'Agent là nhân viên AI làm việc thay Sếp trên các kênh.', target: 'guide.item.do:8' },
  { id: 'G09', title: 'Đặt mức tự trị', body: 'Mức tự trị quyết định agent được tự làm đến đâu trước khi chờ Sếp duyệt.', target: 'guide.item.do:9' },
  { id: 'G10', title: 'Mời nhân viên', body: 'Mời người dùng với mật khẩu tạm; mỗi vai trò chỉ thấy phần việc của mình.', target: 'guide.item.do:10' },
  { id: 'G11', title: 'Sao lưu tự động', body: 'Đặt lịch sao lưu để dữ liệu luôn có bản dự phòng.', target: 'guide.item.do:11' },
  { id: 'G13', title: 'Kết nối Facebook', body: 'Đăng nhập tài khoản Facebook để Gen đọc bình luận và tin nhắn.', target: 'guide.item.do:13' },
  { id: 'G14', title: 'Nối Gen-hub', body: 'Nối Gen-hub để Gen đọc Kho dữ liệu, lịch, mail và Drive.', target: 'guide.item.do:14' },
];

interface State {
  enabled: boolean;
  bell: boolean;
  lessonsPerDay: number;
  quietStart: number;
  quietEnd: number;
  snoozeUntil: string | null;
  followupSnoozedUntil: string | null;
  /** Khoá việc (`boss.hub`…) đã "Không dùng". */
  dismissed: Map<string, { level: Def['level']; title: string }>;
  /** Khoá việc đã "Để mai". */
  tomorrow: Set<string>;
  tipsUnderstood: Set<string>;
  lessonStatus: Map<string, CoachLessonStatus>;
  /** Số bài đã xong/hoãn hôm nay (đủ `lessonsPerDay` thì hôm nay hết bài). */
  lessonsHandledToday: number;
  seen: boolean;
  forceUnseen: boolean | null;
  forceStable: boolean;
  extras: string[];
  /** v0.1.55: đã mời nhân viên (người dùng khác Owner đang hoạt động) ⇒ mới nhắc `boss.remote`. */
  staff: boolean;
  markShownCalls: number;
  /** Lời gọi `/gen/coach/*` từ vai trò khác Owner (bị 403) — e2e kiểm bằng 0: giao diện không được gọi thay nhân viên. */
  nonOwnerCalls: string[];
}

function fresh(): State {
  return {
    enabled: true,
    bell: true,
    lessonsPerDay: 1,
    quietStart: 21,
    quietEnd: 7,
    snoozeUntil: null,
    followupSnoozedUntil: null,
    dismissed: new Map(),
    tomorrow: new Set(),
    tipsUnderstood: new Set(),
    lessonStatus: new Map(),
    lessonsHandledToday: 0,
    seen: false,
    forceUnseen: null,
    forceStable: false,
    extras: [],
    staff: false,
    markShownCalls: 0,
    nonOwnerCalls: [],
  };
}

let S: State = fresh();

/** Về "máy mới 0/1" — gọi mỗi lần `createMock` (mỗi `POST /__mock/reset`). */
export function resetCoachMock(): void {
  S = fresh();
}

const KNOWN_TODO = new Set<string>([...BOSS_ORDER.map((k) => `boss.${k}`), ...Object.keys(EXTRA_DEFS)]);

const statusOf = (id: string): CoachLessonStatus => S.lessonStatus.get(id) ?? 'new';
const finished = (st: CoachLessonStatus) => st === 'understood' || st === 'done';

function bossDone(opts: GenCoachOptions): { done: number; total: number; ok: Set<string> } {
  const o = opts.boss?.();
  if (!o) return { done: 0, total: 1, ok: new Set() };
  return { done: o.required_done, total: o.required_total, ok: new Set(o.rows.filter((r) => r.done).map((r) => r.key)) };
}

function todoFrom(key: string, d: Def): CoachTodo {
  return {
    key,
    level: d.level,
    title: d.title,
    why: d.why,
    ...(d.target ? { target: d.target } : {}),
    ...(d.link ? { link: d.link } : {}),
    can_dismiss: d.level !== 'P0',
    ...(d.level !== 'P0' && d.warning ? { dismiss_warning: d.warning } : {}),
  };
}

function allTodos(opts: GenCoachOptions): CoachTodo[] {
  const { ok } = bossDone(opts);
  const out: Array<{ key: string; def: Def }> = [];
  for (const k of S.extras) if (EXTRA_DEFS[k]) out.push({ key: k, def: EXTRA_DEFS[k] });
  for (const k of BOSS_ORDER) {
    if (ok.has(k)) continue;
    if (k === 'ai' && S.extras.includes('model.missing')) continue; // dedupe với model.missing (P0) như máy chủ
    if (k === 'remote' && !S.staff) continue; // chỉ nhắc khi đã mời nhân viên
    out.push({ key: `boss.${k}`, def: BOSS_DEFS[k] });
  }
  const rank = { P0: 0, P1: 1, P2: 2, P3: 3 } as const;
  return out
    .filter((x) => !S.dismissed.has(x.key) && !S.tomorrow.has(x.key))
    // v0.1.70: "Để sau 7 ngày" của thẻ "Việc thiết lập tiếp" ẩn luôn mọi việc P3 của Gen (như máy chủ).
    .filter((x) => !(x.def.level === 'P3' && !!S.followupSnoozedUntil && Date.parse(S.followupSnoozedUntil) > Date.now()))
    .sort((a, b) => rank[a.def.level] - rank[b.def.level])
    .map((x) => todoFrom(x.key, x.def));
}

function currentLesson(): CoachLesson | null {
  if (S.lessonsPerDay <= 0 || S.lessonsHandledToday >= S.lessonsPerDay) return null;
  const i = LESSON_DEFS.findIndex((l) => {
    const st = statusOf(l.id);
    return st === 'new' || st === 'shown';
  });
  if (i < 0) return null;
  const d = LESSON_DEFS[i];
  return {
    id: d.id,
    k: i + 1,
    total: TOTAL_LESSONS,
    title: d.title,
    body: d.body,
    ...(d.target ? { try: { label: d.title, target: d.target } } : {}),
    status: statusOf(d.id),
  };
}

function progress(opts: GenCoachOptions, todos: CoachTodo[]): CoachProgress {
  const { done, total } = bossDone(opts);
  const stable = S.forceStable || (done >= total && todos.length === 0);
  return {
    required_done: done,
    required_total: total,
    lessons_done: LESSON_DEFS.filter((l) => finished(statusOf(l.id))).length,
    lessons_total: TOTAL_LESSONS,
    stable,
    stable_since: stable ? new Date(Date.now() - 3 * DAY_MS).toISOString() : null,
  };
}

function snoozing(): boolean {
  return !!S.snoozeUntil && Date.parse(S.snoozeUntil) > Date.now();
}

function buildToday(opts: GenCoachOptions): CoachToday {
  const quiet = snoozing();
  const todos = quiet ? [] : allTodos(opts).slice(0, 3);
  const tip = quiet || S.tipsUnderstood.has(TIP.key) ? null : TIP;
  const lesson = quiet ? null : currentLesson();
  const unseen = S.enabled && (S.forceUnseen ?? !S.seen);
  return {
    date: new Date().toISOString().slice(0, 10),
    enabled: S.enabled,
    snoozed_until: quiet ? S.snoozeUntil : null,
    todos: S.enabled ? todos : [],
    tip: S.enabled ? tip : null,
    lesson: S.enabled ? lesson : null,
    progress: progress(opts, allTodos(opts)),
    unseen,
  };
}

function buildPrefs(): CoachPrefs {
  return {
    enabled: S.enabled,
    bell: S.bell,
    lessons_per_day: S.lessonsPerDay,
    quiet_start: S.quietStart,
    quiet_end: S.quietEnd,
    snooze_until: snoozing() ? S.snoozeUntil : null,
    followup_snoozed_until: S.followupSnoozedUntil && Date.parse(S.followupSnoozedUntil) > Date.now() ? S.followupSnoozedUntil : null,
    dismissed: [...S.dismissed.entries()].map(([key, v]) => ({ key, level: v.level, title: v.title })),
  };
}

function buildCurriculum(): Curriculum {
  const lessons: CurriculumLesson[] = LESSON_DEFS.map((d, i) => ({
    id: d.id,
    k: i + 1,
    title: d.title,
    body: d.body,
    ...(d.target ? { try: { label: d.title, target: d.target } } : {}),
    status: statusOf(d.id),
  }));
  return { total: TOTAL_LESSONS, lessons };
}

const ACTIONS = new Set(['understood', 'snooze', 'done', 'dismiss', 'restore']);

export function createMock(opts: GenCoachOptions = {}) {
  resetCoachMock();

  const validation = (ctx: P2Ctx, errors: Record<string, string>) => ctx.problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors });

  /** Xử lý một hành động; trả `true` khi đã trả lời (lỗi), `false` khi thành công (204 do nơi gọi). */
  function applyItem(ctx: P2Ctx, rawKey: string, body: Partial<CoachItemAction>): boolean {
    const [kind, ...rest] = rawKey.split(':');
    const id = rest.join(':');
    const action = body.action;
    if (!action || !ACTIONS.has(action)) return validation(ctx, { action: 'Hành động không hợp lệ' });
    if (action === 'snooze' && ![1, 3, 7].includes(Number(body.days))) return validation(ctx, { days: 'Chỉ nhận 1, 3 hoặc 7 ngày' });
    const unknown = () => ctx.problem(404, 'COACH_ITEM_UNKNOWN', 'Em không biết việc này');

    if (kind === 'todo') {
      if (!KNOWN_TODO.has(id)) return unknown();
      const def = id.startsWith('boss.') ? BOSS_DEFS[id.slice(5)] : EXTRA_DEFS[id];
      if (action === 'dismiss') {
        if (!def || def.level === 'P0') return ctx.problem(422, 'COACH_DISMISS_NOT_ALLOWED', 'Việc khẩn cấp không tắt được — Sếp xử lý giúp em nhé');
        if (body.confirm !== true) return ctx.problem(422, 'COACH_CONFIRM_REQUIRED', 'Sếp xác nhận giúp em trước khi tắt việc này');
        S.dismissed.set(id, { level: def.level, title: def.title });
        return false;
      }
      if (action === 'snooze') {
        S.tomorrow.add(id);
        return false;
      }
      if (action === 'restore') {
        S.dismissed.delete(id);
        S.tomorrow.delete(id);
        return false;
      }
      return validation(ctx, { action: 'Việc cần làm chỉ nhận Để mai, Không dùng hoặc Bật lại' });
    }
    if (kind === 'tip') {
      if (id !== TIP.key) return unknown();
      if (action === 'understood' || action === 'done') S.tipsUnderstood.add(id);
      else if (action === 'restore') S.tipsUnderstood.delete(id);
      return false;
    }
    if (kind === 'lesson') {
      if (!LESSON_DEFS.some((l) => l.id === id)) return unknown();
      if (action === 'dismiss') return validation(ctx, { action: 'Bài học không tắt được — dùng Hoãn' });
      const next: CoachLessonStatus = action === 'understood' ? 'understood' : action === 'done' ? 'done' : action === 'snooze' ? 'snoozed' : 'new';
      const was = statusOf(id);
      S.lessonStatus.set(id, next);
      if (action !== 'restore' && (was === 'new' || was === 'shown')) S.lessonsHandledToday += 1;
      return false;
    }
    if (rawKey === 'card:setup_followup') {
      if (action === 'snooze') S.followupSnoozedUntil = new Date(Date.now() + Number(body.days) * DAY_MS).toISOString();
      else if (action === 'restore') S.followupSnoozedUntil = null;
      else return validation(ctx, { action: 'Thẻ này chỉ nhận Để sau hoặc Bật lại' });
      return false;
    }
    return unknown();
  }

  function patchPrefs(ctx: P2Ctx): boolean {
    const b = ctx.body;
    const errors: Record<string, string> = {};
    const int = (v: unknown) => typeof v === 'number' && Number.isInteger(v);
    if ('lessons_per_day' in b && !(int(b.lessons_per_day) && (b.lessons_per_day as number) >= 0 && (b.lessons_per_day as number) <= 2)) errors.lessons_per_day = 'Từ 0 đến 2 bài mỗi ngày';
    for (const f of ['quiet_start', 'quiet_end'] as const) {
      if (f in b && !(int(b[f]) && (b[f] as number) >= 0 && (b[f] as number) <= 23)) errors[f] = 'Giờ từ 0 đến 23';
    }
    if ('snooze_all_days' in b && ![0, 1, 3, 7].includes(Number(b.snooze_all_days))) errors.snooze_all_days = 'Chỉ nhận 0, 1, 3 hoặc 7 ngày';
    for (const f of ['enabled', 'bell'] as const) if (f in b && typeof b[f] !== 'boolean') errors[f] = 'Chỉ nhận bật hoặc tắt';
    if (Object.keys(errors).length) return validation(ctx, errors);
    if (typeof b.enabled === 'boolean') S.enabled = b.enabled;
    if (typeof b.bell === 'boolean') S.bell = b.bell;
    if (typeof b.lessons_per_day === 'number') S.lessonsPerDay = b.lessons_per_day;
    if (typeof b.quiet_start === 'number') S.quietStart = b.quiet_start;
    if (typeof b.quiet_end === 'number') S.quietEnd = b.quiet_end;
    if ('snooze_all_days' in b) {
      const d = Number(b.snooze_all_days);
      S.snoozeUntil = d > 0 ? new Date(Date.now() + d * DAY_MS).toISOString() : null;
    }
    return ctx.reply(200, buildPrefs());
  }

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p } = ctx;
    if (p !== '/gen/coach' && !p.startsWith('/gen/coach/')) return false;
    if (ctx.role !== 'owner') {
      S.nonOwnerCalls.push(`${ctx.role} ${m} ${p}`);
      return ctx.problem(403, 'FORBIDDEN', 'Chỉ Sếp (Owner) dùng được Gen hướng dẫn');
    }

    if (p === '/gen/coach/today' && m === 'GET') {
      if (ctx.url.searchParams.get('mark_shown') === '1') {
        S.markShownCalls += 1;
        S.seen = true;
        S.forceUnseen = null;
        const cur = currentLesson();
        if (cur && cur.status === 'new') S.lessonStatus.set(cur.id, 'shown');
      }
      return ctx.reply(200, buildToday(opts));
    }
    if (p === '/gen/coach/prefs' && m === 'GET') return ctx.reply(200, buildPrefs());
    if (p === '/gen/coach/prefs' && m === 'PATCH') return patchPrefs(ctx);
    if (p === '/gen/coach/curriculum' && m === 'GET') return ctx.reply(200, buildCurriculum());
    const mm = /^\/gen\/coach\/items\/([^/]+)$/.exec(p);
    if (mm && m === 'POST') {
      let key = mm[1];
      try {
        key = decodeURIComponent(key);
      } catch {
        /* giữ nguyên */
      }
      if (applyItem(ctx, key, ctx.body as Partial<CoachItemAction>)) return true;
      return ctx.reply(204);
    }
    return ctx.problem(404, 'NOT_FOUND', 'Không tồn tại');
  }

  const hooks = {
    scenario: (b: unknown) => {
      const x = (b ?? {}) as { extras?: string[]; stable?: boolean; unseen?: boolean; staff?: boolean };
      if (Array.isArray(x.extras)) S.extras = x.extras.filter((k) => k in EXTRA_DEFS);
      if (typeof x.staff === 'boolean') S.staff = x.staff;
      if (typeof x.stable === 'boolean') S.forceStable = x.stable;
      if (typeof x.unseen === 'boolean') S.forceUnseen = x.unseen;
      return { extras: S.extras, stable: S.forceStable, unseen: S.forceUnseen, staff: S.staff };
    },
    notify: () => {
      S.seen = false;
      S.forceUnseen = null;
      const link = opts.genEnabled?.() === false ? '/guide/viec-sep' : '/overview?gen=coach';
      opts.notifyOwners?.('gen.coach', 'Gen hướng dẫn có việc cho Sếp', 'Có việc cần làm ngay và một bài học hôm nay.', link);
      return { link };
    },
    state: () => ({
      enabled: S.enabled,
      seen: S.seen,
      markShownCalls: S.markShownCalls,
      nonOwnerCalls: [...S.nonOwnerCalls],
      snoozeUntil: S.snoozeUntil,
      tipsUnderstood: [...S.tipsUnderstood],
      dismissed: [...S.dismissed.keys()],
      tomorrow: [...S.tomorrow],
      lessons: Object.fromEntries(S.lessonStatus),
    }),
    reset: () => {
      resetCoachMock();
      return null;
    },
  } as Record<string, (...args: never[]) => unknown>;

  return { handle, hooks, dispose: () => {} };
}
