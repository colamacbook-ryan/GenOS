import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test, type BrowserContext, type Page } from '@playwright/test';
import { SCREENS } from '@gen-harness/contracts';
import { AUDITOR, loginAs, loginAsOwner, repoRoot, resetMock } from './support';

/**
 * Lưới L4 (v0.1.61) — Playwright DUYỆT MỌI ROUTE bằng mock với vai Owner (v0.1.68: thêm Operator và Auditor sau khi sửa F145), ở 390 và 1440, bắt:
 *
 *  - `console`   console.error (React cảnh báo, lỗi chưa bắt…) — không tính dòng "Failed to load resource" (đã nằm ở `http`);
 *  - `pageerror` lỗi JS chưa bắt;
 *  - `http`      mọi phản hồi 4xx/5xx cùng nguồn — NGOẠI TRỪ 423 khi hộp "Mã PIN xác nhận thao tác" đang mở (đó là cơ chế PIN);
 *  - `link`      <a href> nội bộ dẫn tới trang 404 (mở thật từng đích một lần);
 *  - `rac`       chữ hiển thị chứa «[object Object]», «undefined», «NaN», «null»;
 *  - `term:<id>` chữ hiển thị khớp một mẫu cấm `crawl` của `.github/scripts/terms.json` (Value error, failed, worker, Bridge, TUI…);
 *  - `approve`   nút «Duyệt» còn hiện cho vai KHÔNG có action.approve (Operator và Auditor đã thêm vào ROLES ở v0.1.68 (gói D1,
 *                sau khi sửa F145) nên luật này chạy trên trang thật; còn có test riêng).
 *
 * KHÔNG có danh sách bỏ qua route. Hiện trạng (nợ có sẵn lúc dựng lưới) nằm trong `crawl_baseline` của terms.json,
 * khoá `"<cỡ>|<route>|<luật>"` → số vi phạm. RATCHET như check_terms.py: vượt baseline ⇒ hỏng; baseline cao hơn thực tế ⇒ chỉ cảnh báo
 * (sửa xong thì hạ: chạy lại với CRAWL_LOWER_BASELINE=1 rồi commit terms.json). «DƯ» chỉ là cảnh báo (CRAWL_STRICT_STALE=1 để bắt đỏ). Dựng baseline lần đầu: CRAWL_SEED_BASELINE=1.
 * Chữ chuẩn/cấm lấy từ cùng terms.json với check_terms.py nên một nguồn sự thật.
 */

const ORIGIN = `http://localhost:${Number(process.env.E2E_PORT ?? 5174)}`;
const TERMS_PATH = join(repoRoot, '.github/scripts/terms.json');

interface TermsFile {
  forbidden: Array<{ id: string; pattern: string; ignorecase?: boolean; crawl?: boolean; use?: string }>;
  crawl_baseline: Record<string, number>;
  [k: string]: unknown;
}

function readTerms(): TermsFile {
  return JSON.parse(readFileSync(TERMS_PATH, 'utf8')) as TermsFile;
}

interface CrawlRule {
  id: string;
  re: RegExp;
}

/** Mẫu cấm dùng cho chữ hiển thị. Cú pháp mẫu phải chạy được ở cả Python `re` lẫn JavaScript RegExp. */
function crawlRules(terms: TermsFile): CrawlRule[] {
  return terms.forbidden
    .filter((r) => r.crawl)
    .map((r) => ({ id: `term:${r.id}`, re: new RegExp(r.pattern.normalize('NFC'), r.ignorecase ? 'giu' : 'gu') }));
}

const DEFECT = /\[object Object\]|\bundefined\b|\bNaN\b|\bnull\b/gu;

/** Thuần: trả về các vi phạm chữ (luật → các đoạn khớp, đã khử trùng) trong một khối chữ hiển thị. */
function findTextDefects(text: string, rules: CrawlRule[]): Array<{ rule: string; detail: string }> {
  const norm = text.normalize('NFC');
  const out = new Map<string, { rule: string; detail: string }>();
  const add = (rule: string, re: RegExp) => {
    for (const m of norm.matchAll(new RegExp(re.source, re.flags.includes('g') ? re.flags : `${re.flags}g`))) {
      const at = m.index ?? 0;
      const around = norm.slice(Math.max(0, at - 24), at + m[0].length + 24).replace(/\s+/g, ' ');
      out.set(`${rule}|${m[0]}`, { rule, detail: `«${m[0]}» …${around}…` });
    }
  };
  add('rac', DEFECT);
  for (const r of rules) add(r.id, r.re);
  return [...out.values()];
}

