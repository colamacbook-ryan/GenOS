/**
 * v0.1.79 (G1) — TRANG TÌNH BÁO `/intel`, trang chủ của Sếp. Hợp đồng HubPage (G0): xuất đúng MỘT thành phần trang `IntelPage`, nhận `?tab=`,
 * không vẽ thanh bên/header.
 * Sếp: Hôm nay (tín hiệu mở theo mức 🔴🟠⚪ + việc Sếp cần làm + 3 dòng đầu Cần quyết) · Tín hiệu (bảng + ngăn chi tiết + Đúng/Sai) ·
 * Chạy thử 14 ngày (ramp R4, M1, cổng nào đủ để nhắn ngay) · Thiết lập (3 bước + khoá/xác minh, cần PIN như cũ) · Tổng quan (đích Gen `overview.*`) ·
 * Thông tin (dòng tin Mặt tiền cũ, giữ để không mất Trả lời/Tắt tiếng) · Hộp thư (`/intel/inbox`).
 * Vai khác Owner: Hôm nay (Tổng quan) + Hộp thư, lọc theo quyền như cũ. Sự cố hệ thống không có ở đây — chip sức khoẻ và chuông lo việc đó.
 */
import { HubPage, type HubTab } from '../shell/HubPage';
import { OwnerEmbed } from '../shell/OwnerEmbed';
import { LEGACY_SCREENS } from '../shell/legacyScreens';
import { FeedScreen } from '../owner/feed/FeedScreen';
import { OverviewScreen } from '../screens/queue/OverviewScreen';
import { IntelCard } from './IntelCard';
import { IntelSetupCard } from './IntelSetupCard';
import { SignalGrid } from './SignalGrid';
import { TodayTab } from './TodayTab';
import './intelHub.css';

const InboxScreen = LEGACY_SCREENS.inbox;

const TABS: HubTab[] = [
  { key: 'today', label: 'Hôm nay', ownerOnly: true, render: () => <TodayTab /> },
  { key: 'today', label: 'Hôm nay', notOwner: true, screen: 'overview', render: () => <OverviewScreen /> },
  { key: 'signals', label: 'Tín hiệu', ownerOnly: true, render: () => <SignalGrid /> },
  { key: 'trial', label: 'Chạy thử 14 ngày', ownerOnly: true, render: () => <IntelCard part="trial" /> },
  {
    key: 'setup',
    label: 'Thiết lập',
    ownerOnly: true,
    render: () => (
      <div className="ih-stack">
        <IntelSetupCard />
        <IntelCard part="controls" />
      </div>
    ),
  },
  // Review v0.1.78: Sếp vẫn mở được Tổng quan cũ (đích Gen `overview.*`, thẻ "Hôm nay của Sếp") ở tab riêng.
  { key: 'overview', label: 'Tổng quan', ownerOnly: true, screen: 'overview', render: () => <OverviewScreen /> },
  { key: 'feed', label: 'Thông tin', ownerOnly: true, render: () => <OwnerEmbed><FeedScreen /></OwnerEmbed> },
  { key: 'inbox', label: 'Hộp thư', screen: 'inbox', path: 'inbox', render: () => <InboxScreen /> },
];

export function IntelPage() {
  return <HubPage base="/intel" title="Tình báo" label="Tình báo" tabs={TABS} />;
}
