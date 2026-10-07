import { expect, test, Page } from '@playwright/test';
import { registerAndLogin } from './helpers/auth';
import { labelFixture } from './helpers/fixture-label';
import AxeBuilder from '@axe-core/playwright';

const games = ['one', 'two'].map(id => ({
  game_id: id, selection_key: `espn:${id}`, status: 'scheduled',
  team_a: `Away ${id}`, team_b: `Home ${id}`, match_date: '2026-10-08',
  moneyline_home: null, moneyline_away: null, over_under_line: null,
}));

function props(line = 24.5, odds = -110) {
  return games.flatMap(game => ['player_points', 'player_rebounds'].map(market => ({
    ...game, player: 'Fixture Player', market,
    line: market === 'player_points' ? line : 8.5, over_odds: odds, under_odds: -115,
    quote_source: 'odds_api', quote_observed_at: new Date().toISOString(),
    quote_tokens: { over: 'fixture-over-token', under: 'fixture-under-token' },
    quote_books: { over: 'FixtureBook', under: 'FixtureBook' },
  })));
}

async function setup(page: Page) {
  await registerAndLogin(page);
  await page.route('**/nba/upcoming-games', route => route.fulfill({ json: games }));
  await page.route('**/nba/all-props*', route => route.fulfill({ json: props() }));
}

test('guided game, filter and prop selection prefill evidence and prevent duplicates', async ({ page }) => {
  await setup(page);
  let captured: any;
  await page.route('**/nba/place-bets', route => {
    captured = route.request().postDataJSON();
    return route.fulfill({ status: 400, json: { success: false, message: 'Fixture validation retry' } });
  });
  await page.goto('/bets/new');
  await labelFixture(page);
  await expect(page.locator('.manual-record')).not.toHaveAttribute('open', '');
  await expect(page.locator('#ub-game-picker')).toHaveValue('');
  await page.locator('#ub-game-picker').selectOption('provider:espn:one');
  await expect(page.locator('.prop-card')).toHaveCount(2);
  await page.locator('#ub-filter-reb').click();
  await expect(page.locator('.prop-card')).toHaveCount(1);
  await expect(page.locator('#ub-filter-reb')).toHaveAttribute('aria-pressed', 'true');
  await page.locator('#ub-filter-pts').click();
  const add = page.locator('.prop-card').getByRole('button', { name: 'O -110', exact: true });
  await add.click();
  const viewport = page.viewportSize()!;
  for (const width of [320, 375, 412, 1440]) {
    await page.setViewportSize({ width, height: viewport.height });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    for (const selector of ['.prop-card-player', '.parlay-leg-info']) {
      const geometry = await page.locator(selector).first().evaluate(element => {
        const bounds = element.getBoundingClientRect();
        const parent = element.parentElement!.getBoundingClientRect();
        return { width: bounds.width, inside: bounds.left >= parent.left && bounds.right <= parent.right + 1 };
      });
      expect(geometry.width).toBeGreaterThanOrEqual(120);
      expect(geometry.inside).toBe(true);
    }
  }
  await page.setViewportSize(viewport);
  await page.screenshot({ path: test.info().outputPath('guided-props-fixture.png'), fullPage: true });
  await add.click();
  await expect(page.locator('#ub-slip-list .parlay-leg-item')).toHaveCount(1);
  await expect(page.locator('#ub-feedback')).toContainText('already in your slip');
  await expect(page.locator('#ub-slip-list')).toContainText('OVER 24.5');
  await page.locator('#ub-stake').fill('10');
  await page.locator('#ub-submit-btn').click();
  await expect(page.locator('#ub-feedback')).toContainText('Fixture validation retry');
  expect(captured.legs[0]).toMatchObject({ game_id: 'one', player_name: 'Fixture Player', prop_line: 24.5, american_odds: -110, quote_source: 'odds_api' });
  await expect(page.locator('#ub-stake')).toHaveValue('10');
  await expect(page.locator('#ub-submit-btn')).toBeEnabled();
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations.filter(item => ['serious', 'critical'].includes(item.impact || ''))).toEqual([]);
});

test('refresh preserves entered prices and changed quotes require review with cancel and historical escape', async ({ page }) => {
  await setup(page);
  let current = props();
  await page.route('**/nba/all-props*', route => route.fulfill({ json: current }));
  let captured: any;
  await page.route('**/nba/place-bets', route => {
    captured = route.request().postDataJSON();
    return route.fulfill({ json: { success: false, message: 'Fixture saved request' } });
  });
  await page.goto('/bets/new');
  await labelFixture(page);
  await page.locator('#ub-game-picker').selectOption('provider:espn:one');
  await page.locator('#ub-filter-pts').click();
  await page.locator('.prop-card').getByRole('button', { name: 'O -110', exact: true }).click();
  await page.locator('#ub-slip-list input[aria-label="Recorded American odds"]').fill('-135');
  current = props(25.5, -120);
  await page.locator('#ub-refresh-btn').click();
  await expect(page.locator('#ub-slip-list')).toContainText('OVER 24.5');
  await expect(page.locator('#ub-slip-list input[aria-label="Recorded American odds"]')).toHaveValue('-135');
  await page.locator('#ub-stake').fill('12');
  await page.locator('#ub-submit-btn').click();
  await expect(page.locator('#ub-quote-review')).toBeVisible();
  expect(captured).toBeUndefined();
  await page.keyboard.press('Escape');
  await expect(page.locator('#ub-quote-review')).not.toBeVisible();
  await expect(page.locator('#ub-submit-btn')).toBeFocused();
  await page.locator('#ub-submit-btn').click();
  await page.getByRole('button', { name: 'Keep original as manual record' }).click();
  await page.locator('#ub-submit-btn').click();
  await expect.poll(() => captured?.legs[0]?.manual_entry).toBe(true);
  expect(captured.legs[0].american_odds).toBe(-135);
  expect(captured.legs[0].prop_line).toBe(24.5);
});

