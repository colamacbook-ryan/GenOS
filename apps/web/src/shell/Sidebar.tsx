import { type CSSProperties } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import type { NavDomain, NavItem } from '@gen-harness/contracts';
import { ErrorState, Icon, Skeleton, Tooltip, toneColor, toneTint } from '@gen-harness/ui';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';
import { useNavigation } from '../lib/queries';
import { useUiStore } from '../lib/uiStore';
import { useIsMobile } from '../lib/useMediaQuery';
import { AccountFooter } from './AccountFooter';
import { Logo } from './Logo';
import { domainColor, domainOpen, groupAction, groupView, itemTitle, visibleChildren, visibleGroups } from './navModel';

export function Sidebar({ activeKey }: { activeKey: string | null }) {
  const stored = useUiStore((s) => s.sidebarMode);
  // B4: trên điện thoại thanh bên là ngăn kéo trượt ra — luôn hiện đủ tên mục.
  const mobile = useIsMobile();
  const mode = mobile ? 'full' : stored;
  const wide = mode === 'full';
  const nav = useNavigation();
  // v0.1.42 (F-67): phiên bản thật dưới logo — cùng khoá truy vấn với Trợ giúp (HelpPage).
  const about = useQuery({ queryKey: ['system', 'about'], queryFn: ({ signal }) => api.about(signal), staleTime: 5 * 60_000, retry: false });
  // Thứ tự như system_api/org.py: `version` (genh trước, rồi ảnh); bản phát triển ("dev") thì bỏ dòng phiên bản.
  const img = about.data?.image_version;
  const version = about.data ? about.data.version || (img && img !== 'dev' ? img : null) : null;

  return (
    <aside className="sb" id="app-sidebar" data-mode={mode} aria-label="Thanh bên">
      <Logo wide={wide} version={version} />
      <div className="sb-rule" aria-hidden />
      <nav className="sb-nav" aria-label="Danh mục màn hình">
        {nav.isPending ? (
          <NavSkeleton wide={wide} />
        ) : nav.isError ? (
          wide ? (
            <div className="sb-error">
              <ErrorState
                title="Không tải được danh mục"
                onRetry={() => void nav.refetch()}
                retrying={nav.isFetching}
              />
            </div>
          ) : (
            <Tooltip content="Không tải được danh mục — bấm để thử lại" placement="right">
              <button type="button" className="sb-item sb-item--error" onClick={() => void nav.refetch()} aria-label="Thử lại tải danh mục">
                <Icon name="ph ph-warning-circle" size={16} />
              </button>
            </Tooltip>
          )
        ) : nav.data.length === 0 ? (
          wide ? <div className="sb-empty">Vai trò này chưa được cấp màn hình nào.</div> : null
        ) : (
          nav.data.map((dm, di) => <Domain key={dm.domain} dm={dm} index={di} wide={wide} activeKey={activeKey} />)
        )}
      </nav>
      <AccountFooter wide={wide} />
    </aside>
  );
}

function Domain({ dm, index, wide, activeKey }: { dm: NavDomain; index: number; wide: boolean; activeKey: string | null }) {
  const color = domainColor(dm.tone);
  const override = useUiStore((s) => s.domainOpen[dm.domain]);
  const setDomainOpen = useUiStore((s) => s.setDomainOpen);
  const groups = visibleGroups(dm);
  if (!groups.length) return null;
  const collapsible = !!dm.collapsed;
  const items = groups.map((g) => <Group key={g.key ?? g.name} g={g} wide={wide} activeKey={activeKey} level1={!collapsible} />);
  if (!collapsible) {
    return (
      <div className="sb-domain" role="group" aria-label={dm.label}>
        {wide ? (
          <div className="sb-domain__label">
            <Icon name={dm.icon} size={12} color={color} />
            <span className="sb-domain__name" style={{ color }}>
              {dm.label}
            </span>
            <span className="sb-domain__rule" aria-hidden />
            <span className="sb-domain__count">{dm.count} màn</span>
          </div>
        ) : index > 0 ? (
          <div className="sb-rail-rule" aria-hidden />
        ) : null}
        {items}
      </div>
    );
  }
  // v0.1.42: domain thu gọn (Nâng cao) — một nút đầu mục, mặc định đóng trừ khi màn đang mở thuộc domain này.
  const open = domainOpen(dm, activeKey, override);
  const listId = `sb-domain-${dm.domain}`;
  const toggle = (
    <button
      type="button"
      className="sb-item sb-domain-toggle"
      data-level1
      aria-expanded={open}
      aria-controls={open ? listId : undefined}
      aria-label={wide ? undefined : dm.label}
      onClick={() => setDomainOpen(dm.domain, !open)}
    >
      <span className="sb-item__bar" aria-hidden />
      <Icon name={dm.icon} size={16} color={color} />
      {wide ? <span className="sb-item__name">{dm.label}</span> : null}
      {wide ? <span className="sb-domain__count">{dm.count} màn</span> : null}
      {wide ? <Icon className="sb-item__caret" name={open ? 'ph ph-caret-down' : 'ph ph-caret-right'} size={12} /> : null}
    </button>
  );
  return (
    <div className="sb-domain sb-domain--collapsible" role="group" aria-label={dm.label} data-open={open || undefined}>
      {!wide && index > 0 ? <div className="sb-rail-rule" aria-hidden /> : null}
      <div className="sb-group" data-domain={dm.domain}>
        {wide ? (
          toggle
        ) : (
          <Tooltip content={dm.label} sub={open ? 'Bấm để thu gọn' : 'Bấm để mở'} placement="right" delay={150}>
            {toggle}
          </Tooltip>
        )}
      </div>
      {open ? (
        <div className="sb-domain__items" id={listId}>
          {items}
        </div>
      ) : null}
    </div>
  );
}

