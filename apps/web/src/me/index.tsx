/**
 * v0.1.78 (G0) — TRANG GIỮ CHỖ `/me` (Tôi). Gói G3 sở hữu thư mục này. Xuất MỘT thành phần `MePage`, nhận `?tab=`.
 * Tạm: thẻ "Gen nhớ" (đang nằm ở Bộ não AI) — G3 dời hẳn vào đây và dựng 6 tab.
 */
import { HubPage, type HubTab } from '../shell/HubPage';
import { GenMemoryCard } from '../screens/system/GenMemoryCard';

const TABS: HubTab[] = [{ key: 'memory', label: 'Gen nhớ', ownerOnly: true, render: () => <GenMemoryCard /> }];

export function MePage() {
  return <HubPage base="/me" title="Tôi" label="Tôi" tabs={TABS} />;
}
