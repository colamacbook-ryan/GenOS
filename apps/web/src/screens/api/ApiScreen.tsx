import { useEffect, useState, type DragEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import type { AgentBindingSlot, BindableModel, Effort, Provider, ProviderKind } from '@gen-harness/contracts';
import { ApiError, SCREEN_BY_KEY } from '@gen-harness/contracts';
import { Button, Dialog, EmptyState, Icon, SelectField, Switch, TextField } from '@gen-harness/ui';
import { agyBlockedReason, isAgyModel } from '../../lib/agyLayers';
import { useProviders } from '../../lib/dataQueries';
import { errorDetail, errorReasons, errorText } from '../../lib/errorText';
import { useCan } from '../../lib/permissions';
import { toast } from '../../lib/toast';
import { AI_SOURCE_PATH } from '../../setup/steps';
import { CardError, FriendlyErrorText, InlineError, Panel, PinHint, PIN_ROUTE_CHANGE_TITLE, ScreenHead, SkeletonLines, StateChip } from '../common';
import { DefaultBadge } from '../../defaults/DefaultBadge';
import { ModelPicker } from './ModelPicker';
import { CliDiagnose } from '../system/CliDiagnose';
import {
  EFFORT_HINT,
  PROVIDER_ICON,
  PROVIDER_KIND_LABEL,
  PROVIDER_PRESETS,
  bindingEffortText,
  bindingModelText,
  bindingParamsText,
  chainModelText,
  choiceText,
  DEFAULT_CONTEXT_TOKENS,
  effortOptionText,
  findPreset,
  fmtQuota,
  isCliKind,
  parseQuotaInput,
  providerStatus,
  supportedEfforts,
} from './apiModel';
import {
  useAddModel,
  useAddProviderKey,
  useBindings,
  useCreateProvider,
  useFailoverRules,
  useRemoveBinding,
  useRemoveProvider,
  useRemoveProviderKey,
  useRemoveProviderModel,
  useReorderChain,
  useSetBinding,
  useSetProviderEnabled,
  useTestProvider,
} from './queries';

export function ApiScreen() {
  const meta = SCREEN_BY_KEY.api;
  const canManage = useCan('system.manage');
  const providers = useProviders();
  const testAll = useTestProvider();
  const [addingProvider, setAddingProvider] = useState(false);
  const [addInitial, setAddInitial] = useState<AddChoice>('gemini');
  const [addingKeyFor, setAddingKeyFor] = useState<Provider | null>(null);
  const [params, setParams] = useSearchParams();
  const addParam = params.get('add');

  // `/api?add=openrouter` (nút "Thêm nhà cung cấp" ở thẻ "Nguồn AI cho việc nền") ⇒ mở thẳng hộp Thêm, chọn sẵn mẫu;
  // xoá tham số khỏi URL để tải lại/quay lại không mở lại hộp.
  useEffect(() => {
    if (addParam === null) return;
    if (canManage) {
      setAddInitial(isAddChoice(addParam) ? addParam : 'gemini');
      setAddingProvider(true);
    }
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete('add');
        return next;
      },
      { replace: true },
    );
  }, [addParam, canManage, setParams]);

  // v0.1.66 (F019): nút đầu trang chỉ kiểm các nguồn khoá API (nguồn CLI có nút "Kiểm tra kết nối" riêng, mỗi lần là một lượt
  // gọi model thật). Không có nguồn khoá API nào ⇒ nút tắt kèm lý do, không bao giờ báo xanh "0 / 0".
  const apiSources = (providers.data ?? []).filter((p) => !isCliKind(p.kind) && p.kind !== 'system_one');
  const testAllConnections = async () => {
    const list = apiSources;
    if (list.length === 0) {
      toast('Chưa có nguồn khoá API nào để kiểm — nguồn Antigravity / Claude Code kiểm ở nút trên từng thẻ.', 'warn');
      return;
    }
    let ok = 0;
    for (const p of list) {
      try {
        const r = await testAll.mutateAsync(p.id);
        if (r.ok) ok += 1;
      } catch {
        // đã báo lỗi qua từng nút "Kiểm tra" riêng của thẻ; ở đây chỉ đếm tổng
      }
    }
    toast(`Đã kiểm ${list.length} nhà cung cấp — ${ok} kết nối được`, ok === list.length ? 'ok' : 'warn');
  };

  const actions = (
    <>
      <Button
        variant="secondary"
        icon="ph ph-pulse"
        className="btn-30"
        loading={testAll.isPending}
        onClick={() => void testAllConnections()}
        disabled={apiSources.length === 0}
        title={apiSources.length === 0 ? 'Chưa có nguồn khoá API nào — nguồn Antigravity / Claude Code kiểm ở nút trên từng thẻ' : 'Kiểm tra mọi nguồn khoá API'}
      >
        Kiểm tra kết nối
      </Button>
      {canManage ? (
        <Button variant="primary" icon="ph ph-plus" className="btn-30" data-gen-target="api.add_provider" onClick={() => {
            setAddInitial('gemini');
            setAddingProvider(true);
          }}>
          Thêm nhà cung cấp
        </Button>
      ) : null}
    </>
  );

  return (
    <div className="screen">
      <ScreenHead title={meta.title} description={meta.description} maxWidth={760} actions={actions} />

      <div className="apm-providers" aria-busy={providers.isFetching || undefined}>
        {providers.isPending ? (
          Array.from({ length: 2 }, (_, i) => <ProviderCardSkeleton key={i} />)
        ) : providers.isError ? (
          <div className="gh-card">
            <CardError error={providers.error} onRetry={() => void providers.refetch()} retrying={providers.isFetching} />
          </div>
        ) : providers.data.length === 0 ? (
          <div className="gh-card">
            <EmptyState
              icon="ph ph-plugs"
              title="Chưa có nhà cung cấp nào"
              description={canManage ? 'Thêm nguồn AI đầu tiên để agent có model để gọi.' : 'Owner chưa thêm nhà cung cấp nào.'}
              // v0.1.70 (F029): MỘT đường tới "thêm nguồn AI" (cùng `readiness.FIX_AI`): màn Thiết lập nguồn AI.
              action={
                canManage ? (
                  <Link to={AI_SOURCE_PATH} className="gh-btn gh-btn--secondary btn-27" data-testid="api-add-source">
                    <Icon name="ph ph-plus-circle" size={13} />
                    Thêm / kiểm tra nguồn AI
                  </Link>
                ) : undefined
              }
            />
          </div>
        ) : (
          providers.data.map((p) => <ProviderCard key={p.id} provider={p} canManage={canManage} onAddKey={() => setAddingKeyFor(p)} />)
        )}
      </div>

      <div className="apm-mid" style={{ gridTemplateColumns: 'minmax(0, 1fr)' }}>
        <BindingsPanel canManage={canManage} />
      </div>

      {/* v0.1.55: nhiệt độ, ngữ cảnh, bộ quy tắc và hạn mức model là việc hiếm — gom vào "Nâng cao", mặc định đóng. */}
      <details className="brain-advanced" data-testid="api-advanced">
        <summary>Nâng cao — nhiệt độ, ngữ cảnh và hạn mức model</summary>
        <div className="apm-side" style={{ marginTop: 12 }}>
          <AdvancedParamsPanel />
          <RateLimitsPanel />
        </div>
      </details>

      <div className="apm-panels">
        <PriorityChainPanel canManage={canManage} />
        <FailoverRulesPanel />
      </div>

      {/* v0.1.42 (F-61): thẻ tài khoản CLI chỉ ở một chỗ — Kết nối › Bộ não AI. */}
      <div className="gh-card apm-cli-link">
        <Link to="/connections#brain" className="sys-link">
          <Icon name="ph ph-plugs-connected" size={13} />
          Tài khoản Google / Claude CLI ở Kết nối
        </Link>
      </div>

      {addingProvider ? <AddProviderDialog initial={addInitial} onClose={() => setAddingProvider(false)} /> : null}
      {addingKeyFor ? <AddKeyDialog provider={addingKeyFor} onClose={() => setAddingKeyFor(null)} /> : null}
    </div>
  );
}

