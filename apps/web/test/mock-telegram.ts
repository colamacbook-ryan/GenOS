/**
 * Mock v0.1.44 (F-8c) — Kết nối › Telegram ("Báo động & bản tin"), đúng hợp đồng HTTP api ↔ web của v0.1.44 (api làm
 * song song). CHỈ Owner (403 vai trò khác); PUT/DELETE /notify/telegram cần PIN `notify.change` (423 như mock khác).
 * Token KHÔNG BAO GIỜ có trong phản hồi. (Gói chẩn đoán + `POST /client-errors` của F-4b ở `mock-diagnostics.ts`.)
 *
 * Mặc định: chưa cấu hình; Trực canh máy chủ được hỗ trợ (systemd, chạy 40 giây trước).
 * - PUT: token sai dạng → 422 `errors.token`; token chứa "REJECT" → 409 TELEGRAM_TOKEN_REJECTED; chat_id sai (máy chủ
 *   chỉ nhận dãy số `^-?\d{1,20}$`) → 422. Đã cấu hình: token/chat_id trống = giữ; `{}` = "Lưu lại". Token/chat_id đổi
 *   (hoặc nối lần đầu) và DELETE ⇒ xoá kết quả Gửi thử cũ (cả dòng 6 boss_checks).
 * - find-chat: mặc định một chat (987654321 · "Nguyễn Văn A"); `seed {findChats:'none'}` → rỗng; token chứa "REJECT" →
 *   `error_code` TELEGRAM_TOKEN_REJECTED; không token + chưa cấu hình → TELEGRAM_NOT_CONFIGURED.
 * - Gửi thử (`/notify/telegram/test`, `/boss-checks/telegram/run`): chưa cấu hình → TELEGRAM_NOT_CONFIGURED; `seed
 *   {testError:'TELEGRAM_BOT_BLOCKED'}` (hoặc chat_id 111) → lỗi theo mã; TELEGRAM_RATE_LIMITED là lỗi tạm (không ghi).
 * - Trực canh máy chủ: `seed {host:'key_mismatch'|'unsupported'|'failed'|'ok'}`.
 *
 * v0.1.64 (2 chiều): `inbound` — `POST /notify/telegram/pair/start` (PIN) cấp mã 123456 (sống 10 phút), `seed {pairDone:true}`
 *   giả lập Sếp đã gửi `/start 123456` cho bot (⇒ đã ghép + bật nhận tin); `PUT …/inbound {enabled}` (PIN; bật khi chưa ghép ⇒
 *   409 TELEGRAM_NOT_PAIRED); `DELETE …/pair` (PIN) gỡ ghép. `seed {inboundError:'TELEGRAM_POLL_CONFLICT'}` giả lỗi vòng nhận tin.
 *
 * Hook e2e: `POST /api/v1/__mock/p3/telegram/seed {…}`.
 */
import type { BossCheck, TelegramConfig, TelegramHostStatus, TelegramInbound } from '@gen-harness/contracts';
import type { P2Ctx } from './mock-phase2';

export const TELEGRAM_TOKEN_RE = /^\d{5,12}:[A-Za-z0-9_-]{30,64}$/;

/** Kết quả một lượt Gửi thử — mock-boss-checks ghi (trừ lỗi tạm). */
export type TelegramOutcome = Pick<BossCheck, 'status' | 'error_code' | 'message' | 'detail'> & { transient?: boolean; host_requested: boolean };

interface Opts {
  fresh: boolean;
  emit: (type: string, data: unknown) => void;
  /** Ghi kết quả Gửi thử vào boss_checks (gắn sau khi tạo mock-boss-checks). */
  record: (o: TelegramOutcome) => BossCheck;
  /** Xoá kết quả Gửi thử cũ ở boss_checks (đổi token/chat_id, Tắt Telegram) — như api `forget_tests`. */
  forget?: () => void;
}

