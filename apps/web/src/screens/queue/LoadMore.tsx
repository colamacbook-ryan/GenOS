import { Button } from '@gen-harness/ui';
import { errorDetail, errorText } from '../../lib/errorText';
import { fmtInt } from '../../lib/format';
import { InlineError } from '../common';

/**
 * Chân danh sách phân trang: "Đang xem 50/130" + nút "Xem thêm" (Hộp thư, Việc).
 * `total` = null khi không biết tổng chính xác (vd đang lọc theo ý định) — chỉ ghi số đã xem.
 * Hết trang thì không vẽ gì.
 */
export function LoadMore({
  shown,
  total,
  hasMore,
  loading,
  onMore,
  error,
  unit = 'mục',
  testId = 'xem-them',
}: {
  shown: number;
  total: number | null;
  hasMore: boolean;
  loading: boolean;
  onMore: () => void;
  /** Lỗi khi tải trang kế — các dòng đã tải vẫn giữ nguyên, chỉ báo ở đây kèm nút thử lại. */
  error?: unknown;
  unit?: string;
  testId?: string;
}) {
  if (!hasMore) return null;
  const known = total !== null && total >= shown;
  return (
    <div className="load-more" data-testid={testId} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, padding: '12px 16px' }}>
      <span className="muted-note" role="status" data-testid={`${testId}-dem`}>
        {known ? `Đang xem ${fmtInt(shown)}/${fmtInt(total)} ${unit}` : `Đang xem ${fmtInt(shown)} ${unit}, còn nữa`}
      </span>
      {error ? <InlineError detail={errorDetail(error)}>{errorText(error)}</InlineError> : null}
      <Button variant="secondary" icon="ph ph-caret-double-down" loading={loading} onClick={onMore} data-testid={`${testId}-nut`}>
        {error ? 'Thử tải lại' : 'Xem thêm'}
      </Button>
    </div>
  );
}