function ProviderCardSkeleton() {
  return (
    <div className="apm-provider" aria-hidden>
      <SkeletonLines rows={3} padding="0" />
    </div>
  );
}

function ProviderCard({ provider: p, canManage, onAddKey }: { provider: Provider; canManage: boolean; onAddKey: () => void }) {
  const test = useTestProvider();
  const setEnabled = useSetProviderEnabled();
  const remove = useRemoveProvider();
  const status = providerStatus(p);
  const tone = status.tone;
  const testErr = test.isError && test.variables === p.id ? test.error : null;
  const lastResult = test.data && test.variables === p.id ? test.data : null;
  return (
    <article className="apm-provider" aria-label={p.name}>
      <div className="apm-provider__head">
        <div className="apm-provider__icon" style={{ color: tone }}>
          <Icon name={PROVIDER_ICON[p.kind as ProviderKind]} size={15} />
        </div>
        <div className="apm-provider__title">
          <div className="apm-provider__name">{p.name}</div>
          <div className="apm-provider__kind">{PROVIDER_KIND_LABEL[p.kind as ProviderKind]}</div>
        </div>
        <StateChip color={tone} dot>
          {status.label}
        </StateChip>
      </div>
      <div className="apm-provider__body">
        {p.endpoint ? <ProviderField label="ĐỊA CHỈ GỌI" value={p.endpoint} tone="var(--color-neutral-300)" /> : null}
        <ProviderField
          label="KHOÁ"
          value={isCliKind(p.kind) ? 'dùng phiên đăng nhập CLI' : p.keys.length ? p.keys.map((k) => `${k.label} ····${k.last4}`).join(' · ') : 'chưa có khoá'}
          tone={p.keys.length || isCliKind(p.kind) ? 'var(--color-neutral-300)' : 'var(--color-warn)'}
          secret={!isCliKind(p.kind) && p.keys.length > 0}
        />
        <ProviderField
          label="MODEL"
          value={p.models.length ? p.models.map((m) => choiceText(m.model_name, m.effort)).join(', ') : 'chưa có model'}
          tone="var(--color-neutral-300)"
        />
      </div>
      <div className="apm-provider__foot">
        {canManage && !isCliKind(p.kind) ? (
          <Button variant="ghost" className="btn-22" icon="ph ph-key" onClick={onAddKey}>
            Thêm khoá
          </Button>
        ) : null}
        <Button variant="secondary" className="btn-22" icon="ph ph-pulse" loading={test.isPending && test.variables === p.id} onClick={() => test.mutate(p.id)}>
          Kiểm tra kết nối
        </Button>
        {canManage && !isCliKind(p.kind) && p.kind !== 'system_one' ? (
          <Button
            variant="ghost"
            className="btn-22"
            icon="ph ph-trash"
            loading={remove.isPending}
            onClick={() => {
              if (window.confirm(`Xoá nguồn "${p.name}"? Khoá API của nguồn này cũng bị xoá.`)) remove.mutate(p.id);
            }}
          >
            Xoá
          </Button>
        ) : null}
        {canManage ? (
          <span title={`${p.enabled ? 'Tắt' : 'Bật'} nhà cung cấp — ${PIN_ROUTE_CHANGE_TITLE}`}>
            <Switch checked={p.enabled} label={`${p.enabled ? 'Tắt' : 'Bật'} ${p.name}`} disabled={setEnabled.isPending} onChange={(v) => setEnabled.mutate({ id: p.id, enabled: v })} />
          </span>
        ) : null}
      </div>
      {lastResult ? (
        <div className={lastResult.ok ? 'apm-test-result apm-test-result--ok' : 'apm-test-result apm-test-result--bad'} role="status">
          {lastResult.ok ? `Kết nối được · độ trễ ${lastResult.latency_ms} ms` : <FriendlyErrorText raw={lastResult.error} />}
        </div>
      ) : null}
      {canManage && p.kind !== 'system_one' ? <ModelPicker provider={p} test={lastResult ?? p.last_test} /> : null}
      {canManage && p.kind !== 'system_one' ? <ProviderManage provider={p} /> : null}
      {isCliKind(p.kind) ? <CliDiagnose provider={p} /> : null}
      {testErr ? <InlineError detail={errorDetail(testErr)}>{errorText(testErr)}</InlineError> : null}
      {remove.isError ? <InlineError detail={errorDetail(remove.error)}>{errorText(remove.error)}</InlineError> : null}
      {setEnabled.isError ? <InlineError detail={errorDetail(setEnabled.error)}>{errorText(setEnabled.error)}</InlineError> : null}
    </article>
  );
}

