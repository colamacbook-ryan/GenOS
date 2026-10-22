/**
 * v0.1.55 (G3) — Sếp chọn model / mức suy nghĩ ngay trong khung chat Gen: Tự động (chuẩn) · Nhanh · Kỹ hơn (+ Cân bằng,
 * mặc định ẩn) và Mức suy nghĩ Thấp / Vừa / Cao (chỉ khi tầng đó hỗ trợ).
 *
 * Tệp THUẦN (không React, không store): chuẩn hoá lựa chọn, đối chiếu với `model_options` của máy chủ, và nhớ lựa chọn THEO
 * MÃ HỘI THOẠI trong localStorage khoá `gh-gen-model-choice`. Hội thoại mới = Tự động; lựa chọn của hội thoại này không rò sang
 * hội thoại khác. Mọi đọc/ghi localStorage bọc try/catch — bị chặn hoặc ném lỗi thì chuyển sang bộ nhớ tạm của tab (khung Gen
 * vẫn chạy, chỉ không nhớ qua tải lại trang).
 */
import type { GenModelEffort, GenModelOption, GenModelOptions, GenModelTier, ModelChoice } from '@gen-harness/contracts';

export const MODEL_CHOICE_KEY = 'gh-gen-model-choice';
/** Số hội thoại tối đa được nhớ lựa chọn (cũ nhất bị bỏ) — localStorage không phình mãi. */
export const MODEL_CHOICE_MAX = 50;

export const AUTO_CHOICE: ModelChoice = { tier: 'auto' };

/** Nhãn hiển thị (đúng thuật ngữ: Tự động (chuẩn) · Nhanh · Kỹ hơn; Cân bằng mặc định ẩn). */
export const TIER_LABEL: Record<GenModelTier, string> = { auto: 'Tự động (chuẩn)', fast: 'Nhanh', balanced: 'Cân bằng', deep: 'Kỹ hơn' };
export const EFFORT_LABEL: Record<GenModelEffort, string> = { low: 'Thấp', medium: 'Vừa', high: 'Cao' };

const TIERS: readonly GenModelTier[] = ['auto', 'fast', 'balanced', 'deep'];
const EFFORTS: readonly GenModelEffort[] = ['low', 'medium', 'high'];

export function isTier(v: unknown): v is GenModelTier {
  return typeof v === 'string' && (TIERS as readonly string[]).includes(v);
}

export function isEffort(v: unknown): v is GenModelEffort {
  return typeof v === 'string' && (EFFORTS as readonly string[]).includes(v);
}

/** Lựa chọn từ nguồn không tin cậy (localStorage, máy chủ cũ) → `ModelChoice` hợp lệ; không hiểu ⇒ Tự động. Không ném lỗi. */
export function normalizeChoice(raw: unknown): ModelChoice {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return AUTO_CHOICE;
  const o = raw as { tier?: unknown; effort?: unknown };
  if (!isTier(o.tier) || o.tier === 'auto') return AUTO_CHOICE;
  return isEffort(o.effort) ? { tier: o.tier, effort: o.effort } : { tier: o.tier };
}

export function isAuto(c: ModelChoice | null | undefined): boolean {
  return !c || c.tier === 'auto';
}

export function sameChoice(a: ModelChoice, b: ModelChoice): boolean {
  return a.tier === b.tier && (a.effort ?? null) === (b.effort ?? null);
}

/** Phần gửi lên `POST /gen/turns` (`model_choice`): Tự động ⇒ `undefined` (bỏ hẳn trường). */
export function toBody(c: ModelChoice | null | undefined): ModelChoice | undefined {
  const n = normalizeChoice(c);
  return n.tier === 'auto' ? undefined : n;
}

// ─── đối chiếu với `model_options` ───────────────────────────────────────────────

export function optionOf(options: GenModelOptions | null | undefined, tier: GenModelTier): GenModelOption | undefined {
  const rows = options && Array.isArray(options.tiers) ? options.tiers : [];
  return rows.find((r) => r && r.tier === tier);
}

/**
 * Tầng có dùng được không. Chưa biết (máy chủ cũ không gửi `model_options`, đang tải, lỗi) ⇒ CHO PHÉP: máy chủ mới là nơi
 * quyết định và sẽ hạ về Tự động kèm một dòng giải thích.
 */
export function tierAvailable(options: GenModelOptions | null | undefined, tier: GenModelTier): boolean {
  if (tier === 'auto') return true;
  if (!options || !Array.isArray(options.tiers) || options.tiers.length === 0) return true;
  return optionOf(options, tier)?.available === true;
}

/** Mức suy nghĩ mời chọn cho tầng: chỉ những mức máy chủ báo hỗ trợ; chưa biết ⇒ rỗng (ẩn hàng "Mức suy nghĩ"). */
export function effortsOf(options: GenModelOptions | null | undefined, tier: GenModelTier): GenModelEffort[] {
  if (tier === 'auto') return [];
  const raw = optionOf(options, tier)?.efforts;
  return Array.isArray(raw) ? EFFORTS.filter((e) => raw.includes(e)) : [];
}

