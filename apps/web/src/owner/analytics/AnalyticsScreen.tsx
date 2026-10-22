/**
 * v0.1.59 (G3) — `/owner/phan-tich`: màn PHÂN TÍCH (7 / 30 ngày). Dữ liệu: MỘT truy vấn `GET /owner/insights?range=` (9 chỉ số tính thẳng
 * từ dữ liệu đang có, không gọi model). Hàng 1: 4 tile (Tin nhận · Người mới liên hệ · Cơ hội mới · Trả lời nhanh). Hàng 2: "Tin nhận theo
 * ngày" (cột kỳ này + đường kỳ trước) và "Khách hỏi gì" (top 5). Hàng 3: "Phễu cơ hội" và "Chi phí trợ lý Gen theo ngày". Hàng 4: "Bảng
 * số liệu" đủ 9 chỉ số (cũng là phương án cho trình đọc màn hình). Điện thoại: chọn kỳ → tile 2×2 → từng biểu đồ toàn bề ngang → bảng xếp chồng.
 *
 * Trạng thái dữ liệu: `empty` ⇒ MỘT FirstRunCard (vẫn hiện tile Chi phí nếu khác 0) · `collecting` ⇒ ẩn mọi chênh lệch, câu "Đang gom dữ liệu — còn N
 * ngày nữa có so sánh", không vẽ kỳ trước · `ok`. Một chỉ số `state: 'error'` chỉ báo lỗi ở ô của nó. Chênh lệch null ⇒ "Mới".
 * KHÔNG bao giờ "0 ₫" khi còn lượt chưa đặt giá: tổng 0 mà `unpriced_calls > 0` ⇒ "Chưa tính được".
 */
/* eslint-disable react-refresh/only-export-components */
import { useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { InsightKey, InsightMetric, InsightPoint, InsightRange, OwnerInsights } from '@gen-harness/contracts';
import { Badge, Card, DataTable, EmptyState, HBar, Icon, Segmented, StatTile, TrendChart, type DataColumn } from '@gen-harness/ui';
import { DEFAULT_TZ, fmtHM } from '../../lib/format';
import { useOrgTimezone } from '../../lib/permissions';
import { OWNER_PATHS, asText, moneyText } from '../ownerModel';
import { FirstRunCard, OwnerError } from '../parts';
import { useOwnerInsights } from '../queries';
import './analytics.css';

const NUM = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 1 });
const INT = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 0 });
const CELL = 'kit-m2 kit-tb4 kit-c3';
export const COST_UNKNOWN_TEXT = 'Chưa tính được';
/** Chi phí: nơi đặt giá cho các lượt chưa có giá (Console › API & Model). */
export const PRICE_SETTINGS_PATH = '/api';

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

// ── mô hình thuần ─────────────────────────────────────────────────────────────────────────────────────────────────────

/** Chỉ số theo khoá; thiếu / lạ ⇒ không có. */
export function metricMap(data: Pick<OwnerInsights, 'metrics'> | null | undefined): Map<InsightKey, InsightMetric> {
  const m = new Map<InsightKey, InsightMetric>();
  for (const x of Array.isArray(data?.metrics) ? data.metrics : []) if (x && typeof x === 'object' && asText(x.key)) m.set(x.key, x);
  return m;
}

/** Tổng chi phí là 0 nhưng còn lượt chưa đặt giá ⇒ chưa tính được (không phải 0 ₫). */
export function costUnknown(totalVnd: unknown, unpricedCalls: unknown): boolean {
  return num(totalVnd) <= 0 && num(unpricedCalls) > 0;
}

/** "184.000 ₫" / "Chưa tính được" (tổng 0 mà còn lượt chưa đặt giá). */
export function costText(totalVnd: unknown, unpricedCalls: unknown): string {
  return costUnknown(totalVnd, unpricedCalls) ? COST_UNKNOWN_TEXT : moneyText(num(totalVnd));
}

/** "05/10" từ "2026-10-05"; chuỗi lạ ⇒ chính nó. */
export function dayLabel(d: unknown): string {
  const s = asText(d);
  return /^\d{4}-\d{2}-\d{2}/.test(s) ? `${s.slice(8, 10)}/${s.slice(5, 7)}` : s;
}

