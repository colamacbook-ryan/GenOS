import { defineConfig, devices } from '@playwright/test';

/**
 * Chạy web với API thật (không mock).
 * - Giai đoạn 1 (live-phase1): API ở :8000 trên CSDL MỚI với GH_SETUP_TOKEN, GH_COOKIE_SECURE=false; `npx vite --port 5173`;
 *   `npx playwright test -c playwright.live.config.ts live-phase1`.
 * - Giai đoạn 2 (live-phase2): `bash e2e-live/run.sh` tự dựng api + worker + bridge giả + model giả rồi chạy.
 */
export default defineConfig({
  testDir: './e2e-live',
  outputDir: './test-results/live',
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [['list']],
  use: {
    baseURL: process.env.LIVE_BASE_URL ?? 'http://localhost:5173',
    locale: 'vi-VN',
    timezoneId: 'Asia/Ho_Chi_Minh',
    colorScheme: 'dark',
    launchOptions: process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {},
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], deviceScaleFactor: 1 } }],
});
