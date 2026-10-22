/**
 * Mock v0.1.72 — Bot tài khoản riêng, theo HỢP ĐỒNG của `apps/api/gh/owner_channel/routes.py` (xem `packages/contracts/src/ownerChannel.ts`):
 *
 * - `GET/PUT /owner-channel/{ch}` — kênh quản trị Zalo/WhatsApp + cài đặt báo cáo chủ động (CHỈ Owner).
 * - `POST /owner-channel/{ch}/pair/start` (PIN) — mã ghép 6 số trả MỘT lần; `DELETE /owner-channel/{ch}/pair` (PIN) — huỷ ghép.
 * - `PUT /channels/{ch}/sessions/{sid}/account-kind` (PIN) — «bot» ⇄ «owner_self»; rời `bot` ⇒ huỷ ghép + tắt hỗ trợ.
 * - `GET/PUT /groups/{gid}/support` (PIN khi đổi) — Chế độ hỗ trợ; nhóm chưa đủ điều kiện ⇒ 409 SUPPORT_NOT_ALLOWED.
 * - `GET /auto-sends` — sổ tin bot tự gửi (không có nội dung tin) + số liệu hôm nay.
 *
 * Mặc định: Zalo chạy tài khoản bot, đã ghép, 2 mục sổ; WhatsApp là tài khoản cá nhân của Sếp. Trạng thái nằm trong bộ nhớ
 * (mỗi `POST /__mock/reset` về lại ban đầu). Hook e2e `POST /api/v1/__mock/p3/ownerChannel/state {}` đọc trạng thái.
 */
import type {
  AccountKind, AutoSendItem, AutoSendPage, GroupSupportView, OwnerChannelView, OwnerReportPrefs, SupportLimits,
} from '../../../packages/contracts/src/ownerChannel';
import type { P2Ctx } from './mock-phase2';

interface Opts {
  fresh: boolean;
  emit: (type: string, data: unknown) => void;
}

interface Chan {
  sessionId: string | null;
  kind: AccountKind;
  paired: boolean;
  enabled: boolean;
  pairedAt: string | null;
  pendingUntil: string | null;
  prefs: OwnerReportPrefs;
}

const LIMITS: SupportLimits = { per_group_hour: 20, per_member_hour: 5, min_gap_s: 20, per_channel_day: 150, max_len: 600, max_age_s: 300 };
const iso = (minAgo: number) => new Date(Date.now() - minAgo * 60_000).toISOString();

