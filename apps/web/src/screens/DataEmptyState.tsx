import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { EmptyState, Icon } from '@gen-harness/ui';
import { useHeaderStatus, useMe } from '../lib/queries';

export type DataEmptyReason = 'no-channel' | 'channel-down' | 'no-group';

type HeaderCounts = { channels_live: number; channels_connected?: number; groups_listening: number };

/**
 * Vì sao danh sách trống: chưa nối kênh nào, kênh đã nối nhưng mất phiên (cần quét lại QR), hoặc đã có kênh sống nhưng
 * chưa nghe nhóm nào; `null` khi đã có nguồn dữ liệu.
 */
// Hàm thuần đi cùng component (test gọi trực tiếp) — chấp nhận mất fast refresh cho tệp nhỏ này.
// eslint-disable-next-line react-refresh/only-export-components
export function dataEmptyReason(h?: HeaderCounts | null): DataEmptyReason | null {
  if (!h) return null;
  if (!(h.channels_live > 0)) return (h.channels_connected ?? 0) > 0 ? 'channel-down' : 'no-channel';
  if (!(h.groups_listening > 0)) return 'no-group';
  return null;
}

/**
 * v0.1.43 (F-29) — danh sách trống vì CHƯA CÓ NGUỒN dữ liệu thì nói rõ lý do và dẫn đường (Owner: nút tới bước hướng
 * dẫn hoặc Kết nối; vai trò khác: nhờ Owner). Đọc cùng truy vấn với header (`useHeaderStatus`) nên không thêm request.
 * Hiện `fallback` (trạng thái trống cũ của màn) khi: màn đang bật bộ lọc/tab/tìm kiếm (`filtered` — rỗng vì không khớp,
 * không phải vì thiếu nguồn), header/me đang tải hoặc lỗi, hoặc đã có kênh + nhóm.
 */
export function DataEmptyState({ fallback, filtered = false }: { fallback: ReactNode; filtered?: boolean }) {
  const header = useHeaderStatus();
  const me = useMe();
  const reason = !filtered && header.isSuccess && me.isSuccess ? dataEmptyReason(header.data) : null;
  if (!reason) return <>{fallback}</>;
  const isOwner = me.data?.role?.code === 'owner';

  const view = {
    'no-channel': {
      icon: 'ph ph-plugs',
      title: 'Chưa có dữ liệu vì chưa nối kênh',
      ownerText: 'Nối Zalo hoặc WhatsApp để tin nhắn bắt đầu về đây.',
      otherText: 'Nhờ Owner nối kênh (Zalo/WhatsApp) ở Kết nối — tin nhắn sẽ hiện ở đây sau đó.',
      to: '/guide/5',
      cta: 'Nối kênh',
      ctaIcon: 'ph ph-plugs-connected',
    },
    'channel-down': {
      icon: 'ph ph-plugs',
      title: 'Kênh mất kết nối — quét lại QR',
      ownerText: 'Phiên đăng nhập kênh đã hết hạn hoặc bị đăng xuất nên tin mới không về. Quét lại QR ở Kết nối để nhận tin tiếp.',
      otherText: 'Phiên đăng nhập kênh đã hết hạn. Nhờ Owner quét lại QR ở Kết nối.',
      to: '/connections',
      cta: 'Quét lại QR',
      ctaIcon: 'ph ph-qr-code',
    },
    'no-group': {
      icon: 'ph ph-users-three',
      title: 'Chưa chọn nhóm nào để nghe',
      ownerText: 'Kênh đã nối nhưng chưa bật nghe nhóm nào. Chọn nhóm để hệ thống bắt đầu đọc tin.',
      otherText: 'Nhờ Owner chọn nhóm để nghe ở Kết nối.',
      to: '/guide/6',
      cta: 'Chọn nhóm để nghe',
      ctaIcon: 'ph ph-ear',
    },
  }[reason];

  return (
    <div data-testid="data-empty-state" data-reason={reason}>
      <EmptyState
        icon={view.icon}
        title={view.title}
        description={isOwner ? view.ownerText : view.otherText}
        actions={
          isOwner ? (
            <Link to={view.to} className="gh-btn gh-btn--primary btn-24">
              <Icon name={view.ctaIcon} size={12} />
              {view.cta}
            </Link>
          ) : undefined
        }
      />
    </div>
  );
}
