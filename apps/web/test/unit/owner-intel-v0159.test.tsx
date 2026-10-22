/**
 * v0.1.59 (G3) — Tình báo (/owner), Việc (/owner/viec), Phân tích (/owner/phan-tich) của Mặt tiền Owner.
 *
 * Phần 1: hàm thuần (mục bản tin phòng thủ, cảnh báo, giờ giấc, sắp xếp nháp, việc hôm nay, chi phí / chênh lệch).
 * Phần 2: Tình báo — mục lạ / `lines` là object không ném và không render `[object Object]`; dải nguồn AI CHỈ khi `ai_ready=false`
 *   hoặc `needs_api_key` (KHÔNG dựa progress); chưa có bản tin vẫn có số; `channels_connected=0` ⇒ đúng MỘT FirstRunCard; Duyệt → PIN →
 *   nháp biến mất và số giảm; khối lỗi riêng.
 * Phần 3: Việc — nháp vượt ngưỡng lên đầu, đề xuất Gen (PIN), tick Xong, Dời sang mai, Tạo nhắc, Segmented ở điện thoại.
 * Phần 4: Phân tích — "Chưa tính được" (không "0 ₫"), đang gom dữ liệu không có huy hiệu chênh lệch, ngày đầu, bảng 9 chỉ số, 7/30 ngày.
 * Máy chủ giả: `fetch` toàn cục (cùng khuôn owner-actions-v0159.test.tsx) — KHÔNG gọi Gen-hub / Kho thật.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { hasIcon } from '@gen-harness/ui';
import type { ReactElement } from 'react';
import type {
  DraftDetail,
  DraftItem,
  InsightKey,
  InsightMetric,
  InsightPoint,
  OwnerBrief,
  OwnerFeed,
  OwnerInsights,
  OwnerProposal,
  OwnerRelations,
  OwnerToday,
  Task,
} from '@gen-harness/contracts';
import { qk } from '../../src/lib/queries';
import { queryClient } from '../../src/lib/queryClient';
import { PinDialogHost } from '../../src/shell/PinDialogHost';
import { AnalyticsScreen, COST_UNKNOWN_TEXT, buildTableRows, costText, costUnknown, dayLabel, deltaView, formatMetric, metricMap, statusLine } from '../../src/owner/analytics/AnalyticsScreen';
import {
  AI_SETUP_PATH,
  FIRST_BRIEF_TEXT,
  briefSections,
  buildAlertRows,
  DecideBlock,
  buildDecideRows,
  dayHeading,
  heroSubline,
  keySections,
  safeSection,
  safeSections,
  showAiStrip,
  slotWord,
} from '../../src/owner/intel/BriefBlocks';
import { IntelScreen, careLink, channelsConnected, feedWho, messagesNote, todayVsYesterday } from '../../src/owner/intel/IntelScreen';
import { DecideList, decideCounts, draftFlags, draftRank, firstLines, sortDrafts } from '../../src/owner/work/DecideList';
import { buildSuggestions, buildTodayItems, dayKey, dueText, profileLink, tomorrowStartIso } from '../../src/owner/work/TodayTasks';
import { WorkScreen } from '../../src/owner/work/WorkScreen';
import { useOwnerBrief, useOwnerProposals, useOwnerRelations, useOwnerToday } from '../../src/owner/queries';
import { useDrafts } from '../../src/screens/core/queries';

// Các ca trong tệp này viết cho trạng thái ĐÃ theo dõi lời hứa (`PROMISES_TRACKED = true`); mặc định hiện tại là false
// (v0.1.71, P3-4) — trạng thái ẩn được kiểm ở `promises-hidden-v0171.test.tsx`.
vi.mock('../../src/lib/features', () => ({ PROMISES_TRACKED: true }));


// ── dữ liệu mẫu (tên hư cấu, không có địa chỉ Gen-hub / Kho thật) ───────────────────────────────────────────────────────

const MIN = 60_000;
const HOUR = 60 * MIN;
const ago = (ms: number) => new Date(Date.now() - ms).toISOString();
const later = (ms: number) => new Date(Date.now() + ms).toISOString();

const ME = {
  id: 'u1', email: 'owner@example.test', display_name: 'Anh Chủ', role: { code: 'owner', name: 'owner' },
  org: { id: 'o1', name: 'Công ty Mẫu', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
  pin_verified_until: null, permissions: {}, must_change_password: false, features: { gen: true },
};

const SECTION = (key: string, title: string, count: number, lines: string[], group: 'decide' | 'alert' | 'key', to = '/owner', extra: Record<string, unknown> = {}) => ({
  key, title, count, lines, to, group, ...extra,
});

const BRIEF = (over: Partial<OwnerBrief> = {}): OwnerBrief =>
  ({
    latest: {
      id: 'brief-1', slot: 'sáng', at: ago(3 * HOUR), title: 'Bản tin Gen · sáng', summary: 'Sáng nay Sếp có nháp chờ duyệt và một khách có thể sắp rời.',
      needs_api_key: false, sections: [SECTION('calendar_today', 'Lịch hôm nay', 2, ['09:00 · Họp giao ban', '14:00 · Gặp khách tại xưởng'], 'key', '/connections#genhub')],
    },
    live: {
      at: ago(0),
      sections: [
        SECTION('tasks_due', 'Việc đến hạn', 2, ['Gọi lại chị Lan — quá hạn'], 'decide', '/owner/viec'),
        SECTION('hot_customers', 'Khách nóng', 3, ['Trần Văn Hậu — độ nóng 91'], 'key', '/owner/quan-he'),
        SECTION('drafts_pending', 'Nháp chờ duyệt', 2, ['ACT-0231 · Báo giá cho khách'], 'decide', '/owner/viec'),
        SECTION('incidents', 'Sự cố cần Sếp', 1, ['Ổ đĩa sắp đầy (82%)'], 'alert', '/system?tab=storage'),
      ],
    },
    alerts: [{ key: 'disk.low', title: 'Ổ đĩa sắp đầy (82%)', severity: 'warn', raised_at: ago(3 * HOUR), to: '/system?tab=storage&focus=health' }],
    history: [
      { id: 'brief-1', slot: 'sáng', at: ago(3 * HOUR), title: 'Bản tin Gen · sáng' },
      { id: 'brief-0', slot: 'chiều', at: ago(15 * HOUR), title: 'Bản tin Gen · chiều qua' },
    ],
    next_at: later(5 * HOUR), ai_ready: true, channels_connected: 1, ...over,
  }) as OwnerBrief;

const DRAFT_ITEM = (id: string, title: string, hold: string | null = 'ghi ra ngoài'): DraftItem =>
  ({
    id, code: `ACT-${id}`, kind: 'message', kind_label: 'Tin nhắn', title, agent: null, created_by: null, created_at: ago(20 * MIN), status: 'pending', hold_reason: hold,
    subject: { id: 'p1', code: 'PER-1', name: 'Chị Lan', type: 'customer', org_name: null },
  }) as unknown as DraftItem;

const DRAFT_DETAIL = (id: string, title: string, over: Omit<Partial<DraftDetail>, 'flags'> & { flags?: Partial<DraftDetail['flags']> } = {}): DraftDetail => {
  const { flags, ...rest } = over;
  return {
    ...DRAFT_ITEM(id, title),
    paragraphs: ['Dạ em gửi báo giá ván MDF ạ.', 'Giá 15.200.000 ₫ một tấn.'], text: 'Dạ em gửi báo giá ván MDF ạ.\nGiá 15.200.000 ₫ một tấn.', lang: 'vi',
    target: { channel: 'zalo', thread_type: 'user', group: null, person: { id: 'p1', code: 'PER-1', name: 'Chị Lan', type: 'customer', org_name: null } },
    amount_vnd: null, autonomy_level: 4, flags: { writes_external: true, personnel_related: false, over_threshold: false, ...flags },
    approve_label: 'Duyệt và gửi qua Zalo', sources: [], context: [], side_actions: [], decision: null, send_result: null, versions: [], ...rest,
  } as unknown as DraftDetail;
};

const PROPOSAL = (over: Partial<OwnerProposal> = {}): OwnerProposal => ({
  id: 'prop-1', type: 'kho_create', type_label: 'Ghi vào Kho dữ liệu', summary: 'Ghi phiên làm việc hôm nay vào Kho dữ liệu.', needs_pin: true,
  created_at: ago(40 * MIN), expires_at: later(20 * HOUR), conversation_id: 'c1', ...over,
});

const TODAY = (over: Partial<OwnerToday> = {}): OwnerToday =>
  ({
    needs_review: [], kpis: { hot: 3, cooling: 2, open_opps: 12, open_value_vnd: 2_400_000_000, overdue_promises: 1 }, briefing_latest: null,
    filter_value: { filtered: 0, spam_blocked: 0, calls_saved: 0, jev_on: false }, suggestions: [], progress: { required_done: 0, required_total: 6 },
    nav: { urgent_open: 2, decide: 3 }, ...over,
  }) as OwnerToday;

const FEED = (over: Partial<OwnerFeed> = {}): OwnerFeed => ({
  items: [
    { id: 'fd-1', item_type: 'unit', title: 'Than phiền', summary: 'Giao ván chậm hai ngày.', importance: 'Khẩn', event_type: 'Complained', created_at: ago(30 * MIN), person: { id: 'p-bao', name: 'Nguyễn Văn Bảo' }, group: { id: 'g1', name: 'Nhóm Thành Phát', channel: 'zalo' }, suggested_action: null },
    { id: 'fd-2', item_type: 'unit', title: 'Hỏi giá', summary: 'Hỏi giá 3 container ván MDF.', importance: 'Quan trọng', event_type: 'AskedPrice', created_at: ago(2 * HOUR), person: { id: 'p-hau', name: 'Trần Văn Hậu' }, group: null, suggested_action: null },
  ],
  next_offset: null, counts: { important: 2, all: 2, opportunity: 1, alert: 0, approval: 0, urgent_open: 1 }, filtered_7d: { filtered: 40, spam_blocked: 5 }, channels_connected: 1, ...over,
});

const CARE: OwnerRelations = {
  list: 'care',
  items: [
    { id: 'p-thang', name: 'Bùi Đức Thắng', subtitle: 'Chưa phân loại', metric_text: 'Có thể sắp rời · 6 ngày chưa liên lạc', to: '/profile?id=p-thang', person_id: 'p-thang', reason: 'churn', churn_risk: 82, last_contact_days: 6, open_value_vnd: 880_000_000 },
    { id: 'p-tri', name: 'Đặng Hữu Trí', subtitle: 'Khách hàng', metric_text: '33 ngày chưa liên lạc', to: '/profile?id=p-tri', person_id: 'p-tri', reason: 'silent', last_contact_days: 33 },
  ],
};

/** `n` ngày liền nhau tận cùng hôm nay (giờ VN không quan trọng cho test: chỉ cần chuỗi `YYYY-MM-DD` tăng dần). */
const days = (n: number, f: (i: number) => number, offset = 0): InsightPoint[] =>
  Array.from({ length: n }, (_, i) => ({ d: new Date(Date.UTC(2026, 9, 10 + i - offset)).toISOString().slice(0, 10), v: f(i) }));

const METRIC = (key: InsightKey, label: string, unit: InsightMetric['unit'], total: number, prev: number, over: Partial<InsightMetric> = {}): InsightMetric => ({
  key, label, unit, total, prev_total: prev, delta_pct: prev === 0 ? null : Math.round(((total - prev) / prev) * 1000) / 10, good: key === 'unanswered' || key === 'ai_cost_vnd' ? 'down' : 'up',
  series: days(7, (i) => Math.round(total / 7) + (i % 2)), prev_series: days(7, (i) => Math.round(prev / 7) + (i % 3), 7), ...over,
});

