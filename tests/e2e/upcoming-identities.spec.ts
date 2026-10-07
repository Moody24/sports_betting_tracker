import { expect, test } from '@playwright/test';
import { registerAndLogin } from './helpers/auth';
import { labelFixture } from './helpers/fixture-label';

test('Odds-only games select and refresh their own props without saving an ESPN ID', async ({ page }) => {
  await registerAndLogin(page);
  const base = {
    team_a: 'Fixture Away', team_b: 'Fixture Home', match_date: '2026-10-08',
    game_id: '', over_under_line: null, moneyline_away: null, moneyline_home: null,
  };
  const games = ['a', 'b'].map(id => ({
    ...base, label: `Fixture event ${id} (Tomorrow)`,
    selection_key: `odds_api:event-${id}`, odds_event_id: `event-${id}`,
  }));
  let secondLine = 30.5;
  await page.route('**/nba/upcoming-games', route => route.fulfill({ json: games }));
  await page.route('**/nba/all-props*', route => route.fulfill({ json: games.map((game, index) => ({
    ...game, player: 'Fixture Player', market: 'player_points',
    line: index ? secondLine : 20.5, over_odds: -110, under_odds: -115,
    quote_source: 'odds_api', quote_observed_at: new Date().toISOString(),
    quote_tokens: { over: 'fixture-over-token', under: 'fixture-under-token' },
    quote_books: { over: 'FixtureBook', under: 'FixtureBook' },
  })) }));
  let captured: { legs: Array<{ game_id: string; selection_key: string; prop_line: number }> } | undefined;
  await page.route('**/nba/place-bets', async route => {
    captured = route.request().postDataJSON();
    await route.fulfill({ json: { success: false, error: 'Fixture capture complete' } });
  });
  await page.goto('/bets/new');
  await labelFixture(page);
  await page.locator('#ub-game-picker').selectOption('provider:odds_api:event-b');
  await expect(page.locator('.prop-card')).toHaveCount(1);
  await expect(page.locator('.prop-card-line')).toHaveText('30.5');
  await page.locator('.prop-card').getByRole('button', { name: 'O -110', exact: true }).click();
  secondLine = 31.5;
  await page.locator('#ub-refresh-btn').click();
  await expect(page.locator('#ub-game-picker')).toHaveValue('provider:odds_api:event-b');
  await expect(page.locator('#ub-slip-list')).toContainText('OVER 30.5');
  await page.locator('#ub-stake').fill('10');
  await page.locator('#ub-submit-btn').click();
  await expect(page.locator('#ub-quote-review')).toBeVisible();
  await page.getByRole('button', { name: 'Use current quotes' }).click();
  await expect(page.locator('#ub-slip-list')).toContainText('OVER 31.5');
  await page.locator('#ub-submit-btn').click();
  await expect.poll(() => captured?.legs.length).toBe(1);
  expect(captured?.legs[0].game_id).toBe('');
  expect(captured?.legs[0].selection_key).toBe('odds_api:event-b');
  expect(captured?.legs[0].prop_line).toBe(31.5);
});
