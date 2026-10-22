import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { Icon, Skeleton, Tooltip } from '@gen-harness/ui';
import { api } from '../lib/api';
import { useMe } from '../lib/queries';
import { queryClient } from '../lib/queryClient';
import { useUiStore } from '../lib/uiStore';
import { initials, roleLine } from './people';

export function AccountFooter({ wide }: { wide: boolean }) {
  const me = useMe();
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const name = me.data?.display_name ?? '';
  const button = (
    <button
      ref={trigger}
      type="button"
      className="sb-account"
      aria-haspopup="menu"
      aria-expanded={open}
      aria-label={wide ? undefined : `Tài khoản ${name}`}
      onClick={() => setOpen((o) => !o)}
      disabled={me.isPending}
    >
      {me.isPending ? (
        <Skeleton width={29} height={29} radius="50%" />
      ) : (
        <span className="sb-avatar" aria-hidden>
          {initials(name)}
        </span>
      )}
      {wide ? (
        <span className="sb-account__text">
          {me.isPending ? (
            <>
              <Skeleton width={110} height={10} />
              <Skeleton width={80} height={8} style={{ marginTop: 5 }} />
            </>
          ) : me.isError ? (
            <span className="sb-account__name">Không tải được tài khoản</span>
          ) : (
            <>
              <span className="sb-account__name">{name}</span>
              <span className="sb-account__role">{roleLine(me.data!)}</span>
            </>
          )}
        </span>
      ) : null}
      {wide ? <Icon className="sb-account__caret" name="ph ph-caret-up-down" size={14} /> : null}
    </button>
  );

  return (
    <div className="sb-footer" ref={wrap}>
      {wide ? button : <Tooltip content={name || 'Tài khoản'} placement="right">{button}</Tooltip>}
      {open ? (
        <AccountMenu
          email={me.data?.email}
          isOwner={me.data?.role?.code === 'owner'}
          onClose={(refocus) => {
            setOpen(false);
            if (refocus) trigger.current?.focus();
          }}
        />
      ) : null}
    </div>
  );
}

function AccountMenu({ email, isOwner, onClose }: { email?: string; isOwner?: boolean; onClose: (refocus: boolean) => void }) {
  const mode = useUiStore((s) => s.sidebarMode);
  const toggleSidebar = useUiStore((s) => s.toggleSidebarMode);
  const navigate = useNavigate();
  const menu = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    menu.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, []);

  const logout = async () => {
    setBusy(true);
    try {
      await api.auth.logout();
    } catch {
      // the session is gone either way; fall through to the login page
    }
    queryClient.clear();
    navigate('/login', { replace: true });
  };

  const onKeyDown = (e: KeyboardEvent) => {
    const items = Array.from(menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      items[(i + 1) % items.length]?.focus();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      items[(i - 1 + items.length) % items.length]?.focus();
    } else if (e.key === 'Escape' || e.key === 'Tab') {
      if (e.key === 'Escape') e.preventDefault();
      onClose(e.key === 'Escape');
    }
  };

  return (
    <div className="sb-menu" role="menu" aria-label="Tài khoản" ref={menu} onKeyDown={onKeyDown}>
      {email ? <div className="sb-menu__email">{email}</div> : null}
      <button
        type="button"
        role="menuitem"
        className="sb-menu__item"
        onClick={() => {
          navigate('/account');
          onClose(false);
        }}
      >
        <Icon name="ph ph-user-circle" size={15} />
        Tài khoản của tôi
      </button>
      {isOwner ? (
        <button
          type="button"
          role="menuitem"
          className="sb-menu__item"
          onClick={() => {
            navigate('/social');
            onClose(false);
          }}
        >
          <Icon name="ph ph-facebook-logo" size={15} />
          Tài khoản mạng xã hội
        </button>
      ) : null}
      <button
        type="button"
        role="menuitem"
        className="sb-menu__item"
        onClick={() => {
          navigate('/help');
          onClose(false);
        }}
      >
        <Icon name="ph ph-question" size={15} />
        Trợ giúp
      </button>
      {isOwner ? (
        <button
          type="button"
          role="menuitem"
          className="sb-menu__item"
          onClick={() => {
            navigate('/guide');
            onClose(false);
          }}
        >
          <Icon name="ph ph-list-checks" size={15} />
          Hướng dẫn thiết lập
        </button>
      ) : null}
      <button
        type="button"
        role="menuitem"
        className="sb-menu__item"
        onClick={() => {
          toggleSidebar();
          onClose(true);
        }}
      >
        <Icon name="ph ph-sidebar-simple" size={15} />
        {mode === 'full' ? 'Thu gọn thanh bên' : 'Mở rộng thanh bên'}
      </button>
      <div className="sb-menu__sep" role="separator" />
      <button type="button" role="menuitem" className="sb-menu__item" onClick={() => void logout()} disabled={busy}>
        <Icon name="ph ph-sign-out" size={15} />
        Đăng xuất
      </button>
    </div>
  );
}
