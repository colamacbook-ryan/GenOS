/**
 * v0.1.59 (g5-thiet-lap) — Thiết lập MỘT màn, 3 bước (1 Tài khoản · 2 Nguồn AI · 3 Xong) + Cài đặt 4 khối + mọi đích "Chọn model"
 * dồn về /setup?buoc=nguon-ai. Máy chủ giả đủ dùng (trạng thái thiết lập có thật, PUT /setup/steps/n đẩy trạng thái đi tiếp) — chỉ
 * kiểm hành vi của giao diện: ánh xạ ?step= → ?buoc=, bỏ qua bước đã xong, lỗi gắn đúng ô, "Làm sau", PUT 12 rồi mới đi tiếp.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import type { CliProfile, Provider, SetupState } from '@gen-harness/contracts';
import { GuideStepPage } from '../../src/guide/GuideStepPage';
import { setNavigator } from '../../src/lib/navigation';
import { qk } from '../../src/lib/queries';
import { queryClient } from '../../src/lib/queryClient';
import { MoreScreen } from '../../src/owner/MoreScreen';
import { ApiError } from '@gen-harness/contracts';
import { ActionError, ModelUnavailableNotice } from '../../src/screens/common';
import { NoModelNotice } from '../../src/screens/queue/NoModelBanner';
import { SetupPage } from '../../src/setup/SetupPage';
import {
  AI_LATER_KEY,
  AI_SOURCE_PATH,
  SETUP_VIEWS,
  accountReady,
  aiSettled,
  defaultView,
  legacyStepView,
  parseView,
  resolveSetupRoute,
  setupViewPath,
} from '../../src/setup/steps';

// ── trạng thái thiết lập giả ──────────────────────────────────────────────────────────────────────────────────────

function buildState(done: number[], finished = false, skipped: number[] = []): SetupState {
  const settled = new Set([...done, ...skipped]);
  let current = 12;
  for (let n = 1; n <= 12; n += 1) {
    if (!settled.has(n)) {
      current = n;
      break;
    }
  }
  return {
    finished,
    current_step: current,
    steps: Array.from({ length: 12 }, (_, i) => ({
      n: i + 1,
      key: `s${i + 1}`,
      title: `Bước ${i + 1}`,
      required: i + 1 <= 3 || i + 1 === 12,
      available: true,
      status: done.includes(i + 1) ? 'done' : skipped.includes(i + 1) ? 'skipped' : i + 1 === current ? 'doing' : 'todo',
    })),
  } as SetupState;
}
const ALL = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];

// ── thuần: ánh xạ ?step= → ?buoc= ─────────────────────────────────────────────────────────────────────────────────────

describe('steps.ts — ánh xạ bước (hàm thuần)', () => {
  it('ba bước của giao diện + đích nguồn AI duy nhất', () => {
    expect(SETUP_VIEWS.map((v) => `${v.n} ${v.label}`)).toEqual(['1 Tài khoản', '2 Nguồn AI', '3 Xong']);
    expect(AI_SOURCE_PATH).toBe('/setup?buoc=nguon-ai');
    expect(setupViewPath('xong')).toBe('/setup?buoc=xong');
    expect(parseView('tai-khoan')).toBe('tai-khoan');
    expect(parseView('4')).toBeNull();
    expect(parseView(null)).toBeNull();
  });

  it('?step= cũ: 1–3 → tai-khoan · 4 → nguon-ai · 12 → xong · 5–11 → guide · lạ → null', () => {
    expect([1, 2, 3].map(legacyStepView)).toEqual(['tai-khoan', 'tai-khoan', 'tai-khoan']);
    expect(legacyStepView(4)).toBe('nguon-ai');
    expect(legacyStepView(12)).toBe('xong');
    expect([5, 6, 7, 8, 9, 10, 11].every((n) => legacyStepView(n) === 'guide')).toBe(true);
    expect(legacyStepView(0)).toBeNull();
    expect(legacyStepView(13)).toBeNull();
    expect(legacyStepView(Number('abc'))).toBeNull();
  });

  it('bước hiện tại khi chưa xong: 1–3 chưa đủ → tai-khoan; đủ → nguon-ai; bước 4 xong hoặc "Làm sau" → xong', () => {
    expect(defaultView(buildState([]), false)).toBe('tai-khoan');
    expect(defaultView(buildState([1, 2]), true)).toBe('tai-khoan'); // "Làm sau" không nhảy cóc qua tài khoản
    expect(defaultView(buildState([1, 2, 3]), false)).toBe('nguon-ai');
    expect(defaultView(buildState([1, 2, 3]), true)).toBe('xong');
    expect(defaultView(buildState([1, 2, 3, 4]), false)).toBe('xong');
    expect(defaultView(buildState([1, 2, 3], false, [4]), false)).toBe('xong'); // đã để sau ở máy chủ
    expect(accountReady(buildState([1, 2, 3]))).toBe(true);
    expect(accountReady(buildState([1, 2]))).toBe(false);
    expect(aiSettled(buildState([1, 2, 3, 4]))).toBe(true);
    expect(aiSettled(buildState([1, 2, 3]))).toBe(false);
  });

  it('chưa xong: ?step=4 → nguon-ai · ?step=12 → xong khi đủ điều kiện · ?step=5..11 → bước hiện tại', () => {
    const s3 = buildState([1, 2, 3]);
    expect(resolveSetupRoute({ step: '4', state: s3, aiLater: false })).toEqual({ kind: 'view', view: 'nguon-ai' });
    expect(resolveSetupRoute({ step: '7', state: s3, aiLater: false })).toEqual({ kind: 'view', view: 'nguon-ai' });
    // xa hơn bước hiện tại ⇒ về bước hiện tại
    expect(resolveSetupRoute({ step: '12', state: s3, aiLater: false })).toEqual({ kind: 'view', view: 'nguon-ai' });
    expect(resolveSetupRoute({ buoc: 'xong', state: buildState([1]), aiLater: false })).toEqual({ kind: 'view', view: 'tai-khoan' });
    const s4 = buildState([1, 2, 3, 4]);
    expect(resolveSetupRoute({ step: '12', state: s4, aiLater: false })).toEqual({ kind: 'view', view: 'xong' });
    expect(resolveSetupRoute({ buoc: 'xong', state: s3, aiLater: true })).toEqual({ kind: 'view', view: 'xong' });
    // từ bước Xong quay lại đổi nguồn AI được
    expect(resolveSetupRoute({ buoc: 'nguon-ai', state: s4, aiLater: false })).toEqual({ kind: 'view', view: 'nguon-ai' });
    expect(resolveSetupRoute({ buoc: 'rac', state: s3, aiLater: false })).toEqual({ kind: 'view', view: 'nguon-ai' });
    expect(resolveSetupRoute({ state: buildState([]), aiLater: false })).toEqual({ kind: 'view', view: 'tai-khoan' });
  });

  it('đã xong: chỉ nguon-ai mở được; ?step=7 → /guide/7; ?step=4 → nguon-ai; còn lại → /owner', () => {
    const done = buildState(ALL, true);
    expect(resolveSetupRoute({ buoc: 'nguon-ai', state: done, aiLater: false })).toEqual({ kind: 'view', view: 'nguon-ai' });
    expect(resolveSetupRoute({ step: '4', state: done, aiLater: false })).toEqual({ kind: 'view', view: 'nguon-ai' });
    expect(resolveSetupRoute({ step: '7', state: done, aiLater: false })).toEqual({ kind: 'guide', n: 7 });
    expect(resolveSetupRoute({ step: '11', state: done, aiLater: false })).toEqual({ kind: 'guide', n: 11 });
    expect(resolveSetupRoute({ step: '12', state: done, aiLater: false })).toEqual({ kind: 'owner' });
    expect(resolveSetupRoute({ step: '2', state: done, aiLater: false })).toEqual({ kind: 'owner' });
    expect(resolveSetupRoute({ buoc: 'xong', state: done, aiLater: false })).toEqual({ kind: 'owner' });
    expect(resolveSetupRoute({ buoc: 'tai-khoan', state: done, aiLater: true })).toEqual({ kind: 'owner' });
    expect(resolveSetupRoute({ state: done, aiLater: false })).toEqual({ kind: 'owner' });
  });
});

// ── máy chủ giả ────────────────────────────────────────────────────────────────────────────────────────────────────────

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json' } });

interface Call {
  method: string;
  path: string;
  body: Record<string, unknown> | undefined;
}
const ME = (role = 'owner') => ({
  id: 'u1', email: 'owner@example.test', display_name: 'Anh Nguyễn Văn A (Chủ)', role: { code: role, name: role },
  org: { id: 'o1', name: 'Genesis Trading', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Sếp', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: {}, must_change_password: false, features: { gen: true },
});

interface Srv {
  done: number[];
  skipped: number[];
  finished: boolean;
  /** Đã có cookie phiên (sau PUT 2) — GET /auth/me. */
  session: boolean;
  role: string;
  providers: Provider[];
  /** Tài khoản Antigravity đã đăng nhập (GET /cli/profiles?kind=antigravity_cli). */
  agyProfiles: CliProfile[];
  /** `PUT 2` → phản hồi lỗi thay cho thành công. */
  fail: Record<string, Response>;
  firstRun: unknown;
  coach: unknown;
  calls: Call[];
}
let srv: Srv;