/** Nút «Duyệt…» đang hiện (dùng cho vai không có action.approve). */
async function listApproveButtons(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>('button, [role="button"]'))
      .filter((el) => el.offsetParent !== null || getComputedStyle(el).position === 'fixed')
      .map((el) => (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' '))
      .filter((label) => /^Duyệt(\s|$)/u.test(label)),
  );
}

// ── Danh sách route: không có danh sách bỏ qua ────────────────────────────────────────────────────────────────────
function guideSteps(): string[] {
  const src = readFileSync(join(repoRoot, 'apps/web/src/guide/guideContent.ts'), 'utf8');
  return [...src.matchAll(/^\s*n:\s*(\d+),/gm)].map((m) => `/guide/${m[1]}`);
}

const NOT_FOUND_ROUTE = '/khong-co-trang-nay';

function allRoutes(): string[] {
  return [
    '/',
    ...SCREENS.map((s) => `/${s.key}`),
    '/guide',
    '/guide/viec-sep',
    ...guideSteps(),
    '/account',
    '/help',
    '/social',
    '/social/ghi-facebook',
    '/owner',
    '/owner/quan-he',
    '/owner/viec',
    '/owner/gen',
    '/owner/them',
    NOT_FOUND_ROUTE,
  ];
}

/**
 * Vai được duyệt (khoá baseline KHÔNG tách theo vai — số vi phạm của các vai cộng chung theo route/luật). Operator và Auditor
 * không có action.approve (mock-api MATRIX). KHÔNG dùng danh sách bỏ qua để che lỗi của vai nào.
 */
const ROLES = [
  { key: 'owner', email: null, canApprove: true },
  { key: 'operator', email: 'operator@genesis.local', canApprove: false },
  { key: 'auditor', email: AUDITOR.email, canApprove: false },
] as const;

const VIEWPORTS = [
  { width: 390, height: 844 },
  { width: 1440, height: 900 },
] as const;

/** Trang có khung ngoài (thanh bên/đầu trang) cần quét chữ một lần, không lặp ở mọi route. */
const CHROME_ROUTES = new Set(['/overview', '/owner']);

interface Finding {
  route: string;
  rule: string;
  detail: string;
}

interface PageDump {
  main: string;
  chrome: string;
  hrefs: string[];
}

