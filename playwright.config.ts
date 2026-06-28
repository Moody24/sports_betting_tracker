import { defineConfig, devices } from '@playwright/test';

const baseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:5000';
const runLocalServer = !process.env.E2E_BASE_URL;
const e2eSecret = process.env.SECRET_KEY || 'e2e-dev-secret';
const e2eDatabaseUrl = process.env.DATABASE_URL || 'sqlite:////tmp/e2e.sqlite';
const rateLimitEnabled = process.env.RATELIMIT_ENABLED || 'false';
const bootstrapCommand = e2eDatabaseUrl.startsWith('sqlite:')
  ? `SECRET_KEY=${e2eSecret} DATABASE_URL=${e2eDatabaseUrl} RATELIMIT_ENABLED=${rateLimitEnabled} python -c "from app import create_app, db; app = create_app(); ctx = app.app_context(); ctx.push(); db.create_all(); ctx.pop()"`
  : `SECRET_KEY=${e2eSecret} DATABASE_URL=${e2eDatabaseUrl} RATELIMIT_ENABLED=${rateLimitEnabled} python -c "from app import create_app; from flask_migrate import upgrade; app = create_app();\nwith app.app_context(): upgrade(directory='migrations')"`;

const runServerCommand = `SECRET_KEY=${e2eSecret} DATABASE_URL=${e2eDatabaseUrl} RATELIMIT_ENABLED=${rateLimitEnabled} python run.py`;

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60_000,
  fullyParallel: false,
  retries: process.env.CI ? 0 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : [['list'], ['html', { open: 'never' }]],
  expect: {
    timeout: 10_000,
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.02,
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
