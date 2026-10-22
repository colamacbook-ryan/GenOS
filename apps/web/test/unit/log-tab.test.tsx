import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { LogTab } from '../../src/screens/system/LogTab';
import { SystemScreen } from '../../src/screens/system/SystemScreen';
import { actionLabel, actorLabel, fold, localDayStartIso, matchingActionCodes, matchingActorIds } from '../../src/screens/system/auditLabels';
import { qk } from '../../src/lib/queries';
import { useUrlStateStore } from '../../src/lib/uiStore';

/** Nhật ký hành động (A8: F099 xem hết + tìm phía máy chủ, F105 câu tiếng Việt). */

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function row(i: number, over: Record<string, unknown> = {}) {
  return {
    id: `id-${i}`, at: '2026-09-20T08:00:00Z', actor_type: 'user', actor_id: `user:${i}`, actor_label: 'Anh Cơ',
    action: 'provider.model_set', target_type: 'provider', target_id: 'p1', target_label: `Nhà cung cấp ${i}`,
    autonomy_level: null, result: 'ok', detail: null, ...over,
  };
}

const requests: URL[] = [];
function mockAudit(handler: (u: URL) => unknown) {
  requests.length = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL) => {
      const u = new URL(String(url), 'http://x');
      if (u.pathname.endsWith('/audit')) {
        requests.push(u);
        return json(200, handler(u));
      }
      return json(404);
    }),
  );
}

