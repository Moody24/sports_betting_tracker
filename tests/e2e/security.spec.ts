import { expect, test } from '@playwright/test';
import { registerAndLogin } from './helpers/auth';

test.describe('Session security', () => {
  test('logout clears current and legacy parlay storage before another login', async ({ page }) => {
    await registerAndLogin(page);
    await page.evaluate(() => {
      sessionStorage.setItem('sbt_parlay_queue_v1', '[{"player":"Private Player"}]');
      sessionStorage.setItem('parlayQueue', '[{"legacy":true}]');
      localStorage.setItem('parlayQueue', '[{"legacy":true}]');
    });

    await page.locator('.user-btn').click();
    await page.getByRole('button', { name: 'Sign Out' }).click();
    await expect(page).toHaveURL(/\/auth\/login/);

    const storage = await page.evaluate(() => ({
      current: sessionStorage.getItem('sbt_parlay_queue_v1'),
      legacySession: sessionStorage.getItem('parlayQueue'),
      legacyLocal: localStorage.getItem('parlayQueue'),
    }));
    expect(storage).toEqual({ current: null, legacySession: null, legacyLocal: null });

    await registerAndLogin(page);
    expect(await page.evaluate(() => sessionStorage.getItem('sbt_parlay_queue_v1'))).toBeNull();
  });

  test('another signed-in user cannot read or mutate an owners bet', async ({ page }) => {
    await registerAndLogin(page);
    await page.goto('/bets/new');
    const created = await page.evaluate(async () => {
      const token = (window as typeof window & { CSRF_TOKEN: string }).CSRF_TOKEN;
      const response = await fetch('/bets/parlay', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
        body: JSON.stringify({
          stake: 25,
          legs: [{
            team_a: 'Owner Browser Secret',
            team_b: 'Private Opponent',
            match_date: '2026-09-03',
            bet_type: 'moneyline',
            american_odds: -110,
            picked_team: 'Owner Browser Secret',
          }],
        }),
      });
      return response.ok;
    });
    expect(created).toBe(true);

    await page.goto('/bets');
    const ownerDeletePath = await page.locator('.delete-bet-form').first().getAttribute('action');
    expect(ownerDeletePath).toMatch(/^\/delete_bet\/\d+$/);
    const ownerBetId = ownerDeletePath!.split('/').pop()!;

    await page.locator('.user-btn').click();
    await page.getByRole('button', { name: 'Sign Out' }).click();
    await registerAndLogin(page);
    await page.goto('/bets');
    await expect(page.getByText('Owner Browser Secret')).toHaveCount(0);

    const csrfToken = await page.locator('input[name="csrf_token"]').first().getAttribute('value');
    expect(csrfToken).toBeTruthy();
    const requestHeaders = { 'X-CSRFToken': csrfToken! };
    const edit = await page.request.post(`/bets/${ownerBetId}/edit`, {
      headers: { ...requestHeaders, 'Content-Type': 'application/json' },
      data: { notes: 'attacker overwrite' },
    });
    const grade = await page.request.post(`/bets/${ownerBetId}/grade`, {
      headers: requestHeaders,
      form: { outcome: 'win' },
    });
    const remove = await page.request.post(`/delete_bet/${ownerBetId}`, {
      headers: requestHeaders,
    });
    expect([edit.status(), grade.status(), remove.status()]).toEqual([404, 404, 404]);
  });
});
