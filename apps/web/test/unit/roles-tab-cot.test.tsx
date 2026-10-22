import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { PermissionsPage, PermScope } from '@gen-harness/contracts';
import { RolesTab } from '../../src/screens/system/RolesTab';
import { roleLine } from '../../src/shell/people';
import { qk } from '../../src/lib/queries';

/** v0.1.61 (F095, F107): ô ma trận là của CẢ CỘT — đổi ô thì mọi mã quyền sửa được trong cột đổi theo. */

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

interface Call {
  url: string;
  method: string;
  body: { role: string; permission: string; scope: PermScope } | undefined;
}

const COLUMNS: PermissionsPage['columns'] = [
  { key: 'queue', label: 'Hàng đợi', permissions: ['queue.read', 'queue.act'] },
  { key: 'profile', label: 'Hồ sơ khách', permissions: ['profile.read', 'profile.write'] },
  { key: 'people_review', label: 'Đánh giá nhân sự', permissions: ['people_review.read', 'people_review.write', 'care.read'] },
  { key: 'action', label: 'Hành động', permissions: ['action.draft', 'action.approve'] },
  { key: 'audit', label: 'Nhật ký', permissions: ['audit.read'] },
];
const ALL_CODES = COLUMNS.flatMap((c) => c.permissions);
const fill = (scope: PermScope, over: Record<string, PermScope> = {}): Record<string, PermScope> =>
  Object.fromEntries(ALL_CODES.map((c) => [c, over[c] ?? scope]));

function page(over: { manager?: Record<string, PermScope>; auditor?: Record<string, PermScope> } = {}): PermissionsPage {
  return {
    columns: COLUMNS,
    roles: [
      { code: 'owner', name: 'Owner — Sếp', meta: 'thấy toàn cảnh', permissions: fill('all') },
      { code: 'manager', name: 'Quản lý', meta: 'thấy việc được giao', permissions: fill('team', over.manager) },
      { code: 'auditor', name: 'Kiểm soát', meta: 'xem, không hành động', permissions: fill('none', { 'queue.read': 'all', 'audit.read': 'all', ...over.auditor }) },
    ],
  } as PermissionsPage;
}

/** Máy chủ giả giống thật: GET trả bảng hiện có, PATCH đổi đúng MỘT mã quyền rồi trả cả bảng (như PATCH /permissions). */
function mockServer(initial: PermissionsPage, failOn?: string) {
  let current = initial;
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const c: Call = { url: String(url), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(c);
      if (c.url.endsWith('/permissions') && c.method === 'GET') return json(200, current);
      if (c.url.endsWith('/permissions') && c.method === 'PATCH' && c.body) {
        const b = c.body;
        if (failOn === b.permission) return json(500, { code: 'INTERNAL', message: 'lỗi máy chủ' });
        current = { ...current, roles: current.roles.map((r) => (r.code === b.role ? { ...r, permissions: { ...r.permissions, [b.permission]: b.scope } } : r)) };
        return json(200, current);
      }
      if (c.url.endsWith('/listening-groups')) return json(200, []);
      if (c.url.endsWith('/boundaries')) return json(200, []);
      return json(404);
    }),
  );
  return calls;
}

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, {
    id: 'u', email: 'owner@genesis.local', display_name: 'Anh Cơ', role: { code: 'owner', name: 'Owner — Sếp' },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
    pin_verified_until: null, permissions: { 'system.read': 'all', 'roles.manage': 'all', 'system.manage': 'all' },
  });
  return render(
    <QueryClientProvider client={qc}>
      <RolesTab />
    </QueryClientProvider>,
  );
}

const patches = (calls: Call[]) => calls.filter((c) => c.method === 'PATCH').map((c) => c.body);

beforeEach(() => undefined);
afterEach(() => vi.unstubAllGlobals());

