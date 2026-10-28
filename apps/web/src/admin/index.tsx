/**
 * v0.1.78 (G0) — các trang Quản trị `/admin/*`: Kết nối · Người & quyền · Sao lưu & cập nhật · Tổ chức · Nhật ký · Nâng cao.
 * (Bắt đầu nằm ở `start/`.) Nhúng các tab của Cài đặt cũ và màn kỹ thuật; không có màn mới.
 */
import { HubPage, type HubTab } from '../shell/HubPage';
import { LEGACY_SCREENS } from '../shell/legacyScreens';
import { ConnectionsScreen } from '../screens/connections/ConnectionsScreen';
import { BrainTab } from '../screens/system/BrainTab';
import { LogTab } from '../screens/system/LogTab';
import { OrgTab } from '../screens/system/OrgTab';
import { RolesTab } from '../screens/system/RolesTab';
import { StorageTab } from '../screens/system/StorageTab';
import { UsersTab } from '../screens/system/UsersTab';

export function ConnectionsPage() {
  return <HubPage base="/admin/connections" title="Kết nối" label="Kết nối" tabs={[{ key: 'all', label: 'Kết nối', render: () => <ConnectionsScreen /> }]} />;
}

const ACCESS: HubTab[] = [
  { key: 'users', label: 'Người dùng', perm: 'roles.manage', render: () => <UsersTab /> },
  { key: 'roles', label: 'Quyền hạn', perm: 'system.read', render: () => <RolesTab /> },
];
export function AccessPage() {
  return <HubPage base="/admin/access" title="Người & quyền" label="Người & quyền" tabs={ACCESS} />;
}

export function BackupPage() {
  return <HubPage base="/admin/backup" title="Sao lưu & cập nhật" label="Sao lưu & cập nhật" tabs={[{ key: 'storage', label: 'Sao lưu & cập nhật', perm: 'system.read', render: () => <StorageTab /> }]} />;
}

export function OrgPage() {
  return <HubPage base="/admin/org" title="Tổ chức" label="Tổ chức" tabs={[{ key: 'org', label: 'Tổ chức', perm: 'system.read', render: () => <OrgTab /> }]} />;
}

export function LogPage() {
  return <HubPage base="/admin/log" title="Nhật ký" label="Nhật ký" tabs={[{ key: 'log', label: 'Nhật ký', perm: 'audit.read', render: () => <LogTab /> }]} />;
}

const adv = (key: string, label: string, extra: Partial<HubTab> = {}): HubTab => {
  const Screen = LEGACY_SCREENS[key];
  return { key, label, screen: key, render: () => <Screen />, ...extra };
};
// Dữ liệu (5) · Agent & Model (3) · Bộ não AI · Plugin (đường con: Plugin tự dùng `?tab=`).
const ADVANCED: HubTab[] = [
  adv('raw', 'Kho thô'),
  adv('listenlog', 'Nhật ký lắng nghe'),
  adv('rules', 'Quy tắc lọc (đường lọc cũ)'),
  adv('clean', 'Kho sạch'),
  adv('identity', 'Hợp nhất danh tính'),
  adv('agents', 'Danh tính Agent'),
  adv('api', 'API & Model'),
  adv('mcp', 'MCP Hub'),
  { key: 'brain', label: 'Bộ não AI', screen: 'system', perm: 'system.read', render: () => <BrainTab /> },
  adv('plugins', 'Plugin', { path: 'plugins' }),
];
export function AdvancedPage() {
  return <HubPage base="/admin/advanced" title="Nâng cao" label="Nâng cao" tabs={ADVANCED} />;
}