const INSIGHTS = (over: Partial<OwnerInsights> = {}, metrics?: InsightMetric[]): OwnerInsights => ({
  range: 'week', from: '2026-10-10', to: '2026-10-16', prev_from: '2026-10-03', prev_to: '2026-10-09', generated_at: ago(0), state: 'ok', days_until_compare: null, channels_connected: 1,
  metrics: metrics ?? [
    METRIC('messages_in', 'Tin nhận', 'count', 812, 725),
    METRIC('signals', 'Tín hiệu đáng chú ý', 'count', 146, 131),
    METRIC('new_contacts', 'Người mới liên hệ', 'count', 37, 28),
    METRIC('opps_new', 'Cơ hội mới', 'count', 8, 10),
    METRIC('opps_won', 'Cơ hội thắng', 'count', 3, 2, { value_vnd: 420_000_000 }),
    METRIC('reply_fast_pct', 'Trả lời nhanh', 'pct', 68, 73),
    METRIC('unanswered', 'Tin khách chờ trả lời', 'count', 11, 7),
    METRIC('ai_cost_vnd', 'Chi phí trợ lý Gen', 'vnd', 184_000, 151_000),
    METRIC('filtered', 'Gen lọc giúp', 'count', 124, 98, { series: [], prev_series: [] }),
  ],
  breakdown: {
    event_types: [{ event_type: 'AskedPrice', label: 'Hỏi giá', units: 86 }, { event_type: 'Complained', label: 'Than phiền', units: 12 }],
    funnel: [{ stage: 'raw_signal', label: 'Tín hiệu mới', n: 4, value_vnd: 610_000_000 }, { stage: 'negotiating', label: 'Đang đàm phán', n: 2, value_vnd: 470_000_000 }, { stage: 'won', label: 'Thắng', n: 3, value_vnd: 420_000_000 }],
  },
  ai_cost: { calls: 40, unpriced_calls: 0, budget_vnd: 40_000 }, feedback: { helpful: 9, not_helpful: 2 }, ...over,
});

const TASK = (id: string, title: string, over: Partial<Task> = {}): Task =>
  ({
    id, code: `TSK-${id}`, title, priority: 'P3', status: 'todo', assignee: null, subject: { id: 'p1', code: 'PER-1', name: 'Chị Lan', type: 'customer', org_name: null }, due_at: ago(2 * HOUR),
    remind_at: null, overdue: true, source: 'manual', created_at: ago(24 * HOUR), completed_at: null, ...over,
  }) as unknown as Task;

// ── máy chủ giả ─────────────────────────────────────────────────────────────────────────────────────────────────────────

interface Call {
  path: string;
  search: string;
  method: string;
  body: unknown;
}
type Handler = ((c: Call) => Response | undefined) | object;

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json' } });
const problem500 = () => json(500, { status: 500, code: 'INTERNAL', title: 'Máy chủ gặp lỗi', error_id: 'ERR-TEST-3' });

/** Trạng thái thay đổi được giữa các lần gọi (duyệt nháp ⇒ nháp biến khỏi danh sách và số `nav.decide` giảm). */
const state = {
  drafts: [] as DraftDetail[],
  proposals: [] as OwnerProposal[],
  tasks: [] as Task[],
  silencedDrafts: new Set<string>(),
  promises: [] as unknown[],
  pinOk: false,
};

/** Đường → trả lời. Thiếu ⇒ dùng mặc định; giá trị là hàm ⇒ tự quyết Response (trả undefined ⇒ rơi xuống mặc định). */
function mockApi(over: Record<string, Handler> = {}) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const u = new URL(String(input), 'http://x');
      const c: Call = { path: u.pathname.replace(/^\/api\/v1/, ''), search: u.search, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(c);
      const h = over[`${c.method} ${c.path}`] ?? over[c.path];
      if (typeof h === 'function') {
        const r = (h as (x: Call) => Response | undefined)(c);
        if (r) return r;
      } else if (h !== undefined) return json(200, h);
      return defaults(c);
    }),
  );
  return calls;
}

function defaults(c: Call): Response {
  const { path, method } = c;
  if (path === '/auth/me') return json(200, ME);
  if (path === '/owner/brief') return json(200, BRIEF());
  if (path === '/owner/today') return json(200, TODAY({ nav: { urgent_open: 2, decide: state.drafts.length + state.proposals.length } }));
  if (path === '/owner/proposals') return json(200, { items: state.proposals });
  if (path === '/owner/insights') return json(200, INSIGHTS());
  if (path === '/owner/feed') return json(200, FEED());
  if (path === '/owner/relations') return json(200, CARE);
  if (path === '/drafts' && method === 'GET') {
    // như máy chủ: hide_silenced=true bỏ nháp đã Tắt tiếng (cả danh sách lẫn total)
    const rows = new URLSearchParams(c.search).get('hide_silenced') === 'true' ? state.drafts.filter((x) => !state.silencedDrafts.has(x.id)) : state.drafts;
    return json(200, { items: rows, next_cursor: null, total: rows.length });
  }
  const d = /^\/drafts\/([\w-]+)(?:\/(approve|edit-send|reject))?$/.exec(path);
  if (d) {
    const draft = state.drafts.find((x) => x.id === d[1]);
    if (!draft) return json(404, { status: 404, code: 'NOT_FOUND', title: 'Bản nháp không tồn tại' });
    if (!d[2]) return json(200, draft);
    if (!state.pinOk) return json(423, { status: 423, code: 'PIN_REQUIRED', title: 'Thao tác này cần nhập mã PIN' });
    state.drafts = state.drafts.filter((x) => x.id !== draft.id);
    return json(200, { ...draft, status: 'sent' });
  }
  if (path === '/auth/pin/verify') {
    state.pinOk = true;
    return json(200, { pin_verified_until: later(30 * MIN) });
  }
  const p = /^\/gen\/proposals\/([\w-]+)\/(confirm|cancel)$/.exec(path);
  if (p && method === 'POST') {
    const prop = state.proposals.find((x) => x.id === p[1]);
    if (!prop) return json(404, { status: 404, code: 'NOT_FOUND', title: 'Đề xuất không tồn tại' });
    if (p[2] === 'confirm' && prop.needs_pin && !state.pinOk) return json(423, { status: 423, code: 'PIN_REQUIRED', title: 'Thao tác này cần nhập mã PIN' });
    state.proposals = state.proposals.filter((x) => x.id !== prop.id);
    return json(200, { id: prop.id, status: p[2] === 'confirm' ? 'confirmed' : 'cancelled' });
  }
  if (path === '/tasks/promises' && method === 'GET') return json(200, { items: state.promises, next_cursor: null, total: state.promises.length });
  if (path === '/tasks' && method === 'GET') {
    // như máy chủ: lọc status / due_before (chỉ việc CÓ hạn), mới tạo trước, mặc định 50 việc một trang
    const q = new URLSearchParams(c.search);
    const status = q.get('status');
    const dueBefore = q.get('due_before');
    const rows = state.tasks
      .filter((t) => !status || t.status === status)
      .filter((t) => !dueBefore || (!!t.due_at && Date.parse(t.due_at) < Date.parse(dueBefore)))
      .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
    return json(200, { items: rows.slice(0, Number(q.get('limit') ?? 50)), next_cursor: null, total: rows.length });
  }
  if (path === '/tasks' && method === 'POST') return json(201, TASK('t-new', String((c.body as { title?: string }).title ?? ''), { status: 'todo', due_at: String((c.body as { due_at?: string }).due_at ?? '') }));
  const t = /^\/tasks\/([\w-]+)$/.exec(path);
  if (t && method === 'PATCH') {
    const task = state.tasks.find((x) => x.id === t[1]);
    if (!task) return json(404, { status: 404, code: 'NOT_FOUND', title: 'Việc không tồn tại' });
    const b = c.body as { status?: Task['status']; due_at?: string };
    state.tasks = state.tasks
      .map((x) => (x.id === task.id ? { ...x, ...(b.status ? { status: b.status } : {}), ...(b.due_at ? { due_at: later(2 * 24 * HOUR), overdue: false } : {}) } : x))
      .filter((x) => x.status !== 'done');
    return json(200, task);
  }
  return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tìm thấy' });
}

/** Máy tính (≥ 760px / 1024px) khớp media query; điện thoại thì không. */
function setViewport(wide: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: wide && (q.includes('min-width: 760px') || q.includes('min-width: 1024px')), media: q, onchange: null,
    addEventListener: () => undefined, removeEventListener: () => undefined, addListener: () => undefined, removeListener: () => undefined, dispatchEvent: () => false,
  }));
}

function mount(ui: ReactElement, path = '/owner') {
  queryClient.setQueryData(qk.me, ME);
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        {ui}
        <PinDialogHost />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const noRawObjects = () => expect(document.body.textContent ?? '').not.toContain('[object Object]');

beforeEach(() => {
  queryClient.clear();
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } });
  document.cookie = 'gh_csrf=test-csrf';
  state.drafts = [DRAFT_DETAIL('d-1', 'Báo giá cho khách A', { flags: { over_threshold: false, writes_external: false } }), DRAFT_DETAIL('d-2', 'Báo giá lớn cho khách B', { flags: { over_threshold: true, writes_external: true }, hold_reason: 'vượt ngưỡng 50.000.000 ₫' })];
  state.proposals = [PROPOSAL()];
  state.tasks = [TASK('t-1', 'Gọi lại chị Lan về hợp đồng')];
  state.promises = [];
  state.silencedDrafts = new Set();
  state.pinOk = false;
  setViewport(false);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ═══ 1. hàm thuần ═══════════════════════════════════════════════════════════════════════════════════════════════════════

describe('mục bản tin phòng thủ', () => {
  it('safeSection: chuỗi lạ ⇒ asText, object trong lines bị bỏ, nhóm lạ ⇒ key, link ngoài ⇒ /owner, không ném', () => {
    const s = safeSection({ key: 'x', title: 'Mục lạ', count: '7', lines: [{ a: 1 }, 'dòng ok', 5, null], to: '//evil.example', group: 'zzz', state: 'weird' });
    expect(s).toMatchObject({ key: 'x', title: 'Mục lạ', count: null, lines: ['dòng ok', '5'], to: '/owner', group: 'key', state: null });
    expect(safeSection({ key: 'k', title: { o: 1 }, count: 2, lines: { k: 'v' }, group: 'decide' })).toMatchObject({ key: 'k', title: 'k', count: 2, lines: [], group: 'decide' });
    for (const bad of [null, undefined, 42, 'x', [], {}, { key: { a: 1 } }]) expect(safeSection(bad)).toBeNull();
    expect(safeSections('không phải mảng')).toEqual([]);
    expect(safeSection({ key: 'e', title: 'Lỗi', state: 'error', count: 0, lines: Array.from({ length: 9 }, (_, i) => `d${i}`) })?.lines).toHaveLength(5);
  });

  it('briefSections: số liệu `live` + mục Gen-hub chỉ có ở `latest`; trùng khoá lấy `live`', () => {
    const b = BRIEF();
    const keys = briefSections(b).map((s) => s.key);
    expect(keys).toEqual(['tasks_due', 'hot_customers', 'drafts_pending', 'incidents', 'calendar_today']);
    const dup = BRIEF({ latest: { ...b.latest!, sections: [SECTION('hot_customers', 'Khách nóng (cũ)', 9, [], 'key') as never] } });
    expect(briefSections(dup).filter((s) => s.key === 'hot_customers')).toHaveLength(1);
    expect(briefSections(null)).toEqual([]);
    expect(briefSections(BRIEF({ latest: null })).map((s) => s.key)).toEqual(['tasks_due', 'hot_customers', 'drafts_pending', 'incidents']);
  });

  it('keySections: bỏ nháp chờ duyệt, sự cố, mục lỗi và mục rỗng hẳn', () => {
    const s = safeSections([
      SECTION('drafts_pending', 'Nháp', 2, ['a'], 'decide'), SECTION('incidents', 'Sự cố', 1, ['b'], 'alert'), SECTION('mail_reply', 'Mail', 3, ['c'], 'decide'),
      SECTION('kho', 'Kho', 0, [], 'key', '/owner', { state: 'empty' }), SECTION('facebook', 'Facebook', 4, ['d'], 'key', '/owner', { state: 'error' }), SECTION('hot_customers', 'Khách nóng', 3, [], 'key'),
    ]);
    expect(keySections(s).map((x) => x.key)).toEqual(['mail_reply', 'hot_customers']);
  });

  it('showAiStrip: chỉ khi needs_api_key hoặc ai_ready=false (thiếu trường ⇒ không hiện)', () => {
    expect(showAiStrip(BRIEF({ ai_ready: true }))).toBe(false);
    expect(showAiStrip(BRIEF({ ai_ready: false }))).toBe(true);
    expect(showAiStrip(BRIEF({ ai_ready: true, latest: { ...BRIEF().latest!, needs_api_key: true } }))).toBe(true);
    expect(showAiStrip({ latest: null, ai_ready: undefined as never })).toBe(false);
    expect(showAiStrip(null)).toBe(false);
  });

  it('dayHeading / slotWord / heroSubline theo giờ Việt Nam', () => {
    expect(dayHeading('2026-10-11T03:00:00Z')).toBe('Chủ nhật 11/10');
    expect(dayHeading('2026-10-10T20:00:00Z')).toBe('Chủ nhật 11/10'); // 03:00 giờ VN
    expect(dayHeading('2026-10-12T03:00:00Z')).toBe('Thứ hai 12/10');
    expect(dayHeading('không phải ngày')).toBe('');
    expect(slotWord('2026-10-11T00:30:00Z')).toBe('sáng'); // 07:30 VN
    expect(slotWord('2026-10-11T10:30:00Z')).toBe('chiều'); // 17:30 VN
    const sub = heroSubline(BRIEF({ latest: { ...BRIEF().latest!, at: '2026-10-11T00:30:00Z' }, live: { at: '2026-10-11T02:12:00Z', sections: [] }, next_at: '2026-10-11T10:30:00Z' }));
    expect(sub).toBe('Bản tin sáng 07:30 · số liệu cập nhật 09:12 · bản tin chiều lúc 17:30');
    expect(heroSubline(BRIEF({ latest: null, live: { at: '2026-10-11T02:12:00Z', sections: [] }, next_at: '2026-10-11T10:30:00Z' }))).toBe('số liệu cập nhật 09:12 · bản tin chiều lúc 17:30');
    // xem bản CŨ (?id=): máy chủ không chạy phần đọc lúc này ⇒ live = null, không ném, không ghi "số liệu cập nhật"
    expect(heroSubline(BRIEF({ latest: { ...BRIEF().latest!, at: '2026-10-11T00:30:00Z' }, live: null, next_at: '2026-10-11T10:30:00Z' }))).toBe('Bản tin sáng 07:30 · bản tin chiều lúc 17:30');
    expect(briefSections(BRIEF({ live: null })).map((x) => x.key)).toEqual(BRIEF().latest!.sections.map((x) => x.key));
  });
});

