/**
 * Hợp đồng API giai đoạn 4 · API & Model (PLAN 4.2, `apps/api/gh/agents_api/routes.py` +
 * `apps/api/gh/system_api/routes.py`).
 *
 * Provider/khoá/model/CLI (`/providers`, `/providers/{id}/keys`, `/providers/{id}/models`,
 * `/providers/{id}/test`, `/providers/credentials`, `/cli/*`) đã có hợp đồng từ giai đoạn 2 ở `endpoints.ts` —
 * KHÔNG lặp lại. Module này chỉ thêm phần mới của 4.2: gán model theo agent/mục đích (`agent.bindings`) và quy
 * tắc chuyển hướng tĩnh (`/failover-rules`). `PATCH /providers/chain` (kéo-thả) được thêm thẳng vào namespace
 * `providers` sẵn có ở `endpoints.ts`, cùng chỗ với `providers.update`.
 */
import type { ApiClient } from './client';
import type { AgentBinding } from './p4-agents';
import type { Effort } from './phase2';

/** v0.1.55 (G1): dòng gán model — thêm `effort` (mức suy nghĩ riêng của vai; null = theo hồ sơ tiêu chuẩn / model). */
export type BindingWithEffort = AgentBinding & { effort?: Effort | null };

/**
 * v0.1.55 (G1): model "Chuẩn" đang phủ một vai khi Owner chưa gán (hồ sơ tiêu chuẩn theo vai —
 * `gh/defaults/profiles.py`). Chỉ chuỗi/số/null; máy chủ cũ không có trường này.
 */
export interface StandardPick {
  model_name: string;
  provider_name: string;
  tier: 'fast' | 'balanced' | 'strong';
  /** Nhanh / Cân bằng / Kỹ hơn. */
  tier_label: string;
  effort: Effort | null;
  temperature: number;
  context_tokens: number;
}

export interface AgentBindingSlot {
  agent_key: string;
  label: string;
  binding: BindingWithEffort | null;
  /**
   * v0.1.55 (G1): `custom` = Owner đã gán (có dòng) · `standard` = chưa gán, dùng hồ sơ tiêu chuẩn. Máy chủ cũ không có
   * trường này ⇒ suy từ `binding` (có = custom).
   */
  source?: 'custom' | 'standard';
  /** v0.1.55 (G1): model hồ sơ đang phủ khi chưa có dòng gán; null = chưa có nguồn phù hợp. */
  standard?: StandardPick | null;
  /**
   * v0.1.58: vì sao `standard` null ("cần khoá API hoặc đăng nhập Antigravity CLI", "chưa có model — bấm Kiểm tra kết nối ở
   * …"…) — CHUỖI hoặc null, không bao giờ object. Máy chủ cũ không có trường này ⇒ web dùng câu cũ.
   */
  standard_reason?: string | null;
  /**
   * v0.1.63 (H2): lý do Antigravity CLI CHƯA dùng được cho dòng này ("Hộp riêng Antigravity chưa chạy / chưa được cài": dòng
   * chạy agy qua hộp riêng — `core.reply`, `core.refinery`, agent gắn kênh / nhóm / người) — CHUỖI hoặc null (dùng được).
   * PUT /agents/bindings trả 422 khi gán model agy cho dòng có lý do này. Máy chủ cũ không có trường ⇒ coi như null.
   */
  agy_blocked_reason?: string | null;
}

export interface BindableModel {
  id: string;
  model_name: string;
  provider_name: string;
  /** Gói G: loại nguồn của model (`antigravity_cli`, `gemini`…) — web làm mờ model agy ở dòng có `agy_blocked_reason`. */
  kind?: string;
  enabled: boolean;
}

export interface BindingsPage {
  items: AgentBindingSlot[];
  models: BindableModel[];
}

export interface BindingSetBody {
  model_id: string;
  temperature?: number;
  context_tokens?: number;
  rule_codes?: string[];
  /** v0.1.55 (G1): mức suy nghĩ theo vai (422 `effort` khi model không nhận mức đó). Bỏ trống = theo hồ sơ tiêu chuẩn. */
  effort?: Effort | null;
}

export interface FailoverRule {
  key: string;
  value: string;
}

/**
 * v0.1.41 (F-86, QD-12): `GET/PUT /providers/background` — "Nguồn AI cho việc nền" (sàng lọc tin, trực việc, Bản tin Gen;
 * purpose ∈ refinery/duty_decide/gen.briefing). Mặc định chỉ khoá API; Owner có thể cho Claude Code CLI chạy việc nền
 * (cảnh báo + xác nhận + PIN `ai.background_cli`). Antigravity CLI chạy được sàng lọc tin và Bản tin không cần cho phép
 * (v0.1.62); agent trực kênh (ngoại giao) và sàng lọc chạy agy QUA HỘP RIÊNG (v0.1.63, H2) — hộp chưa chạy thì dùng nguồn khác.
 */
export interface BackgroundSource {
  provider_id: string;
  name: string;
  kind: string;
  used: boolean;
  /** Lý do không dùng (chuỗi cho người đọc); null khi đang dùng. */
  reason: string | null;
}
export interface BackgroundSources {
  allow_cli: string[];
  accepted_at: string | null;
  /** Câu cảnh báo — web hiện NGUYÊN VĂN trong hộp xác nhận. */
  risk_text: string;
  purposes: string[];
  has_api_source: boolean;
  /** v0.1.62: có Antigravity CLI đang bật + có model — chạy được việc nền không cần Owner cho phép. Máy chủ cũ: thiếu. */
  has_agy_source?: boolean;
  sources: BackgroundSource[];
}
export interface BackgroundSourcesBody {
  allow_cli: string[];
  accept_risk: boolean;
}

const enc = encodeURIComponent;

/** `agent.bindings` (`/agents/bindings*`) + `/failover-rules` (chỉ đọc, ARCHITECTURE §11). */
export function agentModelEndpoints(r: ApiClient['request']) {
  return {
    bindings: {
      list: (signal?: AbortSignal) => r<BindingsPage>('/agents/bindings', { signal }),
      set: (agentKey: string, body: BindingSetBody) =>
        r<{ agent_key: string; label: string; binding: BindingWithEffort; source?: 'custom'; standard?: null }>(`/agents/bindings/${enc(agentKey)}`, { method: 'PUT', body }),
      remove: (agentKey: string) => r<void>(`/agents/bindings/${enc(agentKey)}`, { method: 'DELETE' }),
    },
    failoverRules: (signal?: AbortSignal) => r<FailoverRule[]>('/failover-rules', { signal }),
  };
}
