/**
 * v0.1.59 (G4) — bảng "Cần chăm" và "Khách nóng" của màn Quan hệ (`GET /owner/relations?list=care|hot`).
 *
 * Cùng kiểu bảng (DataTable: ≥ 760px là bảng, dưới đó tự xếp chồng thành danh sách):
 *   Cần chăm  — Khách · Lý do ("Sắp rời" / "Lâu chưa liên lạc") · Độ nóng · Lần cuối liên lạc · Cơ hội mở · Hành động
 *   Khách nóng — Khách · Độ nóng · Lần cuối liên lạc · Giá trị cơ hội · Hành động
 * Hành động: "Nhờ Gen soạn tin" (mở ngăn Hỏi Gen với câu điền sẵn — Gen chỉ đề xuất, Sếp duyệt) và "Tạo nhắc" (nhắc việc mai 09:00).
 * Bấm cả dòng ⇒ chọn khách (hồ sơ sống hiện bên phải / trong ngăn). Không "điểm" kỹ thuật: số kèm lời xu hướng khi máy chủ có.
 */
import type { OwnerRelationRow } from '@gen-harness/contracts';
import { Badge, DataTable, Icon, type DataColumn } from '@gen-harness/ui';
import { openAskGen } from '../AskGenScreen';
import { asText } from '../ownerModel';
import { RemindButton } from './RemindButton';
import { askGenPrompt, lastContactText, openValueText, personKey, reasonBadge, scoreValue, trendFromMetric, trendWord } from './relationsModel';
import './relations.css';

export type CareVariant = 'care' | 'hot';

function NameCell({ row }: { row: OwnerRelationRow }) {
  const sub = asText(row.subtitle);
  return (
    <span className="rel-name">
      <span className="rel-name__main">{asText(row.name) || 'Chưa có tên'}</span>
      {sub ? <span className="rel-name__sub">{sub}</span> : null}
    </span>
  );
}

function HeatCell({ row }: { row: OwnerRelationRow }) {
  const v = scoreValue(row.heat);
  if (v === null) return <span className="rel-muted">Chưa có</span>;
  const trend = trendFromMetric(row.metric_text);
  return (
    <span className="rel-heat">
      <span className="rel-heat__v">{v}</span>
      {trend ? (
        <span className="rel-heat__t">
          <Icon name={trend === 'up' ? 'ph ph-arrow-up' : 'ph ph-arrow-down'} size={12} />
          {trendWord(trend)}
        </span>
      ) : null}
    </span>
  );
}

function ReasonCell({ row }: { row: OwnerRelationRow }) {
  const b = reasonBadge(row.reason);
  return b ? <Badge kind="status" tone={b.tone}>{b.label}</Badge> : <span className="rel-muted">—</span>;
}

/** Hai nút của một dòng — nằm NGOÀI vùng bấm của dòng (DataTable chặn nổi bọt) nên bấm nút không mở hồ sơ. */
function ActionsCell({ row }: { row: OwnerRelationRow }) {
  const key = personKey(row);
  const name = asText(row.name) || 'khách này';
  const pid = asText(row.person_id);
  return (
    <span className="rel-actions">
      <button type="button" className="kit-btn kit-btn--secondary" data-testid={`rel-ask-${key}`} onClick={() => openAskGen(askGenPrompt(name))}>
        Nhờ Gen soạn tin
      </button>
      <RemindButton title={`Liên hệ lại ${name}`} subject={pid ? { type: 'person', id: pid } : undefined} variant="ghost" testId={`rel-remind-${key}`} />
    </span>
  );
}

function careColumns(variant: CareVariant): DataColumn<OwnerRelationRow>[] {
  const cols: DataColumn<OwnerRelationRow>[] = [
    { key: 'name', label: 'Khách', render: (r) => <NameCell row={r} /> },
  ];
  if (variant === 'care') cols.push({ key: 'reason', label: 'Lý do', render: (r) => <ReasonCell row={r} /> });
  cols.push(
    { key: 'heat', label: 'Độ nóng', priority: 2, render: (r) => <HeatCell row={r} /> },
    { key: 'last', label: 'Lần cuối liên lạc', priority: 2, render: (r) => lastContactText(r.last_contact_days) },
    { key: 'value', label: variant === 'care' ? 'Cơ hội mở' : 'Giá trị cơ hội', priority: 2, align: 'right', render: (r) => openValueText(r.open_value_vnd) },
    { key: 'actions', label: '', action: true, render: (r) => <ActionsCell row={r} /> },
  );
  return cols;
}

export interface CareTableProps {
  variant: CareVariant;
  rows: readonly OwnerRelationRow[];
  /** Mã người đang chọn (để tô dòng). */
  selectedPerson: string | null;
  onSelect: (personId: string) => void;
}

export function CareTable({ variant, rows, selectedPerson, onSelect }: CareTableProps) {
  const selected = rows.find((r) => personKey(r) === selectedPerson);
  return (
    <DataTable
      columns={careColumns(variant)}
      rows={rows}
      rowKey="id"
      selectedKey={selected ? asText(selected.id) : undefined}
      onRowClick={(r) => onSelect(personKey(r))}
      ariaLabel={variant === 'care' ? 'Khách cần chăm' : 'Khách nóng'}
      className="rel-table rel-table--care"
      data-testid={variant === 'care' ? 'care-table' : 'hot-table'}
    />
  );
}
