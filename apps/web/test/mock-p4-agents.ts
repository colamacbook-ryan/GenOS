/**
 * Mock API giai đoạn 4 · Danh tính Agent (PLAN 4.1, `apps/api/gh/agents_api/routes.py`): CRUD `agent.identities`
 * (tạo/sửa/liệt kê/nhân bản/tắt), mẫu có sẵn (spec E13, không mặc định bắt buộc), và `GET /agents/decisions`
 * ("agent đã nói gì, nhân danh gì" — cùng đường với cụm nền chung `p3-core`, nhưng cụm đó chỉ trả stub rỗng vì
 * chưa có màn nào dùng tới; trả lời thật ở đây bằng cách đăng ký `agents` TRƯỚC `core` trong `phase3`
 * — xem `mock-api.ts`).
 *
 * `handle` trả true khi đã trả lời request.
 */
import { randomUUID } from 'node:crypto';
import type { AgentChannelScope, AgentIdentity, AgentTemplate } from '@gen-harness/contracts';
import { BAO, GROUP_TP } from './mock-p3-core';
import { AGENT_IDS } from './mock-ids';
import type { P2Ctx } from './mock-phase2';

export interface P4Options {
  fresh: boolean;
  emit: (type: string, data: unknown) => void;
  /** `mock-phase2.ts` hooks.channels() — chỉ để suy ra `channel_type` từ `channel_id` khi lưu phạm vi nghe. */
  getChannels: () => Array<{ id: string | null; type: string }>;
}

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();

/** Mẫu có sẵn — sao y `gh.agents_api.routes.TEMPLATES` (spec E13) để Console và API khớp nội dung. */
export const TEMPLATES: AgentTemplate[] = [
  {
    code: 'commercial', name: 'Trợ lý thương mại',
    role_desc: 'Theo dõi cơ hội, nhắc việc quá hạn, soạn nháp trả lời khách hàng trong nhóm kinh doanh.',
    voice: 'Thân thiện, chuyên nghiệp, xưng hô lịch sự', speak_when: 'Khi được hỏi trực tiếp, có cơ hội mới, hoặc việc sắp quá hạn',
    forbidden: ['Cam kết giá hoặc chiết khấu ngoài bảng giá đã duyệt', 'Tự ý huỷ đơn hàng'], default_enabled: true,
  },
  {
    code: 'key_account', name: 'Chăm sóc khách lớn',
    role_desc: 'Hỗ trợ chăm sóc khách hàng lớn, theo dõi lời hứa và deal đang mở.',
    voice: 'Trang trọng, đúng hẹn, nhấn mạnh cam kết', speak_when: 'Khi khách VIP nhắn tin hoặc deal sắp tới hạn',
    forbidden: ['Đàm phán giá cuối cùng thay Account Manager', 'Hứa thời gian giao hàng chưa xác nhận với kho'], default_enabled: true,
  },
  {
    code: 'admin', name: 'Admin hậu cần',
    role_desc: 'Ghi nhận yêu cầu hậu cần, nhắc lịch, tổng hợp việc trong nhóm nội bộ.',
    voice: 'Gọn gàng, trung lập, đúng việc', speak_when: 'Khi có yêu cầu hậu cần mới hoặc việc tới hạn',
    forbidden: ['Duyệt chi phí', 'Thay đổi quyền truy cập hệ thống'], default_enabled: true,
  },
  {
    code: 'cs', name: 'CSKH',
    role_desc: 'Trả lời câu hỏi thường gặp, ghi nhận khiếu nại, theo dõi thời gian phản hồi khách hàng.',
    voice: 'Ấm áp, kiên nhẫn, xin lỗi đúng mực khi khách phàn nàn', speak_when: 'Khi khách hỏi hoặc phàn nàn trong nhóm/kênh chăm sóc',
    forbidden: ['Hứa hoàn tiền hoặc đền bù', 'Tiết lộ thông tin khách hàng khác'], default_enabled: true,
  },
  {
    code: 'recruiter', name: 'Tuyển dụng',
    role_desc: 'Sàng lọc ứng viên, nhắc lịch phỏng vấn, tổng hợp hồ sơ.',
    voice: 'Chuyên nghiệp, tôn trọng ứng viên', speak_when: 'Khi có hồ sơ ứng viên mới hoặc lịch phỏng vấn sắp tới',
    forbidden: ['Đưa ra kết quả tuyển dụng cuối cùng', 'Nhận xét đánh giá cá nhân ứng viên'], default_enabled: true,
  },
  {
    code: 'secretary', name: 'Thư ký cá nhân',
    role_desc: 'Nhắc việc, tổng hợp tin nhắn quan trọng, quản lý lịch cho Owner.',
    voice: 'Riêng tư, ngắn gọn, đúng giờ', speak_when: 'Khi có việc cần nhắc hoặc tin nhắn quan trọng gửi tới Owner',
    forbidden: ['Trả lời thay Owner trong các quyết định cá nhân', 'Chia sẻ lịch trình ra ngoài nhóm riêng'], default_enabled: true,
  },
  {
    code: 'mascot', name: 'Bé Heo',
    role_desc: 'Mẫu trò chuyện phiếm, vui vẻ — không đảm nhiệm việc kinh doanh. Tắt sẵn, Sếp tự bật khi cần.',
    voice: 'Dí dỏm, thân mật', speak_when: 'Chỉ khi được gọi trực tiếp',
    forbidden: ['Tự ý tham gia nghiệp vụ kinh doanh', 'Gửi tin ra ngoài khi chưa được bật'], default_enabled: false,
  },
];

