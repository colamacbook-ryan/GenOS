/**
 * v0.1.44 (F-8c) — Kết nối › Telegram ("Báo động & bản tin"): thẻ chưa cấu hình (6 bước BotFather, token, Tìm chat_id,
 * Lưu có PIN), đã cấu hình (@bot → chat che, Gửi thử theo mã lỗi, Tắt cần xác nhận), Trực canh máy chủ; telegramConnStatus.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { TelegramConfig, TelegramHostStatus } from '@gen-harness/contracts';
import { TelegramCard } from '../../src/screens/connections/TelegramCard';
import { telegramConnStatus } from '../../src/screens/connections/connectionsModel';
import {
  BOTFATHER_STEPS,
  FIND_CHAT_EMPTY,
  HOST_FAILED_PREFIX,
  HOST_TEST_POLL_MS,
  HOST_TEST_WAIT_MS,
  HOST_KEY_MISMATCH_TEXT,
  HOST_UNSUPPORTED_TEXT,
  TELEGRAM_ERROR_TEXT,
  TELEGRAM_WARNING,
  TEST_OK_NO_HOST_TEXT,
  TEST_OK_TEXT,
  TOKEN_FORMAT_ERROR,
  hostPollMs,
  hostTestText,
  hostWarning,
  telegramKicker,
  testOkText,
  tokenFormatError,
} from '../../src/screens/connections/telegramModel';
import { BOSS_ERROR_TEXT } from '../../src/guide/bossChecksModel';
import { usePinStore } from '../../src/lib/pinStore';
import { queryClient } from '../../src/lib/queryClient';
import { qk } from '../../src/lib/queries';

const TOKEN = '123456789:AAFakeTokenForTestOnly_abcdefghijkl';

const json = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json', 'X-Request-ID': 'req0123456789abc' },
  });

const HOST: TelegramHostStatus = {
  supported: true, schedule: 'systemd', last_run_at: '2026-10-03T01:00:00Z', state: 'ok', telegram: 'not_configured',
  telegram_error_code: null, incidents: [], test: null,
};
const EMPTY: TelegramConfig = {
  configured: false, enabled: false, bot_username: null, chat_id_masked: null, briefing: true, reminders: true,
  updated_at: null, last_test: null, host: HOST,
};
const SAVED: TelegramConfig = {
  ...EMPTY, configured: true, enabled: true, bot_username: 'gen_harness_sep_bot', chat_id_masked: '•••4321',
  updated_at: '2026-10-03T01:00:00Z', host: { ...HOST, telegram: 'ok' },
};

interface Call {
  path: string;
  method: string;
  body: unknown;
}

interface World {
  config: TelegramConfig;
  chats: Array<{ chat_id: string; name: string; username: string | null }>;
  testCode: string | null;
  saveError?: { status: number; body: Record<string, unknown> };
  pin: boolean;
}

function setup(w: Partial<World> = {}) {
  const world: World = { config: EMPTY, chats: [{ chat_id: '987654321', name: 'Nguyễn Văn A', username: 'nva_test' }], testCode: null, pin: true, ...w };
  const calls: Call[] = [];
  let pinOk = false;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(url), 'http://x').pathname.replace('/api/v1', '');
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ path, method, body });
      if (path === '/notify/telegram' && method === 'GET') return json(200, world.config);
      if (path === '/notify/telegram' && (method === 'PUT' || method === 'DELETE')) {
        if (world.pin && !pinOk) return json(423, { status: 423, code: 'PIN_REQUIRED', title: 'Cần PIN', request_id: 'req0123456789abc' });
        if (method === 'PUT' && world.saveError) return json(world.saveError.status, { status: world.saveError.status, ...world.saveError.body });
        world.config =
          method === 'PUT'
            ? {
                ...SAVED,
                enabled: typeof body.enabled === 'boolean' ? body.enabled : world.config.enabled,
                briefing: typeof body.briefing === 'boolean' ? body.briefing : world.config.briefing,
                reminders: typeof body.reminders === 'boolean' ? body.reminders : world.config.reminders,
                // chat_id bỏ trống ⇒ máy chủ giữ chat cũ.
                chat_id_masked: body.chat_id ? `•••${String(body.chat_id).slice(-4)}` : world.config.chat_id_masked,
                // Như máy chủ (forget_tests): token/chat_id mới ⇒ kết quả Gửi thử cũ bị xoá; chỉ đổi công tắc ⇒ giữ.
                last_test: body.token || body.chat_id ? null : world.config.last_test,
              }
            : EMPTY;
        return json(200, world.config);
      }
      if (path === '/notify/telegram/find-chat') return json(200, { chats: world.chats, error_code: null, message: null });
      if (path === '/notify/telegram/test') {
        const code = world.testCode;
        return json(200, {
          key: 'telegram', status: code ? 'fail' : 'pass', error_code: code, message: code ? `upstream ${code}` : null,
          detail: { bot_username: 'gen_harness_sep_bot', chat_masked: '•••4321' }, checked_at: '2026-10-03T01:05:00Z', runs: 1,
          transient: code === 'TELEGRAM_RATE_LIMITED' ? true : undefined, host_requested: !code,
        });
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

const go = (w: Partial<World> = {}) => {
  const r = setup(w);
  unsubs.push(r.unsub);
  return r;
};
const writes = (calls: Call[]) => calls.filter((c) => c.method !== 'GET');

describe('telegramConnStatus', () => {
  it('chưa cấu hình → Chưa nối; đạt → Đang chạy; lỗi/host failed|key_mismatch/chưa thử → Cần Sếp xử lý', () => {
    expect(telegramConnStatus(undefined)).toBe('not_connected');
    expect(telegramConnStatus(EMPTY)).toBe('not_connected');
    const pass = { status: 'pass' as const, error_code: null, message: null, checked_at: 'x' };
    const fail = { ...pass, status: 'fail' as const, error_code: 'TELEGRAM_BOT_BLOCKED' };
    expect(telegramConnStatus({ ...SAVED, last_test: pass })).toBe('running');
    expect(telegramConnStatus({ ...SAVED, last_test: fail })).toBe('needs_boss');
    expect(telegramConnStatus({ ...SAVED, last_test: null })).toBe('needs_boss');
    expect(telegramConnStatus({ ...SAVED, enabled: false, last_test: pass })).toBe('needs_boss');
    expect(telegramConnStatus({ ...SAVED, last_test: pass, host: { ...HOST, telegram: 'key_mismatch' } })).toBe('needs_boss');
    expect(telegramConnStatus({ ...SAVED, last_test: pass, host: { ...HOST, telegram: 'failed' } })).toBe('needs_boss');
  });

  it('kiểm định dạng token và câu lỗi dùng chung với Việc Sếp cần làm', () => {
    expect(tokenFormatError(TOKEN)).toBeNull();
    expect(tokenFormatError('')).toBeNull();
    for (const bad of ['123:abc', 'abc:AAFakeTokenForTestOnly_abcdefghijkl', `${TOKEN} x`, '123456789AAFakeTokenForTestOnly_abcdefghijkl']) {
      expect(tokenFormatError(bad), bad).toBe(TOKEN_FORMAT_ERROR);
    }
    for (const [code, text] of Object.entries(TELEGRAM_ERROR_TEXT)) expect(BOSS_ERROR_TEXT[code]).toBe(text);
  });
});

describe('Kết nối › Telegram (thẻ)', () => {
  it('chưa cấu hình: viên "Chưa nối", đủ 6 bước BotFather đánh số, ô token kiểu password có nút hiện/ẩn, câu cảnh báo cố định', async () => {
    go();
    const { container } = renderCard();
    const steps = await screen.findByTestId('botfather-steps');
    expect(steps.tagName).toBe('OL');
    expect(within(steps).getAllByRole('listitem').map((li) => li.textContent)).toEqual([...BOTFATHER_STEPS]);
    expect(BOTFATHER_STEPS).toHaveLength(6);
    expect(container.querySelector('.conn-pill')).toHaveTextContent('Chưa nối');
    const token = screen.getByLabelText('Token');
    expect(token).toHaveAttribute('type', 'password');
    await userEvent.setup().click(screen.getByRole('button', { name: 'Hiện mật khẩu' }));
    expect(token).toHaveAttribute('type', 'text');
    expect(screen.getByTestId('telegram-warning')).toHaveTextContent(TELEGRAM_WARNING);
    expect(screen.getByRole('switch', { name: 'Gửi bản tin 07:30/17:30' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('switch', { name: 'Gửi nhắc việc' })).toHaveAttribute('aria-checked', 'true');
  });

  it('token sai định dạng → câu lỗi thân thiện, KHÔNG gọi API (Tìm chat_id lẫn Lưu)', async () => {
    const { calls } = go();
    renderCard();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText('Token'), '12345:ngan-qua');
    await user.click(screen.getByRole('button', { name: 'Tìm chat_id' }));
    expect(await screen.findByText(TOKEN_FORMAT_ERROR)).toBeInTheDocument();
    await user.type(screen.getByLabelText('chat_id'), '987654321');
    await user.click(screen.getByRole('button', { name: 'Lưu' }));
    expect(screen.getByText(TOKEN_FORMAT_ERROR)).toBeInTheDocument();
    expect(writes(calls)).toEqual([]);
  });

  it('Tìm chat_id có kết quả → chọn tên điền chat_id; không có → hướng dẫn gửi tin rồi bấm lại', async () => {
    const { calls, world } = go();
    const { unmount } = renderCard();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText('Token'), TOKEN);
    await user.click(screen.getByRole('button', { name: 'Tìm chat_id' }));
    await user.click(await screen.findByRole('button', { name: 'Nguyễn Văn A (@nva_test)' }));
    expect(screen.getByLabelText('chat_id')).toHaveValue('987654321');
    expect(calls.find((c) => c.path === '/notify/telegram/find-chat')!.body).toEqual({ token: TOKEN });
    unmount();
    queryClient.clear();
    world.chats = [];
    renderCard();
    await user.type(await screen.findByLabelText('Token'), TOKEN);
    await user.click(screen.getByRole('button', { name: 'Tìm chat_id' }));
    expect(await screen.findByTestId('telegram-find-empty')).toHaveTextContent(FIND_CHAT_EMPTY);
  });

  it('Lưu (PIN) gửi PUT đúng thân; xong thì hiện "@bot → chat •••…", token không còn trong DOM', async () => {
    const { calls } = go();
    const { container } = renderCard();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText('Token'), TOKEN);
    await user.type(screen.getByLabelText('chat_id'), '987654321');
    await user.click(screen.getByRole('switch', { name: 'Gửi nhắc việc' }));
    await user.click(screen.getByRole('button', { name: 'Lưu' }));
    expect(await screen.findByTestId('telegram-target')).toHaveTextContent('@gen_harness_sep_bot → chat •••4321');
    const puts = calls.filter((c) => c.method === 'PUT');
    expect(puts).toHaveLength(2); // 423 PIN rồi gửi lại
    expect(puts[1].body).toEqual({ token: TOKEN, chat_id: '987654321', enabled: true, briefing: true, reminders: false });
    expect(container.innerHTML).not.toContain(TOKEN);
    expect(container.innerHTML).not.toContain('AAFakeToken');
  });

  it('Lưu: 422 hiện lỗi tại ô; 409 TELEGRAM_TOKEN_REJECTED → câu theo mã + Chi tiết kỹ thuật (chuỗi)', async () => {
    const { world } = go({ pin: false, saveError: { status: 422, body: { code: 'VALIDATION', title: 'x', errors: { chat_id: 'chat_id chưa đúng dạng' } } } });
    const { unmount } = renderCard();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText('Token'), TOKEN);
    await user.type(screen.getByLabelText('chat_id'), 'abc');
    await user.click(screen.getByRole('button', { name: 'Lưu' }));
    expect(await screen.findByText('chat_id chưa đúng dạng')).toBeInTheDocument();
    unmount();
    queryClient.clear();
    world.saveError = { status: 409, body: { code: 'TELEGRAM_TOKEN_REJECTED', title: 'Telegram từ chối token', request_id: 'req0123456789abc' } };
    const { container } = renderCard();
    await user.type(await screen.findByLabelText('Token'), TOKEN);
    await user.type(screen.getByLabelText('chat_id'), '987654321');
    await user.click(screen.getByRole('button', { name: 'Lưu' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(TELEGRAM_ERROR_TEXT.TELEGRAM_TOKEN_REJECTED);
    expect(within(alert).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(alert.querySelector('code')!.textContent).toContain('TELEGRAM_TOKEN_REJECTED');
    expect(container.textContent).not.toContain('[object Object]');
  });

  it('đã cấu hình: @bot và chat che, không ô token, không token trong DOM; Gửi thử Đạt', async () => {
    go({ config: SAVED });
    const { container } = renderCard();
    expect(await screen.findByTestId('telegram-target')).toHaveTextContent('@gen_harness_sep_bot → chat •••4321');
    expect(screen.queryByLabelText(/Token/)).toBeNull();
    expect(container.querySelector('input[type="password"]')).toBeNull();
    expect(container.innerHTML).not.toMatch(/\d{5,12}:[A-Za-z0-9_-]{30,}/);
    await userEvent.setup().click(screen.getByRole('button', { name: 'Gửi thử' }));
    expect(await screen.findByTestId('telegram-test-result')).toHaveTextContent(TEST_OK_TEXT);
  });

  it.each(['TELEGRAM_TOKEN_REJECTED', 'TELEGRAM_CHAT_NOT_FOUND', 'TELEGRAM_BOT_BLOCKED', 'TELEGRAM_RATE_LIMITED', 'TELEGRAM_UNREACHABLE', 'TELEGRAM_NOT_CONFIGURED'])(
    'Gửi thử lỗi %s → đúng câu + "Chi tiết kỹ thuật" là chuỗi',
    async (code) => {
      go({ config: SAVED, testCode: code });
      const { container } = renderCard();
      await userEvent.setup().click(await screen.findByRole('button', { name: 'Gửi thử' }));
      const res = await screen.findByTestId('telegram-test-result');
      expect(res).toHaveTextContent(TELEGRAM_ERROR_TEXT[code]);
      expect(within(res).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
      expect(res.querySelector('code')!.textContent).toContain(`Mã lỗi ${code}`);
      expect(container.textContent).not.toContain('[object Object]');
    },
  );

  it('BOT_BLOCKED chỉ cách bấm Bắt đầu (Start) — cùng chữ với máy chủ và 6 bước BotFather', () => {
    expect(TELEGRAM_ERROR_TEXT.TELEGRAM_BOT_BLOCKED).toMatch(/bấm Bắt đầu \(Start\)/);
    expect(TELEGRAM_ERROR_TEXT.TELEGRAM_CHAT_NOT_FOUND).toMatch(/bấm Bắt đầu \(Start\)/);
    expect(BOTFATHER_STEPS[0]).toMatch(/bấm Bắt đầu \(Start\)\.$/);
    expect(BOTFATHER_STEPS[5]).toMatch(/bấm Bắt đầu \(Start\)/);
    expect(FIND_CHAT_EMPTY).toBe('Chưa thấy tin nào — mở bot, bấm Bắt đầu (Start), gửi một tin rồi bấm Tìm chat_id lần nữa.');
  });

  it('Trực canh máy chủ: key_mismatch → "bấm Lưu lại một lần" — một lần bấm = PUT {} (PIN), giữ token + chat_id; genh cũ → "Cập nhật genh"', async () => {
    const { calls } = go({ config: { ...SAVED, host: { ...HOST, telegram: 'key_mismatch' } } });
    const { unmount } = renderCard();
    expect(await screen.findByTestId('telegram-host-warning')).toHaveTextContent(HOST_KEY_MISMATCH_TEXT);
    expect(screen.getByText('Cần Sếp xử lý')).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Lưu lại' }));
    await waitFor(() => expect(screen.queryByTestId('telegram-host-warning')).toBeNull());
    const puts = calls.filter((c) => c.method === 'PUT');
    expect(puts).toHaveLength(2); // 423 PIN rồi gửi lại
    expect(puts[1].body).toEqual({});
    expect(screen.queryByLabelText(/Token mới/)).toBeNull(); // không mở form, không đòi tìm lại chat_id
    expect(screen.getByTestId('telegram-target')).toHaveTextContent('chat •••4321');
    unmount();
    queryClient.clear();
    go({ config: { ...SAVED, host: { ...HOST, supported: false, schedule: null, last_run_at: null, state: null, telegram: null } } });
    renderCard();
    expect(await screen.findByTestId('telegram-host-warning')).toHaveTextContent(HOST_UNSUPPORTED_TEXT);
  });

  it('Lưu token mới sau khi Gửi thử Đạt: viên "Cần Sếp xử lý", không còn dòng "Gửi thử gần nhất" của cấu hình cũ', async () => {
    const PASSED: TelegramConfig = { ...SAVED, last_test: { status: 'pass', error_code: null, message: null, checked_at: '2026-10-03T01:05:00Z' } };
    go({ config: PASSED });
    const { container } = renderCard();
    expect(await screen.findByTestId('telegram-last-test')).toHaveTextContent('Gửi thử gần nhất: Đạt');
    expect(container.querySelector('.conn-pill')).toHaveTextContent('Đang chạy');
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Đổi token/chat_id' }));
    await user.type(screen.getByLabelText('Token mới (bỏ trống để giữ)'), TOKEN);
    await user.click(screen.getByRole('button', { name: 'Lưu' }));
    expect(await screen.findByText('Chưa Gửi thử lần nào — bấm Gửi thử để chắc tin tới được điện thoại.')).toBeInTheDocument();
    expect(screen.queryByTestId('telegram-last-test')).toBeNull();
    expect(container.querySelector('.conn-pill')).toHaveTextContent('Cần Sếp xử lý');
  });

  it('kicker theo mục đang bật (không hứa bản tin/nhắc việc đã tắt)', async () => {
    go({ config: { ...SAVED, briefing: false } });
    const { container } = renderCard();
    await screen.findByTestId('telegram-target');
    expect(container.textContent).toContain('Báo động sự cố và nhắc việc');
    expect(container.textContent).not.toContain('Báo động sự cố, bản tin 07:30/17:30 và nhắc việc');
    expect(telegramKicker({ ...SAVED, briefing: false, reminders: false })).toBe('Báo động sự cố qua bot Telegram của Sếp');
    expect(telegramKicker(SAVED)).toBe('Báo động sự cố, bản tin 07:30/17:30 và nhắc việc');
    expect(telegramKicker(EMPTY)).toBe('Nhận báo động & bản tin qua bot Telegram của Sếp');
  });

  it('Đổi token/chat_id: bỏ trống chat_id = giữ chat cũ; chỉ tắt bản tin cũng lưu được (không đòi Tìm chat_id)', async () => {
    const { calls } = go({ config: SAVED });
    renderCard();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Đổi token/chat_id' }));
    expect(screen.getByLabelText('chat_id mới (bỏ trống để giữ •••4321)')).toHaveValue('');
    await user.click(screen.getByRole('switch', { name: 'Gửi bản tin 07:30/17:30' }));
    await user.click(screen.getByRole('button', { name: 'Lưu' }));
    expect(await screen.findByTestId('telegram-target')).toHaveTextContent('chat •••4321');
    const puts = calls.filter((c) => c.method === 'PUT');
    expect(puts[puts.length - 1].body).toEqual({ enabled: true, briefing: false, reminders: true });
  });

  it('v0.1.70 (P3): đang Tạm dừng rồi Đổi token/chat_id → Lưu KHÔNG tự bật lại (giữ enabled:false); thẻ vẫn "đang tắt"', async () => {
    const { calls } = go({ config: { ...SAVED, enabled: false } });
    renderCard();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Đổi token/chat_id' }));
    await user.click(screen.getByRole('switch', { name: 'Gửi nhắc việc' }));
    await user.click(screen.getByRole('button', { name: 'Lưu' }));
    expect(await screen.findByTestId('telegram-target')).toHaveTextContent('đang tắt');
    const puts = calls.filter((c) => c.method === 'PUT');
    expect(puts[puts.length - 1].body).toEqual({ enabled: false, briefing: true, reminders: false });
    expect(screen.getByRole('button', { name: 'Bật lại' })).toBeInTheDocument();
  });

  it('Trực canh máy chủ gửi lỗi (failed) → câu theo mã + Chi tiết kỹ thuật; dòng tin thử từ máy chủ; Gửi thử không hứa tin thứ hai', async () => {
    go({
      config: {
        ...SAVED,
        last_test: { status: 'pass', error_code: null, message: null, checked_at: '2026-10-03T01:00:00Z' },
        host: { ...HOST, telegram: 'failed', telegram_error_code: 'TELEGRAM_BOT_BLOCKED', test: { at: '2026-10-03T01:01:00Z', ok: false, error_code: 'TELEGRAM_BOT_BLOCKED' } },
      },
    });
    const { container } = renderCard();
    const warn = await screen.findByTestId('telegram-host-warning');
    expect(warn).toHaveTextContent(HOST_FAILED_PREFIX);
    expect(warn).toHaveTextContent(TELEGRAM_ERROR_TEXT.TELEGRAM_BOT_BLOCKED);
    expect(within(warn).getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(warn.querySelector('code')!.textContent).toContain('Mã lỗi TELEGRAM_BOT_BLOCKED');
    expect(within(warn).queryByRole('button', { name: 'Lưu lại' })).toBeNull();
    expect(screen.getByTestId('telegram-host-test')).toHaveTextContent(`Tin thử từ máy chủ: Lỗi · ${TELEGRAM_ERROR_TEXT.TELEGRAM_BOT_BLOCKED} · 03/10 08:01`);
    await userEvent.setup().click(screen.getByRole('button', { name: 'Gửi thử' }));
    expect(await screen.findByTestId('telegram-test-result')).toHaveTextContent(TEST_OK_NO_HOST_TEXT);
    expect(screen.getByTestId('telegram-test-result')).not.toHaveTextContent('trực canh');
    expect(container.textContent).not.toContain('[object Object]');
  });

  it('hostPollMs: chỉ hỏi lại khi đang chờ tin thử từ máy chủ, dừng khi kết quả đổi hoặc quá 2 phút', () => {
    const wait = { since: 1_000_000, prevAt: '2026-10-03T01:00:00Z' };
    expect(hostPollMs(null, null, 1_000_000)).toBe(false);
    expect(hostPollMs(wait, '2026-10-03T01:00:00Z', 1_005_000)).toBe(HOST_TEST_POLL_MS);
    expect(hostPollMs(wait, '2026-10-03T01:06:00Z', 1_005_000)).toBe(false);
    expect(hostPollMs(wait, '2026-10-03T01:00:00Z', 1_000_000 + HOST_TEST_WAIT_MS + 1)).toBe(false);
    expect(hostPollMs({ since: 1_000_000, prevAt: null }, null, 1_005_000)).toBe(HOST_TEST_POLL_MS);
    expect(hostPollMs({ since: 1_000_000, prevAt: null }, '2026-10-03T01:06:00Z', 1_005_000)).toBe(false);
  });

  it('hostWarning / hostTestText / testOkText', () => {
    const fmt = (iso: string) => iso.slice(11, 16);
    expect(hostWarning({ ...HOST, telegram: 'ok' })).toBeNull();
    expect(hostWarning({ ...HOST, telegram: 'failed', telegram_error_code: null })).toMatch(/^Trực canh máy chủ chưa gửi được tin Telegram: /);
    expect(hostTestText(null, fmt)).toBeNull();
    expect(hostTestText({ at: '2026-10-03T01:05:00Z', ok: true, error_code: null }, fmt)).toBe('Tin thử từ máy chủ: Đạt · 01:05');
    expect(testOkText({ host_requested: true }, { ...HOST, telegram: 'ok' })).toBe(TEST_OK_TEXT);
    expect(testOkText({ host_requested: true }, { ...HOST, telegram: 'key_mismatch' })).toBe(TEST_OK_NO_HOST_TEXT);
    expect(testOkText({ host_requested: false }, { ...HOST, telegram: 'ok' })).toBe(TEST_OK_NO_HOST_TEXT);
    // Mọi câu lỗi Telegram kết thúc bằng dấu chấm (như các câu BOSS_ERROR_TEXT khác).
    for (const text of Object.values(TELEGRAM_ERROR_TEXT)) expect(text.endsWith('.'), text).toBe(true);
  });

  it('Trực canh máy chủ: lịch, lần chạy gần nhất (giờ tổ chức), sự cố đang mở', async () => {
    go({
      config: {
        ...SAVED,
        host: { ...HOST, telegram: 'ok', state: 'issues', incidents: [{ key: 'disk.low', severity: 'bad', title: 'Ổ đĩa sắp đầy', since: '2026-10-03T00:00:00Z' }] },
      },
    });
    renderCard();
    const host = await screen.findByTestId('telegram-host');
    expect(host).toHaveTextContent('Trực canh máy chủ');
    expect(host).toHaveTextContent('systemd');
    expect(host).toHaveTextContent(/Lần chạy gần nhất: 03\/10 08:00/);
    expect(host).toHaveTextContent('Sự cố đang mở: Ổ đĩa sắp đầy');
  });

  it('v0.1.70 (F092): "Tạm dừng" chỉ tắt gửi tin (PUT {enabled:false}, PIN), KHÔNG xoá token; "Bật lại" bật lại — không DELETE, không về form nối từ đầu', async () => {
    const { calls } = go({ config: SAVED });
    renderCard();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Tạm dừng' }));
    await waitFor(() => expect(screen.getByTestId('telegram-target')).toHaveTextContent('đang tắt'));
    expect(screen.queryByTestId('botfather-steps')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Tạm dừng' })).toBeNull();
    // PIN: lần đầu 423 rồi gửi lại cùng thân ⇒ chỉ có PUT {enabled:false}, không DELETE.
    expect(new Set(writes(calls).map((c) => `${c.method} ${JSON.stringify(c.body)}`))).toEqual(new Set(['PUT {"enabled":false}']));
    await user.click(screen.getByRole('button', { name: 'Bật lại' }));
    await waitFor(() => expect(screen.getByTestId('telegram-target')).toHaveTextContent('đang bật'));
    expect(writes(calls).at(-1)).toMatchObject({ method: 'PUT', body: { enabled: true } });
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
  });

  it('Tắt Telegram cần xác nhận: bấm Giữ lại không gọi DELETE; xác nhận → DELETE (PIN) → về trạng thái chưa cấu hình', async () => {
    const { calls } = go({ config: SAVED });
    renderCard();
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Tắt Telegram' }));
    const dlg = await screen.findByRole('dialog');
    await user.click(within(dlg).getByRole('button', { name: 'Giữ lại' }));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    await user.click(screen.getByRole('button', { name: 'Tắt Telegram' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Tắt Telegram' }));
    expect(await screen.findByTestId('botfather-steps')).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.method === 'DELETE')).toHaveLength(2));
  });
});
