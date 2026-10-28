import { createBrowserRouter, type RouteObject } from 'react-router-dom';
import { setKitNavigator } from '@gen-harness/ui';
import { setNavigator } from './lib/navigation';
import { UrlStateSync } from './lib/uiStore';
import { LoginPage } from './pages/LoginPage';
import { AccountPage } from './account/AccountPage';
import { ForcePasswordPage } from './account/ForcePasswordPage';
import { BossChecksPage } from './guide/BossChecksPage';
import { HelpPage } from './help/HelpPage';
import { GuideStepPage } from './guide/GuideStepPage';
import { NotFoundPage, RouteErrorPage } from './shell/ErrorPage';
import { SetupPage } from './setup/SetupPage';
import { AppShell } from './shell/AppShell';
import type { ReactElement } from 'react';
import { ACCOUNT_CRUMBS, BOSS_CHECKS_CRUMBS, GUIDE_CRUMBS, HELP_CRUMBS, SOCIAL_CRUMBS, type RouteHandle } from './shell/routeHandles';
import { HomeRedirect } from './shell/HomeRedirect';
import { SocialPage } from './social/SocialPage';
import { SocialWriteRiskPage } from './social/SocialWriteRiskPage';
import { RootLayout } from './RootLayout';
import { OwnerShell } from './owner/OwnerShell';
import { IntelPage } from './intel';
import { DecidePage } from './decide';
import { WorkPage } from './work';
import { PanoramaPage } from './panorama';
import { MePage } from './me';
import { TeamPage } from './team';
import { StartPage } from './start';
import { AccessPage, AdvancedPage, BackupPage, ConnectionsPage, LogPage, OrgPage } from './admin';
import { LegacyRedirect } from './shell/LegacyRedirect';
import { PageGate } from './shell/PageGate';
import { LEGACY_PATHS } from './shell/legacyRedirects';

/**
 * v0.1.78 (G0): một route cho mỗi mục thanh bên (packages/contracts/src/screens.ts › SCREEN_PATH), lồng theo miền để breadcrumb
 * lấy từ cùng cây với thanh bên. Màn Console cũ KHÔNG còn route riêng: đường cũ chuyển hướng (shell/legacyRedirects.ts) và màn cũ
 * được nhúng thành tab. `/intel/inbox` và `/admin/advanced/plugins` là đường con vì màn nhúng tự dùng `?tab=`.
 */
export function buildConsoleRoutes(): RouteObject[] {
  const page = (path: string, screen: string, el: ReactElement): RouteObject => ({
    path,
    handle: { screen } satisfies RouteHandle,
    element: <PageGate screenKey={screen}>{el}</PageGate>,
  });
  return [
    {
      id: 'domain:daily',
      handle: { domain: 'daily' } satisfies RouteHandle,
      children: [
        page('intel', 'intel', <IntelPage />),
        page('intel/inbox', 'intel', <IntelPage />),
        page('decide', 'decide', <DecidePage />),
        page('work', 'work', <WorkPage />),
        page('panorama', 'panorama', <PanoramaPage />),
        page('me', 'me', <MePage />),
        page('team', 'team', <TeamPage />),
      ],
    },
    {
      id: 'domain:admin',
      handle: { domain: 'admin' } satisfies RouteHandle,
      children: [
        page('admin/start', 'start', <StartPage />),
        page('admin/connections', 'connections', <ConnectionsPage />),
        page('admin/access', 'access', <AccessPage />),
        page('admin/backup', 'backup', <BackupPage />),
        page('admin/org', 'org', <OrgPage />),
        page('admin/log', 'log', <LogPage />),
        page('admin/advanced', 'advanced', <AdvancedPage />),
        page('admin/advanced/plugins', 'advanced', <AdvancedPage />),
      ],
    },
  ];
}

