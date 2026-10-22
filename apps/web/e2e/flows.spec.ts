import { join } from 'node:path';
import { expect, test } from '@playwright/test';
import { apiCall, AUDITOR, loginAs, loginAsOwner, MANAGER, OWNER, resetMock, resultsDir, SETUP_TOKEN } from './support';

const shots = join(resultsDir, 'visual');

test.describe('auth', () => {
  test.beforeEach(async ({ request }) => resetMock(request, 'finished'));

  test('401 sends to /login?next=, login returns there, Đăng xuất goes back to /login', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/inbox');
    await expect(page).toHaveURL(/\/login\?next=%2Finbox/);
    await page.screenshot({ path: join(shots, 'login-1440.png') });

    await page.getByLabel('Email').fill(OWNER.email);
    await page.getByLabel('Mật khẩu', { exact: true }).fill('sai-mat-khau-roi');
    await page.keyboard.press('Enter');
    await expect(page.getByText('Email hoặc mật khẩu không đúng.')).toBeVisible();

    await page.getByLabel('Mật khẩu', { exact: true }).fill(OWNER.password);
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/inbox$/);
    await expect(page.getByRole('heading', { name: 'Hộp thư', level: 2 })).toBeVisible();
    await expect(page.locator('.hd-group')).toHaveText('Hộp thư & Việc');
    await expect(page.getByRole('tab', { name: /Tất cả/ })).toBeVisible();

    await page.getByRole('button', { name: /Anh Nguyễn Văn A/ }).click();
    await page.getByRole('menuitem', { name: 'Đăng xuất' }).click();
    await expect(page).toHaveURL(/\/login$/);
  });

  test('role filtering: the operator only sees what /navigation returns', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill('operator@genesis.local');
    await page.getByLabel('Mật khẩu', { exact: true }).fill(OWNER.password);
    await page.getByRole('button', { name: 'Đăng nhập' }).click();
    await expect(page).toHaveURL(/\/overview$/);
    await expect(page.getByRole('link', { name: /Plugin & Tiện ích/ })).toHaveCount(0);
    await expect(page.getByRole('link', { name: /Cài đặt/ })).toHaveCount(0);
    await expect(page.getByRole('link', { name: /Kết nối/ })).toHaveCount(0);
    await page.goto('/system');
    await expect(page.getByText('Vai trò của bạn không có quyền xem màn này')).toBeVisible();
  });

  test('rail mode keeps working and persists', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill(OWNER.email);
    await page.getByLabel('Mật khẩu', { exact: true }).fill(OWNER.password);
    await page.keyboard.press('Enter');
    // v0.1.55 (G5): Owner đăng nhập vào Mặt tiền /owner; thanh bên thu gọn là của Console (Cài đặt nâng cao).
    await expect(page).toHaveURL(/\/owner$/);
    await page.goto('/overview');
    await page.getByRole('button', { name: /Anh Nguyễn Văn A/ }).click();
    await page.getByRole('menuitem', { name: 'Thu gọn thanh bên' }).click();
    await expect(page.locator('.app')).toHaveAttribute('data-sidebar', 'rail');
    // v0.1.42: rail — "Nâng cao" là một icon, bấm thì hiện các nhóm của nó.
    await page.getByRole('button', { name: 'Nâng cao' }).click();
    await page.getByRole('button', { name: 'Tầng dữ liệu' }).click();
    await expect(page).toHaveURL(/\/raw$/);
    await page.reload();
    await expect(page.locator('.app')).toHaveAttribute('data-sidebar', 'rail');
  });
});

test.describe('owner setup', () => {
  test.beforeEach(async ({ request }) => resetMock(request, 'fresh'));

  // v0.1.59 (g5-thiet-lap): Thiết lập MỘT màn, 3 bước (1 Tài khoản · 2 Nguồn AI · 3 Xong) thay cho 12 bước; luồng đủ ở setup-one-v0159.spec.ts.
  test('428 → /setup; Tài khoản (PUT 1–3) rồi Nguồn AI; tiến độ còn nguyên sau khi tải lại', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/overview');
    await expect(page).toHaveURL(/\/setup(\?buoc=tai-khoan)?$/);
    await page.goto(`/setup?token=${SETUP_TOKEN}`);
    await expect(page.getByRole('heading', { name: 'Tạo tài khoản Sếp' })).toBeVisible();
    await expect(page.getByLabel('Mã thiết lập')).toHaveCount(0); // mã đã có từ đường dẫn của trình cài
    await page.screenshot({ path: join(shots, 'setup-account-1440.png') });

    const next = page.getByRole('button', { name: /Tiếp tục/ });
    await expect(next).toBeDisabled();
    await page.getByLabel('Tên Sếp').fill('Anh Nguyễn Văn A (Chủ)');
    await page.getByLabel('Email').fill('owner@example.test');
    await page.getByLabel('Mật khẩu', { exact: true }).fill('mot-cau-rat-dai-de-nho-2026');
    await page.getByLabel('Mã PIN (6 số) — chữ số 1/6').click();
    await page.keyboard.type('246810');
    await page.getByLabel('Nhập lại mã PIN — chữ số 1/6').click();
    await page.keyboard.type('246810');
    await page.getByLabel('Tên doanh nghiệp').fill('Genesis Trading');
    await expect(next).toBeEnabled();

    // "Tuỳ chỉnh" thu gọn: múi giờ / tiền tệ / xưng hô điền sẵn, xem trước câu trả lời mẫu cập nhật trực tiếp.
    await page.getByText('Tuỳ chỉnh').click();
    await expect(page.getByLabel('Múi giờ')).toHaveValue('Asia/Ho_Chi_Minh');
    await expect(page.getByLabel('Tiền tệ')).toHaveValue('VND');
    await page.getByLabel('Sếp tự xưng là').fill('Anh');
    await page.getByLabel('Gen gọi Sếp là').fill('anh A');
    await expect(page.getByText(/Dạ Anh A/)).toBeVisible();
    await page.screenshot({ path: join(shots, 'setup-account-custom-1440.png') });
    await next.click();

    await expect(page.getByRole('heading', { name: 'Nối nguồn AI cho Gen' })).toBeVisible();
    // Phase 2: Nguồn AI là form thật (Google/Antigravity + khoá API), không còn "Sắp có".
    await expect(page.getByRole('heading', { name: 'Đăng nhập Google (Antigravity)' })).toBeVisible();
    await expect(page.getByText('Sắp có')).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Nối nguồn AI cho Gen' })).toBeVisible();
    await page.screenshot({ path: join(shots, 'setup-ai-source-1440.png') });
  });
});

