/**
 * Mock `/api/v1/boss-checks*` (v0.1.39, F-74) — "Việc Sếp cần làm": như `apps/api/gh/boss_checks` (hợp đồng gói 1).
 * CHỈ Owner (403 vai trò khác), PIN cho `hub` và `agy_switch` (423 như mock khác), mã lạ 404. Lỗi nghiệp vụ vẫn 200
 * với `status: 'fail'` + `error_code`. Kết quả giữ trong state của mock (tải lại trang vẫn còn — đọc từ API).
 *
 * - hub: dùng chung lượt "Kiểm tra" của `mock-p4-mcp` (token chứa "sai" → HUB_TOKEN_REJECTED). v0.1.49: Đạt kèm
 *   `detail.read_scopes` {calendar, mail, tasks, drive} (token chứa "thieu" → lịch + mail false; vẫn Đạt, quyền đọc thêm không bắt buộc).
 * - facebook: trả `pending`; lần GET cách lượt chạy ≥ 1,5 giây thì thành `pass` (như việc đọc chạy nền).
 * - agy_call / agy_switch / claude_call: theo hồ sơ CLI của `mock-phase2` (`account` = email hồ sơ đang dùng — CHỈ
 *   trong phản hồi `run`; `GET` như `latest()` thật chỉ có `detail.account_masked`).
 * - agy_login / claude_login: ghi trong LUỒNG đăng nhập CLI của `mock-phase2` (như `_boss_check` thật), không suy khi GET.
 * - `runs` = số bản ghi (cả lỗi).
 * - Lỗi tạm (SOCIAL_BUSY khi đang đọc) trả `transient: true`, không ghi.
 * - jev: theo nguồn `system_one` của `mock-phase2` (không có → JEV_NOT_CONFIGURED).
 * - claude_call đạt mà claude_login chưa đạt (phiên có từ trước v0.1.39) → ghi claude_login 'pass'
 *   (`login_source: existing_session`) như `_adopt_existing_claude_login` của api.
 * - v0.1.55 (Thiết lập gọn): CHỈ dòng 0 `ai` ("Có ít nhất 1 nguồn AI chạy được", `ai_source`) bắt buộc ⇒ required_total 1; mọi dòng kết nối
 *   khác tuỳ chọn. `ai` đạt khi bấm Kiểm tra (`POST /boss-checks/ai_source/run`: gọi thử nguồn AI đầu chuỗi sẵn sàng — nhà cung cấp bật có
 *   model và gọi thử OK, hoặc hồ sơ Google/Claude đang dùng; không có → AI_NO_SOURCE) HOẶC Claude / Google đã gọi thử đạt
 *   (`results.ai_source` giả, `detail.via` = 'claude_call' | 'agy_call', `runs` 0 — như máy chủ). Hook `seedAi {}` ghi 'pass'. Dòng agy KHÔNG còn
 *   đòi đổi qua lại hai tài khoản (bỏ `agy_switch` khỏi `checks`).
 * - telegram (v0.1.44, dòng 6): Gửi thử của `mock-telegram` (chưa cấu hình →
 *   TELEGRAM_NOT_CONFIGURED; TELEGRAM_RATE_LIMITED tạm, không ghi); phản hồi `run` kèm `host_requested`.
 * - remote_access (v0.1.46, F-21, dòng 7; KHÔNG PIN): quyết theo hostname của header Origin
 *   (mock-api.ts chặn `POST /boss-checks/remote_access/run` rồi gọi hook `recordRemote`): public_url local →
 *   REMOTE_NOT_CONFIGURED; host local → REMOTE_OPENED_ON_SERVER; còn lại Đạt (`detail.opened_from`, `access_mode`).
 * - agy_switch: chỉ đếm khi hồ sơ đang dùng TRƯỚC khi đổi (`from_profile`) khác hồ sơ đích (đổi sang chính nó = 0).
 * Hook e2e `POST /api/v1/__mock/p3/bossChecks/seedAgy {}`: đặt sẵn 2 hồ sơ Google an@… (không dùng), binh@… (đang dùng).
 * - facebook_reply (v0.1.47, F-79, dòng 8, KHÔNG bắt buộc): không có nút chạy (POST run → 404);
 *   hook `seedFacebookReply {}` ghi 'pass'.
 * - kho_write (v0.1.50, F-81, dòng 9 "Gen ghi Kho", KHÔNG bắt buộc): không có nút chạy (POST run → 404); máy chủ
 *   tự ghi 'pass' sau lần ghi Kho THẬT đầu tiên (mock-p4-mcp gọi hook `recordKhoWrite`); hook `seedKhoWrite {}` ghi 'pass'. Đạt dòng hub
 *   kèm `detail.write_scopes` {kho} + `write_missing` (token chứa "khongghi" ⇒ kho false; vẫn Đạt).
 * Hook e2e `POST /api/v1/__mock/p3/bossChecks/seedClaude {}`: một hồ sơ Claude đang dùng, CHƯA có bản claude_login.
 * Hook e2e `POST /api/v1/__mock/p3/bossChecks/seedHub {}` (v0.1.54): dòng 1 "Nối Gen-hub" đạt (e2e Gen hướng dẫn: việc boss.hub biến mất).
 * Hook e2e `POST /api/v1/__mock/p3/bossChecks/seedAi {}` (v0.1.55): dòng 0 "nguồn AI" đạt (required_done 1/1).
 */