const values = (pts: readonly InsightPoint[] | undefined): number[] => (Array.isArray(pts) ? pts.map((p) => num(p?.v)) : []);

/** Giá trị một chỉ số ở dạng chữ (kỳ này hoặc kỳ trước). `ai` để áp luật "Chưa tính được" cho chi phí. */
export function formatMetric(m: InsightMetric, which: 'cur' | 'prev', ai?: { unpriced_calls?: unknown }): string {
  const v = num(which === 'cur' ? m.total : m.prev_total);
  if (m.unit === 'vnd') return costText(v, ai?.unpriced_calls);
  if (m.unit === 'pct') return `${NUM.format(v)}%`;
  if (m.key === 'opps_won' && which === 'cur') return `${INT.format(v)} · ${moneyText(num(m.value_vnd))}`;
  return INT.format(v);
}

export interface DeltaView {
  /** Chữ trong huy hiệu: "Tăng 12%" / "Giảm 5 điểm" / "Mới" / "Không đổi". */
  text: string;
  /** ok = đổi theo chiều TỐT; warn = chiều xấu; neutral = Mới / không đổi. */
  tone: 'ok' | 'warn' | 'neutral';
  direction: 'up' | 'down' | null;
}

/**
 * Chênh lệch kỳ này so với kỳ trước. Chỉ số phần trăm (trả lời nhanh) nói bằng ĐIỂM; còn lại bằng %. `delta_pct` null ⇒ "Mới".
 * Màu theo NGHĨA (`good`): chi phí tăng ⇒ warn, người mới tăng ⇒ ok.
 */
export function deltaView(m: InsightMetric): DeltaView {
  if (m.delta_pct === null || m.delta_pct === undefined || !Number.isFinite(m.delta_pct)) return { text: 'Mới', tone: 'neutral', direction: null };
  const diff = m.unit === 'pct' ? num(m.total) - num(m.prev_total) : m.delta_pct;
  if (Math.abs(diff) < 0.05) return { text: 'Không đổi', tone: 'neutral', direction: null };
  const up = diff > 0;
  const good = m.good === 'down' ? !up : up;
  const amount = m.unit === 'pct' ? `${NUM.format(Math.abs(diff))} điểm` : `${NUM.format(Math.abs(diff))}%`;
  return { text: `${up ? 'Tăng' : 'Giảm'} ${amount}`, tone: good ? 'ok' : 'warn', direction: up ? 'up' : 'down' };
}

function DeltaBadge({ view }: { view: DeltaView }) {
  return (
    <Badge kind="status" tone={view.tone}>
      {view.direction ? <Icon name={view.direction === 'up' ? 'ph ph-arrow-up' : 'ph ph-arrow-down'} size={12} /> : null}
      {view.text}
    </Badge>
  );
}

/** Dòng dưới nút chọn kỳ. */
export function statusLine(d: Pick<OwnerInsights, 'state' | 'days_until_compare' | 'generated_at'>, days: number, tz: string = DEFAULT_TZ): string {
  const at = Number.isFinite(Date.parse(asText(d.generated_at))) ? `Cập nhật ${fmtHM(d.generated_at, tz)}` : '';
  const head =
    d.state === 'collecting'
      ? typeof d.days_until_compare === 'number' && d.days_until_compare > 0
        ? `Đang gom dữ liệu — còn ${d.days_until_compare} ngày nữa có so sánh`
        : 'Đang gom dữ liệu — chưa đủ ngày để so sánh'
      : d.state === 'empty'
        ? ''
        : `So với ${days} ngày trước đó`;
  return [head, at].filter(Boolean).join(' · ');
}

const RANGE_DAYS: Record<InsightRange, number> = { week: 7, month: 30 };

// ── mảnh dùng chung ───────────────────────────────────────────────────────────────────────────────────────────────────

function BlockFail({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="an-fail" role="alert" data-testid="an-block-fail">
      <p className="an-fail__text">Chưa tính được khối này. Sếp thử lại sau ít phút.</p>
      <button type="button" className="kit-btn kit-btn--secondary" onClick={onRetry}>
        Tải lại
      </button>
    </div>
  );
}

/** Ô báo lỗi riêng của MỘT chỉ số (phần còn lại của màn vẫn chạy). */
const METRIC_ERROR_NOTE = 'Chưa tính được số này lúc này — Sếp thử lại sau ít phút';

