/**
 * v0.1.54 — Gen hướng dẫn (thẻ "Hôm nay của Sếp", chấm đỏ, Cài đặt, Lộ trình học, hoãn "Việc thiết lập tiếp"):
 * - coachModel (thuần): nút theo mức, chỉ đường bằng director, câu điền sẵn "Hỏi Gen thêm", việc khẩn;
 * - CoachTodayCard: 3 khối, nhãn ổn định, chân thẻ, hộp xác nhận "Không dùng việc này", mark_shown đúng 1 lần mỗi lần mở,
 *   lỗi tải thân thiện + "Chi tiết kỹ thuật", chỉ Owner & Gen bật;
 * - GenToggle: chấm đỏ theo `unseen`, không gọi API khi không phải Owner;
 * - GenCoachCard / CurriculumCard / SetupFollowUp / NeedsBossStrip;
 * - uiStore migrate bỏ `followUpHiddenByUser`; hợp đồng gen.ts khớp mock; BOSS_ROW_TARGETS khớp ROWS của api.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { CoachLesson, CoachPrefs, CoachTip, CoachToday, CoachTodo, Curriculum, SetupFollowUpItem } from '@gen-harness/contracts';
import { screenHref } from '../../src/gen/director';
import {
  COACH_PREFS_KEY,
  COACH_TODAY_KEY,
  COACH_URGENT_PROMPT,
  askMorePrompt,
  coachUnavailable,
  dismissedItemKey,
  hasUrgent,
  lessonActions,
  lessonHeading,
  lessonItemKey,
  showCoachDot,
  showMe,
  tipActions,
  todoActions,
  todoItemKey,
  tryMe,
} from '../../src/gen/coachModel';
import { CoachTodayCard } from '../../src/gen/CoachTodayCard';
import { GenToggle } from '../../src/gen/GenToggle';
import { GenPanel } from '../../src/gen/GenPanel';
import { useGenStore } from '../../src/gen/genStore';
import { BOSS_ROW_TARGETS, bossRowTarget } from '../../src/guide/bossChecksModel';
import { CurriculumCard } from '../../src/help/CurriculumCard';
import { setNavigator } from '../../src/lib/navigation';
import { qk } from '../../src/lib/queries';
import { queryClient } from '../../src/lib/queryClient';
import { migrateUiPrefs, useUiStore } from '../../src/lib/uiStore';
import { NeedsBossStrip } from '../../src/screens/queue/NeedsBossStrip';
import { SetupFollowUp } from '../../src/screens/queue/SetupFollowUp';
import { GenCoachCard } from '../../src/screens/system/GenCoachCard';
import { createMock as createCoachMock, resetCoachMock } from '../mock-gen-coach';
import type { P2Ctx } from '../mock-phase2';

// ── dữ liệu mẫu ────────────────────────────────────────────────────────────────────────────────────────

const ME = (role = 'owner', gen = true) => ({
  id: 'u1', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A', role: { code: role, name: role },
  org: { id: 'o1', name: 'Genesis', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: {}, must_change_password: false,
  features: { gen },
});

const todo = (key: string, level: CoachTodo['level'], over: Partial<CoachTodo> = {}): CoachTodo => ({
  key, level, title: `Việc ${key}`, why: `Lý do ${key}`, can_dismiss: level !== 'P0',
  ...(level !== 'P0' ? { dismiss_warning: `Cảnh báo tắt ${key}` } : {}), ...over,
});
const TIP: CoachTip = { key: 'telegram_briefing', title: 'Bản tin Telegram', body: 'Gen gửi bản tin mỗi sáng.', try: { label: 'Bật bản tin', target: 'system.channels.telegram' } };
const LESSON: CoachLesson = { id: 'N01', k: 1, total: 19, title: 'Hỏi Gen thay vì dò menu', body: 'Sếp gõ câu hỏi, Gen mở đúng màn.', try: { label: 'Hỏi Gen', target: 'help.ask_gen' }, status: 'new' };
const TODAY = (over: Partial<CoachToday> = {}): CoachToday => ({
  date: '2026-10-10', enabled: true, snoozed_until: null,
  todos: [todo('boss.hub', 'P1', { target: 'boss_checks.row.hub' }), todo('boss.facebook', 'P1'), todo('boss.agy', 'P1')],
  tip: TIP, lesson: LESSON,
  progress: { required_done: 0, required_total: 1, lessons_done: 0, lessons_total: 19, stable: false, stable_since: null },
  unseen: true, ...over,
});
const PREFS = (over: Partial<CoachPrefs> = {}): CoachPrefs => ({
  enabled: true, bell: true, lessons_per_day: 1, quiet_start: 21, quiet_end: 7, snooze_until: null, followup_snoozed_until: null, dismissed: [], ...over,
});

// ── máy chủ giả ─────────────────────────────────────────────────────────────────────────────────────────

interface Call { method: string; url: string; body: unknown }
const calls: Call[] = [];
let today: CoachToday = TODAY();
let prefs: CoachPrefs = PREFS();
let curriculum: Curriculum = { total: 19, lessons: [] };
let failToday: { status: number; body: unknown } | null = null;
let failItem: { status: number; body: unknown } | null = null;

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const coachCalls = (pred: (c: Call) => boolean = () => true) => calls.filter((c) => c.url.includes('/gen/coach/') && pred(c));
const markShownCalls = () => coachCalls((c) => c.method === 'GET' && c.url.includes('mark_shown=1'));

function stubApi() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : null });
      if (url.includes('/gen/coach/today')) {
        if (failToday) return json(failToday.status, failToday.body);
        // Như máy chủ thật: đánh dấu đã thấy thì các lần GET sau báo unseen=false.
        if (url.includes('mark_shown=1')) today = { ...today, unseen: false };
        return json(200, today);
      }
      if (url.includes('/gen/coach/items/')) return failItem ? json(failItem.status, failItem.body) : new Response(null, { status: 204 });
      if (url.includes('/gen/coach/prefs')) {
        if (method === 'PATCH') {
          const body = JSON.parse(String(init?.body ?? '{}')) as Partial<CoachPrefs>;
          prefs = { ...prefs, ...body };
          return json(200, prefs);
        }
        return json(200, prefs);
      }
      if (url.includes('/gen/coach/curriculum')) return json(200, curriculum);
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
}

const navigations: string[] = [];

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(qk.me, ME());
  calls.length = 0;
  navigations.length = 0;
  today = TODAY();
  prefs = PREFS();
  curriculum = { total: 19, lessons: [] };
  failToday = null;
  failItem = null;
  useGenStore.setState({ openByUser: {}, messages: [], busy: false, spotlight: null, coachFocus: false, composerDraft: null });
  setNavigator((to) => navigations.push(to));
  stubApi();
  window.history.pushState({}, '', '/overview');
  try {
    window.localStorage.clear();
  } catch {
    /* jsdom luôn có localStorage */
  }
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

