import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import type { SystemUpdate } from '@gen-harness/contracts';
import { UpdateCard } from '../../src/update/UpdateCard';
import { autoInstallHint, readableNotes, updateView } from '../../src/update/updateModel';
import { queryClient } from '../../src/lib/queryClient';

const base: SystemUpdate = {
  current: 'v0.1.16', latest: 'v0.1.17', update_available: true, updater: 'systemd', linked: true, can_request: true,
  state: 'idle', message: null, from: null, to: null, started_at: null, finished_at: null, requested_at: null,
  release_url: null, release_notes: '- Nút cập nhật',
};
const NOW = Date.parse('2026-09-28T10:00:00Z');

describe('updateView', () => {
  it('ẩn khi đã mới nhất, hiện "Có bản mới" khi có', () => {
    expect(updateView({ ...base, latest: 'v0.1.16', update_available: false }, { waitingFor: null, offline: false }).kind).toBe('hidden');
    const v = updateView(base, { waitingFor: null, offline: false });
    expect(v.kind).toBe('available');
    expect(v.kind !== 'hidden' && v.title).toBe('Có bản mới v0.1.17');
  });
  it('máy chủ chưa có watcher → hiện lệnh chạy tay thay vì nút', () => {
    const v = updateView({ ...base, can_request: false, updater: null }, { waitingFor: null, offline: false });
    expect(v.kind !== 'hidden' && v.showCommand).toBe(true);
  });
  it('đang cập nhật: requested → running → api tắt để khởi động lại → xong', () => {
    const steps = (d: SystemUpdate | undefined, offline = false) => {
      const v = updateView(d, { waitingFor: 'v0.1.17', offline, now: NOW });
      return v.kind === 'working' ? v.steps.map((s) => s.state) : v.kind;
    };
    expect(steps({ ...base, state: 'requested' })).toEqual(['active', 'todo', 'todo']);
    expect(steps({ ...base, state: 'running' })).toEqual(['done', 'active', 'todo']);
    expect(steps(undefined, true)).toEqual(['done', 'done', 'active']);
    expect(steps({ ...base, state: 'done', current: 'v0.1.17', update_available: false })).toBe('finished');
  });
  it('lỗi gần đây báo đã quay về bản cũ; yêu cầu treo quá lâu cho thử lại', () => {
    const failed = updateView({ ...base, state: 'failed', message: 'Pull lỗi', finished_at: '2026-09-28T09:00:00Z' }, { waitingFor: null, offline: false, now: NOW });
    expect(failed.kind).toBe('failed');
    expect(failed.kind !== 'hidden' && failed.kicker).toMatch(/quay về bản đang dùng/);
    const old = updateView({ ...base, state: 'failed', finished_at: '2026-09-20T09:00:00Z' }, { waitingFor: null, offline: false, now: NOW });
    expect(old.kind).toBe('available');
    expect(updateView({ ...base, state: 'stalled' }, { waitingFor: null, offline: false }).kind).toBe('stalled');
  });
  // v0.1.69 (review P2-1): lịch đêm 03:00 + trễ ngẫu nhiên tới 30 phút ⇒ lần thử kế có thể đến sau mốc 24 giờ của lần lỗi
  // trước; hạn 26 giờ (khớp UPDATE_FAILED_RECENT của API) để thẻ/chuông không đóng rồi mở lại giữa hai đêm.
  it('lỗi cách 25 giờ vẫn là lỗi (khe lịch đêm), quá 26 giờ mới thôi báo', () => {
    const at = (hours: number) => new Date(NOW - hours * 3600 * 1000).toISOString();
    const view = (hours: number) => updateView({ ...base, state: 'failed', message: 'Pull lỗi', finished_at: at(hours) }, { waitingFor: null, offline: false, now: NOW });
    expect(view(25).kind).toBe('failed');
    expect(view(27).kind).toBe('available');
  });

  // v0.1.34: genh ghi "<việc> — <cách xử lý> (GH-E9xx)" — lời dẫn theo mã, nguyên văn vào "Chi tiết kỹ thuật".
  const failedAt = (message: string, over: Partial<SystemUpdate> = {}) =>
    updateView({ ...base, state: 'failed', message, finished_at: '2026-09-28T09:00:00Z', ...over }, { waitingFor: null, offline: false, now: NOW });
  it('GH-E948 ổ đĩa đầy: chưa đụng gì, Owner phải dọn đĩa; chi tiết giữ số GB + mã', () => {
    const msg = 'Ổ đĩa không đủ chỗ để tải bản mới — DỪNG LẠI, chưa đụng gì (còn 1.0 GB trống tại /var/lib/docker, cần tối thiểu 5 GB) — Giải phóng ổ đĩa (xem docker system df), rồi chạy lại genh update — lịch đêm cũng sẽ tự thử lại. (GH-E948)';
    const v = failedAt(msg);
    if (v.kind !== 'failed') throw new Error(v.kind);
    expect(v.tone).toBe('warn');
    expect(v.kicker).toMatch(/Ổ đĩa máy chủ sắp đầy — chưa đụng gì/);
    expect(v.kicker).not.toMatch(/quay về/);
    expect(v.body).toMatch(/giải phóng ổ đĩa/i);
    expect(v.detail).toBe(msg);
  });
  it('GH-E941 tải lỗi: chưa đụng gì, không nói "đã quay về"', () => {
    const v = failedAt('Tải bản mới thất bại sau 3 lần thử — CHƯA đụng gì — Kiểm kết nối mạng. (GH-E941)');
    if (v.kind !== 'failed') throw new Error(v.kind);
    expect(v.kicker).toBe('Chưa đụng gì — bản đang dùng vẫn chạy bình thường');
    expect(v.detail).toMatch(/GH-E941/);
  });
  it('quay về bản cũ CŨNG thất bại: tông đỏ, "Cần xử lý tay", không nói dữ liệu giữ nguyên', () => {
    const v = failedAt('migrate lỗi — ROLLBACK TỰ ĐỘNG CŨNG THẤT BẠI — ROLLBACK TỰ ĐỘNG THẤT BẠI, cần can thiệp tay ngay: khôi phục backups/k.enc lỗi (GH-E945)');
    if (v.kind !== 'failed') throw new Error(v.kind);
    expect(v.tone).toBe('bad');
    expect(v.kicker).toMatch(/Cần xử lý tay/);
    expect(v.kicker).not.toMatch(/dữ liệu giữ nguyên/);
  });
  it('GH-E945 quay về ổn: lời dẫn "đã tự quay về"; tiêu đề theo bản ĐÃ THỬ (to), không theo latest', () => {
    const v = failedAt('ready lỗi — đã tự quay về bản cũ (khôi phục bản sao lưu) — Rollback đã hoàn tất tự động (GH-E945)', {
      latest: 'v0.1.18', to: 'v0.1.17',
    });
    if (v.kind !== 'failed') throw new Error(v.kind);
    expect(v.title).toBe('Cập nhật lên v0.1.17 chưa thành công');
    expect(v.kicker).toMatch(/đã tự quay về bản đang dùng/);
    expect(v.body).toMatch(/lịch đêm sẽ không tự cài lại/);
  });
  it('quay về CŨNG thất bại: không nói "có tên bản sao lưu cần khôi phục" (CSDL có thể chưa bị đụng)', () => {
    const v = failedAt('ready lỗi — CSDL chưa bị đụng, NHƯNG khởi động lại bằng bản cũ chưa trọn — chạy tay docker compose up -d --remove-orphans. (GH-E945)');
    if (v.kind !== 'failed') throw new Error(v.kind);
    expect(v.kicker).toMatch(/Cần xử lý tay/);
    expect(v.body).not.toMatch(/bản sao lưu/);
    expect(v.body).toMatch(/Chi tiết kỹ thuật/);
  });
  it('rollback_failed có cấu trúc (api) thắng chữ trong thông điệp — genh đổi câu chữ vẫn báo đúng', () => {
    const msg = 'ready lỗi — một câu chữ mới hoàn toàn (GH-E945)';
    const v = failedAt(msg, { to: 'v0.1.17', blocked_version: 'v0.1.17', blocked_rollback_failed: true });
    if (v.kind !== 'failed') throw new Error(v.kind);
    expect(v.tone).toBe('bad');
    expect(v.kicker).toMatch(/Cần xử lý tay/);
    // Bị chặn bản KHÁC: không áp cờ của bản đó.
    const other = failedAt(msg, { to: 'v0.1.17', blocked_version: 'v0.1.15', blocked_rollback_failed: true });
    expect(other.kind === 'failed' && other.kicker).not.toMatch(/Cần xử lý tay/);
  });
  it('máy chủ chưa nhận yêu cầu từ nút bấm (can_request=false): không nhắc "bấm Thử lại", hiện lệnh chạy tay', () => {
    for (const msg of [
      'Ổ đĩa không đủ chỗ — DỪNG LẠI, chưa đụng gì (GH-E948)',
      'Tải bản mới thất bại — CHƯA đụng gì (GH-E941)',
      'ready lỗi — đã tự quay về bản cũ (khôi phục bản sao lưu) (GH-E945)',
      'Lỗi lạ (GH-E942)',
    ]) {
      const v = failedAt(msg, { can_request: false, updater: null });
      if (v.kind !== 'failed') throw new Error(v.kind);
      expect(v.body).not.toMatch(/Thử lại/);
      expect(v.body).toMatch(/chạy lệnh bên dưới trên máy chủ/i);
      expect(v.showCommand).toBe(true);
    }
    const withButton = failedAt('Tải bản mới thất bại — CHƯA đụng gì (GH-E941)');
    expect(withButton.kind === 'failed' && withButton.showCommand).toBe(false);
    expect(withButton.kind === 'failed' && withButton.body).toMatch(/bấm Thử lại/);
  });
  it('GH-E946 sau khi cập nhật xong: bản mới ĐANG chạy — không nói "đã tự quay về"', () => {
    const v = failedAt('Cập nhật xong, dịch vụ đã sẵn sàng, NHƯNG chép dữ liệu đã di trú vào volume gh_objects thất bại — Dữ liệu THÔ vẫn còn nguyên (GH-E946)');
    if (v.kind !== 'failed') throw new Error(v.kind);
    expect(v.tone).toBe('warn');
    expect(v.kicker).toMatch(/Bản mới đã chạy/);
    expect(v.kicker).not.toMatch(/quay về/);
  });
  it('GH-E900/GH-E901 và mã chưa có lời dẫn riêng: không nói "đã tự quay về — dữ liệu giữ nguyên"', () => {
    const e901 = failedAt('Không đọc được compose.yaml hiện tại — DỪNG LẠI, chưa đụng gì (GH-E901)');
    expect(e901.kind === 'failed' && e901.kicker).toBe('Chưa đụng gì — bản đang dùng vẫn chạy bình thường');
    const e900 = failedAt('Chưa cài đặt — thiếu bí mật (GH-E900)');
    expect(e900.kind === 'failed' && e900.kicker).toMatch(/Chưa đụng gì/);
    const other = failedAt('Lỗi lạ (GH-E942)');
    if (other.kind !== 'failed') throw new Error(other.kind);
    expect(other.kicker).toBe('Cập nhật chưa xong — xem Chi tiết kỹ thuật');
    expect(other.tone).toBe('warn');
    // E947 đã tự quay về: vẫn báo quay về.
    const e947 = failedAt('Đồng bộ compose.yaml lỗi — CSDL chưa bị đụng, đã tự quay về bản cũ và khởi động lại (GH-E947)');
    expect(e947.kind === 'failed' && e947.kicker).toMatch(/đã tự quay về bản đang dùng/);
  });
  it('"Có bản mới" mà bản đó đang bị chặn: không hứa "Tự cài đêm", nói rõ phải bấm để thử lại', () => {
    const v = updateView({ ...base, auto_update_enabled: true, published_at: '2026-09-20T08:00:00Z', blocked_version: 'v0.1.17' }, { waitingFor: null, offline: false, now: NOW });
    if (v.kind !== 'available') throw new Error(v.kind);
    expect(v.kicker).not.toMatch(/Tự cài/);
    expect(v.kicker).toMatch(/lịch đêm không tự cài lại — bấm Cập nhật ngay để thử lại/);
    // Bị chặn bản KHÁC (cũ hơn) thì vẫn hứa như thường.
    const other = updateView({ ...base, auto_update_enabled: true, published_at: '2026-09-20T08:00:00Z', blocked_version: 'v0.1.16' }, { waitingFor: null, offline: false, now: NOW });
    expect(other.kind === 'available' && other.kicker).toMatch(/Tự cài đêm/);
  });

  // v0.1.33: lịch đêm chỉ cài bản đã là bản chính thức ≥ 24 giờ — thẻ "Có bản mới" nói khi nào tự cài, nhưng CHỈ khi
  // genh báo lịch đêm đang bật (auto_update_enabled). Mốc dựng theo giờ máy chạy test (= giờ trình duyệt).
  const local = (d: number, h: number, m = 0, s = 0) => new Date(2026, 8, d, h, m, s).getTime();
  const LNOW = local(28, 10);
  it('"Có bản mới": lịch đêm BẬT ⇒ báo đêm tự cài — hoặc bấm Cập nhật ngay', () => {
    const on = { ...base, auto_update_enabled: true };
    const kicker = (d: SystemUpdate) => {
      const v = updateView(d, { waitingFor: null, offline: false, now: LNOW });
      return v.kind === 'available' ? v.kicker : v.kind;
    };
    // Promote 28/09 08:00 ⇒ đủ 24 giờ 29/09 08:00 ⇒ lần 03:00 đầu tiên sau đó là 30/09.
    const pub = new Date(local(28, 8)).toISOString();
    expect(kicker({ ...on, published_at: pub })).toBe('Đang dùng v0.1.16 · Tự cài đêm 30/09 (~03:00) — hoặc bấm Cập nhật ngay');
    // Đã đủ 24 giờ từ lâu ⇒ lần 03:00 tới (29/09).
    expect(kicker({ ...on, published_at: new Date(local(25, 0)).toISOString() })).toBe(
      'Đang dùng v0.1.16 · Tự cài đêm 29/09 (~03:00) — hoặc bấm Cập nhật ngay',
    );
    // Máy chủ chưa có watcher ⇒ không có nút, chỉ có lệnh.
    expect(kicker({ ...on, can_request: false, updater: null, published_at: pub })).toMatch(/— hoặc chạy lệnh bên dưới$/);
    // Không biết lúc phát hành ⇒ chỉ hành động tay.
    expect(kicker(on)).toBe('Đang dùng v0.1.16 · bấm Cập nhật ngay (mất khoảng 2–5 phút, tự sao lưu trước)');
    expect(kicker({ ...on, published_at: 'không-phải-ngày' })).toBe('Đang dùng v0.1.16 · bấm Cập nhật ngay (mất khoảng 2–5 phút, tự sao lưu trước)');
  });

  it('"Có bản mới": lịch đêm TẮT hoặc không rõ (genh cũ) ⇒ không hứa "Tự cài", chỉ bấm Cập nhật ngay', () => {
    const pub = new Date(local(28, 8)).toISOString();
    for (const flag of [false, null, undefined]) {
      const v = updateView({ ...base, auto_update_enabled: flag, published_at: pub }, { waitingFor: null, offline: false, now: LNOW });
      expect(v.kind === 'available' && v.kicker).toBe('Đang dùng v0.1.16 · bấm Cập nhật ngay (mất khoảng 2–5 phút, tự sao lưu trước)');
    }
    const cmd = updateView({ ...base, auto_update_enabled: false, can_request: false, updater: null, published_at: pub }, { waitingFor: null, offline: false, now: LNOW });
    expect(cmd.kind === 'available' && cmd.kicker).toBe('Đang dùng v0.1.16 · chạy lệnh bên dưới (mất khoảng 2–5 phút, tự sao lưu trước)');
  });

  it('autoInstallHint: biên 24 giờ và biên 03:00 theo giờ trình duyệt', () => {
    // Đủ 24 giờ đúng lúc 03:00 ⇒ cài ngay lần 03:00 đó.
    expect(autoInstallHint(new Date(local(27, 3)).toISOString(), local(27, 10))).toBe('Tự cài đêm 28/09 (~03:00)');
    // Chín lúc 03:00:01 ⇒ lỡ lần đó, sang đêm sau.
    expect(autoInstallHint(new Date(local(27, 3, 0, 1)).toISOString(), local(27, 10))).toBe('Tự cài đêm 29/09 (~03:00)');
    // Chín trước 03:00 cùng ngày.
    expect(autoInstallHint(new Date(local(27, 1)).toISOString(), local(27, 10))).toBe('Tự cài đêm 28/09 (~03:00)');
    expect(autoInstallHint(null, NOW)).toBeNull();
  });

  it('ghi chú phát hành không hiện dấu promote (chú thích HTML)', () => {
    expect(readableNotes('## Điểm mới\n- Nút cập nhật\n\n<!-- genh:promoted_at=2026-09-28T08:00:00Z -->\n')).toBe('## Điểm mới\n- Nút cập nhật');
  });
});

