import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, type APIRequestContext, type Locator, type Page, type Route } from '@playwright/test';

const here = dirname(fileURLToPath(import.meta.url));
export const repoRoot = resolve(here, '../../..');
export const designDir = join(repoRoot, 'docs/design');
export const resultsDir = resolve(here, '../test-results');
/** Package folder in the workspace node_modules (some packages' `exports` hide package.json). */
function pkgDir(name: string): string {
  for (const base of [resolve(here, '../node_modules'), join(repoRoot, 'node_modules')]) {
    const dir = join(base, name);
    if (existsSync(join(dir, 'package.json'))) return dir;
  }
  throw new Error(`package not installed: ${name}`);
}

export const OWNER = { email: 'owner@genesis.local', password: 'matkhau-rat-dai-2026', pin: '246810' };
export const SETUP_TOKEN = 'GH-SETUP-7Q4K-2M9X';

const DESIGN_ORIGIN = 'http://design.local';
const TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.ttf': 'font/ttf',
  '.svg': 'image/svg+xml',
};
const typeOf = (p: string) => TYPES[p.slice(p.lastIndexOf('.'))] ?? 'application/octet-stream';
const CORS = { 'Access-Control-Allow-Origin': '*' };

function fulfillFile(route: Route, file: string) {
  return route.fulfill({ status: 200, body: readFileSync(file), headers: { 'Content-Type': typeOf(file), ...CORS } });
}