import { randomUUID } from 'node:crypto';
import type { BossCheck, BossCheckKey, BossOverview, BossRow, CliProfile, HubLink, HubReadScopes, HubWriteScopes, Provider, SocialAccount } from '@gen-harness/contracts';
import type { P2Ctx } from './mock-phase2';
import type { TelegramOutcome } from './mock-telegram';

interface Opts {
  fresh: boolean;
  emit: (type: string, data: unknown) => void;
  hubLink: () => HubLink;
  hubTest: () => { ok: boolean; error: string | null; error_code: string | null; read_scopes?: HubReadScopes; write_scopes?: HubWriteScopes; write_missing?: string[] };
  socialAccounts: () => SocialAccount[];
  cliProfiles: (kind: string) => CliProfile[];
  activateCli: (id: string) => boolean;
  providers: () => Provider[];
  onCliLogin: (fn: (kind: string, ok: boolean, email: string | null) => void) => void;
  /** v0.1.44 (F-8c): một lượt Gửi thử Telegram của mock-telegram. */
  telegramTest: () => TelegramOutcome;
}

/** Như `boss.mask_email` của api: 'binh@x.vn' → 'b***@x.vn'. */
const mask = (email: string | null | undefined): string | null => {
  if (!email || !email.includes('@')) return null;
  const i = email.lastIndexOf('@');
  return i > 0 ? `${email[0]}***@${email.slice(i + 1)}` : null;
};

/** Như `access.is_local_url` của api, cho một hostname trần. */
export function isLocalHost(host: string): boolean {
  const h = host.trim().toLowerCase().replace(/^\[|\]$/g, '');
  if (!h || h === 'localhost' || h.endsWith('.localhost') || h === '::1' || h === '::') return true;
  const v4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(h);
  return !!v4 && (Number(v4[1]) === 127 || v4.slice(1).every((x) => Number(x) === 0));
}

const KEYS: BossCheckKey[] = ['hub', 'facebook', 'agy_login', 'agy_call', 'agy_switch', 'claude_login', 'claude_call', 'jev', 'telegram', 'remote_access', 'facebook_reply', 'kho_write', 'ai_source'];
const RUNNABLE = new Set<BossCheckKey>(['hub', 'facebook', 'agy_call', 'agy_switch', 'claude_call', 'jev', 'telegram', 'remote_access', 'ai_source']);
const NEEDS_PIN = new Set<BossCheckKey>(['hub', 'agy_switch']);
const FB_READ_MS = 1500;

