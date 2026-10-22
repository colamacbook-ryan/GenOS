import { useQuery } from '@tanstack/react-query';
import { Outlet } from 'react-router-dom';
import { api } from './lib/api';
import { qk } from './lib/queries';
import { useThemeSync } from './lib/theme';
import { PinDialogHost } from './shell/PinDialogHost';
import { ToastHost } from './shell/ToastHost';

/** B7: giữ <html data-theme> khớp lựa chọn. Chỉ ĐỌC /auth/me đã có trong bộ nhớ đệm (không tự gọi — trang đăng
 *  nhập/thiết lập không được hỏi phiên), nên trước khi đăng nhập dùng lựa chọn gần nhất trên trình duyệt này. */
function ThemeSync() {
  const me = useQuery({ queryKey: qk.me, queryFn: ({ signal }) => api.auth.me(signal), enabled: false });
  useThemeSync(me.data?.id);
  return null;
}

export function RootLayout() {
  return (
    <>
      <ThemeSync />
      <Outlet />
      <PinDialogHost />
      <ToastHost />
    </>
  );
}