interface Seed {
  configured?: boolean;
  testError?: string | null;
  findChats?: 'some' | 'none';
  host?: 'ok' | 'key_mismatch' | 'unsupported' | 'failed';
  /** v0.1.64: giả lập Sếp đã gửi `/start <mã>` cho bot (cần đang có mã ghép sống). */
  pairDone?: boolean;
  /** v0.1.64: đã ghép sẵn (+ bật nhận tin). */
  paired?: boolean;
  inboundError?: string | null;
}

const MESSAGES: Record<string, string> = {
  TELEGRAM_NOT_CONFIGURED: 'Chưa cấu hình Telegram',
  TELEGRAM_TOKEN_REJECTED: 'Telegram từ chối token (401 Unauthorized)',
  TELEGRAM_CHAT_NOT_FOUND: 'Bad Request: chat not found',
  TELEGRAM_BOT_BLOCKED: 'Forbidden: bot was blocked by the user',
  TELEGRAM_RATE_LIMITED: 'Too Many Requests: retry after 30',
  TELEGRAM_UNREACHABLE: 'Không kết nối được api.telegram.org',
  TELEGRAM_POLL_CONFLICT: 'Conflict: terminated by other getUpdates request',
};

/** Token giả CHỈ cho test (vector cố định của hợp đồng) — không phải bot thật. */
const FAKE_TOKEN = '123456789:AAFakeTokenForTestOnly_abcdefghijkl';
const BOT = 'gen_harness_sep_bot';
const mask = (chatId: string) => `•••${chatId.slice(-4)}`;

