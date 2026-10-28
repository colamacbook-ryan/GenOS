/**
 * v0.1.78 (G0) — OwnerShell ĐÃ BỎ: một shell duy nhất (AppShell). Tệp này chỉ còn là bộ chuyển hướng cho `/owner/*`
 * (Mặt tiền cũ) sang route mới theo bảng ở shell/legacyRedirects.ts: `/owner` → `/intel`, `/owner/thong-tin` → `/intel?tab=signals`,
 * `/owner/quan-he` → `/panorama?tab=relations`, `/owner/phan-tich` → `/panorama?tab=trends`, `/owner/viec` → `/decide`,
 * `/owner/them` → `/admin/start`; đường lạ về `/intel`. Giữ nguyên query (`?tin=`, `?nguoi=`, `?gen=`) và băm.
 */
import { LegacyRedirect } from '../shell/LegacyRedirect';

export function OwnerShell() {
  return <LegacyRedirect />;
}