async function dump(page: Page): Promise<PageDump> {
  return page.evaluate(() => {
    const attrs = (root: ParentNode) =>
      Array.from(root.querySelectorAll('[aria-label],[title],[placeholder],img[alt]'))
        .flatMap((el) => ['aria-label', 'title', 'placeholder', 'alt'].map((a) => el.getAttribute(a) ?? ''))
        .filter(Boolean)
        .join('\n');
    const mains = Array.from(document.querySelectorAll<HTMLElement>('main, [role="dialog"]'));
    const body = document.body;
    const main = (mains.length ? mains : [body]).map((m) => `${m.innerText}\n${attrs(m)}`).join('\n');
    const outside = Array.from(document.querySelectorAll<HTMLElement>('aside, header, nav')).filter((el) => !el.closest('main'));
    const chrome = outside.map((m) => `${m.innerText}\n${attrs(m)}`).join('\n');
    const hrefs = Array.from(document.querySelectorAll<HTMLAnchorElement>('a[href]'))
      .map((a) => a.getAttribute('href') ?? '')
      .filter((h) => h.startsWith('/') && !h.startsWith('//') && !h.startsWith('/api/') && !h.startsWith('/__'))
      .map((h) => h.replace(/#.*$/, ''))
      .filter(Boolean);
    return { main, chrome, hrefs: [...new Set(hrefs)] };
  });
}

/**
 * Chờ mạng lặng: không còn yêu cầu HTTP nào đang bay (bỏ WebSocket/SSE) trong 300ms, tối đa `maxMs`. Không dùng
 * `networkidle` của Playwright vì màn có thăm dò định kỳ khiến nó chờ đủ giờ ở mọi route.
 */
async function settleNetwork(page: Page, maxMs = 3000): Promise<void> {
  await page.evaluate(
    (limit) =>
      new Promise<void>((resolve) => {
        const started = performance.now();
        let last = performance.now();
        let seen = performance.getEntriesByType('resource').length;
        const tick = () => {
          const now = performance.now();
          const count = performance.getEntriesByType('resource').length;
          if (count !== seen) {
            seen = count;
            last = now;
          }
          if (now - last >= 300 || now - started >= limit) resolve();
          else setTimeout(tick, 60);
        };
        tick();
      }),
    maxMs,
  );
}

/**
 * Chờ màn vẽ xong chữ: hết khung chờ (skeleton / aria-busy) rồi mới đọc chữ. Mạng lặng chưa đủ — màn có thể còn vẽ sau khi yêu cầu
 * cuối trả về, khi đó chữ hiện trễ làm số lần khớp ít hơn baseline và gây "DƯ" giả. Có trần nên màn kẹt khung chờ vẫn qua được.
 */
async function settleRender(page: Page, maxMs = 2000): Promise<void> {
  await page
    .waitForFunction(() => !document.querySelector('[aria-busy="true"], .gh-skeleton'), undefined, { timeout: maxMs })
    .catch(() => undefined);
}

/** Đích liên kết → có phải trang 404 không (mở thật một lần, nhớ kết quả). */
const linkVerdict = new Map<string, boolean>();
/** Một trang thăm dò cho mỗi ngữ cảnh: đi tới đích bằng router của chính app (pushState), không nạp lại cả trang. */
const probePages = new WeakMap<BrowserContext, Page>();

async function isDeadLink(context: BrowserContext, path: string): Promise<boolean> {
  const known = linkVerdict.get(path);
  if (known !== undefined) return known;
  let probe = probePages.get(context);
  if (!probe || probe.isClosed()) {
    probe = await context.newPage();
    probePages.set(context, probe);
    await probe.goto('/overview', { waitUntil: 'domcontentloaded' });
    await settleNetwork(probe, 1500);
  }
  await probe.evaluate((to) => {
    window.history.pushState({}, '', to);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }, path);
  await settleNetwork(probe, 1500);
  const dead = await probe.getByRole('heading', { name: 'Không tìm thấy trang' }).isVisible().catch(() => false);
  linkVerdict.set(path, dead);
  return dead;
}

async function crawlRoute(page: Page, context: BrowserContext, route: string, canApprove: boolean, rules: CrawlRule[]): Promise<Finding[]> {
  const found: Finding[] = [];
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const responses: Array<{ status: number; line: string }> = [];
  const onConsole = (m: { type(): string; text(): string }) => {
    if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) consoleErrors.push(m.text().slice(0, 200));
  };
  const onPageError = (e: Error) => pageErrors.push(String(e).slice(0, 200));
  const onResponse = (r: { status(): number; url(): string; request(): { method(): string } }) => {
    if (r.status() < 400) return;
    const u = new URL(r.url());
    if (u.origin !== ORIGIN) return;
    responses.push({ status: r.status(), line: `${r.status()} ${r.request().method()} ${u.pathname}` });
  };
  page.on('console', onConsole);
  page.on('pageerror', onPageError);
  page.on('response', onResponse);
  try {
    await page.goto(route, { waitUntil: 'domcontentloaded' });
    await settleNetwork(page);
    await settleRender(page);
    const pinOpen = await page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' }).isVisible().catch(() => false);
    const here = new URL(page.url()).pathname;
    const label = here === route || route === '/' ? route : `${route}→${here}`;
    for (const c of new Set(consoleErrors)) found.push({ route: label, rule: 'console', detail: c });
    for (const e of new Set(pageErrors)) found.push({ route: label, rule: 'pageerror', detail: e });
    for (const r of responses) {
      if (r.status === 423 && pinOpen) continue;
      found.push({ route: label, rule: 'http', detail: r.line });
    }
    const d = await dump(page);
    for (const t of findTextDefects(d.main, rules)) found.push({ route: label, rule: t.rule, detail: t.detail });
    if (CHROME_ROUTES.has(route)) {
      for (const t of findTextDefects(d.chrome, rules)) found.push({ route: `${label}#khung`, rule: t.rule, detail: t.detail });
    }
    for (const href of d.hrefs) {
      if (await isDeadLink(context, href)) found.push({ route: label, rule: 'link', detail: `→ ${href} (404)` });
    }
    if (!canApprove) {
      for (const b of await listApproveButtons(page)) found.push({ route: label, rule: 'approve', detail: `nút «${b}»` });
    }
  } finally {
    page.off('console', onConsole);
    page.off('pageerror', onPageError);
    page.off('response', onResponse);
  }
  return found;
}

/** Đếm (route, luật) → số mục khác nhau; khoá baseline `"<cỡ>|<route>|<luật>"`. */
function tally(width: number, findings: Finding[]): Record<string, number> {
  const seen = new Map<string, Set<string>>();
  for (const f of findings) {
    const key = `${width}|${f.route}|${f.rule}`;
    if (!seen.has(key)) seen.set(key, new Set());
    seen.get(key)!.add(f.detail);
  }
  return Object.fromEntries([...seen].map(([k, v]) => [k, v.size]));
}

/** So với baseline: vượt ⇒ lỗi mới; thấp hơn ⇒ baseline dư. Trả về hai danh sách câu thông báo. */
function ratchet(width: number, observed: Record<string, number>, baseline: Record<string, number>): { over: string[]; stale: string[] } {
  const over: string[] = [];
  const stale: string[] = [];
  for (const [k, n] of Object.entries(observed)) {
    const allowed = baseline[k] ?? 0;
    if (n > allowed) over.push(`${k}: ${n} (baseline ${allowed})`);
  }
  for (const [k, allowed] of Object.entries(baseline)) {
    if (!k.startsWith(`${width}|`)) continue;
    const n = observed[k] ?? 0;
    if (n < allowed) stale.push(`${k}: baseline ${allowed} nhưng chỉ còn ${n} — hạ baseline (CRAWL_LOWER_BASELINE=1)`);
  }
  return { over, stale };
}

function writeBaseline(width: number, mode: 'seed' | 'lower', observed: Record<string, number>): void {
  const terms = readTerms();
  const keep = Object.fromEntries(Object.entries(terms.crawl_baseline).filter(([k]) => !k.startsWith(`${width}|`)));
  const mine: Record<string, number> = {};
  if (mode === 'seed') {
    Object.assign(mine, observed);
  } else {
    for (const [k, allowed] of Object.entries(terms.crawl_baseline)) {
      if (!k.startsWith(`${width}|`)) continue;
      const n = Math.min(allowed, observed[k] ?? 0);
      if (n > 0) mine[k] = n;
    }
  }
  const merged = { ...keep, ...mine };
  terms.crawl_baseline = Object.fromEntries(Object.entries(merged).sort(([a], [b]) => a.localeCompare(b)));
  writeFileSync(TERMS_PATH, `${JSON.stringify(terms, null, 2)}\n`, 'utf8');
}

test.describe('route-crawl — duyệt mọi route, bắt lỗi console/HTTP/link chết/chữ kỹ thuật', () => {
  for (const vp of VIEWPORTS) {
    test(`mọi route (${vp.width}px) không thêm lỗi so với baseline`, async ({ page, context }) => {
      // Trần 15 phút/test (v0.1.68: ba vai Owner/Operator/Auditor nên gấp ~3 lần; CI chạy bình thường ~vài phút); mỗi route tối đa ~20 giây.
      test.setTimeout(900_000);
      page.setDefaultTimeout(15_000);
      page.setDefaultNavigationTimeout(20_000);
      await page.setViewportSize(vp);
      await resetMock(page.request, 'finished');
      await loginAsOwner(page);
      const rules = crawlRules(readTerms());
      const findings: Finding[] = [];
      for (const role of ROLES) {
        if (role.email) {
          // Đổi vai: bỏ phiên Owner rồi đăng nhập vai kia (cùng mật khẩu mock).
          await page.context().clearCookies();
          await loginAs(page, role.email);
        }
        for (const route of allRoutes()) {
          const t0 = Date.now();
          findings.push(...(await crawlRoute(page, context, route, role.canApprove, rules)));
          if (process.env.CRAWL_DEBUG) console.log(`[crawl ${vp.width}] ${route} ${Date.now() - t0}ms`);
        }
      }
      const observed = tally(vp.width, findings);
      const mode = process.env.CRAWL_SEED_BASELINE ? 'seed' : process.env.CRAWL_LOWER_BASELINE ? 'lower' : null;
      const baseline = readTerms().crawl_baseline;
      const { over, stale } = ratchet(vp.width, observed, baseline);
      if (mode === 'seed') {
        writeBaseline(vp.width, 'seed', observed);
        return;
      }
      if (mode === 'lower' && over.length === 0) {
        writeBaseline(vp.width, 'lower', observed);
        return;
      }
      const detail = (keyPrefix: string) =>
        findings
          .filter((f) => `${vp.width}|${f.route}|${f.rule}` === keyPrefix)
          .map((f) => `    - ${f.detail}`)
          .slice(0, 4);
      // MỚI (vượt baseline) làm đỏ. DƯ (baseline cao hơn thực tế) chỉ CẢNH BÁO: chữ có thể hiện trễ so với lúc đọc nên ít hơn baseline
      // là báo động giả do thời gian; hạ baseline thật bằng CRAWL_LOWER_BASELINE=1 khi thấy cảnh báo lặp lại. CRAWL_STRICT_STALE=1 bắt DƯ làm đỏ.
      for (const s of stale) {
        console.warn(`[crawl ${vp.width}] DƯ ${s}`);
        test.info().annotations.push({ type: 'cảnh báo DƯ', description: s });
      }
      const lines = [
        ...over.flatMap((o) => [`MỚI ${o}`, ...detail(o.slice(0, o.indexOf(': ')))]),
        ...(process.env.CRAWL_STRICT_STALE ? stale.map((s) => `DƯ ${s}`) : []),
      ];
      expect(lines, `Route-crawl ${vp.width}px:\n${lines.join('\n')}`).toEqual([]);
    });
  }

  test('luật bắt chữ: bắt chữ lỗi/chữ cấm, tha chữ đúng', () => {
    const rules = crawlRules(readTerms());
    const bad = findTextDefects(
      'Lỗi [object Object] và undefined, NaN, null. Value error, trạng thái failed. Gen đọc Kho tri thức, nhập PIN. Bridge, TUI, chat_id.',
      rules,
    );
    const ids = new Set(bad.map((b) => b.rule));
    for (const want of ['rac', 'term:value-error', 'term:trang-thai-tho', 'term:kho-tri-thuc', 'term:nhap-pin', 'term:tu-ky-thuat']) {
      expect(ids, `phải bắt ${want}`).toContain(want);
    }
    const good = findTextDefects('Gen đọc Kho dữ liệu, nhập mã PIN. Đã duyệt, thất bại, tiến trình nền, cầu nối. Khóa học. Số 0 và Nullable.', rules);
    expect(good).toEqual([]);
    expect(findTextDefects('[object Object]', rules)).toHaveLength(1);
  });

  test('luật nút Duyệt: nhận ra nút Duyệt đang hiện, bỏ qua nút ẩn và nút khác', async ({ page }) => {
    await page.setContent(
      '<button>Duyệt</button><button>Duyệt &amp; gửi</button><button style="display:none">Duyệt ẩn</button><button>Huỷ</button><button>Xem duyệt</button>',
    );
    expect(await listApproveButtons(page)).toEqual(['Duyệt', 'Duyệt & gửi']);
  });

  test('ratchet: vượt baseline hỏng, baseline dư hỏng, đúng baseline thì qua', () => {
    const base = { '390|/inbox|http': 2, '1440|/inbox|http': 1 };
    expect(ratchet(390, { '390|/inbox|http': 2 }, base)).toEqual({ over: [], stale: [] });
    expect(ratchet(390, { '390|/inbox|http': 3 }, base).over).toHaveLength(1);
    expect(ratchet(390, { '390|/help|link': 1 }, base).over).toHaveLength(1);
    expect(ratchet(390, { '390|/inbox|http': 1 }, base).stale).toHaveLength(1);
    expect(ratchet(390, {}, base).stale).toHaveLength(1);
    expect(tally(390, [
      { route: '/a', rule: 'http', detail: 'x' },
      { route: '/a', rule: 'http', detail: 'x' },
      { route: '/a', rule: 'http', detail: 'y' },
    ])).toEqual({ '390|/a|http': 2 });
  });

  test('http: bắt phản hồi 4xx/5xx; 423 khi hộp PIN mở thì được tha', async ({ page, context }) => {
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
    let status = 500;
    await page.route(/\/api\/v1\/(?!__mock|auth|health)/, (route) =>
      route.fulfill({ status, contentType: 'application/json', body: '{"detail":"x"}' }),
    );
    const rules = crawlRules(readTerms());
    const five = await crawlRoute(page, context, '/help', true, rules);
    expect(five.filter((f) => f.rule === 'http').map((f) => f.detail.slice(0, 3))).toContain('500');
    status = 423;
    const locked = await crawlRoute(page, context, '/help', true, rules);
    await expect(page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' })).toBeVisible();
    expect(locked.filter((f) => f.rule === 'http'), '423 khi hộp PIN mở là cơ chế PIN, không phải lỗi').toEqual([]);
    status = 404;
    await page.keyboard.press('Escape');
    const missing = await crawlRoute(page, context, '/help', true, rules);
    expect(missing.filter((f) => f.rule === 'http').map((f) => f.detail.slice(0, 3))).toContain('404');
  });

  test('link chết: phát hiện được đích 404 trên trang thật', async ({ page, context }) => {
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
    expect(await isDeadLink(context, NOT_FOUND_ROUTE)).toBe(true);
    expect(await isDeadLink(context, '/overview')).toBe(false);
  });
});