export function createMock(opts: Opts) {
  const now = () => new Date().toISOString();
  const s = {
    configured: false,
    token: null as string | null,
    chatId: null as string | null,
    enabled: false,
    briefing: true,
    reminders: true,
    updatedAt: null as string | null,
    lastTest: null as TelegramConfig['last_test'],
    testError: null as string | null,
    findChats: 'some' as 'some' | 'none',
    host: 'ok' as NonNullable<Seed['host']>,
    hostTest: null as TelegramHostStatus['test'],
    // v0.1.64 — Telegram 2 chiều
    paired: null as { masked: string; at: string } | null,
    inboundEnabled: false,
    pairExpires: null as number | null,
    inboundError: null as string | null,
  };

  const inboundView = (): TelegramInbound => {
    const pending = s.pairExpires !== null && s.pairExpires > Date.now();
    return {
      enabled: !!s.paired && s.inboundEnabled,
      paired: !!s.paired,
      owner_masked: s.paired?.masked ?? null,
      paired_at: s.paired?.at ?? null,
      pair_pending: pending,
      pair_expires_at: pending ? new Date(s.pairExpires as number).toISOString() : null,
      error_code: s.inboundError,
      message: s.inboundError ? (MESSAGES[s.inboundError] ?? s.inboundError) : null,
    };
  };

  const hostView = (): TelegramHostStatus => {
    if (s.host === 'unsupported') {
      return { supported: false, schedule: null, last_run_at: null, state: null, telegram: null, telegram_error_code: null, incidents: [], test: null };
    }
    const telegram =
      s.host === 'key_mismatch' ? 'key_mismatch' : s.host === 'failed' ? 'failed' : s.configured ? (s.enabled ? 'ok' : 'disabled') : 'not_configured';
    return {
      supported: true,
      schedule: 'systemd',
      last_run_at: new Date(Date.now() - 40_000).toISOString(),
      state: s.host === 'failed' ? 'issues' : 'ok',
      telegram,
      telegram_error_code: s.host === 'failed' ? 'TELEGRAM_BOT_BLOCKED' : null,
      incidents:
        s.host === 'failed' ? [{ key: 'backup.stale', severity: 'warn', title: 'Sao lưu đã cũ', since: new Date(Date.now() - 3 * 3600_000).toISOString() }] : [],
      test: s.hostTest,
    };
  };

  const view = (): TelegramConfig => ({
    configured: s.configured,
    enabled: s.configured && s.enabled,
    bot_username: s.configured ? BOT : null,
    chat_id_masked: s.configured && s.chatId ? mask(s.chatId) : null,
    briefing: s.briefing,
    reminders: s.reminders,
    updated_at: s.updatedAt,
    last_test: s.configured ? s.lastTest : null,
    host: hostView(),
    inbound: inboundView(),
  });

  /** Một lượt Gửi thử (dùng chung cho `/notify/telegram/test` và `/boss-checks/telegram/run`). */
  const runTest = (): TelegramOutcome => {
    const detail: BossCheck['detail'] = s.configured ? { bot_username: BOT, chat_masked: s.chatId ? mask(s.chatId) : null } : {};
    const code = !s.configured ? 'TELEGRAM_NOT_CONFIGURED' : (s.testError ?? (s.chatId === '111' ? 'TELEGRAM_BOT_BLOCKED' : null));
    if (code) {
      const transient = code === 'TELEGRAM_RATE_LIMITED';
      if (!transient && s.configured) s.lastTest = { status: 'fail', error_code: code, message: MESSAGES[code] ?? code, checked_at: now() };
      return { status: 'fail', error_code: code, message: MESSAGES[code] ?? code, detail, ...(transient ? { transient: true } : {}), host_requested: false };
    }
    s.lastTest = { status: 'pass', error_code: null, message: null, checked_at: now() };
    // Trực canh máy chủ (genh) gửi thêm một tin trong ~1 phút — mock ghi luôn kết quả của genh.
    const host = s.host !== 'unsupported';
    if (host) s.hostTest = s.host === 'failed' ? { at: now(), ok: false, error_code: 'TELEGRAM_BOT_BLOCKED' } : { at: now(), ok: true, error_code: null };
    return { status: 'pass', error_code: null, message: null, detail, host_requested: host };
  };

  const needPin = (ctx: P2Ctx) =>
    ctx.needPin() ? ctx.problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation: 'notify.change' } }) : false;

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p, body, reply, problem } = ctx;
    if (p !== '/notify/telegram' && !p.startsWith('/notify/telegram/')) return false;
    if (ctx.role !== 'owner') return problem(403, 'FORBIDDEN', 'Vai trò của bạn không có quyền thao tác này');

    if (p === '/notify/telegram' && m === 'GET') return reply(200, view());
    if (p === '/notify/telegram' && m === 'PUT') {
      if (needPin(ctx)) return true;
      const token = typeof body.token === 'string' ? body.token.trim() : '';
      const chatId = typeof body.chat_id === 'string' ? body.chat_id.trim() : '';
      const errors: Record<string, string> = {};
      if (token && !TELEGRAM_TOKEN_RE.test(token)) errors.token = 'Token bot không đúng dạng — chép nguyên dòng BotFather gửi (dạng 123456789:AA…)';
      if (!token && !s.configured) errors.token = 'Dán token bot lấy từ BotFather';
      const nextChat = chatId || (s.configured ? (s.chatId ?? '') : '');
      // Đúng CHAT_RE của máy chủ (service.py): chỉ dãy số, có thể có dấu trừ.
      if (!/^-?\d{1,20}$/.test(nextChat)) errors.chat_id = 'chat_id là một dãy số — bấm Tìm chat_id sau khi Sếp đã nhắn bot';
      if (Object.keys(errors).length) return problem(422, 'VALIDATION', 'Dữ liệu chưa hợp lệ', { errors });
      if (token.includes('REJECT')) return problem(409, 'TELEGRAM_TOKEN_REJECTED', 'Telegram từ chối token');
      // Như api: token/chat_id THẬT SỰ đổi (hoặc nối lần đầu) ⇒ kết quả Gửi thử cũ bị xoá (cả dòng 6); chỉ bật/tắt
      // bản tin/nhắc việc hoặc "Lưu lại" cùng cấu hình ⇒ giữ.
      const changed = !s.configured || (!!token && token !== s.token) || nextChat !== s.chatId;
      // Như api: đổi sang BOT khác ⇒ gỡ ghép + tắt nhận tin (bot mới là ranh giới tin cậy mới).
      if (token && s.token && token !== s.token) Object.assign(s, { paired: null, inboundEnabled: false, pairExpires: null });
      if (token) s.token = token;
      s.chatId = nextChat;
      if (changed) {
        s.lastTest = null;
        opts.forget?.();
      }
      s.configured = true;
      s.enabled = body.enabled !== false;
      if (typeof body.briefing === 'boolean') s.briefing = body.briefing;
      if (typeof body.reminders === 'boolean') s.reminders = body.reminders;
      s.updatedAt = now();
      if (s.host === 'key_mismatch') s.host = 'ok'; // Lưu lại = api ghi lại run/telegram.json bằng khoá hiện tại.
      return reply(200, view());
    }
    if (p === '/notify/telegram' && m === 'DELETE') {
      if (needPin(ctx)) return true;
      Object.assign(s, { configured: false, token: null, chatId: null, enabled: false, updatedAt: now(), lastTest: null, hostTest: null, paired: null, inboundEnabled: false, pairExpires: null });
      opts.forget?.();
      return reply(200, view());
    }
    if (p === '/notify/telegram/find-chat' && m === 'POST') {
      const token = typeof body.token === 'string' && body.token ? body.token : s.token;
      if (!token) return reply(200, { chats: [], error_code: 'TELEGRAM_NOT_CONFIGURED', message: MESSAGES.TELEGRAM_NOT_CONFIGURED });
      if (token.includes('REJECT')) return reply(200, { chats: [], error_code: 'TELEGRAM_TOKEN_REJECTED', message: MESSAGES.TELEGRAM_TOKEN_REJECTED });
      const chats = s.findChats === 'none' ? [] : [{ chat_id: '987654321', name: 'Nguyễn Văn A', username: 'nva_test' }];
      return reply(200, { chats, error_code: null, message: null });
    }
    if (p === '/notify/telegram/pair/start' && m === 'POST') {
      if (needPin(ctx)) return true;
      if (!s.configured) return problem(409, 'TELEGRAM_NOT_CONFIGURED', 'Chưa nối Telegram — lưu token bot trước rồi mới ghép');
      s.pairExpires = Date.now() + 10 * 60_000;
      return reply(200, { ...view(), pair: { code: '123456', expires_at: new Date(s.pairExpires).toISOString(), bot_username: BOT } });
    }
    if (p === '/notify/telegram/inbound' && m === 'PUT') {
      if (needPin(ctx)) return true;
      const enabled = body.enabled === true;
      if (enabled && !s.paired) return problem(409, 'TELEGRAM_NOT_PAIRED', 'Chưa ghép tài khoản Telegram của Sếp — bấm Ghép Telegram trước');
      s.inboundEnabled = enabled;
      return reply(200, view());
    }
    if (p === '/notify/telegram/pair' && m === 'DELETE') {
      if (needPin(ctx)) return true;
      Object.assign(s, { paired: null, inboundEnabled: false, pairExpires: null });
      return reply(200, view());
    }
    if (p === '/notify/telegram/test' && m === 'POST') {
      const o = runTest();
      const c = o.transient ? { ...o, key: 'telegram' as const, checked_at: now(), runs: 0 } : opts.record(o);
      opts.emit('boss_checks.update', { key: 'telegram' });
      return reply(200, { ...c, host_requested: o.host_requested });
    }
    return false;
  }

  const seed = (b: Seed = {}) => {
    if (b.configured === true) Object.assign(s, { configured: true, enabled: true, token: FAKE_TOKEN, chatId: '987654321', updatedAt: now() });
    if (b.configured === false) Object.assign(s, { configured: false, enabled: false, token: null, chatId: null, lastTest: null });
    if (b.testError !== undefined) s.testError = b.testError;
    if (b.findChats) s.findChats = b.findChats;
    if (b.host) s.host = b.host;
    if (b.paired === true) Object.assign(s, { paired: { masked: '•••4321', at: now() }, inboundEnabled: true, pairExpires: null });
    if (b.pairDone === true && s.pairExpires !== null && s.pairExpires > Date.now()) {
      Object.assign(s, { paired: { masked: '•••4321', at: now() }, inboundEnabled: true, pairExpires: null });
    }
    if (b.inboundError !== undefined) s.inboundError = b.inboundError;
    return view();
  };

  return {
    handle,
    runTest,
    hooks: { seed, view } as Record<string, (...args: never[]) => unknown>,
    dispose: () => {},
  };
}
