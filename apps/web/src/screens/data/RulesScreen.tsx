import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { SCREEN_BY_KEY, type Rule, type RuleTestBatchResult, type RuleTestResult, type Weight } from '@gen-harness/contracts';
import { Button, Dialog, EmptyState, Icon, Skeleton, Switch, Tag, type Tone } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { qk2, useRules, useWeights } from '../../lib/dataQueries';
import { fmtInt } from '../../lib/format';
import { useCan } from '../../lib/permissions';
import { queryClient } from '../../lib/queryClient';
import { toast } from '../../lib/toast';
import { errorText } from '../../lib/errorText';
import { Bar, CardError, InlineError, Panel, ScreenHead, SkeletonLines } from '../common';
import {
  BAD,
  N5,
  OK,
  WARN,
  conditionLabel,
  hitsLabel,
  outputLabel,
  ruleTone,
  testOutputTone,
  weightsMessage,
  weightsValid,
} from './dataModel';
import { PipelineStrip } from './PipelineStrip';
import { RuleEditorDialog } from './RuleEditorDialog';
import { WeightSliders } from './WeightSliders';

function kindTagTone(kind: string): Tone {
  return kind === 'risk' ? 'bad' : kind === 'competition' || kind === 'hr' ? 'warn' : 'neutral';
}

export function RulesScreen() {
  const meta = SCREEN_BY_KEY.rules;
  const canManage = useCan('data.manage');
  const rules = useRules();
  const [editing, setEditing] = useState<{ rule: Rule | null } | null>(null);
  const [batchOpen, setBatchOpen] = useState(false);

  const actions = (
    <>
      <Button variant="secondary" icon="ph ph-flask" className="btn-30" data-gen-target="rules.batch" onClick={() => setBatchOpen(true)}>
        Chạy thử trên 100 bản ghi
      </Button>
      {canManage ? (
        <Button variant="primary" icon="ph ph-plus" className="btn-30" data-gen-target="rules.add" onClick={() => setEditing({ rule: null })}>
          Thêm quy tắc
        </Button>
      ) : null}
    </>
  );

  return (
    <>
      <PipelineStrip screen="rules" />
      <div className="screen">
        <ScreenHead title={meta.title} description={meta.description} maxWidth={760} actions={actions} />
        <div className="rules-grid">
          <div className="rule-list" aria-label="Danh sách quy tắc" aria-busy={rules.isFetching || undefined} data-gen-target="rules.list">
            {rules.isPending ? (
              Array.from({ length: 4 }, (_, i) => <RuleCardSkeleton key={i} />)
            ) : rules.isError ? (
              <div className="gh-card">
                <CardError error={rules.error} onRetry={() => void rules.refetch()} retrying={rules.isFetching} />
              </div>
            ) : rules.data.length === 0 ? (
              <div className="gh-card">
                <EmptyState
                  icon="ph ph-funnel"
                  title="Chưa có quy tắc sàng lọc nào"
                  description={
                    canManage
                      ? 'Thêm quy tắc đầu tiên để hệ thống biết gán nhãn gì cho dữ liệu thô.'
                      : 'Chủ sở hữu chưa định nghĩa quy tắc nào.'
                  }
                />
              </div>
            ) : (
              rules.data.map((r) => (
                <RuleCard key={r.id} rule={r} canManage={canManage} onEdit={() => setEditing({ rule: r })} />
              ))
            )}
          </div>
          <div className="side-col">
            <WeightsCard canManage={canManage} />
            <TestCard />
          </div>
        </div>
      </div>
      <RuleEditorDialog
        open={!!editing}
        onClose={() => setEditing(null)}
        rule={editing?.rule ?? null}
        readOnly={!canManage}
      />
      <TestBatchDialog open={batchOpen} onClose={() => setBatchOpen(false)} />
    </>
  );
}