describe('Cảnh báo, nháp, tile', () => {
  it('buildAlertRows: alerts → Khẩn/Quan trọng, dòng của mục sự cố KHÔNG trùng, mục lỗi báo "Chưa tải được"; chuỗi lạ bị bỏ', () => {
    const rows = buildAlertRows(
      [{ key: 'a', title: 'Bản sao lưu chưa chạy', severity: 'bad', raised_at: ago(HOUR), to: '/system' }, { key: 'b', title: { x: 1 }, severity: 'warn' }, 'lạ', null],
      safeSections([SECTION('incidents', 'Sự cố', 2, ['Bản sao lưu chưa chạy', 'Ổ đĩa sắp đầy'], 'alert'), SECTION('facebook', 'Facebook', 0, [], 'key', '/social', { state: 'error' })]),
    );
    expect(rows.map((r) => r.title)).toEqual(['Bản sao lưu chưa chạy', 'Ổ đĩa sắp đầy', 'Chưa lấy được mục “Facebook”']);
    expect(rows[0].badge).toEqual({ kind: 'level', level: 'Khẩn' });
    expect(rows[1].badge).toEqual({ kind: 'level', level: 'Quan trọng' });
    expect(rows[2].badge).toEqual({ kind: 'status', text: 'Chưa tải được' });
    expect(buildAlertRows('không phải mảng', [])).toEqual([]);
  });

  it('buildDecideRows: ≤ 3 dòng, nháp có lý do giữ lên trước, đề xuất sau; dòng bản tin chỉ khi không đọc được nháp', () => {
    const rows = buildDecideRows([DRAFT_ITEM('a', 'A', null), DRAFT_ITEM('b', 'B', 'vượt ngưỡng'), DRAFT_ITEM('c', 'C', null)], [PROPOSAL()], null);
    expect(rows.map((r) => r.key)).toEqual(['d-b', 'd-a', 'd-c']);
    expect(buildDecideRows([], [PROPOSAL()], null).map((r) => r.kind)).toEqual(['proposal']);
    const fb = safeSection(SECTION('drafts_pending', 'Nháp', 2, ['ACT-1 · Báo giá', 'ACT-2 · Hợp đồng'], 'decide'));
    expect(buildDecideRows(null, [], fb).map((r) => r.kind)).toEqual(['line', 'line']);
    expect(buildDecideRows([], [], fb)).toEqual([]);
  });

  it('todayVsYesterday: điểm cuối và điểm kề cuối (cả ngày hôm qua); không còn %; chuỗi lạ ⇒ 0', () => {
    expect(todayVsYesterday({ series: days(7, (i) => [10, 10, 10, 10, 10, 20, 25][i]) })).toEqual({ today: 25, yesterday: 20, spark: [10, 10, 10, 10, 10, 20, 25] });
    expect(todayVsYesterday({ series: days(3, (i) => [4, 0, 6][i]) })).toMatchObject({ today: 6, yesterday: 0 });
    expect(todayVsYesterday({ series: days(1, () => 5) })).toMatchObject({ today: 5, yesterday: null });
    expect(todayVsYesterday(undefined)).toMatchObject({ today: 0, spark: [] });
    expect(todayVsYesterday({ series: [{ d: 'x', v: 'a' as never }] })).toMatchObject({ today: 0 });
  });

  it('messagesNote: "Tính đến <giờ>" + CẢ NGÀY hôm qua (không so nửa ngày với cả ngày); thiếu giờ/số hôm qua vẫn rõ nghĩa', () => {
    const at = '2026-10-16T05:30:00Z';          // 12:30 giờ VN
    expect(messagesNote({ today: 4, yesterday: 20 }, at, 'Asia/Ho_Chi_Minh')).toBe('Tính đến 12:30 · cả ngày hôm qua 20 tin');
    expect(messagesNote({ today: 0, yesterday: 0 }, at, 'Asia/Ho_Chi_Minh')).toBe('Tính đến 12:30 · cả ngày hôm qua 0 tin');
    expect(messagesNote({ today: 4, yesterday: null }, at, 'Asia/Ho_Chi_Minh')).toBe('Tính đến 12:30 · chưa có số hôm qua');
    expect(messagesNote({ today: 4, yesterday: 20 }, 'không phải giờ', 'Asia/Ho_Chi_Minh')).toBe('Hôm nay chưa hết ngày · cả ngày hôm qua 20 tin');
    expect(messagesNote({ today: 4, yesterday: 20 }, undefined)).toContain('cả ngày hôm qua 20 tin');
  });

  it('careLink / feedWho / channelsConnected', () => {
    expect(careLink({ person_id: 'p-thang' })).toBe('/owner/quan-he?nguoi=p-thang');
    expect(careLink({})).toBe('/owner/quan-he');
    expect(feedWho({ person: { id: 'p', name: 'Chị Lan' }, group: { id: 'g', name: 'Nhóm A', channel: 'zalo' } })).toBe('Chị Lan · Nhóm A · Zalo');
    expect(feedWho({ person: { name: { x: 1 } } as never, group: null })).toBe('');
    expect(channelsConnected(undefined, 0, 1)).toBe(0);
    expect(channelsConnected(undefined, undefined)).toBeUndefined();
  });
});

describe('Việc: nháp, việc hôm nay, gợi ý', () => {
  it('sortDrafts: vượt ngưỡng tự gửi → ghi ra ngoài → còn lại; chưa có chi tiết giữ thứ tự cũ', () => {
    const items = [DRAFT_ITEM('a', 'A'), DRAFT_ITEM('b', 'B'), DRAFT_ITEM('c', 'C'), DRAFT_ITEM('d', 'D')];
    const details = new Map<string, DraftDetail>([
      ['a', DRAFT_DETAIL('a', 'A', { flags: { over_threshold: false, writes_external: false } })],
      ['b', DRAFT_DETAIL('b', 'B', { flags: { over_threshold: false, writes_external: true } })],
      ['c', DRAFT_DETAIL('c', 'C', { flags: { over_threshold: true, writes_external: false } })],
    ]);
    expect(sortDrafts(items, details).map((d) => d.id)).toEqual(['c', 'b', 'a', 'd']);
    expect(draftRank(draftFlags(undefined))).toBe(2);
    expect(draftFlags({ flags: { over_threshold: 'x' as never, writes_external: true } as never })).toEqual({ over_threshold: false, writes_external: true });
    expect(firstLines('\n dòng 1 \n\ndòng 2\ndòng 3')).toBe('dòng 1\ndòng 2');
    expect(firstLines({ o: 1 })).toBe('');
  });

  it('decideCounts: tổng nháp (theo máy chủ) + đề xuất', () => {
    expect(decideCounts({ data: { items: [], next_cursor: null, total: 5 } }, { data: { items: [PROPOSAL(), PROPOSAL({ id: 'p2' })] } })).toEqual({ drafts: 5, proposals: 2, total: 7 });
    expect(decideCounts({ data: undefined }, { data: undefined })).toEqual({ drafts: 0, proposals: 0, total: 0 });
  });

  it('dayKey / buildTodayItems: hạn ≤ hết hôm nay (giờ VN) hoặc quá hạn; bỏ việc xong / không hạn / hạn ngày mai; lời hứa quá hạn chưa giữ', () => {
    const now = Date.parse('2026-10-16T05:00:00Z'); // 12:00 giờ VN
    expect(dayKey('2026-10-16T17:30:00Z', 'Asia/Ho_Chi_Minh')).toBe('2026-10-17'); // 00:30 VN hôm sau
    expect(dayKey('rác')).toBe('');
    const tasks = [
      TASK('over', 'Quá hạn', { due_at: '2026-10-14T05:00:00Z' }),
      TASK('today', 'Hạn chiều nay', { due_at: '2026-10-16T10:00:00Z', subject: null }),
      TASK('late-today', 'Hạn 23:30 giờ VN', { due_at: '2026-10-16T16:30:00Z' }),
      TASK('tomorrow', 'Ngày mai', { due_at: '2026-10-16T17:30:00Z' }),
      TASK('nodue', 'Không hạn', { due_at: null }),
      TASK('done', 'Đã xong', { due_at: '2026-10-15T05:00:00Z', status: 'done' }),
      TASK('grp', 'Việc của nhóm', { due_at: '2026-10-16T08:00:00Z', subject: { id: 'g1', code: 'G', name: 'Nhóm', channel: 'zalo' } as never }),
    ];
    const promises = [
      { id: 'pr-1', text: 'Gửi mẫu gạch', due_at: '2026-10-15T05:00:00Z', kept_at: null, broken: true, from: { id: 'p-bao', name: 'Bảo' }, to: null, evidence: null },
      { id: 'pr-2', text: 'Đã giữ', due_at: '2026-10-15T05:00:00Z', kept_at: '2026-10-15T06:00:00Z', broken: false, from: { id: 'p-x', name: 'X' }, to: null, evidence: null },
      { id: 'pr-3', text: 'Chưa tới hạn', due_at: '2026-10-17T05:00:00Z', kept_at: null, broken: false, from: { id: 'p-y', name: 'Y' }, to: null, evidence: null },
    ] as never[];
    const items = buildTodayItems(tasks, promises, now);
    expect(items.map((i) => i.id)).toEqual(['over', 'pr-1', 'grp', 'today', 'late-today']);
    expect(items.find((i) => i.id === 'over')).toMatchObject({ overdue: true, personId: 'p1', kind: 'task' });
    expect(items.find((i) => i.id === 'grp')?.personId).toBeNull();
    expect(items.find((i) => i.id === 'pr-1')).toMatchObject({ kind: 'promise', personId: 'p-bao', overdue: true });
    expect(dueText({ dueAt: '2026-10-14T05:00:00Z', overdue: true }, now)).toBe('Quá hạn 2 ngày');
    expect(dueText({ dueAt: '2026-10-16T02:00:00Z', overdue: true }, now)).toBe('Quá hạn 3 giờ');
    expect(dueText({ dueAt: '2026-10-16T10:00:00Z', overdue: false }, now)).toBe('Hạn 17:00');
    expect(profileLink('p 1')).toBe('/owner/quan-he?nguoi=p%201');
  });

  it('tomorrowStartIso: đầu ngày mai (00:00) theo giờ tổ chức — cùng một giá trị cả ngày', () => {
    expect(tomorrowStartIso(Date.parse('2026-10-16T05:00:00Z'), 'Asia/Ho_Chi_Minh')).toBe('2026-10-16T17:00:00.000Z'); // 17/10 00:00 VN
    expect(tomorrowStartIso(Date.parse('2026-10-16T16:59:00Z'), 'Asia/Ho_Chi_Minh')).toBe('2026-10-16T17:00:00.000Z'); // 23:59 VN
    expect(tomorrowStartIso(Date.parse('2026-10-16T17:01:00Z'), 'Asia/Ho_Chi_Minh')).toBe('2026-10-17T17:00:00.000Z'); // 00:01 ngày 17 VN
    expect(tomorrowStartIso(Date.parse('2026-10-16T05:00:00Z'), 'UTC')).toBe('2026-10-17T00:00:00.000Z');
    expect(tomorrowStartIso(Date.parse('2026-10-16T05:00:00Z'), 'Múi/Lạ')).toBe('2026-10-16T17:00:00.000Z'); // múi lạ ⇒ giờ VN
    expect(tomorrowStartIso(Date.parse('2026-01-31T12:00:00Z'), 'UTC')).toBe('2026-02-01T00:00:00.000Z'); // sang tháng
  });

  it('buildSuggestions: người ĐÃ có nhắc đang mở (has_open_task) không được gợi ý lại; người vừa tạo trong phiên (keep) vẫn giữ dòng', () => {
    const rows = [{ ...CARE.items[0], has_open_task: true }, CARE.items[1]];
    expect(buildSuggestions(rows).map((x) => x.personId)).toEqual(['p-tri']);
    expect(buildSuggestions(rows, new Set(['p-thang'])).map((x) => x.personId)).toEqual(['p-thang', 'p-tri']);
    expect(buildSuggestions([{ ...CARE.items[0], has_open_task: false }]).map((x) => x.personId)).toEqual(['p-thang']);
  });

  it('buildSuggestions: khách cần chăm có mã người → "Gọi hỏi thăm …"; hàng thiếu mã người / tên bị bỏ', () => {
    const s = buildSuggestions([...CARE.items, { id: 'x', name: 'Không mã', subtitle: '', metric_text: '', to: '/' }, { id: 'y', name: { o: 1 } as never, subtitle: '', metric_text: '', to: '/', person_id: 'p-y' }]);
    expect(s.map((x) => x.task)).toEqual(['Gọi hỏi thăm Bùi Đức Thắng', 'Gọi hỏi thăm Đặng Hữu Trí']);
    expect(s[0].meta).toBe('Có thể sắp rời · 6 ngày chưa liên lạc · còn cơ hội 880 triệu ₫');
    expect(s[1].meta).toBe('33 ngày chưa liên lạc');
  });
});

