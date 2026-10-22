/**
 * Hợp đồng API giai đoạn 4 · Danh tính Agent (PLAN 4.1, `apps/api/gh/agents_api/routes.py`).
 *
 * "Agent đã nói gì, nhân danh gì" dùng `agentDecisions` (đã có ở `p3-core.ts`, `GET /agents/decisions`) —
 * KHÔNG lặp lại ở đây.
 */
import type { ApiClient } from './client';

/** Mẫu có sẵn (spec E13) — gợi ý prefill khi tạo mới, không phải bản sắc hệ thống, không mặc định bắt buộc. */
export interface AgentTemplate {
  code: string;
  name: string;
  role_desc: string;
  voice: string;
  speak_when: string;
  forbidden: string[];
  default_enabled: boolean;
}

export interface AgentChannelScope {
  channel_id: string;
  channel_type: string;
  group_id: string | null;
  group_name: string | null;
}

export interface AgentChannelScopeIn {
  channel_id: string;
  group_id?: string | null;
}

export interface AgentBinding {
  model_id: string;
  model_name: string;
  provider_name: string;
  temperature: number;
  context_tokens: number;
  rule_codes: string[];
  /**
   * v0.1.67 (F016): dòng gán còn đó nhưng nguồn/model của nó đã bị tắt ⇒ agent đang chạy bằng hồ sơ Chuẩn
   * ("Nguồn đã tắt — agent đang dùng Chuẩn"). `null`/thiếu = dòng gán đang có hiệu lực.
   * (v0.1.62 đã bỏ lý do cũ "agy chỉ cho Gen".)
   */
  inactive_reason?: string | null;
}

/** v0.1.67 (F022): agent dùng model nào / chạy được không — một dòng chữ máy chủ dựng sẵn (không object lồng). */
export interface AgentModelLine {
  /** `true` = Sếp đã gán model riêng cho agent; `false` = dùng hồ sơ Chuẩn. */
  custom: boolean;
  /** `false` = agent chưa có model nào chạy được (vd. chỉ có Google/Claude — chưa có khoá API). */
  ok: boolean;
  label: string;
}

export interface AgentIdentity {
  id: string;
  name: string;
  role_desc: string;
  template: string | null;
  addressing: Record<string, unknown>;
  voice: string;
  speak_when: string;
  forbidden: string[];
  autonomy_level: number;
  is_enabled: boolean;
  limits: Record<string, number>;
  created_at: string;
  updated_at: string;
  channel_scopes: AgentChannelScope[];
  binding: AgentBinding | null;
  /** v0.1.67 (F022) — máy chủ cũ không trả thì thiếu. */
  model?: AgentModelLine;
  /** v0.1.67 (F083): lần lên tiếng (đề xuất / soạn nháp / gửi) gần nhất của RIÊNG agent này; `null` = chưa từng. */
  last_spoke_at?: string | null;
  last_spoke_decision?: 'suggest' | 'draft' | 'send' | null;
  /** v0.1.67 (F085): agent đã Lưu trữ (danh sách không trả loại này). */
  archived?: boolean;
}

export interface AgentCreateBody {
  name: string;
  role_desc: string;
  voice: string;
  speak_when: string;
  template?: string | null;
  addressing?: Record<string, unknown>;
  forbidden?: string[];
  autonomy_level?: number;
  limits?: Record<string, number>;
  is_enabled?: boolean;
  channel_scopes?: AgentChannelScopeIn[];
}

export type AgentPatchBody = Partial<Omit<AgentCreateBody, 'channel_scopes'>> & {
  channel_scopes?: AgentChannelScopeIn[] | null;
};

export interface AgentCloneBody {
  name: string;
  copy_channel_scopes?: boolean;
}

/** v0.1.73: «Agent ngoại giao» — agent nói thay Gen ở mọi kênh ngoài Console. `agent_id` null = chưa chọn (bot xưng «trợ lý»). */
export interface DiplomatAgent {
  agent_id: string | null;
  name: string | null;
  neutral_name: string;
}

const enc = encodeURIComponent;

/** `GET/POST /agents`, `/agents/{id}`, `/{id}/clone`, `/{id}/disable`, `/{id}/archive`, `/agents/templates`. */
export function agentsEndpoints(r: ApiClient['request']) {
  return {
    agents: {
      templates: (signal?: AbortSignal) => r<AgentTemplate[]>('/agents/templates', { signal }),
      list: (signal?: AbortSignal) => r<AgentIdentity[]>('/agents', { signal }),
      get: (id: string, signal?: AbortSignal) => r<AgentIdentity>(`/agents/${enc(id)}`, { signal }),
      create: (body: AgentCreateBody) => r<AgentIdentity>('/agents', { method: 'POST', body }),
      update: (id: string, body: AgentPatchBody) => r<AgentIdentity>(`/agents/${enc(id)}`, { method: 'PATCH', body }),
      clone: (id: string, body: AgentCloneBody) => r<AgentIdentity>(`/agents/${enc(id)}/clone`, { method: 'POST', body }),
      setEnabled: (id: string, enabled: boolean) =>
        r<AgentIdentity>(`/agents/${enc(id)}/disable`, { method: 'PATCH', body: { enabled } }),
      /** v0.1.67 (F085): Lưu trữ — tắt hẳn + ẩn khỏi danh sách, giữ lịch sử quyết định. Cần PIN `agent.manage`. */
      archive: (id: string) => r<AgentIdentity>(`/agents/${enc(id)}/archive`, { method: 'POST', body: {} }),
      /** v0.1.73: Agent ngoại giao của tổ chức (đổi cần Owner + mã PIN). */
      diplomat: (signal?: AbortSignal) => r<DiplomatAgent>('/agents/diplomat', { signal }),
      setDiplomat: (agentId: string | null) => r<DiplomatAgent>('/agents/diplomat', { method: 'PUT', body: { agent_id: agentId } }),
    },
  };
}
