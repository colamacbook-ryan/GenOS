// staging-smoke (Gen kiểm từ xa): chạy trên stack THẬT dựng bằng docker compose (xem .github/workflows/staging-smoke.yml).
// Việc làm: (1) gọi health/ready qua proxy; (2) thiết lập lần đầu bằng API (mã thiết lập do workflow đặt qua GH_SETUP_TOKEN)
// → tạo Sếp + PIN giả; (3) đăng nhập qua giao diện; (4) chụp ảnh các màn ở 390 và 1440 px, ghi lỗi console + HTTP >= 400.
// Chạy:  SETUP_TOKEN=… OUT=<thư mục> node scripts/staging-smoke/shots.mjs   (cần `npm ci` ở gốc repo + Chromium của Playwright)
// Thoát 1 nếu bất kỳ bước smoke nào đỏ; ảnh/JSON vẫn được ghi tới đâu hay tới đó.
import { chromium } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';

const BASE = process.env.BASE_URL ?? 'https://localhost:8443';
const OUT = process.env.OUT ?? 'staging-smoke-out';
const TOKEN = process.env.SETUP_TOKEN;
if (!TOKEN) {
  console.error('Thiếu SETUP_TOKEN (workflow đặt GH_SETUP_TOKEN trong .env rồi truyền sang đây).');
  process.exit(2);
}
// Dữ liệu giả, chỉ sống trong runner dùng một lần; mật khẩu/PIN sinh ngẫu nhiên mỗi lượt, không in ra log.
const rnd = (n) => Array.from({ length: n }, () => Math.floor(Math.random() * 10)).join('');
const OWNER = { name: 'Sếp Thử', email: 'sep.thu@example.test', password: `thu-nghiem-${rnd(12)}`, pin: rnd(6) };

// Khoảng ~12 route chính (khoá lấy từ docs/design/screens.json — cùng nguồn router apps/web/src/router.tsx).
const ROUTES = [
  { path: '/overview', name: 'hom-nay' },
  { path: '/inbox', name: 'hop-thu' },
  { path: '/workbench', name: 'ban-lam-viec' },
  { path: '/directory', name: 'khach-nhom' },
  { path: '/opportunity', name: 'co-hoi' },
  { path: '/people', name: 'danh-gia-nguoi' },
  { path: '/system', name: 'cai-dat' },
  { path: '/raw', name: 'kho-tho' },
  { path: '/agents', name: 'agent' },
  { path: '/api', name: 'api-model' },
];
const VIEWPORTS = [{ w: 390, h: 844 }, { w: 1440, h: 900 }];

mkdirSync(`${OUT}/shots`, { recursive: true });
const smoke = [];
const pageReports = [];
const record = (name, ok, detail = '') => {
  smoke.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`);
};
const step = async (name, fn) => {
  try {
    const detail = await fn();
    record(name, true, detail ?? '');
    return true;
  } catch (e) {
    record(name, false, String(e instanceof Error ? e.message : e).split('\n')[0]);
    return false;
  }
};

const browser = await chromium.launch(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {});
const newCtx = (vp) =>
  browser.newContext({ ignoreHTTPSErrors: true, baseURL: BASE, locale: 'vi-VN', timezoneId: 'Asia/Ho_Chi_Minh',
    viewport: { width: vp.w, height: vp.h } });

/** Mở một trang, gom lỗi console + HTTP >= 400 trong lúc tải, chụp ảnh. */
async function visit(page, vp, name, path) {
  const consoleErrors = [];
  const httpErrors = [];
  const onConsole = (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 500)); };
  const onPageError = (e) => consoleErrors.push(`pageerror: ${String(e.message).slice(0, 500)}`);
  const onResponse = (r) => { if (r.status() >= 400) httpErrors.push({ status: r.status(), method: r.request().method(), url: r.url().replace(BASE, '') }); };
  page.on('console', onConsole);
  page.on('pageerror', onPageError);
  page.on('response', onResponse);
  let navError = null;
  try {
    await page.goto(path, { waitUntil: 'domcontentloaded', timeout: 30_000 });
    await page.waitForLoadState('networkidle', { timeout: 15_000 }).catch(() => {});
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${OUT}/shots/${name}-${vp.w}.png`, fullPage: true });
  } catch (e) {
    navError = String(e instanceof Error ? e.message : e).split('\n')[0];
  }
  page.off('console', onConsole);
  page.off('pageerror', onPageError);
  page.off('response', onResponse);
  pageReports.push({ name, path, width: vp.w, finalUrl: page.url().replace(BASE, ''), navError, consoleErrors, httpErrors });
}