describe('Phân tích: chi phí, chênh lệch, bảng', () => {
  it('costUnknown / costText: tổng 0 mà còn lượt chưa đặt giá ⇒ "Chưa tính được" (KHÔNG "0 ₫")', () => {
    expect(costUnknown(0, 12)).toBe(true);
    expect(costText(0, 12)).toBe(COST_UNKNOWN_TEXT);
    expect(costText(0, 0)).toBe('0 ₫');
    expect(costText(184_000, 12)).toBe('184.000 ₫');
    expect(costText('x', undefined)).toBe('0 ₫');
  });

  it('deltaView: màu theo NGHĨA, phần trăm nói bằng ĐIỂM, null ⇒ "Mới"', () => {
    expect(deltaView(METRIC('messages_in', 'x', 'count', 112, 100))).toEqual({ text: 'Tăng 12%', tone: 'ok', direction: 'up' });
    expect(deltaView(METRIC('ai_cost_vnd', 'x', 'vnd', 122, 100))).toEqual({ text: 'Tăng 22%', tone: 'warn', direction: 'up' }); // chi phí tăng là xấu
    expect(deltaView(METRIC('unanswered', 'x', 'count', 5, 10))).toEqual({ text: 'Giảm 50%', tone: 'ok', direction: 'down' });
    expect(deltaView(METRIC('reply_fast_pct', 'x', 'pct', 68, 73))).toEqual({ text: 'Giảm 5 điểm', tone: 'warn', direction: 'down' });
    expect(deltaView(METRIC('new_contacts', 'x', 'count', 5, 0))).toEqual({ text: 'Mới', tone: 'neutral', direction: null });
    expect(deltaView(METRIC('new_contacts', 'x', 'count', 5, 5))).toEqual({ text: 'Không đổi', tone: 'neutral', direction: null });
  });

  it('formatMetric / dayLabel / statusLine / metricMap', () => {
    const won = METRIC('opps_won', 'x', 'count', 3, 2, { value_vnd: 420_000_000 });
    expect(formatMetric(won, 'cur')).toBe('3 · 420 triệu ₫');
    expect(formatMetric(won, 'prev')).toBe('2');
    expect(formatMetric(METRIC('reply_fast_pct', 'x', 'pct', 68.4, 73), 'cur')).toBe('68,4%');
    expect(formatMetric(METRIC('ai_cost_vnd', 'x', 'vnd', 0, 0), 'cur', { unpriced_calls: 3 })).toBe(COST_UNKNOWN_TEXT);
    expect(dayLabel('2026-10-05')).toBe('05/10');
    expect(dayLabel({ o: 1 })).toBe('');
    expect(statusLine({ state: 'ok', days_until_compare: null, generated_at: '2026-10-11T02:12:00Z' }, 7)).toBe('So với 7 ngày trước đó · Cập nhật 09:12');
    expect(statusLine({ state: 'collecting', days_until_compare: 5, generated_at: '2026-10-11T02:12:00Z' }, 7)).toBe('Đang gom dữ liệu — còn 5 ngày nữa có so sánh · Cập nhật 09:12');
    expect(statusLine({ state: 'collecting', days_until_compare: null, generated_at: 'x' }, 7)).toBe('Đang gom dữ liệu — chưa đủ ngày để so sánh');
    expect(metricMap(INSIGHTS()).size).toBe(9);
    expect(metricMap({ metrics: 'lạ' as never }).size).toBe(0);
  });

  it('buildTableRows: đủ 9 chỉ số; gom dữ liệu ⇒ kỳ trước "—"; chỉ số lỗi ⇒ "Chưa tính được"', () => {
    const rows = buildTableRows(INSIGHTS(), false);
    expect(rows).toHaveLength(9);
    expect(rows.map((r) => r.label)).toContain('Trả lời nhanh (trong 15 phút)');
    expect(rows.find((r) => r.key === 'opps_won')?.cur).toBe('3 · 420 triệu ₫');
    expect(buildTableRows(INSIGHTS(), true).every((r) => r.prev === '—')).toBe(true);
    const err = buildTableRows(INSIGHTS({}, [METRIC('messages_in', 'Tin nhận', 'count', 0, 0, { state: 'error' })]), false);
    expect(err).toHaveLength(9);
    expect(err[0].cur).toBe('—');
  });
});

// ═══ 2. Tình báo ════════════════════════════════════════════════════════════════════════════════════════════════════════

