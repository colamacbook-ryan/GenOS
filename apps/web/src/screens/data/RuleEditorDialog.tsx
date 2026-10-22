import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import type { Rule, RuleVersion } from '@gen-harness/contracts';
import { Button, Dialog, EmptyState, IconButton, SelectField, Skeleton, TextField } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { qk2 } from '../../lib/dataQueries';
import { fmtDMClock, fmtDec } from '../../lib/format';
import { useOrgTimezone } from '../../lib/permissions';
import { queryClient } from '../../lib/queryClient';
import { toast } from '../../lib/toast';
import { errorText } from '../../lib/errorText';
import { CardError, InlineError } from '../common';
import { CONDITION_TYPES, ENTITIES, OUTPUT_FIELDS, RULE_KINDS, conditionLabel, outputLabel } from './dataModel';
import {
  draftToBody,
  draftValid,
  emptyDraft,
  ruleToDraft,
  validateRuleDraft,
  type ConditionDraft,
  type OutputDraft,
  type RuleDraft,
} from './ruleForm';

const ACTIONS = [
  { value: 'set', label: 'đặt (set)' },
  { value: 'add', label: 'cộng (add)' },
  { value: 'discard', label: 'loại bỏ' },
  { value: 'alert', label: 'cảnh báo' },
];

function actorName(v: RuleVersion['created_by']): string {
  if (!v) return '—';
  if (typeof v === 'string') return v;
  return v.label ?? v.display_name ?? v.name ?? '—';
}

/**
 * Thêm / sửa quy tắc. Saving an existing rule creates a new version (PUT) —
 * conclusions already written keep the version they were made with.
 */
