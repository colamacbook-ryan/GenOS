/** Rule editor draft ⇄ API body, and its validation. Pure (unit-tested). */
import type { Rule, RuleBody, RuleCondition, RuleOutput } from '@gen-harness/contracts';

export interface ConditionDraft {
  type: RuleCondition['type'];
  /** values (comma separated) / pattern / entity / n / hint, depending on type. */
  arg: string;
  label: string;
  /** Fields the editor does not know (e.g. `no_entity`) — kept as they were. */
  extra?: Record<string, unknown>;
}

export type OutputAction = 'set' | 'add' | 'discard' | 'alert';

export interface OutputDraft {
  action: OutputAction;
  /** set/add: field · alert: priority. */
  field: string;
  value: string;
  label: string;
  extra?: Record<string, unknown>;
}

export interface RuleDraft {
  name: string;
  kind: string;
  threshold: string;
  prompt_hint: string;
  conditions: ConditionDraft[];
  outputs: OutputDraft[];
}

const splitList = (s: string) =>
  s
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean);

const COND_KEYS = new Set(['type', 'values', 'pattern', 'entity', 'n', 'hint', 'label']);
const OUT_KEYS = new Set(['set', 'add', 'discard', 'alert', 'priority', 'value', 'label']);

function extras(o: object, known: Set<string>): Record<string, unknown> | undefined {
  const rest = Object.fromEntries(Object.entries(o).filter(([k]) => !known.has(k)));
  return Object.keys(rest).length ? rest : undefined;
}

export function conditionToDraft(c: RuleCondition): ConditionDraft {
  let arg = '';
  switch (c.type) {
    case 'keyword_any':
    case 'keyword_all':
    case 'kind_in':
      arg = (c.values ?? []).join(', ');
      break;
    case 'regex':
      arg = c.pattern ?? '';
      break;
    case 'has_entity':
      arg = c.entity ?? '';
      break;
    case 'min_words':
    case 'max_words':
    case 'repeat_unanswered':
      arg = c.n !== undefined ? String(c.n) : '';
      break;
    case 'llm':
      arg = c.hint ?? '';
      break;
    default:
      arg = '';
  }
  return { type: c.type, arg, label: c.label ?? '', extra: extras(c, COND_KEYS) };
}

export function draftToCondition(d: ConditionDraft): RuleCondition {
  return { ...(d.extra ?? {}), ...conditionCore(d) } as RuleCondition;
}

function conditionCore(d: ConditionDraft): RuleCondition {
  const label = d.label.trim() || undefined;
  switch (d.type) {
    case 'keyword_any':
    case 'keyword_all':
    case 'kind_in':
      return { type: d.type, values: splitList(d.arg), label };
    case 'regex':
      return { type: d.type, pattern: d.arg.trim(), label };
    case 'has_entity':
      return { type: d.type, entity: d.arg.trim(), label };
    case 'min_words':
    case 'max_words':
    case 'repeat_unanswered':
      return { type: d.type, n: Number(d.arg), label };
    case 'llm':
      return { type: d.type, hint: d.arg.trim(), label };
    default:
      return { type: d.type, label };
  }
}

export function outputToDraft(o: RuleOutput): OutputDraft {
  const extra = extras(o, OUT_KEYS);
  const label = o.label ?? '';
  if (o.set) return { action: 'set', field: o.set, value: String(o.value ?? ''), label, extra };
  if (o.add) return { action: 'add', field: o.add, value: String(o.value ?? ''), label, extra };
  if (o.discard) return { action: 'discard', field: '', value: '', label, extra };
  if (o.alert) {
    const p = typeof o.alert === 'string' ? o.alert : (o.priority ?? 'P1');
    return { action: 'alert', field: p, value: '', label, extra };
  }
  return { action: 'set', field: 'intent', value: '', label, extra };
}