describe('Tình báo (/owner)', () => {
  it('mục lạ / lines là object / tiêu đề object ⇒ không ném, không render [object Object]; bấm mục mở rộng dòng hợp lệ', async () => {
    const weird = BRIEF({
      latest: { ...BRIEF().latest!, summary: { x: 1 } as never, sections: [{ key: 'calendar_today', title: 'Lịch hôm nay', count: 1, lines: [{ o: 1 }], group: 'key', to: '/x' } as never] },
      live: {
        at: ago(0),
        sections: [
          { key: 'weird', title: 'Mục lạ', count: 'x', lines: [{ a: 1 }, 'dòng hợp lệ'], to: '//evil.example', group: 'zzz' },
          42, null,
          { key: 'objlines', title: { x: 1 }, count: 2, lines: { k: 'v' }, group: 'key' },
          { key: 'incidents', title: 'Sự cố', count: 1, lines: [{ z: 1 }], group: 'alert' },
        ] as never,
      },
      alerts: [{ key: 'a', title: { bad: 1 }, severity: 'bad' }, { key: 'b', title: 'Sao lưu chưa chạy', severity: 'bad', raised_at: { x: 1 }, to: { y: 2 } }] as never,
    });
    mockApi({ '/owner/brief': weird });
    mount(<IntelScreen />);
    expect(await screen.findByText('Mục lạ')).toBeInTheDocument();
    expect(screen.getByTestId('intel-summary')).toHaveTextContent(FIRST_BRIEF_TEXT);
    await userEvent.click(screen.getByText('Mục lạ'));
    expect(await screen.findByText('dòng hợp lệ')).toBeInTheDocument();
    expect(screen.getByText('Sao lưu chưa chạy')).toBeInTheDocument();
    noRawObjects();
  });

  it('dải nguồn AI KHÔNG hiện khi ai_ready=true và needs_api_key=false dù progress chưa đạt', async () => {
    mockApi({ '/owner/today': TODAY({ progress: { required_done: 0, required_total: 6 } }), '/owner/brief': BRIEF({ ai_ready: true }) });
    mount(<IntelScreen />);
    expect(await screen.findByTestId('intel-summary')).toHaveTextContent('Sáng nay Sếp có nháp chờ duyệt');
    expect(screen.queryByTestId('intel-ai-strip')).toBeNull();
    expect(screen.queryByText(/Gen chưa có nguồn AI/)).toBeNull();
  });

  it('dải nguồn AI CÓ khi ai_ready=false (link /setup?buoc=nguon-ai) và khi bản tin báo needs_api_key', async () => {
    mockApi({ '/owner/brief': BRIEF({ ai_ready: false, latest: { ...BRIEF().latest!, summary: null } }) });
    const first = mount(<IntelScreen />);
    const strip = await screen.findByTestId('intel-ai-strip');
    expect(strip).toHaveTextContent('Gen chưa có nguồn AI nên chưa viết tóm tắt — Thiết lập 2 phút');
    expect(within(strip).getByRole('link', { name: 'Thiết lập 2 phút' })).toHaveAttribute('href', AI_SETUP_PATH);
    expect(screen.getByTestId('intel-summary')).toHaveTextContent(FIRST_BRIEF_TEXT); // summary null ⇒ câu thay thế
    first.unmount();
    vi.unstubAllGlobals();
    queryClient.clear();
    mockApi({ '/owner/brief': BRIEF({ ai_ready: true, latest: { ...BRIEF().latest!, needs_api_key: true } }) });
    mount(<IntelScreen />);
    expect(await screen.findByTestId('intel-ai-strip')).toBeInTheDocument();
  });

  it('chưa có bản tin (latest = null) ⇒ câu "Bản tin đầu tiên lúc 07:30…" VÀ vẫn có số từ live + kpis', async () => {
    mockApi({ '/owner/brief': BRIEF({ latest: null, history: [] }) });
    mount(<IntelScreen />);
    expect(await screen.findByText('Bản tin đầu tiên lúc 07:30. Dưới đây là số liệu lúc này.')).toBeInTheDocument();
    expect(screen.queryByTestId('intel-brief-history')).toBeNull(); // chưa có bản nào để xem lại
    // số từ `live`: Điểm chính có "Khách nóng 3"
    const row = await screen.findByTestId('intel-key-hot_customers');
    expect(within(row).getByText('3')).toBeInTheDocument();
    // số từ kpis của /owner/today
    expect(await screen.findByTestId('intel-tile-hot')).toHaveTextContent('3');
    expect(screen.getByTestId('intel-tile-opps')).toHaveTextContent('12');
    expect(screen.getByTestId('intel-tile-opps')).toHaveTextContent('2,4 tỷ ₫');
    expect(screen.getByTestId('intel-tile-promises')).toHaveTextContent('1');
    expect(screen.getByTestId('intel-tile-promises')).toHaveAttribute('href', '/owner/viec');
    noRawObjects();
  });

  it('channels_connected = 0 ⇒ đúng MỘT FirstRunCard, không chồng EmptyState, không tile / tin / khách; giữ hàng 1 và "Việc phải quyết" khi có mục', async () => {
    state.drafts = [];
    state.proposals = [];
    mockApi({ '/owner/brief': BRIEF({ latest: null, history: [], channels_connected: 0, alerts: [], live: { at: ago(0), sections: [] } }), '/owner/today': TODAY({ nav: { urgent_open: 0, decide: 0 } }) });
    const first = mount(<IntelScreen />);
    expect(await screen.findAllByTestId('first-run-card')).toHaveLength(1);
    expect(screen.getByTestId('intel-summary')).toHaveTextContent(FIRST_BRIEF_TEXT);
    expect(document.querySelectorAll('.gh-state')).toHaveLength(0); // không có EmptyState nào chồng lên
    for (const id of ['intel-tile-hot', 'intel-tile-messages', 'intel-feed', 'intel-care', 'intel-key', 'intel-decide', 'intel-alerts']) expect(screen.queryByTestId(id), id).toBeNull();
    first.unmount();
    vi.unstubAllGlobals();
    queryClient.clear();
    state.drafts = [DRAFT_DETAIL('d-1', 'Báo giá cho khách A')];
    mockApi({ '/owner/brief': BRIEF({ latest: null, history: [], channels_connected: 0 }) });
    mount(<IntelScreen />);
    expect(await screen.findByTestId('intel-decide-count')).toHaveTextContent('1'); // nav.decide = 1 nháp (số lấy từ /owner/today)
    expect(screen.getAllByTestId('first-run-card')).toHaveLength(1);
    expect(screen.queryByTestId('intel-tile-hot')).toBeNull();
  });

  it('Duyệt nháp ở "Việc phải quyết" → ngăn DraftDecide → mã PIN → nháp biến mất và số giảm', async () => {
    state.proposals = [];
    state.drafts = [DRAFT_DETAIL('d-1', 'Báo giá cho khách A'), DRAFT_DETAIL('d-2', 'Báo giá cho khách B')];
    mockApi();
    mount(<IntelScreen />);
    expect(await screen.findByTestId('intel-decide-count')).toHaveTextContent('2');
    const rows = screen.getAllByTestId('intel-decide-row-draft');
    expect(rows).toHaveLength(2);
    const user = userEvent.setup();
    await user.click(within(rows[0]).getByRole('button', { name: 'Duyệt' }));
    const sheet = await screen.findByRole('dialog', { name: 'Duyệt bản nháp' });
    expect(await within(sheet).findByTestId('owner-draft-text')).toHaveTextContent('Dạ em gửi báo giá ván MDF ạ.');
    await user.click(within(sheet).getByRole('button', { name: 'Duyệt và gửi' }));
    await screen.findByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await user.keyboard('246810');
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Duyệt bản nháp' })).toBeNull());
    await waitFor(() => expect(screen.getByTestId('intel-decide-count')).toHaveTextContent('1'));
    expect(screen.getAllByTestId('intel-decide-row-draft')).toHaveLength(1);
  });

  it('đề xuất của Gen: "Xem" mở ProposalDecide trong ngăn; Duyệt chỉ MỞ, chưa ghi gì', async () => {
    state.drafts = [];
    const calls = mockApi();
    mount(<IntelScreen />);
    const row = await screen.findByTestId('intel-decide-row-proposal');
    await userEvent.click(within(row).getByRole('button', { name: 'Xem' }));
    const sheet = await screen.findByRole('dialog', { name: 'Đề xuất của Gen' });
    expect(within(sheet).getByTestId('owner-proposal-summary')).toHaveTextContent('Ghi phiên làm việc');
    expect(calls.some((c) => c.method === 'POST')).toBe(false);
  });

  it('Điểm chính: bấm mục ⇒ mở rộng ≤ 5 dòng + link; Cảnh báo viền bad khi có mục Khẩn; sparkline chỉ từ only=messages_in; không có Chi phí AI', async () => {
    const calls = mockApi({ '/owner/brief': BRIEF({ alerts: [{ key: 'a', title: 'Sao lưu chưa chạy', severity: 'bad', raised_at: ago(HOUR), to: '/system' }] }) });
    mount(<IntelScreen />);
    const row = await screen.findByTestId('intel-key-calendar_today');
    await userEvent.click(within(row).getByRole('button'));
    expect(within(row).getByText('09:00 · Họp giao ban')).toBeInTheDocument();
    expect(within(row).getByRole('link', { name: 'Xem tất cả' })).toBeInTheDocument();
    const alerts = await screen.findByTestId('intel-alerts');
    expect(alerts).toHaveAttribute('data-urgent', 'true');
    expect(alerts.className).toContain('intel-alerts--bad');
    expect(within(alerts).getByText('Khẩn')).toBeInTheDocument();
    const insightCalls = calls.filter((c) => c.path === '/owner/insights');
    expect(insightCalls.length).toBeGreaterThan(0);
    expect(insightCalls.every((c) => new URLSearchParams(c.search).get('only') === 'messages_in')).toBe(true);
    expect(document.body.textContent).not.toMatch(/Chi phí trợ lý Gen|Chi phí AI/);
    // tile 1 (F-60-R16): ngày chưa hết ⇒ KHÔNG so nửa ngày với cả ngày hôm qua — không %/mũi tên, ghi "tính đến <giờ>"
    const tile = await screen.findByTestId('intel-tile-messages');
    expect(tile).toHaveTextContent(/Tính đến \d{2}:\d{2} · cả ngày hôm qua \d+ tin/);
    expect(tile.textContent).not.toMatch(/so với hôm qua|%|▲|▼/);
  });

  it('cảnh báo rỗng ⇒ khối "quiet" (CSS ẩn trên điện thoại); không có mục Khẩn ⇒ không viền bad', async () => {
    mockApi({ '/owner/brief': BRIEF({ alerts: [], live: { at: ago(0), sections: [SECTION('hot_customers', 'Khách nóng', 3, [], 'key') as never] } }) });
    mount(<IntelScreen />);
    const alerts = await screen.findByTestId('intel-alerts');
    expect(alerts.className).toContain('intel-alerts--quiet');
    expect(alerts.className).not.toContain('intel-alerts--bad');
    expect(alerts).toHaveTextContent('Không có cảnh báo nào đang mở.');
  });

  it('tin quan trọng bấm → /owner/thong-tin?tin=<id>; khách cần chăm bấm → /owner/quan-he?nguoi=<person_id>', async () => {
    mockApi();
    mount(<IntelScreen />);
    const feed = await screen.findAllByTestId('intel-feed-row');
    expect(feed).toHaveLength(2);
    expect(within(feed[0]).getByRole('link')).toHaveAttribute('href', '/owner/thong-tin?tin=fd-1');
    expect(within(feed[0]).getByText('Khẩn')).toBeInTheDocument();
    const care = await screen.findAllByTestId('intel-care-row');
    expect(within(care[0]).getByRole('link')).toHaveAttribute('href', '/owner/quan-he?nguoi=p-thang');
    expect(within(care[0]).getByText('Sắp rời')).toBeInTheDocument();
    expect(within(care[1]).getByText('Lâu chưa liên lạc')).toBeInTheDocument();
  });

  it('nháp đã Tắt tiếng không tính vào "Việc phải quyết" / "Cần Sếp quyết": cả hai đọc /drafts?hide_silenced=true, số khớp today.nav.decide', async () => {
    state.drafts = [DRAFT_DETAIL('d-1', 'Nháp còn hiện'), DRAFT_DETAIL('d-quiet', 'Nháp đã tắt tiếng'), DRAFT_DETAIL('d-3', 'Nháp còn hiện 2')];
    state.silencedDrafts = new Set(['d-quiet']);
    const nav = () => TODAY({ nav: { urgent_open: 0, decide: 3 } }); // 2 nháp hiện + 1 đề xuất = số máy chủ đếm (không có nháp tắt tiếng)
    setViewport(true);
    const calls = mockApi({ '/owner/today': () => json(200, nav()) });
    const intel = mount(<IntelScreen />);
    await waitFor(() => expect(screen.getByTestId('intel-decide-count')).toHaveTextContent('3'));
    expect(screen.getByTestId('intel-decide')).toHaveTextContent('2 nháp gửi khách · 1 đề xuất của Gen');
    expect(screen.getByTestId('intel-decide')).not.toHaveTextContent('Nháp đã tắt tiếng');
    intel.unmount();
    queryClient.clear();
    mount(<WorkScreen />, '/owner/viec');
    await waitFor(() => expect(screen.getByTestId('work-decide-count')).toHaveTextContent('3'));
    expect(screen.getAllByTestId('work-draft-row')).toHaveLength(2);
    const drafts = calls.filter((c) => c.path === '/drafts' && c.method === 'GET');
    expect(drafts.length).toBeGreaterThan(0);
    expect(drafts.every((c) => new URLSearchParams(c.search).get('hide_silenced') === 'true')).toBe(true);
  });

  it('"Bản tin trước" mở danh sách lịch sử; chọn bản cũ gọi /owner/brief?id=', async () => {
    const calls = mockApi({
      '/owner/brief': (c) => (new URLSearchParams(c.search).get('id') === 'brief-0' ? json(200, BRIEF({ latest: { ...BRIEF().latest!, id: 'brief-0', summary: 'Chiều qua có 4 tin hỏi giá mới.' }, live: null })) : undefined),
    });
    mount(<IntelScreen />);
    await screen.findByText(/Sáng nay Sếp có nháp chờ duyệt/);
    const user = userEvent.setup();
    // nút là động từ (checklist "Owner 3 giây hiểu"), ngăn mở ra mang tên danh từ
    expect(screen.getByTestId('intel-brief-history')).toHaveTextContent(/^Xem bản tin trước$/);
    await user.click(screen.getByTestId('intel-brief-history'));
    const sheet = await screen.findByRole('dialog', { name: 'Bản tin trước' });
    await user.click(within(sheet).getByText('Bản tin Gen · chiều qua'));
    expect(await screen.findByText('Chiều qua có 4 tin hỏi giá mới.')).toBeInTheDocument();
    expect(calls.some((c) => c.path === '/owner/brief' && c.search.includes('id=brief-0'))).toBe(true);
    expect(await screen.findByTestId('intel-brief-old')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Xem bản tin mới nhất' }));
    expect(await screen.findByText(/Sáng nay Sếp có nháp chờ duyệt/)).toBeInTheDocument();
  });

  it('mỗi khối có lỗi RIÊNG: bản tin lỗi ⇒ hero + Cảnh báo + Điểm chính báo lỗi, còn Việc phải quyết, tile, tin, khách vẫn chạy', async () => {
    mockApi({ '/owner/brief': () => problem500() });
    mount(<IntelScreen />);
    expect(await screen.findByTestId('intel-hero-error')).toBeInTheDocument();
    expect(await screen.findByTestId('intel-alerts-error')).toBeInTheDocument();
    expect(await screen.findByTestId('intel-key-error')).toBeInTheDocument();
    expect(await screen.findByTestId('intel-decide-count')).toBeInTheDocument();
    expect(await screen.findByTestId('intel-tile-hot')).toBeInTheDocument();
    expect(await screen.findAllByTestId('intel-feed-row')).toHaveLength(2);
    expect(within(screen.getByTestId('intel-hero-error')).getAllByText('Chi tiết kỹ thuật').length).toBeGreaterThan(0);
    noRawObjects();
  });

  it('"Khách cần chăm": máy chủ báo state=error (quá giờ) ⇒ khối LỖI có Thử lại + Chi tiết kỹ thuật, KHÔNG nói "Không khách nào cần chăm gấp."', async () => {
    mockApi({ '/owner/relations': (c) => (c.search.includes('list=care') ? json(200, { list: 'care', items: [], state: 'error' }) : undefined) });
    mount(<IntelScreen />);
    const err = await screen.findByTestId('intel-care-error');
    expect(within(err).getByRole('button', { name: 'Thử lại' })).toBeInTheDocument();
    expect(within(err).getAllByText('Chi tiết kỹ thuật').length).toBeGreaterThan(0);
    expect(screen.queryByText('Không khách nào cần chăm gấp.')).toBeNull();
    noRawObjects();
  });

  it('"Khách cần chăm" dùng MỘT khoá truy vấn cho mọi nơi (Tình báo 3 · Việc 5 · Quan hệ 20): một yêu cầu limit=20, cắt ở máy khách', async () => {
    const calls = mockApi();
    function Both() {
      const three = useOwnerRelations('care', 3);
      const five = useOwnerRelations('care', 5);
      const twenty = useOwnerRelations('care', 20);
      return <div data-testid="care-n">{[three, five, twenty].map((q) => q.data?.items.length ?? '-').join(',')}</div>;
    }
    mount(<Both />);
    await waitFor(() => expect(screen.getByTestId('care-n')).toHaveTextContent('2,2,2')); // CARE mẫu có 2 dòng
    const careCalls = calls.filter((c) => c.path === '/owner/relations' && c.search.includes('list=care'));
    expect(careCalls).toHaveLength(1);
    expect(careCalls[0].search).toContain('limit=20');
  });

  it('khối rỗng: một câu + một nút; khối tải: khung chờ', async () => {
    mockApi({ '/owner/feed': FEED({ items: [] }), '/owner/relations': { list: 'care', items: [] } });
    mount(<IntelScreen />);
    expect(screen.getByTestId('intel-hero-loading')).toBeInTheDocument();
    const feed = await screen.findByTestId('intel-feed');
    expect(feed).toHaveTextContent('Không có tin cần Sếp để ý. Gen đã lọc 40 tin.');
    expect(within(feed).getAllByRole('link', { name: 'Xem tất cả' }).length).toBeGreaterThan(0);
    expect(await screen.findByTestId('intel-care')).toHaveTextContent('Không khách nào cần chăm gấp.');
  });
});

// ═══ 3. Việc ════════════════════════════════════════════════════════════════════════════════════════════════════════════

describe('Việc (/owner/viec)', () => {
  it('máy tính: Cần Sếp quyết = nháp (vượt ngưỡng lên đầu) + đề xuất; hold_reason + 2 dòng đầu; bên phải Việc hôm nay + Gen đề xuất', async () => {
    setViewport(true);
    mockApi();
    mount(<WorkScreen />, '/owner/viec');
    expect(await screen.findAllByTestId('work-draft-row')).toHaveLength(2);
    await waitFor(() => expect(screen.getAllByTestId('work-draft-row')[0]).toHaveTextContent('Báo giá lớn cho khách B')); // over_threshold lên đầu sau khi có chi tiết
    const rows = screen.getAllByTestId('work-draft-row');
    expect(within(rows[0]).getByText('Trên mức tự gửi')).toBeInTheDocument();
    expect(within(rows[0]).getByTestId('work-draft-hold')).toHaveTextContent('vượt ngưỡng 50.000.000 ₫');
    expect(within(rows[0]).getByTestId('work-draft-preview')).toHaveTextContent('Dạ em gửi báo giá ván MDF ạ.');
    expect(rows[0]).toHaveTextContent('Gửi Zalo · Chị Lan');
    expect(screen.getByTestId('work-decide-count')).toHaveTextContent('3');
    const prop = screen.getByTestId('work-proposal-row');
    expect(within(prop).getByText(/Hết hạn sau 20 giờ/)).toBeInTheDocument();
    expect(within(prop).getByRole('button', { name: 'Xác nhận' })).toBeInTheDocument();
    expect(await screen.findByTestId('work-today')).toHaveTextContent('Gọi lại chị Lan về hợp đồng');
    expect(await screen.findByTestId('work-suggest')).toHaveTextContent('Nhắc gọi hỏi thăm Bùi Đức Thắng');
    noRawObjects();
  });

  it('Xem và duyệt mở rộng DraftDecide tại chỗ; Duyệt và gửi → mã PIN → nháp biến mất', async () => {
    setViewport(true);
    state.proposals = [];
    state.drafts = [DRAFT_DETAIL('d-1', 'Báo giá cho khách A')];
    mockApi();
    mount(<WorkScreen />, '/owner/viec');
    const user = userEvent.setup();
    await user.click(await screen.findByTestId('work-draft-toggle'));
    const decide = await screen.findByTestId('owner-draft-decide');
    await user.click(await within(decide).findByRole('button', { name: 'Duyệt và gửi' }));
    await screen.findByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await user.keyboard('246810');
    await waitFor(() => expect(screen.queryByTestId('work-draft-row')).toBeNull());
    expect(await screen.findByText('Không có gì chờ Sếp duyệt.')).toBeInTheDocument();
  });

  it('Xác nhận một đề xuất Gen (needs_pin) → hộp mã PIN → đề xuất biến mất', async () => {
    setViewport(true);
    state.drafts = [];
    mockApi();
    mount(<WorkScreen />, '/owner/viec');
    const user = userEvent.setup();
    const prop = await screen.findByTestId('work-proposal-row');
    await user.click(within(prop).getByRole('button', { name: 'Xác nhận' }));
    await screen.findByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await user.keyboard('246810');
    await waitFor(() => expect(screen.queryByTestId('work-proposal-row')).toBeNull());
  });

  it('tick Xong ⇒ PATCH status=done và việc biến mất; Dời sang mai ⇒ PATCH due_at sau hôm nay', async () => {
    setViewport(true);
    state.tasks = [TASK('t-1', 'Gọi lại chị Lan'), TASK('t-2', 'Gửi báo giá cho anh Hậu')];
    const calls = mockApi();
    mount(<WorkScreen />, '/owner/viec');
    const user = userEvent.setup();
    await user.click(await screen.findByRole('checkbox', { name: 'Xong: Gọi lại chị Lan' }));
    await waitFor(() => expect(screen.queryByText('Gọi lại chị Lan')).toBeNull());
    expect(calls.find((c) => c.method === 'PATCH' && c.path === '/tasks/t-1')?.body).toEqual({ status: 'done' });

    const row = screen.getByText('Gửi báo giá cho anh Hậu').closest('[data-testid="work-task-row"]') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: 'Dời sang mai' }));
    await waitFor(() => expect(screen.queryByText('Gửi báo giá cho anh Hậu')).toBeNull());
    const patch = calls.find((c) => c.method === 'PATCH' && c.path === '/tasks/t-2');
    expect(typeof (patch?.body as { due_at?: string }).due_at).toBe('string');
    expect(Date.parse((patch?.body as { due_at: string }).due_at)).toBeGreaterThan(Date.now());
    expect(await screen.findByText('Hôm nay không có việc đến hạn.')).toBeInTheDocument();
  });

  it('lời hứa quá hạn: tick Xong ghi "đã giữ lời"; việc có người liên quan có "Mở hồ sơ" → /owner/quan-he?nguoi=', async () => {
    setViewport(true);
    state.tasks = [TASK('t-1', 'Gọi lại chị Lan')];
    state.promises = [{ id: 'pr-1', text: 'Gửi mẫu gạch cho anh Bảo', due_at: ago(26 * HOUR), kept_at: null, broken: true, from: { id: 'p-bao', code: 'P', name: 'Bảo', type: 'customer', org_name: null }, to: null, evidence: null }];
    const calls = mockApi({
      'PATCH /tasks/promises/pr-1': () => {
        state.promises = [];
        return json(200, { ok: true });
      },
    });
    mount(<WorkScreen />, '/owner/viec');
    const user = userEvent.setup();
    const link = await screen.findAllByRole('link', { name: 'Mở hồ sơ' });
    expect(link[0]).toHaveAttribute('href', '/owner/quan-he?nguoi=p-bao');
    await user.click(screen.getByRole('checkbox', { name: 'Xong: Gửi mẫu gạch cho anh Bảo' }));
    await waitFor(() => expect(screen.queryByText('Gửi mẫu gạch cho anh Bảo')).toBeNull());
    expect(calls.find((c) => c.path === '/tasks/promises/pr-1')?.body).toEqual({ kept: true });
  });

  it('Gen đề xuất: "Tạo nhắc" ⇒ POST /tasks có đối tượng người + hạn 09:00 sáng mai; chỉ tạo khi Sếp bấm', async () => {
    setViewport(true);
    const calls = mockApi();
    mount(<WorkScreen />, '/owner/viec');
    const user = userEvent.setup();
    const row = (await screen.findAllByTestId('work-suggest-row'))[0];
    expect(calls.some((c) => c.method === 'POST' && c.path === '/tasks')).toBe(false);
    await user.click(within(row).getByRole('button', { name: /Tạo nhắc/ }));
    await within(row).findByTestId('work-suggest-made');
    const post = calls.find((c) => c.method === 'POST' && c.path === '/tasks');
    expect(post?.body).toMatchObject({ title: 'Gọi hỏi thăm Bùi Đức Thắng', subject: { type: 'person', id: 'p-thang' } });
    expect(Date.parse((post?.body as { due_at: string }).due_at)).toBeGreaterThan(Date.now());
  });

  it('Việc hôm nay: việc quá hạn tạo sớm KHÔNG rơi khỏi 50 việc mới nhất — lọc hạn ở máy chủ (due_before = đầu ngày mai)', async () => {
    setViewport(true);
    const newer = Array.from({ length: 60 }, (_, i) => TASK(`n-${i}`, `Việc xa ${i}`, { due_at: later(10 * 24 * HOUR), overdue: false, created_at: ago(HOUR - i * MIN) }));
    state.tasks = [TASK('old', 'Việc quá hạn tạo từ lâu', { created_at: ago(30 * 24 * HOUR), due_at: ago(3 * 24 * HOUR) }), ...newer];
    const calls = mockApi();
    mount(<WorkScreen />, '/owner/viec');
    expect(await screen.findByText('Việc quá hạn tạo từ lâu')).toBeInTheDocument();
    expect(screen.queryByText('Việc xa 0')).toBeNull();
    const req = calls.filter((c) => c.path === '/tasks' && c.method === 'GET').map((c) => new URLSearchParams(c.search));
    expect(req.map((q) => q.get('status')).sort()).toEqual(['doing', 'todo']);
    for (const q of req) {
      expect(q.get('limit')).toBe('200');
      expect(q.get('due_before')).toBe(tomorrowStartIso(Date.now()));
    }
    expect(screen.queryByTestId('work-today-truncated')).toBeNull();
  });

  it('Việc hôm nay: máy chủ còn nhiều việc đến hạn hơn trang tải được ⇒ báo "còn N việc", không im lặng', async () => {
    setViewport(true);
    mockApi({ '/tasks': (c) => (new URLSearchParams(c.search).get('status') === 'todo' ? json(200, { items: [TASK('t-1', 'Gọi lại chị Lan')], next_cursor: 'x', total: 251 }) : undefined) });
    mount(<WorkScreen />, '/owner/viec');
    expect(await screen.findByTestId('work-today-truncated')).toHaveTextContent('Còn 250 việc đến hạn nữa');
  });

  it('Gen đề xuất: người ĐÃ có nhắc đang mở không bị gợi ý lại sau khi tải lại; vừa tạo trong phiên vẫn hiện "Đã tạo nhắc"', async () => {
    setViewport(true);
    const reminded = new Set<string>();
    const careNow = (): OwnerRelations => ({ ...CARE, items: CARE.items.map((r) => (reminded.has(String(r.person_id)) ? { ...r, has_open_task: true } : r)) });
    const calls = mockApi({
      '/owner/relations': () => json(200, careNow()),
      'POST /tasks': (c) => {
        reminded.add(String((c.body as { subject?: { id?: string } }).subject?.id));
        return undefined;
      },
    });
    const first = mount(<WorkScreen />, '/owner/viec');
    const user = userEvent.setup();
    const row = (await screen.findAllByTestId('work-suggest-row'))[0];
    await user.click(within(row).getByRole('button', { name: /Tạo nhắc/ }));
    await within(row).findByTestId('work-suggest-made');
    await waitFor(() => expect(calls.filter((c) => c.path === '/owner/relations').length).toBeGreaterThan(1)); // đã tải lại sau khi ghi
    expect(screen.getAllByTestId('work-suggest-row')).toHaveLength(2); // dòng vừa tạo vẫn còn trong phiên
    first.unmount();
    // "tải lại trang": truy vấn mới ⇒ Bùi Đức Thắng đã có nhắc ⇒ không còn gợi ý cho họ
    queryClient.clear();
    mount(<WorkScreen />, '/owner/viec');
    await screen.findByTestId('work-suggest');
    await waitFor(() => expect(screen.getAllByTestId('work-suggest-row')).toHaveLength(1));
    expect(screen.getByTestId('work-suggest')).not.toHaveTextContent('Bùi Đức Thắng');
    expect(screen.getByTestId('work-suggest')).toHaveTextContent('Đặng Hữu Trí');
  });

  it('điện thoại: Segmented "Cần quyết (n) · Hôm nay (n) · Gen đề xuất (n)", mỗi lần một mục', async () => {
    mockApi();
    mount(<WorkScreen />, '/owner/viec');
    const group = await screen.findByRole('radiogroup', { name: 'Chọn mục việc' });
    await waitFor(() => expect(within(group).getByRole('radio', { name: 'Cần quyết (3)' })).toBeInTheDocument());
    await waitFor(() => expect(within(group).getByRole('radio', { name: 'Hôm nay (1)' })).toBeInTheDocument());
    await waitFor(() => expect(within(group).getByRole('radio', { name: 'Gen đề xuất (2)' })).toBeInTheDocument());
    expect(screen.queryByTestId('work-today')).toBeNull();
    const user = userEvent.setup();
    await user.click(within(group).getByRole('radio', { name: 'Hôm nay (1)' }));
    expect(await screen.findByTestId('work-today')).toBeInTheDocument();
    expect(screen.queryByTestId('work-decide')).toBeNull();
    await user.click(within(group).getByRole('radio', { name: 'Gen đề xuất (2)' }));
    expect(await screen.findByTestId('work-suggest')).toBeInTheDocument();
  });

  it('rỗng: "Không có gì chờ Sếp duyệt." · "Hôm nay không có việc đến hạn."; lỗi riêng từng khối kèm Chi tiết kỹ thuật', async () => {
    setViewport(true);
    state.drafts = [];
    state.proposals = [];
    state.tasks = [];
    mockApi({ '/owner/relations': () => problem500() });
    mount(<WorkScreen />, '/owner/viec');
    expect(await screen.findByText('Không có gì chờ Sếp duyệt.')).toBeInTheDocument();
    expect(await screen.findByText('Hôm nay không có việc đến hạn.')).toBeInTheDocument();
    const err = await screen.findByTestId('work-suggest-error');
    expect(within(err).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    noRawObjects();
  });

  it('Việc hôm nay: lời hứa lỗi mà không có việc nào ⇒ vẫn hiện dòng báo lỗi (Thử lại + Chi tiết kỹ thuật), không chỉ "không có việc"', async () => {
    setViewport(true);
    state.tasks = [];
    mockApi({ '/tasks/promises': () => problem500() });
    mount(<WorkScreen />, '/owner/viec');
    const note = await screen.findByTestId('work-today-partial');
    expect(screen.getByText('Hôm nay không có việc đến hạn.')).toBeInTheDocument();
    expect(note).toHaveTextContent('Chưa tải được danh sách lời hứa');
    expect(within(note).getByRole('button', { name: 'Thử lại' })).toBeInTheDocument();
    expect(within(note).getAllByText('Chi tiết kỹ thuật').length).toBeGreaterThan(0);
    noRawObjects();
  });

  it('Việc hôm nay: chỉ MỘT nguồn việc (doing) lỗi ⇒ báo một phần, các việc đã tải vẫn hiện', async () => {
    setViewport(true);
    mockApi({ '/tasks': (c) => (c.method === 'GET' && c.search.includes('status=doing') ? problem500() : undefined) });
    mount(<WorkScreen />, '/owner/viec');
    const note = await screen.findByTestId('work-today-partial');
    expect(note).toHaveTextContent('Chưa tải được một phần danh sách việc');
    expect(within(note).getAllByText('Chi tiết kỹ thuật').length).toBeGreaterThan(0);
  });

  it('DecideList tách riêng: nháp và đề xuất lỗi cả hai ⇒ MỘT OwnerError có nút Thử lại', async () => {
    mockApi({ '/drafts': () => problem500(), '/owner/proposals': () => problem500() });
    mount(<WorkListHarness />, '/owner/viec');
    expect(await screen.findByTestId('work-decide-error')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Thử lại' })).toBeInTheDocument();
  });
});