const GEMINI_OK: Provider = {
  id: 'pv-gemini', kind: 'gemini', name: 'Gemini API', endpoint: null, failover_rank: 1, enabled: true, auth_state: 'ok',
  keys: [{ id: 'k1', label: 'GEM-KEY-01', last4: '9f2a', enabled: true, cooldown_until: null, quota_left_pct: null }],
  models: [{ id: 'm1', model_name: 'gemini-2.5-flash', daily_quota: 3000, used_today: 0 }],
};

function startServer(init: Partial<Srv> = {}): Srv {
  srv = { done: [], skipped: [], finished: false, session: false, role: 'owner', providers: [], agyProfiles: [], fail: {}, firstRun: null, coach: null, calls: [], ...init };
  srv.session = init.session ?? srv.done.includes(2);
  const stateNow = () => buildState(srv.done, srv.finished, srv.skipped);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, req?: RequestInit) => {
      const u = new URL(String(input), 'http://x');
      const path = u.pathname.replace(/^\/api\/v1/, '');
      const method = req?.method ?? 'GET';
      const body = req?.body ? (JSON.parse(String(req.body)) as Record<string, unknown>) : undefined;
      srv.calls.push({ method, path, body });
      const key = `${method} ${path}`;
      if (srv.fail[key]) return srv.fail[key];
      if (path === '/setup/state') return json(200, stateNow());
      const skip = /^\/setup\/steps\/(\d+)\/skip$/.exec(path);
      if (skip && method === 'POST') {
        const n = Number(skip[1]);
        if (!srv.done.includes(n) && !srv.skipped.includes(n)) srv.skipped.push(n);
        return json(200, stateNow());
      }
      const put = /^\/setup\/steps\/(\d+)$/.exec(path);
      if (put && method === 'PUT') {
        const n = Number(put[1]);
        if (!srv.done.includes(n)) srv.done.push(n);
        if (n === 2) srv.session = true;
        if (n === 12) srv.finished = true;
        return json(200, stateNow());
      }
      if (path === '/auth/me') return srv.session ? json(200, ME(srv.role)) : json(401, { status: 401, code: 'UNAUTHENTICATED', title: 'Chưa đăng nhập' });
      if (path === '/providers' && method === 'GET') return json(200, srv.providers);
      if (path === '/providers' && method === 'POST') {
        const p: Provider = {
          id: 'pv-new', kind: body?.kind as Provider['kind'], name: String(body?.name ?? ''), endpoint: null, failover_rank: srv.providers.length + 1,
          enabled: true, auth_state: 'ok', keys: [{ id: 'k9', label: 'KEY', last4: '1234', enabled: true, cooldown_until: null, quota_left_pct: null }], models: [],
        };
        srv.providers.push(p);
        return json(201, p);
      }
      if (/^\/providers\/[^/]+\/test$/.test(path) && method === 'POST')
        return json(200, { ok: true, latency_ms: 321, models: ['gemini-2.5-flash'], error: null, at: '2026-10-16T01:00:00Z' });
      if (path === '/cli/profiles') return json(200, u.searchParams.get('kind') === 'claude_code_cli' ? [] : srv.agyProfiles);
      if (path === '/setup/first-run')
        return json(200, srv.firstRun ?? { raw_collected: 0, classifying: 0, clean: 0, lowconf: 0, discarded: 0, run: null });
      if (path === '/setup/follow-up') return json(200, [{ n: 4, key: 'brain', title: 'Bộ não AI', status: 'todo', done: srv.done.includes(4) }]);
      if (path === '/gen/coach/today') return srv.coach ? json(200, srv.coach) : json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
  return srv;
}
const puts = (...ns: number[]) => srv.calls.filter((c) => c.method === 'PUT' && ns.some((n) => c.path === `/setup/steps/${n}`)).map((c) => Number(c.path.split('/').pop()));

function Probe() {
  const l = useLocation();
  return <div data-testid="loc">{l.pathname + l.search}</div>;
}
function renderAt(path: string) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/setup" element={<SetupPage />} />
          <Route path="*" element={<div data-testid="elsewhere" />} />
        </Routes>
        <Probe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
const loc = () => screen.getByTestId('loc').textContent;

async function fillOwner(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText('Tên Sếp'), 'Anh Nguyễn Văn A (Chủ)');
  await user.type(screen.getByLabelText('Email'), 'owner@example.test');
  await user.type(screen.getByLabelText('Mật khẩu'), 'mot-cau-rat-dai-de-nho-2026');
  await user.click(screen.getByLabelText('Mã PIN (6 số) — chữ số 1/6'));
  await user.keyboard('246810');
  await user.click(screen.getByLabelText('Nhập lại mã PIN — chữ số 1/6'));
  await user.keyboard('246810');
}

