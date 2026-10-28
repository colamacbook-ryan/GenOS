import { Navigate, useLocation } from 'react-router-dom';
import { can } from '../lib/permissions';
import { useMe } from '../lib/queries';
import { resolveLegacy } from './legacyRedirects';

/**
 * v0.1.78 (G0): route chuyển hướng cho mọi đường dẫn cũ (bảng ở legacyRedirects.ts). Giữ query + băm của link cũ.
 * `/system` không ghi tab chọn Sao lưu hay Nhật ký theo quyền (`system.read`); đường không có trong bảng ⇒ về "/".
 * Phải CHỜ `/auth/me` trước khi quyết (mở thẳng `/system` lúc chưa tải xong quyền từng đưa cả Owner sang Nhật ký vì `system.read` còn "không có").
 */
export function LegacyRedirect() {
  const { pathname, search, hash } = useLocation();
  const me = useMe();
  if (me.isPending) return <div className="screen" aria-busy="true" aria-label="Đang chuyển trang" />;
  const to = resolveLegacy(pathname, search, hash, can(me.data, 'system.read'));
  return <Navigate to={to ?? '/'} replace />;
}