describe('"Cần Sếp quyết" không bao giờ nói "không có gì" khi một nguồn chưa tải được (F-60-R5)', () => {
  const EMPTY_TEXT = 'Không có gì chờ Sếp duyệt.';

  it('đề xuất rỗng + nháp LỖI ⇒ không có EmptyState, có OwnerError với Thử lại + Chi tiết kỹ thuật', async () => {
    mockApi({ '/drafts': () => problem500(), '/owner/proposals': { items: [] } });
    mount(<WorkListHarness />, '/owner/viec');
    const err = await screen.findByTestId('work-decide-error');
    expect(screen.queryByText(EMPTY_TEXT)).toBeNull();
    expect(within(err).getByRole('button', { name: 'Thử lại' })).toBeInTheDocument();
    expect(within(err).getAllByText('Chi tiết kỹ thuật').length).toBeGreaterThan(0);
    noRawObjects();
  });

  it('nháp đang tải (đề xuất đã xong, rỗng) ⇒ khung chờ, chưa có EmptyState', async () => {
    mockApi({ '/drafts': () => new Promise<Response>(() => {}) as unknown as Response, '/owner/proposals': { items: [] } });
    mount(<WorkListHarness />, '/owner/viec');
    expect(await screen.findByTestId('work-decide-loading')).toBeInTheDocument();
    expect(screen.queryByText(EMPTY_TEXT)).toBeNull();
  });

  it('nháp rỗng + đề xuất lỗi hoặc máy chủ báo unavailable ⇒ không EmptyState, có Thử lại + Chi tiết kỹ thuật', async () => {
    state.drafts = [];
    mockApi({ '/owner/proposals': () => problem500() });
    const first = mount(<WorkListHarness />, '/owner/viec');
    const err = await screen.findByTestId('work-decide-error');
    expect(screen.queryByText(EMPTY_TEXT)).toBeNull();
    expect(within(err).getByRole('button', { name: 'Thử lại' })).toBeInTheDocument();
    first.unmount();

    queryClient.clear();
    mockApi({ '/owner/proposals': { items: [], state: 'unavailable' } });
    mount(<WorkListHarness />, '/owner/viec');
    await screen.findByTestId('work-decide-error');
    expect(screen.queryByText(EMPTY_TEXT)).toBeNull();
  });

  it('có nháp nhưng đề xuất lỗi ⇒ vẫn hiện nháp + ghi chú một phần có Thử lại và Chi tiết kỹ thuật', async () => {
    state.drafts = [DRAFT_DETAIL('d1', 'Báo giá MDF')];
    mockApi({ '/owner/proposals': () => problem500() });
    mount(<WorkListHarness />, '/owner/viec');
    const note = await screen.findByTestId('work-decide-partial');
    expect(within(note).getByRole('button', { name: 'Thử lại' })).toBeInTheDocument();
    expect(within(note).getAllByText('Chi tiết kỹ thuật').length).toBeGreaterThan(0);
    expect(screen.queryByText(EMPTY_TEXT)).toBeNull();
  });

  it('cả hai xong và rỗng thật ⇒ mới nói "Không có gì chờ Sếp duyệt."', async () => {
    state.drafts = [];
    state.proposals = [];
    mockApi();
    mount(<WorkListHarness />, '/owner/viec');
    expect(await screen.findByText(EMPTY_TEXT)).toBeInTheDocument();
  });

  it('DecideBlock (Tình báo): today.nav vắng + nháp lỗi + đề xuất rỗng ⇒ lỗi có Thử lại, không "Không có gì…"', async () => {
    mockApi({
      '/drafts': () => problem500(),
      '/owner/proposals': { items: [] },
      '/owner/today': TODAY({ nav: undefined as never }),
      '/owner/brief': BRIEF({ live: { at: ago(0), sections: [] } }),
    });
    mount(<DecideBlockHarness />, '/owner');
    const err = await screen.findByTestId('intel-decide-error');
    expect(screen.queryByText(EMPTY_TEXT)).toBeNull();
    expect(within(err).getByRole('button', { name: 'Thử lại' })).toBeInTheDocument();
    expect(within(err).getAllByText('Chi tiết kỹ thuật').length).toBeGreaterThan(0);
  });

  it('DecideBlock: nav.proposals_unavailable ⇒ ghi chú một phần "Chưa đọc được đề xuất của Gen" có Thử lại', async () => {
    state.drafts = [DRAFT_DETAIL('d1', 'Báo giá MDF')];
    mockApi({ '/owner/today': TODAY({ nav: { urgent_open: 0, decide: 1, proposals_unavailable: true } }), '/owner/proposals': { items: [], state: 'unavailable' } });
    mount(<DecideBlockHarness />, '/owner');
    const note = await screen.findByTestId('intel-decide-partial');
    expect(note).toHaveTextContent('Chưa đọc được đề xuất của Gen');
    expect(within(note).getByRole('button', { name: 'Thử lại' })).toBeInTheDocument();
  });
});

