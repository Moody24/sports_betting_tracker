import { expect, test } from '@playwright/test';
import { registerAndLogin } from './helpers/auth';

const widths = [1440, 1200, 992, 768, 576, 375, 320];
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

  test('performance summary remains readable and primary navigation remains reachable', async ({ page }) => {
    await registerAndLogin(page);

    for (const width of widths) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto('/dashboard');

      const summary = page.locator('[data-testid="dashboard-summary"]');
      for (const label of ['Net profit / loss', 'Return on investment', 'Win rate', 'Total wagered']) {
        await expect(summary.getByText(label, { exact: true })).toBeVisible();
      }
      await expect(summary.getByText('0 total bets', { exact: true })).toBeVisible();

      const nav = page.locator('nav[aria-label="Primary"]');
      await expect(nav).toBeVisible();
      expect(await page.locator('.app-shell').evaluate(el => getComputedStyle(el).paddingLeft)).toBe('0px');
      expect(await page.locator('.masthead').evaluate(el => getComputedStyle(el).position)).not.toBe('fixed');

      for (const section of ['NBA Today', 'Player Props', 'My Bets', 'Results']) {
        await expect(nav.getByRole('link', { name: section, exact: true })).toBeVisible();
      }

      await expect(nav.getByRole('link', { name: 'Results', exact: true })).toHaveAttribute(
        'aria-current',
        'page',
      );
    }
  });
});
