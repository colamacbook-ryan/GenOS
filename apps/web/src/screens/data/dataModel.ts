/**
 * Pure presentation logic for the data-layer screens, ported from the
 * design's renderVals() (rawRows, triggerConfig, refineryRules, cleanRows,
 * idPairs…). Colours are token variables, never raw values.
 */
import type {
  CleanItem,
  IdentityStats,
  Notebook,
  NotebookSection,
  NotebookSubjectType,
  Pipeline,
  RawState,
  RefineryRun,
  RefinerySchedule,
  Rule,
  RuleCondition,
  RuleOutput,
  Weight,
} from '@gen-harness/contracts';
import { fmtAgo, fmtCountdown, fmtDM, fmtDec, fmtHM, fmtInt, fmtInterval, minutesValue } from '../../lib/format';

export const OK = 'var(--color-ok)';
export const WARN = 'var(--color-warn)';
export const BAD = 'var(--color-bad)';
export const ACC3 = 'var(--color-accent-300)';
export const ACC4 = 'var(--color-accent-400)';
export const ACC8 = 'var(--color-accent-800)';
export const TXT = 'var(--color-text)';
export const N3 = 'var(--color-neutral-300)';
export const N4 = 'var(--color-neutral-400)';
export const N5 = 'var(--color-neutral-500)';
export const N8 = 'var(--color-neutral-800)';

// ── raw state ─────────────────────────────────────────────────────────────
export const RAW_STATE_LABEL: Record<RawState, string> = {
  pending: 'Chờ chu kỳ tới',
  processing: 'Đang phân loại',
  clean: 'Đã vào kho sạch',
  lowconf: 'Tin cậy thấp',
  discarded: 'Loại — nhiễu',
  error: 'Lỗi xử lý',
};

const RAW_STATE_TONE: Record<RawState, string> = {
  pending: N4,
  processing: ACC4,
  clean: OK,
  lowconf: WARN,
  discarded: N5,
  error: BAD,
};

export function rawStateLabel(s: RawState | string): string {
  return RAW_STATE_LABEL[s as RawState] ?? s;
}

/** Chip colour + border (neutral tones get the neutral-800 border, as in the design). */
export function rawStateStyle(s: RawState | string): { color: string; border: string } {
  const tone = RAW_STATE_TONE[s as RawState] ?? N4;
  return { color: tone, border: tone === N4 || tone === N5 ? N8 : tone };
}

/** Confidence colour: ≥ 0,80 OK · ≥ 0,60 WARN · else BAD. */
export function confidenceTone(c: number | null | undefined): string {
  if (c === null || c === undefined) return N5;
  return c >= 0.8 ? OK : c >= 0.6 ? WARN : BAD;
}

export const fmtConfidence = (c: number | null | undefined) => (c === null || c === undefined ? '—' : fmtDec(c, 2));

export function channelIcon(type: string): string {
  switch (type) {
    case 'zalo':
      return 'ph ph-chat-circle-dots';
    case 'whatsapp':
      return 'ph ph-device-mobile';
    case 'telegram':
      return 'ph ph-paper-plane-tilt';
    case 'linkedin':
      return 'ph ph-linkedin-logo';
    default:
      return 'ph ph-chat-circle-dots';
  }
}

export function channelTone(type: string): string {
  return type === 'zalo' ? OK : type === 'whatsapp' ? WARN : N4;
}

// ── pipeline strip ────────────────────────────────────────────────────────
export interface PipelineCard {
  step: number;
  name: string;
  icon: string;
  value: string;
  unit: string;
  note: string;
  tone: string;
  on: boolean;
  last: boolean;
}

export type DataScreen = 'raw' | 'rules' | 'clean';

export function pipelineCards(p: Pipeline, screen: DataScreen): PipelineCard[] {
  const rawError = p.raw_error ?? 0; // máy chủ cũ chưa trả raw_error ⇒ coi như 0
  return [
    {
      step: 1,
      name: 'Bridge lắng nghe',
      icon: 'ph ph-broadcast',
      value: fmtInt(p.channels_live),
      unit: `kênh · ${fmtInt(p.groups_listening)} nhóm`,
      note: 'Gom nguyên trạng, không xử lý, không lọc ở bước này.',
      tone: OK,
      on: screen === 'raw',
      last: false,
    },
    {
      step: 2,
      name: 'Kho thô',
      icon: 'ph ph-database',
      value: fmtInt(p.raw_total),
      unit: 'bản ghi',
      note:
        `${fmtInt(p.raw_pending)} bản ghi chưa phân loại đang chờ chu kỳ tới.` +
        (rawError > 0 ? ` ${fmtInt(rawError)} bản ghi lỗi xử lý — hệ thống tự thử lại sau ít phút.` : ''),
      tone: rawError > 0 ? BAD : WARN,
      on: screen === 'raw',
      last: false,
    },
    {
      step: 3,
      name: 'Trợ lý sàng lọc',
      icon: 'ph ph-funnel',
      value: minutesValue(p.interval_seconds),
      unit: 'phút / chu kỳ',
      note: `Hoặc chạy ngay khi kho thô vượt ${fmtInt(p.count_threshold)} bản ghi.`,
      tone: ACC4,
      on: screen === 'rules',
      last: false,
    },
    {
      step: 4,
      name: 'Kho sạch',
      icon: 'ph ph-check-circle',
      value: fmtInt(p.clean_total),
      unit: 'bản ghi',
      note: 'Đã gắn ID nhóm, ID người, nhãn và điểm số.',
      tone: OK,
      on: screen === 'clean',
      last: true,
    },
  ];
}

