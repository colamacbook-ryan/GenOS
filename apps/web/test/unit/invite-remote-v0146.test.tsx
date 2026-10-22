import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { AccessInfo, TempPasswordResult } from '@gen-harness/contracts';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';
import { TempPasswordDialog } from '../../src/screens/system/TempPasswordDialog';
import { RemoteAccessCard } from '../../src/screens/system/RemoteAccessCard';
import { inviteMessage, isLocalAddress } from '../../src/screens/system/usersModel';
import { COPY_FAILED_TEXT } from '../../src/screens/system/accessModel';
import { useToasts } from '../../src/lib/toast';

/**
 * v0.1.46 (F-21) — hộp mời dùng `login_url` của `GET /system/access` (không dùng window.location.origin), cảnh báo đỏ
 * khi địa chỉ chỉ mở được trên máy chủ, thẻ "Truy cập từ xa".
 */

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

const ACCESS_LOCAL: AccessInfo = {
  public_url: 'https://localhost:8443', login_url: 'https://localhost:8443/login', public_url_local: true, mode: 'local',
  bind_addr: '127.0.0.1', site_address: null, checked_at: null, can_manage: true,
};
const ACCESS_TS: AccessInfo = {
  public_url: 'https://gen.tail1234.ts.net', login_url: 'https://gen.tail1234.ts.net/login', public_url_local: false, mode: 'tailscale',
  bind_addr: '127.0.0.1', site_address: 'gen.tail1234.ts.net', checked_at: '2026-10-02T00:00:00Z', can_manage: true,
};

const RESULT = {
  user: { id: 'u2', email: 'lan@x.vn', display_name: 'Lan', role: { code: 'operator', name: 'Vận hành' } },
  temp_password: 'abcd-efgh-jkmn',
} as unknown as TempPasswordResult;

const me = (role: string) => ({
  id: 'u', email: 'x@genesis.local', display_name: 'Anh Cơ', role: { code: role, name: role },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
  addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null,
  permissions: { 'system.read': 'all', 'system.manage': role === 'owner' ? 'all' : 'none' },
});

function stubAccess(reply: () => Response) {
  vi.stubGlobal('fetch', vi.fn(async () => reply()));
}

function renderDialog() {
  return render(
    <QueryClientProvider client={queryClient}>
      <TempPasswordDialog title="Đã mời Lan" result={RESULT} onClose={() => {}} />
    </QueryClientProvider>,
  );
}

beforeEach(() => queryClient.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  queryClient.clear();
});

describe('isLocalAddress', () => {
  it.each(['localhost', 'https://localhost:8443', '127.0.0.5', 'http://127.0.0.5:80', '::1', 'https://[::1]:8443', '0.0.0.0', 'foo.localhost', '', '   '])('%s → local', (u) => {
    expect(isLocalAddress(u)).toBe(true);
  });
  it.each(['gen.tail1234.ts.net', 'https://gen.tail1234.ts.net', '192.168.1.20', 'https://192.168.1.20:8443', 'https://localhost.example.com'])('%s → từ xa', (u) => {
    expect(isLocalAddress(u)).toBe(false);
  });
});

describe('inviteMessage', () => {
  it('có dòng Địa chỉ khi có login_url; bỏ dòng khi null', () => {
    expect(inviteMessage(RESULT, 'https://gen.tail1234.ts.net/login')).toContain('Địa chỉ: https://gen.tail1234.ts.net/login');
    const none = inviteMessage(RESULT, null);
    expect(none).not.toContain('Địa chỉ:');
    expect(none).toContain('Email: lan@x.vn');
  });
});

