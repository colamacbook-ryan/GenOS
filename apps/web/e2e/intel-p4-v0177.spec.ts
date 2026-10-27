import { mkdirSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { loginAsOwner, resetMock } from './support';

/**
 * v0.1.77 (P4) — Bộ não › Thiết lập Tình báo (bước 3 "Khách chủ chốt") và thẻ Tình báo (banner chạy thử R4, dòng bóng + nút Sai 3 lý do).
 * API tình báo của mock chưa có ⇒ trả dữ liệu cố định qua `page.route`; kiểm: hiển thị ở 1440 và 390 không tràn ngang, không lỗi console,
 * bấm Đúng/Sai/khách chủ chốt gọi đúng đường. Đặt `P4_SHOTS=<thư mục>` để lưu ảnh.
 */
const SHOTS = process.env.P4_SHOTS;

const consoleBody = {
  pin_active: false, mode: 'live', ramp_gates: 'digest_top', regular_push_enabled: true,
  assistant: { locked: false, locked_at: null, locked_via: null }, outbound: { unlocked_today: false, locked_until: null },
  backup_channel: { paired: true, kind: 'telegram' }, mutes: [], payees: [], groups: [], contexts: ['kinh_doanh', 'khac'],
  ramp: {
    started_at: '2026-10-26T03:00:00Z', ends_at: '2026-11-09T03:00:00Z', days: 14, days_left: 12, in_trial: true, passed_gates: [],
    m1: { n: 4, k: 3, p: 0.75, lo95: 0.3, ok: false, min_n: 10, wrong_by_reason: { still_buying: 1, not_urgent: 0, staff_handled: 0, none: 0 }, low_precision_gates: [] },
  },
  shadow_items: [
    { id: 's1', kind: 'xung_dot_khieu_nai', gate: 'khach_che_cong_khai_doa_bo', title: 'Chị Mai chê giao trễ trong nhóm khách sỉ', reasons: ['shadow', 'key_customer'], would_push_at: '2026-10-27T03:10:00Z', verdict: null },
    { id: 's2', kind: 'nghia_vu_loi_hua', gate: 'loi_hua_khach_sap_vo', title: 'Hứa giao 20 bao xi măng trước 15h', reasons: ['shadow'], would_push_at: '2026-10-27T01:00:00Z', verdict: null },
    { id: 's3', kind: 'co_hoi_ket_noi', gate: 'co_hoi_lon_sap_troi', title: 'Khách hỏi 50 tấn thép, cần gấp', reasons: ['shadow'], would_push_at: '2026-10-26T09:00:00Z', verdict: 'correct' },
  ],
};

const setupBody = {
  ready: false,
  groups: { done: true, total: 2, unlabeled_count: 0, unlabeled: [] },
  staff: { done: true, confirmed_at: '2026-10-25T02:00:00Z', staff_count: 1, people: [{ id: 'p9', name: 'Hùng giao hàng', person_type: 'staff', prev_type: 'customer' }] },
  key_customers: {
    done: false, confirmed_at: null, key_count: 1,
    customers: [
      { id: 'p1', entity_id: 'e1', name: 'Chị Mai', key_fact_id: 'f1', suggested: false },
      { id: 'p2', entity_id: 'e2', name: 'Chú Bảy khách sỉ', key_fact_id: null, suggested: true },
      { id: 'p3', entity_id: 'e3', name: 'Anh Tư', key_fact_id: null, suggested: false },
    ],
  },
  contexts: ['kinh_doanh', 'khac'], gated_kinds: ['lời hứa với khách', 'câu hỏi chờ trả lời'],
};

async function routeIntel(page: Page) {
  const calls: Array<{ url: string; method: string; body: unknown }> = [];
  await page.route('**/api/v1/intel/console', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(consoleBody) }));
  await page.route('**/api/v1/intel/setup', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(setupBody) }));
  await page.route(/\/api\/v1\/(signals\/[^/]+\/feedback|me\/profile\/items.*|intel\/setup\/key-customers-confirmed)$/, async (r) => {
    const req = r.request();
    calls.push({ url: new URL(req.url()).pathname.replace('/api/v1', ''), method: req.method(), body: req.postData() ? JSON.parse(req.postData()!) : undefined });
    await r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
  });
  return calls;
}