test.describe('cụm Hàng đợi & Hành động', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  test('Tổng quan: KPI và hàng đợi hiện đúng dữ liệu mẫu, mỗi ô KPI dẫn tới màn đã lọc', async ({ page }) => {
    await page.goto('/overview');
    // v0.1.42 (F-64): một hàng 4 số kinh doanh; "Kênh sống" chuyển xuống thẻ Sức khoẻ hệ thống.
    const claimKpi = page.getByText('Tỉ lệ cơ hội được nhận').locator('..').locator('..');
    await expect(claimKpi).toContainText('71,4');
    await expect(page.locator('[data-gen-target="overview.health"]')).toContainText('Kênh đang sống');
    await expect(page.getByText('Tỉ lệ chờ duyệt')).toBeVisible();
    await expect(page.locator('.ov-queue-row', { hasText: 'OPP-1842' })).toBeVisible();
    await expect(page.locator('.ov-spot-row', { hasText: 'Nguyễn Văn Bảo' })).toBeVisible();

    const pendingKpi = page.getByText('Tỉ lệ chờ duyệt').locator('..').locator('..');
    await pendingKpi.click();
    await expect(page).toHaveURL(/\/workbench\?status=pending/);
  });

  test('Hộp thư: tab lọc đúng số đếm; giao người khác; im lặng có chủ đích', async ({ page }) => {
    await page.goto('/inbox');
    const allTab = page.getByRole('tab', { name: /Tất cả/ });
    await expect(allTab).toBeVisible();
    const totalText = await allTab.locator('.gh-tab__count').textContent();
    const total = Number(totalText);
    expect(total).toBeGreaterThan(0);

    const alertTab = page.getByRole('tab', { name: /Cảnh báo/ });
    const alertCount = Number(await alertTab.locator('.gh-tab__count').textContent());
    await alertTab.click();
    await expect(page).toHaveURL(/tab=alert/);
    await expect(page.locator('.ib-card')).toHaveCount(alertCount);
    await expect(page.locator('.ib-card').first()).toContainText('CẢNH BÁO');

    // Giao cho người khác.
    const card = page.locator('.ib-card').first();
    await card.getByRole('button', { name: 'Giao cho người khác' }).click();
    const assignDlg = page.getByRole('dialog', { name: 'Giao cho người khác' });
    await expect(assignDlg).toBeVisible();
    await assignDlg.getByText('Chị Lan Phạm').click();
    await expect(assignDlg).toBeHidden();

    // Im lặng có chủ đích: mục biến mất khỏi hàng đợi.
    const cardTitle = (await card.locator('.ib-card__title').textContent())!.trim();
    await card.getByRole('button', { name: 'Im lặng có chủ đích' }).click();
    const silenceDlg = page.getByRole('dialog', { name: 'Im lặng có chủ đích' });
    await expect(silenceDlg).toBeVisible();
    await silenceDlg.getByRole('button', { name: 'Im lặng mục này' }).click();
    await expect(silenceDlg).toBeHidden();
    await expect(page.locator('.ib-card', { hasText: cardTitle })).toHaveCount(0);
    await expect(alertTab.locator('.gh-tab__count')).toHaveText(String(alertCount - 1));
  });

  test('Việc & Nhắc hẹn: việc quá hạn tô đỏ', async ({ page }) => {
    await page.goto('/tasks');
    const overdueRow = page.locator('.tk-row', { hasText: 'TSK-0410' });
    await expect(overdueRow).toBeVisible();
    await expect(overdueRow).toHaveClass(/tk-row--overdue/);
    await expect(overdueRow).toContainText('hạn');
    const okRow = page.locator('.tk-row', { hasText: 'TSK-0412' });
    await expect(okRow).not.toHaveClass(/tk-row--overdue/);
  });
});

test.describe('cụm Quan hệ & Đối tượng', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  test('Nhóm & Con người: lọc theo độ nhiệt, bật/tắt BOT cho một người', async ({ page }) => {
    await page.goto('/directory');
    await expect(page.locator('.gh-card', { hasText: 'Vận hành Genesis — Quý 4' })).toBeVisible();
    await expect(page.getByText('Nghe hết, chỉ trả lời khi được tag').first()).toBeVisible();

    await page.getByRole('tab', { name: 'Con người' }).click();
    await expect(page).toHaveURL(/dt=people/);
    await expect(page.getByText('Nguyễn Văn Bảo')).toBeVisible();
    await expect(page.getByText('Trịnh Mỹ Duyên')).toBeVisible();

    await page.getByRole('group', { name: 'Độ nhiệt' }).getByRole('button', { name: 'Lạnh' }).click();
    await expect(page).toHaveURL(/heat=cold/);
    await expect(page.getByText('Đặng Hữu Trí')).toBeVisible();
    await expect(page.getByText('Nguyễn Văn Bảo')).toHaveCount(0);

    const row = page.locator('tr', { hasText: 'Đặng Hữu Trí' });
    await expect(row.getByText('Chưa gán')).toBeVisible();
    await row.getByRole('button', { name: 'Đổi' }).click();
    const dlg = page.getByRole('dialog', { name: 'Thiết lập BOT + tự trị' });
    await expect(dlg).toBeVisible();
    // v0.1.35: danh sách trợ lý lấy THẬT từ /pickers/agents (agent đang bật ở Danh tính Agent) — 'Key Account
    // junior' không còn trong danh sách cứng nữa, chọn 'Admin hậu cần' (có thật trong mock-p4-agents).
    await dlg.getByText('Admin hậu cần').click();
    await dlg.getByRole('button', { name: 'Lưu' }).click();
    await expect(dlg).toBeHidden();
    await expect(row.getByText('Admin hậu cần')).toBeVisible();
  });

  test('Hồ sơ sống: xem 5 điểm và "Vì sao hệ thống nghĩ vậy"', async ({ page }) => {
    await page.goto('/profile?id=p-bao');
    await expect(page.getByText('Nguyễn Văn Bảo', { exact: true })).toBeVisible();
    await expect(page.getByText('Zalo', { exact: false }).first()).toBeVisible();
    const heatScore = page.locator('.pf-score', { hasText: 'Độ nóng' });
    await expect(heatScore).toContainText('87');
    await expect(page.getByText('Anh Bảo thích nói chuyện thẳng', { exact: false })).toBeVisible();

    await heatScore.getByRole('button', { name: /Vì sao/ }).click();
    const evidence = page.getByRole('dialog', { name: /Nguyễn Văn Bảo/ });
    await expect(evidence).toBeVisible();
    await expect(evidence).toContainText('87/100');
    await expect(evidence.locator('.gh-dialog__actions').getByRole('button', { name: 'Đóng' })).toBeVisible();
  });

  test('Sổ tay nhận thức: ghim một mục, nén ngay, xem lịch sử nén', async ({ page }) => {
    await page.goto('/notebook');
    await expect(page.locator('.nb-detail__name')).toHaveText('Nguyễn Văn Bảo');
    const line = page.locator('.nb-line', { hasText: 'Đang so sánh giá với Minh Long' });
    await expect(line).toBeVisible();
    const pinBtn = line.getByRole('button', { name: 'Ghim mục này' });
    await pinBtn.click();
    await expect(line.getByRole('button', { name: 'Bỏ ghim mục này' })).toBeVisible();

    await expect(page.getByText('Lần 14', { exact: false })).toBeVisible();
    await page.getByRole('button', { name: 'Nén ngay' }).click();
    await expect(page.getByText('Nén lần thứ 15', { exact: false })).toBeVisible();
    await expect(page.getByText('Lần 15', { exact: false })).toBeVisible();
  });

  test('Tài liệu: tải lên một tệp mới rồi xem lại trong danh sách', async ({ page }) => {
    await page.goto('/documents');
    await expect(page.getByText('BaoGia_ThanhPhat_Q4.docx')).toBeVisible();

    await page.getByRole('button', { name: 'Tải tài liệu lên' }).click();
    const dlg = page.getByRole('dialog', { name: 'Tải tài liệu lên' });
    await dlg.getByLabel('Tiêu đề').fill('Biên bản nghiệm thu tháng 9');
    await dlg.locator('#doc-file').setInputFiles({ name: 'nghiem-thu.txt', mimeType: 'text/plain', buffer: Buffer.from('nội dung mẫu') });
    await dlg.getByRole('button', { name: 'Tải lên' }).click();
    await expect(dlg).toBeHidden();
    await expect(page.getByText('Biên bản nghiệm thu tháng 9')).toBeVisible();

    await page.getByText('Biên bản nghiệm thu tháng 9').click();
    const detail = page.getByRole('dialog', { name: 'Biên bản nghiệm thu tháng 9' });
    await expect(detail).toBeVisible();
    await expect(detail.getByText('role:owner')).toBeVisible();
    await expect(detail.getByRole('link', { name: /Xem \/ tải xuống/ })).toHaveAttribute('href', /\/documents\/.+\/content/);
  });
});

