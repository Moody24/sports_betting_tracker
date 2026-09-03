import { expect, test } from '@playwright/test';
import { registerAndLogin } from './helpers/auth';
import { seedBets } from './helpers/seed';

const pages = [
  { path: '/dashboard', selector: '[data-testid="dashboard-summary"]', name: 'dashboard' },
  { path: '/bets', selector: '[data-testid="bets-list"]', name: 'bets' },
  { path: '/nba/today', selector: '[data-testid="today-active-games"]', name: 'nba-today' },
  { path: '/nba/analysis', selector: '[data-testid="analysis-summary"]', name: 'nba-analysis' },
  { path: '/nba/stat-analysis', selector: '[data-testid="stat-results"]', name: 'nba-stat-analysis' },
  { path: '/bets/new?current_tab=prop#prop', selector: '[data-testid="bet-builder"]', name: 'bet-builder' },
];

const dynamicMasks = (page) => [
  page.locator('.user-btn'),
  page.locator('[id$="last-updated"]'),
];

// Fixed live-progress payloads for the two seeded live-trackable bets — one
// pacing over its line, one pacing under — echoed back keyed by whatever
// card_id the client sends, so the mock doesn't need to guess the URL.
const LIVE_PAYLOADS = {
  player_points: {
    ok: true, player: 'Jayson Tatum', prop_type: 'player_points', bet_type: 'over',
    line: 24.5, current_stat: 27, stat: 27, status_text: 'Q4 2:15', period: 4, clock: '2:15',
    game_state: 'in', elapsed_ratio: 0.85, projected_final: 29.9, progress_pct: 110.2,
    delta_to_line: 2.5, on_track: true, match_score: 1,
  },
  player_rebounds: {
    ok: true, player: 'Nikola Jokic', prop_type: 'player_rebounds', bet_type: 'under',
    line: 9.5, current_stat: 5, stat: 5, status_text: 'Q3 6:40', period: 3, clock: '6:40',
    game_state: 'in', elapsed_ratio: 0.6, projected_final: 8.3, progress_pct: 52.6,
    delta_to_line: -4.5, on_track: true, match_score: 1,
  },
};

async function mockLiveProgress(page) {
  await page.route('**/nba/prop-progress/batch', async (route) => {
    const body = route.request().postDataJSON();
    const results: Record<string, unknown> = {};
    for (const item of body) {
      const payload = LIVE_PAYLOADS[item.prop_type];
      if (payload) results[item.card_id] = payload;
    }
    await route.fulfill({ json: results });
  });
}

test.describe('Visual Regression', () => {
  test('sportsbook core pages remain visually stable', async ({ page }) => {
    await registerAndLogin(page);

    for (const item of pages) {
      await page.goto(item.path);
      await expect(page.locator(item.selector).first()).toBeVisible();
      await expect(page).toHaveScreenshot(`${item.name}.png`, {
        fullPage: true,
        animations: 'disabled',
        mask: dynamicMasks(page),
      });
    }
  });

  test('populated bets list remains visually stable', async ({ page }) => {
    const { username } = await registerAndLogin(page);
    seedBets(username);
    await mockLiveProgress(page);

    await page.goto('/bets');
    await expect(page.locator('[data-testid="bets-list"]')).toBeVisible();
    await expect(page.locator('.row-line')).toHaveCount(6);
    // Live cards resolve async via the batch fetch above; wait for both to
    // leave their "Checking…" placeholder before shooting.
    await expect(page.locator('[data-live-trend]').first()).not.toHaveText('Checking…');
    await expect(page.locator('[data-live-trend]').nth(1)).not.toHaveText('Checking…');

    await expect(page).toHaveScreenshot('bets-populated.png', {
      fullPage: true,
      animations: 'disabled',
      mask: [...dynamicMasks(page), page.locator('.row-sub')],
    });
  });

  test('live-progress rows show over and under pace states', async ({ page }) => {
    const { username } = await registerAndLogin(page);
    seedBets(username);
    await mockLiveProgress(page);

    await page.goto('/bets');
    const liveCards = page.locator('[data-live-prop-card]');
    await expect(liveCards).toHaveCount(2);
    await expect(liveCards.nth(0).locator('[data-live-trend]')).not.toHaveText('Checking…');
    await expect(liveCards.nth(1).locator('[data-live-trend]')).not.toHaveText('Checking…');

    await expect(liveCards.first()).toHaveScreenshot('live-progress-cards.png', {
      animations: 'disabled',
    });
  });

  test('player detail modal remains visually stable', async ({ page }) => {
    await registerAndLogin(page);
    await page.route('**/nba/player-analysis/*', async (route) => {
      await route.fulfill({
        json: {
          player: 'Jayson Tatum',
          prop_type: 'player_points',
          game_log: [
            { date: 'Aug 05', matchup: 'vs NYK', minutes: 34.2, pts: 29, reb: 8, ast: 5, fg3m: 3 },
            { date: 'Aug 03', matchup: '@ MIA', minutes: 36.0, pts: 24, reb: 6, ast: 4, fg3m: 2 },
            { date: 'Aug 01', matchup: 'vs CHI', minutes: 31.5, pts: 31, reb: 9, ast: 6, fg3m: 4 },
          ],
          summary: {},
          breakdown: {
            last_5_avg: 27.4, last_10_avg: 26.1, season_avg: 26.8,
            player_position: 'sf', position_matchup_mult: 1.05,
          },
          context_notes: ['Home', 'Back-to-back'],
          projection: 27.9,
          std_dev: 4.2,
          z_score: 0.6,
          games_played: 10,
          projection_source: 'model',
        },
      });
    });

    await page.goto('/nba/analysis');
    await page.evaluate(() => {
      const btn = document.createElement('button');
      btn.className = 'player-detail-btn';
      btn.dataset.player = 'Jayson Tatum';
      btn.dataset.propType = 'player_points';
      btn.dataset.gameId = '401585183';
      btn.textContent = 'Details';
      document.body.appendChild(btn);
      btn.click();
    });

    const modal = page.locator('#playerDetailModal');
    await expect(modal).toBeVisible();
    await expect(modal.locator('[data-testid="player-detail-table"]')).toBeVisible();

    await expect(modal).toHaveScreenshot('player-detail-modal.png', {
      animations: 'disabled',
    });
  });

  test('login success toast remains visually stable', async ({ page }) => {
    await registerAndLogin(page);
    const toast = page.locator('#toast-stack');
    await expect(toast).toBeVisible();
    await expect(toast).toHaveScreenshot('login-toast.png', {
      animations: 'disabled',
    });
  });

  test('registration validation errors remain visually stable', async ({ page }) => {
    const username = `e2e_invalid_${Date.now().toString().slice(-8)}`;
    await page.goto('/auth/register');
    await page.locator('#username').fill(username);
    await page.locator('#email').fill('not-an-email');
    await page.locator('#password').fill('E2e-safe-passphrase-2026!');
    await page.locator('#confirm_password').fill('doesNotMatch');
    await page.locator('form [type="submit"]').click();

    await expect(page.locator('.field-errors').first()).toBeVisible();
    await expect(page).toHaveScreenshot('register-validation-errors.png', {
      fullPage: true,
      animations: 'disabled',
    });
  });
});
