import { QueryClient } from '@tanstack/react-query';
import { ApiError } from '@gen-harness/contracts';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        // 4xx (auth, permission, setup, validation) will not fix itself.
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
        // v0.1.30: không có model AI chạy được — thử lại ngay không giúp gì, hiện "Chọn model" luôn.
        if (error instanceof ApiError && error.code === 'MODEL_UNAVAILABLE') return false;
        return failureCount < 2;
      },
    },
    mutations: { retry: false },
  },
});
