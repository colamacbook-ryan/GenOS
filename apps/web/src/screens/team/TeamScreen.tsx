import { Link } from 'react-router-dom';
import { SCREEN_BY_KEY } from '@gen-harness/contracts';
import { Icon } from '@gen-harness/ui';
import { useNavigation } from '../../lib/queries';
import { findActive } from '../../shell/navModel';
import { CardError, ScreenHead, SkeletonLines } from '../common';
import { STAFF_HOWTO } from '../people/peopleModel';
import { UsersTab } from '../system/UsersTab';

const STAFF_LINKS = [
  { key: 'people', to: '/people', icon: 'ph ph-users-three', label: 'Đánh giá con người', hint: 'Điểm số có chứng cứ, xu hướng theo tuần' },
  { key: 'care', to: '/care', icon: 'ph ph-heartbeat', label: 'Chất lượng chăm sóc', hint: 'Cách chăm khách, không chỉ số lần nhắn' },
];

/**
 * v0.1.42 (F-7): Đội ngũ — người dùng Console (mời, đổi vai trò, khoá; trước ở Điều khiển hệ thống › Người dùng) và
 * lối vào Đánh giá con người / Chất lượng chăm sóc. Thanh bên ẩn hai mục này khi chưa có nhân viên, nhưng ở đây vẫn
 * giữ link (màn tự giải thích cách đánh dấu nhân viên ở hồ sơ người) để không thành ngõ cụt.
 */
export function TeamScreen() {
  const meta = SCREEN_BY_KEY.team;
  const nav = useNavigation();
  const links = STAFF_LINKS.flatMap((l) => {
    const hit = findActive(nav.data, l.key);
    return hit ? [{ ...l, noStaff: !!hit.item.hidden }] : [];
  });
  const noStaff = links.some((l) => l.noStaff);
  const canDirectory = !!findActive(nav.data, 'directory');
  return (
    <div className="screen">
      <ScreenHead title={meta.title} description={meta.description} maxWidth={meta.descMaxWidth} />
      <div className="sys-tabs-col">
        <UsersTab />
        {nav.isPending ? (
          <SkeletonLines rows={2} padding="0" />
        ) : nav.isError ? (
          <CardError error={nav.error} onRetry={() => void nav.refetch()} retrying={nav.isFetching} />
        ) : links.length ? (
          <>
            <div className="team-links" aria-label="Đánh giá và chăm sóc">
              {links.map((l) => (
                <Link key={l.key} to={l.to} className="gh-card team-link">
                  <Icon name={l.icon} size={18} />
                  <span className="team-link__text">
                    <span className="team-link__title">{l.label}</span>
                    <span className="team-link__hint">{l.noStaff ? 'Chưa có nhân viên nào — chưa có điểm' : l.hint}</span>
                  </span>
                  <Icon name="ph ph-caret-right" size={13} />
                </Link>
              ))}
            </div>
            {noStaff ? (
              <p className="muted-note team-note" role="note">
                Chưa có nhân viên nào nên thanh bên tạm ẩn Đánh giá và Chăm sóc. {STAFF_HOWTO}{' '}
                {canDirectory ? <Link to="/directory">Mở Khách &amp; Nhóm</Link> : null}
              </p>
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}