/** @font-face CSS equivalent to Google Fonts' Inter 400–700, from @fontsource/inter. */
function interCss(): string {
  const dir = pkgDir('@fontsource/inter');
  return [400, 500, 600, 700]
    .map((w) => readFileSync(join(dir, `${w}.css`), 'utf8'))
    .join('\n')
    .replace(/url\(\.\/files\//g, `url(${DESIGN_ORIGIN}/__fonts/inter/`);
}

/**
 * The design HTML loads Inter from Google Fonts and Phosphor + React from
 * unpkg (blocked here). Serve every one of them from node_modules so the design
 * renders offline with exactly the fonts the app self-hosts.
 */
export interface DesignOptions {
  /** Nav labels to click in order after load (e.g. a group, then a child). */
  clicks?: string[];
  /** The design's `sidebarMode` prop (default 'full'). */
  sidebarMode?: 'full' | 'rail';
}

export async function openDesign(page: Page, opts: DesignOptions = {}): Promise<void> {
  const react = pkgDir('react');
  const reactDom = pkgDir('react-dom');
  const phosphor = pkgDir('@phosphor-icons/web');
  const inter = pkgDir('@fontsource/inter');

  await page.route(`${DESIGN_ORIGIN}/**`, (route) => {
    const url = new URL(route.request().url());
    const path = decodeURIComponent(url.pathname);
    if (path.startsWith('/__fonts/inter/')) return fulfillFile(route, join(inter, 'files', path.slice('/__fonts/inter/'.length)));
    const file = join(designDir, path);
    if (path.endsWith('.dc.html') && opts.sidebarMode === 'rail') {
      // flip the design prop default: "sidebarMode": {..."default":"full"...}
      const html = readFileSync(file, 'utf8').replace(
        /(&quot;sidebarMode&quot;:\{[^}]*?&quot;default&quot;:&quot;)full(&quot;)/,
        '$1rail$2',
      );
      return route.fulfill({ status: 200, body: html, headers: { 'Content-Type': typeOf(file) } });
    }
    return fulfillFile(route, file);
  });
  await page.route('https://fonts.googleapis.com/**', (route) =>
    route.fulfill({ status: 200, body: interCss(), headers: { 'Content-Type': 'text/css; charset=utf-8', ...CORS } }),
  );
  await page.route('https://fonts.gstatic.com/**', (route) => route.abort());
  await page.route('https://unpkg.com/**', (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    let m: RegExpExecArray | null;
    if ((m = /^\/@phosphor-icons\/web@[^/]+\/(src\/.+)$/.exec(p))) return fulfillFile(route, join(phosphor, m[1]));
    if ((m = /^\/react@[^/]+\/(umd\/.+)$/.exec(p))) return fulfillFile(route, join(react, m[1]));
    if ((m = /^\/react-dom@[^/]+\/(umd\/.+)$/.exec(p))) return fulfillFile(route, join(reactDom, m[1]));
    return route.fulfill({ status: 404, body: `not vendored: ${p}` });
  });

  await page.goto(`${DESIGN_ORIGIN}/Gen-Harness%20Console.dc.html`);
  await page.locator('aside nav button').first().waitFor();
  for (const label of opts.clicks ?? []) {
    await page.locator('aside nav button', { hasText: label }).first().click();
  }
  await settle(page);
}

/** Wait for web fonts and park the pointer where it hovers nothing. */
export async function settle(page: Page): Promise<void> {
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  const vp = page.viewportSize();
  await page.mouse.move((vp?.width ?? 1000) - 2, (vp?.height ?? 800) - 2);
  await page.waitForTimeout(250);
}

async function csrf(request: APIRequestContext): Promise<string> {
  await request.get('/api/v1/health');
  const state = await request.storageState();
  return state.cookies.find((c) => c.name === 'gh_csrf')?.value ?? '';
}

/** Rebuild the mock. The live simulation is off unless asked for, so screens are deterministic. */
export async function resetMock(
  request: APIRequestContext,
  setup: 'fresh' | 'finished' = 'finished',
  opts: { simulate?: boolean; allowFinish?: boolean; startAtStep?: number; staff?: boolean } = {},
) {
  const res = await request.post('/api/v1/__mock/reset', { data: { setup, simulate: false, ...opts } });
  if (!res.ok()) throw new Error(`mock reset failed: ${res.status()}`);
}

/** Per-viewer layout prefs as the app persists them (zustand persist, key gh-ui). */
export async function setUiPrefs(page: Page, prefs: { sidebarMode?: 'full' | 'rail' }) {
  await page.addInitScript((p) => {
    localStorage.setItem('gh-ui', JSON.stringify({ state: { sidebarMode: 'full', navOpen: {}, ...p }, version: 2 }));
  }, prefs);
}

/** Log in through the API (sets gh_session in the page's context). */
export async function loginAsOwner(page: Page): Promise<void> {
  await loginAs(page, OWNER.email);
}

export const AUDITOR = { email: 'auditor@genesis.local', pin: '975310' };
export const MANAGER = { email: 'manager@genesis.local', pin: '864202' };

export async function loginAs(page: Page, email: string, password = OWNER.password): Promise<void> {
  const token = await csrf(page.request);
  const res = await page.request.post('/api/v1/auth/login', {
    data: { email, password },
    headers: { 'X-CSRF-Token': token },
  });
  if (!res.ok()) throw new Error(`login failed: ${res.status()} ${await res.text()}`);
}

/** POST a test hook on the mock (`/api/v1/__mock/{name}`). */
export async function mockHook(request: APIRequestContext, name: 'emit' | 'raw' | 'listenevent' | 'scan' | 'discover' | 'simulate' | 'bridge' | 'notify' | 'health' | 'perm' | 'pin_expire', data: unknown = {}) {
  const res = await request.post(`/api/v1/__mock/${name}`, { data });
  if (res.status() >= 400) throw new Error(`mock hook ${name} failed: ${res.status()}`);
  return res;
}

/** POST a phase-3 cluster hook on the mock (`/api/v1/__mock/p3/{cluster}/{hook}` → `hooks[hook](data)`). */
export async function p3Hook(request: APIRequestContext, cluster: string, hook: string, data: unknown = {}) {
  const res = await request.post(`/api/v1/__mock/p3/${cluster}/${hook}`, { data });
  if (res.status() >= 400) throw new Error(`mock hook p3/${cluster}/${hook} failed: ${res.status()}`);
  return res.json().catch(() => null);
}

/** Authenticated JSON call through the page's cookies (CSRF handled). */
export async function apiCall(page: Page, method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE', path: string, data?: unknown) {
  const token = await csrf(page.request);
  const res = await page.request.fetch(`/api/v1${path}`, { method, data, headers: { 'X-CSRF-Token': token } });
  if (!res.ok()) throw new Error(`${method} ${path} failed: ${res.status()} ${await res.text()}`);
  return res.status() === 204 ? null : res.json();
}

/**
 * v0.1.50 (F-81, QD-18) — mock Gen-hub: nối sẵn + Kiểm tra xanh (`link:'on'`), quyền ghi Kho của token (`write`), lỗi lần ghi kế tiếp
 * (`kho`). Hook `hubSim` của mock-p4-mcp; không đụng Gen-hub thật.
 */
export async function hubSim(request: APIRequestContext, data: { link?: 'on'; write?: 'ok' | 'missing'; kho?: 'ok' | 'uncertain' | 'rejected' | 'permit'; scopes?: 'full' | 'missing'; breaker?: boolean } = {}) {
  return p3Hook(request, 'mcp', 'hubSim', data);
}

/** Số lời gọi `/hub/kho/write` mà mock đã nhận (kể cả lỗi) + các lần ghi thành công. Huỷ đề xuất thì phải là 0. */
export async function khoCalls(request: APIRequestContext): Promise<{ calls: number; writes: Array<{ proposal_id: string; tool: string; code: string | null }> }> {
  return (await p3Hook(request, 'mcp', 'khoCalls')) as { calls: number; writes: Array<{ proposal_id: string; tool: string; code: string | null }> };
}

/** Hộp PIN đang mở ⇒ nhập mã PIN của Owner (6 chữ số) và chờ hộp đóng. */
export async function enterPin(page: Page): Promise<void> {
  const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
  await expect(dlg).toBeVisible();
  await page.getByLabel('Mã PIN — chữ số 1/6').click();
  await page.keyboard.type(OWNER.pin);
  await expect(dlg).toBeHidden();
}

/** PIN có thể còn hiệu lực từ thao tác trước — chỉ nhập khi hộp PIN hiện (chờ thật tối đa 2,5 giây). */
export async function enterPinIfAsked(page: Page): Promise<void> {
  const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
  const shown = await dlg
    .waitFor({ state: 'visible', timeout: 2500 })
    .then(() => true)
    .catch(() => false);
  if (shown) await enterPin(page);
}

/** Mở khung Gen (nếu đang đóng) và trả về khung. */
export async function openGen(page: Page): Promise<Locator> {
  const panel = page.getByRole('complementary', { name: /Gen — trợ lý quản trị/ });
  if (!(await panel.isVisible())) await page.getByRole('button', { name: /Hỏi Gen/ }).click();
  await expect(panel).toBeVisible();
  return panel;
}

/** Hỏi Gen một câu và trả về thẻ đề xuất mới nhất có tên `Đề xuất: <title>`. */
export async function askGen(page: Page, question: string, cardTitle: string): Promise<Locator> {
  const panel = await openGen(page);
  await panel.getByLabel('Câu hỏi cho Gen').fill(question);
  await panel.getByRole('button', { name: 'Gửi', exact: true }).click();
  const card = panel.getByRole('group', { name: `Đề xuất: ${cardTitle}` }).last();
  await expect(card).toBeVisible();
  return card;
}

// ─── v0.1.59 (g5-thiet-lap) — Thiết lập MỘT màn, 3 bước ────────────────────────────────────────────────────────────

/** Đích DUY NHẤT của mọi nút "Chọn model" / "Đổi nguồn AI" (khớp `AI_SOURCE_PATH` của src/setup/steps.ts). */
export const AI_SOURCE_PATH = '/setup?buoc=nguon-ai';

/** Điền form Tài khoản (bước 1) trên màn Thiết lập — chưa bấm "Tiếp tục". `org` mặc định "Genesis Trading". */
export async function fillSetupAccount(page: Page, opts: { org?: string } = {}): Promise<void> {
  await page.getByLabel('Tên Sếp').fill('Anh Nguyễn Văn A (Chủ)');
  await page.getByLabel('Email').fill('owner@example.test');
  await page.getByLabel('Mật khẩu', { exact: true }).fill('mot-cau-rat-dai-de-nho-2026');
  await page.getByLabel('Mã PIN (6 số) — chữ số 1/6').click();
  await page.keyboard.type(OWNER.pin);
  await page.getByLabel('Nhập lại mã PIN — chữ số 1/6').click();
  await page.keyboard.type(OWNER.pin);
  await page.getByLabel('Tên doanh nghiệp').fill(opts.org ?? 'Genesis Trading');
}

/** Bước Nguồn AI: đăng nhập Google (Antigravity) qua mã xác thực — hộp PIN + liên kết + mã, rồi đợi tài khoản hiện. */
export async function loginGoogleInSetup(page: Page): Promise<void> {
  const google = page.getByTestId('setup-ai-google');
  await google.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' })).toBeVisible();
  await page.keyboard.type(OWNER.pin);
  await expect(page.getByRole('link', { name: 'Mở trang đăng nhập Google' })).toBeVisible({ timeout: 5000 });
  await page.getByLabel('Mã xác thực').fill('4/0AbCd-EfGh');
  await page.getByRole('button', { name: 'Xác nhận' }).click();
  await expect(google.getByText('a@example.test').first()).toBeVisible({ timeout: 5000 });
}

/**
 * Đi tới bước 3 (Xong) của Thiết lập một màn BẰNG GIAO DIỆN, từ mock `fresh`: mở /setup?token=…, điền Tài khoản, rồi Nguồn AI
 * ("Làm sau" khi `skipAi`, không thì đăng nhập Google). Dừng ở bước Xong (chưa bấm nút nào) để test chọn "Vào Mặt tiền" /
 * "Nối Zalo ngay".
 */
export async function setupOneToDone(page: Page, opts: { skipAi?: boolean } = {}): Promise<void> {
  await page.goto(`/setup?token=${SETUP_TOKEN}`);
  await expect(page.getByRole('heading', { name: 'Tạo tài khoản Sếp' })).toBeVisible();
  await fillSetupAccount(page);
  await page.getByRole('button', { name: /Tiếp tục/ }).click();
  await expect(page.getByRole('heading', { name: 'Nối nguồn AI cho Gen' })).toBeVisible();
  if (opts.skipAi) {
    await page.getByRole('button', { name: 'Làm sau' }).click();
  } else {
    await loginGoogleInSetup(page);
    const next = page.getByRole('button', { name: /Tiếp tục/ });
    await expect(next).toBeEnabled();
    await next.click();
  }
  await expect(page.getByRole('heading', { name: 'Xong rồi, thưa Sếp' })).toBeVisible();
}

/** Thiết lập một màn từ đầu tới hết: `setupOneToDone` rồi "Vào Mặt tiền" ⇒ /owner. */
export async function finishSetupOne(page: Page, opts: { skipAi?: boolean } = {}): Promise<void> {
  await setupOneToDone(page, opts);
  await page.getByRole('button', { name: /Vào Mặt tiền/ }).click();
  await expect(page).toHaveURL(/\/owner$/);
}

/**
 * Đang ở Mặt tiền (/owner$) và trang đầu là Tình báo: mục `owner-nav-intel` (thanh trái, máy tính) hoặc `owner-tabbar-intel`
 * (thanh dưới, điện thoại) đang sáng (aria-current="page").
 */
export async function expectOwnerFront(page: Page): Promise<void> {
  await expect(page).toHaveURL(/\/owner$/);
  const intel = page.locator('[data-testid="owner-nav-intel"]:visible, [data-testid="owner-tabbar-intel"]:visible').first();
  await expect(intel).toHaveAttribute('aria-current', 'page');
}
