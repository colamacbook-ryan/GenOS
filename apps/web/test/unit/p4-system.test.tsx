import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { Boundary, PermissionsPage, PermScope, RetentionPolicy } from '@gen-harness/contracts';
import { SystemScreen } from '../../src/screens/system/SystemScreen';
import { LogTab } from '../../src/screens/system/LogTab';
import { retentionRowView } from '../../src/screens/system/systemModel';
import { PinDialogHost } from '../../src/shell/PinDialogHost';
import { qk } from '../../src/lib/queries';
import { useUrlStateStore } from '../../src/lib/uiStore';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

interface Call {
  url: string;
  method: string;
  body: unknown;
}
function mockFetch(handler: (c: Call) => Response | Promise<Response>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const c = { url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(c);
      return handler(c);
    }),
  );
  return calls;
}

function meWith(permissions: Record<string, string>, role = 'owner') {
  return {
    id: 'u', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A (Chủ)',
    role: { code: role, name: role === 'owner' ? 'Owner — Sếp' : role },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
    addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
    permissions,
  };
}
const FULL_PERMS = { 'system.read': 'all', 'system.manage': 'all', 'roles.manage': 'all', 'audit.read': 'all', 'data.manage': 'all' };

function renderScreen(ui: ReactElement, permissions: Record<string, string> = FULL_PERMS, tab = 'channels', role = 'owner') {
  useUrlStateStore.setState({ params: { tab } });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, meWith(permissions, role));
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useUrlStateStore.setState({ params: {} });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

const PERMISSIONS_PAGE: PermissionsPage = {
  columns: [
    { key: 'overview', label: 'Hôm nay', permissions: ['overview.read'] },
    { key: 'queue', label: 'Hàng đợi', permissions: ['queue.read', 'queue.act'] },
    { key: 'profile', label: 'Hồ sơ khách', permissions: ['profile.read', 'profile.write'] },
    { key: 'people_review', label: 'Đánh giá nhân sự', permissions: ['people_review.read', 'people_review.write', 'care.read'] },
    { key: 'opportunity', label: 'Cơ hội', permissions: ['opportunity.read', 'opportunity.write'] },
    { key: 'action', label: 'Hành động', permissions: ['action.draft', 'action.approve'] },
    { key: 'audit', label: 'Nhật ký', permissions: ['audit.read'] },
  ],
  roles: [
    { code: 'owner', name: 'Owner — Sếp', meta: 'thấy toàn cảnh', permissions: { 'overview.read': 'all', 'queue.read': 'all', 'queue.act': 'all', 'profile.read': 'all', 'profile.write': 'all', 'people_review.read': 'all', 'people_review.write': 'all', 'care.read': 'all', 'opportunity.read': 'all', 'opportunity.write': 'all', 'action.draft': 'all', 'action.approve': 'all', 'audit.read': 'all' } },
    { code: 'manager', name: 'Manager', meta: 'thấy team mình', permissions: { 'overview.read': 'team', 'queue.read': 'team', 'queue.act': 'team', 'profile.read': 'team', 'profile.write': 'team', 'people_review.read': 'none', 'people_review.write': 'none', 'care.read': 'none', 'opportunity.read': 'team', 'opportunity.write': 'team', 'action.draft': 'team', 'action.approve': 'team', 'audit.read': 'team' } },
    { code: 'operator', name: 'Operator', meta: 'thấy hàng đợi việc', permissions: { 'overview.read': 'none', 'queue.read': 'all', 'queue.act': 'all', 'profile.read': 'all', 'profile.write': 'all', 'people_review.read': 'none', 'people_review.write': 'none', 'care.read': 'none', 'opportunity.read': 'all', 'opportunity.write': 'all', 'action.draft': 'assigned', 'action.approve': 'none', 'audit.read': 'none' } },
    { code: 'agent_staff', name: 'Agent nhân viên', meta: 'chỉ khách được phân', permissions: { 'overview.read': 'none', 'queue.read': 'assigned', 'queue.act': 'assigned', 'profile.read': 'assigned', 'profile.write': 'assigned', 'people_review.read': 'none', 'people_review.write': 'none', 'care.read': 'none', 'opportunity.read': 'assigned', 'opportunity.write': 'assigned', 'action.draft': 'assigned', 'action.approve': 'none', 'audit.read': 'none' } },
    { code: 'auditor', name: 'Auditor', meta: 'xem, không hành động', permissions: { 'overview.read': 'all', 'queue.read': 'none', 'queue.act': 'none', 'profile.read': 'none', 'profile.write': 'none', 'people_review.read': 'none', 'people_review.write': 'none', 'care.read': 'none', 'opportunity.read': 'none', 'opportunity.write': 'none', 'action.draft': 'none', 'action.approve': 'none', 'audit.read': 'all' } },
  ],
};

