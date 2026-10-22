import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from 'react-router-dom';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import '@gen-harness/tokens/tokens.css';
import '@gen-harness/ui/styles.css';
import '@gen-harness/ui/owner-kit.css';
import './styles/global.css';
import './styles/shell.css';
import './styles/setup.css';
import './styles/data.css';
import './styles/listenlog.css';
import './styles/p3-core.css';
import './styles/p3-queue.css';
import './styles/p3-relations.css';
import './styles/p3-graph.css';
import './styles/p3-market.css';
import './styles/p3-people.css';
import './styles/p4-agents.css';
import './styles/p4-api.css';
import './styles/p4-mcp.css';
import './styles/p4-plugins.css';
import './styles/system.css';
import './styles/connections.css';
import './styles/account.css';
import './styles/gen.css';
import './styles/notifications.css';
import './styles/social.css';
import './styles/errors.css';
import './styles/theme.css';
import { reportClientError } from './lib/clientErrors';
import { newErrorId } from './lib/errorId';
import { queryClient } from './lib/queryClient';
import { createAppRouter } from './router';
import { ErrorBoundary } from './shell/ErrorPage';

const router = createAppRouter();

// v0.1.44 (F-4b): lỗi JS ngoài React (sự kiện, hẹn giờ, promise bị từ chối không ai bắt) cũng báo về máy chủ — mỗi
// sự kiện một mã ERR-… riêng; reportClientError tự khử trùng, giới hạn 5 lần/phút và không bao giờ ném. Hoãn một nhịp
// để lỗi ErrorBoundary đã bắt (React bản dev cũng phát sự kiện "error") được báo trước bằng đúng mã đang hiện cho Sếp.
window.addEventListener('error', (ev) => {
  const error: unknown = ev.error ?? ev.message;
  window.setTimeout(() => {
    const errorId = newErrorId();
    if (reportClientError({ errorId, error })) console.error(`[Gen-Harness] ${errorId} — lỗi chưa bắt`, error);
  }, 0);
});
window.addEventListener('unhandledrejection', (ev) => {
  const error: unknown = ev.reason;
  window.setTimeout(() => {
    const errorId = newErrorId();
    if (reportClientError({ errorId, error })) console.error(`[Gen-Harness] ${errorId} — promise bị từ chối chưa bắt`, error);
  }, 0);
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary variant="page">
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
);
