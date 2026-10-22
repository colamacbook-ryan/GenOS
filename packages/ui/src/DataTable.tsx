import type { CSSProperties, KeyboardEvent, ReactNode } from 'react';
import { cx } from './cx';
import { ListRow } from './ListRow';
import { useMediaQuery } from './useMediaQuery';

/**
 * v0.1.59 (G2) — DataTable: bảng chuẩn của Mặt tiền, TỰ ĐỔI CẤU TRÚC theo cỡ màn (CSS: `kit-table` / `kit-list` trong owner-kit.css):
 *
 *   ≥ 1024px     <table> đủ cột
 *   760–1023px   <table> bỏ cột `priority` ≥ 3 (ít quan trọng nhất)
 *   < 760px      danh sách xếp chồng bằng ListRow: cột đầu = tiêu đề, các cột còn lại (priority ≤ 2) = dòng phụ "Nhãn giá trị";
 *                cột `action: true` ra ngoài vùng bấm của dòng (đặt nút Duyệt/Trả lời được mà không lồng nút trong nút)
 *
 * Chuyển cấu trúc bằng `useMediaQuery('(min-width: 760px)')` (window.matchMedia; không có ⇒ false ⇒ xếp chồng).
 * `priority`: 1 = luôn hiện (mặc định) · 2 = hiện ở bảng, và ở dòng phụ khi xếp chồng · 3 = chỉ hiện ở bảng ≥ 1024px.
 * Ô không có `render` hiện `row[key]` nếu là chuỗi/số/boolean — object bất kỳ KHÔNG BAO GIỜ được vẽ ra (ô trống).
 * `onRowClick` ⇒ cả dòng bấm được (bàn phím: Enter/Space); nút trong ô tự chặn nổi bọt nên không kích hoạt dòng.
 */
export interface DataColumn<T> {
  key: string;
  label: string;
  align?: 'left' | 'right' | 'center';
  width?: number | string;
  render?: (row: T, index: number) => ReactNode;
  priority?: 1 | 2 | 3;
  /** Cột nút hành động: xếp chồng thì ra ngoài vùng bấm của dòng. */
  action?: boolean;
}

export interface DataTableProps<T extends object> {
  columns: readonly DataColumn<T>[];
  rows: readonly T[];
  /** Hiện khi không có dòng nào (mặc định một câu ngắn). */
  empty?: ReactNode;
  onRowClick?: (row: T, index: number) => void;
  /** Tên trường (mặc định `id` rồi `key`) hoặc hàm trả khoá dòng. */
  rowKey?: string | ((row: T, index: number) => string);
  /** Khoá của dòng đang chọn (tô nền). */
  selectedKey?: string;
  /** Tên bảng cho trình đọc màn hình. */
  ariaLabel?: string;
  className?: string;
  'data-testid'?: string;
}

function cellText(v: unknown): string {
  return typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean' ? String(v) : '';
}

function valueOf<T extends object>(c: DataColumn<T>, row: T, i: number): ReactNode {
  return c.render ? c.render(row, i) : cellText((row as Record<string, unknown>)[c.key]);
}

const prio = <T extends object>(c: DataColumn<T>): number => c.priority ?? 1;

export function DataTable<T extends object>({
  columns,
  rows,
  empty,
  onRowClick,
  rowKey,
  selectedKey,
  ariaLabel,
  className,
  'data-testid': testId,
}: DataTableProps<T>) {
  const wide = useMediaQuery('(min-width: 760px)');
  const full = useMediaQuery('(min-width: 1024px)');
  const list = Array.isArray(rows) ? rows : [];
  const keyOf = (row: T, i: number): string => {
    if (typeof rowKey === 'function') return rowKey(row, i);
    const r = row as Record<string, unknown>;
    const v = typeof rowKey === 'string' ? r[rowKey] : (r.id ?? r.key);
    return typeof v === 'string' || typeof v === 'number' ? String(v) : String(i);
  };

  if (list.length === 0) {
    return (
      <div className={cx('kit-table-empty', className)} data-testid={testId} data-layout={wide ? 'table' : 'stack'}>
        {empty ?? <p className="kit-table-empty__text">Chưa có dòng nào để hiện.</p>}
      </div>
    );
  }

  // ── < 760px: danh sách xếp chồng ──
  if (!wide) {
    const [first, ...rest] = columns.filter((c) => !c.action);
    const actions = columns.filter((c) => c.action);
    const meta = rest.filter((c) => prio(c) <= 2);
    return (
      <ul className={cx('kit-list', className)} aria-label={ariaLabel} data-testid={testId} data-layout="stack">
        {list.map((row, i) => {
          const k = keyOf(row, i);
          return (
            <ListRow
              key={k}
              as="li"
              data-testid="kit-list-row"
              title={first ? valueOf(first, row, i) : null}
              meta={
                meta.length > 0 ? (
                  <span className="kit-row__pairs">
                    {meta.map((c) => (
                      <span key={c.key} className="kit-row__pair">
                        {c.label ? <span className="kit-row__k">{c.label}</span> : null}
                        <span className="kit-row__v">{valueOf(c, row, i)}</span>
                      </span>
                    ))}
                  </span>
                ) : undefined
              }
              actions={actions.length > 0 ? actions.map((c) => <span key={c.key}>{valueOf(c, row, i)}</span>) : undefined}
              onClick={onRowClick ? () => onRowClick(row, i) : undefined}
              selected={selectedKey !== undefined && selectedKey === k}
            />
          );
        })}
      </ul>
    );
  }

  // ── ≥ 760px: <table> (760–1023 bỏ cột priority 3) ──
  const cols = columns.filter((c) => full || prio(c) < 3);
  const onKey = (e: KeyboardEvent<HTMLTableRowElement>, row: T, i: number) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onRowClick?.(row, i);
    }
  };
  const cellStyle = (c: DataColumn<T>): CSSProperties => ({ textAlign: c.align, width: c.width });
  return (
    <div className={cx('kit-table-wrap', className)} data-testid={testId} data-layout="table">
      <table className={cx('kit-table', onRowClick && 'kit-table--click')} aria-label={ariaLabel}>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c.key} scope="col" style={cellStyle(c)} data-align={c.align}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {list.map((row, i) => {
            const k = keyOf(row, i);
            const selected = selectedKey !== undefined && selectedKey === k;
            return (
              <tr
                key={k}
                aria-selected={selected || undefined}
                tabIndex={onRowClick ? 0 : undefined}
                onClick={onRowClick ? () => onRowClick(row, i) : undefined}
                onKeyDown={onRowClick ? (e) => onKey(e, row, i) : undefined}
              >
                {cols.map((c) => (
                  <td
                    key={c.key}
                    style={cellStyle(c)}
                    data-align={c.align}
                    className={cx(c === cols[0] && 'kit-table__main', c.action && 'kit-table__act')}
                    onClick={c.action ? (e) => e.stopPropagation() : undefined}
                  >
                    {valueOf(c, row, i)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