function wrap(ui: React.ReactNode) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

// ── coachModel ──────────────────────────────────────────────────────────────────────────────────────────

describe('coachModel (thuần)', () => {
  it('nút của việc: P0 KHÔNG có "Không dùng việc này"; P1/P3 có', () => {
    expect(todoActions(todo('model.missing', 'P0')).map((b) => b.label)).toEqual(['Chỉ cho em', 'Để mai']);
    for (const level of ['P1', 'P2', 'P3'] as const) {
      expect(todoActions(todo('x', level)).map((b) => b.label)).toEqual(['Chỉ cho em', 'Để mai', 'Không dùng việc này']);
    }
  });

  it('"Chỉ cho em" với target: mở màn /guide/viec-sep rồi làm sáng boss_checks.row.hub', () => {
    const steps = showMe(todo('boss.hub', 'P1', { target: 'boss_checks.row.hub', title: 'Nối Gen-hub' }));
    expect(steps).toHaveLength(2);
    const [nav, hi] = steps;
    expect(nav).toMatchObject({ type: 'navigate', screen: 'boss_checks' });
    expect(nav.type === 'navigate' && screenHref(nav.screen, nav.params)).toBe('/guide/viec-sep');
    expect(hi).toEqual({ type: 'highlight', target: 'boss_checks.row.hub', message: 'Nối Gen-hub' });
  });

  it('mục tiêu có tab (system.ai_cost) mở đúng tab; mục tiêu dạng dòng guide.item.do:7 dùng dòng 7', () => {
    const ai = showMe(todo('x', 'P1', { target: 'system.ai_cost' }));
    expect(ai[0]).toMatchObject({ type: 'navigate', screen: 'system', params: { tab: 'brain' } });
    const row = showMe(todo('followup.7', 'P3', { target: 'guide.item.do:7' }));
    expect(row[0]).toMatchObject({ type: 'navigate', screen: 'guide' });
    expect(row[1]).toMatchObject({ type: 'highlight', target: 'guide.item.do:7' });
  });

  it('sự cố sức khoẻ chỉ có link ⇒ đi tới link; không có gì để chỉ ⇒ rỗng', () => {
    expect(showMe(todo('health.channel.down', 'P0', { link: '/connections' }))).toEqual([{ type: 'go', to: '/connections' }]);
    expect(showMe(todo('health.x', 'P1', { link: 'https://evil.example/x' }))).toEqual([]);
    expect(showMe(todo('x', 'P1', { target: 'made.up' }))).toEqual([]);
    expect(showMe(todo('x', 'P1'))).toEqual([]);
    // Có cả hai: ưu tiên mục tiêu.
    expect(showMe(todo('x', 'P1', { target: 'help.guide', link: '/help' }))[1]).toMatchObject({ type: 'highlight', target: 'help.guide' });
  });

  it('nút mẹo, bài học, câu điền sẵn "Hỏi Gen thêm"', () => {
    expect(tipActions(TIP).map((b) => b.label)).toEqual(['Thử ngay', 'Đã hiểu']);
    expect(tipActions({ try: null }).map((b) => b.label)).toEqual(['Đã hiểu']);
    expect(lessonActions(LESSON).map((b) => b.label)).toEqual(['Làm thử', 'Đã hiểu', 'Hỏi Gen thêm', 'Hoãn']);
    expect(lessonActions({}).map((b) => b.label)).toEqual(['Đã hiểu', 'Hỏi Gen thêm', 'Hoãn']);
    expect(askMorePrompt({ title: 'Bản tin Gen' })).toBe('Giải thích thêm cho em bài «Bản tin Gen»');
    expect(lessonHeading(LESSON)).toBe('Bài học hôm nay · 1/19');
    expect(tryMe(LESSON.try)[1]).toEqual({ type: 'highlight', target: 'help.ask_gen', message: 'Hỏi Gen' });
    expect(tryMe(null)).toEqual([]);
  });

  it('hasUrgent: chỉ P0/P1; chấm đỏ cần enabled + unseen; khoá hành động', () => {
    expect(hasUrgent(TODAY())).toBe(true);
    expect(hasUrgent(TODAY({ todos: [todo('a', 'P2'), todo('b', 'P3')] }))).toBe(false);
    expect(hasUrgent(TODAY({ todos: [todo('a', 'P0')] }))).toBe(true);
    expect(hasUrgent(null)).toBe(false);
    expect(showCoachDot({ enabled: true, unseen: true })).toBe(true);
    expect(showCoachDot({ enabled: true, unseen: false })).toBe(false);
    expect(showCoachDot({ enabled: false, unseen: true })).toBe(false);
    expect(showCoachDot(undefined)).toBe(false);
    expect(todoItemKey({ key: 'boss.hub' })).toBe('todo:boss.hub');
    expect(lessonItemKey({ id: 'N01' })).toBe('lesson:N01');
    expect(dismissedItemKey({ key: 'boss.hub' })).toBe('todo:boss.hub');
    expect(dismissedItemKey({ key: 'todo:boss.hub' })).toBe('todo:boss.hub');
  });

  it('coachUnavailable: 403/404 ẩn thẻ lặng lẽ, lỗi khác thì không', async () => {
    const { ApiError } = await import('@gen-harness/contracts');
    expect(coachUnavailable(new ApiError(403, { code: 'FORBIDDEN' }))).toBe(true);
    expect(coachUnavailable(new ApiError(404, { code: 'NOT_FOUND' }))).toBe(true);
    expect(coachUnavailable(new ApiError(500, { code: 'INTERNAL' }))).toBe(false);
    expect(coachUnavailable(new Error('x'))).toBe(false);
  });
});

// ── CoachTodayCard ──────────────────────────────────────────────────────────────────────────────────────

