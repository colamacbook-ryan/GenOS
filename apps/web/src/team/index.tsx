/** v0.1.78 (G0) — `/team` (Đội ngũ): Đánh giá nhân viên + Chất lượng chăm sóc. Chỉ hiện ở thanh bên khi đã có nhân viên. */
import { Navigate, useSearchParams } from 'react-router-dom';
import { HubPage, type HubTab } from '../shell/HubPage';
import { LEGACY_SCREENS } from '../shell/legacyScreens';

const tab = (key: string, tabKey: string, label: string): HubTab => {
  const Screen = LEGACY_SCREENS[key];
  return { key: tabKey, label, screen: key, render: () => <Screen /> };
};

const TABS: HubTab[] = [tab('people', 'reviews', 'Đánh giá nhân viên'), tab('care', 'care', 'Chất lượng chăm sóc')];

export function TeamPage() {
  const [params] = useSearchParams();
  // Link cũ «Đội ngũ › Người dùng» (v0.1.42–v0.1.77: `/team?tab=users`) ⇒ Quản trị › Người & quyền.
  if (params.get('tab') === 'users') return <Navigate to="/admin/access" replace />;
  return <HubPage base="/team" title="Đội ngũ" label="Đội ngũ" tabs={TABS} />;
}
