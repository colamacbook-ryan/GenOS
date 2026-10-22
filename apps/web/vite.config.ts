import type { Plugin } from 'vite';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

const API_TARGET = process.env.GH_API_TARGET ?? 'http://localhost:8000';

/** `vite --mode mock` serves an in-memory API (phase 1 + 2, with `/api/v1/ws`) instead of proxying. */
function mockApi(): Plugin {
  return {
    name: 'gh-mock-api',
    async configureServer(server) {
      const { createMockApi } = await import('./test/mock-api');
      // v0.1.59: MOCK_OWNER_DATA=empty|collecting|nochannel chọn chế độ dữ liệu của Mặt tiền (/owner/*); thiếu/lạ ⇒ đủ dữ liệu.
      const ownerData = ['empty', 'collecting', 'nochannel'].includes(process.env.MOCK_OWNER_DATA ?? '')
        ? (process.env.MOCK_OWNER_DATA as 'empty' | 'collecting' | 'nochannel')
        : undefined;
      const mock = createMockApi({ setup: process.env.MOCK_SETUP === 'fresh' ? 'fresh' : 'finished', updateAvailable: process.env.MOCK_UPDATE === '1', mustChangePassword: process.env.MOCK_MUST_CHANGE === '1', ownerData });
      server.middlewares.use(mock.middleware);
      // Realtime: answer `/api/v1/ws` upgrades; Vite's own HMR socket is left alone.
      server.httpServer?.on('upgrade', (req, socket) => {
        mock.upgrade(req, socket);
      });
    },
  };
}

export default defineConfig(({ mode }) => ({
  plugins: [react(), ...(mode === 'mock' ? [mockApi()] : [])],
  server: {
    port: 5173,
    proxy:
      mode === 'mock'
        ? undefined
        : {
            // Realtime socket first: same origin + cookie, forwarded as a WebSocket.
            '/api/v1/ws': { target: API_TARGET, ws: true, changeOrigin: false },
            '/api/': { target: API_TARGET, changeOrigin: false },
          },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    target: 'es2022',
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}', 'test/unit/**/*.test.{ts,tsx}'],
    css: false,
  },
}));