test.describe('cụm Bản đồ quan hệ', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  test('chuyển 4 chế độ, lọc trong Danh sách, cầu nối nổi bật ở Người↔Người và Nhóm↔Nhóm', async ({ page }) => {
    await page.goto('/graph');
    await expect(page.getByRole('heading', { name: 'Bản đồ quan hệ', level: 2 })).toBeVisible();
    await expect(page.getByText('Nguyễn Văn Bảo')).toBeVisible();

    // Lọc: Độ nóng ≥ 80 chỉ còn khách/nhân sự nóng.
    await page.getByRole('button', { name: /Độ nóng/ }).click();
    await page.getByRole('option', { name: '≥ 80' }).click();
    await expect(page).toHaveURL(/heat=high/);
    await expect(page.getByText('Trần Văn Hậu')).toBeVisible();
    await expect(page.getByText('Đặng Hữu Trí')).toHaveCount(0);

    // Người↔Người: Trần Minh Khoa và Nguyễn Thu Hà là cầu nối (bridge_score > 0), tô nổi bật.
    await page.getByRole('radio', { name: 'Người ↔ Người' }).click();
    await expect(page).toHaveURL(/mode=people/);
    await expect(page.getByText('Người là cầu nối')).toBeVisible();
    const bridgeStat = page.locator('.gp-stat-row', { hasText: 'Nguyễn Thu Hà' });
    await expect(bridgeStat).toContainText('cầu nối');

    // Nhóm↔Nhóm: cạnh Vận hành↔Tài chính mang mã người cầu nối (PER-0007 · Nguyễn Thu Hà).
    await page.getByRole('radio', { name: 'Nhóm ↔ Nhóm' }).click();
    await expect(page).toHaveURL(/mode=groups/);
    await expect(page.getByText('Vận hành Genesis — Quý 4').first()).toBeVisible();
    await expect(page.getByText(/người đang bắc cầu/)).toBeVisible();

    // Luồng chủ đề: danh sách rồi mở chi tiết một luồng.
    await page.getByRole('radio', { name: 'Luồng chủ đề' }).click();
    await expect(page).toHaveURL(/mode=topics/);
    const topicRow = page.getByRole('button', { name: /Mở luồng ván MDF E1/ });
    await expect(topicRow).toBeVisible();
    await topicRow.click();
    await expect(page).toHaveURL(/topic=/);
    await expect(page.getByText('Trần Minh Khoa').first()).toBeVisible();
    await expect(page.getByText('Lâm Văn Được').first()).toBeVisible();
    await page.getByRole('button', { name: 'Tất cả luồng chủ đề' }).click();
    await expect(page).not.toHaveURL(/topic=/);
  });

  test('kéo thả một node ở Người↔Người tự lưu vị trí qua PUT /graph/layout/people', async ({ page }) => {
    await page.goto('/graph?mode=people');
    const node = page.locator('.gp-node', { hasText: 'Trần Minh Khoa' });
    await expect(node).toBeVisible();
    const box = (await node.boundingBox())!;
    const startX = box.x + box.width / 2;
    const startY = box.y + box.height / 2;

    const put = page.waitForResponse((r) => r.url().includes('/api/v1/graph/layout/people') && r.request().method() === 'PUT');
    await page.mouse.move(startX, startY);
    await page.mouse.down();
    await page.mouse.move(startX + 90, startY + 50, { steps: 8 });
    await page.mouse.up();
    const res = await put;
    expect(res.ok()).toBe(true);
    const body = res.request().postDataJSON() as { positions: Record<string, { x: number; y: number }> };
    expect(Object.keys(body.positions).length).toBeGreaterThan(0);

    // Tải lại: vị trí đã lưu được nạp lại từ GET /graph/layout/people (không còn dịch chuyển ngẫu nhiên nữa).
    await page.reload();
    await expect(page.locator('.gp-node', { hasText: 'Trần Minh Khoa' })).toBeVisible();
  });

  test('Dựng lại đồ thị gọi POST /graph/recompute (quyền profile.write)', async ({ page }) => {
    await page.goto('/graph');
    await page.getByRole('button', { name: 'Dựng lại' }).click();
    await expect(page.getByText(/Đã dựng lại/)).toBeVisible();
  });
});