function DecideBlockHarness() {
  const drafts = useDrafts('pending');
  const proposals = useOwnerProposals();
  const today = useOwnerToday();
  const brief = useOwnerBrief();
  return <DecideBlock drafts={drafts} proposals={proposals} today={today} brief={brief} />;
}

function WorkListHarness() {
  const drafts = useDrafts('pending');
  const proposals = useOwnerProposals();
  return <DecideList drafts={drafts} proposals={proposals} />;
}

// ═══ 4. Phân tích ═══════════════════════════════════════════════════════════════════════════════════════════════════════

describe('Phân tích (/owner/phan-tich)', () => {
  it('tổng chi phí = 0 mà unpriced_calls > 0 ⇒ "Chưa tính được" (KHÔNG "0 ₫") + "N lượt chưa đặt giá — chi phí thật có thể cao hơn" + link /api', async () => {
    const metrics = INSIGHTS().metrics.map((m) => (m.key === 'ai_cost_vnd' ? { ...m, total: 0, prev_total: 0, delta_pct: null, series: days(7, () => 0), prev_series: days(7, () => 0, 7) } : m));
    mockApi({ '/owner/insights': INSIGHTS({ ai_cost: { calls: 12, unpriced_calls: 12, budget_vnd: 40_000 } }, metrics) });
    mount(<AnalyticsScreen />, '/owner/phan-tich');
    const card = await screen.findByTestId('an-chart-cost');
    expect(within(card).getByTestId('an-cost-total')).toHaveTextContent('Tổng Chưa tính được');
    expect(within(card).getByTestId('an-cost-unknown')).toHaveTextContent('Chưa tính được');
    expect(card.textContent).not.toContain('0 ₫');
    expect(within(card).getByTestId('an-unpriced')).toHaveTextContent('12 lượt chưa đặt giá — chi phí thật có thể cao hơn');
    expect(within(card).getByRole('link', { name: 'Đặt giá trong Cài đặt nâng cao' })).toHaveAttribute('href', '/api');
    // bảng: ô chi phí cũng không phải "0 ₫"
    const row = screen.getAllByTestId('kit-list-row').find((r) => r.textContent?.includes('Chi phí trợ lý Gen'));
    expect(row).toBeTruthy();
    expect(row!.textContent).toContain('Chưa tính được');
    expect(row!.textContent).not.toContain('0 ₫');
  });

  it('có giá: tổng bằng tiền, trần ngân sách trong chú giải, chú "lượt chưa đặt giá" khi còn lượt chưa có giá; đánh giá chỉ ở 7 ngày', async () => {
    mockApi({
      '/owner/insights': (c) => {
        const month = new URLSearchParams(c.search).get('range') === 'month';
        return json(200, INSIGHTS({ range: month ? 'month' : 'week', ai_cost: { calls: 40, unpriced_calls: 3, budget_vnd: 40_000 }, feedback: month ? null : { helpful: 9, not_helpful: 2 } }));
      },
    });
    mount(<AnalyticsScreen />, '/owner/phan-tich');
    const card = await screen.findByTestId('an-chart-cost');
    expect(within(card).getByTestId('an-cost-total')).toHaveTextContent('Tổng 184.000 ₫');
    expect(card).toHaveTextContent('Trần ngân sách 40.000 ₫ một ngày');
    expect(within(card).getByTestId('an-unpriced')).toHaveTextContent('3 lượt chưa đặt giá');
    expect(within(card).getByTestId('an-feedback')).toHaveTextContent('9 hữu ích');
    await userEvent.click(screen.getByRole('radio', { name: '30 ngày' }));
    await waitFor(() => expect(screen.getByTestId('an-status-line')).toHaveTextContent('So với 30 ngày trước đó'));
    expect(within(screen.getByTestId('an-chart-cost')).queryByTestId('an-feedback')).toBeNull(); // chỉ ở 7 ngày
  });

  it('chuyển 7 ↔ 30 ngày gọi lại /owner/insights?range=month và đổi chữ "7 ngày này" → "30 ngày này"', async () => {
    const calls = mockApi({
      '/owner/insights': (c) => {
        const month = new URLSearchParams(c.search).get('range') === 'month';
        return json(200, INSIGHTS({ range: month ? 'month' : 'week' }, month ? [METRIC('messages_in', 'Tin nhận', 'count', 3333, 3000, { series: days(30, () => 100), prev_series: days(30, () => 90, 30) }), ...INSIGHTS().metrics.slice(1)] : undefined));
      },
    });
    mount(<AnalyticsScreen />, '/owner/phan-tich');
    expect(await screen.findByTestId('an-tile-messages_in')).toHaveTextContent('812');
    expect(screen.getByTestId('an-status-line')).toHaveTextContent('So với 7 ngày trước đó');
    expect(screen.getByTestId('an-status-line')).toHaveTextContent(/Cập nhật \d{2}:\d{2}/);
    await userEvent.click(screen.getByRole('radio', { name: '30 ngày' }));
    await waitFor(() => expect(screen.getByTestId('an-tile-messages_in')).toHaveTextContent('3.333'));
    expect(calls.some((c) => c.path === '/owner/insights' && c.search.includes('range=month'))).toBe(true);
    expect(screen.getByTestId('an-tile-messages_in')).toHaveTextContent('so với 30 ngày trước');
  });

  it('đang gom dữ liệu: KHÔNG có huy hiệu chênh lệch, có câu "Đang gom dữ liệu — còn N ngày nữa có so sánh", không vẽ kỳ trước', async () => {
    const metrics = INSIGHTS().metrics.map((m) => ({ ...m, prev_total: 0, delta_pct: null, prev_series: days(7, () => 0, 7) }));
    mockApi({ '/owner/insights': INSIGHTS({ state: 'collecting', days_until_compare: 5 }, metrics) });
    mount(<AnalyticsScreen />, '/owner/phan-tich');
    expect(await screen.findByTestId('an-tile-messages_in')).toBeInTheDocument();
    expect(screen.getByTestId('an-status-line')).toHaveTextContent('Đang gom dữ liệu — còn 5 ngày nữa có so sánh');
    expect(document.querySelectorAll('.kit-delta')).toHaveLength(0);
    expect(document.querySelectorAll('.kit-badge--ok, .kit-badge--warn')).toHaveLength(0); // không huy hiệu Tăng/Giảm ở bảng
    expect(document.body.textContent).not.toMatch(/Tăng \d|Giảm \d|Mới/);
    expect(within(screen.getByTestId('an-chart-messages')).queryByText('7 ngày trước')).toBeNull(); // không vẽ kỳ trước
    expect(document.querySelector('[data-testid="an-trend-messages"] .kit-chart__prev')).toBeNull();
  });

  it('delta null ⇒ "Mới"; chỉ số lỗi ⇒ ô đó báo lỗi thân thiện, phần còn lại vẫn chạy', async () => {
    const metrics = INSIGHTS().metrics.map((m) => (m.key === 'new_contacts' ? { ...m, prev_total: 0, delta_pct: null } : m.key === 'opps_new' ? { ...m, state: 'error' as const } : m));
    mockApi({ '/owner/insights': INSIGHTS({}, metrics) });
    mount(<AnalyticsScreen />, '/owner/phan-tich');
    const contacts = await screen.findByTestId('an-tile-new_contacts');
    expect(contacts).toHaveTextContent('Mới');
    expect(screen.getByTestId('an-tile-opps_new')).toHaveTextContent('Chưa tính được số này lúc này');
    expect(screen.getByTestId('an-tile-messages_in')).toHaveTextContent('812');
    expect(screen.getByTestId('an-tile-reply_fast_pct')).toHaveTextContent('68%');
    expect(screen.getByTestId('an-tile-reply_fast_pct')).toHaveTextContent('Giảm 5 điểm');
    noRawObjects();
  });

  it('ngày đầu (state empty) ⇒ đúng MỘT FirstRunCard; vẫn hiện tile Chi phí khi khác 0, ẩn khi bằng 0', async () => {
    const empty = INSIGHTS({ state: 'empty', channels_connected: 0, breakdown: { event_types: [], funnel: [] } }, INSIGHTS().metrics.map((m) => ({ ...m, total: 0, prev_total: 0, delta_pct: null })));
    mockApi({ '/owner/insights': empty });
    const first = mount(<AnalyticsScreen />, '/owner/phan-tich');
    expect(await screen.findAllByTestId('first-run-card')).toHaveLength(1);
    expect(screen.queryByTestId('an-tile-cost')).toBeNull();
    expect(screen.queryByTestId('an-tile-messages_in')).toBeNull();
    expect(screen.queryByTestId('an-table-card')).toBeNull();
    first.unmount();
    vi.unstubAllGlobals();
    queryClient.clear();
    const metrics = empty.metrics.map((m) => (m.key === 'ai_cost_vnd' ? { ...m, total: 5_000 } : m));
    mockApi({ '/owner/insights': { ...empty, metrics } });
    mount(<AnalyticsScreen />, '/owner/phan-tich');
    expect(await screen.findByTestId('an-tile-cost')).toHaveTextContent('5.000 ₫');
    expect(screen.getAllByTestId('first-run-card')).toHaveLength(1);
  });

  it('state empty nhưng ĐÃ nối kênh (channels_connected > 0) ⇒ không có thẻ "Nối Zalo": một câu bật nhóm lắng nghe + nút → /guide/6', async () => {
    const empty = INSIGHTS({ state: 'empty', channels_connected: 1, breakdown: { event_types: [], funnel: [] } }, INSIGHTS().metrics.map((m) => ({ ...m, total: 0, prev_total: 0, delta_pct: null })));
    mockApi({ '/owner/insights': empty });
    mount(<AnalyticsScreen />, '/owner/phan-tich');
    const card = await screen.findByTestId('an-empty-listen');
    expect(screen.queryByTestId('first-run-card')).toBeNull();
    expect(card).toHaveTextContent('Chưa có tin nào — bật nhóm lắng nghe để Gen bắt đầu đọc');
    // Ô biểu tượng đầu thẻ phải VẼ được hình (svg), không phải ô xám trơn (tên icon lạ ⇒ span rỗng).
    expect(card.querySelector('.gh-state__tile svg')).not.toBeNull();
    expect(screen.getByTestId('an-empty-listen-cta')).toHaveAttribute('href', '/guide/6');
    expect(screen.queryByTestId('an-table-card')).toBeNull();
  });

  it('bảng số liệu đủ 9 chỉ số (máy tính: <table>; điện thoại: danh sách xếp chồng) + 4 cột Chỉ số · Kỳ này · Kỳ trước · Thay đổi', async () => {
    setViewport(true);
    mockApi();
    const wide = mount(<AnalyticsScreen />, '/owner/phan-tich');
    const table = await screen.findByRole('table', { name: 'Bảng số liệu' });
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['Chỉ số', 'Kỳ này', 'Kỳ trước', 'Thay đổi']);
    expect(within(table).getAllByRole('row')).toHaveLength(10); // tiêu đề + 9 chỉ số
    for (const label of ['Tin nhận', 'Người mới liên hệ', 'Cơ hội mới', 'Tín hiệu đáng chú ý', 'Cơ hội thắng', 'Trả lời nhanh (trong 15 phút)', 'Tin khách chờ trả lời', 'Chi phí trợ lý Gen', 'Gen lọc giúp']) {
      expect(within(table).getByText(label)).toBeInTheDocument();
    }
    wide.unmount();
    vi.unstubAllGlobals();
    queryClient.clear();
    setViewport(false);
    mockApi();
    mount(<AnalyticsScreen />, '/owner/phan-tich');
    await screen.findByTestId('an-table');
    expect(screen.getAllByTestId('kit-list-row')).toHaveLength(9);
  });

  it('khối lỗi riêng: breakdown null ⇒ hai khối biểu đồ báo lỗi thân thiện, tile và bảng vẫn chạy; cả truy vấn lỗi ⇒ OwnerError', async () => {
    mockApi({ '/owner/insights': INSIGHTS({ breakdown: { event_types: null, funnel: null } }) });
    const first = mount(<AnalyticsScreen />, '/owner/phan-tich');
    expect(await screen.findByTestId('an-tile-messages_in')).toBeInTheDocument();
    expect(screen.getAllByTestId('an-block-fail')).toHaveLength(2);
    expect(screen.getByTestId('an-table')).toBeInTheDocument();
    first.unmount();
    vi.unstubAllGlobals();
    queryClient.clear();
    mockApi({ '/owner/insights': () => problem500() });
    mount(<AnalyticsScreen />, '/owner/phan-tich');
    const err = await screen.findByTestId('an-error');
    expect(within(err).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '7 ngày' })).toBeInTheDocument(); // chọn kỳ vẫn dùng được
    noRawObjects();
  });

  it('biểu đồ: Tin nhận theo ngày (cột kỳ này + đường kỳ trước), Khách hỏi gì (top 5), Phễu cơ hội (n và ₫), không NaN', async () => {
    mockApi();
    mount(<AnalyticsScreen />, '/owner/phan-tich');
    const msgs = await screen.findByTestId('an-chart-messages');
    expect(msgs.querySelectorAll('.kit-chart__bar').length).toBe(7);
    expect(msgs.querySelector('.kit-chart__prev')).not.toBeNull();
    expect(within(msgs).getByText('7 ngày trước')).toBeInTheDocument();
    expect(screen.getByTestId('an-chart-asked')).toHaveTextContent('Hỏi giá');
    const funnel = screen.getByTestId('an-chart-funnel');
    expect(funnel).toHaveTextContent('Đang đàm phán');
    expect(funnel).toHaveTextContent('2 · 470 triệu ₫');
    expect(funnel).toHaveTextContent('Đang mở 6 cơ hội, tổng giá trị 1,1 tỷ ₫');
    expect(document.body.textContent).not.toMatch(/NaN|undefined|Infinity/);
  });
});

