/**
 * v0.1.78 (G0) — TRANG GIỮ CHỖ `/admin/start` (Bắt đầu). Gói G5 sở hữu thư mục này. Xuất MỘT thành phần `StartPage`.
 * Tạm: Hướng dẫn thiết lập (có thẻ dẫn tới "Việc Sếp cần làm" ở đầu trang).
 */
import { HubPage, type HubTab } from '../shell/HubPage';
import { GuidePage } from '../guide/GuidePage';

const TABS: HubTab[] = [{ key: 'guide', label: 'Thiết lập', render: () => <GuidePage /> }];

export function StartPage() {
  return <HubPage base="/admin/start" title="Bắt đầu" label="Bắt đầu" tabs={TABS} />;
}
