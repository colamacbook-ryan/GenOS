/**
 * Mock API giai đoạn 4 · API & Model (PLAN 4.2): gán model theo agent/mục đích (`agent.bindings`), quy tắc
 * chuyển hướng tĩnh (`GET /failover-rules`), kéo-thả chuỗi ưu tiên (`PATCH /providers/chain`), và thêm/xoá
 * khoá (`POST /providers/{id}/keys`, `DELETE /providers/{id}/keys/{kid}` — `mock-phase2.ts` chưa có hai
 * đường này dù đã có hợp đồng từ giai đoạn 2; tất cả đọc/ghi thẳng lên mảng `providers` dùng chung với
 * `mock-phase2.ts` qua `hooks.providers()`, KHÔNG copy, để `GET /providers` sau đó thấy đúng thay đổi).
 *
 * Provider CRUD/model/CLI khác đã có đủ ở `mock-phase2.ts` — không lặp lại ở đây.
 */
import { randomUUID } from 'node:crypto';
import type { AgentIdentity, BindableModel, FailoverRule } from '@gen-harness/contracts';
import type { P2Ctx } from './mock-phase2';

interface MockProviderKey {
  id: string;
  label: string;
  last4: string;
  enabled: boolean;
  cooldown_until: string | null;
  quota_left_pct: number | null;
}
interface MockProviderModel {
  id: string;
  model_name: string;
  daily_quota: number | null;
  used_today: number;
}
interface MockProvider {
  id: string;
  kind: string;
  name: string;
  endpoint?: string | null;
  failover_rank: number;
  enabled: boolean;
  auth_state: string;
  keys: MockProviderKey[];
  models: MockProviderModel[];
}

export interface P4ApiOptions {
  fresh: boolean;
  emit: (type: string, data: unknown) => void;
  /** `mock-p4-agents.ts` (đăng ký trước cụm này trong `phase3` — xem `mock-api.ts`). */
  getAgents: () => AgentIdentity[];
  /** `mock-phase2.ts` hooks.providers() — mảng dùng chung, KHÔNG copy (chain cần sửa tại chỗ). */
  getProviders: () => MockProvider[];
}

/** ARCHITECTURE §11 — cố định, chỉ đọc. */
const FAILOVER_RULES: FailoverRule[] = [
  { key: 'hết hạn mức', value: 'chuyển xuống nhà cung cấp kế tiếp trong chuỗi (hạn mức Sếp đặt ở từng model)' },
  { key: 'ngắt mạch', value: 'giữ nguyên hội thoại, thử lại sau 60 giây' },
  { key: 'hộp Antigravity', value: 'vai chạy Antigravity qua hộp riêng (soạn lại / dịch nháp tin khách, sàng lọc tin, Bản tin Gen, agent ngoại giao, Gen của nhân viên) mà hộp chưa chạy thì bỏ qua Antigravity và chuyển nguồn kế tiếp — không bao giờ chạy thay trong tiến trình; chỉ Gen của Sếp và agent nội bộ không cần hộp' },
  { key: 'việc nền', value: 'việc nền (sàng lọc tin, trực việc, Bản tin Gen) bỏ qua Claude Code CLI trừ khi Sếp đã cho phép ở Bộ não AI; Antigravity không cần cho phép này. Chỉ còn Claude Code CLI chưa được phép thì hết chuỗi và báo Sếp' },
  { key: 'hết chuỗi', value: 'xếp hàng và báo Sếp qua hàng đợi cần xử lý' },
  { key: 'ngưỡng cảnh báo', value: 'còn dưới 20% hạn mức ngày trên model nào Sếp đã đặt hạn mức' },
];

/** `gh.agents_api.routes.CORE_AGENT_KEYS`. */
const CORE_AGENT_KEYS: Record<string, string> = {
  'core.refinery': 'Sàng lọc & suy luận chính',
  // v0.1.43 (F-25): bỏ core.intent/core.scoring/core.indexing (không nơi nào dùng); core.reply = soạn lại / dịch nháp.
  'core.reply': 'Soạn lại / dịch nháp',
  // v0.1.21: Gen — trợ lý quản trị (gh.gen.engine.AGENT_KEY). v0.1.62: Antigravity CLI gán được cho MỌI khoá.
  'core.gen': 'Gen — trợ lý quản trị',
  // v0.1.55 (G1): Bản tin Gen có khoá riêng (việc nền) — gh.gen.briefing.AGENT_KEY.
  'core.briefing': 'Bản tin Gen',
};