export function RuleEditorDialog({
  open,
  onClose,
  rule,
  readOnly,
}: {
  open: boolean;
  onClose: () => void;
  /** null = new rule. */
  rule: Rule | null;
  readOnly?: boolean;
}) {
  const tz = useOrgTimezone();
  const [d, setD] = useState<RuleDraft>(() => (rule ? ruleToDraft(rule) : emptyDraft()));
  const [submitted, setSubmitted] = useState(false);
  useEffect(() => {
    if (open) {
      setD(rule ? ruleToDraft(rule) : emptyDraft());
      setSubmitted(false);
    }
  }, [open, rule]);

  const versions = useQuery({
    queryKey: qk2.ruleVersions(rule?.id ?? '-'),
    queryFn: ({ signal }) => api.rules.versions(rule!.id, signal),
    enabled: open && !!rule,
  });

  const errors = validateRuleDraft(d);
  const valid = draftValid(errors);
  const save = useMutation({
    mutationFn: () => (rule ? api.rules.update(rule.id, draftToBody(d)) : api.rules.create(draftToBody(d))),
    onSuccess: (saved) => {
      queryClient.setQueryData<Rule[]>(qk2.rules, (old) =>
        old ? (old.some((r) => r.id === saved.id) ? old.map((r) => (r.id === saved.id ? saved : r)) : [...old, saved]) : old,
      );
      void queryClient.invalidateQueries({ queryKey: qk2.rules });
      if (rule) void queryClient.invalidateQueries({ queryKey: qk2.ruleVersions(rule.id) });
      toast(rule ? `Đã lưu ${saved.code} · phiên bản ${saved.version}` : `Đã thêm quy tắc ${saved.code}`);
      onClose();
    },
  });

  const submit = () => {
    setSubmitted(true);
    if (valid && !readOnly) save.mutate();
  };
  const setCond = (i: number, patch: Partial<ConditionDraft>) =>
    setD((s) => ({ ...s, conditions: s.conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)) }));
  const setOut = (i: number, patch: Partial<OutputDraft>) =>
    setD((s) => ({ ...s, outputs: s.outputs.map((o, j) => (j === i ? { ...o, ...patch } : o)) }));
  const show = (msg?: string) => (submitted ? (msg ?? null) : null);

  const title = rule ? `${rule.code} · ${rule.name}` : 'Thêm quy tắc';
  return (
    <Dialog
      open={open}
      onClose={onClose}
      width={720}
      title={title}
      kicker={rule ? `Phiên bản ${rule.version} · sửa sẽ tạo phiên bản mới` : 'Mã R-nn tự sinh khi lưu'}
      actions={
        readOnly ? (
          <Button variant="secondary" onClick={onClose}>
            Đóng
          </Button>
        ) : (
          <>
            <Button variant="secondary" onClick={onClose}>
              Huỷ
            </Button>
            <Button variant="primary" icon="ph ph-check" loading={save.isPending} disabled={submitted && !valid} onClick={submit}>
              {rule ? 'Lưu phiên bản mới' : 'Thêm quy tắc'}
            </Button>
          </>
        )
      }
    >
      <form
        className="dlg-fields"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <fieldset disabled={readOnly} style={{ border: 'none', margin: 0, padding: 0, display: 'contents' }}>
          <div className="dlg-grid2">
            <TextField label="Tên quy tắc" value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} error={show(errors.name)} data-autofocus />
            <SelectField label="Loại" value={d.kind} options={RULE_KINDS} onChange={(e) => setD({ ...d, kind: e.target.value })} />
            <TextField
              label="Ngưỡng tin cậy tối thiểu (0–1)"
              inputMode="decimal"
              value={d.threshold}
              onChange={(e) => setD({ ...d, threshold: e.target.value })}
              error={show(errors.threshold)}
            />
          </div>

          <div className="dlg-section-title">Nếu — điều kiện</div>
          {d.conditions.map((c, i) => (
            <div key={i}>
              <div className="dlg-edit-row">
                <select
                  className="gh-input"
                  aria-label={`Loại điều kiện ${i + 1}`}
                  value={c.type}
                  onChange={(e) => setCond(i, { type: e.target.value as ConditionDraft['type'], arg: '' })}
                >
                  {CONDITION_TYPES.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
                <ConditionArg c={c} index={i} onChange={(arg) => setCond(i, { arg })} />
                <input
                  className="gh-input"
                  aria-label={`Nhãn hiển thị điều kiện ${i + 1}`}
                  placeholder="nhãn hiển thị (tuỳ chọn)"
                  value={c.label}
                  onChange={(e) => setCond(i, { label: e.target.value })}
                />
                <IconButton
                  icon="ph ph-trash"
                  label={`Xoá điều kiện ${i + 1}`}
                  variant="ghost"
                  iconSize={13}
                  tooltip={false}
                  disabled={readOnly || d.conditions.length <= 1}
                  onClick={() => setD((s) => ({ ...s, conditions: s.conditions.filter((_, j) => j !== i) }))}
                />
              </div>
              {show(errors.conditionRows[i]) ? <InlineError>{errors.conditionRows[i]}</InlineError> : null}
            </div>
          ))}
          {!readOnly ? (
            <div>
              <Button
                variant="ghost"
                icon="ph ph-plus"
                className="btn-27"
                onClick={() => setD((s) => ({ ...s, conditions: [...s.conditions, { type: 'keyword_any', arg: '', label: '' }] }))}
              >
                Thêm điều kiện
              </Button>
            </div>
          ) : null}

          <div className="dlg-section-title" style={{ color: 'var(--color-accent-300)' }}>
            Thì — kết quả
          </div>
          {d.outputs.map((o, i) => (
            <div key={i}>
              <div className="dlg-edit-row">
                <select
                  className="gh-input"
                  aria-label={`Loại kết quả ${i + 1}`}
                  value={o.action}
                  onChange={(e) => {
                    const action = e.target.value as OutputDraft['action'];
                    setOut(i, { action, field: action === 'set' ? 'intent' : action === 'add' ? 'heat' : action === 'alert' ? 'P1' : '', value: '' });
                  }}
                >
                  {ACTIONS.map((a) => (
                    <option key={a.value} value={a.value}>
                      {a.label}
                    </option>
                  ))}
                </select>
                <OutputArgs o={o} index={i} onChange={(patch) => setOut(i, patch)} />
                <input
                  className="gh-input"
                  aria-label={`Nhãn hiển thị kết quả ${i + 1}`}
                  placeholder="nhãn hiển thị (tuỳ chọn)"
                  value={o.label}
                  onChange={(e) => setOut(i, { label: e.target.value })}
                />
                <IconButton
                  icon="ph ph-trash"
                  label={`Xoá kết quả ${i + 1}`}
                  variant="ghost"
                  iconSize={13}
                  tooltip={false}
                  disabled={readOnly || d.outputs.length <= 1}
                  onClick={() => setD((s) => ({ ...s, outputs: s.outputs.filter((_, j) => j !== i) }))}
                />
              </div>
              {show(errors.outputRows[i]) ? <InlineError>{errors.outputRows[i]}</InlineError> : null}
            </div>
          ))}
          {!readOnly ? (
            <div>
              <Button
                variant="ghost"
                icon="ph ph-plus"
                className="btn-27"
                onClick={() => setD((s) => ({ ...s, outputs: [...s.outputs, { action: 'set', field: 'intent', value: '', label: '' }] }))}
              >
                Thêm kết quả
              </Button>
            </div>
          ) : null}

          <div className="gh-field">
            <label className="gh-field__label" htmlFor="rule-hint">
              Gợi ý cho LLM (tuỳ chọn)
            </label>
            <textarea id="rule-hint" className="gh-textarea" value={d.prompt_hint} onChange={(e) => setD({ ...d, prompt_hint: e.target.value })} />
          </div>
        </fieldset>

        {rule ? (
          <div>
            <div className="dlg-section-title" style={{ marginBottom: 4 }}>
              Phiên bản
            </div>
            {versions.isPending ? (
              <Skeleton width="100%" height={28} />
            ) : versions.isError ? (
              <CardError error={versions.error} onRetry={() => void versions.refetch()} />
            ) : versions.data.length === 0 ? (
              <EmptyState icon="ph ph-clock-counter-clockwise" title="Chưa có phiên bản cũ" />
            ) : (
              <div>
                {[...versions.data]
                  .sort((a, b) => b.version - a.version)
                  .map((v) => (
                    <div className="version-row" key={v.version}>
                      <span className="version-row__v">v{v.version}</span>
                      <span style={{ flex: 2, minWidth: 0 }}>
                        {v.conditions.map(conditionLabel).join(' · ')} → {v.outputs.map(outputLabel).join(' · ')}
                      </span>
                      <span className="version-row__meta">
                        ngưỡng {fmtDec(v.threshold, 2)} · {fmtDMClock(v.created_at, tz)} · {actorName(v.created_by)}
                      </span>
                      {!readOnly && v.version !== rule.version ? (
                        <Button
                          variant="ghost"
                          className="btn-22"
                          onClick={() => setD((s) => ({ ...ruleToDraft({ ...rule, ...v }), name: s.name, kind: s.kind, prompt_hint: s.prompt_hint }))}
                        >
                          Dùng lại
                        </Button>
                      ) : v.version === rule.version ? (
                        <span className="version-row__meta" style={{ flex: 'none' }}>
                          đang dùng
                        </span>
                      ) : null}
                    </div>
                  ))}
              </div>
            )}
          </div>
        ) : null}
        <InlineError>{save.isError ? errorText(save.error) : null}</InlineError>
        <button type="submit" hidden />
      </form>
    </Dialog>
  );
}

