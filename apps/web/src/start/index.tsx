/**
 * v0.1.78 (G0) — TRANG GIỮ CHỖ `/admin/start` (Bắt đầu). Gói G5 sở hữu thư mục này. Xuất MỘT thành phần `StartPage`.
 * Tạm: Hướng dẫn thiết lập (có thẻ dẫn tới "Việc Sếp cần làm" ở đầu trang).
 */
import { HubPage, type HubTab } from '../shell/HubPage';
import { OwnerEmbed } from '../shell/OwnerEmbed';
import { GuidePage } from '../guide/GuidePage';
import { MoreScreen } from '../owner/MoreScreen';

// Tab "Việc thiết lập tiếp" (Sếp): giữ lối vào "Đổi nguồn AI", tài khoản, việc nên làm tiếp của Mặt tiền cũ (`/owner/them`) cho tới khi G5 gộp.
const TABS: HubTab[] = [
  { key: 'guide', label: 'Thiết lập', render: () => <GuidePage /> },
  { key: 'more', label: 'Việc nên làm tiếp', ownerOnly: true, render: () => <OwnerEmbed><MoreScreen /></OwnerEmbed> },
];

export function StartPage() {
  return <HubPage base="/admin/start" title="Bắt đầu" label="Bắt đầu" tabs={TABS} />;
}
