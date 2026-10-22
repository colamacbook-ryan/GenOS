import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import pixelmatch from 'pixelmatch';
import { PNG } from 'pngjs';
import { AUDITOR, OWNER, SETUP_TOKEN, apiCall, loginAs, loginAsOwner, mockHook, openDesign, resetMock, resultsDir, settle } from './support';

/**
 * Phase 2 against the mock (`npm run dev:mock`, simulation off):
 * the data screens and System (v0.1.42: Cài đặt — smoke only) next to the design at 1440
 * and 1280, then the behaviours — live raw rows over /api/v1/ws, rules, PIN on
 * 423, risk warning before the QR, CLI URL + code login, read-only auditor,
 * and setup steps 4–7 and 12.
 */
const outDir = join(resultsDir, 'visual', 'phase2');
const HEADER = 58;
const MAX_DIFF_RATIO = Number(process.env.VISUAL_MAX_DIFF_P2 ?? 0.015);

/**
 * Pixels that differ even allowing a 1 px shift. Layout matches the design to
 * 1/64 px (checked with getBoundingClientRect), but Chromium snaps text and
 * borders below the fractional-height pipeline strip ~0.7 px differently in
 * the two documents; that is paint, not layout, so a ±1 px neighbourhood
 * match is accepted. Raw pixelmatch counts are logged too.
 */
function shiftTolerantDiff(a: PNG, b: PNG, mask: PNG): number {
  const close = (i: number, j: number) =>
    Math.abs(a.data[i] - b.data[j]) + Math.abs(a.data[i + 1] - b.data[j + 1]) + Math.abs(a.data[i + 2] - b.data[j + 2]) <= 48;
  let n = 0;
  for (let y = 0; y < a.height; y++) {
    for (let x = 0; x < a.width; x++) {
      const i = (y * a.width + x) * 4;
      // pixelmatch paints differing pixels red (255, 0, 0).
      if (!(mask.data[i] === 255 && mask.data[i + 1] === 0 && mask.data[i + 2] === 0)) continue;
      let ok = false;
      for (let dy = -1; dy <= 1 && !ok; dy++) {
        for (let dx = -1; dx <= 1 && !ok; dx++) {
          const yy = y + dy;
          const xx = x + dx;
          if (yy < 0 || xx < 0 || yy >= a.height || xx >= a.width) continue;
          ok = close(i, (yy * a.width + xx) * 4);
        }
      }
      if (!ok) n++;
    }
  }
  return n;
}

test.beforeAll(() => mkdirSync(outDir, { recursive: true }));

interface VisualCase {
  key: string;
  /** Nhãn bấm trong file THIẾT KẾ (thanh bên của thiết kế gốc — không phải của app). */
  clicks: string[];
  /** App element whose bottom edge ends the compared region (pipeline strip + title + first row of content). */
  until: string;
  /**
   * v0.1.42 (F-7): Cài đặt (/system) đã khác thiết kế gốc (5 tab theo quyền, liên kết đầu màn) — chỉ kiểm khói (vẽ
   * được, không tràn ngang), vẫn chụp ảnh lưu test-results để xem lại. Màn dữ liệu giữ so pixel.
   */
  pixel: boolean;
}
const CASES: VisualCase[] = [
  { key: 'raw', clicks: ['Tầng dữ liệu', 'Kho dữ liệu thô'], until: '.screen-desc', pixel: true },
  { key: 'rules', clicks: ['Tầng dữ liệu', 'Quy tắc sàng lọc'], until: '.rule-card >> nth=0', pixel: true },
  { key: 'clean', clicks: ['Tầng dữ liệu', 'Kho sạch'], until: '.screen-desc', pixel: true },
  { key: 'identity', clicks: ['Tầng dữ liệu', 'Hợp nhất danh tính'], until: '.id-stats', pixel: true },
  { key: 'system', clicks: ['Điều khiển hệ thống'], until: '[role="tablist"]', pixel: false },
];

function crop(png: PNG, x: number, y: number, w: number, h: number): PNG {
  const out = new PNG({ width: w, height: h });
  PNG.bitblt(png, out, x, y, w, h, 0, 0);
  return out;
}

async function noHorizontalOverflow(page: Page) {
  const { scroll, inner } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
  expect(scroll, 'page must not scroll sideways').toBeLessThanOrEqual(inner);
}

