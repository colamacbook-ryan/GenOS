/**
 * v0.1.78 (G0) — TRANG GIỮ CHỖ `/panorama` (Toàn cảnh). Gói G4 sở hữu thư mục này. Xuất MỘT thành phần `PanoramaPage`, nhận `?tab=`.
 * Nhúng các màn cũ: Khách & Nhóm, Bản đồ quan hệ, Cơ hội, Deal, Cung↔Cầu, Tìm, Hồ sơ sống, Sổ tay, Tài liệu + (Sếp) Quan hệ nóng/nguội, Xu hướng.
 */
import { HubPage, type HubTab } from '../shell/HubPage';
import { OwnerEmbed } from '../shell/OwnerEmbed';
import { LEGACY_SCREENS } from '../shell/legacyScreens';
import { AnalyticsScreen } from '../owner/analytics/AnalyticsScreen';
import { RelationsScreen } from '../owner/relations/RelationsScreen';

const legacy = (key: string, label: string): HubTab => {
  const Screen = LEGACY_SCREENS[key];
  return { key: key === 'directory' ? 'people' : key, label, screen: key, render: () => <Screen /> };
};

const TABS: HubTab[] = [
  legacy('directory', 'Người & nhóm'),
  legacy('graph', 'Bản đồ quan hệ'),
  legacy('opportunity', 'Cơ hội'),
  legacy('deals', 'Deal & Vụ việc'),
  legacy('supply', 'Cung ↔ Cầu'),
  legacy('search', 'Tìm'),
  legacy('profile', 'Hồ sơ sống'),
  legacy('notebook', 'Sổ tay'),
  legacy('documents', 'Tài liệu'),
  { key: 'relations', label: 'Nóng / nguội', ownerOnly: true, render: () => <OwnerEmbed><RelationsScreen /></OwnerEmbed> },
  { key: 'trends', label: 'Xu hướng', ownerOnly: true, render: () => <OwnerEmbed><AnalyticsScreen /></OwnerEmbed> },
];

export function PanoramaPage() {
  return <HubPage base="/panorama" title="Toàn cảnh" label="Toàn cảnh" tabs={TABS} />;
}