describe('<UpdateCard>', () => {
  beforeEach(() => {
    queryClient.clear();
    vi.unstubAllGlobals();
  });

  it('bấm Cập nhật ngay → xác nhận → gửi POST /system/update rồi hiện tiến trình', async () => {
    const user = userEvent.setup();
    let posted = false;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        const body = posted ? { ...base, state: 'requested' } : base;
        if (String(url).endsWith('/system/update') && init?.method === 'POST') posted = true;
        return new Response(JSON.stringify(posted ? { ...base, state: 'requested' } : body), {
          status: posted && init?.method === 'POST' ? 202 : 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );
    render(
      <QueryClientProvider client={queryClient}>
        <UpdateCard />
      </QueryClientProvider>,
    );
    expect(await screen.findByText('Có bản mới v0.1.17')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Cập nhật ngay/ }));
    expect(screen.getByText(/tự sao lưu dữ liệu trước/)).toBeInTheDocument();
    const confirm = screen.getAllByRole('button', { name: /Cập nhật ngay/ }).at(-1)!;
    await user.click(confirm);
    await waitFor(() => expect(posted).toBe(true));
    expect(await screen.findByText('Đang cập nhật lên v0.1.17')).toBeInTheDocument();
    expect(screen.getByText('Nhận yêu cầu')).toBeInTheDocument();
  });

  it('cập nhật lỗi + máy chủ chưa nhận yêu cầu từ nút bấm: không có nút Thử lại, hiện lệnh chạy tay', async () => {
    const failed = {
      ...base, can_request: false, updater: null, state: 'failed', to: 'v0.1.17',
      message: 'Tải bản mới thất bại — CHƯA đụng gì (GH-E941)', finished_at: new Date().toISOString(),
    };
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(failed), { status: 200, headers: { 'Content-Type': 'application/json' } })));
    render(
      <QueryClientProvider client={queryClient}>
        <UpdateCard />
      </QueryClientProvider>,
    );
    expect(await screen.findByText('Cập nhật lên v0.1.17 chưa thành công')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Thử lại/ })).toBeNull();
    expect(screen.getByText('~/.gen-harness/bin/genh update')).toBeInTheDocument();
    expect(screen.getByText(/chạy lệnh bên dưới trên máy chủ/)).toBeInTheDocument();
  });

  it('không có quyền (403) thì không hiện gì', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ status: 403, code: 'FORBIDDEN', title: 'Không có quyền' }), { status: 403, headers: { 'Content-Type': 'application/problem+json' } })));
    const { container } = render(
      <QueryClientProvider client={queryClient}>
        <UpdateCard />
      </QueryClientProvider>,
    );
    await new Promise((r) => setTimeout(r, 50));
    expect(container).toBeEmptyDOMElement();
  });
});