const BOUNDARIES: Boundary[] = [
  { code: 'listen_authorized_only', label: 'Chỉ lưu tin của nhóm Sếp đã chọn nghe', enabled: true, locked: true, params: {} },
  { code: 'auto_personnel_decisions', label: 'Hệ thống tự ra quyết định nhân sự', enabled: false, locked: true, params: {} },
  { code: 'approval_gate', label: 'Gửi ra ngoài / vượt ngưỡng tiền / liên quan nhân sự luôn chờ duyệt', enabled: true, locked: true, params: { approval_threshold_vnd: 50_000_000 } },
  { code: 'observe_external_market', label: 'Quan sát nhóm thị trường bên ngoài', enabled: true, locked: false, params: {} },
];

// v0.1.40 (F-2): khuôn mới của GET /retention-policies (mode/editable/note/last_run_at/last_deleted + dòng chỉ đọc).
const LAST_RUN = '2026-10-01T20:00:00Z';
const RETENTION: RetentionPolicy[] = [
  { dataset: 'raw.events', keep_days: 365, anonymize_after_days: null, mode: 'partition', editable: true, note: 'Xoá theo cả tháng khi cả tháng đã quá hạn', last_run_at: LAST_RUN, last_deleted: 0 },
  { dataset: 'clean.meaning_units', keep_days: 730, anonymize_after_days: null, mode: 'partition', editable: true, note: null, last_run_at: null, last_deleted: null },
  { dataset: 'ops.action_log', keep_days: 2555, anonymize_after_days: null, mode: 'not_applicable', editable: false, note: 'Nhật ký hành động chỉ ghi thêm — không xoá theo hạn', last_run_at: null, last_deleted: null },
  { dataset: 'memory.entries', keep_days: null, anonymize_after_days: null, mode: 'batch', editable: true, note: 'Xoá dần các dòng quá hạn mỗi đêm', last_run_at: LAST_RUN, last_deleted: 12 },
  { dataset: 'agent.model_calls', keep_days: 90, anonymize_after_days: 30, mode: 'partition', editable: true, note: null, last_run_at: LAST_RUN, last_deleted: 2 },
  { dataset: 'agent.browser_jobs.result', keep_days: 14, anonymize_after_days: null, mode: 'batch', editable: false, note: 'Kết quả việc trình duyệt nền tự xoá sau 14 ngày', last_run_at: LAST_RUN, last_deleted: 7 },
];

