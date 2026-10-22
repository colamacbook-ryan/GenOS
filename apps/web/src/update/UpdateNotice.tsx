import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Icon } from '@gen-harness/ui';
import { api } from '../lib/api';
import { useCan } from '../lib/permissions';
import { UPDATE_KEY, updatePollMs, updateView } from './updateModel';

/**
 * v0.1.42 (F-61): Hôm nay chỉ còn MỘT dòng báo bản mới — thẻ cập nhật đầy đủ (Cập nhật ngay, Thử lại, Kiểm tra bản
 * mới) chỉ ở Cài đặt › Sao lưu & cập nhật. `hideFailed` (F-6): cập nhật lỗi đã có dòng trong dải "Cần Sếp xử lý" thì
 * không nhắc lại ở đây.
 */
export function UpdateNotice({ hideFailed = false }: { hideFailed?: boolean }) {
  // v0.1.71 (P3-9): `GET /system/update` cần `system.manage` — vai khác (Vận hành, Kiểm soát…) mở Hôm nay không được gọi
  // (trước đây nhận 403 mỗi lần mở trang). Chỉ hỏi khi có quyền.
  const canManage = useCan('system.manage');
  const q = useQuery({
    queryKey: UPDATE_KEY,
    queryFn: ({ signal }) => api.systemUpdate.get(signal),
    enabled: canManage,
    retry: false,
    refetchInterval: (query) => updatePollMs(query.state.data?.state),
  });
  // Không phải Owner/quản trị (403) hay api chưa có tính năng này: im lặng.
  if (!q.data) return null;
  const view = updateView(q.data, { waitingFor: null, offline: false });
  if (view.kind === 'hidden' || view.kind === 'finished') return null;
  if (hideFailed && view.kind === 'failed') return null;
  const target = q.data.latest ?? 'bản mới';
  const text =
    view.kind === 'available'
      ? view.block
        ? `Có bản mới ${target} — ${view.block.title}; xem ở Cài đặt`
        : `Có bản mới ${target} — cập nhật ở Cài đặt`
      : view.kind === 'working'
        ? `Đang cập nhật lên ${target} — xem tiến độ ở Cài đặt`
        : `${view.title} — xem ở Cài đặt`;
  return (
    <Link to="/system?tab=storage" className="upd-notice" data-tone={view.tone} data-testid="update-notice">
      <Icon name="ph ph-arrow-circle-up" size={14} />
      <span className="upd-notice__text">{text}</span>
      <Icon name="ph ph-arrow-right" size={12} />
    </Link>
  );
}
