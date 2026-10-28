/**
 * v0.1.59 (G2) — Khung Mặt tiền Owner 5 trụ cột (/owner/*): mô hình thuần (đường dẫn, nav 5 + Cài đặt, navCounts phòng thủ),
 * khung (thanh trái ≥ 760px / thanh dưới < 760px, số đếm, nút Hỏi Gen, menu ảnh đại diện, vai khác Owner về "/", ?gen=),
 * ngăn Hỏi Gen (openAskGen / closeAskGen), FirstRunCard, nút "← Về Mặt tiền", cụm route; vẫn giữ test hợp đồng mock-owner và
 * các màn Hỏi Gen / Cài đặt. (Đổi tên từ owner-v0155.test.tsx. Test của Hôm nay / Việc / Quan hệ CŨ đã bỏ cùng việc gỡ chúng khỏi
 * route — G3/G4 xoá hẳn tệp màn; màn mới có test riêng của G3/G4.) Không màn nào render object (`[object Object]`).
 */
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider, createMemoryRouter, useLocation } from 'react-router-dom';
import { hasIcon } from '@gen-harness/ui';
import { useGenStore } from '../../src/gen/genStore';
import { AskGenScreen, closeAskGen, openAskGen, useAskGenStore } from '../../src/owner/AskGenScreen';
import { MoreScreen } from '../../src/owner/MoreScreen';
import { FirstRunCard } from '../../src/owner/parts';
import {
  ADVANCED_SETTINGS_PATH,
  OWNER_NAV,
  OWNER_PATHS,
  OWNER_TABBAR,
  activeNavKey,
  asText,
  connectionLines,
  countText,
  filterValueText,
  kpiCards,
  moneyText,
  navBadge,
  navCounts,
  ownerTitle,
  progressText,
  safeLink,
} from '../../src/owner/ownerModel';
import { ownerEndpoints } from '@gen-harness/contracts';
import { createMock as createOwnerMock } from '../mock-owner';
import type { P2Ctx } from '../mock-phase2';

// Các ca trong tệp này viết cho trạng thái ĐÃ theo dõi lời hứa (`PROMISES_TRACKED = true`); mặc định hiện tại là false
// (v0.1.71, P3-4) — trạng thái ẩn được kiểm ở `promises-hidden-v0171.test.tsx`.
vi.mock('../../src/lib/features', () => ({ PROMISES_TRACKED: true }));


// ── dữ liệu mẫu: lấy THẲNG từ mock-owner (nếu mock và hợp đồng lệch nhau thì test này đỏ) ─────────────────────────────