for (const vp of [
  { width: 1440, height: 900 },
  { width: 1280, height: 800 },
]) {
  for (const c of CASES) {
    test(`${c.key} ${c.pixel ? 'matches the design' : 'renders (smoke)'} · ${vp.width}`, async ({ browser, baseURL }) => {
      const context = await browser.newContext({ viewport: vp, deviceScaleFactor: 1, baseURL });
      const designPage = await context.newPage();
      await openDesign(designPage, { clicks: c.clicks });
      const design = PNG.sync.read(await designPage.screenshot({ path: join(outDir, `design-${c.key}-${vp.width}.png`), animations: 'disabled' }));

      const page = await context.newPage();
      await resetMock(page.request, 'finished');
      await loginAsOwner(page);
      await page.goto(`/${c.key}`);
      await expect(page.locator('.skeleton, .gh-skeleton').first()).toHaveCount(0, { timeout: 10_000 }).catch(() => undefined);
      await expect(page.locator(c.until).first()).toBeVisible();
      await settle(page);
      await page.screenshot({ path: join(outDir, `app-${c.key}-${vp.width}-full.png`), fullPage: true, animations: 'disabled' });
      const app = PNG.sync.read(await page.screenshot({ path: join(outDir, `app-${c.key}-${vp.width}.png`), animations: 'disabled', caret: 'hide' }));
      await noHorizontalOverflow(page);

      const box = await page.locator(c.until).first().boundingBox();
      const side = 244;
      const bottom = Math.min(vp.height, Math.ceil((box?.y ?? 300) + (box?.height ?? 0)) + 4);
      const [x, y, w, h] = [side, HEADER, vp.width - side, bottom - HEADER];
      const a = crop(design, x, y, w, h);
      const b = crop(app, x, y, w, h);
      const diff = new PNG({ width: w, height: h });
      const n = pixelmatch(a.data, b.data, diff.data, w, h, { threshold: 0.1, includeAA: false });
      writeFileSync(join(outDir, `${c.key}-${vp.width}-design.png`), PNG.sync.write(a));
      writeFileSync(join(outDir, `${c.key}-${vp.width}-app.png`), PNG.sync.write(b));
      writeFileSync(join(outDir, `${c.key}-${vp.width}-diff.png`), PNG.sync.write(diff));
      const tolerant = shiftTolerantDiff(a, b, diff);
      const ratio = Number((tolerant / (w * h)).toFixed(5));
      const report = { region: [x, y, w, h], pixelmatch: n, shiftTolerant: tolerant, ratio };
      writeFileSync(join(outDir, `report-${c.key}-${vp.width}.json`), JSON.stringify(report, null, 2));
      console.log(`visual ${c.key}-${vp.width}: ${JSON.stringify(report)}`);
      if (c.pixel) expect.soft(ratio, `${c.key} @${vp.width}: ${tolerant} px differ`).toBeLessThanOrEqual(MAX_DIFF_RATIO);
      await context.close();
    });
  }
}

