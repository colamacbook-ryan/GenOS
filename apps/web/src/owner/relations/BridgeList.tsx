/**
 * v0.1.59 (G4) — "Cầu nối" của màn Quan hệ (`GET /owner/relations?list=bridges`): người có mặt ở nhiều nhóm, nối các nhóm với nhau.
 * Cột: Người · Nối với (số) · Ví dụ. KHÔNG vẽ bản đồ trên Mặt tiền — chỉ có danh sách và liên kết "Xem bản đồ trong Console" (/graph).
 * Bấm dòng ⇒ chọn người (hồ sơ sống hiện bên phải / trong ngăn).
 */
import { Link } from 'react-router-dom';
import type { OwnerRelationRow } from '@gen-harness/contracts';
import { DataTable, Icon, type DataColumn } from '@gen-harness/ui';
import { asText } from '../ownerModel';
import { bridgeCount, personKey } from './relationsModel';
import './relations.css';

const GRAPH_PATH = '/graph';

const COLUMNS: DataColumn<OwnerRelationRow>[] = [
  {
    key: 'name',
    label: 'Người',
    render: (r) => <span className="rel-name__main">{asText(r.name) || 'Chưa có tên'}</span>,
  },
  { key: 'links', label: 'Nối với', priority: 1, render: (r) => bridgeCount(r.metric_text) },
  { key: 'example', label: 'Ví dụ', priority: 2, render: (r) => asText(r.subtitle) },
];

export interface BridgeListProps {
  rows: readonly OwnerRelationRow[];
  selectedPerson: string | null;
  onSelect: (personId: string) => void;
}

export function BridgeList({ rows, selectedPerson, onSelect }: BridgeListProps) {
  const selected = rows.find((r) => personKey(r) === selectedPerson);
  return (
    <div className="rel-bridges" data-testid="bridge-list">
      <DataTable
        columns={COLUMNS}
        rows={rows}
        rowKey="id"
        selectedKey={selected ? asText(selected.id) : undefined}
        onRowClick={(r) => onSelect(personKey(r))}
        ariaLabel="Người cầu nối"
        className="rel-table"
      />
      <p className="rel-bridges__link">
        <Link to={GRAPH_PATH} className="kit-link" data-testid="bridge-graph-link">
          Xem bản đồ trong Console
          <Icon name="ph ph-arrow-right" size={14} />
        </Link>
      </p>
    </div>
  );
}
