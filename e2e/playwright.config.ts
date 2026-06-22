import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright config — targets a running janus-camera-page instance.
 *
 * Defaults to localhost:18900 (port mapping of the standard test container
 * janus-installer-test). Override via E2E_BASE_URL for other targets.
 *
 * Admin token (required for admin_config + admin_dashboard pages):
 *   E2E_ADMIN_TOKEN=<token from container's /etc/robot/camera-secrets.env>
 */
const BASE_URL = process.env.E2E_BASE_URL || 'http://127.0.0.1:18900';
const ADMIN_TOKEN = process.env.E2E_ADMIN_TOKEN || 'test-admin-token-123';

export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,        // mutations to single instance — serial
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: BASE_URL,
    extraHTTPHeaders: {
      'X-Admin-Token': ADMIN_TOKEN,
    },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