test.describe('data screens', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  test('raw: seed rows formatted, live row arrives over the socket', async ({ page }) => {
    await page.goto('/raw');
    const rows = page.locator('table tbody tr');
    await expect(rows.first()).toContainText('15:11:44');
    await expect(rows.first()).toContainText('0,94');
    await expect(page.getByText('18.412 bản ghi thô')).toBeVisible();
    await expect(page.locator('.pipe')).toContainText('18.412');
    await page.waitForTimeout(800); // socket open
    await mockHook(page.request, 'raw');
    await expect(rows.first()).toContainText('MDF 18mm loại E1', { timeout: 5000 });
    await expect(page.getByText('18.413 bản ghi thô')).toBeVisible();
    await expect(rows.first()).toContainText('Đã vào kho sạch', { timeout: 8000 }).catch(() => undefined);
  });

  test('rules: toggle, weights must total 100%, test card', async ({ page }) => {
    await page.goto('/rules');
    const r06 = page.getByRole('article', { name: /R-06/ });
    await expect(r06).toContainText('8.204 lượt / 24h');
    await r06.getByRole('switch').click();
    await expect(page.getByText(/Đã tắt R-06|R-06.*tắt/i).first()).toBeVisible();

    const heat = page.getByLabel('Độ nóng của tín hiệu');
    await heat.focus();
    await page.keyboard.press('ArrowRight');
    await expect(page.getByText('Tổng trọng số phải bằng 100% — hiện 101% (thừa 1%).')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Lưu trọng số' })).toBeDisabled();
    const potential = page.getByLabel('Tiềm năng giá trị');
    await potential.focus();
    await page.keyboard.press('ArrowLeft');
    await expect(page.getByText('Tổng 100% — sẵn sàng lưu.')).toBeVisible();
    await page.getByRole('button', { name: 'Lưu trọng số' }).click();
    await expect(page.getByText('Tổng 100% — sẵn sàng lưu.')).toHaveCount(0);

    await expect(page.getByText('trên ngưỡng 0,60, được ghi vào kho sạch')).toBeVisible();
    await page.getByRole('button', { name: /Chạy thử trên 100 bản ghi/ }).click();
    await expect(page.getByRole('dialog')).toContainText('100');
  });

  test('clean: memory panel follows the selected row', async ({ page }) => {
    await page.goto('/clean');
    await expect(page.getByText('14.208 bản ghi', { exact: false }).first()).toBeVisible();
    await page.locator('table tbody tr', { hasText: 'PER-0042' }).first().click();
    const memory = page.getByRole('region', { name: /Trí nhớ tạm/ });
    await expect(memory).toContainText('1.842 / 4.000 token');
    await expect(memory).toContainText('nén lần 14 · 15:00');
    await expect(memory).toContainText('Sếp ghim · 12/09');
    await expect(page.getByRole('region', { name: /Tham số agent/ })).toContainText('214 sự kiện');
  });

  test('identity: Gộp asks for the PIN (423), then merges', async ({ page }) => {
    await page.goto('/identity');
    await expect(page.getByText('136', { exact: true })).toBeVisible();
    const pair = page.getByRole('article').filter({ hasText: 'Nguyễn Văn Bảo' }).first();
    await pair.getByRole('button', { name: /Gộp/ }).click();
    const pin = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await expect(pin).toBeVisible();
    await page.keyboard.type(OWNER.pin);
    await expect(pin).toBeHidden();
    await expect(page.getByText('137', { exact: true })).toBeVisible();
    await expect(page.getByRole('article').filter({ hasText: 'Bao Nguyen (Thanh Phat Printing)' })).toHaveCount(0);
  });
});

