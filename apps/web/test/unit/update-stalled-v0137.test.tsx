import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import type { SystemHealth, SystemUpdate } from '@gen-harness/contracts';
import { UpdateCard } from '../../src/update/UpdateCard';
import { updateView } from '../../src/update/updateModel';
import { healthRows } from '../../src/screens/system/healthModel';
import { queryClient } from '../../src/lib/queryClient';

/**
 * v0.1.37 (F-34): 'running' mà tiến trình genh trên máy chủ đã chết ⇒ api trả 'stalled' + `stalled_reason:
 * 'process_gone'` — thẻ cập nhật nói "bị dừng giữa chừng" và cho Thử lại; mã genh GH-E94B (dừng do tín hiệu) / GH-E94A
 * (đang có lần cập nhật/khôi phục khác — genh ≥ v0.1.37 không ghi mã này vào hộp thư, nhánh chỉ phòng hờ nên chỉ có unit
 * test) có lời dẫn riêng, nguyên văn vẫn ở "Chi tiết kỹ thuật".
 */

const NOW = Date.parse('2026-10-02T10:00:00Z');
const base: SystemUpdate = {
  current: 'v0.1.36', latest: 'v0.1.37', update_available: true, updater: 'systemd', linked: true, can_request: true,
  state: 'idle', stalled_reason: null, message: null, from: null, to: null, started_at: null, finished_at: null,
  requested_at: null, release_url: null, release_notes: '- Tự lành',
};
const opts = { waitingFor: null, offline: false, now: NOW };

describe('updateView — stalled theo lý do', () => {
  it('process_gone: tiêu đề "bị dừng giữa chừng", hướng dẫn Thử lại, không hiện lệnh khi bấm được', () => {
    const v = updateView({ ...base, state: 'stalled', stalled_reason: 'process_gone' }, opts);
    if (v.kind !== 'stalled') throw new Error(v.kind);
    expect(v.title).toBe('Cập nhật lên v0.1.37 bị dừng giữa chừng');
    expect(v.kicker).toBe('Tiến trình cập nhật trên máy chủ không còn chạy — có thể máy vừa tắt hoặc khởi động lại');
    expect(v.body).toMatch(/Bấm Thử lại để chạy lại từ đầu/);
    expect(v.showCommand).toBe(false);
    expect(v.body).not.toMatch(/genh/);
    const manual = updateView({ ...base, can_request: false, state: 'stalled', stalled_reason: 'process_gone' }, opts);
    expect(manual.kind !== 'hidden' && manual.showCommand).toBe(true);
  });
  it('process_gone mà máy đã chạy đúng bản mới nhất: không nói "Cập nhật lên vX bị dừng", không cần Thử lại', () => {
    const v = updateView({ ...base, current: 'v0.1.37', latest: 'v0.1.37', update_available: false, state: 'stalled', stalled_reason: 'process_gone' }, opts);
    expect(v.kind).toBe('finished');
    if (v.kind === 'hidden') throw new Error(v.kind);
    expect(v.tone).toBe('ok');
    expect(v.title).toBe('Đang dùng bản mới nhất v0.1.37');
    expect(v.title).not.toMatch(/bị dừng/);
    expect(v.kicker).toMatch(/không cần làm gì/);
  });
  it('process_gone + can_request=false: không trỏ tới nút Thử lại (thẻ không vẽ nút) — chỉ lệnh chạy tay', () => {
    const v = updateView({ ...base, can_request: false, state: 'stalled', stalled_reason: 'process_gone' }, opts);
    if (v.kind !== 'stalled') throw new Error(v.kind);
    expect(v.body).not.toMatch(/Bấm Thử lại/);
    expect(v.body).toMatch(/Chạy lệnh dưới đây trên máy chủ/);
  });
  it('requested + host_busy: nói đang xếp hàng sau lần khác, không hứa "trong vòng 1 phút"', () => {
    const v = updateView({ ...base, state: 'requested', host_busy: true }, opts);
    if (v.kind !== 'working') throw new Error(v.kind);
    expect(v.kicker).toMatch(/đang chạy một lần cập nhật\/khôi phục khác/);
    expect(v.kicker).not.toMatch(/1 phút/);
    const plain = updateView({ ...base, state: 'requested' }, opts);
    expect(plain.kind === 'working' && plain.kicker).toMatch(/1 phút/);
  });
  it('not_picked_up / thiếu lý do (api cũ): giữ chữ cũ', () => {
    for (const reason of ['not_picked_up', null, undefined] as const) {
      const v = updateView({ ...base, state: 'stalled', stalled_reason: reason }, opts);
      if (v.kind !== 'stalled') throw new Error(v.kind);
      expect(v.title).toBe('Máy chủ chưa nhận yêu cầu cập nhật');
      expect(v.kicker).toMatch(/quá 15 phút/);
    }
  });
});

