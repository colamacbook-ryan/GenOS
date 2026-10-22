/** Presentation logic for API & Model — mirrors `dataModel.ts` / `agentsModel.ts` conventions. */
import { ApiError } from '@gen-harness/contracts';
import type { AgentBindingSlot, Effort, ModelGroup, ModelOption, Provider, ProviderDiagnosis, ProviderKind, ProviderTestResult } from '@gen-harness/contracts';
import { fmtDMClock } from '../../lib/format';

export const OK = 'var(--color-ok)';
export const WARN = 'var(--color-warn)';
export const BAD = 'var(--color-bad)';
export const ACC3 = 'var(--color-accent-300)';
export const N4 = 'var(--color-neutral-400)';
export const N5 = 'var(--color-neutral-500)';

export const PROVIDER_ICON: Record<ProviderKind, string> = {
  antigravity_cli: 'ph ph-terminal-window',
  claude_code_cli: 'ph ph-terminal',
  gemini: 'ph ph-sparkle',
  deepseek: 'ph ph-brain',
  openai_compat: 'ph ph-plugs',
  system_one: 'ph ph-lightning',
};

export const PROVIDER_KIND_LABEL: Record<ProviderKind, string> = {
  antigravity_cli: 'Antigravity CLI',
  claude_code_cli: 'Claude Code CLI · gói Claude',
  gemini: 'Gemini API',
  deepseek: 'DeepSeek API',
  openai_compat: 'API tương thích OpenAI',
  system_one: 'Jev (System One) · quyết định nhanh',
};

/**
 * v0.1.41 (F-84): mẫu nhà cung cấp dựng sẵn — MỘT nguồn dùng chung cho hộp "Thêm nhà cung cấp" (API & Model) và Hướng
 * dẫn bước 4. Chọn mẫu ⇒ gửi `kind` thật (vd `openai_compat`), điền sẵn Tên + Endpoint (vẫn sửa được) và gợi ý model.
 */
export interface ProviderPreset {
  id: string;
  label: string;
  kind: Extract<ProviderKind, 'openai_compat' | 'gemini' | 'deepseek'>;
  name: string;
  endpoint: string;
  modelHint: string;
  /** Dòng hướng dẫn lấy khoá. */
  keyHint: string;
}

export const PROVIDER_PRESETS: ReadonlyArray<ProviderPreset> = [
  {
    id: 'openrouter',
    label: 'OpenRouter (nhiều model, một khoá)',
    kind: 'openai_compat',
    name: 'OpenRouter',
    endpoint: 'https://openrouter.ai/api/v1',
    modelHint: 'google/gemini-2.5-flash',
    keyHint: 'Tạo khoá ở openrouter.ai › Keys rồi dán vào đây',
  },
];

export function findPreset(id: string): ProviderPreset | undefined {
  return PROVIDER_PRESETS.find((p) => p.id === id);
}

export const AUTH_STATE_LABEL: Record<Provider['auth_state'], string> = {
  ok: 'Hoạt động',
  expiring: 'Sắp hết hạn',
  expired: 'Hết hạn',
  error: 'Lỗi kết nối',
  unconfigured: 'Chưa cấu hình',
};

export function authStateTone(s: Provider['auth_state']): string {
  return s === 'ok' ? OK : s === 'expiring' ? WARN : N5;
}

export function providerTone(p: Provider): string {
  return providerStatus(p).tone;
}

/**
 * v0.1.28 (UX N1): MỘT nhãn trạng thái cho nguồn model ở mọi màn (bước 4, API & Model, Điều khiển hệ thống › Bộ não
 * AI) — trước đây cùng một nguồn lỗi hiện "Chưa cấu hình" / "Chờ kết nối" / "Lỗi kết nối" / "Hoạt động" tuỳ màn.
 * Khớp `state_label` của `GET /providers/credentials` (máy chủ).
 */
export function providerStatus(p: Pick<Provider, 'enabled' | 'auth_state' | 'kind'> & { models?: Provider['models'] }): { label: string; tone: string } {
  if (!p.enabled) return { label: 'Đã tắt', tone: N5 };
  switch (p.auth_state) {
    case 'ok':
      return p.kind !== 'system_one' && p.models && p.models.length === 0 ? { label: 'Chưa chọn model', tone: WARN } : { label: 'Hoạt động', tone: OK };
    case 'expiring':
      return { label: 'Sắp hết hạn', tone: WARN };
    case 'expired':
      return { label: 'Hết hạn', tone: BAD };
    case 'error':
      return { label: 'Lỗi kết nối', tone: BAD };
    default:
      return { label: isCliKind(p.kind) ? 'Chưa đăng nhập' : 'Chưa kiểm tra', tone: N5 };
  }
}