test.describe('cụm Cơ hội & Thị trường', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  test('Bảng cơ hội: kéo thả đổi giai đoạn, tổng pipeline của cột cập nhật', async ({ page }) => {
    await page.goto('/opportunity');
    await expect(page.getByRole('heading', { name: 'Bảng cơ hội', level: 2 })).toBeVisible();
    const rawCol = page.locator('.opp-col[data-stage="raw_signal"]');
    const validatedCol = page.locator('.opp-col[data-stage="validated"]');
    await expect(rawCol.locator('.opp-card', { hasText: 'OPP-1851' })).toBeVisible();
    const rawCountBefore = Number(await rawCol.locator('.opp-col__count').textContent());
    const validatedCountBefore = Number(await validatedCol.locator('.opp-col__count').textContent());

    const patch = page.waitForResponse((r) => r.url().includes('/opportunities/') && r.url().includes('/stage') && r.request().method() === 'PATCH');
    await rawCol.locator('.opp-card', { hasText: 'OPP-1851' }).dragTo(validatedCol.locator('.opp-col__drop'));
    const res = await patch;
    expect(res.ok()).toBe(true);
    expect(res.request().postDataJSON()).toEqual({ to_stage: 'validated' });

    await expect(validatedCol.locator('.opp-card', { hasText: 'OPP-1851' })).toBeVisible();
    await expect(rawCol.locator('.opp-card', { hasText: 'OPP-1851' })).toHaveCount(0);
    await expect.poll(async () => Number(await rawCol.locator('.opp-col__count').textContent())).toBe(rawCountBefore - 1);
    await expect.poll(async () => Number(await validatedCol.locator('.opp-col__count').textContent())).toBe(validatedCountBefore + 1);
  });

  test('Bảng cơ hội: thay thế bàn phím "Chuyển sang giai đoạn…" cho kéo thả', async ({ page }) => {
    await page.goto('/opportunity');
    const card = page.locator('.opp-card', { hasText: 'OPP-1849' });
    await card.getByRole('button', { name: /Chuyển giai đoạn cho OPP-1849/ }).click();
    const dlg = page.getByRole('dialog', { name: 'Chuyển sang giai đoạn…' });
    await expect(dlg).toBeVisible();
    await dlg.getByText('Đã ráp khớp', { exact: true }).click();
    await expect(dlg).toBeHidden();
    await expect(page.locator('.opp-col[data-stage="matched"] .opp-card', { hasText: 'OPP-1849' })).toBeVisible();
  });

  test('Cung ↔ Cầu: xem lý do ghép, Giới thiệu hai bên tạo bản nháp và mở ở Bàn làm việc', async ({ page }) => {
    await page.goto('/supply');
    await expect(page.getByRole('heading', { name: 'Cung ↔ Cầu', level: 2 })).toBeVisible();
    const matchRow = page.locator('.sup-match', { hasText: '3 cont ván MDF E1 17mm' });
    await expect(matchRow).toBeVisible();
    await expect(matchRow.getByText(/Cùng mặt hàng.*\+50/)).toBeVisible();
    await expect(matchRow.getByText('khớp 94')).toBeVisible();

    const introduce = page.waitForResponse((r) => r.url().includes('/matches/') && r.url().includes('/introduce') && r.request().method() === 'POST');
    await matchRow.getByRole('button', { name: 'Giới thiệu hai bên' }).click();
    const res = await introduce;
    expect(res.ok()).toBe(true);
    const body = (await res.json()) as { draft: { id: string } };
    expect(body.draft.id).toBeTruthy();

    await expect(page).toHaveURL(new RegExp(`/workbench\\?id=${body.draft.id}`));
  });

  test('Kho hội thoại: tìm đúng người qua facet, hành động hàng loạt', async ({ page }) => {
    await page.goto('/search');
    await expect(page.getByRole('heading', { name: 'Kho hội thoại', level: 2 })).toBeVisible();
    await expect(page.getByText('Trần Văn Hậu')).toBeVisible();
    await expect(page.getByText('Trịnh Mỹ Duyên')).toBeVisible();

    await page.getByRole('button', { name: /^Im lặng/ }).click();
    await expect(page).toHaveURL(/et=WentSilent/);
    await expect(page.getByText('Trịnh Mỹ Duyên')).toBeVisible();
    await expect(page.getByText('Trần Văn Hậu')).toHaveCount(0);
    await page.getByRole('button', { name: /^Im lặng/ }).click(); // bỏ lọc lại

    await page.getByLabel('Chọn Trần Văn Hậu').check();
    await page.getByLabel('Chọn Đặng Hữu Trí').check();
    await page.getByRole('button', { name: /Hành động hàng loạt \(2\)/ }).click();
    const dlg = page.getByRole('dialog', { name: 'Hành động hàng loạt' });
    await dlg.getByText('Giao việc theo dõi').click();
    await dlg.getByLabel('Nội dung việc cần theo dõi').fill('Theo dõi lại trong tuần');
    const bulk = page.waitForResponse((r) => r.url().includes('/search/bulk') && r.request().method() === 'POST');
    await dlg.getByRole('button', { name: /Áp dụng cho 2 người/ }).click();
    const res = await bulk;
    expect(res.ok()).toBe(true);
    expect(res.request().postDataJSON()).toMatchObject({ action: 'task', text: 'Theo dõi lại trong tuần' });
    await expect(dlg).toBeHidden();
  });

  test('Deal & Vụ việc: đổi trạng thái deal và vụ việc', async ({ page }) => {
    await page.goto('/deals');
    await expect(page.getByRole('heading', { name: 'Deal & Vụ việc', level: 2 })).toBeVisible();
    const dealRow = page.locator('tr', { hasText: 'DEA-0092' });
    await expect(dealRow).toBeVisible();
    const dealPatch = page.waitForResponse((r) => r.url().includes('/deals/') && r.request().method() === 'PATCH');
    await dealRow.getByRole('button', { name: 'Đã chốt' }).click();
    await dealPatch;
    await expect(dealRow.getByRole('button', { name: 'Đã chốt' })).toHaveAttribute('aria-pressed', 'true');

    await page.getByRole('tab', { name: 'Vụ việc' }).click();
    await expect(page).toHaveURL(/dtab=cases/);
    const caseRow = page.locator('tr', { hasText: 'CAS-0017' });
    await expect(caseRow).toBeVisible();
    const casePatch = page.waitForResponse((r) => r.url().includes('/cases/') && r.request().method() === 'PATCH');
    await caseRow.getByRole('button', { name: 'Đang xử lý' }).click();
    await casePatch;
    await expect(caseRow.getByRole('button', { name: 'Đang xử lý' })).toHaveAttribute('aria-pressed', 'true');
  });
});

