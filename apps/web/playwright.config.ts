import { defineConfig, devices } from '@playwright/test';

const PORT = Number(process.env.E2E_PORT ?? 5174);
const baseURL = `http://localhost:${PORT}`;

/**
 * Chromium comes preinstalled under PLAYWRIGHT_BROWSERS_PATH (/opt/pw-browsers).
 * If the bundled revision does not match this @playwright/test version, set
 * PW_CHROMIUM=/opt/pw-browsers/chromium to launch that binary directly.
 */
const executablePath = process.env.PW_CHROMIUM || undefined;

export default defineConfig({
  testDir: './e2e',
  // CI chạy route-crawl (lưới L4, chậm) ở job riêng `crawl` nên job `web` đặt E2E_SKIP_CRAWL=1; chạy tay vẫn có đủ.
  testIgnore: process.env.E2E_SKIP_CRAWL ? ['**/route-crawl-*.spec.ts'] : [],
  outputDir: './test-results/playwright',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]],
  use: {
    baseURL,
    trace: 'retain-on-failure',
    locale: 'vi-VN',
    timezoneId: 'Asia/Ho_Chi_Minh',
    colorScheme: 'dark',
    launchOptions: executablePath ? { executablePath } : {},
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], deviceScaleFactor: 1 } }],
  webServer: {
    command: `npm run dev:mock -- --port ${PORT} --strictPort`,
    url: `${baseURL}/api/v1/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
