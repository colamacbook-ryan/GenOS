import { expect, test, type Browser, type Page, type Response } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import type { NavDomain } from '@gen-harness/contracts';
import { firstScreenKey } from '../src/shell/navModel';

/**
 * v0.1.35 (F-14) — e2e THẬT rút gọn chạy trong job `api` của CI (`LIVE_SPECS=live-ci bash e2e-live/run.sh`):
 * api + worker thật trên Postgres/Redis thật, bridge giả, model giả (fake_llm.py). Mục tiêu: lỗi kiểu "mock xanh, máy
 * thật hỏng" (vd ô chọn người dùng id viết cứng 'u-lan' → API thật trả 422) bị CI chặn.
 *
 * Tự đủ — KHÔNG phụ thuộc live-phase2: thiết lập nhanh bằng API (bước 1–4, kênh Zalo, nhóm g-si, sàng lọc một tin
 * hỏi giá, một người dùng thứ hai + một trợ lý), rồi 4 luồng giao/gán qua GIAO DIỆN thật:
 *   (a) Hộp thư → 'Giao cho người khác'   (b) Vụ việc → 'Gán người xử lý'
 *   (c) Nhóm → 'Gán BOT trực nhóm' → 'Lưu' (d) Gen đề xuất 'Giao người phụ trách' → Xác nhận.
 * v0.1.41 (F-84): (e) "nối model" — thêm nhà cung cấp từ mẫu OpenRouter (đổi endpoint sang fake_llm giao thức OpenAI)
 *   → Kiểm tra kết nối OK → thấy trong chuỗi chuyển hướng ở Bộ não AI.
 * v0.1.46 (F-21): (f) Mời người — Owner mời qua giao diện → nhân viên (context trình duyệt MỚI) đăng nhập bằng mật khẩu tạm
 *   → đổi mật khẩu → vào màn đầu của vai trò; đăng nhập lại bằng mật khẩu mới được, mật khẩu tạm bị từ chối.
 * Người dùng thứ hai là dữ liệu kiểm thử nội bộ trên CSDL gh_live (bị xoá mỗi lần chạy) — không phải tài khoản
 * trên dịch vụ ngoài.
 */
const OUT = process.env.LIVE_OUT ?? '../test-results/live-shots';
const PIN = '246810';
const TOKEN = process.env.GH_SETUP_TOKEN ?? 'live-setup-token';
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const STAFF = { name: 'Lê Văn Hải', email: 'hai.vanhanh@genesis.vn' };
const GROUP = 'Chợ thép sỉ miền Nam';
const ASK = 'Cần 3 container thép cuộn, báo giá giúp chị';

let page: Page;
const ids = { owner: '', staff: '', agent: '', agentName: '', group: '', lan: '', item: '' };

async function csrf(p: Page) {
  const c = (await p.context().cookies()).find((x) => x.name === 'gh_csrf');
  return c?.value ?? '';
}
async function call(method: string, path: string, data?: unknown) {
  const res = await page.request.fetch(`/api/v1${path}`, { method, data, headers: { 'X-CSRF-Token': await csrf(page) } });
  if (!res.ok()) throw new Error(`${method} ${path} → ${res.status()} ${await res.text()}`);
  return res.status() === 204 ? null : res.json();
}
const shot = (name: string) => page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
function inbound(...msgs: Array<{ group: string; sender: string; name: string; text: string }>) {
  execFileSync(process.env.PY ?? 'python3', [process.env.SEND_SCRIPT!, JSON.stringify(msgs)], { stdio: 'inherit' });
}
/** Người đang được giao mục Hộp thư (core.assignments subject_type='queue' còn hiệu lực) — đọc thẳng CSDL thử
 * gh_live vì API Hộp thư không trả người được giao; Action Log (`queue.assigned`) được kiểm song song qua /audit. */
function activeAssignee(itemId: string): string {
  expect(itemId).toMatch(UUID_RE);
  return execFileSync('psql', [process.env.LIVE_DB_URL!, '-tAc',
    `SELECT user_id FROM core.assignments WHERE subject_type = 'queue' AND subject_id = '${itemId}' AND active_to IS NULL`],
  { encoding: 'utf8' }).trim();
}
async function lastAssignedTo(itemId: string) {
  const log = (await call('GET', `/audit?action=queue.assigned&target_id=${itemId}&limit=1`)) as { items: Array<{ detail: { to?: string } }> };
  return log.items[0]?.detail?.to;
}
/** Helper KHOAN DUNG (hợp đồng giữa các gói v0.1.35): hộp PIN có thì gõ, không có (phiên PIN còn hạn) thì bỏ qua —
 * xanh cả trước lẫn sau gói f20 (PIN cho thao tác nhà cung cấp AI). */