test.describe('cụm Con người & Chất lượng', () => {
  test.beforeEach(async ({ page, request }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(request, 'finished');
  });

  async function enterOwnerPin(page: import('@playwright/test').Page) {
    const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await expect(dlg).toBeVisible();
    await page.getByLabel('Mã PIN — chữ số 1/6').click();
    await page.keyboard.type(OWNER.pin);
    await expect(dlg).toBeHidden();
  }

  test('Q4: Owner thấy đủ, Auditor chỉ thấy nhật ký ai đã xem, Manager bị chặn cả danh mục lẫn API', async ({ page }) => {
    // Owner — nhánh full: đòi PIN, thấy điểm/tín hiệu/khuyến nghị/nút "Vì sao"/"Sửa điểm tay".
    await loginAsOwner(page);
    await page.goto('/people');
    await enterOwnerPin(page);
    await expect(page.getByRole('heading', { name: 'Đánh giá con người', level: 2 })).toBeVisible();
    const tuRowOwner = page.locator('.ppl-row', { hasText: 'Phạm Anh Tú' });
    await expect(tuRowOwner).toBeVisible();
    await expect(tuRowOwner.getByText('54')).toBeVisible();
    await expect(tuRowOwner.getByRole('button', { name: 'Sửa điểm tay' })).toBeVisible();
    await expect(tuRowOwner.getByRole('button', { name: /Xem chứng cứ/ })).toBeVisible();

    // Auditor — nhánh log: màn vẫn vào được (còn trong danh mục), CHỈ "đã có đánh giá" + nhật ký ai đã xem.
    await loginAs(page, AUDITOR.email);
    await page.goto('/people');
    await expect(page.getByRole('link', { name: /Đánh giá con người/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Đánh giá con người', level: 2 })).toBeVisible();
    await expect(page.getByText('Đã có đánh giá').first()).toBeVisible();
    await expect(page.getByText('54', { exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Sửa điểm tay' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /Xem chứng cứ/ })).toHaveCount(0);
    const tuRowAuditor = page.locator('.ppl-row--log', { hasText: 'Phạm Anh Tú' });
    await tuRowAuditor.getByRole('button', { name: 'Xem nhật ký ai đã xem' }).click();
    const logDlg = page.getByRole('dialog');
    await expect(logDlg.getByText('Nhật ký ai đã xem')).toBeVisible();
    await expect(logDlg.getByText('people_review.audit_viewed').first()).toBeVisible();
    await page.locator('.gh-dialog__actions').getByRole('button', { name: 'Đóng' }).click();

    // Manager — mặc định không thấy (Q4): ẩn khỏi danh mục, vào thẳng URL vẫn 403 → EmptyState, không crash.
    // `loginAs` chỉ đổi cookie phiên qua API — điều hướng (goto) để app tải lại danh mục/nav theo vai trò mới.
    await loginAs(page, MANAGER.email);
    await page.goto('/people');
    await expect(page.getByText('Vai trò của bạn không có quyền xem màn này')).toBeVisible();
    await expect(page.getByRole('link', { name: /Đánh giá con người/ })).toHaveCount(0);
  });

  test('sửa điểm tay giữ lịch sử (Owner)', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/people');
    await enterOwnerPin(page);

    const tuRow = page.locator('.ppl-row', { hasText: 'Phạm Anh Tú' });
    await tuRow.getByRole('button', { name: 'Sửa điểm tay' }).click();
    const dlg = page.getByRole('dialog');
    await expect(dlg.getByText('tự động (rules+model)')).toBeVisible();

    await dlg.getByLabel('Điểm mới (0–100)').fill('70');
    await dlg.getByPlaceholder('Bắt buộc — ghi rõ vì sao sửa điểm').fill('Xem lại, khách im lặng chờ duyệt ngân sách.');
    const patch = page.waitForResponse((r) => r.url().includes('/people/reviews/') && r.request().method() === 'PATCH');
    await dlg.getByRole('button', { name: 'Lưu điểm mới' }).click();
    const res = await patch;
    expect(res.ok()).toBe(true);
    const body = res.request().postDataJSON() as { score: number; reason: string; evidence: unknown[] };
    expect(body.score).toBe(70);
    expect(body.evidence.length).toBeGreaterThan(0);

    await expect(dlg.getByText('Lịch sử điểm', { exact: false })).toBeVisible();
    await expect(dlg.locator('.ppl-row__label', { hasText: 'sửa tay' }).first()).toBeVisible();
    await page.locator('.gh-dialog__actions').getByRole('button', { name: 'Đóng' }).click();
    await expect(tuRow.getByText('70')).toBeVisible(); // danh sách tự cập nhật qua invalidate query.
  });

  test('mở phản biện và giải quyết — không tự đổi điểm (khoá cứng 2)', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/people');
    await enterOwnerPin(page);

    const tuRow = page.locator('.ppl-row', { hasText: 'Phạm Anh Tú' });
    await tuRow.getByRole('button', { name: 'Sửa điểm tay' }).click();
    const dlg = page.getByRole('dialog');
    await expect(dlg.getByText(/khách chủ động im lặng/)).toBeVisible();
    await expect(dlg.getByText('đang mở')).toBeVisible();

    await dlg.getByPlaceholder('Ghi lý do xử lý').fill('Đồng ý, không trừ điểm lần này.');
    const resolve = page.waitForResponse((r) => r.url().includes('/disputes/') && r.request().method() === 'PATCH');
    await dlg.getByRole('button', { name: 'Chấp nhận' }).click();
    await resolve;
    await expect(dlg.getByText('đã chấp nhận')).toBeVisible();
    // Giải quyết phản biện chỉ ghi trạng thái bằng chữ — không tự sửa điểm.
    await expect(dlg.locator('.ppl-detail-score')).toContainText('54');

    // Mở phản biện mới trên cùng đánh giá.
    await dlg.getByPlaceholder('Mở phản biện mới — nêu rõ điểm không đồng ý').fill('Xin xem lại một lần nữa.');
    const create = page.waitForResponse((r) => r.url().includes('/disputes') && r.request().method() === 'POST');
    await dlg.getByRole('button', { name: 'Mở phản biện' }).click();
    const createRes = await create;
    expect(createRes.ok()).toBe(true);
    await expect(dlg.getByText('Xin xem lại một lần nữa.')).toBeVisible();
  });

  test('Chất lượng chăm sóc: lưới phản hồi, lỗi lặp lại, kịch bản thắng/mất đúng seed', async ({ page }) => {
    await loginAsOwner(page);
    await page.goto('/care');
    await expect(page.getByRole('heading', { name: 'Chất lượng chăm sóc', level: 2 })).toBeVisible();

    await expect(page.locator('.care-grid-row', { hasText: 'Phạm Anh Tú' })).toBeVisible();
    await expect(page.locator('.care-issue-row', { hasText: 'Phạm Anh Tú' })).toContainText('Hứa rồi quên');
    await expect(page.locator('.care-issue-row', { hasText: 'Nguyễn Thị Ngọc' })).toContainText('Khách bị bỏ rơi');

    const wonScn = page.locator('.care-scn-row', { hasText: 'DEA-0201' });
    await expect(wonScn).toContainText('Thắng');
    await wonScn.getByRole('link', { name: /xem deal/ }).click();
    await expect(page).toHaveURL(/\/deals$/);
    await expect(page.getByRole('heading', { name: 'Deal & Vụ việc', level: 2 })).toBeVisible();
  });
});

test.describe('giai đoạn 4: Agent + API & Model', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  async function enterOwnerPin(page: import('@playwright/test').Page) {
    const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await expect(dlg).toBeVisible();
    await page.getByLabel('Mã PIN — chữ số 1/6').click();
    await page.keyboard.type(OWNER.pin);
    await expect(dlg).toBeHidden();
  }

  test('tạo, nhân bản và tắt agent; xem "agent đã nói gì"', async ({ page }) => {
    await page.goto('/agents');
    await expect(page.getByRole('heading', { name: 'Danh tính Agent', level: 2 })).toBeVisible();
    const tlsCard = page.getByRole('listitem', { name: 'Trợ lý thương mại', exact: true });
    await expect(tlsCard).toBeVisible();
    // v0.1.43 (F-30): viên tự trị hiện nhãn 3 mức (mức 4 → "Soạn sẵn chờ duyệt").
    await expect(tlsCard).toContainText('Soạn sẵn chờ duyệt');
    await expect(tlsCard).not.toContainText('tự trị 4');

    // Bé Heo — mẫu tắt mặc định (spec E13) — vẫn liệt kê, chỉ mờ đi.
    const mascotCard = page.locator('.ag-card', { hasText: 'Bé Heo' });
    await expect(mascotCard).toHaveAttribute('data-off', '');

    // "Agent đã nói gì, nhân danh gì" — GET /agents/decisions.
    // v0.1.67 (F076): quyết định soạn nháp có nháp đang chờ Sếp duyệt ⇒ nhãn "Soạn sẵn chờ duyệt", không bao giờ "Đã gửi".
    await expect(page.locator('.ag-log-row', { hasText: 'Trợ lý thương mại' }).first()).toContainText('Soạn sẵn chờ duyệt');
    await expect(page.locator('.ag-log-row', { hasText: 'Trợ lý thương mại' }).first()).not.toContainText('Đã gửi');

    // Tạo agent mới — cần PIN (agent.manage). Phiên PIN có hiệu lực 30 phút (mock, cùng khuôn backend) nên
    // chỉ lần thao tác nhạy cảm ĐẦU TIÊN trong bài test mới hiện hộp thoại — các lượt sau trong cùng phiên
    // không hỏi lại, giống hệt hành vi thật.
    await page.getByRole('button', { name: 'Tạo agent mới' }).click();
    const createDlg = page.getByRole('dialog', { name: 'Tạo agent mới' });
    await createDlg.getByLabel('Tên hiển thị').fill('Trợ lý kho vận');
    await createDlg.getByLabel('Vai trò').fill('Theo dõi tồn kho và điều phối giao nhận.');
    await createDlg.getByLabel('Giọng điệu').fill('Gọn gàng, đúng việc');
    await createDlg.getByLabel('Khi nào được nói').fill('Khi có yêu cầu giao nhận mới');
    await createDlg.getByRole('button', { name: 'Tạo agent' }).click();
    await enterOwnerPin(page);
    await expect(createDlg).toBeHidden();
    await expect(page.locator('.ag-card', { hasText: 'Trợ lý kho vận' })).toBeVisible();

    // Nhân bản — bản sao tạo ở trạng thái tắt. Phiên PIN vẫn còn hiệu lực từ bước trên nên không hỏi lại.
    await page.getByRole('button', { name: 'Nhân bản' }).click();
    const cloneDlg = page.getByRole('dialog', { name: 'Nhân bản agent' });
    await cloneDlg.getByLabel('Nhân bản từ').selectOption({ label: 'Trợ lý thương mại' });
    await cloneDlg.getByLabel('Tên agent mới').fill('Trợ lý thương mại (bản sao)');
    await cloneDlg.getByRole('button', { name: 'Nhân bản' }).click();
    await expect(cloneDlg).toBeHidden();
    const cloneCard = page.locator('.ag-card', { hasText: 'Trợ lý thương mại (bản sao)' });
    await expect(cloneCard).toBeVisible();
    await expect(cloneCard).toHaveAttribute('data-off', '');

    // Tắt agent gốc — cũng trong cùng phiên PIN.
    await tlsCard.getByLabel('Tắt agent Trợ lý thương mại').click();
    await expect(tlsCard).toHaveAttribute('data-off', '');
  });

  test('API & Model: thêm khoá, kiểm tra kết nối, kéo-thả (thay bằng nút) chuỗi ưu tiên', async ({ page }) => {
    await page.goto('/api');
    await expect(page.getByRole('heading', { name: 'API & Model', level: 2 })).toBeVisible();
    const geminiCard = page.locator('.apm-provider', { hasText: 'Gemini API' });
    await expect(geminiCard).toBeVisible();
    await expect(geminiCard).toContainText('GEM-KEY-01');

    // Thêm khoá — cần system.manage + PIN `ai.route_change` (v0.1.35, F-20): hộp PIN BẮT BUỘC hiện.
    await geminiCard.getByRole('button', { name: 'Thêm khoá' }).click();
    const keyDlg = page.getByRole('dialog', { name: /Thêm khoá cho Gemini API/ });
    await keyDlg.getByLabel('Khoá API mới').fill('sk-test-khoa-moi-88221');
    await keyDlg.getByRole('button', { name: 'Thêm khoá' }).click();
    await enterOwnerPin(page);
    await expect(keyDlg).toBeHidden();
    await expect(geminiCard).toContainText('GEM-KEY-02');

    // Kiểm tra kết nối một nhà cung cấp.
    await geminiCard.getByRole('button', { name: 'Kiểm tra kết nối' }).click();
    await expect(geminiCard.locator('.apm-test-result')).toContainText('Kết nối được');

    // Gán model cho từng agent — bảng đọc từ agent.bindings.
    await expect(page.locator('.apm-table')).toContainText('Sàng lọc & suy luận chính');

    // Chuỗi ưu tiên — kéo-thả có thay thế bàn phím: nút lên/xuống gọi PATCH /providers/chain.
    const chain = page.locator('.apm-chain-list');
    await expect(chain.locator('.apm-chain-row').nth(0)).toContainText('Antigravity Brain');
    await expect(chain.locator('.apm-chain-row').nth(2)).toContainText('DeepSeek API');
    const chainReq = page.waitForResponse(
      (r) => r.url().includes('/providers/chain') && r.request().method() === 'PATCH' && r.ok(),
    );
    await page.getByRole('button', { name: 'Đưa DeepSeek API lên trước' }).click();
    // Cùng phiên PIN với bước thêm khoá ở trên → máy chủ không hỏi lại.
    await chainReq;
    await expect(page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' })).toBeHidden();
    await expect(chain.locator('.apm-chain-row').nth(1)).toContainText('DeepSeek API');
  });
});

test.describe('giai đoạn 4: MCP Hub + Plugin', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'finished');
    await loginAsOwner(page);
  });

  async function enterOwnerPin(page: import('@playwright/test').Page) {
    const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await expect(dlg).toBeVisible();
    await page.getByLabel('Mã PIN — chữ số 1/6').click();
    await page.keyboard.type(OWNER.pin);
    await expect(dlg).toBeHidden();
  }

  test('MCP Hub: mở tool cần PIN, cấp cho agent qua ma trận, gọi tool chưa mở bị chặn', async ({ page }) => {
    await page.goto('/mcp');
    await expect(page.getByRole('heading', { name: 'MCP Hub', level: 2 })).toBeVisible();
    await expect(page.getByText('Rào chắn khoá cứng')).toBeVisible();

    const erpCard = page.locator('.mcp-server', { hasText: 'ERP Genesis' });
    await expect(erpCard).toBeVisible();
    const writeRow = erpCard.locator('tr', { hasText: 'order.createDraft' });
    const exposeSwitch = writeRow.getByLabel('Mở tool order.createDraft');
    await expect(exposeSwitch).toHaveAttribute('aria-checked', 'false');

    // Mở tool — khoá cứng #4: chỉ Owner (PIN) mở được, mặc định đóng.
    await exposeSwitch.click();
    await enterOwnerPin(page);
    await expect(writeRow.getByLabel('Đóng tool order.createDraft')).toHaveAttribute('aria-checked', 'true');

    // Cấp tool vừa mở cho agent qua ma trận agent × tool — không cần PIN (chỉ system.manage).
    const grantReq = page.waitForResponse((r) => r.url().includes('/mcp/tools/') && r.url().includes('/grants') && r.request().method() === 'POST');
    const cell = page.getByLabel('Cấp order.createDraft cho Admin hậu cần');
    await cell.click();
    await grantReq;
    await expect(cell).toBeChecked();

    // Gọi tool CHƯA mở (event.list, máy chủ Lịch & Họp) → bị chặn, hiện đúng lý do, ghi vào nhật ký.
    const calCard = page.locator('.mcp-server', { hasText: 'Lịch & Họp' });
    await calCard.locator('tr', { hasText: 'event.list' }).getByRole('button', { name: 'Gọi thử' }).click();
    const testDlg = page.getByRole('dialog', { name: 'Gọi thử event.list' });
    await testDlg.getByRole('button', { name: 'Gọi tool' }).click();
    await expect(testDlg.getByText('Bị chặn: tool chưa được Owner mở')).toBeVisible();
    await expect(page.locator('.mcp-log tr[data-outcome="blocked"]').first()).toContainText('event.list');
  });

  test('Plugin & Tiện ích: plugin nền không gỡ được, reset breaker, nạp plugin từ tệp (chữ ký + PIN)', async ({ page }) => {
    await page.goto('/plugins');
    await expect(page.getByRole('heading', { name: 'Plugin & Tiện ích', level: 2 })).toBeVisible();

    // Plugin nền — nút gỡ vô hiệu/ẩn với lý do rõ ràng, KHÔNG phải cho bấm rồi báo lỗi.
    const kernelRow = page.locator('tr[data-package="@gen/chassis-kernel"]');
    await expect(kernelRow).toBeVisible();
    await expect(kernelRow.getByRole('button', { name: 'Gỡ' })).toHaveCount(0);
    await expect(kernelRow.getByText('Không gỡ được')).toBeVisible();
    await expect(kernelRow.getByLabel('Tắt Kernel & Plugin Manager')).toHaveAttribute('aria-disabled', 'true');

    // Chuyển tab xem plugin cài thêm — reset breaker của provider đang nửa mở.
    await page.getByRole('tab', { name: /Plugin cài thêm/ }).click();
    const deepseekRow = page.locator('tr[data-package="@gen/provider-deepseek"]');
    await expect(deepseekRow).toContainText('Nửa mở');
    const resetReq = page.waitForResponse((r) => r.url().includes('/breaker/reset') && r.request().method() === 'POST');
    await deepseekRow.getByRole('button', { name: 'Reset' }).click();
    await resetReq;
    await expect(deepseekRow).toContainText('Đóng');

    // Nạp plugin từ tệp — tính sha256 thật từ tệp, hiện quyền xin, chữ ký + PIN đúng luồng backend.
    await page.getByRole('button', { name: 'Nạp plugin từ tệp' }).click();
    const installDlg = page.getByRole('dialog', { name: 'Nạp plugin từ tệp' });
    await installDlg.locator('#plg-file').setInputFiles({ name: 'plugin.js', mimeType: 'application/javascript', buffer: Buffer.from('console.log("plugin ví dụ")') });
    await expect(installDlg.getByLabel(/sha256 mã nguồn/)).toHaveValue(/^[0-9a-f]{64}$/);
    await expect(installDlg.locator('.plg-perm-chip', { hasText: 'read:clean' })).toBeVisible();
    await expect(installDlg.locator('.plg-perm-chip', { hasText: 'write:notes' })).toBeVisible();
    await installDlg.getByLabel(/Chữ ký/).fill('chu-ky-hop-le-e2e-base64');
    await installDlg.getByRole('button', { name: 'Nạp plugin' }).click();
    await enterOwnerPin(page);
    await expect(installDlg).toBeHidden();

    await expect(page.locator('tr[data-package="@ext/vi-du"]')).toBeVisible();
  });
});

