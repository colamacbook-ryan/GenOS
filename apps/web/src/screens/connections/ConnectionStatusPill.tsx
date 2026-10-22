import { CONN_STATUS_LABEL, type ConnStatus } from './connectionsModel';

/**
 * v0.1.42 (F-7): viên trạng thái chung của mọi thẻ ở Kết nối — Đang chạy (xanh) · Cần Sếp xử lý (vàng) · Chưa nối
 * (xám). Chữ chi tiết (vd "Hết phiên", "Token sắp hết hạn") nằm ở dòng meta của thẻ, không ở viên.
 */
export function ConnectionStatusPill({ status }: { status: ConnStatus }) {
  return (
    <span className="conn-pill" data-status={status} data-tone={status === 'running' ? 'ok' : status === 'needs_boss' ? 'warn' : 'neutral'}>
      <span className="conn-pill__dot" aria-hidden />
      {CONN_STATUS_LABEL[status]}
    </span>
  );
}