function RuleCard({ rule, canManage, onEdit }: { rule: Rule; canManage: boolean; onEdit: () => void }) {
  const tone = ruleTone(rule.kind);
  const pct = Math.round(rule.threshold * 100);
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.rules.setEnabled(rule.id, enabled),
    onMutate: async (enabled) => {
      await queryClient.cancelQueries({ queryKey: qk2.rules });
      const prev = queryClient.getQueryData<Rule[]>(qk2.rules);
      queryClient.setQueryData<Rule[]>(qk2.rules, (old) => old?.map((r) => (r.id === rule.id ? { ...r, enabled } : r)));
      return { prev };
    },
    onError: (e, _v, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(qk2.rules, ctx.prev);
      toast(errorText(e), 'bad');
    },
    onSuccess: (saved) => {
      queryClient.setQueryData<Rule[]>(qk2.rules, (old) => old?.map((r) => (r.id === saved.id ? saved : r)));
      toast(saved.enabled ? `Đã bật ${saved.code}` : `Đã tắt ${saved.code}`, 'neutral');
    },
  });
  return (
    <article className="rule-card" data-off={rule.enabled ? undefined : ''} aria-label={`${rule.code} ${rule.name}`}>
      <div className="rule-card__head">
        <span className="rule-card__code">{rule.code}</span>
        <span className="rule-card__name">{rule.name}</span>
        <Tag tone={kindTagTone(rule.kind)}>{rule.kind_label}</Tag>
        <span className="rule-card__spacer" />
        <span className="rule-card__hits">{hitsLabel(rule)}</span>
        <Switch
          checked={rule.enabled}
          label={`${rule.enabled ? 'Tắt' : 'Bật'} quy tắc ${rule.code}`}
          disabled={!canManage || toggle.isPending}
          onChange={(v) => toggle.mutate(v)}
        />
      </div>
      <div className="rule-card__body">
        <span className="rule-card__if">NẾU</span>
        <div className="rule-chips">
          {rule.conditions.map((c, i) => (
            <span className="cond-chip" key={i}>
              {conditionLabel(c)}
            </span>
          ))}
        </div>
        <span className="rule-card__then">THÌ</span>
        <div className="rule-chips">
          {rule.outputs.map((o, i) => (
            <span className="out-chip" key={i}>
              {outputLabel(o)}
            </span>
          ))}
        </div>
      </div>
      <div className="rule-card__foot">
        <span className="rule-card__thlabel">Ngưỡng tin cậy tối thiểu</span>
        <Bar pct={pct} tone={tone} height={4} className="rule-card__thbar" />
        <span className="rule-card__thval" style={{ color: tone }}>
          {pct}%
        </span>
        <Button variant="ghost" className="btn-22" onClick={onEdit} aria-label={`${canManage ? 'Sửa' : 'Xem'} quy tắc ${rule.code}`}>
          {canManage ? 'Sửa' : 'Xem'}
        </Button>
      </div>
    </article>
  );
}

function RuleCardSkeleton() {
  return (
    <div className="rule-card" aria-hidden>
      <div className="rule-card__head">
        <Skeleton width={28} height={10} />
        <Skeleton width={160} height={12} />
        <span className="rule-card__spacer" />
        <Skeleton width={30} height={16} radius={999} />
      </div>
      <Skeleton width="80%" height={20} />
      <Skeleton width="55%" height={20} />
      <Skeleton width="100%" height={4} />
    </div>
  );
}

