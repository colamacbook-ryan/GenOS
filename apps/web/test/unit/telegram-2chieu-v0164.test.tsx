/**
 * v0.1.64 (T3) — Kết nối › Telegram, phần "Nhận tin từ Sếp" (Telegram 2 chiều): ghép bằng mã 6 số, công tắc (mặc định TẮT),
 * gỡ ghép, lỗi thân thiện + Chi tiết kỹ thuật. Telegram chỉ đọc/đề xuất: KHÔNG có nút Duyệt, KHÔNG ô nhập mã PIN trên thẻ,
 * mã ghép không lọt vào cache. Mọi thao tác đổi cần PIN (hộp PIN chuẩn của Console, tự mở khi API trả 423).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { TelegramConfig, TelegramHostStatus, TelegramInbound } from '@gen-harness/contracts';
import { TelegramCard } from '../../src/screens/connections/TelegramCard';
import {
  INBOUND_ERROR_TEXT,
  INBOUND_NOTE,
  INBOUND_UNPAIRED_HINT,
  PAIR_POLL_MS,
  TELEGRAM_KEY,
  TELEGRAM_WARNING,
  asInbound,
  inboundStatusText,
  pairInstruction,
  pairPollMs,
  spacedCode,
  telegramErrorText,
} from '../../src/screens/connections/telegramModel';
import { usePinStore } from '../../src/lib/pinStore';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json', 'X-Request-ID': 'req0123456789abc' },
  });

const HOST: TelegramHostStatus = {
  supported: true, schedule: 'systemd', last_run_at: '2026-10-17T01:00:00Z', state: 'ok', telegram: 'ok', telegram_error_code: null,
  incidents: [], test: null,
};
const OFF: TelegramInbound = {
  enabled: false, paired: false, owner_masked: null, paired_at: null, pair_pending: false, pair_expires_at: null, error_code: null, message: null,
};
const BASE: TelegramConfig = {
  configured: true, enabled: true, bot_username: 'gen_sep_bot', chat_id_masked: '•••4321', briefing: true, reminders: true,
  updated_at: '2026-10-17T01:00:00Z', last_test: { status: 'pass', error_code: null, message: null, checked_at: '2026-10-17T01:05:00Z' },
  host: HOST, inbound: OFF,
};
const PAIRED: TelegramInbound = { ...OFF, paired: true, enabled: true, owner_masked: '•••4321', paired_at: '2026-10-17T02:00:00Z' };

interface Call {
  path: string;
  method: string;
  body: unknown;
}

function setup(initial: TelegramConfig = BASE) {
  const world = { config: initial, pairCode: '123456', rejectToggle: false };
  const calls: Call[] = [];
  let pinOk = false;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(url), 'http://x').pathname.replace('/api/v1', '');
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ path, method, body });
      const pin = () => (!pinOk ? json(423, { status: 423, code: 'PIN_REQUIRED', title: 'Cần PIN', request_id: 'req0123456789abc' }) : null);
      if (path === '/notify/telegram' && method === 'GET') return json(200, world.config);
      if (path === '/notify/telegram/pair/start' && method === 'POST') {
        const r = pin();
        if (r) return r;
        world.config = { ...world.config, inbound: { ...OFF, ...world.config.inbound, pair_pending: true, pair_expires_at: '2026-10-17T02:10:00Z' } as TelegramInbound };
        return json(200, { ...world.config, pair: { code: world.pairCode, expires_at: '2026-10-17T02:10:00Z', bot_username: 'gen_sep_bot' } });
      }
      if (path === '/notify/telegram/inbound' && method === 'PUT') {
        const r = pin();
        if (r) return r;
        const inb = world.config.inbound as TelegramInbound;
        if (body.enabled && (!inb.paired || world.rejectToggle)) return json(409, { status: 409, code: 'TELEGRAM_NOT_PAIRED', title: 'Chưa ghép tài khoản Telegram của Sếp' });
        world.config = { ...world.config, inbound: { ...inb, enabled: body.enabled } };
        return json(200, world.config);
      }
      if (path === '/notify/telegram/pair' && method === 'DELETE') {
        const r = pin();
        if (r) return r;
        world.config = { ...world.config, inbound: OFF };
        return json(200, world.config);
      }
      return json(404, { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' });
    }),
  );
  const unsub = usePinStore.subscribe((s, prev) => {
    if (s.open && !prev.open) {
      pinOk = true;
      queueMicrotask(() => usePinStore.getState().finish(true));
    }
  });
  return { world, calls, unsub };
}

function renderCard() {
  queryClient.setQueryData(qk.me, {
    id: 'u', email: 'o@x', display_name: 'Sếp', role: { code: 'owner', name: 'Owner' },
    org: { id: 'o', name: 'x', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' }, addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
    pin_verified_until: null, permissions: { 'system.manage': 'all' },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <TelegramCard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

let unsubs: Array<() => void> = [];
beforeEach(() => queryClient.clear());
afterEach(() => {
  unsubs.forEach((u) => u());
  unsubs = [];
  usePinStore.setState({ open: false, waiters: [] });
  vi.unstubAllGlobals();
  queryClient.clear();
});
const go = (c?: TelegramConfig) => {
  const r = setup(c);
  unsubs.push(r.unsub);
  return r;
};
const block = async () => within(await screen.findByTestId('telegram-inbound'));
const writes = (calls: Call[]) => calls.filter((c) => c.method !== 'GET');

describe('mô hình (hàm thuần)', () => {
  it('pairInstruction, spacedCode, pairPollMs, inboundStatusText, asInbound', () => {
    expect(pairInstruction('gen_sep_bot', '123456')).toBe('Mở bot @gen_sep_bot bằng tài khoản Telegram của Sếp và gửi đúng dòng: /start 123456');
    expect(pairInstruction(null, '123456')).toContain('trên Telegram');
    expect(spacedCode('123456')).toBe('123 456');
    expect(spacedCode('12')).toBe('12');
    expect(pairPollMs(null)).toBe(false);
    expect(pairPollMs({ inbound: OFF })).toBe(false);
    expect(pairPollMs({ inbound: { ...OFF, pair_pending: true } })).toBe(PAIR_POLL_MS);
    const t = (iso: string) => `T(${iso})`;
    expect(inboundStatusText(OFF, t)).toBe(INBOUND_UNPAIRED_HINT);
    expect(inboundStatusText(PAIRED, t)).toBe('Đã ghép với tài khoản Telegram •••4321 · từ T(2026-10-17T02:00:00Z) · đang nhận tin');
    expect(inboundStatusText({ ...PAIRED, enabled: false }, t)).toContain('đang TẮT nhận tin');
    expect(asInbound(BASE)).toEqual(OFF);
    expect(asInbound({ ...BASE, inbound: undefined })).toBeNull();
    expect(asInbound({ ...BASE, inbound: 'x' as unknown as TelegramInbound })).toBeNull();
    expect(asInbound(null)).toBeNull();
  });

  it('mã lỗi nhận tin có câu tiếng Việt riêng; mã lạ không lộ object', () => {
    for (const [code, text] of Object.entries(INBOUND_ERROR_TEXT)) expect(telegramErrorText(code)).toBe(text);
    expect(telegramErrorText('TELEGRAM_POLL_CONFLICT', 'Conflict: terminated')).toBe(INBOUND_ERROR_TEXT.TELEGRAM_POLL_CONFLICT);
    expect(typeof telegramErrorText('MA_LA', { x: 1 } as unknown as string)).toBe('string');
  });

  it('câu cảnh báo cố định nói rõ: chỉ đọc/đề xuất, PIN chỉ ở Console', () => {
    expect(TELEGRAM_WARNING).toContain('chỉ đọc và đề xuất');
    expect(TELEGRAM_WARNING).toContain('không bao giờ gõ mã PIN vào Telegram');
    expect(INBOUND_NOTE).toContain('Gen chỉ đọc và đề xuất');
    expect(INBOUND_NOTE).toContain('Xác nhận và nhập mã PIN');
  });
});

describe('thẻ Telegram › Nhận tin từ Sếp', () => {
  it('chưa ghép: công tắc TẮT và khoá, có nút Ghép Telegram, câu hướng dẫn; không nút Duyệt, không ô mã PIN', async () => {
    go();
    const { container } = renderCard();
    const b = await block();
    const sw = b.getByRole('switch', { name: 'Nhận tin từ Sếp' });
    expect(sw).toHaveAttribute('aria-checked', 'false');
    expect(sw).toHaveAttribute('aria-disabled', 'true');
    expect(b.getByTestId('telegram-inbound-status')).toHaveTextContent(INBOUND_UNPAIRED_HINT);
    expect(b.getByRole('button', { name: 'Ghép Telegram' })).toBeInTheDocument();
    expect(b.queryByRole('button', { name: 'Gỡ ghép' })).toBeNull();
    expect(b.getByTestId('telegram-inbound-note')).toHaveTextContent(INBOUND_NOTE);
    // Telegram không phải nơi duyệt / nhập PIN
    expect(screen.queryByRole('button', { name: /duyệt/i })).toBeNull();
    expect(container.querySelector('input[type="password"][inputmode="numeric"]')).toBeNull();
    expect(screen.queryByLabelText(/mã PIN/i)).toBeNull();
  });

  it('Ghép Telegram (PIN) → hiện mã 6 số + câu "gửi /start <mã> cho @bot"; mã KHÔNG vào cache react-query', async () => {
    const { calls } = go();
    renderCard();
    const user = userEvent.setup();
    const b = await block();
    await user.click(b.getByRole('button', { name: 'Ghép Telegram' }));
    expect(await b.findByTestId('telegram-pair-code')).toHaveTextContent('123 456');
    const pair = b.getByTestId('telegram-pair');
    expect(pair).toHaveTextContent('Mở bot @gen_sep_bot bằng tài khoản Telegram của Sếp và gửi đúng dòng: /start 123456');
    expect(pair).toHaveTextContent('Mã sống 10 phút');
    const posts = calls.filter((c) => c.path === '/notify/telegram/pair/start');
    expect(posts).toHaveLength(2); // 423 PIN rồi gửi lại
    expect(JSON.stringify(queryClient.getQueryData(TELEGRAM_KEY))).not.toContain('123456');
    expect(JSON.stringify(queryClient.getQueryData(TELEGRAM_KEY))).not.toContain('"pair"');
  });

  it('Sếp gửi mã cho bot → lần hỏi lại thấy "Đã ghép", mã biến mất, công tắc mở khoá (đang bật)', async () => {
    const { world } = go();
    renderCard();
    const user = userEvent.setup();
    const b = await block();
    await user.click(b.getByRole('button', { name: 'Ghép Telegram' }));
    await b.findByTestId('telegram-pair-code');
    // Worker nhận /start 123456 ⇒ API báo đã ghép; vòng hỏi lại 3 giây của thẻ (pairPollMs) kéo trạng thái mới.
    world.config = { ...world.config, inbound: PAIRED };
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: TELEGRAM_KEY });
    });
    await waitFor(() => expect(screen.queryByTestId('telegram-pair-code')).toBeNull());
    expect(b.getByTestId('telegram-inbound-status')).toHaveTextContent('Đã ghép với tài khoản Telegram •••4321');
    const sw = b.getByRole('switch', { name: 'Nhận tin từ Sếp' });
    expect(sw).toHaveAttribute('aria-checked', 'true');
    expect(sw).not.toHaveAttribute('aria-disabled');
    expect(b.getByRole('button', { name: 'Ghép lại' })).toBeInTheDocument();
    expect(b.getByRole('button', { name: 'Gỡ ghép' })).toBeInTheDocument();
  });

  it('công tắc: tắt rồi bật gửi PUT /inbound {enabled} (PIN)', async () => {
    const { calls } = go({ ...BASE, inbound: PAIRED });
    renderCard();
    const user = userEvent.setup();
    const b = await block();
    await user.click(b.getByRole('switch', { name: 'Nhận tin từ Sếp' }));
    await waitFor(() => expect(b.getByRole('switch', { name: 'Nhận tin từ Sếp' })).toHaveAttribute('aria-checked', 'false'));
    expect(b.getByTestId('telegram-inbound-status')).toHaveTextContent('đang TẮT nhận tin');
    await user.click(b.getByRole('switch', { name: 'Nhận tin từ Sếp' }));
    await waitFor(() => expect(b.getByRole('switch', { name: 'Nhận tin từ Sếp' })).toHaveAttribute('aria-checked', 'true'));
    const puts = calls.filter((c) => c.path === '/notify/telegram/inbound' && c.method === 'PUT');
    expect(puts.map((c) => c.body)).toEqual([{ enabled: false }, { enabled: false }, { enabled: true }]); // lần đầu bị 423 rồi gửi lại
  });

  it('bật nhưng máy chủ từ chối (409 TELEGRAM_NOT_PAIRED) → câu thân thiện + Chi tiết kỹ thuật (chuỗi), không render object', async () => {
    const { world } = go({ ...BASE, inbound: { ...PAIRED, enabled: false } });
    world.rejectToggle = true; // vừa bị gỡ ghép ở nơi khác: GET cũ vẫn thấy "đã ghép"
    renderCard();
    const user = userEvent.setup();
    const b = await block();
    await user.click(b.getByRole('switch', { name: 'Nhận tin từ Sếp' }));
    const alert = await b.findByText(INBOUND_ERROR_TEXT.TELEGRAM_NOT_PAIRED);
    expect(alert).toBeInTheDocument();
    expect(screen.getByTestId('telegram-inbound').textContent).not.toContain('[object Object]');
    expect(b.getByRole('switch', { name: 'Nhận tin từ Sếp' })).toHaveAttribute('aria-checked', 'false');
  });

  it('Gỡ ghép: hộp xác nhận → DELETE /pair (PIN) → về trạng thái chưa ghép', async () => {
    const { calls } = go({ ...BASE, inbound: PAIRED });
    renderCard();
    const user = userEvent.setup();
    const b = await block();
    await user.click(b.getByRole('button', { name: 'Gỡ ghép' }));
    const dlg = await screen.findByRole('dialog');
    expect(dlg).toHaveTextContent('Gỡ ghép Telegram?');
    expect(dlg).toHaveTextContent('Báo động, bản tin, nhắc việc vẫn gửi như cũ');
    await userEvent.setup().click(within(dlg).getByRole('button', { name: 'Gỡ ghép' }));
    await waitFor(() => expect(b.getByTestId('telegram-inbound-status')).toHaveTextContent(INBOUND_UNPAIRED_HINT));
    expect(writes(calls).filter((c) => c.method === 'DELETE' && c.path === '/notify/telegram/pair')).toHaveLength(2);
    expect(b.getByRole('switch', { name: 'Nhận tin từ Sếp' })).toHaveAttribute('aria-checked', 'false');
  });

  it('lỗi vòng nhận tin (webhook/máy khác giữ bot) → câu thân thiện + Chi tiết kỹ thuật là chuỗi', async () => {
    go({ ...BASE, inbound: { ...PAIRED, error_code: 'TELEGRAM_POLL_CONFLICT', message: 'Bot đang bị nơi khác giữ kênh nhận tin' } });
    renderCard();
    const err = await screen.findByTestId('telegram-inbound-error');
    expect(err).toHaveTextContent(INBOUND_ERROR_TEXT.TELEGRAM_POLL_CONFLICT);
    const tech = within(err).getByText('Chi tiết kỹ thuật');
    expect(tech.closest('details')).not.toBeNull();
    expect(err.querySelector('code')?.textContent).toContain('TELEGRAM_POLL_CONFLICT');
    expect(err.textContent).not.toContain('[object Object]');
  });

  it('máy chủ cũ (không có khối inbound) → không vẽ phần Nhận tin, thẻ vẫn dùng được', async () => {
    go({ ...BASE, inbound: undefined });
    renderCard();
    expect(await screen.findByTestId('telegram-target')).toHaveTextContent('@gen_sep_bot → chat •••4321');
    expect(screen.queryByTestId('telegram-inbound')).toBeNull();
    expect(screen.getByTestId('telegram-warning')).toHaveTextContent(TELEGRAM_WARNING);
  });

  it('chưa nối Telegram (chưa cấu hình) → không có phần Nhận tin (phải lưu token bot trước)', async () => {
    go({ ...BASE, configured: false, enabled: false, bot_username: null, chat_id_masked: null, last_test: null, inbound: undefined });
    renderCard();
    expect(await screen.findByTestId('telegram-setup')).toBeInTheDocument();
    expect(screen.queryByTestId('telegram-inbound')).toBeNull();
  });
});