function Badge({ it }: { it: NavItem }) {
  if (!it.badge) return null;
  const style = {
    '--badge-bg': toneTint(it.badge.tone),
    '--badge-tone': toneColor(it.badge.tone),
  } as CSSProperties;
  return (
    <span className="gh-badge" style={style}>
      {it.badge.value}
    </span>
  );
}

function Group({ g, wide, activeKey, level1 }: { g: NavItem; wide: boolean; activeKey: string | null; level1: boolean }) {
  const navOpen = useUiStore((s) => s.navOpen);
  const setNavOpen = useUiStore((s) => s.setNavOpen);
  const navigate = useNavigate();
  const view = groupView(g, activeKey, navOpen, wide);
  const action = groupAction(g, activeKey, navOpen, wide);
  const isPureGroup = !g.key;
  const kids = visibleChildren(g);

  const inner = (
    <>
      <span className="sb-item__bar" aria-hidden />
      <Icon name={g.icon} size={16} />
      {wide ? <span className="sb-item__name">{g.name}</span> : null}
      {wide ? <Badge it={g} /> : null}
      {view.showCaret ? (
        <Icon className="sb-item__caret" name={view.open ? 'ph ph-caret-down' : 'ph ph-caret-right'} size={12} />
      ) : null}
    </>
  );

  const common = {
    className: 'sb-item',
    'data-level1': level1 || undefined,
    'data-on': view.on || undefined,
    'data-self': view.self || undefined,
    title: wide ? (isPureGroup ? g.name : itemTitle(g)) : undefined,
  };

  let el;
  if (!isPureGroup && g.key) {
    el = (
      <Link
        to={`/${g.key}`}
        {...common}
        aria-current={view.self ? 'page' : undefined}
        aria-label={wide ? undefined : g.name}
        aria-expanded={view.showCaret ? view.open : undefined}
      >
        {inner}
      </Link>
    );
  } else {
    el = (
      <button
        type="button"
        {...common}
        aria-expanded={wide ? view.open : undefined}
        aria-label={wide ? undefined : g.name}
        onClick={() => {
          if (action.type === 'toggle') setNavOpen(action.group, action.open);
          else if (action.type === 'navigate') navigate(`/${action.key}`);
        }}
      >
        {inner}
      </button>
    );
  }

  return (
    <div className="sb-group" data-screen={g.key ?? undefined}>
      {wide ? (
        el
      ) : (
        <Tooltip content={g.name} sub={g.en ?? undefined} placement="right" delay={150}>
          {el}
        </Tooltip>
      )}
      {view.open ? (
        <div className="sb-children" role="group" aria-label={g.name}>
          {kids.map((c) =>
            c.key ? (
              <Link
                key={c.key}
                to={`/${c.key}`}
                className="sb-child"
                data-screen={c.key}
                data-on={c.key === activeKey || undefined}
                aria-current={c.key === activeKey ? 'page' : undefined}
                title={itemTitle(c)}
              >
                <span className="sb-child__dot" aria-hidden />
                <span className="sb-child__name">{c.name}</span>
                <Badge it={c} />
              </Link>
            ) : null,
          )}
        </div>
      ) : null}
    </div>
  );
}

function NavSkeleton({ wide }: { wide: boolean }) {
  const rows = [6, 1];
  return (
    <div aria-busy="true" aria-label="Đang tải danh mục">
      {rows.map((n, d) => (
        <div className="sb-domain" key={d}>
          {wide ? (
            <div className="sb-domain__label">
              <Skeleton width={90} height={9} />
            </div>
          ) : d > 0 ? (
            <div className="sb-rail-rule" />
          ) : null}
          {Array.from({ length: n }, (_, i) => (
            <div className="sb-item sb-item--skeleton" key={i}>
              <Skeleton width={16} height={16} radius={4} />
              {wide ? <Skeleton width={`${50 + ((i * 17) % 40)}%`} height={10} /> : null}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