describe('Điều khiển hệ thống › Quyền hạn', () => {
  it('ma trận quyền: khoá Owner (luôn toàn quyền) và Auditor (cột ghi) — ô khoá không có select để sửa', async () => {
    mockFetch((c) => {
      if (c.url.endsWith('/permissions') && c.method === 'GET') return json(200, PERMISSIONS_PAGE);
      if (c.url.endsWith('/listening-groups')) return json(200, []);
      if (c.url.endsWith('/boundaries')) return json(200, BOUNDARIES);
      return json(404);
    });
    renderScreen(<SystemScreen />, FULL_PERMS, 'roles');
    await screen.findByText('Ma trận quyền theo vai trò');

    const ownerRow = (await screen.findByText('Owner — Sếp')).closest('tr') as HTMLElement;
    // Owner: mọi cột đều là icon khoá (không có <select>) — khoá cứng ARCHITECTURE §8.3.
    expect(within(ownerRow).queryAllByRole('combobox')).toHaveLength(0);

    const managerRow = screen.getByText('Quản lý').closest('tr') as HTMLElement;
    // Manager không phải Owner/Auditor: mọi cột sửa được → có <select> cho từng cột.
    // Riêng cột Hành động tách hai ô (Soạn nháp / Duyệt & gửi ra ngoài) nên nhiều hơn số cột đúng một ô.
    expect(within(managerRow).getAllByRole('combobox').length).toBe(PERMISSIONS_PAGE.columns.length + 1);
    // v0.1.28 (UX N7): tên vai trò tiếng Việt; Auditor = "chỉ xem", ô không bao giờ ghi "Toàn quyền"; nhãn ngắn.
    const auditorRow = screen.getByText('Kiểm soát').closest('tr') as HTMLElement;
    expect(within(auditorRow).getByText('chỉ xem, không làm thao tác nào')).toBeInTheDocument();
    expect(screen.queryByText(/Toàn quyền/)).toBeNull();
    for (const o of within(managerRow).getAllByRole('option')) expect((o.textContent ?? '').length).toBeLessThanOrEqual(16);
  });

  it('sửa một ô (Manager · Hành động) gửi đúng PATCH /permissions rồi cập nhật lại bảng', async () => {
    let current = PERMISSIONS_PAGE;
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/permissions') && c.method === 'GET') return json(200, current);
      if (c.url.endsWith('/permissions') && c.method === 'PATCH') {
        const b = c.body as { role: string; permission: string; scope: PermScope };
        current = { ...current, roles: current.roles.map((r) => (r.code === b.role ? { ...r, permissions: { ...r.permissions, [b.permission]: b.scope } } : r)) };
        return json(200, current);
      }
      if (c.url.endsWith('/listening-groups')) return json(200, []);
      if (c.url.endsWith('/boundaries')) return json(200, BOUNDARIES);
      return json(404);
    });
    const user = userEvent.setup();
    renderScreen(<SystemScreen />, FULL_PERMS, 'roles');
    await screen.findByText('Ma trận quyền theo vai trò');

    const managerRow = (await screen.findByText('Quản lý')).closest('tr') as HTMLElement;
    const actionSelect = within(managerRow).getByLabelText('Quản lý · Hành động · Soạn nháp') as HTMLSelectElement;
    expect(actionSelect.value).toBe('team');
    await user.selectOptions(actionSelect, 'none');

    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/permissions') && c.method === 'PATCH')).toBe(true));
    const patch = calls.find((c) => c.method === 'PATCH');
    expect(patch?.body).toEqual({ role: 'manager', permission: 'action.draft', scope: 'none' });
  });

  it('ranh giới có trách nhiệm: 6 dòng từ API + 2 dòng tĩnh (không công tắc), khoá cứng disable Switch', async () => {
    mockFetch((c) => {
      if (c.url.endsWith('/permissions')) return json(200, PERMISSIONS_PAGE);
      if (c.url.endsWith('/listening-groups')) return json(200, []);
      if (c.url.endsWith('/boundaries')) return json(200, BOUNDARIES);
      return json(404);
    });
    renderScreen(<SystemScreen />, FULL_PERMS, 'roles');
    await screen.findByText('Ranh giới có trách nhiệm');
    await screen.findByText('Chỉ lưu tin của nhóm Sếp đã chọn nghe');
    // 2 khoá cứng không có dòng ops.policy_boundaries (STATIC_HARD_BOUNDARIES) — hiện tĩnh, "Không có công tắc".
    expect(screen.getAllByText('Không có công tắc')).toHaveLength(2);
    const lockedRow = screen.getByText('Chỉ lưu tin của nhóm Sếp đã chọn nghe').closest('.boundary-row') as HTMLElement;
    expect(within(lockedRow).getByRole('switch')).toHaveAttribute('aria-disabled', 'true');
  });
});