/**
 * v0.1.41 (F-86, QD-12): câu cảnh báo khi Owner cho Claude Code CLI chạy việc nền — web hiện NGUYÊN VĂN (như
 * `risk_text` của `GET /providers/background` ở API thật).
 */
export const BACKGROUND_RISK_TEXT =
  'Claude Code CLI dùng gói Claude Pro/Max cá nhân của Sếp. Cho nó chạy việc nền tự động (sàng lọc tin, trực việc, bản tin) có thể trái điều khoản gói và tài khoản có thể bị hạn chế hoặc khoá. Đây là quyết định và rủi ro của Sếp (QD-12). Cách an toàn: dán khoá API OpenRouter hoặc Gemini.';
/** Như `BACKGROUND_PURPOSE_LABELS` của API thật — nhãn, không phải mã purpose. */
const BACKGROUND_PURPOSES = ['Sàng lọc tin', 'Trực việc (agent soạn nháp)', 'Bản tin Gen'];
const API_KINDS = new Set(['gemini', 'deepseek', 'openai_compat']);

interface BindingState {
  model_id: string;
  model_name: string;
  provider_name: string;
  temperature: number;
  context_tokens: number;
  rule_codes: string[];
  /** v0.1.55 (G1): mức suy nghĩ riêng của vai (null = theo hồ sơ chuẩn / model). */
  effort: string | null;
}

/** Như `gh/defaults/profiles.py` (rút gọn): hồ sơ chuẩn theo vai khi chưa có dòng gán. */
const STANDARD_PROFILE: Record<string, { tier: 'fast' | 'balanced' | 'strong'; tier_label: string; effort: string | null; temperature: number; context_tokens: number }> = {
  'core.gen': { tier: 'balanced', tier_label: 'Cân bằng', effort: null, temperature: 0.3, context_tokens: 6000 },
  'core.briefing': { tier: 'fast', tier_label: 'Nhanh', effort: null, temperature: 0.2, context_tokens: 6000 },
  'core.refinery': { tier: 'fast', tier_label: 'Nhanh', effort: null, temperature: 0.2, context_tokens: 6000 },
  'core.reply': { tier: 'balanced', tier_label: 'Cân bằng', effort: null, temperature: 0.3, context_tokens: 6000 },
};
const AGENT_PROFILE = { tier: 'balanced' as const, tier_label: 'Cân bằng', effort: null, temperature: 0.3, context_tokens: 6000 };