// ── refinery trigger config (Kích hoạt sàng lọc) ──────────────────────────
export interface TriggerRow {
  label: string;
  value: string;
  /** 0–100 */
  bar: number;
  tone: string;
  note: string;
}

const clampPct = (n: number) => Math.max(0, Math.min(100, Math.round(n)));

export function triggerRows(s: RefinerySchedule, now = Date.now()): TriggerRow[] {
  const nextIn = new Date(s.next_run_at).getTime() - now;
  const byCount = s.next_trigger === 'count';
  return [
    {
      label: 'Chu kỳ thời gian',
      value: `mỗi ${fmtInterval(s.interval_seconds)}`,
      bar: clampPct((s.interval_seconds / 3600) * 100),
      tone: ACC4,
      note: Number.isNaN(nextIn) ? 'Chưa có lịch chạy' : nextIn <= 0 ? 'Chu kỳ tới đang bắt đầu' : `Chu kỳ tới chạy sau ${fmtCountdown(nextIn)}`,
    },
    {
      label: 'Ngưỡng số lượng',
      value: `${fmtInt(s.count_threshold)} bản ghi`,
      bar: clampPct((s.pending / Math.max(1, s.count_threshold)) * 100),
      tone: WARN,
      note: byCount
        ? `Đang có ${fmtInt(s.pending)} chờ — sẽ chạy theo ngưỡng trước`
        : `Đang có ${fmtInt(s.pending)} chờ — chu kỳ sẽ chạy trước`,
    },
    {
      label: 'Số bản ghi mỗi lượt',
      value: `${fmtInt(s.batch_size)} / lượt`,
      bar: clampPct((s.batch_size / Math.max(1, s.count_threshold)) * 100),
      tone: OK,
      note: 'Giới hạn để không cạn hạn mức model',
    },
    {
      label: 'Ngưỡng tin cậy vào kho sạch',
      value: `≥ ${fmtDec(s.min_confidence, 2)}`,
      bar: clampPct(s.min_confidence * 100),
      tone: OK,
      note: 'Dưới ngưỡng thì giữ lại chờ Sếp xem',
    },
  ];
}

export function validateSchedule(v: { interval_seconds: number; count_threshold: number; batch_size: number; min_confidence: number }) {
  const errors: Partial<Record<keyof typeof v, string>> = {};
  if (!Number.isInteger(v.interval_seconds) || v.interval_seconds < 60 || v.interval_seconds > 86400)
    errors.interval_seconds = 'Chu kỳ từ 1 phút đến 24 giờ.';
  if (!Number.isInteger(v.count_threshold) || v.count_threshold < 1 || v.count_threshold > 100000)
    errors.count_threshold = 'Ngưỡng từ 1 đến 100.000 bản ghi.';
  if (!Number.isInteger(v.batch_size) || v.batch_size < 1 || v.batch_size > 2000) errors.batch_size = 'Mỗi lượt từ 1 đến 2.000 bản ghi.';
  if (Number.isNaN(v.min_confidence) || v.min_confidence < 0 || v.min_confidence > 1)
    errors.min_confidence = 'Ngưỡng tin cậy từ 0 đến 1.';
  return errors;
}

// ── refinery runs (Chu kỳ gần nhất) ───────────────────────────────────────
export function runLine(r: RefineryRun): { meta: string; tone: string } {
  const n = `${fmtInt(r.input_count)} bản ghi`;
  if (r.status === 'running' || r.status === 'queued') return { meta: `${n} · ${r.status === 'queued' ? 'đang chờ' : 'đang chạy'}`, tone: ACC4 };
  if (r.status === 'failed') return { meta: `${n} · lỗi`, tone: BAD };
  if (r.error_count > 0) return { meta: `${n} · ${fmtInt(r.error_count)} lỗi`, tone: BAD };
  if (r.lowconf_count > 0) return { meta: `${n} · ${fmtInt(r.lowconf_count)} tin cậy thấp`, tone: WARN };
  if (r.trigger === 'threshold') return { meta: `${n} · theo ngưỡng`, tone: OK };
  if (r.trigger === 'manual') return { meta: `${n} · chạy tay`, tone: OK };
  if (r.trigger === 'fast') return { meta: `${n} · đường nhanh`, tone: OK };
  return { meta: `${n} · 0 lỗi`, tone: OK };
}

