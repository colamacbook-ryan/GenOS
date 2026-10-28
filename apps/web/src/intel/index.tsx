/**
 * v0.1.78 (G0) — TRANG GIỮ CHỖ `/intel` (Tình báo). Gói G1 sở hữu trọn thư mục này và thay file bằng IntelHub (4 tab).
 * Hợp đồng: xuất đúng MỘT thành phần trang `IntelPage`, nhận `?tab=`, không vẽ thanh bên/header.
 * Hôm nay: Sếp = Tình báo của Mặt tiền cũ; vai khác = Hôm nay (Tổng quan). Tín hiệu: hàng đợi cảnh báo của Mặt tiền (Sếp);
 * Hộp thư (đường con `/intel/inbox`) vì Hộp thư tự dùng `?tab=`.
 */
import { HubPage, type HubTab } from '../shell/HubPage';
import { OwnerEmbed } from '../shell/OwnerEmbed';
import { LEGACY_SCREENS } from '../shell/legacyScreens';
import { FeedScreen } from '../owner/feed/FeedScreen';
import { IntelScreen } from '../owner/intel/IntelScreen';
import { OverviewScreen } from '../screens/queue/OverviewScreen';

const InboxScreen = LEGACY_SCREENS.inbox;

const TABS: HubTab[] = [
  { key: 'today', label: 'Hôm nay', ownerOnly: true, render: () => <OwnerEmbed><IntelScreen /></OwnerEmbed> },
  { key: 'today', label: 'Hôm nay', notOwner: true, screen: 'overview', render: () => <OverviewScreen /> },
  // Review v0.1.78: Sếp vẫn mở được Tổng quan cũ (đích Gen `overview.*`, thẻ "Hôm nay của Sếp") ở tab riêng.
  { key: 'overview', label: 'Tổng quan', ownerOnly: true, screen: 'overview', render: () => <OverviewScreen /> },
  { key: 'signals', label: 'Tín hiệu', ownerOnly: true, render: () => <OwnerEmbed><FeedScreen /></OwnerEmbed> },
  { key: 'inbox', label: 'Hộp thư', screen: 'inbox', path: 'inbox', render: () => <InboxScreen /> },
];

export function IntelPage() {
  return <HubPage base="/intel" title="Tình báo" label="Tình báo" tabs={TABS} />;
}