function seedAgents(): AgentIdentity[] {
  const now = new Date().toISOString();
  const scope = (channelType: string, groupId: string | null, groupName: string | null): AgentChannelScope => ({
    channel_id: `ch-${channelType}`, channel_type: channelType, group_id: groupId, group_name: groupName,
  });
  return [
    {
      id: AGENT_IDS.tls, name: 'Trợ lý thương mại', role_desc: TEMPLATES[0].role_desc, template: 'commercial',
      addressing: { customer: 'anh/chị', internal: 'Sếp' }, voice: TEMPLATES[0].voice, speak_when: TEMPLATES[0].speak_when,
      forbidden: TEMPLATES[0].forbidden, autonomy_level: 4, is_enabled: true,
      limits: { decisions_per_min: 20, drafts_per_hour: 30 }, created_at: ago(60 * 24 * 40), updated_at: ago(60 * 6),
      channel_scopes: [scope('zalo', GROUP_TP.id, GROUP_TP.name)],
      binding: { model_id: 'md-gemini-flash', model_name: 'gemini-2.5-flash', provider_name: 'Gemini API', temperature: 0.4, context_tokens: 32_000, rule_codes: ['R-01', 'R-02'] },
    },
    {
      id: AGENT_IDS.hc, name: 'Admin hậu cần', role_desc: TEMPLATES[2].role_desc, template: 'admin',
      addressing: { internal: 'anh/chị' }, voice: TEMPLATES[2].voice, speak_when: TEMPLATES[2].speak_when,
      forbidden: TEMPLATES[2].forbidden, autonomy_level: 3, is_enabled: true,
      limits: { decisions_per_min: 20, drafts_per_hour: 30 }, created_at: ago(60 * 24 * 30), updated_at: ago(60 * 20),
      channel_scopes: [scope('zalo', null, null)],
      binding: { model_id: 'md-deepseek-chat', model_name: 'deepseek-chat', provider_name: 'DeepSeek API', temperature: 0.3, context_tokens: 16_000, rule_codes: [] },
    },
    {
      id: AGENT_IDS.cs, name: 'CSKH', role_desc: TEMPLATES[3].role_desc, template: 'cs',
      addressing: { customer: 'anh/chị' }, voice: TEMPLATES[3].voice, speak_when: TEMPLATES[3].speak_when,
      forbidden: TEMPLATES[3].forbidden, autonomy_level: 2, is_enabled: true,
      limits: { decisions_per_min: 20, drafts_per_hour: 30 }, created_at: ago(60 * 24 * 12), updated_at: ago(60 * 3),
      channel_scopes: [],
      binding: null,
    },
    {
      id: AGENT_IDS.mascot, name: 'Bé Heo', role_desc: TEMPLATES[6].role_desc, template: 'mascot',
      addressing: {}, voice: TEMPLATES[6].voice, speak_when: TEMPLATES[6].speak_when,
      forbidden: TEMPLATES[6].forbidden, autonomy_level: 0, is_enabled: false,
      limits: { decisions_per_min: 20, drafts_per_hour: 30 }, created_at: ago(60 * 24 * 400), updated_at: ago(60 * 24 * 400),
      channel_scopes: [],
      binding: null,
    },
  ].map((a) => ({ ...a, created_at: a.created_at, updated_at: a.updated_at || now }));
}