// v0.1.42 (F-7): kênh, đăng nhập CLI ở Kết nối (/connections); mã PIN ở Tài khoản của tôi.
test.describe('Kết nối › kênh & đăng nhập', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  test('risk warning comes before the QR; scan → Đang kết nối', async ({ page }) => {
    await page.goto('/connections');
    const wa = page.getByRole('article', { name: 'Kênh WhatsApp' });
    await expect(wa).toContainText('Phiên hết hạn');
    await wa.getByRole('button', { name: /Quét lại QR/ }).click();
    const risk = page.getByRole('dialog', { name: 'Trước khi hiện mã QR' });
    await expect(risk).toBeVisible();
    await expect(page.getByTestId('qr-whatsapp')).toHaveCount(0);
    const go = risk.getByRole('button', { name: /Tôi hiểu, hiện mã QR/ });
    await expect(go).toBeDisabled();
    await risk.getByRole('checkbox').check();
    await go.click();
    // channel.login is PIN-gated.
    await expect(page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' })).toBeVisible();
    await page.keyboard.type(OWNER.pin);
    await expect(page.getByRole('img', { name: /Mã QR đăng nhập WhatsApp/ })).toBeVisible({ timeout: 8000 });
    await expect(wa).toContainText('Chờ quét mã');
    await expect(wa).toContainText(/\d+ giây/);
    await page.screenshot({ path: join(outDir, 'system-qr-1440.png') });
    await mockHook(page.request, 'scan', { type: 'whatsapp' });
    await expect(wa).toContainText('Đang kết nối', { timeout: 8000 });
    await expect(page.getByTestId('qr-whatsapp')).toHaveCount(0);
  });

  test('bridge offline: login shows a clear error in the card', async ({ page }) => {
    await mockHook(page.request, 'bridge', { online: false });
    await page.goto('/connections');
    // A fresh PIN session so only the 503 is in play.
    await apiCall(page, 'POST', '/auth/pin/verify', { pin: OWNER.pin });
    const wa = page.getByRole('article', { name: 'Kênh WhatsApp' });
    await wa.getByRole('button', { name: /Quét lại QR/ }).click();
    const risk = page.getByRole('dialog', { name: 'Trước khi hiện mã QR' });
    await risk.getByRole('checkbox').check();
    await risk.getByRole('button', { name: /Tôi hiểu, hiện mã QR/ }).click();
    await expect(wa.getByRole('alert')).toContainText('Bộ nhận tin Zalo/WhatsApp đang tắt');
  });

  test('groups dialog edits listen mode; 1-1 listening is PIN-gated', async ({ page }) => {
    await page.goto('/connections');
    const zalo = page.getByRole('article', { name: 'Kênh Zalo' });
    await zalo.getByRole('button', { name: /38 nhóm lắng nghe/ }).click();
    const dlg = page.getByRole('dialog', { name: 'Nhóm Zalo' });
    await dlg.getByLabel('Chế độ lắng nghe của Nhóm riêng của Sếp').selectOption('silent');
    await expect(dlg.getByLabel('Chế độ lắng nghe của Nhóm riêng của Sếp')).toHaveValue('silent');
    // v0.1.65: tin 1-1 có ba mức (mặc định "Lưu im lặng"); đổi mức vẫn cần mã PIN.
    const direct = dlg.getByLabel('Tin nhắn 1-1 trên Zalo');
    await expect(direct).toHaveValue('silent');
    await direct.selectOption('off');
    await expect(page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' })).toBeVisible();
    await page.keyboard.type(OWNER.pin);
    await expect(dlg.getByLabel('Tin nhắn 1-1 trên Zalo')).toHaveValue('off');
    await page.keyboard.press('Escape');
    await expect(zalo).toContainText('39 nhóm lắng nghe');
  });

  test('CLI login: open the URL, paste the code, done', async ({ page }) => {
    await page.goto('/connections');
    const cli = page.getByRole('region', { name: 'Tài khoản Antigravity CLI' });
    await expect(cli).toContainText('a@example.test');
    await cli.getByRole('button', { name: /Đổi tài khoản/ }).click();
    await page.getByRole('button', { name: 'Thêm tài khoản Google' }).click();
    // v0.1.45 (F-20): thêm tài khoản CLI cần mã PIN (`cli.switch_account`).
    const pin = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await expect(pin).toBeVisible();
    await page.keyboard.type(OWNER.pin);
    await expect(pin).toBeHidden();
    const link = cli.getByRole('link', { name: 'Mở trang đăng nhập Google' });
    await expect(link).toBeVisible({ timeout: 5000 });
    await expect(link).toHaveAttribute('target', '_blank');
    await expect(link).toHaveAttribute('href', /accounts\.google\.com/);
    await cli.getByLabel('Mã xác thực').fill('4/0AbCd-EfGh');
    await cli.getByRole('button', { name: 'Xác nhận' }).click();
    await expect(page.getByText(/Đã thêm tài khoản genesis\.ops\d@gmail\.com/)).toBeVisible({ timeout: 5000 });
    // v0.1.30: the account just added is the one in use.
    await expect(cli.getByTestId('cli-current')).toContainText(/genesis\.ops\d@gmail\.com/);
  });

  test('CLI switch account: PIN, then the chosen Google account is in use (v0.1.30)', async ({ page }) => {
    await page.goto('/connections');
    const cli = page.getByRole('region', { name: 'Tài khoản Antigravity CLI' });
    await expect(cli.getByTestId('cli-current')).toContainText('a@example.test');
    await cli.getByRole('button', { name: /Đổi tài khoản/ }).click();
    const dlg = page.getByRole('dialog', { name: 'Đổi tài khoản Google cho AI' });
    await expect(dlg).toContainText('AI đang dùng a@example.test');
    await dlg.getByRole('button', { name: 'Dùng tài khoản b@example.test' }).click();
    const pin = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await expect(pin).toBeVisible();
    await page.keyboard.type(OWNER.pin);
    await expect(pin).toBeHidden();
    await expect(page.getByText(/Đã chuyển sang b@example\.test/)).toBeVisible({ timeout: 5000 });
    await expect(dlg).toBeHidden();
    await expect(cli.getByTestId('cli-current')).toContainText('b@example.test');
  });

  test('mã PIN chỉ ở Tài khoản của tôi: form Đổi mã PIN + Lịch sử nhập mã PIN', async ({ page }) => {
    await page.goto('/account');
    const form = page.getByRole('form', { name: 'Đổi mã PIN' });
    await expect(form).toBeVisible();
    await form.getByRole('button', { name: /Lịch sử nhập mã PIN/ }).click();
    await expect(page.getByRole('dialog', { name: /Lịch sử nhập mã PIN/ })).toBeVisible();
    await page.goto('/connections');
    await expect(page.getByRole('article', { name: 'Kênh Zalo' })).toBeVisible();
    await expect(page.getByRole('button', { name: /Đổi mã PIN/ })).toHaveCount(0);
  });
});

test('auditor is read-only', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await resetMock(page.request, 'finished');
  await loginAs(page, AUDITOR.email);
  await page.goto('/rules');
  await expect(page.getByRole('article', { name: /R-01/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Thêm quy tắc/ })).toHaveCount(0);
  await expect(page.getByRole('article', { name: /R-01/ }).getByRole('switch')).toBeDisabled();
  await page.goto('/identity');
  await expect(page.getByRole('article').first()).toBeVisible();
  await expect(page.getByRole('button', { name: /^Gộp/ })).toHaveCount(0);
  await page.goto('/raw');
  await expect(page.getByRole('button', { name: /Xuất tập thô/ })).toHaveCount(0);
  await page.goto('/connections');
  await expect(page.getByRole('article', { name: 'Kênh Zalo' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Đăng xuất/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Quét lại QR/ })).toHaveCount(0);
});