/** Các tầng hiện nút: Tự động · Nhanh · Kỹ hơn; "Cân bằng" mặc định ẩn — chỉ hiện khi đang được chọn. */
export function visibleTiers(choice: ModelChoice): GenModelTier[] {
  return choice.tier === 'balanced' ? ['auto', 'fast', 'balanced', 'deep'] : ['auto', 'fast', 'deep'];
}

/** Chữ tooltip khi một tầng bị khoá (nút `disabled` + `title`). */
export function unavailableReason(tier: GenModelTier, isOwner: boolean): string {
  const label = TIER_LABEL[tier];
  return isOwner
    ? `Mức “${label}” chưa dùng được: chưa có model nào ở mức này — Sếp thêm nguồn ở màn API & Model, hoặc mở cuộc trò chuyện mới nếu cuộc này đã đọc nội dung từ bên ngoài.`
    : `Mức “${label}” chưa dùng được cho tài khoản này: nguồn AI phù hợp chỉ dành cho Sếp hoặc chưa được cấu hình.`;
}

/** Đổi tầng: giữ mức suy nghĩ cũ chỉ khi tầng mới hỗ trợ nó. */
export function withTier(choice: ModelChoice, tier: GenModelTier, options: GenModelOptions | null | undefined): ModelChoice {
  if (tier === 'auto') return AUTO_CHOICE;
  const keep = choice.effort && effortsOf(options, tier).includes(choice.effort) ? choice.effort : undefined;
  return keep ? { tier, effort: keep } : { tier };
}

/** Đổi mức suy nghĩ (`null` = bỏ chọn, để máy chủ dùng mức chuẩn). Tự động không có mức suy nghĩ. */
export function withEffort(choice: ModelChoice, effort: GenModelEffort | null): ModelChoice {
  if (choice.tier === 'auto') return AUTO_CHOICE;
  return effort ? { tier: choice.tier, effort } : { tier: choice.tier };
}

// ─── nhớ theo mã hội thoại ─────────────────────────────────────────────────────

/** Bộ nhớ tạm của tab khi localStorage bị chặn / ném lỗi (cũng là bản sao nhanh của lần ghi gần nhất). */
const memory = new Map<string, ModelChoice>();

/** Chỉ cho test: quên bộ nhớ tạm (giả lập tải lại trang). */
export function resetModelChoiceMemory(): void {
  memory.clear();
}

function readStored(): Record<string, ModelChoice> {
  try {
    const raw = window.localStorage.getItem(MODEL_CHOICE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const out: Record<string, ModelChoice> = {};
    for (const [id, v] of Object.entries(parsed)) {
      const c = normalizeChoice(v);
      if (c.tier !== 'auto') out[id] = c;
    }
    return out;
  } catch {
    return {};
  }
}

function writeStored(map: Record<string, ModelChoice>): void {
  try {
    if (Object.keys(map).length === 0) window.localStorage.removeItem(MODEL_CHOICE_KEY);
    else window.localStorage.setItem(MODEL_CHOICE_KEY, JSON.stringify(map));
  } catch {
    /* bị chặn / đầy / hỏng: vẫn còn bộ nhớ tạm của tab */
  }
}

/** Lựa chọn đã nhớ của hội thoại; không có mã (hội thoại mới) hoặc chưa từng chọn ⇒ Tự động. */
export function loadChoice(conversationId: string | null | undefined): ModelChoice {
  if (!conversationId) return AUTO_CHOICE;
  return memory.get(conversationId) ?? readStored()[conversationId] ?? AUTO_CHOICE;
}

/** Nhớ lựa chọn cho hội thoại. Tự động ⇒ xoá mục của hội thoại. Chưa có mã hội thoại ⇒ không làm gì (giữ trong store). */
export function saveChoice(conversationId: string | null | undefined, choice: ModelChoice): void {
  if (!conversationId) return;
  const c = normalizeChoice(choice);
  const map = readStored();
  delete map[conversationId];
  memory.delete(conversationId);
  if (c.tier !== 'auto') {
    map[conversationId] = c; // ghi lại ở cuối = mới nhất
    memory.set(conversationId, c);
  }
  const ids = Object.keys(map);
  for (const old of ids.slice(0, Math.max(0, ids.length - MODEL_CHOICE_MAX))) {
    delete map[old];
    memory.delete(old);
  }
  writeStored(map);
}

/** Bỏ nhớ lựa chọn của một hội thoại (vd khi hội thoại bị xoá). */
export function forgetChoice(conversationId: string | null | undefined): void {
  saveChoice(conversationId, AUTO_CHOICE);
}