beforeEach(() => {
  queryClient.clear();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  sessionStorage.clear();
  // Như trình duyệt thật: đang ở /setup (apiClient không đẩy 401 sang /login và không xoá cache khi ở /setup).
  window.history.replaceState({}, '', '/setup');
  document.cookie = 'gh_csrf=test-csrf';
  vi.stubGlobal('WebSocket', undefined);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

// ── màn Tài khoản ───────────────────────────────────────────────────────────────────────────────────────────────────────

describe('Bước 1 · Tài khoản', () => {
  it('máy mới: một form (Tên Sếp, Email, Mật khẩu, PIN ×2, Tên doanh nghiệp), "Tuỳ chỉnh" thu gọn, thanh bước 1·2·3; URL chuẩn ?buoc=tai-khoan', async () => {
    startServer();
    renderAt('/setup?token=GH-SETUP-7Q4K');
    await screen.findByRole('heading', { name: 'Tạo tài khoản Sếp' });
    for (const label of ['Tên Sếp', 'Email', 'Mật khẩu', 'Tên doanh nghiệp']) expect(screen.getByLabelText(label)).toBeInTheDocument();
    expect(screen.getByLabelText('Mã PIN (6 số) — chữ số 1/6')).toBeInTheDocument();
    expect(screen.getByLabelText('Nhập lại mã PIN — chữ số 1/6')).toBeInTheDocument();
    expect(screen.getByText('Tuỳ chỉnh').closest('details')).not.toHaveAttribute('open');
    expect(screen.queryByLabelText('Mã thiết lập')).toBeNull();
    const bar = screen.getByRole('list', { name: 'Các bước thiết lập' });
    expect(within(bar).getAllByRole('listitem').map((li) => li.textContent?.replace(/Bước \d: /, '').trim())).toEqual(['1Tài khoản', '2Nguồn AI', '3Xong']);
    await waitFor(() => expect(loc()).toBe('/setup?token=GH-SETUP-7Q4K&buoc=tai-khoan'));
  });

  it('Tiếp tục gọi lần lượt PUT 1 → 2 → 3 (thân đúng), rồi sang Nguồn AI (?buoc=nguon-ai)', async () => {
    const user = userEvent.setup();
    startServer();
    renderAt('/setup?token=GH-SETUP-7Q4K');
    await fillOwner(user);
    await user.type(screen.getByLabelText('Tên doanh nghiệp'), 'Genesis Trading');
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await screen.findByRole('heading', { name: 'Nối nguồn AI cho Gen' });
    expect(puts(1, 2, 3)).toEqual([1, 2, 3]);
    const body = (n: number) => srv.calls.find((c) => c.path === `/setup/steps/${n}`)!.body;
    expect(body(1)).toEqual({ token: 'GH-SETUP-7Q4K', language: 'vi', mode: 'empty' });
    expect(body(2)).toEqual({
      token: 'GH-SETUP-7Q4K', display_name: 'Anh Nguyễn Văn A (Chủ)', email: 'owner@example.test', password: 'mot-cau-rat-dai-de-nho-2026', pin: '246810', pin_confirm: '246810',
    });
    expect(body(3)).toEqual({ org_name: 'Genesis Trading', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND', self_name: 'Sếp', bot_calls_me: 'Sếp' });
    await waitFor(() => expect(loc()).toContain('buoc=nguon-ai'));
  });

  it('tải lại giữa chừng (bước 1 xong, mã nằm trong sessionStorage): chỉ PUT 2 → 3, không gọi lại PUT 1', async () => {
    const user = userEvent.setup();
    startServer({ done: [1] });
    sessionStorage.setItem('gh_setup_token', 'GH-SETUP-7Q4K');
    renderAt('/setup');
    await fillOwner(user);
    await user.type(screen.getByLabelText('Tên doanh nghiệp'), 'Genesis Trading');
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await screen.findByRole('heading', { name: 'Nối nguồn AI cho Gen' });
    expect(puts(1, 2, 3)).toEqual([2, 3]);
  });

  it('tải lại sau PUT 2 và mất mã: không hỏi mã, không form tài khoản — chỉ tên doanh nghiệp; chỉ PUT 3', async () => {
    const user = userEvent.setup();
    startServer({ done: [1, 2] });
    renderAt('/setup');
    await screen.findByRole('heading', { name: 'Tạo tài khoản Sếp' });
    expect(screen.queryByLabelText('Mã thiết lập')).toBeNull();
    expect(screen.queryByLabelText('Mật khẩu')).toBeNull();
    expect(await screen.findByTestId('setup-account-done')).toHaveTextContent('Tài khoản Sếp đã tạo');
    await user.type(screen.getByLabelText('Tên doanh nghiệp'), 'Genesis Trading');
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await screen.findByRole('heading', { name: 'Nối nguồn AI cho Gen' });
    expect(puts(1, 2, 3)).toEqual([3]);
  });

  it('thiếu mã thiết lập (không ?token=, không sessionStorage): hiện ô "Mã thiết lập" và bắt nhập', async () => {
    const user = userEvent.setup();
    startServer();
    renderAt('/setup');
    const token = await screen.findByLabelText('Mã thiết lập');
    await fillOwner(user);
    await user.type(screen.getByLabelText('Tên doanh nghiệp'), 'Genesis Trading');
    expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeDisabled();
    await user.type(token, 'GH-SETUP-7Q4K');
    expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeEnabled();
  });

  it('lỗi 422 của máy chủ gắn đúng ô (Email, Mật khẩu), có "Chi tiết kỹ thuật", không render object; bước 1 đã lưu vẫn được ghi nhận', async () => {
    const user = userEvent.setup();
    startServer({
      fail: { 'PUT /setup/steps/2': json(422, { status: 422, code: 'VALIDATION_ERROR', title: 'Dữ liệu chưa hợp lệ', errors: { email: 'Email đã được dùng.', password: 'Mật khẩu quá phổ biến.' } }) },
    });
    renderAt('/setup?token=GH-SETUP-7Q4K');
    await fillOwner(user);
    await user.type(screen.getByLabelText('Tên doanh nghiệp'), 'Genesis Trading');
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    const emailErr = await screen.findByText('Email đã được dùng.');
    expect(emailErr).toHaveClass('gh-field__error');
    expect(screen.getByLabelText('Email')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Mật khẩu quá phổ biến.')).toHaveClass('gh-field__error');
    expect(screen.getByLabelText('Mật khẩu')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('alert')).toHaveTextContent('Kiểm tra lại các trường được đánh dấu.');
    expect(screen.getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('[object Object]');
    expect(puts(1, 2, 3)).toEqual([1, 2]);
    // sửa ô lỗi ⇒ lỗi máy chủ của ô đó biến mất
    await user.type(screen.getByLabelText('Email'), 'x');
    expect(screen.queryByText('Email đã được dùng.')).toBeNull();
  });

  it('lỗi 422 ở ô nằm trong "Tuỳ chỉnh" tự mở phần đó; mã bị từ chối (403) hiện ô "Mã thiết lập" kèm lỗi', async () => {
    const user = userEvent.setup();
    startServer({
      done: [1, 2],
      fail: { 'PUT /setup/steps/3': json(422, { status: 422, code: 'VALIDATION_ERROR', title: 'Dữ liệu chưa hợp lệ', errors: { timezone: 'Múi giờ không hợp lệ.' } }) },
    });
    renderAt('/setup');
    await user.type(await screen.findByLabelText('Tên doanh nghiệp'), 'Genesis Trading');
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    expect(await screen.findByText('Múi giờ không hợp lệ.')).toBeInTheDocument();
    expect(screen.getByText('Tuỳ chỉnh').closest('details')).toHaveAttribute('open');
  });

  it('PUT 3 gặp 401 (mất phiên): câu thân thiện + link đăng nhập lại, không treo', async () => {
    const user = userEvent.setup();
    startServer({ done: [1, 2], fail: { 'PUT /setup/steps/3': json(401, { status: 401, code: 'UNAUTHENTICATED', title: 'Chưa đăng nhập' }) } });
    renderAt('/setup');
    await user.type(await screen.findByLabelText('Tên doanh nghiệp'), 'Genesis Trading');
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    expect(await screen.findByText(/Phiên đăng nhập của Sếp đã hết/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Đăng nhập lại' })).toHaveAttribute('href', '/login?next=%2Fsetup');
  });
});

// ── chuyển hướng theo URL cũ / trạng thái ─────────────────────────────────────────────────────────────────────────────────

describe('/setup — ánh xạ URL', () => {
  it('/setup?step=4 → ?buoc=nguon-ai (Nguồn AI), bỏ ?step=', async () => {
    startServer({ done: [1, 2, 3] });
    renderAt('/setup?step=4');
    await screen.findByRole('heading', { name: 'Nối nguồn AI cho Gen' });
    await waitFor(() => expect(loc()).toBe('/setup?buoc=nguon-ai'));
  });

  it('chưa đủ bước 1–3 mà đòi ?buoc=nguon-ai ⇒ về Tài khoản', async () => {
    startServer({ done: [1] });
    renderAt('/setup?buoc=nguon-ai');
    await screen.findByRole('heading', { name: 'Tạo tài khoản Sếp' });
    await waitFor(() => expect(loc()).toBe('/setup?buoc=tai-khoan'));
  });

  it('?step=12 khi bước 4 xong ⇒ bước Xong', async () => {
    startServer({ done: [1, 2, 3, 4], session: true });
    renderAt('/setup?step=12');
    await screen.findByRole('heading', { name: 'Xong rồi, thưa Sếp' });
    await waitFor(() => expect(loc()).toBe('/setup?buoc=xong'));
  });

  it('đã xong: /setup?step=7 → /guide/7; /setup → /owner', async () => {
    startServer({ done: ALL, finished: true });
    const { unmount } = renderAt('/setup?step=7');
    await waitFor(() => expect(loc()).toBe('/guide/7'));
    unmount();
    renderAt('/setup');
    await waitFor(() => expect(loc()).toBe('/owner'));
  });

  it('đã xong: mở ?buoc=nguon-ai (Owner) đổi nguồn AI, lưu PUT 4 rồi về /owner; không có phiên ⇒ /login?next=…; không phải Owner ⇒ "/"', async () => {
    const user = userEvent.setup();
    startServer({ done: ALL, finished: true, session: true, providers: [GEMINI_OK] });
    const { unmount } = renderAt('/setup?buoc=nguon-ai');
    await screen.findByRole('heading', { name: 'Đổi nguồn AI' });
    await waitFor(() => expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeEnabled());
    expect(screen.queryByRole('button', { name: 'Làm sau' })).toBeNull(); // đã xong thì không còn "Làm sau"
    await user.click(screen.getByRole('button', { name: /Tiếp tục/ }));
    await waitFor(() => expect(loc()).toBe('/owner'));
    expect(srv.calls.find((c) => c.method === 'PUT' && c.path === '/setup/steps/4')!.body).toEqual({ provider_ids: ['pv-gemini'] });
    unmount();

    queryClient.clear();
    startServer({ done: ALL, finished: true, session: false });
    const r2 = renderAt('/setup?buoc=nguon-ai');
    await waitFor(() => expect(loc()).toBe('/login?next=%2Fsetup%3Fbuoc%3Dnguon-ai'));
    r2.unmount();

    queryClient.clear();
    startServer({ done: ALL, finished: true, session: true, role: 'operator' });
    renderAt('/setup?buoc=nguon-ai');
    await waitFor(() => expect(loc()).toBe('/'));
  });
});

// ── Nguồn AI ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

describe('Bước 2 · Nguồn AI', () => {
  it('hai lựa chọn lớn (Google/Antigravity, Dán khoá API); "Nâng cao" thu gọn có Claude Code CLI, tương thích OpenAI/OpenRouter, Thứ tự dự phòng → /api; Tiếp tục khoá', async () => {
    startServer({ done: [1, 2, 3], session: true });
    renderAt('/setup');
    await screen.findByRole('heading', { name: 'Nối nguồn AI cho Gen' });
    expect(screen.getByRole('heading', { name: 'Đăng nhập Google (Antigravity)' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Dán khoá API' })).toBeInTheDocument();
    const adv = screen.getByTestId('setup-ai-advanced');
    expect(adv).not.toHaveAttribute('open');
    expect(within(adv).getByText('Claude Code CLI · gói Claude (tuỳ chọn)')).toBeInTheDocument();
    expect(within(adv).getByText('Tương thích OpenAI / OpenRouter')).toBeInTheDocument();
    expect(within(adv).getByRole('link', { name: /Thứ tự dự phòng/ })).toHaveAttribute('href', '/api');
    expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeDisabled();
    expect(screen.queryByTestId('setup-ai-ready')).toBeNull();
  });

  it('chưa đăng nhập Google: "Chưa đăng nhập" chỉ hiện MỘT lần trong thẻ Google (không lặp ở dòng trạng thái và pill cạnh nút)', async () => {
    startServer({ done: [1, 2, 3], session: true, agyProfiles: [] });
    renderAt('/setup');
    await screen.findByRole('heading', { name: 'Nối nguồn AI cho Gen' });
    const google = await screen.findByTestId('setup-ai-google');
    await waitFor(() => expect(within(google).getByText('Chưa đăng nhập')).toBeInTheDocument());
    expect(within(google).getAllByText('Chưa đăng nhập')).toHaveLength(1);
    expect(within(google).getByRole('button', { name: /Đăng nhập/ })).toBeInTheDocument();
  });

  it('Dán khoá Gemini → Kiểm tra (PIN do hộp PIN lo) → "Đã chạy được" → Tiếp tục gửi PUT 4 {provider_ids} → bước Xong "Đã chạy"', async () => {
    const user = userEvent.setup();
    startServer({ done: [1, 2, 3], session: true });
    renderAt('/setup');
    await screen.findByRole('heading', { name: 'Nối nguồn AI cho Gen' });
    const form = within(screen.getByTestId('setup-ai-keyform-basic'));
    expect(form.getByText('Cần mã PIN 6 số')).toBeInTheDocument();
    await user.selectOptions(form.getByLabelText('Loại khoá'), 'deepseek');
    await user.selectOptions(form.getByLabelText('Loại khoá'), 'gemini');
    expect(form.getByRole('button', { name: 'Kiểm tra' })).toBeDisabled();
    await user.type(form.getByLabelText('Khoá API'), 'AIza-good-key-1234');
    await user.click(form.getByRole('button', { name: 'Kiểm tra' }));
    const ready = await screen.findByTestId('setup-ai-ready');
    expect(ready).toHaveTextContent('Đã chạy được');
    expect(ready).toHaveTextContent('Gemini API');
    expect(screen.getByTestId('setup-ai-provider')).toHaveTextContent('Gọi thử OK');
    expect(srv.calls.find((c) => c.method === 'POST' && c.path === '/providers')!.body).toMatchObject({ kind: 'gemini', name: 'Gemini API', keys: ['AIza-good-key-1234'] });
    // khoá API không bao giờ vào kết quả hiển thị
    expect(document.body.textContent).not.toContain('AIza-good-key-1234');
    const next = screen.getByRole('button', { name: /Tiếp tục/ });
    await waitFor(() => expect(next).toBeEnabled());
    await user.click(next);
    await screen.findByRole('heading', { name: 'Xong rồi, thưa Sếp' });
    expect(srv.calls.find((c) => c.method === 'PUT' && c.path === '/setup/steps/4')!.body).toEqual({ provider_ids: ['pv-new'] });
    expect(screen.getByTestId('setup-done-ai')).toHaveTextContent('Đã chạy');
    await waitFor(() => expect(loc()).toBe('/setup?buoc=xong'));
  });

  describe('Antigravity vừa đăng nhập (thật: 0 dòng model, chưa gọi thử)', () => {
    const AGY_PROFILE: CliProfile = { id: 'cp1', email: 'an@example.vn', plan_label: null, active: true, expires_at: null, state: 'ok' };
    const AGY_BARE: Provider = {
      id: 'pv-agy', kind: 'antigravity_cli', name: 'Antigravity Brain', endpoint: null, failover_rank: 1, enabled: true, auth_state: 'ok', keys: [], models: [],
    };
    const agyTests = () => srv.calls.filter((c) => c.method === 'POST' && c.path === '/providers/pv-agy/test');

    it('tự "Kiểm tra" đúng MỘT lần ⇒ "Đã chạy được" và Tiếp tục mở; không nói "Đã chạy được" trước đó', async () => {
      startServer({ done: [1, 2, 3], session: true, providers: [{ ...AGY_BARE }], agyProfiles: [AGY_PROFILE] });
      renderAt('/setup');
      await screen.findByRole('heading', { name: 'Nối nguồn AI cho Gen' });
      const ready = await screen.findByTestId('setup-ai-ready');
      expect(ready).toHaveTextContent('Đã chạy được');
      expect(ready).toHaveTextContent('Antigravity Brain');
      await waitFor(() => expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeEnabled());
      expect(agyTests()).toHaveLength(1);
      const row = screen.getByTestId('setup-ai-cli-provider');
      expect(row).toHaveTextContent('Gọi thử OK');
      expect(within(row).getByRole('button', { name: 'Kiểm tra lại Antigravity Brain' })).toBeInTheDocument();
    });

    it('gọi thử lỗi ⇒ không "Đã chạy được", Tiếp tục khoá, dòng nguồn có câu thân thiện + "Chi tiết kỹ thuật" + "Kiểm tra lại" gọi lại', async () => {
      const user = userEvent.setup();
      startServer({
        done: [1, 2, 3], session: true, providers: [{ ...AGY_BARE }], agyProfiles: [AGY_PROFILE],
        fail: { 'POST /providers/pv-agy/test': json(200, { ok: false, latency_ms: null, models: [], error: 'agy: hết hạn đăng nhập', error_detail: 'exit 1', at: '2026-10-16T01:00:00Z' }) },
      });
      renderAt('/setup');
      const row = await screen.findByTestId('setup-ai-cli-provider');
      await waitFor(() => expect(row).toHaveTextContent('Chưa dùng được'));
      expect(within(row).getAllByText('Chi tiết kỹ thuật').length).toBeGreaterThan(0);
      expect(screen.queryByTestId('setup-ai-ready')).toBeNull();
      expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeDisabled();
      expect(screen.getByText(/có một nguồn chạy được là tiếp tục được/)).toBeInTheDocument();
      expect(agyTests()).toHaveLength(1); // lỗi không kéo theo vòng lặp tự thử
      await user.click(within(row).getByRole('button', { name: 'Kiểm tra lại Antigravity Brain' }));
      await waitFor(() => expect(agyTests()).toHaveLength(2));
    });

    it('nguồn đã có model sẵn (đăng nhập từ trước) ⇒ không tự gọi thử, vẫn "Đã chạy được"', async () => {
      startServer({
        done: [1, 2, 3], session: true, agyProfiles: [AGY_PROFILE],
        providers: [{ ...AGY_BARE, models: [{ id: 'm9', model_name: 'gemini-2.5-pro', daily_quota: null, used_today: 0 }] }],
      });
      renderAt('/setup');
      expect(await screen.findByTestId('setup-ai-ready')).toHaveTextContent('Đã chạy được');
      await waitFor(() => expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeEnabled());
      expect(agyTests()).toHaveLength(0);
    });
  });

  it('Kiểm tra lỗi: câu thân thiện + "Chi tiết kỹ thuật", Tiếp tục vẫn khoá', async () => {
    const user = userEvent.setup();
    startServer({
      done: [1, 2, 3], session: true,
      fail: { 'POST /providers/pv-new/test': json(200, { ok: false, latency_ms: null, models: [], error: 'HTTP 401 invalid api key AIza-good-key-1234', error_detail: 'status 401' }) },
    });
    renderAt('/setup');
    const form = within(await screen.findByTestId('setup-ai-keyform-basic'));
    await user.type(form.getByLabelText('Khoá API'), 'AIza-good-key-1234');
    await user.click(form.getByRole('button', { name: 'Kiểm tra' }));
    const row = await screen.findByTestId('setup-ai-provider');
    await waitFor(() => expect(row).toHaveTextContent('Chưa dùng được'));
    expect(within(row).getAllByText('Chi tiết kỹ thuật').length).toBeGreaterThan(0);
    expect(screen.queryByTestId('setup-ai-ready')).toBeNull();
    expect(screen.getByRole('button', { name: /Tiếp tục/ })).toBeDisabled();
  });

  it('"Làm sau": nhớ trong sessionStorage, sang bước Xong với câu "Gen sẽ chưa tóm tắt được bản tin"; máy chủ không bị gọi PUT 4', async () => {
    const user = userEvent.setup();
    startServer({ done: [1, 2, 3], session: true });
    renderAt('/setup');
    await screen.findByRole('heading', { name: 'Nối nguồn AI cho Gen' });
    expect(screen.getByText('Gen sẽ chưa tóm tắt được bản tin.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Làm sau' }));
    await screen.findByRole('heading', { name: 'Xong rồi, thưa Sếp' });
    expect(sessionStorage.getItem(AI_LATER_KEY)).toBe('1');
    expect(screen.getByTestId('setup-done-ai')).toHaveTextContent('Làm sau — Gen sẽ chưa tóm tắt được bản tin');
    // câu mở đầu không nói "Gen đã sẵn sàng" khi Sếp chọn Làm sau (mâu thuẫn với dòng Nguồn AI)
    expect(screen.getByText('Tài khoản đã xong. Nối nguồn AI lúc nào cũng được.')).toBeInTheDocument();
    expect(screen.queryByText(/Gen đã sẵn sàng/)).toBeNull();
    expect(puts(4)).toEqual([]);
    await waitFor(() => expect(loc()).toBe('/setup?buoc=xong'));
  });
});

// ── Xong ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────

describe('Bước 3 · Xong', () => {
  const RUNNING = {
    raw_collected: 120, classifying: 30, clean: 80, lowconf: 5, discarded: 5,
    run: { id: 'r1', trigger: 'first_run', status: 'running', started_at: '2026-10-16T01:00:00Z', finished_at: null, input_count: 120, clean_count: 80, lowconf_count: 5, noise_count: 5, error_count: 0 },
  };

  it('ba dòng tóm tắt (tài khoản · nguồn AI · lọc tin lần đầu), nút chính "Vào Mặt tiền", nút phụ "Nối Zalo ngay" + câu giải thích, hai link nhỏ', async () => {
    startServer({ done: [1, 2, 3, 4], session: true, firstRun: RUNNING });
    renderAt('/setup?buoc=xong');
    await screen.findByRole('heading', { name: 'Xong rồi, thưa Sếp' });
    expect(await screen.findByText('Anh Nguyễn Văn A (Chủ) · Genesis Trading')).toBeInTheDocument();
    expect(screen.getByTestId('setup-done-ai')).toHaveTextContent('Đã chạy');
    expect(await screen.findByText(/Gen đang lọc tin lần đầu/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Vào Mặt tiền/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Nối Zalo ngay/ })).toBeInTheDocument();
    expect(screen.getByText('Zalo là nguồn tin chính — chưa nối thì Mặt tiền còn trống.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Nhận bản tin qua Telegram' })).toHaveAttribute('href', '/connections#telegram');
    expect(screen.getByRole('link', { name: 'Mời đội' })).toHaveAttribute('href', '/guide/10');
  });

  it('nguồn AI chưa nối (không phải "Làm sau"): gợi ý chỉ tới nơi có thật — Cài đặt › Nguồn AI › Đổi nguồn AI; nguồn AI xong thì "Gen đã sẵn sàng"', async () => {
    startServer({ done: [1, 2, 3], skipped: [4], session: true }); // máy chủ đã ghi "để sau" nguồn AI, trình duyệt không nhớ "Làm sau"
    const first = renderAt('/setup?buoc=xong');
    await screen.findByRole('heading', { name: 'Xong rồi, thưa Sếp' });
    expect(screen.getByText(/Cài đặt › Nguồn AI › Đổi nguồn AI/)).toBeInTheDocument();
    expect(screen.queryByText(/nút “Chọn model”/)).toBeNull();
    first.unmount();
    queryClient.clear();
    startServer({ done: [1, 2, 3, 4], session: true });
    renderAt('/setup?buoc=xong');
    expect(await screen.findByText('Gen đã sẵn sàng. Đây là những gì đã bật:')).toBeInTheDocument();
  });

  it('"Vào Mặt tiền": PUT 12 rồi /owner; "Nối Zalo ngay": PUT 12 rồi /guide/5', async () => {
    const user = userEvent.setup();
    startServer({ done: [1, 2, 3, 4], session: true });
    const first = renderAt('/setup?buoc=xong');
    await user.click(await screen.findByRole('button', { name: /Vào Mặt tiền/ }));
    await waitFor(() => expect(loc()).toBe('/owner'));
    expect(puts(12)).toEqual([12]);
    first.unmount();

    queryClient.clear();
    startServer({ done: [1, 2, 3, 4], session: true });
    const second = renderAt('/setup?buoc=xong');
    await user.click(await screen.findByRole('button', { name: /Nối Zalo ngay/ }));
    await waitFor(() => expect(loc()).toBe('/guide/5'));
    expect(puts(12)).toEqual([12]);
    second.unmount();

    queryClient.clear();
    startServer({ done: [1, 2, 3, 4], session: true });
    renderAt('/setup?buoc=xong');
    await user.click(await screen.findByRole('link', { name: 'Mời đội' }));
    await waitFor(() => expect(loc()).toBe('/guide/10'));
    expect(puts(12)).toEqual([12]);
  });

  it('Hoàn tất ghi bước 4–11 chưa xong là "để sau" (mặc định máy chủ) RỒI mới PUT 12; "Làm sau" bước Nguồn AI cũng được ghi', async () => {
    const user = userEvent.setup();
    startServer({ done: [1, 2, 3], session: true });
    sessionStorage.setItem(AI_LATER_KEY, '1');
    renderAt('/setup?buoc=xong');
    await user.click(await screen.findByRole('button', { name: /Vào Mặt tiền/ }));
    await waitFor(() => expect(loc()).toBe('/owner'));
    const order = srv.calls.filter((c) => c.method !== 'GET' && c.path.startsWith('/setup/steps/')).map((c) => `${c.method} ${c.path}`);
    expect(order).toEqual([4, 5, 6, 7, 8, 9, 10, 11].map((n) => `POST /setup/steps/${n}/skip`).concat('PUT /setup/steps/12'));
    // bước 4 xong thật thì KHÔNG bị ghi "để sau"
    queryClient.clear();
    startServer({ done: [1, 2, 3, 4], session: true });
    const again = renderAt('/setup?buoc=xong');
    await user.click(await screen.findByRole('button', { name: /Vào Mặt tiền/ }));
    await waitFor(() => expect(srv.calls.some((c) => c.path === '/setup/steps/12')).toBe(true));
    expect(srv.calls.some((c) => c.path === '/setup/steps/4/skip')).toBe(false);
    again.unmount();
  });

  it('PUT 12 lỗi: câu thân thiện + "Chi tiết kỹ thuật", ở lại bước Xong', async () => {
    const user = userEvent.setup();
    startServer({
      done: [1, 2, 3, 4], session: true,
      fail: { 'PUT /setup/steps/12': json(409, { status: 409, code: 'STEP_INCOMPLETE', title: 'Còn bước bắt buộc chưa xong: 3' }) },
    });
    renderAt('/setup?buoc=xong');
    await user.click(await screen.findByRole('button', { name: /Vào Mặt tiền/ }));
    expect(await screen.findByText('Còn bước bắt buộc chưa xong: 3')).toBeInTheDocument();
    expect(screen.getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(loc()).toBe('/setup?buoc=xong');
  });
});

// ── mọi đích "Chọn model" về /setup?buoc=nguon-ai ──────────────────────────────────────────────────────────────────────────

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(ts|tsx)$/.test(name)) out.push(p);
  }
  return out;
}

describe('Đích "Chọn model" dồn về /setup?buoc=nguon-ai', () => {
  it('ModelUnavailableNotice: "Chọn model" → màn Thiết lập nguồn AI (v0.1.62: không còn nhánh riêng cho chuỗi chỉ có Antigravity)', () => {
    const wrap = (ui: JSX.Element) => render(<MemoryRouter>{ui}</MemoryRouter>);
    const a = wrap(<ModelUnavailableNotice reasons="Không có nhà cung cấp" />);
    expect(screen.getByRole('link', { name: /Chọn model/ })).toHaveAttribute('href', '/setup?buoc=nguon-ai');
    a.unmount();
    wrap(<ModelUnavailableNotice reasons="Antigravity CLI: lỗi tạm" />);
    expect(screen.getByRole('link', { name: /Chọn model/ })).toHaveAttribute('href', '/setup?buoc=nguon-ai');
    expect(screen.queryByRole('link', { name: /Thêm nguồn AI/ })).toBeNull();
  });

  it('vai không phải Owner (Quản lý, Vận hành…): KHÔNG có nút "Chọn model" dẫn tới màn họ không vào được — chữ "Báo Sếp chọn nguồn AI" + Chi tiết kỹ thuật', () => {
    const wrap = (ui: JSX.Element) => render(<MemoryRouter>{ui}</MemoryRouter>);
    queryClient.setQueryData(qk.me, ME('manager'));
    const a = wrap(<ModelUnavailableNotice reasons="Không có nhà cung cấp: lỗi 401" />);
    expect(screen.queryByRole('link', { name: /Chọn model/ })).toBeNull();
    expect(screen.getByTestId('model-unavailable-ask-owner')).toHaveTextContent('Báo Sếp chọn nguồn AI');
    expect(screen.getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    a.unmount();
    const b = wrap(<ModelUnavailableNotice reasons="Antigravity CLI: lỗi tạm" />);
    expect(screen.queryByRole('link')).toBeNull();
    expect(screen.getByTestId('model-unavailable-ask-owner')).toBeInTheDocument();
    b.unmount();
    // Bàn làm việc (dịch / tạo lại nháp) dùng ActionError: Quản lý gặp MODEL_UNAVAILABLE cũng chỉ thấy chữ báo Sếp
    const err = new ApiError(503, { code: 'MODEL_UNAVAILABLE', title: 'Chưa có model AI chạy được', detail: { reasons: ['Gemini: HTTP 401'] } } as never);
    const c = wrap(<ActionError error={err} />);
    expect(screen.queryByRole('link', { name: /Chọn model/ })).toBeNull();
    expect(screen.getByTestId('model-unavailable-ask-owner')).toBeInTheDocument();
    c.unmount();
    // Owner vẫn thấy nút
    queryClient.setQueryData(qk.me, ME('owner'));
    wrap(<ModelUnavailableNotice reasons="x" />);
    expect(screen.getByRole('link', { name: /Chọn model/ })).toHaveAttribute('href', '/setup?buoc=nguon-ai');
    queryClient.removeQueries({ queryKey: qk.me });
  });

  it('NoModelNotice: "Chọn model" → /setup?buoc=nguon-ai', async () => {
    startServer({ done: [1, 2, 3], session: true });
    queryClient.setQueryData(qk.me, ME());
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <NoModelNotice to={AI_SOURCE_PATH} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole('link', { name: /Chọn model/ })).toHaveAttribute('href', '/setup?buoc=nguon-ai');
  });

  it('/guide/4 vẫn vẽ form Bộ não AI đầy đủ, quay về Mặt tiền (không còn "Hôm nay"), kèm "Cách nhanh: Thiết lập nguồn AI 2 phút"', async () => {
    startServer({ done: [1, 2, 3], session: true, providers: [GEMINI_OK] });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/guide/4']}>
          <Routes>
            <Route path="/guide/:n" element={<GuideStepPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole('link', { name: /Mặt tiền/ })).toHaveAttribute('href', '/owner');
    expect(screen.queryByRole('link', { name: /Hôm nay/ })).toBeNull();
    expect(screen.getByRole('link', { name: 'Cách nhanh: Thiết lập nguồn AI 2 phút' })).toHaveAttribute('href', '/setup?buoc=nguon-ai');
    expect(await screen.findByTestId('setup-cli-antigravity_cli')).toBeInTheDocument(); // Step4Brain đầy đủ
    expect(screen.getByText('Khoá API · thứ tự dùng')).toBeInTheDocument();
  });

  it('không còn đích "/guide/4" viết cứng trong mã giao diện (chỉ AI_SOURCE_PATH)', () => {
    const root = resolve(__dirname, '../../src');
    const code = (f: string) => readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
    const offenders = walk(root).filter((f) => /["'`]\/guide\/4["'`]/.test(code(f)));
    expect(offenders.map((f) => f.slice(root.length + 1))).toEqual([]);
  });
});

// ── Cài đặt: 4 khối ──────────────────────────────────────────────────────────────────────────────────────────────────────

describe('Cài đặt (/owner/them) — đúng 4 khối', () => {
  const TODAY = {
    date: '2026-10-16', enabled: true, snoozed_until: null, tip: null, lesson: null, unseen: false,
    progress: { required_done: 1, required_total: 2, lessons_done: 0, lessons_total: 19, stable: false, stable_since: null },
    todos: [
      { key: 'model.missing', level: 'P0', title: 'Gen chưa có nguồn AI để trả lời', why: 'Chưa có nguồn AI thì Gen chưa tóm tắt được tin.', link: '/setup?buoc=nguon-ai', can_dismiss: false },
      { key: 'health.disk.low', level: 'P1', title: 'Ổ đĩa sắp đầy', why: 'Dọn bớt dữ liệu cũ.', link: '/system?tab=storage&focus=health', can_dismiss: true },
      { key: 'boss.hub', level: 'P3', title: 'Nối Gen-hub', why: 'Để Gen đọc Kho dữ liệu.', target: 'boss_checks.row.hub', can_dismiss: true },
    ],
  };
  const renderMore = () =>
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/owner/them']}>
          <MoreScreen />
        </MemoryRouter>
      </QueryClientProvider>,
    );

  it('4 khối: Tài khoản · Nguồn AI · Việc nên làm tiếp · Cài đặt nâng cao; MỘT danh sách việc (boss.* + health.*); không còn thẻ Hôm nay của Sếp / khối Kết nối / link Hướng dẫn thiết lập', async () => {
    startServer({ done: ALL, finished: true, session: true, coach: TODAY });
    const { container } = renderMore();
    await screen.findAllByTestId('owner-more-todo-row');
    const titles = Array.from(container.querySelectorAll('.gh-card__title')).map((e) => e.textContent);
    expect(titles).toEqual(['Tài khoản · Anh Nguyễn Văn A (Chủ)', 'Nguồn AI', 'Việc nên làm tiếp', 'Cài đặt nâng cao']);
    expect(container.querySelectorAll('section.gh-card')).toHaveLength(4);
    // MỘT danh sách việc: đủ 3 dòng từ /gen/coach/today, mỗi dòng "Chưa" + nút tới đích
    const rows = screen.getAllByTestId('owner-more-todo-row');
    expect(rows).toHaveLength(3);
    for (const r of rows) {
      expect(r).toHaveTextContent('Chưa');
      expect(within(r).getByRole('button', { name: /Làm ngay/ })).toBeInTheDocument();
    }
    expect(rows[0]).toHaveTextContent('Khẩn');
    expect(rows[1]).toHaveTextContent('Quan trọng');
    expect(rows[2]).toHaveTextContent('Thường');
    expect(container.querySelectorAll('[data-testid="owner-more-todo"]')).toHaveLength(1);
    // không còn: thẻ Hôm nay của Sếp (CoachTodayCard), khối Kết nối, link Hướng dẫn thiết lập
    expect(container.querySelector('[data-gen-target="gen.coach.card"]')).toBeNull();
    expect(screen.queryByText('Hôm nay của Sếp')).toBeNull();
    expect(screen.queryByTestId('owner-connections')).toBeNull();
    expect(screen.queryByTestId('owner-conn-row')).toBeNull();
    expect(screen.queryByRole('link', { name: /Hướng dẫn thiết lập/ })).toBeNull();
    expect(srv.calls.some((c) => c.path.includes('boss-checks'))).toBe(false);
    // Nguồn AI + nâng cao
    expect(screen.getByTestId('owner-more-ai-status')).toHaveTextContent('Đã chạy');
    expect(screen.getByRole('link', { name: /Đổi nguồn AI/ })).toHaveAttribute('href', '/setup?buoc=nguon-ai');
    expect(within(screen.getByTestId('owner-more-advanced')).getAllByRole('link').map((a) => a.getAttribute('href'))).toEqual(['/api', '/system?tab=brain', '/overview']);
    expect(document.body.textContent).not.toContain('[object Object]');
  });

  it('Tài khoản: đổi mật khẩu/PIN → /account; đổi giao diện sáng/tối; "Làm ngay" của model.missing đi thẳng /setup?buoc=nguon-ai', async () => {
    const user = userEvent.setup();
    const go = vi.fn();
    setNavigator(go);
    startServer({ done: ALL, finished: true, session: true, coach: TODAY });
    renderMore();
    expect(await screen.findByRole('link', { name: /Đổi mật khẩu/ })).toHaveAttribute('href', '/account');
    expect(screen.getByRole('link', { name: /Đổi mã PIN/ })).toHaveAttribute('href', '/account');
    await user.click(screen.getByRole('radio', { name: 'Tối' }));
    expect(screen.getByRole('radio', { name: 'Tối' })).toHaveAttribute('aria-checked', 'true');
    await user.click(screen.getByRole('radio', { name: 'Sáng' }));
    expect(screen.getByRole('radio', { name: 'Sáng' })).toHaveAttribute('aria-checked', 'true');
    const row = screen.getAllByTestId('owner-more-todo-row')[0];
    await user.click(within(row).getByRole('button', { name: /Làm ngay/ }));
    await waitFor(() => expect(go).toHaveBeenCalled());
    expect(go.mock.calls[0][0]).toBe('/setup?buoc=nguon-ai');
  });

  it('hết việc ⇒ MỘT dòng "Đã xong"; chưa nối nguồn AI ⇒ "Chưa nối nguồn AI" kèm "Đổi nguồn AI"', async () => {
    startServer({ done: [1, 2, 3], finished: true, session: true, coach: { ...TODAY, todos: [] } });
    renderMore();
    const row = await screen.findByTestId('owner-more-todo-row');
    expect(row).toHaveTextContent('Đã xong');
    expect(await screen.findByText('Chưa nối nguồn AI')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Đổi nguồn AI/ })).toBeInTheDocument();
  });

  it('lỗi tải việc: câu thân thiện + "Chi tiết kỹ thuật", vẫn còn Cài đặt nâng cao', async () => {
    startServer({ done: ALL, finished: true, session: true, coach: TODAY, fail: { 'GET /gen/coach/today': json(500, { status: 500, code: 'INTERNAL', title: 'Máy chủ gặp lỗi' }) } });
    renderMore();
    expect(await screen.findByTestId('owner-error')).toHaveTextContent('Chi tiết kỹ thuật');
    expect(screen.getByTestId('owner-advanced')).toBeInTheDocument();
  });
});

// ── lính gác CSS ─────────────────────────────────────────────────────────────────────────────────────────────────────────

describe('setup-one.css — quy ước hình ảnh', () => {
  const css = readFileSync(resolve(__dirname, '../../src/setup/one/setup-one.css'), 'utf8');
  const code = css.replace(/\/\*[\s\S]*?\*\//g, '');

  it('chỉ token: không mã màu cứng, không rgb()/hsl(), không chữ in hoa', () => {
    expect(code).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(code).not.toMatch(/\b(rgb|rgba|hsl|hsla)\(/);
    expect(code).not.toMatch(/text-transform|uppercase/);
  });

  it('cỡ chữ 12/14/18/28, độ đậm 400/600, điểm gãy chỉ min-width 760/1024', () => {
    const sizes = new Set([...code.matchAll(/font-size:\s*(\d+)px/g)].map((m) => Number(m[1])));
    expect([...sizes].every((n) => [12, 14, 18, 28].includes(n))).toBe(true);
    const weights = new Set([...code.matchAll(/font-weight:\s*(\d+)/g)].map((m) => Number(m[1])));
    expect([...weights].every((n) => [400, 600].includes(n))).toBe(true);
    const media = [...code.matchAll(/@media\s*([^{]+)\{/g)].map((m) => m[1].trim());
    expect(media.length).toBeGreaterThan(0);
    expect(media.every((q) => q === '(min-width: 760px)' || q === '(min-width: 1024px)')).toBe(true);
  });
});