interface DecisionRow {
  id: string;
  at: string;
  agent: { id: string; name: string };
  decision: 'silent' | 'note' | 'suggest' | 'draft' | 'send';
  rationale: string | null;
  trigger: { type: string; id: string; code?: string | null; label?: string | null };
  context_refs: { type: string; id: string; code?: string | null; label?: string | null }[];
  draft: { id: string; code: string; status?: string } | null;
  outcome?: string | null;
  proposal_text?: string | null;
}

function seedDecisions(agents: AgentIdentity[]): DecisionRow[] {
  const nameOf = (id: string) => agents.find((a) => a.id === id)?.name ?? id;
  return [
    { id: 'adec-1', at: ago(4), agent: { id: AGENT_IDS.tls, name: nameOf(AGENT_IDS.tls) }, decision: 'draft', rationale: 'Khách hỏi giá lần hai, có đủ thông tin để soạn báo giá theo bảng giá đã duyệt.', trigger: { type: 'meaning_unit', id: 'mu-901', code: 'OPP-1842', label: 'Hỏi giá MDF E1' }, context_refs: [], draft: { id: 'draft-ACT-0231', code: 'ACT-0231', status: 'pending' }, outcome: 'held', proposal_text: 'Dạ em gửi anh báo giá MDF E1 theo bảng giá đã duyệt ạ.' },
    { id: 'adec-2', at: ago(18), agent: { id: AGENT_IDS.tls, name: nameOf(AGENT_IDS.tls) }, decision: 'send', rationale: 'Trả lời câu hỏi thường gặp về thời gian giao hàng, không vượt phạm vi cho phép.', trigger: { type: 'raw', id: 'raw-4471', code: null, label: `Tin nhắn của ${BAO.name}` }, context_refs: [{ type: 'person', id: BAO.id, code: BAO.code, label: BAO.name }], draft: null, outcome: 'held' },
    { id: 'adec-3', at: ago(47), agent: { id: AGENT_IDS.hc, name: nameOf(AGENT_IDS.hc) }, decision: 'draft', rationale: 'Đối tác yêu cầu hợp đồng vòng ba, đã có đủ điều khoản đã thống nhất trong nhóm.', trigger: { type: 'meaning_unit', id: 'mu-877', code: 'ACT-0234', label: 'Yêu cầu hợp đồng vòng ba' }, context_refs: [{ type: 'group', id: GROUP_TP.id, code: GROUP_TP.code, label: GROUP_TP.name }], draft: { id: 'draft-ACT-0234', code: 'ACT-0234' } },
    { id: 'adec-4', at: ago(90), agent: { id: AGENT_IDS.hc, name: nameOf(AGENT_IDS.hc) }, decision: 'note', rationale: 'Ghi nhận yêu cầu hậu cần mới, chưa đủ thông tin để hành động ngay.', trigger: { type: 'raw', id: 'raw-4402', code: null, label: 'Yêu cầu đổi lịch giao hàng' }, context_refs: [], draft: null },
    { id: 'adec-5', at: ago(134), agent: { id: AGENT_IDS.tls, name: nameOf(AGENT_IDS.tls) }, decision: 'suggest', rationale: 'Khách có dấu hiệu lạnh dần, đề xuất Sếp chủ động liên hệ thay vì tự trả lời.', trigger: { type: 'alert', id: 'iq-alert-1', code: 'ALR-0233', label: 'Khách đang lạnh / sắp mất' }, context_refs: [{ type: 'person', id: BAO.id, code: BAO.code, label: BAO.name }], draft: null, outcome: 'suggested', proposal_text: 'Anh nên gọi chủ động cho khách trong chiều nay, nhắc lại báo giá hôm qua.' },
    { id: 'adec-6', at: ago(300), agent: { id: AGENT_IDS.cs, name: nameOf(AGENT_IDS.cs) }, decision: 'silent', rationale: 'Câu hỏi ngoài phạm vi CSKH (giá hợp đồng), im lặng chờ nhân viên phụ trách.', trigger: { type: 'raw', id: 'raw-4390', code: null, label: 'Câu hỏi ngoài phạm vi' }, context_refs: [], draft: null },
  ];
}

