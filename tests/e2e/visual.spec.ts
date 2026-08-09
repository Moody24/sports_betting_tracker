import { expect, test } from '@playwright/test';
import { registerAndLogin } from './helpers/auth';

const pages = [
  { path: '/dashboard', selector: '.kpi-card', name: 'dashboard' },
  { path: '/bets', selector: '.bets-list-wrap', name: 'bets' },
  { path: '/nba/today', selector: '#active-games-section', name: 'nba-today' },
  { path: '/nba/analysis', selector: '#analysis-kpis', name: 'nba-analysis' },
  { path: '/nba/stat-analysis', selector: '#stat-analysis-results-shell', name: 'nba-stat-analysis' },
  { path: '/bets/new?current_tab=prop#prop', selector: '#ub-root', name: 'bet-builder' },
];

test.describe('Visual Regression', () => {
  test('sportsbook core pages remain visually stable', async ({ page }) => {
    await registerAndLogin(page);

    for (const item of pages) {
      await page.goto(item.path);
      await expect(page.locator(item.selector).first()).toBeVisible();
      await expect(page).toHaveScreenshot(`${item.name}.png`, {
        fullPage: true,
        animations: 'disabled',
        mask: [
          page.locator('.user-btn'),
          page.locator('[id$="last-updated"]'),
        ],
      });
    }
  });
});