export function createMock(opts: Opts) {
  const chans: Record<string, Chan> = {
    zalo: {
      sessionId: opts.fresh ? null : 'sess-zalo-1', kind: 'bot', paired: !opts.fresh, enabled: !opts.fresh,
      pairedAt: opts.fresh ? null : iso(60 * 24), pendingUntil: null,
      prefs: { quiet_start: 22, quiet_end: 7, max_per_hour: 6, min_severity: 'important' },
    },
    whatsapp: {
      sessionId: opts.fresh ? null : 'sess-wa-1', kind: 'owner_self', paired: false, enabled: false,
      pairedAt: null, pendingUntil: null,
      prefs: { quiet_start: 22, quiet_end: 7, max_per_hour: 6, min_severity: 'important' },
    },
  };
  const support = new Map<string, { enabled: boolean; since: string | null; until: string | null }>();
  let sends = 0;
  const items: AutoSendItem[] = opts.fresh ? [] : [
    { id: 'as-1', at: iso(12), basis: 'owner_dm', basis_label: 'Kênh quản trị', channel: 'zalo', thread_type: 'user', status: 'sent', error: null, body_len: 84, rules: [], group: null },
    { id: 'as-2', at: iso(95), basis: 'support', basis_label: 'Hỗ trợ', channel: 'zalo', thread_type: 'group', status: 'sent', error: null, body_len: 142, rules: ['no_price'],
      group: { id: 'grp-1', code: 'G-001', name: 'Nhóm khách hàng thân thiết' } },
  ];

  const view = (ch: string): OwnerChannelView => {
    const c = chans[ch];
    const pending = !!c.pendingUntil && new Date(c.pendingUntil).getTime() > Date.now();
    return {
      channel: ch,
      session: c.sessionId ? { id: c.sessionId, account_kind: c.kind } : null,
      bot_account: c.kind === 'bot',
      paired: c.paired, enabled: c.paired && c.enabled,
      owner_masked: c.paired ? '•••4821' : null, owner_display: c.paired ? 'Sếp Ryan' : null,
      paired_at: c.pairedAt, pair_pending: pending, pair_expires_at: pending ? c.pendingUntil : null,
      report_prefs: { ...c.prefs },
      support_groups: [...support.values()].filter((s) => s.enabled).length && ch === 'zalo' ? [...support.values()].filter((s) => s.enabled).length : 0,
    };
  };

  const supportView = (gid: string): GroupSupportView => {
    const s = support.get(gid) ?? { enabled: false, since: null, until: null };
    const z = chans.zalo;
    const reasons: string[] = [];
    if (z.kind !== 'bot') reasons.push('Kênh chưa chạy bằng tài khoản riêng của bot');
    if (!z.paired) reasons.push('Sếp chưa ghép kênh quản trị 1-1 với bot');
    return {
      group: { id: gid, code: 'G-001', name: 'Nhóm khách hàng thân thiết', channel: 'zalo', listen_mode: 'tagged_only', added_by_kind: 'owner' },
      enabled: s.enabled, since: s.since, via: s.enabled ? 'console' : null, until_at: s.until, limits: { ...LIMITS },
      can_enable: reasons.length === 0, blocked_reasons: reasons,
      history: s.since ? [{ enabled_at: s.since, via: 'console', disabled_at: s.enabled ? null : iso(1), reason: s.enabled ? null : 'owner_off' }] : [],
    };
  };

  const page = (limit: number): AutoSendPage => {
    const by = (b: string) => items.filter((i) => i.basis === b).length;
    return {
      items: items.slice(0, limit),
      today: { owner_dm: by('owner_dm'), owner_tag: by('owner_tag'), support: by('support'), escalated: 0, total: items.length },
    };
  };

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p, reply, problem } = ctx;
    const oc = /^\/owner-channel\/([^/]+)(\/pair(?:\/start)?)?$/.exec(p);
    const ak = /^\/channels\/([^/]+)\/sessions\/([^/]+)\/account-kind$/.exec(p);
    const gs = /^\/groups\/([^/]+)\/support$/.exec(p);
    if (!oc && !ak && !gs && p !== '/auto-sends') return false;
    if (ctx.role !== 'owner') return problem(403, 'FORBIDDEN', 'Vai trò của bạn không có quyền thao tác này');
    const pinFail = () => problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation: 'owner_channel' } });

    if (p === '/auto-sends' && m === 'GET') return reply(200, page(Number(ctx.url.searchParams.get('limit') ?? 50) || 50));

    if (oc) {
      const ch = decodeURIComponent(oc[1]);
      if (!chans[ch]) return problem(404, 'CHANNEL_NOT_FOUND', 'Kênh này chưa hỗ trợ');
      const c = chans[ch];
      if (!oc[2]) {
        if (m === 'GET') return reply(200, view(ch));
        if (m === 'PUT') {
          if (typeof ctx.body.enabled === 'boolean') c.enabled = ctx.body.enabled;
          if (ctx.body.report_prefs && typeof ctx.body.report_prefs === 'object') Object.assign(c.prefs, ctx.body.report_prefs);
          return reply(200, view(ch));
        }
      } else if (oc[2] === '/pair/start' && m === 'POST') {
        if (ctx.needPin()) return pinFail();
        if (c.kind !== 'bot') return problem(409, 'NOT_BOT_ACCOUNT', 'Kênh này chưa chạy bằng tài khoản riêng của bot');
        c.pendingUntil = new Date(Date.now() + 10 * 60_000).toISOString();
        return reply(200, { ...view(ch), pair: { code: '482915', expires_at: c.pendingUntil } });
      } else if (oc[2] === '/pair' && m === 'DELETE') {
        if (ctx.needPin()) return pinFail();
        c.paired = false; c.enabled = false; c.pairedAt = null; c.pendingUntil = null;
        support.forEach((s) => { s.enabled = false; });
        return reply(200, view(ch));
      }
      return problem(405, 'METHOD_NOT_ALLOWED', 'Không hỗ trợ thao tác này');
    }

    if (ak && m === 'PUT') {
      if (ctx.needPin()) return pinFail();
      const ch = decodeURIComponent(ak[1]);
      const c = chans[ch];
      if (!c || c.sessionId !== decodeURIComponent(ak[2])) return problem(404, 'SESSION_NOT_FOUND', 'Không thấy phiên này');
      const kind = ctx.body.account_kind;
      if (kind !== 'bot' && kind !== 'owner_self') return problem(422, 'VALIDATION', 'Dữ liệu chưa hợp lệ', { errors: { account_kind: 'Chọn tài khoản riêng của bot hoặc tài khoản cá nhân' } });
      c.kind = kind;
      if (kind !== 'bot') { c.paired = false; c.enabled = false; c.pairedAt = null; support.forEach((s) => { s.enabled = false; }); }
      return reply(200, view(ch));
    }

    if (gs) {
      const gid = decodeURIComponent(gs[1]);
      if (m === 'GET') return reply(200, supportView(gid));
      if (m === 'PUT') {
        if (ctx.needPin()) return pinFail();
        const want = ctx.body.enabled === true;
        if (want) {
          const v = supportView(gid);
          if (!v.can_enable) return problem(409, 'SUPPORT_NOT_ALLOWED', v.blocked_reasons.join('; '));
          if (v.enabled) return problem(409, 'SUPPORT_ALREADY_ON', 'Nhóm này đã bật Chế độ hỗ trợ');
          const h = Number(ctx.body.until_hours);
          support.set(gid, { enabled: true, since: new Date().toISOString(), until: h > 0 ? new Date(Date.now() + h * 3_600_000).toISOString() : null });
        } else {
          const s = support.get(gid);
          if (s) s.enabled = false;
        }
        return reply(200, supportView(gid));
      }
    }
    return problem(405, 'METHOD_NOT_ALLOWED', 'Không hỗ trợ thao tác này');
  }

  return {
    handle,
    dispose: () => {},
    hooks: {
      state: () => ({ channels: JSON.parse(JSON.stringify(chans)), support: [...support.entries()], sends }),
      push: (b: Partial<AutoSendItem> = {}) => { sends += 1; items.unshift({ id: `as-x${sends}`, at: new Date().toISOString(), basis: 'owner_dm', basis_label: 'Kênh quản trị', channel: 'zalo', thread_type: 'user', status: 'sent', error: null, body_len: 50, rules: [], group: null, ...b }); return page(50); },
    },
  };
}