const normForbidden = (v: unknown): string[] =>
  (Array.isArray(v) ? (v as unknown[]) : []).map((x) => String(x).trim()).filter(Boolean);
const sameJson = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** v0.1.45 (F-20): gương `gh.agents_api.routes._barrier_changed` — gửi lại đúng giá trị cũ thì không coi là đổi. */
function barrierChanged(target: AgentIdentity, b: Record<string, unknown>): boolean {
  if (typeof b.autonomy_level === 'number' && b.autonomy_level !== target.autonomy_level) return true;
  if (Array.isArray(b.forbidden) && !sameJson(normForbidden(b.forbidden), normForbidden(target.forbidden))) return true;
  if (b.limits && typeof b.limits === 'object') {
    const cur = { ...target.limits };
    const next = { ...target.limits, ...(b.limits as Record<string, number>) };
    if (Object.keys(next).some((k) => next[k] !== cur[k])) return true;
  }
  if (Array.isArray(b.channel_scopes)) {
    const key = (s: { channel_id: string; group_id?: string | null }) => `${s.channel_id}|${s.group_id ?? ''}`;
    const want = new Set((b.channel_scopes as Array<{ channel_id: string; group_id?: string | null }>).map(key));
    const have = new Set(target.channel_scopes.map(key));
    if (want.size !== have.size || [...want].some((k) => !have.has(k))) return true;
  }
  return false;
}