describe('v0.1.30 — mục "Cập nhật phần mềm" cố định', () => {
  beforeEach(() => {
    queryClient.clear();
    vi.unstubAllGlobals();
  });

  it('đã mới nhất: thẻ Tổng quan ẩn, mục cố định vẫn hiện; "Kiểm tra bản mới" gọi POST /system/update/check và hiện nút cập nhật', async () => {
    const user = userEvent.setup();
    const calls: string[] = [];
    let latest = 'v0.1.16';
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        const u = String(url);
        calls.push(`${init?.method ?? 'GET'} ${u.replace(/^.*\/api\/v1/, '')}`);
        if (u.endsWith('/system/update/check')) latest = 'v0.1.17';
        const body = { ...base, latest, update_available: latest !== base.current, checked_at: '2026-09-28T10:00:00Z', throttled: false };
        return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }),
    );
    const overview = render(
      <QueryClientProvider client={queryClient}>
        <UpdateCard />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(calls).toContain('GET /system/update'));
    await new Promise((r) => setTimeout(r, 30));
    expect(overview.container).toBeEmptyDOMElement();
    overview.unmount();

    render(
      <QueryClientProvider client={queryClient}>
        <UpdateCard always />
      </QueryClientProvider>,
    );
    const section = await screen.findByTestId('update-section');
    expect(screen.getByText('Cập nhật phần mềm')).toBeInTheDocument();
    expect(screen.getByText('Đang dùng bản mới nhất')).toBeInTheDocument();
    expect(section).toHaveTextContent('v0.1.16');
    expect(screen.queryByRole('button', { name: /Cập nhật ngay/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Kiểm tra bản mới/ }));
    await waitFor(() => expect(calls).toContain('POST /system/update/check'));
    expect(await screen.findByText('Có bản mới v0.1.17')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Cập nhật ngay/ })).toBeInTheDocument();
  });
});