const ROWS: Array<Omit<BossRow, 'done'>> = [
  // v0.1.55: dòng 0 — việc BẮT BUỘC duy nhất; số dòng 1–9 giữ nguyên.
  { row: 0, key: 'ai', title: 'Có ít nhất 1 nguồn AI chạy được', optional: false, checks: ['ai_source'] },
  { row: 1, key: 'hub', title: 'Nối Gen-hub', optional: true, checks: ['hub'] },
  { row: 2, key: 'facebook', title: 'Kết nối Facebook', optional: true, checks: ['facebook'] },
  { row: 3, key: 'agy', title: 'Google (Antigravity)', optional: true, checks: ['agy_login', 'agy_call'] },
  { row: 4, key: 'claude', title: 'Claude Code CLI', optional: true, checks: ['claude_login', 'claude_call'] },
  { row: 5, key: 'jev', title: 'Jev', optional: true, checks: ['jev'] },
  { row: 6, key: 'telegram', title: 'Telegram (báo động & bản tin)', optional: true, checks: ['telegram'] },
  { row: 7, key: 'remote', title: 'Truy cập từ xa', optional: true, checks: ['remote_access'] },
  // v0.1.47 (F-79): không bắt buộc, không có nút chạy (RUNNABLE không có) — Đạt do hook seedFacebookReply / gửi thật.
  { row: 8, key: 'facebook_reply', title: 'Facebook trả lời', optional: true, checks: ['facebook_reply'] },
  // v0.1.50 (F-81): không bắt buộc, không có nút chạy — Đạt do hook seedKhoWrite / lần ghi Kho thật đầu tiên.
  { row: 9, key: 'kho_write', title: 'Gen ghi Kho', optional: true, checks: ['kho_write'] },
];