// ── Hàng 1: tile ─────────────────────────────────────────────────────────────────────────────────────────────────────

interface TileSpec {
  key: InsightKey;
  label: string;
  note: string;
}

const TILES: readonly TileSpec[] = [
  { key: 'messages_in', label: 'Tin nhận', note: 'Tin khách gửi về' },
  { key: 'new_contacts', label: 'Người mới liên hệ', note: 'Tính cả người chưa phân loại' },
  { key: 'opps_new', label: 'Cơ hội mới', note: '' },
  { key: 'reply_fast_pct', label: 'Trả lời nhanh', note: 'Trong 15 phút, tin nhắn riêng' },
];

function MetricTile({ spec, m, won, collecting, days }: { spec: TileSpec; m: InsightMetric | undefined; won: InsightMetric | undefined; collecting: boolean; days: number }) {
  const testId = `an-tile-${spec.key}`;
  if (!m || m.state === 'error') {
    return <StatTile className={CELL} label={spec.label} value="—" note={METRIC_ERROR_NOTE} data-testid={testId} />;
  }
  const compare = `so với ${days} ngày trước`;
  const spark = values(m.series);
  let note: ReactNode = spec.note;
  if (spec.key === 'opps_new') note = won && won.state !== 'error' ? `Thắng ${INT.format(num(won.total))} · ${moneyText(num(won.value_vnd))}` : '';
  // Chỉ số phần trăm: chênh lệch nói bằng ĐIỂM (không phải %), nên đi cùng ghi chú thay vì huy hiệu % của tile.
  const view = collecting ? null : deltaView(m);
  if (m.unit === 'pct' && view) {
    note = (
      <span className="an-pct-note">
        <DeltaBadge view={view} />
        <span>{view.text === 'Mới' ? 'chưa có kỳ trước để so sánh' : compare}</span>
        <span>{spec.note}</span>
      </span>
    );
  }
  return (
    <StatTile
      className={CELL}
      label={spec.label}
      value={m.unit === 'pct' ? `${NUM.format(num(m.total))}%` : num(m.total)}
      delta={view && m.unit !== 'pct' ? { pct: m.delta_pct, good: m.good, compare } : undefined}
      spark={spark.length > 0 ? spark : undefined}
      note={note}
      data-testid={testId}
    />
  );
}

function CostTile({ m, ai }: { m: InsightMetric | undefined; ai: OwnerInsights['ai_cost'] | undefined }) {
  if (!m || m.state === 'error') return <StatTile className={CELL} label="Chi phí trợ lý Gen" value="—" note={METRIC_ERROR_NOTE} data-testid="an-tile-cost" />;
  const unknown = costUnknown(m.total, ai?.unpriced_calls);
  return (
    <StatTile
      className={CELL}
      label="Chi phí trợ lý Gen"
      value={unknown ? COST_UNKNOWN_TEXT : moneyText(num(m.total))}
      note={num(ai?.unpriced_calls) > 0 ? `${INT.format(num(ai?.unpriced_calls))} lượt chưa đặt giá` : 'Tổng trong kỳ này'}
      data-testid="an-tile-cost"
    />
  );
}

// ── Hàng 2–3: biểu đồ ───────────────────────────────────────────────────────────────────────────────────────────────

function MessagesChart({ m, collecting, days }: { m: InsightMetric | undefined; collecting: boolean; days: number }) {
  const title = 'Tin nhận theo ngày';
  if (!m || m.state === 'error') {
    return (
      <Card title={title} className="kit-c8" data-testid="an-chart-messages">
        <p className="an-fail__text">{METRIC_ERROR_NOTE}</p>
      </Card>
    );
  }
  const cur = values(m.series);
  const prev = collecting ? [] : values(m.prev_series);
  const labels = (Array.isArray(m.series) ? m.series : []).map((p) => dayLabel(p?.d));
  const series = [{ name: `${days} ngày này`, values: cur }, ...(prev.length > 0 ? [{ name: `${days} ngày trước`, values: prev }] : [])];
  const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
  const aria = `Tin nhận theo ngày: ${INT.format(sum(cur))} tin trong ${days} ngày${prev.length > 0 ? `, kỳ trước ${INT.format(sum(prev))} tin` : ''}`;
  return (
    <Card title={title} kicker={prev.length > 0 ? 'Cột là kỳ này, đường là kỳ trước' : 'Cột là kỳ này'} className="kit-c8" data-testid="an-chart-messages">
      <TrendChart kind="bar" series={series} labels={labels} yFormat={(v) => INT.format(v)} ariaLabel={aria} data-testid="an-trend-messages" />
    </Card>
  );
}