const ME = (over: Record<string, unknown> = {}, role = 'owner') => ({
  id: 'u1', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A', role: { code: role, name: role },
  org: { id: 'o1', name: 'Genesis', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: {}, must_change_password: false,
  features: { gen: true }, ...over,
});

interface MockReply { status: number; body: unknown }

function callMock(mock: ReturnType<typeof createOwnerMock>, url: string, role = 'owner', method = 'GET'): MockReply | null {
  const u = new URL(url, 'http://x');
  let out: MockReply | null = null;
  const ctx = {
    method, path: u.pathname.replace(/^\/api\/v1/, ''), url: u, body: {}, perms: {},
    reply: (status: number, body?: unknown) => { out = { status, body }; return true; },
    problem: (status: number, code: string, title: string, extra?: Record<string, unknown>) => { out = { status, body: { status, code, title, ...extra } }; return true; },
    text: () => true, needPin: () => false, userLabel: 'x', owner: role === 'owner', role,
  } as unknown as P2Ctx;
  return mock.handle(ctx) ? out : null;
}

const BOSS = {
  rows: [
    { row: 1, key: 'hub', title: 'Gen-hub', optional: false, checks: ['hub'], done: true },
    { row: 2, key: 'facebook', title: 'Facebook', optional: false, checks: ['facebook'], done: false },
    { row: 3, key: 'agy', title: 'Google / Antigravity', optional: false, checks: [], done: false },
    { row: 5, key: 'jev', title: 'Jev', optional: true, checks: ['jev'], done: false },
    { row: 8, key: 'facebook_reply', title: 'Facebook trả lời', optional: true, checks: [], done: false },
  ],
  results: {}, required_done: 1, required_total: 6,
};

// ── máy chủ giả ─────────────────────────────────────────────────────────────────────────────────────────────────────

type Mode = 'data' | 'empty' | 'error' | 'hang';
const server = {
  mock: createOwnerMock({ fresh: false, emit: () => undefined, boss: () => BOSS as never }),
  mode: 'data' as Mode,
  me: ME() as unknown,
  meFails: false,
  boss: BOSS as unknown,
  bossMode: 'data' as Mode,
  /** v0.1.59: Cài đặt đọc GET /gen/coach/today (Việc nên làm tiếp) và GET /setup/follow-up (bước 4 = nguồn AI). */
  coachMode: 'data' as Mode,
  aiDone: true,
  calls: [] as string[],
  /** Khác undefined ⇒ GET /owner/today trả trường `nav` này thay cho của mock; `null` ⇒ BỎ hẳn `nav` (giả máy chủ cũ). */
  todayNav: undefined as unknown,
};

const COACH_TODAY = {
  date: '2026-10-16', enabled: true, snoozed_until: null, tip: null, lesson: null, unseen: false,
  progress: { required_done: 1, required_total: 2, lessons_done: 0, lessons_total: 19, stable: false, stable_since: null },
  todos: [
    { key: 'model.missing', level: 'P0', title: 'Gen chưa có nguồn AI để trả lời', why: 'Chưa có nguồn AI thì Gen chưa tóm tắt được tin.', link: '/setup?buoc=nguon-ai', can_dismiss: false },
    { key: 'boss.hub', level: 'P1', title: 'Nối Gen-hub', why: 'Để Gen đọc Kho dữ liệu.', target: 'boss_checks.row.hub', can_dismiss: true },
  ],
};

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json' } });
const problem500 = () => json(500, { status: 500, code: 'INTERNAL', title: 'Máy chủ gặp lỗi', error_id: 'ERR-TEST-1' });

function stubApi() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      server.calls.push(url);
      if (url.includes('/auth/me')) return server.meFails ? problem500() : json(200, server.me);
      if (url.includes('/owner/')) {
        if (server.mode === 'hang') return new Promise<Response>(() => undefined);
        server.mock.hooks.scenario?.({ mode: server.mode === 'empty' ? 'empty' : server.mode === 'error' ? 'error' : 'data' } as never);
        const r = callMock(server.mock, url);
        if (r && r.status === 200 && server.todayNav !== undefined && new URL(url, 'http://x').pathname.endsWith('/owner/today')) {
          if (server.todayNav === null) {
            const { nav: _omit, ...rest } = r.body as Record<string, unknown>;
            void _omit;
            return json(200, rest);
          }
          return json(200, { ...(r.body as object), nav: server.todayNav });
        }
        return r ? json(r.status, r.body) : json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
      }
      if (url.includes('/boss-checks')) {
        if (server.bossMode === 'hang') return new Promise<Response>(() => undefined);
        if (server.bossMode === 'error') return problem500();
        return json(200, server.bossMode === 'empty' ? { ...BOSS, rows: [] } : server.boss);
      }
      if (url.includes('/setup/follow-up')) return json(200, [{ n: 4, key: 'brain', title: 'Bộ não AI', status: 'skipped', done: server.aiDone }]);
      if (url.includes('/gen/coach/today')) {
        if (server.coachMode === 'hang') return new Promise<Response>(() => undefined);
        if (server.coachMode === 'error') return problem500();
        return json(200, server.coachMode === 'empty' ? { ...COACH_TODAY, todos: [] } : COACH_TODAY);
      }
      if (url.includes('/notifications')) return json(200, { items: [], unread: 0 });
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
}

/** `mobile` = dưới 760px: các query `(min-width: 760px)` / `(min-width: 1024px)` không khớp; máy tính: khớp cả hai. */
function setViewport(mobile: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: !mobile && (q.includes('min-width: 760px') || q.includes('min-width: 1024px')), media: q, onchange: null,
    addEventListener: () => undefined, removeEventListener: () => undefined,
    addListener: () => undefined, removeListener: () => undefined, dispatchEvent: () => false,
  }));
}

function Where() {
  const l = useLocation();
  return <div data-testid="where">{`${l.pathname}${l.search}`}</div>;
}

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
}

