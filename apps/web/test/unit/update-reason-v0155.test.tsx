import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { SystemUpdate, SystemUpdateBlockReason } from '@gen-harness/contracts';
import { UpdateCard } from '../../src/update/UpdateCard';
import { UpdateNotice } from '../../src/update/UpdateNotice';
import { UPDATE_COMMAND, WATCHER_COMMAND, blockOf, blockReasonCopy, canClickUpdate, updateView } from '../../src/update/updateModel';
import { qk } from '../../src/lib/queries';
import { queryClient } from '../../src/lib/queryClient';
import { permissionsOf } from '../mock-api';

/**
 * v0.1.55 (G2) — thẻ Cập nhật nói rõ vì sao KHÔNG có nút "Cập nhật ngay" (api `request_block_reason`): lý do + việc cần làm; lệnh
 * (nếu có) luôn đi sau câu lý do. KHÔNG BAO GIỜ hiện lệnh mà không có câu lý do.
 */

const base: SystemUpdate = {
  current: 'v0.1.16', latest: 'v0.1.17', update_available: true, updater: 'systemd', linked: true, can_request: true,
  state: 'idle', message: null, from: null, to: null, started_at: null, finished_at: null, requested_at: null,
  release_url: null, release_notes: '- Nút cập nhật', request_block_reason: null,
};
const REASONS: SystemUpdateBlockReason[] = ['not_owner', 'in_progress', 'genh_unlinked', 'watcher_stalled', 'up_to_date'];
const view = (d: SystemUpdate) => updateView(d, { waitingFor: null, offline: false });

describe('blockReasonCopy (hàm thuần)', () => {
  it('đủ 5 lý do; câu ngắn đúng chữ của Sếp', () => {
    expect(blockReasonCopy('not_owner').title).toBe('Chỉ Owner cập nhật được — nhờ Owner bấm');
    expect(blockReasonCopy('genh_unlinked').title).toBe('Máy chủ chưa bật cập nhật bằng nút bấm');
    expect(blockReasonCopy('watcher_stalled').title).toBe('Người gác cập nhật đang lỗi');
    expect(blockReasonCopy('in_progress').title).toBe('Đang cập nhật…');
    expect(blockReasonCopy('up_to_date').title).toBe('Đang là bản mới nhất');
  });

  it('lệnh: genh_unlinked = UPDATE_COMMAND, watcher_stalled = "genh auto-update enable"; ba lý do còn lại KHÔNG có lệnh', () => {
    expect(blockReasonCopy('genh_unlinked').command).toBe(UPDATE_COMMAND);
    expect(blockReasonCopy('watcher_stalled').command).toBe('genh auto-update enable');
    expect(WATCHER_COMMAND).toBe('genh auto-update enable');
    for (const r of ['not_owner', 'in_progress', 'up_to_date'] as const) expect(blockReasonCopy(r).command, r).toBeUndefined();
  });

  it('ca nào có lệnh thì có lý do + việc cần làm: title, body, action đều khác rỗng và là chuỗi', () => {
    for (const r of REASONS) {
      const c = blockReasonCopy(r);
      for (const k of ['title', 'body', 'action'] as const) {
        expect(typeof c[k], `${r}.${k}`).toBe('string');
        expect(c[k].trim().length, `${r}.${k}`).toBeGreaterThan(0);
      }
      if (c.command) {
        expect(c.body.length, r).toBeGreaterThan(20);
        expect(c.action, r).toMatch(/lệnh/i);
      }
    }
    // Người dùng có "việc cần làm" ngay cả khi không có lệnh.
    expect(blockReasonCopy('not_owner').action).toMatch(/Owner/);
    expect(blockReasonCopy('in_progress').action).toMatch(/Chờ/);
  });
});

