import { useEffect, useMemo, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import type { Effort, ModelGroup, Provider, ProviderModel, ProviderTestResult } from '@gen-harness/contracts';
import { Button } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { errorText } from '../../lib/errorText';
import { qk2 } from '../../lib/dataQueries';
import { queryClient } from '../../lib/queryClient';
import { toast } from '../../lib/toast';
import { PIN_ROUTE_CHANGE_TITLE } from '../common';
import { EFFORT_HINT, choiceText, effortOptionText, currentChoice, isCliKind, modelOptionText, pickEffort, pickerGroups, technicalDetail } from './apiModel';

/**
 * v0.1.31 (Boss 01/10: "không thấy model và nhóm model nào để chọn") — ô chọn model THEO NHÓM (Gemini, Claude qua
 * Antigravity, Claude…) kèm gợi ý nhanh/rẻ hay mạnh. "Dùng model này" = model mặc định của nguồn; nguồn CLI được gọi thử
 * thật trước khi lưu (máy chủ trả 422 khi CLI không nhận → hiện câu lỗi + "Chi tiết kỹ thuật", không lưu).
 *
 * v0.1.32 (Boss 01/10: "high" là mức suy nghĩ, không phải tên model) — ô model chỉ có TÊN MODEL GỐC; "Mức suy nghĩ"
 * (Thấp / Vừa / Cao…) là ô riêng, chỉ hiện các mức model đó nhận. Danh sách không bao giờ thu gọn còn model đã lưu.
 */
export function ModelPicker({ provider: p, test }: { provider: Provider; test: ProviderTestResult | null | undefined }) {
  const current = currentChoice(p);
  // v0.1.66 (F116): ô chọn KHÔNG biến mất khi Kiểm tra đỏ / chưa kiểm — lấy danh mục dự phòng (chưa xác minh) + model đã lưu.
  const { groups: offered, live } = useMemo(() => pickerGroups(test), [test]);
  const groups = useMemo(() => withSaved(offered, p.models), [offered, p.models]);
  const all = useMemo(() => groups.flatMap((g) => g.models), [groups]);
  const probe = test?.probe_model && all.some((m) => m.id === test.probe_model) ? test.probe_model : null;
  const initial = (current && all.some((m) => m.id === current.model) ? current.model : null) ?? probe ?? all[0]?.id ?? '';
  const [model, setModel] = useState(initial);
  const chosen = all.find((m) => m.id === model);
  const [effort, setEffort] = useState<Effort | null>(() => pickEffort(chosen, model === current?.model ? current?.effort : test?.probe_effort));
  useEffect(() => {
    if (!all.some((m) => m.id === model)) setModel(initial);
  }, [all, model, initial]);
  useEffect(() => {
    // Model mới không nhận mức đang chọn → về mức hợp lệ của model đó.
    const effs = chosen?.efforts ?? [];
    if (effort && !effs.includes(effort)) setEffort(pickEffort(chosen, null));
    else if (!effort && effs.length) setEffort(pickEffort(chosen, model === current?.model ? current?.effort : null));
  }, [chosen, effort, model, current?.model, current?.effort]);
  const add = useMutation({
    mutationFn: () => api.providers.addModel(p.id, { model_name: model, make_default: true, effort }),
    onSuccess: (next) => {
      queryClient.setQueryData<Provider[]>(qk2.providers, (old) => old?.map((x) => (x.id === next.id ? next : x)));
      void queryClient.invalidateQueries({ queryKey: qk2.providers });
      const what = choiceText(model, effort);
      toast(isCliKind(p.kind) ? `Đã gọi thử OK và chọn ${what} cho ${p.name}.` : `Đã chọn ${what} cho ${p.name}.`);
    },
  });
  if (!groups.length) return null;
  // v0.1.66 (F133): "Đang dùng" chỉ khi Sếp ĐÃ chốt model cho nguồn này ("Dùng model này" = `is_default`); chưa chốt thì mỗi
  // vai tự chọn model + mức theo hồ sơ chuẩn — không nói "Đang dùng … Cao" cho model chỉ là dòng đầu danh sách.
  const pinned = p.models.find((m) => m.is_default) ?? null;
  const isCurrent = !!pinned && model === pinned.model_name && (effort ?? null) === (pinned.effort ?? null);
  const saved = p.models.map((m) => choiceText(m.model_name, m.effort)).join(', ');
  const efforts = chosen?.efforts ?? [];
  const tech = add.isError ? technicalDetail(add.error) : null;
  return (
    <div className="prov-model" data-testid={`model-picker-${p.id}`}>
      <select
        className="mini-select"
        aria-label={`Model cho ${p.name}`}
        value={model}
        onChange={(e) => {
          setModel(e.target.value);
          add.reset();
        }}
      >
        {groups.map((g) => (
          <optgroup key={g.label} label={g.label}>
            {g.models.map((m) => (
              <option key={m.id} value={m.id} title={m.source_ref ? `${m.id} — nguồn: ${m.source_ref}` : m.id}>
                {modelOptionText(m)}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      {efforts.length ? (
        <label className="prov-model__effort">
          <span className="muted-note">Mức suy nghĩ</span>
        <select
          className="mini-select"
          aria-label={`Mức suy nghĩ (effort) cho ${p.name}`}
          title={EFFORT_HINT}
          value={effort ?? ''}
          onChange={(e) => {
            setEffort((e.target.value || null) as Effort | null);
            add.reset();
          }}
        >
          {efforts.map((x) => (
            <option key={x} value={x}>
              {effortOptionText(x)}
            </option>
          ))}
        </select>
        </label>
      ) : null}
      <Button variant="secondary" size="sm" loading={add.isPending} disabled={!model || isCurrent} title={PIN_ROUTE_CHANGE_TITLE} onClick={() => add.mutate()}>
        {isCurrent ? 'Đang dùng' : 'Dùng model này'}
      </Button>
      <span className="muted-note">
        {add.isPending && isCliKind(p.kind)
          ? 'Đang gọi thử model này…'
          : pinned
            ? `Đang dùng ${choiceText(pinned.model_name, pinned.effort ?? null)}.`
            : p.models.length
              ? `Chưa chốt model cho nguồn này — mỗi vai tự chọn model và mức suy nghĩ theo hồ sơ chuẩn (xem bảng "Gán model cho từng agent"). Đã lưu: ${saved}.`
              : 'Chưa lưu model nào — chọn rồi bấm "Dùng model này".'}
        {chosen && chosen.label !== chosen.id ? ` Mã: ${chosen.id}.` : ''}
        {efforts.length ? ` Mức suy nghĩ: ${EFFORT_HINT}.` : isCliKind(p.kind) && chosen ? ' Model này không chỉnh mức suy nghĩ — CLI tự chọn.' : ''}
        {chosen?.verified === false
          ? ' Chưa xác minh bằng CLI — sẽ gọi thử thật trước khi lưu.'
          : test?.models_source === 'catalog' && p.kind === 'claude_code_cli'
            ? ' Tên gọi tắt của Claude Code, tự trỏ tới bản mới nhất gói Claude cho dùng.'
            : ''}
      </span>
      {!live ? (
        <span className="muted-note" role="note" data-testid={`model-picker-unverified-${p.id}`}>
          Kiểm tra kết nối chưa xanh nên danh sách này là danh mục dự phòng và model đã lưu (chưa xác minh) — vẫn chọn được; bấm
          Kiểm tra kết nối để lấy danh sách thật.
        </span>
      ) : null}
      {add.isError ? (
        <div className="prov-model__err" role="alert">
          {errorText(add.error)}
          {tech ? (
            <details className="tech-detail">
              <summary>Chi tiết kỹ thuật</summary>
              <code>{tech}</code>
            </details>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Model đã lưu mà danh sách không có (vd máy chủ cũ, hoặc Kiểm tra đỏ) vẫn hiện — không bao giờ mất lựa chọn đang dùng.
 * Review v0.1.32: mang theo MỨC đã lưu (đã gọi thử OK lúc lưu) để nút hiện "Đang dùng" và bấm lại không ghi đè mức thành
 * rỗng. v0.1.66: mọi model đã lưu (không chỉ model đang dùng) được thêm vào nhóm "Đã lưu" khi danh sách thiếu.
 */
function withSaved(groups: ModelGroup[], models: ProviderModel[]): ModelGroup[] {
  const usable = models.filter((m) => !/embed/i.test(m.model_name));
  if (!usable.length) return groups;
  const cur = usable.find((m) => m.is_default) ?? usable[0];
  const eff = cur.effort ?? null;
  let out = groups;
  if (eff && groups.some((g) => g.models.some((m) => m.id === cur.model_name)))
    out = groups.map((g) => ({
      ...g,
      models: g.models.map((m) => (m.id === cur.model_name && !(m.efforts ?? []).includes(eff) ? { ...m, efforts: sortEfforts([...(m.efforts ?? []), eff]) } : m)),
    }));
  const known = new Set(out.flatMap((g) => g.models.map((m) => m.id)));
  const extra = usable.filter((m) => !known.has(m.model_name));
  if (!extra.length) return out;
  return [
    ...out,
    {
      label: 'Đã lưu',
      models: extra.map((m) => ({
        id: m.model_name,
        label: m.model_name,
        group: 'Đã lưu',
        tier: 'balanced' as const,
        hint: '',
        source: 'saved' as const,
        efforts: m.effort ? [m.effort] : [],
        default_effort: m.effort ?? null,
      })),
    },
  ];
}

const EFFORT_ORDER: Effort[] = ['low', 'medium', 'high', 'xhigh', 'max'];
function sortEfforts(e: Effort[]): Effort[] {
  return EFFORT_ORDER.filter((x) => e.includes(x));
}