function ConditionArg({ c, index, onChange }: { c: ConditionDraft; index: number; onChange: (v: string) => void }) {
  const label = `Giá trị điều kiện ${index + 1}`;
  switch (c.type) {
    case 'is_question':
      return <span className="muted-note" style={{ alignSelf: 'center' }}>không cần giá trị</span>;
    case 'has_entity':
      return (
        <select className="gh-input" aria-label={label} value={c.arg} onChange={(e) => onChange(e.target.value)}>
          <option value="">chọn thực thể…</option>
          {ENTITIES.map((en) => (
            <option key={en} value={en}>
              {en}
            </option>
          ))}
        </select>
      );
    case 'min_words':
    case 'max_words':
    case 'repeat_unanswered':
      return <input className="gh-input" aria-label={label} inputMode="numeric" placeholder="n" value={c.arg} onChange={(e) => onChange(e.target.value)} />;
    case 'regex':
      return <input className="gh-input gh-mono" aria-label={label} placeholder="biểu thức" value={c.arg} onChange={(e) => onChange(e.target.value)} />;
    case 'llm':
      return <input className="gh-input" aria-label={label} placeholder="điều LLM cần xét" value={c.arg} onChange={(e) => onChange(e.target.value)} />;
    default:
      return (
        <input className="gh-input" aria-label={label} placeholder="từ khoá, cách nhau bằng dấu phẩy" value={c.arg} onChange={(e) => onChange(e.target.value)} />
      );
  }
}

function OutputArgs({ o, index, onChange }: { o: OutputDraft; index: number; onChange: (p: Partial<OutputDraft>) => void }) {
  if (o.action === 'discard') return <span className="muted-note" style={{ alignSelf: 'center' }}>không ghi vào kho sạch</span>;
  if (o.action === 'alert') {
    return (
      <select className="gh-input" aria-label={`Mức cảnh báo ${index + 1}`} value={o.field} onChange={(e) => onChange({ field: e.target.value })}>
        {['P1', 'P2', 'P3'].map((p) => (
          <option key={p} value={p}>
            {p}
          </option>
        ))}
      </select>
    );
  }
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)', gap: 8 }}>
      <select className="gh-input" aria-label={`Trường kết quả ${index + 1}`} value={o.field} onChange={(e) => onChange({ field: e.target.value })}>
        {OUTPUT_FIELDS[o.action].map((f) => (
          <option key={f} value={f}>
            {f}
          </option>
        ))}
      </select>
      <input
        className="gh-input"
        aria-label={`Giá trị kết quả ${index + 1}`}
        placeholder={o.action === 'add' ? '+30' : 'giá trị'}
        inputMode={o.action === 'add' ? 'numeric' : undefined}
        value={o.value}
        onChange={(e) => onChange({ value: e.target.value })}
      />
    </div>
  );
}