function AskedChart({ data, onRetry }: { data: OwnerInsights; onRetry: () => void }) {
  const title = 'Khách hỏi gì';
  const rows = data.breakdown?.event_types;
  if (!Array.isArray(rows)) {
    return (
      <Card title={title} state="error" className="kit-c4" data-testid="an-chart-asked">
        <BlockFail onRetry={onRetry} />
      </Card>
    );
  }
  if (rows.length === 0) {
    return (
      <Card title={title} className="kit-c4" data-testid="an-chart-asked">
        <EmptyState
          icon="ph ph-chat-text"
          title="Chưa có tin nào để phân loại."
          action={
            <Link to={OWNER_PATHS.feed} className="kit-btn kit-btn--secondary">
              Xem Thông tin
            </Link>
          }
        />
      </Card>
    );
  }
  const top = rows.slice(0, 5);
  return (
    <Card title={title} kicker="5 loại nhiều nhất" className="kit-c4" data-testid="an-chart-asked">
      <HBar ariaLabel="Khách hỏi gì" items={top.map((r) => ({ label: asText(r.label) || asText(r.event_type), value: num(r.units) }))} />
    </Card>
  );
}

const CLOSED = new Set(['won', 'lost']);

function FunnelChart({ data, onRetry }: { data: OwnerInsights; onRetry: () => void }) {
  const title = 'Phễu cơ hội';
  const rows = data.breakdown?.funnel;
  if (!Array.isArray(rows)) {
    return (
      <Card title={title} state="error" className="kit-c6" data-testid="an-chart-funnel">
        <BlockFail onRetry={onRetry} />
      </Card>
    );
  }
  if (rows.length === 0) {
    return (
      <Card title={title} className="kit-c6" data-testid="an-chart-funnel">
        <EmptyState
          icon="ph ph-funnel"
          title="Chưa có cơ hội nào trong phễu."
          action={
            <Link to={OWNER_PATHS.feed} className="kit-btn kit-btn--secondary">
              Xem Thông tin
            </Link>
          }
        />
      </Card>
    );
  }
  const open = rows.filter((r) => !CLOSED.has(asText(r.stage)));
  const openN = open.reduce((s, r) => s + num(r.n), 0);
  const openV = open.reduce((s, r) => s + num(r.value_vnd), 0);
  return (
    <Card title={title} kicker="Số cơ hội · giá trị" className="kit-c6" footer={openN > 0 ? `Đang mở ${INT.format(openN)} cơ hội, tổng giá trị ${moneyText(openV)}` : undefined} data-testid="an-chart-funnel">
      <HBar
        ariaLabel="Phễu cơ hội"
        items={rows.map((r) => ({
          label: asText(r.label) || asText(r.stage),
          value: num(r.n),
          display: num(r.value_vnd) > 0 ? `${INT.format(num(r.n))} · ${moneyText(num(r.value_vnd))}` : INT.format(num(r.n)),
        }))}
      />
    </Card>
  );
}

