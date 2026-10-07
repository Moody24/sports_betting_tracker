import { expect, test } from '@playwright/test';

async function showCards(page, count: number, games: number) {
  await page.goto('/');
  await page.setContent(Array.from({ length: count }, (_, i) => `
    <article data-live-prop-card data-url="/nba/prop-progress/game${i % games}?player=Player&amp;prop_type=player_points&amp;line=25.5&amp;bet_type=over">
      <span data-live-current></span><span data-live-status>Checking</span>
      <span data-live-trend></span><span data-live-error hidden></span>
    </article>`).join(''));
  await page.evaluate(() => { (window as any).CSRF_TOKEN = 'fixture'; });
}

test('large progress refresh uses sequential bounded batches and updates every card', async ({ page }) => {
  await showCards(page, 201, 16);
  const requests: any[][] = [];
  let active = 0;
  let maxActive = 0;
  await page.route('**/nba/prop-progress/batch', async (route) => {
    active += 1;
    maxActive = Math.max(maxActive, active);
    const body = route.request().postDataJSON();
    requests.push(body);
    expect(body.length).toBeLessThanOrEqual(100);
    expect(new Set(body.map((item) => item.espn_id)).size).toBeLessThanOrEqual(15);
    await new Promise((resolve) => setTimeout(resolve, 20));
    await route.fulfill({ json: Object.fromEntries(body.map((item) => [item.card_id, {
      ok: true, current_stat: 27, projected_final: 30, line: 25.5,
      bet_type: 'over', status_text: 'Final', game_state: 'final',
    }])) });
    active -= 1;
  });
  await page.addScriptTag({ url: '/static/js/bets_list.js' });
  await expect(page.locator('[data-live-current]').last()).toHaveText('27');
  expect(await page.locator('[data-live-current]').allTextContents()).toEqual(Array(201).fill('27'));
  const sent = requests.flat();
  expect(sent).toHaveLength(201);
  expect(new Set(sent.map((item) => item.card_id)).size).toBe(201);
  expect(maxActive).toBe(1);
});

test('failed progress batch shows errors and backs off without overlapping refreshes', async ({ page }) => {
  await showCards(page, 101, 1);
  await page.clock.install();
  let requests = 0;
  await page.route('**/nba/prop-progress/batch', async (route) => {
    requests += 1;
    expect(route.request().postDataJSON()).toHaveLength(100);
    await route.fulfill({ status: 429, json: { ok: false, error: 'Rate limited' } });
  });
  await page.addScriptTag({ url: '/static/js/bets_list.js' });
  await expect(page.locator('[data-live-status]').last()).toHaveText('Live update failed');
  expect(requests).toBe(1);
  await page.clock.fastForward(30000);
  expect(requests).toBe(1);
  await page.clock.fastForward(30000);
  await expect.poll(() => requests).toBe(2);
});

test('partial refresh keeps successful cards and retries only unresolved cards', async ({ page }) => {
  await showCards(page, 101, 1);
  await page.clock.install();
  const sizes: number[] = [];
  await page.route('**/nba/prop-progress/batch', async (route) => {
    const body = route.request().postDataJSON();
    sizes.push(body.length);
    if (sizes.length === 2) {
      await route.fulfill({ status: 503, json: { ok: false } });
      return;
    }
    await route.fulfill({ json: Object.fromEntries(body.map((item) => [item.card_id, {
      ok: true, current_stat: 27, projected_final: 30, line: 25.5,
      bet_type: 'over', status_text: 'Final', game_state: 'final',
    }])) });
  });
  await page.addScriptTag({ url: '/static/js/bets_list.js' });
  await expect(page.locator('[data-live-current]').first()).toHaveText('27');
  await expect(page.locator('[data-live-status]').last()).toHaveText('Live update failed');
  expect(sizes).toEqual([100, 1]);
  await page.clock.fastForward(60000);
  await expect(page.locator('[data-live-current]').last()).toHaveText('27');
  expect(sizes).toEqual([100, 1, 1]);
  await page.clock.fastForward(300000);
  expect(sizes).toEqual([100, 1, 1]);
});

test('deferred cards retry first after backoff and recover without losing terminal errors', async ({ page }) => {
  await showCards(page, 3, 3);
  await page.clock.install();
  const sent: any[][] = [];
  await page.route('**/nba/prop-progress/batch', async (route) => {
    const body = route.request().postDataJSON();
    sent.push(body);
    if (sent.length === 1) {
      await route.fulfill({ json: {
        [body[0].card_id]: { ok: false, status: 'game_not_started', retryable: true, error: 'Temporarily unavailable' },
        [body[1].card_id]: { ok: false, status: 'progress_deferred', retryable: true, error: 'Budget exhausted' },
        [body[2].card_id]: { ok: false, error: 'Player not found in boxscore' },
      } });
    } else {
      await route.fulfill({ json: Object.fromEntries(body.map((item) => [item.card_id, {
        ok: true, current_stat: 27, projected_final: 30, line: 25.5,
        bet_type: 'over', status_text: 'Final', game_state: 'final',
      }])) });
    }
  });
  await page.addScriptTag({ url: '/static/js/bets_list.js' });
  await expect(page.locator('[data-live-status]').first()).toHaveText('Live update delayed; retrying');
  await expect(page.locator('[data-live-trend]').nth(2)).toHaveText('Unavailable');
  await page.clock.fastForward(30000);
  expect(sent).toHaveLength(1);
  await page.clock.fastForward(30000);
  await expect(page.locator('[data-live-current]').first()).toHaveText('27');
  expect(sent).toHaveLength(2);
  expect(sent[1].map((item) => item.espn_id)).toEqual(['game1', 'game0']);
  await expect(page.locator('[data-live-error]').first()).toBeHidden();
  await page.clock.fastForward(300000);
  expect(sent).toHaveLength(2);
});
