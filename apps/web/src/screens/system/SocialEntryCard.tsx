import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Icon } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { useMe } from '../../lib/queries';
import { qkSocial } from '../../social/socialModel';
import { CardError, Panel, SkeletonLines } from '../common';
import { ConnectionStatusPill } from '../connections/ConnectionStatusPill';
import { facebookStatus } from '../connections/connectionsModel';

/**
 * Thẻ Facebook ở Kết nối (v0.1.42, trước ở Hệ thống › Kênh — v0.1.39 F-32) — lối vào trang Tài khoản mạng xã hội
 * (/social), nơi Sếp đăng nhập Facebook ngay trong app để Gen đọc thông báo và tin nhắn. Chỉ Owner (trang /social chỉ
 * cho Owner) — target Gen `system.channels.facebook` khai `permission: 'roles.manage'` (quyền chỉ Owner có) để Gen
 * không chỉ một Admin tới thẻ không hiện trên màn hình của họ.
 */
export function SocialEntryCard() {
  const me = useMe();
  if (me.data?.role?.code !== 'owner') return null;
  return <SocialEntryBody />;
}

function SocialEntryBody() {
  const accounts = useQuery({ queryKey: qkSocial.accounts, queryFn: ({ signal }) => api.social.accounts.list(signal) });
  const status = useQuery({ queryKey: qkSocial.status, queryFn: ({ signal }) => api.social.status(signal) });
  const items = accounts.data?.items;
  const fb = (items ?? []).filter((a) => a.platform === 'facebook' && a.status !== 'revoked');
  return (
    <Panel
      genTarget="system.channels.facebook"
      title="Facebook"
      kicker={
        accounts.data
          ? fb.length
            ? `${fb.length} tài khoản · đọc thông báo và tin nhắn${status.data?.halted ? ' · đang dừng khẩn' : ''}`
            : 'Đọc thông báo và tin nhắn — đăng nhập ngay trong app'
          : 'Đọc thông báo và tin nhắn'
      }
      label="Facebook"
      bodyClass="pin-body"
      aside={accounts.data ? <ConnectionStatusPill status={facebookStatus({ accounts: items, halted: !!status.data?.halted })} /> : undefined}
    >
      {accounts.isPending ? (
        <SkeletonLines rows={1} padding="0" />
      ) : accounts.isError ? (
        <CardError error={accounts.error} onRetry={() => void accounts.refetch()} retrying={accounts.isFetching} />
      ) : null}
      <div className="pin-actions">
        <Link to="/social" className="gh-btn gh-btn--secondary" data-main-action>
          <Icon name="ph ph-facebook-logo" size={14} />
          Mở Facebook
        </Link>
      </div>
    </Panel>
  );
}
