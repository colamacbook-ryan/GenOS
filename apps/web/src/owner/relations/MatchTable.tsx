/**
 * v0.1.59 (G4) — "Cung–cầu" của màn Quan hệ (`GET /owner/relations?list=matches`, dòng có `match_id`).
 * Cột: Ai cần · Cần gì · Ai có · Khớp %. Hành động:
 *   - "Giới thiệu"  → `POST /matches/{id}/introduce` (useIntroduceMatch): CHỈ tạo bản nháp chờ duyệt, KHÔNG gửi gì — báo "Đã tạo nháp, duyệt ở Việc".
 *   - "Bỏ qua"      → `POST /matches/{id}/reject` (useRejectMatch): cặp này biến khỏi danh sách.
 * Gen chỉ đề xuất: gửi tin giới thiệu thật phải duyệt nháp bằng mã PIN ở màn Việc. Lỗi = câu thân thiện + "Chi tiết kỹ thuật".
 */
import { Link } from 'react-router-dom';
import type { OwnerRelationRow } from '@gen-harness/contracts';
import { DataTable, Icon, type DataColumn } from '@gen-harness/ui';
import { errorDetail, errorText } from '../../lib/errorText';
import { InlineError } from '../../screens/common';
import { useIntroduceMatch, useRejectMatch } from '../actions/useOwnerActions';
import { OWNER_PATHS, asText } from '../ownerModel';
import { parseMatch, personKey } from './relationsModel';
import './relations.css';

/** Hai nút của một cặp. Mỗi dòng có hook riêng nên trạng thái "đã tạo nháp" / lỗi không lẫn sang dòng khác. */
function MatchActions({ row }: { row: OwnerRelationRow }) {
  const matchId = asText(row.match_id);
  const introduce = useIntroduceMatch();
  const reject = useRejectMatch();
  const busy = introduce.isPending || reject.isPending;
  const done = introduce.isSuccess;
  const failed = introduce.error ?? reject.error;
  if (!matchId) return null;
  return (
    <span className="rel-actions">
      <button
        type="button"
        className="kit-btn kit-btn--secondary"
        data-testid={`match-introduce-${matchId}`}
        disabled={busy || done}
        aria-busy={introduce.isPending || undefined}
        onClick={() => introduce.mutate(matchId)}
      >
        {done ? <Icon name="ph ph-check" size={16} /> : null}
        {done ? 'Đã tạo nháp' : introduce.isPending ? 'Đang tạo nháp…' : 'Giới thiệu'}
      </button>
      <button type="button" className="kit-btn kit-btn--ghost" data-testid={`match-reject-${matchId}`} disabled={busy} onClick={() => reject.mutate(matchId)}>
        Bỏ qua
      </button>
      {done ? (
        <span className="rel-note" role="status" data-testid={`match-done-${matchId}`}>
          Đã tạo nháp, duyệt ở <Link to={OWNER_PATHS.work}>Việc</Link>
        </span>
      ) : null}
      {failed ? (
        <span className="rel-note rel-note--bad" data-testid={`match-error-${matchId}`}>
          <InlineError detail={errorDetail(failed)}>{errorText(failed)}</InlineError>
        </span>
      ) : null}
    </span>
  );
}

function SupplyCell({ row }: { row: OwnerRelationRow }) {
  const m = parseMatch(row);
  return (
    <span className="rel-name">
      <span className="rel-name__main">{m.who_has || '—'}</span>
      {m.supply ? <span className="rel-name__sub">{m.supply}</span> : null}
    </span>
  );
}

const COLUMNS: DataColumn<OwnerRelationRow>[] = [
  { key: 'who_needs', label: 'Ai cần', render: (r) => <span className="rel-name__main">{parseMatch(r).who_needs || '—'}</span> },
  { key: 'need', label: 'Cần gì', render: (r) => parseMatch(r).need },
  { key: 'has', label: 'Ai có', priority: 2, render: (r) => <SupplyCell row={r} /> },
  {
    key: 'score',
    label: 'Khớp',
    priority: 2,
    align: 'right',
    render: (r) => {
      const p = parseMatch(r).percent;
      return p === null ? '—' : `${p}%`;
    },
  },
  { key: 'actions', label: '', action: true, render: (r) => <MatchActions row={r} /> },
];

export interface MatchTableProps {
  /** Chỉ các dòng có `match_id` (cặp không có mã thì không giới thiệu / bỏ qua được). */
  rows: readonly OwnerRelationRow[];
  selectedPerson: string | null;
  /** Dòng có người ⇒ chọn người đó (hồ sơ sống). */
  onSelect: (personId: string) => void;
}

export function MatchTable({ rows, selectedPerson, onSelect }: MatchTableProps) {
  const list = rows.filter((r) => asText(r.match_id));
  const selected = list.find((r) => asText(r.person_id) && personKey(r) === selectedPerson);
  return (
    <DataTable
      columns={COLUMNS}
      rows={list}
      rowKey="id"
      selectedKey={selected ? asText(selected.id) : undefined}
      onRowClick={(r) => {
        if (asText(r.person_id)) onSelect(personKey(r));
      }}
      ariaLabel="Gợi ý ghép cung–cầu"
      className="rel-table"
      data-testid="match-table"
    />
  );
}