describe('updateView + request_block_reason', () => {
  it('null ⇒ thẻ "Có bản mới" bình thường, không khối lý do, nút hiện được', () => {
    const v = view(base);
    expect(v.kind).toBe('available');
    expect(v.kind !== 'hidden' && v.block).toBeUndefined();
    expect(canClickUpdate(base)).toBe(true);
  });

  it('not_owner / genh_unlinked / watcher_stalled khi có bản mới ⇒ khối lý do (không nút); lệnh chỉ đi kèm lý do', () => {
    for (const r of ['not_owner', 'genh_unlinked', 'watcher_stalled'] as const) {
      const d: SystemUpdate = { ...base, can_request: r === 'watcher_stalled', request_block_reason: r };
      const v = view(d);
      if (v.kind !== 'available') throw new Error(`${r}: ${v.kind}`);
      expect(v.block, r).toEqual(blockReasonCopy(r));
      expect(v.showCommand, r).toBeFalsy(); // lệnh nằm trong khối lý do, không phải khối "chạy tay" cũ
      expect(v.kicker).toContain('Đang dùng v0.1.16');
      expect(canClickUpdate(d), r).toBe(false);
    }
  });

  it('in_progress ⇒ thẻ tiến trình (không khối lý do); up_to_date ⇒ ẩn/idle như cũ', () => {
    const working = view({ ...base, state: 'requested', request_block_reason: 'in_progress' });
    expect(working.kind).toBe('working');
    expect(working.kind !== 'hidden' && working.block).toBeUndefined();
    const idle = view({ ...base, latest: 'v0.1.16', update_available: false, request_block_reason: 'up_to_date' });
    expect(idle.kind).toBe('hidden');
    expect(idle.kind === 'hidden' && idle.block).toBeUndefined();
  });

  it('api cũ (thiếu trường) ⇒ như trước: theo can_request', () => {
    const { request_block_reason: _drop, ...legacy } = base;
    void _drop;
    expect(canClickUpdate(legacy as SystemUpdate)).toBe(true);
    expect(canClickUpdate({ ...legacy, can_request: false } as SystemUpdate)).toBe(false);
    const v = view({ ...legacy, can_request: false, updater: null } as SystemUpdate);
    expect(v.kind !== 'hidden' && v.showCommand).toBe(true);
    expect(v.kind !== 'hidden' && v.body).toBe('Máy chủ chưa bật cập nhật bằng nút bấm.');
  });

  it('bản không cài bằng genh (linked=false) không hiện lệnh genh: không có khối genh_unlinked', () => {
    expect(blockOf({ request_block_reason: 'genh_unlinked', linked: false })).toBeUndefined();
    expect(blockOf({ request_block_reason: 'genh_unlinked', linked: true })?.command).toBe(UPDATE_COMMAND);
    expect(blockOf({ request_block_reason: 'watcher_stalled', linked: false })?.command).toBe(WATCHER_COMMAND);
  });

  it('KHÔNG BAO GIỜ hiện lệnh mà không có câu lý do — quét mọi tổ hợp trạng thái', () => {
    const states: SystemUpdate['state'][] = ['idle', 'requested', 'running', 'done', 'failed', 'stalled'];
    const stalled: SystemUpdate['stalled_reason'][] = [null, 'not_picked_up', 'process_gone', 'linger_off', 'watcher_failed'];
    const reasons: Array<SystemUpdate['request_block_reason']> = [undefined, null, ...REASONS];
    let withCommand = 0;
    for (const state of states)
      for (const stalled_reason of stalled)
        for (const request_block_reason of reasons)
          for (const can_request of [true, false])
            for (const update_available of [true, false])
              for (const linked of [true, false]) {
                const d: SystemUpdate = {
                  ...base, state, stalled_reason, request_block_reason, can_request, update_available, linked,
                  latest: update_available ? 'v0.1.17' : 'v0.1.16',
                  message: state === 'failed' ? 'Tải bản mới thất bại — CHƯA đụng gì (GH-E941)' : null,
                  to: 'v0.1.17', finished_at: new Date().toISOString(),
                };
                const v = view(d);
                const commands = v.kind === 'hidden' ? [v.block?.command] : [v.showCommand ? (v.command ?? UPDATE_COMMAND) : undefined, v.block?.command];
                if (commands.some(Boolean)) {
                  withCommand += 1;
                  const reason = v.kind === 'hidden' ? v.block?.body : (v.block?.body ?? v.body);
                  expect(reason && reason.trim().length > 0, JSON.stringify({ state, stalled_reason, request_block_reason, can_request, update_available, linked })).toBe(true);
                }
              }
    expect(withCommand).toBeGreaterThan(20); // phép thử có thật sự chạm tới các ca có lệnh
  });
});