export function createMock(opts: P4Options) {
  let agents: AgentIdentity[] = opts.fresh ? [] : seedAgents();
  const decisions: DecisionRow[] = opts.fresh ? [] : seedDecisions(agents);
  const has = (ctx: P2Ctx, perm: string) => !!ctx.perms[perm] && ctx.perms[perm] !== 'none';
  const pin = (ctx: P2Ctx, operation: string) => {
    if (!ctx.needPin()) return true;
    ctx.problem(423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation } });
    return false;
  };
  function resolveScopes(raw: unknown): AgentChannelScope[] {
    if (!Array.isArray(raw)) return [];
    const channels = opts.getChannels();
    return (raw as Array<{ channel_id: string; group_id?: string | null }>).map((s) => ({
      channel_id: s.channel_id, channel_type: channels.find((c) => c.id === s.channel_id)?.type ?? '?',
      group_id: s.group_id ?? null, group_name: null,
    }));
  }

  const nameTaken = (name: string) => agents.some((a) => !a.archived && a.name.trim().toLowerCase() === name.trim().toLowerCase());
  /** Gương `_attach_model_and_spoke`: dòng model + lần lên tiếng gần nhất theo từng agent (chỉ suggest/draft/send). */
  const withLive = (a: AgentIdentity): AgentIdentity => {
    const last = decisions.find((d) => d.agent.id === a.id && ['suggest', 'draft', 'send'].includes(d.decision));
    return {
      ...a,
      model: a.binding
        ? { custom: true, ok: true, label: `${a.binding.model_name} · ${a.binding.provider_name}` }
        : { custom: false, ok: true, label: 'Chuẩn: gemini-2.5-flash (tự chọn)' },
      last_spoke_at: last?.at ?? null,
      last_spoke_decision: (last?.decision as 'suggest' | 'draft' | 'send' | undefined) ?? null,
    };
  };

  function handle(ctx: P2Ctx): boolean {
    const { method: m, path: p, url, body, reply, problem } = ctx;
    const seg = p.split('/').filter(Boolean);

    if (p === '/agents/diplomat' && m === 'GET') {
      if (!has(ctx, 'system.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      return reply(200, { agent_id: null, name: null, neutral_name: 'trợ lý' });
    }
    if (p === '/agents/decisions' && m === 'GET') {
      if (!has(ctx, 'system.read') && !has(ctx, 'action.approve')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      const agentId = url.searchParams.get('agent_id');
      const decision = url.searchParams.get('decision');
      const limit = Number(url.searchParams.get('limit') ?? 50);
      let rows = decisions;
      if (agentId) rows = rows.filter((d) => d.agent.id === agentId);
      if (decision) rows = rows.filter((d) => d.decision === decision);
      return reply(200, { items: rows.slice(0, limit), next_cursor: null, total: rows.length });
    }

    // `/agents/bindings*` (PLAN 4.2, gán model) thuộc `mock-p4-api.ts` — không phải CRUD danh tính agent ở
    // đây. Chặn TRƯỚC nhánh `agents.find(a => a.id === seg[1])` bên dưới, nếu không "bindings" bị nuốt nhầm
    // thành một agent_id (giống lý do `/providers/chain` phải tránh `/providers/{pid}` ở phase 2/backend).
    if (seg[0] !== 'agents' || seg[1] === 'bindings') return false;

    if (seg[1] === 'templates' && seg.length === 2 && m === 'GET') {
      if (!has(ctx, 'system.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      return reply(200, TEMPLATES);
    }

    if (seg.length === 1 && m === 'GET') {
      if (!has(ctx, 'system.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      return reply(200, agents.filter((a) => !a.archived).map(withLive));
    }

    if (seg.length === 1 && m === 'POST') {
      if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (!pin(ctx, 'agent.manage')) return true;
      const b = body as Record<string, unknown>;
      const name = String(b.name ?? '').trim();
      const roleDesc = String(b.role_desc ?? '').trim();
      const voice = String(b.voice ?? '').trim();
      const speakWhen = String(b.speak_when ?? '').trim();
      const errors: Record<string, string> = {};
      if (!name) errors.name = 'Không được để trống';
      else if (nameTaken(name)) errors.name = `Đã có agent tên “${name}” — đặt tên khác để khỏi nhầm.`;
      if (!roleDesc) errors.role_desc = 'Không được để trống';
      if (!voice) errors.voice = 'Không được để trống';
      if (!speakWhen) errors.speak_when = 'Không được để trống';
      if (Object.keys(errors).length) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors });
      const a: AgentIdentity = {
        id: randomUUID(), name, role_desc: roleDesc, voice, speak_when: speakWhen,
        template: b.template != null ? String(b.template) : null,
        addressing: (b.addressing as Record<string, unknown>) ?? {},
        forbidden: Array.isArray(b.forbidden) ? (b.forbidden as string[]) : [],
        autonomy_level: typeof b.autonomy_level === 'number' ? b.autonomy_level : 4,
        is_enabled: b.is_enabled !== false,
        limits: (b.limits as Record<string, number>) ?? {},
        created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
        channel_scopes: resolveScopes(b.channel_scopes),
        binding: null,
      };
      agents = [...agents, a];
      return reply(201, a);
    }

    const target = agents.find((a) => a.id === seg[1]);

    if (seg.length === 2 && m === 'GET') {
      if (!has(ctx, 'system.read')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (!target) return problem(404, 'NOT_FOUND', 'Agent không tồn tại');
      return reply(200, target);
    }

    if (seg.length === 2 && m === 'PATCH') {
      if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (!target) return problem(404, 'NOT_FOUND', 'Agent không tồn tại');
      const b = body as Record<string, unknown>;
      // v0.1.45 (F-20): như API — PIN `policy.change` chỉ khi rào chắn (mức tự trị/điều cấm/giới hạn/phạm vi kênh) đổi giá trị.
      if (barrierChanged(target, b) && !pin(ctx, 'policy.change')) return true;
      if (typeof b.name === 'string' && b.name.trim()) target.name = b.name.trim();
      if (typeof b.role_desc === 'string' && b.role_desc.trim()) target.role_desc = b.role_desc.trim();
      if (typeof b.voice === 'string' && b.voice.trim()) target.voice = b.voice.trim();
      if (typeof b.speak_when === 'string' && b.speak_when.trim()) target.speak_when = b.speak_when.trim();
      if ('template' in b) target.template = b.template == null ? null : String(b.template);
      if (typeof b.autonomy_level === 'number') target.autonomy_level = b.autonomy_level;
      if (Array.isArray(b.forbidden)) target.forbidden = b.forbidden as string[];
      if (b.addressing && typeof b.addressing === 'object') target.addressing = b.addressing as Record<string, unknown>;
      if (b.limits && typeof b.limits === 'object') target.limits = b.limits as Record<string, number>;
      if (Array.isArray(b.channel_scopes)) {
        target.channel_scopes = resolveScopes(b.channel_scopes);
      } else if (b.channel_scopes === null) {
        target.channel_scopes = [];
      }
      target.updated_at = new Date().toISOString();
      return reply(200, target);
    }

    if (seg[2] === 'clone' && seg.length === 3 && m === 'POST') {
      if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (!pin(ctx, 'agent.manage')) return true;
      if (!target) return problem(404, 'NOT_FOUND', 'Agent không tồn tại');
      const b = body as { name?: string; copy_channel_scopes?: boolean };
      const name = String(b.name ?? '').trim();
      if (!name) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { name: 'Không được để trống' } });
      if (nameTaken(name)) return problem(422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { name: `Đã có agent tên “${name}” — đặt tên khác để khỏi nhầm.` } });
      const clone: AgentIdentity = {
        ...target, id: randomUUID(), name, is_enabled: false,
        channel_scopes: b.copy_channel_scopes === false ? [] : target.channel_scopes.map((s) => ({ ...s })),
        binding: target.binding ? { ...target.binding } : null, created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
      };
      agents = [...agents, clone];
      return reply(201, clone);
    }

    if (seg[2] === 'disable' && seg.length === 3 && m === 'PATCH') {
      if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      const enabled = (body as { enabled?: boolean }).enabled === true;
      // v0.1.67 (F089): TẮT không cần PIN, BẬT lại cần PIN.
      if (enabled && !pin(ctx, 'agent.manage')) return true;
      if (!target) return problem(404, 'NOT_FOUND', 'Agent không tồn tại');
      target.is_enabled = enabled;
      target.updated_at = new Date().toISOString();
      return reply(200, target);
    }

    if (seg[2] === 'archive' && seg.length === 3 && m === 'POST') {
      if (!has(ctx, 'system.manage')) return problem(403, 'FORBIDDEN', 'Vai trò không có quyền này');
      if (!pin(ctx, 'agent.manage')) return true;
      if (!target) return problem(404, 'NOT_FOUND', 'Agent không tồn tại');
      target.archived = true;
      target.is_enabled = false;
      target.binding = null;
      return reply(200, target);
    }

    return false;
  }

  return {
    handle,
    hooks: {
      list: () => agents,
    } as Record<string, (...args: never[]) => unknown>,
    dispose: () => {},
  };
}