// ── Trọng số chấm điểm ────────────────────────────────────────────────────
function WeightsCard({ canManage }: { canManage: boolean }) {
  const weights = useWeights();
  const [draft, setDraft] = useState<Weight[] | null>(null);
  const current = draft ?? weights.data ?? [];
  const dirty = !!draft && !!weights.data && draft.some((w, i) => w.value !== weights.data[i]?.value);
  const msg = weightsMessage(current);

  const save = useMutation({
    mutationFn: () => api.rules.setWeights(current.map((w) => ({ dimension: w.dimension, value: w.value }))),
    onSuccess: (ws) => {
      queryClient.setQueryData(qk2.weights, ws);
      setDraft(null);
      toast('Đã lưu trọng số chấm điểm');
    },
  });

  const setValue = (i: number, v: number) => {
    const base = draft ?? weights.data ?? [];
    setDraft(base.map((w, j) => (j === i ? { ...w, value: v } : w)));
  };

  return (
    <Panel flush genTarget="rules.weights" title="Trọng số chấm điểm" kicker="Dùng chung cho mọi đối tượng" label="Trọng số chấm điểm">
      {weights.isPending ? (
        <SkeletonLines rows={6} padding="13px 16px 15px" gap={11} />
      ) : weights.isError ? (
        <CardError error={weights.error} onRetry={() => void weights.refetch()} retrying={weights.isFetching} />
      ) : current.length === 0 ? (
        <EmptyState icon="ph ph-sliders-horizontal" title="Chưa có chiều chấm điểm nào" />
      ) : (
        <div className="weights">
          <WeightSliders weights={current} disabled={!canManage} onChange={setValue} />
          {canManage && (dirty || msg) ? (
            <div className="weights__foot">
              <span className="weights__sum" role="status" style={{ color: msg ? BAD : OK }}>
                {msg ?? 'Tổng 100% — sẵn sàng lưu.'}
              </span>
              <Button variant="ghost" className="btn-27" disabled={!dirty} onClick={() => setDraft(null)}>
                Đặt lại
              </Button>
              <Button
                variant="primary"
                className="btn-27"
                disabled={!dirty || !weightsValid(current)}
                loading={save.isPending}
                onClick={() => save.mutate()}
              >
                Lưu trọng số
              </Button>
            </div>
          ) : null}
          {save.isError ? <InlineError>{errorText(save.error)}</InlineError> : null}
        </div>
      )}
    </Panel>
  );
}

// ── Chạy thử một bản ghi ──────────────────────────────────────────────────
function TestCard() {
  // Seed: the latest raw record, so the card shows a real example like the design.
  const seed = useQuery({
    queryKey: ['rules', 'test-seed'],
    queryFn: ({ signal }) => api.raw.list({ limit: 1, state: 'clean' }, signal),
    staleTime: 5 * 60_000,
  });
  const seedItem = seed.data?.items[0] ?? null;
  const [text, setText] = useState<string | null>(null);
  const [input, setInput] = useState<{ raw_event_id: string } | { text: string } | null>(null);

  useEffect(() => {
    if (text === null && seedItem) setInput({ raw_event_id: seedItem.id });
  }, [seedItem, text]);

  // Debounce edits: test 600 ms after the last keystroke.
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const onEdit = (v: string) => {
    setText(v);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setInput(v.trim() ? { text: v.trim() } : null), 600);
  };
  useEffect(() => () => clearTimeout(timer.current), []);

  const test = useQuery<RuleTestResult>({
    queryKey: ['rules', 'test', input],
    queryFn: () => api.rules.test(input!),
    enabled: !!input,
    staleTime: 30_000,
  });

  const shownText = text ?? test.data?.input.text ?? seedItem?.text ?? '';
  const code = text !== null ? 'tự nhập' : (test.data?.input.code ?? seedItem?.code ?? '');

  return (
    <Panel flush
      genTarget="rules.tryone"
      title="Chạy thử một bản ghi"
      kicker="Thô vào — sạch ra"
      label="Chạy thử một bản ghi"
      aside={code ? <span className="card-aside-code">{code}</span> : undefined}
    >
      <div className="test-body">
        <div className="test-raw">
          <label className="test-label" htmlFor="rule-test-text">
            Thô
          </label>
          {seed.isPending && text === null ? (
            <div style={{ marginTop: 8 }}>
              <Skeleton width="95%" height={10} />
              <Skeleton width="70%" height={10} style={{ marginTop: 6 }} />
            </div>
          ) : (
            <textarea
              id="rule-test-text"
              className="test-text"
              rows={2}
              value={shownText ? (text === null ? `"${shownText}"` : shownText) : ''}
              placeholder="Dán một tin nhắn để xem hệ thống sẽ gán nhãn gì…"
              onFocus={() => {
                if (text === null) setText(shownText);
              }}
              onChange={(e) => onEdit(e.target.value)}
            />
          )}
        </div>
        <Icon name="ph ph-arrow-down" size={15} className="test-arrow" />
        <div className="test-clean" aria-live="polite" aria-busy={test.isFetching || undefined}>
          <div className="test-label">Sạch</div>
          {!input ? (
            <p className="test-out__val" style={{ color: N5, marginTop: 8 }}>
              {seed.isError ? errorText(seed.error) : seed.isPending ? 'Đang lấy bản ghi mẫu…' : 'Nhập một tin nhắn để chạy thử — không ghi gì vào kho.'}
            </p>
          ) : test.isError ? (
            <p className="test-out__val" style={{ color: BAD, marginTop: 8 }}>
              {errorText(test.error)}
            </p>
          ) : !test.data ? (
            <div className="test-out">
              {Array.from({ length: 5 }, (_, i) => (
                <div className="test-out__row" key={i}>
                  <Skeleton width={60} height={9} />
                  <Skeleton width={`${50 + i * 8}%`} height={10} />
                </div>
              ))}
            </div>
          ) : (
            <div className="test-out" style={{ opacity: test.isFetching ? 0.6 : 1 }}>
              {test.data.output.map((o) => (
                <div className="test-out__row" key={o.key}>
                  <span className="test-out__key">{o.key}</span>
                  <span className="test-out__val" style={{ color: testOutputTone(o.key, test.data.would_write) }}>
                    {o.value}
                  </span>
                </div>
              ))}
              {test.data.output.length === 0 ? (
                <span className="test-out__val" style={{ color: N5 }}>
                  {test.data.discarded_by ? `Bị loại bởi ${test.data.discarded_by}` : 'Không quy tắc nào khớp.'}
                </span>
              ) : null}
            </div>
          )}
        </div>
      </div>
    </Panel>
  );
}