describe('TempPasswordDialog', () => {
  it('địa chỉ localhost → cảnh báo đỏ role=alert mở đầu đúng câu, lệnh trong <code>; vẫn giữ nút/aria cũ', async () => {
    stubAccess(() => json(200, ACCESS_LOCAL));
    renderDialog();
    const warn = await screen.findByTestId('invite-local-warning');
    expect(warn).toHaveAttribute('role', 'alert');
    expect(warn.textContent).toMatch(/^Địa chỉ này chỉ mở được trên chính máy chủ — nhân viên ở máy khác hoặc điện thoại sẽ KHÔNG vào được\. Trên máy chủ chạy genh remote tailscale \(khuyên dùng\) hoặc genh remote --lan, rồi bấm Chép lời nhắn lại\.$/);
    expect(within(warn).getByText('genh remote tailscale').tagName).toBe('CODE');
    expect(screen.getByLabelText('Mật khẩu tạm')).toHaveTextContent('abcd-efgh-jkmn');
    expect(screen.getByRole('button', { name: /Chép lời nhắn gửi nhân viên/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Đã gửi, đóng' })).toBeInTheDocument();
  });

  it('địa chỉ Tailscale → không cảnh báo; lời nhắn chép chứa login_url, không chứa window.location.origin', async () => {
    stubAccess(() => json(200, ACCESS_TS));
    const writeText = vi.fn(async () => {});
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    renderDialog();
    const btn = screen.getByRole('button', { name: /Chép lời nhắn gửi nhân viên/ });
    await waitFor(() => expect(btn).toBeEnabled());
    fireEvent.click(btn);
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(screen.queryByTestId('invite-local-warning')).toBeNull();
    const msg = String((writeText.mock.calls[0] as unknown[])[0]);
    expect(msg).toContain('Địa chỉ: https://gen.tail1234.ts.net/login');
    expect(msg).not.toContain(window.location.origin);
    expect(msg).toContain('abcd-efgh-jkmn');
  });

  it('Owner chạy genh remote rồi bấm chép lại → lời nhắn lấy địa chỉ MỚI (không dùng bộ nhớ đệm), cảnh báo đỏ tắt', async () => {
    let current: AccessInfo = ACCESS_LOCAL;
    stubAccess(() => json(200, current));
    const writeText = vi.fn(async () => {});
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    renderDialog();
    await screen.findByTestId('invite-local-warning');
    const btn = screen.getByRole('button', { name: /Chép lời nhắn gửi nhân viên/ });
    fireEvent.click(btn);
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(String((writeText.mock.calls[0] as unknown[])[0])).toContain('Địa chỉ: https://localhost:8443/login');
    current = ACCESS_TS; // Owner vừa chạy `genh remote tailscale` trên máy chủ
    fireEvent.click(screen.getByRole('button', { name: /Đã chép|Chép lời nhắn gửi nhân viên/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(2));
    expect(String((writeText.mock.calls[1] as unknown[])[0])).toContain('Địa chỉ: https://gen.tail1234.ts.net/login');
    await waitFor(() => expect(screen.queryByTestId('invite-local-warning')).toBeNull());
  });

  it('Safari: địa chỉ localhost → clipboard.write gọi ĐỒNG BỘ trong lượt bấm (không chờ mạng), nội dung lấy địa chỉ MỚI', async () => {
    let current: AccessInfo = ACCESS_LOCAL;
    stubAccess(() => json(200, current));
    const items: Array<Record<string, Promise<Blob>>> = [];
    class FakeClipboardItem {
      constructor(data: Record<string, Promise<Blob>>) {
        items.push(data);
      }
    }
    vi.stubGlobal('ClipboardItem', FakeClipboardItem);
    const write = vi.fn(async () => {});
    const writeText = vi.fn(async () => {});
    vi.stubGlobal('navigator', { ...navigator, clipboard: { write, writeText } });
    renderDialog();
    await screen.findByTestId('invite-local-warning');
    current = ACCESS_TS; // Owner vừa chạy `genh remote tailscale`; bộ nhớ đệm còn localhost
    fireEvent.click(screen.getByRole('button', { name: /Chép lời nhắn gửi nhân viên/ }));
    // Ngay trong handler bấm (chưa có await nào): đã gọi clipboard.write — Safari giữ quyền chép.
    expect(write).toHaveBeenCalledTimes(1);
    expect(writeText).not.toHaveBeenCalled();
    const blob = await items[0]['text/plain'];
    // jsdom chưa có Blob.text() — đọc bằng FileReader.
    const text = await new Promise<string>((resolve) => {
      const r = new FileReader();
      r.onload = () => resolve(String(r.result));
      r.readAsText(blob);
    });
    expect(text).toContain('Địa chỉ: https://gen.tail1234.ts.net/login');
    expect(text).toContain('abcd-efgh-jkmn');
    await screen.findByRole('button', { name: /Đã chép/ });
  });

  it('trình duyệt không nhận Promise trong ClipboardItem → rơi về writeText với địa chỉ mới', async () => {
    stubAccess(() => json(200, ACCESS_LOCAL));
    vi.stubGlobal('ClipboardItem', class {});
    const write = vi.fn(async () => {
      throw new TypeError('không hỗ trợ');
    });
    const writeText = vi.fn(async () => {});
    vi.stubGlobal('navigator', { ...navigator, clipboard: { write, writeText } });
    renderDialog();
    await screen.findByTestId('invite-local-warning');
    fireEvent.click(screen.getByRole('button', { name: /Chép lời nhắn gửi nhân viên/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(String((writeText.mock.calls[0] as unknown[])[0])).toContain('Địa chỉ: https://localhost:8443/login');
    await screen.findByRole('button', { name: /Đã chép/ });
  });

  it('không có navigator.clipboard (trang không HTTPS) → toast "Không chép được", không ném lỗi', async () => {
    stubAccess(() => json(200, ACCESS_TS));
    vi.stubGlobal('navigator', { ...navigator, clipboard: undefined });
    renderDialog();
    const btn = screen.getByRole('button', { name: /Chép lời nhắn gửi nhân viên/ });
    await waitFor(() => expect(btn).toBeEnabled());
    fireEvent.click(btn);
    await waitFor(() => expect(useToasts.getState().toasts.map((t) => t.text)).toContain(COPY_FAILED_TEXT));
    expect(screen.queryByRole('button', { name: /Đã chép/ })).toBeNull();
  });

  it('lỗi tải access → cảnh báo đỏ + Chi tiết kỹ thuật, không render object; lời nhắn bỏ dòng địa chỉ', async () => {
    stubAccess(() => json(409, { code: 'ACCESS_DOWN', title: 'Không đọc được địa chỉ' }));
    const writeText = vi.fn(async () => {});
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    const { container } = renderDialog();
    const warn = await screen.findByTestId('invite-local-warning');
    expect(warn).toHaveAttribute('role', 'alert');
    expect(warn.textContent).toContain('Chưa đọc được địa chỉ đăng nhập — gửi kèm địa chỉ Console mà nhân viên mở được');
    expect(within(warn).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(container.innerHTML).not.toContain('[object Object]');
    expect(document.body.innerHTML).not.toContain('[object Object]');
    fireEvent.click(screen.getByRole('button', { name: /Chép lời nhắn gửi nhân viên/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(String((writeText.mock.calls[0] as unknown[])[0])).not.toContain('Địa chỉ:');
  });
});

describe('RemoteAccessCard', () => {
  function renderCard(role: string, access: AccessInfo) {
    queryClient.setQueryData(qk.me, me(role));
    stubAccess(() => json(200, access));
    return render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/system?tab=storage&focus=access']}>
          <RemoteAccessCard />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it('Owner: thấy chế độ, địa chỉ đăng nhập và các lệnh genh remote chép được', async () => {
    renderCard('owner', ACCESS_TS);
    const card = await screen.findByRole('region', { name: 'Truy cập từ xa' });
    expect(await within(card).findByText('Tailscale')).toBeInTheDocument();
    expect(within(card).getByText('https://gen.tail1234.ts.net/login')).toBeInTheDocument();
    expect(within(card).getByText('genh remote tailscale')).toBeInTheDocument();
    expect(within(card).getByText('genh remote --local')).toBeInTheDocument();
    expect(within(card).getByText('genh remote --lan')).toBeInTheDocument();
    expect(within(card).getByText('genh remote cloudflare --hostname <tên-miền>')).toBeInTheDocument();
  });

  it('người không phải Owner: không thấy lệnh, chỉ câu nhờ Owner', async () => {
    renderCard('auditor', { ...ACCESS_TS, can_manage: false });
    const card = await screen.findByRole('region', { name: 'Truy cập từ xa' });
    expect(await within(card).findByText('Nhờ Owner chọn cách truy cập từ xa.')).toBeInTheDocument();
    expect(within(card).queryByText(/genh remote/)).toBeNull();
  });

  it('lan_legacy: hiện nhãn "Đang mở cho cả mạng (bản cài cũ)" và cảnh báo', async () => {
    renderCard('owner', { ...ACCESS_LOCAL, mode: 'lan_legacy', bind_addr: '0.0.0.0', public_url_local: false });
    const card = await screen.findByRole('region', { name: 'Truy cập từ xa' });
    expect(await within(card).findByText('Đang mở cho cả mạng (bản cài cũ)')).toBeInTheDocument();
    expect(within(card).getByRole('alert')).toHaveTextContent('Mọi máy cùng mạng');
  });

  it('Cloudflare: nút Chép không chép chỗ trống <tên-miền> (dán vào bash `<` là chuyển hướng, lệnh hỏng)', async () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    renderCard('owner', ACCESS_TS);
    const card = await screen.findByRole('region', { name: 'Truy cập từ xa' });
    fireEvent.click(await within(card).findByRole('button', { name: 'Chép lệnh genh remote cloudflare --hostname <tên-miền>' }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    const copied = String((writeText.mock.calls[0] as unknown[])[0]);
    expect(copied).toBe('genh remote cloudflare --hostname ');
    expect(copied).not.toContain('<');
    expect(within(card).getByText(/gõ tiếp tên miền, ví dụ gen\.congty\.vn/)).toBeInTheDocument();
  });

  it('chế độ "Chưa rõ" (thiếu run/network-status.json): Owner được chỉ chạy genh remote status', async () => {
    renderCard('owner', { ...ACCESS_LOCAL, mode: 'unknown' });
    const card = await screen.findByRole('region', { name: 'Truy cập từ xa' });
    expect(await within(card).findByText('Chưa rõ')).toBeInTheDocument();
    expect(within(card).getByTestId('access-unknown')).toHaveTextContent('chạy genh remote status trên máy chủ để cập nhật');
  });

  it('lỗi tải → CardError, không render object', async () => {
    queryClient.setQueryData(qk.me, me('owner'));
    stubAccess(() => json(409, { code: 'ACCESS_DOWN', title: 'Không đọc được địa chỉ' }));
    const { container } = render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <RemoteAccessCard />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const card = await screen.findByRole('region', { name: 'Truy cập từ xa' });
    await within(card).findByText(/Chi tiết kỹ thuật/);
    expect(container.innerHTML).not.toContain('[object Object]');
  });
});