/**
 * v0.1.66 (F008, F009): xoá được khoá dán sai và model thêm nhầm; đặt hạn mức ngày / lượt-phút cho từng model (backend đã giữ
 * hạn mức và cảnh báo "còn dưới 20%", nhưng trước đây không có ô nhập nên không bao giờ chạy).
 */
function ProviderManage({ provider: p }: { provider: Provider }) {
  const removeKey = useRemoveProviderKey();
  const removeModel = useRemoveProviderModel();
  const keys = isCliKind(p.kind) ? [] : p.keys;
  if (!keys.length && !p.models.length) return null;
  const err = removeKey.error ?? removeModel.error;
  return (
    <details className="brain-advanced apm-manage" data-testid={`provider-manage-${p.id}`}>
      <summary>Quản lý khoá và model</summary>
      {keys.length ? (
        <div className="apm-manage__group">
          <div className="apm-manage__title">Khoá API</div>
          {keys.map((k) => (
            <div className="apm-manage__row" key={k.id}>
              <span className="apm-manage__name mono">{`${k.label} ····${k.last4}`}</span>
              <Button
                variant="ghost"
                className="btn-22"
                icon="ph ph-trash"
                aria-label={`Xoá khoá ${k.label}`}
                loading={removeKey.isPending && removeKey.variables?.keyId === k.id}
                onClick={() => {
                  if (window.confirm(`Xoá khoá ${k.label} (····${k.last4}) của "${p.name}"? Khoá này rời vòng xoay ngay.`)) removeKey.mutate({ id: p.id, keyId: k.id });
                }}
              >
                Xoá khoá
              </Button>
            </div>
          ))}
        </div>
      ) : null}
      {p.models.length ? (
        <div className="apm-manage__group">
          <div className="apm-manage__title">Model và hạn mức</div>
          {p.models.map((m) => (
            <ModelLimitRow key={`${m.id}-${m.daily_quota ?? ''}-${m.rate_limit_per_min ?? ''}`} provider={p} model={m} onRemove={() => removeModel.mutate({ id: p.id, modelId: m.id })} removing={removeModel.isPending && removeModel.variables?.modelId === m.id} />
          ))}
        </div>
      ) : null}
      {err ? <InlineError detail={errorDetail(err)}>{errorText(err)}</InlineError> : null}
    </details>
  );
}

