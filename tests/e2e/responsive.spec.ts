import { expect, test } from '@playwright/test';
import { registerAndLogin } from './helpers/auth';

const widths = [1200, 992, 768, 576, 375, 320];
const pages = [
  { path: '/dashboard', ready: '[data-testid="dashboard-summary"]' },
  { path: '/bets', ready: '[data-testid="bets-list"]' },
  { path: '/nba/today', ready: '[data-testid="today-active-games"]' },
  { path: '/nba/analysis', ready: '[data-testid="analysis-summary"]' },
  { path: '/nba/stat-analysis', ready: '[data-testid="stat-results"]' },
  { path: '/bets/new?current_tab=prop#prop', ready: '[data-testid="bet-builder"]' },
];

test.describe('Responsive layout contract', () => {
  test('public and error pages fit compact and desktop viewports', async ({ page }) => {
    const publicPaths = [
      '/',
      '/methodology',
      '/responsible-gambling',
      '/privacy',
      '/terms',
      '/data-sources',
      '/about',
      '/missing-responsive-fixture',
    ];

    for (const width of [1200, 412, 320]) {
      await page.setViewportSize({ width, height: 900 });
      for (const path of publicPaths) {
        await page.goto(path);
        await expect(page.locator('main')).toBeVisible();
        const overflow = await page.evaluate(() => ({
          documentWidth: document.documentElement.scrollWidth,
          viewportWidth: document.documentElement.clientWidth,
          bodyWidth: document.body.scrollWidth,
        }));
        expect(overflow, `${path} overflows horizontally at ${width}px`).toEqual({
          documentWidth: width,
          viewportWidth: width,
          bodyWidth: width,
        });
      }
    }
  });

  test('core pages fit every supported viewport without document overflow', async ({ page }) => {
    await registerAndLogin(page);

    for (const width of widths) {
      await page.setViewportSize({ width, height: 900 });

      for (const item of pages) {
        await page.goto(item.path);
        await expect(page.locator(item.ready).first()).toBeVisible();

        const overflow = await page.evaluate(() => ({
          documentWidth: document.documentElement.scrollWidth,
          viewportWidth: document.documentElement.clientWidth,
          bodyWidth: document.body.scrollWidth,
        }));

        expect(
          overflow,
          `${item.path} overflows horizontally at ${width}px`,
        ).toEqual({
          documentWidth: width,
          viewportWidth: width,
          bodyWidth: width,
        });
      }
    }
  });

  test('dashboard ledger remains usable and primary navigation remains reachable', async ({ page }) => {
    await registerAndLogin(page);

    for (const width of widths) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto('/dashboard');

      const ledger = page.locator('[data-testid="dashboard-summary"]');
      await expect(ledger.locator('.band')).toBeVisible();
      await expect(ledger.locator('.row-line')).toHaveCount(1);
      await expect(ledger.locator('.row-figure.is-lead')).toBeVisible();

      // The masthead replaced the sidebar drawer, so navigation is the same
      // component at every width — no toggle, no overlay, no focus trap.
      // What still has to hold is that every section stays reachable and the
      // current one is marked by more than colour alone.
      const nav = page.locator('nav[aria-label="Primary"]');
      await expect(nav).toBeVisible();

      for (const section of ['Dashboard', 'Prop Analysis', 'My Bets']) {
        await expect(nav.getByRole('link', { name: section, exact: true })).toBeVisible();
      }

      await expect(nav.getByRole('link', { name: 'Dashboard', exact: true })).toHaveAttribute(
        'aria-current',
        'page',
      );
    }
  });
});
