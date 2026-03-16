import { expect, test } from '@playwright/test';
import { registerAndLogin } from './helpers/auth';

test.describe('Sportsbook Flow', () => {
  test('analysis/stat/today route to bet builder prop mode', async ({ page }) => {
    await registerAndLogin(page);

    await page.goto('/nba/today');
    await expect(page.locator('a[href*="/bets/new?current_tab=prop#prop"]')).toBeVisible();

    await page.goto('/nba/analysis');
    await expect(page.locator('a[href*="/bets/new?current_tab=prop#prop"]')).toBeVisible();
    await expect(page.locator('a[href*="/bets/new?current_tab=parlay#parlay"]')).toBeVisible();

    await page.goto('/nba/stat-analysis');
    await expect(page.locator('a[href*="/bets/new?current_tab=prop#prop"]')).toBeVisible();
  });

  test('add-bet page prefill and tab deep links work', async ({ page }) => {
    await registerAndLogin(page);

    await page.goto('/bets/new?current_tab=prop&team_a=Lakers&team_b=Celtics&match_date=2026-03-10&bet_type=over&player_name=LeBron+James&prop_type=player_points&prop_line=27.5&game_id=espn123#prop');
    await expect(page.locator('#prop-player-name')).toHaveValue('LeBron James');
    await expect(page.locator('#prop-line')).toHaveValue('27.5');

    await page.goto('/bets/new?current_tab=parlay#parlay');
    await expect(page.locator('#bb-panel-parlay')).toBeVisible();
  });
});
