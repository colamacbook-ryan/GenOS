import { useEffect, useState } from 'react';
import type { AiCost, AiCostModel } from '@gen-harness/contracts';
import { Button, TextField } from '@gen-harness/ui';
import { errorDetail, errorText } from '../../lib/errorText';
import { fmtInt } from '../../lib/format';
import { useCan } from '../../lib/permissions';
import { parseVnd, priceStr } from './moneyInput';
import { toast } from '../../lib/toast';
import { CardError, InlineError, Panel, SkeletonLines } from '../common';
import { useAiCost, useSetAiBudget, useSetModelPrice } from './queries';

const PRICE_SOURCE_LABEL: Record<string, string> = {
  owner: 'Sếp nhập',
  subscription: 'Trả theo gói',
  none: 'Chưa có giá',
};

/** Hiện giá chỉ đọc: số nguyên có phân cách nghìn, có phần lẻ thì giữ (dấu phẩy). */
const fmtPrice = (n: number) => (Number.isInteger(Math.round(n * 100) / 100) ? fmtInt(n) : priceStr(n));

/** So giá ở cùng độ chính xác lưu trữ (numeric(14,2)). */
const samePrice = (a: number | null, b: number | null) => (a == null || b == null ? a === b : Math.round(a * 100) === Math.round(b * 100));

/**
 * v0.1.41 (F-84): "Chi phí & trần ngân sách" ở Bộ não AI — trần chi phí mỗi ngày (₫, trống = không giới hạn) và bảng giá
 * theo model (₫ cho 1 triệu token vào/ra) sửa tại chỗ. Nguồn CLI trả theo gói ⇒ "Trả theo gói — 0 ₫", không sửa. Chỉ
 * vai trò có `system.manage` mới sửa; vượt trần ⇒ máy chủ mở sự cố `ai.budget_exceeded` (chuông + "Cần Sếp xử lý").
 */