function ModelLimitRow({ provider: p, model: m, onRemove, removing }: { provider: Provider; model: Provider['models'][number]; onRemove: () => void; removing: boolean }) {
  const save = useAddModel();
  const [daily, setDaily] = useState(m.daily_quota == null ? '' : String(m.daily_quota));
  const [perMin, setPerMin] = useState(m.rate_limit_per_min == null ? '' : String(m.rate_limit_per_min));
  const [bad, setBad] = useState(false);
  const enabled = m.enabled !== false;   // thiếu trường (máy chủ cũ) = đang bật
  const submit = () => {
    const d = parseQuotaInput(daily);
    const r = parseQuotaInput(perMin);
    if (d === undefined || r === undefined) {
      setBad(true);
      return;
    }
    setBad(false);
    save.mutate({ id: p.id, body: { model_name: m.model_name, daily_quota: d, rate_limit_per_min: r } }, { onSuccess: () => toast(`Đã lưu hạn mức của ${m.model_name}.`) });
  };
  return (
    <div className="apm-manage__row apm-manage__row--model">
      <span className="apm-manage__name mono" title={m.model_name}>
        {m.model_name}
        {enabled ? '' : ' (đã tắt)'}
      </span>
      <label className="apm-manage__field">
        <span className="muted-note">Hạn mức/ngày</span>
        <input className="gh-input" inputMode="numeric" placeholder="không giới hạn" aria-label={`Hạn mức ngày của ${m.model_name}`} value={daily} onChange={(e) => setDaily(e.target.value)} />
      </label>
      <label className="apm-manage__field">
        <span className="muted-note">Lượt/phút</span>
        <input className="gh-input" inputMode="numeric" placeholder="không giới hạn" aria-label={`Lượt mỗi phút của ${m.model_name}`} value={perMin} onChange={(e) => setPerMin(e.target.value)} />
      </label>
      <Button variant="secondary" className="btn-22" loading={save.isPending} aria-label={`Lưu hạn mức của ${m.model_name}`} title={PIN_ROUTE_CHANGE_TITLE} onClick={submit}>
        Lưu hạn mức
      </Button>
      <Button
        variant="secondary"
        className="btn-22"
        loading={save.isPending && save.variables?.body.enabled !== undefined}
        aria-label={`${enabled ? 'Tắt' : 'Bật'} model ${m.model_name}`}
        title={PIN_ROUTE_CHANGE_TITLE}
        onClick={() =>
          save.mutate(
            { id: p.id, body: { model_name: m.model_name, enabled: !enabled } },
            { onSuccess: () => toast(enabled ? `Đã tắt ${m.model_name} — lịch sử chi phí vẫn giữ.` : `Đã bật lại ${m.model_name}.`) },
          )
        }
      >
        {enabled ? 'Tắt model' : 'Bật model'}
      </Button>
      <Button
        variant="ghost"
        className="btn-22"
        icon="ph ph-trash"
        loading={removing}
        aria-label={`Gỡ model ${m.model_name}`}
        title={PIN_ROUTE_CHANGE_TITLE}
        onClick={() => {
          if (window.confirm(`Gỡ model "${m.model_name}" khỏi "${p.name}"? Vai nào đang gán model này sẽ quay về model chuẩn.`)) onRemove();
        }}
      >
        Gỡ model
      </Button>
      {bad ? <InlineError>Nhập số nguyên từ 1 trở lên, hoặc để trống = không giới hạn.</InlineError> : null}
      {save.isError ? <InlineError detail={errorDetail(save.error)}>{errorText(save.error)}</InlineError> : null}
    </div>
  );
}

function ProviderField({ label, value, tone, secret }: { label: string; value: string; tone: string; secret?: boolean }) {
  return (
    <div className="apm-field">
      <span className="apm-field__key">{label}</span>
      <div className="apm-field__box">
        <span className="apm-field__val" style={{ color: tone }} title={value}>
          {value}
        </span>
        {secret ? <Icon name="ph ph-eye" size={13} color="var(--color-neutral-700)" /> : null}
      </div>
    </div>
  );
}

