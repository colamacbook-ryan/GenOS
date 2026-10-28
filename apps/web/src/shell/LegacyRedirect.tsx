import { Navigate, useLocation } from 'react-router-dom';
import { useCan } from '../lib/permissions';
import { resolveLegacy } from './legacyRedirects';

/**
 * v0.1.78 (G0): route chuyển hướng cho mọi đường dẫn cũ (bảng ở legacyRedirects.ts). Giữ query + băm của link cũ.
 * `/system` không ghi tab chọn Sao lưu hay Nhật ký theo quyền (`system.read`); đường không có trong bảng ⇒ về "/".
 */
export function LegacyRedirect() {
  const { pathname, search, hash } = useLocation();
  const canSystem = useCan('system.read');
  const to = resolveLegacy(pathname, search, hash, canSystem);
  return <Navigate to={to ?? '/'} replace />;
}