// ── rules ─────────────────────────────────────────────────────────────────
/** Kind chip colour: Rủi ro BAD · Cạnh tranh / Nhân sự WARN · else neutral. */
export function ruleKindTone(kind: string): string {
  return kind === 'risk' ? BAD : kind === 'competition' || kind === 'hr' ? WARN : N4;
}

/** Threshold bar colour by kind (design: intent OK, risk BAD, competition/hr WARN, hygiene neutral-500). */
export function ruleTone(kind: string): string {
  switch (kind) {
    case 'intent':
      return OK;
    case 'risk':
      return BAD;
    case 'competition':
    case 'hr':
      return WARN;
    case 'hygiene':
      return N5;
    default:
      return ACC4;
  }
}

export const RULE_KINDS: Array<{ value: string; label: string }> = [
  { value: 'intent', label: 'Ý định' },
  { value: 'risk', label: 'Rủi ro' },
  { value: 'competition', label: 'Cạnh tranh' },
  { value: 'hr', label: 'Nhân sự' },
  { value: 'hygiene', label: 'Vệ sinh' },
  { value: 'custom', label: 'Tuỳ chỉnh' },
];

export const CONDITION_TYPES: Array<{ value: RuleCondition['type']; label: string }> = [
  { value: 'keyword_any', label: 'có một trong các từ' },
  { value: 'keyword_all', label: 'có đủ các từ' },
  { value: 'regex', label: 'khớp biểu thức' },
  { value: 'has_entity', label: 'có thực thể' },
  { value: 'min_words', label: 'ít nhất n từ' },
  { value: 'max_words', label: 'nhiều nhất n từ' },
  { value: 'is_question', label: 'là câu hỏi' },
  { value: 'kind_in', label: 'loại tin thuộc' },
  { value: 'repeat_unanswered', label: 'nhắc lại chưa trả lời ≥ n lần' },
  { value: 'llm', label: 'để LLM xét' },
];

export const ENTITIES = ['qty', 'price', 'budget', 'phone', 'product', 'date'];

const ENTITY_LABEL: Record<string, string> = {
  qty: 'số lượng',
  price: 'mức giá',
  budget: 'ngân sách',
  phone: 'số điện thoại',
  product: 'mặt hàng',
  date: 'ngày',
};

/** Card text for a condition: its human label, else a readable rendering. */
export function conditionLabel(c: RuleCondition): string {
  if (c.label) return c.label;
  const q = (v: string[] | undefined) => (v ?? []).map((x) => `"${x}"`).join(', ');
  switch (c.type) {
    case 'keyword_any':
      return `có từ ${q(c.values)}`;
    case 'keyword_all':
      return `có đủ ${q(c.values)}`;
    case 'regex':
      return `khớp /${c.pattern ?? ''}/`;
    case 'has_entity':
      return `có ${ENTITY_LABEL[c.entity ?? ''] ?? c.entity ?? 'thực thể'}`;
    case 'min_words':
      return `≥ ${c.n ?? 0} từ`;
    case 'max_words':
      return `dưới ${c.n ?? 0} từ`;
    case 'is_question':
      return 'câu hỏi trực tiếp';
    case 'kind_in':
      return (c.values ?? []).join(', ');
    case 'repeat_unanswered':
      return `nhắc lại ≥ ${c.n ?? 2} lần chưa được trả lời`;
    case 'llm':
      return c.hint ? `LLM: ${c.hint}` : 'để LLM xét';
    default:
      return String((c as { type: string }).type);
  }
}

export const OUTPUT_FIELDS: Record<'set' | 'add', string[]> = {
  set: ['intent', 'side', 'label', 'person_type'],
  add: ['heat', 'potential', 'churn_risk', 'fit'],
};

export function outputLabel(o: RuleOutput): string {
  if (o.label) return o.label;
  if (o.set) return `${o.set} = ${o.value ?? ''}`;
  if (o.add) return `${o.add} += ${o.value ?? ''}`;
  if (o.discard) return 'không ghi vào kho sạch';
  if (o.alert) return `đẩy cảnh báo ${o.priority ?? (typeof o.alert === 'string' ? o.alert : '')}`.trim();
  return '—';
}

export const hitsLabel = (r: Pick<Rule, 'hits_24h'>) => `${fmtInt(r.hits_24h)} lượt / 24h`;

// ── weights ───────────────────────────────────────────────────────────────
export function weightsSum(ws: Array<Pick<Weight, 'value'>>): number {
  return ws.reduce((n, w) => n + (Number.isFinite(w.value) ? w.value : 0), 0);
}