async function maybeEnterOwnerPin(p: Page) {
  const dlg = p.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
  try {
    await dlg.waitFor({ state: 'visible', timeout: 4000 });
  } catch {
    return;
  }
  await p.keyboard.type(PIN);
  await expect(dlg).toBeHidden();
}
/** Đợi response ghi THẬT (bỏ qua lượt 423 PIN_REQUIRED đầu nếu có — mutation tự gọi lại sau khi nhập PIN). */
function writeResponse(p: Page, method: string, re: RegExp) {
  return p.waitForResponse((r) => r.request().method() === method && re.test(new URL(r.url()).pathname) && r.status() !== 423);
}
async function newPage(browser: Browser) {
  return browser.newPage({
    baseURL: process.env.LIVE_BASE_URL ?? 'http://localhost:5175',
    locale: 'vi-VN',
    timezoneId: 'Asia/Ho_Chi_Minh',
    colorScheme: 'dark',
    viewport: { width: 1440, height: 900 },
  });
}

test.describe.serial('CI — e2e thật rút gọn: giao/gán người & trợ lý thật, Gen đề xuất', () => {
  test.beforeAll(async ({ browser }) => {
    page = await newPage(browser);
  });
  test.afterAll(async () => {
    await page?.close();
  });

  test('thiết lập nhanh bằng API: bước 1–4, kênh Zalo, nhóm g-si, sàng lọc, người dùng thứ hai, trợ lý', async () => {
    test.setTimeout(180_000);
    await call('PUT', '/setup/steps/1', { token: TOKEN, language: 'vi', mode: 'empty' });
    await call('PUT', '/setup/steps/2', { token: TOKEN, display_name: 'Anh Cơ', email: 'owner@genesis.vn',
      password: 'mot-cau-rat-dai-de-nho-2026', pin: PIN, pin_confirm: PIN });
    await call('PUT', '/setup/steps/3', { org_name: 'Genesis Trading', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND',
      self_name: 'Anh', bot_calls_me: 'Sếp' });
    // Một phiên PIN cho mọi thao tác nhạy cảm phía dưới (nhà cung cấp AI, đăng nhập kênh, người dùng, agent).
    await call('POST', '/auth/pin/verify', { pin: PIN });

    // Bước 4: model giả tương thích OpenAI (fake_llm.py) — khoá test cố định, không phải bí mật.
    const prov = (await call('POST', '/providers', { kind: 'openai_compat', name: 'Model nội bộ',
      endpoint: 'http://127.0.0.1:9911/v1', keys: ['sk-live-test-9911'] })) as { id: string };
    const probe = (await call('POST', `/providers/${prov.id}/test`, {})) as { ok?: boolean };
    expect(probe.ok, 'gọi thử model giả phải OK').toBeTruthy();
    await call('PUT', '/setup/steps/4', { provider_ids: [prov.id] });

    // Kênh Zalo: bridge giả trả QR → "quét" sau SCAN_AFTER giây → active + gửi danh bạ nhóm.
    await call('POST', '/channels/zalo/login', { accept_risk: true });
    await expect.poll(async () => {
      const chans = (await call('GET', '/channels')) as Array<{ type: string; state: string }>;
      return chans.find((c) => c.type === 'zalo')?.state;
    }, { timeout: 30_000, message: 'chờ kênh Zalo hoạt động' }).toBe('active');
    await expect.poll(async () => {
      const groups = (await call('GET', '/channels/zalo/groups')) as Array<{ id: string; name: string }>;
      ids.group = groups.find((g) => g.name === GROUP)?.id ?? '';
      return ids.group;
    }, { timeout: 20_000, message: `chờ danh bạ có nhóm ${GROUP}` }).toMatch(UUID_RE);
    await call('PATCH', `/groups/${ids.group}`, { listen_mode: 'silent' });

    // Bước 7 với bộ quy tắc mặc định, rồi bơm tin hỏi giá → sàng lọc → mục Hộp thư.
    const presets = (await call('GET', '/setup/rule-presets')) as Array<{ code: string; enabled: boolean }>;
    await call('PUT', '/setup/steps/7', { interval_seconds: 900, count_threshold: 500, min_confidence: 0.6,
      rule_codes: presets.filter((r) => r.enabled).map((r) => r.code) });
    inbound({ group: 'g-si', sender: 'u-lan', name: 'Nguyễn Thị Lan', text: ASK });
    await expect.poll(async () => {
      const raw = (await call('GET', '/channels')) as Array<{ type: string; stats: { msgs_24h: number | null } }>;
      return raw.find((c) => c.type === 'zalo')?.stats.msgs_24h ?? 0;
    }, { timeout: 20_000, message: 'chờ tin hỏi giá vào Kho thô' }).toBeGreaterThan(0);
    await call('POST', '/refinery/run', {});
    await expect.poll(async () => {
      const inbox = (await call('GET', '/inbox')) as { items: Array<{ id: string }> };
      return inbox.items.length;
    }, { timeout: 60_000, message: 'chờ mục Hộp thư sau sàng lọc' }).toBeGreaterThan(0);
    const people = (await call('GET', '/directory/people')) as { items: Array<{ id: string; name: string }> };
    ids.lan = people.items.find((p) => p.name === 'Nguyễn Thị Lan')?.id ?? '';
    expect(ids.lan, 'người Nguyễn Thị Lan phải có trong kho sạch').toMatch(UUID_RE);

    // Người dùng thứ hai THẬT (vai trò operator) + một trợ lý từ mẫu 'commercial'.
    const made = (await call('POST', '/users', { display_name: STAFF.name, email: STAFF.email, role: 'operator' })) as { user: { id: string } };
    expect(made.user.id).toMatch(UUID_RE);
    const tpl = ((await call('GET', '/agents/templates')) as Array<Record<string, unknown> & { code: string }>).find((t) => t.code === 'commercial')!;
    const agent = (await call('POST', '/agents', { name: tpl.name, role_desc: tpl.role_desc, voice: tpl.voice,
      speak_when: tpl.speak_when, forbidden: tpl.forbidden, template: 'commercial' })) as { id: string; name: string };
    ids.agent = agent.id;
    ids.agentName = agent.name;
    expect(ids.agent).toMatch(UUID_RE);

    // Id THẬT lấy từ API chọn người (hợp đồng F-1) — không đoán, không viết cứng.
    const picks = (await call('GET', '/pickers/users')) as { items: Array<{ id: string; name: string; me: boolean }> };
    ids.owner = picks.items.find((u) => u.me)?.id ?? '';
    ids.staff = picks.items.find((u) => u.name === STAFF.name)?.id ?? '';
    expect(ids.owner).toMatch(UUID_RE);
    expect(ids.staff).toBe(made.user.id);
    const agentPicks = (await call('GET', '/pickers/agents')) as { items: Array<{ id: string; name: string }> };
    expect(agentPicks.items.map((a) => a.id)).toContain(ids.agent);
  });

  test('(a) Hộp thư → Giao cho người khác → người dùng thật', async () => {
    await page.goto('/inbox');
    const card = page.locator('.ib-card').first();
    await expect(card).toBeVisible({ timeout: 20_000 });
    await card.getByRole('button', { name: 'Giao cho người khác' }).click();
    const dlg = page.getByRole('dialog', { name: 'Giao cho người khác' });
    await expect(dlg).toBeVisible();
    const resP = writeResponse(page, 'POST', /\/api\/v1\/inbox\/[^/]+\/assign$/);
    await dlg.getByRole('button', { name: STAFF.name, exact: true }).click();
    const res: Response = await resP;
    expect(res.status(), await res.text()).toBe(200);
    const sent = res.request().postDataJSON() as { user_id: string };
    expect(sent.user_id).toMatch(UUID_RE);
    expect(sent.user_id).toBe(ids.staff);
    expect(((await res.json()) as { assigned_to: { id: string } }).assigned_to.id).toBe(ids.staff);
    ids.item = new URL(res.url()).pathname.split('/').at(-2)!;
    await expect(dlg).toBeHidden();
    // Đọc lại: Action Log qua API + bản ghi giao việc còn hiệu lực trong CSDL thử.
    expect(await lastAssignedTo(ids.item)).toBe(ids.staff);
    expect(activeAssignee(ids.item)).toBe(ids.staff);
    await shot('ci-a-inbox-assign');
  });

  test('(b) Deal & Vụ việc › Vụ việc → Gán người xử lý → người dùng thật', async () => {
    const c = (await call('POST', '/cases', { title: 'Khiếu nại giao thiếu thép cuộn (e2e CI)', priority: 'P2',
      subject: { type: 'person', id: ids.lan } })) as { id: string; code: string };
    await page.goto('/deals');
    await page.getByRole('tab', { name: 'Vụ việc' }).click();
    const row = page.getByRole('row').filter({ hasText: c.code });
    await expect(row).toBeVisible({ timeout: 20_000 });
    await row.getByRole('button', { name: 'Đổi' }).click();
    const dlg = page.getByRole('dialog', { name: 'Gán người xử lý' });
    await expect(dlg).toBeVisible();
    const resP = writeResponse(page, 'PATCH', new RegExp(`/api/v1/cases/${c.id}$`));
    await dlg.getByRole('button', { name: STAFF.name, exact: true }).click();
    const res = await resP;
    expect(res.status(), await res.text()).toBe(200);
    expect((res.request().postDataJSON() as { assignee_user_id: string }).assignee_user_id).toBe(ids.staff);
    const back = (await call('GET', `/cases/${c.id}`)) as { assignee: { id: string } | null };
    expect(back.assignee?.id).toBe(ids.staff);
    await expect(row).toContainText(STAFF.name);
    await shot('ci-b-case-assign');
  });

  test('(c) Nhóm & Con người › Nhóm → Gán BOT trực nhóm → trợ lý thật → Lưu', async () => {
    await page.goto('/directory');
    await page.getByRole('tab', { name: /Nhóm/ }).click();
    const row = page.getByRole('row').filter({ hasText: GROUP });
    await expect(row).toBeVisible({ timeout: 20_000 });
    await row.getByRole('button', { name: 'Đổi' }).click();
    const dlg = page.getByRole('dialog', { name: 'Gán BOT trực nhóm' });
    await expect(dlg).toBeVisible();
    await dlg.getByRole('button', { name: ids.agentName, exact: true }).click();
    const resP = writeResponse(page, 'POST', new RegExp(`/api/v1/directory/groups/${ids.group}/bot$`));
    await dlg.getByRole('button', { name: 'Lưu' }).click();
    const res = await resP;
    expect(res.status(), await res.text()).toBe(200);
    expect((res.request().postDataJSON() as { agent_id: string }).agent_id).toBe(ids.agent);
    const groups = (await call('GET', '/directory/groups')) as { items: Array<{ id: string; bot: { id: string } | null }> };
    expect(groups.items.find((g) => g.id === ids.group)?.bot?.id).toBe(ids.agent);
    await expect(row).toContainText(ids.agentName);
    await shot('ci-c-group-bot');
  });

  test('(d) Gen đề xuất Giao người phụ trách → Xác nhận → giao thật', async () => {
    // Trả mục về "Tôi" trước, để việc Gen giao lại cho người dùng thứ hai là thay đổi THẬT, không phải trạng thái cũ.
    await call('POST', `/inbox/${ids.item}/assign`, { user_id: ids.owner });
    expect(activeAssignee(ids.item)).toBe(ids.owner);

    await page.goto('/inbox');
    const panel = page.getByRole('complementary', { name: /Gen — trợ lý quản trị/ });
    if (!(await panel.isVisible())) await page.getByRole('button', { name: /Hỏi Gen/ }).click();
    await expect(panel).toBeVisible();
    await panel.getByLabel('Câu hỏi cho Gen').fill(`Giao việc hỏi giá thép cho ${STAFF.name}`);
    await panel.getByRole('button', { name: 'Gửi' }).click();
    const card = panel.getByRole('group', { name: 'Đề xuất: Giao người phụ trách' });
    await expect(card).toBeVisible({ timeout: 45_000 });
    await expect(card).toContainText(STAFF.name);
    const resP = writeResponse(page, 'POST', /\/api\/v1\/gen\/proposals\/[^/]+\/confirm$/);
    await card.getByRole('button', { name: 'Xác nhận' }).click();
    await maybeEnterOwnerPin(page);
    const res = await resP;
    expect(res.status(), await res.text()).toBe(200);
    const body = (await res.json()) as { result?: { id?: string; type?: string } };
    const itemId = body.result?.id ?? '';
    expect(itemId).toMatch(UUID_RE);
    expect(activeAssignee(itemId)).toBe(ids.staff);
    expect(await lastAssignedTo(itemId)).toBe(ids.staff);
    await shot('ci-d-gen-assign');
  });

  test('(e) Nối model từ mẫu OpenRouter → gọi thử → thấy trong chuỗi', async () => {
    // `/api` là tiền tố proxy của vite dev (→ api thật) nên không tải thẳng được: vào app rồi chuyển màn phía trình duyệt.
    await page.goto('/overview');
    await page.evaluate(() => {
      window.history.pushState({}, '', '/api');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    await expect(page).toHaveURL(/\/api$/);
    await page.getByRole('button', { name: /Thêm nhà cung cấp/ }).first().click();
    const dlg = page.getByRole('dialog', { name: 'Thêm nhà cung cấp' });
    await expect(dlg).toBeVisible();
    await dlg.getByLabel('Loại').selectOption('openrouter');
    const endpoint = dlg.getByLabel('Địa chỉ gọi (Endpoint)');
    await expect(endpoint).toHaveValue('https://openrouter.ai/api/v1');
    await expect(dlg.getByLabel('Tên hiển thị')).toHaveValue('OpenRouter');
    // Máy CI không ra Internet: trỏ mẫu về fake_llm.py (giao thức OpenAI) — khoá test giả, không phải bí mật.
    await endpoint.fill('http://127.0.0.1:9911/v1');
    await dlg.getByLabel('Khoá API (mỗi dòng một khoá)').fill('sk-live-or-9911');
    await dlg.getByLabel(/Model ban đầu/).fill('fake-flash');
    const createdP = writeResponse(page, 'POST', /\/api\/v1\/providers$/);
    await dlg.getByRole('button', { name: 'Thêm', exact: true }).click();
    await maybeEnterOwnerPin(page);
    const created = await createdP;
    expect(created.status(), await created.text()).toBe(201);
    const sent = created.request().postDataJSON() as { kind: string; name: string; endpoint: string; models: string[] };
    expect({ kind: sent.kind, name: sent.name, endpoint: sent.endpoint, models: sent.models }).toEqual({
      kind: 'openai_compat', name: 'OpenRouter', endpoint: 'http://127.0.0.1:9911/v1', models: ['fake-flash'],
    });
    const provId = ((await created.json()) as { id: string }).id;
    expect(provId).toMatch(UUID_RE);
    await expect(dlg).toBeHidden();

    // Gọi thử thật (api liệt kê model qua fake_llm) ⇒ "Kết nối được".
    const card = page.getByRole('article', { name: 'OpenRouter' });
    await expect(card).toBeVisible({ timeout: 20_000 });
    const testP = writeResponse(page, 'POST', new RegExp(`/api/v1/providers/${provId}/test$`));
    await card.getByRole('button', { name: 'Kiểm tra kết nối' }).click();
    const tested = await testP;
    expect(tested.status(), await tested.text()).toBe(200);
    expect(((await tested.json()) as { ok: boolean }).ok, 'gọi thử nguồn OpenRouter (fake_llm) phải OK').toBe(true);
    await expect(card.locator('.apm-test-result--ok')).toContainText('Kết nối được');

    // Bộ não AI › Chuỗi chuyển hướng có OpenRouter.
    await page.goto('/system?tab=brain');
    const chain = page.getByRole('region', { name: 'Chuỗi chuyển hướng' });
    await expect(chain.locator('.brain-chain-row__name', { hasText: 'OpenRouter' })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText('[object Object]')).toHaveCount(0);
    await shot('ci-e-openrouter-chain');
  });

  test('(f) Mời người: Owner mời → nhân viên đăng nhập mật khẩu tạm → đổi mật khẩu → màn đầu của vai trò', async ({ browser }) => {
    test.setTimeout(120_000);
    // Email ngẫu nhiên theo lượt — CSDL gh_live bị xoá mỗi lần chạy, nhưng chạy lại cục bộ không được đụng lượt trước.
    const email = `moi.${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}@genesis.vn`;
    const newPassword = `mat-khau-moi-${Date.now().toString(36)}-2026`;

    await page.goto('/team');
    await page.getByRole('button', { name: /Mời người dùng/ }).first().click();
    const form = page.getByRole('dialog', { name: 'Mời người dùng' });
    await expect(form).toBeVisible();
    await form.getByLabel('Tên hiển thị').fill('Phạm Thị Mời');
    await form.getByLabel('Email đăng nhập').fill(email);
    await form.getByLabel('Vai trò').selectOption('operator');
    const invitedP = writeResponse(page, 'POST', /\/api\/v1\/users$/);
    await form.getByRole('button', { name: 'Mời', exact: true }).click();
    await maybeEnterOwnerPin(page);
    const invited = await invitedP;
    expect(invited.status(), await invited.text()).toBe(201);

    // Live không đặt GH_PUBLIC_URL ⇒ địa chỉ đăng nhập là localhost ⇒ hộp mời phải cảnh báo đỏ.
    const box = page.getByRole('dialog', { name: /Đã mời Phạm Thị Mời/ });
    await expect(box).toBeVisible();
    const warn = box.getByTestId('invite-local-warning');
    await expect(warn).toBeVisible();
    await expect(warn).toHaveAttribute('role', 'alert');
    await expect(warn).toContainText('Địa chỉ này chỉ mở được trên chính máy chủ');
    const tempPassword = (await box.locator("[aria-label='Mật khẩu tạm']").innerText()).trim();
    expect(tempPassword.length).toBeGreaterThan(8);
    await box.getByRole('button', { name: 'Đã gửi, đóng' }).click();
    await expect(box).toBeHidden();

    // Nhân viên: context MỚI (không chung cookie với Owner).
    const ctx = await browser.newContext({
      baseURL: process.env.LIVE_BASE_URL ?? 'http://localhost:5175',
      locale: 'vi-VN', timezoneId: 'Asia/Ho_Chi_Minh', colorScheme: 'dark', viewport: { width: 1440, height: 900 },
    });
    try {
      const sp = await ctx.newPage();
      await sp.goto('/login');
      await sp.getByLabel('Email').fill(email);
      await sp.getByLabel('Mật khẩu', { exact: true }).fill(tempPassword);
      await sp.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
      await expect(sp).toHaveURL(/\/change-password$/);
      await expect(sp.getByRole('heading', { name: 'Đặt mật khẩu mới' })).toBeVisible();
      await sp.getByLabel('Mật khẩu tạm hiện tại').fill(tempPassword);
      await sp.getByLabel('Mật khẩu mới', { exact: true }).fill(newPassword);
      await sp.getByLabel('Nhập lại mật khẩu mới').fill(newPassword);
      const changeP = writeResponse(sp, 'POST', /\/api\/v1\/account\/password$/);
      await sp.getByRole('button', { name: 'Lưu mật khẩu mới' }).click();
      const changed = await changeP;
      expect(changed.status(), await changed.text()).toBeLessThan(300);

      // Màn đầu: kỳ vọng lấy từ ĐÚNG API mà HomeRedirect dùng (GET /navigation) trong context nhân viên.
      const navRes = await sp.request.get('/api/v1/navigation');
      expect(navRes.ok(), await navRes.text()).toBe(true);
      const key = firstScreenKey((await navRes.json()) as NavDomain[]);
      expect(key, 'vai trò Vận hành phải có ít nhất một màn').toBeTruthy();
      await expect.poll(() => new URL(sp.url()).pathname, { timeout: 20_000 }).toBe(`/${key}`);
      const path = new URL(sp.url()).pathname;
      expect(path).not.toMatch(/^\/(login|account|change-password)/);
      await expect(sp.getByText('[object Object]')).toHaveCount(0);
      await sp.screenshot({ path: `${OUT}/ci-f-invited-first-screen.png`, fullPage: true });

      // Đăng xuất → mật khẩu mới vào được, mật khẩu tạm bị từ chối (401).
      const csrfOf = async () => (await ctx.cookies()).find((c) => c.name === 'gh_csrf')?.value ?? '';
      const out = await sp.request.post('/api/v1/auth/logout', { headers: { 'X-CSRF-Token': await csrfOf() } });
      expect(out.status()).toBeLessThan(300);
      const bad = await sp.request.post('/api/v1/auth/login', { data: { email, password: tempPassword }, headers: { 'X-CSRF-Token': await csrfOf() } });
      expect(bad.status(), 'mật khẩu tạm phải hết dùng được').toBe(401);
      const good = await sp.request.post('/api/v1/auth/login', { data: { email, password: newPassword }, headers: { 'X-CSRF-Token': await csrfOf() } });
      expect(good.status(), await good.text()).toBe(200);
      expect(((await good.json()) as { must_change_password?: boolean }).must_change_password).toBeFalsy();
    } finally {
      await ctx.close();
    }
  });
});
