import { expect, test, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';

/**
 * Giai đoạn 5.3 — luồng 4–8 của docs/handoff/07-acceptance.md, TIẾP NỐI live-phase2.spec.ts trên CÙNG một
 * phiên api/worker/CSDL thật (`run.sh` chạy `live-phase2 live-phase3` trong một lệnh `playwright test`,
 * `workers: 1` nên hai tệp chạy tuần tự — không dựng lại thiết lập từ đầu). Owner/PIN, tổ chức, kênh Zalo,
 * nhóm "Chợ thép sỉ miền Nam" (g-si, nghe "tagged_only" — nhóm "silent" không bao giờ trả lời) và các tin nhắn ở
 * live-phase2 đã có sẵn khi test này chạy.
 */
const OUT = process.env.LIVE_OUT ?? '../test-results/live-shots';
const PIN = '246810';
const OWNER = { email: 'owner@genesis.vn', password: 'mot-cau-rat-dai-de-nho-2026' };

async function csrf(page: Page) {
  const c = (await page.context().cookies()).find((x) => x.name === 'gh_csrf');
  return c?.value ?? '';
}
async function call(page: Page, method: string, path: string, data?: unknown) {
  const res = await page.request.fetch(`/api/v1${path}`, { method, data, headers: { 'X-CSRF-Token': await csrf(page) } });
  if (!res.ok()) throw new Error(`${method} ${path} → ${res.status()} ${await res.text()}`);
  return res.status() === 204 ? null : res.json();
}
const shot = (page: Page, name: string) => page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
/** Tin nhắn "từ Zalo" đi qua bridge giả → stream inbound → ingest thật (giống live-phase2, cộng `phone` cho
 * luồng 6 — hợp nhất danh tính — và `channel`/`mention` như send.py hiện hỗ trợ). */
function inbound(...msgs: Array<{ group: string; sender: string; name: string; text: string; mention?: boolean; phone?: string }>) {
  execFileSync(process.env.PY ?? 'python3', [process.env.SEND_SCRIPT!, JSON.stringify(msgs)], { stdio: 'inherit' });
}
/** Chạy một script Python phụ trợ của thư mục này (khoá bí mật, dò danh tính, bơm lỗi plugin…), trả stdout. */
function runPy(script: string, args: string[] = []): string {
  return execFileSync(process.env.PY ?? 'python3', [script, ...args], { encoding: 'utf8' }).trim();
}
/** Phiên PIN có hiệu lực 30 phút (GH_PIN_SESSION_MINUTES) sau lần xác nhận ĐẦU TIÊN trong test — mọi thao tác
 * cần PIN sau đó trong CÙNG một test này không hiện lại hộp thoại nữa (đúng hành vi thật, không phải giả).
 * Vì vậy chỉ đợi/điền PIN NẾU hộp thoại thật sự xuất hiện. */
async function maybeEnterOwnerPin(page: Page) {
  const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
  try {
    await dlg.waitFor({ state: 'visible', timeout: 4000 });
  } catch {
    return;
  }
  await page.keyboard.type(PIN);
  await expect(dlg).toBeHidden();
}

test('luồng 4–8: cơ hội thật, agent soạn & Sếp duyệt & gửi thật, hợp nhất danh tính, plugin lỗi liên tục, MCP', async ({ page }) => {
  test.setTimeout(240_000);

  // Owner đã có tài khoản từ bước 2 của live-phase2 — mỗi test Playwright có context/cookie riêng nên đăng
  // nhập lại thật qua /auth/login (không phải mock).
  await call(page, 'POST', '/auth/login', OWNER);

  // Bước 8–9 của trình thiết lập ("Agent đầu tiên", "Tự trị & ranh giới") vẫn là ComingSoonStep phía web —
  // "Tiếp tục" chỉ điều hướng ở client, KHÔNG gọi PUT /setup/steps/8|9 (xem apps/web/src/setup/
  // ComingSoonStep.tsx: "được dựng ở giai đoạn sau"), nên live-phase2 không tạo ra agent nào dù đã bấm qua tới
  // "Hoàn tất". Đây là khoảng trống của bản thân trình thiết lập (ngoài phạm vi 5.3), không sửa ở đây — tạo
  // agent qua đúng màn Console THẬT đã có (Danh tính Agent → /agents, cùng đường mà spec §2 "agent đầu tiên"
  // rồi cũng dùng lại), chọn một mẫu có sẵn và gán thẳng phạm vi kênh Zalo (bao mọi nhóm, gồm cả g-si).
  await page.goto('/agents');
  await page.locator('.ag-template-row').first().getByRole('button', { name: 'Dùng mẫu' }).click();
  const agentDlg = page.getByRole('dialog', { name: 'Tạo agent mới' });
  await agentDlg.getByLabel('Mức tự trị').selectOption('4');
  await agentDlg.getByLabel('Zalo').check();
  await agentDlg.getByRole('button', { name: 'Tạo agent' }).click();
  // Lượt gọi ĐẦU TIÊN của POST /agents luôn 423 (chưa có phiên PIN) — hộp thoại PIN thật hiện ra ở đây, sau khi
  // điền thì mutation tự gọi lại và mới thành công; vì vậy lấy agent vừa tạo qua GET /agents sau khi hộp thoại
  // đã đóng, không bắt trực tiếp response của POST (dễ vồ nhầm lượt 423 đầu).
  await maybeEnterOwnerPin(page); // thao tác PIN ĐẦU TIÊN của test — hộp thoại thật sự hiện ra ở đây
  await expect(agentDlg).toBeHidden();
  const agents = (await call(page, 'GET', '/agents')) as Array<{ id: string; name: string }>;
  expect(agents.length, 'chưa thấy agent vừa tạo qua /agents').toBeGreaterThan(0);
  const agent = agents[0];
  const agentKey = `agent:${agent.id}`;

  // ── Luồng 4: tin hỏi giá thật (đã gửi ở live-phase2, "Cần 3 container thép cuộn") → cơ hội + tín hiệu cầu
  // thật qua refinery + hook `market_signal_capture` thật (gh.biz.market.jobs) → hiện ở Hàng đợi, Bảng cơ hội.
  // Tin chào bán thật của live-phase2 ("...còn tồn 20 tấn...") độ tin cố tình dưới `min_confidence` của bước 7
  // (kiểm bộ lọc — xem fake_llm.py) nên KHÔNG vào kho sạch; gửi thêm một tin cung độ tin đủ ngưỡng ở đây để có
  // tín hiệu cung thật cho Cung ↔ Cầu, rồi ép chấm lại ghép ngay thay vì đợi lịch (`JOBS` chạy phút :11,:26,:41,
  // :56) để không phụ thuộc thời điểm chạy CI.
  inbound({ group: 'g-si', sender: 'u-tung', name: 'Trần Văn Tùng',
    text: 'Bên em còn hàng thép cuộn, số lượng lớn, giao nhanh, liên hệ ngay' });
  await call(page, 'POST', '/refinery/run', {});
  // `/refinery/run` là 202 (chạy nền) — đợi tín hiệu cung THẬT xuất hiện trước khi ép chấm lại ghép, tránh đua
  // với worker (matches/recompute chạy trước khi refinery ghi xong sẽ bỏ lỡ tín hiệu vừa gửi).
  await expect.poll(async () => {
    const r = (await call(page, 'GET', '/supply?side=supply')) as { items: Array<{ item: string }> };
    return r.items.some((s) => s.item === 'thép cuộn');
  }, { timeout: 30_000, message: 'chờ tín hiệu cung "thép cuộn" vào kho sạch' }).toBe(true);
  await call(page, 'POST', '/matches/recompute', {});

  await page.goto('/inbox?tab=opportunity');
  await expect(page.locator('.ib-card', { hasText: 'Hỏi giá: Cần 3 container thép cuộn' })).toBeVisible({ timeout: 20_000 });
  await shot(page, '12-inbox-opportunity');

  await page.goto('/opportunity');
  const oppCard = page.locator('.opp-card', { hasText: 'Nguyễn Thị Lan' });
  await expect(oppCard).toBeVisible();
  await shot(page, '13-opportunity-board');

  await page.goto('/supply');
  // Chị Lan hỏi giá hai lần ở live-phase2 ("Cần 3 container…" và "Giá 18 triệu/tấn…") → hai tín hiệu cầu
  // "thép" riêng, cùng ghép được với tín hiệu cung "thép cuộn" vừa gửi ở trên → 2 dòng khớp, lấy dòng đầu.
  const matchRow = page.locator('.sup-match', { hasText: 'Nguyễn Thị Lan' }).first();
  await expect(matchRow).toBeVisible({ timeout: 20_000 });
  await expect(matchRow.getByText(/khớp \d+/)).toBeVisible();
  await shot(page, '14-supply-match');

  // ── Luồng 5: agent vừa tạo đã có phạm vi kênh Zalo (bao cả nhóm g-si) — ghi một mục sổ tay thật cho Chị Lan,
  // rồi tag agent bằng tin thật → agent đọc sổ tay + dữ liệu sạch thật (fake_llm.py trả "draft" chỉ khi có tag)
  // → vào Bàn làm việc → Sếp duyệt qua UI thật → gửi thật qua fake_bridge.py → Action Log đúng.

  const opportunities = (await call(page, 'GET', '/opportunities')) as { items: Array<{ person?: { id: string; name: string } }> };
  const lanOpp = opportunities.items.find((o) => o.person?.name === 'Nguyễn Thị Lan');
  expect(lanOpp?.person, 'không thấy cơ hội của Nguyễn Thị Lan').toBeTruthy();
  const personId = lanOpp!.person!.id;
  await call(page, 'POST', `/notebook/person/${personId}/entries`, {
    section: 'preferences', body: 'Chị Lan thích được báo giá nhanh, ưu tiên trả lời trong ngày.',
  });

  inbound({ group: 'g-si', sender: 'u-lan', name: 'Nguyễn Thị Lan',
    text: 'Anh ơi, giá container thép đợt trước còn hiệu lực không ạ, anh trả lời giúp em với', mention: true });
  await call(page, 'POST', '/refinery/run', {});

  await page.goto('/workbench');
  const draftRow = page.locator('.wb-list__row', { hasText: 'Nguyễn Thị Lan' });
  await expect(draftRow).toBeVisible({ timeout: 45_000 });
  await draftRow.click();
  const detail = page.locator('.wb-detail');
  await expect(detail.locator('.wb-sources__chips')).toContainText('Sổ tay người', { timeout: 10_000 });
  await expect(detail.locator('.wb-sources__chips')).toContainText('Hồ sơ Nguyễn Thị Lan');
  await shot(page, '15-workbench-draft');

  await detail.getByRole('button', { name: /Duyệt và gửi/ }).click();
  await maybeEnterOwnerPin(page);
  await expect(detail.locator('.wb-footer__decided')).toContainText('sent', { timeout: 20_000 });
  await shot(page, '16-workbench-sent');

  await page.goto('/system?tab=log');
  await page.getByLabel('Tìm trong nhật ký').fill('Nguyễn Thị Lan');
  await expect(page.getByText('draft.sent')).toBeVisible({ timeout: 10_000 });
  await page.getByLabel('Tìm trong nhật ký').fill('');
  await shot(page, '17-action-log-sent');

  // ── Luồng 6: hai tài khoản cùng số điện thoại → identity.detect() thật (worker job, ép chạy ngay qua
  // detect_identities.py) → gợi ý hợp nhất → Sếp gộp qua UI thật (PIN) → Action Log ghi identity.merged, lịch
  // sử gộp/tách thấy được (hồ sơ sống gộp lịch sử + sổ tay do gh.identity.service.merge chuyển toàn bộ tài
  // khoản kênh, thành viên nhóm, đơn vị ý nghĩa và sổ tay sang hồ sơ giữ lại).
  inbound(
    { group: 'g-si', sender: 'u-lan-zalo2', name: 'Lan (Zalo cá nhân)', phone: '0909123456', text: 'Chào cả nhà, em mới vào nhóm ạ' },
    { group: 'g-si', sender: 'u-lan', name: 'Nguyễn Thị Lan', phone: '0909123456', text: 'Số này là số chính của chị nhé mọi người lưu giúp' },
  );
  runPy(process.env.DETECT_SCRIPT!);

  await page.goto('/identity');
  const pair = page.getByRole('article').filter({ hasText: 'Nguyễn Thị Lan' }).filter({ hasText: 'Lan (Zalo cá nhân)' });
  await expect(pair).toBeVisible({ timeout: 15_000 });
  await expect(pair).toContainText('trùng số điện thoại');
  await shot(page, '18-identity-candidate');
  await pair.getByRole('button', { name: /^Gộp/ }).click();
  await maybeEnterOwnerPin(page);
  await expect(pair).toHaveCount(0);

  const history = (await call(page, 'GET', '/identity/history')) as Array<{ op: string }>;
  expect(history.some((h) => h.op === 'merge')).toBe(true);
  await page.getByRole('button', { name: /Lịch sử gộp/ }).click();
  await expect(page.getByRole('dialog')).toContainText('Nguyễn Thị Lan');
  await page.keyboard.press('Escape');

  await page.goto('/system?tab=log');
  await page.getByLabel('Tìm trong nhật ký').fill('Nguyễn Thị Lan');
  await expect(page.getByText('identity.merged')).toBeVisible({ timeout: 10_000 });
  await page.getByLabel('Tìm trong nhật ký').fill('');
  await shot(page, '19-identity-merged-log');

  // ── Luồng 7: cài một plugin từ tệp qua UI thật (chữ ký ed25519 THẬT ký ngoài trình duyệt bằng sign_plugin.py,
  // không phải chuỗi giả — khớp GH_PLUGIN_TRUSTED_SIGNING_KEYS mà run.sh cấu hình cho api) + PIN + quyền xin
  // hiện đúng; rồi breaker THẬT (gh.chassis.breaker) tự mở khi plugin lỗi liên tục — dùng plugin
  // "@e2e/exploder" mà run.sh đã seed (đã 'approved', PluginManager nạp thật lúc boot, entry
  // `tests.plugin_fixtures:Exploder` LUÔN raise) và cô lập trên stream riêng "e2e.plugin.explode" nên không
  // đụng gì tới luồng nghiệp vụ chính. Backend chưa hỗ trợ CHẠY runtime thật cho plugin nạp-từ-tệp
  // (gh.plugins_api.routes.install_local nói rõ "KHÔNG chạy mã tải lên" — phạm vi có chủ đích, không phải lỗi)
  // nên hai nửa của luồng 7 được kiểm bằng hai đường thật khác nhau của cùng một hệ thống, ghi rõ trong báo cáo.
  await page.goto('/plugins');
  await page.getByRole('button', { name: 'Nạp plugin từ tệp' }).click();
  const installDlg = page.getByRole('dialog', { name: 'Nạp plugin từ tệp' });
  const pluginSource = 'console.log("plugin nạp qua UI thật — giai đoạn 5.3 luồng 7")';
  await installDlg.locator('#plg-file').setInputFiles({ name: 'plugin.js', mimeType: 'application/javascript', buffer: Buffer.from(pluginSource) });
  const codeSha = createHash('sha256').update(pluginSource).digest('hex');
  await expect(installDlg.getByLabel(/sha256 mã nguồn/)).toHaveValue(codeSha);
  await expect(installDlg.locator('.plg-perm-chip').first()).toBeVisible();
  const manifestText = await installDlg.locator('#plg-manifest').inputValue();
  const signature = runPy(process.env.SIGN_SCRIPT!, [process.env.PLUGIN_SIGN_KEY!, manifestText, codeSha]);
  await installDlg.getByLabel(/Chữ ký/).fill(signature);
  await shot(page, '20-plugin-install-dialog');
  await installDlg.getByRole('button', { name: 'Nạp plugin' }).click();
  await maybeEnterOwnerPin(page);
  await expect(installDlg).toBeHidden();

  // Plugin nạp từ tệp (origin local_file) hiện ở tab "Plugin cài thêm", không phải tab "Plugin nền" mặc định.
  await page.getByRole('tab', { name: /Plugin cài thêm/ }).click();
  await expect(page.locator('tr[data-package="@ext/vi-du"]')).toBeVisible();
  const exploderRow = page.locator('tr[data-package="@e2e/exploder"]');
  await expect(exploderRow).toBeVisible();
  await expect(exploderRow).toContainText('Đóng');

  // "@e2e/exploder" chạy trong CẢ HAI tiến trình api và worker (build_plugin_manager nạp mọi plugin 'approved'
  // ở cả hai nơi), và cả hai đọc cùng MỘT nhóm tiêu thụ Redis ("@e2e/exploder", xem PluginManager._consume) trên
  // stream "e2e.plugin.explode" → 5 sự kiện lỗi bị CHIA ngẫu nhiên giữa hai tiến trình thay vì cả hai đều thấy
  // đủ cả 5 (đã xác nhận thật ở lần chạy trước: worker nhận 4 lỗi tự mở breaker của NÓ, api chỉ nhận 1 lỗi nên
  // breaker của riêng api — chính là cái `GET /plugins` trả về, vì route đọc `request.app.state.plugins` của
  // tiến trình đang phục vụ HTTP — vẫn "closed"). Breaker là trạng thái TRONG TIẾN TRÌNH, không đồng bộ giữa
  // api/worker, nên phép chờ đúng không phải "bơm N lỗi rồi đợi" mà là bơm liên tục theo đợt nhỏ tới khi CHÍNH
  // breaker của tiến trình api (auto ngưỡng 3 lỗi/120s) tự mở — vẫn trong cùng cửa sổ 120s của breaker đó.
  const deadline = Date.now() + 90_000;
  let breakerOpen = false;
  while (!breakerOpen) {
    runPy(process.env.EXPLODE_SCRIPT!, ['5']);
    try {
      await expect.poll(async () => {
        const plugins = (await call(page, 'GET', '/plugins')) as Array<{ package: string; breaker: { state: string } }>;
        return plugins.find((p) => p.package === '@e2e/exploder')?.breaker.state;
      }, { timeout: 8_000 }).toBe('open');
      breakerOpen = true;
    } catch (e) {
      if (Date.now() > deadline) throw new Error(`chờ breaker @e2e/exploder mở: đã bơm nhiều đợt trong 90s, vẫn closed (${e})`);
    }
  }
  // Màn Plugin không tự làm mới (không polling) — đã xác nhận qua API rằng breaker của tiến trình api đã mở,
  // giờ mới tải lại trang một lần để hiện đúng trạng thái (tránh chờ suông trên một DOM không tự cập nhật).
  await page.reload();
  await page.getByRole('tab', { name: /Plugin cài thêm/ }).click();
  await expect(exploderRow).toContainText('Mở — đã cách ly');
  await shot(page, '21-plugin-breaker-open');

  // Hệ thống chính vẫn chạy: một hành động nghiệp vụ bình thường vẫn thành công trong lúc breaker của
  // "@e2e/exploder" đang mở.
  await expect((await call(page, 'GET', '/health')) as { status?: string }).toBeTruthy();
  await page.goto('/overview');
  await expect(page.getByRole('heading', { name: 'Tổng quan điều hành' }).or(page.locator('h1, h2').first())).toBeVisible();

  await page.goto('/plugins?tab=addon');
  const resetReq = page.waitForResponse((r) => r.url().includes('/breaker/reset') && r.request().method() === 'POST');
  await exploderRow.getByRole('button', { name: 'Reset' }).click();
  await resetReq;
  await expect(exploderRow).toContainText('Đóng', { timeout: 10_000 });

  // ── Luồng 8: MCP thật (fake_mcp.py, JSON-RPC streamable_http) — tool đọc OK; tool ghi → bản nháp chờ duyệt;
  // tool chưa mở → bị chặn + ghi log (khoá cứng #4, gh.mcp_api.routes.call_tool).
  await page.goto('/mcp');
  await page.getByRole('button', { name: 'Thêm máy chủ' }).click();
  const serverDlg = page.getByRole('dialog', { name: 'Thêm máy chủ MCP' });
  await serverDlg.getByLabel('Tên hiển thị').fill('MCP E2E (giai đoạn 5.3)');
  await serverDlg.getByLabel('Kiểu kết nối').selectOption('streamable_http');
  await serverDlg.getByLabel('Endpoint').fill('http://127.0.0.1:9913/');
  await serverDlg.getByRole('button', { name: 'Thêm máy chủ' }).click();
  await expect(serverDlg).toBeHidden();

  const mcpCard = page.locator('.mcp-server', { hasText: 'MCP E2E (giai đoạn 5.3)' });
  await expect(mcpCard).toBeVisible();
  const discoverReq = page.waitForResponse((r) => r.url().includes('/discover') && r.request().method() === 'POST');
  await mcpCard.getByRole('button', { name: /Khám phá tool/ }).click();
  await discoverReq;
  await expect(mcpCard.locator('tr', { hasText: 'list_customer' })).toBeVisible();
  await expect(mcpCard.locator('tr', { hasText: 'update_crm' })).toBeVisible();
  await shot(page, '22-mcp-discovered');

  // Tool ghi CHƯA mở → "Gọi thử" phải bị chặn (khoá cứng #4) và ghi vào nhật ký.
  const writeRow = mcpCard.locator('tr', { hasText: 'update_crm' });
  await writeRow.getByRole('button', { name: 'Gọi thử' }).click();
  let testDlg = page.getByRole('dialog', { name: 'Gọi thử update_crm' });
  await testDlg.getByLabel('Gọi nhân danh agent').selectOption(agentKey);
  await testDlg.getByRole('button', { name: 'Gọi tool' }).click();
  await expect(testDlg.getByText(/Bị chặn/)).toBeVisible();
  await testDlg.locator('.gh-dialog__actions').getByRole('button', { name: 'Đóng' }).click();
  await expect(page.locator('.mcp-log tr[data-outcome="blocked"]').first()).toContainText('update_crm');

  // Mở + cấp list_customer (đọc) cho agent → gọi thử → OK, gọi ra máy chủ MCP giả thật.
  const readRow = mcpCard.locator('tr', { hasText: 'list_customer' });
  await readRow.getByLabel('Mở tool list_customer').click();
  await maybeEnterOwnerPin(page);
  await expect(readRow.getByLabel('Đóng tool list_customer')).toHaveAttribute('aria-checked', 'true');
  const grantReadReq = page.waitForResponse((r) => r.url().includes('/grants') && r.request().method() === 'POST');
  // `.check()` tự xác minh `checked` NGAY sau click — checkbox này là controlled (checked={t.grants.includes(…)})
  // không cập nhật lạc quan, đợi mutation POST /grants xong mới thấy checked thật, nên `.check()` báo "did not
  // change its state" dù thao tác đúng. Dùng `.click()` thuần rồi tự đợi POST + trạng thái checked thật sau đó.
  const grantReadBox = page.getByLabel(`Cấp list_customer cho ${agent.name}`);
  await grantReadBox.click();
  await grantReadReq;
  await expect(grantReadBox).toBeChecked({ timeout: 10_000 });

  await readRow.getByRole('button', { name: 'Gọi thử' }).click();
  testDlg = page.getByRole('dialog', { name: 'Gọi thử list_customer' });
  await testDlg.getByLabel('Gọi nhân danh agent').selectOption(agentKey);
  await testDlg.getByRole('button', { name: 'Gọi tool' }).click();
  await expect(testDlg.getByText(/Kết quả giả cho list_customer/)).toBeVisible({ timeout: 10_000 });
  await testDlg.locator('.gh-dialog__actions').getByRole('button', { name: 'Đóng' }).click();
  await expect(page.locator('.mcp-log tr[data-outcome="ok"]').first()).toContainText('list_customer');
  await shot(page, '23-mcp-read-ok');

  // Mở + cấp update_crm (ghi) cho agent → gọi thử → giữ lại chờ duyệt, tạo bản nháp ở Bàn làm việc, KHÔNG gọi
  // ra máy chủ MCP thật (route dừng ở action_drafts, đúng docstring gh.mcp_api.routes.call_tool).
  await writeRow.getByLabel('Mở tool update_crm').click();
  await maybeEnterOwnerPin(page);
  await expect(writeRow.getByLabel('Đóng tool update_crm')).toHaveAttribute('aria-checked', 'true');
  const grantWriteReq = page.waitForResponse((r) => r.url().includes('/grants') && r.request().method() === 'POST');
  const grantWriteBox = page.getByLabel(`Cấp update_crm cho ${agent.name}`);
  await grantWriteBox.click();
  await grantWriteReq;
  await expect(grantWriteBox).toBeChecked({ timeout: 10_000 });

  await writeRow.getByRole('button', { name: 'Gọi thử' }).click();
  testDlg = page.getByRole('dialog', { name: 'Gọi thử update_crm' });
  await testDlg.getByLabel('Gọi nhân danh agent').selectOption(agentKey);
  await testDlg.getByRole('button', { name: 'Gọi tool' }).click();
  await expect(testDlg.getByText(/Chờ duyệt ở Bàn làm việc/)).toBeVisible({ timeout: 10_000 });
  await testDlg.locator('.gh-dialog__actions').getByRole('button', { name: 'Đóng' }).click();
  await expect(page.locator('.mcp-log tr[data-outcome="held_for_approval"]').first()).toContainText('update_crm');
  await shot(page, '24-mcp-write-held');

  await page.goto('/workbench');
  await expect(page.locator('.wb-list__row', { hasText: 'Gọi tool update_crm' })).toBeVisible({ timeout: 10_000 });
});