test.describe('giai đoạn 4.5/4.6: Điều khiển hệ thống', () => {
  async function enterOwnerPin(page: import('@playwright/test').Page) {
    const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    await expect(dlg).toBeVisible();
    await page.getByLabel('Mã PIN — chữ số 1/6').click();
    await page.keyboard.type(OWNER.pin);
    await expect(dlg).toBeHidden();
  }

  test.describe('trong Console (setup đã xong)', () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await resetMock(page.request, 'finished');
      await loginAsOwner(page);
    });

    test('Quyền hạn: ma trận khoá cứng Owner/Auditor không sửa được; sửa ô Manager cần PIN và giữ nguyên sau tải lại', async ({ page }) => {
      await page.goto('/system?tab=roles');
      await expect(page.getByText('Ma trận quyền theo vai trò')).toBeVisible();

      // Owner: khoá cứng — ô là icon, không có <select> để bấm sửa (không phải cho bấm rồi báo lỗi).
      const ownerRow = page.locator('tr', { has: page.getByText('Owner — Sếp') });
      await expect(ownerRow.locator('select')).toHaveCount(0);
      await expect(ownerRow.locator('[data-locked]').first()).toBeVisible();

      // Auditor: cột "Hành động" (quyền ghi) khoá cứng — không có select cho ô này dù các cột đọc khác sửa được.
      const auditorRow = page.locator('tr', { has: page.getByText('Kiểm soát', { exact: true }) });
      await expect(auditorRow.getByLabel('Kiểm soát · Hành động')).toHaveCount(0);
      await expect(auditorRow.getByLabel('Kiểm soát · Nhật ký')).toHaveCount(1);

      // Manager: sửa cột "Hành động" — cần mã PIN, ghi vào nhật ký, giữ nguyên sau khi tải lại trang.
      const managerCell = page.getByLabel('Quản lý · Hành động · Soạn nháp');
      await expect(managerCell).toHaveValue('team');
      await managerCell.selectOption('none');
      await enterOwnerPin(page);
      await expect(managerCell).toHaveValue('none');
      await page.reload();
      await expect(page.getByLabel('Quản lý · Hành động · Soạn nháp')).toHaveValue('none');
    });

    test('Ranh giới có trách nhiệm: khoá cứng không bật/tắt được; 2 khoá không có công tắc hiện tĩnh', async ({ page }) => {
      await page.goto('/system?tab=roles');
      await expect(page.getByText('Ranh giới có trách nhiệm')).toBeVisible();
      const lockedRow = page.locator('.boundary-row', { hasText: 'Chỉ lưu tin của nhóm Sếp đã chọn nghe' });
      await expect(lockedRow.getByRole('switch')).toHaveAttribute('aria-disabled', 'true');
      await expect(page.getByText('Không có công tắc', { exact: true })).toHaveCount(2);
    });

    test('Nhật ký: tìm theo tên/hành động rồi Xuất CSV (cần PIN) tải đúng tệp', async ({ page }) => {
      await page.goto('/system?tab=log');
      await expect(page.getByText('Nhật ký hành động')).toBeVisible();
      await expect(page.getByText('Trợ lý thương mại')).toBeVisible();

      await page.getByLabel('Tìm trong nhật ký').fill('Policy Engine');
      await expect(page.getByText('Trợ lý thương mại')).toHaveCount(0);
      // A8 (F105): cột Hành động hiện câu tiếng Việt; mã kỹ thuật chỉ còn trong chú thích (title) khi rê chuột.
      await expect(page.locator('td.sys-log__action[title="action.blocked"]')).toBeVisible();
      await page.getByLabel('Tìm trong nhật ký').fill('');

      const downloadPromise = page.waitForEvent('download');
      await page.getByRole('button', { name: /Xuất CSV/ }).click();
      await enterOwnerPin(page);
      const download = await downloadPromise;
      expect(download.suggestedFilename()).toMatch(/^nhat-ky-.*\.csv$/);
    });

    test('Dữ liệu & lưu trữ: sửa hạn lưu (cần PIN), xuất và xoá dữ liệu một người (cần PIN, ghi nhật ký)', async ({ page }) => {
      await page.goto('/system?tab=storage');
      await expect(page.getByText('Hạn lưu dữ liệu')).toBeVisible();

      // v0.1.40 (F-2): việc nền dọn theo hạn lưu đã chạy thật — nút "Sửa" hoạt động, không còn nhãn "Chưa tự xoá".
      await expect(page.getByText(/Chưa tự xoá/)).toHaveCount(0);
      const rawRow = page.locator('tr', { has: page.getByText('Tin gốc') });
      await rawRow.getByRole('button', { name: /Sửa/ }).click();
      await rawRow.getByLabel(/Giữ trong \(ngày\)/).fill('500');
      await rawRow.getByRole('button', { name: 'Lưu', exact: true }).click();
      await page.getByTestId('retention-confirm').getByRole('button', { name: 'Đồng ý xoá dữ liệu quá hạn' }).click();
      await enterOwnerPin(page);
      await expect(rawRow.getByText('500 ngày')).toBeVisible();

      await page.getByLabel('Tìm người (tên hoặc mã)').fill('Bảo');
      await page.getByLabel('Người', { exact: true }).selectOption('p-bao');
      // F100: gói xuất tải ngay thành tệp JSON (máy chủ không lưu gói) — không còn "Đã tạo gói" mà không có gì để tải.
      const exportDownload = page.waitForEvent('download');
      await page.getByRole('button', { name: 'Xuất dữ liệu' }).click();
      expect((await exportDownload).suggestedFilename()).toMatch(/^du-lieu-.*\.json$/);
      await expect(page.getByText(/Đã tải tệp dữ liệu của/)).toBeVisible();
      await expect(page.getByText('Xuất dữ liệu').last()).toBeVisible(); // dòng lịch sử vừa ghi

      // F101: "Ẩn danh hoá" hỏi lại, nêu rõ không xoá tin nhắn gốc. Phiên PIN vừa mở ở lượt sửa hạn lưu vẫn còn hiệu lực.
      await page.getByRole('button', { name: 'Ẩn danh hoá' }).click();
      const eraseDlg = page.getByRole('dialog', { name: 'Ẩn danh hoá người này?' });
      await expect(eraseDlg.getByText(/Không xoá tin nhắn gốc/)).toBeVisible();
      await eraseDlg.getByRole('button', { name: 'Ẩn danh hoá' }).click();
      await expect(page.getByText(/Đã ẩn danh hoá người này/)).toBeVisible();
    });
  });

  // v0.1.59: bước 10–11 không còn nằm trong trình thiết lập (3 bước) — mở ở Hướng dẫn thiết lập (/guide/10, /guide/11); lưu xong quay về /guide.
  test('Hướng dẫn bước 10–11: mời đội ngũ (mật khẩu tạm) rồi cấu hình sao lưu', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await resetMock(page.request, 'fresh', { startAtStep: 10 });
    await loginAsOwner(page);
    await page.goto('/guide/10');

    await expect(page.getByRole('heading', { name: 'Mời đội ngũ' })).toBeVisible();
    // v0.1.55: trong trình thiết lập bước 10 là thẻ gợi ý (nút chính "Để sau"); mở từ Hướng dẫn (/guide/10) thì hiện thẳng form mời.
    await expect(page.getByTestId('step10-suggestion')).toHaveCount(0);
    await page.getByRole('button', { name: 'Thêm người' }).click();
    await page.getByLabel('Tên hiển thị').fill('Chị Hồng Quản');
    await page.getByLabel('Email').fill('hong@genesis.vn');
    await page.getByLabel('Vai trò').selectOption('manager');
    await page.getByRole('button', { name: /Tiếp tục/ }).click();

    await expect(page.getByText('Đã tạo 1 tài khoản')).toBeVisible();
    await expect(page.getByText('hong@genesis.vn')).toBeVisible();
    await page.getByRole('button', { name: /Đã lưu, sang bước sau/ }).click();
    await expect(page).toHaveURL(/\/guide$/);

    await page.goto('/guide/11');
    await expect(page.getByRole('heading', { name: 'Sao lưu' })).toBeVisible();
    await expect(page.getByLabel('Giờ chạy (HH:MM)')).toHaveValue('02:00');
    await page.getByRole('button', { name: /Tiếp tục/ }).click();
    await expect(page).toHaveURL(/\/guide$/);

    const state = (await apiCall(page, 'GET', '/setup/state')) as { steps: Array<{ n: number; status: string }> };
    expect(state.steps.filter((s) => s.status === 'done').map((s) => s.n)).toEqual(expect.arrayContaining([10, 11]));
  });
});