/** Integer percents, each 0–100, summing to exactly 100. */
export function weightsValid(ws: Array<Pick<Weight, 'value'>>): boolean {
  return ws.length > 0 && ws.every((w) => Number.isInteger(w.value) && w.value >= 0 && w.value <= 100) && weightsSum(ws) === 100;
}

export function weightsMessage(ws: Array<Pick<Weight, 'value'>>): string | null {
  if (weightsValid(ws)) return null;
  const s = weightsSum(ws);
  return `Tổng trọng số phải bằng 100% — hiện ${s}% (${s > 100 ? `thừa ${s - 100}` : `thiếu ${100 - s}`}%).`;
}

// ── rule test output tones ────────────────────────────────────────────────
export function testOutputTone(key: string, wouldWrite: 'clean' | 'lowconf' | 'discarded'): string {
  if (key === 'scores' || key === 'confidence') return wouldWrite === 'clean' ? OK : wouldWrite === 'lowconf' ? WARN : N5;
  if (key === 'actions') return ACC3;
  if (key === 'group_id' || key === 'entities') return N3;
  return TXT;
}

// ── clean ─────────────────────────────────────────────────────────────────
/**
 * Event chip / score colour (design cleanRows): WentSilent always BAD,
 * Complained BAD from 75, else OK ≥ 70 · WARN ≥ 50 · neutral.
 */
export function cleanTone(eventType: string, score: number): string {
  if (eventType === 'WentSilent') return BAD;
  if (eventType === 'Complained') return score >= 75 ? BAD : WARN;
  return score >= 70 ? OK : score >= 50 ? WARN : N4;
}

// ── identity ──────────────────────────────────────────────────────────────
export function levelTone(level: string): string {
  return level === 'high' ? OK : level === 'mid' ? WARN : BAD;
}

export const confidencePct = (c: number) => `${Math.round(c * 100)}%`;

export function channelName(type: string): string {
  switch (type) {
    case 'zalo':
      return 'Zalo';
    case 'whatsapp':
      return 'WhatsApp';
    case 'telegram':
      return 'Telegram';
    case 'linkedin':
      return 'LinkedIn';
    default:
      return type;
  }
}

// ── clean: memory panel & agent params ────────────────────────────────────
export const SECTION_TONE: Record<string, string> = {
  attention_now: BAD,
  rolling_context: ACC4,
  guardrails: WARN,
  preferences: ACC3,
  open_threads: N4,
};

export const sectionTone = (key: string) => SECTION_TONE[key] ?? N4;

export function subjectOf(row: CleanItem | null): { type: NotebookSubjectType; id: string } | null {
  if (!row) return null;
  if (row.person) return { type: 'person', id: row.person.id };
  if (row.group) return { type: 'group', id: row.group.id };
  return null;
}

/** Right-aligned note on a memory section head (design: "cập nhật 2 phút trước", "nén lần 14 · 15:00", "Sếp ghim · 12/09"). */
export function sectionAge(s: NotebookSection, nb: Notebook, tz: string, now = Date.now()): string {
  if (s.key === 'rolling_context' && nb.compaction_no > 0)
    return `nén lần ${nb.compaction_no}${nb.last_compacted_at ? ` · ${fmtHM(nb.last_compacted_at, tz)}` : ''}`;
  const pinned = s.entries.filter((e) => e.pinned && e.author.type === 'user');
  if (s.key === 'guardrails' && pinned.length) {
    const latest = pinned.map((e) => e.created_at).sort().at(-1)!;
    return `Sếp ghim · ${fmtDM(latest, tz)}`;
  }
  return s.updated_at ? `cập nhật ${fmtAgo(s.updated_at, now, tz)}` : '';
}

export function paramTone(icon: string): string {
  if (/users-three|\buser\b|ph-user$|brain|gauge/.test(icon)) return ACC3;
  if (/check-circle/.test(icon)) return OK;
  return N4;
}


/** Four identity stat cards (design idStats). */
export function idStatCards(s: IdentityStats) {
  return [
    { label: 'Danh tính đã hợp nhất', value: s.merged_people, unit: 'người', sub: `trên ${fmtInt(s.live_profiles)} hồ sơ đang sống`, tone: OK },
    { label: 'Chờ Sếp xác nhận', value: s.pending_pairs, unit: 'cặp', sub: 'hệ thống không tự gộp', tone: WARN },
    { label: 'Đã tách tay', value: s.manual_splits, unit: 'lần', sub: 'giữ nguyên lịch sử thao tác', tone: N4 },
    { label: 'Tài khoản chưa gắn người', value: s.unlinked_accounts, unit: 'tài khoản', sub: 'phần lớn là người lạ trong group ngành', tone: WARN },
  ];
}