/** Đường dẫn cũ → LegacyRedirect (bảng ở shell/legacyRedirects.ts). */
export function buildLegacyRoutes(): RouteObject[] {
  return LEGACY_PATHS.map((path) => ({ path, element: <LegacyRedirect /> }));
}

export const routes: RouteObject[] = [
  {
    element: (
      <>
        <UrlStateSync />
        <RootLayout />
      </>
    ),
    // B5: lỗi vẽ/tải trong route → trang lỗi có mã thay cho trang lỗi mặc định của react-router.
    errorElement: <RouteErrorPage />,
    children: [
      { path: '/login', element: <LoginPage /> },
      { path: '/setup', element: <SetupPage /> },
      // v0.1.19: mật khẩu tạm sau `genh reset-password` — AppShell chuyển mọi màn Console về đây tới khi đổi xong.
      { path: '/change-password', element: <ForcePasswordPage /> },
      // v0.1.78 (G0): Mặt tiền Owner đã gộp vào một shell — `/owner` và `/owner/*` chuyển hướng sang route mới.
      { path: '/owner', element: <OwnerShell /> },
      { path: '/owner/*', element: <OwnerShell /> },
      {
        path: '/',
        element: <AppShell />,
        children: [
          // v0.1.42 (F-26): "/" → màn đầu tiên KHÔNG ẩn của vai trò (GET /navigation), giữ ?gen=.
          { index: true, element: <HomeRedirect /> },
          ...buildConsoleRoutes(),
          ...buildLegacyRoutes(),
          // Hướng dẫn thiết lập: `/guide` chuyển sang `/admin/start` (G5 gộp); các bước và "Việc Sếp cần làm" giữ đường cũ, tô sáng Bắt đầu.
          // v0.1.39 (F-74): "Việc Sếp cần làm" — đặt TRƯỚC `guide/:n`.
          { path: 'guide/viec-sep', handle: { page: BOSS_CHECKS_CRUMBS, navKey: 'start' } satisfies RouteHandle, element: <BossChecksPage /> },
          { path: 'guide/:n', handle: { page: GUIDE_CRUMBS, navKey: 'start' } satisfies RouteHandle, element: <GuideStepPage /> },
          // Tài khoản của tôi (v0.1.19) — mở từ khối tài khoản ở chân thanh bên và từ Cài đặt.
          { path: 'account', handle: { page: ACCOUNT_CRUMBS } satisfies RouteHandle, element: <AccountPage /> },
          // Trợ giúp / Giới thiệu (v0.1.22) — phiên bản, hỏi Gen, lệnh genh, Báo lỗi.
          { path: 'help', handle: { page: HELP_CRUMBS } satisfies RouteHandle, element: <HelpPage /> },
          // Tài khoản mạng xã hội (v0.1.29, chỉ Owner) — mở từ thẻ Facebook ở Kết nối và menu tài khoản.
          { path: 'social', handle: { page: SOCIAL_CRUMBS, navKey: 'connections' } satisfies RouteHandle, element: <SocialPage /> },
          // v0.1.47 (F-79/F-85): cảnh báo rủi ro + đồng ý gửi trả lời/tin nhắn Facebook (chỉ Owner), tô sáng Kết nối.
          {
            path: 'social/ghi-facebook',
            handle: { page: { domain: 'KẾT NỐI', title: 'Tài khoản mạng xã hội › Gửi trả lời & tin nhắn' }, navKey: 'connections' } satisfies RouteHandle,
            element: <SocialWriteRiskPage />,
          },
          // B5: trang 404 trong khung Console (thanh bên vẫn dùng được).
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
];

export function createAppRouter() {
  const router = createBrowserRouter(routes);
  setNavigator((to, opts) => void router.navigate(to, opts));
  // v0.1.59 (G2): thành phần kit có `to` (StatTile, ListRow) chuyển trang trong ứng dụng thay vì tải lại cả trang.
  setKitNavigator((to) => void router.navigate(to));
  return router;
}