function BindingsPanel({ canManage }: { canManage: boolean }) {
  const bindings = useBindings();
  const [editing, setEditing] = useState<AgentBindingSlot | null>(null);
  return (
    <Panel
      title="Gán model cho từng agent"
      genTarget="api.bindings"
      kicker="Agent nào dùng model nào, với quy tắc nào"
      label="Gán model cho từng agent"
      bodyClass="apm-table-wrap"
    >
      {bindings.isPending ? (
        <SkeletonLines rows={5} padding="10px 16px" />
      ) : bindings.isError ? (
        <CardError error={bindings.error} onRetry={() => void bindings.refetch()} retrying={bindings.isFetching} />
      ) : bindings.data.items.length === 0 ? (
        <EmptyState icon="ph ph-git-branch" title="Chưa có agent nào để gán model" />
      ) : (
        <table className="apm-table apm-table--bindings">
          <thead>
            <tr>
              <th>Agent</th>
              <th>Model</th>
              <th>Mức suy nghĩ</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {bindings.data.items.map((slot) => (
              <tr key={slot.agent_key}>
                <td className="apm-td-agent">
                  <span className="apm-table__agent">{slot.label}</span>
                </td>
                <td className="apm-td-model">
                  {canManage ? (
                    <button type="button" className="apm-model-pill" onClick={() => setEditing(slot)}>
                      {bindingModelText(slot)}
                      <Icon name="ph ph-caret-down" size={10} />
                    </button>
                  ) : (
                    <span>{bindingModelText(slot)}</span>
                  )}
                  {/* Gói G: dòng gán agy cũ của khoá ngoại giao (máy chủ bỏ qua khi chạy) ⇒ nói thật để Sếp đổi model. */}
                  {slot.binding && agyBlockedReason(slot) && isAgyModel(bindings.data.models.find((m) => m.id === slot.binding?.model_id) ?? {}) ? (
                    <div className="muted-note" role="note" data-testid={`binding-agy-blocked-${slot.agent_key}`}>
                      {agyBlockedReason(slot)} — dòng này đang chạy bằng model khác, hãy chọn lại model.
                    </div>
                  ) : null}
                  {/* v0.1.67 (F016): dòng gán còn đó nhưng nguồn / model đã tắt ⇒ máy chủ chạy bằng Chuẩn — nói thẳng. Lý do
                      agy bị chặn đã có ghi chú ngay trên (tránh hai câu cho một chuyện). */}
                  {slot.binding?.inactive_reason &&
                  !(agyBlockedReason(slot) && isAgyModel(bindings.data.models.find((m) => m.id === slot.binding?.model_id) ?? {})) ? (
                    <div className="muted-note" role="note" data-testid={`binding-inactive-${slot.agent_key}`}>
                      {slot.binding.inactive_reason}
                    </div>
                  ) : null}
                  {/* v0.1.55: chưa gán ⇒ "Mặc định" (hồ sơ tiêu chuẩn theo vai); Sếp đã gán ⇒ "Đã đổi". Máy chủ cũ: không chip. */}
                  {slot.source ? (
                    <span style={{ marginLeft: 6 }}>
                      <DefaultBadge customized={slot.source === 'custom'} testId={`binding-badge-${slot.agent_key}`} />
                    </span>
                  ) : null}
                </td>
                <td className="mono apm-td-effort" data-label="Mức suy nghĩ" data-testid={`binding-effort-${slot.agent_key}`}>
                  {bindingEffortText(slot)}
                </td>
                <td className="apm-td-act">
                  {canManage && slot.binding ? (
                    <RemoveBindingButton agentKey={slot.agent_key} />
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {editing ? <BindingEditDialog slot={editing} models={bindings.data?.models ?? []} onClose={() => setEditing(null)} /> : null}
    </Panel>
  );
}

/**
 * Lỗi khi lưu gán model — câu thân thiện (title của ApiError) + phần mã/lý do để trong "Chi tiết kỹ thuật". Luôn trả
 * chuỗi (không bao giờ đưa đối tượng vào JSX). Model của Antigravity CLI gán được cho mọi khoá (không còn lỗi 409 AGY_OWNER_GEN_ONLY); riêng khoá chạy agy qua hộp riêng
 * mà hộp chưa chạy / chưa cài thì máy chủ trả 422 (xem `agy_blocked_reason`).
 */
function bindingErrorView(e: unknown): { message: string; detail: string | null } {
  const message = errorText(e);
  const reasons = errorReasons(e);
  const code = e instanceof ApiError ? `${e.status} ${e.code}` : null;
  const detail = [code, reasons].filter(Boolean).join(' · ');
  return { message, detail: reasons ? detail : null };
}

function RemoveBindingButton({ agentKey }: { agentKey: string }) {
  const remove = useRemoveBinding();
  return (
    <Button variant="ghost" className="btn-22" icon="ph ph-x" loading={remove.isPending} aria-label="Bỏ gán model" title="Bỏ gán — vai này dùng model chuẩn (tự chọn)" onClick={() => remove.mutate(agentKey)} />
  );
}

function BindingEditDialog({ slot, models, onClose }: { slot: AgentBindingSlot; models: BindableModel[]; onClose: () => void }) {
  const set = useSetBinding();
  const providers = useProviders();
  // v0.1.55: slot chưa gán chọn sẵn đúng model hồ sơ chuẩn đang phủ (nếu có trong danh sách), rồi mới tới model đầu tiên.
  // Dòng chạy agy qua hộp riêng (core.reply, core.refinery, agent gắn kênh / nhóm / người) mà hộp chưa chạy / chưa cài ⇒ model agy
  // bị làm mờ + giải thích; lý do do máy chủ trả (`agy_blocked_reason`), máy chủ từ chối 422 nếu vẫn gán.
  const agyBlocked = agyBlockedReason(slot);
  const usable = (m: BindableModel) => !(agyBlocked && isAgyModel(m));
  const std = slot.standard;
  const standardId = std ? models.find((m) => m.model_name === std.model_name && m.provider_name === std.provider_name)?.id : undefined;
  const boundId = slot.binding && models.some((m) => m.id === slot.binding?.model_id && usable(m)) ? slot.binding.model_id : undefined;
  const preferred = boundId ?? standardId ?? models.find(usable)?.id ?? models[0]?.id ?? '';
  const [picked, setPicked] = useState<string | null>(null);
  const modelId = picked ?? preferred;
  const setModelId = setPicked;
  // v0.1.55: mức suy nghĩ theo vai — chỉ model CLI có mức; bỏ trống = theo hồ sơ chuẩn / mức của model.
  const chosenProvider = (providers.data ?? []).find((p) => p.models.some((m) => m.id === modelId));
  const chosenName = chosenProvider?.models.find((m) => m.id === modelId)?.model_name;
  const efforts = supportedEfforts(chosenProvider, chosenName);
  const [effortPick, setEffortPick] = useState<Effort | '' | null>(null);
  const savedEffort = slot.binding?.effort ?? '';
  const effortValue: Effort | '' = (effortPick ?? savedEffort) as Effort | '';
  const effort: Effort | '' = effortValue && efforts.includes(effortValue) ? effortValue : '';
  // v0.1.66 (F021): chỉ agent trực việc (`agent:*`) dùng nhiệt độ / ngữ cảnh do Sếp đặt; bốn vai lõi chạy theo hồ sơ chuẩn
  // (nhiệt độ, ngữ cảnh cố định) và "bộ quy tắc" không vai nào dùng ⇒ hộp chỉ hiện các ô đó cho agent, ô ngữ cảnh mặc định
  // = 6000 (đúng mặc định máy chủ) để lưu hộp chỉ để đổi model không âm thầm đổi ngữ cảnh của agent.
  const isAgent = slot.agent_key.startsWith('agent:');
  const [temperature, setTemperature] = useState(String(slot.binding?.temperature ?? slot.standard?.temperature ?? 0.3));
  const [contextTokens, setContextTokens] = useState(String(slot.binding?.context_tokens ?? slot.standard?.context_tokens ?? DEFAULT_CONTEXT_TOKENS));
  const submit = () => {
    if (!modelId) return;
    set.mutate(
      {
        agentKey: slot.agent_key,
        body: {
          model_id: modelId,
          temperature: Number(temperature),
          context_tokens: Number(contextTokens),
          rule_codes: slot.binding?.rule_codes ?? [],
          effort: effort || null,
        },
      },
      { onSuccess: onClose },
    );
  };
  return (
    <Dialog
      open
      onClose={onClose}
      width={420}
      title={`Gán model cho ${slot.label}`}
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" loading={set.isPending} disabled={!modelId} onClick={submit}>
            Lưu
          </Button>
        </>
      }
    >
      <form
        className="dlg-fields"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        {models.length === 0 ? (
          <p className="muted-note">Chưa có model nào — thêm model ở thẻ nhà cung cấp trước.</p>
        ) : (
          <div className="gh-field">
            <label className="gh-field__label" htmlFor="apm-binding-model">
              Model
            </label>
            <select id="apm-binding-model" className="gh-input" value={modelId} onChange={(e) => setModelId(e.target.value)}>
              {models.map((m) => (
                <option key={m.id} value={m.id} disabled={!usable(m)}>
                  {`${m.provider_name} · ${m.model_name}${m.enabled ? '' : ' (đã tắt)'}${usable(m) ? '' : ' (chưa dùng cho dòng này)'}`}
                </option>
              ))}
            </select>
            {agyBlocked ? (
              <p className="muted-note" data-testid="binding-agy-blocked-note">
                {agyBlocked}
              </p>
            ) : null}
          </div>
        )}
        {efforts.length ? (
          <div className="gh-field">
            <label className="gh-field__label" htmlFor="apm-binding-effort">
              Mức suy nghĩ
            </label>
            <select id="apm-binding-effort" className="gh-input" title={EFFORT_HINT} value={effort} onChange={(e) => setEffortPick(e.target.value as Effort | '')}>
              <option value="">Theo hồ sơ chuẩn (tự chọn)</option>
              {efforts.map((x) => (
                <option key={x} value={x}>
                  {effortOptionText(x)}
                </option>
              ))}
            </select>
            <p className="muted-note">{EFFORT_HINT}</p>
          </div>
        ) : modelId ? (
          <p className="muted-note" data-testid="binding-no-effort">
            Model này không chỉnh mức suy nghĩ — hệ thống tự chọn.
          </p>
        ) : null}
        {isAgent ? (
          <details className="brain-advanced" data-testid="binding-edit-advanced">
            <summary>Nâng cao — nhiệt độ, ngữ cảnh</summary>
            <TextField label="Nhiệt độ (0–2)" type="number" step="0.05" min={0} max={2} value={temperature} onChange={(e) => setTemperature(e.target.value)} />
            <TextField label="Ngữ cảnh (token)" type="number" min={256} value={contextTokens} onChange={(e) => setContextTokens(e.target.value)} />
          </details>
        ) : (
          <p className="muted-note" data-testid="binding-core-fixed-note">
            Nhiệt độ và ngữ cảnh của vai này theo hồ sơ chuẩn (cố định) — chỉ agent trực việc chỉnh được.
          </p>
        )}
        {set.isError ? <BindingSaveError error={set.error} /> : null}
      </form>
    </Dialog>
  );
}

function BindingSaveError({ error }: { error: unknown }) {
  const v = bindingErrorView(error);
  return (
    <InlineError>
      {v.message}
      {v.detail ? (
        <details className="tech-detail">
          <summary>Chi tiết kỹ thuật</summary>
          <code>{v.detail}</code>
        </details>
      ) : null}
    </InlineError>
  );
}

/**
 * v0.1.55 (Nâng cao): nhiệt độ · ngữ cảnh của MỌI vai — mỗi vai một dòng chữ. v0.1.66: không còn thẻ "Tham số sàng lọc" riêng
 * (trùng thẻ này, và báo "chưa gán model" dù bảng ghi Chuẩn — F018) và không còn "bộ quy tắc" (không vai nào dùng — F021).
 */
function AdvancedParamsPanel() {
  const bindings = useBindings();
  const items = bindings.data?.items ?? [];
  return (
    <Panel title="Tham số theo vai" kicker="Nhiệt độ, ngữ cảnh — chỉ agent trực việc chỉnh được (hộp gán model › Nâng cao); vai lõi theo hồ sơ chuẩn" bodyClass="apm-params" label="Tham số theo vai">
      {bindings.isPending ? (
        <SkeletonLines rows={4} padding="0" />
      ) : bindings.isError ? (
        <CardError error={bindings.error} onRetry={() => void bindings.refetch()} retrying={bindings.isFetching} />
      ) : items.length === 0 ? (
        <EmptyState icon="ph ph-sliders-horizontal" title="Chưa có agent nào" />
      ) : (
        items.map((slot) => (
          // Chữ dài xuống dòng được (màn hẹp): không dùng ô một dòng `apm-param-row__box` (cắt bằng dấu …).
          <p className="muted-note" style={{ margin: '6px 0', overflowWrap: 'anywhere' }} key={slot.agent_key} data-testid={`binding-params-${slot.agent_key}`}>
            {bindingParamsText(slot)}
          </p>
        ))
      )}
    </Panel>
  );
}

function RateLimitsPanel() {
  const providers = useProviders();
  const rows = (providers.data ?? []).flatMap((p) => p.models.map((m) => ({ key: `${p.id}-${m.id}`, label: `${p.name} · ${m.model_name}`, quota: fmtQuota(m.used_today, m.daily_quota) })));
  return (
    <Panel title="Giới hạn gọi API" kicker="Đặt ở thẻ nhà cung cấp › Quản lý khoá và model" bodyClass="apm-rates" label="Giới hạn gọi API">
      {providers.isPending ? (
        <SkeletonLines rows={4} padding="8px 16px" />
      ) : providers.isError ? (
        <CardError error={providers.error} onRetry={() => void providers.refetch()} retrying={providers.isFetching} />
      ) : rows.length === 0 ? (
        <EmptyState icon="ph ph-gauge" title="Chưa có model nào" />
      ) : (
        rows.map((r) => (
          <div className="apm-rate-row" key={r.key}>
            <span className="apm-rate-row__label" title={r.label}>{r.label}</span>
            <span className="apm-rate-row__value mono">{r.quota}</span>
          </div>
        ))
      )}
    </Panel>
  );
}

function PriorityChainPanel({ canManage }: { canManage: boolean }) {
  const providers = useProviders();
  const reorder = useReorderChain();
  const [dragId, setDragId] = useState<string | null>(null);
  const list = [...(providers.data ?? [])].sort((a, b) => a.failover_rank - b.failover_rank);

  const move = (from: number, to: number) => {
    if (to < 0 || to >= list.length || from === to) return;
    const ids = list.map((p) => p.id);
    const [id] = ids.splice(from, 1);
    ids.splice(to, 0, id);
    reorder.mutate(ids);
  };

  const onDrop = (e: DragEvent, index: number) => {
    e.preventDefault();
    const id = e.dataTransfer.getData('text/plain') || dragId;
    setDragId(null);
    if (!id) return;
    const from = list.findIndex((p) => p.id === id);
    if (from < 0) return;
    move(from, index);
  };

  return (
    <Panel title="Chuỗi chuyển hướng" kicker="Kéo để đổi thứ tự — hoặc dùng nút lên/xuống" bodyClass="apm-chain" label="Chuỗi chuyển hướng nhà cung cấp">
      {providers.isPending ? (
        <SkeletonLines rows={3} padding="10px 16px" />
      ) : providers.isError ? (
        <CardError error={providers.error} onRetry={() => void providers.refetch()} retrying={providers.isFetching} />
      ) : list.length === 0 ? (
        <EmptyState icon="ph ph-arrows-down-up" title="Chưa có nhà cung cấp nào" />
      ) : (
        <ol className="apm-chain-list" aria-label="Chuỗi chuyển hướng nhà cung cấp">
          {list.map((p, i) => (
            <li
              key={p.id}
              className="apm-chain-row"
              draggable={canManage}
              onDragStart={(e) => {
                e.dataTransfer.setData('text/plain', p.id);
                e.dataTransfer.effectAllowed = 'move';
                setDragId(p.id);
              }}
              onDragOver={(e) => canManage && e.preventDefault()}
              onDrop={(e) => canManage && onDrop(e, i)}
            >
              {canManage ? <Icon name="ph ph-dots-six-vertical" size={15} color="var(--color-neutral-700)" /> : null}
              <span className="apm-chain-row__rank mono">{i + 1}</span>
              <div className="apm-chain-row__body">
                <div className="apm-chain-row__name">{p.name}</div>
                <div className="apm-chain-row__model mono">{chainModelText(p)}</div>
              </div>
              <span className="apm-chain-row__state" style={{ color: providerStatus(p).tone }}>
                {providerStatus(p).label}
              </span>
              {canManage ? (
                <span className="apm-chain-row__keys">
                  <Button variant="ghost" className="btn-22" icon="ph ph-caret-up" aria-label={`Đưa ${p.name} lên trước`} disabled={i === 0 || reorder.isPending} onClick={() => move(i, i - 1)} />
                  <Button
                    variant="ghost"
                    className="btn-22"
                    icon="ph ph-caret-down"
                    aria-label={`Đưa ${p.name} xuống sau`}
                    disabled={i === list.length - 1 || reorder.isPending}
                    onClick={() => move(i, i + 1)}
                  />
                </span>
              ) : null}
            </li>
          ))}
        </ol>
      )}
      {canManage && list.length > 1 ? (
        <div style={{ padding: '6px 16px 0' }}>
          <PinHint />
        </div>
      ) : null}
      {reorder.isError ? <InlineError detail={errorDetail(reorder.error)}>{errorText(reorder.error)}</InlineError> : null}
    </Panel>
  );
}

function FailoverRulesPanel() {
  const rules = useFailoverRules();
  return (
    <Panel title="Quy tắc chuyển hướng" kicker="Cố định, không cần chỉnh" bodyClass="apm-rules" label="Quy tắc chuyển hướng">
      {rules.isPending ? (
        <SkeletonLines rows={4} padding="8px 16px" />
      ) : rules.isError ? (
        <CardError error={rules.error} onRetry={() => void rules.refetch()} retrying={rules.isFetching} />
      ) : (
        rules.data.map((r) => (
          <div className="apm-rule-row" key={r.key}>
            <span className="apm-rule-row__key mono">{r.key}</span>
            <span className="apm-rule-row__val">{r.value}</span>
          </div>
        ))
      )}
    </Panel>
  );
}

/** Lựa chọn ô "Loại": loại thật hoặc mẫu dựng sẵn (v0.1.41, F-84 — `PROVIDER_PRESETS`). */
type AddChoice = 'gemini' | 'deepseek' | 'openai_compat' | (typeof PROVIDER_PRESETS)[number]['id'];

function isAddChoice(v: string): v is AddChoice {
  return v === 'gemini' || v === 'deepseek' || v === 'openai_compat' || Boolean(findPreset(v));
}

function AddProviderDialog({ onClose, initial = 'gemini' }: { onClose: () => void; initial?: AddChoice }) {
  const create = useCreateProvider();
  const initialPreset = findPreset(initial);
  const [choice, setChoice] = useState<AddChoice>(initial);
  const preset = findPreset(choice);
  const kind: ProviderKind = preset ? preset.kind : (choice as ProviderKind);
  const [name, setName] = useState(initialPreset?.name ?? '');
  const [endpoint, setEndpoint] = useState(initialPreset?.endpoint ?? '');
  const [keys, setKeys] = useState('');
  const [models, setModels] = useState(initialPreset?.modelHint ?? '');
  const onChoice = (next: AddChoice) => {
    const prev = findPreset(choice);
    const p = findPreset(next);
    setChoice(next);
    // Mẫu điền sẵn Tên + Endpoint + model gợi ý THẬT (vẫn sửa được — để trống thì việc nền báo "Chưa chọn model");
    // rời mẫu thì xoá phần mẫu đã điền nếu Sếp chưa sửa.
    if (p) {
      setName(p.name);
      setEndpoint(p.endpoint);
      if (!models.trim() || (prev && models === prev.modelHint)) setModels(p.modelHint);
    } else if (prev) {
      if (name === prev.name) setName('');
      if (endpoint === prev.endpoint) setEndpoint('');
      if (models === prev.modelHint) setModels('');
    }
  };
  const submit = () => {
    const keyList = keys.split('\n').map((k) => k.trim()).filter(Boolean);
    if (!name.trim() || !keyList.length || (kind === 'openai_compat' && !endpoint.trim())) return;
    create.mutate(
      {
        kind,
        name: name.trim(),
        endpoint: kind === 'openai_compat' ? endpoint.trim() || undefined : undefined,
        keys: keyList,
        models: models
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean),
      },
      { onSuccess: onClose },
    );
  };
  return (
    <Dialog
      open
      onClose={onClose}
      width={440}
      title="Thêm nhà cung cấp"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" loading={create.isPending} onClick={submit}>
            Thêm
          </Button>
        </>
      }
    >
      <form
        className="dlg-fields"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <SelectField
          label="Loại"
          value={choice}
          onChange={(e) => onChoice(e.target.value as AddChoice)}
          options={[
            { value: 'gemini', label: 'Gemini API' },
            { value: 'deepseek', label: 'DeepSeek API' },
            { value: 'openai_compat', label: 'API tương thích OpenAI' },
            ...PROVIDER_PRESETS.map((p) => ({ value: p.id, label: p.label })),
          ]}
        />
        {preset ? (
          <p className="muted-note" data-testid="provider-preset-hint">
            {preset.keyHint}
          </p>
        ) : null}
        <TextField label="Tên hiển thị" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
        {kind === 'openai_compat' ? <TextField label="Địa chỉ gọi (Endpoint)" value={endpoint} onChange={(e) => setEndpoint(e.target.value)} placeholder="https://…" /> : null}
        <div className="gh-field">
          <label className="gh-field__label" htmlFor="apm-new-keys">
            Khoá API (mỗi dòng một khoá)
          </label>
          <textarea id="apm-new-keys" className="gh-input" rows={2} value={keys} onChange={(e) => setKeys(e.target.value)} />
        </div>
        <TextField
          label="Model ban đầu (tuỳ chọn, cách nhau dấu phẩy)"
          value={models}
          onChange={(e) => setModels(e.target.value)}
          placeholder={preset ? preset.modelHint : 'gemini-2.5-flash'}
        />
        {preset ? <p className="muted-note">Gợi ý model: <span className="mono">{preset.modelHint}</span></p> : null}
        <PinHint />
        {create.isError ? <InlineError detail={errorDetail(create.error)}>{errorText(create.error)}</InlineError> : null}
      </form>
    </Dialog>
  );
}

function AddKeyDialog({ provider, onClose }: { provider: Provider; onClose: () => void }) {
  const addKey = useAddProviderKey();
  const addModel = useAddModel();
  const [secret, setSecret] = useState('');
  const [modelName, setModelName] = useState('');
  const submit = () => {
    if (!secret.trim() || secret.trim().length < 8) return;
    addKey.mutate(
      { id: provider.id, secret: secret.trim() },
      {
        onSuccess: () => {
          if (modelName.trim()) addModel.mutate({ id: provider.id, body: { model_name: modelName.trim() } });
          onClose();
        },
      },
    );
  };
  return (
    <Dialog
      open
      onClose={onClose}
      width={400}
      title={`Thêm khoá cho ${provider.name}`}
      kicker="Chỉ hiện 4 ký tự cuối sau khi lưu"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" loading={addKey.isPending} disabled={secret.trim().length < 8} onClick={submit}>
            Thêm khoá
          </Button>
        </>
      }
    >
      <form
        className="dlg-fields"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <TextField label="Khoá API mới" value={secret} onChange={(e) => setSecret(e.target.value)} revealable autoComplete="off" spellCheck={false} />
        <TextField label="Model đi kèm (tuỳ chọn)" value={modelName} onChange={(e) => setModelName(e.target.value)} placeholder="gemini-2.5-flash" />
        <PinHint />
        {addKey.isError ? <InlineError detail={errorDetail(addKey.error)}>{errorText(addKey.error)}</InlineError> : null}
      </form>
    </Dialog>
  );
}