async function main() {
  // 1) Health/ready qua proxy thật (đường: Caddy → api / web). Đường dẫn lấy từ apps/api/gh/shell/routes.py + apps/web/nginx.conf.
  const anon = await newCtx(VIEWPORTS[1]);
  const get = async (path, okStatus = [200]) => {
    const r = await anon.request.get(path);
    if (!okStatus.includes(r.status())) throw new Error(`GET ${path} → ${r.status()}`);
    return r;
  };
  await step('GET /api/v1/health (api qua proxy)', async () => { await get('/api/v1/health'); });
  await step('GET /api/v1/ready (db, redis)', async () => JSON.stringify(await (await get('/api/v1/ready')).json()));
  await step('GET /healthz (web qua proxy)', async () => { await get('/healthz'); });
  await step('GET /api/v1/setup/state (chưa thiết lập)', async () => {
    const s = await (await get('/api/v1/setup/state')).json();
    if (s.owner_created) throw new Error('Owner đã tồn tại — stack không sạch');
  });

  // 2) Ảnh trang thiết lập (trước khi có Owner), 2 khổ.
  for (const vp of VIEWPORTS) {
    const ctx = await newCtx(vp);
    const page = await ctx.newPage();
    await visit(page, vp, 'thiet-lap', `/setup?token=${encodeURIComponent(TOKEN)}`);
    await ctx.close();
  }

  // 3) Thiết lập bằng API (bước 1–3 bắt buộc, rồi 12 hoàn tất) — cùng hợp đồng e2e-live/live-ci.spec.ts.
  const csrf = async () => (await anon.cookies()).find((c) => c.name === 'gh_csrf')?.value ?? '';
  const put = async (path, data) => {
    const r = await anon.request.put(`/api/v1${path}`, { data, headers: { 'X-CSRF-Token': await csrf() } });
    if (!r.ok()) throw new Error(`PUT ${path} → ${r.status()}`);
  };
  await step('Thiết lập bước 1 (chào mừng, mã thiết lập)', () => put('/setup/steps/1', { token: TOKEN, language: 'vi', mode: 'empty' }));
  await step('Thiết lập bước 2 (tạo Sếp + PIN giả)', () => put('/setup/steps/2', {
    token: TOKEN, display_name: OWNER.name, email: OWNER.email, password: OWNER.password, pin: OWNER.pin, pin_confirm: OWNER.pin }));
  await step('Thiết lập bước 3 (tổ chức)', () => put('/setup/steps/3', {
    org_name: 'Tổ chức thử', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND', self_name: 'Sếp', bot_calls_me: 'Sếp' }));
  await step('Thiết lập bước 12 (hoàn tất)', () => put('/setup/steps/12', undefined));
  await anon.close();

  // 4) Đăng nhập qua giao diện + chụp các màn, mỗi khổ một context sạch.
  for (const vp of VIEWPORTS) {
    const ctx = await newCtx(vp);
    const page = await ctx.newPage();
    await visit(page, vp, 'dang-nhap', '/login');
    const ok = await step(`Đăng nhập qua giao diện (${vp.w}px)`, async () => {
      await page.getByLabel('Email').fill(OWNER.email);
      await page.getByLabel('Mật khẩu', { exact: true }).fill(OWNER.password);
      await page.getByRole('button', { name: 'Đăng nhập' }).click();
      await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20_000 });
      const me = await ctx.request.get('/api/v1/auth/me');
      if (me.status() !== 200) throw new Error(`GET /auth/me → ${me.status()}`);
    });
    if (ok) for (const r of ROUTES) await visit(page, vp, r.name, r.path);
    await ctx.close();
  }
}

try {
  await main();
} catch (e) {
  record('Script smoke (lỗi không mong đợi)', false, String(e instanceof Error ? e.stack : e).split('\n').slice(0, 3).join(' | '));
} finally {
  await browser.close();
  const withConsole = pageReports.filter((p) => p.consoleErrors.length > 0).length;
  const withHttp = pageReports.filter((p) => p.httpErrors.length > 0).length;
  const failed = smoke.filter((s) => !s.ok).length;
  writeFileSync(`${OUT}/smoke.json`, JSON.stringify({ smoke, failed }, null, 2));
  writeFileSync(`${OUT}/page-errors.json`, JSON.stringify({ pages: pageReports.length, withConsoleErrors: withConsole, withHttpErrors: withHttp, reports: pageReports }, null, 2));
  const md = [
    `- Smoke: ${smoke.length - failed}/${smoke.length} bước đạt${failed ? ` — **${failed} đỏ**: ${smoke.filter((s) => !s.ok).map((s) => s.name).join('; ')}` : ''}`,
    `- Trang đã chụp: ${pageReports.length} (390 + 1440 px) — ${withConsole} trang có lỗi console, ${withHttp} trang có HTTP >= 400`,
  ].join('\n');
  writeFileSync(`${OUT}/summary.md`, `${md}\n`);
  console.log(md);
  process.exitCode = failed ? 1 : 0;
}