const ME = {
  id: 'u', email: 'o@x.vn', display_name: 'Anh Cơ', role: { code: 'owner', name: 'Owner — Sếp' },
  org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
  pin_verified_until: null, permissions: { 'system.read': 'all', 'audit.read': 'all', 'data.manage': 'all' },
};
function renderLog(ui = <LogTab />) {
  useUrlStateStore.setState({ params: { tab: 'log' } });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(qk.me, ME);
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

describe('Nhật ký hành động › câu tiếng Việt (F105)', () => {
  it('không in mã máy: hành động, tác nhân hệ thống và dòng chung đều thành câu cho Sếp đọc', async () => {
    mockAudit(() => ({
      items: [
        row(1),
        row(2, { actor_type: 'system', actor_id: 'system:worker', actor_label: 'system:worker', action: 'draft.sent', target_label: null }),
        row(3, { actor_type: 'system', actor_id: 'bridge:zalo', actor_label: 'bridge:zalo', action: 'message.sent', target_label: null }),
        row(4, { action: 'user.role_changed', detail: { from: 'operator', to: 'auditor' } }),
        row(5, { action: 'http.post', target_type: 'route', target_id: '/api/v1/vi-du', target_label: null }),
        row(6, { action: 'mot.hanh_dong_moi_chua_co_nhan' }),
      ],
      next_cursor: null,
    }));
    renderLog();
    await screen.findByText('Chọn mô hình AI');
    expect(screen.getAllByText('Hệ thống').length).toBeGreaterThan(0);
    expect(screen.getByText('Bộ nhận tin Zalo')).toBeInTheDocument();
    expect(screen.getByText('Đổi vai trò từ Vận hành sang Kiểm soát')).toBeInTheDocument();
    expect(screen.getByText('Thao tác chung (chưa có tên riêng)')).toBeInTheDocument();
    expect(screen.getByText('/api/v1/vi-du')).toBeInTheDocument();
    expect(screen.getByText('Thao tác khác')).toBeInTheDocument();
    const text = document.body.textContent ?? '';
    for (const raw of ['provider.model_set', 'system:worker', 'bridge:zalo', 'http.post', 'user.role_changed']) {
      expect(text).not.toContain(raw);
    }
    // Mã gốc vẫn tra được: nằm ở tooltip của ô.
    expect(screen.getByText('Chọn mô hình AI')).toHaveAttribute('title', 'provider.model_set');
  });

  it('đầu tab và ô trống không còn tiếng Anh; nhãn tab không còn "30 ngày"', async () => {
    mockAudit(() => ({ items: [], next_cursor: null }));
    renderLog(<SystemScreen />);
    await screen.findByText('Nhật ký hành động');
    const text = document.body.textContent ?? '';
    expect(text).not.toMatch(/Action log|Auditor|Manager|30 ngày/);
    expect(screen.getByRole('tab', { name: /Nhật ký/ })).not.toHaveTextContent('30 ngày');
  });

  it('bảng nhãn: mẫu theo vai trò, nhãn nhóm cho mã mới, tìm không dấu', () => {
    expect(actionLabel('user.role_changed', { from: 'manager', to: 'owner' })).toBe('Đổi vai trò từ Quản lý sang Owner — Sếp');
    expect(actionLabel('user.role_changed')).toBe('Đổi vai trò của một người');
    expect(actionLabel('provider.mot_viec_moi')).toBe('Nhà cung cấp AI — thao tác khác');
    expect(actionLabel('xyz.abc')).toBe('Thao tác khác');
    // Gói R: gắn/gỡ MODEL cho agent (không phải gắn vào nhóm chat); thao tác BOT hàng loạt có nhãn riêng
    expect(actionLabel('channel.new_group_mode_reset')).toBe('Nhóm mới của kênh chat về "Lắng nghe im lặng" (đổi tài khoản)');
    expect(actionLabel('agent.bound')).toBe('Gắn model cho agent');
    expect(actionLabel('agent.unbound')).toBe('Gỡ model khỏi agent');
    expect(actionLabel('directory.bulk_bot_set')).toBe('Chọn bộ nhận tin hàng loạt cho nhiều người');
    expect(actorLabel({ actor_type: 'agent', actor_id: 'agent:1', actor_label: 'agent:1' })).toBe('Agent (đã xoá)');
    expect(actorLabel({ actor_type: 'user', actor_id: 'user:1', actor_label: 'Anh Cơ' })).toBe('Anh Cơ');
    expect(fold('Đăng nhập')).toBe('dang nhap');
    expect(matchingActionCodes('dang nhap')).toEqual(expect.arrayContaining(['auth.login', 'auth.login_failed']));
    expect(matchingActorIds('bộ nhận tin')).toEqual(expect.arrayContaining(['bridge:zalo']));
    expect(matchingActionCodes('')).toEqual([]);
  });

  it('ngày lọc theo múi giờ tổ chức: 20/09 giờ Việt Nam = 17:00 UTC hôm trước', () => {
    expect(localDayStartIso('2026-09-20', 'Asia/Ho_Chi_Minh')).toBe('2026-09-19T17:00:00.000Z');
    expect(localDayStartIso('2026-09-20', 'Asia/Ho_Chi_Minh', 1)).toBe('2026-09-20T17:00:00.000Z');
    expect(localDayStartIso('2026-09-20', 'UTC')).toBe('2026-09-20T00:00:00.000Z');
    expect(localDayStartIso('hỏng', 'UTC')).toBeUndefined();
  });
});

describe('Nhật ký hành động › xem hết, tìm phía máy chủ (F099)', () => {
  it('"Xem thêm" nạp trang kế bằng cursor và nối vào bảng; hết trang thì nút biến mất', async () => {
    mockAudit((u) =>
      u.searchParams.get('cursor') === 'c1'
        ? { items: [row(51), row(52)], next_cursor: null }
        : { items: [row(1), row(2)], next_cursor: 'c1' },
    );
    const user = userEvent.setup();
    renderLog();
    await screen.findByText('Nhà cung cấp 1');
    expect(requests[0]?.searchParams.get('limit')).toBe('50');
    expect(screen.getByText('Đang hiện 2 dòng mới nhất')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Xem thêm' }));
    await screen.findByText('Nhà cung cấp 52');
    expect(screen.getByText('Nhà cung cấp 1')).toBeInTheDocument();
    expect(requests.some((u) => u.searchParams.get('cursor') === 'c1')).toBe(true);
    expect(screen.queryByRole('button', { name: 'Xem thêm' })).toBeNull();
    expect(screen.getByText('Đã hiện hết — 4 dòng')).toBeInTheDocument();
  });

  it('ô tìm gửi q + mã hành động có nhãn tiếng Việt khớp lên máy chủ, không lọc tại máy', async () => {
    mockAudit((u) => ({ items: u.searchParams.get('q') ? [row(9, { action: 'auth.login' })] : [row(1), row(2)], next_cursor: null }));
    const user = userEvent.setup();
    renderLog();
    await screen.findByText('Nhà cung cấp 1');
    await user.type(screen.getByLabelText('Tìm trong nhật ký'), 'đăng nhập');
    await screen.findByText('Đăng nhập');
    const last = requests[requests.length - 1] as URL;
    expect(last.searchParams.get('q')).toBe('đăng nhập');
    expect(last.searchParams.get('q_actions')?.split(',')).toEqual(expect.arrayContaining(['auth.login']));
    expect(screen.queryByText('Nhà cung cấp 1')).toBeNull();
    // Không gửi một yêu cầu cho mỗi phím: có trễ ngắn trước khi hỏi máy chủ.
    expect(requests.filter((u) => u.searchParams.get('q')).length).toBe(1);
  });

  it('mặc định ẩn dòng hệ thống (hide_noise); bật "Hiện cả dòng hệ thống" thì bỏ tham số', async () => {
    mockAudit(() => ({ items: [row(1)], next_cursor: null }));
    const user = userEvent.setup();
    renderLog();
    await screen.findByText('Nhà cung cấp 1');
    expect(requests[0]?.searchParams.get('hide_noise')).toBe('true');
    await user.click(screen.getByRole('checkbox', { name: /Hiện cả dòng hệ thống/ }));
    await waitFor(() => expect(requests.some((u) => !u.searchParams.has('hide_noise'))).toBe(true));
  });

  it('lọc ngày gửi since/until theo múi giờ tổ chức; đổi loại tác nhân gửi actor_type', async () => {
    mockAudit(() => ({ items: [row(1)], next_cursor: null }));
    const user = userEvent.setup();
    renderLog();
    await screen.findByText('Nhà cung cấp 1');
    const from = screen.getByLabelText('Từ ngày');
    const to = screen.getByLabelText('Đến hết ngày');
    await user.type(from, '2026-09-20');
    await user.type(to, '2026-09-20');
    await waitFor(() => {
      const last = requests[requests.length - 1] as URL;
      expect(last.searchParams.get('since')).toBe('2026-09-19T17:00:00.000Z');
      expect(last.searchParams.get('until')).toBe('2026-09-20T17:00:00.000Z');
    });
    await user.selectOptions(screen.getByLabelText('Lọc theo loại tác nhân'), 'agent');
    await waitFor(() => expect((requests[requests.length - 1] as URL).searchParams.get('actor_type')).toBe('agent'));
    await user.click(screen.getByRole('button', { name: 'Bỏ lọc ngày' }));
    await waitFor(() => expect((requests[requests.length - 1] as URL).searchParams.has('since')).toBe(false));
  });

  it('trống thì nói rõ: dòng hệ thống đang ẩn', async () => {
    mockAudit(() => ({ items: [], next_cursor: null }));
    renderLog();
    await screen.findByText('Chưa có hành động nào');
    expect(screen.getByText(/Các dòng hệ thống đang ẩn/)).toBeInTheDocument();
  });
});
