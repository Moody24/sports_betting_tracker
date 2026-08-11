import { defineConfig, devices } from '@playwright/test';
import { E2E_SECRET_KEY, E2E_DATABASE_URL, E2E_RATELIMIT_ENABLED } from './tests/e2e/helpers/env';

const baseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:5010';
const runLocalServer = !process.env.E2E_BASE_URL;
const e2eSecret = E2E_SECRET_KEY;
const e2eDatabaseUrl = E2E_DATABASE_URL;
const rateLimitEnabled = E2E_RATELIMIT_ENABLED;
const bootstrapCommand = e2eDatabaseUrl.startsWith('sqlite:')
  ? `SECRET_KEY=${e2eSecret} DATABASE_URL=${e2eDatabaseUrl} RATELIMIT_ENABLED=${rateLimitEnabled} ./.venv/bin/python -c "from app import create_app, db; app = create_app(); ctx = app.app_context(); ctx.push(); db.create_all(); ctx.pop()"`
  : `SECRET_KEY=${e2eSecret} DATABASE_URL=${e2eDatabaseUrl} RATELIMIT_ENABLED=${rateLimitEnabled} ./.venv/bin/python -c "from app import create_app; from flask_migrate import upgrade; app = create_app();\nwith app.app_context(): upgrade(directory='migrations')"`;

const runServerCommand = `SECRET_KEY=${e2eSecret} DATABASE_URL=${e2eDatabaseUrl} RATELIMIT_ENABLED=${rateLimitEnabled} ./.venv/bin/flask run --host 127.0.0.1 --port 5010`;

export default defineConfig({
  testDir: './tests/e2e',
  snapshotPathTemplate: '{testDir}/{testFilePath}-snapshots/{arg}-{projectName}{ext}',
  timeout: 60_000,
  fullyParallel: false,
  retries: process.env.CI ? 0 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : [['list'], ['html', { open: 'never' }]],
  expect: {
    timeout: 10_000,
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.005,
    },
  },
  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer: runLocalServer
    ? {
        command: `${bootstrapCommand} && ${runServerCommand}`,
        url: baseURL,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      }
    : undefined,
  projects: [
    {
      name: 'chromium-desktop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'chromium-mobile',
      use: { ...devices['Pixel 7'], viewport: { width: 412, height: 915 } },
    },
  ],
});