export function createMock(opts: P4ApiOptions) {
  const bindings = new Map<string, BindingState>(
    opts.fresh
      ? []
      : [
          ['core.refinery', { model_id: 'md-antigravity-pro', model_name: 'gemini-2.5-pro', provider_name: 'Antigravity Brain', temperature: 0.2, context_tokens: 64_000, rule_codes: ['R-01', 'R-02', 'R-03', 'R-04', 'R-05', 'R-06'], effort: null }],
        ],
  );
  const has = (ctx: P2Ctx, perm: string) => !!ctx.perms[perm] && ctx.perms[perm] !== 'none';
  // v0.1.41 (F-86): nguồn AI cho việc nền — khoá API và (v0.1.62) Antigravity CLI; Claude Code CLI mặc định TẮT.
  const background: { allow_cli: string[]; accepted_at: string | null } = { allow_cli: [], accepted_at: null };

  /** Như API thật: chuỗi theo `failover_rank` (bỏ Jev), mỗi nguồn có dùng cho việc nền không + lý do. */
  function backgroundView() {
    const list = [...opts.getProviders()].filter((x) => x.kind !== 'system_one').sort((a, b) => a.failover_rank - b.failover_rank);
    const sources = list.map((pv) => {
      let reason: string | null = null;
      if (pv.kind === 'claude_code_cli') reason = background.allow_cli.includes('claude_code_cli') ? null : 'Claude Code CLI (gói Pro/Max của Sếp) mặc định chỉ dùng khi Sếp hỏi Gen trực tiếp — việc nền dùng khoá API hoặc Antigravity CLI';
      else if (pv.kind !== 'antigravity_cli' && !API_KINDS.has(pv.kind)) reason = 'Không phải nguồn sinh chữ';
      if (!reason && !pv.enabled) reason = 'Nguồn đang tắt';
      if (!reason && API_KINDS.has(pv.kind) && !pv.keys.some((k) => k.enabled)) reason = 'Chưa có khoá API';
      if (!reason && pv.kind === 'antigravity_cli' && !pv.models.length) reason = 'Chưa chọn model';
      return { provider_id: pv.id, name: pv.name, kind: pv.kind, used: reason === null, reason };
    });
    return {
      allow_cli: [...background.allow_cli],
      accepted_at: background.accepted_at,
      risk_text: BACKGROUND_RISK_TEXT,
      purposes: [...BACKGROUND_PURPOSES],
      has_api_source: sources.some((x) => x.used && API_KINDS.has(x.kind)),
      has_agy_source: sources.some((x) => x.used && x.kind === 'antigravity_cli'),
      sources,
    };
  }

  function findModel(modelId: string): { model_name: string; provider_name: string } | null {
    for (const p of opts.getProviders()) {
      const m = p.models.find((x) => x.id === modelId);
      if (m) return { model_name: m.model_name, provider_name: p.name };
    }
    return null;
  }

  function bindableModels(): BindableModel[] {
    const out: BindableModel[] = [];
    for (const p of opts.getProviders()) {
      for (const m of p.models) out.push({ id: m.id, model_name: m.model_name, provider_name: p.name, kind: p.kind, enabled: p.enabled });
    }
    return out;
  }

  /** Như `gh.providers.agy_policy.decide` khi hộp riêng chưa chạy — core.reply (qua hộp) chưa dùng được agy; khoá khác dùng được. */
  const AGY_DIPLOMATIC_REASON =
    'Hộp riêng Antigravity chưa chạy — việc có người ngoài (trả lời khách, sàng lọc tin, Gen của nhân viên) tạm dùng nguồn AI kế, không dùng Antigravity trong tiến trình';
  const agyBlockedFor = (agentKey: string): string | null => (agentKey === 'core.reply' ? AGY_DIPLOMATIC_REASON : null);

  /** Model "Chuẩn" cho một khoá: model đầu tiên của nguồn đầu tiên dùng được (agy cho khoá core, trừ lớp ngoại giao); không có ⇒ null. */
  function standardFor(agentKey: string) {
    const prof = STANDARD_PROFILE[agentKey] ?? AGENT_PROFILE;
    const blocked = agyBlockedFor(agentKey) !== null;
    const providers = [...opts.getProviders()]
      .filter((x) => x.enabled && x.kind !== 'system_one' && !(blocked && x.kind === 'antigravity_cli'))
      .sort((a, b) => a.failover_rank - b.failover_rank);
    for (const pv of providers) {
      const m = pv.models[0];
      if (m) return { model_name: m.model_name, provider_name: pv.name, ...prof };
    }
    return null;
  }

  function keysAndLabels(): Array<[string, string]> {
    const agents = opts.getAgents();
    return [...Object.entries(CORE_AGENT_KEYS), ...agents.map((a): [string, string] => [`agent:${a.id}`, a.name])];
  }

  function labelOf(agentKey: string): string | null {
    if (CORE_AGENT_KEYS[agentKey]) return CORE_AGENT_KEYS[agentKey];
    if (agentKey.startsWith('agent:')) return opts.getAgents().find((a) => a.id === agentKey.slice('agent:'.length))?.name ?? null;
    return null;
  }

  function stripSecret(p: MockProvider) {
    const { _secret: _s, ...rest } = p as MockProvider & { _secret?: string };
    return rest;
  }

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p, reply, problem, body } = ctx;
    const seg = p.split('/').filter(Boolean);

    if (p === '/failover-rules' && m === 'GET') {
      if (!has(ctx, 'system.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      return reply(200, FAILOVER_RULES);
    }

    // v0.1.41 (F-86): `/providers/background` — mock-phase2.ts nhường đường này (đứng TRƯỚC mẫu `/providers/{id}`).
    if (p === '/providers/background') {
      if (m === 'GET') {
        if (!has(ctx, 'system.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
        return reply(200, backgroundView());
      }
      if (m === 'PUT') {
        if (ctx.role !== 'owner') return problem(403, 'FORBIDDEN', 'Chỉ Owner đổi nguồn AI cho việc nền');
        const b = body as { allow_cli?: unknown; accept_risk?: unknown };
        const allow = Array.isArray(b.allow_cli) ? b.allow_cli.map(String) : null;
        if (!allow) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { allow_cli: 'Danh sách CLI không hợp lệ' } });
        if (allow.includes('antigravity_cli')) {
          return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { allow_cli: 'Antigravity CLI luôn chạy được việc nền — không cần cho phép (chỉ Claude Code CLI cần Sếp cho phép)' } });
        }
        if (allow.some((k) => k !== 'claude_code_cli')) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { allow_cli: 'Chỉ Claude Code CLI được cho chạy việc nền' } });
        const adding = allow.includes('claude_code_cli') && !background.allow_cli.includes('claude_code_cli');
        if (adding) {
          if (ctx.needPin()) return problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation: 'ai.background_cli' } });
          if (b.accept_risk !== true) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { accept_risk: 'Sếp cần tích xác nhận đã đọc cảnh báo' } });
          background.accepted_at = new Date().toISOString();
        }
        background.allow_cli = [...new Set(allow)];
        if (!background.allow_cli.length) background.accepted_at = null;
        return reply(200, backgroundView());
      }
    }

    if (seg[0] === 'providers' && seg[2] === 'keys') {
      if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      // v0.1.35 (F-20): thêm khoá cần PIN `ai.route_change` (sau quyền, như API thật; detail null). Xoá khoá không cần.
      if (seg.length === 3 && m === 'POST' && ctx.needPin()) return problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN');
      const pv = opts.getProviders().find((x) => x.id === seg[1]);
      if (!pv) return problem(404, 'NOT_FOUND', 'Nhà cung cấp không tồn tại');
      if (seg.length === 3 && m === 'POST') {
        if (pv.kind === 'antigravity_cli') return problem(409, 'CLI_NO_KEYS', 'Antigravity CLI dùng phiên đăng nhập, không dùng khoá API');
        const secret = String((body as { secret?: string }).secret ?? '').trim();
        if (secret.length < 8) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { secret: 'Khoá API không hợp lệ' } });
        const prefix = pv.kind === 'gemini' ? 'GEM' : pv.kind === 'deepseek' ? 'DS' : 'API';
        pv.keys.push({ id: randomUUID(), label: `${prefix}-KEY-0${pv.keys.length + 1}`, last4: secret.slice(-4), enabled: true, cooldown_until: null, quota_left_pct: null });
        return reply(201, stripSecret(pv));
      }
      if (seg.length === 4 && m === 'DELETE') {
        const i = pv.keys.findIndex((k) => k.id === seg[3]);
        if (i < 0) return problem(404, 'NOT_FOUND', 'Khoá không tồn tại');
        pv.keys.splice(i, 1);
        return reply(204);
      }
    }

    if (p === '/providers/chain' && m === 'PATCH') {
      if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (ctx.needPin()) return problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN');
      const ids = Array.isArray((body as { provider_ids?: unknown }).provider_ids) ? ((body as { provider_ids: string[] }).provider_ids) : [];
      const uniqueIds = [...new Set(ids)];
      const providers = opts.getProviders();
      const have = new Set(providers.map((x) => x.id));
      const want = new Set(uniqueIds);
      if (uniqueIds.length !== have.size || [...have].some((id) => !want.has(id))) {
        return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { provider_ids: 'Cần đúng và đủ danh sách nhà cung cấp hiện có, không thiếu không thừa' } });
      }
      uniqueIds.forEach((id, i) => {
        const pv = providers.find((x) => x.id === id);
        if (pv) pv.failover_rank = i + 1;
      });
      return reply(200, [...providers].sort((a, b) => a.failover_rank - b.failover_rank).map(stripSecret));
    }

    if (seg[0] !== 'agents' || seg[1] !== 'bindings') return false;

    if (seg.length === 2 && m === 'GET') {
      if (!has(ctx, 'system.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      // v0.1.62: như gh/agents_api/routes.py::_binding_out — không còn `blocked_reason` (agy gán được cho mọi khoá).
      const items = keysAndLabels().map(([agent_key, label]) => {
        const b = bindings.get(agent_key);
        return {
          agent_key,
          label,
          binding: b ?? null,
          // v0.1.55 (G1): `custom` = Sếp đã gán; `standard` = dùng hồ sơ chuẩn (+ model đang phủ).
          source: b ? 'custom' : 'standard',
          standard: b ? null : standardFor(agent_key),
          standard_reason: b || standardFor(agent_key) ? null : agyBlockedFor(agent_key),
          agy_blocked_reason: agyBlockedFor(agent_key),
        };
      });
      return reply(200, { items, models: bindableModels() });
    }

    const agentKey = decodeURIComponent(seg[2] ?? '');

    if (seg.length === 3 && m === 'PUT') {
      if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      const label = labelOf(agentKey);
      if (label === null) return problem(404, 'NOT_FOUND', 'Agent không tồn tại');
      const b = body as Record<string, unknown>;
      const modelId = String(b.model_id ?? '');
      const found = findModel(modelId);
      if (!found) return problem(404, 'NOT_FOUND', 'Model không tồn tại');
      const blocked = agyBlockedFor(agentKey);
      if (blocked && opts.getProviders().find((x) => x.models.some((mm) => mm.id === modelId))?.kind === 'antigravity_cli') {
        return problem(422, 'VALIDATION', 'Dữ liệu chưa hợp lệ', { errors: { model_id: blocked } });
      }
      // v0.1.55 (G1): `effort` kiểm theo nguồn — khoá API và haiku không có mức suy nghĩ (422 `effort`).
      const effort = typeof b.effort === 'string' ? b.effort : null;
      if (effort) {
        const kind = opts.getProviders().find((x) => x.models.some((mm) => mm.id === modelId))?.kind;
        const cli = kind === 'antigravity_cli' || kind === 'claude_code_cli';
        if (!cli || /haiku/i.test(found.model_name) || !['low', 'medium', 'high', 'xhigh', 'max'].includes(effort) || (kind === 'antigravity_cli' && !['low', 'medium', 'high'].includes(effort))) {
          return problem(422, 'VALIDATION', 'Dữ liệu chưa hợp lệ', { errors: { effort: `Model “${found.model_name}” không chỉnh được mức suy nghĩ này — bỏ trống ô này` } });
        }
      }
      const binding: BindingState = {
        model_id: modelId, model_name: found.model_name, provider_name: found.provider_name,
        temperature: typeof b.temperature === 'number' ? b.temperature : 0.3,
        context_tokens: typeof b.context_tokens === 'number' ? b.context_tokens : 8000,
        rule_codes: Array.isArray(b.rule_codes) ? (b.rule_codes as string[]) : [],
        effort,
      };
      bindings.set(agentKey, binding);
      return reply(200, { agent_key: agentKey, label, binding, source: 'custom', standard: null });
    }

    if (seg.length === 3 && m === 'DELETE') {
      if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (!bindings.has(agentKey)) return problem(404, 'NOT_FOUND', 'Gán model không tồn tại');
      bindings.delete(agentKey);
      return reply(204);
    }

    return false;
  }

  return {
    handle,
    hooks: {
      /**
       * v0.1.41 (F-86): `__mock/p3/api/background` {claude?: true, allow_cli?: string[]} — thêm nguồn Claude Code CLI (đã
       * đăng nhập) vào chuỗi để e2e bật được "Cho Claude Code CLI chạy việc nền"; đặt thẳng allow_cli.
       */
      background: (b: { claude?: boolean; allow_cli?: string[] }) => {
        const list = opts.getProviders();
        if (b?.claude && !list.some((x) => x.kind === 'claude_code_cli')) {
          list.push({ id: randomUUID(), kind: 'claude_code_cli', name: 'Claude Code CLI', endpoint: null, failover_rank: list.length + 1, enabled: true, auth_state: 'ok', keys: [], models: [] });
        }
        if (Array.isArray(b?.allow_cli)) {
          background.allow_cli = b.allow_cli.map(String);
          background.accepted_at = background.allow_cli.length ? new Date().toISOString() : null;
        }
        return backgroundView();
      },
    } as unknown as Record<string, (...args: never[]) => unknown>,
    dispose: () => {},
  };
}