export function createMock(opts: Opts) {
  const results = Object.fromEntries(KEYS.map((k) => [k, null])) as Record<BossCheckKey, BossCheck | null>;
  let fbStartedAt = 0;

  const now = () => new Date().toISOString();
  const record = (key: BossCheckKey, status: BossCheck['status'], extra: Partial<BossCheck> = {}): BossCheck => {
    const prev = results[key];
    const c: BossCheck = {
      key, status, error_code: null, message: null, detail: {}, checked_at: now(), runs: (prev?.runs ?? 0) + 1, ...extra,
    };
    // Bản lưu như CSDL thật: không có email đầy đủ (`account` chỉ có trong phản hồi `run`).
    const { account: _full, ...stored } = c;
    void _full;
    results[key] = stored;
    return c;
  };
  const fail = (key: BossCheckKey, code: string, message: string) => record(key, 'fail', { error_code: code, message });
  /** Lỗi tạm (bận/hạn mức) — như api: trả về nhưng KHÔNG ghi. */
  const transient = (key: BossCheckKey, code: string, message: string): BossCheck => ({
    key, status: 'fail', error_code: code, message, detail: {}, checked_at: now(), runs: 0, transient: true,
  });
  const activeOf = (kind: string) => opts.cliProfiles(kind).find((p) => p.active) ?? null;

  // Đăng nhập CLI: luồng đăng nhập của mock-phase2 báo xong/lỗi → ghi kết quả (như `_boss_check` của api thật).
  opts.onCliLogin((kind, ok, email) => {
    const key: BossCheckKey = kind === 'claude_code_cli' ? 'claude_login' : 'agy_login';
    if (ok) record(key, 'pass', { detail: { account_masked: mask(email), credentials_file: true } });
    else fail(key, 'CLI_LOGIN_FAILED', 'Đăng nhập chưa xong — bấm Đăng nhập lại, mở link mới và dán đúng mã vừa nhận');
  });

  const syncLogins = () => {
    const fb = results.facebook;
    if (fb?.status === 'pending' && Date.now() - fbStartedAt >= FB_READ_MS) {
      results.facebook = { ...fb, status: 'pass', checked_at: now(), detail: { items: 3 } };
    }
  };

  /** Gửi thử Telegram: lỗi tạm (TELEGRAM_RATE_LIMITED) không ghi; còn lại ghi như máy chủ, kèm `host_requested`. */
  const recordTelegram = (o: TelegramOutcome): BossCheck => {
    const { transient: isTransient, host_requested, ...rest } = o;
    if (isTransient) return { ...transient('telegram', o.error_code ?? 'TELEGRAM_RATE_LIMITED', o.message ?? ''), detail: rest.detail, host_requested } as BossCheck;
    return { ...record('telegram', rest.status, { error_code: rest.error_code, message: rest.message, detail: rest.detail }), host_requested } as BossCheck;
  };

  /** Như `telegram.service.forget_tests` của api: đổi token/chat_id hoặc Tắt Telegram ⇒ dòng 6 về "Chưa kiểm". */
  const forgetTelegram = () => {
    results.telegram = null;
  };

  const pass = (k: BossCheckKey) => results[k]?.status === 'pass';
  /** Như `_derived_ai` của api: Claude / Google gọi thử đạt ⇒ coi như có nguồn AI chạy được (kết quả giả, `runs` 0). */
  const derivedAi = (): BossCheck | null => {
    for (const k of ['claude_call', 'agy_call'] as const) {
      const r = results[k];
      if (r?.status === 'pass') return { key: 'ai_source', status: 'pass', error_code: null, message: null, detail: { via: k }, checked_at: r.checked_at, runs: 0 };
    }
    return null;
  };
  const overview = (): BossOverview => {
    syncLogins();
    const shown: Record<BossCheckKey, BossCheck | null> = { ...results };
    if (!pass('ai_source')) {
      const d = derivedAi();
      if (d) shown.ai_source = d;
    }
    const done: Record<number, boolean> = {
      0: shown.ai_source?.status === 'pass',
      1: pass('hub'),
      2: pass('facebook'),
      3: pass('agy_call'),
      4: pass('claude_login') && pass('claude_call'),
      5: pass('jev'),
      6: pass('telegram'),
      7: pass('remote_access'),
      8: pass('facebook_reply'),
      9: pass('kho_write'),
    };
    const rows = ROWS.map((r) => ({ ...r, done: done[r.row] }));
    return { rows, results: shown, required_done: rows.filter((r) => !r.optional && r.done).length, required_total: ROWS.filter((r) => !r.optional).length };
  };

  const run = (key: BossCheckKey, body: { profile_id?: string; account_id?: string }): BossCheck | null => {
    switch (key) {
      case 'hub': {
        if (!opts.hubLink().configured) return fail('hub', 'HUB_LINK_NOT_CONFIGURED', 'Chưa nhập địa chỉ và token Gen-hub');
        const t = opts.hubTest();
        // v0.1.49 (QD-16): `detail.read_scopes` = quyền đọc thêm (không bắt buộc) của lần Kiểm tra — thiếu quyền vẫn là Đạt.
        return t.ok ? record('hub', 'pass', { detail: { tools: 3, ...(t.read_scopes ? { read_scopes: { ...t.read_scopes } } : {}), ...(t.write_scopes ? { write_scopes: { ...t.write_scopes }, write_missing: t.write_missing ?? [] } : {}) } }) : fail('hub', t.error_code ?? 'HUB_ERROR', t.error ?? 'Gen-hub báo lỗi');
      }
      case 'facebook': {
        const acc = opts.socialAccounts().find((a) => a.id === body.account_id && a.status !== 'revoked');
        if (!acc) return fail('facebook', 'SOCIAL_NO_ACCOUNT', 'Chưa có tài khoản Facebook');
        if (acc.status !== 'active') return fail('facebook', 'SOCIAL_NOT_ACTIVE', 'Tài khoản chưa đăng nhập');
        if (results.facebook?.status === 'pending')
          return { ...transient('facebook', 'SOCIAL_BUSY', 'Tài khoản này đang có một việc chạy') };
        fbStartedAt = Date.now();
        return record('facebook', 'pending', { detail: { account_id: acc.id } });
      }
      case 'agy_call': {
        const a = activeOf('antigravity_cli');
        return a ? record('agy_call', 'pass', { account: a.email, detail: { account_masked: mask(a.email) } }) : fail('agy_call', 'AGY_NOT_LOGGED_IN', 'Chưa đăng nhập Google cho Antigravity');
      }
      case 'agy_switch': {
        const p = opts.cliProfiles('antigravity_cli').find((x) => x.id === body.profile_id);
        if (!p) return null;
        const from = activeOf('antigravity_cli')?.id ?? null;
        opts.activateCli(p.id);
        return record('agy_switch', 'pass', {
          account: p.email,
          detail: { expected_masked: mask(p.email), account_masked: mask(p.email), account_match: true, target_profile: p.id, from_profile: from },
        });
      }
      case 'claude_call': {
        const a = activeOf('claude_code_cli');
        if (!a) return fail('claude_call', 'CLAUDE_NOT_LOGGED_IN', 'Chưa đăng nhập Claude Code');
        const out = record('claude_call', 'pass', { account: a.email, detail: { account_masked: mask(a.email) } });
        if (!pass('claude_login')) record('claude_login', 'pass', { detail: { login_source: 'existing_session', account_masked: mask(a.email), credentials_file: true } });
        return out;
      }
      case 'ai_source': {
        // Như `_run_ai_source`: nguồn đầu chuỗi sẵn sàng — nhà cung cấp bật, có model, gọi thử OK; hoặc hồ sơ Google / Claude đang dùng.
        const api = opts.providers().find((p) => p.enabled && p.kind !== 'system_one' && p.models.length > 0 && p.auth_state === 'ok');
        const cli = activeOf('claude_code_cli') ?? activeOf('antigravity_cli');
        if (api) return record('ai_source', 'pass', { detail: { latency_ms: 120, probe_model: api.models[0]?.model_name ?? null, models_count: api.models.length } });
        if (cli) return record('ai_source', 'pass', { detail: { latency_ms: 450, account_masked: mask(cli.email) } });
        return fail('ai_source', 'AI_NO_SOURCE', 'Chưa có nguồn AI nào — thêm khoá API hoặc đăng nhập Google / Claude Code ở màn Thiết lập nguồn AI rồi bấm Kiểm tra');
      }
      case 'telegram':
        return recordTelegram(opts.telegramTest());
      case 'remote_access':
        return recordRemote('localhost', true, 'unknown'); // chỉ khi bị gọi không qua mock-api (không có Origin)
      case 'jev': {
        const jev = opts.providers().find((p) => p.kind === 'system_one');
        return jev ? record('jev', 'pass') : fail('jev', 'JEV_NOT_CONFIGURED', 'Chưa nhập khoá Jev');
      }
      default:
        return null;
    }
  };

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p, body, reply, problem } = ctx;
    if (p !== '/boss-checks' && !p.startsWith('/boss-checks/')) return false;
    if (ctx.role !== 'owner') return problem(403, 'FORBIDDEN', 'Vai trò của bạn không có quyền thao tác này');
    if (p === '/boss-checks' && m === 'GET') return reply(200, overview());
    const mm = /^\/boss-checks\/([a-z_]+)\/run$/.exec(p);
    if (mm && m === 'POST') {
      const key = mm[1] as BossCheckKey;
      if (!RUNNABLE.has(key)) return problem(404, 'NOT_FOUND', 'Không có mục kiểm này');
      if (NEEDS_PIN.has(key) && ctx.needPin()) return problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation: `boss_checks.${key}` } });
      const c = run(key, body as { profile_id?: string; account_id?: string });
      if (!c) return problem(404, 'NOT_FOUND', 'Không tìm thấy tài khoản');
      opts.emit('boss_checks.update', { key });
      return reply(200, c);
    }
    return false;
  }

  /** v0.1.46 (F-21): như `_run_remote` của api — `host` = hostname của Origin (hoặc Host), `publicLocal` = GH_PUBLIC_URL local. */
  const recordRemote = (host: string, publicLocal: boolean, mode: string): BossCheck => {
    if (publicLocal) {
      return fail('remote_access', 'REMOTE_NOT_CONFIGURED', 'Chưa chọn cách truy cập từ xa — trên máy chủ chạy genh remote tailscale (khuyên dùng) hoặc genh remote --lan');
    }
    if (isLocalHost(host)) {
      return fail('remote_access', 'REMOTE_OPENED_ON_SERVER', 'Đang mở trên chính máy chủ — mở Console trên điện thoại bằng địa chỉ ở Cài đặt › Sao lưu & cập nhật › Truy cập từ xa rồi bấm Kiểm tra từ đó');
    }
    return record('remote_access', 'pass', { detail: { opened_from: host, access_mode: mode } });
  };

  /** Hook e2e: hai hồ sơ Google an@ (không dùng) và binh@ (đang dùng) cho kịch bản đổi qua lại. */
  const seedAgy = () => {
    const list = opts.cliProfiles('antigravity_cli');
    const exp = new Date(Date.now() + 5 * 86_400_000).toISOString();
    list.splice(0, list.length,
      { id: randomUUID(), email: 'an@genesis.vn', plan_label: 'Google AI · miễn phí', active: false, expires_at: exp, state: 'ok' },
      { id: randomUUID(), email: 'binh@genesis.vn', plan_label: 'Google AI Pro', active: true, expires_at: exp, state: 'ok' },
    );
    return list;
  };

  /** Hook e2e: một hồ sơ Claude đang dùng, chưa có bản claude_login (như phiên có từ trước v0.1.39). */
  const seedClaude = () => {
    const list = opts.cliProfiles('claude_code_cli');
    const exp = new Date(Date.now() + 5 * 86_400_000).toISOString();
    list.splice(0, list.length, { id: randomUUID(), email: 'a@example.test', plan_label: 'Claude Max', active: true, expires_at: exp, state: 'ok' });
    return list;
  };

  /** Hook e2e: dòng 8 'Facebook trả lời' đạt (như sau một lần gửi thật được xác nhận). */
  const seedFacebookReply = () => record('facebook_reply', 'pass', { detail: { action: 'reply_comment' } });

  /** Dòng 9 'Gen ghi Kho' đạt — máy chủ ghi sau lần ghi Kho THẬT đầu tiên được xác nhận (không ghi đè nếu đã đạt). */
  const recordKhoWrite = () => (pass('kho_write') ? (results.kho_write as BossCheck) : record('kho_write', 'pass', { detail: { action: 'kho_write' } }));
  const seedKhoWrite = () => recordKhoWrite();
  /** v0.1.54 (e2e Gen hướng dẫn): giả lập dòng 1 "Nối Gen-hub" đạt (như lượt Kiểm tra đạt) — không qua PIN/Gen-hub mock. */
  const seedHub = () => record('hub', 'pass', { detail: { tools: 3 } });
  /** v0.1.55 (e2e Thiết lập gọn / Việc Sếp cần làm): giả lập dòng 0 "nguồn AI" đạt (như một lượt Kiểm tra đạt). */
  const seedAi = () => record('ai_source', 'pass', { detail: { latency_ms: 120, probe_model: 'gemini-2.5-flash', models_count: 1 } });

  return { handle, hooks: { seedAgy, seedClaude, seedFacebookReply, seedKhoWrite, seedHub, seedAi, recordKhoWrite, overview, recordTelegram, forgetTelegram, recordRemote } as Record<string, (...args: never[]) => unknown>, dispose: () => {} };
}