function mountScreen(ui: React.ReactElement, path = '/owner', qc: QueryClient = client()) {
  const router = createMemoryRouter([{ path: '*', element: ui }], { initialEntries: [path] });
  render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

/** Không màn nào được để lộ object thô ra chữ. */
const noRawObjects = () => expect(document.body.textContent ?? '').not.toContain('[object Object]');

beforeEach(() => {
  server.mock = createOwnerMock({ fresh: false, emit: () => undefined, boss: () => BOSS as never });
  server.mode = 'data';
  server.me = ME();
  server.meFails = false;
  server.boss = BOSS;
  server.bossMode = 'data';
  server.coachMode = 'data';
  server.aiDone = true;
  server.calls = [];
  server.todayNav = undefined;
  useAskGenStore.setState({ open: false });
  useGenStore.setState({ openByUser: {}, conversationId: null, conversationOwner: null, messages: [], busy: false, coachFocus: false, composerDraft: null });
  vi.stubGlobal('WebSocket', undefined);
  setViewport(false);
  stubApi();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

// ── mô hình thuần ───────────────────────────────────────────────────────────────────────────────────────────────────

describe('ownerModel (thuần)', () => {
  it('thanh trái 5 trụ cột + Cài đặt (vạch ngăn trước Cài đặt); thanh dưới đúng 5 trụ cột; không còn mục "sắp có"', () => {
    expect(OWNER_NAV.map((i) => i.label)).toEqual(['Tình báo', 'Thông tin', 'Quan hệ', 'Phân tích', 'Việc', 'Cài đặt']);
    expect(OWNER_NAV.map((i) => i.key)).toEqual(['intel', 'feed', 'relations', 'analytics', 'work', 'settings']);
    expect(OWNER_NAV.filter((i) => i.separatorBefore).map((i) => i.key)).toEqual(['settings']);
    expect(OWNER_TABBAR.map((i) => i.label)).toEqual(['Tình báo', 'Thông tin', 'Quan hệ', 'Phân tích', 'Việc']);
    expect(OWNER_TABBAR).toHaveLength(5);
    expect(JSON.stringify(OWNER_NAV)).not.toContain('sắp có');
    expect(OWNER_NAV.every((i) => hasIcon(i.icon))).toBe(true);
  });

  it('OWNER_PATHS: đường mới theo trụ cột; bí danh cũ today/tasks/more trỏ cùng đường', () => {
    expect(OWNER_PATHS).toMatchObject({
      intel: '/owner', feed: '/owner/thong-tin', relations: '/owner/quan-he', analytics: '/owner/phan-tich',
      work: '/owner/viec', gen: '/owner/gen', settings: '/owner/them',
    });
    expect(OWNER_PATHS.today).toBe(OWNER_PATHS.intel);
    expect(OWNER_PATHS.tasks).toBe(OWNER_PATHS.work);
    expect(OWNER_PATHS.more).toBe(OWNER_PATHS.settings);
  });

  it('mục đang sáng + tiêu đề theo đường dẫn', () => {
    expect(activeNavKey('/owner')).toBe('intel');
    expect(activeNavKey('/owner/')).toBe('intel');
    expect(activeNavKey('/owner/thong-tin')).toBe('feed');
    expect(activeNavKey('/owner/quan-he')).toBe('relations');
    expect(activeNavKey('/owner/phan-tich')).toBe('analytics');
    expect(activeNavKey('/owner/viec')).toBe('work');
    expect(activeNavKey('/owner/them')).toBe('settings');
    expect(activeNavKey('/owner/gen')).toBe('gen');
    expect(activeNavKey('/owner/khong-co')).toBe('intel');
    expect(ownerTitle('/owner')).toBe('Tình báo');
    expect(ownerTitle('/owner/thong-tin')).toBe('Thông tin');
    expect(ownerTitle('/owner/phan-tich')).toBe('Phân tích');
    expect(ownerTitle('/owner/them')).toBe('Cài đặt');
    expect(ownerTitle('/owner/gen')).toBe('Hỏi Gen');
  });

  it('navCounts đọc phòng thủ today.nav: thiếu/sai kiểu/âm/NaN ⇒ 0, không ném', () => {
    const zero = { urgent_open: 0, decide: 0 };
    for (const bad of [undefined, null, 5, 'x', [], {}, { nav: null }, { nav: 7 }, { nav: {} }, { nav: { urgent_open: '3', decide: null } }, { nav: { urgent_open: -2, decide: Number.NaN } }, { nav: { urgent_open: Infinity } }]) {
      expect(navCounts(bad)).toEqual(zero);
    }
    expect(navCounts({ nav: { urgent_open: 3, decide: 4 } })).toEqual({ urgent_open: 3, decide: 4 });
    expect(navCounts({ nav: { urgent_open: 2.9, decide: 0 }, kpis: {} })).toEqual({ urgent_open: 2, decide: 0 });
    // mỗi nút tối đa MỘT số: Thông tin ← urgent_open, Việc ← decide, các trụ cột còn lại không có số
    const c = { urgent_open: 3, decide: 4 };
    expect(OWNER_NAV.map((i) => navBadge(i.key, c))).toEqual([0, 3, 0, 0, 4, 0]);
  });

  it('safeLink chỉ nhận đường dẫn trong Console; asText không bao giờ trả object', () => {
    expect(safeLink('/profile?id=p-hau')).toBe('/profile?id=p-hau');
    expect(safeLink('/system?tab=brain#chuan')).toBe('/system?tab=brain#chuan');
    for (const bad of ['//evil.example', 'https://evil.example', 'javascript:alert(1)', '', ' /x y', '/\\evil', { a: 1 }, null, undefined, 5]) {
      expect(safeLink(bad)).toBe('/owner');
    }
    expect(safeLink('x', '/overview')).toBe('/overview');
    expect(asText({ a: 1 })).toBe('');
    expect(asText(['x'])).toBe('');
    expect(asText('ok')).toBe('ok');
    expect(asText(7)).toBe('7');
  });

  it('định dạng số/tiền/câu', () => {
    expect(countText(12)).toBe('12');
    expect(countText(999)).toBe('999+');
    expect(countText(1500)).toBe('999+');
    expect(countText(NaN)).toBe('—');
    expect(countText('3')).toBe('—');
    expect(moneyText(2_400_000_000)).toBe('2,4 tỷ ₫');
    expect(moneyText(3_500_000)).toBe('3,5 triệu ₫');
    expect(moneyText(850_000)).toBe('850.000 ₫');
    expect(moneyText(0)).toBe('0 ₫');
    expect(moneyText(null)).toBe('—');
    expect(filterValueText({ filtered: 128, calls_saved: 94 })).toBe('Tuần này Gen lọc giúp Sếp 128 tin rác/trùng, bớt 94 lượt gọi AI.');
    expect(filterValueText({ filtered: 0, calls_saved: 0 })).toBeNull();
    expect(filterValueText(null)).toBeNull();
    expect(progressText(4, 6)).toBe('Việc Sếp cần làm: 4/6 việc bắt buộc đã xong');
    expect(progressText(6, 6)).toBe('Việc Sếp cần làm: đã xong cả 6 việc bắt buộc');
    expect(progressText(undefined, 0)).toBe('Việc Sếp cần làm');
  });

  it('4 số mở đúng danh sách; số đếm chạm trần hiện "999+"', () => {
    const cards = kpiCards({ hot: 3, cooling: 2, open_opps: 12, open_value_vnd: 2_400_000_000, overdue_promises: 1000 });
    expect(cards.map((c) => c.key)).toEqual(['hot', 'cooling', 'open_opps', 'overdue_promises']);
    expect(cards.map((c) => c.to)).toEqual(['/owner/quan-he?list=hot', '/owner/quan-he?list=cooling', '/opportunity', '/tasks?ptab=overdue']);
    expect(cards[2].sub).toBe('2,4 tỷ ₫');
    expect(cards[3].value).toBe('999+');
    expect(kpiCards({ hot: 0, cooling: 0, open_opps: 0, open_value_vnd: 0, overdue_promises: 0 }).every((c) => c.tone === 'neutral')).toBe(true);
  });

  it('trạng thái kết nối: một dòng mỗi dịch vụ, Facebook → /social, bỏ dòng không phải dịch vụ', () => {
    const lines = connectionLines(BOSS.rows);
    expect(lines.map((l) => l.key)).toEqual(['hub', 'facebook', 'agy', 'jev']);
    // Dòng nguồn AI (bắt buộc duy nhất) đứng ĐẦU danh sách Kết nối, chữ riêng "Đã chạy được" / "Chưa có — cần làm".
    const withAi = connectionLines([{ key: 'ai', title: 'Có ít nhất 1 nguồn AI chạy được', optional: false, done: false }, ...BOSS.rows]);
    expect(withAi.map((l) => l.key)).toEqual(['ai', 'hub', 'facebook', 'agy', 'jev']);
    expect(withAi[0]).toMatchObject({ state: 'todo', text: 'Chưa có — cần làm', to: '/guide/viec-sep' });
    expect(connectionLines([{ key: 'ai', title: 'Nguồn AI', optional: false, done: true }])[0]).toMatchObject({ state: 'ok', text: 'Đã chạy được' });
    expect(lines[0]).toMatchObject({ state: 'ok', text: 'Đã kết nối', to: '/guide/viec-sep' });
    expect(lines[1]).toMatchObject({ state: 'todo', text: 'Chưa kết nối', to: '/social' });
    expect(lines[3]).toMatchObject({ state: 'optional', text: 'Chưa bật (tuỳ chọn)' });
    expect(connectionLines(undefined)).toEqual([]);
  });

  it('hợp đồng: chỉ GET, kèm query list/limit', async () => {
    const seen: Array<{ path: string; opts: Record<string, unknown> }> = [];
    const r = vi.fn(async (path: string, opts: Record<string, unknown> = {}) => {
      seen.push({ path, opts });
      return {} as never;
    });
    const api = ownerEndpoints(r as never);
    await api.today();
    await api.relations('cooling', 30);
    await api.tasks();
    expect(seen.map((s) => s.path)).toEqual(['/owner/today', '/owner/relations', '/owner/tasks']);
    expect(seen[1].opts.query).toEqual({ list: 'cooling', limit: 30 });
    expect(seen.every((s) => s.opts.method === undefined)).toBe(true);
    const src = readFileSync(resolve(__dirname, '../../../../packages/contracts/src/owner.ts'), 'utf8');
    expect(src).not.toMatch(/method:\s*['"](POST|PUT|PATCH|DELETE)/);
    expect(src).not.toContain("from './defaults'");
  });

  it('mock-owner khớp hợp đồng: 403 vai khác, 422 list lạ, limit bị chặn, kịch bản rỗng/lỗi', () => {
    const m = createOwnerMock({ fresh: false, emit: () => undefined });
    expect(callMock(m, '/api/v1/owner/today', 'operator')).toMatchObject({ status: 403, body: { code: 'FORBIDDEN' } });
    expect(callMock(m, '/api/v1/owner/relations?list=zzz')).toMatchObject({ status: 422, body: { code: 'VALIDATION' } });
    expect(callMock(m, '/api/v1/owner/today', 'owner', 'POST')?.status).toBe(405);
    const rel = callMock(m, '/api/v1/owner/relations?list=hot&limit=2')?.body as { items: unknown[] };
    expect(rel.items).toHaveLength(2);
    expect((callMock(m, '/api/v1/owner/relations?list=hot&limit=900')?.body as { items: unknown[] }).items).toHaveLength(3);
    m.hooks.scenario?.({ mode: 'empty' } as never);
    expect(callMock(m, '/api/v1/owner/tasks')?.body).toMatchObject({ groups: [{ count: 0, items: [] }, { count: 0 }, { count: 0 }] });
    m.hooks.scenario?.({ mode: 'error', only: 'today' } as never);
    expect(callMock(m, '/api/v1/owner/today')?.status).toBe(500);
    expect(callMock(m, '/api/v1/owner/tasks')?.status).toBe(200);
    expect((m.hooks.state?.() as { nonOwnerCalls: string[] }).nonOwnerCalls).toEqual(['operator GET /owner/today']);
  });
});

// ── 5 màn × (tải, rỗng, lỗi, có dữ liệu) ───────────────────────────────────────────────────────────────────────────

describe('Hỏi Gen (/owner/gen)', () => {
  it('đang tải (chưa biết người dùng)', async () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => undefined)));
    mountScreen(<AskGenScreen />, '/owner/gen');
    expect(await screen.findByTestId('owner-loading')).toBeInTheDocument();
  });

  it('có dữ liệu: nhúng khung chat Gen; câu gợi ý chỉ điền sẵn ô nhập', async () => {
    mountScreen(<AskGenScreen />, '/owner/gen');
    const panel = await screen.findByTestId('owner-gen-panel');
    expect(within(panel).getByLabelText('Gen — trợ lý quản trị')).toBeInTheDocument();
    expect(within(panel).getByLabelText('Câu hỏi cho Gen')).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole('complementary', { name: 'Gợi ý câu hỏi' })).getByRole('button', { name: /Khách nào hỏi giá hôm nay/ }));
    await waitFor(() => expect(within(panel).getByLabelText('Câu hỏi cho Gen')).toHaveValue('Khách nào hỏi giá hôm nay?'));
    expect(useGenStore.getState().messages).toHaveLength(0);                 // chỉ điền sẵn, không gửi
    expect(server.calls.some((u) => u.includes('/gen/ask') || u.includes('/gen/turns'))).toBe(false);
  });

  it('rỗng: Gen tắt ⇒ giải thích + link Bộ não AI', async () => {
    server.me = ME({ features: { gen: false } });
    mountScreen(<AskGenScreen />, '/owner/gen');
    expect(await screen.findByText('Gen đang tắt')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Mở Bộ não AI' })).toHaveAttribute('href', '/system?tab=brain');
    expect(screen.queryByTestId('owner-gen-panel')).not.toBeInTheDocument();
  });

  it('lỗi: không tải được tài khoản ⇒ "Chi tiết kỹ thuật"', async () => {
    server.meFails = true;
    mountScreen(<AskGenScreen />, '/owner/gen');
    expect(await screen.findByTestId('owner-error')).toHaveTextContent('Chi tiết kỹ thuật');
    noRawObjects();
  });
});