describe('failedCopy — mã GH-E94B / GH-E94A', () => {
  const failedAt = (message: string) =>
    updateView({ ...base, state: 'failed', to: 'v0.1.37', message, finished_at: '2026-10-02T09:00:00Z' }, opts);
  it('GH-E94B đã quay về: "dừng giữa chừng", không coi là bản hỏng; nguyên văn ở chi tiết', () => {
    const msg = 'Cập nhật bị dừng giữa chừng do tín hiệu dừng — đã tự quay về v0.1.36 (GH-E94B)';
    const v = failedAt(msg);
    if (v.kind !== 'failed') throw new Error(v.kind);
    expect(v.tone).toBe('warn');
    expect(v.kicker).toBe('Cập nhật bị dừng giữa chừng');
    expect(v.body).toMatch(/đã tự quay về bản đang dùng/);
    expect(v.body).toMatch(/không phải lỗi của bản mới/);
    expect(v.body).not.toMatch(/không tự cài lại/);
    expect(v.detail).toBe(msg);
  });
  it('GH-E94B chưa quay về: không nói "đã tự quay về"', () => {
    const v = failedAt('Cập nhật bị dừng giữa chừng do tín hiệu dừng (GH-E94B)');
    if (v.kind !== 'failed') throw new Error(v.kind);
    expect(v.kicker).toBe('Cập nhật bị dừng giữa chừng');
    expect(v.body).not.toMatch(/đã tự quay về/);
    expect(v.body).toMatch(/Chi tiết kỹ thuật/);
    // Chưa quay về trọn: genh chặn lịch đêm — không được hứa lịch đêm tự thử lại.
    expect(v.body).not.toMatch(/lịch đêm/);
  });
  it('GH-E94B chưa đụng gì: lịch đêm tự thử lại', () => {
    const v = failedAt('Cập nhật bị dừng giữa chừng (máy tắt, khởi động lại hoặc bị dừng tay) — chưa đụng gì (CSDL, compose.yaml giữ nguyên) (GH-E94B)');
    if (v.kind !== 'failed') throw new Error(v.kind);
    expect(v.kicker).toMatch(/chưa đụng gì/);
    expect(v.body).toMatch(/lịch đêm tự thử lại/);
  });
  it('GH-E94B máy tắt sau khi đã đổi CSDL (cần chạy tiếp): bảo chạy lại để đi tiếp, KHÔNG bảo khôi phục bản cũ', () => {
    const v = failedAt('Cập nhật bị dừng giữa chừng (máy tắt, khởi động lại hoặc bị dừng tay) — CSDL đã sang bản mới, cần chạy tiếp — Dữ liệu vẫn còn nguyên trong CSDL (bản sao lưu trước cập nhật: backups/x.enc). Sau khi máy bật lại: chạy genh update để đi tiếp lên bản mới (genh tự sao lưu lại CSDL hiện tại trước); lịch đêm, nếu bật, cũng sẽ tự làm. KHÔNG khôi phục bản sao lưu cũ — sẽ mất dữ liệu ghi sau lúc đó. (GH-E94B)');
    if (v.kind !== 'failed') throw new Error(v.kind);
    expect(v.tone).toBe('warn');
    expect(v.kicker).toMatch(/cần chạy lại để hoàn tất/);
    expect(v.kicker).not.toMatch(/xử lý tay/);
    expect(v.body).toMatch(/bấm Thử lại để đi tiếp/);
    expect(v.body).toMatch(/Không khôi phục bản sao lưu cũ/);
  });
  it('GH-E94B quay về CHƯA trọn (genh ghi update-blocked rollback_failed): "Cần xử lý tay", không hứa lịch đêm', () => {
    const v = failedAt('Cập nhật bị dừng giữa chừng (máy tắt, khởi động lại hoặc bị dừng tay) — quay về bản cũ CHƯA trọn — Máy tắt giữa lúc cập nhật đã đổi CSDL (GH-E94B)');
    if (v.kind !== 'failed') throw new Error(v.kind);
    expect(v.kicker).toMatch(/Cần xử lý tay/);
    expect(v.body).not.toMatch(/lịch đêm/);
  });
  it('GH-E94A: đang có lần cập nhật/khôi phục khác chạy', () => {
    const msg = 'Đang có một lần cập nhật/khôi phục khác chạy — chờ xong rồi thử lại (GH-E94A)';
    const v = failedAt(msg);
    if (v.kind !== 'failed') throw new Error(v.kind);
    expect(v.kicker).toBe('Đang có một lần cập nhật/khôi phục khác chạy — chờ xong rồi thử lại');
    expect(v.kicker).not.toMatch(/quay về/);
    expect(v.detail).toBe(msg);
  });
});

