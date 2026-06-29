import { expect, test } from '@playwright/test';
import { registerAndLogin } from './helpers/auth';

test.describe('Sportsbook Flow', () => {
  test('analysis/stat/today route to bet builder prop mode', async ({ page }) => {
    await registerAndLogin(page);

    await page.goto('/nba/today');
    await expect(page.locator('a[href*="/bets/new?current_tab=prop#prop"]')).toBeVisible();

    await page.goto('/nba/analysis');
    await expect(page.locator('a[href*="/bets/new?current_tab=prop#prop"]')).toBeVisible();

    await page.goto('/nba/stat-analysis');
    await expect(page.locator('a[href*="/bets/new?current_tab=prop#prop"]')).toBeVisible();
  });

  test('bet builder loads unified slip', async ({ page }) => {
    await registerAndLogin(page);

    await page.goto('/bets/new');
    await expect(page.locator('#ub-root')).toBeVisible();
    await expect(page.locator('#ub-stake')).toBeVisible();
    await expect(page.locator('#ub-submit-btn')).toBeVisible();
  });
});