describe('Cài đặt (/owner/them) — v0.1.59: 4 khối', () => {
  it('đang tải việc nên làm tiếp', async () => {
    server.coachMode = 'hang';
    mountScreen(<MoreScreen />, '/owner/them');
    expect((await screen.findAllByTestId('owner-loading')).length).toBeGreaterThan(0);
  });

  it('có dữ liệu: đúng 4 khối; Việc nên làm tiếp là MỘT danh sách (Chưa + nút), không còn khối Kết nối; nâng cao đúng 3 lối', async () => {
    mountScreen(<MoreScreen />, '/owner/them');
    const rows = await screen.findAllByTestId('owner-more-todo-row');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('Gen chưa có nguồn AI để trả lời');
    expect(rows[0]).toHaveTextContent('Khẩn · Chưa');
    expect(rows[1]).toHaveTextContent('Quan trọng · Chưa');
    expect(within(rows[0]).getByRole('button', { name: /Làm ngay/ })).toBeInTheDocument();
    expect(screen.queryByTestId('owner-connections')).toBeNull();
    expect(screen.queryByTestId('owner-conn-row')).toBeNull();
    expect(screen.queryByRole('link', { name: /Hướng dẫn thiết lập/ })).toBeNull();
    // Nguồn AI: trạng thái + "Đổi nguồn AI" → màn Thiết lập nguồn AI.
    expect(screen.getByTestId('owner-more-ai-status')).toHaveTextContent('Đã chạy');
    expect(screen.getByRole('link', { name: /Đổi nguồn AI/ })).toHaveAttribute('href', '/setup?buoc=nguon-ai');
    // Cài đặt nâng cao: đúng 3 lối vào; "Mở Console đầy đủ" → /overview (không phải "/").
    const adv = within(screen.getByTestId('owner-more-advanced')).getAllByRole('link');
    expect(adv.map((a) => a.getAttribute('href'))).toEqual(['/api', '/system?tab=brain', ADVANCED_SETTINGS_PATH]);
    expect(screen.getByTestId('owner-advanced')).toHaveAttribute('href', '/overview');
    expect(screen.getByTestId('owner-advanced').getAttribute('href')).not.toBe('/');
    noRawObjects();
  });

  it('rỗng: hết việc ⇒ một dòng "Đã xong"; vẫn còn Cài đặt nâng cao', async () => {
    server.coachMode = 'empty';
    mountScreen(<MoreScreen />, '/owner/them');
    const row = await screen.findByTestId('owner-more-todo-row');
    expect(row).toHaveTextContent('Đã xong');
    expect(screen.getByTestId('owner-advanced')).toBeInTheDocument();       // lối vào Cài đặt nâng cao luôn có
  });

  it('lỗi: có "Chi tiết kỹ thuật", vẫn còn Cài đặt nâng cao', async () => {
    server.coachMode = 'error';
    mountScreen(<MoreScreen />, '/owner/them');
    expect(await screen.findByTestId('owner-error')).toHaveTextContent('Chi tiết kỹ thuật');
    expect(screen.getByTestId('owner-advanced')).toBeInTheDocument();
    noRawObjects();
  });
});

