/**
 * v0.1.42 (F-7): link cũ của Cài đặt (`/system?tab=channels|users`, kể cả trong thông báo đã lưu từ bản trước) →
 * trang mới. Chỉ chuyển khi vai trò mở được trang đích; không thì ở lại Cài đặt (tab đầu tiên được phép). Giữ các
 * tham số khác (vd `?gen=<id>` mở khung Gen), bỏ `tab`. Hàm thuần — test được.
 */
export const MOVED_TABS: Record<string, { to: string; perm: string }> = {
  channels: { to: '/connections', perm: 'system.read' },
  users: { to: '/team', perm: 'roles.manage' },
};

export function movedTabTarget(search: string, can: (perm: string) => boolean): string | null {
  const params = new URLSearchParams(search);
  const moved = MOVED_TABS[params.get('tab') ?? ''];
  if (!moved || !can(moved.perm)) return null;
  params.delete('tab');
  const rest = params.toString();
  return `${moved.to}${rest ? `?${rest}` : ''}`;
}
