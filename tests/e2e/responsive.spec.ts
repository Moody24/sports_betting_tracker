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

  test('dashboard cards remain usable and primary navigation remains reachable', async ({ page }) => {
    await registerAndLogin(page);

    for (const width of widths) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto('/dashboard');

      // Deliberately still pinned to `.kpi-card`: unlike the readiness
      // selectors above, this is a real assertion about card shape, not
      // plumbing. Phase 3 replaces the KPI card row with a ledger band, at
      // which point this assertion stops being meaningful and the migrator
      // must replace it rather than repoint it.
      const firstCard = page.locator('.kpi-card').first();
      await expect(firstCard).toBeVisible();
      expect(await firstCard.evaluate((element) => element.getBoundingClientRect().width)).toBeGreaterThan(120);

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