describe('CoachTodayCard', () => {
  it('vẽ 3 khối (Việc cần làm ngay, Sếp biết chưa?, Bài học hôm nay · k/19), thứ tự việc của máy chủ và chân thẻ', async () => {
    wrap(<CoachTodayCard />);
    const card = await screen.findByRole('region', { name: 'Hôm nay của Sếp' });
    expect(card).toHaveAttribute('data-gen-target', 'gen.coach.card');
    expect(within(card).getByRole('group', { name: 'Việc cần làm ngay' })).toBeInTheDocument();
    expect(within(card).getByRole('group', { name: 'Sếp biết chưa?' })).toBeInTheDocument();
    expect(within(card).getByRole('group', { name: 'Bài học hôm nay · 1/19' })).toBeInTheDocument();
    expect(within(card).getAllByTestId('coach-todo').map((li) => li.getAttribute('data-level'))).toEqual(['P1', 'P1', 'P1']);
    expect(within(card).getAllByTestId('coach-todo').map((li) => within(li).getByText(/^Việc boss\./).textContent)).toEqual(['Việc boss.hub', 'Việc boss.facebook', 'Việc boss.agy']);
    expect(within(card).getByTestId('coach-required')).toHaveTextContent('Đã đạt 0/1 việc bắt buộc');
    // Mẹo + bài học.
    expect(within(card).getByRole('button', { name: 'Thử ngay' })).toBeInTheDocument();
    for (const name of ['Làm thử', 'Hỏi Gen thêm', 'Hoãn']) expect(within(card).getByRole('button', { name })).toBeInTheDocument();
    expect(within(card).getAllByRole('button', { name: 'Đã hiểu' })).toHaveLength(2);
    // Chân thẻ.
    for (const d of [1, 3, 7]) expect(within(card).getByRole('button', { name: `Hoãn tất cả ${d} ngày` })).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: 'Tắt hướng dẫn' })).toBeInTheDocument();
    expect(within(card).queryByTestId('coach-stable')).toBeNull();
  });

  it('chỉ lấy tối đa 3 việc dù máy chủ trả nhiều hơn; P0 không có nút "Không dùng việc này"', async () => {
    today = TODAY({ todos: [todo('model.missing', 'P0'), todo('a', 'P1'), todo('b', 'P2'), todo('c', 'P3')] });
    wrap(<CoachTodayCard />);
    const items = await screen.findAllByTestId('coach-todo');
    expect(items).toHaveLength(3);
    expect(items.map((li) => li.getAttribute('data-level'))).toEqual(['P0', 'P1', 'P2']);
    expect(within(items[0]).queryByRole('button', { name: 'Không dùng việc này' })).toBeNull();
    expect(within(items[1]).getByRole('button', { name: 'Không dùng việc này' })).toBeInTheDocument();
  });

  it('nhãn "Hệ thống đã ổn định" khi progress.stable', async () => {
    today = TODAY({ todos: [], tip: null, lesson: null, progress: { required_done: 1, required_total: 1, lessons_done: 19, lessons_total: 19, stable: true, stable_since: '2026-10-01T00:00:00Z' } });
    wrap(<CoachTodayCard />);
    expect(await screen.findByTestId('coach-stable')).toHaveTextContent('Hệ thống đã ổn định');
    expect(screen.queryByRole('group', { name: 'Việc cần làm ngay' })).toBeNull();
  });

  it('"Không dùng việc này" mở hộp xác nhận có dismiss_warning + cách bật lại; chỉ khi xác nhận mới gửi confirm:true', async () => {
    const user = userEvent.setup();
    wrap(<CoachTodayCard />);
    const first = (await screen.findAllByTestId('coach-todo'))[0];
    await user.click(within(first).getByRole('button', { name: 'Không dùng việc này' }));
    const dlg = await screen.findByRole('dialog', { name: /Không dùng việc này\?/ });
    expect(within(dlg).getByTestId('coach-dismiss-warning')).toHaveTextContent('Cảnh báo tắt boss.hub');
    expect(dlg).toHaveTextContent('Sếp bật lại được ở Cài đặt › Bộ não AI › Gen hướng dẫn');
    expect(coachCalls((c) => c.url.includes('/items/'))).toHaveLength(0);

    // "Giữ lại" không gửi gì.
    await user.click(within(dlg).getByRole('button', { name: 'Giữ lại' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(coachCalls((c) => c.url.includes('/items/'))).toHaveLength(0);

    await user.click(within((await screen.findAllByTestId('coach-todo'))[0]).getByRole('button', { name: 'Không dùng việc này' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Xác nhận tắt việc này' }));
    await waitFor(() => expect(coachCalls((c) => c.url.includes('/items/'))).toHaveLength(1));
    const post = coachCalls((c) => c.url.includes('/items/'))[0];
    expect(post.method).toBe('POST');
    expect(post.url).toBe('/api/v1/gen/coach/items/todo%3Aboss.hub');
    expect(post.body).toEqual({ action: 'dismiss', confirm: true });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('máy chủ từ chối tắt (422) ⇒ câu thân thiện + "Chi tiết kỹ thuật" trong hộp, không vẽ đối tượng', async () => {
    failItem = { status: 422, body: { status: 422, code: 'COACH_DISMISS_NOT_ALLOWED', title: 'Việc khẩn cấp không tắt được — Sếp xử lý giúp em nhé' } };
    const user = userEvent.setup();
    wrap(<CoachTodayCard />);
    await user.click(within((await screen.findAllByTestId('coach-todo'))[0]).getByRole('button', { name: 'Không dùng việc này' }));
    const dlg = await screen.findByRole('dialog');
    await user.click(within(dlg).getByRole('button', { name: 'Xác nhận tắt việc này' }));
    const alert = await within(dlg).findByRole('alert');
    expect(alert).toHaveTextContent('Việc khẩn cấp không tắt được — Sếp xử lý giúp em nhé');
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(alert).toHaveTextContent('COACH_DISMISS_NOT_ALLOWED');
    expect(dlg.textContent).not.toContain('[object Object]');
  });

  it('mark_shown: gọi ĐÚNG 1 lần mỗi lần mở khung (kể cả khi vẽ lại), lần mở sau gọi lại; thu gọn thì không gọi', async () => {
    const first = wrap(<CoachTodayCard />);
    await screen.findByRole('region', { name: 'Hôm nay của Sếp' });
    await waitFor(() => expect(markShownCalls()).toHaveLength(1));
    // Vẽ lại / làm mới không gọi thêm.
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: COACH_TODAY_KEY });
    });
    first.rerender(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <CoachTodayCard />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await new Promise((r) => setTimeout(r, 30));
    expect(markShownCalls()).toHaveLength(1);
    expect(queryClient.getQueryData<CoachToday>(COACH_TODAY_KEY)?.unseen).toBe(false);

    // Đóng rồi mở lại khung (gỡ + gắn lại thẻ) ⇒ gọi lần nữa.
    first.unmount();
    wrap(<CoachTodayCard />);
    await screen.findByRole('region', { name: 'Hôm nay của Sếp' });
    await waitFor(() => expect(markShownCalls()).toHaveLength(2));

    // Đang thu gọn ⇒ thẻ chưa "thật sự hiện" ⇒ không gọi; mở rộng thì gọi (một lần).
    cleanup();
    window.localStorage.setItem('gh-coach-collapsed', JSON.stringify({ u1: true }));
    const before = markShownCalls().length;
    wrap(<CoachTodayCard />);
    await screen.findByRole('region', { name: 'Hôm nay của Sếp' });
    await new Promise((r) => setTimeout(r, 30));
    expect(markShownCalls()).toHaveLength(before);
    expect(screen.queryByRole('group', { name: 'Việc cần làm ngay' })).toBeNull();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Mở rộng thẻ Hôm nay của Sếp' }));
    await waitFor(() => expect(markShownCalls()).toHaveLength(before + 1));
  });

  it('lỗi tải ⇒ câu thân thiện + "Chi tiết kỹ thuật" (mã), không render đối tượng; 404/403 ⇒ ẩn lặng lẽ', async () => {
    failToday = { status: 500, body: { status: 500, code: 'INTERNAL', title: 'Hệ thống gặp lỗi khi xử lý yêu cầu', detail: { trace: ['a', 'b'] } } };
    queryClient.setDefaultOptions({ queries: { retry: false } });
    const first = wrap(<CoachTodayCard />);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Em chưa tải được việc hôm nay của Sếp');
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(alert).toHaveTextContent('INTERNAL');
    expect(first.container.textContent).not.toContain('[object Object]');
    first.unmount();

    queryClient.clear();
    queryClient.setQueryData(qk.me, ME());
    failToday = { status: 404, body: { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' } };
    const second = wrap(<CoachTodayCard />);
    await waitFor(() => expect(coachCalls()).not.toHaveLength(0));
    await new Promise((r) => setTimeout(r, 30));
    expect(second.container).toBeEmptyDOMElement();
  });

  it('chỉ vẽ khi Owner và Gen bật: vai trò khác hoặc Gen tắt ⇒ không vẽ, không gọi API', async () => {
    queryClient.setQueryData(qk.me, ME('manager', true));
    const a = wrap(<CoachTodayCard />);
    await new Promise((r) => setTimeout(r, 30));
    expect(a.container).toBeEmptyDOMElement();
    expect(coachCalls()).toHaveLength(0);
    a.unmount();
    queryClient.setQueryData(qk.me, ME('owner', false));
    const b = wrap(<CoachTodayCard />);
    await new Promise((r) => setTimeout(r, 30));
    expect(b.container).toBeEmptyDOMElement();
    expect(coachCalls()).toHaveLength(0);
  });

  it('thẻ KHÔNG tạo hội thoại: "Hỏi Gen thêm" chỉ điền sẵn ô nhập; các nút khác không gọi /gen/turns', async () => {
    const user = userEvent.setup();
    wrap(<CoachTodayCard />);
    const lesson = await screen.findByRole('group', { name: 'Bài học hôm nay · 1/19' });
    await user.click(within(lesson).getByRole('button', { name: 'Hỏi Gen thêm' }));
    expect(useGenStore.getState().composerDraft).toBe('Giải thích thêm cho em bài «Hỏi Gen thay vì dò menu»');
    await user.click(within(lesson).getByRole('button', { name: 'Hoãn' }));
    await waitFor(() => expect(coachCalls((c) => c.url.includes('/items/'))).toHaveLength(1));
    expect(coachCalls((c) => c.url.includes('/items/'))[0].body).toEqual({ action: 'snooze', days: 1 });
    expect(calls.filter((c) => c.url.includes('/gen/turns') || c.url.includes('/gen/conversations'))).toHaveLength(0);
  });

  it('GenPanel điền sẵn ô nhập (không gửi) và thêm câu mẫu "Hôm nay em cần làm gì?" khi có việc khẩn', async () => {
    wrap(<GenPanel userId="u1" />);
    expect(await screen.findByRole('button', { name: COACH_URGENT_PROMPT })).toBeInTheDocument();
    const lesson = await screen.findByRole('group', { name: 'Bài học hôm nay · 1/19' });
    await userEvent.setup().click(within(lesson).getByRole('button', { name: 'Hỏi Gen thêm' }));
    const box = screen.getByLabelText('Câu hỏi cho Gen') as HTMLTextAreaElement;
    await waitFor(() => expect(box.value).toBe('Giải thích thêm cho em bài «Hỏi Gen thay vì dò menu»'));
    expect(useGenStore.getState().composerDraft).toBeNull();
    expect(calls.filter((c) => c.url.includes('/gen/turns'))).toHaveLength(0);
  });

  it('GenPanel: không có việc khẩn ⇒ không thêm câu mẫu', async () => {
    today = TODAY({ todos: [todo('a', 'P3')] });
    wrap(<GenPanel userId="u1" />);
    await screen.findByRole('region', { name: 'Hôm nay của Sếp' });
    expect(screen.queryByRole('button', { name: COACH_URGENT_PROMPT })).toBeNull();
  });

  it('"Chỉ cho em" làm sáng đúng mục tiêu (director đặt spotlight) và đi tới màn của mục tiêu', async () => {
    const user = userEvent.setup();
    wrap(<CoachTodayCard />);
    await user.click(within((await screen.findAllByTestId('coach-todo'))[0]).getByRole('button', { name: 'Chỉ cho em' }));
    await waitFor(() => expect(useGenStore.getState().spotlight?.target).toBe('boss_checks.row.hub'));
    expect(useGenStore.getState().spotlight?.message).toBe('Việc boss.hub');
    expect(navigations).toContain('/guide/viec-sep');
  });

  it('việc không có chỗ để chỉ ⇒ báo thân thiện, không làm gì', async () => {
    today = TODAY({ todos: [todo('x', 'P1')] });
    const user = userEvent.setup();
    wrap(<CoachTodayCard />);
    await user.click(within((await screen.findAllByTestId('coach-todo'))[0]).getByRole('button', { name: 'Chỉ cho em' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Em chưa có chỗ để chỉ');
    expect(useGenStore.getState().spotlight).toBeNull();
  });

  it('"Để mai" gửi snooze 1 ngày; "Hoãn tất cả 3 ngày" và "Tắt hướng dẫn" PATCH cài đặt', async () => {
    const user = userEvent.setup();
    wrap(<CoachTodayCard />);
    const items = await screen.findAllByTestId('coach-todo');
    await user.click(within(items[1]).getByRole('button', { name: 'Để mai' }));
    await waitFor(() => expect(coachCalls((c) => c.url.includes('/items/'))).toHaveLength(1));
    expect(coachCalls((c) => c.url.includes('/items/'))[0].url).toBe('/api/v1/gen/coach/items/todo%3Aboss.facebook');
    expect(coachCalls((c) => c.url.includes('/items/'))[0].body).toEqual({ action: 'snooze', days: 1 });

    await user.click(screen.getByRole('button', { name: 'Hoãn tất cả 3 ngày' }));
    await waitFor(() => expect(coachCalls((c) => c.method === 'PATCH')).toHaveLength(1));
    expect(coachCalls((c) => c.method === 'PATCH')[0].body).toEqual({ snooze_all_days: 3 });

    await user.click(screen.getByRole('button', { name: 'Tắt hướng dẫn' }));
    await waitFor(() => expect(coachCalls((c) => c.method === 'PATCH')).toHaveLength(2));
    expect(coachCalls((c) => c.method === 'PATCH')[1].body).toEqual({ enabled: false });
  });

  it('coachFocus (?gen=coach) cuộn tới thẻ, mở rộng và hạ cờ', async () => {
    window.localStorage.setItem('gh-coach-collapsed', JSON.stringify({ u1: true }));
    useGenStore.setState({ coachFocus: true });
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    wrap(<CoachTodayCard />);
    await screen.findByRole('group', { name: 'Việc cần làm ngay' });
    expect(scroll).toHaveBeenCalled();
    expect(useGenStore.getState().coachFocus).toBe(false);
  });
});

// ── GenToggle (chấm đỏ) ─────────────────────────────────────────────────────────────────────────────────

describe('GenToggle — chấm đỏ', () => {
  it('unseen ⇒ chấm đỏ + aria-label có "có việc mới", không tự mở khung; unseen=false ⇒ không chấm', async () => {
    wrap(<GenToggle />);
    expect(await screen.findByTestId('gen-coach-dot')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /có việc mới/ })).toBeInTheDocument();
    expect(useGenStore.getState().openByUser).toEqual({});
    cleanup();
    queryClient.clear();
    queryClient.setQueryData(qk.me, ME());
    today = TODAY({ unseen: false });
    wrap(<GenToggle />);
    await waitFor(() => expect(coachCalls()).not.toHaveLength(0));
    await screen.findByRole('button', { name: 'Hỏi Gen — trợ lý quản trị' });
    expect(screen.queryByTestId('gen-coach-dot')).toBeNull();
  });

  it('hướng dẫn đã tắt (enabled=false) ⇒ không chấm dù unseen', async () => {
    today = TODAY({ enabled: false, unseen: true });
    wrap(<GenToggle />);
    await waitFor(() => expect(coachCalls()).not.toHaveLength(0));
    await screen.findByRole('button', { name: 'Hỏi Gen — trợ lý quản trị' });
    expect(screen.queryByTestId('gen-coach-dot')).toBeNull();
  });

  it('không phải Owner ⇒ không gọi /gen/coach/today', async () => {
    queryClient.setQueryData(qk.me, ME('manager', true));
    wrap(<GenToggle />);
    await screen.findByRole('button', { name: 'Hỏi Gen — trợ lý quản trị' });
    await new Promise((r) => setTimeout(r, 30));
    expect(coachCalls()).toHaveLength(0);
    expect(screen.queryByTestId('gen-coach-dot')).toBeNull();
  });
});

// ── Cài đặt › Gen hướng dẫn ─────────────────────────────────────────────────────────────────────────────

describe('GenCoachCard (Cài đặt › Bộ não AI)', () => {
  it('công tắc, chuông, số bài, giờ yên lặng; danh sách việc đã tắt có "Bật lại" (POST restore, không PIN)', async () => {
    prefs = PREFS({ dismissed: [{ key: 'todo:boss.hub', level: 'P1', title: 'Nối Gen-hub' }] });
    const user = userEvent.setup();
    wrap(<GenCoachCard />);
    const card = await screen.findByRole('region', { name: 'Gen hướng dẫn' });
    expect(card).toHaveAttribute('data-gen-target', 'system.brain.coach');
    expect(await within(card).findByRole('switch', { name: 'Bật hướng dẫn' })).toHaveAttribute('aria-checked', 'true');
    expect(within(card).getByRole('switch', { name: 'Chuông nhắc' })).toBeInTheDocument();
    expect(within(card).getByLabelText('Số bài mỗi ngày')).toHaveValue('1');
    expect(within(card).getByLabelText('Giờ yên lặng từ')).toHaveValue('21');
    expect(within(card).getByLabelText('đến')).toHaveValue('7');

    await user.selectOptions(within(card).getByLabelText('Số bài mỗi ngày'), '2');
    await waitFor(() => expect(coachCalls((c) => c.method === 'PATCH')).toHaveLength(1));
    expect(coachCalls((c) => c.method === 'PATCH')[0].body).toEqual({ lessons_per_day: 2 });
    await user.click(within(card).getByRole('switch', { name: 'Bật hướng dẫn' }));
    await waitFor(() => expect(coachCalls((c) => c.method === 'PATCH')).toHaveLength(2));
    expect(coachCalls((c) => c.method === 'PATCH')[1].body).toEqual({ enabled: false });

    const row = within(card).getByTestId('coach-dismissed-row');
    expect(row).toHaveTextContent('Nối Gen-hub');
    await user.click(within(row).getByRole('button', { name: 'Bật lại' }));
    await waitFor(() => expect(coachCalls((c) => c.url.includes('/items/'))).toHaveLength(1));
    expect(coachCalls((c) => c.url.includes('/items/'))[0].url).toBe('/api/v1/gen/coach/items/todo%3Aboss.hub');
    expect(coachCalls((c) => c.url.includes('/items/'))[0].body).toEqual({ action: 'restore' });
  });

  it('vai trò khác Owner ⇒ không vẽ, không gọi API', async () => {
    queryClient.setQueryData(qk.me, ME('manager', true));
    const { container } = wrap(<GenCoachCard />);
    await new Promise((r) => setTimeout(r, 30));
    expect(container).toBeEmptyDOMElement();
    expect(coachCalls()).toHaveLength(0);
  });
});

// ── Trợ giúp › Lộ trình học cùng Gen ────────────────────────────────────────────────────────────────────

describe('CurriculumCard (Trợ giúp)', () => {
  it('liệt kê 19 bài kèm trạng thái, mở xem nội dung, "Làm thử" chỉ đường, "Học lại" POST restore', async () => {
    const statuses = ['understood', 'shown', 'snoozed', 'done'] as const;
    curriculum = {
      total: 19,
      lessons: Array.from({ length: 19 }, (_, i) => ({
        id: `N${String(i + 1).padStart(2, '0')}`, k: i + 1, title: `Bài ${i + 1}`, body: `Nội dung bài ${i + 1}`,
        try: { label: `Thử bài ${i + 1}`, target: 'help.ask_gen' }, status: i < 4 ? statuses[i] : ('new' as const),
      })),
    };
    const user = userEvent.setup();
    wrap(<CurriculumCard />);
    const card = await screen.findByTestId('help-curriculum');
    expect(card).toHaveAttribute('data-gen-target', 'help.curriculum');
    expect(card).toHaveTextContent('Lộ trình học cùng Gen');
    const rows = await within(card).findAllByTestId('curriculum-lesson');
    expect(rows).toHaveLength(19);
    expect(within(rows[0]).getByText('Đã hiểu')).toBeInTheDocument();
    expect(within(rows[1]).getByText('Đã gặp')).toBeInTheDocument();
    expect(within(rows[2]).getByText('Đang hoãn')).toBeInTheDocument();
    expect(within(rows[3]).getByText('Đã làm')).toBeInTheDocument();
    expect(within(rows[10]).getByText('Chưa học')).toBeInTheDocument();
    expect(card).toHaveTextContent('2/19 bài đã xong');

    // Bài chưa mở: không có nút. Mở bài 11 (mới): có "Làm thử", không có "Học lại".
    expect(within(card).queryByRole('button', { name: 'Làm thử' })).toBeNull();
    await user.click(within(rows[10]).getByRole('button', { name: 'Bài 11' }));
    expect(within(rows[10]).getByText('Nội dung bài 11')).toBeInTheDocument();
    expect(within(rows[10]).getByRole('button', { name: 'Làm thử' })).toBeInTheDocument();
    expect(within(rows[10]).queryByRole('button', { name: 'Học lại' })).toBeNull();

    // Bài 1 (đã hiểu): "Học lại" ⇒ POST restore lesson:N01.
    await user.click(within(rows[0]).getByRole('button', { name: 'Bài 1' }));
    await user.click(within(rows[0]).getByRole('button', { name: 'Học lại' }));
    await waitFor(() => expect(coachCalls((c) => c.url.includes('/items/'))).toHaveLength(1));
    expect(coachCalls((c) => c.url.includes('/items/'))[0].url).toBe('/api/v1/gen/coach/items/lesson%3AN01');
    expect(coachCalls((c) => c.url.includes('/items/'))[0].body).toEqual({ action: 'restore' });

    // "Làm thử" (Gen bật): làm sáng mục tiêu của bài.
    await user.click(within(rows[10]).getByRole('button', { name: 'Làm thử' }));
    await waitFor(() => expect(useGenStore.getState().spotlight?.target).toBe('help.ask_gen'));
  });

  it('vai trò khác Owner ⇒ không vẽ, không gọi API', async () => {
    queryClient.setQueryData(qk.me, ME('manager', false));
    const { container } = wrap(<CurriculumCard />);
    await new Promise((r) => setTimeout(r, 30));
    expect(container).toBeEmptyDOMElement();
    expect(coachCalls()).toHaveLength(0);
  });
});

// ── Tổng quan ───────────────────────────────────────────────────────────────────────────────────────────

describe('Tổng quan', () => {
  const overview = (done: number, total = 1) => ({ rows: [], results: {}, required_done: done, required_total: total });

  it('NeedsBossStrip: "Đã đạt x/N việc bắt buộc → Xem" tới /guide/viec-sep khi x<N; đủ N thì không hiện', async () => {
    // N lấy từ máy chủ (không ghi cứng): 0/1 là máy mới sau Thiết lập gọn; 2/3 chứng minh x/N động.
    queryClient.setQueryData(['boss-checks'], overview(0));
    queryClient.setQueryData(['setup', 'follow-up'], []);
    const zero = wrap(<NeedsBossStrip />);
    const line0 = await screen.findByTestId('boss-progress');
    expect(line0).toHaveTextContent('Đã đạt 0/1 việc bắt buộc');
    zero.unmount();

    queryClient.setQueryData(['boss-checks'], overview(2, 3));
    const first = wrap(<NeedsBossStrip />);
    const line = await screen.findByTestId('boss-progress');
    expect(line).toHaveTextContent('Đã đạt 2/3 việc bắt buộc');
    expect(within(line).getByRole('link', { name: /Xem/ })).toHaveAttribute('href', '/guide/viec-sep');
    first.unmount();

    queryClient.setQueryData(['boss-checks'], overview(1));
    const second = wrap(<NeedsBossStrip />);
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId('boss-progress')).toBeNull();
    second.unmount();

    // Vai trò khác Owner: không hiện (và không gọi /boss-checks).
    queryClient.setQueryData(qk.me, ME('manager', false));
    queryClient.setQueryData(['boss-checks'], overview(0));
    wrap(<NeedsBossStrip />);
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId('boss-progress')).toBeNull();
  });

  it('SetupFollowUp: "Để sau 7 ngày" POST snooze 7 ngày lên máy chủ (card:setup_followup), không còn "Ẩn" ở trình duyệt', async () => {
    const item: SetupFollowUpItem = { n: 5, key: 'k5', title: 'Bước 5', status: 'skipped', done: false };
    queryClient.setQueryData(['setup', 'follow-up'], [item]);
    queryClient.setQueryData(COACH_PREFS_KEY, PREFS());
    const user = userEvent.setup();
    wrap(<SetupFollowUp />);
    expect(screen.queryByRole('button', { name: 'Ẩn' })).toBeNull();
    await user.click(await screen.findByRole('button', { name: 'Để sau 7 ngày' }));
    await waitFor(() => expect(coachCalls((c) => c.url.includes('/items/'))).toHaveLength(1));
    expect(coachCalls((c) => c.url.includes('/items/'))[0].url).toBe('/api/v1/gen/coach/items/card%3Asetup_followup');
    expect(coachCalls((c) => c.url.includes('/items/'))[0].body).toEqual({ action: 'snooze', days: 7 });
    await waitFor(() => expect(screen.queryByText('Việc thiết lập tiếp')).toBeNull());
  });

  it('SetupFollowUp: thẻ là đích của việc gộp ở Hôm nay (v0.1.70, F031/F138) — "Chỉ cho em" mở Tổng quan và làm sáng đúng thẻ', async () => {
    const item: SetupFollowUpItem = { n: 5, key: 'k5', title: 'Bước 5', status: 'skipped', done: false };
    queryClient.setQueryData(['setup', 'follow-up'], [item]);
    queryClient.setQueryData(COACH_PREFS_KEY, PREFS());
    wrap(<SetupFollowUp />);
    await screen.findByRole('button', { name: 'Để sau 7 ngày' });
    expect(document.querySelector('[data-gen-target="overview.setup_followup"]')).not.toBeNull();
    const steps = showMe(todo('followup', 'P3', { target: 'overview.setup_followup' }));
    expect(steps[0]).toMatchObject({ type: 'navigate', screen: 'overview' });
    expect(steps[1]).toMatchObject({ type: 'highlight', target: 'overview.setup_followup' });
  });
});

// ── uiStore ─────────────────────────────────────────────────────────────────────────────────────────────

describe('uiStore — migrate bỏ followUpHiddenByUser', () => {
  it('migrateUiPrefs xoá khoá cũ, giữ lựa chọn khác, chịu được dữ liệu hỏng', () => {
    const out = migrateUiPrefs({ sidebarMode: 'rail', theme: 'dark', followUpHiddenByUser: { u1: [5, 6] }, showEnglish: true });
    expect(out).toEqual({ sidebarMode: 'rail', theme: 'dark' });
    for (const bad of [null, undefined, 'x', 42, [], [1]]) expect(migrateUiPrefs(bad)).toEqual({});
  });

  it('tải bản v2 đã lưu (có followUpHiddenByUser) ⇒ khoá biến mất khỏi store, thanh bên giữ nguyên', async () => {
    window.localStorage.setItem('gh-ui', JSON.stringify({ state: { sidebarMode: 'rail', navOpen: {}, theme: 'light', themeByUser: {}, followUpHiddenByUser: { u1: [5] } }, version: 2 }));
    await useUiStore.persist.rehydrate();
    const s = useUiStore.getState() as unknown as Record<string, unknown>;
    expect(s.sidebarMode).toBe('rail');
    expect(s.theme).toBe('light');
    expect('followUpHiddenByUser' in s).toBe(false);
    expect('hideFollowUp' in s).toBe(false);
    useUiStore.setState({ sidebarMode: 'full', theme: 'system' });
  });
});

// ── hợp đồng ────────────────────────────────────────────────────────────────────────────────────────────

describe('hợp đồng gen.ts ↔ mock-gen-coach', () => {
  interface Reply { status: number; body?: unknown }
  function call(mock: ReturnType<typeof createCoachMock>, method: string, path: string, body: Record<string, unknown> = {}, role = 'owner'): Reply {
    let out: Reply = { status: 0 };
    const ctx = {
      method, path: path.split('?')[0], url: new URL(`http://mock.local/api/v1${path}`), body, perms: {}, role, owner: role === 'owner', userLabel: 'mock',
      reply: (status: number, b?: unknown) => ((out = { status, body: b }), true as const),
      problem: (status: number, code: string, title: string, extra?: Record<string, unknown>) => ((out = { status, body: { status, code, title, ...extra } }), true as const),
      text: () => true as const, needPin: () => false,
    } as unknown as P2Ctx;
    expect(mock.handle(ctx)).toBe(true);
    return out;
  }

  beforeEach(() => resetCoachMock());

  it('máy mới 0/1: việc boss.ai (P1) rồi gợi ý boss.hub (P3), mẹo telegram_briefing, bài N01 1/19, unseen; đủ khoá của CoachToday', () => {
    const mock = createCoachMock();
    const r = call(mock, 'GET', '/gen/coach/today');
    const t = r.body as CoachToday;
    expect(r.status).toBe(200);
    expect(Object.keys(t).sort()).toEqual(['date', 'enabled', 'lesson', 'progress', 'snoozed_until', 'tip', 'todos', 'unseen']);
    // v0.1.55: chỉ dòng bắt buộc (boss.ai) là P1; Gen-hub chỉ là gợi ý P3; Facebook/Telegram/Google/Claude không còn là việc.
    expect(t.todos.map((x) => [x.key, x.level])).toEqual([['boss.ai', 'P1'], ['boss.hub', 'P3']]);
    for (const x of t.todos) {
      expect(typeof x.title).toBe('string');
      expect(typeof x.why).toBe('string');
      expect(x.can_dismiss).toBe(true);
      expect(typeof x.dismiss_warning).toBe('string');
    }
    expect(t.todos[0]).toMatchObject({ title: 'Kiểm tra nguồn AI chạy được', link: '/setup?buoc=nguon-ai' });
    expect(t.todos[1]).toMatchObject({ title: 'Nối Gen-hub', target: 'boss_checks.row.hub' });
    expect(t.tip?.key).toBe('telegram_briefing');
    expect(t.lesson).toMatchObject({ id: 'N01', k: 1, total: 19, status: 'new' });
    expect(t.progress).toEqual({ required_done: 0, required_total: 1, lessons_done: 0, lessons_total: 19, stable: false, stable_since: null });
    expect(t.unseen).toBe(true);
    expect(JSON.stringify(t)).not.toMatch(/token|email|message/i);
  });

  it('boss.remote chỉ hiện khi đã mời nhân viên (scenario staff); model.missing ẩn boss.ai như máy chủ', () => {
    const mock = createCoachMock();
    const keys = () => (call(mock, 'GET', '/gen/coach/today').body as CoachToday).todos.map((x) => x.key);
    expect(keys()).not.toContain('boss.remote');
    mock.hooks.scenario({ staff: true } as never);
    expect(keys()).toEqual(['boss.ai', 'boss.hub', 'boss.remote']);
    mock.hooks.scenario({ extras: ['model.missing'] } as never);
    expect(keys()).toEqual(['model.missing', 'boss.hub', 'boss.remote']);
  });

  it('v0.1.59: model.missing có link /setup?buoc=nguon-ai và không có target ⇒ "Chỉ cho em" đi thẳng tới màn Thiết lập nguồn AI', () => {
    const mock = createCoachMock();
    mock.hooks.scenario({ extras: ['model.missing'] } as never);
    const t = (call(mock, 'GET', '/gen/coach/today').body as CoachToday).todos.find((x) => x.key === 'model.missing')!;
    expect(t.link).toBe('/setup?buoc=nguon-ai');
    expect(t.target ?? null).toBeNull();
    expect(showMe(t)).toEqual([{ type: 'go', to: '/setup?buoc=nguon-ai' }]);
  });

  it('mark_shown tắt unseen; vai trò khác Owner ⇒ 403 FORBIDDEN', () => {
    const mock = createCoachMock();
    expect((call(mock, 'GET', '/gen/coach/today').body as CoachToday).unseen).toBe(true);
    expect((call(mock, 'GET', '/gen/coach/today?mark_shown=1').body as CoachToday).unseen).toBe(false);
    expect((call(mock, 'GET', '/gen/coach/today').body as CoachToday).unseen).toBe(false);
    const denied = call(mock, 'GET', '/gen/coach/today', {}, 'manager');
    expect(denied.status).toBe(403);
    expect(denied.body).toMatchObject({ code: 'FORBIDDEN', title: 'Chỉ Sếp (Owner) dùng được Gen hướng dẫn' });
  });

  it('dismiss bắt buộc confirm; việc P0 ⇒ 422 COACH_DISMISS_NOT_ALLOWED; khoá lạ ⇒ 404 COACH_ITEM_UNKNOWN; days sai ⇒ 422', () => {
    const mock = createCoachMock();
    mock.hooks.scenario({ extras: ['health.channel.down'] } as never);
    const noConfirm = call(mock, 'POST', '/gen/coach/items/todo%3Aboss.ai', { action: 'dismiss' });
    expect(noConfirm.status).toBe(422);
    expect(noConfirm.body).toMatchObject({ code: 'COACH_CONFIRM_REQUIRED', title: 'Sếp xác nhận giúp em trước khi tắt việc này' });
    const p0 = call(mock, 'POST', '/gen/coach/items/todo%3Ahealth.channel.down', { action: 'dismiss', confirm: true });
    expect(p0.status).toBe(422);
    expect(p0.body).toMatchObject({ code: 'COACH_DISMISS_NOT_ALLOWED', title: 'Việc khẩn cấp không tắt được — Sếp xử lý giúp em nhé' });
    const unknown = call(mock, 'POST', '/gen/coach/items/todo%3Ano.such', { action: 'dismiss', confirm: true });
    expect(unknown.status).toBe(404);
    expect(unknown.body).toMatchObject({ code: 'COACH_ITEM_UNKNOWN', title: 'Em không biết việc này' });
    expect(call(mock, 'POST', '/gen/coach/items/todo%3Aboss.ai', { action: 'snooze', days: 2 }).status).toBe(422);
    // Khoá P1 cũ của dòng không còn bắt buộc (facebook, agy…) không còn là việc ⇒ 404.
    expect(call(mock, 'POST', '/gen/coach/items/todo%3Aboss.facebook', { action: 'dismiss', confirm: true }).status).toBe(404);

    const ok = call(mock, 'POST', '/gen/coach/items/todo%3Aboss.ai', { action: 'dismiss', confirm: true });
    expect(ok.status).toBe(204);
    expect((call(mock, 'GET', '/gen/coach/today').body as CoachToday).todos.map((x) => x.key)).toEqual(['health.channel.down', 'boss.hub']);
    const prefsOut = call(mock, 'GET', '/gen/coach/prefs').body as CoachPrefs;
    expect(prefsOut.dismissed).toEqual([{ key: 'boss.ai', level: 'P1', title: 'Kiểm tra nguồn AI chạy được' }]);
    expect(call(mock, 'POST', '/gen/coach/items/todo%3Aboss.ai', { action: 'restore' }).status).toBe(204);
    expect((call(mock, 'GET', '/gen/coach/prefs').body as CoachPrefs).dismissed).toEqual([]);
  });

  it('prefs: PATCH kiểm miền giá trị; curriculum 19 bài; "Đã hiểu" ẩn bài rồi bài kế tiếp hiện (2 bài mỗi ngày)', () => {
    const mock = createCoachMock();
    expect(call(mock, 'PATCH', '/gen/coach/prefs', { lessons_per_day: 3 }).status).toBe(422);
    expect(call(mock, 'PATCH', '/gen/coach/prefs', { quiet_start: 24 }).status).toBe(422);
    expect(call(mock, 'PATCH', '/gen/coach/prefs', { snooze_all_days: 2 }).status).toBe(422);
    const ok = call(mock, 'PATCH', '/gen/coach/prefs', { lessons_per_day: 2, quiet_start: 22, bell: false });
    expect(ok.body).toMatchObject({ lessons_per_day: 2, quiet_start: 22, bell: false, enabled: true });
    expect(Object.keys(ok.body as object).sort()).toEqual(['bell', 'dismissed', 'enabled', 'followup_snoozed_until', 'lessons_per_day', 'quiet_end', 'quiet_start', 'snooze_until']);

    const cur = call(mock, 'GET', '/gen/coach/curriculum').body as Curriculum;
    expect(cur.total).toBe(19);
    expect(cur.lessons).toHaveLength(19);
    expect(cur.lessons.map((l) => l.k)).toEqual(Array.from({ length: 19 }, (_, i) => i + 1));

    expect(call(mock, 'POST', '/gen/coach/items/lesson%3AN01', { action: 'understood' }).status).toBe(204);
    expect((call(mock, 'GET', '/gen/coach/curriculum').body as Curriculum).lessons[0].status).toBe('understood');
    // lessons_per_day = 2 ⇒ vẫn còn một bài hôm nay (N02), bài N01 đã ẩn.
    expect((call(mock, 'GET', '/gen/coach/today').body as CoachToday).lesson?.id).toBe('N02');
  });

  it('trạng thái ở bộ nhớ MODULE: hai mock (hai "context") thấy cùng bài đã hiểu; reset đưa về máy mới', () => {
    const a = createCoachMock();
    call(a, 'POST', '/gen/coach/items/lesson%3AN01', { action: 'understood' });
    const b = { handle: a.handle };
    expect((call(b as ReturnType<typeof createCoachMock>, 'GET', '/gen/coach/today').body as CoachToday).lesson).toBeNull();
    resetCoachMock();
    expect((call(a, 'GET', '/gen/coach/today').body as CoachToday).lesson?.id).toBe('N01');
  });
});

describe('BOSS_ROW_TARGETS khớp ROWS của api (boss_checks/service.py)', () => {
  it('cùng tập khoá và cùng số dòng; bossRowTarget trả đúng id', () => {
    const src = readFileSync(resolve(__dirname, '../../../api/gh/boss_checks/service.py'), 'utf8');
    const start = src.indexOf('ROWS: tuple[dict[str, Any], ...] = (');
    const end = src.indexOf('\n)\n', start);
    expect(start).toBeGreaterThanOrEqual(0);
    expect(end).toBeGreaterThan(start);
    const all = [...src.slice(start, end).matchAll(/"row":\s*(\d+),\s*"key":\s*"(\w+)"/g)].map((m) => [m[2], Number(m[1])] as const);
    expect(all.length).toBeGreaterThanOrEqual(10);
    // v0.1.55: dòng 0 "ai" (nguồn AI, bắt buộc duy nhất) có mục tiêu `boss_checks.row.ai` như mọi dòng kết nối khác.
    expect(all[0]).toEqual(['ai', 0]);
    expect(bossRowTarget(0)).toBe('boss_checks.row.ai');
    const rows = all;
    expect(Object.keys(BOSS_ROW_TARGETS).sort()).toEqual(rows.map(([k]) => k).sort());
    for (const [key, row] of rows) {
      const t = BOSS_ROW_TARGETS[key as keyof typeof BOSS_ROW_TARGETS];
      expect(t.row, key).toBe(row);
      expect(t.genTarget).toBe(`boss_checks.row.${key}`);
      expect(bossRowTarget(row)).toBe(t.genTarget);
    }
    expect(bossRowTarget(99)).toBeUndefined();
  });
});