function CostChart({ m, data, range }: { m: InsightMetric | undefined; data: OwnerInsights; range: InsightRange }) {
  const title = 'Chi phí trợ lý Gen theo ngày';
  const ai = data.ai_cost;
  if (!m || m.state === 'error') {
    return (
      <Card title={title} className="kit-c6" data-testid="an-chart-cost">
        <p className="an-fail__text">{METRIC_ERROR_NOTE}</p>
      </Card>
    );
  }
  const unpriced = num(ai?.unpriced_calls);
  const unknown = costUnknown(m.total, unpriced);
  const total = costText(m.total, unpriced);
  const budget = typeof ai?.budget_vnd === 'number' && ai.budget_vnd > 0 ? ai.budget_vnd : undefined;
  const feedback = range === 'week' && data.feedback && num(data.feedback.helpful) + num(data.feedback.not_helpful) > 0 ? data.feedback : null;
  return (
    <Card title={title} action={<span className="an-total" data-testid="an-cost-total">{`Tổng ${total}`}</span>} className="kit-c6" data-testid="an-chart-cost">
      {unknown ? (
        <p className="an-unknown" data-testid="an-cost-unknown">
          {COST_UNKNOWN_TEXT}
        </p>
      ) : num(ai?.calls) === 0 && num(m.total) === 0 ? (
        <p className="an-fail__text">Chưa có lượt dùng trợ lý Gen trong kỳ này.</p>
      ) : (
        <TrendChart
          kind="bar"
          series={[{ name: 'Chi phí mỗi ngày', values: values(m.series) }]}
          labels={(Array.isArray(m.series) ? m.series : []).map((p) => dayLabel(p?.d))}
          yFormat={(v) => INT.format(v)}
          valueFormat={(v) => moneyText(v)}
          threshold={budget}
          thresholdLabel={budget ? `Trần ngân sách ${moneyText(budget)} một ngày` : undefined}
          ariaLabel={`Chi phí trợ lý Gen theo ngày, tổng ${total}`}
          data-testid="an-trend-cost"
        />
      )}
      {unpriced > 0 ? (
        <p className="an-warn" role="note" data-testid="an-unpriced">
          <Icon name="ph ph-warning" size={16} />
          <span>
            {`${INT.format(unpriced)} lượt chưa đặt giá — chi phí thật có thể cao hơn. `}
            <Link to={PRICE_SETTINGS_PATH} className="kit-link">
              Đặt giá trong Cài đặt nâng cao
            </Link>
          </span>
        </p>
      ) : null}
      {feedback ? (
        <p className="owner-muted" data-testid="an-feedback">
          {`Sếp đã đánh giá câu trả lời của Gen: ${INT.format(num(feedback.helpful))} hữu ích · ${INT.format(num(feedback.not_helpful))} không hữu ích (7 ngày).`}
        </p>
      ) : null}
    </Card>
  );
}

// ── Hàng 4: bảng số liệu ─────────────────────────────────────────────────────────────────────────────────────────────

interface TableRow {
  key: string;
  label: string;
  cur: string;
  prev: string;
  delta: ReactNode;
}

/** Nhãn dòng của bảng: giữ nhãn máy chủ, thêm điều kiện cho chỉ số cần giải thích. */
const ROW_LABEL: Partial<Record<InsightKey, string>> = { reply_fast_pct: 'Trả lời nhanh (trong 15 phút)' };

export function buildTableRows(data: OwnerInsights, collecting: boolean): TableRow[] {
  const by = metricMap(data);
  const ai = data.ai_cost;
  const out: TableRow[] = [];
  const keys: readonly InsightKey[] = ['messages_in', 'new_contacts', 'opps_new', 'signals', 'opps_won', 'reply_fast_pct', 'unanswered', 'ai_cost_vnd', 'filtered'];
  for (const k of keys) {
    const m = by.get(k);
    if (!m) {
      out.push({ key: k, label: ROW_LABEL[k] ?? k, cur: '—', prev: '—', delta: <span className="an-muted">—</span> });
      continue;
    }
    const label = ROW_LABEL[k] ?? (asText(m.label) || k);
    if (m.state === 'error') {
      out.push({ key: k, label, cur: '—', prev: '—', delta: <span className="an-muted">Chưa tính được</span> });
      continue;
    }
    const cost = m.unit === 'vnd' && costUnknown(m.total, ai?.unpriced_calls);
    out.push({
      key: k,
      label,
      cur: formatMetric(m, 'cur', ai),
      prev: collecting ? '—' : formatMetric(m, 'prev', ai),
      delta: collecting || cost ? <span className="an-muted">—</span> : <DeltaBadge view={deltaView(m)} />,
    });
  }
  return out;
}

const COLUMNS: readonly DataColumn<TableRow>[] = [
  { key: 'label', label: 'Chỉ số', priority: 1 },
  { key: 'cur', label: 'Kỳ này', align: 'right', priority: 1 },
  { key: 'prev', label: 'Kỳ trước', align: 'right', priority: 2 },
  { key: 'delta', label: 'Thay đổi', align: 'right', priority: 1, action: true, render: (r) => r.delta },
];

