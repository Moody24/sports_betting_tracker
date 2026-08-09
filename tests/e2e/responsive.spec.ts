import { expect, test } from '@playwright/test';
import { registerAndLogin } from './helpers/auth';

const widths = [1200, 992, 768, 576, 375, 320];
const pages = [
  { path: '/dashboard', ready: '.kpi-card' },
  { path: '/bets', ready: '.bets-list-wrap' },
  { path: '/nba/today', ready: '#active-games-section' },
  { path: '/nba/analysis', ready: '#analysis-kpis' },
  { path: '/nba/stat-analysis', ready: '#stat-analysis-results-shell' },
  { path: '/bets/new?current_tab=prop#prop', ready: '#ub-root' },
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

  test('dashboard cards remain usable and mobile navigation remains reachable', async ({ page }) => {
    await registerAndLogin(page);

    for (const width of widths) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto('/dashboard');

      const firstCard = page.locator('.kpi-card').first();
      await expect(firstCard).toBeVisible();
      expect(await firstCard.evaluate((element) => element.getBoundingClientRect().width)).toBeGreaterThan(120);

      const toggle = page.locator('#sidebar-toggle');
      if (width < 992) {
        await expect(toggle).toBeVisible();
        await expect(toggle).toHaveAttribute('aria-expanded', 'false');
        await toggle.click();
        await expect(page.locator('#sidebar')).toHaveClass(/open/);
        await expect(toggle).toHaveAttribute('aria-expanded', 'true');
      } else {
        await expect(toggle).toBeHidden();
        await expect(page.locator('#sidebar')).toBeVisible();
      }
    }
  });
});
