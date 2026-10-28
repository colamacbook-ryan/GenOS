import { useEffect, type ReactNode } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { Card, EmptyState, Tabs, type TabItem } from '@gen-harness/ui';
import { can } from '../lib/permissions';
import { useMe, useNavigation } from '../lib/queries';
import { screenKeys } from './navModel';

/**
 * v0.1.78 (G0) — khung trang có tab dùng chung cho mọi mục thanh bên (Tình báo, Cần quyết, Toàn cảnh, Quản trị…).
 * HỢP ĐỒNG với các gói G1–G5: trang nhận `?tab=` từ URL, KHÔNG tự vẽ thanh bên/header (AppShell lo), mỗi thư mục xuất một
 * thành phần trang. Tab lọc theo vai: `screen` = khoá màn cũ phải có trong GET /navigation, `perm` = quyền, `ownerOnly`.
 * Tab có `path` mở bằng đường con (`/intel/inbox`) vì màn nhúng tự dùng `?tab=` (Hộp thư, Plugin).
 */
export interface HubTab {
  key: string;
  label: string;
  screen?: string;
  perm?: string;
  ownerOnly?: boolean;
  /** Chỉ vai KHÁC Owner (thay thế một tab ownerOnly cùng chỗ). */
  notOwner?: boolean;
  path?: string;
  render: () => ReactNode;
}

export function HubPage({ base, title, label, tabs }: { base: string; title: string; label: string; tabs: HubTab[] }) {
  const me = useMe();
  const nav = useNavigation();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [params] = useSearchParams();
  useEffect(() => {
    document.title = `${title} · Gen-Harness`;
  }, [title]);

  const keys = screenKeys(nav.data);
  const owner = me.data?.role?.code === 'owner';
  const visible = tabs.filter(
    (t) => (!t.screen || keys.has(t.screen)) && (!t.perm || can(me.data, t.perm)) && (!t.ownerOnly || owner) && (!t.notOwner || !owner),
  );
  if (!visible.length) {
    return (
      <div className="screen">
        <Card padded={false}>
          <EmptyState icon="ph ph-lock-simple" title="Vai trò của bạn không có quyền xem trang này" description="Liên hệ Owner nếu cần mở quyền." />
        </Card>
      </div>
    );
  }
  const clean = pathname.replace(/\/+$/, '');
  const byPath = visible.find((t) => t.path && clean === `${base}/${t.path}`);
  const q = params.get('tab');
  const first = visible.find((t) => !t.path) ?? visible[0];
  const current = byPath ?? visible.find((t) => !t.path && t.key === q) ?? first;
  const items: TabItem[] = visible.map((t) => ({ key: t.key, label: t.label }));
  const open = (key: string) => {
    const t = visible.find((x) => x.key === key);
    if (!t) return;
    if (t.path) navigate(`${base}/${t.path}`);
    else navigate(t === first ? base : `${base}?tab=${t.key}`);
  };
  return (
    <div className="screen hub" data-testid={`hub-${base.replace(/^\//, '').replace(/\//g, '-')}`}>
      {visible.length > 1 ? <Tabs items={items} value={current.key} onChange={open} label={label} idPrefix="hub" /> : null}
      <div role="tabpanel" id={`hub-panel-${current.key}`} aria-labelledby={`hub-${current.key}`} className="hub__panel">
        {current.render()}
      </div>
    </div>
  );
}