function renderCard(data: SystemUpdate, ui: 'card' | 'always' | 'notice' = 'card') {
  // Dòng báo ở Hôm nay chỉ hỏi `GET /system/update` khi người xem có `system.manage` (v0.1.71, P3-9).
  queryClient.setQueryData(qk.me, {
    id: 'u', email: 'owner@genesis.local', display_name: 'Chủ', role: { code: 'owner', name: 'Owner' },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
    addressing: { self: 'Anh', bot_calls_me: 'Sếp' }, pin_verified_until: null, permissions: permissionsOf('owner'),
  });
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } })));
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui === 'notice' ? <UpdateNotice /> : <UpdateCard always={ui === 'always'} />}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('UpdateCard — lý do khi ẩn nút', () => {
  beforeEach(() => {
    queryClient.clear();
    vi.unstubAllGlobals();
  });

  it('null ⇒ có nút "Cập nhật ngay", không khối lý do', async () => {
    renderCard(base);
    expect(await screen.findByText('Có bản mới v0.1.17')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Cập nhật ngay/ })).toBeInTheDocument();
    expect(screen.queryByTestId('update-block-reason')).toBeNull();
  });

  it('watcher_stalled ⇒ KHÔNG nút; hiện lý do + việc cần làm + lệnh `genh auto-update enable` SAU câu lý do', async () => {
    renderCard({ ...base, request_block_reason: 'watcher_stalled' });
    const block = await screen.findByTestId('update-block-reason');
    expect(block).toHaveAttribute('data-reason', 'watcher_stalled');
    const c = blockReasonCopy('watcher_stalled');
    expect(within(block).getByText(c.title)).toBeInTheDocument();
    expect(within(block).getByText(c.body)).toBeInTheDocument();
    expect(within(block).getByText(c.action)).toBeInTheDocument();
    const code = within(block).getByText('genh auto-update enable');
    expect(code.tagName).toBe('CODE');
    // Lệnh đứng SAU câu lý do trong cây DOM.
    expect(within(block).getByText(c.body).compareDocumentPosition(code) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Cập nhật ngay/ })).toBeNull();
  });

  it('genh_unlinked ⇒ "Máy chủ chưa bật cập nhật bằng nút bấm" + lệnh cập nhật hiện có; không nút', async () => {
    renderCard({ ...base, can_request: false, updater: null, request_block_reason: 'genh_unlinked' });
    const block = await screen.findByTestId('update-block-reason');
    expect(within(block).getByText('Máy chủ chưa bật cập nhật bằng nút bấm')).toBeInTheDocument();
    expect(within(block).getByText(UPDATE_COMMAND)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Cập nhật ngay/ })).toBeNull();
    // Chỉ MỘT khối lệnh (không lặp khối "chạy tay" cũ).
    expect(screen.getAllByText(UPDATE_COMMAND)).toHaveLength(1);
  });

  it('not_owner ⇒ "Chỉ Owner cập nhật được — nhờ Owner bấm", không lệnh, không nút', async () => {
    renderCard({ ...base, request_block_reason: 'not_owner' });
    const block = await screen.findByTestId('update-block-reason');
    expect(within(block).getByText('Chỉ Owner cập nhật được — nhờ Owner bấm')).toBeInTheDocument();
    expect(block.querySelector('code')).toBeNull();
    expect(screen.queryByRole('button', { name: /Cập nhật ngay/ })).toBeNull();
  });

  it('in_progress ⇒ thẻ "Đang cập nhật lên …" (không khối lý do, không nút)', async () => {
    renderCard({ ...base, state: 'requested', request_block_reason: 'in_progress' });
    expect(await screen.findByText('Đang cập nhật lên v0.1.17')).toBeInTheDocument();
    expect(screen.queryByTestId('update-block-reason')).toBeNull();
    expect(screen.queryByRole('button', { name: /Cập nhật ngay/ })).toBeNull();
  });

  it('up_to_date ở mục cố định ⇒ "Đang dùng bản mới nhất", không nút, không lệnh', async () => {
    renderCard({ ...base, latest: 'v0.1.16', update_available: false, request_block_reason: 'up_to_date' }, 'always');
    expect(await screen.findByText('Đang dùng bản mới nhất')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Cập nhật ngay/ })).toBeNull();
    expect(screen.queryByTestId('update-block-reason')).toBeNull();
  });

  it('mục cố định + người gác lỗi nhưng đã mới nhất ⇒ vẫn nói người gác lỗi (kèm lệnh) để lịch đêm/nút không im lặng hỏng', async () => {
    renderCard({ ...base, latest: 'v0.1.16', update_available: false, request_block_reason: 'watcher_stalled' }, 'always');
    const block = await screen.findByTestId('update-block-reason');
    expect(within(block).getByText('Người gác cập nhật đang lỗi')).toBeInTheDocument();
    expect(within(block).getByText('genh auto-update enable')).toBeInTheDocument();
  });

  it('không render object: mọi chữ lý do là chuỗi', async () => {
    const { container } = renderCard({ ...base, request_block_reason: 'watcher_stalled' });
    await screen.findByTestId('update-block-reason');
    expect(container.innerHTML).not.toContain('[object Object]');
  });
});

describe('UpdateNotice — một dòng ở Hôm nay nêu lý do', () => {
  beforeEach(() => {
    queryClient.clear();
    vi.unstubAllGlobals();
  });

  it('có bản mới + nút bị ẩn ⇒ dòng báo kèm lý do; bình thường ⇒ "cập nhật ở Cài đặt"', async () => {
    const first = renderCard({ ...base, request_block_reason: 'watcher_stalled' }, 'notice');
    expect(await screen.findByTestId('update-notice')).toHaveTextContent('Có bản mới v0.1.17 — Người gác cập nhật đang lỗi; xem ở Cài đặt');
    first.unmount();
    queryClient.clear();
    renderCard(base, 'notice');
    expect(await screen.findByTestId('update-notice')).toHaveTextContent('Có bản mới v0.1.17 — cập nhật ở Cài đặt');
  });
});