export function AiBudgetCard() {
  const canRead = useCan('system.read');
  const q = useAiCost(canRead);
  if (!canRead) return null;
  return (
    <Panel title="Chi phí & trần ngân sách" genTarget="system.ai_cost" kicker="Giá theo model · trần chi phí mỗi ngày" label="Chi phí & trần ngân sách" bodyClass="ai-budget">
      {q.isPending ? (
        <SkeletonLines rows={4} padding="0" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : (
        <AiBudgetBody data={q.data} />
      )}
    </Panel>
  );
}

function AiBudgetBody({ data }: { data: AiCost }) {
  const canManage = useCan('system.manage');
  const setBudget = useSetAiBudget();
  const [budget, setBudgetText] = useState(priceStr(data.budget_vnd));
  useEffect(() => setBudgetText(priceStr(data.budget_vnd)), [data.budget_vnd]);
  const parsed = parseVnd(budget);
  const changed = parsed !== undefined && parsed !== (data.budget_vnd ?? null);
  const models = Array.isArray(data.models) ? data.models : [];

  return (
    <>
      <form
        className="ai-budget__form"
        onSubmit={(e) => {
          e.preventDefault();
          if (!canManage || parsed === undefined || !changed) return;
          setBudget.mutate(
            { daily_budget_vnd: parsed },
            { onSuccess: () => toast(parsed == null ? 'Đã bỏ trần chi phí — không giới hạn' : `Đã đặt trần ${fmtInt(parsed)} ₫ mỗi ngày`) },
          );
        }}
      >
        <TextField
          label="Trần chi phí mỗi ngày (₫)"
          inputMode="numeric"
          value={budget}
          disabled={!canManage}
          placeholder="Trống = không giới hạn"
          error={parsed === undefined ? 'Nhập số tiền nguyên (₫), không âm — để trống nếu không giới hạn' : null}
          onChange={(e) => setBudgetText(e.target.value)}
        />
        {canManage ? (
          <Button variant="secondary" type="submit" className="btn-27" disabled={!changed} loading={setBudget.isPending}>
            Lưu trần
          </Button>
        ) : null}
      </form>
      <p className="muted-note">
        {data.budget_vnd == null ? 'Chưa đặt trần — không giới hạn.' : `Trần hiện tại ${fmtInt(data.budget_vnd)} ₫ mỗi ngày.`} Vượt trần thì
        Gen báo chuông, không tự dừng.
      </p>
      {setBudget.isError ? <InlineError detail={errorDetail(setBudget.error)}>{errorText(setBudget.error)}</InlineError> : null}

      {models.length === 0 ? (
        <p className="muted-note">Chưa có model nào.</p>
      ) : (
        <div className="ai-budget__table-wrap">
          <table className="ai-budget__table">
            <thead>
              <tr>
                <th>Nhà cung cấp</th>
                <th>Model</th>
                <th>₫/1M token vào</th>
                <th>₫/1M token ra</th>
                <th>Nguồn giá</th>
                {canManage ? <th aria-label="Thao tác" /> : null}
              </tr>
            </thead>
            <tbody>
              {models.map((m) => (
                <PriceRow key={m.model_id} m={m} canManage={canManage} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!canManage ? <p className="muted-note">Chỉ vai trò quản trị hệ thống sửa được trần và giá.</p> : null}
    </>
  );
}

function PriceRow({ m, canManage }: { m: AiCostModel; canManage: boolean }) {
  const setPrice = useSetModelPrice();
  const [pin, setPin] = useState(priceStr(m.in_vnd_per_mtok));
  const [pout, setPout] = useState(priceStr(m.out_vnd_per_mtok));
  useEffect(() => {
    setPin(priceStr(m.in_vnd_per_mtok));
    setPout(priceStr(m.out_vnd_per_mtok));
  }, [m.in_vnd_per_mtok, m.out_vnd_per_mtok]);
  const subscription = m.price_source === 'subscription';
  const source = PRICE_SOURCE_LABEL[m.price_source] ?? String(m.price_source);
  if (subscription) {
    return (
      <tr data-testid={`ai-price-${m.model_id}`}>
        <td>{m.provider_name}</td>
        <td className="mono">{m.model_name}</td>
        <td colSpan={2} className="ai-budget__sub">
          Trả theo gói — 0 ₫
        </td>
        <td>{source}</td>
        {canManage ? <td /> : null}
      </tr>
    );
  }
  const vin = parseVnd(pin, 2);
  const vout = parseVnd(pout, 2);
  const bad = vin === undefined || vout === undefined;
  const changed = !bad && (!samePrice(vin, m.in_vnd_per_mtok ?? null) || !samePrice(vout, m.out_vnd_per_mtok ?? null));
  return (
    <tr data-testid={`ai-price-${m.model_id}`}>
      <td>{m.provider_name}</td>
      <td className="mono">{m.model_name}</td>
      <td>
        {canManage ? (
          <input
            className="gh-input ai-budget__price"
            inputMode="decimal"
            aria-label={`Giá token vào của ${m.model_name} (₫/1M token)`}
            value={pin}
            onChange={(e) => setPin(e.target.value)}
          />
        ) : (
          <span className="mono">{m.in_vnd_per_mtok == null ? '—' : `${fmtPrice(m.in_vnd_per_mtok)} ₫`}</span>
        )}
      </td>
      <td>
        {canManage ? (
          <input
            className="gh-input ai-budget__price"
            inputMode="decimal"
            aria-label={`Giá token ra của ${m.model_name} (₫/1M token)`}
            value={pout}
            onChange={(e) => setPout(e.target.value)}
          />
        ) : (
          <span className="mono">{m.out_vnd_per_mtok == null ? '—' : `${fmtPrice(m.out_vnd_per_mtok)} ₫`}</span>
        )}
      </td>
      <td>
        <span className="ai-budget__source" data-source={m.price_source}>
          {source}
        </span>
      </td>
      {canManage ? (
        <td>
          <Button
            variant="secondary"
            className="btn-24"
            aria-label={`Lưu giá ${m.model_name}`}
            disabled={!changed}
            loading={setPrice.isPending}
            onClick={() =>
              setPrice.mutate(
                { modelId: m.model_id, body: { in_vnd_per_mtok: vin ?? null, out_vnd_per_mtok: vout ?? null } },
                { onSuccess: () => toast(`Đã lưu giá ${m.model_name}`) },
              )
            }
          >
            Lưu
          </Button>
          {bad ? <InlineError>Giá là số tiền ₫ không âm; phần lẻ tối đa 2 số, dùng dấu phẩy (vd 0,5).</InlineError> : null}
          {setPrice.isError ? <InlineError detail={errorDetail(setPrice.error)}>{errorText(setPrice.error)}</InlineError> : null}
        </td>
      ) : null}
    </tr>
  );
}