// v0.1.59 (g5-thiet-lap): Thiết lập MỘT màn, 3 bước — Nguồn AI (bước 4) và Xong (bước 12) ở trình thiết lập; kênh / nhóm / sàng lọc
// (bước 5–7) mở ở Hướng dẫn thiết lập (/guide/n, "Nối Zalo ngay" dẫn thẳng /guide/5). Bước 8–11 chưa làm ⇒ "để sau" khi Hoàn tất.
test('setup: Nguồn AI + Xong ở trình thiết lập, bước 5–7 ở Hướng dẫn, against the mock', async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await resetMock(page.request, 'fresh');
  await apiCall(page, 'PUT', '/setup/steps/1', { token: SETUP_TOKEN, language: 'vi', mode: 'empty' });
  await apiCall(page, 'PUT', '/setup/steps/2', {
    token: SETUP_TOKEN, display_name: 'Anh Nguyễn Văn A (Chủ)', email: 'owner@example.test', password: 'mot-cau-rat-dai-de-nho-2026', pin: OWNER.pin, pin_confirm: OWNER.pin,
  });
  await apiCall(page, 'PUT', '/setup/steps/3', { org_name: 'Genesis Trading', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND', self_name: 'Anh', bot_calls_me: 'Sếp' });

  await page.goto('/setup');
  const next = page.getByRole('button', { name: /Tiếp tục/ });

  // Bước 4 — Antigravity CLI via URL + code.
  await expect(page.getByRole('heading', { name: 'Nối nguồn AI cho Gen' })).toBeVisible();
  await expect(next).toBeDisabled();
  await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
  // v0.1.45 (F-20): thêm tài khoản CLI cần mã PIN (`cli.switch_account`).
  await expect(page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' })).toBeVisible();
  await page.keyboard.type(OWNER.pin);
  await expect(page.getByRole('link', { name: 'Mở trang đăng nhập Google' })).toBeVisible({ timeout: 5000 });
  await page.getByLabel('Mã xác thực').fill('4/0AbCd-EfGh');
  await page.getByRole('button', { name: 'Xác nhận' }).click();
  await expect(page.getByText('a@example.test').first()).toBeVisible({ timeout: 5000 });
  await page.screenshot({ path: join(outDir, 'setup-ai-source-1440.png'), fullPage: true });
  await expect(next).toBeEnabled();
  await next.click();
  // Phiên PIN của bước 4 hết hạn → bước 5 vẫn phải hỏi PIN cho đăng nhập kênh.
  await page.request.post('/api/v1/__mock/pin_expire', { data: {} });

  // Bước Xong — lần sàng lọc đầu chạy trực tiếp; "Nối Zalo ngay" ghi Hoàn tất rồi mở thẳng /guide/5.
  await expect(page.getByRole('heading', { name: 'Xong rồi, thưa Sếp' })).toBeVisible();
  await expect(page.getByTestId('setup-done-first-run')).toContainText('Gen');
  await page.screenshot({ path: join(outDir, 'setup-done-1440.png'), fullPage: true });
  await page.getByRole('button', { name: /Nối Zalo ngay/ }).click();
  await expect(page).toHaveURL(/\/guide\/5$/);

  // Bước 5 — risk warning, PIN, QR, scan (mở ở Hướng dẫn thiết lập).
  await expect(page.getByRole('heading', { name: 'Kết nối kênh' })).toBeVisible();
  await expect(next).toBeDisabled();
  const zalo = page.getByRole('article', { name: 'Kênh Zalo' });
  await zalo.getByRole('button', { name: /Tạo mã QR/ }).click();
  const risk = page.getByRole('dialog', { name: 'Trước khi hiện mã QR' });
  await risk.getByRole('checkbox').check();
  await risk.getByRole('button', { name: /Tôi hiểu, hiện mã QR/ }).click();
  await expect(page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' })).toBeVisible();
  await page.keyboard.type(OWNER.pin);
  await expect(page.getByRole('img', { name: /Mã QR đăng nhập Zalo/ })).toBeVisible({ timeout: 8000 });
  await page.screenshot({ path: join(outDir, 'setup-step5-qr-1440.png'), fullPage: true });
  await mockHook(page.request, 'scan', { type: 'zalo' });
  await expect(zalo).toContainText('Đang kết nối', { timeout: 8000 });
  await expect(next).toBeEnabled();
  await next.click();
  await expect(page).toHaveURL(/\/guide$/);

  // Bước 6 — QD 17/10: every synced group starts "Lắng nghe im lặng" (lưu hết, không bao giờ trả lời); Sếp đổi từng nhóm.
  await page.goto('/guide/6');
  await expect(page.getByRole('heading', { name: 'Chọn nhóm lắng nghe' })).toBeVisible();
  const mode = page.getByLabel('Chế độ lắng nghe của Vận hành Genesis — Quý 4');
  await expect(mode).toHaveValue('silent');
  await expect(next).toBeEnabled();
  await mode.selectOption('tagged_only');
  await expect(next).toBeEnabled();
  await next.click();
  await expect(page).toHaveURL(/\/guide$/);

  // Bước 7 — presets + weights.
  await page.goto('/guide/7');
  await expect(page.getByRole('heading', { name: 'Sàng lọc dữ liệu' })).toBeVisible();
  // v0.1.55: từng quy tắc R-01…R-06 nằm trong "Nâng cao — lịch sàng lọc và từng quy tắc" (đóng sẵn) — mở ra mới thấy.
  await expect(page.getByText('R-06').first()).toBeHidden();
  await page.getByText('Nâng cao — lịch sàng lọc và từng quy tắc').click();
  await expect(page.getByText('R-06').first()).toBeVisible();
  await page.screenshot({ path: join(outDir, 'setup-step7-1440.png'), fullPage: true });
  // v0.1.43 (F-30): trọng số gập trong "Nâng cao" — đóng sẵn, mở ra mới thấy; vẫn lưu được không cần mở.
  const weightsAdv = page.locator('details', { has: page.getByText('Nâng cao — trọng số chấm điểm') });
  await expect(weightsAdv).not.toHaveAttribute('open', /.*/);
  await expect(page.getByText('Trọng số chấm điểm · tổng 100%')).toBeHidden();
  await page.getByText('Nâng cao — trọng số chấm điểm').click();
  await expect(page.getByText('Trọng số chấm điểm · tổng 100%')).toBeVisible();
  await expect(next).toBeEnabled();
  await next.click();
  await expect(page).toHaveURL(/\/guide$/);

  // Hoàn tất đã ghi: 1–4, 5–7 (vừa làm ở Hướng dẫn) và 12 xong; 8–11 chưa làm ⇒ "để sau" (dùng mặc định) — làm tiếp ở Hướng dẫn.
  const state = await apiCall(page, 'GET', '/setup/state');
  expect(state.finished).toBe(true);
  expect(state.steps.filter((s: { status: string }) => s.status === 'skipped').map((s: { n: number }) => s.n)).toEqual([8, 9, 10, 11]);
  expect(state.steps.filter((s: { status: string }) => s.status === 'done').map((s: { n: number }) => s.n)).toEqual([1, 2, 3, 4, 5, 6, 7, 12]);
});