/** Encoding as the API's presets use it: `{set,value}`, `{add,value}`, `{discard:true}`, `{alert:"P1"}`. */
export function draftToOutput(d: OutputDraft): RuleOutput {
  const label = d.label.trim() || undefined;
  const base = d.extra ?? {};
  switch (d.action) {
    case 'set':
      return { ...base, set: d.field, value: d.value.trim(), label };
    case 'add':
      return { ...base, add: d.field, value: Number(d.value), label };
    case 'discard':
      return { ...base, discard: true, label };
    case 'alert':
      return { ...base, alert: d.field || 'P1', label };
  }
}

export function emptyDraft(): RuleDraft {
  return {
    name: '',
    kind: 'intent',
    threshold: '0.7',
    prompt_hint: '',
    conditions: [{ type: 'keyword_any', arg: '', label: '' }],
    outputs: [{ action: 'set', field: 'intent', value: '', label: '' }],
  };
}

export function ruleToDraft(r: Pick<Rule, 'name' | 'kind' | 'threshold' | 'prompt_hint' | 'conditions' | 'outputs'>): RuleDraft {
  return {
    name: r.name,
    kind: r.kind,
    threshold: String(r.threshold),
    prompt_hint: r.prompt_hint ?? '',
    conditions: r.conditions.map(conditionToDraft),
    outputs: r.outputs.map(outputToDraft),
  };
}

export function draftToBody(d: RuleDraft): RuleBody {
  return {
    name: d.name.trim(),
    kind: d.kind,
    threshold: Number(d.threshold.replace(',', '.')),
    prompt_hint: d.prompt_hint.trim() || null,
    conditions: d.conditions.map(draftToCondition),
    outputs: d.outputs.map(draftToOutput),
  };
}

export interface RuleDraftErrors {
  name?: string;
  threshold?: string;
  conditions?: string;
  outputs?: string;
  conditionRows: Record<number, string>;
  outputRows: Record<number, string>;
}

const needsList = new Set(['keyword_any', 'keyword_all', 'kind_in']);
const needsN = new Set(['min_words', 'max_words', 'repeat_unanswered']);

export function validateRuleDraft(d: RuleDraft): RuleDraftErrors {
  const e: RuleDraftErrors = { conditionRows: {}, outputRows: {} };
  if (!d.name.trim()) e.name = 'Đặt tên cho quy tắc.';
  const t = Number(d.threshold.replace(',', '.'));
  if (d.threshold.trim() === '' || Number.isNaN(t) || t < 0 || t > 1) e.threshold = 'Ngưỡng tin cậy từ 0 đến 1.';
  if (!d.conditions.length) e.conditions = 'Cần ít nhất một điều kiện.';
  if (!d.outputs.length) e.outputs = 'Cần ít nhất một kết quả.';
  d.conditions.forEach((c, i) => {
    if (needsList.has(c.type) && !splitList(c.arg).length) e.conditionRows[i] = 'Nhập ít nhất một giá trị, cách nhau bằng dấu phẩy.';
    else if (c.type === 'regex') {
      if (!c.arg.trim()) e.conditionRows[i] = 'Nhập biểu thức.';
      else {
        try {
          new RegExp(c.arg);
        } catch {
          e.conditionRows[i] = 'Biểu thức không hợp lệ.';
        }
      }
    } else if (c.type === 'has_entity' && !c.arg.trim()) e.conditionRows[i] = 'Chọn loại thực thể.';
    else if (needsN.has(c.type) && (!/^\d+$/.test(c.arg.trim()) || Number(c.arg) < 1)) e.conditionRows[i] = 'Nhập một số nguyên dương.';
    else if (c.type === 'llm' && !c.arg.trim()) e.conditionRows[i] = 'Mô tả điều LLM cần xét.';
  });
  d.outputs.forEach((o, i) => {
    if (o.action === 'set' && !o.value.trim()) e.outputRows[i] = 'Nhập giá trị.';
    if (o.action === 'add' && (o.value.trim() === '' || Number.isNaN(Number(o.value)))) e.outputRows[i] = 'Nhập một số (có thể âm).';
  });
  return e;
}

export function draftValid(e: RuleDraftErrors): boolean {
  return !e.name && !e.threshold && !e.conditions && !e.outputs && !Object.keys(e.conditionRows).length && !Object.keys(e.outputRows).length;
}