test('game history navigation, retry and schedule-only unavailable state stay usable', async ({ page }) => {
  await setup(page);
  let unavailable = true;
  await page.route('**/nba/all-props*', route => unavailable
    ? route.fulfill({ status: 503, json: { message: 'unavailable' } })
    : route.fulfill({ json: props() }));
  await page.goto('/bets/new');
  await labelFixture(page);
  await page.locator('#ub-game-picker').selectOption('provider:espn:one');
  await expect(page.locator('#ub-props-status')).toContainText('Unable to load');
  unavailable = false;
  await page.locator('#ub-refresh-btn').click();
  await expect(page.locator('.prop-card')).toHaveCount(2);
  await page.locator('#ub-game-picker').selectOption('provider:espn:two');
  await page.goBack();
  await expect(page.locator('#ub-game-picker')).toHaveValue('provider:espn:one');
  await page.goForward();
  await expect(page.locator('#ub-game-picker')).toHaveValue('provider:espn:two');
  await page.locator('#ub-game-picker').focus();
  await expect(page.locator('#ub-game-picker')).toBeFocused();
  await expect(page.locator('body')).toHaveJSProperty('scrollWidth', await page.locator('body').evaluate(el => el.clientWidth));
});

test('free Games stay schedule-only and unavailable quotes cannot be added', async ({ page }) => {
  await setup(page);
  const schedule = { ...games[0], game_id: '', selection_key: 'balldontlie:77', market_state: 'unavailable', source: 'balldontlie' };
  await page.route('**/nba/upcoming-games', route => route.fulfill({ json: [schedule] }));
  let marketRequests = 0;
  await page.route('**/nba/all-props*', route => { marketRequests++; return route.fulfill({ json: props() }); });
  await page.goto('/bets/new');
  await labelFixture(page);
  await page.locator('#ub-game-picker').selectOption('provider:balldontlie:77');
  await expect(page.locator('#ub-props-status')).toContainText('does not include player props or odds');
  await expect(page.locator('.prop-card')).toHaveCount(0);
  await expect(page.locator('#ub-home-ml-btn')).toHaveCount(0);
  expect(marketRequests).toBe(0);
  await expect(page.locator('.manual-record')).not.toHaveAttribute('open', '');
});

test('research links preselect only a verified game and filter without adding a recommendation', async ({ page }) => {
  await setup(page);
  await page.goto('/bets/new?game_id=two&player_name=Fixture%20Player&prop_type=player_rebounds');
  await labelFixture(page);
  await expect(page.locator('#ub-game-picker')).toHaveValue('provider:espn:two');
  await expect(page.locator('#ub-player-search')).toHaveValue('Fixture Player');
  await expect(page.locator('#ub-filter-reb')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.prop-card')).toHaveCount(1);
  await expect(page.locator('#ub-slip-list .parlay-leg-item')).toHaveCount(0);
  await page.goto('/bets/new?game_id=unknown&player_name=Fixture%20Player');
  await labelFixture(page);
  await expect(page.locator('#ub-game-picker')).toHaveValue('');
  await expect(page.locator('.prop-card')).toHaveCount(0);
});

test('expired quotes block saving and a network retry retains selections', async ({ page }) => {
  await setup(page);
  let expired = false;
  await page.route('**/nba/all-props*', route => route.fulfill({ json: props().map(prop => ({
    ...prop, quote_observed_at: new Date(Date.now() - (expired ? 20 * 60000 : 0)).toISOString(),
  })) }));
  let saves = 0;
  const attempts: string[] = [];
  await page.route('**/nba/place-bets', route => {
    saves++;
    attempts.push(route.request().postData() || '');
    return saves === 1 ? route.abort('failed') : route.fulfill({ status: 400, json: { success: false, message: 'Fixture retry received' } });
  });
  await page.goto('/bets/new');
  await labelFixture(page);
  await page.locator('#ub-game-picker').selectOption('provider:espn:one');
  await page.locator('#ub-filter-pts').click();
  await page.locator('.prop-card').getByRole('button', { name: 'O -110', exact: true }).click();
  await page.locator('#ub-stake').fill('10');
  expired = true;
  await page.locator('#ub-submit-btn').click();
  await expect(page.locator('#ub-quote-review')).toBeVisible();
  await expect(page.locator('#ub-review-update')).toBeDisabled();
  expect(saves).toBe(0);
  await page.locator('#ub-review-cancel').click();
  expired = false;
  await page.locator('#ub-submit-btn').click();
  await expect(page.locator('#ub-feedback')).toContainText('Network');
  await expect(page.locator('#ub-submit-btn')).toBeEnabled();
  await expect(page.locator('#ub-slip-list .parlay-leg-item')).toHaveCount(1);
  await expect(page.locator('#ub-stake')).toHaveValue('10');
  await page.locator('#ub-submit-btn').click();
  await expect(page.locator('#ub-feedback')).toContainText('Fixture retry received');
  expect(attempts).toHaveLength(2);
  expect(attempts[1]).toBe(attempts[0]);
  expect(JSON.parse(attempts[0]).request_id).toMatch(/^[0-9a-f-]{36}$/);
  await page.locator('#ub-stake').fill('11');
  await page.locator('#ub-submit-btn').click();
  await expect.poll(() => attempts.length).toBe(3);
  expect(JSON.parse(attempts[2]).request_id).not.toBe(JSON.parse(attempts[0]).request_id);
});
