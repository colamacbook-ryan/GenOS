import { Link, Navigate, useLocation } from 'react-router-dom';
import { SCREEN_BY_KEY } from '@gen-harness/contracts';
import { EmptyState, Icon, Tabs } from '@gen-harness/ui';
import { can, useCan } from '../../lib/permissions';
import { useMe } from '../../lib/queries';
import { useUrlState } from '../../lib/uiStore';
import { ScreenHead, SkeletonLines } from '../common';
import { BrainTab } from './BrainTab';
import { LogTab } from './LogTab';
import { OrgTab } from './OrgTab';
import { RolesTab } from './RolesTab';
import { StorageTab } from './StorageTab';
import { movedTabTarget } from './settingsModel';

type SysTab = 'storage' | 'org' | 'brain' | 'roles' | 'log';

/**
 * v0.1.42 (F-7): Cài đặt — 5 tab theo thứ tự, mỗi tab có quyền riêng; chỉ hiện tab vai trò được xem (Manager chỉ có
 * `audit.read` ⇒ đúng 1 tab Nhật ký, không gọi /providers). Kênh & đăng nhập chuyển sang Kết nối (/connections),
 * Người dùng sang Đội ngũ (/team) — link cũ `?tab=channels`, `?tab=users` tự chuyển tới đó khi vai trò mở được trang
 * đích (giữ các tham số khác, vd `?gen=`); không thì ở lại Cài đặt, tab đầu tiên được phép (settingsModel).
 */
const TABS: Array<{ key: SysTab; label: string; count?: string; genTarget?: string; perm: 'system.read' | 'audit.read' }> = [
  { key: 'storage', label: 'Sao lưu & cập nhật', count: 'sao lưu · bản mới', genTarget: 'system.tab.storage', perm: 'system.read' },
  { key: 'org', label: 'Tổ chức', count: 'tên · giờ', genTarget: 'system.tab.org', perm: 'system.read' },
  { key: 'brain', label: 'Bộ não AI', count: 'model · hạn mức', genTarget: 'system.tab.brain', perm: 'system.read' },
  { key: 'roles', label: 'Quyền hạn', count: 'vai trò', perm: 'system.read' },
  { key: 'log', label: 'Nhật ký', count: 'ai đã làm gì', perm: 'audit.read' },
];

export function SystemScreen() {
  const meta = SCREEN_BY_KEY.system;
  const canSystem = useCan('system.read');
  const canAudit = useCan('audit.read');
  const me = useMe();
  const isOwner = me.data?.role?.code === 'owner';
  const allowed = TABS.filter((t) => (t.perm === 'audit.read' ? canAudit : canSystem));
  const fallback: SysTab = allowed[0]?.key ?? 'storage';
  const [tab, setTab] = useUrlState<string>('tab', fallback);
  const { search } = useLocation();
  const moved = me.data ? movedTabTarget(search, (perm) => can(me.data, perm)) : null;
  if (moved) return <Navigate to={moved} replace />;
  const current = allowed.find((t) => t.key === tab)?.key ?? fallback;
  const head = (
    <ScreenHead
      title={meta.title}
      description={meta.description}
      maxWidth={700}
      actions={
        <nav className="sys-links" aria-label="Liên kết cài đặt">
          <Link to="/account" className="sys-link">
            <Icon name="ph ph-user-circle" size={13} />
            Tài khoản &amp; PIN của tôi
          </Link>
          <Link to="/help" className="sys-link">
            <Icon name="ph ph-question" size={13} />
            Trợ giúp
          </Link>
          {isOwner ? (
            <Link to="/guide" className="sys-link">
              <Icon name="ph ph-list-checks" size={13} />
              Hướng dẫn thiết lập
            </Link>
          ) : null}
        </nav>
      }
    />
  );
  if (me.isPending) {
    return (
      <div className="screen">
        {head}
        <SkeletonLines rows={3} padding="10px 0" />
      </div>
    );
  }
  if (!allowed.length) {
    return (
      <div className="screen">
        {head}
        <div className="gh-card">
          <EmptyState icon="ph ph-lock-simple" title="Vai trò của bạn không xem được Cài đặt" />
        </div>
      </div>
    );
  }
  return (
    <div className="screen">
      {head}
      <Tabs items={allowed} value={current} onChange={(k) => setTab(k)} label="Cài đặt" idPrefix="sys" />
      <div role="tabpanel" id={`sys-panel-${current}`} aria-labelledby={`sys-${current}`} className="sys-tabs-panel">
        {current === 'storage' ? (
          <StorageTab />
        ) : current === 'org' ? (
          <OrgTab />
        ) : current === 'brain' ? (
          <BrainTab />
        ) : current === 'roles' ? (
          <RolesTab />
        ) : (
          <LogTab />
        )}
      </div>
    </div>
  );
}