// ── Chạy thử trên 100 bản ghi ─────────────────────────────────────────────
function TestBatchDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const batch = useMutation<RuleTestBatchResult>({ mutationFn: () => api.rules.testBatch(100) });
  const { mutate, reset } = batch;
  useEffect(() => {
    if (open) mutate();
    else reset();
  }, [open, mutate, reset]);
  const r = batch.data;
  const max = useMemo(() => Math.max(1, ...(r?.by_rule.map((b) => b.hits) ?? [1])), [r]);
  const pct = (n: number) => (r && r.n ? Math.round((n / r.n) * 100) : 0);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      width={460}
      title="Chạy thử trên 100 bản ghi"
      kicker="Bộ quy tắc hiện tại · không ghi gì vào kho"
      actions={
        <>
          <Button variant="secondary" onClick={() => mutate()} loading={batch.isPending} icon="ph ph-arrow-clockwise">
            Chạy lại
          </Button>
          <Button variant="primary" onClick={onClose}>
            Xong
          </Button>
        </>
      }
    >
      {batch.isPending ? (
        <SkeletonLines rows={4} padding="0" />
      ) : batch.isError ? (
        <CardError error={batch.error} onRetry={() => mutate()} />
      ) : r ? (
        <div className="dlg-fields" aria-live="polite">
          <div className="mini-bars">
            {[
              { label: 'Vào kho sạch', n: r.clean, tone: OK },
              { label: 'Tin cậy thấp', n: r.lowconf, tone: WARN },
              { label: 'Loại — nhiễu', n: r.discarded, tone: N5 },
            ].map((x) => (
              <div className="mini-bars__row" key={x.label}>
                <span style={{ width: 110, flex: 'none' }}>{x.label}</span>
                <Bar pct={pct(x.n)} tone={x.tone} />
                <span className="mini-bars__n" style={{ color: x.tone } as CSSProperties}>
                  {fmtInt(x.n)}
                </span>
              </div>
            ))}
          </div>
          <div className="dlg-section-title">Lượt khớp theo quy tắc · {fmtInt(r.n)} bản ghi mới nhất</div>
          {r.by_rule.length === 0 ? (
            <p className="muted-note">Không quy tắc nào khớp.</p>
          ) : (
            <div className="mini-bars">
              {r.by_rule.map((b) => (
                <div className="mini-bars__row" key={b.code}>
                  <span className="gh-mono" style={{ width: 110, flex: 'none' }}>
                    {b.code}
                  </span>
                  <Bar pct={(b.hits / max) * 100} tone="var(--color-accent)" height={4} />
                  <span className="mini-bars__n">{fmtInt(b.hits)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </Dialog>
  );
}
