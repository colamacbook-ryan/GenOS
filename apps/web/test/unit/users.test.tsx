/** v0.1.22 (Đợt B1–B3): Người dùng, Tổ chức, Trợ giúp. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { ReactNode } from 'react';
import type { ManagedUser, UsersPage } from '@gen-harness/contracts';
import { HelpPage } from '../../src/help/HelpPage';
import { GENH_COMMANDS, diagnosticText, withVersions } from '../../src/help/helpModel';
import { queryClient } from '../../src/lib/queryClient';
import { useToasts } from '../../src/lib/toast';
import { OrgTab } from '../../src/screens/system/OrgTab';
import { UsersTab } from '../../src/screens/system/UsersTab';
import { userStatus } from '../../src/screens/system/usersModel';

const OWNER_PERMS = { 'roles.manage': 'all', 'system.read': 'all', 'system.manage': 'all' };
const me = (perms: Record<string, string> = OWNER_PERMS, role = { code: 'owner', name: 'Owner — Sếp' }) => ({
  id: 'u1', email: 'owner@genesis.local', display_name: 'Anh Nguyễn Văn A (Chủ)', role,
  org: { id: 'o1', name: 'Genesis Trading', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: perms, must_change_password: false,
  features: { gen: true },
});

const U = (over: Partial<ManagedUser>): ManagedUser => ({
  id: 'x', display_name: 'X', email: 'x@genesis.local', role: { code: 'operator', name: 'Operator · vận hành' }, status: 'active',
  must_change_password: false, last_login_at: '2026-09-28T01:00:00Z', created_at: '2026-05-04T02:15:00Z', is_self: false, ...over,
});
const PAGE: UsersPage = {
  items: [
    U({ id: 'u1', display_name: 'Anh Nguyễn Văn A (Chủ)', email: 'owner@genesis.local', role: { code: 'owner', name: 'Owner — Sếp' }, is_self: true }),
    U({ id: 'u2', display_name: 'Chị Lan Phạm', email: 'operator@genesis.local' }),
  ],
  roles: [],
};

type Handler = (method: string, url: string, body: unknown) => { status: number; body?: unknown } | undefined;
const calls: Array<{ method: string; url: string; body: unknown }> = [];
function stubApi(handler: Handler, meBody: unknown = me()) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ method, url, body });
    const r = url.endsWith('/auth/me') ? { status: 200, body: meBody } : handler(method, url, body) ?? { status: 404, body: { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' } };
    return new Response(JSON.stringify(r.body ?? {}), { status: r.status, headers: { 'Content-Type': r.status >= 400 ? 'application/problem+json' : 'application/json' } });
  }));
}
const wrap = (ui: ReactNode) =>
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );

beforeEach(() => {
  queryClient.clear();
  calls.length = 0;
  useToasts.setState({ toasts: [] });
  vi.stubGlobal('WebSocket', undefined);
});
afterEach(() => vi.unstubAllGlobals());

describe('usersModel', () => {
  it('trạng thái: khoá / chưa đăng nhập / chờ đổi mật khẩu / hoạt động', () => {
    expect(userStatus({ status: 'inactive', must_change_password: true, last_login_at: null }).label).toBe('Đã khoá');
    expect(userStatus({ status: 'active', must_change_password: true, last_login_at: null }).label).toBe('Chưa đăng nhập');
    expect(userStatus({ status: 'active', must_change_password: true, last_login_at: '2026-09-01T00:00:00Z' }).label).toBe('Chờ đổi mật khẩu');
    expect(userStatus({ status: 'active', must_change_password: false, last_login_at: null }).tone).toBe('ok');
  });
});

describe('UsersTab', () => {
  it('liệt kê người dùng; hàng của chính mình không có nút; mời → hiện mật khẩu tạm một lần', async () => {
    stubApi((m, url, body) => {
      if (m === 'GET' && url.endsWith('/users')) return { status: 200, body: PAGE };
      if (m === 'POST' && url.endsWith('/users')) {
        const b = body as { display_name: string; email: string; role: string };
        return { status: 201, body: { user: U({ id: 'u3', display_name: b.display_name, email: b.email, must_change_password: true, last_login_at: null }), temp_password: 'tam-1234567890' } };
      }
      return undefined;
    });
    const user = userEvent.setup();
    wrap(<UsersTab />);
    const table = await screen.findByRole('table');
    expect(within(table).getByText('Chị Lan Phạm')).toBeInTheDocument();
    expect(within(table).getByText('sửa ở Tài khoản của tôi')).toBeInTheDocument();
    expect(within(table).queryByLabelText('Vai trò của Anh Nguyễn Văn A (Chủ)')).toBeNull();
    expect(within(table).getByLabelText('Vai trò của Chị Lan Phạm')).toHaveValue('operator');

    await user.click(screen.getByRole('button', { name: 'Mời người dùng' }));
    const dlg = await screen.findByRole('dialog', { name: 'Mời người dùng' });
    await user.click(within(dlg).getByRole('button', { name: 'Mời' }));
    expect(await within(dlg).findByText('Nhập tên hiển thị.')).toBeInTheDocument();
    await user.type(within(dlg).getByLabelText('Tên hiển thị'), 'Anh Tuấn');
    await user.type(within(dlg).getByLabelText('Email đăng nhập'), 'Tuan@Genesis.local');
    await user.selectOptions(within(dlg).getByLabelText('Vai trò'), 'auditor');
    await user.click(within(dlg).getByRole('button', { name: 'Mời' }));
    const res = await screen.findByRole('dialog', { name: 'Đã mời Anh Tuấn' });
    expect(within(res).getByLabelText('Mật khẩu tạm')).toHaveTextContent('tam-1234567890');
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ display_name: 'Anh Tuấn', email: 'tuan@genesis.local', role: 'auditor' });
    await user.click(within(res).getByRole('button', { name: 'Đã gửi, đóng' }));
    expect(screen.queryByText('tam-1234567890')).toBeNull();
    expect(within(table).getByText('Anh Tuấn')).toBeInTheDocument();
  });

  it('đổi vai trò, khoá (hỏi lại), đặt lại mật khẩu; lỗi Owner cuối hiện rõ', async () => {
    stubApi((m, url, body) => {
      if (m === 'GET' && url.endsWith('/users')) return { status: 200, body: PAGE };
      if (m === 'PATCH' && url.endsWith('/users/u2/role')) return { status: 200, body: U({ id: 'u2', display_name: 'Chị Lan Phạm', role: { code: (body as { role: string }).role, name: 'Auditor · kiểm toán' } }) };
      if (m === 'POST' && url.endsWith('/users/u2/deactivate')) return { status: 409, body: { status: 409, code: 'LAST_OWNER', title: 'Đây là Owner cuối cùng — tổ chức phải luôn có ít nhất một Owner' } };
      if (m === 'POST' && url.endsWith('/users/u2/reset-password')) return { status: 200, body: { user: U({ id: 'u2', display_name: 'Chị Lan Phạm', must_change_password: true }), temp_password: 'moi-9876543210' } };
      return undefined;
    });
    const user = userEvent.setup();
    wrap(<UsersTab />);
    const select = await screen.findByLabelText('Vai trò của Chị Lan Phạm');
    await user.selectOptions(select, 'auditor');
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH' && (c.body as { role: string }).role === 'auditor')).toBe(true));
    await waitFor(() => expect(useToasts.getState().toasts[0]?.text).toContain('Kiểm soát'));

    await user.click(screen.getByRole('button', { name: 'Khoá Chị Lan Phạm' }));
    const confirm = await screen.findByRole('dialog', { name: 'Khoá tài khoản Chị Lan Phạm?' });
    await user.click(within(confirm).getByRole('button', { name: 'Khoá tài khoản' }));
    expect(await within(confirm).findByText(/Owner cuối cùng/)).toBeInTheDocument();
    await user.click(within(confirm).getByRole('button', { name: 'Huỷ' }));

    await user.click(screen.getByRole('button', { name: 'Đặt lại mật khẩu Chị Lan Phạm' }));
    const reset = await screen.findByRole('dialog', { name: 'Đặt lại mật khẩu cho Chị Lan Phạm?' });
    await user.click(within(reset).getByRole('button', { name: 'Đặt lại mật khẩu' }));
    const shown = await screen.findByRole('dialog', { name: 'Mật khẩu tạm mới cho Chị Lan Phạm' });
    expect(within(shown).getByLabelText('Mật khẩu tạm')).toHaveTextContent('moi-9876543210');
    expect(screen.getByText('Chờ đổi mật khẩu')).toBeInTheDocument();
  });

  it('vai trò không có roles.manage thấy thông báo khoá, không gọi /users', async () => {
    stubApi(() => undefined, me({ 'system.read': 'all' }, { code: 'auditor', name: 'Auditor · kiểm toán' }));
    wrap(<UsersTab />);
    expect(await screen.findByText('Chỉ Owner quản lý người dùng')).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith('/users'))).toBe(false);
  });
});

describe('OrgTab', () => {
  const ORG = { org_name: 'Genesis Trading', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND', self_name: 'Anh', bot_calls_me: 'Sếp', currencies: ['VND', 'USD'], can_edit: true };

  it('sửa tên + xưng hô, xem trước như bước 3, lưu gửi giá trị đã cắt khoảng trắng', async () => {
    stubApi((m, _url, body) => {
      if (m === 'GET') return { status: 200, body: ORG };
      if (m === 'PATCH') return { status: 200, body: { ...ORG, ...(body as object) } };
      return undefined;
    });
    const user = userEvent.setup();
    wrap(<OrgTab />);
    const name = await screen.findByLabelText('Tên tổ chức');
    expect(name).toHaveValue('Genesis Trading');
    const save = screen.getByRole('button', { name: 'Lưu thông tin tổ chức' });
    expect(save).toBeDisabled();
    await user.clear(screen.getByLabelText('Agent gọi Sếp là'));
    await user.type(screen.getByLabelText('Agent gọi Sếp là'), 'anh A');
    expect(screen.getByText(/Dạ Anh A, sáng nay/)).toBeInTheDocument();
    await user.clear(name);
    await user.type(name, '  Genesis Group ');
    await user.click(save);
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({
      org_name: 'Genesis Group', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND', self_name: 'Anh', bot_calls_me: 'anh A',
    }));
    await waitFor(() => expect(useToasts.getState().toasts[0]?.text).toBe('Đã lưu thông tin tổ chức.'));
  });

  it('lỗi 422 từ máy chủ hiện dưới đúng ô; vai trò không phải Owner chỉ xem', async () => {
    stubApi((m) => {
      if (m === 'GET') return { status: 200, body: ORG };
      return { status: 422, body: { status: 422, code: 'VALIDATION', title: 'Dữ liệu chưa hợp lệ', errors: { self_name: 'Nhập cách Sếp tự xưng' } } };
    });
    const user = userEvent.setup();
    wrap(<OrgTab />);
    await user.type(await screen.findByLabelText('Sếp tự xưng là'), 'x');
    await user.click(screen.getByRole('button', { name: 'Lưu thông tin tổ chức' }));
    expect(await screen.findByText('Nhập cách Sếp tự xưng')).toBeInTheDocument();
  });

  it('chỉ đọc khi can_edit=false', async () => {
    stubApi((m) => (m === 'GET' ? { status: 200, body: { ...ORG, can_edit: false } } : undefined), me({ 'system.read': 'all' }, { code: 'auditor', name: 'Auditor · kiểm toán' }));
    wrap(<OrgTab />);
    expect(await screen.findByLabelText('Tên tổ chức')).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Lưu thông tin tổ chức' })).toBeNull();
    expect(screen.getByText('Chỉ Owner sửa được thông tin tổ chức.')).toBeInTheDocument();
  });
});

describe('HelpPage', () => {
  it('hiện phiên bản, lệnh genh, Hướng dẫn thiết lập; Báo lỗi chép thông tin chẩn đoán', async () => {
    stubApi((_m, url) => (url.endsWith('/system/about') ? { status: 200, body: { version: 'v0.1.22', org_name: 'Genesis Trading', timezone: 'Asia/Ho_Chi_Minh', role: { code: 'owner', name: 'Owner — Sếp' } } } : undefined));
    const user = userEvent.setup(); // cài clipboard giả của user-event — thay SAU đó
    const writeText = vi.fn(async (_t: string) => {});
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    wrap(<HelpPage />);
    expect(await screen.findByText('v0.1.22')).toBeInTheDocument();
    for (const c of ['genh update', 'genh reset-password', 'genh trust-ca', 'genh backup']) expect(screen.getByText(c)).toBeInTheDocument();
    // v0.1.43 (F-30): ví dụ Gen ở Trợ giúp là câu hỏi việc thật, không còn "khoá Jev".
    expect(screen.getByText('“Khách nào hỏi giá hôm nay?”')).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/khoá Jev/i);
    expect(GENH_COMMANDS.length).toBeGreaterThanOrEqual(4);
    expect(await screen.findByRole('link', { name: /Mở Hướng dẫn thiết lập/ })).toHaveAttribute('href', '/guide');
    await user.click(screen.getByRole('button', { name: /Báo lỗi/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    const text = writeText.mock.calls[0][0];
    expect(text).toContain('Phiên bản: v0.1.22');
    expect(text).toContain('Vai trò: Owner — Sếp');
    expect(text).not.toMatch(/gh_session|csrf/i);
  });

  it('v0.1.28 (UX N9): Operator không thấy lệnh genh, Gen hay Hướng dẫn thiết lập — thấy cách nhờ Owner', async () => {
    const opMe = { ...me({ 'queue.read': 'all' }, { code: 'operator', name: 'Operator' }), features: { gen: false } };
    stubApi((_m, url) => (url.endsWith('/system/about') ? { status: 200, body: { version: 'v0.1.28', org_name: 'Genesis Trading', timezone: 'Asia/Ho_Chi_Minh', role: { code: 'operator', name: 'Operator' } } } : undefined), opMe);
    wrap(<HelpPage />);
    expect(await screen.findByText('Cần giúp về tài khoản')).toBeInTheDocument();
    expect(screen.getByText(/nhờ Owner vào Đội ngũ › Người dùng › Đặt lại mật khẩu/)).toBeInTheDocument();
    expect(screen.queryByText('genh update')).toBeNull();
    expect(screen.queryByText('Hỏi Gen')).toBeNull();
    expect(screen.queryByRole('link', { name: /Mở Hướng dẫn thiết lập/ })).toBeNull();
    expect(await screen.findByText('Vận hành')).toBeInTheDocument(); // tên vai trò tiếng Việt
  });

  it('withVersions: cùng chữ với thẻ Giới thiệu — "Phiên bản máy chủ" + "phiên bản công cụ cài đặt (genh)"', () => {
    const about = { version: 'v0.1.36', image_version: 'v0.1.36', genh_version: 'v0.1.35', org_name: 'G', timezone: 'Asia/Ho_Chi_Minh', role: { code: 'owner', name: 'Owner' } };
    const text = withVersions(diagnosticText(about as never, undefined, new Date('2026-10-02T00:00:00Z')), about as never);
    expect(text).toContain('Phiên bản máy chủ: v0.1.36 · phiên bản công cụ cài đặt (genh): v0.1.35');
    expect(text).not.toContain('Phiên bản ảnh');
    expect(GENH_COMMANDS.find((c) => c.cmd === 'genh status')?.what).not.toContain('ổ đĩa');
    // v0.1.44: genh stop cũng tạm dừng trực canh; thẻ Gói chẩn đoán/khối Trực canh bảo chạy genh doctor.
    expect(GENH_COMMANDS.find((c) => c.cmd === 'genh stop')?.what).toContain('trực canh máy chủ tạm nghỉ');
    expect(GENH_COMMANDS.map((c) => c.cmd)).toEqual(expect.arrayContaining(['genh doctor', 'genh watchdog status']));
  });

  it('diagnosticText: bản phát triển khi không có phiên bản', () => {
    expect(diagnosticText(undefined, undefined, new Date('2026-09-29T00:00:00Z'))).toContain('Phiên bản: bản phát triển');
  });
});
