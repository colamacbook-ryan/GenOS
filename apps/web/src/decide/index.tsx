/**
 * v0.1.78 (G0) — TRANG GIỮ CHỖ `/decide` (Cần quyết). Gói G2a sở hữu thư mục này. Xuất MỘT thành phần `DecidePage`, nhận `?tab=`.
 * Chờ Sếp quyết: Việc của Mặt tiền cũ (đề xuất + nháp + việc hôm nay); Nháp tin: Bàn làm việc.
 */
import { HubPage, type HubTab } from '../shell/HubPage';
import { OwnerEmbed } from '../shell/OwnerEmbed';
import { LEGACY_SCREENS } from '../shell/legacyScreens';
import { WorkScreen } from '../owner/work/WorkScreen';

const Workbench = LEGACY_SCREENS.workbench;

const TABS: HubTab[] = [
  { key: 'pending', label: 'Chờ Sếp quyết', ownerOnly: true, render: () => <OwnerEmbed><WorkScreen /></OwnerEmbed> },
  { key: 'drafts', label: 'Nháp tin', screen: 'workbench', render: () => <Workbench /> },
];

export function DecidePage() {
  return <HubPage base="/decide" title="Cần quyết" label="Cần quyết" tabs={TABS} />;
}