// ── khung ───────────────────────────────────────────────────────────────────────────────────────────────────────────

describe('Hỏi Gen: hợp đồng openAskGen / closeAskGen', () => {
  it('openAskGen(prefill) đặt composer draft và mở ngăn; closeAskGen đóng', () => {
    expect(useAskGenStore.getState().open).toBe(false);
    openAskGen('Nhờ Gen soạn tin cho khách này');
    expect(useGenStore.getState().composerDraft).toBe('Nhờ Gen soạn tin cho khách này');
    expect(useAskGenStore.getState().open).toBe(true);
    closeAskGen();
    expect(useAskGenStore.getState().open).toBe(false);
  });

  it('openAskGen() không có prefill thì không đụng composer draft', () => {
    useGenStore.getState().setComposerDraft('câu đang soạn');
    openAskGen();
    expect(useGenStore.getState().composerDraft).toBe('câu đang soạn');
    openAskGen('');
    expect(useGenStore.getState().composerDraft).toBe('câu đang soạn');
    expect(useAskGenStore.getState().open).toBe(true);
  });
});

describe('FirstRunCard (ngày đầu)', () => {
  it('đúng một thẻ: tiêu đề, một câu, nút chính "Nối Zalo" → /guide/5, link phụ mở ngăn Hỏi Gen', async () => {
    const router = mountScreen(<><FirstRunCard /><Where /></>, '/owner');
    const card = await screen.findByTestId('first-run-card');
    expect(screen.getAllByTestId('first-run-card')).toHaveLength(1);
    expect(within(card).getByRole('heading', { name: 'Nối Zalo để Gen bắt đầu đọc tin' })).toBeInTheDocument();
    expect(within(card).getByText('Sau khi nối, ở đây Sếp sẽ thấy tin quan trọng, khách cần chăm và số liệu tuần')).toBeInTheDocument();
    expect(within(card).getByRole('link', { name: 'Nối Zalo' })).toHaveAttribute('href', '/guide/5');
    // link phụ: mở ngăn Hỏi Gen, KHÔNG chuyển trang
    await userEvent.click(within(card).getByRole('link', { name: 'Xem Gen làm được gì' }));
    expect(useAskGenStore.getState().open).toBe(true);
    expect(router.state.location.pathname).toBe('/owner');
    expect(card.textContent ?? '').not.toMatch(/\b(model|token|API|P1|P2|score|pipeline|failover)\b/i);
    noRawObjects();
  });

  it('bấm "Nối Zalo" đi tới /guide/5', async () => {
    const router = mountScreen(<FirstRunCard />, '/owner');
    await userEvent.click(await screen.findByRole('link', { name: 'Nối Zalo' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/guide/5'));
  });
});

describe('biểu tượng', () => {
  it('mọi icon Mặt tiền dùng đều có trong bảng icon của @gen-harness/ui (tên lạ vẽ ra ô trống)', () => {
    const dir = resolve(__dirname, '../../src/owner');
    const names = new Set<string>();
    for (const f of readdirSync(dir)) {
      if (!/\.tsx?$/.test(f)) continue;
      for (const m of readFileSync(resolve(dir, f), 'utf8').matchAll(/['"`](ph(?:-fill)? ph-[a-z0-9-]+)['"`]/g)) names.add(m[1]);
    }
    expect(names.size).toBeGreaterThan(15);
    expect([...names].filter((n) => !hasIcon(n))).toEqual([]);
  });
});