for (const [label, width, height] of [['1440', 1440, 1000], ['390', 390, 844]] as const) {
  test.describe(`v0.1.77 P4 — Tình báo ở ${label}px`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize({ width, height });
      await resetMock(page.request, 'finished');
      await loginAsOwner(page);
    });

    test('bước 3, banner chạy thử, dòng bóng: hiển thị, không tràn ngang, không lỗi console, bấm Sai hỏi 3 lý do', async ({ page }) => {
      const errors: string[] = [];
      const failedIntel: string[] = [];
      // "Failed to load resource" là dòng console của trình duyệt cho mọi HTTP ≥ 400 (mock chưa phủ hết thẻ khác của tab Bộ não) — kiểm riêng:
      // lỗi JS thật phải rỗng, và không đường nào của Tình báo / chấm / Hồ sơ trả lỗi.
      page.on('console', (m) => m.type() === 'error' && !m.text().startsWith('Failed to load resource') && errors.push(m.text()));
      page.on('pageerror', (e) => errors.push(String(e)));
      page.on('response', (r) => r.status() >= 400 && /\/api\/v1\/(intel|signals|me\/profile)/.test(r.url()) && failedIntel.push(`${r.status()} ${r.url()}`));
      const calls = await routeIntel(page);
      await page.goto('/system?tab=brain');

      const setup = page.getByRole('region', { name: 'Thiết lập Tình báo' });
      await expect(setup).toBeVisible();
      const step3 = setup.getByTestId('intel-setup-key-customers');
      await expect(step3).toContainText('Khách chủ chốt');
      await expect(step3).toContainText('Trợ lý gợi ý — chờ Sếp xác nhận');
      await expect(setup.getByRole('img', { name: 'Đã xong 2 trên 3 bước' })).toBeVisible();

      const card = page.getByRole('region', { name: 'Tình báo', exact: true });
      await expect(card.getByTestId('intel-ramp-banner')).toContainText('Chạy thử lại 14 ngày (từ 26/10)');
      const shadow = card.getByTestId('intel-shadow');
      await expect(shadow).toContainText('Chị Mai chê giao trễ trong nhóm khách sỉ');
      await expect(shadow).toContainText('khách chủ chốt (Sếp đã đánh dấu)');
      await expect(shadow.getByText('Đã chấm Đúng')).toBeVisible();

      if (SHOTS) {
        mkdirSync(SHOTS, { recursive: true });
        await setup.scrollIntoViewIfNeeded();
        await setup.screenshot({ path: `${SHOTS}/p4-setup-step3-${label}.png` });
      }

      await shadow.getByRole('button', { name: 'Báo sai: Chị Mai chê giao trễ trong nhóm khách sỉ' }).click();
      const grp = shadow.getByRole('group', { name: 'Vì sao báo sai?' });
      await expect(grp.getByRole('button')).toHaveText(['Khách vẫn mua', 'Không gấp', 'NV lo rồi', 'Huỷ']);
      if (SHOTS) {
        await card.scrollIntoViewIfNeeded();
        await card.screenshot({ path: `${SHOTS}/p4-intel-card-sai-${label}.png` });
      }
      await grp.getByRole('button', { name: 'Sai: Không gấp' }).click();
      await expect.poll(() => calls.find((c) => c.url === '/signals/s1/feedback')?.body).toEqual({ verdict: 'wrong', reason: 'not_urgent' });
      await shadow.getByRole('button', { name: 'Báo đúng: Hứa giao 20 bao xi măng trước 15h' }).click();
      await expect.poll(() => calls.find((c) => c.url === '/signals/s2/feedback')?.body).toEqual({ verdict: 'correct' });

      await step3.getByRole('switch', { name: 'Anh Tư là khách chủ chốt' }).click();
      await expect.poll(() => calls.find((c) => c.url === '/me/profile/items')?.body).toEqual({ section: 'key_relations', object_entity_id: 'e3' });

      // không tràn ngang ở thẻ nào của màn
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(1);
      await expect(page.getByText('[object Object]')).toHaveCount(0);
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/p4-brain-tab-${label}.png`, fullPage: true });
      expect(errors.filter((e) => !/favicon|ResizeObserver/.test(e))).toEqual([]);
      expect(failedIntel).toEqual([]);
    });
  });
}