// ═══ 5. ngôn ngữ ════════════════════════════════════════════════════════════════════════════════════════════════════════

describe('Mặt tiền không dùng chữ kỹ thuật', () => {
  it('Tình báo / Việc / Phân tích: không có model, token, failover, pipeline, score, P1–P3; nút không viết hoa toàn bộ', async () => {
    setViewport(true);
    const TECH = /\b(model|token|failover|pipeline|score|P[123])\b/i;
    const cases: Array<[ReactElement, string]> = [
      [<IntelScreen />, 'intel-feed-row'],
      [<WorkScreen />, 'work-suggest-row'],
      [<AnalyticsScreen />, 'an-chart-funnel'],
    ];
    for (const [ui, ready] of cases) {
      mockApi();
      const { unmount } = mount(ui);
      await screen.findAllByTestId(ready);
      await new Promise((r) => setTimeout(r, 50));
      expect(document.body.textContent ?? '', ready).not.toMatch(TECH);
      noRawObjects();
      for (const b of screen.queryAllByRole('button')) {
        const name = (b.textContent ?? '').trim();
        if (name) expect(name, `nút "${name}" không được viết hoa toàn bộ`).not.toBe(name.toLocaleUpperCase('vi'));
      }
      unmount();
      vi.unstubAllGlobals();
      queryClient.clear();
      setViewport(true);
    }
  });
});

function tsxFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...tsxFiles(p));
    else if (/\.tsx$/.test(name)) out.push(p);
  }
  return out;
}

describe('Biểu tượng Mặt tiền: mọi "ph ph-…" đều có trong iconRegistry (tên lạ = ô xám trơn, không hình)', () => {
  it('apps/web/src/owner/**/*.tsx chỉ dùng biểu tượng có trong bảng', () => {
    const root = resolve(__dirname, '../../src/owner');
    const missing = tsxFiles(root).flatMap((f) => {
      const src = readFileSync(f, 'utf8');
      return [...src.matchAll(/\bph ph-[a-z0-9-]+/g)].filter((m) => !hasIcon(m[0])).map((m) => `${relative(root, f)}: ${m[0]}`);
    });
    expect(missing, `Biểu tượng không có trong iconRegistry.ts:\n${missing.join('\n')}`).toEqual([]);
  });
});