describe('Quyền hạn › ô ma trận là của cả cột (trừ Soạn nháp / Duyệt tách riêng)', () => {
  it('cột Hành động tách hai ô: tắt "Duyệt & gửi ra ngoài" chỉ đổi action.approve, không đụng action.draft', async () => {
    const calls = mockServer(page());
    const user = userEvent.setup();
    renderTab();
    const approve = (await screen.findByLabelText('Quản lý · Hành động · Duyệt & gửi ra ngoài')) as HTMLSelectElement;
    expect(approve.value).toBe('team');
    expect(screen.queryByLabelText('Quản lý · Hành động')).toBeNull();           // không còn một ô chung cho cả hai quyền
    await user.selectOptions(approve, 'none');
    await waitFor(() => expect(patches(calls)).toHaveLength(1));
    expect(patches(calls)).toEqual([{ role: 'manager', permission: 'action.approve', scope: 'none' }]);
    await waitFor(() => expect((screen.getByLabelText('Quản lý · Hành động · Duyệt & gửi ra ngoài') as HTMLSelectElement).value).toBe('none'));
    expect((screen.getByLabelText('Quản lý · Hành động · Soạn nháp') as HTMLSelectElement).value).toBe('team');
  });

  it('cho phép SOẠN nháp không kéo theo cho phép DUYỆT và gửi ra ngoài (soạn ≠ duyệt, giữ được mặc định)', async () => {
    const calls = mockServer(page({ manager: { 'action.draft': 'assigned', 'action.approve': 'none' } }));
    const user = userEvent.setup();
    renderTab();
    const draft = (await screen.findByLabelText('Quản lý · Hành động · Soạn nháp')) as HTMLSelectElement;
    expect(draft.value).toBe('assigned');
    expect((screen.getByLabelText('Quản lý · Hành động · Duyệt & gửi ra ngoài') as HTMLSelectElement).value).toBe('none');
    // v0.1.69 (F096): mức "Việc được giao" (team) không còn mời chọn mới khi chưa có tính năng Nhóm ⇒ đổi sang "Tất cả".
    expect(within(draft).queryByRole('option', { name: 'Việc được giao' })).toBeNull();
    await user.selectOptions(draft, 'all');
    await waitFor(() => expect(patches(calls)).toHaveLength(1));
    expect(patches(calls)).toEqual([{ role: 'manager', permission: 'action.draft', scope: 'all' }]);   // KHÔNG có action.approve
    await waitFor(() => expect((screen.getByLabelText('Quản lý · Hành động · Soạn nháp') as HTMLSelectElement).value).toBe('all'));
    expect((screen.getByLabelText('Quản lý · Hành động · Duyệt & gửi ra ngoài') as HTMLSelectElement).value).toBe('none');
  });

  it('ô Duyệt & gửi ra ngoài nói rõ quyền này cho gửi tin ra ngoài', async () => {
    mockServer(page());
    renderTab();
    await screen.findByLabelText('Quản lý · Hành động · Duyệt & gửi ra ngoài');
    expect(screen.getAllByTitle(/duyệt được và gửi tin ra ngoài/).length).toBeGreaterThan(0);
  });

  it('cột 3 mã (Đánh giá nhân sự) đổi đủ 3 mã; mã đã đúng mức thì không gửi lại', async () => {
    const calls = mockServer(page({ manager: { 'care.read': 'all' } }));
    const user = userEvent.setup();
    renderTab();
    // care.read đang "all", hai mã kia "team" ⇒ cột lệch mức.
    const select = (await screen.findByLabelText('Quản lý · Đánh giá nhân sự')) as HTMLSelectElement;
    expect(select.value).toBe('mixed');
    await user.selectOptions(select, 'all');
    await waitFor(() => expect(patches(calls)).toHaveLength(2));
    expect(patches(calls).map((p) => p?.permission)).toEqual(['people_review.read', 'people_review.write']);
    expect(patches(calls).every((p) => p?.scope === 'all')).toBe(true);
  });

  it('cột lệch mức hiện "Khác nhau" kèm cảnh báo nói rõ quyền nào khác; chọn một mức là đồng bộ', async () => {
    const calls = mockServer(page({ manager: { 'people_review.write': 'none' } }));
    const user = userEvent.setup();
    renderTab();
    const select = (await screen.findByLabelText('Quản lý · Đánh giá nhân sự')) as HTMLSelectElement;
    expect(select.value).toBe('mixed');
    expect(within(select).getByRole('option', { name: 'Khác nhau' })).toBeDisabled();
    const warn = screen.getByTestId('roles-mixed');
    expect(warn).toHaveAttribute('title', expect.stringContaining('đang khác mức'));
    expect(warn).toHaveTextContent('sửa không');
    expect(warn).toHaveTextContent('xem việc được giao');
    await user.selectOptions(select, 'none');
    await waitFor(() => expect(patches(calls)).toHaveLength(2)); // people_review.write đã none
    expect(patches(calls).map((p) => p?.permission)).toEqual(['people_review.read', 'care.read']);
    await waitFor(() => expect(screen.queryByTestId('roles-mixed')).toBeNull());
  });

  it('Owner khoá hẳn; Kiểm soát giữ khoá cứng: đổi cột Hàng đợi chỉ gửi queue.read, không đụng queue.act', async () => {
    const calls = mockServer(page());
    const user = userEvent.setup();
    renderTab();
    await screen.findByLabelText('Quản lý · Hàng đợi');
    expect(screen.queryByLabelText('Owner — Sếp · Hàng đợi')).toBeNull();
    // Kiểm soát × Hành động: cả hai mã là quyền ghi bị khoá cứng ⇒ không có ô chọn.
    expect(screen.queryByLabelText('Kiểm soát · Hành động · Soạn nháp')).toBeNull();
    expect(screen.queryByLabelText('Kiểm soát · Hành động · Duyệt & gửi ra ngoài')).toBeNull();
    const queueSelect = screen.getByLabelText('Kiểm soát · Hàng đợi') as HTMLSelectElement;
    expect(queueSelect.value).toBe('all'); // queue.read=all, queue.act=none (khoá) ⇒ KHÔNG coi là lệch mức
    expect(screen.queryByTestId('roles-mixed')).toBeNull();
    await user.selectOptions(queueSelect, 'none');
    await waitFor(() => expect(patches(calls)).toHaveLength(1));
    expect(patches(calls)[0]).toEqual({ role: 'auditor', permission: 'queue.read', scope: 'none' });
  });

  it('PATCH giữa chừng lỗi: dừng, báo lỗi thân thiện, tải lại bảng để ô nói đúng cái đang có', async () => {
    const calls = mockServer(page(), 'people_review.write');
    const user = userEvent.setup();
    renderTab();
    const select = (await screen.findByLabelText('Quản lý · Đánh giá nhân sự')) as HTMLSelectElement;
    await user.selectOptions(select, 'none');
    await waitFor(() => expect(screen.getByLabelText('Quản lý · Đánh giá nhân sự')).not.toBeDisabled());
    expect(patches(calls).map((p) => p?.permission)).toEqual(['people_review.read', 'people_review.write']);
    // read đã về none còn write vẫn team ⇒ ô hiện "Khác nhau", không giả vờ đã tắt hết.
    await waitFor(() => expect((screen.getByLabelText('Quản lý · Đánh giá nhân sự') as HTMLSelectElement).value).toBe('mixed'));
    expect(screen.queryByText(/\[object Object\]/)).toBeNull();
  });
});

describe('Tên vai trò tiếng Việt thống nhất', () => {
  it('chân sidebar hiện tên Việt kể cả khi máy chủ cũ còn trả tên Anh', () => {
    expect(roleLine({ role: { code: 'manager', name: 'Manager' } })).toBe('Quản lý');
    expect(roleLine({ role: { code: 'operator', name: 'Operator' } })).toBe('Vận hành');
    expect(roleLine({ role: { code: 'auditor', name: 'Auditor' } })).toBe('Kiểm soát');
    expect(roleLine({ role: { code: 'custom_x', name: 'Trưởng ca' } })).toBe('Trưởng ca');
    expect(roleLine({ role: { code: 'owner', name: 'Owner — Sếp' } })).toBe('Owner · thấy toàn cảnh');
  });
});