// ── màn ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

export function AnalyticsScreen(): JSX.Element {
  useEffect(() => {
    document.title = 'Phân tích · Gen-Harness';
  }, []);
  const tz = useOrgTimezone() || DEFAULT_TZ;
  const [range, setRange] = useState<InsightRange>('week');
  const q = useOwnerInsights(range);
  const days = RANGE_DAYS[range];

  const picker = (
    <Segmented<InsightRange>
      label="Chọn khoảng thời gian"
      value={range}
      onChange={setRange}
      options={[
        { value: 'week', label: '7 ngày' },
        { value: 'month', label: '30 ngày' },
      ]}
    />
  );

  let body: ReactNode;
  let line = `So với ${days} ngày trước đó`;
  if (q.isPending) {
    body = (
      <div className="kit-grid" aria-busy="true" data-testid="an-loading">
        {TILES.map((t) => (
          <Card key={t.key} title={t.label} state="loading" className={CELL} />
        ))}
        <Card title="Tin nhận theo ngày" state="loading" className="kit-c8" />
        <Card title="Khách hỏi gì" state="loading" className="kit-c4" />
      </div>
    );
  } else if (q.isError) {
    body = (
      <Card title="Phân tích" state="error" data-testid="an-error">
        <OwnerError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      </Card>
    );
  } else {
    const d = q.data;
    line = statusLine(d, days, tz);
    const by = metricMap(d);
    const collecting = d.state === 'collecting';
    if (d.state === 'empty') {
      const cost = by.get('ai_cost_vnd');
      const showCost = !!cost && cost.state !== 'error' && (num(cost.total) > 0 || num(d.ai_cost?.unpriced_calls) > 0);
      body = (
        <div className="kit-grid" data-testid="an-empty">
          <div className="kit-c12">
            {/* `empty` = chưa từng có tin, KHÔNG phải chưa nối kênh: thẻ "Nối Zalo" chỉ khi thật sự chưa nối kênh nào
                (cùng luật với Tình báo, Thông tin, Quan hệ — channels_connected === 0). Đã nối mà chưa có tin ⇒ chỉ đường bật nhóm. */}
            {d.channels_connected === 0 ? (
              <FirstRunCard />
            ) : (
              <Card data-testid="an-empty-listen">
                <EmptyState
                  icon="ph ph-broadcast"
                  title="Chưa có tin nào — bật nhóm lắng nghe để Gen bắt đầu đọc"
                  description="Zalo đã nối nhưng mọi nhóm đang ở “Không nghe”. Bật nghe một nhóm là số liệu sẽ hiện ở đây."
                  action={
                    <Link to="/guide/6" className="kit-btn kit-btn--primary" data-testid="an-empty-listen-cta">
                      Bật nhóm lắng nghe
                    </Link>
                  }
                />
              </Card>
            )}
          </div>
          {showCost ? <CostTile m={cost} ai={d.ai_cost} /> : null}
        </div>
      );
    } else {
      const retry = () => void q.refetch();
      body = (
        <div className="kit-grid" data-testid="an-body" data-state={d.state}>
          {TILES.map((t) => (
            <MetricTile key={t.key} spec={t} m={by.get(t.key)} won={by.get('opps_won')} collecting={collecting} days={days} />
          ))}
          <MessagesChart m={by.get('messages_in')} collecting={collecting} days={days} />
          <AskedChart data={d} onRetry={retry} />
          <FunnelChart data={d} onRetry={retry} />
          <CostChart m={by.get('ai_cost_vnd')} data={d} range={range} />
          <Card title="Bảng số liệu" kicker={`Kỳ này là ${days} ngày gần nhất`} padded={false} className="kit-c12" data-testid="an-table-card">
            <DataTable<TableRow> columns={COLUMNS} rows={buildTableRows(d, collecting)} rowKey="key" ariaLabel="Bảng số liệu" data-testid="an-table" />
          </Card>
        </div>
      );
    }
  }

  return (
    <div className="owner-screen analytics" data-testid="owner-frame-analytics">
      <div className="an-head">
        {picker}
        {line ? (
          <p className="an-line" data-testid="an-status-line" aria-live="polite">
            {line}
          </p>
        ) : null}
      </div>
      {body}
    </div>
  );
}