describe('healthModel — dòng Cập nhật', () => {
  const health = (update: SystemHealth['update']): SystemHealth => ({
    checked_at: new Date(NOW).toISOString(), overall: 'warn',
    worker: { state: 'ok', alive: true, last_seen_at: null, silent_minutes: null },
    browser: { state: 'off', last_heartbeat_at: null }, queues: [], crons: [],
    backup: { configured: true, latest_at: new Date(NOW).toISOString(), age_hours: 0, stale: false },
    update, disk: { state: 'unknown', free_bytes: null, min_bytes: null, checked_at: null }, issues: [],
  });
  const row = (u: SystemHealth['update']) => healthRows(health(u), NOW).find((r) => r.key === 'update')!;
  it('stalled/process_gone ⇒ "Cập nhật bị dừng giữa chừng" (vàng); not_picked_up giữ chữ cũ', () => {
    const gone = row({ state: 'stalled', stalled_reason: 'process_gone', failed: false, blocked_version: null, finished_at: null });
    expect(gone.value).toBe('Cập nhật bị dừng giữa chừng');
    expect(gone.tone).toBe('warn');
    const stale = row({ state: 'stalled', stalled_reason: 'not_picked_up', failed: false, blocked_version: null, finished_at: null });
    expect(stale.value).toBe('Máy chủ chưa nhận yêu cầu cập nhật');
    expect(row({ state: 'running', stalled_reason: null, failed: false, blocked_version: null, finished_at: null }).value).toBe('Đang cập nhật');
  });
  it('failed + interrupted (GH-E94B dừng gọn) ⇒ "Cập nhật bị dừng giữa chừng" (vàng), không "lỗi" đỏ', () => {
    for (const interrupted of ['rolled_back', 'resume'] as const) {
      const r = row({ state: 'failed', stalled_reason: null, failed: true, interrupted, blocked_version: null, finished_at: null });
      expect(r.value).toBe('Cập nhật bị dừng giữa chừng');
      expect(r.tone).toBe('warn');
    }
    const bad = row({ state: 'failed', stalled_reason: null, failed: true, interrupted: null, blocked_version: null, finished_at: null });
    expect(bad.value).toBe('Lần cập nhật gần nhất lỗi');
    expect(bad.tone).toBe('bad');
  });
});

describe('<UpdateCard> — dừng giữa chừng', () => {
  beforeEach(() => {
    queryClient.clear();
    vi.unstubAllGlobals();
  });

  it('process_gone: thẻ hiện "bị dừng giữa chừng", bấm Thử lại gửi POST /system/update', async () => {
    const user = userEvent.setup();
    const stalled: SystemUpdate = { ...base, state: 'stalled', stalled_reason: 'process_gone', to: 'v0.1.37' };
    let posted = false;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        if (String(url).endsWith('/system/update') && init?.method === 'POST') posted = true;
        const body = posted ? { ...stalled, state: 'requested', stalled_reason: null } : stalled;
        return new Response(JSON.stringify(body), {
          status: init?.method === 'POST' ? 202 : 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }),
    );
    render(
      <QueryClientProvider client={queryClient}>
        <UpdateCard />
      </QueryClientProvider>,
    );
    expect(await screen.findByText('Cập nhật lên v0.1.37 bị dừng giữa chừng')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Thử lại/ }));
    await waitFor(() => expect(posted).toBe(true));
    expect(await screen.findByText('Đang cập nhật lên v0.1.37')).toBeInTheDocument();
  });
});