/** ARCHITECTURE §11 — tham số core dùng cho `core.refinery` (sàng lọc thô → sạch). */
export function fmtTemperature(t: number): string {
  return t.toFixed(2).replace('.', ',');
}

export function fmtContextTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(n % 1000 === 0 ? 0 : 1)}k token` : `${n} token`;
}

export function fmtRateLimit(n: number | null): string {
  return n == null ? 'không giới hạn' : `${n} / phút`;
}

export function fmtQuota(used: number, quota: number | null): string {
  if (quota == null) return `${used.toLocaleString('vi-VN')} · không hạn mức`;
  const leftPct = Math.max(0, Math.round(100 - (used * 100) / quota));
  return `${used.toLocaleString('vi-VN')} / ${quota.toLocaleString('vi-VN')} · còn ${leftPct}%`;
}

// ── v0.1.31: nguồn CLI + danh sách model theo nhóm ─────────────────────────
/** Nguồn dùng phiên đăng nhập CLI (không khoá API, không xoá ở danh sách nguồn). */
export function isCliKind(kind: ProviderKind | string): kind is 'antigravity_cli' | 'claude_code_cli' {
  return kind === 'antigravity_cli' || kind === 'claude_code_cli';
}

/**
 * Nhóm model để chọn (Gemini, Claude…) từ lần kiểm tra gần nhất — chỉ khi kiểm tra OK. Máy chủ cũ (không có
 * `model_groups`) → một nhóm "Model" từ danh sách tên. Bỏ model embedding (không sinh chữ được).
 */
export function offeredGroups(t: ProviderTestResult | null | undefined): ModelGroup[] {
  if (!t?.ok) return [];
  const notEmbed = (id: string) => !/embed/i.test(id);
  if (t.model_groups?.length)
    return t.model_groups.map((g) => ({ ...g, models: g.models.filter((m) => notEmbed(m.id)) })).filter((g) => g.models.length > 0);
  const ids = (t.models ?? []).filter(notEmbed);
  return ids.length
    ? [{ label: 'Model', models: ids.map((id) => ({ id, label: id, group: 'Model', tier: 'balanced' as const, hint: '', source: 'cli' as const })) }]
    : [];
}

/**
 * v0.1.66 (F116, bài học BAI-10 "một nút kiểm tra không được làm mất đường thoát của người dùng"): nhóm model cho ô chọn
 * LUÔN có — lần Kiểm tra xanh thì lấy danh sách thật của CLI / nhà cung cấp; đỏ hoặc chưa kiểm thì lấy danh mục dự
 * phòng máy chủ gửi kèm (gắn "chưa xác minh"), nguồn khoá API không có thì chỉ còn model đã lưu (`ModelPicker` thêm).
 */
export function pickerGroups(t: ProviderTestResult | null | undefined): { groups: ModelGroup[]; live: boolean } {
  const live = offeredGroups(t);
  if (live.length) return { groups: live, live: true };
  const notEmbed = (id: string) => !/embed/i.test(id);
  const fallback = (t?.model_groups ?? [])
    .map((g) => ({ ...g, models: g.models.filter((m) => notEmbed(m.id)).map((m) => ({ ...m, verified: false })) }))
    .filter((g) => g.models.length > 0);
  return { groups: fallback, live: false };
}

/** Model Sếp đã chốt cho nguồn ("Dùng model này"); chưa chốt ⇒ null (mỗi vai tự chọn theo hồ sơ chuẩn). */
export function currentModelName(p: Pick<Provider, 'models'>): string | null {
  return p.models.find((m) => m.is_default)?.model_name ?? null;
}

/** Chữ ở chuỗi chuyển hướng (API & Model, Bộ não AI): model đã chốt; chưa chốt thì nói thật là theo vai (review v0.1.66, P2-1). */
export const CHAIN_BY_ROLE = 'theo vai — xem bảng Gán model cho từng agent';
export function chainModelText(p: Pick<Provider, 'models'>): string {
  return currentModelName(p) ?? (p.models.length ? CHAIN_BY_ROLE : '—');
}

/** Chữ hiện trong ô chọn: "Gemini 3.8 Flash · nhanh, rẻ" (+ "chưa xác minh" khi không có nguồn CLI/tài liệu). */
export function modelOptionText(m: Pick<ModelOption, 'id' | 'label' | 'hint'> & { verified?: boolean }): string {
  const base = m.hint ? `${m.label} · ${m.hint}` : m.label;
  return m.verified === false ? `${base} · chưa xác minh` : base;
}

// ── v0.1.32: mức suy nghĩ (effort) tách khỏi tên model ────────────────────
/** Boss 01/10: "high" là MỨC SUY NGHĨ, không phải tên model. */
export const EFFORT_LABEL: Record<Effort, string> = { low: 'Thấp', medium: 'Vừa', high: 'Cao', xhigh: 'Rất cao', max: 'Tối đa' };
export const EFFORT_HINT = 'Thấp = nhanh, rẻ · Cao = kỹ hơn, chậm hơn, tốn hạn mức hơn';
const EFFORT_NOTE: Record<Effort, string> = { low: 'nhanh, rẻ', medium: 'cân bằng', high: 'kỹ, chậm hơn', xhigh: 'kỹ hơn nữa', max: 'kỹ nhất, chậm nhất' };

/** Chữ trong ô "Mức suy nghĩ": "Thấp · nhanh, rẻ". */
export function effortOptionText(e: Effort): string {
  return `${EFFORT_LABEL[e] ?? e} · ${EFFORT_NOTE[e] ?? ''}`;
}

/**
 * v0.1.55 (G1): mức suy nghĩ mà một model của nguồn `p` nhận — theo danh sách CLI/danh mục ở lần kiểm tra gần nhất, không có thì
 * theo luật của máy chủ (`gh/defaults/profiles.py::allowed_efforts`): Claude Code CLI năm mức trừ haiku (không mức nào); Antigravity
 * CLI ba mức; nguồn khoá API không có mức suy nghĩ.
 */
export function supportedEfforts(p: Pick<Provider, 'kind' | 'last_test'> | undefined, modelName: string | undefined): Effort[] {
  if (!p || !modelName || !isCliKind(p.kind)) return [];
  const opt = offeredGroups(p.last_test)
    .flatMap((g) => g.models)
    .find((m) => m.id === modelName);
  if (opt && Array.isArray(opt.efforts)) return opt.efforts;
  if (p.kind === 'antigravity_cli') return ['low', 'medium', 'high'];
  return /haiku/i.test(modelName) ? [] : ['low', 'medium', 'high', 'xhigh', 'max'];
}

type SlotView = Pick<AgentBindingSlot, 'binding' | 'source' | 'standard'> & Partial<Pick<AgentBindingSlot, 'standard_reason'>>;

/**
 * Ô "Model" của bảng gán: model Sếp đã gán; chưa gán ⇒ "Chuẩn: <model> (tự chọn)" (hồ sơ tiêu chuẩn theo vai); chưa có model
 * chuẩn ⇒ "Chuẩn: <lý do>" (v0.1.58, máy chủ nêu `standard_reason`) — máy chủ cũ không có lý do ⇒ câu cũ.
 */
export function bindingModelText(slot: SlotView): string {
  if (slot.binding) return slot.binding.model_name;
  if (slot.standard && typeof slot.standard.model_name === 'string' && slot.standard.model_name) return `Chuẩn: ${slot.standard.model_name} (tự chọn)`;
  const why = typeof slot.standard_reason === 'string' ? slot.standard_reason.trim() : '';
  if (why) return `Chuẩn: ${why}`;
  return slot.source === 'standard' ? 'Chuẩn: chưa có nguồn phù hợp' : 'chưa gán';
}

/** Ô "Mức suy nghĩ": mức Sếp chọn cho vai; không có thì mức của hồ sơ chuẩn ("Vừa (chuẩn)"); không thì "—". */
export function bindingEffortText(slot: SlotView): string {
  const own = slot.binding?.effort;
  if (own) return EFFORT_LABEL[own] ?? own;
  const std = slot.binding ? null : slot.standard?.effort;
  return std ? `${EFFORT_LABEL[std] ?? std} (chuẩn)` : '—';
}

/**
 * Dòng "Nâng cao" của một vai (một chuỗi — web không render object). v0.1.66 (F021): bốn vai lõi (Gen, Bản tin, Sàng
 * lọc, Soạn lại) chạy theo hồ sơ chuẩn — nhiệt độ / ngữ cảnh cố định, không chỉnh được ở hộp gán model, và "bộ quy tắc"
 * không có tác dụng với vai nào ⇒ chỉ agent trực việc (`agent:*`) mới có nhiệt độ + ngữ cảnh do Sếp đặt.
 */
export function bindingParamsText(slot: SlotView & Pick<AgentBindingSlot, 'label'> & Partial<Pick<AgentBindingSlot, 'agent_key'>>): string {
  const b = slot.binding;
  const isAgent = (slot.agent_key ?? '').startsWith('agent:');
  const t = isAgent && b ? b.temperature : slot.standard?.temperature;
  const c = isAgent && b ? b.context_tokens : slot.standard?.context_tokens;
  if (!isAgent && (t == null || c == null)) return `${slot.label} — nhiệt độ và ngữ cảnh theo hồ sơ chuẩn (cố định)`;
  const tail = isAgent ? (b ? '' : ' (chuẩn)') : ' (theo hồ sơ chuẩn, cố định)';
  return `${slot.label} — nhiệt độ ${t == null ? '—' : fmtTemperature(t)} · ngữ cảnh ${c == null ? '—' : fmtContextTokens(c)}${tail}`;
}

/** Ngữ cảnh mặc định của agent trực việc (`DEFAULT_CONTEXT_TOKENS` của máy chủ) — ô nhập lấy đúng số này. */
export const DEFAULT_CONTEXT_TOKENS = 6000;

/** Ô nhập hạn mức → số nguyên dương hoặc null (bỏ hạn mức); chữ không hợp lệ ⇒ undefined. */
export function parseQuotaInput(v: string): number | null | undefined {
  const t = v.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isInteger(n) && n >= 1 ? n : undefined;
}

/** Model + mức đang dùng của nguồn ("Dùng model này"), không có thì model đã lưu đầu tiên. */
export function currentChoice(p: Pick<Provider, 'models'>): { model: string; effort: Effort | null } | null {
  const m = p.models.find((x) => x.is_default) ?? p.models[0];
  return m ? { model: m.model_name, effort: m.effort ?? null } : null;
}

/** "gemini-3.1-pro · Cao" — tên model kèm mức suy nghĩ (nếu có). */
export function choiceText(model: string | null | undefined, effort: Effort | null | undefined): string {
  if (!model) return '';
  return effort ? `${model} · ${EFFORT_LABEL[effort] ?? effort}` : model;
}

/** Mức mặc định khi chọn một model: mức đã lưu (nếu model nhận), mức CLI đang dùng, "Vừa", rồi mức đầu tiên. */
export function pickEffort(m: Pick<ModelOption, 'efforts' | 'default_effort'> | undefined, saved?: Effort | null): Effort | null {
  const effs = m?.efforts ?? [];
  if (!effs.length) return null;
  if (saved && effs.includes(saved)) return saved;
  if (m?.default_effort && effs.includes(m.default_effort)) return m.default_effort;
  return effs.includes('medium') ? 'medium' : effs[0];
}

/** "Gọi thử OK · 4,63s · gemini-3.8-flash · Cao · lúc 10:21 01/10" — luôn kèm giờ của lần gọi thật. */
export function testOkText(t: Pick<ProviderTestResult, 'latency_ms' | 'probe_model' | 'probe_effort' | 'at'>, fmtLatency: (ms: number) => string): string {
  const parts = ['Gọi thử OK'];
  if (t.latency_ms != null) parts.push(fmtLatency(t.latency_ms));
  if (t.probe_model) parts.push(choiceText(t.probe_model, t.probe_effort ?? null));
  parts.push(t.at ? `lúc ${fmtDMClock(t.at)}` : 'chưa rõ giờ — bấm Kiểm tra để gọi lại');
  return parts.join(' · ');
}

/** Lỗi gốc (đã che) máy chủ gửi kèm 422 khi CLI không nhận model — cho "Chi tiết kỹ thuật". */
export function technicalDetail(e: unknown): string | null {
  if (!(e instanceof ApiError)) return null;
  const t = (e.problem as { technical?: unknown }).technical;
  return typeof t === 'string' && t.trim() ? t : null;
}

/** v0.1.32 — chẩn đoán CLI: văn bản để Boss chép gửi. */
export function diagnosisText(d: ProviderDiagnosis): string {
  const head = `Chẩn đoán ${d.provider} · ${fmtDMClock(d.at)} · model ${choiceText(d.model, d.effort) || '—'}`;
  const steps = d.steps.map((s) =>
    [
      `## ${s.label} — $ ${s.command}`,
      `mã thoát: ${s.exit_code ?? '—'} · ${s.ms} ms${s.note ? ` · ${s.note}` : ''}`,
      s.stdout ? `stdout:\n${s.stdout.trimEnd()}` : 'stdout: (trống)',
      s.stderr ? `stderr:\n${s.stderr.trimEnd()}` : 'stderr: (trống)',
    ].join('\n'),
  );
  return [head, ...steps].join('\n\n');
}