describe('Điều khiển hệ thống › Nhật ký', () => {
  it('ô tìm gửi lên máy chủ (không lọc tại máy); Xuất CSV gọi đúng GET /audit-log/export', async () => {
    const AUDIT_ITEMS = [
      { id: '1', at: '2026-09-20T08:00:00Z', actor_type: 'user', actor_id: 'u1', actor_label: 'Anh Nguyễn Văn A', action: 'auth.pin_verified', target_type: null, target_id: null, target_label: null, autonomy_level: null, result: 'ok', detail: null },
      { id: '2', at: '2026-09-20T07:00:00Z', actor_type: 'agent', actor_id: 'a1', actor_label: 'Trợ lý thương mại', action: 'draft.created', target_type: 'draft', target_id: 'd1', target_label: 'ACT-0231', autonomy_level: 4, result: 'held', detail: null },
    ];
    const calls = mockFetch((c) => {
      if (c.url.includes('/audit-log/export')) return json(200, 'at,actor_type\n2026-09-20,user');
      if (c.url.includes('/audit?')) {
        // Máy chủ lọc theo `q` (như gh.audit.routes.query_log); web không tự lọc lại.
        const q = new URL(c.url, 'http://x').searchParams.get('q')?.toLowerCase();
        return json(200, { items: q ? AUDIT_ITEMS.filter((a) => (a.actor_label ?? '').toLowerCase().includes(q)) : AUDIT_ITEMS, next_cursor: null });
      }
      return json(404);
    });
    // jsdom không có URL.createObjectURL/revokeObjectURL — test/setup.ts đã bù trên URL thật (không stub URL ở đây:
    // hẹn giờ revokeObjectURL 1 giây của downloadText sẽ chạy sau khi unstub). Tải tệp thật: xem e2e.
    const user = userEvent.setup();
    renderScreen(<SystemScreen />, FULL_PERMS, 'log');
    await screen.findByText('Trợ lý thương mại');
    expect(screen.getByText('Anh Nguyễn Văn A')).toBeInTheDocument();

    await user.type(screen.getByLabelText('Tìm trong nhật ký'), 'Trợ lý');
    await waitFor(() => expect(screen.queryByText('Anh Nguyễn Văn A')).not.toBeInTheDocument());
    expect(calls.some((c) => c.url.includes('/audit?') && new URL(c.url, 'http://x').searchParams.get('q') === 'Trợ lý')).toBe(true);
    expect(screen.getByText('Trợ lý thương mại')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Xuất CSV/ }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/audit-log/export'))).toBe(true));
  });

  it('vai trò không có audit.read thấy thông báo khoá, không thấy bảng', async () => {
    mockFetch(() => json(404));
    renderScreen(<LogTab />, { 'system.read': 'all' }, 'log');
    expect(await screen.findByText('Vai trò của bạn không xem được Nhật ký')).toBeInTheDocument();
  });

  it('v0.1.42: Cài đặt không hiện tab Nhật ký cho vai trò không có audit.read (?tab=log → tab đầu tiên)', async () => {
    mockFetch(() => json(404));
    renderScreen(<SystemScreen />, { 'system.read': 'all' }, 'log');
    expect(await screen.findByRole('tab', { name: /Sao lưu & cập nhật/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByRole('tab', { name: /Nhật ký/ })).not.toBeInTheDocument();
  });
});

describe('Điều khiển hệ thống › Dữ liệu & lưu trữ', () => {
  // v0.1.40 (F-2): việc nền dọn theo hạn lưu đã chạy thật — nút "Sửa" mở lại (PIN như cũ), bỏ nhãn tạm "Chưa tự xoá".
  it('hạn lưu: nút Sửa bật, bấm ⇒ form, Lưu gửi PATCH kèm PIN (gửi lại nguyên anonymize_after_days) rồi đóng form', async () => {
    let current = RETENTION;
    let pinOk = false;
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/retention-policies') && c.method === 'GET') return json(200, current);
      if (c.url.endsWith('/retention-policies') && c.method === 'PATCH') {
        if (!pinOk) return json(423, { status: 423, code: 'PIN_REQUIRED', title: 'Thao tác này cần nhập mã PIN' });
        const b = c.body as { dataset: string; keep_days: number | null };
        current = current.map((r) => (r.dataset === b.dataset ? { ...r, keep_days: b.keep_days } : r));
        return json(200, current);
      }
      if (c.url.endsWith('/auth/pin/verify')) {
        pinOk = true;
        return json(200, { pin_verified_until: new Date(Date.now() + 1800_000).toISOString() });
      }
      if (c.url.includes('/directory/people')) return json(200, { items: [], next_cursor: null, total: 0 });
      return json(404);
    });
    document.cookie = 'gh_csrf=test-csrf';
    const user = userEvent.setup();
    renderScreen(
      <>
        <SystemScreen />
        <PinDialogHost />
      </>,
      FULL_PERMS,
      'storage',
    );
    const panel = await screen.findByRole('region', { name: 'Hạn lưu dữ liệu' });
    expect(within(panel).getByText('Mỗi tập dữ liệu một hạn — đổi cần mã PIN')).toBeInTheDocument();
    expect(screen.queryByText(/Chưa tự xoá/)).toBeNull();
    expect(screen.queryByText(/CHƯA tự xoá/)).toBeNull();
    // Không còn cột/ô "Ẩn danh sau" (hệ thống chưa thi hành ẩn danh — không hứa điều chưa làm).
    await within(panel).findByText(/Tin gốc/);
    // v0.1.70 (F111): mã bảng kỹ thuật (raw.events…) chỉ nằm trong "Chi tiết kỹ thuật", không in thẳng dưới tên tập dữ liệu.
    const tech = panel.querySelectorAll('details.retention-table__tech');
    expect(tech.length).toBeGreaterThan(0);
    expect(tech[0].querySelector('summary')?.textContent).toBe('Chi tiết kỹ thuật');
    expect(panel.querySelector('td > .retention-table__code')).toBeNull();
    expect(within(panel).queryByText('Ẩn danh sau')).toBeNull();
    expect(within(panel).queryAllByRole('columnheader').map((h) => h.textContent)).not.toContain('Ẩn danh sau');

    const modelRow = within(panel).getByText('Lượt gọi model').closest('tr') as HTMLElement;
    expect(within(modelRow).getByText(/Lần dọn gần nhất: .* · đã xoá 2 tháng$/)).toBeInTheDocument();
    const edit = within(modelRow).getByRole('button', { name: /Sửa/ });
    expect(edit).toBeEnabled();
    await user.click(edit);
    const input = within(modelRow).getByLabelText(/Giữ trong \(ngày\)/);
    expect(within(modelRow).queryByLabelText(/Ẩn danh sau/)).toBeNull();
    await user.clear(input);
    await user.type(input, '120');
    await user.click(within(modelRow).getByRole('button', { name: 'Lưu' }));
    // v0.1.40 (F-2): hỏi lại trước — dữ liệu quá hạn bị XOÁ VĨNH VIỄN; chưa gửi PATCH nào trước khi đồng ý.
    const confirm = await within(panel).findByTestId('retention-confirm');
    expect(confirm).toHaveTextContent('Mọi tháng dữ liệu đã cũ hơn 120 ngày sẽ bị XOÁ VĨNH VIỄN ở lượt dọn kế tiếp');
    expect(calls.filter((c) => c.method === 'PATCH')).toHaveLength(0);
    await user.click(within(confirm).getByRole('button', { name: 'Đồng ý xoá dữ liệu quá hạn' }));

    const pin = await screen.findByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
    const boxes = within(pin).getAllByLabelText(/Mã PIN — chữ số/);
    await waitFor(() => expect(boxes[0]).toHaveFocus());
    await user.keyboard('246810');

    await waitFor(() => expect(calls.filter((c) => c.url.endsWith('/retention-policies') && c.method === 'PATCH')).toHaveLength(2));
    const patch = calls.filter((c) => c.method === 'PATCH').pop();
    expect(patch?.body).toEqual({ dataset: 'agent.model_calls', keep_days: 120, anonymize_after_days: 30, confirm_delete: true });
    await waitFor(() => expect(within(panel).queryByLabelText(/Giữ trong \(ngày\)/)).toBeNull());
    const after = within(panel).getByText('Lượt gọi model').closest('tr') as HTMLElement;
    expect(within(after).getByText('120 ngày')).toBeInTheDocument();
  });

  it('hạn lưu: ops.action_log "Không áp dụng" (không có nút Sửa); kết quả trình duyệt nền 14 ngày chỉ đọc; 422 hiện dưới ô', async () => {
    mockFetch((c) => {
      if (c.url.endsWith('/retention-policies') && c.method === 'GET') return json(200, RETENTION);
      if (c.url.endsWith('/retention-policies') && c.method === 'PATCH') {
        return json(422, { status: 422, code: 'VALIDATION_ERROR', title: 'Dữ liệu chưa hợp lệ', errors: { keep_days: 'Số ngày từ 1 đến 3650' } });
      }
      if (c.url.includes('/directory/people')) return json(200, { items: [], next_cursor: null, total: 0 });
      return json(404);
    });
    const user = userEvent.setup();
    renderScreen(<SystemScreen />, FULL_PERMS, 'storage');
    const panel = await screen.findByRole('region', { name: 'Hạn lưu dữ liệu' });
    const logRow = (await within(panel).findByText('Nhật ký hành động')).closest('tr') as HTMLElement;
    expect(within(logRow).getByText('Không áp dụng')).toBeInTheDocument();
    expect(within(logRow).getByText('Nhật ký hành động chỉ ghi thêm — không xoá theo hạn')).toBeInTheDocument();
    expect(within(logRow).queryByRole('button', { name: /Sửa/ })).toBeNull();

    const jobsRow = within(panel).getByText('agent.browser_jobs.result').closest('tr') as HTMLElement;
    expect(within(jobsRow).getByText('14 ngày (cố định)')).toBeInTheDocument();
    expect(within(jobsRow).queryByRole('button', { name: /Sửa/ })).toBeNull();
    expect(within(jobsRow).getByText(/Lần dọn gần nhất: .* · đã xoá 7 dòng$/)).toBeInTheDocument();

    const rawRow = within(panel).getByText(/Tin gốc/).closest('tr') as HTMLElement;
    expect(within(rawRow).getByText('Xoá theo cả tháng khi cả tháng đã quá hạn')).toBeInTheDocument();
    await user.click(within(rawRow).getByRole('button', { name: /Sửa/ }));
    const input = within(rawRow).getByLabelText(/Giữ trong \(ngày\)/);
    await user.clear(input);
    await user.type(input, '0');
    await user.click(within(rawRow).getByRole('button', { name: 'Lưu' }));
    expect(await within(rawRow).findByText('Số ngày từ 1 đến 3650')).toBeInTheDocument();
    expect(screen.queryByText('[object Object]')).toBeNull();
  });

  it('hạn lưu đặt trước v0.1.40 (needs_confirm) hiện "chưa áp dụng"; Quay lại không gửi; để trống = giữ mãi không cần hỏi lại', async () => {
    const rows: RetentionPolicy[] = RETENTION.map((r) => (r.dataset === 'memory.entries' ? { ...r, keep_days: 60, needs_confirm: true } : r));
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/retention-policies') && c.method === 'GET') return json(200, rows);
      if (c.url.endsWith('/retention-policies') && c.method === 'PATCH') return json(200, rows);
      if (c.url.includes('/directory/people')) return json(200, { items: [], next_cursor: null, total: 0 });
      return json(404);
    });
    document.cookie = 'gh_csrf=test-csrf';
    const user = userEvent.setup();
    renderScreen(<SystemScreen />, FULL_PERMS, 'storage');
    const panel = await screen.findByRole('region', { name: 'Hạn lưu dữ liệu' });
    const memRow = (await within(panel).findByText('Sổ tay nhận thức')).closest('tr') as HTMLElement;
    expect(within(memRow).getByText('60 ngày (chưa áp dụng)')).toBeInTheDocument();
    expect(within(memRow).getByText(/Chưa áp dụng — hạn này đặt trước bản v0\.1\.40/)).toBeInTheDocument();

    await user.click(within(memRow).getByRole('button', { name: /Sửa/ }));
    await user.click(within(memRow).getByRole('button', { name: 'Lưu' }));
    const confirm = await within(panel).findByTestId('retention-confirm');
    expect(confirm).toHaveTextContent('Dữ liệu cũ hơn 60 ngày sẽ bị XOÁ VĨNH VIỄN');
    await user.click(within(confirm).getByRole('button', { name: 'Quay lại' }));
    expect(within(panel).queryByTestId('retention-confirm')).toBeNull();
    expect(calls.filter((c) => c.method === 'PATCH')).toHaveLength(0);

    await user.clear(within(memRow).getByLabelText(/Giữ trong \(ngày\)/));
    await user.click(within(memRow).getByRole('button', { name: 'Lưu' }));
    await waitFor(() => expect(calls.filter((c) => c.method === 'PATCH')).toHaveLength(1));
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ dataset: 'memory.entries', keep_days: null, anonymize_after_days: null });
  });

  it('hạn lưu: Manager (system.manage, không phải Owner) không sửa được bảng xoá theo tháng, vẫn sửa được Sổ tay', async () => {
    mockFetch((c) => {
      if (c.url.endsWith('/retention-policies') && c.method === 'GET') return json(200, RETENTION);
      if (c.url.includes('/directory/people')) return json(200, { items: [], next_cursor: null, total: 0 });
      return json(404);
    });
    renderScreen(<SystemScreen />, { 'system.read': 'all', 'system.manage': 'all' }, 'storage', 'manager');
    const panel = await screen.findByRole('region', { name: 'Hạn lưu dữ liệu' });
    const rawRow = (await within(panel).findByText(/Tin gốc/)).closest('tr') as HTMLElement;
    const rawEdit = within(rawRow).getByRole('button', { name: /Sửa/ });
    expect(rawEdit).toBeDisabled();
    expect(rawEdit).toHaveAttribute('title', 'Chỉ Owner đổi được hạn lưu của dữ liệu xoá theo tháng');
    const memRow = within(panel).getByText('Sổ tay nhận thức').closest('tr') as HTMLElement;
    expect(within(memRow).getByRole('button', { name: /Sửa/ })).toBeEnabled();
  });

  it('hạn lưu: lượt dọn lỗi (last_ok false) báo lỗi + giờ thử lại, không hiện "đã xoá 0"', async () => {
    const rows: RetentionPolicy[] = RETENTION.map((r) => (r.dataset === 'memory.entries' ? { ...r, last_deleted: 0, last_ok: false } : r));
    mockFetch((c) => {
      if (c.url.endsWith('/retention-policies') && c.method === 'GET') return json(200, rows);
      if (c.url.includes('/directory/people')) return json(200, { items: [], next_cursor: null, total: 0 });
      return json(404);
    });
    renderScreen(<SystemScreen />, FULL_PERMS, 'storage');
    const panel = await screen.findByRole('region', { name: 'Hạn lưu dữ liệu' });
    const memRow = (await within(panel).findByText('Sổ tay nhận thức')).closest('tr') as HTMLElement;
    const failed = within(memRow).getByTestId('retention-failed-memory.entries');
    expect(failed).toHaveTextContent(/^Lần dọn gần nhất lỗi \(.*\) — hệ thống sẽ thử lại lúc 05:00$/);
    expect(within(memRow).queryByText(/đã xoá 0/)).toBeNull();
  });

  it('hạn lưu chưa xác nhận: Manager trên bảng xoá theo tháng được nhờ Owner (không bảo bấm Sửa), lý do hiện thành chữ', async () => {
    const rows: RetentionPolicy[] = RETENTION.map((r) => (r.dataset === 'raw.events' ? { ...r, needs_confirm: true } : r));
    mockFetch((c) => {
      if (c.url.endsWith('/retention-policies') && c.method === 'GET') return json(200, rows);
      if (c.url.includes('/directory/people')) return json(200, { items: [], next_cursor: null, total: 0 });
      return json(404);
    });
    renderScreen(<SystemScreen />, { 'system.read': 'all', 'system.manage': 'all' }, 'storage', 'manager');
    const panel = await screen.findByRole('region', { name: 'Hạn lưu dữ liệu' });
    const rawRow = (await within(panel).findByText(/Tin gốc/)).closest('tr') as HTMLElement;
    expect(within(rawRow).getByText('Chưa áp dụng — hạn này đặt trước bản v0.1.40. Nhờ Owner xác nhận lại hạn này.')).toBeInTheDocument();
    expect(within(rawRow).queryByText(/Bấm Sửa/)).toBeNull();
    expect(within(rawRow).getByTestId('retention-locked-raw.events')).toHaveTextContent('Chỉ Owner đổi được hạn lưu của dữ liệu xoá theo tháng');
  });

  it('retentionRowView: người chỉ xem (system.read) được nhờ Owner; Owner giữ câu "Bấm Sửa → Lưu"', () => {
    const mem = RETENTION.find((x) => x.dataset === 'memory.entries') as RetentionPolicy;
    const r: RetentionPolicy = { ...mem, keep_days: 60, needs_confirm: true };
    expect(retentionRowView(r, undefined, { isOwner: false, canManage: false }).pending).toMatch(/Nhờ Owner xác nhận lại hạn này\.$/);
    expect(retentionRowView(r, undefined, { isOwner: false, canManage: false }).lockedReason).toBeNull();
    expect(retentionRowView(r, undefined, { isOwner: true, canManage: true }).pending).toMatch(/Bấm Sửa → Lưu để xác nhận/);
    // Manager vẫn sửa được Sổ tay (không phải bảng xoá theo tháng) ⇒ giữ câu "Bấm Sửa".
    expect(retentionRowView(r, undefined, { isOwner: false, canManage: true }).pending).toMatch(/Bấm Sửa → Lưu/);
  });

  it('yêu cầu xuất dữ liệu một người gọi đúng POST /persons/{id}/data-requests với kind=export', async () => {
    const calls = mockFetch((c) => {
      if (c.url.endsWith('/retention-policies')) return json(200, RETENTION);
      if (c.url.includes('/directory/people')) return json(200, { items: [{ id: 'p1', code: 'PER-0042', name: 'Nguyễn Văn Bảo', type: null, org_name: null, relation: 'customer', channels: [], heat: null, heat_trend: null, value_vnd: null, priority: 'normal', bot: null }], next_cursor: null, total: 1 });
      if (c.url.includes('/persons/p1/data-requests') && c.method === 'POST') return json(201, { id: 'req1', kind: 'export', status: 'completed', result: {} });
      if (c.url.includes('/persons/p1/data-requests') && c.method === 'GET') return json(200, []);
      return json(404);
    });
    const user = userEvent.setup();
    renderScreen(<SystemScreen />, FULL_PERMS, 'storage');
    await screen.findByText('Yêu cầu xuất / xoá dữ liệu một người');
    await screen.findByText('Nguyễn Văn Bảo · PER-0042');

    await user.selectOptions(screen.getByLabelText('Người'), 'p1');
    await user.click(screen.getByRole('button', { name: 'Xuất dữ liệu' }));

    await waitFor(() => expect(calls.some((c) => c.url.includes('/persons/p1/data-requests') && c.method === 'POST')).toBe(true));
    const post = calls.find((c) => c.url.includes('/persons/p1/data-requests') && c.method === 'POST');
    expect(post?.body).toEqual({ kind: 'export' });
  });
});
